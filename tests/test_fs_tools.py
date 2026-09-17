import dataclasses, json, pytest
from pathlib import Path
from app.agent.tools import ToolContext, ToolError, resolve_in_workspace, execute_tool

@dataclasses.dataclass
class FakeSF:  # 不落库的最小替代：execute_tool 内写 file_change 需 session_factory
    events: list

class Recorder:
    def __init__(self): self.sent = []
    async def __call__(self, ev): self.sent.append(ev)

def make_ctx(tmp_path, sf=None):
    return ToolContext(root=tmp_path, session_id=1, session_factory=sf, send=Recorder())

@pytest.mark.asyncio
async def test_path_guard(tmp_path):
    resolve_in_workspace(tmp_path, "a/b.txt")
    resolve_in_workspace(tmp_path, None)
    with pytest.raises(ToolError):
        resolve_in_workspace(tmp_path, "../outside.txt")
    with pytest.raises(ToolError):
        resolve_in_workspace(tmp_path, "C:\\Windows\\system32")

@pytest.mark.asyncio
async def test_write_and_edit_and_list(tmp_path):
    ctx = make_ctx(tmp_path)
    r1 = await execute_tool(ctx, "write_file", {"path": "src/a.py", "content": "print('hi')\n"})
    assert r1["ok"] is True
    assert (tmp_path / "src" / "a.py").read_text(encoding="utf-8") == "print('hi')\n"
    r2 = await execute_tool(ctx, "edit_file", {"path": "src/a.py", "old_string": "hi", "new_string": "hello"})
    assert r2["ok"] is True
    assert "hello" in (tmp_path / "src" / "a.py").read_text(encoding="utf-8")
    r3 = await execute_tool(ctx, "list_dir", {"path": "src"})
    assert any(e["name"] == "a.py" for e in r3["entries"])
    assert any(ev["type"] == "file_change" for ev in ctx.send.sent)

@pytest.mark.asyncio
async def test_edit_errors(tmp_path):
    ctx = make_ctx(tmp_path)
    await execute_tool(ctx, "write_file", {"path": "a.txt", "content": "x x x"})
    with pytest.raises(ToolError):
        await execute_tool(ctx, "edit_file", {"path": "a.txt", "old_string": "zz", "new_string": "y"})  # 0 次
    with pytest.raises(ToolError):
        await execute_tool(ctx, "edit_file", {"path": "a.txt", "old_string": "x", "new_string": "y"})  # 3 次

@pytest.mark.asyncio
async def test_read_limits(tmp_path):
    ctx = make_ctx(tmp_path)
    (tmp_path / "big.bin").write_bytes(b"\x00\xff" * 300_000)
    with pytest.raises(ToolError):
        await execute_tool(ctx, "read_file", {"path": "big.bin"})
    (tmp_path / "ok.txt").write_text("好的", encoding="utf-8")
    r = await execute_tool(ctx, "read_file", {"path": "ok.txt"})
    assert "好的" in r["content"]

@pytest.mark.asyncio
async def test_edit_rejects_non_utf8(tmp_path):
    ctx = make_ctx(tmp_path)
    p = tmp_path / "gbk.txt"
    raw = "print('hi')  # ".encode("utf-8") + "中文注释".encode("gbk")  # 含非法 UTF-8 字节
    p.write_bytes(raw)
    with pytest.raises(ToolError):
        await execute_tool(ctx, "edit_file", {"path": "gbk.txt", "old_string": "print", "new_string": "pass"})
    assert p.read_bytes() == raw  # 字节级比对：原文未变
    # 二进制扩展名守卫：字节恰为合法 UTF-8 也必须拒绝
    q = tmp_path / "img.png"
    q.write_bytes(b"hello png")
    with pytest.raises(ToolError):
        await execute_tool(ctx, "edit_file", {"path": "img.png", "old_string": "hello", "new_string": "x"})
    assert q.read_bytes() == b"hello png"

@pytest.mark.asyncio
async def test_write_rejects_non_utf8_and_root(tmp_path):
    ctx = make_ctx(tmp_path)
    p = tmp_path / "gbk.txt"
    raw = "中文".encode("gbk")  # 非法 UTF-8 字节
    p.write_bytes(raw)
    with pytest.raises(ToolError):
        await execute_tool(ctx, "write_file", {"path": "gbk.txt", "content": "new"})
    assert p.read_bytes() == raw  # 拒绝覆写：原文未变
    with pytest.raises(ToolError):  # root 目录守卫：path 为空指向工作区根（目录）
        await execute_tool(ctx, "write_file", {"path": "", "content": "x"})

@pytest.mark.asyncio
async def test_edit_preserves_line_endings(tmp_path):
    # 行尾字节保真：edit 只改替换区，其余行尾（CRLF/LF 混排）必须逐字节原样保留
    ctx = make_ctx(tmp_path)
    p = tmp_path / "mixed.txt"
    p.write_bytes(b"alpha\r\nbeta\n")
    await execute_tool(ctx, "edit_file", {"path": "mixed.txt", "old_string": "beta", "new_string": "gamma"})
    assert p.read_bytes() == b"alpha\r\ngamma\n"

@pytest.mark.asyncio
async def test_write_preserves_line_endings(tmp_path):
    # write_file 不得把 \n 翻转为 \r\n（Windows 默认文本模式会翻转，污染 LF 目标仓库）
    ctx = make_ctx(tmp_path)
    await execute_tool(ctx, "write_file", {"path": "lf.txt", "content": "l1\nl2\n"})
    assert (tmp_path / "lf.txt").read_bytes() == b"l1\nl2\n"
