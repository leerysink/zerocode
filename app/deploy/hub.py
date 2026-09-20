# app/deploy/hub.py
"""DeployHub：按应用分槽的事件广播（对齐 ChatHub 分槽裁定，杜绝跨应用串扰）。"""
import asyncio


class DeployHub:
    def __init__(self):
        self.subs: dict[int, list[asyncio.Queue]] = {}

    def subscribe(self, app_id: int) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self.subs.setdefault(app_id, []).append(q)
        return q

    def unsubscribe(self, app_id: int, q: asyncio.Queue) -> None:
        lst = self.subs.get(app_id)
        if lst and q in lst:
            lst.remove(q)
            if not lst:
                self.subs.pop(app_id, None)

    def publish(self, app_id: int, event: dict) -> None:
        """同步广播（put_nowait）：调用方（engine / Task 12 API 线）以 notify(app_id, event) 直发，
        队列满时丢弃最旧不如丢弃最新——预览日志是可再生的观测流，积压比丢行更危险（不阻塞事件循环）。"""
        for q in list(self.subs.get(app_id, [])):
            if not q.full():
                q.put_nowait(event)
