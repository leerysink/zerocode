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
    return (base.set(database=cred["db"], user=cred["user"], password=cred["password"])
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
