"""Task 8：WS 端点 /ws/chat/{session_id} + ChatHub 集成测试。

同步测试：TestClient 触发 lifespan（走开发库 zerocode，迁移幂等）；
工作区/会话 fixture 自建自删（teardown 删 workspace 级联清会话与消息）。

裁定（事件计数修正）：brief 原文 busy 测试 tail 只读 2 条，但取消后 runner
依次补发 tool_result({"cancelled": true}) → error("已取消") → status(idle)，
读 3 条才与「tail 含 error(已取消)+status」断言自洽。
"""
import json, tempfile
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.agent.llm import ScriptedLLM

@pytest.fixture
def client():
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
            "arguments": json.dumps({"question": "选哪个", "options": ["A", "B"]}, ensure_ascii=False)}}]),
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
        assert got and got["question"] == "选哪个"
        ws.send_json({"type": "ask_choice", "option": "A"})
        seen_result = seen_done = False
        for _ in range(8):
            ev = ws.receive_json()
            if ev["type"] == "tool_result":
                seen_result = True
                assert ev["result"]["option"] == "A"
            if ev["type"] == "message_done":
                seen_done = True
                break
        assert seen_result and seen_done

def test_ws_busy_rejected(client):
    client.app.state.llm_factory = lambda cfg: ScriptedLLM([
        done(None, [{"id": "c1", "function": {
            "name": "ask_user", "arguments": json.dumps({"question": "?", "options": []})}}])])
    with client.websocket_connect(f"/ws/chat/{client.zc_sid}") as ws:
        ws.send_json({"type": "user_message", "content": "先挂着"})
        for _ in range(6):
            if ws.receive_json()["type"] == "ask_user":
                break
        # 第二个连接：并发消息被拒；由于 hub.emit 被后连者覆盖，取消后的事件都发给 ws2
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
