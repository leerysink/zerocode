"""WS 聊天端点 /ws/chat/{session_id}。

上行：user_message / ask_submit / confirm / cancel / compact；
下行：token / tool_call / tool_result / tool_progress / ask_user / confirm_request /
message_done / error / status / file_change / compacting / compaction_done / busy /
steering_done（由 runner 与 hub 经 emit 下发）/ interaction_expired（confirm、ask_submit 上行时
后端已无挂起点且该会话不在跑，让前端把幽灵卡收口成已中断）。tool_progress 是预览工具的**临时**进度，
不落库、刷新即消失；error 与 status 是本轮终态；message_done 是**轮次收口**而非终态
（工具轮在派发工具前也发一次，一次执行可以有多条，判终态一律看 status）；busy 是执行中被拒的非终态提示
（不得据此复位前端执行态，否则「停止」按钮消失 → 发不出也停不掉的死循环）。

执行中再发 user_message = 插话（steering）：进 hub.pending 队列（对象项：content +
attachment_ids + client_msg_id），由 runner 在下一个工具边界落库绑定附件并回
steering_done（带 client_msg_id）；压缩中、队列排满或图片插话撞上不支持视觉的本 run
模型仍回 busy（都不落库）。busy 一律带 client_msg_id 与 running（是否仍在执行），
前端据此只摘掉那条乐观气泡而不复位执行态。

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
from app.agent.model_resolver import resolve_model
from app.agent.preview_tools import (DEFAULT_CONFIRM_RUNNING, DEFAULT_LOG_LINES,
                                     DEFAULT_MAX_RESTARTS, DEFAULT_WAIT_SECONDS)
from app.services import attachment_service
from app.services import compaction_service
from app.services import config_service

router = APIRouter()


def _parse_attachment_ids(raw_ids) -> list[int]:
    """附件 id 严格解析：只收 JSON 整数与纯数字字符串（ASCII），bool/float/其它一律 ValueError。

    int() 宽松强转不可用：true 会悄悄变 1、3.5 被截成 3，协议错误被伪装成「附件不存在」。
    去重保序（先出现的保留位置）。
    """
    out: list[int] = []
    for i in raw_ids:
        if isinstance(i, bool):
            raise ValueError(f"非法附件 id: {i!r}")
        if isinstance(i, int):
            v = i
        elif isinstance(i, str) and i.isascii() and i.isdigit():
            v = int(i)
        else:
            raise ValueError(f"非法附件 id: {i!r}")
        if v not in out:
            out.append(v)
    return out


@router.websocket("/ws/chat/{session_id}")
async def ws_chat(ws: WebSocket, session_id: int):
    # WS 认证收口（Ticket 009 / spec §3 故事12）：/ws scope 不走 HTTP guard 中间件，
    # 这里 accept 前解析 zc_actor cookie → by_token，未登录拒 4401；
    # 会话经 workspace 归属他人拒 4404（HTTP 侧越权统一 404 的 WS 映射，不暴露存在性）。
    # 拒绝用 accept+close 而非 accept 前 close：uvicorn 会把后者转成 HTTP 403，
    # close code 被吞（浏览器只见 1006），前端 onclose 按 4401 跳登录就无从判断。
    # 校验本身在 accept 前完成：被拒连接从不注册 emit 槽/不触任何业务。
    from app.store import identity
    actor = await identity.by_token(
        ws.app.state.session_factory, ws.cookies.get(identity.ACTOR_COOKIE))
    if actor is None:
        await ws.accept()
        await ws.close(code=4401)
        return
    async with ws.app.state.session_factory() as s:
        owner = (await s.execute(_sql(
            "SELECT w.owner_user_id FROM chat_session cs "
            "JOIN workspace w ON w.id = cs.workspace_id WHERE cs.id=:i"),
            {"i": session_id})).scalar()
    if owner != actor["id"]:
        await ws.accept()
        await ws.close(code=4404)
        return

    await ws.accept()
    app = ws.app
    hub = app.state.hub

    async def emit(sid: int, event: dict):
        try:
            await ws.send_text(json.dumps(event, ensure_ascii=False))
        except Exception:
            pass  # 连接已断开等场景：静默丢弃（单用户场景无回压需求）

    async def reject(message: str, client_msg_id=None, running: bool = False):
        """非终态拒绝：带 client_msg_id（前端摘掉乐观气泡）与 running
        （false=根本没开跑，可解锁；true=仍在执行中，保持「停止」按钮）。"""
        await emit(session_id, {"type": "busy", "message": message,
                                "client_msg_id": client_msg_id, "running": running})

    # 按会话分槽注入：同会话后连覆盖，跨会话互不影响（终审修复轮2）
    hub.set_emit(session_id, emit)
    # 重连状态自洽：连入时该会话仍在跑（刷新页面/断线重连/另开标签页），补发一帧
    # status(running) 快照，让前端把「停止」按钮亮回来。不补则前端 openSession 一律
    # 视为空闲 → 输入框解锁而后端仍在执行，用户每发一句都撞拒绝（死循环根因之一）。
    if session_id in hub.running:
        await emit(session_id, {"type": "status", "status": "running"})
    # 挂起中的提问/确认在重连时补发：切走、刷新、另开标签页后卡片不消失，
    # ask/confirm future 不会永久挂起（否则会话卡死，用户既答不了也停不掉）。
    await hub.replay_pending(session_id)

    async def config_getter():
        # 用户视图（Ticket 007）：会话经 workspace 取 owner，llm.* 读自己的/回落全局；
        # agent.* 等平台级键所有人共享，owner 为 NULL 时等价纯全局视图
        async with app.state.session_factory() as s:
            uid = (await s.execute(_sql(
                "SELECT w.owner_user_id FROM chat_session cs "
                "JOIN workspace w ON w.id = cs.workspace_id WHERE cs.id=:i"),
                {"i": session_id})).scalar()
            cfg = await config_service.get_all(s, owner_user_id=uid)
        # 预览工具的平台级默认值（规格 2026-10-29 §5.9）：与 agent.max_tool_rounds 同一读法——
        # 库里存了就以库为准，没存才补代码默认值。默认值只有 `preview_tools` 的 DEFAULT_* 一份字面量：
        # `build_preview_prompt` 会把 DEFAULT_WAIT_SECONDS 写进 system 文案，这里再抄一遍数字的话，
        # 改常量时真正生效的仍是 ws 这份 setdefault，而模型读到的是旧数字（先被骗的是模型）
        cfg.setdefault("agent.preview_wait_seconds", DEFAULT_WAIT_SECONDS)
        cfg.setdefault("agent.preview_log_lines", DEFAULT_LOG_LINES)
        cfg.setdefault("agent.preview_max_restarts", DEFAULT_MAX_RESTARTS)
        # 用 setdefault 而不是 `cfg.get(key) or DEFAULT_CONFIRM_RUNNING`：后者会把库里 JSON 的
        # false 当成「没有值」吃掉，等于这个「中断运行中的预览要先确认」的开关永远关不掉
        cfg.setdefault("agent.preview_confirm_running", DEFAULT_CONFIRM_RUNNING)
        return cfg

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
                cid = msg.get("client_msg_id")
                text_ = (msg.get("content") or "").strip()
                raw_ids = msg.get("attachment_ids") or []
                if not isinstance(raw_ids, list):
                    await reject("附件参数非法", cid, running=session_id in hub.running)
                    continue
                try:
                    attachment_ids = _parse_attachment_ids(raw_ids)
                except (TypeError, ValueError):
                    await reject("附件参数非法", cid, running=session_id in hub.running)
                    continue
                if not text_ and not attachment_ids:
                    continue  # 空消息（无字无图）不打断也不回帧
                if len(attachment_ids) > attachment_service.MAX_ATTACHMENTS_PER_MESSAGE:
                    await reject(f"单条消息最多 {attachment_service.MAX_ATTACHMENTS_PER_MESSAGE} 张图片",
                                 cid, running=session_id in hub.running)
                    continue

                if session_id in hub.running:
                    # 执行中再发一句 = **插话（steering）**：入队等下一个工具边界注入，
                    # 不另起一轮 run、也不再当错误拒收（历史上回 error 造成过
                    # 「发不出也停不掉」的死循环，见 a0cecb1）。
                    if session_id in hub.compacting:
                        # 压缩没有工具边界可注入，也没有模型会读它 —— 仍然拒收（非终态）
                        await reject("上下文压缩中，请稍候", cid, running=True)
                        continue
                    if attachment_ids:
                        # 能力门按**本 run 实际模型**判定（不是当前默认模型）：
                        # run 跑的是启动时解析的那个模型，中途换默认不该让插话混进读不懂图的上下文
                        if hub.run_vision.get(session_id) is not True:
                            await reject("当前模型不支持图片输入，请切换支持视觉的模型",
                                         cid, running=True)
                            continue
                        try:
                            await attachment_service.load_bindable(
                                app.state.session_factory, session_id, attachment_ids)
                        except attachment_service.AttachmentError as e:
                            await reject(str(e), cid, running=True)
                            continue
                    cfg_now = await config_getter()
                    cap = int(cfg_now.get("agent.steering_queue_max", 20) or 20)
                    item = {"content": text_, "attachment_ids": attachment_ids,
                            "client_msg_id": cid}
                    if not hub.queue(session_id, item, cap):
                        await reject(f"插话已排满（上限 {cap} 条），请等本轮跑完或点「停止」",
                                     cid, running=True)
                    continue

                # 新起一轮：图片消息在**建任务前**完成预校验（绑定资格 + 视觉能力），
                # 校验失败直接拒、不落库不置 running；解析结果顺带传给任务复用，避免重复查库
                cfg_now = None
                model_cfg = None
                if attachment_ids:
                    # 预校验含多个 await（load_bindable/config/resolve_model），期间若还不占
                    # 运行位，第二条 user_message（另一连接）会看到「未在跑」再开一个 run
                    # （双跑竞态）。故先预留运行位：任何拒绝/异常路径都要摘掉，成功路径不摘，
                    # 由 run_task 首行（幂等 add）接管。
                    hub.running.add(session_id)
                    try:
                        try:
                            await attachment_service.load_bindable(
                                app.state.session_factory, session_id, attachment_ids)
                        except attachment_service.AttachmentError as e:
                            hub.running.discard(session_id)
                            await reject(str(e), cid, running=False)
                            continue
                        cfg_now = await config_getter()
                        model_cfg = await resolve_model(
                            app.state.session_factory, session_id, cfg_now)
                        if not model_cfg.get("supports_vision"):
                            hub.running.discard(session_id)
                            await reject("当前模型不支持图片输入，请切换支持视觉的模型",
                                         cid, running=False)
                            continue
                    except Exception:
                        # 意外异常（含上面 reject 自身失败）：摘掉预留位再抛，不留幽灵 running
                        hub.running.discard(session_id)
                        raise

                async def send(event: dict):
                    # AgentRunner.send 是单参签名；经 hub 会话槽晚绑定转发，
                    # 同会话后连覆盖槽后事件自动切到新连接（见 busy 测试语义）
                    await hub.emit_event(session_id, event)

                async def run_task(user_text_: str, attachment_ids_: list[int],
                                   cfg_now_, model_cfg_):
                    # 裁定 R3：hub.running 必须在任何 await 之前置位。若等 cfg/模型解析
                    # 完再加，解析期间到达的第二条 user_message 会看到「未在跑」而再开一个
                    # run（双跑竞态）。status 落库仍紧随其后，保持 watchdog 语义。
                    hub.running.add(session_id)
                    if model_cfg_ is not None:  # 图片消息已预解析：先亮能力位再等首个 await
                        hub.run_vision[session_id] = bool(model_cfg_.get("supports_vision"))
                    cancel = asyncio.Event()
                    hub.cancel_events[session_id] = cancel
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
                        if cfg_now_ is None:
                            cfg_now_ = await config_getter()
                        if model_cfg_ is None:
                            model_cfg_ = await resolve_model(
                                app.state.session_factory, session_id, cfg_now_)
                        # 本 run 实际模型的能力位：执行中的图片插话按这个放行/拒绝，
                        # 结束时清掉（下次 run 重新解析）
                        hub.run_vision[session_id] = bool(model_cfg_.get("supports_vision"))
                        # LLM 按 run 解析一次：runner 对 callable 每轮重建实例，
                        # 而测试注入工厂每次返回新 ScriptedLLM（turns 从头重放）；
                        # 生产路径 cfg 每 run 只取一次，重建 OpenAICompatLLM 语义等价
                        llm = llm_factory(cfg_now_, model_cfg_)
                        runner = AgentRunner(app.state.session_factory, llm,
                                             config_getter, send, hub.ask, hub.confirm,
                                             hub.pop_pending,
                                             deploy_hub=app.state.deploy_hub,
                                             dev_tasks=app.state.dev_tasks,
                                             model_cfg=model_cfg_)
                        await runner.run(session_id, user_text_, cancel,
                                         attachment_ids=attachment_ids_)
                    finally:
                        hub.run_vision.pop(session_id, None)
                        hub.running.discard(session_id)
                        hub.cancel_events.pop(session_id, None)

                asyncio.create_task(run_task(text_, attachment_ids, cfg_now, model_cfg))
            elif t == "ask_submit":
                resolved = await hub.submit_answers(session_id, msg.get("answers") or [])
                if not resolved and session_id not in hub.running:
                    await emit(session_id, {"type": "interaction_expired"})
            elif t == "confirm":
                resolved = await hub.resolve_confirm(session_id, bool(msg.get("approved")))
                # 挂起点不在（进程重启/执行早已收口）且确实没在跑，才回帧收口幽灵卡：
                # 少这一帧的话前端点「确认写入」零反馈，卡片永远停在可点状态。
                # 仍在跑时不回：那多半是连点第二下，真实结果正在路上，回帧会把好卡打成已中断。
                if not resolved and session_id not in hub.running:
                    await emit(session_id, {"type": "interaction_expired"})
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
