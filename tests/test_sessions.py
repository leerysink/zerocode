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
async def test_messages_project_compaction_markers(client):
    # 压缩留痕只活在 compaction 表：读取时投成 notice 行，插在「被摘到的最后一条消息」之后，
    # 刷新/切会话后照样看得见。故意不写 message 表——模型上下文因此不会掺进提示条。
    from sqlalchemy import text
    ws = await _mk_ws(client)
    s = (await client.post("/api/sessions", json={"workspace_id": ws["id"]})).json()
    sf = client.app.state.session_factory
    for i in range(6):
        await add_message(sf, s["id"], "user", content=f"消息{i}")
    async with sf() as db:
        await db.execute(text("INSERT INTO compaction (session_id, summary, up_to_seq) "
                             "VALUES (:i, '第一摘要', 2)"), {"i": s["id"]})
        await db.execute(text("INSERT INTO compaction (session_id, summary, up_to_seq) "
                             "VALUES (:i, '第二摘要', 4)"), {"i": s["id"]})
        await db.commit()
    msgs = (await client.get(f"/api/sessions/{s['id']}/messages")).json()
    assert [(m["role"], m.get("kind")) for m in msgs] == [
        ("user", None), ("user", None),
        ("notice", "compacted"),
        ("user", None), ("user", None),
        ("notice", "compacted"),
        ("user", None), ("user", None)]
    marker = msgs[2]
    assert marker["seq"] == 2  # 定位到被摘要的末条水位
    assert marker["id"] is None and marker["attachments"] == []
    assert "第一摘要" not in str(msgs)  # 摘要正文不外泄给列表接口，前端只显示事件

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

@pytest.mark.asyncio
async def test_file_change_detail_endpoint(client, tmp_path):
    """详情接口带 before/after 全文：列表只给 diff（侧栏一次拉几十条不该把两份全文拖回来），
    整页对比才需要全文——行号、折叠段原文、以及「diff 与全文对不对得上」的回验都靠它。"""
    from sqlalchemy import text as sql
    ws = (await client.post("/api/workspaces", json={"root_path": str(tmp_path)})).json()
    s = (await client.post("/api/sessions", json={"workspace_id": ws["id"]})).json()
    async with client.app.state.session_factory() as db:
        cid = (await db.execute(sql(
            "INSERT INTO file_change (session_id, path, action, before_content, after_content, diff) "
            "VALUES (:sid, 'a.txt', 'edit', 'x', 'y', '--- a/a.txt') RETURNING id"),
            {"sid": s["id"]})).scalar()
        await db.commit()

    d = (await client.get(f"/api/sessions/{s['id']}/file-changes/{cid}")).json()
    assert d["id"] == cid and d["path"] == "a.txt" and d["action"] == "edit"
    assert d["before_content"] == "x" and d["after_content"] == "y"
    assert d["diff"] == "--- a/a.txt"
    # 整页顶栏要工作区名、要能跳回编辑器：页面在新标签里，没有聊天页的上下文可借
    assert str(d["workspace_id"]) == str(ws["id"])
    assert d["workspace_name"] == ws["name"]

    # 记录不在这个会话下：同一个用户、另一个会话的地址也拿不到（不给按 id 直取全库的口子）
    other = (await client.post("/api/sessions", json={"workspace_id": ws["id"]})).json()
    assert (await client.get(f"/api/sessions/{other['id']}/file-changes/{cid}")).status_code == 404
    assert (await client.get(f"/api/sessions/{s['id']}/file-changes/999999")).status_code == 404
    # 脏 id 不能让 int() 炸成 500
    assert (await client.get(f"/api/sessions/{s['id']}/file-changes/abc")).status_code in (404, 422)

# ---------- 会话状态角标：账号级实时状态 ----------

async def test_live_session_status_reports_hub_facts(client, tmp_path):
    """角标数据源：hub 内存的 running/挂起/最近结果，不读库里可能滞后的 status。
    同一会话 ask 与 confirm 都挂起时（理论上不会），ask 优先。"""
    ws = (await client.post("/api/workspaces", json={"root_path": str(tmp_path)})).json()
    sid = (await client.post("/api/sessions", json={"workspace_id": ws["id"]})).json()["id"]
    idle_sid = (await client.post("/api/sessions", json={"workspace_id": ws["id"]})).json()["id"]
    hub = client.app.state.hub
    hub.running.add(sid)
    hub.pending_confirms[sid] = {"tool": "write_file", "args": {}, "summary": ""}
    hub.last_outcome[sid] = {"outcome": "completed", "finished_at": 123.5}
    try:
        rows = (await client.get("/api/sessions/live-status")).json()["sessions"]
        row = next(r for r in rows if r["session_id"] == sid)
        assert row["running"] is True
        assert row["waiting"] == "confirm"
        assert row["last_outcome"] == "completed"
        assert row["finished_at"] == 123.5
        # 库里仍是 idle：证明读数来自 hub，而不是 chat_session.status
        assert (await client.get(f"/api/sessions/{sid}")).json()["status"] == "idle"
        # 没有实时事实的会话不出现在列表里（避免每 5 秒把所有会话扫一遍）
        assert all(r["session_id"] != idle_sid for r in rows)

        hub.pending_asks[sid] = [{"question": "选哪个", "options": []}]
        rows = (await client.get("/api/sessions/live-status")).json()["sessions"]
        assert next(r for r in rows if r["session_id"] == sid)["waiting"] == "ask"
    finally:
        hub.running.discard(sid)
        hub.pending_asks.pop(sid, None)
        hub.pending_confirms.pop(sid, None)
        hub.last_outcome.pop(sid, None)


async def test_live_session_status_scoped_to_owner(two_auth_clients, tmp_path):
    """账号隔离：别人的会话即使正在跑，也不得出现在我的状态列表里。"""
    alice, bob = two_auth_clients
    ws_b = (await bob.post("/api/workspaces", json={"root_path": str(tmp_path)})).json()
    sid_b = (await bob.post("/api/sessions", json={"workspace_id": ws_b["id"]})).json()["id"]
    hub = alice.app.state.hub
    hub.running.add(sid_b)
    try:
        alice_rows = (await alice.get("/api/sessions/live-status")).json()["sessions"]
        assert all(r["session_id"] != sid_b for r in alice_rows)
        bob_rows = (await bob.get("/api/sessions/live-status")).json()["sessions"]
        assert any(r["session_id"] == sid_b for r in bob_rows)
    finally:
        hub.running.discard(sid_b)


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


async def test_messages_gzip_for_large_payload(client):
    """大会话首包压缩（规格 2026-11-09）：客户端声明 gzip 时，消息响应压缩传输，
    解压后与未压缩响应逐字等价。950 KB 的原样首包是线上打开 3 秒的主因之一。"""
    ws = await _mk_ws(client)
    s = (await client.post("/api/sessions", json={"workspace_id": ws["id"]})).json()
    sf = client.app.state.session_factory
    for i in range(3):
        await add_message(sf, s["id"], "user", content=f"{i}:" + "长消息" * 2000)
    gz = await client.get(f"/api/sessions/{s['id']}/messages",
                          headers={"Accept-Encoding": "gzip"})
    assert gz.status_code == 200
    assert gz.headers.get("content-encoding") == "gzip"
    plain = await client.get(f"/api/sessions/{s['id']}/messages",
                             headers={"Accept-Encoding": "identity"})
    assert "content-encoding" not in plain.headers
    # httpx 自动解压：两侧解析出的 JSON 必须等价（压缩不能改内容）
    assert gz.json() == plain.json()


async def test_small_response_not_gzipped(client):
    """小于 minimum_size 的响应不压缩：会话详情这类小 JSON 保持明文，避免无谓开销。"""
    ws = await _mk_ws(client)
    s = (await client.post("/api/sessions", json={"workspace_id": ws["id"]})).json()
    r = await client.get(f"/api/sessions/{s['id']}", headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200
    assert "content-encoding" not in r.headers
