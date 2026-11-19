# tests/test_store_upgrade.py
"""M3-b 手动升级（规格 M3-a §6.4）：前缀校验/影子预演/真迁移/程序切换/失败处置。

影子克隆走真 PG；迁移容器/镜像构建/起栈体检用桩（与 test_store_install_flow 同纪律）。
"""
import hashlib
import json
import secrets
import shutil
from pathlib import Path
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from app.db import make_session_factory
from app.deploy import compose, engine, prod, provision, shadow, sqlrun
from app.deploy.errors import DeployError
from app.services import app_service
from app.store import anchor, packages, upgrade


@pytest.fixture
async def sf(test_engine):
    return make_session_factory(test_engine)


def _md5(s):
    return hashlib.md5(s.encode("utf-8")).hexdigest()


def _mz(root, mapping):
    (root / "backend").mkdir(parents=True, exist_ok=True)
    (root / "backend" / "main.py").write_text("# app\n", encoding="utf-8")


V1 = "CREATE TABLE t(id int);\n"
V2 = "INSERT INTO t VALUES (3);\n"


async def _setup(sf, tmp_path, monkeypatch, *, extra_hist=False, fail=None):
    slug = "up" + secrets.token_hex(3)
    app = await app_service.create_app_project(sf, name="升应", slug=slug, scale="small",
                                               stack="vue3-fastapi", materialize=_mz)
    root = await engine._app_root(sf, app)
    cred = await provision.provision_env(sf, slug, "prod")
    # 实例 prod 库：history（V1 已应用）+ 数据表 t（2 行）
    eng = create_async_engine(anchor.app_db_url(cred))
    async with eng.begin() as c:
        await c.execute(text("CREATE TABLE sqlchange_history(filename VARCHAR(200) PRIMARY KEY, "
                             "checksum VARCHAR(32), success BOOLEAN NOT NULL DEFAULT true)"))
        await c.execute(text("INSERT INTO sqlchange_history(filename, checksum) VALUES ('V1.sql', :s)"),
                        {"s": _md5(V1)})
        if extra_hist:
            await c.execute(text("INSERT INTO sqlchange_history(filename, checksum) "
                                 "VALUES ('Vx.sql', 'xx')"))
        await c.execute(text("CREATE TABLE t(id int)"))
        await c.execute(text("INSERT INTO t VALUES (1), (2)"))
    await eng.dispose()
    # 包：b2 = V1 + V2
    store_root = tmp_path / "store"
    pkg = packages.package_path(store_root, slug, "b2")
    (pkg / "sqlchange").mkdir(parents=True)
    (pkg / "sqlchange" / "V1.sql").write_text(V1, encoding="utf-8")
    (pkg / "sqlchange" / "V2.sql").write_text(V2, encoding="utf-8")
    a1 = [{"filename": "V1.sql", "checksum": _md5(V1)}]
    a2 = a1 + [{"filename": "V2.sql", "checksum": _md5(V2)}]
    (pkg / "manifest.json").write_text(json.dumps(
        {"schema": "zc-release/1", "slug": slug, "version": "b2", "anchor": a2,
         "fingerprint": "fp2", "stack": "vue3-fastapi"}), encoding="utf-8")
    async with sf() as s:
        r1 = (await s.execute(text(
            "INSERT INTO app_release (app_id, slug, version, title, stack, anchor, fingerprint, "
            "status) VALUES (0, :sl, 'b1', '升源', 'vue3-fastapi', CAST(:a AS JSONB), 'fp1', "
            "'published') RETURNING id"), {"sl": slug, "a": json.dumps(a1)})).scalar()
        r2 = (await s.execute(text(
            "INSERT INTO app_release (app_id, slug, version, title, stack, anchor, fingerprint, "
            "status) VALUES (0, :sl, 'b2', '升源', 'vue3-fastapi', CAST(:a AS JSONB), 'fp2', "
            "'published') RETURNING id"), {"sl": slug, "a": json.dumps(a2)})).scalar()
        await s.execute(text(
            "INSERT INTO app_instance_link (app_id, release_id, channel, baseline_manifest, "
            "baseline_hash, applied_fingerprint) VALUES (:a, :r, 'managed', CAST(:m AS JSONB), "
            ":h, 'fp1')"), {"a": app["id"], "r": r1, "m": json.dumps(a1),
                           "h": packages.source_hash(root)})
        await s.execute(text("UPDATE app_project SET current_version='b1' WHERE id=:i"),
                        {"i": app["id"]})
        await s.commit()

    async def _cfg(session_factory):
        return {"store.root": str(store_root)}
    monkeypatch.setattr(upgrade, "deploy_cfg", _cfg)

    calls = []

    async def fake_sqlchange(app_dir, env, cred, on_log=None):
        calls.append(cred["db"])
        if fail == "rehearse" and cred["db"].endswith("_shadow"):
            return {"ok": False, "code": 1, "log": "rehearse boom"}
        if fail == "migrate" and cred["db"] == f"{slug}_prod":
            return {"ok": False, "code": 1, "log": "migrate boom"}
        return {"ok": True, "code": 0, "log": ""}
    monkeypatch.setattr(sqlrun, "run_sqlchange", fake_sqlchange)

    async def fake_docker(*a, **k):
        return 0, ""
    monkeypatch.setattr(engine, "docker", fake_docker)
    monkeypatch.setattr(compose, "ensure_deploy_files", lambda *a, **k: None)

    async def fake_prod_start(session_factory, app, notify=None):
        if fail == "health":
            raise DeployError("体检未通过")
        return {"status": "running"}
    monkeypatch.setattr(prod, "prod_start", fake_prod_start)

    app = await app_service.get_app(sf, app["id"])
    return {"app": app, "slug": slug, "root": root, "r2": int(r2), "calls": calls}


async def _cleanup(sf, ctx):
    slug = ctx["slug"]
    for env in ("dev", "prod"):
        try:
            await provision.drop_env(sf, slug, env)
        except Exception:
            pass
    for name in (shadow.shadow_name(slug), shadow.prev_name(slug, "b2")):
        try:
            await shadow.discard(name)
        except Exception:
            pass
    try:
        await app_service.delete_app(sf, ctx["app"]["id"])
        shutil.rmtree(ctx["root"], ignore_errors=True)
    except Exception:
        pass


async def test_升级成功回写link与版本(sf, tmp_path, monkeypatch):
    ctx = await _setup(sf, tmp_path, monkeypatch)
    try:
        res = await upgrade.run_upgrade(sf, app=ctx["app"], to_release_id=ctx["r2"])
        assert res["ok"] is True and res["version"] == "b2"
        assert res["prev_db"] == f"{ctx['slug']}_prev_b2"
        async with sf() as s:
            link = (await s.execute(text(
                "SELECT release_id, applied_fingerprint, baseline_hash, last_upgraded_at "
                "FROM app_instance_link WHERE app_id=:i"), {"i": ctx["app"]["id"]})).mappings().first()
            ver = (await s.execute(text("SELECT current_version FROM app_project WHERE id=:i"),
                                   {"i": ctx["app"]["id"]})).scalar()
            rec = (await s.execute(text(
                "SELECT status FROM deploy_record WHERE app_id=:i AND kind='upgrade'"),
                {"i": ctx["app"]["id"]})).scalar()
        assert link["release_id"] == ctx["r2"]
        assert link["applied_fingerprint"] == "fp2"
        assert link["baseline_hash"] == packages.source_hash(ctx["root"])
        assert link["last_upgraded_at"] is not None
        assert ver == "b2" and rec == "success"
    finally:
        await _cleanup(sf, ctx)


async def test_前缀分叉判detached不起克隆(sf, tmp_path, monkeypatch):
    ctx = await _setup(sf, tmp_path, monkeypatch, extra_hist=True)
    try:
        with pytest.raises(DeployError) as e:
            await upgrade.run_upgrade(sf, app=ctx["app"], to_release_id=ctx["r2"])
        assert e.value.status == 409
        assert ctx["calls"] == [], "分叉应在克隆前被拦"
        async with sf() as s:
            row = (await s.execute(text(
                "SELECT channel, detach_reason FROM app_instance_link WHERE app_id=:i"),
                {"i": ctx["app"]["id"]})).mappings().first()
        assert row["channel"] == "detached" and row["detach_reason"] == "sql_diverged"
    finally:
        await _cleanup(sf, ctx)


async def test_预演失败真库未动无prev(sf, tmp_path, monkeypatch):
    ctx = await _setup(sf, tmp_path, monkeypatch, fail="rehearse")
    try:
        with pytest.raises(DeployError):
            await upgrade.run_upgrade(sf, app=ctx["app"], to_release_id=ctx["r2"])
        assert not any(c.endswith("_prev_b2") for c in ctx["calls"])
        async with sf() as s:
            ver = (await s.execute(text("SELECT current_version FROM app_project WHERE id=:i"),
                                   {"i": ctx["app"]["id"]})).scalar()
            n = (await s.execute(text(
                "SELECT count(*) FROM pg_database WHERE datname=:n"),
                {"n": f"{ctx['slug']}_prev_b2"})).scalar()
            rec = (await s.execute(text(
                "SELECT status FROM deploy_record WHERE app_id=:i AND kind='upgrade'"),
                {"i": ctx["app"]["id"]})).scalar()
        assert ver == "b1" and n == 0 and rec == "failed"
    finally:
        await _cleanup(sf, ctx)


async def test_真迁移失败程序仍旧版prev保留(sf, tmp_path, monkeypatch):
    ctx = await _setup(sf, tmp_path, monkeypatch, fail="migrate")
    try:
        with pytest.raises(DeployError):
            await upgrade.run_upgrade(sf, app=ctx["app"], to_release_id=ctx["r2"])
        async with sf() as s:
            ver = (await s.execute(text("SELECT current_version FROM app_project WHERE id=:i"),
                                   {"i": ctx["app"]["id"]})).scalar()
            n = (await s.execute(text(
                "SELECT count(*) FROM pg_database WHERE datname=:n"),
                {"n": f"{ctx['slug']}_prev_b2"})).scalar()
        assert ver == "b1", "真迁移失败程序仍跑旧版"
        assert n == 1, "prev 回滚点保留"
    finally:
        await _cleanup(sf, ctx)
