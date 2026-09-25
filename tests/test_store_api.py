# tests/test_store_api.py
"""商店 REST：身份 cookie、商店列表、申请审批。Docker 不参与（安装用例见 test_store_install.py）。"""
from sqlalchemy import text


async def _sf(client):
    return client.app.state.session_factory


async def test_store_requires_identity(anon_client):
    # Ticket 002 起无身份先被全局 guard 拦 401（detail 为字符串）；
    # store 自身的 identity_required 依赖在 008 身份合流后一并收口。
    r = await anon_client.get("/api/store/me")
    assert r.status_code == 401


async def test_匿名identify建号端点已退役(client):
    """Ticket 008 合流：不再面向前端匿名建号（注册端点收编），路由已删。

    404 或 405 均算数：POST 不匹配任何 API 路由时，SPA 的 GET catch-all 会接住路径回 405，
    语义都是「这个端点不存在了」。"""
    r = await client.post("/api/store/identify", json={"nickname": "看客"})
    assert r.status_code in (404, 405)


async def test_商店身份即登录用户没有第二套actor(client):
    """两套身份合流：/store/me 与 /auth/me 必须是同一个人（cookie 同一、行同一）。"""
    me = (await client.get("/api/auth/me")).json()
    actor = (await client.get("/api/store/me")).json()
    assert actor["id"] == me["id"] and actor["nickname"] == me["nickname"]


async def test_releases_list_only_published(client):
    """未上架时是空列表而不是 404；withdrawn 不出现。"""
    r = await client.get("/api/store/releases")
    assert r.status_code == 200
    assert r.json() == []


async def test_release_endpoint_requires_identity_and_publish(anon_client):
    # 同 store_requires_identity：401 现在由 guard 给出（detail 字符串），不再断言 code 形状
    r = await anon_client.post("/api/apps/1/release", json={"title": "x", "summary": ""})
    assert r.status_code == 401


async def test_store_releases_lists_published(client, monkeypatch):
    """列表不触发 Docker：直接往 app_release 插行，验形状与 withdrawn 过滤。"""
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
