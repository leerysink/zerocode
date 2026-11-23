"""账号治理 · 邀请码与首号自举（规格 2026-11-23 §3 B / ADR-0069）。

接缝 S2：`POST /api/auth/register` 端到端。空库免码、有库要码、码一用即废、
失败不烧码——这四条全在同一个端点上验，不打内部函数。
"""
import asyncio

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

pytestmark = pytest.mark.asyncio


async def _new_client(app_):
    return AsyncClient(transport=ASGITransport(app=app_), base_url="http://t")


async def _register(app_, nickname, password="pw", invite=None):
    c = await _new_client(app_)
    try:
        body = {"nickname": nickname, "password": password}
        if invite is not None:
            body["invite_code"] = invite
        return await c.post("/api/auth/register", json=body)
    finally:
        await c.aclose()


async def _clear_actors(app):
    """空库场景的前置：首号自举只在「一个账号都没有」时成立，而测试库不是每次都被清干净
    （上一轮被中止时会留下 actor 行）。"""
    async with app.state.session_factory() as s:
        await s.execute(text("TRUNCATE actor_session, actor RESTART IDENTITY CASCADE"))
        await s.commit()


async def _codes(admin):
    r = await admin.get("/api/admin/invites")
    assert r.status_code == 200, f"{r.status_code} {r.text}"
    assert isinstance(r.json(), list), r.json()
    return r.json()


async def _issue(admin, **kw):
    r = await admin.post("/api/admin/invites", json=kw)
    assert r.status_code == 200, r.text
    return r.json()


# ---------- 首号自举 ----------

async def test_empty_platform_registers_first_account_without_code(anon_client):
    """空库上第一个账号免码注册，且照常当选创始人（ADR-0069 §2 保住 ADR-0003 那条事务）。"""
    await _clear_actors(anon_client.app)
    assert (await anon_client.get("/api/auth/registration-mode")).json() ==         {"require_invite": False}
    r = await anon_client.post("/api/auth/register",
                               json={"nickname": "首号", "password": "pw"})
    assert r.status_code == 200, r.text
    assert r.json()["is_founder"] is True

    mode = (await anon_client.get("/api/auth/registration-mode")).json()
    assert mode["require_invite"] is True   # 一号落地即关门


async def test_second_registration_without_code_is_refused(client):
    """已有账号的库上不带码：403，且不产生任何 actor 行。"""
    before = len((await client.get("/api/users")).json())
    r = await _register(client.app, "后来者")
    assert r.status_code == 403, r.text
    assert "邀请码" in r.json()["detail"], r.json()
    assert len((await client.get("/api/users")).json()) == before


# ---------- 签发 / 核销 / 作废 ----------

async def test_code_is_single_use(client):
    issued = await _issue(client)
    assert issued["code"]

    ok = await _register(client.app, "小明", invite=issued["code"])
    assert ok.status_code == 200, ok.text

    again = await _register(client.app, "小红", invite=issued["code"])
    assert again.status_code in (403, 409), again.text

    rows = await _codes(client)
    used = next(r for r in rows if r["id"] == issued["id"])
    assert used["status"] == "used"
    assert used["used_by_nickname"] == "小明"


async def test_unknown_code_is_refused(client):
    r = await _register(client.app, "猜码的", invite="0" * 32)
    assert r.status_code == 403, r.text


async def test_failed_registration_does_not_burn_the_code(client):
    """昵称撞车（409）时码必须原封不动：失败不该消耗名额（US-10）。"""
    await _register(client.app, "占用者", invite=(await _issue(client))["code"])
    issued = await _issue(client)

    dup = await _register(client.app, "占用者", invite=issued["code"])
    assert dup.status_code == 409, dup.text
    assert (await _codes(client))[0]  # 列表可读
    status = next(r for r in await _codes(client) if r["id"] == issued["id"])["status"]
    assert status == "unused", "撞昵称失败却把码烧掉了"

    retry = await _register(client.app, "换个名", invite=issued["code"])
    assert retry.status_code == 200, retry.text


async def test_concurrent_registration_on_one_code_only_one_wins(client):
    """两人同抢一枚码：条件核销保证恰好一个成功、库里只有一条 used_by。"""
    issued = await _issue(client)
    results = await asyncio.gather(
        _register(client.app, "甲", invite=issued["code"]),
        _register(client.app, "乙", invite=issued["code"]),
    )
    codes = sorted(r.status_code for r in results)
    assert codes == [200, 409] or codes == [200, 403], codes

    async with client.app.state.session_factory() as s:
        n = (await s.execute(text(
            "SELECT count(*) FROM invite_code WHERE id=:i AND used_at IS NOT NULL"),
            {"i": issued["id"]})).scalar()
        actors = (await s.execute(text("SELECT count(*) FROM actor"))).scalar()
    assert n == 1, n
    assert actors == 2, actors   # 首号 + 恰好一个新账号


async def test_revoke_blocks_use_and_used_code_cannot_be_revoked(client):
    issued = await _issue(client)
    r = await client.post(f"/api/admin/invites/{issued['id']}/revoke")
    assert r.status_code == 200, r.text
    assert (await _register(client.app, "已被作废", invite=issued["code"])).status_code == 403

    used = await _issue(client)
    await _register(client.app, "用掉了", invite=used["code"])
    r2 = await client.post(f"/api/admin/invites/{used['id']}/revoke")
    assert r2.status_code == 409, r2.text


async def test_code_plaintext_never_leaves_the_list(client):
    """码是新的入口凭据：明文只在签发响应里出现一次，列表一律回掩码。"""
    issued = await _issue(client)
    body = await _codes(client)
    assert issued["code"] not in str(body), "列表里漏出了完整明文码"
    row = next(r for r in body if r["id"] == issued["id"])
    assert row["code_masked"].endswith(issued["code"][-4:])
    assert issued["code"] not in row["code_masked"]


async def test_invite_endpoints_are_admin_only(two_auth_clients):
    admin, member = two_auth_clients
    assert (await member.post("/api/admin/invites", json={})).status_code == 403
    assert (await member.get("/api/admin/invites")).status_code == 403
    rows = await _codes(admin)
    if rows:
        assert (await member.post(f"/api/admin/invites/{rows[0]['id']}/revoke")).status_code == 403


async def test_registration_mode_is_public_and_read_only(anon_client):
    """未登录也要能问到「现在要不要码」——登录页据此决定那个字段出不出现。

    空库答 False（首号自举窗口，第一人不该被一枚无处索取的码挡住），
    首位落地后立刻翻成 True。这个翻转就是 ADR-0069 的全部用户可见语义。
    """
    await _clear_actors(anon_client.app)
    r = await anon_client.get("/api/auth/registration-mode")
    assert r.status_code == 200, r.text
    assert r.json() == {"require_invite": False}   # 空库 = 首位自举窗口

    await anon_client.post("/api/auth/register",
                           json={"nickname": "首位", "password": "pw"})
    assert (await anon_client.get("/api/auth/registration-mode")).json() == \
        {"require_invite": True}


async def test_banned_nickname_cannot_be_reregistered_around_the_gate(client):
    """关门只挡「新进来」，不改变昵称唯一约束：被封者的昵称仍不可被抢注。"""
    issued = await _issue(client)
    assert (await _register(client.app, "会被封", invite=issued["code"])).status_code == 200
    uid = next(r["id"] for r in (await client.get("/api/users")).json()
               if r["nickname"] == "会被封")
    await client.post(f"/api/admin/accounts/{uid}/ban", json={"reason": "x"})

    again = await _issue(client)
    r = await _register(client.app, "会被封", invite=again["code"])
    assert r.status_code == 409, r.text


async def test_code_never_reaches_the_log(client, caplog):
    """邀请码是入口凭据：失败路径也不许把它打进日志（日志的可见面比响应宽得多）。

    这条今天几乎必然通过——它防的是将来有人加一句 `logger.info(body)` 把请求体整包写出去。
    """
    import logging

    bogus = "deadbeef" * 4
    with caplog.at_level(logging.DEBUG):
        assert (await _register(client.app, "带脏码", invite=bogus)).status_code == 403
        await _register(client.app, "有效码持有者", invite=(await _issue(client))["code"])
    assert bogus not in caplog.text
