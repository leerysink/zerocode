# app/api/agent_metrics.py
"""Agent 工具度量聚合 `GET /api/agent/tool-metrics`（2026-11-10）。

只读接口：按时间窗口把执行记录按工具聚合（次数/失败/拒绝/截断/耗时/结果体积）。
鉴权与 app metrics 同款：创始人看全部（owner=None），其他人只看自己工作区下的会话；
没有「按 id 取单个会话」的路径，所以不存在 IDOR 的入口面。
"""
from fastapi import APIRouter, HTTPException, Request

from app.services import tool_metric_service as tm

router = APIRouter(prefix="/api/agent")


@router.get("/tool-metrics")
async def tool_metrics(request: Request, window: str = "24h"):
    interval = tm.WINDOWS.get(window)
    if interval is None:
        raise HTTPException(400, f"未知窗口: {window}（可选 {'/'.join(tm.WINDOWS)}）")
    actor = request.state.actor
    owner = None if actor.get("is_founder") else actor["id"]
    return await tm.summary(request.app.state.session_factory, window=window,
                            owner_user_id=owner)
