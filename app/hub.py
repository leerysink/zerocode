"""ChatHub：会话级消息总线。

- ask/confirm：挂起等待前端上行（ask_answer/ask_choice/confirm），cancel 时统一复位；
  confirm_request 下行事件由 AgentRunner._dispatch 统一下发（Task 7 裁定：runner 唯一
  发出方），hub.confirm 只负责挂 future 等待，避免前端收到重复卡片。
- emit：由 WS 层注入的下行发送器 async (session_id, event) -> None（单用户单连接，
  后连连接覆盖）。
"""
import asyncio

class ChatHub:
    def __init__(self):
        self.ask_futures: dict[int, asyncio.Future] = {}
        self.confirm_futures: dict[int, asyncio.Future] = {}
        self.cancel_events: dict[int, asyncio.Event] = {}
        self.running: set[int] = set()
        self.emit = None  # 由 WS 层注入: async (session_id, event) -> None

    async def ask(self, session_id, question, options, timeout):
        fut = asyncio.get_running_loop().create_future()
        self.ask_futures[session_id] = fut
        try:
            if self.emit:
                await self.emit(session_id, {"type": "ask_user", "question": question, "options": options})
            return await fut
        finally:
            self.ask_futures.pop(session_id, None)

    async def confirm(self, session_id, payload):
        fut = asyncio.get_running_loop().create_future()
        self.confirm_futures[session_id] = fut
        try:
            # confirm_request 事件由 AgentRunner._dispatch 统一下发（Task 7 裁定：runner 唯一发出方），
            # hub 只负责挂 future 等待前端 confirm 上行，避免前端收到重复卡片
            return await fut
        finally:
            self.confirm_futures.pop(session_id, None)

    def _resolve(self, store, session_id, value):
        fut = store.get(session_id)
        if fut and not fut.done():
            fut.set_result(value)

    async def answer(self, session_id, text): self._resolve(self.ask_futures, session_id, {"answer": text})
    async def choose(self, session_id, option): self._resolve(self.ask_futures, session_id, {"option": option, "answer": option})
    async def resolve_confirm(self, session_id, approved): self._resolve(self.confirm_futures, session_id, bool(approved))

    def cancel(self, session_id):
        ev = self.cancel_events.get(session_id)
        if ev:
            ev.set()
        self._resolve(self.ask_futures, session_id, None)
        self._resolve(self.confirm_futures, session_id, False)
