"""Ticket 010 第五出口：tool_result 的凭据遮断。

`read_file` 等工具把文件正文放进返回值 `{"content": ...}`，而 `tool_result` 会
落平台库 message.tool_result、推 WebSocket `tool_result` 事件、并回注下一轮 prompt
——这是 tool_args 三出口、file_change 第四出口之外，把凭据带回平台侧的第五个明文出口。

口径与前四出口不同：tool_result 是**环境回给模型**的数据，`read_file` 的真实内容必须
进当轮 messages，否则模型看不到文件、`edit_file` 构造不出 old_string。所以：
- 落库 / WS 推送 → 净化版（平台侧永不留明文）；
- 当轮回注模型 → 原文（保住 Agent 读文件的功能）。
跨轮从库里重建历史，读到的已是净化版，故后续轮次回注自然无明文（要再看可 read_file 重读）。
"""
import asyncio
import json
import pytest
from sqlalchemy import text as sql
from app.db import make_session_factory
from app.agent.llm import ScriptedLLM
from app.agent.loop import AgentRunner
from app.agent.redact import redact_tool_result
from tests.test_agent_loop import Recorder, cfg_provider, done, mk_session, tool_call


# ---------------- 单元：redact_tool_result 纯函数 ----------------

def test_strips_password_from_content_value():
    r = redact_tool_result({"path": "db.conf", "content": "password=hunter2\nhost=localhost\n", "size": 30})
    assert "hunter2" not in r["content"]
    assert "<redacted:password>" in r["content"]
    assert "host=localhost" in r["content"]      # 非敏感上下文保留
    assert r["path"] == "db.conf"                 # 结构/键原样
    assert r["size"] == 30


def test_does_not_mutate_original_result():
    """净化版给库/事件，原文给模型——函数绝不就地改，否则模型也看不到明文。"""
    original = {"content": "api_key=sk-abcdefgh12345"}
    sanitized = redact_tool_result(original)
    assert "sk-abcdefgh12345" in original["content"]   # 入参未被污染
    assert "sk-abcdefgh12345" not in sanitized["content"]


def test_preserves_non_string_types():
    r = redact_tool_result({"ok": True, "bytes": 0, "count": 3, "none": None, "path": "a.txt"})
    assert r == {"ok": True, "bytes": 0, "count": 3, "none": None, "path": "a.txt"}


def test_no_secret_result_unchanged():
    r = redact_tool_result({"path": "clean.txt", "content": "hello world\nfoo=bar\n", "size": 16})
    assert r["content"] == "hello world\nfoo=bar\n"


def test_nested_list_of_dict_deep_redaction():
    r = redact_tool_result({"logs": [{"line": "token=abc123def"}, {"line": "plain"}]})
    assert "abc123def" not in r["logs"][0]["line"]
    assert "<redacted:token>" in r["logs"][0]["line"]
    assert r["logs"][1]["line"] == "plain"


def test_idempotent_double_redaction():
    once = redact_tool_result({"content": "password=hunter2"})
    twice = redact_tool_result(once)
    assert twice == once                          # 已遮文本再过一遍不套娃
    assert twice["content"].count("<redacted:password>") == 1


def test_empty_and_non_dict_passthrough():
    assert redact_tool_result({}) == {}
    assert redact_tool_result([]) == []


def test_top_level_string_result_redacted():
    """返回值直接是 str（工具可能回纯文本）也要遮——顶层 str 不得绕过走 _redact_deep。"""
    out = redact_tool_result("password=hunter2")
    assert "hunter2" not in out
    assert "<redacted:password>" in out


def test_top_level_string_without_secret_unchanged():
    assert redact_tool_result("hello world") == "hello world"


def test_top_level_non_container_passthrough():
    """空串 / None / int 等非字符串标量原样透传，不得被 str 分支误处理。"""
    assert redact_tool_result("") == ""
    assert redact_tool_result(None) is None
    assert redact_tool_result(42) == 42


# ---------------- 集成：loop 三处出口的口径分离 ----------------

def _runner(sf, llm, rec):
    return AgentRunner(sf, llm, cfg_provider(), rec,
                       lambda *a, **k: asyncio.sleep(0, None), lambda *a, **k: True)


async def _read_file_tool_result(sf, sid):
    async with sf() as s:
        return (await s.execute(sql(
            "SELECT CAST(tool_result AS text) AS tr FROM message "
            "WHERE session_id=:i AND tool_name='read_file'"), {"i": sid})).scalar()


@pytest.mark.asyncio
async def test_read_file_result_purged_from_db_and_event(test_engine, tmp_path):
    """write 一个含口令的文件 → read_file 回读：库与 WS 事件都不得带回明文。"""
    sid = await mk_session(test_engine, tmp_path)
    sf = make_session_factory(test_engine)
    rec = Recorder()
    llm = ScriptedLLM([
        done(None, [tool_call("c1", "write_file", path="db.conf", content="password=hunter2\nhost=localhost\n")]),
        done(None, [tool_call("c2", "read_file", path="db.conf")]),
        done("读完了"),
    ])
    await _runner(sf, llm, rec).run(sid, "开始", asyncio.Event())

    ev = [e for e in rec.events if e["type"] == "tool_result" and e["name"] == "read_file"]
    assert ev
    assert "hunter2" not in json.dumps(ev, ensure_ascii=False)
    assert "<redacted:password>" in ev[-1]["result"]["content"]

    stored = await _read_file_tool_result(sf, sid)
    assert "hunter2" not in stored
    assert "<redacted:password>" in stored


@pytest.mark.asyncio
async def test_read_file_result_reinjected_raw_to_model_same_run(test_engine, tmp_path):
    """同轮回注给模型的当轮 messages 必须是原文——否则模型读不到真实文件内容。"""
    sid = await mk_session(test_engine, tmp_path)
    sf = make_session_factory(test_engine)
    llm = ScriptedLLM([
        done(None, [tool_call("c1", "write_file", path="db.conf", content="password=hunter2\n")]),
        done(None, [tool_call("c2", "read_file", path="db.conf")]),
        done("读完了"),
    ])
    await _runner(sf, llm, Recorder()).run(sid, "开始", asyncio.Event())
    # 第三次 chat_stream 收到的是 read_file 执行后的 messages：tool 结果应含真口令
    last_turn = llm.received[-1]
    joined = json.dumps(last_turn, ensure_ascii=False)
    assert "hunter2" in joined                    # 原文回注，功能不被遮断破坏


@pytest.mark.asyncio
async def test_tool_result_purged_across_runs(test_engine, tmp_path):
    """跨轮：新 runner 从库里重建历史，读到的 tool_result 已净化，回注不再含明文。"""
    sid = await mk_session(test_engine, tmp_path)
    sf = make_session_factory(test_engine)
    llm1 = ScriptedLLM([
        done(None, [tool_call("c1", "write_file", path="db.conf", content="password=hunter2\n")]),
        done(None, [tool_call("c2", "read_file", path="db.conf")]),
        done("读完了"),
    ])
    await _runner(sf, llm1, Recorder()).run(sid, "开始", asyncio.Event())

    llm2 = ScriptedLLM([done("收到")])
    await _runner(sf, llm2, Recorder()).run(sid, "再来", asyncio.Event())
    joined = json.dumps(llm2.received[0], ensure_ascii=False)
    assert "hunter2" not in joined                # 历史从净化库重建
    assert "<redacted:password>" in joined
