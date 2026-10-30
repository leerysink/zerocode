"""ChatHub：会话级消息总线。

- ask/confirm：挂起等待前端上行（ask_submit/confirm），cancel 时统一复位；
  confirm_request 下行事件由 AgentRunner._dispatch 统一下发（Task 7 裁定：runner 唯一
  发出方），hub.confirm 只负责挂 future 等待，避免前端收到重复卡片。
- pending：执行中用户插话（steering）的待注入队列，项为
  {content, attachment_ids, client_msg_id}——附件随插话一起在边界绑定落库；
  cancel 时一并丢弃（未送达的插话不得偷偷活到下一次 run）。
- run_vision：本 run 实际解析出的模型是否支持视觉。执行中的图片插话按它放行/拒绝，
  在 run 结束时清理（不能按「当前默认模型」判断——run 跑的是启动时解析的那个模型）。
- emits：按会话分槽的下行发送器（session_id → async (session_id, event) -> None），
  同会话后连覆盖；断连 drop 清槽（仅清自己注入的槽，防误删后连者的槽），
  跨会话互不串扰（终审修复轮2：原全局单槽会被任意会话的连接覆盖导致事件串线）。
- pending_confirms：挂起中的确认卡载荷。WS 连入时由 replay_pending 补发（同一张卡的恢复，
  不是重复确认）——不补则切走/刷新后卡片消失、future 永久挂起，会话卡死。
  提问卡不在此列：它由消息流里的 ask_user 调用行渲染，「有调用行、无结果行」就是待答态，
  重连后从历史原样重建，不需要补发（ADR 0029）。
"""
import asyncio

class ChatHub:
    def __init__(self):
        self.ask_futures: dict[int, asyncio.Future] = {}
        self.confirm_futures: dict[int, asyncio.Future] = {}
        self.pending_confirms: dict[int, dict] = {}
        self.cancel_events: dict[int, asyncio.Event] = {}
        self.running: set[int] = set()
        self.compacting: set[int] = set()  # 压缩占位（无工具边界可注入，插话要区别对待）
        self.pending: dict[int, list[dict]] = {}  # 执行中插话：{content, attachment_ids, client_msg_id}
        self.run_vision: dict[int, bool] = {}  # 本 run 实际模型是否支持视觉（插话能力门）
        self.emits: dict[int, callable] = {}  # session_id → 下行发送器: async (session_id, event) -> None

    def queue(self, session_id, item, max_pending) -> bool:
        """插话入队（保序）。队列满则拒收，由 ws 侧回 busy 非终态提示。"""
        q = self.pending.setdefault(session_id, [])
        if len(q) >= int(max_pending):
            return False
        q.append(item)
        return True

    async def pop_pending(self, session_id) -> list[dict]:
        """取空即清：注入权在 runner，避免同一句被重复注入。"""
        return self.pending.pop(session_id, None) or []

    def set_emit(self, session_id, emit):
        """注入/覆盖同会话的下行发送器（WS 连接建立时调用，同会话后连覆盖）。"""
        self.emits[session_id] = emit

    def drop_emit(self, session_id, emit):
        """断连清理：仅当当前槽仍是自己注入的 emit 才移除，防误删后连者的槽。"""
        if self.emits.get(session_id) is emit:
            self.emits.pop(session_id, None)

    def active_session_ids(self) -> list:
        """当前有活跃下行流的会话（发布闸门 FR4-09 数据源）：emits 登记表键集快照。"""
        return list(self.emits)

    async def emit_event(self, session_id, event):
        """向指定会话的当前连接下发事件；该会话无连接（槽为空）时静默丢弃。"""
        emit = self.emits.get(session_id)
        if emit:
            await emit(session_id, event)

    async def ask(self, session_id, questions, timeout):
        fut = asyncio.get_running_loop().create_future()
        self.ask_futures[session_id] = fut
        try:
            # 事件只是「本轮正等人回答」的信号：卡片本身由调用行渲染，前端不消费这个事件建卡
            await self.emit_event(session_id, {"type": "ask_user", "questions": questions})
            return await fut
        finally:
            self.ask_futures.pop(session_id, None)

    async def replay_pending(self, session_id):
        """WS 连入补发挂起中的确认卡（同一张卡的恢复，不是重复确认）。"""
        payload = self.pending_confirms.get(session_id)
        if payload is not None:
            await self.emit_event(session_id, {"type": "confirm_request", **payload})

    async def confirm(self, session_id, payload):
        fut = asyncio.get_running_loop().create_future()
        self.confirm_futures[session_id] = fut
        self.pending_confirms[session_id] = payload
        try:
            # confirm_request 事件由 AgentRunner._dispatch 统一下发（Task 7 裁定：runner 唯一发出方），
            # hub 只负责挂 future 等待前端 confirm 上行，避免前端收到重复卡片
            return await fut
        finally:
            self.confirm_futures.pop(session_id, None)
            self.pending_confirms.pop(session_id, None)

    def _resolve(self, store, session_id, value):
        fut = store.get(session_id)
        if fut and not fut.done():
            fut.set_result(value)

    async def submit_answers(self, session_id, answers):
        self._resolve(self.ask_futures, session_id, {"answers": answers})
    async def resolve_confirm(self, session_id, approved): self._resolve(self.confirm_futures, session_id, bool(approved))

    def cancel(self, session_id):
        ev = self.cancel_events.get(session_id)
        if ev:
            ev.set()
        # 未送达的插话随取消一并丢弃：它们没落库，留着会让下一次 run 莫名收到上一次的半句话
        self.pending.pop(session_id, None)
        self._resolve(self.ask_futures, session_id, None)
        self._resolve(self.confirm_futures, session_id, False)
