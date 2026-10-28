import pytest
from app.services.message_service import add_message

async def _mk_ws(client):
    import tempfile
    tmp = tempfile.mkdtemp()
    return (await client.post("/api/workspaces", json={"root_path": tmp})).json()

@pytest.mark.asyncio
async def test_session_lifecycle(client):
    ws = await _mk_ws(client)
    s = (await client.post("/api/sessions", json={"workspace_id": ws["id"]})).json()
    assert s["title"] == "新会话"
    assert (await client.patch(f"/api/sessions/{s['id']}", json={"title": "改个名"})).status_code == 200
    lst = (await client.get(f"/api/sessions?workspace_id={ws['id']}")).json()
    assert lst[0]["title"] == "改个名"
    msgs = (await client.get(f"/api/sessions/{s['id']}/messages")).json()
    assert msgs == []
    assert (await client.delete(f"/api/sessions/{s['id']}")).status_code == 200

@pytest.mark.asyncio
async def test_message_seq_increments(client):
    ws = await _mk_ws(client)
    s = (await client.post("/api/sessions", json={"workspace_id": ws["id"]})).json()
    sf = client.app.state.session_factory
    m1 = await add_message(sf, s["id"], "user", content="你好")
    m2 = await add_message(sf, s["id"], "assistant", content="你好！")
    assert m2["seq"] == m1["seq"] + 1
    msgs = (await client.get(f"/api/sessions/{s['id']}/messages")).json()
    assert [m["role"] for m in msgs] == ["user", "assistant"]

@pytest.mark.asyncio
async def test_message_jsonb_roundtrip(client):
    # add_message 的 json.dumps + CAST AS JSONB 写入 → 回读必须还原为等价 Python 对象
    ws = await _mk_ws(client)
    s = (await client.post("/api/sessions", json={"workspace_id": ws["id"]})).json()
    sf = client.app.state.session_factory
    await add_message(sf, s["id"], "assistant", content=None, tool_call_id="call_1",
                      tool_name="write_file",
                      tool_args={"tool_calls": [{"id": "call_1"}]},
                      tool_result={"ok": True, "path": "a.txt"})
    msgs = (await client.get(f"/api/sessions/{s['id']}/messages")).json()
    assert len(msgs) == 1
    m = msgs[0]
    assert m["role"] == "assistant"
    assert m["content"] is None
    assert m["tool_call_id"] == "call_1"
    assert m["tool_args"]["tool_calls"][0]["id"] == "call_1"
    assert m["tool_result"]["ok"] is True
    assert m["tool_result"]["path"] == "a.txt"

@pytest.mark.asyncio
async def test_file_changes_endpoint(client, tmp_path):
    from sqlalchemy import text as sql
    ws = (await client.post("/api/workspaces", json={"root_path": str(tmp_path)})).json()
    s = (await client.post("/api/sessions", json={"workspace_id": ws["id"]})).json()
    async with client.app.state.session_factory() as db:
        await db.execute(sql(
            "INSERT INTO file_change (session_id, path, action, before_content, after_content, diff) "
            "VALUES (:sid, 'a.txt', 'create', '', 'hi', '--- a/a.txt')"), {"sid": s["id"]})
        await db.commit()
    rows = (await client.get(f"/api/sessions/{s['id']}/file-changes")).json()
    assert len(rows) == 1 and rows[0]["path"] == "a.txt"

# ---------- 交互增强：会话详情 / 模型切换 / messages 带 reasoning ----------

async def test_session_detail(client):
    ws = (await client.post("/api/workspaces", json={"root_path": "D:/tmp"})).json()
    sid = (await client.post("/api/sessions", json={"workspace_id": ws["id"]})).json()["id"]
    d = (await client.get(f"/api/sessions/{sid}")).json()
    assert d["todo_state"] == [] and d["model_config_id"] is None
    assert (await client.get("/api/sessions/999999")).status_code == 404

async def test_patch_session_model(client):
    ws = (await client.post("/api/workspaces", json={"root_path": "D:/tmp"})).json()
    sid = (await client.post("/api/sessions", json={"workspace_id": ws["id"]})).json()["id"]
    mc = (await client.post("/api/models", json={
        "name": "m", "base_url": "http://x/v1", "model": "g"})).json()
    r = await client.patch(f"/api/sessions/{sid}/model", json={"model_config_id": mc["id"]})
    assert r.json()["ok"] is True
    d = (await client.get(f"/api/sessions/{sid}")).json()
    assert d["model_config_id"] == mc["id"]
    # 回退默认：null
    await client.patch(f"/api/sessions/{sid}/model", json={"model_config_id": None})
    assert ((await client.get(f"/api/sessions/{sid}")).json())["model_config_id"] is None
    # 不存在/他人的模型 → 404（Ticket 007 越权统一口径，不泄露存在性）
    r = await client.patch(f"/api/sessions/{sid}/model", json={"model_config_id": 999})
    assert r.status_code == 404

async def test_messages_include_reasoning(client):
    ws = (await client.post("/api/workspaces", json={"root_path": "D:/tmp"})).json()
    sid = (await client.post("/api/sessions", json={"workspace_id": ws["id"]})).json()["id"]
    from app.services.message_service import add_message
    await add_message(client.app.state.session_factory, sid, "assistant",
                      content="答", reasoning="想了想")
    msgs = (await client.get(f"/api/sessions/{sid}/messages")).json()
    assert msgs[0]["reasoning"] == "想了想"

# ---------- 图片附件（消息绑定 / 会话详情 / 回显） ----------

async def _mk_ws_session(client, tmp_path):
    ws = (await client.post("/api/workspaces", json={"root_path": str(tmp_path)})).json()
    return ws, (await client.post("/api/sessions", json={"workspace_id": ws["id"]})).json()


async def test_session_detail_effective_model_sources(client, tmp_path):
    ws, sess = await _mk_ws_session(client, tmp_path)
    sid = sess["id"]
    d = (await client.get(f"/api/sessions/{sid}")).json()
    assert d["effective_model"] == {
        "id": None, "name": "deepseek-chat", "model": "deepseek-chat",
        "supports_vision": False, "source": "global"}

    dft = (await client.post("/api/models", json={
        "name": "dft", "base_url": "http://x/v1", "api_key": "k",
        "model": "dft-m", "is_default": True, "supports_vision": True})).json()
    d = (await client.get(f"/api/sessions/{sid}")).json()
    assert d["effective_model"] == {
        "id": dft["id"], "name": "dft", "model": "dft-m",
        "supports_vision": True, "source": "default"}

    bound = (await client.post("/api/models", json={
        "name": "bound", "base_url": "http://x/v1", "api_key": "k",
        "model": "bound-m", "supports_vision": False})).json()
    r = await client.patch(f"/api/sessions/{sid}/model", json={"model_config_id": bound["id"]})
    assert r.status_code == 200
    assert r.json()["effective_model"] == {
        "id": bound["id"], "name": "bound", "model": "bound-m",
        "supports_vision": False, "source": "session"}


async def test_messages_include_attachments(client, tmp_path, monkeypatch):
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path / "data"))
    from app.services.message_service import add_message
    ws, sess = await _mk_ws_session(client, tmp_path)
    sid = sess["id"]
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    up = await client.post(f"/api/sessions/{sid}/attachments",
                           params={"filename": "a.png"}, content=png,
                           headers={"Content-Type": "image/png"})
    att_row = up.json()
    sf = client.app.state.session_factory
    await add_message(sf, sid, "user", content="看图", attachment_ids=[att_row["id"]])
    msgs = (await client.get(f"/api/sessions/{sid}/messages")).json()
    assert msgs[0]["content"] == "看图"
    assert msgs[0]["attachments"] == [att_row]

    other = (await client.post("/api/sessions", json={"workspace_id": ws["id"]})).json()
    assert (await client.get(f"/api/sessions/{other['id']}/messages")).json() == []


async def test_add_message_rejects_unbindable_attachment(client, tmp_path, monkeypatch):
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path / "data"))
    import pytest
    from app.services.message_service import add_message
    ws, sess = await _mk_ws_session(client, tmp_path)
    sid = sess["id"]
    # 附件挂在另一个会话里：绑定按 session 收口，跨会话的 id 必然绑不上（同时验证会话作用域）
    other = (await client.post("/api/sessions", json={"workspace_id": ws["id"]})).json()
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    up = await client.post(f"/api/sessions/{other['id']}/attachments",
                           params={"filename": "a.png"}, content=png,
                           headers={"Content-Type": "image/png"})
    att_id = up.json()["id"]
    sf = client.app.state.session_factory
    with pytest.raises(ValueError):
        await add_message(sf, sid, "user", content="x", attachment_ids=[att_id])
    # 消息也回滚了：绑定失败不产生半条消息
    assert (await client.get(f"/api/sessions/{sid}/messages")).json() == []
