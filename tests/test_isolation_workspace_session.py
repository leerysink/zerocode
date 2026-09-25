# tests/test_isolation_workspace_session.py
"""Ticket 005 隔离迁移·工作区/会话域：A 的资源 B 永远不可见，越权按 id 直取一律 404。

接缝＝双账号 HTTP fixture（two_auth_clients）；覆盖列表过滤与详情/改名/删除/消息/
文件变更/文件系统浏览的传递隔离（chat_session/message 不加列，经 workspace JOIN 收口）。
"""
import pytest
from pathlib import Path

pytestmark = pytest.mark.asyncio


async def _mk_ws_session(client, root: str):
    Path(root).mkdir(parents=True, exist_ok=True)   # 注册要求目录真实存在
    ws = (await client.post("/api/workspaces", json={"root_path": root})).json()
    sess = (await client.post("/api/sessions", json={"workspace_id": ws["id"]})).json()
    return ws, sess


async def test_B的列表看不到A的工作区(two_auth_clients, tmp_path):
    alice, bob = two_auth_clients
    ws, _ = await _mk_ws_session(alice, str(tmp_path / "a"))
    listing = (await bob.get("/api/workspaces")).json()
    assert [w["id"] for w in listing] == []
    listing_a = (await alice.get("/api/workspaces")).json()
    assert [w["id"] for w in listing_a] == [ws["id"]]


async def test_B在A的工作区上建会话得404(two_auth_clients, tmp_path):
    alice, bob = two_auth_clients
    ws, _ = await _mk_ws_session(alice, str(tmp_path / "a"))
    r = await bob.post("/api/sessions", json={"workspace_id": ws["id"]})
    assert r.status_code == 404


async def test_B列A工作区的会话得404(two_auth_clients, tmp_path):
    alice, bob = two_auth_clients
    ws, sess = await _mk_ws_session(alice, str(tmp_path / "a"))
    r = await bob.get("/api/sessions", params={"workspace_id": ws["id"]})
    assert r.status_code == 404
    r2 = await alice.get("/api/sessions", params={"workspace_id": ws["id"]})
    assert r2.status_code == 200 and [x["id"] for x in r2.json()] == [sess["id"]]


async def test_B按id直取A的会话全部404(two_auth_clients, tmp_path):
    alice, bob = two_auth_clients
    _, sess = await _mk_ws_session(alice, str(tmp_path / "a"))
    sid = sess["id"]
    assert (await bob.get(f"/api/sessions/{sid}")).status_code == 404
    assert (await bob.patch(f"/api/sessions/{sid}", json={"title": "夺舍"})).status_code == 404
    assert (await bob.get(f"/api/sessions/{sid}/messages")).status_code == 404
    assert (await bob.get(f"/api/sessions/{sid}/file-changes")).status_code == 404
    assert (await bob.delete(f"/api/sessions/{sid}")).status_code == 404
    # A 自己一切正常
    assert (await alice.get(f"/api/sessions/{sid}")).status_code == 200
    assert (await alice.patch(f"/api/sessions/{sid}", json={"title": "改名"})).status_code == 200


async def test_B删A的工作区得404且资源仍在(two_auth_clients, tmp_path):
    alice, bob = two_auth_clients
    ws, sess = await _mk_ws_session(alice, str(tmp_path / "a"))
    assert (await bob.delete(f"/api/workspaces/{ws['id']}")).status_code == 404
    assert (await alice.get(f"/api/sessions/{sess['id']}")).status_code == 200
    assert (await alice.delete(f"/api/workspaces/{ws['id']}")).status_code == 200


async def test_B经文件接口摸A的工作区全404(two_auth_clients, tmp_path):
    alice, bob = two_auth_clients
    ws, _ = await _mk_ws_session(alice, str(tmp_path / "a"))
    root = str(tmp_path / "a")
    assert (await bob.get(f"/api/fs/tree?workspace_id={ws['id']}")).status_code == 404
    assert (await bob.get("/api/fs/ls", params={"workspace_id": ws["id"], "path": ""})).status_code == 404
    assert (await bob.get("/api/files/read",
                          params={"workspace_id": ws["id"], "path": "x.txt"})).status_code == 404
    r = await bob.put("/api/files/save",
                      json={"workspace_id": ws["id"], "path": "evil.txt", "content": "x"})
    assert r.status_code == 404
    assert not (Path(root) / "evil.txt").exists()
    # A 自己读自己的树没问题
    assert (await alice.get(f"/api/fs/tree?workspace_id={ws['id']}")).status_code == 200
