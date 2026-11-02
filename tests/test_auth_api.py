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


# ---------- 多设备并发会话（V20261030_01，推翻 spec §7「不做多设备并发会话」）----------

def _device(app):
    """一台独立设备：不共享 cookie jar 的裸 client。"""
    from httpx import AsyncClient, ASGITransport
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def test_同账号两处登录都在线(client):
    """登录不再换发全局唯一凭据：B 上线不影响 A 继续用。"""
    token_a = client.cookies.get("zc_actor")
    async with _device(client.app) as dev_b:
        r = await dev_b.post("/api/auth/login", json={"nickname": "owner", "password": "owner-pw"})
        assert r.status_code == 200
        assert dev_b.cookies.get("zc_actor") != token_a
        assert (await dev_b.get("/api/auth/me")).json()["nickname"] == "owner"
    assert (await client.get("/api/auth/me")).status_code == 200, "B 登录不得踢掉 A"


async def test_登出只作废自己的会话(client):
    async with _device(client.app) as dev_b:
        await dev_b.post("/api/auth/login", json={"nickname": "owner", "password": "owner-pw"})
        assert (await client.post("/api/auth/logout")).status_code == 200
        assert (await client.get("/api/auth/me")).status_code == 401
        assert (await dev_b.get("/api/auth/me")).status_code == 200, "A 登出只该带走 A"


async def test_坐满上限时挤掉最久未活动的(client):
    """上限取自 actor.max_sessions（列默认 10，用例改成 2 好验证）：登录永不失败，代价由最久没动的人承担。"""
    from sqlalchemy import text
    from app.store.identity import sleep_a_bit
    async with client.app.state.session_factory() as s:
        await s.execute(text("UPDATE actor SET max_sessions = 2 WHERE nickname = 'owner'"))
        await s.commit()
    token_old = client.cookies.get("zc_actor")
    async with _device(client.app) as dev:
        await dev.post("/api/auth/login", json={"nickname": "owner", "password": "owner-pw"})
        token_mid = dev.cookies.get("zc_actor")
        await sleep_a_bit()  # LRU 靠 last_seen_at 排序，时间戳必须有先后
        await dev.post("/api/auth/login", json={"nickname": "owner", "password": "owner-pw"})
        token_new = dev.cookies.get("zc_actor")
    async with _device(client.app) as probe:
        probe.cookies.set("zc_actor", token_old)
        assert (await probe.get("/api/auth/me")).status_code == 401, "第 3 人上线该挤掉最久未活动的"
        for t in (token_mid, token_new):
            probe.cookies.set("zc_actor", t)
            assert (await probe.get("/api/auth/me")).status_code == 200
