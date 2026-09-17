import pytest

@pytest.mark.asyncio
async def test_workspace_crud_and_browse(client, tmp_path):
    r = await client.post("/api/workspaces", json={"name": "demo", "root_path": str(tmp_path)})
    assert r.status_code == 200
    ws = r.json()
    assert ws["name"] == "demo"
    # 重复路径 409
    r2 = await client.post("/api/workspaces", json={"root_path": str(tmp_path)})
    assert r2.status_code == 409
    # 不存在路径 400
    r3 = await client.post("/api/workspaces", json={"root_path": str(tmp_path / "nope")})
    assert r3.status_code == 400
    # 列表带 session_count
    lst = (await client.get("/api/workspaces")).json()
    assert lst[0]["session_count"] == 0
    # 浏览：空 path → 盘符
    r4 = (await client.post("/api/fs/browse", json={})).json()
    assert any(e["is_dir"] for e in r4["entries"])
    # 浏览子目录
    (tmp_path / "sub").mkdir()
    r5 = (await client.post("/api/fs/browse", json={"path": str(tmp_path)})).json()
    names = [e["name"] for e in r5["entries"]]
    assert "sub" in names
    # 删除
    assert (await client.delete(f"/api/workspaces/{ws['id']}")).status_code == 200
