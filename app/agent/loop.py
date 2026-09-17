"""Agent 循环：流式对话 + 工具调度 + ask_user 挂起恢复 + 写入确认 + 取消。

事件协议（send 下发）：token/tool_call/tool_result/file_change/message_done/error，
另有 confirm_request（写入确认请求）、status（终态回执）与 compaction_done（自动压缩完成）。
"""
import asyncio, datetime, inspect, json
from pathlib import Path
from app.agent.prompts import build_system_prompt
from app.agent.tools import ToolContext, ToolError, execute_tool
from app.agent.mentions import expand_mentions
from app.services import compaction_service as _comp
from app.services.message_service import add_message
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
    def __init__(self, session_factory, llm, config_provider, send, ask, confirm):
        self.sf = session_factory
        self.llm = llm
        self.get_config = config_provider
        self.send = send
        self.ask = ask
        self.confirm = confirm

    async def _root_of(self, session_id: int):
        async with self.sf() as s:
            row = (await s.execute(text(
                "SELECT w.root_path FROM chat_session cs JOIN workspace w ON w.id = cs.workspace_id WHERE cs.id = :i"),
                {"i": session_id})).first()
        return Path(row[0])  # DB 返回 str；工具层与 send/file_change 均需 Path（f-string 渲染结果不变）

    async def run(self, session_id: int, user_text: str, cancel: asyncio.Event) -> str:
        status = "idle"
        try:
            await self._inner(session_id, user_text, cancel)
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
                "SELECT role, COALESCE(context_content, content) AS content, tool_call_id, tool_name, tool_args, tool_result "
                "FROM message WHERE session_id=:i AND seq > :w ORDER BY seq DESC LIMIT :win"),
                {"i": session_id, "w": wm, "win": window})).mappings().all()
        msgs = []
        for r in reversed(rows):
            if r["role"] == "user":
                msgs.append({"role": "user", "content": r["content"]})
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

    async def _llm_for(self, cfg):
        llm = self.llm(cfg) if callable(self.llm) else self.llm
        if inspect.isawaitable(llm):
            llm = await llm
        return llm

    async def _build_messages(self, session_id: int, root: Path, cfg: dict, send) -> list[dict]:
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
        system_text = build_system_prompt(str(root), datetime.date.today().isoformat())
        if summary:
            system_text += f"\n\n<context_summary>\n{summary}\n</context_summary>"
        return [{"role": "system", "content": system_text}] + history

    async def _inner(self, session_id: int, user_text: str, cancel: asyncio.Event):
        cfg = await self.get_config()
        root = await self._root_of(session_id)
        expanded = expand_mentions(root, user_text)
        await add_message(self.sf, session_id, "user", content=user_text, context_content=expanded)
        messages = await self._build_messages(session_id, root, cfg, self.send)
        ctx = ToolContext(root=root, session_id=session_id, session_factory=self.sf, send=self.send)
        max_rounds = int(cfg.get("agent.max_tool_rounds", 30))
        for _round in range(max_rounds):
            if cancel.is_set():
                raise _Cancelled()
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
                await add_message(self.sf, session_id, "assistant", content=final.get("content"),
                                  tool_args={"tool_calls": final["tool_calls"]})
                messages.append({"role": "assistant", "content": final.get("content"),
                                 "tool_calls": final["tool_calls"]})
                for tc in final["tool_calls"]:
                    if cancel.is_set():
                        raise _Cancelled()
                    name = tc["function"]["name"]
                    try:
                        args = json.loads(tc["function"]["arguments"] or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    await self.send({"type": "tool_call", "name": name, "args": args})
                    result = await self._dispatch(ctx, cfg, session_id, name, args, cancel)
                    await add_message(self.sf, session_id, "tool", tool_call_id=tc.get("id"),
                                      tool_name=name, tool_result=result)
                    await self.send({"type": "tool_result", "name": name, "result": result})
                    messages.append({"role": "tool", "tool_call_id": tc.get("id"),
                                     "content": json.dumps(result, ensure_ascii=False)})
                continue
            await add_message(self.sf, session_id, "assistant", content=final.get("content"),
                              reasoning=final.get("reasoning"))
            await self.send({"type": "message_done", "content": final.get("content")})
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
