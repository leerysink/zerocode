# tests/test_provision.py
import pytest
from asyncpg.exceptions import InsufficientPrivilegeError, InvalidCatalogNameError
from app.db import make_engine, make_session_factory
from app.deploy import provision
# 平台库连接串与测试库同实例同凭据，仅库名不同（app.config.test_database_url）

pytestmark = pytest.mark.asyncio

SLUG = "zctask3"

async def _cleanup():
    for env in ("dev", "prod"):
        await provision.drop_env(make_session_factory(make_engine()), SLUG, env)

@pytest.fixture(autouse=True)
async def _clean():
    await _cleanup()
    yield
    await _cleanup()

async def test_provision_creates_role_db_and_cred():
    sf = make_session_factory(make_engine())
    cred = await provision.provision_env(sf, SLUG, "dev")
    assert cred["db"] == f"{SLUG}_dev" and cred["user"] == f"{SLUG}_dev"
    assert len(cred["password"]) >= 20
    # 幂等：再次供给返回同一凭据
    cred2 = await provision.provision_env(sf, SLUG, "dev")
    assert cred2 == cred
    # 角色只能连自己的库：真实供给 prod 库后，dev 凭据连 prod 应因权限被拒
    # （库存在，InsufficientPrivilege 42501），而非旧空断言踩中的库不存在（3D000）
    await provision.provision_env(sf, SLUG, "prod")
    from sqlalchemy.ext.asyncio import create_async_engine
    bad = f"postgresql+asyncpg://{cred['user']}:{cred['password']}@localhost:5433/{SLUG}_prod"
    with pytest.raises(Exception) as ei:
        e = create_async_engine(bad)
        async with e.connect():
            pass
        await e.dispose()
    assert not isinstance(ei.value, InvalidCatalogNameError)  # 库存在，拒绝不得来自 catalog 缺失
    assert isinstance(ei.value, InsufficientPrivilegeError)  # 权限拒绝（REVOKE ... FROM PUBLIC 收口）

async def test_read_cred_none_when_missing():
    sf = make_session_factory(make_engine())
    assert await provision.read_cred(sf, "nope9x", "dev") is None

async def test_config_get_all_hides_dbcred():
    """dbcred.* 凭据不得出现在通用配置读取（/api/config 与 agent 配置流共用 get_all）。"""
    sf = make_session_factory(make_engine())
    await provision.provision_env(sf, SLUG, "dev")
    from app.services import config_service
    async with sf() as s:  # get_all 接收 AsyncSession（同 /api/config 与 agent 配置流）
        cfg = await config_service.get_all(s)
    assert not [k for k in cfg if k.startswith("dbcred.")]
    assert await provision.read_cred(sf, SLUG, "dev") is not None  # 专用通道仍可读
