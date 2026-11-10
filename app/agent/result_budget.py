"""工具结果的统一预算（2026-11-10）。

工具模块各自有文本/行数上限，但新工具可以轻易返回一个几十万字符的 JSON 结构，一次就吃掉
半个上下文窗口。这里提供**唯一出口**的结构化截断：不切半截 JSON、不就地改原对象、
截断后在结果里留一条机器可读的 `_budget` 说明，让模型知道「这不是全部，请缩小范围」。

预算按工具在 registry 里声明；默认值只防意外，真正需要大结果的工具（read_file）显式放宽。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, replace

BUDGET_KEY = "_budget"
TRUNC_MARK = "[结果已按预算截断]"


@dataclass(frozen=True)
class ResultPolicy:
    """单个工具的结果预算：序列化总字符、列表项数、单个字符串长度。"""

    max_chars: int = 24_000
    max_items: int = 200
    max_str_chars: int = 8_000


DEFAULT_POLICY = ResultPolicy()


@dataclass
class _Stats:
    omitted_items: int = 0
    truncated_strings: int = 0

    @property
    def truncated(self) -> bool:
        return bool(self.omitted_items or self.truncated_strings)


def _dump(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _size(value) -> int:
    return len(_dump(value))


def _cut_text(text: str, limit: int) -> str:
    limit = max(0, limit)
    if len(text) <= limit:
        return text
    body = max(0, limit - len(TRUNC_MARK))
    return text[:body] + TRUNC_MARK


def _shrink(value, policy: ResultPolicy, stats: _Stats):
    """按策略递归收缩结构；返回新对象，绝不修改入参。"""
    if isinstance(value, str):
        if len(value) > policy.max_str_chars:
            stats.truncated_strings += 1
            return _cut_text(value, policy.max_str_chars)
        return value
    if isinstance(value, list):
        items = [_shrink(v, policy, stats) for v in value[:policy.max_items]]
        if len(value) > policy.max_items:
            stats.omitted_items += len(value) - policy.max_items
        return items
    if isinstance(value, dict):
        return {k: _shrink(v, policy, stats) for k, v in value.items()}
    return value


def _meta(stats: _Stats, limit: int) -> dict:
    meta = {
        "truncated": True,
        "limit_chars": limit,
        "note": "结果超过平台预算，已截断；请缩小查询范围或分页读取，不要用截断内容改写文件",
    }
    if stats.omitted_items:
        meta["omitted_items"] = stats.omitted_items
    if stats.truncated_strings:
        meta["truncated_strings"] = stats.truncated_strings
    return meta


def _tighter(policy: ResultPolicy) -> ResultPolicy:
    return replace(policy,
                   max_items=max(1, policy.max_items // 2),
                   max_str_chars=max(40, policy.max_str_chars // 2))


def _fallback(value, policy: ResultPolicy, stats: _Stats, before_chars: int):
    """极端结构（键极多）仍超预算时的折叠形态：保留 ok 与一段原文预览。"""
    ok = value.get("ok", True) if isinstance(value, dict) else True
    meta = _meta(stats or _Stats(omitted_items=1), policy.max_chars)
    base = {"ok": ok, BUDGET_KEY: meta}
    raw = _dump(value)
    remaining = max(0, policy.max_chars - _size(base) - 2)
    preview = raw[:remaining]
    out = {**base, "preview": preview}
    while _size(out) > policy.max_chars and remaining > 0:
        remaining = max(0, remaining - (_size(out) - policy.max_chars) - 8)
        out = {**base, "preview": raw[:remaining]}
    return out


def apply_budget(value, policy: ResultPolicy | None = None):
    """应用预算，返回 `(结果, info)`。

    未触发截断时原样返回**同一个对象**（调用点可继续把它当原文用）；触发时返回新对象。
    `info` 供度量记录：before/after 字符数、是否截断、省略项数。
    """
    policy = policy or DEFAULT_POLICY
    before_chars = _size(value)
    if before_chars <= policy.max_chars and not _would_shrink(value, policy):
        return value, {"truncated": False, "before_chars": before_chars,
                       "after_chars": before_chars, "omitted_items": 0,
                       "truncated_strings": 0}

    current = policy
    stats = _Stats()
    candidate = value
    for _ in range(8):
        stats = _Stats()
        shrunk = _shrink(value, current, stats)
        if isinstance(shrunk, dict):
            candidate = {**shrunk, BUDGET_KEY: _meta(stats, policy.max_chars)}
        elif isinstance(shrunk, str):
            # 顶层字符串没有地方挂说明，截断标记已经写在文本尾部
            candidate = shrunk
        else:
            candidate = {"ok": True, "items": shrunk, BUDGET_KEY: _meta(stats, policy.max_chars)}
        if _size(candidate) <= policy.max_chars:
            return candidate, {"truncated": True, "before_chars": before_chars,
                               "after_chars": _size(candidate),
                               "omitted_items": stats.omitted_items,
                               "truncated_strings": stats.truncated_strings}
        current = _tighter(current)

    candidate = _fallback(value, policy, stats, before_chars)
    return candidate, {"truncated": True, "before_chars": before_chars,
                       "after_chars": _size(candidate),
                       "omitted_items": stats.omitted_items,
                       "truncated_strings": stats.truncated_strings}


def _would_shrink(value, policy: ResultPolicy) -> bool:
    if isinstance(value, str):
        return len(value) > policy.max_str_chars
    if isinstance(value, list):
        return len(value) > policy.max_items or any(_would_shrink(v, policy) for v in value)
    if isinstance(value, dict):
        return any(_would_shrink(v, policy) for v in value.values())
    return False
