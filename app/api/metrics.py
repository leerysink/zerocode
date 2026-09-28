# app/api/metrics.py
"""应用资源监控 REST：当前实时 `GET /api/apps/{app_id}/metrics/current`。

与 apps.py / dev_logs.py 同前缀但独立成模块（沿用 dev_logs 的隔离纪律：路由互不干涉，
避免把别人的半成品卷进本次改动）。本期（Ticket 001）只回 workspace_size + prod_status；
CPU/内存/db_size 由后续票在同一响应形状上扩字段，前端一处读取不随之改结构。

鉴权（本期）：经 app_service.get_app 传当前账号做归属过滤，别人的应用/未知应用 404；
创始人看全部的 bypass 在 Ticket 003 接入（届时在此把 owner_user_id 按 is_founder 置 None）。
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import text

from app.deploy.errors import DeployError
from app.deploy.metrics import LIVE_PROD_STATUSES
from app.services import app_service

router = APIRouter(prefix="/api/apps")

# 历史窗口白名单：键是前端可传的 window，值是 timedelta（绑定为 interval 参数，不接外部字符串）。
WINDOWS = {"1h": timedelta(hours=1), "24h": timedelta(hours=24),
           "7d": timedelta(days=7), "30d": timedelta(days=30)}
# 响应里恒备的 kind 键：本期只有 workspace_size 出数据，cpu/memory/db_size 由后续票填。
KNOWN_KINDS = ("cpu", "memory", "db_size", "workspace_size")
# 历史读取侧时间桶收敛上限：把任意窗口都折到 ≤ 这多个点（与 sparkline 视口对齐），
# 避免 30d × 30s 粒度直返十几万行打爆内存/响应/前端渲染（存储侧不动，只读侧聚合）。
MAX_HISTORY_POINTS = 300


async def _load_app(request: Request, app_id: int) -> dict:
    """取应用并做归属校验：owner 看自己、创始人看全部；别人的/未知的一律 404。

    鉴权只这一处，current/history 共用：非创始人传 owner_user_id 走 get_app 的归属过滤（命中不到
    即 404，与不存在同口径不暴露存在性）；创始人（is_founder）传 None 绕过过滤。复用
    get_app 现有语义，不新增存在性区分。"""
    actor = request.state.actor
    owner = None if actor.get("is_founder") else actor["id"]
    try:
        return await app_service.get_app(request.app.state.session_factory, app_id,
                                         owner_user_id=owner)
    except DeployError as e:
        raise HTTPException(e.status, str(e))


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
    app = await _load_app(request, app_id)
    sf = request.app.state.session_factory
    # 停止态语义（US-10）：cpu/memory 只在栈真正有活容器时才是「当前值」；非运行态下
    # 最旧可能是几小时前的残留，当「当前」会误导 → 归 None（前端空态）。磁盘/db 与运行态无关，照回。
    live = app.get("status_prod") in LIVE_PROD_STATUSES
    return {
        "cpu": await _latest(sf, app_id, "cpu") if live else None,
        "memory": await _latest(sf, app_id, "memory") if live else None,
        "db_size": await _latest(sf, app_id, "db_size"),
        "workspace_size": await _latest(sf, app_id, "workspace_size"),
        "prod_status": app.get("status_prod"),
    }


@router.get("/{app_id}/metrics/history")
async def metrics_history(request: Request, app_id: int, window: str = "24h"):
    """历史曲线：窗口内各 kind 按时间桶均值聚合的时序（按桶升序），kind 键恒在（无数据为空列表）。

    窗口只截时轴（US-02）；未知 window → 400。桶宽 = 窗口秒数/MAX_HISTORY_POINTS，把点数
    固定收敛到 ≤~300（与 sparkline 对齐）；interval 来自硬编码白名单（timedelta），桶宽在 Python 算好
    后作为数值参数绑定，均无注入面。"""
    interval = WINDOWS.get(window)
    if interval is None:
        raise HTTPException(400, f"未知窗口: {window}（可选 {'/'.join(WINDOWS)}）")
    await _load_app(request, app_id)
    sf = request.app.state.session_factory
    step = max(interval.total_seconds() / MAX_HISTORY_POINTS, 1.0)
    async with sf() as s:
        rows = (await s.execute(text(
            "SELECT kind, floor(extract(epoch FROM ts) / :step)::bigint AS bucket, "
            "avg(value) AS value FROM app_metrics "
            "WHERE app_id = :a AND ts >= now() - CAST(:iv AS INTERVAL) "
            "GROUP BY kind, bucket ORDER BY kind, bucket"),
            {"a": app_id, "step": step, "iv": interval})).mappings().all()
    series = {k: [] for k in KNOWN_KINDS}
    for r in rows:
        ts = datetime.fromtimestamp(r["bucket"] * step, tz=timezone.utc).isoformat()
        series.setdefault(r["kind"], []).append({"ts": ts, "value": float(r["value"])})
    return series
