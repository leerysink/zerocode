# app/api/metrics.py
"""应用资源监控 REST：当前实时 `GET /api/apps/{app_id}/metrics/current`。

与 apps.py / dev_logs.py 同前缀但独立成模块（沿用 dev_logs 的隔离纪律：路由互不干涉，
避免把别人的半成品卷进本次改动）。本期（Ticket 001）只回 workspace_size + prod_status；
CPU/内存/db_size 由后续票在同一响应形状上扩字段，前端一处读取不随之改结构。

鉴权（本期）：经 app_service.get_app 传当前账号做归属过滤，别人的应用/未知应用 404；
创始人看全部的 bypass 在 Ticket 003 接入（届时在此把 owner_user_id 按 is_founder 置 None）。
"""
from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import text

from app.deploy.errors import DeployError
from app.services import app_service

router = APIRouter(prefix="/api/apps")


async def _latest(session_factory, app_id: int, kind: str):
    """取某应用某类指标的最近一个点 → {value, ts}；从未采过返 None（不是 0）。"""
    async with session_factory() as s:
        row = (await s.execute(text(
            "SELECT value, ts FROM app_metrics WHERE app_id = :a AND kind = :k "
            "ORDER BY ts DESC LIMIT 1"), {"a": app_id, "k": kind})).mappings().first()
    if not row:
        return None
    return {"value": float(row["value"]), "ts": row["ts"].isoformat()}


@router.get("/{app_id}/metrics/current")
async def metrics_current(request: Request, app_id: int):
    sf = request.app.state.session_factory
    try:
        app = await app_service.get_app(sf, app_id, owner_user_id=request.state.actor["id"])
    except DeployError as e:
        raise HTTPException(e.status, str(e))
    return {
        "workspace_size": await _latest(sf, app_id, "workspace_size"),
        "prod_status": app.get("status_prod"),
    }
