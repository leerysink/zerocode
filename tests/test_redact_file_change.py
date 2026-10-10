"""Ticket 010 第四出口：file_change 的凭据遮断。

`_record_change` 把 `before_content` / `after_content` / `diff` 整体写进平台库
`file_change` 表、并把 `diff` 推给 WebSocket——这是 tool_args 三出口之外、
把用户写进工作区的口令复制进平台侧的第四个明文出口。

遮断只在 `_record_change` 这个唯一收口处生效：用户真正要写的明文留在工作区
文件里（那是用户自己的域、且在收口之前已写盘），平台侧历史/事件只剩可看懂的改动结构。
"""
import json
import pytest
from sqlalchemy import text as sql
from app.db import make_session_factory
from app.agent.tools import ToolContext, execute_tool
from tests.test_agent_loop import Recorder, mk_session


def _ctx(sf, sid, tmp_path):
    return ToolContext(root=tmp_path, session_id=sid, session_factory=sf, send=Recorder())


async def _file_change_row(sf, sid):
    async with sf() as s:
        # ORDER BY id：write 后 edit 的用例靠 rows[-1] 取 edit 行，插入序不能赌堆扫描
        return (await s.execute(sql(
            "SELECT CAST(before_content AS text) AS b, CAST(after_content AS text) AS a, "
            "CAST(diff AS text) AS d FROM file_change WHERE session_id=:i ORDER BY id"),
            {"i": sid})).mappings().all()


# ---------------- 落库出口 ----------------

@pytest.mark.asyncio
async def test_write_file_change_table_strips_plaintext(test_engine, tmp_path):
    sid = await mk_session(test_engine, tmp_path)
    sf = make_session_factory(test_engine)
    await execute_tool(_ctx(sf, sid, tmp_path), "write_file",
                       {"path": "db.conf", "content": "password=hunter2\nhost=localhost\n"})
    # 工作区文件保住真口令：遮断不得污染用户真正要写的内容
    assert "hunter2" in (tmp_path / "db.conf").read_text(encoding="utf-8")
    rows = await _file_change_row(sf, sid)
    assert len(rows) == 1
    blob = (rows[0]["b"] or "") + (rows[0]["a"] or "") + (rows[0]["d"] or "")
    assert "hunter2" not in blob
    assert "<redacted:password>" in rows[0]["a"]      # after_content 有占位符
    assert "host=localhost" in rows[0]["a"]           # 非敏感上下文保留（前端可读）
    assert "<redacted:password>" in rows[0]["d"]      # diff 同样只剩占位符


@pytest.mark.asyncio
async def test_edit_file_change_table_strips_plaintext(test_engine, tmp_path):
    sid = await mk_session(test_engine, tmp_path)
    sf = make_session_factory(test_engine)
    ctx = _ctx(sf, sid, tmp_path)
    await execute_tool(ctx, "write_file", {"path": "a.py", "content": "token=oldsecret11\n"})
    await execute_tool(ctx, "edit_file", {"path": "a.py", "old_string": "oldsecret11",
                                          "new_string": "newsecret22"})
    rows = await _file_change_row(sf, sid)
    last = rows[-1]  # edit 那次：before 含 old、after 含 new，两侧都不能漏
    assert "oldsecret11" not in (last["b"] + last["a"] + last["d"])
    assert "newsecret22" not in (last["b"] + last["a"] + last["d"])
    assert "token=" in last["a"]                       # 键名保留，只遮值


# ---------------- 事件流出口 ----------------

@pytest.mark.asyncio
async def test_file_change_event_strips_plaintext(test_engine, tmp_path):
    sid = await mk_session(test_engine, tmp_path)
    sf = make_session_factory(test_engine)
    rec = Recorder()
    ctx = ToolContext(root=tmp_path, session_id=sid, session_factory=sf, send=rec)
    await execute_tool(ctx, "write_file", {"path": "x.env", "content": "api_key=sk-abcdefgh12345\n"})
    fc = [e for e in rec.events if e["type"] == "file_change"]
    assert fc
    assert "sk-abcdefgh12345" not in json.dumps(fc, ensure_ascii=False)
    assert "<redacted:api_key>" in fc[-1]["diff"]


# ---------------- 边界 ----------------

@pytest.mark.asyncio
async def test_non_secret_diff_unchanged(test_engine, tmp_path):
    """不含任何凭据形态时，before/after/diff 必须一字不改（不能误伤正常改动）。"""
    sid = await mk_session(test_engine, tmp_path)
    sf = make_session_factory(test_engine)
    await execute_tool(_ctx(sf, sid, tmp_path), "write_file",
                       {"path": "clean.txt", "content": "hello world\nfoo=bar\n"})
    rows = await _file_change_row(sf, sid)
    assert rows[0]["a"] == "hello world\nfoo=bar\n"
    assert "<redacted" not in rows[0]["d"]


@pytest.mark.asyncio
async def test_pure_credential_value_change_leaks_nothing(test_engine, tmp_path):
    """起止都是凭据、值不同的编辑：两个明文都不得出现在库（宁可看不出改了哪个字）。"""
    sid = await mk_session(test_engine, tmp_path)
    sf = make_session_factory(test_engine)
    ctx = _ctx(sf, sid, tmp_path)
    await execute_tool(ctx, "write_file", {"path": "p.txt", "content": "password=aaa111\n"})
    await execute_tool(ctx, "edit_file", {"path": "p.txt", "old_string": "aaa111",
                                          "new_string": "bbb222"})
    rows = await _file_change_row(sf, sid)
    last = rows[-1]
    assert "aaa111" not in (last["b"] + last["a"] + last["d"])
    assert "bbb222" not in (last["b"] + last["a"] + last["d"])


@pytest.mark.asyncio
async def test_create_file_change_empty_before(test_engine, tmp_path):
    """新建文件：before 为空串，遮断空串应原样（空/非字符串交给 _check_*）。"""
    sid = await mk_session(test_engine, tmp_path)
    sf = make_session_factory(test_engine)
    await execute_tool(_ctx(sf, sid, tmp_path), "write_file",
                       {"path": "brand.txt", "content": "pwd=leak999\n"})
    rows = await _file_change_row(sf, sid)
    assert rows[0]["b"] == ""                     # 新建前空内容，保持空
    assert "leak999" not in rows[0]["a"]
    assert "leak999" not in rows[0]["d"]


@pytest.mark.asyncio
async def test_chinese_fullwidth_credential_in_diff(test_engine, tmp_path):
    """中文正文里的凭据：值被遮、周边中文说明保留（右边界不能吃掉后半句）。"""
    sid = await mk_session(test_engine, tmp_path)
    sf = make_session_factory(test_engine)
    await execute_tool(_ctx(sf, sid, tmp_path), "write_file",
                       {"path": "说明.txt", "content": "数据库密码 password=hunter2，改成读环境变量\n"})
    rows = await _file_change_row(sf, sid)
    assert "hunter2" not in rows[0]["a"]
    assert "改成读环境变量" in rows[0]["a"]        # 全角逗号后的说明保留
