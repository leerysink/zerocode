# tests/test_guard.py
from pathlib import Path
import pytest
from app.deploy.guard import is_protected, is_secret_read
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


# ---- M-3（终审）：deploy/** 读面收口——凭据不可进模型上下文与 UI 预览 ----

def test_is_secret_read(tmp_path):
    # 读禁只圈 deploy/**（密文面）；app.json 读放行（AI 需看清单）；折叠/大小写与 is_protected 同逻辑
    (tmp_path / "app.json").write_text("{}", encoding="utf-8")
    (tmp_path / "deploy").mkdir()
    for p in ("deploy", "deploy/.env", "DEPLOY/.env", "./deploy/.env", "backend/../deploy/.env",
              "deploy\\dev-compose.yml", "deploy\\dev/../.env"):
        assert is_secret_read(tmp_path, p), p
    assert not is_secret_read(tmp_path, "app.json")            # 清单可读
    assert not is_secret_read(tmp_path, "backend/app/main.py")
    assert not is_secret_read(tmp_path, "deployed/notes.md")   # 前缀相近不误判
    assert not is_secret_read(tmp_path, "../deploy/.env")      # 越根逃逸：guard 不管，v1 越界兜底
    plain = tmp_path / "plain"
    plain.mkdir()
    assert not is_secret_read(plain, "deploy/.env")            # 非应用工作区不生效


@pytest.mark.asyncio
async def test_ai_read_file_rejected(tmp_path):
    # M-3①：AI 工具 read_file 命中 deploy/** → ToolError 中文边界提示；app.json 与业务文件照读
    (tmp_path / "app.json").write_text('{"a":1}', encoding="utf-8")
    (tmp_path / "deploy").mkdir()
    env = tmp_path / "deploy" / ".env"
    env.write_text("DEV_PASSWORD=super-secret", encoding="utf-8")
    (tmp_path / "deploy" / "dev-compose.yml").write_text("x", encoding="utf-8")
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "main.py").write_text("print(1)", encoding="utf-8")
    ctx = ToolContext(root=tmp_path, session_id=1, session_factory=None, send=Recorder())
    for p in ("deploy/.env", "DEPLOY/.env", "./deploy/.env", "deploy/dev-compose.yml"):
        with pytest.raises(ToolError) as ei:
            await execute_tool(ctx, "read_file", {"path": p})
        assert "deploy/ 为平台保留区，凭据不可读取" in str(ei.value), p
        assert "super-secret" not in str(ei.value)             # 错误信息本身不得回带内容
    # 越根变形仍由 v1 拒（400 语义不变）
    with pytest.raises(ToolError):
        await execute_tool(ctx, "read_file", {"path": "../deploy/.env"})
    # 非密文面照常可读
    r = await execute_tool(ctx, "read_file", {"path": "app.json"})
    assert r["content"] == '{"a":1}'
    r2 = await execute_tool(ctx, "read_file", {"path": "backend/main.py"})
    assert r2["content"] == "print(1)"


@pytest.mark.asyncio
async def test_plain_workspace_reads_unaffected(tmp_path, client):
    # 非应用工作区零影响：没有 app.json 的普通工作区里同名 deploy/ 目录与文件照读照列（不误伤 v1）
    (tmp_path / "deploy").mkdir()
    (tmp_path / "deploy" / ".env").write_text("PLAIN=1", encoding="utf-8")
    ws = (await client.post("/api/workspaces", json={"root_path": str(tmp_path)})).json()
    r = await client.get("/api/files/read", params={"workspace_id": ws["id"], "path": "deploy/.env"})
    assert r.status_code == 200 and r.json()["content"] == "PLAIN=1"
    names = [n["name"] for n in (await client.get(f"/api/fs/tree?workspace_id={ws['id']}")).json()]
    assert "deploy" in names
