# tests/test_apk_build.py
"""Android 远程 URL 壳的后台构建（ADR-0042 / 规格 2026-11-05）。

不碰真 Docker：构建容器的执行以 `apkbuild._docker` 替身代替，这里验「入队语义、参数复用、
版本递增、失败收口、工作区产物」。真实 APK 的签名/badging 由 ticket 006 的打包核对覆盖。
"""
import asyncio
import shutil
from pathlib import Path

import pytest
from sqlalchemy import text

from app.db import make_session_factory
from app.deploy import apkbuild, engine as eng_mod, provision, shell, sqlrun
from app.deploy.errors import DeployError
from app.services import app_service

pytestmark = pytest.mark.asyncio

SLUG = "zcapk"
ROOT = Path("D:/__zc_apk_build__")
MOBILE_STACK = "vue3-springboot-mobile"


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


@pytest.fixture
async def make_app(client, test_engine):
    sf = make_session_factory(test_engine)
    ROOT.mkdir(parents=True, exist_ok=True)
    uid = (await client.get("/api/auth/me")).json()["id"]
    created: list[tuple[int, int]] = []

    async def _make(*, stack: str = MOBILE_STACK, status_prod: str = "running") -> int:
        async with sf() as s:
            wid = (await s.execute(text(
                "INSERT INTO workspace (name, root_path, owner_user_id) "
                "VALUES ('apk', :r, :o) RETURNING id"), {"r": str(ROOT), "o": uid})).scalar()
            aid = (await s.execute(text(
                "INSERT INTO app_project (workspace_id, name, slug, scale, stack, prod_port, dev_port, "
                "status_prod, owner_user_id) "
                "VALUES (:w, '打包应用', :sl, 'large', :st, 8210, 8211, :sp, :o) RETURNING id"),
                {"w": wid, "sl": SLUG, "st": stack, "sp": status_prod, "o": uid})).scalar()
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


async def test_current_params_ignores_publish_version(test_engine):
    """壳参数只含包名/名称/启动地址/图标/壳修订号：应用发布版本变化不得让 APK 重出。"""
    app = {"name": "打包应用", "slug": SLUG, "prod_port": 8210,
           "stack": MOBILE_STACK, "current_version": "b20260101000000"}
    a = apkbuild.current_params(app)
    app["current_version"] = "b20260102000000"
    b = apkbuild.current_params(app)
    assert a == b
    assert shell.params_hash(a) == shell.params_hash(b)
    assert a["package"] == "com.zerocode.zcapk"
    assert a["start_url"] == "https://zcapk.zerocode.com/h5/"


async def test_make_workspace_writes_manifest_and_auto_icon(test_engine):
    app = {"id": 1, "name": "打包应用", "slug": SLUG, "prod_port": 8210,
           "stack": MOBILE_STACK}
    params = apkbuild.current_params(app)
    ws = apkbuild._make_workspace(app, params, 7, None)

    manifest = (ws / "AndroidManifest.xml").read_text(encoding="utf-8")
    assert 'package="com.zerocode.zcapk"' in manifest
    assert 'android:versionCode="7"' in manifest and 'android:versionName="1.0.7"' in manifest
    assert 'android:usesCleartextTraffic="false"' in manifest
    assert (ws / "res" / "values" / "strings.xml").read_text(encoding="utf-8").find("打包应用") > 0
    config = (ws / "src" / "com" / "zerocode" / "shell" / "ShellConfig.java").read_text(encoding="utf-8")
    assert 'https://zcapk.zerocode.com/h5/' in config
    assert (ws / "icon.svg").is_file() and not (ws / "icon.png").exists()
    # 构建容器只挂 workspace；build.sh 不进 workspace 就是「cannot open /workspace/build.sh」
    assert (ws / "build.sh").read_bytes() == (
        apkbuild.TEMPLATE_DIR / "build.sh").read_bytes()
    shutil.rmtree(ws, ignore_errors=True)


async def test_make_workspace_uses_uploaded_icon(test_engine):
    app = {"id": 1, "name": "打包应用", "slug": SLUG, "prod_port": 8210,
           "stack": MOBILE_STACK}
    apkbuild.save_icon(SLUG, b"\x89PNG\r\n\x1a\nxx", "png")
    try:
        params = apkbuild.current_params(app)
        ws = apkbuild._make_workspace(app, params, 1, b"\xff\xd8\xffjpg")
        assert (ws / "icon.png").read_bytes() == b"\xff\xd8\xffjpg"
        assert not (ws / "icon.svg").exists()
        shutil.rmtree(ws, ignore_errors=True)
    finally:
        apkbuild.delete_icon(SLUG)


async def test_enqueue_skips_non_mobile(test_engine, make_app):
    sf = make_session_factory(test_engine)
    aid = await make_app(stack="vue3-fastapi")
    b = apkbuild.ApkBuilder(sf)
    assert await b.enqueue(aid) == "skipped"
    assert (await apkbuild.get_state(sf, aid))["status"] == "idle"


async def test_enqueue_defers_when_prod_stopped(test_engine, make_app):
    sf = make_session_factory(test_engine)
    aid = await make_app(status_prod="stopped")
    b = apkbuild.ApkBuilder(sf)
    # 自动路径：不造失败记录，等下一次发布成功再建
    assert await b.enqueue(aid) == "deferred"
    assert (await apkbuild.get_state(sf, aid))["status"] == "idle"
    # 手动路径：端点同步 409
    with pytest.raises(DeployError) as e:
        await b.enqueue(aid, force=True)
    assert e.value.status == 409
    assert (await apkbuild.get_state(sf, aid))["status"] == "idle"


async def test_enqueue_reuses_ready_apk_and_force_rebuilds(test_engine, make_app, monkeypatch):
    sf = make_session_factory(test_engine)
    aid = await make_app()
    app = await _row(sf, aid)
    digest = shell.params_hash(apkbuild.current_params(app))
    async with sf() as s:
        await s.execute(text(
            "INSERT INTO app_mobile_apk (app_id, status, shell_version, params_hash, built_at) "
            "VALUES (:i, 'ready', 2, :h, now())"), {"i": aid, "h": digest})
        await s.commit()

    async def _size(root, slug):
        return 1024

    monkeypatch.setattr(apkbuild, "_apk_size", _size)
    b = apkbuild.ApkBuilder(sf)
    assert await b.enqueue(aid, app=app) == "reused"
    assert (await apkbuild.get_state(sf, aid))["status"] == "ready"

    # force：参数没变也重出包，立刻转 building 并进队列
    assert await b.enqueue(aid, force=True, app=app) == "queued"
    state = await apkbuild.get_state(sf, aid)
    assert state["status"] == "building" and state["shell_version"] == 2
    assert b._queued == {aid}


async def test_enqueue_rebuilds_when_params_stale(test_engine, make_app, monkeypatch):
    sf = make_session_factory(test_engine)
    aid = await make_app()
    app = await _row(sf, aid)
    async with sf() as s:
        await s.execute(text(
            "INSERT INTO app_mobile_apk (app_id, status, shell_version, params_hash) "
            "VALUES (:i, 'ready', 1, 'old-hash')"), {"i": aid})
        await s.commit()
    async def _size(root, slug):
        return 999

    monkeypatch.setattr(apkbuild, "_apk_size", _size)
    b = apkbuild.ApkBuilder(sf)
    assert await b.enqueue(aid, app=app) == "queued"
    assert (await apkbuild.get_state(sf, aid))["status"] == "building"


async def test_enqueue_rebuilds_when_apk_file_missing(test_engine, make_app, monkeypatch):
    """哈希相同但卷里没文件（用户清过卷/换机）→ 不能算 reused，必须重出。"""
    sf = make_session_factory(test_engine)
    aid = await make_app()
    app = await _row(sf, aid)
    digest = shell.params_hash(apkbuild.current_params(app))
    async with sf() as s:
        await s.execute(text(
            "INSERT INTO app_mobile_apk (app_id, status, shell_version, params_hash) "
            "VALUES (:i, 'ready', 1, :h)"), {"i": aid, "h": digest})
        await s.commit()

    async def _no_size(root, slug):
        return None

    monkeypatch.setattr(apkbuild, "_apk_size", _no_size)
    b = apkbuild.ApkBuilder(sf)
    assert await b.enqueue(aid, app=app) == "queued"


async def test_enqueue_dedupes_and_reruns_force(test_engine, make_app):
    sf = make_session_factory(test_engine)
    aid = await make_app()
    app = await _row(sf, aid)
    b = apkbuild.ApkBuilder(sf)
    assert await b.enqueue(aid, app=app) == "queued"
    # 已在队列：普通请求折叠；force 记补跑，本轮结束后再跑一次
    assert await b.enqueue(aid, app=app) == "building"
    assert b._rerun == set()
    assert await b.enqueue(aid, force=True, app=app) == "building"
    assert b._rerun == {aid}


async def test_recover_marks_interrupted_builds_failed(test_engine, make_app):
    sf = make_session_factory(test_engine)
    aid = await make_app()
    async with sf() as s:
        await s.execute(text(
            "INSERT INTO app_mobile_apk (app_id, status) VALUES (:i, 'building')"), {"i": aid})
        await s.commit()
    await apkbuild.ApkBuilder(sf).recover()
    state = await apkbuild.get_state(sf, aid)
    assert state["status"] == "failed"
    assert "平台重启" in state["error"]


async def test_recover_tolerates_missing_table_on_first_boot():
    """升级/首装镜像里没有 changelog，新表要等 apply-init.sh；启动不得因此失败。"""
    from sqlalchemy.exc import ProgrammingError

    class _Sess:
        async def execute(self, *a, **k):
            raise ProgrammingError("UPDATE app_mobile_apk", {},
                                   type("Orig", (), {"sqlstate": "42P01"})())

        async def commit(self):
            pass

    class _SF:
        def __call__(self):
            class _Ctx:
                async def __aenter__(self):
                    return _Sess()

                async def __aexit__(self, *a):
                    return False
            return _Ctx()

    await apkbuild.ApkBuilder(_SF()).recover()   # 不抛即通过


async def test_recover_does_not_swallow_other_db_errors():
    from sqlalchemy.exc import ProgrammingError

    class _Sess:
        async def execute(self, *a, **k):
            raise ProgrammingError("UPDATE app_mobile_apk", {},
                                   type("Orig", (), {"sqlstate": "42601"})())

    class _SF:
        def __call__(self):
            class _Ctx:
                async def __aenter__(self):
                    return _Sess()

                async def __aexit__(self, *a):
                    return False
            return _Ctx()

    with pytest.raises(ProgrammingError):
        await apkbuild.ApkBuilder(_SF()).recover()


async def test_build_success_increments_shell_version_and_saves(test_engine, make_app, monkeypatch):
    sf = make_session_factory(test_engine)
    aid = await make_app()
    saved: list[bytes] = []
    payload = b"PK\x03\x04built-apk"

    def fake_make_workspace(app, params, version, custom_icon):
        ws = apkbuild.build_workspace(app["slug"])
        shutil.rmtree(ws, ignore_errors=True)
        (ws / "out").mkdir(parents=True)
        (ws / "out" / "app.apk").write_bytes(payload)
        return ws

    async def fake_save(root, slug, stream):
        data = b""
        while chunk := await stream.read(1024):
            data += chunk
        saved.append(data)
        return len(data)

    async def ok_docker(*args, cwd=None, timeout=600):
        return 0, "ok"

    def ok_keys():
        return Path("D:/__zc_apk_build__/keys")

    async def ok_image():
        return None

    monkeypatch.setattr(apkbuild, "_make_workspace", fake_make_workspace)
    monkeypatch.setattr(apkbuild, "_save", fake_save)
    monkeypatch.setattr(apkbuild, "_docker", ok_docker)
    monkeypatch.setattr(apkbuild, "prepare_keys", ok_keys)
    monkeypatch.setattr(apkbuild, "_ensure_image", ok_image)

    await apkbuild._build(sf, aid)
    state = await apkbuild.get_state(sf, aid)
    assert state["status"] == "ready"
    assert state["shell_version"] == 1
    assert state["apk_size"] == len(payload)
    assert saved == [payload]
    app = await _row(sf, aid)
    assert state["params_hash"] == shell.params_hash(apkbuild.current_params(app))

    # 第二次构建：版本号继续递增（同一包名下的升级路径）
    await apkbuild._build(sf, aid)
    assert (await apkbuild.get_state(sf, aid))["shell_version"] == 2
    assert saved == [payload, payload]


async def test_build_rejects_prod_not_running(test_engine, make_app):
    sf = make_session_factory(test_engine)
    aid = await make_app(status_prod="stopped")
    with pytest.raises(DeployError) as e:
        await apkbuild._build(sf, aid)
    assert "正式环境未运行" in str(e.value)


async def test_build_fails_when_builder_produces_no_apk(test_engine, make_app, monkeypatch):
    sf = make_session_factory(test_engine)
    aid = await make_app()

    def empty_workspace(app, params, version, custom_icon):
        ws = apkbuild.build_workspace(app["slug"])
        shutil.rmtree(ws, ignore_errors=True)
        ws.mkdir(parents=True)
        return ws

    async def ok_docker(*args, cwd=None, timeout=600):
        return 0, "built but nothing to show"

    def ok_keys():
        return Path(".")

    async def ok_image():
        return None

    monkeypatch.setattr(apkbuild, "_make_workspace", empty_workspace)
    monkeypatch.setattr(apkbuild, "_docker", ok_docker)
    monkeypatch.setattr(apkbuild, "prepare_keys", ok_keys)
    monkeypatch.setattr(apkbuild, "_ensure_image", ok_image)

    with pytest.raises(DeployError) as e:
        await apkbuild._build(sf, aid)
    assert "没有产出" in str(e.value)


async def test_loop_marks_failed_and_keeps_running(test_engine, make_app, monkeypatch):
    sf = make_session_factory(test_engine)
    aid = await make_app()

    async def boom(sf_, app_id):
        raise DeployError("构建炸了")

    monkeypatch.setattr(apkbuild, "_build", boom)
    b = apkbuild.ApkBuilder(sf)
    b._task = asyncio.create_task(b._loop(), name="apk-builder-test")
    try:
        await b._queue.put(aid)
        await asyncio.wait_for(b._queue.join(), 5)
        state = await apkbuild.get_state(sf, aid)
        assert state["status"] == "failed" and "构建炸了" in state["error"]
    finally:
        await b.close()


async def test_cleanup_app_removes_platform_side_files(test_engine):
    apkbuild.save_icon(SLUG, b"\x89PNG\r\n\x1a\nx", "png")
    ws = apkbuild.build_workspace(SLUG)
    ws.mkdir(parents=True)
    (ws / "x").write_text("x", encoding="utf-8")
    apkbuild.cleanup_app(SLUG)
    assert apkbuild.custom_icon_path(SLUG) is None
    assert not ws.exists()
