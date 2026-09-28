# app/services/db_admin_service.py
"""应用 DB 操作台服务层（设计 docs/superpowers/specs/2026-09-28-app-db-admin-design.md）。

连接策略：按请求以 dbcred.{slug}.{env} 凭据、基于运行时平台引擎 URL 派生目标库连接，用完 dispose。
不能用 app_config.database_url()——测试里它读环境变量返回默认库，而非 create_app 注入的测试 URL；
app.state.engine.url 才是当前进程真实连接的实例/端口。安全：标识符 quote_ident 转义 + 值参数绑定。
"""
import re
import time

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.deploy.errors import DeployError

ENV_WHITELIST = ("dev", "prod")
_DDL_KEYWORDS = {"create", "alter", "drop", "truncate", "comment"}


def validate_env(env: str) -> None:
    if env not in ENV_WHITELIST:
        raise DeployError(f"非法环境: {env}（仅 {'/'.join(ENV_WHITELIST)}）", status=400)


def quote_ident(name: str) -> str:
    """双引号包裹标识符并转义内部双引号，防标识符注入。"""
    return '"' + name.replace('"', '""') + '"'


def classify_op(sql: str) -> str:
    stripped = sql.lstrip().lower()
    head = stripped.split(None, 1)[0] if stripped.strip() else ""
    if head in _DDL_KEYWORDS:
        return "ddl"
    if head in ("insert", "update", "delete"):
        return head
    if head == "select":
        # SELECT ... INTO 会建表写入；含函数调用式写（setval/pg_sleep 等）不当纯读处理。
        if re.search(r"\binto\b", stripped):
            return "other"
        return "select"
    return "other"


def target_engine_url(request, cred: dict) -> str:
    """以当前平台引擎 URL 为底座，仅替换 database/user/password 为应用凭据（host/port 沿用同实例）。"""
    base = request.app.state.engine.url
    return (base.set(database=cred["db"], username=cred["user"], password=cred["password"])
            .render_as_string(hide_password=False))


async def log_operation(sf, *, app_id: int, actor_user_id, env: str, op_type: str,
                        sql_text, target_table, status: str, affected=None,
                        error=None, duration_ms=None) -> None:
    """向平台库 db_operation_log 写一条审计（成功/失败均调）。"""
    async with sf() as s:
        await s.execute(text(
            "INSERT INTO db_operation_log (app_id, actor_user_id, env, op_type, sql_text, "
            "target_table, status, affected, error, duration_ms) "
            "VALUES (:a, :u, :e, :o, :q, :t, :st, :af, :er, :d)"),
            {"a": app_id, "u": actor_user_id, "e": env, "o": op_type, "q": sql_text,
             "t": target_table, "st": status, "af": affected, "er": error, "d": duration_ms})
        await s.commit()


async def _cred_or_409(request, slug: str, env: str) -> dict:
    from app.deploy.provision import read_cred
    cred = await read_cred(request.app.state.session_factory, slug, env)
    if not cred:
        raise DeployError(f"库 {slug}_{env} 凭据缺失（未供给或已删除）", status=409)
    return cred


async def list_tables(request, slug: str, env: str) -> list[dict]:
    """列 public schema 下基表与视图（information_schema 参数化，无注入面）。"""
    cred = await _cred_or_409(request, slug, env)
    eng = create_async_engine(target_engine_url(request, cred))
    try:
        async with eng.connect() as conn:
            await conn.exec_driver_sql(f"SET statement_timeout = '{STATEMENT_TIMEOUT}'")
            rows = (await conn.execute(text(
                "SELECT table_name AS name, table_type AS type FROM information_schema.tables "
                "WHERE table_schema = 'public' ORDER BY table_name"))).mappings().all()
        return [dict(r) for r in rows]
    finally:
        await eng.dispose()


async def _table_columns_pk(conn, table: str) -> dict | None:
    """在给定 conn 上读该 public 表的列/主键（绑定参数查 information_schema）；表不存在返回 None。
    供 table_schema 与 table_data 复用，避免 table_data 为做存在性校验另起一个引擎。"""
    exists = (await conn.execute(text(
        "SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name=:t"),
        {"t": table})).first()
    if not exists:
        return None
    cols = (await conn.execute(text(
        "SELECT column_name AS name, data_type AS type, is_nullable AS nullable, "
        "column_default AS default FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name=:t ORDER BY ordinal_position"),
        {"t": table})).mappings().all()
    pk = (await conn.execute(text(
        "SELECT kcu.column_name AS c FROM information_schema.table_constraints tc "
        "JOIN information_schema.key_column_usage kcu "
        "  ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema "
        "WHERE tc.constraint_type='PRIMARY KEY' AND tc.table_schema='public' AND tc.table_name=:t "
        "ORDER BY kcu.ordinal_position"), {"t": table})).scalars().all()
    return {
        "columns": [{"name": c["name"], "type": c["type"],
                     "nullable": c["nullable"] == "YES", "default": c["default"]}
                    for c in cols],
        "primary_key": list(pk),
    }


async def table_schema(request, slug: str, env: str, table: str) -> dict:
    """读列/主键/索引。table 作绑定参数查 information_schema；不存在 → DeployError(404)。"""
    cred = await _cred_or_409(request, slug, env)
    eng = create_async_engine(target_engine_url(request, cred))
    try:
        async with eng.connect() as conn:
            await conn.exec_driver_sql(f"SET statement_timeout = '{STATEMENT_TIMEOUT}'")
            base = await _table_columns_pk(conn, table)
            if base is None:
                raise DeployError(f"表不存在: {table}", status=404)
            idx = (await conn.execute(text(
                "SELECT indexname AS name, indexdef AS definition FROM pg_indexes "
                "WHERE schemaname='public' AND tablename=:t ORDER BY indexname"),
                {"t": table})).mappings().all()
        return {**base, "indexes": [dict(i) for i in idx]}
    finally:
        await eng.dispose()


MAX_PAGE_SIZE = 500


async def table_data(request, slug: str, env: str, table: str, *, page: int = 1,
                     size: int = 50, sort: str | None = None, dirn: str = "asc") -> dict:
    """分页取数。单请求一个引擎/一条连接内完成存在性校验 + 排序列白名单 + 取数。
    排序列必须是该表实际列（白名单），方向 asc|desc，其余 400；标识符 quote_ident 转义。"""
    cred = await _cred_or_409(request, slug, env)
    if dirn not in ("asc", "desc"):
        raise DeployError(f"非法排序方向: {dirn}", status=400)
    if not (1 <= size <= MAX_PAGE_SIZE):
        raise DeployError(f"size 需在 1..{MAX_PAGE_SIZE}", status=400)
    if page < 1:
        raise DeployError("page 需 >= 1", status=400)
    eng = create_async_engine(target_engine_url(request, cred))
    try:
        async with eng.connect() as conn:
            await conn.exec_driver_sql(f"SET statement_timeout = '{STATEMENT_TIMEOUT}'")
            base = await _table_columns_pk(conn, table)  # 顺带做表存在性 404
            if base is None:
                raise DeployError(f"表不存在: {table}", status=404)
            valid_cols = {c["name"] for c in base["columns"]}
            order_sql = ""
            if sort:
                if sort not in valid_cols:
                    raise DeployError(f"非法排序列: {sort}", status=400)
                order_sql = f" ORDER BY {quote_ident(sort)} {dirn.upper()}"
            tbl = quote_ident(table)
            total = (await conn.execute(text(f"SELECT count(*) FROM {tbl}"))).scalar()
            offset = (page - 1) * size
            rows = (await conn.execute(text(
                f"SELECT * FROM {tbl}{order_sql} LIMIT :lim OFFSET :off"),
                {"lim": size, "off": offset})).mappings().all()
        cols = [c["name"] for c in base["columns"]]
        return {"columns": cols, "rows": [dict(r) for r in rows],
                "total": int(total), "truncated": total > offset + len(rows)}
    finally:
        await eng.dispose()


SELECT_ROW_CAP = 5000
STATEMENT_TIMEOUT = "15s"


async def execute_one(conn, sql: str) -> dict:
    """在**已打开的连接**上执行一条 SQL 并返回结果字典（不建连、不 dispose、不记审计）。

    有返回列 → rows（截断 SELECT_ROW_CAP）；否则 → affected。
    SELECT 类：外层包 `SELECT * FROM (<sql>) LIMIT cap+1` 在库侧即截断（不把整表 fetchall 进内存、防 OOM），
    并加 statement_timeout 护栏；写/DDL 不加超时（用户承担）。
    子查询包裹要求内层是合法 SELECT 源（重复输出列名等极端情形可能报错，属管控台可接受限制）。
    包裹时内层前后各补一个换行：否则以 `--` 行注释收尾的 SELECT 会把右括号和 LIMIT 一起注释掉。
    关键：SQLAlchemy 2.0 默认「commit as you go」，写完必须显式 conn.commit()，否则
    连接关闭即回滚——写/DDL 不落库却返回成功、审计误记 success。异常原样上抛给调用方。

    单条 execute_sql 与批量 execute_batch **共用本函数**：护栏（超时/行数上限/提交时机）只此一份，
    两条路径不各写一套，避免行为漂移。"""
    op = classify_op(sql)
    if op == "select":
        await conn.exec_driver_sql(f"SET statement_timeout = '{STATEMENT_TIMEOUT}'")
        inner = sql.strip().rstrip(";")
        # 换行不可省：内层以 `--` 收尾时，同一行上的 `) AS _zc_cap LIMIT n` 会被注释吃掉。
        capped = f"SELECT * FROM (\n{inner}\n) AS _zc_cap LIMIT {SELECT_ROW_CAP + 1}"
        result = await conn.exec_driver_sql(capped)
    else:
        result = await conn.exec_driver_sql(sql)
    if result.returns_rows:
        cols = list(result.keys())
        fetched = result.fetchall()  # select 至多 cap+1 行；写/RETURNING 一般小量
        truncated = len(fetched) > SELECT_ROW_CAP
        rows = [dict(zip(cols, r)) for r in fetched[:SELECT_ROW_CAP]]
        await conn.commit()  # SELECT 提交无害；带副作用的 SELECT（如 INTO）需此提交才落库
        return {"kind": "rows", "columns": cols, "rows": rows, "truncated": truncated}
    affected = int(result.rowcount or 0)
    await conn.commit()  # 写/DDL 必须显式提交
    return {"kind": "affected", "affected": max(affected, 0)}


async def execute_sql(request, slug: str, env: str, sql: str) -> dict:
    """直连执行一条 SQL：建连/销毁与结果体分开，结果体一律走 execute_one（护栏单一来源）。
    异常原样上抛给 API 层记审计。"""
    cred = await _cred_or_409(request, slug, env)
    eng = create_async_engine(target_engine_url(request, cred))
    try:
        async with eng.connect() as conn:
            return await execute_one(conn, sql)
    finally:
        await eng.dispose()


async def execute_batch(request, slug: str, env: str, statements: list[str]) -> list[dict]:
    """同一引擎/同一连接内**按序**执行多条 SQL，逐条返回结果（不记审计）。

    只建一次连接：N 条语句重连 N 次既慢又会让"已成功的语句"落在不同会话里。
    每条结果 = execute_one 的返回 + {index, op, sql, status, duration_ms}；
    审计留到 API 层逐条写（保持 service 只管执行、API 只管归属/留痕/状态码的分工）。
    本票只走全部成功的路径：语句级异常与"遇错即停"在 Ticket 003 补。"""
    cred = await _cred_or_409(request, slug, env)
    eng = create_async_engine(target_engine_url(request, cred))
    results = []
    try:
        async with eng.connect() as conn:
            for index, sql in enumerate(statements):
                t0 = time.monotonic()
                outcome = await execute_one(conn, sql)
                results.append({"index": index, "op": classify_op(sql), "sql": sql,
                                "status": "ok",
                                "duration_ms": int((time.monotonic() - t0) * 1000),
                                **outcome})
    finally:
        await eng.dispose()
    return results


async def list_audit(sf, app_id: int, *, env: str | None = None, op: str | None = None,
                     limit: int = 100) -> list[dict]:
    """读平台库 db_operation_log（归属校验在 API 层 _load_app 已做）。env/op 可选过滤，limit 上限 500。"""
    limit = min(max(int(limit), 1), 500)
    clauses, params = ["app_id = :a"], {"a": app_id, "lim": limit}
    if env:
        clauses.append("env = :e"); params["e"] = env
    if op:
        clauses.append("op_type = :o"); params["o"] = op
    async with sf() as s:
        rows = (await s.execute(text(
            "SELECT id, env, op_type, sql_text, target_table, status, affected, error, "
            "duration_ms, ts FROM db_operation_log WHERE " + " AND ".join(clauses) +
            " ORDER BY ts DESC LIMIT :lim"), params)).mappings().all()
    return [{**dict(r), "ts": r["ts"].isoformat()} for r in rows]
