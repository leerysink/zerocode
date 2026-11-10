"""Agent 工具度量：每次工具调用一行 + 时间窗口聚合（2026-11-10）。

写入是**旁路**：度量失败绝不影响对话与工具结果本身，因此这里吞异常、只记日志。
聚合按 created_at 走白名单窗口（duration 由调用方从固定表取，不接受外部字符串）。
归属口径与会话一致：chat_session → workspace.owner_user_id；owner_user_id 传 None 表示
看全部（创始人 bypass），由 API 层按 actor 决定。
"""
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

logger = logging.getLogger(__name__)

# 前端可传的窗口白名单：值绑定为 interval 参数，不接外部字符串。
WINDOWS = {"1h": timedelta(hours=1), "24h": timedelta(hours=24),
           "7d": timedelta(days=7), "30d": timedelta(days=30)}

_INSERT = text(
    "INSERT INTO agent_tool_metric "
    "(session_id, tool_name, ok, denied, truncated, duration_ms, result_chars, round_no) "
    "VALUES (:sid, :tool, :ok, :denied, :truncated, :ms, :chars, :round)")

_AGG = text(
    "SELECT m.tool_name AS tool, count(*) AS calls, "
    "count(*) FILTER (WHERE m.ok) AS ok, "
    "count(*) FILTER (WHERE m.denied) AS denied, "
    "count(*) FILTER (WHERE m.truncated) AS truncated, "
    "avg(m.duration_ms) AS avg_ms, max(m.duration_ms) AS max_ms, "
    "sum(m.duration_ms) AS total_ms, "
    "sum(m.result_chars) AS total_result_chars, max(m.result_chars) AS max_result_chars "
    "FROM agent_tool_metric m "
    "JOIN chat_session cs ON cs.id = m.session_id "
    "JOIN workspace w ON w.id = cs.workspace_id "
    "WHERE m.created_at >= now() - CAST(:iv AS INTERVAL) "
    "AND (CAST(:owner AS BIGINT) IS NULL OR w.owner_user_id = CAST(:owner AS BIGINT)) "
    "GROUP BY m.tool_name ORDER BY count(*) DESC, m.tool_name")


async def record(session_factory, *, session_id: int, tool_name: str, ok: bool,
                 duration_ms: int, result_chars: int, truncated: bool = False,
                 denied: bool = False, round_no: int = 0) -> None:
    """写一行度量；任何失败只记日志（旁路纪律，见模块 docstring）。"""
    try:
        async with session_factory() as s:
            await s.execute(_INSERT, {"sid": session_id, "tool": tool_name, "ok": ok,
                                      "denied": denied, "truncated": truncated,
                                      "ms": max(0, int(duration_ms)),
                                      "chars": max(0, int(result_chars)),
                                      "round": max(0, int(round_no))})
            await s.commit()
    except Exception:
        logger.warning("工具度量写入失败（不影响本次执行）session=%s tool=%s",
                       session_id, tool_name, exc_info=True)


def _tool_row(r) -> dict:
    calls = int(r["calls"])
    total_chars = int(r["total_result_chars"] or 0)
    return {
        "tool": r["tool"],
        "calls": calls,
        "ok": int(r["ok"]),
        "failed": calls - int(r["ok"]),
        "denied": int(r["denied"]),
        "truncated": int(r["truncated"]),
        "avg_ms": round(float(r["avg_ms"] or 0), 1),
        "max_ms": int(r["max_ms"] or 0),
        "total_result_chars": total_chars,
        "avg_result_chars": int(total_chars / calls) if calls else 0,
        "max_result_chars": int(r["max_result_chars"] or 0),
        "total_ms": int(r["total_ms"] or 0),
    }


async def summary(session_factory, *, window: str = "24h",
                  owner_user_id: int | None = None) -> dict:
    """窗口内的按工具聚合：totals + by_tool。未知窗口由调用方先校验（这里 KeyError 即 bug）。"""
    interval = WINDOWS[window]
    since = datetime.now(timezone.utc) - interval
    async with session_factory() as s:
        rows = (await s.execute(_AGG, {"iv": interval, "owner": owner_user_id})).mappings().all()
    by_tool = [_tool_row(r) for r in rows]
    totals = {
        "calls": sum(r["calls"] for r in by_tool),
        "ok": sum(r["ok"] for r in by_tool),
        "failed": sum(r["failed"] for r in by_tool),
        "denied": sum(r["denied"] for r in by_tool),
        "truncated": sum(r["truncated"] for r in by_tool),
        "total_result_chars": sum(r["total_result_chars"] for r in by_tool),
        "avg_ms": None,
    }
    if totals["calls"]:
        totals["avg_ms"] = round(
            sum(r["total_ms"] for r in by_tool) / totals["calls"], 1)
    return {"window": window, "since": since.isoformat(), "totals": totals,
            "by_tool": by_tool}
