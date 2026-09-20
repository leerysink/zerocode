import shutil
from pathlib import Path

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

@pytest.mark.asyncio
async def test_save_preserves_line_endings(client, tmp_path):
    # UI 保存路径同款字节保真：LF 不得被翻转为 CRLF
    ws = await _ws(client, tmp_path)
    r = await client.put("/api/files/save", json={"workspace_id": ws["id"], "path": "lf.txt", "content": "one\ntwo\n"})
    assert r.status_code == 200
    assert (tmp_path / "lf.txt").read_bytes() == b"one\ntwo\n"


# ---------- 工作区文件访问三件套：单层列目录 / 下载 / 调起系统文件管理器 ----------
# 内置「类桌面」文件管理器的数据面：ls 只给单层（不递归、不拉全树），download 允许二进制，
# open-folder 只在后端与浏览器同机时才有意义（判定在前端，见 utils/openFolder.js）。

@pytest.mark.asyncio
async def test_ls_lists_one_level_sorted_with_meta(client, tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "inner.txt").write_text("ii", encoding="utf-8")
    (tmp_path / "b.txt").write_text("hello", encoding="utf-8")
    (tmp_path / "a.md").write_text("# t", encoding="utf-8")
    (tmp_path / ".git").mkdir()  # 与 /fs/tree 共用同一份忽略名单
    ws = await _ws(client, tmp_path)

    body = (await client.get("/api/fs/ls", params={"workspace_id": ws["id"], "path": ""})).json()
    assert [e["name"] for e in body["entries"]] == ["src", "a.md", "b.txt"]  # 目录优先 + 名称升序
    src = body["entries"][0]
    assert src["is_dir"] is True and src["path"] == "src" and "children" not in src  # 单层，不递归
    md = next(e for e in body["entries"] if e["name"] == "a.md")
    assert md["is_dir"] is False and md["size"] == 3 and md["mtime"]
    assert body["path"] == "" and body["parent"] is None  # 根层没有上级

    sub = (await client.get("/api/fs/ls", params={"workspace_id": ws["id"], "path": "src"})).json()
    assert [e["name"] for e in sub["entries"]] == ["inner.txt"]
    assert sub["path"] == "src" and sub["parent"] == ""  # 上级回空串 = 工作区根


@pytest.mark.asyncio
async def test_ls_rejects_escape_missing_and_file(client, tmp_path):
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    ws = await _ws(client, tmp_path)
    assert (await client.get("/api/fs/ls", params={"workspace_id": ws["id"],
            "path": "../x"})).status_code == 400  # 越界
    assert (await client.get("/api/fs/ls", params={"workspace_id": ws["id"],
            "path": str(tmp_path)})).status_code == 400  # 绝对路径同样拒
    assert (await client.get("/api/fs/ls", params={"workspace_id": ws["id"],
            "path": "nope"})).status_code == 404  # 目录不存在
    assert (await client.get("/api/fs/ls", params={"workspace_id": ws["id"],
            "path": "a.txt"})).status_code == 400  # 文件不是目录
    assert (await client.get(f"/api/fs/ls?workspace_id=999999&path=")).status_code == 404  # 工作区不存在


@pytest.mark.asyncio
async def test_download_allows_binary_and_sets_filename(client, tmp_path):
    (tmp_path / "报告.txt").write_text("内容", encoding="utf-8")
    (tmp_path / "img.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x01")  # 预览拒读，下载必须放行
    ws = await _ws(client, tmp_path)

    r = await client.get("/api/fs/download", params={"workspace_id": ws["id"], "path": "报告.txt"})
    assert r.status_code == 200 and r.content == "内容".encode("utf-8")
    cd = r.headers["content-disposition"]
    # 中文名只能走 RFC 5987 百分号编码（Starlette 输出小写 utf-8''，直接塞原始字节会 latin-1 报错）
    assert cd.startswith("attachment") and "filename*=utf-8''" in cd.lower()
    assert "%E6%8A%A5%E5%91%8A.txt" in cd

    assert (await client.get("/api/fs/download", params={"workspace_id": ws["id"],
            "path": "img.png"})).status_code == 200
    assert (await client.get("/api/fs/download", params={"workspace_id": ws["id"],
            "path": "../a.txt"})).status_code == 400  # 真越界才拒（sub/../a.txt 折回工作区内属合法）
    assert (await client.get("/api/fs/download", params={"workspace_id": ws["id"],
            "path": "nope.txt"})).status_code == 404  # 不存在
    assert (await client.get("/api/fs/download", params={"workspace_id": ws["id"],
            "path": ""})).status_code == 404  # 目录不可下载


@pytest.mark.asyncio
async def test_open_folder_launches_manager_with_workspace_root(client, tmp_path, monkeypatch):
    import app.api.files as files_api
    launched = []
    monkeypatch.setattr(files_api, "open_in_file_manager", lambda p: launched.append(Path(p)))
    ws = await _ws(client, tmp_path)

    r = await client.post("/api/fs/open-folder", json={"workspace_id": ws["id"]})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert launched == [Path(str(tmp_path))]  # 只开登记过的工作区根，不接受任意路径入参


@pytest.mark.asyncio
async def test_open_folder_rejects_gone_dir_and_missing_workspace(client, tmp_path, monkeypatch):
    import app.api.files as files_api
    monkeypatch.setattr(files_api, "open_in_file_manager", lambda p: None)
    assert (await client.post("/api/fs/open-folder", json={"workspace_id": 999999})).status_code == 404

    (tmp_path / "gone").mkdir()
    ws = await _ws(client, tmp_path / "gone")
    shutil.rmtree(tmp_path / "gone")
    r = await client.post("/api/fs/open-folder", json={"workspace_id": ws["id"]})
    assert r.status_code == 400 and "不存在" in r.json()["detail"]
    # 目录已消失时绝不能再起文件管理器（上一用例的 lambda 会记不到调用，这里直接断言未抛错即可）


@pytest.mark.asyncio
async def test_open_folder_maps_launch_failure_to_500(client, tmp_path, monkeypatch):
    import app.api.files as files_api

    def boom(p):
        raise OSError("找不到 xdg-open")

    monkeypatch.setattr(files_api, "open_in_file_manager", boom)
    ws = await _ws(client, tmp_path)
    r = await client.post("/api/fs/open-folder", json={"workspace_id": ws["id"]})
    assert r.status_code == 500 and "打开文件夹失败" in r.json()["detail"]  # 前端据此降级到内置文件管理器


@pytest.mark.asyncio
async def test_app_workspace_deploy_denied_read_hidden_tree_ls(client, tmp_path):
    """终审 M-3 残余收口：应用工作区的 deploy/** UI 读面拒读，树/单层列表隐藏；app.json 读放行。"""
    (tmp_path / "app.json").write_text('{"schema": "zc-app/1"}', encoding="utf-8")
    (tmp_path / "deploy").mkdir()
    (tmp_path / "deploy" / ".env").write_text("DEV_PASSWORD=sekret", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x=1", encoding="utf-8")
    ws = await _ws(client, tmp_path)
    # 读禁：精确、大小写与折叠变形都拒
    for p in ("deploy/.env", "DEPLOY/.env", "src/../deploy/.env"):
        r = await client.get(f"/api/files/read?workspace_id={ws['id']}&path={p}")
        assert r.status_code == 403 and "平台" in r.json()["detail"], p
    # 下载是第二读通道——同样必须拒（fail-closed）
    for p in ("deploy/.env", "deploy%2Fdev-compose.yml"):
        r = await client.get(f"/api/fs/download?workspace_id={ws['id']}&path={p}")
        assert r.status_code == 403, p
    # app.json（无口令的清单）读仍放行
    r = await client.get(f"/api/files/read?workspace_id={ws['id']}&path=app.json")
    assert r.status_code == 200
    # 树与单层列表隐藏保留区目录
    r = await client.get(f"/api/fs/tree?workspace_id={ws['id']}")
    names = [n["name"] for n in r.json()]
    assert "src" in names and "deploy" not in names
    r = await client.get(f"/api/fs/ls?workspace_id={ws['id']}&path=")
    assert "deploy" not in [e["name"] for e in r.json()["entries"]]
    # 普通工作区（无 app.json）里的 deploy 目录零影响
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "deploy").mkdir()
    ws2 = await _ws(client, str(plain))
    r = await client.get(f"/api/fs/tree?workspace_id={ws2['id']}")
    assert "deploy" in [n["name"] for n in r.json()]
    r = await client.get(f"/api/files/read?workspace_id={ws2['id']}&path=deploy/.env")
    assert r.status_code == 404  # 不存在≠被禁，普通工作区走原有语义

