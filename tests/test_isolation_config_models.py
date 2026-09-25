# tests/test_isolation_config_models.py
"""Ticket 007 隔离迁移·配置域：模型档案与 llm.* 按账号隔离；平台级键共享。

接缝＝models/config 的 HTTP CRUD + resolve_model 的 owner 解析链；
agent.*、deploy.* 等平台级键（owner NULL）对所有用户可读是刻意保留的共享面。
"""
import pytest
from pathlib import Path
from sqlalchemy import text

from app.agent.model_resolver import resolve_model

pytestmark = pytest.mark.asyncio


async def _me(client) -> int:
    return (await client.get("/api/auth/me")).json()["id"]


async def _mk_ws_session(client, root: str):
    Path(root).mkdir(parents=True, exist_ok=True)
    ws = (await client.post("/api/workspaces", json={"root_path": root})).json()
    sess = (await client.post("/api/sessions", json={"workspace_id": ws["id"]})).json()
    return ws, sess


async def _mk_model(client, name: str, base_url: str, **kw) -> dict:
    body = {"name": name, "base_url": base_url, "api_key": "sk-x",
            "model": "m1", **kw}
    return (await client.post("/api/models", json=body)).json()


async def test_B列表看不到A的模型档案且改删全404(two_auth_clients):
    alice, bob = two_auth_clients
    m = await _mk_model(alice, "A档", "https://alice.invalid/v1")
    assert [x["id"] for x in (await bob.get("/api/models")).json()] == []
    body = {"name": "夺舍", "base_url": "https://evil.invalid/v1",
            "api_key": "sk-e", "model": "e"}
    assert (await bob.put(f"/api/models/{m['id']}", json=body)).status_code == 404
    assert (await bob.delete(f"/api/models/{m['id']}")).status_code == 404
    rows = (await alice.get("/api/models")).json()
    assert [x["id"] for x in rows] == [m["id"]] and rows[0]["name"] == "A档"


async def test_B把A的模型档案绑到自己会话得404(two_auth_clients, tmp_path):
    alice, bob = two_auth_clients
    m = await _mk_model(alice, "A档", "https://alice.invalid/v1")
    _, sess = await _mk_ws_session(bob, str(tmp_path / "b"))
    r = await bob.patch(f"/api/sessions/{sess['id']}/model",
                        json={"model_config_id": m["id"]})
    assert r.status_code == 404
    # bob 绑自己的档案照常成功
    mine = await _mk_model(bob, "B档", "https://bob.invalid/v1")
    r2 = await bob.patch(f"/api/sessions/{sess['id']}/model",
                         json={"model_config_id": mine["id"]})
    assert r2.status_code == 200


async def test_llm配置按用户隔离平台级键共享(two_auth_clients):
    alice, bob = two_auth_clients
    r = await alice.put("/api/config", json={"llm.model": "from-alice"})
    assert r.status_code == 200
    cfg_a = (await alice.get("/api/config")).json()
    cfg_b = (await bob.get("/api/config")).json()
    assert cfg_a["llm.model"] == "from-alice"
    # 后注册用户不继承创始人 llm.*（004 已划归）：设置页为空需自填，key 绝不串号
    assert cfg_b.get("llm.model") != "from-alice"
    assert cfg_b["llm.api_key"] == ""
    # 平台级键（owner NULL）双方可读
    assert "agent.max_tool_rounds" in cfg_a and "agent.max_tool_rounds" in cfg_b


async def test_Agent会话按owner解析模型配置(two_auth_clients, tmp_path, test_engine):
    """行为关键链：各自的默认档案只服务各自的会话；直绑他人行也被回落，不串 key。"""
    alice, bob = two_auth_clients
    sf = alice.app.state.session_factory
    await _mk_model(alice, "a-def", "https://alice.invalid/v1", is_default=True)
    await _mk_model(bob, "b-def", "https://bob.invalid/v1", is_default=True)
    _, sess_a = await _mk_ws_session(alice, str(tmp_path / "a"))
    _, sess_b = await _mk_ws_session(bob, str(tmp_path / "b"))
    ra = await resolve_model(sf, sess_a["id"], {})
    rb = await resolve_model(sf, sess_b["id"], {})
    assert ra["source"] == "default" and ra["base_url"] == "https://alice.invalid/v1"
    assert rb["source"] == "default" and rb["base_url"] == "https://bob.invalid/v1"
    # 旁路：把 alice 的档案 id 直绑进 bob 的会话（绕过 API 校验）→ 不得读出 alice 的 key
    async with sf() as s:
        mid = (await s.execute(text(
            "SELECT id FROM model_config WHERE base_url = :u"),
            {"u": "https://alice.invalid/v1"})).scalar()
        await s.execute(text("UPDATE chat_session SET model_config_id=:m WHERE id=:i"),
                        {"m": mid, "i": sess_b["id"]})
        await s.commit()
    rb2 = await resolve_model(sf, sess_b["id"], {})
    assert rb2["source"] == "default" and rb2["base_url"] == "https://bob.invalid/v1"
