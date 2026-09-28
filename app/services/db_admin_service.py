# app/services/db_admin_service.py
"""应用 DB 操作台服务层（设计 docs/superpowers/specs/2026-09-28-app-db-admin-design.md）。

连接策略：按请求以 dbcred.{slug}.{env} 凭据、基于运行时平台引擎 URL 派生目标库连接，用完 dispose。
不能用 app_config.database_url()——测试里它读环境变量返回默认库，而非 create_app 注入的测试 URL；
app.state.engine.url 才是当前进程真实连接的实例/端口。安全：标识符 quote_ident 转义 + 值参数绑定。
"""
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
    if head in ("select", "insert", "update", "delete"):
        return head
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
            rows = (await conn.execute(text(
                "SELECT table_name AS name, table_type AS type FROM information_schema.tables "
                "WHERE table_schema = 'public' ORDER BY table_name"))).mappings().all()
        return [dict(r) for r in rows]
    finally:
        await eng.dispose()


async def table_schema(request, slug: str, env: str, table: str) -> dict:
    """读列/主键/索引。table 作绑定参数查 information_schema；不存在 → DeployError(404)。"""
    cred = await _cred_or_409(request, slug, env)
    eng = create_async_engine(target_engine_url(request, cred))
    try:
        async with eng.connect() as conn:
            exists = (await conn.execute(text(
                "SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name=:t"),
                {"t": table})).first()
            if not exists:
                raise DeployError(f"表不存在: {table}", status=404)
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
            idx = (await conn.execute(text(
                "SELECT indexname AS name, indexdef AS definition FROM pg_indexes "
                "WHERE schemaname='public' AND tablename=:t ORDER BY indexname"),
                {"t": table})).mappings().all()
        return {
            "columns": [{"name": c["name"], "type": c["type"],
                         "nullable": c["nullable"] == "YES", "default": c["default"]}
                        for c in cols],
            "primary_key": list(pk),
            "indexes": [dict(i) for i in idx],
        }
    finally:
        await eng.dispose()


MAX_PAGE_SIZE = 500


async def table_data(request, slug: str, env: str, table: str, *, page: int = 1,
                     size: int = 50, sort: str | None = None, dirn: str = "asc") -> dict:
    """分页取数。排序列必须是该表实际列（白名单），方向 asc|desc，其余 400；标识符 quote_ident 转义。"""
    cred = await _cred_or_409(request, slug, env)
    schema = await table_schema(request, slug, env, table)  # 顺带做表存在性 404
    valid_cols = {c["name"] for c in schema["columns"]}
    if dirn not in ("asc", "desc"):
        raise DeployError(f"非法排序方向: {dirn}", status=400)
    if not (1 <= size <= MAX_PAGE_SIZE):
        raise DeployError(f"size 需在 1..{MAX_PAGE_SIZE}", status=400)
    if page < 1:
        raise DeployError("page 需 >= 1", status=400)
    order_sql = ""
    if sort:
        if sort not in valid_cols:
            raise DeployError(f"非法排序列: {sort}", status=400)
        order_sql = f" ORDER BY {quote_ident(sort)} {dirn.upper()}"
    tbl = quote_ident(table)
    eng = create_async_engine(target_engine_url(request, cred))
    try:
        async with eng.connect() as conn:
            total = (await conn.execute(text(f"SELECT count(*) FROM {tbl}"))).scalar()
            offset = (page - 1) * size
            rows = (await conn.execute(text(
                f"SELECT * FROM {tbl}{order_sql} LIMIT :lim OFFSET :off"),
                {"lim": size, "off": offset})).mappings().all()
        cols = [c["name"] for c in schema["columns"]]
        return {"columns": cols, "rows": [dict(r) for r in rows],
                "total": int(total), "truncated": total > offset + len(rows)}
    finally:
        await eng.dispose()


SELECT_ROW_CAP = 5000


async def execute_sql(request, slug: str, env: str, sql: str) -> dict:
    """直连执行一条 SQL。有返回列 → rows（截断 SELECT_ROW_CAP）；否则 → affected。
    SELECT 类加语句超时护栏；写/DDL 不加（用户承担）。异常原样上抛给 API 层记审计。"""
    cred = await _cred_or_409(request, slug, env)
    eng = create_async_engine(target_engine_url(request, cred))
    op = classify_op(sql)
    try:
        async with eng.connect() as conn:
            if op == "select":
                await conn.exec_driver_sql("SET statement_timeout = '15s'")
            result = await conn.exec_driver_sql(sql)
            if result.returns_rows:
                cols = list(result.keys())
                fetched = result.fetchall()
                truncated = len(fetched) > SELECT_ROW_CAP
                rows = [dict(zip(cols, r)) for r in fetched[:SELECT_ROW_CAP]]
                return {"kind": "rows", "columns": cols, "rows": rows, "truncated": truncated}
            return {"kind": "affected", "affected": int(result.rowcount or 0)}
    finally:
        await eng.dispose()
