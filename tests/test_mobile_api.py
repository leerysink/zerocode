# tests/test_mobile_api.py
"""移动端分发 REST（规格 2026-11-03 US-15…US-19）：H5/APK 地址投影 + APK 代管上传。

Docker 边界统一收在 `mobile._docker` 替身上：本文件验「REST 语义 + 落点命令 + 体积闸门」，
真实 `compose exec` 的管道行为由 tests/test_engine.py 的 docker 单测与人工验收覆盖。
"""
import re
import shutil
from pathlib import Path

import pytest
from sqlalchemy import text

from app.db import make_session_factory
from app.deploy import engine as eng_mod, mobile, ports, provision, sqlrun

pytestmark = pytest.mark.asyncio

SLUG = "zcmob"
ROOT = Path("D:/__zc_mobile_api__")
MOBILE_STACK = "vue3-springboot-mobile"


@pytest.fixture(autouse=True)
def _no_startup_docker(monkeypatch):
    """与 test_apps_dev_api 同口径：lifespan 的部署侧准备收成替身，本文件不碰真 Docker。"""
    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(eng_mod, "cleanup_orphans", _noop)
    monkeypatch.setattr(eng_mod, "stop_all_dev_on_exit", _noop)
    monkeypatch.setattr(sqlrun, "build_if_missing", _noop)
    monkeypatch.setattr(provision, "ensure_zcnet", _noop)


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


async def test_info_reports_urls_and_apk_size(client, make_app, monkeypatch):
    app = await make_app()
    calls: list[dict] = []
    monkeypatch.setattr(mobile, "_docker", _fake_docker(calls, out="1234567"))
    monkeypatch.delenv("ZEROCODE_SSL_ENABLE", raising=False)

    r = await client.get(f"/api/apps/{app['id']}/mobile")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status_dev"] == "stopped"      # 弹框用它决定 dev 码与「打开」是否可用
    assert body["status_prod"] == "running"
    assert body["h5_url"] == f"https://{SLUG}.zerocode.com/h5/"
    assert body["apk_url"] == f"https://{SLUG}.zerocode.com/api/uploads/mobile/{SLUG}.apk"
    assert body["dev_url"].endswith(f":{app['dev_port']}/h5/")
    assert body["apk_size"] == 1234567
    assert body["apk_max_bytes"] == 200 * 1024 * 1024
    # 探体积必须走 prod compose + backend 容器内的 uploads 卷
    args = calls[0]["args"]
    assert args[:2] == ("compose", "-f") and Path(args[2]) == ROOT / "deploy" / "prod-compose.yml"
    assert args[3:6] == ("exec", "-T", "backend")
    assert f"/uploads/mobile/{SLUG}.apk" in args[8]


async def test_info_stopped_app_does_not_touch_docker(client, make_app, monkeypatch):
    app = await make_app(status_prod="stopped")
    calls: list[dict] = []
    monkeypatch.setattr(mobile, "_docker", _fake_docker(calls))

    r = await client.get(f"/api/apps/{app['id']}/mobile")
    assert r.status_code == 200, r.text
    assert r.json()["apk_size"] is None
    assert calls == []          # 容器不在：不 exec 探文件


async def test_info_rejects_non_mobile_stack(client, make_app):
    app = await make_app(stack="vue3-fastapi")
    r = await client.get(f"/api/apps/{app['id']}/mobile")
    assert r.status_code == 400


async def test_upload_writes_into_uploads_volume(client, make_app, monkeypatch):
    app = await make_app()
    calls: list[dict] = []
    monkeypatch.setattr(mobile, "_docker", _fake_docker(calls, out="ok"))

    payload = b"PK\x03\x04fake-apk-bytes"
    r = await client.post(f"/api/apps/{app['id']}/mobile/apk",
                          files={"file": ("demo.apk", payload, "application/vnd.android.package-archive")})
    assert r.status_code == 200, r.text
    assert r.json()["size"] == len(payload)
    assert r.json()["apk_url"].endswith(f"/api/uploads/mobile/{SLUG}.apk")

    assert len(calls) == 2          # ① 灌进 .part ② 单独 mv 转正
    stage, promote = calls
    for call in calls:
        args = call["args"]
        assert args[3:6] == ("exec", "-T", "backend")
        assert args[6] == "sh" and args[7] == "-c"
        assert Path(args[2]) == ROOT / "deploy" / "prod-compose.yml"
    # 先写隐藏 .part、灌流完整（退出码 0）才单独 mv：中断最多留 .part，不会出现半个正式 APK。
    # .part 名带随机 token（并发上传互不截断），转正命令必须引用同一个名字。
    m = re.fullmatch(rf"mkdir -p /uploads/mobile && cat > (/uploads/mobile/\.{SLUG}\.apk\.[0-9a-f]{{32}}\.part)",
                     stage["args"][8])
    assert m
    assert stage["feed"] == payload
    assert promote["args"][8] == f"mv {m.group(1)} /uploads/mobile/{SLUG}.apk"
    assert promote["feed"] is None   # 转正不灌流


async def test_upload_rejects_empty_file(client, make_app, monkeypatch):
    """空文件必须在任何写入之前挡下：否则「转正」会产出一个 0 字节的正式 APK。"""
    app = await make_app()
    calls: list[dict] = []
    monkeypatch.setattr(mobile, "_docker", _fake_docker(calls))

    r = await client.post(f"/api/apps/{app['id']}/mobile/apk",
                          files={"file": ("demo.apk", b"")})
    assert r.status_code == 400 and "为空" in r.json()["detail"]
    assert calls == []


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


async def test_upload_promote_failure_keeps_old_apk(client, make_app, monkeypatch):
    """转正失败 → 503：旧 APK 原样可下载，残留只是隐藏 .part（下一次上传覆盖）。"""
    app = await make_app()
    results = [(0, "ok"), (1, "mv: cannot stat '/uploads/mobile/.zcmob.apk.part'")]
    calls: list[dict] = []

    async def _f(*args, cwd=None, timeout=600, feed=None):
        if feed is not None:
            while await feed.read(1024 * 1024):
                pass
        calls.append({"args": args, "cwd": cwd, "feed": b"", "timeout": timeout})
        return results.pop(0)

    monkeypatch.setattr(mobile, "_docker", _f)
    r = await client.post(f"/api/apps/{app['id']}/mobile/apk",
                          files={"file": ("demo.apk", b"x")})
    assert r.status_code == 503
    assert len(calls) == 2


async def test_upload_blocked_when_prod_not_running(client, make_app, monkeypatch):
    app = await make_app(status_prod="stopped")
    calls: list[dict] = []
    monkeypatch.setattr(mobile, "_docker", _fake_docker(calls))

    r = await client.post(f"/api/apps/{app['id']}/mobile/apk",
                          files={"file": ("demo.apk", b"x")})
    assert r.status_code == 409 and "正式环境未运行" in r.json()["detail"]
    assert calls == []          # US-17：不隐式起栈


async def test_upload_rejects_wrong_suffix(client, make_app):
    app = await make_app()
    r = await client.post(f"/api/apps/{app['id']}/mobile/apk",
                          files={"file": ("demo.zip", b"x")})
    assert r.status_code == 400


async def test_upload_rejects_non_mobile_stack(client, make_app):
    app = await make_app(stack="vue3-springboot")
    r = await client.post(f"/api/apps/{app['id']}/mobile/apk",
                          files={"file": ("demo.apk", b"x")})
    assert r.status_code == 400


async def test_upload_rejects_oversize_before_docker(client, make_app, monkeypatch):
    app = await make_app()
    monkeypatch.setattr(mobile, "APK_MAX_BYTES", 4)
    calls: list[dict] = []
    monkeypatch.setattr(mobile, "_docker", _fake_docker(calls))

    r = await client.post(f"/api/apps/{app['id']}/mobile/apk",
                          files={"file": ("demo.apk", b"0123456789")})
    assert r.status_code == 413
    assert calls == []          # 快路径：声明体积已超限，不开写管道


async def test_capped_reader_aborts_midstream():
    """声明体积缺失时的兜底：读满上限立即抛 413，超出部分不得进管道。"""
    class _File:
        def __init__(self):
            self.left = 3

        async def read(self, n):
            if not self.left:
                return b""
            self.left -= 1
            return b"xxxxx"

    reader = mobile._CappedReader(_File(), limit=8)
    await reader.read(1024)
    with pytest.raises(Exception) as e:
        await reader.read(1024)
    assert getattr(e.value, "status", None) == 413


async def test_upload_maps_backend_failure_to_503(client, make_app, monkeypatch):
    app = await make_app()
    calls: list[dict] = []
    monkeypatch.setattr(mobile, "_docker",
                        _fake_docker(calls, code=1, out="service \"backend\" is not running"))
    r = await client.post(f"/api/apps/{app['id']}/mobile/apk",
                          files={"file": ("demo.apk", b"x")})
    assert r.status_code == 503
    assert "APK 写入失败" in r.json()["detail"]
