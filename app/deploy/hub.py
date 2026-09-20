# app/deploy/hub.py
"""DeployHub：按应用分槽的事件广播（对齐 ChatHub 分槽裁定，杜绝跨应用串扰）。

日志环形缓冲：`publish` 时顺手把 `dev_log` 存进每应用的 deque。推送型事件流只服务
「此刻在线的连接」，而页面「查看日志」要回答的是「刚才那次启动死在哪一步」——
连接断开即丢的历史必须有个地方接着，否则用户打开面板只会看到一片空白。
内存有界（默认 500 行/应用），平台重启即清；容器侧输出另有 `docker compose logs` 兜底。
"""
import asyncio
from collections import deque
from time import time

LOG_RING_DEFAULT = 500      # 每应用保留最近多少条平台侧日志行


class DeployHub:
    def __init__(self, log_ring: int = LOG_RING_DEFAULT):
        self.subs: dict[int, list[asyncio.Queue]] = {}
        self.logs: dict[int, deque] = {}
        self.ring_size = max(int(log_ring), 1)
        self._seq = 0

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
        队列满时丢弃最旧不如丢弃最新——预览日志是可再生的观测流，积压比丢行更危险（不阻塞事件循环）。

        `dev_log` 先入环形缓冲并领一个 `seq`：REST 历史与 WS 实时两路会重叠，前端按 seq 去重；
        seq 也写回事件本身（同一 dict 随即广播），两路才共用同一套编号。
        """
        if event.get("type") == "dev_log":
            self._seq += 1
            event["seq"] = self._seq
            self.logs.setdefault(app_id, deque(maxlen=self.ring_size)).append({
                "seq": self._seq, "epoch": time(),
                "source": "platform", "line": str(event.get("line", "")),
            })
        for q in list(self.subs.get(app_id, [])):
            if not q.full():
                q.put_nowait(event)

    def tail_logs(self, app_id: int, limit: int = 200, since_seq: int = 0) -> list[dict]:
        """取某应用最近的日志行（`since_seq` 之后的），按 seq 升序。分槽隔离，不跨应用串读。"""
        buf = self.logs.get(app_id)
        if not buf:
            return []
        rows = [dict(r) for r in buf if r["seq"] > int(since_seq)]
        return rows[-max(int(limit), 1):]
