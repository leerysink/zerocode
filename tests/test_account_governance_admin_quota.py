"""账号治理 · 治理台的配额读写（规格 2026-11-23 §3 D / ADR-0068）。

配额从「只有一个平台默认」变成「能按人管」：名册上带实时用量，改一个数字只作用于那个人。
"""
import json

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.asyncio


async def _uid(client, nickname):
    rows = (await client.get("/api/users")).json()
    return next(r["id"] for r in rows if r["nickname"] == nickname)


async def _raw(sf, key, owner):
    async with sf() as s:
        return (await s.execute(text(
            "SELECT value FROM app_config WHERE key=:k "
            "AND owner_user_id IS NOT DISTINCT FROM :u"), {"k": key, "u": owner})).scalar()


async def test_accounts_readout_carries_usage_and_limits(two_auth_clients):
    """名册每行给出「已创建 / 上限」「已启动 / 上限」；未覆盖的账号读的是平台默认。"""
    admin, member = two_auth_clients
    rows = (await admin.get("/api/admin/accounts")).json()
    bob = next(r for r in rows if r["nickname"] == "bob")
    assert bob["apps_used"] == 0 and bob["stacks_running"] == 0
    assert bob["apps_limit"] == 10 and bob["stacks_limit"] == 4
    # 管理员豁免在数据层就是 null，界面据此显示「—」而不是一个假数字
    alice = next(r for r in rows if r["nickname"] == "alice")
    assert alice["apps_limit"] is None and alice["is_founder"] is True


async def test_admin_sets_per_account_limits(two_auth_clients):
    admin, member = two_auth_clients
    sf = admin.app.state.session_factory
    bob = await _uid(member, "bob")

    r = await admin.put(f"/api/admin/accounts/{bob}/quota",
                        json={"max_apps": 2, "max_running_stacks": 1})
    assert r.status_code == 200, r.text
    assert await _raw(sf, "quota.max_apps", owner=bob) == 2
    assert await _raw(sf, "quota.max_running_stacks", owner=bob) == 1

    # 只影响他：平台打底行一字不动
    assert await _raw(sf, "quota.max_apps", owner=None) == 10
    rows = (await admin.get("/api/admin/accounts")).json()
    assert next(x for x in rows if x["nickname"] == "bob")["apps_limit"] == 2


async def test_null_means_unlimited_not_fallback(two_auth_clients):
    """显式 null = 「这一档不限」，与「没有覆盖行（回落平台默认）」是两件事（ADR-0068）。"""
    admin, member = two_auth_clients
    sf = admin.app.state.session_factory
    bob = await _uid(member, "bob")
    await admin.put(f"/api/admin/accounts/{bob}/quota",
                    json={"max_apps": 2, "max_running_stacks": 1})
    r = await admin.put(f"/api/admin/accounts/{bob}/quota",
                        json={"max_apps": None, "max_running_stacks": None})
    assert r.status_code == 200, r.text
    rows = (await admin.get("/api/admin/accounts")).json()
    bob_row = next(x for x in rows if x["nickname"] == "bob")
    assert bob_row["apps_limit"] is None and bob_row["stacks_limit"] is None
    # 值行存在且是 JSON null（不是被删掉），所以它不会又回落到平台默认
    assert await _raw(sf, "quota.max_apps", owner=bob) is None
    async with sf() as s:
        present = (await s.execute(text(
            "SELECT count(*) FROM app_config WHERE key='quota.max_apps' "
            "AND owner_user_id=:u"), {"u": bob})).scalar()
    assert present == 1, present


async def test_quota_endpoint_rejects_illegal_values(two_auth_clients):
    """负数与 1.5 一律 400 且不落库——读侧对脏值的态度是「当不限」，写侧不能制造脏值。"""
    admin, member = two_auth_clients
    sf = admin.app.state.session_factory
    bob = await _uid(member, "bob")
    for bad in (-1, 1.5, "many", [3]):
        r = await admin.put(f"/api/admin/accounts/{bob}/quota",
                            json={"max_apps": bad, "max_running_stacks": 1})
        assert r.status_code == 400, f"{bad!r} → {r.status_code} {r.text}"
    assert await _raw(sf, "quota.max_apps", owner=bob) is None

    r2 = await admin.put(f"/api/admin/accounts/{bob}/quota",
                         json={"max_apps": 0, "max_running_stacks": 0})
    assert r2.status_code == 200, r2.text   # 0 是合法的「一个都不许」，不是非法值


async def test_lowering_limit_never_retroactively_reclaims(two_auth_clients):
    """US-23：下调额度**不追溯**——不删应用、不停栈，他只是下一次启动前得自己降下来。

    这条是「不追溯」的唯一防线：若实现顺手加了个回收器，管理员改一个数字就会
    凭空打断别人正在跑的活。
    """
    admin, member = two_auth_clients
    sf = admin.app.state.session_factory
    bob = await _uid(member, "bob")
    async with sf() as s:
        ws = (await s.execute(text(
            "INSERT INTO workspace (name, root_path, owner_user_id) "
            "VALUES ('retire-ws', '/tmp/retire-bob', :u) RETURNING id"),
            {"u": bob})).scalar()
        for i in range(3):
            await s.execute(text(
                "INSERT INTO app_project (workspace_id, name, slug, scale, stack, "
                " prod_port, dev_port, status_dev, owner_user_id) VALUES "
                "(:w, :n, :slug, 'small', 'static', :pp, :dp, 'stopped', :u)"),
                {"w": ws, "n": f"rt{i}", "slug": f"retire{i}", "pp": 8750 + i * 2,
                 "dp": 8751 + i * 2, "u": bob})
        await s.commit()

    r = await admin.put(f"/api/admin/accounts/{bob}/quota",
                        json={"max_apps": 1, "max_running_stacks": 1})
    assert r.status_code == 200, r.text

    rows = (await admin.get("/api/admin/accounts")).json()
    bob_row = next(x for x in rows if x["nickname"] == "bob")
    assert bob_row["apps_used"] == 3, "超额的应用被动过了"
    async with sf() as s:
        left = (await s.execute(text(
            "SELECT count(*) FROM app_project WHERE owner_user_id=:u"), {"u": bob})).scalar()
        recs = (await s.execute(text(
            "SELECT count(*) FROM deploy_record d JOIN app_project p ON p.id=d.app_id "
            "WHERE p.owner_user_id=:u"), {"u": bob})).scalar()
    assert left == 3, left
    assert recs == 0, "下调额度触发了停栈动作"


async def test_member_cannot_edit_any_quota(two_auth_clients):
    """自提额必须挡在治理端点上（`/api/config` 那一半在 006 已守）。"""
    admin, member = two_auth_clients
    alice = await _uid(admin, "alice")
    r = await member.put(f"/api/admin/accounts/{alice}/quota",
                         json={"max_apps": 99, "max_running_stacks": 99})
    assert r.status_code == 403, r.text
    r2 = await member.put(f"/api/admin/accounts/1/quota",
                          json={"max_apps": 99, "max_running_stacks": 99})
    assert r2.status_code == 403, r2.text


async def test_quota_edit_rejects_unknown_account(two_auth_clients):
    admin, member = two_auth_clients
    r = await admin.put("/api/admin/accounts/999999/quota",
                        json={"max_apps": 1, "max_running_stacks": 1})
    assert r.status_code == 404, r.text
