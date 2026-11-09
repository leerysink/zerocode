# tests/test_mobile_api.py
"""移动端分发 REST（规格 2026-11-03 H5 + 规格 2026-11-05 APK 壳 / ADR-0042）。

APK 从 2026-11-05 起由平台构建，本文件只验 REST 语义：状态/参数过期/图标读写/手动重建闸门，
以及构建产物落卷（`mobile._save`）的原子性。真实构建由 tests/test_apk_build.py 与手工验收覆盖。
"""
import re
import shutil
from pathlib import Path

import pytest
from sqlalchemy import text

from app.db import make_session_factory
from app.deploy import (apkbuild, engine as eng_mod, mobile, mpbuild, mpcreds, mpruntime, ports,
                        provision, shell, sqlrun)
from app.services import app_service

pytestmark = pytest.mark.asyncio

SLUG = "zcmob"
ROOT = Path("D:/__zc_mobile_api__")
MOBILE_STACK = "vue3-springboot-mobile"
PNG = b"\x89PNG\r\n\x1a\n" + b"fake-png-body"
MP_APPID = "wx1234567890abcdef"
MP_KEY = b"-----BEGIN PRIVATE KEY-----\nMIIEvfake\n-----END PRIVATE KEY-----\n"



@pytest.fixture(autouse=True)
def _no_startup_docker(monkeypatch):
    """与 test_apps_dev_api 同口径：lifespan 的部署侧准备收成替身，本文件不碰真 Docker。"""
    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(eng_mod, "cleanup_orphans", _noop)
    monkeypatch.setattr(eng_mod, "stop_all_dev_on_exit", _noop)
    monkeypatch.setattr(sqlrun, "build_if_missing", _noop)
    monkeypatch.setattr(provision, "ensure_zcnet", _noop)


@pytest.fixture(autouse=True)
def _isolate_data_dir(monkeypatch, tmp_path):
    """图标/构建工作区落盘重定向到 tmp：不能污染仓库 data/，也不受别的用例残留影响。"""
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path / "data"))


@pytest.fixture(autouse=True)
def _readiness_env(monkeypatch):
    """GET /mobile 会带出网 IP：固定 env，探测一次也不打真外网。"""
    monkeypatch.setenv("ZEROCODE_EGRESS_IP", "203.0.113.9")
    mpruntime._cache.update(ip="", at=None)


@pytest.fixture
async def make_app(client, test_engine):
    """工厂：在测试库造应用行（workspace + app_project），端口由当前登记状态推导。"""
    sf = make_session_factory(test_engine)
    ROOT.mkdir(parents=True, exist_ok=True)
    uid = (await client.get("/api/auth/me")).json()["id"]
    created: list[tuple[int, int]] = []

    async def _make(*, stack: str = MOBILE_STACK, status_prod: str = "running",
                    slug: str = SLUG) -> dict:
        async with sf() as s:
            prod_port, dev_port = await ports.allocate_pair(s)
            root = ROOT if slug == SLUG else ROOT / slug
            root.mkdir(parents=True, exist_ok=True)
            wid = (await s.execute(text(
                "INSERT INTO workspace (name, root_path, owner_user_id) "
                "VALUES ('mob', :r, :o) RETURNING id"), {"r": str(root), "o": uid})).scalar()
            aid = (await s.execute(text(
                "INSERT INTO app_project (workspace_id, name, slug, scale, stack, prod_port, dev_port, "
                "status_prod, owner_user_id) "
                "VALUES (:w, 'mob', :sl, 'large', :st, :pp, :dp, :sp, :o) RETURNING id"),
                {"w": wid, "sl": slug, "st": stack, "pp": prod_port, "dp": dev_port,
                 "sp": status_prod, "o": uid})).scalar()
            await s.commit()
        created.append((aid, wid))
        return {"id": aid, "slug": slug, "prod_port": prod_port, "dev_port": dev_port}

    yield _make
    async with sf() as s:
        for aid, wid in created:
            await s.execute(text("DELETE FROM app_project WHERE id=:i"), {"i": aid})
            await s.execute(text("DELETE FROM workspace WHERE id=:i"), {"i": wid})
        await s.commit()
    shutil.rmtree(ROOT, ignore_errors=True)


class _FakeBuilder:
    """替身：只记录入队语义；真正的串行/复用逻辑由 tests/test_apk_build.py 覆盖。"""

    def __init__(self, result="queued"):
        self.result = result
        self.calls: list[dict] = []

    async def enqueue(self, app_id, *, force=False, app=None):
        self.calls.append({"app_id": app_id, "force": force, "app": app})
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


@pytest.fixture
def fake_builder(client, monkeypatch):
    """替换真实 worker 的 enqueue（保留实例，lifespan 关闭路径不受影响）。"""
    fb = _FakeBuilder()
    monkeypatch.setattr(client.app.state.apk_builder, "enqueue", fb.enqueue)
    return fb


class _FakeMpBuilder:
    def __init__(self, result="queued"):
        self.result = result
        self.calls: list[dict] = []

    async def enqueue(self, app_id, *, kind="upload", force=False, app=None):
        self.calls.append({"app_id": app_id, "kind": kind, "force": force, "app": app})
        return self.result


@pytest.fixture(autouse=True)
def mp_builder_fake(client, monkeypatch):
    """绑定/改名会触发预检入队：本文件只验 REST，真正构建由 test_mp_build 覆盖。"""
    fb = _FakeMpBuilder()
    monkeypatch.setattr(client.app.state.mp_builder, "enqueue", fb.enqueue)
    return fb


def _fake_docker(calls: list[dict], *, code: int = 0, out: str = ""):
    """替身：把入参与灌流内容记下来；feed 读完才返回，模拟真实管道的背压。"""
    async def _f(*args, cwd=None, timeout=600, feed=None):
        data = b""
        if feed is not None:
            while chunk := await feed.read(1024 * 1024):
                data += chunk
        calls.append({"args": args, "cwd": cwd, "feed": None if feed is None else data,
                      "timeout": timeout})
        return code, out
    return _f


async def test_prod_url_shapes(monkeypatch):
    """地址投影跟 `nics.app_url` 单点走：SSL 开域名、关则 PREVIEW_HOST:prod_port。"""
    app = {"slug": SLUG, "stack": MOBILE_STACK, "prod_port": 8210, "dev_port": 8211}
    monkeypatch.delenv("ZEROCODE_SSL_ENABLE", raising=False)
    assert mobile.h5_url(app) == f"https://{SLUG}.zerocode.com/h5/"
    assert mobile.apk_url(app) == f"https://{SLUG}.zerocode.com/api/uploads/mobile/{SLUG}.apk"

    monkeypatch.setenv("ZEROCODE_SSL_ENABLE", "false")
    monkeypatch.setenv("ZEROCODE_PREVIEW_HOST", "192.168.1.5")
    assert mobile.h5_url(app) == "http://192.168.1.5:8210/h5/"
    assert mobile.apk_url(app) == f"http://192.168.1.5:8210/api/uploads/mobile/{SLUG}.apk"


async def test_info_reports_build_state_and_icon(client, make_app, monkeypatch):
    app = await make_app()
    monkeypatch.setattr(mobile, "_apk_size", _fake_size(1234567))
    monkeypatch.delenv("ZEROCODE_SSL_ENABLE", raising=False)

    r = await client.get(f"/api/apps/{app['id']}/mobile")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status_dev"] == "stopped"
    assert body["status_prod"] == "running"
    assert body["h5_url"] == f"https://{SLUG}.zerocode.com/h5/"
    assert body["apk_url"] == f"https://{SLUG}.zerocode.com/api/uploads/mobile/{SLUG}.apk"
    assert body["dev_url"].endswith(f":{app['dev_port']}/h5/")
    assert body["apk_size"] == 1234567
    assert body["apk_max_bytes"] == 200 * 1024 * 1024
    # 还没构建过：无行 = idle，参数自然算 stale，允许手动重建
    assert body["apk"]["status"] == "idle"
    assert body["apk"]["shell_version"] == 0
    assert body["apk"]["params_stale"] is True
    assert body["apk"]["can_rebuild"] is True
    assert body["apk"]["package_name"] == "com.zerocode.zcmob"
    assert body["apk"]["built_at"] is None
    assert body["icon"]["custom"] is False
    assert body["icon"]["url"].startswith(f"/api/apps/{app['id']}/mobile/icon?v=")


async def test_info_params_stale_false_when_hash_matches(client, make_app, test_engine, monkeypatch):
    """params_stale 是「卷里 APK 是否还代表当前壳参数」的唯一信号：哈希相等才为 false。"""
    app = await make_app()
    monkeypatch.setattr(mobile, "_apk_size", _fake_size(99))
    monkeypatch.delenv("ZEROCODE_SSL_ENABLE", raising=False)
    sf = make_session_factory(test_engine)
    row = await app_service.get_app(sf, app["id"])
    digest = shell.params_hash(apkbuild.current_params(row))
    async with sf() as s:
        await s.execute(text(
            "INSERT INTO app_mobile_apk (app_id, status, shell_version, params_hash, built_at) "
            "VALUES (:i, 'ready', 3, :h, now())"), {"i": app["id"], "h": digest})
        await s.commit()

    body = (await client.get(f"/api/apps/{app['id']}/mobile")).json()
    assert body["apk"]["status"] == "ready"
    assert body["apk"]["shell_version"] == 3
    assert body["apk"]["params_stale"] is False


async def test_info_stopped_app_does_not_probe(client, make_app, monkeypatch):
    app = await make_app(status_prod="stopped")
    calls: list[tuple] = []

    async def _probe(root, slug):
        calls.append((root, slug))
        return 1

    monkeypatch.setattr(mobile, "_apk_size", _probe)

    r = await client.get(f"/api/apps/{app['id']}/mobile")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["apk_size"] is None
    assert body["apk"]["can_rebuild"] is False
    assert calls == []          # 容器不在：不 exec 探文件


async def test_info_rejects_non_mobile_stack(client, make_app):
    app = await make_app(stack="vue3-fastapi")
    r = await client.get(f"/api/apps/{app['id']}/mobile")
    assert r.status_code == 400


async def test_rebuild_enqueues_force(client, make_app, fake_builder):
    app = await make_app()
    r = await client.post(f"/api/apps/{app['id']}/mobile/apk/rebuild")
    assert r.status_code == 202, r.text
    assert r.json() == {"ok": True, "status": "building", "deduped": False}
    assert len(fake_builder.calls) == 1
    assert fake_builder.calls[0]["app_id"] == app["id"]
    assert fake_builder.calls[0]["force"] is True


async def test_rebuild_deduped_while_building(client, make_app, fake_builder):
    """构建中再点重建：端点仍 202，但 deduped=true（worker 在本轮后补跑一次）。"""
    app = await make_app()
    fake_builder.result = "building"
    r = await client.post(f"/api/apps/{app['id']}/mobile/apk/rebuild")
    assert r.status_code == 202
    assert r.json()["deduped"] is True


async def test_rebuild_blocked_when_prod_not_running(client, make_app, fake_builder):
    app = await make_app(status_prod="stopped")
    r = await client.post(f"/api/apps/{app['id']}/mobile/apk/rebuild")
    assert r.status_code == 409 and "正式环境未运行" in r.json()["detail"]
    assert fake_builder.calls == []


async def test_rebuild_rejects_non_mobile_stack(client, make_app, fake_builder):
    app = await make_app(stack="vue3-fastapi")
    r = await client.post(f"/api/apps/{app['id']}/mobile/apk/rebuild")
    assert r.status_code == 400
    assert fake_builder.calls == []


async def test_icon_defaults_to_generated_svg(client, make_app):
    app = await make_app()
    r = await client.get(f"/api/apps/{app['id']}/mobile/icon")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/svg+xml")
    assert r.headers["cache-control"] == "no-store"
    assert r.text.startswith("<svg") and ">M</text>" in r.text   # 名称 'mob' 的首字符


async def test_icon_upload_overrides_then_reset(client, make_app, fake_builder):
    app = await make_app()
    base = f"/api/apps/{app['id']}/mobile"

    r = await client.put(f"{base}/icon", files={"file": ("logo.png", PNG, "image/png")})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "build": "queued"}
    assert len(fake_builder.calls) == 1
    assert fake_builder.calls[0]["force"] is False

    got = await client.get(f"{base}/icon")
    assert got.status_code == 200
    assert got.headers["content-type"].startswith("image/png")
    assert got.content == PNG

    info = (await client.get(base)).json()
    assert info["icon"]["custom"] is True
    assert info["icon"]["updated_at"] is not None
    assert info["apk"]["params_stale"] is True   # 图标换了：现有包已过期

    # 恢复自动图标：删掉覆盖文件并再次入队
    r = await client.delete(f"{base}/icon")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "removed": True, "build": "queued"}
    assert len(fake_builder.calls) == 2
    assert fake_builder.calls[1]["force"] is False
    assert (await client.get(f"{base}/icon")).headers["content-type"].startswith("image/svg+xml")


async def test_icon_reset_without_custom_is_not_an_error(client, make_app, fake_builder):
    app = await make_app()
    r = await client.delete(f"/api/apps/{app['id']}/mobile/icon")
    assert r.status_code == 200
    assert r.json()["removed"] is False


async def test_icon_upload_defers_when_prod_stopped(client, make_app, fake_builder):
    """图标先落盘；prod 没跑就让 enqueue 自己判 deferred，端点不额外挡（下次发布自动重建）。"""
    app = await make_app(status_prod="stopped")
    fake_builder.result = "deferred"
    r = await client.put(f"/api/apps/{app['id']}/mobile/icon",
                         files={"file": ("logo.png", PNG, "image/png")})
    assert r.status_code == 200
    assert r.json()["build"] == "deferred"
    assert apkbuild.custom_icon_path(SLUG) is not None


async def test_icon_upload_rejects_empty(client, make_app, fake_builder):
    app = await make_app()
    r = await client.put(f"/api/apps/{app['id']}/mobile/icon",
                         files={"file": ("logo.png", b"", "image/png")})
    assert r.status_code == 400 and "为空" in r.json()["detail"]
    assert fake_builder.calls == []


async def test_icon_upload_rejects_wrong_magic(client, make_app, fake_builder):
    app = await make_app()
    r = await client.put(f"/api/apps/{app['id']}/mobile/icon",
                         files={"file": ("logo.png", b"GIF89a...", "image/png")})
    assert r.status_code == 400 and "PNG/JPEG" in r.json()["detail"]
    assert fake_builder.calls == []


async def test_icon_upload_rejects_oversize(client, make_app, fake_builder, monkeypatch):
    app = await make_app()
    monkeypatch.setattr(shell, "MOBILE_ICON_MAX_BYTES", 4)
    r = await client.put(f"/api/apps/{app['id']}/mobile/icon",
                         files={"file": ("logo.png", PNG, "image/png")})
    assert r.status_code == 413
    assert fake_builder.calls == []
    assert apkbuild.custom_icon_path(SLUG) is None


async def test_icon_endpoints_reject_non_mobile_stack(client, make_app, fake_builder):
    app = await make_app(stack="vue3-springboot")
    base = f"/api/apps/{app['id']}/mobile/icon"
    assert (await client.get(base)).status_code == 400
    assert (await client.put(base, files={"file": ("a.png", PNG, "image/png")})).status_code == 400
    assert (await client.delete(base)).status_code == 400


async def test_manual_apk_upload_endpoint_removed(client, make_app):
    """ADR-0042：手工上传入口已移除。404/405 都算「打不到」——SPA 静态挂载会把未命中的
    POST 变成 405，关键是绝不能继续写卷。"""
    app = await make_app()
    r = await client.post(f"/api/apps/{app['id']}/mobile/apk",
                          files={"file": ("demo.apk", b"x")})
    assert r.status_code in (404, 405)


class _File:
    def __init__(self, payload: bytes):
        self._payload = payload
        self._done = False

    async def read(self, n: int) -> bytes:
        if self._done:
            return b""
        self._done = True
        return self._payload


async def test_save_writes_into_uploads_volume(monkeypatch):
    """构建产物落卷：先隐藏 .part 灌流完整，再单独 mv 转正（旧 APK 全程可下载）。"""
    calls: list[dict] = []
    monkeypatch.setattr(mobile, "_docker", _fake_docker(calls, out="ok"))

    payload = b"PK\x03\x04fake-apk-bytes"
    total = await mobile._save(ROOT, SLUG, _File(payload))
    assert total == len(payload)

    assert len(calls) == 2          # ① 灌进 .part ② 单独 mv 转正
    stage, promote = calls
    for call in calls:
        args = call["args"]
        assert args[3:6] == ("exec", "-T", "backend")
        assert args[6] == "sh" and args[7] == "-c"
        assert Path(args[2]) == ROOT / "deploy" / "prod-compose.yml"
    m = re.fullmatch(rf"mkdir -p /uploads/mobile && cat > (/uploads/mobile/\.{SLUG}\.apk\.[0-9a-f]{{32}}\.part)",
                     stage["args"][8])
    assert m
    assert stage["feed"] == payload
    assert promote["args"][8] == f"mv {m.group(1)} /uploads/mobile/{SLUG}.apk"
    assert promote["feed"] is None   # 转正不灌流


async def test_save_rejects_empty_stream_without_promote(monkeypatch):
    """声明体积缺失（size=None）时的兜底：灌流为 0 字节，同样不得 mv 转正。"""
    calls: list[dict] = []

    class _Empty:
        async def read(self, n):
            return b""

    monkeypatch.setattr(mobile, "_docker", _fake_docker(calls))
    with pytest.raises(Exception) as e:
        await mobile._save(ROOT, SLUG, _Empty())
    assert getattr(e.value, "status", None) == 400
    assert len(calls) == 1          # 只有写入 .part 那一步，没有 promote


async def test_save_promote_failure_keeps_old_apk(monkeypatch):
    """转正失败 → 503：旧 APK 原样可下载，残留只是隐藏 .part（下一次上传覆盖）。"""
    results = [(0, "ok"), (1, "mv: cannot stat '/uploads/mobile/.zcmob.apk.part'")]
    calls: list[dict] = []

    async def _f(*args, cwd=None, timeout=600, feed=None):
        if feed is not None:
            while await feed.read(1024 * 1024):
                pass
        calls.append({"args": args, "cwd": cwd, "feed": b"", "timeout": timeout})
        return results.pop(0)

    monkeypatch.setattr(mobile, "_docker", _f)
    with pytest.raises(Exception) as e:
        await mobile._save(ROOT, SLUG, _File(b"x"))
    assert getattr(e.value, "status", None) == 503
    assert len(calls) == 2


async def test_capped_reader_aborts_midstream():
    """声明体积缺失时的兜底：读满上限立即抛 413，超出部分不得进管道。"""
    class _Big:
        def __init__(self):
            self.left = 3

        async def read(self, n):
            if not self.left:
                return b""
            self.left -= 1
            return b"xxxxx"

    reader = mobile._CappedReader(_Big(), limit=8)
    await reader.read(1024)
    with pytest.raises(Exception) as e:
        await reader.read(1024)
    assert getattr(e.value, "status", None) == 413


def _fake_size(value):
    async def _f(root, slug):
        return value
    return _f


# ---------- 小程序凭据（规格 2026-11-09 / ADR-0043~0045） ----------

def _mp_base(app_id: int) -> str:
    return f"/api/apps/{app_id}/mobile/miniprogram/credentials"


async def _bind_mp(client, app_id: int, *, appid: str = MP_APPID, key: bytes = MP_KEY):
    return await client.put(
        _mp_base(app_id), data={"appid": appid},
        files={"file": ("private.key", key, "application/octet-stream")})


async def test_miniprogram_bind_masks_and_hides_key(client, make_app, mp_builder_fake):
    """绑定后只暴露脱敏 AppID 与上传时间；私钥永不回显（ADR-0045）。"""
    app = await make_app()
    r = await _bind_mp(client, app["id"])
    assert r.status_code == 200, r.text
    assert r.json()["appid_masked"] == "wx12****cdef"
    assert b"PRIVATE KEY" not in r.content
    # 绑定即触发一次异步预检（编译 + preview，不产开发版）
    assert [(c["app_id"], c["kind"]) for c in mp_builder_fake.calls] == [(app["id"], "precheck")]

    got = await client.get(f"/api/apps/{app['id']}/mobile")
    assert got.status_code == 200, got.text
    mp = got.json()["miniprogram"]
    assert mp["bound"] is True
    assert mp["appid_masked"] == "wx12****cdef"
    assert mp["key_uploaded_at"]
    assert mp["status"] == "idle"
    assert b"PRIVATE KEY" not in got.content
    assert mpcreds.key_path(SLUG).is_file()


async def test_miniprogram_unbind_clears_row_and_key(client, make_app):
    app = await make_app()
    assert (await _bind_mp(client, app["id"])).status_code == 200

    r = await client.delete(_mp_base(app["id"]))
    assert r.status_code == 200, r.text
    assert not mpcreds.key_path(SLUG).exists()
    mp = (await client.get(f"/api/apps/{app['id']}/mobile")).json()["miniprogram"]
    assert mp["bound"] is False and mp["appid_masked"] == ""
    assert mp["status"] == "idle"


async def test_miniprogram_appid_is_globally_unique(client, make_app):
    """一应用一账号（ADR-0045）：AppID 被别的应用占用时 409，且不破坏原绑定的密钥。"""
    first = await make_app()
    second = await make_app(slug="zcmob2")
    assert (await _bind_mp(client, first["id"])).status_code == 200

    r = await _bind_mp(client, second["id"])
    assert r.status_code == 409 and "已绑定" in r.json()["detail"]
    assert mpcreds.key_path("zcmob2").exists() is False
    assert mpcreds.key_path(SLUG).is_file()
    assert (await client.get(f"/api/apps/{first['id']}/mobile")).json()["miniprogram"]["bound"] is True


async def test_miniprogram_bind_rejects_bad_input(client, make_app, monkeypatch):
    app = await make_app()
    base = _mp_base(app["id"])

    r = await _bind_mp(client, app["id"], appid="wx123")
    assert r.status_code == 400 and "AppID" in r.json()["detail"]

    r = await _bind_mp(client, app["id"], key=b"not a key")
    assert r.status_code == 400 and "密钥" in r.json()["detail"]

    r = await client.put(base, data={"appid": MP_APPID},
                         files={"file": ("private.key", b"", "application/octet-stream")})
    assert r.status_code == 400 and "为空" in r.json()["detail"]

    monkeypatch.setattr(mpcreds, "KEY_MAX_BYTES", 8)
    r = await _bind_mp(client, app["id"])
    assert r.status_code == 413
    assert (await client.get(f"/api/apps/{app['id']}/mobile")).json()["miniprogram"]["bound"] is False


async def test_miniprogram_endpoints_reject_non_mobile_stack(client, make_app):
    app = await make_app(stack="vue3-springboot")
    assert (await _bind_mp(client, app["id"])).status_code == 400
    assert (await client.delete(_mp_base(app["id"]))).status_code == 400


def _mp_preview(app_id: int) -> str:
    return f"/api/apps/{app_id}/mobile/miniprogram/preview"


def _mp_upload(app_id: int) -> str:
    return f"/api/apps/{app_id}/mobile/miniprogram/upload"


async def test_miniprogram_upload_forces_and_dedupes(client, make_app, mp_builder_fake):
    """手动重传：force=True 覆盖开发版；进行中再点返回 deduped，不并行。"""
    app = await make_app()
    assert (await _bind_mp(client, app["id"])).status_code == 200
    mp_builder_fake.calls.clear()

    r = await client.post(_mp_upload(app["id"]))
    assert r.status_code == 202, r.text
    assert r.json() == {"ok": True, "status": "building", "deduped": False}
    call = mp_builder_fake.calls[0]
    assert (call["app_id"], call["kind"], call["force"]) == (app["id"], "upload", True)

    mp_builder_fake.result = "building"
    r = await client.post(_mp_upload(app["id"]))
    assert r.status_code == 202 and r.json()["deduped"] is True


async def test_miniprogram_upload_gates(client, make_app, mp_builder_fake):
    """非移动栈 400；未绑定 409（先绑定）；prod 未运行 409（先发布）——都不产生任务。"""
    plain = await make_app(stack="vue3-springboot")
    assert (await client.post(_mp_upload(plain["id"]))).status_code == 400

    unbound = await make_app(slug="zcmob2")
    r = await client.post(_mp_upload(unbound["id"]))
    assert r.status_code == 409 and "绑定" in r.json()["detail"]

    stopped = await make_app(status_prod="stopped", slug="zcmob3")
    assert (await _bind_mp(client, stopped["id"], appid="wxabcdef1234567890")).status_code == 200
    mp_builder_fake.calls.clear()
    r = await client.post(_mp_upload(stopped["id"]))
    assert r.status_code == 409 and "正式环境未运行" in r.json()["detail"]
    assert mp_builder_fake.calls == []


async def test_miniprogram_preview_enqueues_and_dedupes(client, make_app, mp_builder_fake):
    """按需生成预览码：202 入队；任务进行中再点仍 202，但 deduped=true（不并行）。"""
    app = await make_app()
    assert (await _bind_mp(client, app["id"])).status_code == 200
    mp_builder_fake.calls.clear()

    r = await client.post(_mp_preview(app["id"]))
    assert r.status_code == 202, r.text
    assert r.json() == {"ok": True, "status": "building", "deduped": False}
    assert [(c["app_id"], c["kind"]) for c in mp_builder_fake.calls] == [(app["id"], "preview")]

    mp_builder_fake.result = "building"
    r = await client.post(_mp_preview(app["id"]))
    assert r.status_code == 202
    assert r.json()["deduped"] is True


async def test_miniprogram_preview_requires_binding_and_mobile(client, make_app, mp_builder_fake):
    plain = await make_app(stack="vue3-springboot")
    assert (await client.post(_mp_preview(plain["id"]))).status_code == 400
    assert (await client.get(_mp_preview(plain["id"]))).status_code == 400

    app = await make_app(slug="zcmob2")
    r = await client.post(_mp_preview(app["id"]))
    assert r.status_code == 409 and "绑定" in r.json()["detail"]
    assert mp_builder_fake.calls == []


async def test_miniprogram_preview_image_needs_auth(client):
    """同源 <img> 也走全局 guard：未登录不得读到预览码。

    本文件 autouse 的 mp_builder_fake 依赖 client（会把与会话灌进同一个 anon_client），
    故这里另建一个不带 cookie 的裸 client。"""
    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(transport=ASGITransport(app=client.app), base_url="http://t") as anon:
        r = await anon.get("/api/apps/1/mobile/miniprogram/preview")
    assert r.status_code == 401


async def test_miniprogram_preview_image_exists(client, make_app):
    app = await make_app()
    assert (await client.get(_mp_preview(app["id"]))).status_code == 404

    path = mpbuild.preview_path(SLUG)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xff\xd8\xffpreview-jpeg")
    assert "uploads" not in path.parts          # 码不进任何公开目录

    r = await client.get(_mp_preview(app["id"]))
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"
    assert r.headers["cache-control"] == "no-store"
    assert r.content == b"\xff\xd8\xffpreview-jpeg"

    info = (await client.get(f"/api/apps/{app['id']}/mobile")).json()["miniprogram"]
    assert info["preview_available"] is True


async def test_miniprogram_preview_available_false_without_file(client, make_app):
    app = await make_app()
    info = (await client.get(f"/api/apps/{app['id']}/mobile")).json()["miniprogram"]
    assert info["preview_available"] is False


async def test_miniprogram_readiness_and_guidance_shape(client, make_app, monkeypatch):
    """弹框「为什么还不能用」的数据源：readiness 原因 + 域名/出网 IP/三步指引键。"""
    monkeypatch.delenv("ZEROCODE_SSL_ENABLE", raising=False)
    app = await make_app()
    assert (await _bind_mp(client, app["id"])).status_code == 200
    mp = (await client.get(f"/api/apps/{app['id']}/mobile")).json()["miniprogram"]
    domain = f"https://{SLUG}.zerocode.com"
    assert mp["readiness"] == {"ok": True, "reasons": [], "request_domain": domain,
                               "egress_ip": "203.0.113.9"}
    assert mp["guidance"] == {
        "request_domain": domain,
        "egress_ip": "203.0.113.9",
        "console_url": "https://mp.weixin.qq.com/",
        "steps": ["dev_version", "trial_version", "submit_review"],
    }

    monkeypatch.setenv("ZEROCODE_SSL_ENABLE", "false")
    mp = (await client.get(f"/api/apps/{app['id']}/mobile")).json()["miniprogram"]
    assert mp["readiness"]["ok"] is False
    assert mp["readiness"]["reasons"] == ["ssl_off"]
    assert mp["guidance"]["request_domain"] == ""


async def test_miniprogram_credentials_cleaned_on_app_delete(client, make_app, monkeypatch):
    async def _noop(*args, **kwargs):
        return []

    monkeypatch.setattr(eng_mod, "down_app_stacks", _noop)
    app = await make_app()
    assert (await _bind_mp(client, app["id"])).status_code == 200
    assert mpcreds.key_path(SLUG).is_file()
    qr = mpbuild.preview_path(SLUG)
    qr.parent.mkdir(parents=True, exist_ok=True)
    qr.write_bytes(b"qr")
    mpbuild.workspace(SLUG).mkdir(parents=True, exist_ok=True)

    r = await client.delete(f"/api/apps/{app['id']}")
    assert r.status_code == 200, r.text
    assert not (mpcreds.keys_dir() / SLUG).exists()
    assert not qr.exists() and not mpbuild.workspace(SLUG).exists()

