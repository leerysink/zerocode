import pytest

async def _ws(client, tmp_path):
    return (await client.post("/api/workspaces", json={"root_path": str(tmp_path)})).json()

@pytest.mark.asyncio
async def test_tree_ignores_and_nests(client, tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x=1", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / "node_modules").mkdir()
    ws = await _ws(client, tmp_path)
    r = (await client.get(f"/api/fs/tree?workspace_id={ws['id']}")).json()
    names = [n["name"] for n in r]
    assert "src" in names and ".git" not in names and "node_modules" not in names
    src = next(n for n in r if n["name"] == "src")
    assert src["type"] == "dir" and src["children"][0]["name"] == "a.py"

@pytest.mark.asyncio
async def test_read_and_save_and_guard(client, tmp_path):
    ws = await _ws(client, tmp_path)
    (tmp_path / "a.txt").write_text("你好", encoding="utf-8")
    r = (await client.get("/api/files/read", params={"workspace_id": ws["id"], "path": "a.txt"})).json()
    assert r["content"] == "你好"
    r2 = await client.put("/api/files/save", json={"workspace_id": ws["id"], "path": "sub/b.txt", "content": "新"})
    assert r2.status_code == 200
    assert (tmp_path / "sub" / "b.txt").read_text(encoding="utf-8") == "新"
    # 越界 400
    r3 = await client.get("/api/files/read", params={"workspace_id": ws["id"], "path": "../x.txt"})
    assert r3.status_code == 400
    # 二进制 400
    (tmp_path / "img.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 10)
    r4 = await client.get("/api/files/read", params={"workspace_id": ws["id"], "path": "img.png"})
    assert r4.status_code == 400
