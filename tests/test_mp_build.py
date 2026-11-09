# tests/test_mp_build.py
"""小程序构建任务器（ADR-0044/0045 / 规格 2026-11-09）。

不碰真 Docker：`_docker` 与 `_ensure_image` 用替身；这里验入队语义、预检不产开发版、
上传写版本、失败分类与收口、工作区不残留。
"""
import asyncio
import json
import shutil
from pathlib import Path

import pytest
from sqlalchemy import text

from app.db import make_session_factory
from app.deploy import engine as eng_mod, mpbuild, mpcreds, mpruntime, ports, provision, sqlrun
from app.deploy.errors import DeployError
from app.services import app_service

pytestmark = pytest.mark.asyncio

SLUG = "zcmp"
ROOT = Path("D:/__zc_mp_build__")
MOBILE_STACK = "vue3-springboot-mobile"
APPID = "wx1234567890abcdef"
KEY = b"-----BEGIN PRIVATE KEY-----\nMIIfake\n-----END PRIVATE KEY-----\n"


@pytest.fixture(autouse=True)
def _no_startup_docker(monkeypatch):
    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(eng_mod, "cleanup_orphans", _noop)
    monkeypatch.setattr(eng_mod, "stop_all_dev_on_exit", _noop)
    monkeypatch.setattr(sqlrun, "build_if_missing", _noop)
    monkeypatch.setattr(provision, "ensure_zcnet", _noop)


@pytest.fixture(autouse=True)
def _isolate_data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path / "data"))


@pytest.fixture(autouse=True)
def _mp_runtime_offline(monkeypatch):
    """预检会做 TLS 可达性检查：本文件不碰真外网，可达性与出网 IP 都给确定值。"""
    async def reachable(url, timeout=3.0):
        return True

    monkeypatch.setattr(mpruntime, "probe_tls", reachable)
    monkeypatch.setenv("ZEROCODE_EGRESS_IP", "203.0.113.9")
    mpruntime._cache.update(ip="", at=None)


@pytest.fixture
async def make_app(client, test_engine):
    sf = make_session_factory(test_engine)
    ROOT.mkdir(parents=True, exist_ok=True)
    uid = (await client.get("/api/auth/me")).json()["id"]
    created: list[tuple[int, int]] = []

    async def _make(*, stack: str = MOBILE_STACK, status_prod: str = "running",
                    current_version: str | None = "b20261109000000",
                    slug: str = SLUG) -> int:
        async with sf() as s:
            root = ROOT if not created else ROOT / f"w{len(created)}"
            root.mkdir(parents=True, exist_ok=True)
            prod_port, dev_port = await ports.allocate_pair(s)
            wid = (await s.execute(text(
                "INSERT INTO workspace (name, root_path, owner_user_id) "
                "VALUES ('mp', :r, :o) RETURNING id"), {"r": str(root), "o": uid})).scalar()
            aid = (await s.execute(text(
                "INSERT INTO app_project (workspace_id, name, slug, scale, stack, prod_port, dev_port, "
                "status_prod, current_version, owner_user_id) "
                "VALUES (:w, '小程序应用', :sl, 'large', :st, :pp, :dp, :sp, :cv, :o) RETURNING id"),
                {"w": wid, "sl": slug, "st": stack, "pp": prod_port, "dp": dev_port,
                 "sp": status_prod, "cv": current_version, "o": uid})).scalar()
            await s.commit()
        created.append((aid, wid))
        return aid

    yield _make
    async with sf() as s:
        for aid, wid in created:
            await s.execute(text("DELETE FROM app_project WHERE id=:i"), {"i": aid})
            await s.execute(text("DELETE FROM workspace WHERE id=:i"), {"i": wid})
        await s.commit()
    shutil.rmtree(ROOT, ignore_errors=True)


async def _row(sf, app_id):
    return await app_service.get_app(sf, app_id)


async def _bound(sf, app_id, *, appid=APPID):
    app = await _row(sf, app_id)
    await mpcreds.bind(sf, app, appid, KEY)
    return app


def _source_tree(root: Path, *, request_js: str | None = None):
    """最小可叠加工程：package.json + manifest + request.js。"""
    mobile = root / "frontend" / "mobile"
    (mobile / "src" / "utils").mkdir(parents=True, exist_ok=True)
    (mobile / "package.json").write_text(json.dumps({
        "dependencies": {"@dcloudio/uni-app": "3.0.0-5030420261029002"},
        "scripts": {"build:h5": "uni build"},
    }), encoding="utf-8")
    (mobile / "src" / "manifest.json").write_text(json.dumps({"name": "小程序应用"}),
                                                  encoding="utf-8")
    (mobile / "src" / "utils" / "request.js").write_text(
        request_js if request_js is not None else "url: '/api' + path\n", encoding="utf-8")


def _ok_docker(calls: list[dict] | None = None, *, code: int = 0, out: str = "ok"):
    async def _f(*args, cwd=None, timeout=600):
        if calls is not None:
            calls.append({"args": args, "timeout": timeout})
        return code, out
    return _f


async def _no_image():
    return None


def _fake_workspace(preview: bytes | None = b"jpeg"):
    def _make(app, root, appid, slug):
        ws = mpbuild.workspace(slug)
        shutil.rmtree(ws, ignore_errors=True)
        ws.mkdir(parents=True)
        if preview is not None:
            (ws / "preview.jpg").write_bytes(preview)
        return ws
    return _make


async def test_enqueue_skips_unbound_and_non_mobile(test_engine, make_app):
    sf = make_session_factory(test_engine)
    aid = await make_app()
    assert await mpbuild.MiniprogramBuilder(sf).enqueue(aid, kind="precheck") == "skipped"
    assert await mpbuild.MiniprogramBuilder(sf).enqueue(aid, kind="upload") == "skipped"
    assert (await mpcreds.get_binding(sf, aid))["status"] == "idle"

    plain = await make_app(stack="vue3-fastapi", slug="zcmp2")
    await _bound(sf, plain)
    assert await mpbuild.MiniprogramBuilder(sf).enqueue(plain, kind="precheck") == "skipped"


async def test_precheck_queues_even_when_prod_stopped(test_engine, make_app):
    """绑定后的预检与发布无关：prod 没跑也能验凭据/编译（不产生开发版）。"""
    sf = make_session_factory(test_engine)
    aid = await make_app(status_prod="stopped")
    await _bound(sf, aid)
    b = mpbuild.MiniprogramBuilder(sf)
    assert await b.enqueue(aid, kind="precheck") == "queued"
    assert (await mpcreds.get_binding(sf, aid))["status"] == "building"
    assert (await mpcreds.get_binding(sf, aid))["job_kind"] == "precheck"


async def test_upload_defers_when_prod_stopped_and_force_raises(test_engine, make_app):
    sf = make_session_factory(test_engine)
    aid = await make_app(status_prod="stopped")
    await _bound(sf, aid)
    b = mpbuild.MiniprogramBuilder(sf)
    assert await b.enqueue(aid, kind="upload") == "deferred"
    assert (await mpcreds.get_binding(sf, aid))["status"] == "idle"
    with pytest.raises(DeployError) as e:
        await b.enqueue(aid, kind="upload", force=True)
    assert e.value.status == 409


async def test_enqueue_dedupes_and_remembers_rerun(test_engine, make_app):
    sf = make_session_factory(test_engine)
    aid = await make_app()
    app = await _bound(sf, aid)
    b = mpbuild.MiniprogramBuilder(sf)
    assert await b.enqueue(aid, kind="precheck", app=app) == "queued"
    assert await b.enqueue(aid, kind="precheck", app=app) == "building"
    assert await b.enqueue(aid, kind="preview", app=app, force=True) == "building"
    assert b._rerun == {aid: "preview"}


async def test_recover_marks_interrupted_jobs_failed(test_engine, make_app):
    sf = make_session_factory(test_engine)
    aid = await make_app()
    await _bound(sf, aid)
    async with sf() as s:
        await s.execute(text(
            "UPDATE app_mobile_miniprogram SET status='uploading', job_kind='upload' "
            "WHERE app_id=:i"), {"i": aid})
        await s.commit()
    await mpbuild.MiniprogramBuilder(sf).recover()
    state = await mpcreds.get_binding(sf, aid)
    assert state["status"] == "failed" and "平台重启" in state["error"]


async def test_precheck_success_keeps_preview_without_dev_version(test_engine, make_app, monkeypatch):
    sf = make_session_factory(test_engine)
    aid = await make_app()
    await _bound(sf, aid)
    monkeypatch.setattr(mpbuild.mpoverlay, "make_workspace", _fake_workspace(b"preview-jpeg"))
    monkeypatch.setattr(mpbuild, "_ensure_image", _no_image)
    monkeypatch.setattr(mpbuild, "_docker", _ok_docker())

    await mpbuild._job(sf, aid, "precheck")

    state = await mpcreds.get_binding(sf, aid)
    assert state["status"] == "ready" and state["job_kind"] == "precheck"
    assert state["version"] is None and state["uploaded_at"] is None   # 预检不产开发版
    assert state["preview_at"] is not None
    assert mpbuild.preview_path(SLUG).read_bytes() == b"preview-jpeg"
    assert not mpbuild.workspace(SLUG).exists()                        # 工作区不长期保留


async def test_job_requires_https_domain(test_engine, make_app, monkeypatch):
    """小程序真机只接受备案 HTTPS 域名：SSL 关时预检直接失败并说清前置条件。"""
    sf = make_session_factory(test_engine)
    aid = await make_app()
    await _bound(sf, aid)
    monkeypatch.setenv("ZEROCODE_SSL_ENABLE", "false")
    with pytest.raises(DeployError) as e:
        await mpbuild._job(sf, aid, "precheck")
    assert "HTTPS" in str(e.value)


async def test_precheck_fails_when_domain_tls_unreachable(test_engine, make_app, monkeypatch):
    """自签证书/域名未生效：预检直接失败并归类「域名或证书」，不产出指错后端的包。"""
    sf = make_session_factory(test_engine)
    aid = await make_app()
    await _bound(sf, aid)

    async def unreachable(url, timeout=3.0):
        return False

    monkeypatch.setattr(mpruntime, "probe_tls", unreachable)
    with pytest.raises(DeployError) as e:
        await mpbuild._job(sf, aid, "precheck")
    assert "域名或证书" in str(e.value)


async def test_job_failure_is_classified(test_engine, make_app, monkeypatch):
    sf = make_session_factory(test_engine)
    aid = await make_app()
    await _bound(sf, aid)
    monkeypatch.setattr(mpbuild.mpoverlay, "make_workspace", _fake_workspace())
    monkeypatch.setattr(mpbuild, "_ensure_image", _no_image)
    monkeypatch.setattr(mpbuild, "_docker", _ok_docker(
        code=1, out="Error: invalid ip: 1.2.3.4, not in whitelist"))

    with pytest.raises(DeployError) as e:
        await mpbuild._job(sf, aid, "precheck")
    assert "IP 白名单" in str(e.value)
    assert not mpbuild.workspace(SLUG).exists()


async def test_upload_success_records_version_and_time(test_engine, make_app, monkeypatch):
    sf = make_session_factory(test_engine)
    aid = await make_app()
    await _bound(sf, aid)
    calls: list[dict] = []
    monkeypatch.setattr(mpbuild.mpoverlay, "make_workspace", _fake_workspace())
    monkeypatch.setattr(mpbuild, "_ensure_image", _no_image)
    monkeypatch.setattr(mpbuild, "_docker", _ok_docker(calls))

    await mpbuild._job(sf, aid, "upload")

    state = await mpcreds.get_binding(sf, aid)
    assert state["status"] == "ready" and state["job_kind"] == "upload"
    assert state["version"] == "b20261109000000"
    assert state["uploaded_at"] is not None
    assert state["preview_at"] is None
    assert not mpbuild.preview_path(SLUG).exists()          # 上传只产开发版，不产预览码
    env = calls[0]["args"]
    assert "MP_MODE=upload" in env and f"MP_APPID={APPID}" in env
    assert "MP_VERSION=b20261109000000" in env
    desc = next(a for a in env if a.startswith("MP_DESC="))
    assert desc.startswith("MP_DESC=小程序应用 ")


async def test_upload_force_requeues_even_when_version_matches(test_engine, make_app):
    """手动重传不看上次结果/版本：同版本也再传一次（覆盖开发版）。"""
    sf = make_session_factory(test_engine)
    aid = await make_app()
    await _bound(sf, aid)
    async with sf() as s:
        await s.execute(text(
            "UPDATE app_mobile_miniprogram SET status='ready', job_kind='upload', "
            "version=:v WHERE app_id=:i"), {"i": aid, "v": "b20261109000000"})
        await s.commit()
    b = mpbuild.MiniprogramBuilder(sf)
    assert await b.enqueue(aid, kind="upload", force=True) == "queued"
    assert (await mpcreds.get_binding(sf, aid))["status"] == "building"
    # 已排队的同一应用再 force：本轮后补跑一次，不并行
    assert await b.enqueue(aid, kind="upload", force=True) == "building"
    assert b._rerun == {aid: "upload"}


async def test_upload_failure_keeps_previous_version(test_engine, make_app, monkeypatch):
    """上传失败只改状态与原因：旧开发版（version）仍可下载，可手动重试。"""
    sf = make_session_factory(test_engine)
    aid = await make_app()
    await _bound(sf, aid)
    async with sf() as s:
        await s.execute(text(
            "UPDATE app_mobile_miniprogram SET status='ready', job_kind='upload', "
            "version='b20260101000000' WHERE app_id=:i"), {"i": aid})
        await s.commit()
    monkeypatch.setattr(mpbuild.mpoverlay, "make_workspace", _fake_workspace())
    monkeypatch.setattr(mpbuild, "_ensure_image", _no_image)
    monkeypatch.setattr(mpbuild, "_docker", _ok_docker(code=1, out="invalid ip 1.2.3.4"))

    b = mpbuild.MiniprogramBuilder(sf)
    b._task = asyncio.create_task(b._loop(), name="mp-builder-test")
    try:
        await b._queue.put((aid, "upload"))
        await asyncio.wait_for(b._queue.join(), 5)
    finally:
        await b.close()

    state = await mpcreds.get_binding(sf, aid)
    assert state["status"] == "failed" and "IP 白名单" in state["error"]
    assert state["version"] == "b20260101000000"


async def test_job_fails_when_request_js_structure_changed(test_engine, make_app, monkeypatch):
    sf = make_session_factory(test_engine)
    aid = await make_app()
    await _bound(sf, aid)
    _source_tree(ROOT, request_js="export const nope = 1\n")
    monkeypatch.setattr(mpbuild, "_ensure_image", _no_image)
    monkeypatch.setattr(mpbuild, "_docker", _ok_docker())

    with pytest.raises(DeployError) as e:
        await mpbuild._job(sf, aid, "precheck")
    assert "结构已变化" in str(e.value)


async def test_loop_marks_failed_and_keeps_running(test_engine, make_app, monkeypatch):
    sf = make_session_factory(test_engine)
    aid = await make_app()
    await _bound(sf, aid)

    async def boom(sf_, app_id, kind):
        raise DeployError("预检炸了")

    monkeypatch.setattr(mpbuild, "_job", boom)
    b = mpbuild.MiniprogramBuilder(sf)
    b._task = asyncio.create_task(b._loop(), name="mp-builder-test")
    try:
        await b._queue.put((aid, "precheck"))
        await asyncio.wait_for(b._queue.join(), 5)
        state = await mpcreds.get_binding(sf, aid)
        assert state["status"] == "failed" and "预检炸了" in state["error"]
    finally:
        await b.close()


async def test_preview_success_keeps_only_latest_qr(test_engine, make_app, monkeypatch):
    """按需预览：成功写 preview_at、不产开发版；码只留最新一张（旧码被覆盖）。"""
    sf = make_session_factory(test_engine)
    aid = await make_app()
    await _bound(sf, aid)
    old = mpbuild.preview_path(SLUG)
    old.parent.mkdir(parents=True, exist_ok=True)
    old.write_bytes(b"old-qr")

    monkeypatch.setattr(mpbuild.mpoverlay, "make_workspace", _fake_workspace(b"new-qr"))
    monkeypatch.setattr(mpbuild, "_ensure_image", _no_image)
    monkeypatch.setattr(mpbuild, "_docker", _ok_docker())

    await mpbuild._job(sf, aid, "preview")

    state = await mpcreds.get_binding(sf, aid)
    assert state["status"] == "ready" and state["job_kind"] == "preview"
    assert state["preview_at"] is not None
    assert state["version"] is None and state["uploaded_at"] is None
    assert old.read_bytes() == b"new-qr"
    assert not mpbuild.workspace(SLUG).exists()


async def test_preview_failure_keeps_previous_qr(test_engine, make_app, monkeypatch):
    """预览失败不动上一张码：弹框可继续展示旧码并给出可读原因。"""
    sf = make_session_factory(test_engine)
    aid = await make_app()
    await _bound(sf, aid)
    old = mpbuild.preview_path(SLUG)
    old.parent.mkdir(parents=True, exist_ok=True)
    old.write_bytes(b"old-qr")

    monkeypatch.setattr(mpbuild.mpoverlay, "make_workspace", _fake_workspace(b"new-qr"))
    monkeypatch.setattr(mpbuild, "_ensure_image", _no_image)
    monkeypatch.setattr(mpbuild, "_docker", _ok_docker(code=1, out="invalid ip 1.2.3.4"))

    with pytest.raises(DeployError) as e:
        await mpbuild._job(sf, aid, "preview")
    assert "IP 白名单" in str(e.value)
    assert old.read_bytes() == b"old-qr"


async def test_preview_fails_when_builder_outputs_no_qr(test_engine, make_app, monkeypatch):
    sf = make_session_factory(test_engine)
    aid = await make_app()
    await _bound(sf, aid)
    monkeypatch.setattr(mpbuild.mpoverlay, "make_workspace", _fake_workspace(preview=None))
    monkeypatch.setattr(mpbuild, "_ensure_image", _no_image)
    monkeypatch.setattr(mpbuild, "_docker", _ok_docker())

    with pytest.raises(DeployError) as e:
        await mpbuild._job(sf, aid, "preview")
    assert "没有输出 preview.jpg" in str(e.value)


async def test_cleanup_app_removes_workspace_and_qr():
    ws = mpbuild.workspace(SLUG)
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "x").write_text("x", encoding="utf-8")
    qr = mpbuild.preview_path(SLUG)
    qr.parent.mkdir(parents=True, exist_ok=True)
    qr.write_bytes(b"qr")

    mpbuild.cleanup_app(SLUG)
    assert not ws.exists() and not qr.exists()


async def test_classify_error_labels_common_failures():
    assert mpbuild.classify_error("invalid ip 1.2.3.4").startswith("IP 白名单")
    assert mpbuild.classify_error("invalid appid").startswith("AppID")
    assert mpbuild.classify_error("invalid private key").startswith("上传密钥")
    assert mpbuild.classify_error("getaddrinfo ENOTFOUND api.weixin.qq.com").startswith("出网")
    assert mpbuild.classify_error("certificate verify failed").startswith("域名或证书")
    assert mpbuild.classify_error("Module not found: vue").startswith("编译或上传")
