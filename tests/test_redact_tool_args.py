"""Ticket 010：tool_args 凭据遮断的单元测试 + loop 三出口集成测试。"""

import asyncio
import json
import pytest
from sqlalchemy import text as sql
from app.db import make_session_factory
from app.agent.llm import ScriptedLLM
from app.agent.loop import AgentRunner
from app.agent.redact import redact_tool_calls_args, _redact_deep
from tests.test_agent_loop import Recorder, cfg_provider, done, mk_session, tool_call


# ---- 辅助 ----

def _tc(args_dict, tc_id="call_abc", name="fs_write"):
    """构造一个标准 OpenAI tool_call 结构。"""
    return {
        "id": tc_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args_dict, ensure_ascii=False)},
    }


# ---- 主流程 ----

class TestRedactToolCallsArgs:
    def test_password_in_args_gets_redacted(self):
        tc = _tc({"path": "a.py", "content": "password=hunter2"})
        result, hits = redact_tool_calls_args([tc])
        assert hits >= 1
        args = json.loads(result[0]["function"]["arguments"])
        assert "hunter2" not in args["content"]
        assert "<redacted:password>" in args["content"]

    def test_protocol_structure_preserved(self):
        tc = _tc({"key": "sk-1234567890abcdef"}, tc_id="call_xyz", name="memory_write")
        result, _ = redact_tool_calls_args([tc])
        out = result[0]
        assert out["id"] == "call_xyz"
        assert out["type"] == "function"
        assert out["function"]["name"] == "memory_write"
        # arguments 仍是合法 JSON
        json.loads(out["function"]["arguments"])

    def test_multiple_tool_calls_all_processed(self):
        tcs = [_tc({"a": "token=abc123"}), _tc({"b": "password=xyz"})]
        result, hits = redact_tool_calls_args(tcs)
        assert hits >= 2
        assert len(result) == 2

    def test_no_sensitive_content_leaves_args_unchanged(self):
        original = {"path": "src/main.py", "content": "print('hello')"}
        tc = _tc(original)
        result, hits = redact_tool_calls_args([tc])
        assert hits == 0
        assert result[0] is tc  # 同一对象，未拷贝（不命中则原文返回）


# ---- 边界 ----

class TestEdgeCases:
    def test_empty_dict_args(self):
        tc = _tc({})
        result, hits = redact_tool_calls_args([tc])
        assert hits == 0
        assert json.loads(result[0]["function"]["arguments"]) == {}

    def test_none_arguments_field(self):
        tc = {"id": "x", "type": "function", "function": {"name": "f", "arguments": None}}
        result, hits = redact_tool_calls_args([tc])
        assert hits == 0
        assert result[0] is tc

    def test_empty_string_arguments(self):
        tc = {"id": "x", "type": "function", "function": {"name": "f", "arguments": ""}}
        result, hits = redact_tool_calls_args([tc])
        assert hits == 0

    def test_malformed_json_not_modified(self):
        tc = {"id": "x", "type": "function", "function": {"name": "f", "arguments": "not json {"}}
        result, hits = redact_tool_calls_args([tc])
        assert hits == 0
        assert result[0]["function"]["arguments"] == "not json {"

    def test_deeply_nested_dict(self):
        tc = _tc({"outer": {"inner": {"secret": "api_key=sk-abcdefghijk"}}})
        result, hits = redact_tool_calls_args([tc])
        assert hits >= 1
        args = json.loads(result[0]["function"]["arguments"])
        assert "sk-abcdefghijk" not in args["outer"]["inner"]["secret"]

    def test_credentials_in_array_elements(self):
        tc = _tc({"items": ["normal text", "password=leak123", "another"]})
        result, hits = redact_tool_calls_args([tc])
        assert hits >= 1
        args = json.loads(result[0]["function"]["arguments"])
        assert "leak123" not in args["items"][1]

    def test_non_string_values_preserved(self):
        tc = _tc({"count": 42, "flag": True, "data": None, "ratio": 3.14})
        result, hits = redact_tool_calls_args([tc])
        assert hits == 0
        args = json.loads(result[0]["function"]["arguments"])
        assert args["count"] == 42
        assert args["flag"] is True
        assert args["data"] is None
        assert args["ratio"] == 3.14

    def test_chinese_and_fullwidth_in_value(self):
        # 中文上下文里的凭据仍被遮断
        tc = _tc({"msg": "数据库密码是 password=hunter2 请查收"})
        result, hits = redact_tool_calls_args([tc])
        assert hits >= 1
        args = json.loads(result[0]["function"]["arguments"])
        assert "hunter2" not in args["msg"]
        assert "数据库密码是" in args["msg"]  # 非敏感文字保留

    def test_very_long_args_no_performance_collapse(self):
        # 1000 条元素，每条含一个 password=，验证不超时
        big = {"items": [{"text": f"line{i} password=x{i}"} for i in range(1000)]}
        tc = _tc(big)
        result, hits = redact_tool_calls_args([tc])
        assert hits >= 1000

    def test_tool_calls_list_empty(self):
        result, hits = redact_tool_calls_args([])
        assert result == []
        assert hits == 0


# ---- 幂等 ----

class TestIdempotent:
    def test_already_redacted_stable(self):
        """已遮断的 tool_args 再过一遍，文本不变、计数归零。"""
        tc = _tc({"content": "password=hunter2"})
        r1, h1 = redact_tool_calls_args([tc])
        assert h1 >= 1
        r2, h2 = redact_tool_calls_args(r1)
        assert h2 == 0
        assert r2[0]["function"]["arguments"] == r1[0]["function"]["arguments"]


# ---- 集成：loop 三出口（落库 / 回注 / 事件推送）+ 工具执行用原始 args ----

def _runner(sf, llm, rec):
    return AgentRunner(sf, llm, cfg_provider(), rec,
                       lambda *a, **k: asyncio.sleep(0, None), lambda *a, **k: True)


@pytest.mark.asyncio
async def test_redaction_lands_on_all_three_outputs(test_engine, tmp_path):
    """一次带口令的 tool_call：文件落真口令，DB/事件/同轮回注三处只剩占位符。"""
    sid = await mk_session(test_engine, tmp_path)
    sf = make_session_factory(test_engine)
    rec = Recorder()
    llm = ScriptedLLM([
        done(None, [tool_call("c1", "write_file", path="db.conf", content="password=hunter2")]),
        done("好了"),
    ])
    status = await _runner(sf, llm, rec).run(sid, "写个配置", asyncio.Event())
    assert status == "idle"

    # 出口 0（对照）：工具真实执行用的是原始 args → 工作区文件里是真口令
    assert (tmp_path / "db.conf").read_text(encoding="utf-8") == "password=hunter2"

    # 出口 1（落库）：message.tool_args 明文不含 hunter2、含占位符
    async with sf() as s:
        raw = (await s.execute(sql(
            "SELECT CAST(tool_args AS text) AS t FROM message "
            "WHERE session_id=:i AND role='assistant' AND tool_args IS NOT NULL ORDER BY seq"),
            {"i": sid})).scalars().all()
    stored = "".join(raw)
    assert "hunter2" not in stored
    assert "<redacted:password>" in stored

    # 出口 3（事件流）：推送给前端的 tool_call 事件 args 不含 hunter2
    calls = [e for e in rec.events if e["type"] == "tool_call"]
    assert calls
    assert "hunter2" not in json.dumps(calls, ensure_ascii=False)

    # 出口 2（回注）：同轮下一趟发给模型的 messages 不含 hunter2
    assert "hunter2" not in json.dumps(llm.received[1], ensure_ascii=False)


@pytest.mark.asyncio
async def test_redaction_persists_across_runs(test_engine, tmp_path):
    """跨轮：历史从 DB 读回再拼进 prompt，仍不带明文（回注出口读的是已遮断的库）。"""
    sid = await mk_session(test_engine, tmp_path)
    sf = make_session_factory(test_engine)
    llm1 = ScriptedLLM([
        done(None, [tool_call("c1", "write_file", path="x.txt", content="api_key=sk-abcdef123456")]),
        done("好了"),
    ])
    await _runner(sf, llm1, Recorder()).run(sid, "写", asyncio.Event())

    llm2 = ScriptedLLM([done("收到")])
    await _runner(sf, llm2, Recorder()).run(sid, "继续", asyncio.Event())
    prompt = json.dumps(llm2.received[0], ensure_ascii=False)
    assert "sk-abcdef123456" not in prompt
    assert "<redacted:api_key>" in prompt


@pytest.mark.asyncio
async def test_redaction_preserves_tool_pairing(test_engine, tmp_path):
    """遮断不能拆散 assistant(tool_calls)/tool 配对：id 必须原样，否则 API 400。"""
    sid = await mk_session(test_engine, tmp_path)
    sf = make_session_factory(test_engine)
    rec = Recorder()
    llm = ScriptedLLM([
        done(None, [tool_call("call_keep_id", "write_file", path="p.txt", content="token=abc12345")]),
        done("好了"),
    ])
    status = await _runner(sf, llm, rec).run(sid, "写", asyncio.Event())
    assert status == "idle"
    async with sf() as s:
        raw = (await s.execute(sql(
            "SELECT CAST(tool_args AS text) AS t FROM message "
            "WHERE session_id=:i AND role='assistant' AND tool_args IS NOT NULL"),
            {"i": sid})).scalars().all()
    assert "call_keep_id" in "".join(raw)  # 协议 id 未被遮断破坏
