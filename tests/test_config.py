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
