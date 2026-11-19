# tests/test_store_shots.py
"""商店截图（规格 2026-11-19 §4.3）：魔数/大小/数量闸、作者增删、鉴权读、落盘不进包。"""
import secrets
import pytest
from sqlalchemy import text

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


@pytest.fixture
def shot_root(tmp_path, monkeypatch):
    from app.store import shots
    async def _cfg(sf):
        return {"store.root": str(tmp_path / "store")}
    monkeypatch.setattr(shots, "deploy_cfg", _cfg)
    return tmp_path / "store"


async def _mk_release(client):
    me = (await client.get("/api/store/me")).json()
    sf = client.app.state.session_factory
    async with sf() as s:
        rid = (await s.execute(text(
            "INSERT INTO app_release (app_id, slug, version, title, stack, anchor, "
            "fingerprint, status, created_by) VALUES (0, :sl, 'b1', '截图应用', "
            "'vue3-fastapi', '[]'::jsonb, 'fp-s', 'published', :a) RETURNING id"),
            {"sl": "shot" + secrets.token_hex(3), "a": me["id"]})).scalar()
        await s.commit()
    return int(rid), me


async def _upload(client, rid, data=PNG, name="a.png"):
    return await client.post(f"/api/store/releases/{rid}/screenshots",
                             files={"file": (name, data, "image/png")})


async def test_上传截图随列表回URL且可鉴权读取(client, shot_root):
    rid, _ = await _mk_release(client)
    r = await _upload(client, rid)
    assert r.status_code == 201, r.text
    body = (await client.get("/api/store/releases")).json()
    item = next(x for x in body if x["id"] == rid)
    assert item["screenshots"] == [f"/api/store/releases/{rid}/screenshots/0"]
    got = await client.get(item["screenshots"][0])
    assert got.status_code == 200 and got.content == PNG
    # 落盘在 store/releases/<id>/，与包目录（store/<slug>/<version>/）互不相干——不进包
    assert (shot_root / "releases" / str(rid)).is_dir()
    assert not (shot_root / item["slug"]).exists()


async def test_截图闸_数量大小格式(client, shot_root):
    rid, _ = await _mk_release(client)
    for i in range(5):
        assert (await _upload(client, rid, name=f"{i}.png")).status_code == 201
    assert (await _upload(client, rid, name="6.png")).status_code == 400
    # 大小/格式闸用未满额的版本验，避免先撞数量闸
    rid2, _ = await _mk_release(client)
    big = b"\x89PNG\r\n\x1a\n" + b"0" * (2 * 1024 * 1024)
    assert (await _upload(client, rid2, data=big)).status_code == 413
    assert (await _upload(client, rid2, data=b"GIF89a" + b"0" * 64)).status_code == 400


async def test_非作者不能增删(two_auth_clients, shot_root):
    alice, bob = two_auth_clients
    rid, _ = await _mk_release(alice)
    assert (await _upload(bob, rid)).status_code == 403
    assert (await bob.delete(f"/api/store/releases/{rid}/screenshots/0")).status_code == 403


async def test_删除截图并重排索引(client, shot_root):
    rid, _ = await _mk_release(client)
    await _upload(client, rid, name="a.png")
    await _upload(client, rid, data=b"\xff\xd8\xff" + b"0" * 64, name="b.jpg")
    r = await client.delete(f"/api/store/releases/{rid}/screenshots/0")
    assert r.status_code == 200, r.text
    item = next(x for x in (await client.get("/api/store/releases")).json() if x["id"] == rid)
    assert item["screenshots"] == [f"/api/store/releases/{rid}/screenshots/0"]
    got = await client.get(item["screenshots"][0])
    assert got.content.startswith(b"\xff\xd8\xff"), "删除后剩下的必须是第二张"
    assert (await client.get(f"/api/store/releases/{rid}/screenshots/1")).status_code == 404


async def test_匿名读截图401(client, shot_root, app_):
    # client 夹具本身由 anon_client 注册而来（已持 cookie）；真匿名要另起裸 client
    rid, _ = await _mk_release(client)
    await _upload(client, rid)
    from httpx import AsyncClient, ASGITransport
    async with AsyncClient(transport=ASGITransport(app=app_), base_url="http://t") as bare:
        r = await bare.get(f"/api/store/releases/{rid}/screenshots/0")
        assert r.status_code == 401
