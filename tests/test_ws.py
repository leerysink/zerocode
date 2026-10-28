"""Task 8：WS 端点 /ws/chat/{session_id} + ChatHub 集成测试。

同步测试：TestClient 触发 lifespan（走开发库 zerocode，迁移幂等）；
工作区/会话 fixture 自建自删（teardown 删 workspace 级联清会话与消息）。

裁定（事件计数修正）：brief 原文 busy 测试 tail 只读 2 条，但取消后 runner
依次补发 tool_result({"cancelled": true}) → error("已取消") → status(idle)，
读 3 条才与「tail 含 error(已取消)+status」断言自洽。
"""
import asyncio, json, secrets, tempfile
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
        # Ticket 002 全局 guard：业务接口需登录态。开发库无清理机制，随机昵称注册不跨轮冲突；
        # 遗留账号无口令/不入生产语义，仅测试痕迹。
        reg = c.post("/api/auth/register",
                     json={"nickname": f"ws-test-{secrets.token_hex(4)}", "password": "test-pw"})
        assert reg.status_code == 200, reg.text
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
        evs = [ws.receive_json() for _ in range(5)]  # session_title,token,token,message_done,status
    types = [e["type"] for e in evs]
    assert types[0] == "session_title" and evs[0]["title"] == "打个招呼"  # 自动命名走在模型输出前
    assert "token" in types and "message_done" in types and "status" in types

def test_ws_autotitles_default_session_on_first_message(client):
    """自动命名（2026-09-21）：「新会话」收到首条用户消息 → 改名并下行 session_title，
    侧栏不必重拉列表即可实时刷新（事件排在模型输出之前）。"""
    client.app.state.llm_factory = lambda cfg: ScriptedLLM([done("好的")])
    with client.websocket_connect(f"/ws/chat/{client.zc_sid}") as ws:
        ws.send_json({"type": "user_message", "content": "帮我写一个待办清单应用\n再加个导出按钮"})
        evs = []
        for _ in range(4):
            ev = ws.receive_json()
            evs.append(ev)
            if ev["type"] == "status":
                break
    assert evs[0] == {"type": "session_title", "title": "帮我写一个待办清单应用"}
    assert client.get(f"/api/sessions/{client.zc_sid}").json()["title"] == "帮我写一个待办清单应用"


def test_ws_skips_autotitle_for_renamed_session(client):
    """用户手动改过名的会话永不被自动命名覆盖：不改库、也不发 session_title 帧。"""
    client.patch(f"/api/sessions/{client.zc_sid}", json={"title": "我起的名字"})
    client.app.state.llm_factory = lambda cfg: ScriptedLLM([done("好的")])
    with client.websocket_connect(f"/ws/chat/{client.zc_sid}") as ws:
        ws.send_json({"type": "user_message", "content": "随便一句开场白"})
        types = []
        for _ in range(4):
            ev = ws.receive_json()
            types.append(ev["type"])
            if ev["type"] == "status":
                break
    assert "session_title" not in types
    assert client.get(f"/api/sessions/{client.zc_sid}").json()["title"] == "我起的名字"


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

def test_ws_steer_while_running_then_cancel(client):
    """契约更新（steering 上线后）：执行中再发消息**不再被拒**，而是入队等边界注入；
    取消时队列一并丢弃，runner 的收尾链不变。

    本用例同时盯着「同会话后连覆盖 emit 槽」与取消后的
    tool_result → error(已取消) → status(idle) 三帧次序（brief 原文读 2 帧漏计 tool_result）。
    """
    client.app.state.llm_factory = lambda cfg: ScriptedLLM([
        done(None, [{"id": "c1", "function": {
            "name": "ask_user", "arguments": json.dumps({"question": "?", "options": []})}}])])
    hub = client.app.state.hub
    sid = client.zc_sid
    with client.websocket_connect(f"/ws/chat/{sid}") as ws:
        ws.send_json({"type": "user_message", "content": "先挂着"})
        for _ in range(6):
            if ws.receive_json()["type"] == "ask_user":
                break
        # 第二个连接：并发消息入队；同会话后连覆盖该会话的 emit 槽（per-session 语义），
        # 取消后的事件都发给 ws2。后连首帧是 running 快照（重连状态自洽），不是拒绝。
        with client.websocket_connect(f"/ws/chat/{sid}") as ws2:
            assert ws2.receive_json() == {"type": "status", "status": "running"}
            # 同连接上行严格 FIFO：插话先入队，cancel 后到 —— 不必（也不能）读 hub.pending 抢时序
            ws2.send_json({"type": "user_message", "content": "插一句"})
            ws2.send_json({"type": "cancel"})
            tail = [ws2.receive_json() for _ in range(3)]  # tool_result + error(已取消) + status(idle)
        assert any(e["type"] == "error" and e["message"] == "已取消" for e in tail)
        assert any(e["type"] == "status" for e in tail)
        assert not any(e["type"] == "steering_done" for e in tail)  # 停止后不再注入
        assert not hub.pending.get(sid)  # 未送达插话一并丢弃，不许偷偷活到下一次 run

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

# ---------- 死循环修复：执行中拒绝必须是非终态 busy，不得回 error ----------

def _busy_hanging_llm():
    """单轮：ask_user 挂起（timeout=0 不限时），把会话钉在 running 态。"""
    return ScriptedLLM([
        done(None, [{"id": "c1", "function": {
            "name": "ask_user", "arguments": json.dumps({"question": "?", "options": []})}}])])


def _reject_event(ws):
    """取「并发消息被拒」那一帧；跳过连入补发的 running 快照（实现后首帧是它）。

    TestClient 的 receive 无超时：只读确定会来的帧（快照 1 帧 + 拒绝 1 帧），
    不得盲读定长 N 帧——少一帧就挂死（RED 阶段的 hang 即此）。
    """
    ev = ws.receive_json()
    if ev["type"] == "status" and ev.get("status") == "running":
        ev = ws.receive_json()
    return ev


def test_ws_busy_is_non_terminal(client):
    """插话排满时回 busy（**非终态**），且不打断正在跑的这一轮。

    盯的是 a0cecb1 那个死循环的根：拒绝一旦被发成 error，前端就当本轮终态清掉 running，
    红色「停止」按钮随之消失，用户既发不出也停不掉。user_message 路径现在只在
    「队列排满 / 压缩中」两种情况下拒绝，这里取队列排满（默认上限 20，直接预置占位）。
    """
    client.app.state.llm_factory = lambda cfg: ScriptedLLM([
        done(None, [{"id": "c1", "function": {
            "name": "ask_user", "arguments": json.dumps({"question": "?", "options": []})}}]),
        done("应答后继续"),
    ])
    sid = client.zc_sid
    hub = client.app.state.hub
    with client.websocket_connect(f"/ws/chat/{sid}") as ws:
        ws.send_json({"type": "user_message", "content": "先挂着"})
        for _ in range(6):
            if ws.receive_json()["type"] == "ask_user":
                break
        hub.pending[sid] = [
            {"content": f"占位{i}", "attachment_ids": [], "client_msg_id": None}
            for i in range(20)]  # 顶到默认上限
        ws.send_json({"type": "user_message", "content": "第 21 句"})
        assert _reject_event(ws)["type"] == "busy"
        # busy 不是终态：ask 仍挂起，应答后这一轮照常收尾
        # （预置的 20 条占位插话无字无图，drain 时被整体跳过 → 0 帧 steering_done，
        #   下一帧确定性的 message_done 直接从事件流里读即可）
        ws.send_json({"type": "ask_submit", "answers": [{"answer": "随便"}]})
        for _ in range(60):
            if ws.receive_json()["type"] == "message_done":
                break
        else:
            pytest.fail("busy 之后应答，runner 应跑到 message_done")
    # 被拒的那句话不落库（丢弃语义不变，只是不再伪装成终态错误）
    contents = [m["content"] for m in client.get(f"/api/sessions/{sid}/messages").json()]
    assert "第 21 句" not in contents


def test_ws_compact_busy_is_non_terminal(client):
    """执行中点「压缩上下文」同理回 busy，不得回 error 解锁 UI。"""
    client.app.state.llm_factory = lambda cfg: _busy_hanging_llm()
    with client.websocket_connect(f"/ws/chat/{client.zc_sid}") as ws:
        ws.send_json({"type": "user_message", "content": "跑着"})
        for _ in range(6):
            if ws.receive_json()["type"] == "ask_user":
                break
        ws.send_json({"type": "compact"})
        assert _reject_event(ws)["type"] == "busy"  # 修复前是 error → 快速 RED
        ws.send_json({"type": "cancel"})  # 收口，免留挂起任务


def test_ws_reconnect_replays_running_status(client):
    """刷新/重连后状态自洽：连入时会话仍在跑 → 首帧补发 status(running)，
    前端据此把「停止」按钮亮回来，不再出现「前端解锁 + 后端在跑」的脱钩死循环。

    写法约束：TestClient 的 receive 无超时，直等补帧会挂死。故连入后立即 cancel，
    让「有实现」与「无实现」都有确定性产出——有则首帧必为 status 快照（connect 时同步
    下发，排在 cancel 引发的收尾事件之前），无则首帧是取消链路的 tool_result/error。
    """
    client.app.state.llm_factory = lambda cfg: _busy_hanging_llm()
    sid = client.zc_sid
    with client.websocket_connect(f"/ws/chat/{sid}") as ws:
        ws.send_json({"type": "user_message", "content": "挂着"})
        for _ in range(6):
            if ws.receive_json()["type"] == "ask_user":
                break
        with client.websocket_connect(f"/ws/chat/{sid}") as ws2:
            ws2.send_json({"type": "cancel"})
            head = ws2.receive_json()
            assert head == {"type": "status", "status": "running"}
            for _ in range(4):  # 收尾链：tool_result / error(已取消) / status(idle)
                if ws2.receive_json()["type"] == "status":
                    break
    # 空闲会话连入不得补发 status 快照（否则每次连接多一帧噪声，且掩盖真实终态）
    client.app.state.llm_factory = lambda cfg: ScriptedLLM([done("正常回复")])
    with client.websocket_connect(f"/ws/chat/{sid}") as ws3:
        ws3.send_json({"type": "user_message", "content": "hi"})
        assert ws3.receive_json()["type"] == "message_done"


# ---------- 插话（steering）：执行中的新消息排队，下一轮工具边界注入 ----------

def _ask_tool_call(cid="c1", q="?"):
    return {"id": cid, "function": {"name": "ask_user",
            "arguments": json.dumps({"question": q, "options": []})}}


def test_ws_steering_injected_at_round_boundary(client):
    """执行中再发一句：不再被拒，也不另起一轮 run —— 它在**下一个工具边界**作为 user 消息
    落库并注入本次对话，模型下一轮就看得见（用户要的"中途补一句结束语/改方向"）。

    顺序契约走**可观察事实**（落库 seq + 下行事件次序），不看 ScriptedLLM.received：
    runner 全程复用同一个 messages list，received 的每个元素都是同一对象的别名，
    拿它断"注入在哪一步之间"等于没断（调试期就是这么骗过自己的）。
    """
    cap = {}
    def factory(cfg):
        cap["llm"] = ScriptedLLM([
            done(None, [_ask_tool_call()]),
            done("收到，按新方向继续"),
        ])
        return cap["llm"]
    client.app.state.llm_factory = factory
    steer = "插一句：改成用 SQLite"
    with client.websocket_connect(f"/ws/chat/{client.zc_sid}") as ws:
        ws.send_json({"type": "user_message", "content": "先挂着"})
        for _ in range(6):
            if ws.receive_json()["type"] == "ask_user":
                break
        # 同一连接上行严格 FIFO：插话一定排在 ask_submit 之前被处理
        ws.send_json({"type": "user_message", "content": steer})
        ws.send_json({"type": "ask_submit", "answers": [{"answer": "都行"}]})
        evs = []
        for _ in range(10):
            ev = ws.receive_json()
            evs.append(ev)
            if ev["type"] == "status":
                break
    types = [e["type"] for e in evs]
    assert "steering_done" in types, types
    assert types.index("steering_done") < types.index("message_done"), types  # 先注入，后答复
    rows = [(r["role"], r["content"]) for r in client.get(f"/api/sessions/{client.zc_sid}/messages").json()]
    i = next(k for k, (role, c) in enumerate(rows) if role == "user" and c == steer)
    assert rows[i - 1][0] == "tool", rows  # 落在工具结果之后（没拆散 assistant(tool_calls)/tool 对）
    assert rows[i + 1][0] == "assistant", rows  # 模型答复在它之后：这句确实被看见了
    assert sum(1 for role, _ in rows if role == "user") == 2  # 原问题 + 插话
    # 模型**真的看见了这句话**（不是落库了却给模型塞了个 None：expand_mentions 无 @ 时返回 None）
    assert cap["llm"].received[-1][-1] == {"role": "user", "content": steer}


def test_ws_steering_dropped_on_cancel(client):
    """点「停止」后队列里未送达的插话一并丢弃：不落库、不留到下一次 run
    （否则会出现「我以为停了，下一条新消息却被旧队列抢先注入」的错位）。"""
    client.app.state.llm_factory = lambda cfg: ScriptedLLM([done(None, [_ask_tool_call()])])
    hub = client.app.state.hub
    sid = client.zc_sid
    steer = "停止前想补的一句"
    with client.websocket_connect(f"/ws/chat/{sid}") as ws:
        ws.send_json({"type": "user_message", "content": "挂着"})
        for _ in range(6):
            if ws.receive_json()["type"] == "ask_user":
                break
        # 同连接严格 FIFO：插话先入队，cancel 后到（不去抢读 hub.pending 的中间态）
        ws.send_json({"type": "user_message", "content": steer})
        ws.send_json({"type": "cancel"})
        evs = []
        for _ in range(5):
            ev = ws.receive_json()
            evs.append(ev)
            if ev["type"] == "status":
                break
    assert not any(e["type"] == "steering_done" for e in evs)  # 停止后没被偷偷注入
    assert not hub.pending.get(sid)
    contents = [m["content"] for m in client.get(f"/api/sessions/{sid}/messages").json()]
    assert steer not in contents


class _SteerAfterStream(ScriptedLLM):
    """让"最终答复产出的那一刻用户正好插一句"可确定性复现。

    chat_stream 的最后一个事件被消费后，`async for` 还会再推一次生成器才结束，
    所以 after 钩子恰好跑在 runner 的「最终答复分支」之前 —— 正是那条分支要处理的时机。
    """
    def __init__(self, turns, after=None):
        super().__init__(turns)
        self.after = after

    async def chat_stream(self, messages, tools):
        async for ev in super().chat_stream(messages, tools):
            yield ev
        if self.after:
            hook, self.after = self.after, None
            hook()


def test_ws_steering_arriving_with_final_answer_gets_a_round(client):
    """模型已经给出最终答复时插话：不能等下一次提问才被看见 —— 答复照常落库/下发，
    插话紧随其后，再走一轮让模型回应它（顺序必须是「答复在前、插话在后」，否则历史错位）。
    """
    hub = client.app.state.hub
    steer = "补一句：再加个导出按钮"

    def factory(cfg):
        return _SteerAfterStream(
            [done("第一段答复"), done("好，导出按钮加上")],
            after=lambda: hub.queue(
                client.zc_sid,
                {"content": steer, "attachment_ids": [], "client_msg_id": None}, 20))
    client.app.state.llm_factory = factory
    with client.websocket_connect(f"/ws/chat/{client.zc_sid}") as ws:
        ws.send_json({"type": "user_message", "content": "开始"})
        evs = []
        for _ in range(10):
            ev = ws.receive_json()
            evs.append(ev)
            if ev["type"] == "status":
                break
    types = [e["type"] for e in evs]
    dones = [k for k, t in enumerate(types) if t == "message_done"]
    assert len(dones) == 2, types  # 原答复 + 因插话多走的一轮
    assert "steering_done" in types
    assert dones[0] < types.index("steering_done") < dones[1], types  # 夹在两次答复之间
    rows = [(r["role"], r["content"])
            for r in client.get(f"/api/sessions/{client.zc_sid}/messages").json()]
    i = next(k for k, (role, c) in enumerate(rows) if role == "user" and c == steer)
    assert rows[i - 1] == ("assistant", "第一段答复"), rows  # 顺序：答复在前、插话在后，历史不错位
    assert rows[i + 1][0] == "assistant", rows  # 多出来的一轮确实回应了它


def test_hub_queue_cap_and_drain():
    """hub 队列：入队保序、drain 取空、超上限拒收（ws 侧按 sys_config 传上限）。"""
    hub = ChatHub()
    a = {"content": "第一句", "attachment_ids": [], "client_msg_id": None}
    b = {"content": "第二句", "attachment_ids": [], "client_msg_id": None}
    c = {"content": "第三句", "attachment_ids": [], "client_msg_id": None}
    assert hub.queue(1, a, 2) is True
    assert hub.queue(1, b, 2) is True
    assert hub.queue(1, c, 2) is False  # 满 → ws 回 busy 非终态提示
    assert asyncio.run(hub.pop_pending(1)) == [a, b]
    assert asyncio.run(hub.pop_pending(1)) == []  # drain 即清空，不重复注入


def test_session_detail_exposes_queued_steering(client):
    """刷新时「已排队、尚未注入」的插话不能凭空消失：会话详情带上 hub.pending，
    前端据此把「排队中」气泡还原（否则用户说了话，刷新一下就没了，跟丢消息没区别）。"""
    hub = client.app.state.hub
    sid = client.zc_sid
    hub.queue(sid, {"content": "还在排队的一句", "attachment_ids": [],
                    "client_msg_id": "c1"}, 20)
    hub.queue(sid, {"content": "排第二句", "attachment_ids": [],
                    "client_msg_id": "c2"}, 20)
    detail = client.get(f"/api/sessions/{sid}").json()
    assert detail["pending_steering"] == [
        {"content": "还在排队的一句", "client_msg_id": "c1", "attachments": []},
        {"content": "排第二句", "client_msg_id": "c2", "attachments": []}]
    hub.pending.pop(sid, None)
    assert client.get(f"/api/sessions/{sid}").json()["pending_steering"] == []


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


# ---------- 聊天图片输入（WS 协议） ----------

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def _vision_model(client, vision=True):
    r = client.post("/api/models", json={
        "name": "vl", "base_url": "http://x/v1", "api_key": "k",
        "model": "vl-m", "is_default": True, "supports_vision": vision})
    assert r.status_code == 200, r.text
    return r.json()


def _upload(client, sid, name="a.png"):
    r = client.post(f"/api/sessions/{sid}/attachments", params={"filename": name},
                    content=PNG, headers={"Content-Type": "image/png"})
    assert r.status_code == 201, r.text
    return r.json()


def test_ws_pure_image_message_starts_run(client, monkeypatch, tmp_path):
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path / "data"))
    _vision_model(client)
    att_row = _upload(client, client.zc_sid)
    client.app.state.llm_factory = lambda cfg: ScriptedLLM([done("看到了")])
    with client.websocket_connect(f"/ws/chat/{client.zc_sid}") as ws:
        ws.send_json({"type": "user_message", "content": "",
                      "attachment_ids": [att_row["id"]], "client_msg_id": "c-img"})
        for _ in range(8):
            if ws.receive_json()["type"] == "status":
                break
    rows = client.get(f"/api/sessions/{client.zc_sid}/messages").json()
    assert [r["role"] for r in rows] == ["user", "assistant"]
    assert rows[0]["content"] == ""
    assert rows[0]["attachments"][0]["id"] == att_row["id"]


def test_ws_image_rejected_when_model_lacks_vision(client, monkeypatch, tmp_path):
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path / "data"))
    att_row = _upload(client, client.zc_sid)
    with client.websocket_connect(f"/ws/chat/{client.zc_sid}") as ws:
        ws.send_json({"type": "user_message", "content": "",
                      "attachment_ids": [att_row["id"]], "client_msg_id": "c-no"})
        ev = _reject_event(ws)
    assert ev["type"] == "busy" and ev["client_msg_id"] == "c-no"
    assert ev["running"] is False
    assert client.get(f"/api/sessions/{client.zc_sid}/messages").json() == []


def test_ws_image_steering_binds_and_echoes_client_msg_id(client, monkeypatch, tmp_path):
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path / "data"))
    _vision_model(client)
    att_row = _upload(client, client.zc_sid)
    client.app.state.llm_factory = lambda cfg: ScriptedLLM([
        done(None, [_ask_tool_call()]), done("收到图片")])
    with client.websocket_connect(f"/ws/chat/{client.zc_sid}") as ws:
        ws.send_json({"type": "user_message", "content": "先挂着"})
        for _ in range(6):
            if ws.receive_json()["type"] == "ask_user":
                break
        ws.send_json({"type": "user_message", "content": "",
                      "attachment_ids": [att_row["id"]], "client_msg_id": "c-steer"})
        ws.send_json({"type": "ask_submit", "answers": [{"answer": "ok"}]})
        evs = []
        for _ in range(12):
            ev = ws.receive_json()
            evs.append(ev)
            if ev["type"] == "status":
                break
    done_ev = [e for e in evs if e["type"] == "steering_done"]
    assert done_ev and done_ev[0]["client_msg_id"] == "c-steer"
    rows = client.get(f"/api/sessions/{client.zc_sid}/messages").json()
    users = [r for r in rows if r["role"] == "user"]
    assert len(users) == 2
    assert users[1]["content"] == ""
    assert users[1]["attachments"][0]["id"] == att_row["id"]


def test_ws_image_steering_rejected_when_run_model_lacks_vision(client, monkeypatch, tmp_path):
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path / "data"))
    att_row = _upload(client, client.zc_sid)
    client.app.state.llm_factory = lambda cfg: ScriptedLLM([done(None, [_ask_tool_call()])])
    with client.websocket_connect(f"/ws/chat/{client.zc_sid}") as ws:
        ws.send_json({"type": "user_message", "content": "先挂着"})
        for _ in range(6):
            if ws.receive_json()["type"] == "ask_user":
                break
        ws.send_json({"type": "user_message", "content": "",
                      "attachment_ids": [att_row["id"]], "client_msg_id": "c-no"})
        ev = _reject_event(ws)
        assert ev["type"] == "busy" and ev["client_msg_id"] == "c-no"
        assert ev["running"] is True
        ws.send_json({"type": "cancel"})
        for _ in range(5):
            if ws.receive_json()["type"] == "status":
                break
    assert not client.app.state.hub.pending.get(client.zc_sid)


def test_ws_image_steering_gate_uses_run_model_not_latest_default(client, monkeypatch, tmp_path):
    """能力门按**本 run 启动时**解析的模型判定，不是收到插话那一刻的默认模型。

    运行开始后才把默认模型换成视觉模型：本 run 的 run_vision 仍为 false，
    带图插话必须按本 run 能力拒绝；若实现改回「收信时临时解析默认模型」，
    这里会放行入队（随后读不到 busy → 断言失败）。
    """
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path / "data"))
    att_row = _upload(client, client.zc_sid)
    client.app.state.llm_factory = lambda cfg: ScriptedLLM([done(None, [_ask_tool_call()]), done("ok")])
    with client.websocket_connect(f"/ws/chat/{client.zc_sid}") as ws:
        ws.send_json({"type": "user_message", "content": "先挂着"})
        for _ in range(6):
            if ws.receive_json()["type"] == "ask_user":
                break
        _vision_model(client)  # 运行开始后才切到视觉默认模型：本 run 的能力位应仍是 false
        ws.send_json({"type": "user_message", "content": "",
                      "attachment_ids": [att_row["id"]], "client_msg_id": "c-stale"})
        ev = _reject_event(ws)
        assert ev["type"] == "busy" and ev["client_msg_id"] == "c-stale"
        assert ev["running"] is True
        ws.send_json({"type": "cancel"})
        for _ in range(5):
            if ws.receive_json()["type"] == "status":
                break
