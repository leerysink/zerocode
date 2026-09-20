"""Task 8：WS 端点 /ws/chat/{session_id} + ChatHub 集成测试。

同步测试：TestClient 触发 lifespan（走开发库 zerocode，迁移幂等）；
工作区/会话 fixture 自建自删（teardown 删 workspace 级联清会话与消息）。

裁定（事件计数修正）：brief 原文 busy 测试 tail 只读 2 条，但取消后 runner
依次补发 tool_result({"cancelled": true}) → error("已取消") → status(idle)，
读 3 条才与「tail 含 error(已取消)+status」断言自洽。
"""
import asyncio, json, tempfile
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text as _sql
from app import main as main_module
from app.db import make_engine
from app.hub import ChatHub
from app.main import create_app
from app.agent.llm import ScriptedLLM

@pytest.fixture
def client(monkeypatch):
    # Task 12 起 create_app 的 lifespan 要做部署侧启动清理（把库里所有 dev 栈 compose down，
    # 退出时再停一遍 running 的）：本 fixture 连的是**开发库 zerocode**，不打桩就会停掉开发者
    # 正在预览的应用栈，并且每个用例白敲几次 docker CLI。部署链路由
    # tests/test_apps_dev_api.py + tests/test_engine.py 覆盖，这里只测聊天 WS。
    from app.deploy import engine as deploy_engine, provision, sqlrun

    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(deploy_engine, "cleanup_orphans", _noop)
    monkeypatch.setattr(deploy_engine, "stop_all_dev_on_exit", _noop)
    monkeypatch.setattr(provision, "ensure_zcnet", _noop)
    monkeypatch.setattr(sqlrun, "build_if_missing", _noop)
    app = create_app()  # 开发库；lifespan 负责迁移
    with TestClient(app) as c:
        tmp = tempfile.mkdtemp()
        ws = c.post("/api/workspaces", json={"root_path": tmp}).json()
        sess = c.post("/api/sessions", json={"workspace_id": ws["id"]}).json()
        c.zc_sid = sess["id"]
        c.zc_wsid = ws["id"]
        yield c
        c.delete(f"/api/workspaces/{c.zc_wsid}")

def done(content, tool_calls=None):
    return [{"type": "done", "message": {"role": "assistant", "content": content, "tool_calls": tool_calls}}]

def test_ws_plain_flow(client):
    client.app.state.llm_factory = lambda cfg: ScriptedLLM([
        [{"type": "content_delta", "content": "你"},
         {"type": "content_delta", "content": "好"},
         {"type": "done", "message": {"role": "assistant", "content": "你好", "tool_calls": None}}]])
    with client.websocket_connect(f"/ws/chat/{client.zc_sid}") as ws:
        ws.send_json({"type": "user_message", "content": "打个招呼"})
        evs = [ws.receive_json() for _ in range(4)]  # token,token,message_done,status
    types = [e["type"] for e in evs]
    assert "token" in types and "message_done" in types and "status" in types

def test_ws_ask_flow(client):
    client.app.state.llm_factory = lambda cfg: ScriptedLLM([
        done(None, [{"id": "c1", "function": {
            "name": "ask_user",
            "arguments": json.dumps({"questions": [{"question": "选哪个", "options": [{"label": "A"}]}]}, ensure_ascii=False)}}]),
        done("好的，按 A 继续"),
    ])
    with client.websocket_connect(f"/ws/chat/{client.zc_sid}") as ws:
        ws.send_json({"type": "user_message", "content": "提问吧"})
        got = None
        for _ in range(6):
            ev = ws.receive_json()
            if ev["type"] == "ask_user":
                got = ev
                break
        assert got and got["questions"][0]["question"] == "选哪个"
        ws.send_json({"type": "ask_submit", "answers": [{"options": ["A"]}]})
        seen_result = seen_done = False
        for _ in range(8):
            ev = ws.receive_json()
            if ev["type"] == "tool_result":
                seen_result = True
                assert ev["result"]["answers"] == [{"options": ["A"]}]
            if ev["type"] == "message_done":
                seen_done = True
                break
        assert seen_result and seen_done

def test_ws_multi_question_submit(client):
    client.app.state.llm_factory = lambda cfg: ScriptedLLM([
        done(None, [{"id": "c1", "function": {
            "name": "ask_user", "arguments": json.dumps({"questions": [
                {"question": "用哪种库", "options": [{"label": "X", "recommended": True}, {"label": "Y"}],
                 "multi_select": False},
                {"question": "补充说明？"}]}, ensure_ascii=False)}}]),
        done("按回答继续"),
    ])
    with client.websocket_connect(f"/ws/chat/{client.zc_sid}") as ws:
        ws.send_json({"type": "user_message", "content": "提问"})
        for _ in range(6):
            ev = ws.receive_json()
            if ev["type"] == "ask_user":
                break
        assert len(ev["questions"]) == 2
        ws.send_json({"type": "ask_submit", "answers": [{"options": ["X"]}, {"answer": "无"}]})
        for _ in range(8):
            ev = ws.receive_json()
            if ev["type"] == "message_done":
                break

def test_ws_busy_rejected(client):
    client.app.state.llm_factory = lambda cfg: ScriptedLLM([
        done(None, [{"id": "c1", "function": {
            "name": "ask_user", "arguments": json.dumps({"question": "?", "options": []})}}])])
    with client.websocket_connect(f"/ws/chat/{client.zc_sid}") as ws:
        ws.send_json({"type": "user_message", "content": "先挂着"})
        for _ in range(6):
            if ws.receive_json()["type"] == "ask_user":
                break
        # 第二个连接：并发消息被拒；同会话后连覆盖该会话的 emit 槽（per-session 语义），
        # 取消后的事件都发给 ws2
        with client.websocket_connect(f"/ws/chat/{client.zc_sid}") as ws2:
            ws2.send_json({"type": "user_message", "content": "并发消息"})
            ev = ws2.receive_json()
            assert ev["type"] == "error" and "正在执行" in ev["message"]
            ws2.send_json({"type": "cancel"})
            # 裁定：取消后 runner 依次发 tool_result(已取消)、error(已取消)、status(idle)，
            # 读 3 条（brief 原文读 2 条漏计 tool_result）
            tail = [ws2.receive_json() for _ in range(3)]  # tool_result + error(已取消) + status(idle)
        assert any(e["type"] == "error" and e["message"] == "已取消" for e in tail)
        assert any(e["type"] == "status" for e in tail)

def test_ws_cross_session_emit_isolation(client):
    """终审修复轮2回归网：hub 下行 emit 必须按会话分槽（多标签页/多浏览器串扰修复）。
    修复前（全局单槽）：A 会话挂在 ask 上时 B 会话连入覆盖 hub.emit，A 应答恢复后的
    tool_result/message_done/status 全部串到 B 的连接（A 永远收不到，UI 卡 running）。
    修复后：各会话事件只进各自的连接——B 只收到 B 自己的事件，A 的下行全回 A。"""
    calls = {"n": 0}
    def factory(cfg):
        calls["n"] += 1
        if calls["n"] == 1:  # A 会话的 run：ask 挂起 → 应答后补发 tool_result/message_done/status
            return ScriptedLLM([
                done(None, [{"id": "c1", "function": {
                    "name": "ask_user",
                    "arguments": json.dumps({"question": "A的问题", "options": ["x"]}, ensure_ascii=False)}}]),
                done("A完成"),
            ])
        return ScriptedLLM([[{"type": "content_delta", "content": "B"}, *done("B完成")]])
    client.app.state.llm_factory = factory
    sid_a = client.zc_sid
    sid_b = client.post("/api/sessions", json={"workspace_id": client.zc_wsid}).json()["id"]
    with client.websocket_connect(f"/ws/chat/{sid_a}") as ws_a:
        ws_a.send_json({"type": "user_message", "content": "A 开始"})
        for _ in range(6):
            if ws_a.receive_json()["type"] == "ask_user":
                break
        # A 挂在 ask 上；B 会话此刻连入（旧代码在此抢走全局单槽 hub.emit）
        with client.websocket_connect(f"/ws/chat/{sid_b}") as ws_b:
            ws_a.send_json({"type": "ask_submit", "answers": [{"options": ["x"]}]})
            ws_b.send_json({"type": "user_message", "content": "B 开始"})
            # B 读到终态 status 为止：只能看到 B 自己的 message_done("B完成")；
            # 修复前这里会先串入 A 的 tool_result + message_done("A完成") + status(idle)
            seen_done, saw_status = [], False
            for _ in range(10):
                ev = ws_b.receive_json()
                if ev["type"] == "message_done":
                    seen_done.append(ev["content"])
                if ev["type"] == "status":
                    saw_status = True
                    break
            assert saw_status
            assert seen_done == ["B完成"]  # 修复前：串入 "A完成" → RED
        # A 应答后的下行必须全部回到 A 自己的连接（tool_result → message_done("A完成") → status）
        got_tail = saw_status_a = False
        for _ in range(8):
            ev = ws_a.receive_json()
            if ev["type"] == "tool_result" and ev["result"]["answers"] == [{"options": ["x"]}]:
                got_tail = True
            if ev["type"] == "message_done" and ev["content"] == "A完成":
                got_tail = True
            if ev["type"] == "status":
                saw_status_a = True
                break
        assert got_tail and saw_status_a
    # 断连清理：两条连接退出后各自会话槽已 drop，不残留僵尸槽
    hub = client.app.state.hub
    assert sid_a not in hub.emits and sid_b not in hub.emits

# ---------- 修复轮：watchdog 激活（run_task 落库 running）与挂起问答/确认防误报 ----------

def _db_status_dev(session_id: int) -> str:
    """同步测试线程内查询开发库会话状态：自建临时 engine + 一次性事件循环，用完即释放，
    避免与 TestClient 的 portal 事件循环交叉复用连接池。"""
    async def _go():
        engine = make_engine(None)  # None → app.config.database_url()（开发库，与 client fixture 同库）
        try:
            async with engine.connect() as conn:
                return (await conn.execute(_sql("SELECT status FROM chat_session WHERE id=:i"),
                                           {"i": session_id})).scalar_one()
        finally:
            await engine.dispose()
    return asyncio.run(_go())

async def _stale_running_session(engine, root_path: str) -> int:
    """在测试库造一个 status='running'、updated_at 超 10 分钟的孤儿会话，返回会话 id。"""
    async with engine.begin() as conn:
        wid = (await conn.execute(_sql(
            "INSERT INTO workspace (name, root_path) VALUES ('wd', :p) RETURNING id"),
            {"p": root_path})).scalar_one()
        return (await conn.execute(_sql(
            "INSERT INTO chat_session (workspace_id, status, updated_at) "
            "VALUES (:w, 'running', now() - interval '11 minutes') RETURNING id"),
            {"w": wid})).scalar_one()

async def _session_status(engine, session_id: int) -> str:
    async with engine.connect() as conn:
        return (await conn.execute(_sql("SELECT status FROM chat_session WHERE id=:i"),
                                   {"i": session_id})).scalar_one()

def test_run_task_marks_running(client):
    """修复点 1：run_task 先落库 'running' 激活 watchdog；ask 挂起期间 DB status='running'，
    结束后终态回写 'idle'（runner 终态回写未被破坏）。"""
    client.app.state.llm_factory = lambda cfg: ScriptedLLM([
        done(None, [{"id": "c1", "function": {
            "name": "ask_user",
            "arguments": json.dumps({"questions": [{"question": "选哪个", "options": [{"label": "A"}]}]}, ensure_ascii=False)}}]),
        done("完成"),
    ])
    sid = client.zc_sid
    with client.websocket_connect(f"/ws/chat/{sid}") as ws:
        ws.send_json({"type": "user_message", "content": "开始"})
        for _ in range(6):
            ev = ws.receive_json()
            if ev["type"] == "ask_user":
                break
        assert ev["type"] == "ask_user"
        # ask 挂起中（G2 timeout=0 不限时）：执行中状态已落库，watchdog 才有匹配对象
        assert _db_status_dev(sid) == "running"
        ws.send_json({"type": "ask_submit", "answers": [{"options": ["A"]}]})
        for _ in range(8):
            ev = ws.receive_json()
            if ev["type"] == "status":  # 终态 status 事件在 runner._set_status 落库之后发出
                break
    assert _db_status_dev(sid) == "idle"

async def test_watchdog_marks_stale_running(test_engine):
    """修复点 1（watchdog 侧）：hub 无挂起 future 的 stale running 会话被标为 error。
    直测单次巡检 _watchdog_tick（_watchdog 循环体每 60s 调它一次）。"""
    hub = ChatHub()
    sid = await _stale_running_session(test_engine, tempfile.mkdtemp())
    marked = await main_module._watchdog_tick(test_engine, hub)
    assert sid in marked
    assert await _session_status(test_engine, sid) == "error"

async def test_watchdog_skips_pending_ask(test_engine):
    """修复点 2：ask future 挂起（用户作答期间 updated_at 停更属正常等待）不标 error。"""
    hub = ChatHub()
    sid = await _stale_running_session(test_engine, tempfile.mkdtemp())
    fut = asyncio.get_running_loop().create_future()
    hub.ask_futures[sid] = fut
    try:
        marked = await main_module._watchdog_tick(test_engine, hub)
        assert sid not in marked
        assert await _session_status(test_engine, sid) == "running"
    finally:
        hub.ask_futures.pop(sid, None)
        if not fut.done():
            fut.cancel()

def test_ws_manual_compact(client):
    import asyncio as _aio
    from app.db import make_session_factory as _mk_sf
    from app.services.message_service import add_message as _add

    async def _seed():
        eng = make_engine(None)
        sf = _mk_sf(eng)
        try:
            for i in range(12):
                await _add(sf, client.zc_sid, "user" if i % 2 == 0 else "assistant", content=f"消息{i}")
        finally:
            await eng.dispose()
    _aio.run(_seed())
    client.app.state.llm_factory = lambda cfg: ScriptedLLM([
        [{"type": "done", "message": {"role": "assistant", "content": "压缩摘要OK", "tool_calls": None}}]])
    with client.websocket_connect(f"/ws/chat/{client.zc_sid}") as ws:
        ws.send_json({"type": "compact"})
        types = []
        for _ in range(4):
            ev = ws.receive_json()
            types.append(ev["type"])
            if ev["type"] == "compaction_done":
                assert ev["summary"] == "压缩摘要OK"
                break
        assert "compaction_done" in types
