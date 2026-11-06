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
from app.deploy import apkbuild, engine as eng_mod, mobile, ports, provision, shell, sqlrun
from app.services import app_service

pytestmark = pytest.mark.asyncio

SLUG = "zcmob"
ROOT = Path("D:/__zc_mobile_api__")
MOBILE_STACK = "vue3-springboot-mobile"
PNG = b"\x89PNG\r\n\x1a\n" + b"fake-png-body"


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


@pytest.fixture
async def make_app(client, test_engine):
    """工厂：在测试库造应用行（workspace + app_project），端口由当前登记状态推导。"""
    sf = make_session_factory(test_engine)
    ROOT.mkdir(parents=True, exist_ok=True)
    uid = (await client.get("/api/auth/me")).json()["id"]
    created: list[tuple[int, int]] = []

    async def _make(*, stack: str = MOBILE_STACK, status_prod: str = "running") -> dict:
        async with sf() as s:
            prod_port, dev_port = await ports.allocate_pair(s)
            wid = (await s.execute(text(
                "INSERT INTO workspace (name, root_path, owner_user_id) "
                "VALUES ('mob', :r, :o) RETURNING id"), {"r": str(ROOT), "o": uid})).scalar()
            aid = (await s.execute(text(
                "INSERT INTO app_project (workspace_id, name, slug, scale, stack, prod_port, dev_port, "
                "status_prod, owner_user_id) "
                "VALUES (:w, 'mob', :sl, 'large', :st, :pp, :dp, :sp, :o) RETURNING id"),
                {"w": wid, "sl": SLUG, "st": stack, "pp": prod_port, "dp": dev_port,
                 "sp": status_prod, "o": uid})).scalar()
            await s.commit()
        created.append((aid, wid))
        return {"id": aid, "slug": SLUG, "prod_port": prod_port, "dev_port": dev_port}

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
