"""Task 2：模型库 CRUD（掩码回显 / 默认唯一 / 掩码哨兵不改密钥）；Ticket 005：kind 列与逐类默认位。"""
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


# ---- kind（Ticket 005 / D-2）----
_MIN = {"name": "m", "base_url": "http://x/v1", "api_key": "sk-real", "model": "vm"}


async def test_create_defaultsToChatKind(client):
    """缺省与显式 null 都算「未传」：存量前端不带 kind 也不能报错。"""
    assert (await _create(client))["kind"] == "chat"
    payload = dict(_MIN, kind=None)
    assert (await client.post("/api/models", json=payload)).json()["kind"] == "chat"


@pytest.mark.parametrize("bad", ["vl", "CHAT", "", "  ", "embedding "])
async def test_create_rejectsUnknownKind(client, bad):
    """脏 kind 落库后就是「谁都查不到的档案」（两头把关的第一头）。"""
    r = await client.post("/api/models", json=dict(_MIN, kind=bad))
    assert r.status_code == 400
    assert "未知的模型类型" in r.text
    assert (await client.get("/api/models")).json() == []  # 拒绝得干净，一行都没落


async def test_list_filtersByKindAndKeepsUnfiltered(client):
    await _create(client, name="chat1")
    await _create(client, name="vec1", kind="embedding")
    only = (await client.get("/api/models", params={"kind": "embedding"})).json()
    assert [r["name"] for r in only] == ["vec1"]
    assert len((await client.get("/api/models")).json()) == 2


async def test_list_rejectsUnknownKindFilter(client):
    assert (await client.get("/api/models", params={"kind": "vl"})).status_code == 400


async def test_defaultPerKindDoNotCrowdEachOther(client):
    """关键回归：清默认位必须带 kind，否则配一个向量模型会把对话默认当场顶掉。"""
    await _create(client, name="chat", is_default=True)
    await _create(client, name="vec", kind="embedding", is_default=True)
    rows = (await client.get("/api/models")).json()
    assert sorted(r["name"] for r in rows if r["is_default"]) == ["chat", "vec"]
    # 同类型内仍然只能有一个默认
    await _create(client, name="vec2", kind="embedding", is_default=True)
    rows = (await client.get("/api/models")).json()
    assert sorted(r["name"] for r in rows if r["is_default"]) == ["chat", "vec2"]


async def test_putWithoutKindKeepsExistingKind(client):
    """最容易被静默破坏的一条：PUT 不带 kind 必须是「不改类型」而不是「刷回 chat」。"""
    mid = (await _create(client, kind="embedding"))["id"]
    body = {"name": "m1b", "base_url": "http://y/v1", "api_key": "sk-***", "model": "m2"}
    assert (await client.put(f"/api/models/{mid}", json=body)).json() == {"ok": True}
    rows = (await client.get("/api/models")).json()
    assert rows[0]["kind"] == "embedding"
    assert rows[0]["name"] == "m1b"


async def test_putChangingKindTakesNewKindDefaultSlot(client):
    """chat→embedding 的改型要把 embedding 那一类的默认位抢过来，且不伤自己原来的 chat 默认。"""
    chat = await _create(client, name="chat")
    vec = await _create(client, name="vec", kind="embedding", is_default=True)
    await _create(client, name="keep", kind="embedding")  # 占位，验证只动默认行
    body = dict(_MIN, name="chat", kind="embedding", is_default=True)
    assert (await client.put(f"/api/models/{chat['id']}", json=body)).json() == {"ok": True}
    rows = {r["id"]: r for r in (await client.get("/api/models")).json()}
    assert rows[chat["id"]]["kind"] == "embedding" and rows[chat["id"]]["is_default"] is True
    assert rows[vec["id"]]["is_default"] is False


@pytest.mark.parametrize("bad", ["vl", "CHAT", ""])
async def test_put_rejectsUnknownKindKeepsRow(client, bad):
    """改型失败必须整行不动：不能先把 kind 刷坏再报错。"""
    mid = (await _create(client, kind="embedding"))["id"]
    assert (await client.put(f"/api/models/{mid}", json=dict(_MIN, kind=bad))).status_code == 400
    rows = (await client.get("/api/models")).json()
    assert rows[0]["kind"] == "embedding" and rows[0]["name"] == "m1"
