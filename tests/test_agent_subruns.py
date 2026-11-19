"""子 run 委派（ADR-0052 / 规格 docs/specs/2026-11-18-agent-subruns.md）。

最高接缝 = AgentRunner + ScriptedLLM + 测试 engine（沿用 test_agent_loop 的 mk_session 模式）。
覆盖：并发执行与合并回执、归属落库、主历史/压缩排除、失败隔离、轮数用尽、上限拒绝、
工具面白名单、停止级联、脱敏三出口、先落行后发事件。
"""
import asyncio
import json

import pytest
from sqlalchemy import text as sql

from app.agent import registry
from app.agent.llm import ScriptedLLM
from app.agent.loop import AgentRunner
from app.db import make_session_factory
from app.services import compaction_service as _comp
from app.services.message_service import add_message


def done(content, tool_calls=None):
    return [{"type": "done", "message": {"role": "assistant", "content": content,
                                         "tool_calls": tool_calls}}]


def tool_call(id_, name, **args):
    return {"id": id_, "function": {"name": name,
                                    "arguments": json.dumps(args, ensure_ascii=False)}}


def cfg_provider(max_rounds=30, **over):
    async def _p():
        return {"agent.write_confirm": False, "agent.ask_timeout_seconds": 0,
                "agent.max_tool_rounds": max_rounds, "agent.history_window": 40,
                "clarify.enabled": False, **over}
    return _p


class Recorder:
    def __init__(self):
        self.events = []
        self.hooks = []

    async def __call__(self, ev):
        self.events.append(ev)
        for h in self.hooks:
            await h(ev)


class RoutedLLM:
    """单实例按任务书路由：子 run（messages[1] 是 brief）按标记选脚本，其余走主脚本。

    `ScriptedLLM` 是有状态的（按轮 pop），主 run 与每个子 run 各持一份实例，互不串台。
    """

    def __init__(self, main_turns, sub_turns):
        self.main = ScriptedLLM(main_turns)
        self.subs = {marker: ScriptedLLM(turns) for marker, turns in sub_turns.items()}

    async def chat_stream(self, messages, tools):
        brief = messages[1].get("content") if len(messages) > 1 else ""
        target = self.main
        if isinstance(brief, str):
            for marker, llm in self.subs.items():
                if marker in brief:
                    target = llm
                    break
        async for ev in target.chat_stream(messages, tools):
            yield ev


async def mk_session(test_engine, tmp_path) -> int:
    sf = make_session_factory(test_engine)
    async with sf() as s:
        ws = (await s.execute(sql(
            "INSERT INTO workspace (name, root_path) VALUES ('t', :p) RETURNING id"),
            {"p": str(tmp_path)})).scalar()
        sid = (await s.execute(sql(
            "INSERT INTO chat_session (workspace_id) VALUES (:w) RETURNING id"),
            {"w": ws})).scalar()
        await s.commit()
    return sid


async def run_agent(test_engine, tmp_path, llm, cancel=None, **cfgkw):
    sid = await mk_session(test_engine, tmp_path)
    sf = make_session_factory(test_engine)
    rec = Recorder()
    runner = AgentRunner(sf, llm, cfg_provider(**cfgkw), rec,
                         lambda *a, **k: asyncio.sleep(0, None),
                         lambda *a, **k: True)
    status = await runner.run(sid, "开始", cancel or asyncio.Event())
    return sid, status, rec, runner, sf


def _two_task_delegate():
    return done(None, [tool_call("d1", "delegate", tasks=[
        {"title": "调研 A", "brief": "调研A：读 a.txt 并汇报"},
        {"title": "调研 B", "brief": "调研B：列目录并汇报"},
    ])])


@pytest.mark.asyncio
async def test_two_subruns_run_concurrently_and_merge_receipts(test_engine, tmp_path):
    (tmp_path / "a.txt").write_text("内容A", encoding="utf-8")
    llm = RoutedLLM(
        main_turns=[_two_task_delegate(), done("两个都完成了")],
        sub_turns={
            "调研A": [done(None, [tool_call("sa1", "read_file", path="a.txt")]), done("A 的结果")],
            "调研B": [done(None, [tool_call("sb1", "list_dir", path="")]), done("B 的结果")],
        })
    sid, status, rec, runner, sf = await run_agent(test_engine, tmp_path, llm)
    assert status == "idle"
    evs = rec.events

    # 并发：两条 subrun_start 都在任何 subrun_done 之前（确有重叠执行）
    last_start = max(i for i, e in enumerate(evs) if e["type"] == "subrun_start")
    first_done = min(i for i, e in enumerate(evs) if e["type"] == "subrun_done")
    assert last_start < first_done

    # 合并回执：按 index 排序，字段齐全
    tr = next(e for e in evs if e["type"] == "tool_result" and e["name"] == "delegate")
    subs = tr["result"]["subruns"]
    assert [s["index"] for s in subs] == [0, 1]
    assert [s["status"] for s in subs] == ["completed", "completed"]
    assert subs[0]["result"] == "A 的结果" and subs[1]["result"] == "B 的结果"
    assert subs[0]["tool_calls"] == 1 and subs[0]["rounds_used"] == 2
    assert subs[0]["title"] == "调研 A" and subs[1]["title"] == "调研 B"

    # 子 run 事件带 index 成对出现
    assert sorted(e["index"] for e in evs if e["type"] == "subrun_done") == [0, 1]
    tcalls = [e for e in evs if e["type"] == "subrun_tool_call"]
    assert {(e["index"], e["name"]) for e in tcalls} == {(0, "read_file"), (1, "list_dir")}
    texts = {e["index"]: e["content"] for e in evs if e["type"] == "subrun_text"}
    assert texts[0] == "A 的结果" and texts[1] == "B 的结果"

    # 归属落库：子 run 行带 parent_tool_call_id 与 subrun_index
    async with sf() as s:
        rows = (await s.execute(sql(
            "SELECT role, subrun_index, tool_call_id FROM message "
            "WHERE session_id=:i AND parent_tool_call_id='d1' ORDER BY seq"),
            {"i": sid})).mappings().all()
    assert {(r["role"], r["subrun_index"]) for r in rows} == {
        ("assistant", 0), ("tool", 0), ("assistant", 1), ("tool", 1)}
    assert {r["tool_call_id"] for r in rows if r["role"] == "tool"} == {"sa1", "sb1"}

    # 主历史排除：主 run 第二轮看不到子 run 的中间内容与工具行
    main_second = llm.main.received[1]
    dump = json.dumps(main_second, ensure_ascii=False)
    assert "内容A" not in dump and "sa1" not in dump and "sb1" not in dump


@pytest.mark.asyncio
async def test_subrun_rows_land_before_their_events(test_engine, tmp_path):
    """先落行、后发事件：subrun_tool_result 到达时对应 tool 行必须已在库里（ADR-0029）。"""
    (tmp_path / "a.txt").write_text("内容A", encoding="utf-8")
    llm = RoutedLLM(
        main_turns=[_two_task_delegate(), done("完成")],
        sub_turns={
            "调研A": [done(None, [tool_call("sa1", "read_file", path="a.txt")]), done("A 的结果")],
            "调研B": [done("B 的结果")],
        })
    sid, status, rec, runner, sf = await run_agent(test_engine, tmp_path, llm)
    assert status == "idle"

    async def check(ev):
        if ev["type"] == "subrun_tool_result" and ev["name"] == "read_file":
            async with sf() as s:
                n = (await s.execute(sql(
                    "SELECT count(*) FROM message WHERE session_id=:i AND tool_call_id='sa1'"),
                    {"i": sid})).scalar()
            assert n == 1, "事件先于落库：live 画出的东西刷新后会消失"
        if ev["type"] == "subrun_done":
            idx = ev["index"]
            async with sf() as s:
                n = (await s.execute(sql(
                    "SELECT count(*) FROM message WHERE session_id=:i AND subrun_index=:k"),
                    {"i": sid, "k": idx})).scalar()
            assert n >= 1
    rec.hooks.append(check)


@pytest.mark.asyncio
async def test_failed_subrun_is_isolated(test_engine, tmp_path):
    """子 run 的 LLM 炸了只影响它自己：error 回执 + 其余照常完成。"""
    llm = RoutedLLM(
        main_turns=[_two_task_delegate(), done("收工")],
        sub_turns={
            "调研A": [],  # 空脚本 → chat_stream 时 IndexError，模拟上游异常
            "调研B": [done("B 的结果")],
        })
    sid, status, rec, runner, sf = await run_agent(test_engine, tmp_path, llm)
    assert status == "idle"
    tr = next(e for e in rec.events if e["type"] == "tool_result" and e["name"] == "delegate")
    subs = tr["result"]["subruns"]
    assert subs[0]["status"] == "error" and subs[0]["error"]
    assert subs[1]["status"] == "completed" and subs[1]["result"] == "B 的结果"
    done_ev = next(e for e in rec.events
                   if e["type"] == "subrun_done" and e["index"] == 0)
    assert done_ev["status"] == "error" and done_ev["error"]


@pytest.mark.asyncio
async def test_round_budget_exhaustion_is_an_error_receipt(test_engine, tmp_path):
    """轮数用尽是 error 回执（子 run 的意义之一就是成本封顶），不是静默完成。"""
    llm = RoutedLLM(
        main_turns=[done(None, [tool_call("d1", "delegate", tasks=[
            {"title": "调研 A", "brief": "调研A：反复查"}]),
        ]), done("收工")],
        sub_turns={"调研A": [
            done(None, [tool_call("sa1", "list_dir", path="")]),
            done(None, [tool_call("sa2", "list_dir", path="")]),
        ]})
    sid, status, rec, runner, sf = await run_agent(test_engine, tmp_path, llm,
                                                   **{"subrun.max_rounds": 2})
    assert status == "idle"
    tr = next(e for e in rec.events if e["type"] == "tool_result" and e["name"] == "delegate")
    sub = tr["result"]["subruns"][0]
    assert sub["status"] == "error" and "最大轮数" in sub["error"]
    assert sub["rounds_used"] == 2


@pytest.mark.asyncio
async def test_zero_max_rounds_means_unlimited(test_engine, tmp_path):
    """subrun.max_rounds=0 → 不限轮数（与主循环同口径）：越过有限值时期满即中止的轮数照样跑完。"""
    rounds_needed = 12  # 大于 DEFAULT_MAX_ROUNDS(10)，有限值下必被中止
    llm = RoutedLLM(
        main_turns=[done(None, [tool_call("d1", "delegate", tasks=[
            {"title": "调研 A", "brief": "调研A：多轮"}]),
        ]), done("收工")],
        sub_turns={"调研A": [
            done(None, [tool_call(f"s{i}", "list_dir", path="")])
            for i in range(rounds_needed)] + [done("查完了")]})
    sid, status, rec, runner, sf = await run_agent(test_engine, tmp_path, llm,
                                                   **{"subrun.max_rounds": 0})
    assert status == "idle"
    tr = next(e for e in rec.events if e["type"] == "tool_result" and e["name"] == "delegate")
    sub = tr["result"]["subruns"][0]
    assert sub["status"] == "completed" and sub["result"] == "查完了"
    assert sub["rounds_used"] == rounds_needed + 1


@pytest.mark.asyncio
async def test_empty_reply_nudge_then_success_or_error(test_engine, tmp_path):
    """空回复沿用主循环的补催口径（同常量 _EMPTY_REPLY_RETRIES=2）：
    补催后给出文字 → completed；超过上限仍空 → error 回执，不伪造总结行。"""
    llm = RoutedLLM(
        main_turns=[done(None, [tool_call("d1", "delegate", tasks=[
            {"title": "调研 A", "brief": "调研A：补催后完成"},
            {"title": "调研 B", "brief": "调研B：一直空"}]),
        ]), done("收工")],
        sub_turns={
            "调研A": [done(""), done(""), done("补催后的结果")],
            "调研B": [done(""), done(""), done("")],
        })
    sid, status, rec, runner, sf = await run_agent(test_engine, tmp_path, llm)
    assert status == "idle"
    tr = next(e for e in rec.events if e["type"] == "tool_result" and e["name"] == "delegate")
    a, b = tr["result"]["subruns"]
    assert a["status"] == "completed" and a["result"] == "补催后的结果"
    assert b["status"] == "error" and "空回复" in b["error"]
    assert b["rounds_used"] == 3  # 两次补催 + 第三次仍空，第 3 轮收口为 error
    # 失败分支不落 assistant 行：不伪造总结（子 run 行只有 A 的那条）
    async with sf() as s:
        rows = (await s.execute(sql(
            "SELECT subrun_index, content FROM message WHERE session_id=:i "
            "AND parent_tool_call_id='d1' ORDER BY subrun_index"),
            {"i": sid})).fetchall()
    assert [(r[0], r[1]) for r in rows] == [(0, "补催后的结果")]


@pytest.mark.asyncio
async def test_over_max_tasks_rejects_with_zero_side_effects(test_engine, tmp_path):
    """超过 subrun.max_tasks 整批拒绝：不建任何子 run、不截断。"""
    llm = RoutedLLM(
        main_turns=[done(None, [tool_call("d1", "delegate", tasks=[
            {"title": "A", "brief": "做A"},
            {"title": "B", "brief": "做B"},
            {"title": "C", "brief": "做C"},
        ])]), done("收工")],
        sub_turns={})
    sid, status, rec, runner, sf = await run_agent(test_engine, tmp_path, llm,
                                                   **{"subrun.max_tasks": 2})
    assert status == "idle"
    tr = next(e for e in rec.events if e["type"] == "tool_result" and e["name"] == "delegate")
    assert tr["result"]["ok"] is False and "最多委派 2 个" in tr["result"]["error"]
    assert not [e for e in rec.events if e["type"].startswith("subrun_")]
    async with sf() as s:
        n = (await s.execute(sql(
            "SELECT count(*) FROM message WHERE session_id=:i AND parent_tool_call_id IS NOT NULL"),
            {"i": sid})).scalar()
    assert n == 0


def test_build_subrun_tools_whitelist_order_and_scopes():
    names = [t["function"]["name"] for t in registry.build_subrun_tools(app=True)]
    assert names == list(registry.SUBRUN_TOOL_NAMES)
    for banned in ("delegate", "write_file", "edit_file", "delete_file", "move_file",
                   "revert_change", "ask_user", "clarify_done", "todo_write",
                   "memory_write", "db_write", "preview_restart", "preview_logs",
                   "browser_open", "browser_step", "browser_close",
                   "web_open", "web_step", "web_close"):
        assert banned not in names
    # 非应用工作区只剩 fs 三件套 + web_fetch：联网读取是无状态的，与应用身份无关（ADR-0053）
    assert [t["function"]["name"] for t in registry.build_subrun_tools(app=False)] == [
        "list_dir", "read_file", "search", "web_fetch"]


@pytest.mark.asyncio
async def test_subrun_rows_are_excluded_from_compaction_and_history(test_engine, tmp_path):
    sid = await mk_session(test_engine, tmp_path)
    sf = make_session_factory(test_engine)
    await add_message(sf, sid, "user", content="主问题")
    await add_message(sf, sid, "assistant", content="子中间结果",
                      parent_tool_call_id="d1", subrun_index=0)
    await add_message(sf, sid, "tool", tool_call_id="sa1", tool_name="read_file",
                      tool_result={"content": "子工具结果"},
                      parent_tool_call_id="d1", subrun_index=0)
    rows = await _comp.rows_after(sf, sid, 0)
    assert [r["content"] for r in rows] == ["主问题"]
    runner = AgentRunner(sf, ScriptedLLM([]), cfg_provider(), Recorder(),
                         lambda *a, **k: asyncio.sleep(0, None), lambda *a, **k: True)
    hist, _ = await runner._history(sid, 40)
    assert [m["content"] for m in hist] == ["主问题"]


@pytest.mark.asyncio
async def test_stop_cancels_all_subruns_and_marks_delegate_cancelled(test_engine, tmp_path):
    """停止级联：子 run 全部收口、不发 subrun_done；委派结果行为 cancelled。"""
    cancel = asyncio.Event()

    class Hang:
        async def chat_stream(self, messages, tools):
            await cancel.wait()
            yield done("来不及了")

    class FirstToolThenHang:
        """第一轮给一个工具调用（落出部分行），第二轮挂起等停止。"""

        def __init__(self):
            self.calls = 0

        async def chat_stream(self, messages, tools):
            self.calls += 1
            target = (ScriptedLLM([done(None, [tool_call("sx", "list_dir", path="")])])
                      if self.calls == 1 else Hang())
            async for ev in target.chat_stream(messages, tools):
                yield ev

    class Routed:
        def __init__(self):
            self.main = ScriptedLLM([_two_task_delegate(), done("收工")])
            self.subs = {}   # brief → 每子 run 一份状态（按轮切换脚本）

        async def chat_stream(self, messages, tools):
            brief = messages[1].get("content") if len(messages) > 1 else ""
            if isinstance(brief, str) and brief.startswith("调研"):
                target = self.subs.setdefault(brief, FirstToolThenHang())
            else:
                target = self.main
            async for ev in target.chat_stream(messages, tools):
                yield ev

    sid = await mk_session(test_engine, tmp_path)
    sf = make_session_factory(test_engine)
    rec = Recorder()

    async def on_event(ev):
        # 任一子 run 落出第一条工具结果即请求停止（此刻两个子 run 都已在各自的第一轮里）
        if ev["type"] == "subrun_tool_result":
            cancel.set()
    rec.hooks.append(on_event)
    runner = AgentRunner(sf, Routed(), cfg_provider(), rec,
                         lambda *a, **k: asyncio.sleep(0, None), lambda *a, **k: True)
    status = await runner.run(sid, "开始", cancel)
    assert status == "idle"
    assert runner.last_outcome == "cancelled"
    assert not [e for e in rec.events if e["type"] == "subrun_done"]
    assert any(e["type"] == "error" and e["message"] == "已取消" for e in rec.events)
    async with sf() as s:
        # 部分行原样保留（不伪造成败、不删除）
        n = (await s.execute(sql(
            "SELECT count(*) FROM message WHERE session_id=:i AND subrun_index IS NOT NULL"),
            {"i": sid})).scalar()
        r = (await s.execute(sql(
            "SELECT tool_result FROM message WHERE session_id=:i AND role='tool' "
            "AND tool_name='delegate'"), {"i": sid})).scalar()
    assert n >= 1
    assert r == {"cancelled": True}


@pytest.mark.asyncio
async def test_credentials_are_redacted_in_rows_and_events_but_not_for_the_model(
        test_engine, tmp_path):
    """脱敏与主 run 同口径：落库/事件走净化版，当轮回注模型的工具结果仍是原文。"""
    secret = "sk-abcdef1234567890"
    (tmp_path / "conf.txt").write_text(f"api_key: {secret}", encoding="utf-8")
    llm = RoutedLLM(
        main_turns=[done(None, [tool_call("d1", "delegate", tasks=[
            {"title": "查密钥", "brief": "查密钥：搜索 conf.txt 里的 api_key"}]),
        ]), done("收工")],
        sub_turns={"查密钥": [
            done(None, [tool_call("sa1", "search", mode="content", pattern=secret)]),
            done("查到了"),
        ]})
    sid, status, rec, runner, sf = await run_agent(test_engine, tmp_path, llm)
    assert status == "idle"
    async with sf() as s:
        arow = (await s.execute(sql(
            "SELECT tool_args FROM message WHERE session_id=:i AND role='assistant' "
            "AND subrun_index=0"), {"i": sid})).scalar()
        trow = (await s.execute(sql(
            "SELECT tool_result FROM message WHERE session_id=:i AND tool_call_id='sa1'"),
            {"i": sid})).scalar()
    assert secret not in json.dumps(arow, ensure_ascii=False)
    assert "<redacted:api_key>" in json.dumps(arow, ensure_ascii=False)
    assert secret not in json.dumps(trow, ensure_ascii=False)
    tc_ev = next(e for e in rec.events if e["type"] == "subrun_tool_call")
    tr_ev = next(e for e in rec.events if e["type"] == "subrun_tool_result")
    assert secret not in json.dumps(tc_ev, ensure_ascii=False)
    assert secret not in json.dumps(tr_ev, ensure_ascii=False)
    # 回注模型：子 run 第二轮的 tool 消息是原文（不然模型看不到真实内容）
    second = llm.subs["查密钥"].received[1]
    tool_msgs = [m for m in second if m["role"] == "tool"]
    assert any(secret in m["content"] for m in tool_msgs)


def test_int_cfg_clamps_to_hard_bounds_and_code_defaults():
    """Ticket 005 / US-16：默认值只住在代码与前端表单两处；库里缺键、畸形值都落回合法区间，
    越界只会被夹取，绝不放大成一次超支（下游还有整批拒绝兜底）。"""
    from app.agent import subrun as _sub
    d_tasks = (_sub.DEFAULT_MAX_TASKS, 1, _sub.MAX_TASKS_CAP)
    d_rounds = (_sub.DEFAULT_MAX_ROUNDS, 0, _sub.MAX_ROUNDS_CAP)
    assert _sub.DEFAULT_MAX_TASKS == 5 and _sub.DEFAULT_MAX_ROUNDS == 10
    assert _sub._int_cfg({}, "subrun.max_tasks", *d_tasks) == 5
    assert _sub._int_cfg({}, "subrun.max_rounds", *d_rounds) == 10
    assert _sub._int_cfg({"subrun.max_tasks": 99}, "subrun.max_tasks", *d_tasks) == 8
    assert _sub._int_cfg({"subrun.max_tasks": 0}, "subrun.max_tasks", *d_tasks) == 1
    assert _sub._int_cfg({"subrun.max_rounds": 999}, "subrun.max_rounds", *d_rounds) == 50
    assert _sub._int_cfg({"subrun.max_rounds": -3}, "subrun.max_rounds", *d_rounds) == 0
    assert _sub._int_cfg({"subrun.max_rounds": 0}, "subrun.max_rounds", *d_rounds) == 0
    assert _sub._int_cfg({"subrun.max_rounds": "abc"}, "subrun.max_rounds", *d_rounds) == 10
    assert _sub._int_cfg({"subrun.max_rounds": None}, "subrun.max_rounds", *d_rounds) == 10


@pytest.mark.asyncio
async def test_over_cap_config_is_clamped_not_trusted(test_engine, tmp_path):
    """配置成 99 不是免检通行证：执行器先夹到硬上限 8，第 9 条照样整批拒绝、零副作用。"""
    llm = RoutedLLM(
        main_turns=[done(None, [tool_call("d1", "delegate", tasks=[
            {"title": f"T{i}", "brief": f"做{i}"} for i in range(9)])]), done("收工")],
        sub_turns={})
    sid, status, rec, runner, sf = await run_agent(test_engine, tmp_path, llm,
                                                   **{"subrun.max_tasks": 99})
    assert status == "idle"
    tr = next(e for e in rec.events if e["type"] == "tool_result" and e["name"] == "delegate")
    assert tr["result"]["ok"] is False and "最多委派 8 个" in tr["result"]["error"]
    assert not [e for e in rec.events if e["type"].startswith("subrun_")]
    async with sf() as s:
        n = (await s.execute(sql(
            "SELECT count(*) FROM message WHERE session_id=:i AND parent_tool_call_id IS NOT NULL"),
            {"i": sid})).scalar()
    assert n == 0
