"""Task 2：模型库 CRUD（掩码回显 / 默认唯一 / 掩码哨兵不改密钥）。"""
import pytest

async def _create(client, **kw):
    payload = {"name": "m1", "base_url": "http://x/v1", "api_key": "sk-real", "model": "deepseek-chat", **kw}
    return (await client.post("/api/models", json=payload)).json()

async def test_create_and_list_masks_key(client):
    mid = (await _create(client))["id"]
    data = (await client.get("/api/models")).json()
    assert len(data) == 1 and data[0]["id"] == mid
    assert data[0]["api_key"] == "sk-***"  # 列表永远掩码

async def test_default_unique(client):
    await _create(client, is_default=True)
    m2 = await _create(client, name="m2", is_default=True)
    rows = (await client.get("/api/models")).json()
    defaults = [r for r in rows if r["is_default"]]
    assert len(defaults) == 1 and defaults[0]["id"] == m2["id"]

async def test_put_masked_key_keeps_old(client):
    mid = (await _create(client))["id"]
    await client.put(f"/api/models/{mid}", json={
        "name": "m1b", "base_url": "http://y/v1", "api_key": "sk-***", "model": "m2"})
    async with client.app.state.session_factory() as s:
        from sqlalchemy import text
        key = (await s.execute(text("SELECT api_key FROM model_config WHERE id=:i"), {"i": mid})).scalar()
    assert key == "sk-real"  # 掩码哨兵=不改

async def test_delete(client):
    mid = (await _create(client))["id"]
    assert (await client.delete(f"/api/models/{mid}")).json()["ok"] is True
    assert (await client.get("/api/models")).json() == []
