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


async def test_审批权在HTTP层收口到创始人(two_auth_clients, test_engine):
    """端点接线的靶心：is_founder 只能来自会话 actor——
    bob（非创始人，即便是版本发布者）决策 403、收件箱空；alice（创始人，局外人）可批。
    直连 sf 写库的用例显式请求 test_engine（同 test_isolation_config_models:73 先例），
    保证清库 finalizer 的拆序正确，不泄漏状态给下一个用例。"""
    alice, bob = two_auth_clients   # alice 首注册即创始人
    sf = alice.app.state.session_factory
    async with sf() as s:
        bob_id = (await s.execute(text("SELECT id FROM actor WHERE nickname = 'bob'"))).scalar()
        rid = (await s.execute(text(
            "INSERT INTO app_release (app_id, slug, version, title, stack, anchor, "
            "fingerprint, status, created_by) VALUES (0, 'gating', 'b1', '门禁', "
            "'vue3-fastapi', '[]'::jsonb, 'fp-g1', 'published', :a) RETURNING id"),
            {"a": bob_id})).scalar()
        await s.commit()
    a = await bob.post("/api/store/applications",
                       json={"release_id": int(rid), "want_name": "bob的实例"})
    assert a.status_code == 201, a.text
    aid = a.json()["id"]
    # 发布者本人也无权决策（权限翻转）
    r = await bob.post(f"/api/store/applications/{aid}/reject",
                       json={"approve": False, "reason": "越权尝试"})
    assert r.status_code == 403
    # bob 的审批收件箱为空（不再按版本归属分发）
    assert (await bob.get("/api/store/applications?box=review")).json() == []
    # alice 看得到全部 pending 并可代批
    rows = (await alice.get("/api/store/applications?box=review")).json()
    assert [x["id"] for x in rows] == [aid]
    r2 = await alice.post(f"/api/store/applications/{aid}/reject",
                          json={"approve": False, "reason": "名字不合适"})
    assert r2.status_code == 200 and r2.json()["status"] == "rejected"
    assert (await alice.get("/api/store/applications?box=review")).json() == []


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
