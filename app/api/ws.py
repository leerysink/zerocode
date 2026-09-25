"""WS 聊天端点 /ws/chat/{session_id}。

上行：user_message / ask_submit / confirm / cancel / compact；
下行：token / tool_call / tool_result / ask_user / confirm_request /
message_done / error / status / file_change / compacting / compaction_done / busy /
steering_done（由 runner 与 hub 经 emit 下发）。error 与 status 是**本轮终态**；busy 是
执行中被拒的非终态提示（不得据此复位前端执行态，否则「停止」按钮消失 → 发不出也停不掉的死循环）。

执行中再发 user_message = 插话（steering）：进 hub.pending 队列，由 runner 在下一个
工具边界落库注入并回 steering_done；压缩中或队列排满仍回 busy（两条路径都不落库）。

按会话分槽：同会话后连连接覆盖该会话的 emit 槽，下行事件随之切到新连接；
断连 drop 自己的槽（防僵尸槽），跨会话互不串扰（终审修复轮2）。
runner 的 send 为单参签名，这里经 hub 会话槽晚绑定转发；LLM 实例按 run
解析一次（cfg 本就每 run 取一次，避免测试注入的工厂每轮重放轮次）；
模型来源按会话逐级回退：会话绑定 model_config → 默认模型 → 全局 llm.*
（resolve_model，spec §4.4）。
"""
import asyncio, json
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import text as _sql
from app.agent.llm import OpenAICompatLLM
from app.agent.loop import AgentRunner
from app.services import compaction_service
from app.services import config_service

router = APIRouter()

@router.websocket("/ws/chat/{session_id}")
async def ws_chat(ws: WebSocket, session_id: int):
    await ws.accept()
    app = ws.app
    hub = app.state.hub

    async def emit(sid: int, event: dict):
        try:
            await ws.send_text(json.dumps(event, ensure_ascii=False))
        except Exception:
            pass  # 连接已断开等场景：静默丢弃（单用户场景无回压需求）

    # 按会话分槽注入：同会话后连覆盖，跨会话互不影响（终审修复轮2）
    hub.set_emit(session_id, emit)
    # 重连状态自洽：连入时该会话仍在跑（刷新页面/断线重连/另开标签页），补发一帧
    # status(running) 快照，让前端把「停止」按钮亮回来。不补则前端 openSession 一律
    # 视为空闲 → 输入框解锁而后端仍在执行，用户每发一句都撞拒绝（死循环根因之一）。
    if session_id in hub.running:
        await emit(session_id, {"type": "status", "status": "running"})

    async def config_getter():
        # 用户视图（Ticket 007）：会话经 workspace 取 owner，llm.* 读自己的/回落全局；
        # agent.* 等平台级键所有人共享，owner 为 NULL 时等价纯全局视图
        async with app.state.session_factory() as s:
            uid = (await s.execute(_sql(
                "SELECT w.owner_user_id FROM chat_session cs "
                "JOIN workspace w ON w.id = cs.workspace_id WHERE cs.id=:i"),
                {"i": session_id})).scalar()
            return await config_service.get_all(s, owner_user_id=uid)

    def llm_factory(cfg, model_cfg=None):
        if hasattr(app.state, "llm_factory"):
            return app.state.llm_factory(cfg)
        mc = model_cfg or {}
        return OpenAICompatLLM(
            mc.get("base_url") or cfg.get("llm.base_url"),
            mc.get("api_key") if mc.get("api_key") is not None else cfg.get("llm.api_key"),
            mc.get("model") or cfg.get("llm.model"))

    try:
        while True:
            msg = json.loads(await ws.receive_text())
            t = msg.get("type")
            if t == "user_message":
                text_ = (msg.get("content") or "").strip()
                if not text_:
                    continue
                if session_id in hub.running:
                    # 执行中再发一句 = **插话（steering）**：入队等下一个工具边界注入，
                    # 不另起一轮 run、也不再当错误拒收（历史上回 error 造成过
                    # 「发不出也停不掉」的死循环，见 a0cecb1）。
                    if session_id in hub.compacting:
                        # 压缩没有工具边界可注入，也没有模型会读它 —— 仍然拒收（非终态）
                        await emit(session_id, {"type": "busy", "message": "上下文压缩中，请稍候"})
                        continue
                    cfg_now = await config_getter()
                    cap = int(cfg_now.get("agent.steering_queue_max", 20) or 20)
                    if not hub.queue(session_id, text_, cap):
                        await emit(session_id, {"type": "busy",
                                                "message": f"插话已排满（上限 {cap} 条），请等本轮跑完或点「停止」"})
                    continue

                async def send(event: dict):
                    # AgentRunner.send 是单参签名；经 hub 会话槽晚绑定转发，
                    # 同会话后连覆盖槽后事件自动切到新连接（见 busy 测试语义）
                    await hub.emit_event(session_id, event)

                async def run_task(user_text_: str):
                    cancel = asyncio.Event()
                    hub.cancel_events[session_id] = cancel
                    hub.running.add(session_id)
                    try:
                        # 落库 'running' 激活 watchdog（spec §7 崩溃恢复）：此前全仓无代码写
                        # 'running'（schema 默认 idle，runner 终态只写 idle/error），watchdog 的
                        # WHERE 永远空匹配；若本任务后续步骤（config/llm 解析等）即崩溃，残留的
                        # 'running' 会在 10 分钟后被 watchdog 回收为 error
                        async with app.state.session_factory() as s:
                            await s.execute(_sql(
                                "UPDATE chat_session SET status='running', updated_at=now() WHERE id=:i"),
                                {"i": session_id})
                            await s.commit()
                        # LLM 按 run 解析一次：runner 对 callable 每轮重建实例，
                        # 而测试注入工厂每次返回新 ScriptedLLM（turns 从头重放）；
                        # 生产路径 cfg 每 run 只取一次，重建 OpenAICompatLLM 语义等价
                        from app.agent.model_resolver import resolve_model
                        cfg_now = await config_getter()
                        model_cfg = await resolve_model(app.state.session_factory, session_id, cfg_now)
                        llm = llm_factory(cfg_now, model_cfg)
                        runner = AgentRunner(app.state.session_factory, llm,
                                             config_getter, send, hub.ask, hub.confirm,
                                             hub.pop_pending)
                        await runner.run(session_id, user_text_, cancel)
                    finally:
                        hub.running.discard(session_id)
                        hub.cancel_events.pop(session_id, None)

                asyncio.create_task(run_task(text_))
            elif t == "ask_submit":
                await hub.submit_answers(session_id, msg.get("answers") or [])
            elif t == "confirm":
                await hub.resolve_confirm(session_id, bool(msg.get("approved")))
            elif t == "cancel":
                hub.cancel(session_id)
            elif t == "compact":
                if session_id in hub.running:
                    await emit(session_id, {"type": "busy", "message": "会话正在执行中，无法压缩"})
                    continue

                async def send_local(event: dict):
                    # 基线适配（控制方裁定）：hub 已是 per-session emit 槽（d86f9b5），
                    # brief 中 hub.emit 单槽判断落为 emit_event 定向下发
                    await hub.emit_event(session_id, event)

                async def do_compact():
                    hub.running.add(session_id)
                    hub.compacting.add(session_id)  # 压缩无工具边界，插话要区别拒绝
                    try:
                        await send_local({"type": "compacting"})
                        cfg = await config_getter()
                        llm = llm_factory(cfg)
                        keep = int(cfg.get("agent.compact_keep_recent", 8))
                        await compaction_service.compact(
                            app.state.session_factory, llm, session_id, send_local, keep)
                    finally:
                        hub.running.discard(session_id)
                        hub.compacting.discard(session_id)

                asyncio.create_task(do_compact())
    except WebSocketDisconnect:
        hub.drop_emit(session_id, emit)  # 断连清槽防僵尸（仅清自己注入的槽）
        pass
