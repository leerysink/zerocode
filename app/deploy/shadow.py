# app/deploy/shadow.py
"""影子克隆（规格 M3-a §6.4③⑤）：升级前在真实数据的克隆上预演，克隆改名留作回滚点。

一次克隆同时买到「预演」与「回滚点」：不新增 env/端口/ compose 栈/角色，磁盘代价＝一个库体积。
克隆只换库 owner，库内对象 owner 仍是 <slug>_prod 角色——故只授它 CONNECT/TEMP，
预演与真实迁移走逐字节一致的权限路径（用超级用户预演会假绿）。
"""
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from app.deploy.provision import admin_url, drop_db


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def shadow_name(slug: str) -> str:
    return f"{slug}_shadow"


def prev_name(slug: str, target_version: str) -> str:
    return f"{slug}_prev_{target_version}"


async def _terminate(conn, name: str) -> None:
    for _ in range(3):
        r = await conn.execute(text(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = :n AND pid <> pg_backend_pid()"), {"n": name})
        if r.rowcount == 0:
            break


async def _drop_if_exists(conn, name: str) -> None:
    await _terminate(conn, name)
    has = (await conn.execute(text("SELECT 1 FROM pg_database WHERE datname = :n"),
                              {"n": name})).first()
    if has:
        await conn.exec_driver_sql(f"DROP DATABASE {_q(name)}")


async def clone_prod(slug: str) -> str:
    """terminate 源 prod 活动连接（数秒断连窗口）→ TEMPLATE 克隆 → 收 PUBLIC、授实例角色。"""
    src, shadow, role = f"{slug}_prod", shadow_name(slug), f"{slug}_prod"
    eng = create_async_engine(admin_url(), isolation_level="AUTOCOMMIT")
    try:
        async with eng.connect() as conn:
            await _drop_if_exists(conn, shadow)      # 幂等：残留影子先丢
            await _terminate(conn, src)
            await conn.exec_driver_sql(f"CREATE DATABASE {_q(shadow)} TEMPLATE {_q(src)}")
            await conn.exec_driver_sql(f"REVOKE ALL ON DATABASE {_q(shadow)} FROM PUBLIC")
            await conn.exec_driver_sql(f"GRANT CONNECT, TEMP ON DATABASE {_q(shadow)} TO {_q(role)}")
    finally:
        await eng.dispose()
    return shadow


async def rename_to_prev(slug: str, target_version: str) -> str:
    """影子改名留作回滚点（不 DROP）；同名 prev 残留先丢。"""
    shadow, prev = shadow_name(slug), prev_name(slug, target_version)
    eng = create_async_engine(admin_url(), isolation_level="AUTOCOMMIT")
    try:
        async with eng.connect() as conn:
            await _drop_if_exists(conn, prev)
            await _terminate(conn, shadow)
            await conn.exec_driver_sql(f"ALTER DATABASE {_q(shadow)} RENAME TO {_q(prev)}")
    finally:
        await eng.dispose()
    return prev


async def discard(name: str) -> None:
    """丢弃影子/prev 库（强断连后删，幂等；不动角色）。"""
    await drop_db(None, name)
