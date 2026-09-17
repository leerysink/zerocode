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

# ---------- V20260917_02：交互增强表结构 ----------

async def test_interaction_migration_schema(test_engine):
    from sqlalchemy import text
    async with test_engine.connect() as conn:
        tables = (await conn.execute(text(
            "SELECT tablename FROM pg_tables WHERE schemaname='public'"))).scalars().all()
        cols = (await conn.execute(text(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_name IN ('chat_session', 'message')"))).all()
    assert "model_config" in tables and "compaction" in tables
    s_cols = {c[1] for c in cols if c[0] == "chat_session"}
    assert {"model_config_id", "todo_state"} <= s_cols
    m_cols = {c[1] for c in cols if c[0] == "message"}
    assert {"context_content", "reasoning"} <= m_cols
