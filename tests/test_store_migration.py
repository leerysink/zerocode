# tests/test_store_migration.py
from sqlalchemy import text


async def test_marketplace_tables_exist(test_engine):
    async with test_engine.connect() as c:
        for t in ("actor", "app_release", "release_application", "app_instance_link"):
            row = (await c.execute(text(
                "SELECT 1 FROM pg_tables WHERE tablename = :t"), {"t": t})).first()
            assert row, f"缺少表 {t}"


async def test_app_project_has_origin_column(test_engine):
    async with test_engine.connect() as c:
        row = (await c.execute(text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'app_project' AND column_name = 'origin'"))).first()
        assert row, "app_project.origin 未创建"


async def test_release_version_unique_per_slug(test_engine):
    """同一 slug 的同一版本只能有一条——包不可等的落库兜底。"""
    async with test_engine.begin() as c:
        await c.execute(text("INSERT INTO actor (nickname, token) VALUES ('t', 'tk-mig-1')"))
        aid = (await c.execute(text("SELECT id FROM actor WHERE token='tk-mig-1'"))).scalar()
        await c.execute(text(
            "INSERT INTO app_release (app_id, slug, version, title, anchor, fingerprint, created_by) "
            "VALUES (0, 'demo', 'b1', 'Demo', '[]'::jsonb, 'fp1', :a)"), {"a": aid})
        try:
            await c.execute(text(
                "INSERT INTO app_release (app_id, slug, version, title, anchor, fingerprint, created_by) "
                "VALUES (0, 'demo', 'b1', 'Demo2', '[]'::jsonb, 'fp2', :a)"), {"a": aid})
            raise AssertionError("重复 slug+version 应被 UNIQUE 拒绝")
        except Exception as e:
            assert "unique" in str(e).lower() or "duplicate" in str(e).lower()
