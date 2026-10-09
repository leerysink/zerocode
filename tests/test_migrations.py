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

# ---------- V20261008_02：向量召回就位（ticket 005） ----------

async def _embedding_shape(test_engine):
    """一次把 kind 列、默认位索引、embedding 定维、向量索引的元数据取全，用例只判断言。"""
    async with test_engine.connect() as conn:
        m_cols = (await conn.execute(text(
            "SELECT column_name, data_type, is_nullable, column_default "
            "FROM information_schema.columns WHERE table_name='model_config'"))).all()
        emb_type = (await conn.execute(text(
            "SELECT format_type(a.atttypid, a.atttypmod) FROM pg_attribute a "
            "WHERE a.attrelid='workspace_memory'::regclass AND a.attname='embedding'"))).scalar()
        m_idx = (await conn.execute(text(
            "SELECT indexname, indexdef FROM pg_indexes WHERE tablename='model_config'"))).all()
        w_idx = (await conn.execute(text(
            "SELECT indexname, indexdef FROM pg_indexes WHERE tablename='workspace_memory'"))).all()
    return m_cols, emb_type, m_idx, w_idx


async def test_memory_embedding_migration_schema(test_engine):
    """kind 列就位 + 默认位唯一索引按 kind 重建 + embedding 定维 1024 + HNSW  cosine 索引。"""
    from app.agent import embeddings as emb
    m_cols, emb_type, m_idx, w_idx = await _embedding_shape(test_engine)
    kind = {c[0]: c for c in m_cols}["kind"]
    assert kind[1] == "character varying" and kind[2] == "NO"
    assert "'chat'" in (kind[3] or "")  # 存量行迁移后全部落在 chat，主链路零变化
    names = {r[0] for r in m_idx}
    assert "ux_model_default_per_owner" not in names, "旧索引不删就会拦死「默认 chat + 默认 embedding」"
    assert "ux_model_default_per_owner_kind" in names
    per_kind = [r[1] for r in m_idx if r[0] == "ux_model_default_per_owner_kind"][0]
    assert "owner_user_id, kind" in per_kind and "WHERE is_default" in per_kind
    assert emb_type == f"vector({emb.EMBEDDING_DIM})", "列维度与运行期常量必须成对修改"
    vec_idx = {r[0]: r[1] for r in w_idx}["ix_ws_memory_embedding"]
    assert "USING hnsw" in vec_idx and "vector_cosine_ops" in vec_idx


async def test_memory_embedding_migration_replayable(test_engine):
    """幂等守卫：手工重放全部语句不报错（存量库升级路径），语句切分不含 $$ 块。"""
    from app.db import split_statements
    from pathlib import Path
    stmts = split_statements(
        Path("changelog/V20261008_02_memory_embedding.sql").read_text(encoding="utf-8"))
    assert len(stmts) == 5  # 加列 + 删旧索引 + 建新索引 + 定维 + 建向量索引
    async with test_engine.begin() as conn:
        for st in stmts:
            await conn.execute(text(st))


async def test_memory_embedding_migration_reappliesThroughRunMigrations(test_engine):
    """真正的重放口径：清掉版本行后跑 `run_migrations`，等价于「同一个库上重启一次程序」。

    只手工重放语句是不够的：`run_migrations` 会按文件名排序整批重跑，
    这条才能证明本文件插在中间时不会把后面的版本一起带倒。
    """
    from app.db import run_migrations
    target = "V20261008_02_memory_embedding.sql"
    async with test_engine.begin() as conn:
        await conn.execute(text("DELETE FROM schema_migration WHERE filename=:f"), {"f": target})
    applied = await run_migrations(test_engine)
    assert applied == [target]  # 只有被清掉的那一条被重新应用，其余版本不受牵连


async def test_memory_embeddingMigrationKeepsLegacyRowsAsChat(test_engine):
    """边界（存量兼容）：不给 kind 的老 INSERT 必须落成 'chat'，
    否则迁移后既有档案会被 kind='chat' 过滤整体漏掉，对话直接退回全局配置。"""
    async with test_engine.begin() as conn:
        kind = (await conn.execute(text(
            "INSERT INTO model_config (name, base_url, api_key, model, is_default) "
            "VALUES ('legacy', 'http://l/v1', 'k', 'l-m', FALSE) RETURNING kind"))).scalar()
    assert kind == "chat"


async def test_memory_embeddingMigrationPerKindDefaultsCoexist(test_engine):
    """边界（唯一索引重建的目的）：同 owner 的默认 chat + 默认 embedding 能并存，
    同 kind 的第二个默认仍被 DB 拦下——应用层清位漏了也不脏库。"""
    from sqlalchemy.exc import IntegrityError
    async with test_engine.begin() as conn:
        uid = (await conn.execute(text(
            "INSERT INTO actor (nickname, token) VALUES ('kind-owner', 'tok-kind') "
            "RETURNING id"))).scalar()
        for name, k in (("c", "chat"), ("e", "embedding")):
            await conn.execute(text(
                "INSERT INTO model_config (name, base_url, api_key, model, is_default, owner_user_id, kind) "
                "VALUES (:n, 'http://x/v1', 'k', :m, TRUE, :o, :k)"),
                {"n": name, "m": name + "-m", "o": uid, "k": k})
        n = (await conn.execute(text(
            "SELECT count(*) FROM model_config WHERE owner_user_id=:o AND is_default"),
            {"o": uid})).scalar()
    assert n == 2
    with pytest.raises(IntegrityError):
        async with test_engine.begin() as conn:
            await conn.execute(text(
                "INSERT INTO model_config (name, base_url, api_key, model, is_default, owner_user_id, kind) "
                "VALUES ('c2', 'http://c2/v1', 'k', 'c2-m', TRUE, :o, 'chat')"), {"o": uid})


async def test_memoryEmbeddingColumnRejectsWrongDimension(test_engine, tmp_path):
    """边界（定维的代价）：非 1024 维必须被列类型当场挡下，
    而不是静默入库后在下一次 <=> 检索时才炸（那时已经查不出是哪条脏数据）。"""
    from sqlalchemy.exc import DBAPIError
    async with test_engine.begin() as conn:
        ws = (await conn.execute(text(
            "INSERT INTO workspace (name, root_path) VALUES ('dim', :p) RETURNING id"),
            {"p": str(tmp_path / "dim")})).scalar()
    with pytest.raises(DBAPIError):
        async with test_engine.begin() as conn:
            await conn.execute(text(
                "INSERT INTO workspace_memory (workspace_id, type, content, embedding) "
                "VALUES (:w, 'fact', 'x', CAST(:v AS vector))"),
                {"w": ws, "v": "[1,2,3]"})
