"""工具调用入参的凭据遮断（Ticket 010）。

单一职责：在 tool_calls 入库 / 推送 / 回注之前，对 function.arguments 里的
字符串值递归跑 redact_sensitive，把凭据换成占位符。

设计约束：
- 复用 memory_service.redact_sensitive 规则表，不再抄第二份正则（票面 L29）。
- 只改 arguments 字符串中的值，不动 tool_calls[].id / type / function.name
  （协议结构破坏 = OpenAI 兼容 API 返回 400）。
- 幂等：已遮断的文本再过一遍，<redacted:kind> 不命中任何规则（_NOT_REDACTED 负向前瞻）。
- 全量工具生效：所有工具的 args 统一遮，不设白名单。
"""

from __future__ import annotations

import json

from app.services.memory_service import redact_sensitive


def _redact_value(v):
    """遮断单个值：str 跑规则、dict/list 递归、其余（int/float/bool/None）原样。返回 (值, 处数)。"""
    if isinstance(v, str):
        return redact_sensitive(v)
    if isinstance(v, (dict, list)):
        return _redact_deep(v)
    return v, 0


def _redact_deep(obj):
    """递归走 dict / list，对每一个 str 值跑 redact_sensitive。返回 (遮断后对象, 总处数)。"""
    total = 0
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            out[k], hits = _redact_value(v)
            total += hits
        return out, total
    if isinstance(obj, list):
        out = []
        for item in obj:
            rv, hits = _redact_value(item)
            out.append(rv)
            total += hits
        return out, total
    # 非容器（int/float/bool/None）直接返回
    return obj, 0


def redact_tool_calls_args(tool_calls: list[dict]) -> tuple[list[dict], int]:
    """对一组 tool_calls 的 function.arguments 做凭据遮断。

    返回 (遮断后的 tool_calls 新列表, 总脱敏处数)。
    原始列表不被修改（防御性拷贝）。
    协议结构（id / type / function.name）保持原样；
    arguments 仍是合法 JSON 字符串（若原始值不是合法 JSON 则跳过该条不破坏）。
    """
    result = []
    total = 0
    for tc in tool_calls:
        fn = tc.get("function", {})
        raw_args = fn.get("arguments")
        if not raw_args or not isinstance(raw_args, str):
            result.append(tc)
            continue
        try:
            parsed = json.loads(raw_args)
        except (json.JSONDecodeError, TypeError):
            # 畸形 JSON 走既有 ToolError 路径，此处不遮不改
            result.append(tc)
            continue
        redacted, hits = _redact_deep(parsed)
        total += hits
        if hits:
            new_tc = {**tc, "function": {**fn, "arguments": json.dumps(redacted, ensure_ascii=False)}}
            result.append(new_tc)
        else:
            # 不含任何敏感形态时原文一字不改
            result.append(tc)
    return result, total
