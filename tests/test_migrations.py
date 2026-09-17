import pytest
from sqlalchemy import text

@pytest.mark.asyncio
async def test_migrations_create_tables_and_seed(test_engine):
    async with test_engine.begin() as conn:
        for t in ["workspace", "chat_session", "message", "file_change", "app_config", "schema_migration"]:
            r = await conn.execute(text("SELECT to_regclass(:t)"), {"t": t})
            assert r.scalar() is not None, t
        cnt = (await conn.execute(text("SELECT count(*) FROM app_config"))).scalar()
        assert cnt >= 7

@pytest.mark.asyncio
async def test_migrations_idempotent(test_engine):
    from app.db import run_migrations
    applied2 = await run_migrations(test_engine)
    assert applied2 == []
