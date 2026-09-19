import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from app.db import make_engine, run_migrations

@pytest.fixture
async def test_engine():
    """建测试库（不存在时）并返回 engine；测试库幂等迁移后由各测试自行使用。"""
    # PG 禁止在事务块内 CREATE DATABASE，管理连接必须用 AUTOCOMMIT
    admin = create_async_engine(
        "postgresql+asyncpg://zerocode:zerocode123@localhost:5433/postgres",
        isolation_level="AUTOCOMMIT",
    )
    async with admin.begin() as conn:
        exists = (await conn.execute(text("SELECT 1 FROM pg_database WHERE datname='zerocode_test'"))).first()
        if not exists:
            await conn.exec_driver_sql("CREATE DATABASE zerocode_test")
    await admin.dispose()
    from app.config import test_database_url
    engine = make_engine(test_database_url())
    await run_migrations(engine)
    yield engine
    # 测试间清库（全局约束），保留 schema_migration 与 app_config 种子
    async with engine.begin() as conn:
        await conn.execute(text(
            "TRUNCATE message, file_change, compaction, chat_session, workspace, app_project, model_config "
            "RESTART IDENTITY CASCADE"))
    await engine.dispose()

@pytest.fixture
async def client(test_engine):
    from asgi_lifespan import LifespanManager
    from httpx import AsyncClient, ASGITransport
    from app.main import create_app
    # str(test_engine.url) 会掩码密码（SQLAlchemy 默认 hide_password=True）导致认证失败，
    # 故用 render_as_string(hide_password=False) 取真实连接串
    app = create_app(test_engine.url.render_as_string(hide_password=False))
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            # httpx AsyncClient 不自带 .app 属性，补挂 FastAPI 实例（同 starlette TestClient 约定），测试可取 app.state
            c.app = app
            yield c
