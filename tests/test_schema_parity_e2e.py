# tests/test_schema_parity_e2e.py
import pytest
from app.db import make_engine, make_session_factory
from app.deploy import provision, scratch
from app.deploy.sqlrun import docker_available

pytestmark = pytest.mark.skipif(not docker_available(), reason="docker/pg 不可用")


@pytest.fixture
async def sf():
    """平台库 session_factory（provision/read_cred 都走它）。"""
    return make_session_factory(make_engine())


@pytest.mark.asyncio
async def test_scratch_create_and_drop(sf):
    name = scratch.verify_db_name("zcp1verify")
    cred = await scratch.ensure_scratch(sf, "zcp1verify")
    assert cred["db"] == name
    await scratch.cleanup_scratch(sf, "zcp1verify")
