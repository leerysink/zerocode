# tests/test_isolation_ws_auth.py
"""Ticket 009：WebSocket 认证收口——未登录连 WS 被 4401 拒绝；跨用户连会话流被 4404 拒绝。

WS scope 不走 HTTP guard 中间件（main.py 注释留的口子在这里收口）：
/ws/chat/{sid} 与 /ws/deploy/{app_id} 在 accept 前解析 cookie → by_token；
会话流额外校验 session 经 workspace 归属该用户（越权 close code 与 HTTP 404 同哲学 = 4404）。

形态说明：测试把「连接 + 首帧读取」整体包进 pytest.raises——
accept 前 close（TestClient 在 __enter__ 抛）与 accept 后 close（首帧 receive 抛）
两种实现都能读到 close code，测试不锁死握手时序。
"""
import secrets, tempfile
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.agent.llm import ScriptedLLM
from app.main import create_app


def _done(content):
    return [{"type": "done",
             "message": {"role": "assistant", "content": content, "tool_calls": None}}]


@pytest.fixture
def client(monkeypatch):
    # 同 test_ws.py：连开发库 zerocode，打桩部署启动清理（本文件不碰 docker 链路）。
    from app.deploy import engine as deploy_engine, provision, sqlrun

    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(deploy_engine, "cleanup_orphans", _noop)
    monkeypatch.setattr(deploy_engine, "stop_all_dev_on_exit", _noop)
    monkeypatch.setattr(provision, "ensure_zcnet", _noop)
    monkeypatch.setattr(sqlrun, "build_if_missing", _noop)
    app = create_app()
    with TestClient(app) as c:
        reg = c.post("/api/auth/register",
                     json={"nickname": f"wsauth-a-{secrets.token_hex(4)}", "password": "test-pw"})
        assert reg.status_code == 200, reg.text
        tmp = tempfile.mkdtemp()
        ws = c.post("/api/workspaces", json={"root_path": tmp}).json()
        sess = c.post("/api/sessions", json={"workspace_id": ws["id"]}).json()
        c.zc_sid = sess["id"]
        c.zc_wsid = ws["id"]
        yield c
        c.delete(f"/api/workspaces/{c.zc_wsid}")


def test_匿名连聊天流被4401拒绝(client):
    # 注入脚本化模型：现状（无认证）下匿名连接会真被应答——首帧断言把「泄漏」
    # 变成干净失败，而非死等（chat 端点 accept 后静默，不发消息则 receive 永阻）。
    # 匿名 = 同一 TestClient 清掉会话 cookie：另起嵌套 TestClient 会把 lifespan
    # 跑第二遍，shutdown 时 dispose/关环会波及共享 app 的连接池。
    client.app.state.llm_factory = lambda cfg: ScriptedLLM(_done("泄露"))
    token_a = client.cookies.get("zc_actor")
    client.cookies.clear()
    try:
        with pytest.raises(WebSocketDisconnect) as ei:
            with client.websocket_connect(f"/ws/chat/{client.zc_sid}") as ws:
                ws.send_json({"type": "user_message", "content": "喂"})
                ev = ws.receive_json()
                assert ev["type"] != "message_done", "匿名连接不该被模型应答"
        assert ei.value.code == 4401
    finally:
        client.cookies.set("zc_actor", token_a)


def test_匿名连部署流被4401拒绝(client):
    token_a = client.cookies.get("zc_actor")
    client.cookies.clear()
    try:
        with pytest.raises(WebSocketDisconnect) as ei:
            with client.websocket_connect("/ws/deploy/999999") as ws:
                ev = ws.receive_json()
                assert ev["type"] != "hello", "匿名连接不该订阅到部署事件"
        assert ei.value.code == 4401
    finally:
        client.cookies.set("zc_actor", token_a)


def test_跨用户连他人会话流被拒(client):
    """B 带自己的有效会话连 A 的 session：连接被拒（close code 4404，越权统一语义的 WS 映射）。"""
    client.app.state.llm_factory = lambda cfg: ScriptedLLM(_done("串话"))
    token_a = client.cookies.get("zc_actor")
    nick_b = f"wsauth-b-{secrets.token_hex(4)}"
    r = client.post("/api/auth/register",
                    json={"nickname": nick_b, "password": "test-pw"})
    assert r.status_code == 200, r.text
    token_b = client.cookies.get("zc_actor")  # register 的 Set-Cookie 已写进 jar
    # 先 clear 再 set：jar 里已有域匹配的 A cookie，叠 set 会发重复 Cookie 头
    client.cookies.clear()
    client.cookies.set("zc_actor", token_b)    # 切到 B 的凭据连 A 的会话
    try:
        with pytest.raises(WebSocketDisconnect) as ei:
            with client.websocket_connect(f"/ws/chat/{client.zc_sid}") as ws:
                ws.send_json({"type": "user_message", "content": "串个话"})
                ev = ws.receive_json()
                assert ev["type"] != "message_done", "他人会话不该被跨账号驱动"
        assert ei.value.code == 4404
    finally:
        client.cookies.clear()
        client.cookies.set("zc_actor", token_a)  # 还原 A：teardown 删 workspace 要用
