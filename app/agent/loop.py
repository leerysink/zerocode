"""Agent 循环：流式对话 + 工具调度 + ask_user 挂起恢复 + 写入确认 + 取消。

事件协议（send 下发）：token/tool_call/tool_result/file_change/message_done/error，
另有 confirm_request（写入确认请求）、status（终态回执）、compaction_done（自动压缩完成）
与 session_title（首条消息自动命名后的新标题，前端就地刷新侧栏）。
"""
import asyncio, datetime, inspect, itertools, json
from pathlib import Path
from app.agent.prompts import build_system_prompt
from app.agent.tools import ToolContext, ToolError, execute_tool
from app.agent.mentions import expand_mentions
from app.agent.redact import redact_tool_calls_args, redact_tool_result
from app.services import compaction_service as _comp
from app.services import memory_service as _mem
from app.services.message_service import add_message
from app.services.session_title_service import ensure_session_title
from sqlalchemy import text

def _normalize_questions(args: dict) -> list[dict]:
    """新协议 questions 优先；兼容旧单题参数（question/options 字符串）。"""
    qs = args.get("questions")
    if isinstance(qs, list) and qs:
        return qs
    q = args.get("question")
    if isinstance(q, str) and q:
        opts = [{"label": o} if isinstance(o, str) else o for o in (args.get("options") or [])]
        return [{"question": q, "options": opts}]
    raise ToolError("缺少 question(s)")

class AgentRunner:
    def __init__(self, session_factory, llm, config_provider, send, ask, confirm, pop_pending=None):
        self.sf = session_factory
        self.llm = llm
        self.get_config = config_provider
        self.send = send
        self.ask = ask
        self.confirm = confirm
        # 执行中用户插话（steering）的取空回调：async (session_id) -> list[dict]，
        # 项为 {content, attachment_ids, client_msg_id}（兼容裸字符串）；
        # 缺省 None = 不支持插话（旧调用方与单测保持原签名可用）
        self.pop_pending = pop_pending
        # 本轮历史已内联的图片原始字节合计（_history 构建时写入）：插话带图与历史共用
        # 同一份 MAX_INLINE_BYTES 预算，否则一次运行的内联总量可以越过上限
        self._inline_used = 0

    async def _root_of(self, session_id: int):
        async with self.sf() as s:
            row = (await s.execute(text(
                "SELECT w.root_path FROM chat_session cs JOIN workspace w ON w.id = cs.workspace_id WHERE cs.id = :i"),
                {"i": session_id})).first()
        return Path(row[0])  # DB 返回 str；工具层与 send/file_change 均需 Path（f-string 渲染结果不变）

    async def _enabled_skills_of(self, session_id: int) -> list[dict]:
        """session → workspace.owner_user_id → 该用户已启用技能清单（spec §5 prompt 注入）。

        一条 join 反查归属再取 enabled 技能；owner 为 NULL 的历史工作区自然查不到，
        返回空（不注入空块）。技能目录绝对路径直接给 skill_dir，Agent 用 read_file 读全文。
        """
        async with self.sf() as s:
            rows = (await s.execute(text(
                "SELECT us.name, us.description, us.skill_dir FROM user_skill us "
                "JOIN workspace w ON w.owner_user_id = us.owner_user_id "
                "JOIN chat_session cs ON cs.workspace_id = w.id "
                "WHERE cs.id = :i AND us.enabled ORDER BY us.id"),
                {"i": session_id})).mappings().all()
        return [{"name": r["name"], "description": r["description"], "path": r["skill_dir"]}
                for r in rows]

    async def _skills_root_of(self, session_id: int):
        """会话所属用户的技能母本根（受控读面）：session → workspace.owner_user_id。

        owner 为 NULL 的历史工作区返回 None（无读面，与不注入技能一致）。
        """
        async with self.sf() as s:
            uid = (await s.execute(text(
                "SELECT w.owner_user_id FROM chat_session cs JOIN workspace w ON w.id = cs.workspace_id "
                "WHERE cs.id = :i"), {"i": session_id})).scalar()
        if uid is None:
            return None
        from app.skills.library import skills_root
        return skills_root(uid)

    async def run(self, session_id: int, user_text: str, cancel: asyncio.Event,
                  attachment_ids: list[int] | None = None) -> str:
        status = "idle"
        try:
            await self._inner(session_id, user_text, cancel, attachment_ids)
        except _Cancelled:
            await self.send({"type": "error", "message": "已取消"})
            status = "idle"
        except Exception as e:
            await self.send({"type": "error", "message": str(e)})
            status = "error"
        finally:
            await self._set_status(session_id, status)
            await self.send({"type": "status", "status": status})
        return status

    async def _set_status(self, session_id, status):
        async with self.sf() as s:
            await s.execute(text("UPDATE chat_session SET status=:st, updated_at=now() WHERE id=:i"),
                            {"st": status, "i": session_id})
            await s.commit()

    async def _history(self, session_id: int, window: int) -> tuple[list[dict], str | None]:
        # 水位过滤（Task 8）：已压缩摘要覆盖 seq≤wm 的旧消息，窗口只取 wm 之后的原文
        summary, wm = await _comp.latest(self.sf, session_id)
        async with self.sf() as s:
            rows = (await s.execute(text(
                "SELECT id, role, COALESCE(context_content, content) AS content, "
                "tool_call_id, tool_name, tool_args, tool_result "
                "FROM message WHERE session_id=:i AND seq > :w ORDER BY seq DESC LIMIT :win"),
                {"i": session_id, "w": wm, "win": window})).mappings().all()
        rows = list(reversed(rows))
        # 附件只回放最近两条带图用户消息：内联原图对 token/带宽都太重，更早的退化为
        # 文字占位（文件名仍可见，模型知道自己漏看了什么）。当前消息（窗口最后一条
        # user）永远保留——它正是本轮要回答的对象，预算只能挤掉更早的。
        from app.services import attachment_service as _att
        user_ids = [r["id"] for r in rows if r["role"] == "user"]
        meta = await _att.for_messages(self.sf, session_id, user_ids)
        with_atts = [mid for mid in user_ids if meta.get(mid)]
        inline = set(with_atts[-2:])
        self._inline_used = 0
        files = await _att.for_llm(self.sf, sorted(inline)) if inline else {}
        if inline:
            current_id = user_ids[-1] if user_ids else None

            def _inline_bytes(mid):
                # 只算真正会内联的图（文件仍在、data_url 非空）：外部删掉的文件既不挤占
                # 预算，也不该把另一张有效的旧图挤成占位
                return sum(a["size_bytes"] for a in files.get(mid, []) if a.get("data_url"))

            total = sum(_inline_bytes(mid) for mid in inline)
            while total > _att.MAX_INLINE_BYTES:
                droppable = [mid for mid in with_atts if mid in inline and mid != current_id]
                if not droppable:
                    break
                old = droppable[0]
                inline.discard(old)
                total -= _inline_bytes(old)
            # 本 run 后续插话带图要用这份账本（见 _drain_steering）
            self._inline_used = total
            files = {mid: atts for mid, atts in files.items() if mid in inline}
        msgs = []
        for r in rows:
            if r["role"] == "user":
                msgs.append({"role": "user", "content": self._user_content(
                    r["content"], meta.get(r["id"], []), r["id"] in inline,
                    files.get(r["id"], []))})
            elif r["role"] == "assistant":
                m = {"role": "assistant", "content": r["content"]}
                if r["tool_args"] and "tool_calls" in r["tool_args"]:
                    m["tool_calls"] = r["tool_args"]["tool_calls"]
                msgs.append(m)
            elif r["role"] == "tool":
                msgs.append({"role": "tool", "tool_call_id": r["tool_call_id"],
                             "content": json.dumps(r["tool_result"], ensure_ascii=False)})
        # 修剪窗口头部拆散的 assistant(tool_calls)/tool 消息对：孤儿 tool 消息或
        # 无结果的 tool_calls 会让 OpenAI 兼容 API 返回 400；从头部逐条丢弃，
        # 直到头部干净或列表为空（数据在内存且 N≤40，O(N²) 可接受）
        while msgs:
            head = msgs[0]
            if head["role"] == "tool":
                if not any(m["role"] == "assistant" and any(
                        tc.get("id") == head["tool_call_id"] for tc in (m.get("tool_calls") or []))
                        for m in msgs):
                    msgs.pop(0)
                    continue
            elif head["role"] == "assistant" and head.get("tool_calls"):
                kept_tools = {m["tool_call_id"] for m in msgs if m["role"] == "tool"}
                if not all(tc.get("id") in kept_tools for tc in head["tool_calls"]):
                    msgs.pop(0)
                    continue
            break
        return msgs, summary

    @staticmethod
    def _user_content(text_val, meta_atts, inline: bool, file_atts) -> str | list:
        """单条用户消息 → LLM content：无附件保持字符串；有图拼 OpenAI 多模态 parts。

        内联时 text 头 + 每张图的 data_url；文件缺失（data_url 为 None）只留
        「[图片缺失：名]」文字，不发对应 image_url part——坏 part 会让整轮请求 400。
        占位分支反向告知模型「这里曾有图」，避免它把文件名当工作区路径去 read_file。
        """
        text_val = text_val or ""
        if not meta_atts:
            return text_val
        if not inline:
            names = [a["filename"] for a in meta_atts]
            note = f"[图片 ×{len(names)}：{', '.join(names)}（较早的图片已省略）]"
            return f"{text_val}\n\n{note}" if text_val else note
        by_id = {a["id"]: a for a in file_atts}
        usable = [by_id[a["id"]] for a in meta_atts if by_id.get(a["id"], {}).get("data_url")]
        missing = [a["filename"] for a in meta_atts if not by_id.get(a["id"], {}).get("data_url")]
        head = text_val if text_val.strip() else ("[图片]" if usable else "")
        if missing:
            head = (head + "\n" if head else "") + "[图片缺失：" + "，".join(missing) + "]"
        parts = [{"type": "text", "text": head or "[图片]"}]
        parts.extend({"type": "image_url", "image_url": {"url": a["data_url"]}} for a in usable)
        return parts

    async def _llm_for(self, cfg):
        llm = self.llm(cfg) if callable(self.llm) else self.llm
        if inspect.isawaitable(llm):
            llm = await llm
        return llm

    async def _build_messages(self, session_id: int, root: Path, cfg: dict, send,
                              user_text: str = "") -> list[dict]:
        """自动压缩（阈值+最小新增双条件，thr=0 关闭）→ 摘要注入 system 尾部 → 水位后窗口消息。"""
        summary, wm = await _comp.latest(self.sf, session_id)
        rows = await _comp.rows_after(self.sf, session_id, wm)
        thr = int(cfg.get("agent.auto_compact_chars", 50000))
        min_new = int(cfg.get("agent.compact_min_new", 6))
        keep = int(cfg.get("agent.compact_keep_recent", 8))
        if thr > 0 and len(rows) >= min_new and len(rows) > keep and _comp.estimate_chars(rows) > thr:
            llm = await self._llm_for(cfg)
            res = await _comp.compact(self.sf, llm, session_id, send, keep)
            if res:
                summary = res["summary"]
        history, _ = await self._history(session_id, int(cfg.get("agent.history_window", 40)))
        from app.deploy.contract import build_app_contract
        skills = await self._enabled_skills_of(session_id)
        system_text = build_system_prompt(str(root), datetime.date.today().isoformat(),
                                          app_contract=build_app_contract(root), skills=skills)
        if summary:
            system_text += f"\n\n<context_summary>\n{summary}\n</context_summary>"
        # 当前任务清单回灌：todo_write 调用会被历史窗口截掉，模型跨运行就"看不见"清单，
        # 无从"随做随更"；todo_state 是为 UI 持久化的，这里同一事实喂回模型
        async with self.sf() as s:
            todos = (await s.execute(text(
                "SELECT todo_state FROM chat_session WHERE id=:i"), {"i": session_id})).scalar()
        if todos:
            system_text += ("\n\n<current_todos>\n"
                            + json.dumps(todos, ensure_ascii=False)
                            + "\n</current_todos>")
        # 工作区记忆回灌（ticket 002 / D-7，收口见 006）：以当前任务文本为查询做召回，
        # 空命中不注空块（故事 14）——宁可不注入，也不能让空标签污染 system。
        # cfg 直接复用本轮已解析的那份：开关/阈值/预算的口径必须和其余 agent.* 配置同源。
        # 纯图片消息（无文字）没有召回查询词，直接用空文本召回只会命中噪声/报错，
        # 故短路为空（不注块）。
        memories = ([] if not (user_text or "").strip()
                    else await self._memories_of(session_id, user_text, cfg))
        if memories:
            system_text += ("\n\n<memories>\n"
                            + "\n".join(self._memory_line(m) for m in memories)
                            + "\n</memories>")
        return [{"role": "system", "content": system_text}] + history

    async def _memories_of(self, session_id: int, user_text: str,
                           cfg: dict | None = None) -> list[dict]:
        """session → workspace 反查后按当前任务文本召回；任一环为空就返回空（不注块）。

        召回走服务层的唯一接缝 `recall_memories`：开关、相似度阈值、topK、token 封顶和
        向量/关键词降级都在那里面，本方法只负责反查 workspace 和把 cfg 递过去。
        cfg 由本轮传入（006）：loop 已经解析过 owner 视角的配置，服务层再查一次 app_config
        是白的一次往返，还会出现两份真值——改配置后本轮生效与否取决于谁先查到。
        """
        ws = await _mem.workspace_id_of_session(self.sf, session_id)
        if ws is None:
            return []
        return await _mem.recall_memories(self.sf, ws, user_text, cfg=cfg)

    @staticmethod
    def _memory_line(m: dict) -> str:
        """注入行格式委托服务层：预算估算与实际注入必须同源（006）。"""
        return _mem.format_memory_line(m)

    async def _drain_steering(self, session_id: int, root: Path, messages: list[dict]) -> int:
        """注入执行中的用户插话（steering）：落库为普通 user 消息 + 追加到本轮 messages。

        只在**工具边界**调用（一轮开始时工具结果已全部入列），绝不能插在 assistant(tool_calls)
        与它的 tool 结果之间 —— 拆散那一对会让 OpenAI 兼容接口直接 400。
        队列项是对象 {content, attachment_ids, client_msg_id}（兼容老的裸字符串）：
        带图插话与普通消息同一条路径落库绑定附件，再按本 run 的视觉能力渲染给模型。
        注入后回 steering_done（带 client_msg_id），前端据此摘掉「排队中」角标并校验送达。
        返回注入条数（调用方据此判断"还要不要再走一轮"）。
        """
        from app.services import attachment_service as _att
        if not self.pop_pending:
            return 0
        injected = 0
        for item in await self.pop_pending(session_id):
            if isinstance(item, str):
                item = {"content": item, "attachment_ids": [], "client_msg_id": None}
            text_ = (item.get("content") or "").strip()
            ids = item.get("attachment_ids") or []
            if not text_ and not ids:
                continue
            expanded = expand_mentions(root, text_) if text_ else None
            try:
                row = await add_message(self.sf, session_id, "user", content=text_,
                                        context_content=expanded, attachment_ids=ids)
            except ValueError as e:
                # 附件在排队期间被占用/删除：这句插话无法完整送达，单独提示并继续，
                # 不能连累队列里后面的插话，也不能让整轮 run 崩掉。必须用**非终态**
                # busy：error 会被前端当本轮终态清掉执行态，而这轮其实还在跑
                await self.send({"type": "busy", "message": f"插话未能送达：{e}",
                                 "client_msg_id": item.get("client_msg_id"),
                                 "running": True})
                continue
            atts = row.get("attachments") or []
            need = sum(a["size_bytes"] for a in atts)
            # 插话与历史共用一份内联预算（_history 已把本轮历史内联字节记入 _inline_used）：
            # 超预算就降级成「较早的图片已省略」占位，绝不让一次运行的内联总量越过
            # MAX_INLINE_BYTES。送达不受影响——消息照常落库，steering_done 照常下发。
            if atts and self._inline_used + need <= _att.MAX_INLINE_BYTES:
                files = await _att.for_llm(self.sf, [row["id"]])
                self._inline_used += need
                # @展开要进模型可见内容（与主消息路径一致），否则插话里的 "@a.py" 只带原文
                content = self._user_content(expanded or text_, atts, True,
                                             files.get(row["id"], []))
            else:
                content = self._user_content(expanded or text_, atts, False, {})
            messages.append({"role": "user", "content": content})
            await self.send({"type": "steering_done", "content": text_,
                             "client_msg_id": item.get("client_msg_id")})
            injected += 1
        return injected

    async def _inner(self, session_id: int, user_text: str, cancel: asyncio.Event,
                     attachment_ids: list[int] | None = None):
        cfg = await self.get_config()
        root = await self._root_of(session_id)
        # 先消费上一次 run 停止/收尾前没来得及注入的插话，再落本次新问题：
        # 保证 DB seq 与对话顺序一致（插话发生在先，新问题在后）
        await self._drain_steering(session_id, root, [])
        expanded = expand_mentions(root, user_text)
        row = await add_message(self.sf, session_id, "user", content=user_text,
                                context_content=expanded, attachment_ids=attachment_ids)
        # 自动命名（2026-09-21）：首条用户消息落库后，仍叫默认「新会话」就按内容派生改名
        # （条件更新 WHERE title=默认：用户手动改过名的永不覆盖）；纯图片消息用首个附件
        # 文件名兜底；改名随即下发 session_title 供侧栏就地刷新，不等重拉列表。
        titled = await ensure_session_title(
            self.sf, session_id, user_text,
            [a["filename"] for a in row.get("attachments") or []])
        if titled:
            await self.send({"type": "session_title", "title": titled})
        messages = await self._build_messages(session_id, root, cfg, self.send, user_text)
        ctx = ToolContext(root=root, session_id=session_id, session_factory=self.sf, send=self.send,
                          skills_root=await self._skills_root_of(session_id))
        max_rounds = int(cfg.get("agent.max_tool_rounds", 30))
        # <=0 = 不限工具轮数（与超时参数 <=0 不限制的约定一致），不再 range(0) 零轮即错
        rounds = itertools.count() if max_rounds <= 0 else range(max_rounds)
        for _round in rounds:
            if cancel.is_set():
                raise _Cancelled()
            # 边界注入：用户在我们跑工具时说的话，模型下一轮就看得见
            await self._drain_steering(session_id, root, messages)
            llm = await self._llm_for(cfg)
            final = None
            async for ev in llm.chat_stream(messages, _TOOLS):
                if cancel.is_set():
                    raise _Cancelled()
                if ev["type"] == "reasoning_delta":
                    await self.send(ev)
                elif ev["type"] == "content_delta":
                    await self.send({"type": "token", "delta": ev["content"]})
                elif ev["type"] == "done":
                    final = ev["message"]
            if final.get("tool_calls"):
                # 凭据遮断（Ticket 010）：三个持久/展示出口（落库、回注、事件推送）共用
                # 同一份净化后的 tool_calls；工具执行 _dispatch 仍收原始 args——会话历史防
                # 泄露 ≠ 破坏工具本意，fs_write 之类得能把用户真正要写的凭据落盘。
                original_calls = final["tool_calls"]
                sanitized_calls, _ = redact_tool_calls_args(original_calls)
                final["tool_calls"] = sanitized_calls
                await add_message(self.sf, session_id, "assistant", content=final.get("content"),
                                  tool_args={"tool_calls": sanitized_calls})
                messages.append({"role": "assistant", "content": final.get("content"),
                                 "tool_calls": sanitized_calls})
                # 结构被 redact 原样保留（id/type/name 不变、只改 arguments），按位次配对：
                # exec_tc 供工具真实执行，push_tc 供前端事件展示。等长同序是配对成立的前提，
                # 加一道廉价护栏：万一将来给 redact 加了过滤分支致列表变短，在这里显式炸，
                # 不要静默丢一个工具调用 / 错位 tool_call_id 配对（OpenAI 兼容 API 会 400）。
                assert len(sanitized_calls) == len(original_calls)
                for exec_tc, push_tc in zip(original_calls, sanitized_calls):
                    if cancel.is_set():
                        raise _Cancelled()
                    name = exec_tc["function"]["name"]
                    try:
                        exec_args = json.loads(exec_tc["function"]["arguments"] or "{}")
                    except json.JSONDecodeError:
                        exec_args = {}
                    try:
                        push_args = json.loads(push_tc["function"]["arguments"] or "{}")
                    except json.JSONDecodeError:
                        push_args = {}
                    await self.send({"type": "tool_call", "name": name, "args": push_args})
                    result = await self._dispatch(ctx, cfg, session_id, name, exec_args, cancel)
                    # 凭据遮断（Ticket 010 第五出口）：tool_result 落库 / WS 推送走净化版，
                    # 但**当轮回注模型仍用原文**——read_file 的真实内容必须进 messages，
                    # 否则模型看不到文件、edit_file 构造不出 old_string。平台侧库/事件不留
                    # 明文；跨轮从净化库重建历史，回注自然无明文（要再看可 read_file 重读）。
                    sanitized_result = redact_tool_result(result)
                    await add_message(self.sf, session_id, "tool", tool_call_id=exec_tc.get("id"),
                                      tool_name=name, tool_result=sanitized_result)
                    await self.send({"type": "tool_result", "name": name, "result": sanitized_result})
                    messages.append({"role": "tool", "tool_call_id": exec_tc.get("id"),
                                     "content": json.dumps(result, ensure_ascii=False)})
                continue
            await add_message(self.sf, session_id, "assistant", content=final.get("content"),
                              reasoning=final.get("reasoning"))
            await self.send({"type": "message_done", "content": final.get("content")})
            # 答复已出，但期间用户还在说话：不能等下一次提问才被看见 —— 答复先落库/入列，
            # 插话紧随其后，再走一轮让模型回应（队列空了就正常收工）
            messages.append({"role": "assistant", "content": final.get("content")})
            if await self._drain_steering(session_id, root, messages):
                continue
            return
        raise RuntimeError(f"已达到最大工具轮数（{max_rounds}）")

    async def _dispatch(self, ctx, cfg, session_id, name, args, cancel):
        if name == "ask_user":
            timeout = cfg.get("agent.ask_timeout_seconds", 0)
            try:
                questions = _normalize_questions(args)
                ans = await asyncio.wait_for(self.ask(session_id, questions, timeout),
                                             None if timeout in (None, 0) else float(timeout))
            except ToolError as e:
                return {"ok": False, "error": str(e)}
            except asyncio.TimeoutError:
                return {"cancelled": True, "reason": "提问超时"}
            if ans is None:
                return {"cancelled": True}
            return {"answers": ans.get("answers", [])}
        if name in ("write_file", "edit_file") and cfg.get("agent.write_confirm"):
            await self.send({"type": "confirm_request", "tool": name, "args": args})
            ok = await self.confirm(session_id, {"tool": name, "args": args})
            if not ok:
                return {"ok": False, "error": "用户拒绝该修改"}
        try:
            return await execute_tool(ctx, name, args)
        except ToolError as e:
            return {"ok": False, "error": str(e)}
        except Exception as e:  # pragma: no cover
            return {"ok": False, "error": f"工具执行异常: {e}"}

class _Cancelled(Exception):
    pass

from app.agent.llm import TOOLS as _TOOLS
