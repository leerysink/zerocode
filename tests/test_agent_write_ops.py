"""Week 4 写面：delete_file / move_file / revert_change。

三条不变式钉在这里：
- delete 真删盘但把原文全文记进 file_change，回滚能把内容逐字还原；
- move 目标已存在一律拒，留痕 action=move（不解析内容，二进制可移）；
- revert 以路径为粒度取本会话最近一条**带原文**的记录，写前比对磁盘与 after_content，
  不一致就拒绝——绝不覆盖用户在本轮之后的改动。
"""
import pytest
from sqlalchemy import text as sql

from app.agent.tools import ToolContext, ToolError, execute_tool
from app.db import make_session_factory

PROTECTED_APP = {"slug": "demo"}


class Recorder:
    def __init__(self):
        self.sent = []

    async def __call__(self, ev):
        self.sent.append(ev)


@pytest.fixture
async def ws(test_engine, tmp_path):
    sf = make_session_factory(test_engine)
    async with sf() as s:
        wid = (await s.execute(sql(
            "INSERT INTO workspace (name, root_path) VALUES ('t', :p) RETURNING id"),
            {"p": str(tmp_path)})).scalar()
        sid = (await s.execute(sql(
            "INSERT INTO chat_session (workspace_id) VALUES (:w) RETURNING id"),
            {"w": wid})).scalar()
        await s.commit()
    return tmp_path, sid, sf


def ctx_of(root, sid, sf):
    return ToolContext(root=root, session_id=sid, session_factory=sf, send=Recorder())


async def _changes(sf, sid):
    async with sf() as s:
        rows = (await s.execute(sql(
            "SELECT path, action, before_content, after_content, diff FROM file_change "
            "WHERE session_id=:s ORDER BY id"), {"s": sid})).mappings().all()
    return [dict(r) for r in rows]


# ---------- delete_file ----------

async def test_delete_records_full_text_and_can_be_reverted(ws):
    root, sid, sf = ws
    (root / "src").mkdir()
    f = root / "src" / "old.py"
    f.write_text("print('bye')\r\n", encoding="utf-8", newline="")
    ctx = ctx_of(root, sid, sf)
    r = await execute_tool(ctx, "delete_file", {"path": "src/old.py"})
    assert r["ok"] is True and r["revertible"] is True
    assert not f.exists()
    rows = await _changes(sf, sid)
    assert rows[-1]["action"] == "delete"
    assert rows[-1]["before_content"] == "print('bye')\r\n"
    assert rows[-1]["after_content"] == ""
    assert any(ev["type"] == "file_change" and ev["action"] == "delete" for ev in ctx.send.sent)


async def test_delete_refuses_directories_and_protected_paths(ws):
    root, sid, sf = ws
    ctx = ctx_of(root, sid, sf)
    (root / "pkg").mkdir()
    (root / "pkg" / "keep.txt").write_text("x", encoding="utf-8")
    (root / "app.json").write_text("{}", encoding="utf-8")
    (root / "deploy").mkdir()
    (root / "deploy" / "docker-compose.yml").write_text("services: {}", encoding="utf-8")
    for path, word in (("pkg", "目录"), ("app.json", "平台管理"),
                       ("deploy/docker-compose.yml", "平台管理")):
        with pytest.raises(ToolError) as ei:
            await execute_tool(ctx, "delete_file", {"path": path})
        assert word in str(ei.value)
    assert (root / "pkg" / "keep.txt").is_file()
    with pytest.raises(ToolError):
        await execute_tool(ctx, "delete_file", {"path": "nope.txt"})


async def test_delete_binary_says_not_revertible(ws):
    root, sid, sf = ws
    f = root / "logo.bin"
    f.write_bytes(b"\x00\xff\x00")
    ctx = ctx_of(root, sid, sf)
    r = await execute_tool(ctx, "delete_file", {"path": "logo.bin"})
    assert r["ok"] is True and r["revertible"] is False and "不可回滚" in r["notice"]
    assert not f.exists()
    rows = await _changes(sf, sid)
    assert rows[-1]["action"] == "delete" and rows[-1]["before_content"] is None


# ---------- move_file ----------

async def test_move_keeps_content_and_records_action(ws):
    root, sid, sf = ws
    (root / "a").mkdir()
    (root / "b").mkdir()
    (root / "a" / "x.txt").write_text("你好", encoding="utf-8")
    ctx = ctx_of(root, sid, sf)
    r = await execute_tool(ctx, "move_file", {"old_path": "a/x.txt", "new_path": "b/y.txt"})
    assert r["ok"] is True and r["from"] == "a/x.txt" and r["to"] == "b/y.txt"
    assert not (root / "a" / "x.txt").exists()
    assert (root / "b" / "y.txt").read_text(encoding="utf-8") == "你好"
    rows = await _changes(sf, sid)
    assert rows[-1]["action"] == "move" and rows[-1]["path"] == "a/x.txt"
    assert "b/y.txt" in rows[-1]["diff"] if rows[-1].get("diff") else True


async def test_move_refuses_existing_target_and_directories(ws):
    root, sid, sf = ws
    (root / "a").mkdir()
    (root / "a" / "x.txt").write_text("1", encoding="utf-8")
    (root / "taken.txt").write_text("2", encoding="utf-8")
    ctx = ctx_of(root, sid, sf)
    with pytest.raises(ToolError) as ei:
        await execute_tool(ctx, "move_file", {"old_path": "a/x.txt", "new_path": "taken.txt"})
    assert "已存在" in str(ei.value)
    with pytest.raises(ToolError):
        await execute_tool(ctx, "move_file", {"old_path": "a", "new_path": "b"})
    assert (root / "a" / "x.txt").is_file()


async def test_move_refuses_protected_paths(ws):
    root, sid, sf = ws
    (root / "app.json").write_text("{}", encoding="utf-8")
    (root / "deploy").mkdir()
    (root / "deploy" / "a.yml").write_text("x", encoding="utf-8")
    ctx = ctx_of(root, sid, sf)
    with pytest.raises(ToolError):
        await execute_tool(ctx, "move_file", {"old_path": "app.json", "new_path": "z.json"})
    with pytest.raises(ToolError):
        await execute_tool(ctx, "move_file", {"old_path": "deploy/a.yml", "new_path": "b.yml"})
    assert (root / "app.json").is_file()


# ---------- revert_change ----------

async def test_revert_after_delete_restores_exact_bytes(ws):
    root, sid, sf = ws
    (root / "v.txt").write_text("第一行\r\n第二行\r\n", encoding="utf-8", newline="")
    ctx = ctx_of(root, sid, sf)
    await execute_tool(ctx, "delete_file", {"path": "v.txt"})
    r = await execute_tool(ctx, "revert_change", {"path": "v.txt"})
    assert r["ok"] is True and r["change_id"]
    assert (root / "v.txt").read_bytes() == "第一行\r\n第二行\r\n".encode()
    rows = await _changes(sf, sid)
    assert rows[-1]["action"] == "create" and rows[-1]["after_content"].startswith("第一行")


async def test_revert_after_edit_restores_previous_content(ws):
    root, sid, sf = ws
    f = root / "e.txt"
    f.write_text("old", encoding="utf-8")
    ctx = ctx_of(root, sid, sf)
    await execute_tool(ctx, "edit_file", {"path": "e.txt", "old_string": "old", "new_string": "new"})
    assert f.read_text(encoding="utf-8") == "new"
    r = await execute_tool(ctx, "revert_change", {"path": "e.txt"})
    assert r["ok"] is True
    assert f.read_text(encoding="utf-8") == "old"


async def test_revert_refuses_when_disk_drifted(ws):
    root, sid, sf = ws
    f = root / "d.txt"
    f.write_text("v1", encoding="utf-8")
    ctx = ctx_of(root, sid, sf)
    await execute_tool(ctx, "edit_file", {"path": "d.txt", "old_string": "v1", "new_string": "v2"})
    f.write_text("user changed by hand", encoding="utf-8")
    with pytest.raises(ToolError) as ei:
        await execute_tool(ctx, "revert_change", {"path": "d.txt"})
    assert "不一致" in str(ei.value) or "已改动" in str(ei.value)
    assert f.read_text(encoding="utf-8") == "user changed by hand"


async def test_revert_unknown_path_and_non_revertible_record(ws):
    root, sid, sf = ws
    ctx = ctx_of(root, sid, sf)
    with pytest.raises(ToolError):
        await execute_tool(ctx, "revert_change", {"path": "never-touched.txt"})
    (root / "img.bin").write_bytes(b"\x00\x01")
    await execute_tool(ctx, "delete_file", {"path": "img.bin"})
    with pytest.raises(ToolError) as ei:
        await execute_tool(ctx, "revert_change", {"path": "img.bin"})
    assert "不可回滚" in str(ei.value)


async def test_revert_of_creation_deletes_the_file(ws):
    root, sid, sf = ws
    ctx = ctx_of(root, sid, sf)
    await execute_tool(ctx, "write_file", {"path": "new.txt", "content": "hello"})
    r = await execute_tool(ctx, "revert_change", {"path": "new.txt"})
    assert r["ok"] is True and r["deleted"] is True
    assert not (root / "new.txt").exists()


async def test_revert_without_session_store_is_refused(tmp_path):
    ctx = ToolContext(root=tmp_path, session_id=1, session_factory=None, send=Recorder())
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    with pytest.raises(ToolError):
        await execute_tool(ctx, "revert_change", {"path": "a.txt"})
