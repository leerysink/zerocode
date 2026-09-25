import dataclasses, json, pytest
from pathlib import Path
from app.agent.tools import (ToolContext, ToolError, resolve_in_workspace, resolve_skill_path,
                             is_skill_scripts, execute_tool)

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


# ---- Ticket 005：受控读面——技能目录绝对路径白名单 read_file（spec §3 故事11）----

def _skills_fixture(tmp_path):
    """造一个与工作区分离的技能根：users/1/skills/pdf（含 SKILL.md / references / scripts / 非白名单件）。"""
    skills = tmp_path / "users" / "1" / "skills"
    pdf = skills / "pdf"
    (pdf / "references").mkdir(parents=True)
    (pdf / "SKILL.md").write_text("# PDF 技能\n读我全文", encoding="utf-8")
    (pdf / "references" / "guide.md").write_text("指南正文", encoding="utf-8")
    (pdf / "scripts").mkdir()
    (pdf / "scripts" / "run.py").write_text("print(1)", encoding="utf-8")
    (pdf / "blob.pdf").write_bytes(b"%PDF-1.4 fake")
    ws = tmp_path / "ws"
    ws.mkdir()
    return skills, pdf, ws


def _mk_skill_ctx(ws, skills_root):
    return ToolContext(root=ws, session_id=1, session_factory=None, send=Recorder(),
                       skills_root=skills_root)


def test_resolve_skill_path_fence(tmp_path):
    # 单元级路径围栏：根内命中、根外/相对/根缺失/None 一律 None（不开第二读面）
    root = tmp_path / "users" / "1" / "skills"
    inner = root / "pdf" / "SKILL.md"
    assert resolve_skill_path(root, str(inner)) == inner.resolve()
    assert resolve_skill_path(root, str(root)) == root.resolve()  # 根目录本身不算越界
    assert resolve_skill_path(root, str(tmp_path / "elsewhere.md")) is None  # 根外
    assert resolve_skill_path(root, "pdf/SKILL.md") is None  # 非绝对（工作区相对面管）
    assert resolve_skill_path(root, None) is None
    assert resolve_skill_path(None, str(inner)) is None  # 无读面默认关
    assert is_skill_scripts(root / "pdf" / "scripts" / "run.py") is True
    assert is_skill_scripts(root / "pdf" / "SKILL.md") is False


@pytest.mark.asyncio
async def test_skill_absolute_path_readable(tmp_path):
    skills, pdf, ws = _skills_fixture(tmp_path)
    ctx = _mk_skill_ctx(ws, skills)
    r = await execute_tool(ctx, "read_file", {"path": str(pdf / "SKILL.md")})
    assert "PDF 技能" in r["content"]
    r2 = await execute_tool(ctx, "read_file", {"path": str(pdf / "references" / "guide.md")})
    assert "指南正文" in r2["content"]  # references 相对链接支撑文件可读


@pytest.mark.asyncio
async def test_skill_scripts_denied(tmp_path):
    skills, pdf, ws = _skills_fixture(tmp_path)
    ctx = _mk_skill_ctx(ws, skills)
    with pytest.raises(ToolError) as e:
        await execute_tool(ctx, "read_file", {"path": str(pdf / "scripts" / "run.py")})
    assert "scripts" in str(e.value)


@pytest.mark.asyncio
async def test_skill_non_whitelist_ext_denied(tmp_path):
    skills, pdf, ws = _skills_fixture(tmp_path)
    ctx = _mk_skill_ctx(ws, skills)
    with pytest.raises(ToolError):
        await execute_tool(ctx, "read_file", {"path": str(pdf / "blob.pdf")})


@pytest.mark.asyncio
async def test_skill_out_of_root_absolute_denied(tmp_path):
    # 技能根外的绝对路径：回落不命中→维持原「必须是相对工作区的相对路径」错（不开二读面）
    skills, pdf, ws = _skills_fixture(tmp_path)
    ctx = _mk_skill_ctx(ws, skills)
    outside = tmp_path / "secret.txt"
    outside.write_text("不得读", encoding="utf-8")
    with pytest.raises(ToolError) as e:
        await execute_tool(ctx, "read_file", {"path": str(outside)})
    assert "相对" in str(e.value) or "越界" in str(e.value)


@pytest.mark.asyncio
async def test_write_not_allowed_into_skill_dir(tmp_path):
    # 无双开口：写类工具对技能母本不回落（只读）；绝对路径仍按越界拒绝且母本未变
    skills, pdf, ws = _skills_fixture(tmp_path)
    ctx = _mk_skill_ctx(ws, skills)
    target = pdf / "SKILL.md"
    with pytest.raises(ToolError):
        await execute_tool(ctx, "write_file", {"path": str(target), "content": "篡改"})
    assert "读我全文" in target.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_no_skills_root_means_no_read_face(tmp_path):
    # 默认 skills_root=None（历史工作区/无技能用户）：绝对路径无读面，同旧行为拒绝
    skills, pdf, ws = _skills_fixture(tmp_path)
    ctx = ToolContext(root=ws, session_id=1, session_factory=None, send=Recorder())
    with pytest.raises(ToolError):
        await execute_tool(ctx, "read_file", {"path": str(pdf / "SKILL.md")})


@pytest.mark.asyncio
async def test_workspace_relative_read_unchanged_with_skill_root(tmp_path):
    # 回归：开技能读面不影响工作区相对路径读取（既有行为零变化）
    skills, pdf, ws = _skills_fixture(tmp_path)
    ctx = _mk_skill_ctx(ws, skills)
    (ws / "a.txt").write_text("工作区内容", encoding="utf-8")
    r = await execute_tool(ctx, "read_file", {"path": "a.txt"})
    assert "工作区内容" in r["content"]
