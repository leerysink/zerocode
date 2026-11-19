# tests/test_store_shadow.py
"""影子克隆（规格 M3-a §6.4③⑤）：真库克隆保真、实例角色可连、改名留回滚点、丢弃幂等。"""
import secrets
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from app.db import make_session_factory
from app.deploy import provision, shadow
from app.store import anchor


@pytest.fixture
async def sf(test_engine):
    return make_session_factory(test_engine)


async def _count(cred, db=None):
    c = dict(cred)
    if db:
        c["db"] = db
    eng = create_async_engine(anchor.app_db_url(c))
    try:
        async with eng.connect() as conn:
            return (await conn.execute(text("SELECT count(*) FROM t"))).scalar()
    finally:
        await eng.dispose()


async def test_克隆保真_实例角色可连_改名丢弃(sf):
    slug = "sh" + secrets.token_hex(3)
    cred = await provision.provision_env(sf, slug, "prod")
    eng = create_async_engine(anchor.app_db_url(cred))
    async with eng.begin() as c:
        await c.execute(text("CREATE TABLE t(id int)"))
        await c.execute(text("INSERT INTO t VALUES (1), (2)"))
    await eng.dispose()
    try:
        name = await shadow.clone_prod(slug)
        assert name == f"{slug}_shadow"
        # 克隆带数据，且实例 prod 角色（非超级用户）连得上——预演权限路径一致
        assert await _count(cred, db=name) == 2
        assert await _count(cred) == 2, "真实库未被克隆动作改动"

        prev = await shadow.rename_to_prev(slug, "b2")
        assert prev == f"{slug}_prev_b2"
        assert await _count(cred, db=prev) == 2, "改名后数据仍在（回滚点）"

        await shadow.discard(prev)
        await shadow.discard(prev)   # 幂等
        # 残留影子可被再次克隆覆盖（幂等）
        name2 = await shadow.clone_prod(slug)
        assert name2 == name
        await shadow.discard(name2)
    finally:
        await shadow.discard(shadow.shadow_name(slug))
        await provision.drop_env(sf, slug, "prod")
