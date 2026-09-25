# tests/test_founder_takeover.py
"""Ticket 004：首个注册账号 = 创始人，同事务承接全部无主存量；CLI 口令重置。

验收靶心（spec §3 故事 3/7）：
- 首注册 is_founder=true，无主 workspace/app_project/model_config 与全局 llm.* 配置划归自己；
  dbcred.%、agent.* 等平台键保持无主（NULL=平台级）。
- 第二个注册者不触发承接，也拿不到别人资源。
- reset_password CLI 服务函数改哈希后新口令可登录、旧口令失效；未知昵称返回 False。
承接必须单事务：中途失败不留半划归（由 register 端点的事务边界保证，行为侧断言终态）。
"""
import pytest
from sqlalchemy import text

pytestmark = pytest.mark.asyncio


async def _seed_ownerless(app) -> dict:
    """直插无主存量行（模拟老开发库升级前状态——写侧 API 已一律落 owner，只能旁路造）。"""
    ids = {}
    async with app.state.session_factory() as s:
        ids["ws"] = (await s.execute(text(
            "INSERT INTO workspace (name, root_path) VALUES ('老工作区', 'D:/legacy/ws') "
            "RETURNING id"))).scalar()
        await s.execute(text(
            "INSERT INTO app_project (workspace_id, name, slug, scale, stack, prod_port, dev_port) "
            "VALUES (:w, '老应用', 'legacyapp', 'small', 'vue3-fastapi', 8290, 8291)"),
            {"w": ids["ws"]})
        await s.execute(text(
            "INSERT INTO model_config (name, base_url, api_key, model, is_default) "
            "VALUES ('老默认', 'https://legacy.invalid/v1', 'sk-legacy', 'm0', true)"))
        await s.commit()
    return ids


async def test_首注册创始人承接全部无主存量(anon_client):
    await _seed_ownerless(anon_client.app)
    r = await anon_client.post("/api/auth/register",
                               json={"nickname": "first", "password": "pw-first"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["is_founder"] is True
    fid = body["id"]
    async with anon_client.app.state.session_factory() as s:
        for table in ("workspace", "app_project", "model_config"):
            owners = (await s.execute(text(
                f"SELECT owner_user_id FROM {table}"))).scalars().all()  # noqa: S608 白名单表名
            assert owners and all(o == fid for o in owners), table
        llm_owner = (await s.execute(text(
            "SELECT owner_user_id FROM app_config WHERE key LIKE 'llm.%'"))).scalars().all()
        assert llm_owner and all(o == fid for o in llm_owner)
        # agent.*/deploy.*/store.* 平台键不划归（NULL = 平台级）
        plat_owner = (await s.execute(text(
            "SELECT DISTINCT owner_user_id FROM app_config "
            "WHERE key LIKE 'agent.%' OR key LIKE 'deploy.%'"))).scalars().all()
        assert plat_owner == [None]


async def test_第二个注册者不触发承接(anon_client):
    await _seed_ownerless(anon_client.app)
    r1 = await anon_client.post("/api/auth/register",
                                json={"nickname": "first", "password": "pw1"})
    first_id = r1.json()["id"]
    # 首位承接后新登记的资源属于自己，第二注册者不得染指
    r2 = await anon_client.post("/api/auth/register",
                                json={"nickname": "second", "password": "pw2"})
    assert r2.status_code == 200, r2.text
    assert r2.json()["is_founder"] is False
    async with anon_client.app.state.session_factory() as s:
        owners = (await s.execute(text("SELECT owner_user_id FROM workspace"))).scalars().all()
        assert owners and all(o == first_id for o in owners)


async def test_空库存量下首注册也仅是创始人无副作用(anon_client):
    r = await anon_client.post("/api/auth/register",
                               json={"nickname": "solo", "password": "pw"})
    assert r.status_code == 200 and r.json()["is_founder"] is True


async def test_cli重置口令后新密码可登录旧密码失效(client):
    from app import cli
    sf = client.app.state.session_factory
    assert await cli.reset_password(sf, "owner", "brand-new-pw") is True
    r = await client.post("/api/auth/login",
                          json={"nickname": "owner", "password": "brand-new-pw"})
    assert r.status_code == 200, r.text
    r2 = await client.post("/api/auth/login",
                           json={"nickname": "owner", "password": "owner-pw"})
    assert r2.status_code == 401


async def test_cli重置未知昵称返回失败(client):
    from app import cli
    assert await cli.reset_password(client.app.state.session_factory,
                                    "查无此人", "pw") is False
