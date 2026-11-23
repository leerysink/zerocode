"""账号治理 · 封禁与解封（规格 2026-11-23 §3 C / ADR-0067）。

接缝 S1：`identity.by_token` 是掉线的唯一真源——全局 guard 中间件（app/main.py:229）对
每个 /api/* 都过它，所以封禁在这里生效 == 所有设备的下一个请求即 401。
"""
import pytest
from sqlalchemy import text


async def _accounts(admin):
    r = await admin.get("/api/admin/accounts")
    assert r.status_code == 200, r.text
    return {a["nickname"]: a for a in r.json()}


async def test_admin_accounts_list_founder_only(two_auth_clients):
    """治理列表仅管理员可读；且出库字段是白名单，不含口令与会话凭据。"""
    admin, member = two_auth_clients
    r = await member.get("/api/admin/accounts")
    assert r.status_code == 403, r.text

    rows = await _accounts(admin)
    assert {"alice", "bob"} <= set(rows)
    alice = rows["alice"]
    assert alice["is_founder"] is True and rows["bob"]["is_founder"] is False
    for banned_field in ("banned_at", "banned_reason"):
        assert banned_field in alice and alice[banned_field] is None
    for secret in ("password_hash", "token", "max_sessions"):
        assert secret not in alice, secret


async def test_ban_revokes_live_session_immediately(two_auth_clients):
    """S1 红线：封禁后**已签发的会话**下一个请求即 401，不等 cookie 过期。

    这条用例是「把封禁只实现在 login 上」的变异检验——那样 login 会被拦住，
    而这里带着旧 cookie 的 client 仍能继续操作，测试必须红。
    """
    admin, member = two_auth_clients
    target = (await _accounts(admin))["bob"]

    assert (await member.get("/api/users")).status_code == 200
    r = await admin.post(f"/api/admin/accounts/{target['id']}/ban",
                         json={"reason": "滥用资源"})
    assert r.status_code == 200, r.text

    assert (await member.get("/api/users")).status_code == 401
    assert (await member.get("/api/auth/me")).status_code == 401


async def test_banned_actor_cannot_login_but_record_survives(two_auth_clients, app_):
    """被封者口令正确也拒入，文案点名「封禁」（此处刻意放弃不泄露存在性的口径）；
    同时他的业务数据一行不少（spec §3 C：封禁只否入口，不是删除）。"""
    admin, member = two_auth_clients
    target = (await _accounts(admin))["bob"]

    r = await admin.post(f"/api/admin/accounts/{target['id']}/ban", json={"reason": "x"})
    assert r.status_code == 200, r.text

    from httpx import ASGITransport, AsyncClient
    async with AsyncClient(transport=ASGITransport(app=app_), base_url="http://t") as c:
        login = await c.post("/api/auth/login",
                             json={"nickname": "bob", "password": "pw-bob"})
    assert login.status_code == 401
    assert "封禁" in login.json()["detail"], login.json()

    # 封禁前建的数据必须在封禁后仍躺在库里
    async with member.app.state.session_factory() as s:
        uid = target["id"]
        await s.execute(text(
            "INSERT INTO workspace (name, root_path, owner_user_id) "
            "VALUES ('gov-keep', :p, :u)"), {"p": f"/tmp/gov-keep-{uid}", "u": uid})
        await s.commit()
    async with admin.app.state.session_factory() as s:
        cnt = (await s.execute(text(
            "SELECT count(*) FROM workspace WHERE owner_user_id=:u"), {"u": uid})).scalar()
    assert cnt == 1, cnt


async def test_unban_restores_login_and_calls_no_deploy_path(two_auth_clients, monkeypatch):
    """解封 = 只恢复入口。封禁与解封都不碰任何起停栈的路径：封禁默认不停栈
    （那是独立勾选项，见 003），解封更不替他重启（US-15）。

    刻意用调用监视而不是往库里塞一条 `status_dev='running'` 的假应用：平台启动会清扫
    残留 running 栈（ADR-0019），假行会让下一个用例的 lifespan 去敲 Docker 而挂住。
    """
    admin, member = two_auth_clients
    target = (await _accounts(admin))["bob"]
    uid = target["id"]

    calls = []
    import app.deploy.engine as engine
    import app.deploy.prod as prod
    for mod, fn in ((engine, "start_dev"), (engine, "stop_dev"),
                    (prod, "prod_start"), (prod, "prod_stop")):
        if hasattr(mod, fn):
            monkeypatch.setattr(mod, fn,
                                (lambda name: lambda *a, **k: calls.append(name))(f"{mod.__name__}.{fn}"))

    assert (await admin.post(f"/api/admin/accounts/{uid}/ban",
                             json={"reason": "x"})).status_code == 200
    r = await admin.post(f"/api/admin/accounts/{uid}/unban")
    assert r.status_code == 200, r.text
    assert calls == [], calls

    row = (await _accounts(admin))["bob"]
    assert row["banned_at"] is None and row["banned_reason"] is None

    from httpx import ASGITransport, AsyncClient
    async with AsyncClient(transport=ASGITransport(app=admin.app), base_url="http://t") as c:
        login = await c.post("/api/auth/login",
                             json={"nickname": "bob", "password": "pw-bob"})
        assert login.status_code == 200, login.text
        assert (await c.get("/api/users")).status_code == 200


async def _seed_running_stacks(two_auth_clients, tag):
    """给 bob 铺一个 dev+prod 都在跑的应用，返回 (app_id, cleanup)。

    状态必须在删除前改回 stopped：库里留着 running 行，下一轮平台启动的残留栈清扫会去敲
    Docker（ADR-0019），测试就挂在别人的容器上。
    """
    admin, member = two_auth_clients
    sf = admin.app.state.session_factory
    uid = (await _accounts(admin))["bob"]["id"]
    async with sf() as s:
        ws = (await s.execute(text(
            "INSERT INTO workspace (name, root_path, owner_user_id) VALUES (:n, :p, :u) "
            "RETURNING id"), {"n": f"stop-{tag}", "p": f"/tmp/stop-{tag}", "u": uid})).scalar()
        app_id = (await s.execute(text(
            "INSERT INTO app_project (workspace_id, name, slug, scale, stack, prod_port, "
            " dev_port, status_dev, status_prod, owner_user_id) VALUES "
            "(:w, :n, :slug, 'small', 'static', :pp, :dp, 'running', 'running', :u) "
            "RETURNING id"),
            {"w": ws, "n": tag, "slug": f"stop{tag}", "pp": 8860, "dp": 8861,
             "u": uid})).scalar()
        await s.commit()

    async def cleanup():
        async with sf() as s:
            await s.execute(text("UPDATE app_project SET status_dev='stopped', "
                                 "status_prod='stopped' WHERE id=:i"), {"i": app_id})
            await s.execute(text("DELETE FROM app_project WHERE id=:i"), {"i": app_id})
            await s.execute(text("DELETE FROM workspace WHERE id=:i"), {"i": ws})
            await s.commit()
    return uid, app_id, cleanup


async def test_ban_with_opt_in_stops_both_cells(two_auth_clients, monkeypatch):
    """勾选停栈 → dev 与 prod 各下一次停止；封禁本身仍然即时生效。"""
    import asyncio

    import app.deploy.engine as engine
    import app.deploy.prod as prod

    admin, member = two_auth_clients
    uid, app_id, cleanup = await _seed_running_stacks(two_auth_clients, "opt")
    calls = []
    monkeypatch.setattr(engine, "stop_dev",
                        lambda sf, app, notify=None: _record(calls, "dev", app["id"]))
    monkeypatch.setattr(prod, "prod_stop",
                        lambda sf, app, notify=None: _record(calls, "prod", app["id"]))
    try:
        r = await admin.post(f"/api/admin/accounts/{uid}/ban",
                             json={"reason": "占资源", "stop_stacks": True})
        assert r.status_code == 200, r.text
        assert r.json()["banned_at"] is not None
        assert r.json()["stacks_stopped"] == 2, r.json()
        await asyncio.sleep(0.05)
        assert sorted(calls) == [("dev", app_id), ("prod", app_id)], calls
    finally:
        await cleanup()


async def test_ban_without_opt_in_leaves_stacks_alone(two_auth_clients, monkeypatch):
    """不勾（默认）→ 一个栈都不停。这条与上一条成对，缺一条就等于把破坏性动作送上了默认路径。"""
    import asyncio

    import app.deploy.engine as engine
    import app.deploy.prod as prod

    admin, member = two_auth_clients
    uid, app_id, cleanup = await _seed_running_stacks(two_auth_clients, "noopt")
    calls = []
    monkeypatch.setattr(engine, "stop_dev",
                        lambda sf, app, notify=None: _record(calls, "dev", app["id"]))
    monkeypatch.setattr(prod, "prod_stop",
                        lambda sf, app, notify=None: _record(calls, "prod", app["id"]))
    try:
        r = await admin.post(f"/api/admin/accounts/{uid}/ban", json={"reason": "只封不停"})
        assert r.status_code == 200, r.text
        assert r.json()["stacks_stopped"] == 0, r.json()
        await asyncio.sleep(0.05)
        assert calls == [], calls
        async with admin.app.state.session_factory() as s:
            dev = (await s.execute(text("SELECT status_dev FROM app_project WHERE id=:i"),
                                   {"i": app_id})).scalar()
        assert dev == "running", "不勾选也停了栈"
    finally:
        await cleanup()


async def test_stop_failure_does_not_roll_back_the_ban(two_auth_clients, monkeypatch):
    """停栈失败（Docker 不在）不能把封禁一起带失败：封禁是主意图，停栈是顺带清理。"""
    import asyncio

    import app.deploy.engine as engine

    admin, member = two_auth_clients
    uid, app_id, cleanup = await _seed_running_stacks(two_auth_clients, "boom")

    async def explode(sf, app, notify=None):
        raise RuntimeError("docker unavailable")

    monkeypatch.setattr(engine, "stop_dev", explode)
    monkeypatch.setattr("app.deploy.prod.prod_stop",
                        lambda sf, app, notify=None: _record([], "prod", app["id"]))
    try:
        r = await admin.post(f"/api/admin/accounts/{uid}/ban",
                             json={"reason": "x", "stop_stacks": True})
        assert r.status_code == 200, r.text
        assert r.json()["banned_at"] is not None
        await asyncio.sleep(0.05)
    finally:
        await cleanup()


async def _record(sink, env, app_id):
    sink.append((env, app_id))
    return {"ok": True}


async def test_admin_cannot_ban_self(two_auth_clients):
    """自封 = 把平台锁死（管理员不可移交，ADR-0067），后端同口径拒绝而非只藏按钮。"""
    admin, member = two_auth_clients
    me = (await _accounts(admin))["alice"]
    r = await admin.post(f"/api/admin/accounts/{me['id']}/ban", json={"reason": "oops"})
    assert r.status_code == 400, r.text

    other = (await _accounts(admin))["bob"]
    r2 = await member.post(f"/api/admin/accounts/{other['id']}/ban", json={"reason": "x"})
    assert r2.status_code == 403, r2.text


async def test_ban_rejects_unknown_account_and_persists_reason(two_auth_clients):
    admin, member = two_auth_clients
    r = await admin.post("/api/admin/accounts/999999/ban", json={"reason": "x"})
    assert r.status_code == 404, r.text

    target = (await _accounts(admin))["bob"]
    await admin.post(f"/api/admin/accounts/{target['id']}/ban", json={"reason": "占满端口池"})
    row = (await _accounts(admin))["bob"]
    assert row["banned_at"] and row["banned_reason"] == "占满端口池"

    await admin.post(f"/api/admin/accounts/{target['id']}/unban")
    row2 = (await _accounts(admin))["bob"]
    assert row2["banned_at"] is None and row2["banned_reason"] is None


async def test_by_token_is_the_single_enforcement_point(two_auth_clients):
    """接缝直测：不经过任何端点，`by_token` 对被封账号必须返回 None。

    这条是 S1 的定义式断言。若将来有人把封禁判定挪进各端点，本用例仍绿而其他用例红——
    两条一起看才知道接线没跑偏。
    """
    from app.store import identity

    admin, member = two_auth_clients
    token = member.cookies.get(identity.ACTOR_COOKIE)
    uid = (await _accounts(admin))["bob"]["id"]
    async with admin.app.state.session_factory() as s:
        await s.execute(text("UPDATE actor SET banned_at = now() WHERE id=:u"), {"u": uid})
        await s.commit()
    assert await identity.by_token(admin.app.state.session_factory, token) is None
    # 反向：管理员自己的 token 不受牵连
    admin_token = admin.cookies.get(identity.ACTOR_COOKIE)
    assert await identity.by_token(admin.app.state.session_factory, admin_token) is not None
