# app/deploy/provision.py
"""应用分库供给（规格 06）：复用 zerocode-pg16 同实例，一应用两库两角色，最小权限。
凭据仅存 app_config 的 dbcred.<slug>.<env> 与 deploy/.env；通用配置读取必须排除 dbcred.*。
"""
import asyncio, json, secrets
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from app import config as app_config
from app.deploy.errors import DeployError

ENVS = ("dev", "prod")

def admin_url() -> str:
    url = make_url(app_config.database_url()).set(database="postgres")
    return url.render_as_string(hide_password=False)

async def _app_config_set(session_factory, key: str, value: dict) -> None:
    async with session_factory() as s:
        # 仲裁目标跟 ux_app_config_owner_key（Ticket 003）：owner 缺省 NULL = 平台级键
        await s.execute(text(
            "INSERT INTO app_config (key, value) VALUES (:k, CAST(:v AS JSONB)) "
            "ON CONFLICT (owner_user_id, key) DO UPDATE SET value = CAST(:v AS JSONB), updated_at = now()"),
            {"k": key, "v": json.dumps(value, ensure_ascii=False)})
        await s.commit()

async def read_cred(session_factory, slug: str, env: str) -> dict | None:
    async with session_factory() as s:
        row = (await s.execute(text(
            "SELECT value FROM app_config WHERE key = :k"),
            {"k": f"dbcred.{slug}.{env}"})).first()
    return dict(row[0]) if row else None

async def provision_env(session_factory, slug: str, env: str) -> dict:
    assert env in ENVS
    name, user = f"{slug}_{env}", f"{slug}_{env}"
    existing = await read_cred(session_factory, slug, env)
    # 角色与库已存在且凭据已登记 → 幂等返回
    engine = create_async_engine(admin_url(), isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            has_role = (await conn.execute(text(
                "SELECT 1 FROM pg_roles WHERE rolname = :n"), {"n": user})).first() is not None
            has_db = (await conn.execute(text(
                "SELECT 1 FROM pg_database WHERE datname = :n"), {"n": name})).first() is not None
        if has_role and has_db and existing:
            return existing
        password = (existing or {}).get("password") or secrets.token_urlsafe(18)
        async with engine.connect() as conn:
            if not has_role:
                pw = password.replace("'", "''")
                await conn.exec_driver_sql(f"CREATE ROLE {user} LOGIN PASSWORD '{pw}'")
            elif not existing:
                # 部分状态自愈：角色在而凭据键缺失（旧密码不可知），必须先对齐新密码再补建库，
                # 否则生成的密码从未应用到角色即写入 app_config，固化坏凭据
                pw = password.replace("'", "''")
                await conn.exec_driver_sql(f"ALTER ROLE {user} WITH LOGIN PASSWORD '{pw}'")
            if not has_db:
                await conn.exec_driver_sql(f"CREATE DATABASE {name} OWNER {user}")
                # 收口 PUBLIC 的 CONNECT/TEMP：角色只能连自己的库（owner 权益不受影响）
                await conn.exec_driver_sql(f"REVOKE ALL ON DATABASE {name} FROM PUBLIC")
    finally:
        await engine.dispose()
    cred = {"db": name, "user": user, "password": password}
    await _app_config_set(session_factory, f"dbcred.{slug}.{env}", cred)
    return cred

async def drop_env(session_factory, slug: str, env: str) -> None:
    name, user = f"{slug}_{env}", f"{slug}_{env}"
    engine = create_async_engine(admin_url(), isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            # 断开活动连接（重试 3 次）
            for _ in range(3):
                r = await conn.execute(text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :n AND pid <> pg_backend_pid()"), {"n": name})
                if r.rowcount == 0:
                    break
            has_db = (await conn.execute(text(
                "SELECT 1 FROM pg_database WHERE datname = :n"), {"n": name})).first()
            if has_db:
                await conn.exec_driver_sql(f"DROP DATABASE {name}")
            has_role = (await conn.execute(text(
                "SELECT 1 FROM pg_roles WHERE rolname = :n"), {"n": user})).first()
            if has_role:
                await conn.exec_driver_sql(f"DROP ROLE {user}")
    finally:
        await engine.dispose()
    async with session_factory() as s:
        await s.execute(text("DELETE FROM app_config WHERE key = :k"),
                        {"k": f"dbcred.{slug}.{env}"})
        await s.commit()

async def create_db_with_role(sf, name: str) -> dict:
    """建一个以平台超级角色(默认连接串的 username，通常是 zerocode)为 owner 的库，返回可连 cred；幂等。

    供发布校验的临时库使用，不动 dev/prod 语义。密码直接复用平台连接串（超级角色跨库可连）。
    """
    url = make_url(app_config.database_url())
    admin_user = url.username or "zerocode"
    admin_pw = url.password or ""
    engine = create_async_engine(admin_url(), isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            has_db = (await conn.execute(text(
                "SELECT 1 FROM pg_database WHERE datname = :n"), {"n": name})).first()
            if not has_db:
                await conn.exec_driver_sql(f'CREATE DATABASE "{name}" OWNER "{admin_user}"')
    finally:
        await engine.dispose()
    return {"db": name, "user": admin_user, "password": admin_pw}


async def drop_db(sf, name: str) -> None:
    """强断连后删库；幂等。仅删库，不动角色（临时库复用超级角色，无专属角色需回收）。"""
    engine = create_async_engine(admin_url(), isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            for _ in range(3):
                r = await conn.execute(text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :n AND pid <> pg_backend_pid()"), {"n": name})
                if r.rowcount == 0:
                    break
            has_db = (await conn.execute(text(
                "SELECT 1 FROM pg_database WHERE datname = :n"), {"n": name})).first()
            if has_db:
                await conn.exec_driver_sql(f'DROP DATABASE "{name}"')
    finally:
        await engine.dispose()


async def ensure_zcnet() -> None:
    """创建外部网络 zcnet 并把 zerocode-pg16 接入（幂等；docker 不可用报 503）。"""
    async def docker(*args, ok=(0,)):
        p = await asyncio.create_subprocess_exec(
            "docker", *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, err = await p.communicate()
        if p.returncode not in ok:
            raise RuntimeError(err.decode(errors="replace").strip() or f"docker {' '.join(args)} 失败")
        return out.decode(errors="replace")
    try:
        await docker("network", "inspect", "zcnet")
    except RuntimeError:
        try:
            await docker("network", "create", "zcnet")
        except RuntimeError as e:
            raise DeployError(f"无法创建 docker 网络 zcnet（Docker 未启动？）: {e}", status=503)
    try:
        await docker("network", "connect", "zcnet", "zerocode-pg16")
    except RuntimeError as e:
        if "already exists" not in str(e):  # 容器已在网内 → 幂等通过
            raise DeployError(f"无法将 zerocode-pg16 接入 zcnet: {e}", status=503)
