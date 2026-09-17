"""WS 聊天端点 /ws/chat/{session_id}。

上行：user_message / ask_answer / ask_choice / confirm / cancel；
下行：token / tool_call / tool_result / ask_user / confirm_request /
message_done / error / status / file_change（由 runner 与 hub 经 emit 下发）。

按会话分槽：同会话后连连接覆盖该会话的 emit 槽，下行事件随之切到新连接；
断连 drop 自己的槽（防僵尸槽），跨会话互不串扰（终审修复轮2）。
runner 的 send 为单参签名，这里经 hub 会话槽晚绑定转发；LLM 实例按 run
解析一次（cfg 本就每 run 取一次，避免测试注入的工厂每轮重放轮次）。
"""
import asyncio, json
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import text as _sql
from app.agent.llm import OpenAICompatLLM
from app.agent.loop import AgentRunner
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

    async def config_getter():
        async with app.state.session_factory() as s:
            return await config_service.get_all(s)

    def llm_factory(cfg):
        if hasattr(app.state, "llm_factory"):
            return app.state.llm_factory(cfg)
        return OpenAICompatLLM(cfg.get("llm.base_url"), cfg.get("llm.api_key"), cfg.get("llm.model"))

    try:
        while True:
            msg = json.loads(await ws.receive_text())
            t = msg.get("type")
            if t == "user_message":
                if session_id in hub.running:
                    await emit(session_id, {"type": "error", "message": "会话正在执行中"})
                    continue
                text_ = (msg.get("content") or "").strip()
                if not text_:
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
                        llm = llm_factory(await config_getter())
                        runner = AgentRunner(app.state.session_factory, llm,
                                             config_getter, send, hub.ask, hub.confirm)
                        await runner.run(session_id, user_text_, cancel)
                    finally:
                        hub.running.discard(session_id)
                        hub.cancel_events.pop(session_id, None)

                asyncio.create_task(run_task(text_))
            elif t == "ask_answer":
                await hub.answer(session_id, msg.get("text", ""))
            elif t == "ask_choice":
                await hub.choose(session_id, msg.get("option", ""))
            elif t == "confirm":
                await hub.resolve_confirm(session_id, bool(msg.get("approved")))
            elif t == "cancel":
                hub.cancel(session_id)
    except WebSocketDisconnect:
        hub.drop_emit(session_id, emit)  # 断连清槽防僵尸（仅清自己注入的槽）
        pass
