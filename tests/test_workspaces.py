import pytest
from sqlalchemy import text

from app.deploy import engine


@pytest.mark.asyncio
async def test_delete_workspace_releases_the_bound_app_stacks(client, monkeypatch, tmp_path):
    """删工作区是应用记录消失的第二条路径（app_project 行靠 FK CASCADE 跟走），端口同样在库里被释放。

    与 DELETE /api/apps 同源收口：库里没了行而容器还在跑，等于把同一个端口原封不动埋给下一个应用。
    """
    ws = (await client.post("/api/workspaces",
                            json={"name": "demo", "root_path": str(tmp_path)})).json()
    async with client.app.state.session_factory() as s:
        await s.execute(text("INSERT INTO app_project (workspace_id, name, slug, scale, stack, "
                             "prod_port, dev_port) VALUES (:w, 'zsws', 'zsws', 'small', 'static', "
                             "8230, 8231)"), {"w": ws["id"]})
        await s.commit()
    calls = []

    async def fake_docker(*args, **kwargs):
        calls.append(args)
        return 0, ""

    monkeypatch.setattr(engine, "docker", fake_docker)
    assert (await client.delete(f"/api/workspaces/{ws['id']}")).status_code == 200
    assert [a[2] for a in calls if "-p" in a] == ["zc-zsws-dev", "zc-zsws-prod"]


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
