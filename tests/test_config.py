import pytest

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
