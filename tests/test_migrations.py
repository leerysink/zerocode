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

# ---------- V20261008_01：工作区记忆表结构（ticket 002） ----------

async def test_workspace_memory_migration_schema(test_engine):
    """建表 + 关键列可空性 + 查询索引 + pgvector 扩展就位（本票只建 embedding 列不写值）。"""
    async with test_engine.connect() as conn:
        t = (await conn.execute(text("SELECT to_regclass('workspace_memory')"))).scalar()
        cols = (await conn.execute(text(
            "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
            "WHERE table_name='workspace_memory'"))).all()
        idx = (await conn.execute(text(
            "SELECT indexname FROM pg_indexes WHERE tablename='workspace_memory'"))).scalars().all()
        ext = (await conn.execute(text(
            "SELECT extversion FROM pg_extension WHERE extname='vector'"))).scalars().all()
    assert t is not None
    by_name = {c[0]: c for c in cols}
    assert {"id", "workspace_id", "type", "title", "content", "embedding",
            "enabled", "created_at", "updated_at"} <= set(by_name)
    assert by_name["content"][2] == "NO" and by_name["workspace_id"][2] == "NO"
    assert by_name["title"][2] == "YES" and by_name["embedding"][2] == "YES"
    assert len(ext) == 1  # vector 扩展存在，embedding 列的 vector 类型才建得起来
    assert "ix_ws_memory_ws_enabled" in idx


async def test_workspace_memory_migration_replayable(test_engine):
    """幂等守卫：表/索引已存在时手工重放迁移语句不报错（存量库升级路径）。"""
    from app.db import split_statements
    from pathlib import Path
    stmts = split_statements(
        Path("changelog/V20261008_01_workspace_memory.sql").read_text(encoding="utf-8"))
    assert len(stmts) == 3  # 扩展 + 建表 + 建索引，切分不含 $$ 块或分号字面量
    async with test_engine.begin() as conn:
        for st in stmts:
            await conn.execute(text(st))
        n = (await conn.execute(text("SELECT count(*) FROM workspace_memory"))).scalar()
    assert n == 0
