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
