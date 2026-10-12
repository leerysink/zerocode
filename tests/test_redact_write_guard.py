"""Ticket 010 第五出口衍生护栏：拒绝把遮断占位符写进工作区文件。

第五出口让平台库/事件里的 tool_result 变成净化版（`<redacted:kind>`）。跨轮历史从
净化库重建，模型在后续轮次看到的前一次 read_file 结果已是 `<redacted:password>` 占位符。
若模型把这个占位符当成文件真实内容，构造出 write_file/edit_file 的入参，而**工具执行收的是
原始 args**（用户拍板：不让遮断污染执行面），占位符字面量就会被真写进工作区文件——一次静默
的数据破坏。

护栏只挡写盘类入参（write_file.content、edit_file.old_string/new_string）里出现的平台保留
占位符标记 `<redacted:`，命中即 ToolError 让模型先 read_file 重取真实内容；读面、路径等不拦。
"""
import pytest
from app.db import make_session_factory
from app.agent.tools import ToolContext, ToolError, execute_tool
from tests.test_agent_loop import Recorder, mk_session

REDACTED = "<redacted:password>"


def _ctx(sf, sid, tmp_path):
    return ToolContext(root=tmp_path, session_id=sid, session_factory=sf, send=Recorder())


@pytest.mark.asyncio
async def test_write_file_rejects_redaction_placeholder(test_engine, tmp_path):
    """write_file 正文含占位符：拒绝，且不得在盘上留下半截文件。"""
    sid = await mk_session(test_engine, tmp_path)
    sf = make_session_factory(test_engine)
    with pytest.raises(ToolError):
        await execute_tool(_ctx(sf, sid, tmp_path), "write_file",
                           {"path": "db.conf", "content": f"password={REDACTED}\n"})
    assert not (tmp_path / "db.conf").exists()


@pytest.mark.asyncio
async def test_edit_file_rejects_placeholder_in_old_string(test_engine, tmp_path):
    """edit_file 的 old_string 含占位符：拒绝（否则会拿占位符去匹配真实内容）。"""
    sid = await mk_session(test_engine, tmp_path)
    sf = make_session_factory(test_engine)
    ctx = _ctx(sf, sid, tmp_path)
    await execute_tool(ctx, "write_file", {"path": "a.py", "content": "password=hunter2\n"})
    with pytest.raises(ToolError):
        await execute_tool(ctx, "edit_file",
                           {"path": "a.py", "old_string": REDACTED, "new_string": "x"})
    # 原文件未被破坏
    assert "hunter2" in (tmp_path / "a.py").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_edit_file_rejects_placeholder_in_new_string(test_engine, tmp_path):
    """edit_file 的 new_string 含占位符：拒绝（否则占位符字面量被写进文件）。"""
    sid = await mk_session(test_engine, tmp_path)
    sf = make_session_factory(test_engine)
    ctx = _ctx(sf, sid, tmp_path)
    await execute_tool(ctx, "write_file", {"path": "a.py", "content": "password=hunter2\n"})
    with pytest.raises(ToolError):
        await execute_tool(ctx, "edit_file",
                           {"path": "a.py", "old_string": "hunter2", "new_string": REDACTED})
    # 拒绝后文件保持原明文，未被占位符替换
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == "password=hunter2\n"


@pytest.mark.asyncio
async def test_write_file_allows_normal_content_with_angle_brackets(test_engine, tmp_path):
    """边界：普通含尖括号/HTML 的正文不得被误伤，只有平台保留标记才拦。"""
    sid = await mk_session(test_engine, tmp_path)
    sf = make_session_factory(test_engine)
    body = "<div class='redacted-note'>ok</div>\n"
    await execute_tool(_ctx(sf, sid, tmp_path), "write_file", {"path": "i.html", "content": body})
    assert (tmp_path / "i.html").read_text(encoding="utf-8") == body


@pytest.mark.asyncio
async def test_write_file_allows_plain_content(test_engine, tmp_path):
    """happy path 回归：不含占位符的正常写入照常成功。"""
    sid = await mk_session(test_engine, tmp_path)
    sf = make_session_factory(test_engine)
    r = await execute_tool(_ctx(sf, sid, tmp_path), "write_file",
                           {"path": "ok.txt", "content": "hello world\n"})
    assert r["ok"] is True
    assert (tmp_path / "ok.txt").read_text(encoding="utf-8") == "hello world\n"


@pytest.mark.asyncio
async def test_write_file_allows_narrative_redacted_colon(test_engine, tmp_path):
    """边界：叙述性 `<redacted: 待定>` / 未闭合裸标记不得被误伤，只遮完整占位符。"""
    sid = await mk_session(test_engine, tmp_path)
    sf = make_session_factory(test_engine)
    body = "示例：<redacted: 待定> 与 bare <redacted: 未闭合\n"
    await execute_tool(_ctx(sf, sid, tmp_path), "write_file", {"path": "n.md", "content": body})
    assert (tmp_path / "n.md").read_text(encoding="utf-8") == body


def test_contains_redaction_marker_full_vs_narrative():
    """护栏判定的纯函数边界：只认带闭合的完整标记，叙述/明文/非串都不算。"""
    from app.agent.redact import contains_redaction_marker
    assert contains_redaction_marker("x <redacted:password> y")
    assert contains_redaction_marker("<redacted:api_key>")
    # 叙述性 / 未闭合 / 无标记 / 明文口令 / 非字符串都不得命中
    assert not contains_redaction_marker("<redacted: 待定>")
    assert not contains_redaction_marker("bare <redacted: no close")
    assert not contains_redaction_marker("<div class='redacted-note'>")
    assert not contains_redaction_marker("password=hunter2")
    assert not contains_redaction_marker(None)
