"""工具调用入参的凭据遮断（Ticket 010）。

单一职责：在 tool_calls 入库 / 推送 / 回注之前，对 function.arguments 里的
字符串值递归跑 redact_sensitive，把凭据换成占位符。

设计约束：
- 复用 memory_service.redact_sensitive 规则表，不再抄第二份正则（票面 L29）。
- 只改 arguments 字符串中的值，不动 tool_calls[].id / type / function.name
  （协议结构破坏 = OpenAI 兼容 API 返回 400）。
- 幂等：已遮断的文本再过一遍，<redacted:kind> 不命中任何规则（_NOT_REDACTED 负向前瞻）。
- 全量工具生效：所有工具的 args 统一遮，不设白名单。
- 规则表只按**形态**匹配，抓不住用户自己应用里那些不成形态的口令（admin123 一类）；
  工具侧要补一刀时借用 `rewrite_tool_calls_args`（同一份结构保持机械动作 + 可选工具白名单），
  不要再抄一份 JSON 解析（裁定 R-33）。
"""

from __future__ import annotations

import json
import re

from app.services.memory_service import redact_sensitive, REDACTED_MARKER

# 完整占位符形态：前缀 + kind 词 + 闭合 `>`，与 REDACTED_TEMPLATE 产物严格同形。
# 故意要求闭合：叙述性文本里的裸 `<redacted:`、`<redacted: 待定>`（含空格/中文、无闭合）
# 不算占位符，不应被写盘护栏误伤。
_REDACTED_FULL = re.compile(rf"{re.escape(REDACTED_MARKER)}[A-Za-z0-9_]+>")


def contains_redaction_marker(value) -> bool:
    """是否含平台遮断占位符标记 `<redacted:kind>`（Ticket 010 第五出口衍生护栏）。

    跨轮历史从净化库重建，模型看到的前一次 tool_result 已是 `<redacted:kind>` 占位符；
    若它把占位符当作文件真实内容递回写盘工具，而工具执行收的是原始 args，就会把
    占位符字面量真写进工作区文件。护栏只认**完整标记**（带闭合 `>`），不误伤普通
    含尖括号正文或叙述性文本。非字符串一律返回 False。
    """
    return isinstance(value, str) and _REDACTED_FULL.search(value) is not None


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


def rewrite_tool_calls_args(tool_calls: list[dict], rewrite,
                            tool_names: tuple[str, ...] | None = None
                            ) -> tuple[list[dict], int]:
    """改写 tool_calls 的 function.arguments 的**唯一**机械动作，返回 (新列表, 总改写处数)。

    `rewrite(parsed_args) -> (新 args, 处数)`；`tool_names` 给了就只改那几个工具，不给就全改。
    不管改写的是凭据还是别的什么形状，协议结构（id / type / function.name）一律不动、列表长度
    一律不变、arguments 一律保持合法 JSON——这些是 OpenAI 兼容 API 会不会 400 的分界线，
    每个新需求各写一遍迟早漏一条，所以只留这一份（裁定 R-33 的参数侧遮断是第二个使用者）。

    原始列表不被修改（防御性拷贝）；处数为 0 时保留**原条目对象**，既省一次重建，
    也让「已过一遍的文本再过一遍」幂等（见模块头的 `_NOT_REDACTED` 约定）。
    """
    result = []
    total = 0
    for tc in tool_calls:
        fn = tc.get("function", {})
        if tool_names is not None and fn.get("name") not in tool_names:
            result.append(tc)
            continue
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
        rewritten, hits = rewrite(parsed)
        total += hits
        if hits:
            new_tc = {**tc, "function": {**fn, "arguments": json.dumps(rewritten, ensure_ascii=False)}}
            result.append(new_tc)
        else:
            # 不含任何敏感形态时原文一字不改
            result.append(tc)
    return result, total


def redact_tool_calls_args(tool_calls: list[dict]) -> tuple[list[dict], int]:
    """对一组 tool_calls 的 function.arguments 做凭据遮断。

    返回 (遮断后的 tool_calls 新列表, 总脱敏处数)。
    按形态匹配的通用规则表只抓得住「长得像凭据」的串；用户自己应用里那些不成形态的
    口令（`admin123`、`hunter2`）要靠工具侧的补刀，见 `browser_session_tools`。
    """
    return rewrite_tool_calls_args(tool_calls, _redact_deep)


def redact_tool_result(result):
    """遮断工具返回值里的凭据（Ticket 010 第五出口：落库 + WS 推送走净化版）。

    复用同一张规则表递归走 dict/list，把每个 str 值里的凭据换成 <redacted:kind>；
    返回**新对象**、绝不就地改——loop 里净化版给库/事件，原文仍回注当轮模型，
    两条口径共用同一个 result 入参，改坏了模型就读不到真实文件内容。
    非容器 / 空值原样透传（_redact_deep 对 int/bool/None 直接返回）。
    顶层直接是 str 时也要遮（_redact_deep 会把非容器原样返回，不跑规则），否则未来
    返回纯文本的工具会绕过遮断。
    """
    if isinstance(result, str):
        return redact_sensitive(result)[0]
    sanitized, _ = _redact_deep(result)
    return sanitized
