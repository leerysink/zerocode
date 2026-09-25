# tests/test_auth_api.py
"""Ticket 002：认证垂直切片——register/login/logout/me + 全局 401 guard。
（接缝 = HTTP API，spec §6；ADR-0003）"""


async def test_新用户可注册并获得会话cookie(client):
    r = await client.post("/api/auth/register", json={"nickname": "alice", "password": "pw123"})
    assert r.status_code == 200
    assert "zc_actor" in r.cookies  # 注册即登录：当场下发会话 cookie
    me = await client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["nickname"] == "alice"


async def test_重复昵称注册被拒(client):
    await client.post("/api/auth/register", json={"nickname": "alice", "password": "pw123"})
    r = await client.post("/api/auth/register", json={"nickname": "alice", "password": "other"})
    assert r.status_code == 409


async def test_缺口令注册被拒(client):
    r = await client.post("/api/auth/register", json={"nickname": "bob", "password": ""})
    assert r.status_code == 400


async def test_正确口令可登录并重建会话(client):
    await client.post("/api/auth/register", json={"nickname": "alice", "password": "pw123"})
    # 模拟换浏览器：另开一个无 cookie 的 client（不能复用 client/anon_client，同一实例），靠登录拿回会话
    from httpx import AsyncClient, ASGITransport
    async with AsyncClient(transport=ASGITransport(app=client.app), base_url="http://t") as fresh:
        assert (await fresh.get("/api/auth/me")).status_code == 401
        r = await fresh.post("/api/auth/login", json={"nickname": "alice", "password": "pw123"})
        assert r.status_code == 200
        assert "zc_actor" in r.cookies
        assert (await fresh.get("/api/auth/me")).json()["nickname"] == "alice"


async def test_错误口令不能登录(client):
    await client.post("/api/auth/register", json={"nickname": "alice", "password": "pw123"})
    r = await client.post("/api/auth/login", json={"nickname": "alice", "password": "wrong"})
    assert r.status_code == 401


async def test_无口令的存量actor无法被任何口令登录(client):
    # 存量轻身份（迁移扩展态：password_hash 为 NULL）不得开门
    r = await client.post("/api/auth/login", json={"nickname": "ghost", "password": "anything"})
    assert r.status_code == 401


async def test_登出后会话作废(client):
    await client.post("/api/auth/register", json={"nickname": "alice", "password": "pw123"})
    assert (await client.get("/api/auth/me")).status_code == 200
    assert (await client.post("/api/auth/logout")).status_code == 200
    assert (await client.get("/api/auth/me")).status_code == 401


async def test_未登录访问业务接口被拒(anon_client):
    # guard 中间件：/api/* 除 /api/auth/* 外无 cookie 一律 401
    # （注意：client 与 anon_client 同 app 下实为同一实例，本用例不得双取）
    r = await anon_client.get("/api/workspaces")
    assert r.status_code == 401
    r2 = await anon_client.post("/api/sessions", json={"workspace_id": 1})
    assert r2.status_code == 401


async def test_登出后旧cookie对业务接口失效(client):
    # 不只 /me：guard 对所有 /api/* 同样作废旧凭据
    assert (await client.get("/api/workspaces")).status_code == 200
    await client.post("/api/auth/logout")
    assert (await client.get("/api/workspaces")).status_code == 401
