"""账号治理 · 创建闸（规格 2026-11-23 §3 D / ADR-0068、ADR-0070）。

接缝：`app/deploy/quota.py` 的额度解析 + `app_service.create_app_project` 的入口闸。
刻意不真建 11 个应用：创建一条 app_project 行会供给数据库与目录，慢且有副作用。
限额用「已存在的行数 + 1 次被拒的创建」来验，成功路径交给既有创建用例。
"""
import json

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.asyncio

FAKE_APP = ("INSERT INTO app_project (workspace_id, name, slug, scale, stack, "
            " prod_port, dev_port, owner_user_id) "
            "VALUES (:w, :n, :slug, 'small', 'static', :pp, :dp, :u)")


async def _ws(sf, uid, tag):
    async with sf() as s:
        r = (await s.execute(text(
            "INSERT INTO workspace (name, root_path, owner_user_id) "
            "VALUES (:n, :p, :u) RETURNING id"),
            {"n": f"quota-{tag}", "p": f"/tmp/quota-{tag}-{uid}", "u": uid})).first()
        await s.commit()
    return r[0]


async def _fake_apps(sf, uid, n, tag):
    """铺 n 行已存在的应用（status 一律默认 stopped——伪造 running 会触发启动清扫，用例互挂）。"""
    ws = await _ws(sf, uid, tag)
    async with sf() as s:
        for i in range(n):
            await s.execute(text(FAKE_APP), {
                "w": ws, "n": f"{tag}-{i}", "slug": f"q-{tag}-{uid}-{i}",
                "pp": 8600 + i * 2, "dp": 8601 + i * 2, "u": uid})
        await s.commit()


async def _uid(client, nickname="bob"):
    rows = (await client.get("/api/users")).json()
    return next(r["id"] for r in rows if r["nickname"] == nickname)


async def _set_platform(sf, key, value):
    async with sf() as s:
        await s.execute(text(
            "INSERT INTO app_config (key, value, owner_user_id) VALUES (:k, CAST(:v AS JSONB), NULL) "
            "ON CONFLICT (owner_user_id, key) DO UPDATE SET value = CAST(:v AS JSONB)"),
            {"k": key, "v": json.dumps(value)})
        await s.commit()


# ---------- 额度解析：两级回落 + 非法值 + 管理员豁免 ----------

async def test_resolve_limit_two_tier_and_admin_exempt(two_auth_clients):
    from app.deploy import quota

    admin, member = two_auth_clients
    sf = admin.app.state.session_factory
    uid = await _uid(member)
    admin_id = await _uid(admin, "alice")

    async with sf() as s:
        assert await quota.resolve_limit(s, uid, "quota.max_apps") == 10      # 平台打底
        assert await quota.resolve_limit(s, admin_id, "quota.max_apps") is None  # 管理员豁免
        await s.execute(text(
            "INSERT INTO app_config (key, value, owner_user_id) "
            "VALUES ('quota.max_apps', CAST(:v AS JSONB), :u) "
            "ON CONFLICT (owner_user_id, key) DO UPDATE SET value = CAST(:v AS JSONB)"),
            {"v": json.dumps(3), "u": uid})
        await s.commit()
    async with sf() as s:
        assert await quota.resolve_limit(s, uid, "quota.max_apps") == 3       # 账号覆盖压过平台
        assert await quota.resolve_limit(s, admin_id, "quota.max_apps") is None

    # 删掉覆盖行 → 回落平台默认；再删平台行 → 不限（缺键不炸人，US-27）
    async with sf() as s:
        await s.execute(text("DELETE FROM app_config WHERE key='quota.max_apps' "
                             "AND owner_user_id=:u"), {"u": uid})
        await s.commit()
    async with sf() as s:
        assert await quota.resolve_limit(s, uid, "quota.max_apps") == 10
        await s.execute(text("DELETE FROM app_config WHERE key='quota.max_apps'"))
        await s.commit()
    async with sf() as s:
        assert await quota.resolve_limit(s, uid, "quota.max_apps") is None


@pytest.mark.parametrize("raw", [-1, "abc", 1.5, True, None])
async def test_resolve_limit_illegal_value_means_unlimited(two_auth_clients, raw):
    """库里躺着一个脏值时宁可放行，不可把人锁死——额度读不出来不是用户的错。

    `None` 是「不限」的合法表示，其余四个都是非法形态，一并落到同一个出口。
    """
    from app.deploy import quota

    admin, member = two_auth_clients
    sf = admin.app.state.session_factory
    uid = await _uid(member)
    async with sf() as s:
        await s.execute(text(
            "INSERT INTO app_config (key, value, owner_user_id) "
            "VALUES ('quota.max_apps', CAST(:v AS JSONB), :u) "
            "ON CONFLICT (owner_user_id, key) DO UPDATE SET value = CAST(:v AS JSONB)"),
            {"v": json.dumps(raw), "u": uid})
        await s.commit()
    async with sf() as s:
        assert await quota.resolve_limit(s, uid, "quota.max_apps") is None


async def test_resolve_limit_zero_is_a_real_limit(two_auth_clients):
    """0 是「一个都不许建」，不是「不限」——管理员要用它关人时得有这个工具。"""
    from app.deploy import quota

    admin, member = two_auth_clients
    sf = admin.app.state.session_factory
    uid = await _uid(member)
    async with sf() as s:
        await s.execute(text(
            "INSERT INTO app_config (key, value, owner_user_id) "
            "VALUES ('quota.max_apps', CAST(:v AS JSONB), :u) "
            "ON CONFLICT (owner_user_id, key) DO UPDATE SET value = CAST(:v AS JSONB)"),
            {"v": json.dumps(0), "u": uid})
        await s.commit()
    async with sf() as s:
        assert await quota.resolve_limit(s, uid, "quota.max_apps") == 0


# ---------- 创建闸：拦在 allocate_pair 之前 ----------

async def test_create_rejected_at_limit_before_touching_ports(two_auth_clients, monkeypatch):
    """第 2 个应用被拒，且**端口一对都没被占**：判定必须早于 allocate_pair（ADR-0070）。

    端口段是全局共享的稀缺资源（约 94 对，不分辨账号）。先占端口再拒，
    等于让每次越限尝试白吃一格，几个人反复试就能把平台自己的端口池耗尽。
    """
    from app.deploy import ports
    from app.services import app_service

    admin, member = two_auth_clients
    sf = admin.app.state.session_factory
    uid = await _uid(member)
    await _fake_apps(sf, uid, 1, "gate")
    await _set_platform(sf, "quota.max_apps", 1)

    calls = []
    monkeypatch.setattr(app_service, "allocate_pair",
                        lambda *a, **k: calls.append(a) or (8700, 8701))

    r = await member.post("/api/apps", json={
        "name": "第二个", "slug": "gatesecond", "scale": "small", "stack": "static"})
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert "1" in detail and "上限" in detail, detail
    assert calls == [], "超限后仍去分配了端口"

    async with sf() as s:
        cnt = (await s.execute(text(
            "SELECT count(*) FROM app_project WHERE owner_user_id=:u"), {"u": uid})).scalar()
    assert cnt == 1, cnt


async def test_zero_limit_blocks_the_first_app(two_auth_clients):
    """额度 0 = 一个都不许建。这是「按人关掉」的实际工具，不是边界装饰。"""
    admin, member = two_auth_clients
    sf = admin.app.state.session_factory
    uid = await _uid(member)
    await _set_platform(sf, "quota.max_apps", 0)

    r = await member.post("/api/apps", json={
        "name": "零额度", "slug": "gatezero", "scale": "small", "stack": "static"})
    assert r.status_code == 409, r.text


async def test_admin_not_gated_by_platform_default(two_auth_clients, monkeypatch):
    """管理员豁免：平台默认 1 对普通账号生效，对管理员自己不生效。"""
    admin, member = two_auth_clients
    sf = admin.app.state.session_factory
    uid = await _uid(admin, "alice")
    await _fake_apps(sf, uid, 3, "adm")
    await _set_platform(sf, "quota.max_apps", 1)

    async with sf() as s:
        cnt = (await s.execute(text(
            "SELECT count(*) FROM app_project WHERE owner_user_id=:u"), {"u": uid})).scalar()
    assert cnt == 3, "夹具没铺上"

    from app.deploy import quota
    async with sf() as s:
        assert await quota.resolve_limit(s, uid, "quota.max_apps") is None


async def test_below_limit_creation_is_allowed_through_the_gate(two_auth_clients, monkeypatch):
    """**放行路径必须单独测**：只测「超限被拒」会让「一律拒」这种实现活下来。

    这里把 `allocate_pair` 换成一个抛哨兵错误的替身：闸门放行才会走到它，
    于是「报哨兵」= 放行，「报上限」= 被拦，两者都可观测，且不必真的建出一个应用。
    """
    from app.deploy import quota
    from app.services import app_service

    admin, member = two_auth_clients
    sf = admin.app.state.session_factory
    uid = await _uid(member, "bob")
    await _fake_apps(sf, uid, 1, "under")
    await _set_platform(sf, "quota.max_apps", 2)

    async def sentinel(*a, **k):
        raise app_service.DeployError("SENTINEL-past-the-gate")

    monkeypatch.setattr(app_service, "allocate_pair", sentinel)
    with pytest.raises(app_service.DeployError) as ei:
        await app_service.create_app_project(
            sf, name="第二个", slug="undersecond", scale="small", stack="static",
            owner_user_id=uid)
    assert "SENTINEL" in str(ei.value), str(ei.value)

    # 同一账号在同一处闸上门禁自己放行（上面那条是端到端，这条钉住判定本体）
    async with sf() as s:
        await quota.ensure_app_creation_allowed(sf, uid)


async def test_gate_lives_in_service_so_install_cannot_bypass(two_auth_clients, monkeypatch):
    """闸在 `create_app_project` 里，不在端点里：商店安装调同一个函数，天然没有旁路（US-25）。"""
    from app.services import app_service

    admin, member = two_auth_clients
    sf = admin.app.state.session_factory
    uid = await _uid(member)
    await _fake_apps(sf, uid, 1, "svc")
    await _set_platform(sf, "quota.max_apps", 1)

    calls = []
    monkeypatch.setattr(app_service, "allocate_pair",
                        lambda *a, **k: calls.append(a) or (8710, 8711))
    with pytest.raises(app_service.DeployError) as ei:
        await app_service.create_app_project(
            sf, name="服务层直连", slug="svcgate", scale="small", stack="static",
            owner_user_id=uid)
    assert ei.value.status == 409
    assert calls == []
