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
            "TRUNCATE message, file_change, chat_session, workspace RESTART IDENTITY CASCADE"))
    await engine.dispose()
