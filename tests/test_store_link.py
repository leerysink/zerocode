# tests/test_store_link.py
"""应用列表要能说出「这实例来自哪个包、跟不跟版本」，卡片角标与升级入口都靠它。"""
import secrets
import pytest
from sqlalchemy import text
from app.db import make_session_factory


@pytest.fixture
async def sf(test_engine):
    return make_session_factory(test_engine)


async def _mk(sf, **kw):
    from app.services import app_service
    slug = "lk" + secrets.token_hex(3)

    def mz(root, mapping):
        (root / "backend").mkdir(parents=True, exist_ok=True)
        (root / "backend" / "main.py").write_text("#\n", encoding="utf-8")
    app = await app_service.create_app_project(sf, name="链应", slug=slug, scale="small",
                                               stack="vue3-fastapi", materialize=mz, **kw)
    return app


async def test_list_apps_carries_origin_and_link(sf):
    app = await _mk(sf, origin="store:12")
    try:
        async with sf() as s:
            aid = (await s.execute(text(
                "INSERT INTO app_release (app_id, slug, version, title, stack, anchor, "
                "fingerprint) VALUES (0, 'demo', 'b1', '演示', 'vue3-fastapi', '[]'::jsonb, "
                "'fp1') RETURNING id"))).scalar()
            await s.execute(text(
                "INSERT INTO app_instance_link (app_id, release_id, channel, baseline_manifest, "
                "baseline_hash, applied_fingerprint) VALUES (:a, :r, 'managed', '[]'::jsonb, "
                "'h', 'fp1')"), {"a": app["id"], "r": aid})
            await s.commit()
        from app.services import app_service
        rows = {a["id"]: a for a in await app_service.list_apps(sf)}
        assert rows[app["id"]]["origin"] == "store:12"
        assert rows[app["id"]]["store"]["channel"] == "managed"
        assert rows[app["id"]]["store"]["release_version"] == "b1"
        # 详情页与列表页字段一致（前端一处判定逻辑两处复用）
        one = await app_service.get_app(sf, app["id"])
        assert one["origin"] == "store:12"
        assert one["store"]["release_slug"] == "demo"
    finally:
        from app.deploy import provision
        for env in ("dev", "prod"):
            try:
                await provision.drop_env(sf, app["slug"], env)
            except Exception:
                pass
        await app_service.delete_app(sf, app["id"])


async def test_store_link_endpoint_404_for_non_store_app(client):
    await client.post("/api/store/identify", json={"nickname": "看客"})
    r = await client.get("/api/store/link/999999")
    assert r.status_code == 404
