# tests/test_store_reconcile.py
"""M3-b 启动对账（规格 M3-a §6.6/§7）：中断 upgrade 记账、残留影子清理、代码分叉、schema_drift。"""
import secrets
import shutil
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from app.db import make_session_factory
from app.deploy import engine, provision, records
from app.services import app_service
from app.store import anchor, baseline, packages, reconcile


@pytest.fixture
async def sf(test_engine):
    return make_session_factory(test_engine)


def _mz(root, mapping):
    (root / "backend").mkdir(parents=True, exist_ok=True)
    (root / "backend" / "main.py").write_text("# app\n", encoding="utf-8")


async def _mk_app(sf):
    slug = "rc" + secrets.token_hex(3)
    return await app_service.create_app_project(sf, name="对账应", slug=slug, scale="small",
                                                stack="vue3-fastapi", materialize=_mz)


async def _release(sf, slug):
    async with sf() as s:
        rid = (await s.execute(text(
            "INSERT INTO app_release (app_id, slug, version, title, stack, anchor, fingerprint, "
            "status) VALUES (0, :sl, 'b1', '对账源', 'vue3-fastapi', '[]'::jsonb, 'fp1', "
            "'published') RETURNING id"), {"sl": slug})).scalar()
        await s.commit()
    return int(rid)


async def _link(sf, app, rid, root, applied="fp1"):
    async with sf() as s:
        await s.execute(text(
            "INSERT INTO app_instance_link (app_id, release_id, channel, baseline_manifest, "
            "baseline_hash, applied_fingerprint) VALUES (:a, :r, 'managed', '[]'::jsonb, :h, :f)"),
            {"a": app["id"], "r": rid, "h": packages.source_hash(root), "f": applied})
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


async def test_中断的upgrade记录标interrupted(sf):
    app = await _mk_app(sf)
    async with sf() as s:
        await s.execute(text(
            "INSERT INTO deploy_record (app_id, kind, version, status) "
            "VALUES (:a, 'upgrade', 'b1', 'running')"), {"a": app["id"]})
        await s.commit()
    try:
        n = await records.mark_interrupted(sf)
        assert n >= 1
        async with sf() as s:
            row = (await s.execute(text(
                "SELECT status, step FROM deploy_record WHERE kind='upgrade' AND app_id=:a"),
                {"a": app["id"]})).mappings().first()
        assert row["status"] == "failed" and row["step"] == "interrupted"
    finally:
        await _cleanup(sf, app)


async def test_残留影子库被清理(sf):
    await provision.create_db_with_role(sf, "orphx_shadow")
    res = await reconcile.reconcile_store(sf)
    assert "orphx_shadow" in res["dropped_shadows"]
    async with sf() as s:
        n = (await s.execute(text("SELECT count(*) FROM pg_database WHERE datname='orphx_shadow'"))).scalar()
    assert n == 0


async def test_对账标代码分叉(sf):
    app = await _mk_app(sf)
    root = await engine._app_root(sf, app)
    rid = await _release(sf, app["slug"])
    await _link(sf, app, rid, root)
    try:
        (root / "backend" / "main.py").write_text("# changed\n", encoding="utf-8")
        res = await reconcile.reconcile_store(sf)
        assert app["id"] in res["detached"]
        async with sf() as s:
            channel = (await s.execute(text(
                "SELECT channel FROM app_instance_link WHERE app_id=:i"),
                {"i": app["id"]})).scalar()
        assert channel == "detached"
    finally:
        await _cleanup(sf, app)


async def test_schema_drift标志(sf):
    app = await _mk_app(sf)
    root = await engine._app_root(sf, app)
    rid = await _release(sf, app["slug"])
    await _link(sf, app, rid, root, applied="fp1")
    cred = await provision.provision_env(sf, app["slug"], "prod")
    eng = create_async_engine(anchor.app_db_url(cred))
    async with eng.begin() as c:
        await c.execute(text("CREATE TABLE sqlchange_history(filename VARCHAR(200) PRIMARY KEY, "
                             "checksum VARCHAR(32), success BOOLEAN NOT NULL DEFAULT true)"))
        await c.execute(text("INSERT INTO sqlchange_history(filename, checksum) "
                             "VALUES ('V1.sql', 'abc')"))
    await eng.dispose()
    try:
        info = await baseline.upgrade_info(sf, app)
        assert info["schema_drift"] is True, "history 指纹与 applied_fingerprint 不一致应警示"
    finally:
        await _cleanup(sf, app)
