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
- watchers：只读旁观连接（被共享者，Ticket 008）。与 emits 分开登记：旁观者连入/断开
  不得覆盖或摘掉所有者的 emit 槽；emit_event 向该会话的 owner 槽与全部 watcher 各发一份。
  上行由 ws 层直接拒绝，hub 不接收观者的任何状态变更。
- pending_asks / pending_confirms：挂起中的提问卡/确认卡载荷。WS 连入时由 replay_pending
  补发（同一张卡的恢复，不是重复提问、重复确认）——两张卡都画在消息流的调用行上，而**待答**
  的那次调用还没有 tool 行（工具行要等结果回来才落库），刷新后历史里没有可贴标记的行，
  只能靠补发让前端合成一条；不补则卡片消失、future 永久挂起，会话卡死。
- subruns：正在执行的委派批次快照（Ticket 004）。子 run 事件全部经 emit_event 下发，
  这里顺路记下当前批次的任务与每行状态；WS 连入时补发 subrun_snapshot，刷新中不丢委派卡
  （回执行没回来，历史里没有 delegate 调用行可贴）。run 收口由 ws.run_task 的 finally 清除：
  进程重启即丢，与挂起点/运行态同一语义。同一会话先后两次委派时，新的 delegate tool_call
  重置快照——快照永远只描述当前那一次。
"""
import asyncio

class ChatHub:
    def __init__(self):
        self.ask_futures: dict[int, asyncio.Future] = {}
        self.confirm_futures: dict[int, asyncio.Future] = {}
        self.pending_asks: dict[int, list[dict]] = {}
        self.pending_confirms: dict[int, dict] = {}
        self.cancel_events: dict[int, asyncio.Event] = {}
        self.running: set[int] = set()
        self.compacting: set[int] = set()  # 压缩占位（无工具边界可注入，插话要区别对待）
        self.pending: dict[int, list[dict]] = {}  # 执行中插话：{content, attachment_ids, client_msg_id}
        self.run_vision: dict[int, bool] = {}  # 本 run 实际模型是否支持视觉（插话能力门）
        # 最近结果（会话状态角标数据源）：{outcome: completed|cancelled|error, finished_at: epoch 秒}。
        # 只活在进程内存：数据库 status 把「完成」与用户取消都落 idle，分不出绿/红。
        self.last_outcome: dict[int, dict] = {}
        # 需求澄清门禁的会话级解除标记（ADR-0032）：用户点过「自行决定」即置位，
        # 后续每个 run 都不再强制澄清。与挂起点同样是内存态——进程重启后失效（该会话
        # 回到「未解除」状态），这是既有代价，不为它加一层持久化。
        self.clarify_waived: dict[int, bool] = {}
        # 正在执行的委派快照（Ticket 004）：session_id → {"tasks": [{"title"}], "state": {index: {...}}}
        self.subruns: dict[int, dict] = {}
        self.emits: dict[int, callable] = {}  # session_id → 下行发送器: async (session_id, event) -> None
        # 只读旁观者（Ticket 008）：session_id → {emit, ...}。绝不动 emits，
        # 所有者的槽不被夺走；断连只摘自己那一份。
        self.watchers: dict[int, set] = {}
        # 旁观连接的归属与断连手段（Ticket 010）：emit → {session_id, workspace_id,
        # user_id, close}。撤销共享时据此按 (工作区, 用户) 精准关闭，别人的旁观连接与
        # 所有者的 emit 槽一概不碰。
        self.watcher_meta: dict = {}

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

    def add_watcher(self, session_id, emit, *, workspace_id=None, user_id=None, close=None):
        """登记只读旁观连接：集合与 emits 分开，旁观者永远不占所有者那一槽。

        close 是「服务端主动断开这条连接」的协程（撤销共享时用，Ticket 010）；
        没给就只登记发送器（纯事件扇出的旧路径不受影响）。
        """
        self.watchers.setdefault(session_id, set()).add(emit)
        if close is not None:
            self.watcher_meta[emit] = {"session_id": session_id,
                                       "workspace_id": workspace_id,
                                       "user_id": user_id, "close": close}

    def drop_watcher(self, session_id, emit):
        self.watcher_meta.pop(emit, None)
        group = self.watchers.get(session_id)
        if not group:
            return
        group.discard(emit)
        if not group:
            self.watchers.pop(session_id, None)

    async def close_watchers_for(self, *, workspace_id, user_id) -> int:
        """撤销共享/退出后关闭该工作区下该用户的全部旁观连接（Ticket 010）。

        只认 (workspace_id, user_id) 两张牌：同应用其他被共享者的连接、所有者的
        emit 槽都不动。返回真正发起关闭的连接数（测试与端点可观测）。
        发出关闭即注销登记：连接已判死，继续留在扇出表里只会有静默失败的发送；
        连接自身 finally 的 drop_watcher 是幂等的，重复清理无害。
        """
        closed = 0
        for group in list(self.watchers.values()):
            for emit in list(group):
                meta = self.watcher_meta.get(emit)
                if not meta or meta["workspace_id"] != workspace_id \
                        or meta["user_id"] != user_id:
                    continue
                closed += 1
                self.drop_watcher(meta["session_id"], emit)
                try:
                    await meta["close"]()
                except Exception:
                    pass  # 已断开/正在收口：撤销本身不因断连失败而失败
        return closed

    def active_session_ids(self) -> list:
        """当前有活跃下行流的会话：emits 登记表键集快照（只说明 WS 连着，不代表在跑）。"""
        return list(self.emits)

    def running_session_ids(self) -> list:
        """当前真正在执行 Agent run 的会话（发布闸门 FR4-09 数据源）。

        聊天页开着但会话 idle 时 emits 也非空，拿它当闸门会让普通发布误报「AI 正在
        修改代码」。run 的生命周期由 running 维护（含断线后仍在后台跑的任务），与
        /api/sessions/live-status 同源。
        """
        return list(self.running)

    def _track_subrun(self, session_id, event):
        """从流经的下行事件里维护委派快照（Ticket 004）：子 run 事件不带连接态，
        hub 是唯一同时看得到「delegate tool_call + 全部 subrun_*」的地方。"""
        t = event.get("type")
        if t == "tool_call" and event.get("name") == "delegate":
            args = event.get("args") if isinstance(event.get("args"), dict) else {}
            raw = args.get("tasks") if isinstance(args.get("tasks"), list) else []
            self.subruns[session_id] = {
                "tasks": [{"title": str(x.get("title") or "")} for x in raw
                          if isinstance(x, dict)],
                "state": {},
            }
            return
        cur = self.subruns.get(session_id)
        if cur is None:
            return
        idx = event.get("index")
        if not isinstance(idx, int) or isinstance(idx, bool):
            return
        if t == "subrun_start":
            cur["state"][idx] = {"title": event.get("title") or "", "status": "running"}
        elif t == "subrun_done":
            st = cur["state"].setdefault(idx, {"title": "", "status": "running"})
            st["status"] = "completed" if event.get("status") == "completed" else "error"
            if event.get("error") is not None:
                st["error"] = event["error"]

    def subrun_snapshot(self, session_id):
        """当前在跑那次委派的快照载荷；没有在跑的委派时返回 None。

        只带状态与标题——已落库的步骤由前端从历史子 run 行还原（行先于事件落库，
        连入前抓到的历史一定覆盖到已发出的事件）。"""
        cur = self.subruns.get(session_id)
        if not cur:
            return None
        subs = []
        for idx in sorted(cur["state"]):
            st = cur["state"][idx]
            item = {"index": idx, "title": st.get("title") or "",
                    "status": st.get("status") or "running"}
            if st.get("error") is not None:
                item["error"] = st["error"]
            subs.append(item)
        return {"type": "subrun_snapshot", "tasks": list(cur["tasks"]), "subruns": subs}

    def clear_subruns(self, session_id):
        self.subruns.pop(session_id, None)

    async def emit_event(self, session_id, event):
        """向指定会话的当前连接下发事件；该会话无连接（槽为空）时静默丢弃。

        旁观连接（Ticket 008）与 owner 槽各收一份，同一事件同一形状；旁观者发得慢
        不影响别人的事实语义——发送器自己吞掉断连异常（见 ws.py）。
        """
        self._track_subrun(session_id, event)
        emit = self.emits.get(session_id)
        if emit:
            await emit(session_id, event)
        for watcher in list(self.watchers.get(session_id, ())):
            await watcher(session_id, event)

    async def ask(self, session_id, questions, timeout):
        fut = asyncio.get_running_loop().create_future()
        self.ask_futures[session_id] = fut
        self.pending_asks[session_id] = questions
        try:
            # 实时流里 ask_user 调用行已由 tool_call 事件建好，这个事件只是「本轮正等人回答」
            # 的信号；重连补发时它是待答卡的唯一来源（见 replay_pending）
            await self.emit_event(session_id, {"type": "ask_user", "questions": questions})
            return await fut
        finally:
            self.ask_futures.pop(session_id, None)
            self.pending_asks.pop(session_id, None)

    async def replay_pending(self, session_id):
        """WS 连入补发挂起中的提问/确认（同一张卡的恢复，不是重复提问、重复确认）。

        两类卡片都画在消息流的调用行上，而**待答**的那次调用还没有 tool 行——工具行要等
        答案/结果回来才落库。所以刷新或切回会话后历史里根本没有可贴标记的行，只能靠这两份
        补发让前端合成一条；不补则卡片连同唯一的回答入口一起消失，future 永久挂起、会话卡死。
        """
        questions = self.pending_asks.get(session_id)
        if questions is not None:
            await self.emit_event(session_id, {"type": "ask_user", "questions": questions})
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

    def _resolve(self, store, session_id, value) -> bool:
        """兑现挂起点，返回**是否真有东西被兑现**。
        调用方（ws）拿它判断「这次上行是不是撞在了空挂起点上」——进程重启后挂起点全在
        丢失的内存里，前端却还留着可点的卡片。"""
        fut = store.get(session_id)
        if fut and not fut.done():
            fut.set_result(value)
            return True
        return False

    async def submit_answers(self, session_id, answers, declined=False):
        # declined 是「用户点了不再提问，自行决定」的机器可读标记（ADR-0032）：
        # 门禁据此解除本会话的强制澄清。刻意不靠匹配那段固定文案识别——散文匹配的真源
        # 在前端常量里，改一个字就静默失效（ADR-0029 同类教训）。
        payload = {"answers": answers}
        if declined:
            payload["declined"] = True
        return self._resolve(self.ask_futures, session_id, payload)
    async def resolve_confirm(self, session_id, approved):
        return self._resolve(self.confirm_futures, session_id, bool(approved))

    def cancel(self, session_id):
        ev = self.cancel_events.get(session_id)
        if ev:
            ev.set()
        # 未送达的插话随取消一并丢弃：它们没落库，留着会让下一次 run 莫名收到上一次的半句话
        self.pending.pop(session_id, None)
        self._resolve(self.ask_futures, session_id, None)
        self._resolve(self.confirm_futures, session_id, False)

    def is_cancelling(self, session_id) -> bool:
        """停止已请求、run 尚未走完收口。对外不再宣称「运行中」：
        重连补发 status(running) 会把刚停的任务在界面上立回来（侧栏转圈/停止按钮复活）。"""
        ev = self.cancel_events.get(session_id)
        return ev is not None and ev.is_set()
