# app/api/dev_logs.py
"""预览日志 REST：`GET /api/apps/{app_id}/dev/logs`。

一次请求拼两路来源（平台步骤行 + dev 栈容器输出），归并逻辑在 `app/deploy/logs.py`——
Agent 的 `preview_logs` 工具读的是同一个收集器，两路口径不会漂移。
实时增量继续走 `/ws/deploy/{app_id}` 的 `dev_log` 事件（带 `seq`，前端据此与 REST 历史去重）。

刻意单独成模块而不是塞进 app/api/apps.py：前缀同为 /api/apps，路由互不干涉，
而 apps.py 同一时间有另一路改动在改（避免把别人的半成品卷进本次提交）。
"""
from fastapi import APIRouter, HTTPException, Request

from app.deploy.errors import DeployError
from app.deploy.logs import MAX_TAIL, collect_dev_logs
from app.services import app_service

router = APIRouter(prefix="/api/apps")

__all__ = ["router", "MAX_TAIL"]     # MAX_TAIL 是既有测试的导入锚（from app.api.dev_logs import MAX_TAIL）


@router.get("/{app_id}/dev/logs")
async def dev_logs(request: Request, app_id: int, tail: int = 200, since_seq: int = 0):
    """预览日志（平台步骤行 + 容器输出，按时间归并）。

    `tail` 夹在 [1, 1000]：手改 URL 传个 999999 不该把平台内存里的历史连锅端走。
    `since_seq` 只要平台侧 seq 大于它的行——不开 WS 时前端可用它做增量轮询。
    """
    sf = request.app.state.session_factory
    hub = request.app.state.deploy_hub
    try:
        app = await app_service.get_app(sf, app_id, owner_user_id=request.state.actor["id"])
    except DeployError as e:
        raise HTTPException(e.status, str(e))
    n = min(max(int(tail), 1), MAX_TAIL)
    return await collect_dev_logs(sf, app, hub, tail=n, since_seq=max(int(since_seq), 0))
