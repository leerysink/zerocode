# app/api/dev_logs.py
"""预览日志 REST：`GET /api/apps/{app_id}/dev/logs`。

一次请求拼两路来源，页面「查看日志」因此既能回看也能续看：
  ① 平台侧步骤行（DeployHub 环形缓冲）——「预检 / 渲染 deploy/ / 迁移 / 起栈 / 健康检查」
     走到哪一步、各花了多久；这些行只在平台进程内存里，容器日志里没有它们。
  ② dev 栈容器输出（`docker compose logs --tail N --no-color --timestamps`）——
     应用自己打的启动 banner 与运行输出。
两路按时间戳归并返回；实时增量继续走 `/ws/deploy/{app_id}` 的 `dev_log` 事件（带 `seq`，
前端据此与 REST 历史去重）。

刻意单独成模块而不是塞进 app/api/apps.py：前缀同为 /api/apps，路由互不干涉，
而 apps.py 同一时间有另一路改动在改（避免把别人的半成品卷进本次提交）。
"""
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request

from app.deploy import engine
from app.deploy.errors import DeployError
from app.services import app_service

router = APIRouter(prefix="/api/apps")

MAX_TAIL = 1000        # 日志面板要的是「最近发生了什么」，不是全量转储


def _iso(epoch) -> str:
    """环形缓冲存的是 epoch 秒；给人看的时间转成本地时区 + 秒精度，解析不了就留空。"""
    try:
        return datetime.fromtimestamp(float(epoch), timezone.utc).astimezone().isoformat(
            timespec="seconds")
    except (TypeError, ValueError, OSError):
        return ""


@router.get("/{app_id}/dev/logs")
async def dev_logs(request: Request, app_id: int, tail: int = 200, since_seq: int = 0):
    """预览日志（平台步骤行 + 容器输出，按时间归并）。

    `tail` 夹在 [1, 1000]：手改 URL 传个 999999 不该把平台内存里的历史连锅端走。
    `since_seq` 只要平台侧 seq 大于它的行——不开 WS 时前端可用它做增量轮询。
    容器侧拿不到（Docker 没开 / 栈没起）**不报错**：note 讲明原因，步骤行照样返回。
    """
    sf = request.app.state.session_factory
    hub = request.app.state.deploy_hub
    try:
        app = await app_service.get_app(sf, app_id, owner_user_id=request.state.actor["id"])
    except DeployError as e:
        raise HTTPException(e.status, str(e))
    n = min(max(int(tail), 1), MAX_TAIL)
    since = max(int(since_seq), 0)
    platform = [{"seq": r["seq"], "ts": _iso(r["epoch"]), "epoch": r["epoch"],
                 "source": "platform", "service": "平台", "line": r["line"]}
                for r in hub.tail_logs(app_id, limit=n, since_seq=since)]
    container, note = await engine.container_logs(sf, app, n)
    lines = engine.merge_logs(platform, container)
    return {"lines": lines, "note": note, "status_dev": app.get("status_dev"),
            "url": (f"http://localhost:{app['dev_port']}" if app.get("dev_port") else ""),
            "last_seq": platform[-1]["seq"] if platform else since}
