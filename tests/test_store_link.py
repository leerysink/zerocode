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
    r = await client.get("/api/store/link/999999")
    assert r.status_code == 404


# ---------- 007/008：解绑与卸载（规格 2026-11-19 §4.4） ----------

async def _owner(sf, name):
    from app.store import identity
    return (await identity.create_actor(sf, name + secrets.token_hex(3)))["id"]


async def _store_app(sf, owner_id):
    from app.services import app_service
    async with sf() as s:
        rid = (await s.execute(text(
            "INSERT INTO app_release (app_id, slug, version, title, stack, anchor, fingerprint) "
            "VALUES (0, :sl, 'b1', '链源', 'vue3-fastapi', '[]'::jsonb, 'fp1') RETURNING id"),
            {"sl": "lkrel" + secrets.token_hex(3)})).scalar()
        await s.commit()
    app = await _mk(sf, origin=f"store:{rid}", owner_user_id=owner_id)
    async with sf() as s:
        await s.execute(text(
            "INSERT INTO app_instance_link (app_id, release_id, channel, baseline_manifest, "
            "baseline_hash, applied_fingerprint, owner_actor) VALUES (:a, :r, 'managed', "
            "'[]'::jsonb, 'h', 'fp1', :o)"), {"a": app["id"], "r": rid, "o": owner_id})
        await s.commit()
    return app, int(rid)


async def _root_of(sf, app):
    async with sf() as s:
        return (await s.execute(text("SELECT root_path FROM workspace WHERE id=:w"),
                                {"w": app["workspace_id"]})).scalar()


async def _cleanup(sf, app):
    from app.deploy import provision
    from app.services import app_service
    import shutil
    for env in ("dev", "prod"):
        try:
            await provision.drop_env(sf, app["slug"], env)
        except Exception:
            pass
    try:
        root = await _root_of(sf, app)
        await app_service.delete_app(sf, app["id"])
        if root:
            shutil.rmtree(root, ignore_errors=True)
    except Exception:
        pass


async def test_解绑保留应用只断链清origin(sf):
    from app.store import links
    from app.services import app_service
    from pathlib import Path
    owner = await _owner(sf, "解绑主人")
    app, _ = await _store_app(sf, owner)
    root = await _root_of(sf, app)
    try:
        res = await links.unlink(sf, app_id=app["id"], actor_id=owner)
        assert res["ok"] is True
        one = await app_service.get_app(sf, app["id"])
        assert not one.get("store"), "解绑后不再是商店实例"
        assert one.get("origin") is None, "origin 标记清空"
        assert Path(root).exists(), "源码目录保留"
    finally:
        await _cleanup(sf, app)


async def test_解绑权限与非商店闸门(sf):
    from app.store import links
    from app.deploy.errors import DeployError
    owner = await _owner(sf, "解绑主人")
    stranger = await _owner(sf, "路人")
    app, _ = await _store_app(sf, owner)
    try:
        # 陌生人不可见 → 404（藏存在性，get_app_for_user 平台口径）
        with pytest.raises(DeployError) as e:
            await links.unlink(sf, app_id=app["id"], actor_id=stranger)
        assert e.value.status == 404
        # 被共享者可见但非 owner → 403
        async with sf() as s:
            await s.execute(text(
                "INSERT INTO app_share (app_id, user_id, created_by) VALUES (:a, :u, :c)"),
                {"a": app["id"], "u": stranger, "c": owner})
            await s.commit()
        with pytest.raises(DeployError) as e:
            await links.unlink(sf, app_id=app["id"], actor_id=stranger)
        assert e.value.status == 403
    finally:
        await _cleanup(sf, app)
    plain = await _mk(sf, owner_user_id=owner)
    try:
        with pytest.raises(DeployError) as e:
            await links.unlink(sf, app_id=plain["id"], actor_id=owner)
        assert e.value.status == 404
    finally:
        await _cleanup(sf, plain)


async def test_卸载全链_停栈先于删库_记录目录库全清(sf, monkeypatch):
    from app.store import links
    from app.deploy import engine, provision
    from pathlib import Path
    owner = await _owner(sf, "卸载主人")
    app, _ = await _store_app(sf, owner)
    root = await _root_of(sf, app)
    calls = []

    async def fake_down(slug):
        calls.append("down")
        return []
    monkeypatch.setattr(engine, "down_app_stacks", fake_down)
    real_drop = provision.drop_env

    async def spy_drop(session_factory, slug, env):
        calls.append(f"drop:{env}")
        return await real_drop(session_factory, slug, env)
    monkeypatch.setattr(provision, "drop_env", spy_drop)

    res = await links.uninstall(sf, app_id=app["id"], actor_id=owner)
    assert res["ok"] is True
    assert calls[0] == "down", "停栈必须先于删库"
    assert "drop:dev" in calls and "drop:prod" in calls
    async with sf() as s:
        assert (await s.execute(text("SELECT count(*) FROM app_project WHERE id=:i"),
                                {"i": app["id"]})).scalar() == 0
        assert (await s.execute(text("SELECT count(*) FROM app_instance_link WHERE app_id=:i"),
                                {"i": app["id"]})).scalar() == 0
    assert not Path(root).exists(), "源码目录删除"
    assert await provision.read_cred(sf, app["slug"], "dev") is None, "dev 库与角色删除"


async def test_解绑卸载路由接线(client, monkeypatch):
    from app.deploy import engine
    me = (await client.get("/api/store/me")).json()
    sf = client.app.state.session_factory
    app, _ = await _store_app(sf, me["id"])

    async def fake_down(slug):
        return []
    monkeypatch.setattr(engine, "down_app_stacks", fake_down)
    try:
        r = await client.post(f"/api/store/link/{app['id']}/unlink")
        assert r.status_code == 200 and r.json()["ok"] is True
        # 解绑后已非商店实例，卸载应 404
        assert (await client.delete(f"/api/store/link/{app['id']}")).status_code == 404
    finally:
        await _cleanup(sf, app)

    app2, _ = await _store_app(sf, me["id"])
    r2 = await client.delete(f"/api/store/link/{app2['id']}")
    assert r2.status_code == 200 and r2.json()["ok"] is True


async def test_升级路由不存在的应用404(client):
    r = await client.post("/api/apps/999999/store/upgrade", json={"to_release_id": 1})
    assert r.status_code == 404
