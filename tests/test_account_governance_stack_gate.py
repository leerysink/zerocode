"""账号治理 · 启动闸（规格 2026-11-23 §3 D / ADR-0070）。

七个会让容器起来的入口逐个撞闸。**逐入口各一条，不合并**——「判定函数有测试」不等于
「每个入口都调了它」，而 `publish` / `批准发布申请` 恰恰是最重、最容易被漏掉的路径。
"""
import json

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.asyncio

_PORT = [8800]


async def _uid(client, nickname):
    rows = (await client.get("/api/users")).json()
    return next(r["id"] for r in rows if r["nickname"] == nickname)


async def _set_limit(sf, owner_id, value):
    """给某个账号设启动上限（走管理员写入的同一张表，只是测试里直接落库以省掉一轮往返）。"""
    async with sf() as s:
        await s.execute(text(
            "INSERT INTO app_config (key, value, owner_user_id) "
            "VALUES ('quota.max_running_stacks', CAST(:v AS JSONB), :u) "
            "ON CONFLICT (owner_user_id, key) DO UPDATE SET value = CAST(:v AS JSONB)"),
            {"v": json.dumps(value), "u": owner_id})
        await s.commit()


async def _app(sf, uid, tag, *, dev="stopped", prod="stopped", shared_with=None):
    """铺一个属于 uid 的应用（默认两套栈都停着），返回 app_id。

    用完必须把状态改回 stopped 再删行：库里留着 running 的行，下一轮平台启动的残留栈
    清扫会去敲 Docker（ADR-0019），测试就会挂在别人的容器上。
    """
    _PORT[0] += 2
    pp, dp = _PORT[0], _PORT[0] + 1
    async with sf() as s:
        ws = (await s.execute(text(
            "INSERT INTO workspace (name, root_path, owner_user_id) "
            "VALUES (:n, :p, :u) RETURNING id"),
            {"n": f"stack-{tag}", "p": f"/tmp/stack-{tag}-{uid}", "u": uid})).scalar()
        app_id = (await s.execute(text(
            "INSERT INTO app_project (workspace_id, name, slug, scale, stack, prod_port, "
            " dev_port, status_dev, status_prod, owner_user_id) "
            "VALUES (:w, :n, :slug, 'small', 'static', :pp, :dp, :dv, :pd, :u) "
            "RETURNING id"),
            {"w": ws, "n": tag, "slug": f"stack{tag}", "pp": pp, "dp": dp,
             "dv": dev, "pd": prod, "u": uid})).scalar()
        if shared_with is not None:
            await s.execute(text(
                "INSERT INTO app_share (app_id, user_id, created_by) VALUES (:a, :a2, :u)"),
                {"a": app_id, "a2": shared_with, "u": uid})
        await s.commit()
    return app_id


async def _teardown(sf, app_id):
    async with sf() as s:
        await s.execute(text("UPDATE app_project SET status_dev='stopped', "
                             "status_prod='stopped' WHERE id=:i"), {"i": app_id})
        await s.execute(text("DELETE FROM app_share WHERE app_id=:i"), {"i": app_id})
        await s.execute(text("DELETE FROM app_project WHERE id=:i"), {"i": app_id})
        await s.commit()


async def test_running_stack_count_is_per_environment(two_auth_clients):
    """dev 与 prod 各算一格（CONTEXT「实例」）：一个应用双跑就是 2。"""
    from app.deploy import quota

    admin, member = two_auth_clients
    sf = admin.app.state.session_factory
    uid = await _uid(member, "bob")
    a1 = await _app(sf, uid, "cnt1", dev="running", prod="running")
    a2 = await _app(sf, uid, "cnt2", dev="running")
    try:
        async with sf() as s:
            assert await quota.count_running_stacks(s, uid) == 3
    finally:
        await _teardown(sf, a1)
        await _teardown(sf, a2)


async def _gate_case(app_, uid, *, limit, dev, prod, app_tag):
    sf = app_.state.session_factory
    await _set_limit(sf, uid, limit)
    occupied = await _app(sf, uid, f"occ{app_tag}", dev=dev, prod=prod)
    target = await _app(sf, uid, f"tgt{app_tag}")
    return sf, occupied, target


async def test_each_starting_endpoint_is_gated(two_auth_clients):
    """六个 HTTP 入口逐条撞闸：dev/start、dev/restart、prod/start、prod/restart、
    publish、rollback。前四个共用 `_dev_task`，后两个走发布侧闸门。
    """
    admin, member = two_auth_clients
    uid = await _uid(member, "bob")
    sf, occupied, target = await _gate_case(
        member.app, uid, limit=1, dev="running", prod="stopped", app_tag="ep")
    try:
        endpoints = [
            f"/api/apps/{target}/dev/start",
            f"/api/apps/{target}/dev/restart",
            f"/api/apps/{target}/prod/start",
            f"/api/apps/{target}/prod/restart",
            f"/api/apps/{target}/publish",
            f"/api/apps/{target}/rollback",
        ]
        for path in endpoints:
            body = {"version": "b20261123000000"} if path.endswith("rollback") else {}
            r = await member.post(path, json=body)
            assert r.status_code == 409, f"{path} → {r.status_code} {r.text}"
            assert "上限" in r.json()["detail"], f"{path} 文案不对：{r.json()}"
    finally:
        await _teardown(sf, occupied)
        await _teardown(sf, target)


async def test_publish_gate_is_synchronous_and_emits_nothing(two_auth_clients):
    """US-21：发布超限是**同步 409**，不是 202 + 后台日志里失败；且零 deploy_record 落库。

    对齐既有预检契约（`_dev_task` 的 docker_gate 同款纪律）：受理后才发现超限，
    用户只能在「预览日志」里看到失败，而 prod 侧根本没有运行日志接口。
    """
    admin, member = two_auth_clients
    uid = await _uid(member, "bob")
    sf, occupied, target = await _gate_case(
        member.app, uid, limit=1, dev="running", prod="stopped", app_tag="sync")
    try:
        async with sf() as s:
            before = (await s.execute(text(
                "SELECT count(*) FROM deploy_record WHERE app_id=:i"),
                {"i": target})).scalar()
        r = await member.post(f"/api/apps/{target}/publish", json={})
        assert r.status_code == 409, r.text
        async with sf() as s:
            after = (await s.execute(text(
                "SELECT count(*) FROM deploy_record WHERE app_id=:i"),
                {"i": target})).scalar()
            status = (await s.execute(text(
                "SELECT status_prod FROM app_project WHERE id=:i"),
                {"i": target})).scalar()
        assert after == before == 0, "超限发布仍留下了部署记录"
        assert status == "stopped", "超限发布把状态推进了"
    finally:
        await _teardown(sf, occupied)
        await _teardown(sf, target)


async def test_approve_publish_request_is_gated_too(two_auth_clients):
    """第七个入口：`批准发布申请` 即执行发布（ADR-0050），不接这道闸就是最大的绕行面。"""
    admin, member = two_auth_clients
    owner_uid = await _uid(member, "bob")
    sf, occupied, target = await _gate_case(
        member.app, owner_uid, limit=1, dev="running", prod="stopped", app_tag="appr")
    try:
        async with sf() as s:
            req_id = (await s.execute(text(
                "INSERT INTO app_publish_request (app_id, requester_id, status) "
                "VALUES (:a, :r, 'pending') RETURNING id"),
                {"a": target, "r": owner_uid})).scalar()
            await s.commit()
        r = await member.post(f"/api/apps/{target}/publish-requests/{req_id}/approve", json={})
        assert r.status_code == 409, r.text
        assert "上限" in r.json()["detail"], r.json()
        async with sf() as s:
            status = (await s.execute(text(
                "SELECT status FROM app_publish_request WHERE id=:i"),
                {"i": req_id})).scalar()
        assert status == "pending", "闸门失败却已把申请置成 approved"
    finally:
        async with sf() as s:
            await s.execute(text("DELETE FROM app_publish_request WHERE app_id=:i"),
                            {"i": target})
            await s.commit()
        await _teardown(sf, occupied)
        await _teardown(sf, target)


async def test_restart_of_an_already_running_stack_stays_allowed(two_auth_clients, monkeypatch):
    """US-20a 净增规则：刚好跑满额度的人**必须还能重启**自己已有的栈。

    反向断言——若实现成「running 数 ≥ 上限就拒」，跑满额度的人会永远无法重启任何东西，
    那是把自己锁死而不是被治理。动作本身打桩：本用例只关心闸门放不放行。
    """
    import app.deploy.engine as engine

    admin, member = two_auth_clients
    uid = await _uid(member, "bob")
    sf = member.app.state.session_factory
    await _set_limit(sf, uid, 1)
    # 唯一的一格就是目标应用自己（dev 已 running）
    target = await _app(sf, uid, "rs", dev="running")
    calls = []

    async def fake_restart(sf_, app, notify=None):
        calls.append(app["id"])
        return {"ok": True}

    monkeypatch.setattr(engine, "restart_dev", fake_restart)
    try:
        r = await member.post(f"/api/apps/{target}/dev/restart")
        assert r.status_code == 202, r.text
        assert calls == [target], calls
    finally:
        await _teardown(sf, target)


async def test_gate_follows_app_owner_not_the_actor(two_auth_clients):
    """US-20b：额度算在应用所有者头上。管理员把应用共享给普通账号、自己额度不限，
    普通账号去起栈时不该被算成「管理员在起栈」而放行。"""
    admin, member = two_auth_clients
    bob = await _uid(member, "bob")
    alice = await _uid(admin, "alice")   # 只是被共享者的账号 id，用于 app_share
    sf = admin.app.state.session_factory
    await _set_limit(sf, bob, 0)
    # 应用归 bob，alice 只是被共享者
    app_id = await _app(sf, bob, "own", shared_with=alice)
    try:
        # alice（管理员本人）是被共享者：他点启动，占的仍该是 bob 的额度，而不是他的豁免
        r = await admin.post(f"/api/apps/{app_id}/dev/start")
        assert r.status_code == 409, r.text
        assert "上限" in r.json()["detail"], r.json()
    finally:
        await _teardown(sf, app_id)


async def test_admin_owner_is_exempt_from_stack_limit(two_auth_clients, monkeypatch):
    """管理员自己不受闸（ADR-0070），且豁免按 owner 判。起栈动作打桩，只验放行与否。"""
    import app.deploy.engine as engine
    from app.deploy import quota

    admin, member = two_auth_clients
    sf = admin.app.state.session_factory
    alice = await _uid(admin, "alice")
    await _set_limit(sf, alice, 0)
    app_id = await _app(sf, alice, "admex")
    monkeypatch.setattr(engine, "start_dev",
                        lambda sf_, app, notify=None: _accepted())
    try:
        r = await admin.post(f"/api/apps/{app_id}/dev/start")
        assert r.status_code == 202, r.text
        async with sf() as s:
            assert await quota.resolve_limit(s, alice, "quota.max_running_stacks") is None
    finally:
        await _teardown(sf, app_id)


async def _accepted():
    return {"ok": True}
