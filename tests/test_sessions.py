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
