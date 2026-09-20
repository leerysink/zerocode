# app/api/deploy_ws.py
"""WS /ws/deploy/{app_id}：订阅 DeployHub，实时下发部署事件（规格 03/04 WS 协议）。

事件来源是 `app.state.deploy_hub`（Task 11 的按应用分槽广播）：引擎与 API 线以
`publish(app_id, event)` 投递，本端点只做「队列 → 连接」的搬运，不解释业务语义。

保活：队列空转 `_KEEPALIVE` 秒就下发一条 `{"type":"ping"}`。前端无需响应——它的作用是
①让 Nginx/代理侧的空闲连接不被掐，②在客户端已经走掉时让下一次 send 抛错，从而走进
`finally` 完成退订（推送型端点不读 receive，断连只能在发送时被发现）。
"""
import asyncio, logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)
router = APIRouter()
_KEEPALIVE = 30.0      # 秒：无事件时的保活探测间隔（测试里改小即可，无需等 30s）


@router.websocket("/ws/deploy/{app_id}")
async def deploy_ws(ws: WebSocket, app_id: int):
    await ws.accept()
    hub = ws.app.state.deploy_hub
    q = hub.subscribe(app_id)
    try:
        await ws.send_json({"type": "hello", "app_id": app_id})
        while True:
            try:
                ev = await asyncio.wait_for(q.get(), timeout=_KEEPALIVE)
            except asyncio.TimeoutError:
                await ws.send_json({"type": "ping"})       # 保活探测；前端无需响应
                continue
            await ws.send_json(ev)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        # 推送型端点不读 receive，客户端断开只能在下一次 send 时被发现（RuntimeError 等）：
        # 与正常断连同等收口，只留一条 debug（否则每次关页面都刷一条 ASGI traceback）。
        logger.debug("部署事件流收口 app_id=%s: %s", app_id, e)
    finally:
        hub.unsubscribe(app_id, q)                          # 退订必达，不留僵尸槽
        try:
            await ws.close()
        except Exception:
            pass
