# tests/test_deploy_migrations.py
"""M1 迁移：app_project 表与 deploy.* 配置种子（依赖 test_engine fixture，见 tests/conftest.py）。"""
from sqlalchemy import text

async def test_app_project_exists(test_engine):
    async with test_engine.begin() as conn:
        r = await conn.execute(text("SELECT to_regclass('app_project')"))
        assert r.scalar() is not None

async def test_deploy_config_seeded(test_engine):
    async with test_engine.begin() as conn:
        rows = (await conn.execute(text(
            "SELECT key, value FROM app_config WHERE key LIKE 'deploy.%'"))).fetchall()
        cfg = dict(rows)
    assert cfg["deploy.port_base"] == 8210
    assert cfg["deploy.port_max"] == 8399
    assert cfg["deploy.keep_versions"] == 5
    assert cfg["deploy.health_timeout"] == 60
    assert cfg["deploy.auto_stop_dev_on_exit"] is True
    assert cfg["deploy.apps_root"] == ""

async def test_migrations_idempotent(test_engine):
    from app.db import run_migrations
    assert await run_migrations(test_engine) == []
