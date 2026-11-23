import os

import pytest


def test_no_proxy_ipv6_brackets_normalized(monkeypatch):
    """NO_PROXY 带 `[::1]` 会让 httpx 构造客户端即抛 InvalidURL（技能安装 500 的根因）。"""
    import httpx

    from app.config import normalize_no_proxy

    monkeypatch.setenv("NO_PROXY", " localhost, [::1] ,::1 ")
    normalize_no_proxy()
    assert os.environ["NO_PROXY"] == "localhost,::1,::1"
    httpx.Client().close()  # 归一化后构造不再抛错

@pytest.mark.asyncio
async def test_get_config_masked(client):
    r = await client.get("/api/config")
    assert r.status_code == 200
    cfg = r.json()
    assert cfg["llm.base_url"].startswith("https://")
    assert cfg["agent.ask_timeout_seconds"] == 0

@pytest.mark.asyncio
async def test_put_config_partial_and_mask_sentinel(client):
    try:
        r = await client.put("/api/config", json={"llm.model": "deepseek-reasoner", "llm.api_key": "sk-***"})
        assert r.status_code == 200
        r2 = await client.put("/api/config", json={"llm.api_key": "sk-real-key"})
        assert r2.status_code == 200
        got = (await client.get("/api/config")).json()
        assert got["llm.model"] == "deepseek-reasoner"
        assert got["llm.api_key"] == "sk-***"  # 脱敏
        # pg 状态
        r3 = await client.get("/api/config/pg-status")
        assert r3.status_code == 200 and r3.json()["ok"] is True
    finally:
        # 还原 app_config 种子（llm.model="deepseek-chat"、llm.api_key=""），
        # 避免 PUT 写入的值永久留在测试库造成用例间顺序依赖
        rf = await client.put("/api/config", json={"llm.model": "deepseek-chat", "llm.api_key": ""})
        assert rf.status_code == 200

@pytest.mark.asyncio
async def test_quota_keys_are_user_scoped(client):
    """`quota.` 归入账号级前缀（ADR-0068）：写入落 owner 行，读侧压过平台默认。

    这里的反向断言是真靶心：若 `quota.` 漏出 USER_PREFIXES，set_many 会把它当平台级键
    写进 owner NULL 行——账号的一次覆盖就此洗掉了全平台打底值，而读侧看起来仍是「对的数」。
    """
    from sqlalchemy import text

    from app.services import config_service

    uid = (await client.get("/api/auth/me")).json()["id"]
    sf = client.app.state.session_factory
    async with sf() as s:
        await config_service.set_many(s, {"quota.max_apps": 3}, owner_user_id=uid,
                                      actor_is_admin=True)
        await s.commit()
    async with sf() as s:
        platform = (await s.execute(text(
            "SELECT value FROM app_config WHERE key='quota.max_apps' "
            "AND owner_user_id IS NULL"))).scalar()
        view = await config_service.get_all(s, owner_user_id=uid)
    assert platform == 10, platform          # 打底行未被账号覆盖洗掉
    assert view["quota.max_apps"] == 3       # 该账号读到自己那一行


@pytest.mark.asyncio
async def test_test_llm_endpoint(client, monkeypatch):
    import app.api.config_api as capi

    class FakeLLM:
        def __init__(self, base_url, api_key, model): pass
        async def chat_stream(self, messages, tools):
            yield {"type": "content_delta", "content": "pong"}
            yield {"type": "done", "message": {"role": "assistant", "content": "pong", "tool_calls": None}}

    monkeypatch.setattr(capi, "OpenAICompatLLM", FakeLLM)
    r = await client.post("/api/config/test-llm",
                          json={"base_url": "http://x/v1", "api_key": "k", "model": "m"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["reply"] == "pong"
