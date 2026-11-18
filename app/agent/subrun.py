"""子 run 执行器（ADR-0052 / 规格 docs/specs/2026-11-18-agent-subruns.md §2.2）。

`delegate` 工具的唯一实现：校验任务书 → 每任务一个协程并发执行（共享主 run 的 cancel）→
全部落定后按 index 合并回执。子 run 是**同会话只读派生分支**：工具面走白名单、独立上下文
与 LLM 循环、消息落同一会话并带归属列（parent_tool_call_id/subrun_index）；失败隔离，
任何子 run 的异常都收敛成 status=error 回执，绝不给整批抛错。

循环依赖方向：loop.py 在 `_dispatch` 里**惰性** import 本模块，故这里的顶层
`from app.agent.loop import ...` 成立；本模块若被直接 import，会先把 loop 完整加载。
"""
from __future__ import annotations

import asyncio
import contextlib
import datetime
import json

from app.agent import clarify as _cl
from app.agent import registry as _registry
from app.agent.loop import (_CANCEL, _EMPTY_REPLY_NUDGE, _EMPTY_REPLY_RETRIES,
                            _abort_stream, _wait_or_cancel)
from app.agent.preview_tools import build_preview_prompt
from app.agent.prompts import build_app_prompt_blocks, build_system_prompt
from app.agent.redact import redact_tool_calls_args, redact_tool_result
from app.agent.result_budget import apply_budget
from app.agent.tools import ToolContext, ToolError, execute_tool
from app.services.message_service import add_message

DEFAULT_MAX_TASKS = 5
MAX_TASKS_CAP = 8
DEFAULT_MAX_ROUNDS = 10
MAX_ROUNDS_CAP = 50


class _SubrunFailure(Exception):
    """子 run 的可预期失败（轮数用尽/补催后仍空/流无收口）：转成 status=error 回执。"""


def _int_cfg(cfg: dict, key: str, default: int, lo: int, hi: int) -> int:
    try:
        v = int(cfg.get(key, default))
    except (TypeError, ValueError):
        v = default
    return min(hi, max(lo, v))


def _parse_tasks(args: dict, max_tasks: int) -> tuple[list[dict], str | None]:
    """校验先于派发：不合法就整批拒绝、零副作用，绝不静默截断丢任务。"""
    raw = args.get("tasks")
    if isinstance(raw, str):
        # 与 options 同一类畸形：模型把整个数组塞成 JSON 字符串。不接的话会白烧一轮。
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = None
    if not isinstance(raw, list) or not raw:
        return [], "delegate 需要至少 1 个任务（tasks 数组，每项含 title 与 brief）"
    if len(raw) > max_tasks:
        return [], f"一次最多委派 {max_tasks} 个子任务，请拆成多批"
    tasks = []
    for i, t in enumerate(raw, start=1):
        if not isinstance(t, dict):
            return [], f"第 {i} 个任务格式错误：需要含 title 与 brief 的对象"
        title = str(t.get("title") or "").strip()
        brief = str(t.get("brief") or "").strip()
        if not title or not brief:
            return [], f"第 {i} 个任务缺少 title 或 brief（两项均必填）"
        tasks.append({"title": title, "brief": brief})
    return tasks, None


async def run_batch(runner, ctx, cfg: dict, args: dict, call_id: str | None = None) -> dict:
    """批量执行一次委派，返回经预算截断的合并回执（唯一出口与主 run 相同）。"""
    max_tasks = _int_cfg(cfg, "subrun.max_tasks", DEFAULT_MAX_TASKS, 1, MAX_TASKS_CAP)
    max_rounds = _int_cfg(cfg, "subrun.max_rounds", DEFAULT_MAX_ROUNDS, 1, MAX_ROUNDS_CAP)
    tasks, error = _parse_tasks(args, max_tasks)
    if error:
        return {"ok": False, "error": error}
    children = [asyncio.create_task(
        _run_one(runner, ctx, cfg, index, task, max_rounds, call_id))
        for index, task in enumerate(tasks)]
    try:
        receipts = await asyncio.gather(*children)
    finally:
        # 停止级联：主循环取消的是本协程，子任务在这里一并收口；已落定的行原样保留。
        pending = [t for t in children if not t.done()]
        for t in pending:
            t.cancel()
        if pending:
            with contextlib.suppress(BaseException):
                await asyncio.gather(*pending, return_exceptions=True)
    receipts.sort(key=lambda r: r["index"])
    result = {"ok": True, "subruns": receipts}
    result, _ = apply_budget(result, _registry.DELEGATE_POLICY)
    return result


async def _run_one(runner, ctx, cfg, index: int, task: dict, max_rounds: int,
                   call_id: str | None) -> dict:
    title, brief = task["title"], task["brief"]
    send = ctx.send
    stats = {"rounds": 0, "tool_calls": 0}
    await send({"type": "subrun_start", "index": index, "title": title})
    try:
        text = await _execute(runner, ctx, cfg, index, brief, max_rounds, call_id, stats)
    except asyncio.CancelledError:
        # 停止时不发 subrun_done：卡片由「委派结果为 cancelled + 缺 done」推出「已中断」，
        # live 与历史同一条规则（ADR-0029）。
        raise
    except _SubrunFailure as e:
        await send({"type": "subrun_done", "index": index, "status": "error", "error": str(e)})
        return _receipt(index, title, "error", None, str(e), stats)
    except Exception as e:  # LLM 异常等：失败隔离，绝不连累整批
        await send({"type": "subrun_done", "index": index, "status": "error", "error": str(e)})
        return _receipt(index, title, "error", None, str(e), stats)
    await send({"type": "subrun_done", "index": index, "status": "completed"})
    return _receipt(index, title, "completed", text, None, stats)


def _receipt(index, title, status, result, error, stats) -> dict:
    return {"index": index, "title": title, "status": status, "result": result,
            "error": error, "rounds_used": stats["rounds"], "tool_calls": stats["tool_calls"]}


def _attributed_send(send, index: int):
    """子 run 的工具侧发件器：只放行 tool_progress（转成带 index 的 subrun_progress）。

    白名单工具不会产生 file_change/todo_sync 一类事件；真有新工具忘了归类时宁可静默丢，
    也不把主 run 的事件形状弄脏。
    """
    async def _send(ev: dict):
        if ev.get("type") == "tool_progress":
            await send({"type": "subrun_progress", "index": index,
                        "name": ev.get("name"), "line": ev.get("line")})
    return _send


async def _build_messages(runner, ctx, cfg: dict, brief: str) -> list[dict]:
    """子 run 上下文 = [system, user(brief)]：同款骨架、无主历史、无摘要/任务清单。

    技能/应用契约/记忆/决策索引与主 run 同源；preview 段只拼 diag/check/db/preview 四块
    （白名单里没有浏览器工具），memory 召回以任务书为查询词。
    """
    from app.deploy.contract import build_app_contract
    root = ctx.root
    skills = await runner._enabled_skills_of(ctx.session_id)
    preview = (build_app_prompt_blocks(build_preview_prompt(ctx.app["stack"]))
               if ctx.app else None)
    system_text = build_system_prompt(str(root), datetime.date.today().isoformat(),
                                      app_contract=build_app_contract(root), skills=skills,
                                      preview=preview, clarify=None)
    if brief.strip():
        memories = await runner._memories_of(ctx.session_id, brief, cfg)
        if memories:
            system_text += ("\n\n<memories>\n"
                            + "\n".join(runner._memory_line(m) for m in memories)
                            + "\n</memories>")
    decisions = _cl.render_decisions(_cl.collect_decisions(root))
    if decisions:
        system_text += decisions
    return [{"role": "system", "content": system_text},
            {"role": "user", "content": brief}]


def _parse_args(tc: dict) -> dict:
    try:
        return json.loads(tc["function"]["arguments"] or "{}")
    except json.JSONDecodeError:
        return {}


async def _execute(runner, ctx, cfg, index: int, brief: str, max_rounds: int,
                   call_id: str | None, stats: dict) -> str:
    messages = await _build_messages(runner, ctx, cfg, brief)
    tools = _registry.build_subrun_tools(app=bool(ctx.app))
    llm = await runner._llm_for(cfg)
    sub_ctx = ToolContext(
        root=ctx.root, session_id=ctx.session_id, session_factory=ctx.session_factory,
        send=_attributed_send(ctx.send, index), skills_root=ctx.skills_root,
        app=ctx.app, cfg=cfg, cancel=ctx.cancel, deploy_hub=None, deploy_tasks={},
        run_state={}, model_cfg=ctx.model_cfg, llm=llm)
    empty_retries = 0
    for round_no in range(max_rounds):
        stats["rounds"] = round_no + 1
        if ctx.cancel.is_set():
            raise asyncio.CancelledError()
        final = None
        finish_reason = None
        stream = llm.chat_stream(messages, tools)
        while True:
            pending = asyncio.ensure_future(stream.__anext__())
            try:
                ev = await _wait_or_cancel(pending, ctx.cancel)
            except StopAsyncIteration:
                break
            if ev is _CANCEL:
                await _abort_stream(stream, pending)
                raise asyncio.CancelledError()
            if ev["type"] == "done":
                final = ev["message"]
                finish_reason = ev.get("finish_reason")
        if final is None:
            raise _SubrunFailure("模型流结束时没有返回完整消息（缺少 done 事件）")
        calls = final.get("tool_calls")
        if calls:
            empty_retries = 0
            # 凭据遮断与主 run 同口径：落库/事件用净化版，工具执行与当轮回注用原值。
            sanitized_calls, _ = redact_tool_calls_args(calls)
            await add_message(ctx.session_factory, ctx.session_id, "assistant",
                              content=final.get("content"), reasoning=final.get("reasoning"),
                              tool_args={"tool_calls": sanitized_calls},
                              parent_tool_call_id=call_id, subrun_index=index)
            messages.append({"role": "assistant", "content": final.get("content"),
                             "tool_calls": sanitized_calls})
            # 先落行、后发事件：live 画出来的东西刷新后都能从行上重建。
            if final.get("content"):
                await ctx.send({"type": "subrun_text", "index": index,
                                "content": final["content"]})
            for exec_tc, push_tc in zip(calls, sanitized_calls):
                name = exec_tc["function"]["name"]
                exec_args = _parse_args(exec_tc)
                await ctx.send({"type": "subrun_tool_call", "index": index,
                                "name": name, "args": _parse_args(push_tc)})
                try:
                    result = await execute_tool(sub_ctx, name, exec_args)
                except ToolError as e:
                    result = {"ok": False, "error": str(e)}
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    result = {"ok": False, "error": f"工具执行异常: {e}"}
                stats["tool_calls"] += 1
                sanitized_result = redact_tool_result(result)
                await add_message(ctx.session_factory, ctx.session_id, "tool",
                                  tool_call_id=exec_tc.get("id"), tool_name=name,
                                  tool_result=sanitized_result,
                                  parent_tool_call_id=call_id, subrun_index=index)
                await ctx.send({"type": "subrun_tool_result", "index": index,
                                "name": name, "result": sanitized_result})
                # 当轮回注模型用原文（与主 run 同一出口口径）；default=str 兜底非 JSON 原生值。
                messages.append({"role": "tool", "tool_call_id": exec_tc.get("id"),
                                 "content": json.dumps(result, ensure_ascii=False, default=str)})
            continue
        if not (final.get("content") or "").strip():
            empty_retries += 1
            if empty_retries > _EMPTY_REPLY_RETRIES:
                raise _SubrunFailure(
                    f"模型连续返回空回复（finish_reason={finish_reason or '未知'}），子任务未完成")
            messages.append({"role": "user", "content": _EMPTY_REPLY_NUDGE})
            continue
        await add_message(ctx.session_factory, ctx.session_id, "assistant",
                          content=final.get("content"), reasoning=final.get("reasoning"),
                          parent_tool_call_id=call_id, subrun_index=index)
        await ctx.send({"type": "subrun_text", "index": index, "content": final["content"]})
        return final["content"]
    raise _SubrunFailure(f"子任务超过最大轮数（{max_rounds}），已中止")
