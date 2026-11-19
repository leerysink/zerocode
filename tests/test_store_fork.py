# tests/test_store_fork.py
"""M3-b fork 判定（规格 M3-a §6.5）：代码 hash 变更 → detached；可升级机器判定。"""
import secrets
import shutil
import pytest
from sqlalchemy import text
from app.db import make_session_factory
from app.deploy import engine, provision
from app.services import app_service
from app.store import baseline, packages


@pytest.fixture
async def sf(test_engine):
    return make_session_factory(test_engine)


def _mz(root, mapping):
    (root / "backend").mkdir(parents=True, exist_ok=True)
    (root / "backend" / "main.py").write_text("# app\n", encoding="utf-8")


async def _mk_app(sf):
    slug = "fk" + secrets.token_hex(3)
    return await app_service.create_app_project(sf, name="叉应", slug=slug, scale="small",
                                                stack="vue3-fastapi", materialize=_mz)


async def _mk_release(sf, slug, version, fp):
    async with sf() as s:
        rid = (await s.execute(text(
            "INSERT INTO app_release (app_id, slug, version, title, stack, anchor, fingerprint, "
            "status) VALUES (0, :sl, :v, '叉源', 'vue3-fastapi', '[]'::jsonb, :fp, 'published') "
            "RETURNING id"), {"sl": slug, "v": version, "fp": fp})).scalar()
        await s.commit()
    return int(rid)


async def _link(sf, app, rid, root):
    async with sf() as s:
        await s.execute(text(
            "INSERT INTO app_instance_link (app_id, release_id, channel, baseline_manifest, "
            "baseline_hash, applied_fingerprint) VALUES (:a, :r, 'managed', '[]'::jsonb, :h, 'fp1')"),
            {"a": app["id"], "r": rid, "h": packages.source_hash(root)})
        await s.commit()


async def _cleanup(sf, app):
    for env in ("dev", "prod"):
        try:
            await provision.drop_env(sf, app["slug"], env)
        except Exception:
            pass
    try:
        root = await engine._app_root(sf, app)
        await app_service.delete_app(sf, app["id"])
        shutil.rmtree(root, ignore_errors=True)
    except Exception:
        pass


async def test_未改动时无新版不可升级_有新版可升级(sf):
    app = await _mk_app(sf)
    root = await engine._app_root(sf, app)
    rid = await _mk_release(sf, app["slug"], "b1", "fp1")
    await _link(sf, app, rid, root)
    try:
        info = await baseline.upgrade_info(sf, app)
        assert info["channel"] == "managed"
        assert info["upgradable"] is False
        assert info["latest_release"] is None
        await _mk_release(sf, app["slug"], "b2", "fp2")
        info = await baseline.upgrade_info(sf, app)
        assert info["upgradable"] is True
        assert info["latest_release"]["version"] == "b2"
    finally:
        await _cleanup(sf, app)


async def test_代码改动判detached并落库(sf):
    app = await _mk_app(sf)
    root = await engine._app_root(sf, app)
    rid = await _mk_release(sf, app["slug"], "b1", "fp1")
    await _link(sf, app, rid, root)
    await _mk_release(sf, app["slug"], "b2", "fp2")   # 即便有新版，分叉后也不可升级
    try:
        (root / "backend" / "main.py").write_text("# changed\n", encoding="utf-8")
        info = await baseline.upgrade_info(sf, app)
        assert info["channel"] == "detached"
        assert info["detach_reason"] == "code_diverged"
        assert info["upgradable"] is False
        async with sf() as s:
            row = (await s.execute(text(
                "SELECT channel, detach_reason FROM app_instance_link WHERE app_id=:i"),
                {"i": app["id"]})).mappings().first()
        assert row["channel"] == "detached" and row["detach_reason"] == "code_diverged"
    finally:
        await _cleanup(sf, app)


async def test_保留定制永久detached(sf):
    app = await _mk_app(sf)
    root = await engine._app_root(sf, app)
    rid = await _mk_release(sf, app["slug"], "b1", "fp1")
    await _link(sf, app, rid, root)
    try:
        (root / "backend" / "main.py").write_text("# changed\n", encoding="utf-8")
        await baseline.upgrade_info(sf, app)
        res = await baseline.resolve_fork(sf, app, "keep_custom")
        assert res["channel"] == "detached"
        async with sf() as s:
            reason = (await s.execute(text(
                "SELECT detach_reason FROM app_instance_link WHERE app_id=:i"),
                {"i": app["id"]})).scalar()
        assert reason == "choice_keep_custom"
    finally:
        await _cleanup(sf, app)


async def test_丢弃定制回官方_代码重置库保留(sf, tmp_path, monkeypatch):
    import json
    from sqlalchemy.ext.asyncio import create_async_engine
    from app.store import anchor
    app = await _mk_app(sf)
    root = await engine._app_root(sf, app)
    rid = await _mk_release(sf, app["slug"], "b1", "fp1")
    await _link(sf, app, rid, root)
    rid2 = await _mk_release(sf, app["slug"], "b2", "fp2")
    # 官方包 b2：src 与原始模板一致（重置后 hash 回到基线）
    store_root = tmp_path / "store"
    pkg = packages.package_path(store_root, app["slug"], "b2")
    (pkg / "src" / "backend").mkdir(parents=True)
    (pkg / "src" / "backend" / "main.py").write_text("# app\n", encoding="utf-8")
    (pkg / "sqlchange").mkdir()
    (pkg / "manifest.json").write_text(json.dumps(
        {"slug": app["slug"], "version": "b2", "anchor": [], "fingerprint": "fp2"}),
        encoding="utf-8")

    async def _cfg(session_factory):
        return {"store.root": str(store_root)}
    monkeypatch.setattr(baseline, "deploy_cfg", _cfg)
    cred = await provision.provision_env(sf, app["slug"], "prod")
    eng = create_async_engine(anchor.app_db_url(cred))
    async with eng.begin() as c:
        await c.execute(text("CREATE TABLE t(id int)"))
        await c.execute(text("INSERT INTO t VALUES (1)"))
    await eng.dispose()
    try:
        (root / "backend" / "main.py").write_text("# changed\n", encoding="utf-8")
        res = await baseline.resolve_fork(sf, app, "reset_to_official")
        assert res["channel"] == "managed" and res["release_id"] == rid2
        assert (root / "backend" / "main.py").read_text(encoding="utf-8") == "# app\n"
        async with sf() as s:
            row = (await s.execute(text(
                "SELECT channel, baseline_hash, release_id FROM app_instance_link WHERE app_id=:i"),
                {"i": app["id"]})).mappings().first()
        assert row["channel"] == "managed"
        assert row["baseline_hash"] == packages.source_hash(root)
        # 库保留：实例数据未被重置动作触碰
        eng2 = create_async_engine(anchor.app_db_url(cred))
        async with eng2.connect() as c:
            n = (await c.execute(text("SELECT count(*) FROM t"))).scalar()
        await eng2.dispose()
        assert n == 1
    finally:
        await _cleanup(sf, app)
