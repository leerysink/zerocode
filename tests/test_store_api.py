# tests/test_store_api.py
"""商店 REST：身份 cookie、商店列表、申请审批。Docker 不参与（安装用例见 test_store_install.py）。"""
from sqlalchemy import text


async def _sf(client):
    return client.app.state.session_factory


async def test_store_requires_identity(client):
    r = await client.get("/api/store/me")
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "identity_required"


async def test_identify_sets_cookie_and_reuses_actor(client):
    r = await client.post("/api/store/identify", json={"nickname": "陌如玉的用户"})
    assert r.status_code == 200, r.text
    actor = r.json()
    assert actor["nickname"] == "陌如玉的用户"
    assert "zc_actor" in r.cookies or client.cookies.get("zc_actor")
    # 同一 cookie 再访问 → 同一个人（cookie 即设备标识，不重复建行）
    r2 = await client.get("/api/store/me")
    assert r2.status_code == 200
    assert r2.json()["id"] == actor["id"]


async def test_identify_rejects_blank(client):
    r = await client.post("/api/store/identify", json={"nickname": "  "})
    assert r.status_code == 400


async def test_releases_list_only_published(client):
    """未上架时是空列表而不是 404；withdrawn 不出现。"""
    await client.post("/api/store/identify", json={"nickname": "看客"})
    r = await client.get("/api/store/releases")
    assert r.status_code == 200
    assert r.json() == []


async def test_release_endpoint_requires_identity_and_publish(client):
    r = await client.post("/api/apps/1/release", json={"title": "x", "summary": ""})
    assert r.status_code == 401 and r.json()["detail"]["code"] == "identity_required"


async def test_store_releases_lists_published(client, monkeypatch):
    """列表不触发 Docker：直接往 app_release 插行，验形状与 withdrawn 过滤。"""
    await client.post("/api/store/identify", json={"nickname": "看客"})
    sf = client.app.state.session_factory
    from sqlalchemy import text
    async with sf() as s:
        aid = (await s.execute(text("SELECT id FROM actor LIMIT 1"))).scalar()
        for version, status in (("b1", "published"), ("b0", "withdrawn")):
            await s.execute(text(
                "INSERT INTO app_release (app_id, slug, version, title, stack, anchor, "
                "fingerprint, status, created_by) VALUES (0, 'demo', :v, '演示应用', "
                "'vue3-fastapi', '[]'::jsonb, :f, :st, :a)"),
                {"v": version, "f": "fp" + version, "st": status, "a": aid})
        await s.commit()
    r = await client.get("/api/store/releases")
    assert r.status_code == 200
    body = r.json()
    assert [x["version"] for x in body] == ["b1"], "下架版本不得出现在商店列表"
    assert body[0]["title"] == "演示应用" and "updated_count" in body[0]
