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
