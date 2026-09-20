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

def test_is_protected_folding_and_case(tmp_path):
    # 审查 C-1：判定前折叠（./ 与 .. 点段）与大小写归案；M-1：保留目录本身
    (tmp_path / "app.json").write_text("{}", encoding="utf-8")
    (tmp_path / "deploy").mkdir()
    for p in ("./deploy/.env", "backend/../deploy/.env", "DEPLOY/.env", "APP.JSON"):   # 四类绕过输入
        assert is_protected(tmp_path, p), p
    assert is_protected(tmp_path, "App.JSON")
    assert is_protected(tmp_path, "deploy")            # 保留目录本身（M-1）
    assert is_protected(tmp_path, "deploy/")           # 尾斜杠：normpath 折叠后仍命中
    assert is_protected(tmp_path, "deploy\\dev-compose.yml")   # 反斜杠既有例不回归
    assert is_protected(tmp_path, "./app.json")
    assert is_protected(tmp_path, "app.json.")         # NTFS 尾点形变
    assert is_protected(tmp_path, "app.json ")         # 尾空格形变
    assert is_protected(tmp_path, "backend\\..\\deploy\\dev\\..\\.env")
    assert not is_protected(tmp_path, "../outside")    # 折叠后越根逃逸：guard 不管，v1 越界兜底
    assert not is_protected(tmp_path, "a/../../b")
    assert not is_protected(tmp_path, "..")
    assert not is_protected(tmp_path, "backend/app/main.py")   # 业务文件不误判
    assert not is_protected(tmp_path, "deployed/notes.md")     # 前缀相近不误判

@pytest.mark.asyncio
async def test_write_tool_rejects_bypass_inputs(tmp_path):
    # I-1 行为断言：绕过输入经 execute_tool 必须 ToolError，且盘上字节未变（识破静默落盘）
    app = tmp_path / "app.json"
    app.write_text("{}", encoding="utf-8")
    (tmp_path / "deploy").mkdir()
    (tmp_path / "backend").mkdir()
    env = tmp_path / "deploy" / ".env"
    env.write_text("dbcred=super-secret", encoding="utf-8")
    env_bytes, app_bytes = env.read_bytes(), app.read_bytes()
    ctx = ToolContext(root=tmp_path, session_id=1, session_factory=None, send=Recorder())
    for p in ("./deploy/.env", "backend/../deploy/.env", "DEPLOY/.env", "deploy\\dev/../.env"):
        with pytest.raises(ToolError):
            await execute_tool(ctx, "write_file", {"path": p, "content": "pwned"})
        assert env.read_bytes() == env_bytes, p
    for p in ("./app.json", "APP.JSON", "app.json."):
        with pytest.raises(ToolError):
            await execute_tool(ctx, "edit_file", {"path": p, "old_string": "{", "new_string": "X"})
        assert app.read_bytes() == app_bytes, p

@pytest.mark.asyncio
async def test_ui_save_rejected_protected(tmp_path, client):
    # I-2：files_save 挂点 403 + 盘上字节未变；应用工作区 = tmp 手放 app.json，无需 app_project 行
    app = tmp_path / "app.json"
    app.write_text("{}", encoding="utf-8")
    (tmp_path / "deploy").mkdir()
    env = tmp_path / "deploy" / ".env"
    env.write_text("dbcred=super-secret", encoding="utf-8")
    env_bytes, app_bytes = env.read_bytes(), app.read_bytes()
    ws = (await client.post("/api/workspaces", json={"root_path": str(tmp_path)})).json()
    async def _denied(p, target, before):
        r = await client.put("/api/files/save", json={"workspace_id": ws["id"], "path": p, "content": "pwned"})
        assert r.status_code == 403, p
        assert target.read_bytes() == before, p
    for p in ("deploy/.env", "./deploy/.env", "backend/../deploy/.env", "DEPLOY/.env"):
        await _denied(p, env, env_bytes)
    for p in ("app.json", "./app.json", "APP.JSON"):
        await _denied(p, app, app_bytes)
    # 非保护区照常可写
    r2 = await client.put("/api/files/save", json={"workspace_id": ws["id"], "path": "backend/a.py", "content": "ok"})
    assert r2.status_code == 200
    assert (tmp_path / "backend" / "a.py").read_bytes() == b"ok"
