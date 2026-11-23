"""账号治理 · 平台默认（规格 2026-11-23 §3 E / ADR-0068）。

接缝 S5：`config_service.set_many` 的写侧分流。本票把「谁能改全平台」收口到管理员，
并补上一条现存缺陷：第二个账号开箱拿不到任何模型配置。
"""
import pytest
from sqlalchemy import text


async def _raw_value(sf, key, owner=None):
    async with sf() as s:
        return (await s.execute(text(
            "SELECT value FROM app_config WHERE key=:k AND owner_user_id IS NOT DISTINCT FROM :o"),
            {"k": key, "o": owner})).scalar()


async def test_config_meta_exposes_user_prefixes(two_auth_clients):
    """设置页靠这份清单决定「可编辑 / 由管理员统一维护」。

    真源必须是后端那份：前端抄一份常量迟早分叉，分叉的症状是普通账号一点保存就整批 403
    （保存是一条 PUT 全量 34 键的链）。所以这里钉的是「后端说了才算」，而不是具体几个前缀。
    """
    from app.services import config_service

    admin, member = two_auth_clients
    r = await member.get("/api/config/meta")
    assert r.status_code == 200, r.text
    assert r.json()["user_prefixes"] == list(config_service.USER_PREFIXES)
    assert "quota." in r.json()["user_prefixes"]


async def test_member_cannot_write_platform_key(two_auth_clients):
    """普通账号改 `agent.*` 从「影响全平台」降级为 403（ADR-0068 推翻了 Ticket 007 的旧裁定）。"""
    admin, member = two_auth_clients
    sf = admin.app.state.session_factory
    before = await _raw_value(sf, "agent.max_tool_rounds")

    r = await member.put("/api/config", json={"agent.max_tool_rounds": 99})
    assert r.status_code == 403, r.text
    assert "管理员" in r.json()["detail"], r.json()
    assert await _raw_value(sf, "agent.max_tool_rounds") == before, "被拒但仍落库"


async def test_mixed_batch_rejected_wholesale(two_auth_clients):
    """一批里夹一个越权键 → 整批不落库。半写会让用户以为「改成功了另一半」。"""
    admin, member = two_auth_clients
    sf = admin.app.state.session_factory
    before_agent = await _raw_value(sf, "agent.max_tool_rounds")

    r = await member.put("/api/config", json={
        "llm.model": "should-not-stick", "agent.max_tool_rounds": 77})
    assert r.status_code == 403, r.text
    assert await _raw_value(sf, "agent.max_tool_rounds") == before_agent
    uid = (await member.get("/api/auth/me")).json()["id"]
    assert await _raw_value(sf, "llm.model", owner=uid) is None, "越权批次里的合法键也被写进去了"


async def test_member_cannot_self_raise_quota(two_auth_clients):
    """`quota.` 形态上是账号级、语义上是「管理员给我的额度」——自提额必须挡（ADR-0068）。"""
    admin, member = two_auth_clients
    sf = admin.app.state.session_factory
    uid = (await member.get("/api/auth/me")).json()["id"]

    r = await member.put("/api/config", json={"quota.max_apps": 999})
    assert r.status_code == 403, r.text
    assert await _raw_value(sf, "quota.max_apps", owner=uid) is None


async def test_admin_writes_platform_rows_via_admin_config(two_auth_clients):
    """治理台的默认读写口：恒写 owner NULL 平台行，管理员的账号覆盖行不受牵连。"""
    admin, member = two_auth_clients
    sf = admin.app.state.session_factory

    r = await admin.put("/api/admin/config", json={"agent.max_tool_rounds": 12})
    assert r.status_code == 200, r.text
    assert await _raw_value(sf, "agent.max_tool_rounds") == 12

    got = await member.get("/api/config")
    assert got.status_code == 200
    assert got.json()["agent.max_tool_rounds"] == 12


async def test_admin_config_read_is_admin_only_and_masked(two_auth_clients):
    admin, member = two_auth_clients
    await admin.put("/api/admin/config", json={
        "llm.base_url": "https://platform.example/v1", "llm.api_key": "sk-platform-secret"})

    r = await member.get("/api/admin/config")
    assert r.status_code == 403, r.text

    body = (await admin.get("/api/admin/config")).json()
    assert body["llm.base_url"] == "https://platform.example/v1"
    assert body["llm.api_key"] != "sk-platform-secret", "平台默认密钥明文出库"
    # 别人的账号级覆盖行不得从平台视图里漏出来
    assert all(not k.startswith("dbcred.") for k in body), body.keys()


async def test_newcomer_inherits_platform_llm_defaults_without_configuring(two_auth_clients):
    """US-29 的真靶心：管理员把模型配在平台层，后来者一个字不改也能读到可用的模型配置。

    这条同时是对注册事务的守护——`is_first` 分支把 owner NULL 的 llm.* **UPDATE 划归**创始人
    （app/api/auth.py），后续注册不得再划走管理员新建的平台行。
    """
    admin, member = two_auth_clients
    sf = admin.app.state.session_factory
    await admin.put("/api/admin/config", json={
        "llm.base_url": "https://shared.example/v1",
        "llm.model": "shared-model",
        "llm.api_key": "sk-shared",
    })
    # 已存在的第二个账号（member）也没配过自己：直接吃到平台默认
    got = (await member.get("/api/config")).json()
    assert got["llm.base_url"] == "https://shared.example/v1"
    assert got["llm.model"] == "shared-model"

    # 再注册第三人：平台行必须仍归平台（owner NULL），否则「改一处、全员生效」当场失效
    from httpx import ASGITransport, AsyncClient
    async with AsyncClient(transport=ASGITransport(app=admin.app), base_url="http://t") as c:
        reg = await c.post("/api/auth/register",
                           json={"nickname": "carol", "password": "pw-carol"})
        assert reg.status_code == 200, reg.text
        carol_cfg = (await c.get("/api/config")).json()
    assert carol_cfg["llm.model"] == "shared-model"
    assert await _raw_value(sf, "llm.model") == "shared-model", "平台默认行被注册事务划走了"

    # 第三人若自己覆盖，只影响他自己
    async with AsyncClient(transport=ASGITransport(app=admin.app), base_url="http://t") as c2:
        await c2.post("/api/auth/login", json={"nickname": "carol", "password": "pw-carol"})
        await c2.put("/api/config", json={"llm.model": "carol-own"})
        assert (await c2.get("/api/config")).json()["llm.model"] == "carol-own"
    assert await _raw_value(sf, "llm.model") == "shared-model"
    assert (await admin.get("/api/admin/config")).json()["llm.model"] == "shared-model"
