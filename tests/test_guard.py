# tests/test_guard.py
from pathlib import Path
import pytest
from app.deploy.guard import is_protected
from app.agent.tools import ToolContext, ToolError, execute_tool

def test_is_protected(tmp_path):
    (tmp_path / "app.json").write_text("{}", encoding="utf-8")
    assert is_protected(tmp_path, "app.json")
    assert is_protected(tmp_path, "deploy/.env")
    assert is_protected(tmp_path, "deploy\\dev-compose.yml")   # Windows 反斜杠归一
    assert not is_protected(tmp_path, "backend/app/main.py")
    plain = tmp_path / "plain"
    plain.mkdir()
    assert not is_protected(plain, "app.json")                 # 非应用工作区不保护

class Recorder:  # v1 的 execute_tool 写路径会 await ctx.send(...)，send=None 会 TypeError，故用可调用替身
    def __init__(self): self.sent = []
    async def __call__(self, ev): self.sent.append(ev)

@pytest.mark.asyncio
async def test_write_tool_rejected(tmp_path):
    (tmp_path / "app.json").write_text("{}", encoding="utf-8")
    ctx = ToolContext(root=tmp_path, session_id=1, session_factory=None, send=Recorder())
    with pytest.raises(ToolError):
        await execute_tool(ctx, "write_file", {"path": "deploy/.env", "content": "x"})
    with pytest.raises(ToolError):
        await execute_tool(ctx, "edit_file", {"path": "app.json", "old_string": "{", "new_string": "{"})
    # 非保护区正常
    r = await execute_tool(ctx, "write_file", {"path": "backend/x.py", "content": "ok"})
    assert r["ok"] is True
