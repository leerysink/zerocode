"""工具结果统一预算：截断后可序列化、结构字段不丢、明确告知模型被截断。"""
import dataclasses
import json

import pytest

from app.agent import registry
from app.agent.result_budget import ResultPolicy, apply_budget
from app.agent.tools import ToolContext, execute_tool


def _size(value) -> int:
    return len(json.dumps(value, ensure_ascii=False, default=str))


def test_short_result_is_returned_untouched():
    value = {"ok": True, "rows": [1, 2, 3]}
    out, info = apply_budget(value, ResultPolicy(max_chars=1000, max_items=10, max_str_chars=100))
    assert out == value
    assert info["truncated"] is False


def test_long_list_is_cut_and_marked_without_mutating_input():
    value = {"ok": True, "rows": list(range(50))}
    out, info = apply_budget(value, ResultPolicy(max_chars=10000, max_items=5, max_str_chars=100))
    assert out["rows"] == [0, 1, 2, 3, 4]
    assert out["ok"] is True
    assert out["_budget"]["truncated"] is True
    assert out["_budget"]["omitted_items"] == 45
    assert info["truncated"] is True
    assert value["rows"] == list(range(50))


def test_long_string_is_cut_and_marked():
    value = {"ok": True, "text": "x" * 5000}
    out, info = apply_budget(value, ResultPolicy(max_chars=10000, max_items=10, max_str_chars=100))
    assert len(out["text"]) < 5000
    assert out["text"].endswith("[结果已按预算截断]")
    assert out["_budget"]["truncated"] is True
    assert info["truncated"] is True


def test_serialized_size_is_hard_capped():
    value = {"ok": True, "text": "x" * 100000, "rows": list(range(1000))}
    out, info = apply_budget(value, ResultPolicy(max_chars=1000, max_items=1000, max_str_chars=50000))
    assert _size(out) <= 1000
    assert out["_budget"]["truncated"] is True
    assert info["truncated"] is True


def test_nested_lists_and_strings_are_bounded():
    value = {"ok": True, "tables": [{"name": "t", "columns": ["c" * 500] * 20}] * 20}
    out, info = apply_budget(value, ResultPolicy(max_chars=2000, max_items=5, max_str_chars=50))
    assert len(out["tables"]) == 5
    assert len(out["tables"][0]["columns"]) == 5
    assert out["tables"][0]["columns"][0].endswith("[结果已按预算截断]")
    assert _size(out) <= 2000
    assert info["truncated"] is True


def test_failure_result_keeps_ok_false():
    value = {"ok": False, "error": "boom", "rows": list(range(100))}
    out, _ = apply_budget(value, ResultPolicy(max_chars=500, max_items=3, max_str_chars=100))
    assert out["ok"] is False
    assert out["error"] == "boom"
    assert out["_budget"]["truncated"] is True


def test_top_level_string_is_truncated_without_becoming_a_dict():
    out, info = apply_budget("y" * 5000, ResultPolicy(max_chars=500, max_items=10, max_str_chars=5000))
    assert isinstance(out, str)
    assert len(out) <= 500
    assert info["truncated"] is True


@pytest.mark.asyncio
async def test_execute_tool_applies_the_registry_policy(tmp_path, monkeypatch):
    """接线守卫：预算必须在 execute_tool 这个唯一出口生效，而不是只存在于单元函数里。"""
    for name in ("a.txt", "b.txt", "c.txt", "d.txt"):
        (tmp_path / name).write_text("x", encoding="utf-8")
    spec = registry.spec_of("list_dir")
    monkeypatch.setitem(registry.SPEC_BY_NAME, "list_dir", dataclasses.replace(
        spec, result_policy=ResultPolicy(max_chars=10000, max_items=2, max_str_chars=100)))

    ctx = ToolContext(root=tmp_path, session_id=1, session_factory=None, send=lambda _e: None)
    result = await execute_tool(ctx, "list_dir", {"path": "."})

    assert len(result["entries"]) == 2
    assert result["_budget"]["truncated"] is True
