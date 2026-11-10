import asyncio, json, pytest
from sqlalchemy import text as sql
from app.db import make_session_factory
from app.agent.llm import ScriptedLLM
from app.agent.loop import AgentRunner
from app.services.message_service import add_message

def delta(t):
    return {"type": "content_delta", "content": t}

def done(content, tool_calls=None):
    return [{"type": "done", "message": {"role": "assistant", "content": content, "tool_calls": tool_calls}}]

def tool_call(id_, name, **args):
    return {"id": id_, "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}

def raw_tool_call(id_, name, raw_args):
    """与 `tool_call` 同形，但 `arguments` 是**原样字符串**：复现「无参数调用」与流式截断的形状。

    `llm.py` 把 slot 的 arguments 初始化成 `""`、只在 delta 带 args 时才往后追加，所以模型对一个
    schema 里 `"required": []` 的 `browser_open` 产出的就是空串——`arguments == ""` 不是畸形调用，
    是日常可达路径（`browser_open` 缺省 path 就是打开 `/`）。
    """
    return {"id": id_, "type": "function", "function": {"name": name, "arguments": raw_args}}

def cfg_provider(write_confirm=False, ask_timeout=0, max_rounds=30, clarify=False, **over):
    async def _p():
        # clarify 缺省关：本夹具服务的是与澄清无关的用例，而门禁的代码默认是「缺行即开」，
        # 不显式关掉就会让每个写文件的用例都先撞上这道闸。澄清自身的用例传 clarify=True。
        return {"agent.write_confirm": write_confirm, "agent.ask_timeout_seconds": ask_timeout,
                "agent.max_tool_rounds": max_rounds, "agent.history_window": 40,
                "clarify.enabled": clarify, **over}
    return _p

class Recorder:
    def __init__(self): self.events = []
    async def __call__(self, ev): self.events.append(ev)

async def mk_session(test_engine, tmp_path) -> int:
    sf = make_session_factory(test_engine)
    async with sf() as s:
        ws = (await s.execute(sql(
            "INSERT INTO workspace (name, root_path) VALUES ('t', :p) RETURNING id"),
            {"p": str(tmp_path)})).scalar()
        sid = (await s.execute(sql(
            "INSERT INTO chat_session (workspace_id) VALUES (:w) RETURNING id"), {"w": ws})).scalar()
        await s.commit()
    return sid

async def run_once(test_engine, tmp_path, turns, ask=None, confirm=None, cancel=None, **cfgkw):
    sid = await mk_session(test_engine, tmp_path)
    sf = make_session_factory(test_engine)
    rec = Recorder()
    runner = AgentRunner(sf, ScriptedLLM(turns), cfg_provider(**cfgkw), rec,
                         ask or (lambda *a, **k: asyncio.sleep(0, None)), confirm or (lambda *a, **k: True))
    status = await runner.run(sid, "开始", cancel or asyncio.Event())
    return sid, status, rec.events, sf

@pytest.mark.asyncio
async def test_plain_reply(test_engine, tmp_path):
    # ScriptedLLM 契约是 turns=list[list[dict]]（每轮为事件列表）；delta 是单事件，
    # 故与 done 事件合并为同一轮，避免 dict 被当轮迭代出字符串键
    sid, status, evs, sf = await run_once(test_engine, tmp_path, [[delta("你"), *done("你好")]])
    assert status == "idle"
    types = [e["type"] for e in evs]
    assert "token" in types and "message_done" in types
    async with sf() as s:
        row = (await s.execute(sql(
            "SELECT content FROM message WHERE session_id=:i AND role='assistant' ORDER BY seq"),
            {"i": sid})).scalars().all()
    assert "你好" in row

@pytest.mark.asyncio
async def test_write_file_flow(test_engine, tmp_path):
    turns = [done(None, [tool_call("c1", "write_file", path="a.txt", content="hi")]), done("写好了")]
    sid, status, evs, sf = await run_once(test_engine, tmp_path, turns)
    assert status == "idle"
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "hi"
    tr = next(e for e in evs if e["type"] == "tool_result")
    assert tr["result"]["ok"] is True
    async with sf() as s:
        n = (await s.execute(sql("SELECT count(*) FROM file_change WHERE session_id=:i"), {"i": sid})).scalar()
    assert n == 1

@pytest.mark.asyncio
async def test_file_change_event_carries_row_id(test_engine, tmp_path):
    # WS 的 file_change 事件要带落库行 id：聊天流里那条内联 diff 也要能跳「查看具体」整页，
    # 地址是 /sessions/{会话}/changes/{记录}。事件里没有 id，就只能给一个点了没反应的按钮。
    turns = [done(None, [tool_call("c1", "write_file", path="a.txt", content="hi")]), done("写好了")]
    sid, status, evs, sf = await run_once(test_engine, tmp_path, turns)
    fc = next(e for e in evs if e["type"] == "file_change")
    async with sf() as s:
        row_id = (await s.execute(sql(
            "SELECT id FROM file_change WHERE session_id=:i"), {"i": sid})).scalar()
    assert fc["change_id"] == row_id

@pytest.mark.asyncio
async def test_file_change_event_without_db_still_sent(test_engine, tmp_path):
    # 不落库的调用点（session_factory 为空）仍要收到事件，只是没有 id 可给——
    # 前端据此不渲染跳转入口，不能因为拿不到 id 就把 diff 本身也吞掉
    from app.agent.tools import ToolContext, execute_tool
    sid = await mk_session(test_engine, tmp_path)
    rec = Recorder()
    ctx = ToolContext(root=tmp_path, session_id=sid, session_factory=None, send=rec)
    await execute_tool(ctx, "write_file", {"path": "n.txt", "content": "x\n"})
    fc = [e for e in rec.events if e["type"] == "file_change"]
    assert fc and fc[-1]["change_id"] is None and fc[-1]["diff"]

@pytest.mark.asyncio
async def test_ask_user_resume(test_engine, tmp_path):
    async def ask_ok(session_id, questions, timeout):
        return {"answers": [{"options": ["方案A"]}]}
    turns = [done(None, [tool_call("c1", "ask_user", questions=[
        {"question": "选哪个", "options": [{"label": "方案A", "recommended": True}, {"label": "方案B"}]}])]),
        done("好的按方案A")]
    sid, status, evs, sf = await run_once(test_engine, tmp_path, turns, ask=ask_ok)
    assert status == "idle"
    async with sf() as s:
        row = (await s.execute(sql(
            "SELECT tool_result FROM message WHERE session_id=:i AND role='tool'"), {"i": sid})).scalar()
    assert row["answers"] == [{"options": ["方案A"]}]

@pytest.mark.asyncio
async def test_tool_round_text_flushed_before_dispatch(test_engine, tmp_path):
    """工具轮里模型说的文字必须在派发工具**之前**固化下发。

    否则它只活在前端的流式缓冲区里，等下一轮收口到达时被直接抹掉——表现为
    「问答结束后，提问前那段说明文字消失了」，而刷新页面又回来（库里其实存着）。
    见 spec 2026-10-30-chat-ask-trace-live-history-parity 用户故事 9/10/13。"""
    async def ask_ok(session_id, questions, timeout):
        return {"answers": [{"options": ["方案A"]}]}
    turns = [
        [delta("让我看看"),
         *done("让我看看", [tool_call("c1", "ask_user", question="选哪个", options=["方案A", "方案B"])])],
        [delta("好了"), *done("好了")],
    ]
    sid, status, evs, sf = await run_once(test_engine, tmp_path, turns, ask=ask_ok)
    assert status == "idle"
    types = [e["type"] for e in evs]
    # 收口排在同轮第一个 tool_call 之前：前端才会先固化文字、再画提问卡
    assert types.index("message_done") < types.index("tool_call"), types
    # 两轮文字各固化一条，顺序与轮次一致
    assert [e["content"] for e in evs if e["type"] == "message_done"] == ["让我看看", "好了"]
    # 同构的后端一半：事件里的文字序列 == DB assistant 行的 content 序列
    async with sf() as s:
        rows = (await s.execute(sql(
            "SELECT content FROM message WHERE session_id=:i AND role='assistant' ORDER BY seq"),
            {"i": sid})).scalars().all()
    assert list(rows) == ["让我看看", "好了"]

@pytest.mark.asyncio
async def test_tool_round_without_text_emits_no_close(test_engine, tmp_path):
    """模型没说话就调工具的那一轮不发收口事件。

    发了前端就会 push 一条空 assistant 消息（渲染门 `content || reasoning` 会跳过它，
    但消息流里多出一条空记录，实时与历史的条数就此叉开）。
    注意 DB 里的 assistant 行**必须**照常写——它承载 tool_calls，缺了回注模型会 400。"""
    async def ask_ok(session_id, questions, timeout):
        return {"answers": [{"options": ["A"]}]}
    turns = [done(None, [tool_call("c1", "ask_user", question="选哪个", options=["A"])]), done("好了")]
    sid, status, evs, sf = await run_once(test_engine, tmp_path, turns, ask=ask_ok)
    assert [e["content"] for e in evs if e["type"] == "message_done"] == ["好了"]
    async with sf() as s:
        n = (await s.execute(sql(
            "SELECT count(*) FROM message WHERE session_id=:i AND role='assistant'"), {"i": sid})).scalar()
    assert n == 2  # 工具轮那条（content 为空、带 tool_args）+ 终答那条

@pytest.mark.asyncio
async def test_ask_user_legacy_args_wrapped(test_engine, tmp_path):
    # 旧参数（question/options 为字符串）自动包装为单题，保证模型误用时对话不断
    async def ask_ok(session_id, questions, timeout):
        assert questions == [{"question": "选哪个", "options": [{"label": "A"}]}]
        return {"answers": [{"options": ["A"]}]}
    turns = [done(None, [tool_call("c1", "ask_user", question="选哪个", options=["A"])]), done("ok")]
    sid, status, evs, sf = await run_once(test_engine, tmp_path, turns, ask=ask_ok)
    assert status == "idle"

@pytest.mark.asyncio
async def test_ask_user_cancelled(test_engine, tmp_path):
    async def ask_none(session_id, questions, timeout):
        return None
    turns = [done(None, [tool_call("c1", "ask_user", question="?")]), done("好的，已取消该问题")]
    sid, status, evs, sf = await run_once(test_engine, tmp_path, turns, ask=ask_none)
    assert status == "idle"
    tr = next(e for e in evs if e["type"] == "tool_result")
    assert tr["result"].get("cancelled") is True

@pytest.mark.asyncio
async def test_write_confirm_reject(test_engine, tmp_path):
    async def confirm_no(session_id, payload): return False
    turns = [done(None, [tool_call("c1", "write_file", path="a.txt", content="hi")]), done("明白了")]
    sid, status, evs, sf = await run_once(test_engine, tmp_path, turns,
                                          confirm=confirm_no, write_confirm=True)
    assert not (tmp_path / "a.txt").exists()
    tr = next(e for e in evs if e["type"] == "tool_result")
    assert "拒绝" in tr["result"]["error"]
    assert any(e["type"] == "confirm_request" for e in evs)

@pytest.mark.asyncio
async def test_max_rounds(test_engine, tmp_path):
    turns = [done(None, [tool_call(f"c{i}", "write_file", path=f"f{i}.txt", content="x")]) for i in range(2)]
    sid, status, evs, sf = await run_once(test_engine, tmp_path, turns, max_rounds=2)
    assert status == "error"
    assert any(e["type"] == "error" and "最大工具轮数" in e["message"] for e in evs)

@pytest.mark.asyncio
async def test_cancel(test_engine, tmp_path):
    cancel = asyncio.Event()
    async def ask_and_cancel(session_id, questions, timeout):
        cancel.set()
        return None
    turns = [done(None, [tool_call("c1", "ask_user", question="?")]), done("不该到达")]
    sid, status, evs, sf = await run_once(test_engine, tmp_path, turns, ask=ask_and_cancel, cancel=cancel)
    assert status == "idle"
    assert any(e["type"] == "error" and e["message"] == "已取消" for e in evs)

@pytest.mark.asyncio
async def test_cancel_midround_pairs_undispatched_calls(test_engine, tmp_path):
    """中断落在多调用轮的中间时，没派发的那些调用也要补写配对结果行。

    否则库里留下「assistant 行带 N 个 tool_calls、只有 k 条 tool 行」的孤儿：
    _history 只从窗口**头部**修剪孤儿（见 test_history_trim_keeps_tool_pairs），中间的修不到，
    回注模型时 OpenAI 兼容 API 会 400；前端也会把没人认领的调用画成永远待答的死卡。"""
    cancel = asyncio.Event()
    async def ask_and_cancel(session_id, questions, timeout):
        cancel.set()
        return None
    turns = [done(None, [tool_call("c1", "ask_user", question="?"),
                         tool_call("c2", "read_file", path="a.txt")]),
             done("不该到达")]
    sid, status, evs, sf = await run_once(test_engine, tmp_path, turns, ask=ask_and_cancel, cancel=cancel)
    assert status == "idle"
    async with sf() as s:
        rows = (await s.execute(sql(
            "SELECT tool_call_id, tool_name, tool_result FROM message "
            "WHERE session_id=:i AND role='tool' ORDER BY seq"), {"i": sid})).mappings().all()
    assert [r["tool_call_id"] for r in rows] == ["c1", "c2"]
    assert rows[1]["tool_name"] == "read_file"
    assert rows[1]["tool_result"] == {"cancelled": True}
    # 事件也要补齐：实时流少一条记录，就会和刷新后的历史叉开
    types = [e["type"] for e in evs]
    assert types.count("tool_call") == 2 and types.count("tool_result") == 2

@pytest.mark.asyncio
async def test_history_trim_keeps_tool_pairs(test_engine, tmp_path):
    # 窗口边界落在 assistant(tool_calls)/tool 之间会拆散消息对：请求带孤儿 tool 消息
    # 或无结果的 tool_calls，OpenAI 兼容 API 会 400。_history 必须从头部修剪孤儿。
    sid = await mk_session(test_engine, tmp_path)
    sf = make_session_factory(test_engine)
    await add_message(sf, sid, "user", content="第一问")
    await add_message(sf, sid, "assistant", content=None,
                      tool_args={"tool_calls": [tool_call("call_1", "write_file", path="a.txt", content="hi")]})
    await add_message(sf, sid, "tool", tool_call_id="call_1", tool_name="write_file",
                      tool_result={"ok": True})
    # 与 run() 时序一致：新 user 消息先落库再读历史 → window=2 时原始尾部恰切在对中间
    await add_message(sf, sid, "user", content="第二问")
    rec = Recorder()
    runner = AgentRunner(sf, ScriptedLLM([]), cfg_provider(), rec,
                         lambda *a, **k: asyncio.sleep(0, None), lambda *a, **k: True)
    msgs, _ = await runner._history(sid, 2)
    # 核心：任何 tool 消息的 tool_call_id 都能在保留 assistant 的 tool_calls 中找到
    kept_call_ids = {tc.get("id") for m in msgs if m["role"] == "assistant"
                     for tc in (m.get("tool_calls") or [])}
    assert all(m.get("tool_call_id") in kept_call_ids for m in msgs if m["role"] == "tool")
    # 核心：任何保留 assistant 的 tool_calls 都有对应 tool 消息
    kept_tool_ids = {m["tool_call_id"] for m in msgs if m["role"] == "tool"}
    assert all(tc.get("id") in kept_tool_ids for m in msgs if m["role"] == "assistant"
               for tc in (m.get("tool_calls") or []))
    # 头部孤儿 tool(call_1) 被修剪，窗口有效内容从干净的 user(第二问) 开始
    assert [m["role"] for m in msgs] == ["user"]
    # 对照：窗口=3 时对完整，user 开头的正常截断不得被误剪
    full, _ = await runner._history(sid, 3)
    assert [m["role"] for m in full] == ["assistant", "tool", "user"]

@pytest.mark.asyncio
async def test_reasoning_forwarded_and_saved(test_engine, tmp_path):
    turns = [[{"type": "reasoning_delta", "content": "想"}, {"type": "content_delta", "content": "答"},
              {"type": "done", "message": {"role": "assistant", "content": "答", "tool_calls": None,
                                           "reasoning": "想"}}]]
    sid, status, evs, sf = await run_once(test_engine, tmp_path, turns)
    assert any(e["type"] == "reasoning_delta" and e["content"] == "想" for e in evs)
    async with sf() as s:
        r = (await s.execute(sql(
            "SELECT reasoning FROM message WHERE session_id=:i AND role='assistant'"), {"i": sid})).scalar()
    assert r == "想"

@pytest.mark.asyncio
async def test_todo_write_sync_and_persist(test_engine, tmp_path):
    todos = [{"content": "实现A", "status": "in_progress"}, {"content": "写测试", "status": "pending"}]
    turns = [done(None, [tool_call("c1", "todo_write", todos=todos)]), done("清单已建")]
    sid, status, evs, sf = await run_once(test_engine, tmp_path, turns)
    assert status == "idle"
    sync = next(e for e in evs if e["type"] == "todo_sync")
    assert sync["todos"] == todos
    async with sf() as s:
        st = (await s.execute(sql(
            "SELECT todo_state FROM chat_session WHERE id=:i"), {"i": sid})).scalar()
    assert st == todos

@pytest.mark.asyncio
async def test_todo_write_invalid_status(test_engine, tmp_path):
    turns = [done(None, [tool_call("c1", "todo_write", todos=[{"content": "x", "status": "bad"}])]),
             done("知道了")]
    sid, status, evs, sf = await run_once(test_engine, tmp_path, turns)
    tr = next(e for e in evs if e["type"] == "tool_result")
    assert tr["result"]["ok"] is False and "非法" in tr["result"]["error"]

@pytest.mark.asyncio
async def test_mention_expanded_into_context(test_engine, tmp_path):
    (tmp_path / "a.txt").write_text("文件内容XYZ", encoding="utf-8")
    llm = ScriptedLLM([[{"type": "content_delta", "content": "好"},
                        {"type": "done", "message": {"role": "assistant", "content": "好", "tool_calls": None}}]])
    sid = await mk_session(test_engine, tmp_path)
    sf = make_session_factory(test_engine)
    rec = Recorder()
    runner = AgentRunner(sf, llm, cfg_provider(), rec, lambda *a, **k: asyncio.sleep(0, None), lambda *a, **k: True)
    await runner.run(sid, "请看 @a.txt", asyncio.Event())
    # 模型收到的是展开后的 <file> 块
    assert '<file path="a.txt">' in llm.received[0][1]["content"]
    # 落库双份：content 原文、context_content 展开
    async with sf() as s:
        row = (await s.execute(sql(
            "SELECT content, context_content FROM message WHERE session_id=:i AND role='user'"),
            {"i": sid})).first()
    assert row[0] == "请看 @a.txt" and "文件内容XYZ" in row[1]

@pytest.mark.asyncio
async def test_auto_compaction_triggers(test_engine, tmp_path):
    sid = await mk_session(test_engine, tmp_path)
    sf = make_session_factory(test_engine)
    for i in range(12):
        await add_message(sf, sid, "user" if i % 2 == 0 else "assistant", content=f"历史{i}的一些内容")
    llm = ScriptedLLM([
        [{"type": "done", "message": {"role": "assistant", "content": "摘要：历史已压缩", "tool_calls": None}}],
        [delta("收到"), {"type": "done", "message": {"role": "assistant", "content": "收到", "tool_calls": None}}]])
    rec = Recorder()

    async def _p():
        return {"agent.write_confirm": False, "agent.ask_timeout_seconds": 0,
                "agent.max_tool_rounds": 30, "agent.history_window": 40,
                "agent.auto_compact_chars": 10, "agent.compact_min_new": 1,
                "agent.compact_keep_recent": 8, "clarify.enabled": False}
    runner = AgentRunner(sf, llm, _p, rec,
                         lambda *a, **k: asyncio.sleep(0, None), lambda *a, **k: True)
    status = await runner.run(sid, "继续", asyncio.Event())
    assert status == "idle"
    system_msg = llm.received[1][0]
    assert system_msg["role"] == "system" and "<context_summary>" in system_msg["content"]
    assert "摘要：历史已压缩" in system_msg["content"]
    # 自动压缩同样要在聊天流里可见：开始帧先于完成帧（前端据此在流中放/收「压缩中」标记）
    types = [e["type"] for e in rec.events]
    assert "compacting" in types
    assert types.index("compacting") < types.index("compaction_done")

# 平台清单合法形态（app_service 写入方的全字段产物），供契约注入用例复用
APP_META = {"schema": "zc-app/1", "name": "来料登记", "slug": "lailiao", "scale": "small",
            "stack": "vue3-fastapi", "ports": {"prod": 8210, "dev": 8211},
            "database": {"dev": {"name": "lailiao_dev", "user": "lailiao_dev"},
                         "prod": {"name": "lailiao_prod", "user": "lailiao_prod"}},
            "health": {"frontend": "/", "backend": "/api/health"}}

@pytest.mark.asyncio
async def test_app_contract_injected_into_system(test_engine, tmp_path):
    # I-1 回归保护：应用工作区的契约必须经 loop 真实消息构建路径进 system（删掉注入该行此例即红）
    (tmp_path / "app.json").write_text(json.dumps(APP_META, ensure_ascii=False), encoding="utf-8")
    llm = ScriptedLLM([[delta("好"), *done("好了")]])
    sid = await mk_session(test_engine, tmp_path)
    sf = make_session_factory(test_engine)
    runner = AgentRunner(sf, llm, cfg_provider(), Recorder(),
                         lambda *a, **k: asyncio.sleep(0, None), lambda *a, **k: True)
    status = await runner.run(sid, "写个页面", asyncio.Event())
    assert status == "idle"
    assert llm.received[0][0]["role"] == "system"
    c = llm.received[0][0]["content"]
    # 锚用契约内的 slug/端口事实，不只断言标题字符串
    assert "【应用契约】" in c and "lailiao" in c and "8211" in c
    assert c.index("8. 上下文可能被压缩") < c.index("【应用契约】")   # 钉住顺序：v1 规则在前

@pytest.mark.asyncio
async def test_no_app_contract_without_app_json(test_engine, tmp_path):
    # 反例：无 app.json 的普通工作区，system 不含契约段（v1 零影响钉到行为层）
    llm = ScriptedLLM([[delta("好"), *done("好了")]])
    sid = await mk_session(test_engine, tmp_path)
    sf = make_session_factory(test_engine)
    runner = AgentRunner(sf, llm, cfg_provider(), Recorder(),
                         lambda *a, **k: asyncio.sleep(0, None), lambda *a, **k: True)
    status = await runner.run(sid, "写个页面", asyncio.Event())
    assert status == "idle"
    assert "【应用契约】" not in llm.received[0][0]["content"]

@pytest.mark.asyncio
async def test_max_rounds_zero_means_unlimited(test_engine, tmp_path):
    # 0/负数 = 不限工具轮数（与超时参数 <=0 不限制的约定一致），而非 range(0) 零轮即错
    turns = [done(None, [tool_call(f"c{i}", "write_file", path=f"f{i}.txt", content="x")])
             for i in range(5)] + [done("完成了")]
    sid, status, evs, sf = await run_once(test_engine, tmp_path, turns, max_rounds=0)
    assert status == "idle"
    assert (tmp_path / "f4.txt").read_text(encoding="utf-8") == "x"

@pytest.mark.asyncio
async def test_current_todos_injected_into_system(test_engine, tmp_path):
    # 跨运行历史窗口会截掉旧 todo_write 调用，todo_state 必须回灌 system 尾部，
    # 否则模型第二轮起根本看不见清单，"随做随更"无从谈起
    sid = await mk_session(test_engine, tmp_path)
    sf = make_session_factory(test_engine)
    todos = [{"content": "实现模块A", "status": "in_progress"},
             {"content": "补A的测试", "status": "pending"}]
    async with sf() as s:
        await s.execute(sql("UPDATE chat_session SET todo_state = CAST(:j AS JSONB) WHERE id=:i"),
                        {"j": json.dumps(todos, ensure_ascii=False), "i": sid})
        await s.commit()
    llm = ScriptedLLM([[delta("好的"), *done("好的")]])
    runner = AgentRunner(sf, llm, cfg_provider(), Recorder(),
                         lambda *a, **k: asyncio.sleep(0, None), lambda *a, **k: True)
    await runner.run(sid, "继续", asyncio.Event())
    sysmsg = llm.received[0][0]["content"]
    # 锚定注入块形态（空行+数组），与规则6文本里提及的标签名区分
    assert "\n\n<current_todos>\n[" in sysmsg and "实现模块A" in sysmsg

@pytest.mark.asyncio
async def test_no_current_todos_block_when_empty(test_engine, tmp_path):
    # 反例守卫：无清单会话不注入空块（省 token，也避免模型对空块瞎更新）
    sid = await mk_session(test_engine, tmp_path)
    sf = make_session_factory(test_engine)
    llm = ScriptedLLM([[delta("好的"), *done("好的")]])
    runner = AgentRunner(sf, llm, cfg_provider(), Recorder(),
                         lambda *a, **k: asyncio.sleep(0, None), lambda *a, **k: True)
    await runner.run(sid, "你好", asyncio.Event())
    assert "\n\n<current_todos>" not in llm.received[0][0]["content"]


# ---- Ticket 004：prompt 注入 <available_skills>（session → workspace.owner_user_id → 已启用技能）----

async def _mk_owned_session(test_engine, tmp_path) -> tuple[int, int]:
    """建 actor + 带 owner_user_id 的 workspace + chat_session，返回 (sid, uid)。"""
    sf = make_session_factory(test_engine)
    async with sf() as s:
        uid = (await s.execute(sql(
            "INSERT INTO actor (nickname) VALUES ('loop-owner') RETURNING id"))).scalar()
        ws = (await s.execute(sql(
            "INSERT INTO workspace (name, root_path, owner_user_id) VALUES ('t', :p, :u) RETURNING id"),
            {"p": str(tmp_path), "u": uid})).scalar()
        sid = (await s.execute(sql(
            "INSERT INTO chat_session (workspace_id) VALUES (:w) RETURNING id"), {"w": ws})).scalar()
        await s.commit()
    return sid, uid


async def _add_skill(test_engine, uid, slug, name, desc, path, enabled=True):
    sf = make_session_factory(test_engine)
    async with sf() as s:
        await s.execute(sql(
            "INSERT INTO user_skill (owner_user_id, slug, name, description, source_type, "
            "source_url, skill_dir, enabled) "
            "VALUES (:u, :sl, :n, :d, 'url', 'https://x.invalid/sk', :p, :e)"),
            {"u": uid, "sl": slug, "n": name, "d": desc, "p": path, "e": enabled})
        await s.commit()


async def _system_content(test_engine, sf, sid, text_="继续") -> str:
    llm = ScriptedLLM([[delta("好"), *done("好了")]])
    runner = AgentRunner(sf, llm, cfg_provider(), Recorder(),
                         lambda *a, **k: asyncio.sleep(0, None), lambda *a, **k: True)
    await runner.run(sid, text_, asyncio.Event())
    return llm.received[0][0]["content"]


@pytest.mark.asyncio
async def test_enabled_skill_injected(test_engine, tmp_path):
    sid, uid = await _mk_owned_session(test_engine, tmp_path)
    sf = make_session_factory(test_engine)
    await _add_skill(test_engine, uid, "pdf", "PDF 导出", "生成 PDF 报告",
                     str(tmp_path / "skills" / "pdf"), enabled=True)
    c = await _system_content(test_engine, sf, sid)
    assert "<available_skills>" in c and "PDF 导出" in c and "生成 PDF 报告" in c
    assert str(tmp_path / "skills" / "pdf") in c  # 母本目录绝对路径入清单


@pytest.mark.asyncio
async def test_disabled_skill_not_injected(test_engine, tmp_path):
    # 故事10：停用技能不出现在块中（enabled 布尔驱动）
    sid, uid = await _mk_owned_session(test_engine, tmp_path)
    sf = make_session_factory(test_engine)
    await _add_skill(test_engine, uid, "on", "启用技能", "在用", str(tmp_path / "a"), enabled=True)
    await _add_skill(test_engine, uid, "off", "停用技能", "停用", str(tmp_path / "b"), enabled=False)
    c = await _system_content(test_engine, sf, sid)
    assert "启用技能" in c
    assert "停用技能" not in c


@pytest.mark.asyncio
async def test_no_available_skills_block_without_enabled(test_engine, tmp_path):
    # 无启用技能（全部停用）不注入空块，与 current_todos 空守卫同口径
    sid, uid = await _mk_owned_session(test_engine, tmp_path)
    sf = make_session_factory(test_engine)
    await _add_skill(test_engine, uid, "off", "停用技能", "停用", str(tmp_path / "b"), enabled=False)
    c = await _system_content(test_engine, sf, sid)
    assert "<available_skills>" not in c


@pytest.mark.asyncio
async def test_skill_injection_is_user_scoped(test_engine, tmp_path):
    # 隔离：会话归属 alice，只注入 alice 的技能；bob 的同 owner 表技能不得泄露
    sid, alice = await _mk_owned_session(test_engine, tmp_path)
    sf = make_session_factory(test_engine)
    async with sf() as s:
        bob = (await s.execute(sql(
            "INSERT INTO actor (nickname) VALUES ('bob') RETURNING id"))).scalar()
        await s.commit()
    await _add_skill(test_engine, alice, "a", "Alice技能", "属于A", str(tmp_path / "a"))
    await _add_skill(test_engine, bob, "b", "Bob技能", "属于B", str(tmp_path / "b"))
    c = await _system_content(test_engine, sf, sid)
    assert "Alice技能" in c
    assert "Bob技能" not in c


# ---- 预览工具接线（规格 docs/specs/2026-10-29-agent-preview-tools.md §5.1 / §5.6 / §8.1）----

from app.deploy.hub import DeployHub


async def mk_app_session(test_engine, tmp_path, *, stack="vue3-fastapi", status="stopped",
                         slug="pvloop", dev_port=8271, prod_port=8270):
    """workspace + app_project + chat_session 三行一起建，返回 (sid, app_id, sf)。

    owner 一律留 NULL：与 test_agent_loop.py 既有会话同形（WS 所有权校验不在本文件的靶心上），
    `resolve_session_app` 传 owner=None 时不加过滤，正好命中。
    """
    sf = make_session_factory(test_engine)
    async with sf() as s:
        wid = (await s.execute(sql(
            "INSERT INTO workspace (name, root_path) VALUES ('pv', :p) RETURNING id"),
            {"p": str(tmp_path)})).scalar()
        aid = (await s.execute(sql(
            "INSERT INTO app_project (workspace_id, name, slug, scale, stack, prod_port, dev_port) "
            "VALUES (:w, '预览应用', :sl, 'small', :st, :pp, :dp) RETURNING id"),
            {"w": wid, "sl": slug, "st": stack, "pp": prod_port, "dp": dev_port})).scalar()
        sid = (await s.execute(sql(
            "INSERT INTO chat_session (workspace_id) VALUES (:w) RETURNING id"),
            {"w": wid})).scalar()
        if status != "stopped":
            await s.execute(sql("UPDATE app_project SET status_dev=:st WHERE id=:i"),
                            {"st": status, "i": aid})
        await s.commit()
    return sid, aid, sf


def preview_cfg(**over):
    async def _p():
        base = {"agent.write_confirm": False, "agent.ask_timeout_seconds": 0,
                "agent.max_tool_rounds": 10, "agent.history_window": 40, "clarify.enabled": False}
        base.update(over)
        return base
    return _p


def mk_runner(sf, llm, rec, *, confirm=None, hub=None, tasks=None, cfg=None):
    """带预览接线的 runner：hub/tasks 缺省 None（= 不接线），cfg 缺省平台默认那几键。

    默认 confirm 用 `asyncio.sleep(0, True)` 而不是裸 `lambda: True`：`AgentRunner.confirm`
    按契约是 awaitable（WS 侧传的是 `hub.confirm`），preview_restart 的确认分支会 `await` 它，
    同步替身会在那里抛 TypeError 并被 run() 吞成 error 事件（工具结果就再也拿不到了）。
    """
    return AgentRunner(sf, llm, cfg or preview_cfg(), rec,
                       lambda *a, **k: asyncio.sleep(0, None),
                       confirm or (lambda *a, **k: asyncio.sleep(0, True)),
                       deploy_hub=hub, dev_tasks=tasks)


def tool_names(llm, round_index=0):
    return [t["function"]["name"] for t in llm.received_tools[round_index]]


@pytest.mark.asyncio
async def test_preview_tools_mounted_only_for_app_workspace(test_engine, tmp_path):
    sid, aid, sf = await mk_app_session(test_engine, tmp_path)
    llm = ScriptedLLM([[delta("好"), *done("好了")]])
    await mk_runner(sf, llm, Recorder()).run(sid, "起个预览", asyncio.Event())
    assert tool_names(llm)[-13:] == ["preview_status", "preview_logs", "preview_restart",
                                     "db_query", "db_write", "preview_page",
                                     "browser_open", "browser_step", "browser_close",
                                     "release_preflight", "db_schema", "sqlchange_status",
                                     "run_check"]
    assert "<app_diagnostics>" in llm.received[0][0]["content"]
    assert "<app_checks>" in llm.received[0][0]["content"]
    assert "<preview>" in llm.received[0][0]["content"]
    assert "<browser>" in llm.received[0][0]["content"]
    assert "<browser_session>" in llm.received[0][0]["content"]
    assert "vue3-fastapi" in llm.received[0][0]["content"]


@pytest.mark.asyncio
async def test_no_preview_tools_without_app_row(test_engine, tmp_path):
    """US-01：非应用工作区里模型根本看不到这三个工具（不是置灰，是不存在）。"""
    sid = await mk_session(test_engine, tmp_path)
    sf = make_session_factory(test_engine)
    llm = ScriptedLLM([[delta("好"), *done("好了")]])
    await mk_runner(sf, llm, Recorder()).run(sid, "起个预览", asyncio.Event())
    names = tool_names(llm)
    assert not any(n.startswith("preview_") for n in names)
    assert not any(n.startswith("db_") for n in names)
    assert not any(n.startswith("browser_") for n in names)   # 交互工具同闸（规格 §5.1）
    assert "release_preflight" not in names and "sqlchange_status" not in names
    assert "run_check" not in names
    assert "<preview>" not in llm.received[0][0]["content"]
    assert "<app_diagnostics>" not in llm.received[0][0]["content"]
    assert "<app_checks>" not in llm.received[0][0]["content"]
    assert "<database>" not in llm.received[0][0]["content"]
    assert "<browser>" not in llm.received[0][0]["content"]
    assert "<browser_session>" not in llm.received[0][0]["content"]


@pytest.mark.asyncio
async def test_duplicate_workspace_binding_fails_closed_with_note(test_engine, tmp_path):
    """US-01/§5.1：一个工作区绑了两个应用 → 不挂载工具，但 system 里给模型一句能转述的原因。"""
    sid, aid, sf = await mk_app_session(test_engine, tmp_path)
    async with sf() as s:
        wid = (await s.execute(sql("SELECT workspace_id FROM app_project WHERE id=:i"),
                               {"i": aid})).scalar()
        await s.execute(sql(
            "INSERT INTO app_project (workspace_id, name, slug, scale, stack, prod_port, dev_port) "
            "VALUES (:w, '重复应用', 'pvloop2', 'small', 'vue3-fastapi', 8273, 8272)"), {"w": wid})
        await s.commit()
    llm = ScriptedLLM([[delta("好"), *done("好了")]])
    await mk_runner(sf, llm, Recorder()).run(sid, "起个预览", asyncio.Event())
    assert not any(n.startswith("preview_") for n in tool_names(llm))
    assert not any(n.startswith("db_") for n in tool_names(llm))   # 数据库工具同闸 fail closed
    assert not any(n.startswith("browser_") for n in tool_names(llm))
    assert "<database>" not in llm.received[0][0]["content"]
    assert "<browser>" not in llm.received[0][0]["content"]
    assert "<browser_session>" not in llm.received[0][0]["content"]
    assert "绑定了多个应用" in llm.received[0][0]["content"]


@pytest.mark.asyncio
async def test_preview_prompt_follows_the_stack(test_engine, tmp_path):
    sid, aid, sf = await mk_app_session(test_engine, tmp_path, stack="vue3-springboot",
                                        slug="pvloopj", dev_port=8275, prod_port=8274)
    llm = ScriptedLLM([[delta("好"), *done("好了")]])
    await mk_runner(sf, llm, Recorder()).run(sid, "改个后端", asyncio.Event())
    c = llm.received[0][0]["content"]
    assert "必须 preview_restart" in c and "uvicorn" not in c     # 按栈差异化，不串味
    assert c.rstrip().endswith("</browser_session>")              # 块在 system 尾部（契约/技能之后）
    assert "</preview>" in c and "<browser>" in c                 # 浏览器指引跟在预览块之后
    assert "</browser>" in c and "<browser_session>" in c         # 交互指引跟在快检指引之后


@pytest.mark.asyncio
async def test_run_close_sweeps_the_browser_session(test_engine, tmp_path, monkeypatch):
    """US-21：模型忘了 browser_close，run 收口也要 best-effort 发 DELETE。

    sidecar 在 `mem_limit: 1g` 的容器里攒僵尸 context 是唯一运维义务；绑定与收口都只在服务端，
    模型拿到的结果里既没有 session_id，也没有被重写过的容器地址（裁定 R-16）。
    """
    from app.agent import browser_session_tools as bst

    sid, aid, sf = await mk_app_session(test_engine, tmp_path, status="running", slug="pvsess",
                                        dev_port=8287, prod_port=8286)

    async def fake_open(url, **kw):
        return {"ok": True, "session_id": "secret-sid", "title": "订单", "text": "共 3 条",
                "url": "http://192.168.65.254:8287/orders", "interactive": [],
                "console": [], "failed_requests": [], "elapsed_ms": 10}, ""

    deleted = []

    async def fake_delete(sid_):
        deleted.append(sid_)
        return True

    monkeypatch.setattr(bst, "_sidecar_open", fake_open)
    monkeypatch.setattr(bst, "_sidecar_delete", fake_delete)
    rec = Recorder()
    turns = [done(None, [tool_call("c1", "browser_open", path="/orders")]), done("看完了")]
    await mk_runner(sf, ScriptedLLM(turns), rec, tasks={}).run(sid, "走一遍", asyncio.Event())
    tr = next(e for e in rec.events if e["type"] == "tool_result" and e["name"] == "browser_open")
    assert tr["result"]["ok"] is True
    blob = json.dumps(tr["result"], ensure_ascii=False)
    assert "secret-sid" not in blob and "192.168.65.254" not in blob
    assert deleted == ["secret-sid"]


@pytest.mark.asyncio
async def test_error_path_still_closes_the_browser_session(test_engine, tmp_path, monkeypatch):
    """收口覆盖每一条出口：run 打成 error 终态也不能把会话留在 sidecar 里（只有 TTL 兜底就行）。"""
    from app.agent import browser_session_tools as bst

    sid, aid, sf = await mk_app_session(test_engine, tmp_path, status="running", slug="pvsess2",
                                        dev_port=8289, prod_port=8288)

    async def fake_open(url, **kw):
        return {"ok": True, "session_id": "sid-err", "title": "t", "text": "", "url": "http://h/",
                "interactive": [], "console": [], "failed_requests": [], "elapsed_ms": 1}, ""

    deleted = []

    async def fake_delete(sid_):
        deleted.append(sid_)
        return True

    monkeypatch.setattr(bst, "_sidecar_open", fake_open)
    monkeypatch.setattr(bst, "_sidecar_delete", fake_delete)
    # 只给一轮：派发完 browser_open 后 ScriptedLLM 会在第二轮炸掉，run 落 error 终态
    turns = [done(None, [tool_call("c1", "browser_open", path="/")])]
    rec = Recorder()
    status = await mk_runner(sf, ScriptedLLM(turns), rec, tasks={}).run(sid, "走一遍", asyncio.Event())
    assert status == "error"
    assert deleted == ["sid-err"]


@pytest.mark.asyncio
async def test_open_expect_survives_the_loop_into_the_sidecar_payload(test_engine, tmp_path,
                                                                     monkeypatch):
    """US-06 走真 loop：模型在 `browser_open` 上写出的 expect 必须原样送达 sidecar。

    单元级那条（`test_open_expect_reaches_the_sidecar_request_body`）钉的是 `_open` 到线协议这一程，
    这里钉的是模型到 `_open` 这一程：loop 会解析 args、把它交给通用凭据遮断与位次计数那两道闸
    （裁定 R-33/R-35/R-38），expect 是嵌套对象、顶层没有 `value` 键，因此既不该被改写、也不该被
    遮成占位符——一旦哪道闸把它当填表值处理，模型收到的就是「等了一句占位符」。
    返回值一侧照旧：判定结论进得来，session_id 与被重写过的容器地址进不来（R-16/ADR-0037）。
    """
    from app.agent import browser_session_tools as bst

    sid, aid, sf = await mk_app_session(test_engine, tmp_path, status="running", slug="psexp",
                                        dev_port=8311, prod_port=8310)
    seen = []

    async def fake_open(url, **kw):
        seen.append(kw.get("expect"))
        return {"ok": True, "session_id": "sid-secret", "title": "看板", "text": "订单列表",
                "url": "http://192.168.65.254:8311/", "interactive": [], "console": [],
                "failed_requests": [], "page_errors": [], "passed": True,
                "note": "页面文本未包含「订单列表」", "elapsed_ms": 10}, ""

    deleted = []

    async def fake_delete(sid_):
        deleted.append(sid_)
        return True

    monkeypatch.setattr(bst, "_sidecar_open", fake_open)
    monkeypatch.setattr(bst, "_sidecar_delete", fake_delete)
    rec = Recorder()
    turns = [done(None, [tool_call("c1", "browser_open", path="/",
                                   expect={"type": "text_contains", "value": "订单列表"})]),
             done("首屏等到了")]
    await mk_runner(sf, ScriptedLLM(turns), rec, tasks={}).run(sid, "走一遍", asyncio.Event())

    expect = {"type": "text_contains", "value": "订单列表"}
    assert seen == [expect]                                   # 一字未改地送达 sidecar
    tr = next(e for e in rec.events if e["type"] == "tool_result" and e["name"] == "browser_open")
    assert tr["result"]["passed"] is True and tr["result"]["note"] == ""   # 通过时不带失败措辞
    blob = json.dumps(rec.events, ensure_ascii=False)
    assert "sid-secret" not in blob and "192.168.65.254" not in blob
    # 落库与推送那两份 args：expect 是嵌套对象、顶层没有 `value` 键，遮断闸对它应当完全不动。
    # 结构一旦被动过（被当填表值遮成占位符、或被裁掉一层），模型下一轮重建历史时就读不回它声明过什么。
    async with sf() as s:
        rows = (await s.execute(sql(
            "SELECT tool_args FROM message WHERE session_id=:i AND role='assistant'"
            " AND tool_args IS NOT NULL ORDER BY seq"), {"i": sid})).scalars().all()
    opened = next(r["tool_calls"][0] for r in rows if isinstance(r, dict)
                  and (r["tool_calls"][0]["function"]["name"] == "browser_open"))
    assert json.loads(opened["function"]["arguments"]) == {"path": "/", "expect": expect}
    ev = next(e for e in rec.events if e["type"] == "tool_call" and e["name"] == "browser_open")
    assert ev["args"]["expect"] == expect


async def _fill_env(test_engine, tmp_path, monkeypatch, bst, *, slug, dev_port, prod_port):
    """跑起来的预览应用 + sidecar 替身：open 回一份含 sensitive 元素的清单，step 记下发的载荷。

    返回 `(会话 id, 应用 id, session_factory, 打到 sidecar 的载荷)`。清单里 e2 是密码框
    （sensitive:true）、e3 是备注框（false）——两条参数侧遮断的用例只差这个 ref。
    """
    sid, aid, sf = await mk_app_session(test_engine, tmp_path, status="running", slug=slug,
                                        dev_port=dev_port, prod_port=prod_port)
    refs = [{"ref": "e2", "role": "textbox", "name": "密码", "sensitive": True},
            {"ref": "e3", "role": "textbox", "name": "备注", "sensitive": False}]
    seen = []

    async def fake_open(url, **kw):
        return {"ok": True, "session_id": "sid-secret", "title": "登录", "text": "",
                "url": f"http://192.168.65.254:{dev_port}/", "interactive": refs,
                "console": [], "failed_requests": [], "page_errors": [], "elapsed_ms": 1}, ""

    async def fake_step(session_id, payload):
        seen.append((session_id, payload))
        return {"ok": True, "title": "登录", "text": "",
                "url": f"http://192.168.65.254:{dev_port}/", "interactive": refs,
                "interactive_truncated": False, "dialog": None, "console_new": [],
                "failed_requests_new": [], "page_errors_new": [], "truncated": False,
                "timed_out": False, "steps_charged": True, "elapsed_ms": 1}, ""

    monkeypatch.setattr(bst, "_sidecar_open", fake_open)
    monkeypatch.setattr(bst, "_sidecar_step", fake_step)
    return sid, aid, sf, seen


@pytest.mark.asyncio
async def test_sensitive_fill_value_is_masked_before_persisting_the_args(test_engine, tmp_path,
                                                                         monkeypatch):
    """US-19 的参数侧（裁定 R-33）：走一遍真 loop，钉住「落库/推送遮、sidecar 与当轮回注不遮」。

    通用规则表按凭据**形态**匹配（`password=xxx`、`sk-…`），`hunter2` 这种用户自己应用里的
    口令一个字符都不成形态，抓不住 —— 它会原样进 `message.tool_args`，并被通用折叠块明文打印。
    Task 4/5 只遮了结果侧的 `browser_log`，参数侧这半边在两轮审查里都没进 diff。
    """
    from app.agent import browser_session_tools as bst

    sid, aid, sf, seen = await _fill_env(test_engine, tmp_path, monkeypatch, bst, slug="pvsens1",
                                         dev_port=8301, prod_port=8300)
    turns = [done(None, [tool_call("c1", "browser_open", path="/")]),
             done(None, [tool_call("c2", "browser_step", action="fill", target="e2",
                                   value="hunter2")]),
             done("填好了")]
    rec = Recorder()
    llm = ScriptedLLM(turns)
    await mk_runner(sf, llm, rec, tasks={}).run(sid, "登录一遍", asyncio.Event())

    # ① 落库那一份：口令不在，但参数结构（action/target）照旧读得出这一步做了什么
    async with sf() as s:
        rows = (await s.execute(sql(
            "SELECT tool_args FROM message WHERE session_id=:i AND role='assistant' "
            "AND tool_args IS NOT NULL ORDER BY seq"), {"i": sid})).scalars().all()
    blob = json.dumps(list(rows), ensure_ascii=False)
    assert "hunter2" not in blob
    step_tc = rows[1]["tool_calls"][0]
    assert step_tc["id"] == "c2" and step_tc["function"]["name"] == "browser_step"
    masked = json.loads(step_tc["function"]["arguments"])
    assert masked["value"] == "<redacted:sensitive_input>"
    assert masked["action"] == "fill" and masked["target"] == "e2"
    # ② WS 推送那一份：前端通用折叠块打印的就是它
    ev = next(e for e in rec.events if e["type"] == "tool_call" and e["name"] == "browser_step")
    assert ev["args"]["value"] == "<redacted:sensitive_input>"
    # ③ 原值必须送达 sidecar，否则 US-08 的登录根本填不进去
    assert seen[0][1]["value"] == "hunter2"
    # ④ 当轮回注模型仍是原值：下一步重试同一个表单时读回占位符就会把占位符真填进去
    #    （跨轮从净化库重建，那时才是遮断版——与 tool_result 那条同一个口径）
    back = [m for m in llm.received[2] if m["role"] == "assistant" and m.get("tool_calls")]
    assert json.loads(back[-1]["tool_calls"][0]["function"]["arguments"])["value"] == "hunter2"


@pytest.mark.asyncio
async def test_non_sensitive_fill_value_stays_plain_in_the_persisted_args(test_engine, tmp_path,
                                                                          monkeypatch):
    """一刀切全遮会毁掉可读性：非 sensitive 目标的 value（备注、地址）照旧明文进历史。"""
    from app.agent import browser_session_tools as bst

    sid, aid, sf, seen = await _fill_env(test_engine, tmp_path, monkeypatch, bst, slug="pvsens2",
                                        dev_port=8303, prod_port=8302)
    turns = [done(None, [tool_call("c1", "browser_open", path="/")]),
             done(None, [tool_call("c2", "browser_step", action="fill", target="e3",
                                   value="第一单")]),
             done("填好了")]
    rec = Recorder()
    await mk_runner(sf, ScriptedLLM(turns), rec, tasks={}).run(sid, "填个备注", asyncio.Event())
    async with sf() as s:
        rows = (await s.execute(sql(
            "SELECT tool_args FROM message WHERE session_id=:i AND role='assistant' "
            "AND tool_args IS NOT NULL ORDER BY seq"), {"i": sid})).scalars().all()
    assert json.loads(rows[1]["tool_calls"][0]["function"]["arguments"])["value"] == "第一单"
    ev = next(e for e in rec.events if e["type"] == "tool_call" and e["name"] == "browser_step")
    assert ev["args"]["value"] == "第一单"
    assert seen[0][1]["value"] == "第一单"


@pytest.mark.asyncio
async def test_two_steps_in_one_round_mask_the_same_value_on_both_sides(test_engine, tmp_path,
                                                                        monkeypatch):
    """Critical C-1：一条 assistant 消息里发两个 browser_step 时，参数侧只能用「本轮开始时」那份清单。

    形状是登录页最常见的那种：先 `click` 展开带口令框的表单，紧接着 `fill` 那个**新出现**的 ref。
    loop 落库那行写在派发**之前**（`loop.py`），用的只能是轮初的 `browser_refs`；而 `_step` 的结果侧
    读的是各步**动作前**的清单，第二步已经能看到第一步返回的新清单。旧口径下 `is_sensitive_ref`
    对不在轮初清单里的 ref 返回 False（没有「未知」这第三态），于是同一个 value 一侧明文、
    一侧遮断——`message.tool_args` 与 WS `tool_call` 里是 `hunter2`，`browser_log` 里是占位符。
    一轮多调用是结构性支持的（`llm.py` 按 index 累加并输出全部调用，loop 顺序派发，没有任何地方
    关掉它），所以这条不是理论路径。

    这里走真 loop 钉「两侧对同一个 value 给同一个结论」：落库、WS 事件、`browser_log` 三处都必须
    遮，而 sidecar 与当轮回注必须拿到原值（否则 US-08 的登录根本填不进去）。
    """
    from app.agent import browser_session_tools as bst

    sid, aid, sf, seen = await _fill_env(test_engine, tmp_path, monkeypatch, bst, slug="pvsens3",
                                         dev_port=8305, prod_port=8304)
    # 轮初清单只有 e2（密码）与 e3（备注）；click 之后 sidecar 返回的新清单里才出现 e9（口令框）
    fresh = [{"ref": "e9", "role": "textbox", "name": "支付密码", "sensitive": True},
             {"ref": "e3", "role": "textbox", "name": "备注", "sensitive": False}]

    async def fake_step(session_id, payload):
        seen.append((session_id, payload))
        return {"ok": True, "title": "登录", "text": "",
                "url": "http://192.168.65.254:8305/", "interactive": fresh,
                "interactive_truncated": False, "dialog": None, "console_new": [],
                "failed_requests_new": [], "page_errors_new": [], "truncated": False,
                "timed_out": False, "steps_charged": True, "elapsed_ms": 1}, ""

    monkeypatch.setattr(bst, "_sidecar_step", fake_step)
    # 抓住真 handler 的 ctx：结果侧那份账（browser_log）只在 run_state 里，别处读不到
    captured = {}
    real_run = bst.run_browser_session_tool

    async def spy_run(ctx, name, args):
        captured["ctx"] = ctx
        return await real_run(ctx, name, args)

    monkeypatch.setattr(bst, "run_browser_session_tool", spy_run)

    turns = [done(None, [tool_call("c1", "browser_open", path="/")]),
             done(None, [tool_call("c2", "browser_step", action="click", target="e1"),
                         tool_call("c3", "browser_step", action="fill", target="e9",
                                   value="hunter2")]),
             done("填好了")]
    rec = Recorder()
    llm = ScriptedLLM(turns)
    await mk_runner(sf, llm, rec, tasks={}).run(sid, "登录一遍", asyncio.Event())

    # ① 落库那一份：轮初清单里没有 e9，未知即遮——同一个 value 与结果侧给出同一个结论
    async with sf() as s:
        rows = (await s.execute(sql(
            "SELECT role, tool_args, tool_result FROM message WHERE session_id=:i ORDER BY seq"),
            {"i": sid})).all()
    blob = json.dumps([list(r) for r in rows], ensure_ascii=False)
    assert "hunter2" not in blob, "口令明文进了消息表（tool_args 或 tool_result）"
    step_row = next(list(r)[1] for r in rows
                    if isinstance(list(r)[1], dict)
                    and len(list(r)[1].get("tool_calls") or []) == 2)
    calls = step_row["tool_calls"]
    assert [c["function"]["name"] for c in calls] == ["browser_step", "browser_step"]
    fill_tc = calls[1]
    assert fill_tc["id"] == "c3"
    masked = json.loads(fill_tc["function"]["arguments"])
    assert masked["value"] == "<redacted:sensitive_input>"
    assert masked["action"] == "fill" and masked["target"] == "e9"   # 结构照旧读得出做了什么
    assert "value" not in json.loads(calls[0]["function"]["arguments"])  # click 没有 value，不动

    # ② WS 推送那一份（前端通用折叠块打印的就是它）
    evs = [e for e in rec.events if e["type"] == "tool_call" and e["name"] == "browser_step"]
    assert len(evs) == 2
    assert evs[1]["args"]["value"] == "<redacted:sensitive_input>"
    assert "hunter2" not in json.dumps(evs, ensure_ascii=False)

    # ③ 结果侧那份账：browser_log 对同一个 value 给出同一个结论（遮）
    logged = [s for s in captured["ctx"].run_state["browser_log"] if s.get("action") == "fill"]
    assert len(logged) == 1
    assert logged[0]["value_redacted"] is True and "hunter2" not in str(logged[0])

    # ④ 原值照样送达 sidecar（两次调用都在），否则 US-08 的登录填不进去
    assert [p["value"] for _sid, p in seen if p["action"] == "fill"] == ["hunter2"]

    # ⑤ 当轮回注模型仍是原值（跨轮从净化库重建才是遮断版）
    back = [m for m in llm.received[2] if m["role"] == "assistant" and m.get("tool_calls")]
    assert json.loads(back[-1]["tool_calls"][1]["function"]["arguments"])["value"] == "hunter2"


@pytest.mark.asyncio
async def test_two_steps_in_one_round_mask_a_ref_that_was_plain_at_round_start(test_engine,
                                                                               tmp_path,
                                                                               monkeypatch):
    """C-1 残留（裁定 R-35）：同轮第 2 步的 ref **就摆在轮初清单里**，但当步已换成 sensitive 元素。

    上面那条挑的是「轮初清单里查无此 ref」（`e9`）——那是最容易的一半。真实形状是另一半：
    ref 每次重渲染都从头重编（规格 §6.2），展开出来的口令框编号通常正落在旧清单已有的索引上。
    轮初 `[e1 展开 / e2 搜索框 / e3 备注]` → `click e1` 展开表单 → 新清单
    `[e1 用户名 / e2 口令(sensitive) / e3 备注]` → 同轮紧接着 `fill e2 value=hunter2`。

    「存在即信任」的口径在这一形状下仍然漏：参数侧看轮初那份（`e2` 在、且非 sensitive）判
    False，`hunter2` 明文进 `message.tool_args` 与 WS `tool_call`；结果侧看派发中途被 `_step`
    更新过的那份（`loop.py` 整轮只在派发前读一次轮初清单，`_step` 每步都往后写新清单）判 True，
    遮断。同一个 value 两侧两个结论，就是 R-33 禁止的分家。

    修法不是去猜那份中途清单长什么样（落库行写在派发之前，天生写不出来），而是**位次从严**：
    只有位次 1 那条的轮初清单是权威的——本条消息在这一步之前没有别的**浏览器调用**改写过页面
    （改写者含 `_open`，裁定 R-36；而「排在前面那条」按 `function.name` 数、不看它的 args 能否
    解析，裁定 R-38——见下面 `test_an_open_with_empty_arguments_in_one_round_holds_the_ordinal`
    那条同族测试）；第 2 条及以后一律按 sensitive 处理。
    这里同样钉「两侧对同一个 value 给同一个结论」，并顺手钉住第 ① 步那条 click 不被改坏。
    """
    from app.agent import browser_session_tools as bst

    sid, aid, sf, seen = await _fill_env(test_engine, tmp_path, monkeypatch, bst, slug="pvsens4",
                                         dev_port=8307, prod_port=8306)
    stale = [{"ref": "e1", "role": "button", "name": "展开", "sensitive": False},
             {"ref": "e2", "role": "textbox", "name": "搜索框", "sensitive": False},
             {"ref": "e3", "role": "textbox", "name": "备注", "sensitive": False}]
    fresh = [{"ref": "e1", "role": "textbox", "name": "用户名", "sensitive": False},
             {"ref": "e2", "role": "textbox", "name": "口令", "sensitive": True},
             {"ref": "e3", "role": "textbox", "name": "备注", "sensitive": False}]

    async def fake_open(url, **kw):
        return {"ok": True, "session_id": "sid-secret", "title": "登录", "text": "",
                "url": "http://192.168.65.254:8307/", "interactive": stale,
                "console": [], "failed_requests": [], "page_errors": [], "elapsed_ms": 1}, ""

    async def fake_step(session_id, payload):
        seen.append((session_id, payload))
        return {"ok": True, "title": "登录", "text": "",
                "url": "http://192.168.65.254:8307/", "interactive": fresh,
                "interactive_truncated": False, "dialog": None, "console_new": [],
                "failed_requests_new": [], "page_errors_new": [], "truncated": False,
                "timed_out": False, "steps_charged": True, "elapsed_ms": 1}, ""

    monkeypatch.setattr(bst, "_sidecar_open", fake_open)
    monkeypatch.setattr(bst, "_sidecar_step", fake_step)
    captured = {}
    real_run = bst.run_browser_session_tool

    async def spy_run(ctx, name, args):
        captured["ctx"] = ctx
        return await real_run(ctx, name, args)

    monkeypatch.setattr(bst, "run_browser_session_tool", spy_run)

    turns = [done(None, [tool_call("c1", "browser_open", path="/")]),
             done(None, [tool_call("c2", "browser_step", action="click", target="e1"),
                         tool_call("c3", "browser_step", action="fill", target="e2",
                                   value="hunter2")]),
             done("填好了")]
    rec = Recorder()
    llm = ScriptedLLM(turns)
    await mk_runner(sf, llm, rec, tasks={}).run(sid, "登录一遍", asyncio.Event())

    # ① 消息表全表（tool_args + tool_result 一起 dump）不含明文
    async with sf() as s:
        rows = (await s.execute(sql(
            "SELECT role, tool_args, tool_result FROM message WHERE session_id=:i ORDER BY seq"),
            {"i": sid})).all()
    blob = json.dumps([list(r) for r in rows], ensure_ascii=False)
    assert "hunter2" not in blob, "口令明文进了消息表：轮初清单里存在≠当步的那个 ref 不是口令框"
    step_row = next(list(r)[1] for r in rows
                    if isinstance(list(r)[1], dict)
                    and len(list(r)[1].get("tool_calls") or []) == 2)
    calls = step_row["tool_calls"]
    fill_tc = calls[1]
    assert fill_tc["id"] == "c3"
    masked = json.loads(fill_tc["function"]["arguments"])
    assert masked["value"] == "<redacted:sensitive_input>"
    assert masked["action"] == "fill" and masked["target"] == "e2"
    assert "value" not in json.loads(calls[0]["function"]["arguments"])   # click 没有 value，不动

    # ② WS 推送那一份
    evs = [e for e in rec.events if e["type"] == "tool_call" and e["name"] == "browser_step"]
    assert len(evs) == 2
    assert evs[1]["args"]["value"] == "<redacted:sensitive_input>"
    assert "hunter2" not in json.dumps(evs, ensure_ascii=False)

    # ③ 结果侧同一份账（browser_log 读的是各步动作前的清单，第二步已能看到新清单）
    logged = [s_ for s_ in captured["ctx"].run_state["browser_log"] if s_.get("action") == "fill"]
    assert len(logged) == 1
    assert logged[0]["value_redacted"] is True and "hunter2" not in str(logged[0])

    # ④ 原值照样送达 sidecar（US-08 的登录填得进去）
    assert [p["value"] for _sid, p in seen if p["action"] == "fill"] == ["hunter2"]

    # ⑤ 当轮回注模型仍是原值
    back = [m for m in llm.received[2] if m["role"] == "assistant" and m.get("tool_calls")]
    assert json.loads(back[-1]["tool_calls"][1]["function"]["arguments"])["value"] == "hunter2"


@pytest.mark.asyncio
async def test_an_open_before_a_step_in_one_round_holds_the_ordinal(test_engine, tmp_path,
                                                                    monkeypatch):
    """形状 ④（裁定 R-36）：改写页面清单的不止 `browser_step`——`_open` 也写 `browser_refs`。

    位次规则管的是「本条消息在这一步之前，有没有别的**浏览器调用**改写过清单」，而 R-35 的计数器
    只数 step（白名单 `("browser_step",)`）。于是一条消息里
    `[browser_open "/login", browser_step fill e2 value=hunter2]` 这一路：
    落库那行看到的是**旧页**的轮初清单（`e2=搜索框`，非 sensitive），fill 又被数成位次 1，
    于是判「已知非 sensitive」→ 原值进 `message.tool_args` 与 WS `tool_call`；
    而派发是顺序的：`_open` 先把 `browser_refs` 换成 /login 那份（`e2=口令`，sensitive），
    `_step` 读到新清单 → 结果侧遮断。两侧又分家——与前三轮收掉的是同一个根因（清单时点不同），
    而现有测试没有任何一条把 open 与 step 放进同一条消息。

    这里走的仍是真 loop、钉的仍是那五件事。`browser_open` 的 args 里没有 `value` 键，
    所以它进计数器只是占掉一个位次、自身原样返回（`redact.py` 的「处数 0 保原条目对象」那条
    身份契约不许动），落库与推送那份都读得出 `path == "/login"`。
    """
    from app.agent import browser_session_tools as bst

    sid, aid, sf, seen = await _fill_env(test_engine, tmp_path, monkeypatch, bst, slug="pvsens5",
                                         dev_port=8309, prod_port=8308)
    stale = [{"ref": "e1", "role": "button", "name": "搜索", "sensitive": False},
             {"ref": "e2", "role": "textbox", "name": "搜索框", "sensitive": False},
             {"ref": "e3", "role": "textbox", "name": "备注", "sensitive": False}]
    login = [{"ref": "e1", "role": "textbox", "name": "用户名", "sensitive": False},
             {"ref": "e2", "role": "textbox", "name": "口令", "sensitive": True},
             {"ref": "e3", "role": "textbox", "name": "备注", "sensitive": False}]
    opened = []

    async def fake_open(url, **kw):
        # 首屏与 /login 是两份清单，同一个 `e2` 是两个不同的元素（规格 §6.2：每次重渲染从头重编）
        opened.append(url)
        interactive = login if url.rstrip("/").endswith("/login") else stale
        return {"ok": True, "session_id": f"sid-{len(opened)}", "title": "页面", "text": "",
                "url": "http://192.168.65.254:8309/", "interactive": interactive,
                "console": [], "failed_requests": [], "page_errors": [], "elapsed_ms": 1}, ""

    async def fake_step(session_id, payload):
        seen.append((session_id, payload))
        return {"ok": True, "title": "登录", "text": "",
                "url": "http://192.168.65.254:8309/login", "interactive": login,
                "interactive_truncated": False, "dialog": None, "console_new": [],
                "failed_requests_new": [], "page_errors_new": [], "truncated": False,
                "timed_out": False, "steps_charged": True, "elapsed_ms": 1}, ""

    deleted = []

    async def fake_delete(sid_):
        deleted.append(sid_)
        return True

    monkeypatch.setattr(bst, "_sidecar_open", fake_open)
    monkeypatch.setattr(bst, "_sidecar_step", fake_step)
    monkeypatch.setattr(bst, "_sidecar_delete", fake_delete)
    captured = {}
    real_run = bst.run_browser_session_tool

    async def spy_run(ctx, name, args):
        captured["ctx"] = ctx
        return await real_run(ctx, name, args)

    monkeypatch.setattr(bst, "run_browser_session_tool", spy_run)

    turns = [done(None, [tool_call("c1", "browser_open", path="/")]),
             done(None, [tool_call("c2", "browser_open", path="/login"),
                         tool_call("c3", "browser_step", action="fill", target="e2",
                                   value="hunter2")]),
             done("填好了")]
    rec = Recorder()
    llm = ScriptedLLM(turns)
    await mk_runner(sf, llm, rec, tasks={}).run(sid, "登录一遍", asyncio.Event())

    # ① 消息表全表（tool_args + tool_result 一起 dump）不含明文
    async with sf() as s:
        rows = (await s.execute(sql(
            "SELECT role, tool_args, tool_result FROM message WHERE session_id=:i ORDER BY seq"),
            {"i": sid})).all()
    blob = json.dumps([list(r) for r in rows], ensure_ascii=False)
    assert "hunter2" not in blob, "口令明文进了消息表：open 改写过清单，轮初那份对它不再权威"
    pair_row = next(list(r)[1] for r in rows
                    if isinstance(list(r)[1], dict)
                    and len(list(r)[1].get("tool_calls") or []) == 2)
    calls = pair_row["tool_calls"]
    assert [c["function"]["name"] for c in calls] == ["browser_open", "browser_step"]
    open_tc, fill_tc = calls
    assert open_tc["id"] == "c2"
    # open 只是占位次：它自己的 args 一个字不改（没有 value 键，走的是那条早退分支）
    assert json.loads(open_tc["function"]["arguments"]) == {"path": "/login"}
    assert fill_tc["id"] == "c3"
    masked = json.loads(fill_tc["function"]["arguments"])
    assert masked["value"] == "<redacted:sensitive_input>"
    assert masked["action"] == "fill" and masked["target"] == "e2"   # 结构照旧读得出做了什么

    # ② WS 推送那一份：三条事件、顺序与派发一致，open 的 path 仍读得出
    evs = [e for e in rec.events if e["type"] == "tool_call"]
    assert [e["name"] for e in evs] == ["browser_open", "browser_open", "browser_step"]
    assert evs[1]["args"] == {"path": "/login"}
    assert evs[2]["args"]["value"] == "<redacted:sensitive_input>"
    assert "hunter2" not in json.dumps(evs, ensure_ascii=False)

    # ③ 结果侧同一份账：`_step` 读到的是 open 换上的新清单，所以它遮
    logged = [s_ for s_ in captured["ctx"].run_state["browser_log"] if s_.get("action") == "fill"]
    assert len(logged) == 1
    assert logged[0]["value_redacted"] is True and "hunter2" not in str(logged[0])
    # 这一步的证据链闭合：元素名取自动作前的清单，而它已经是新页的「口令」——
    # 也就是「参数侧那份轮初清单（搜索框）确实被同一条消息里的 open 换掉了」
    assert logged[0]["target_name"] == "口令"

    # ④ 原值照样送达 sidecar（US-08 的登录填得进去）
    assert [p["value"] for _sid, p in seen if p["action"] == "fill"] == ["hunter2"]
    # 中间那次 open 确实自动关掉了旧会话：派发顺序就是这条形状成立的原因；
    # 第二条是新会话在 run 收口时被 best-effort 清掉的（US-21），不是模型发的 close
    assert deleted == ["sid-1", "sid-2"]

    # ⑤ 当轮回注模型仍是原值（跨轮从净化库重建才是遮断版）
    back = [m for m in llm.received[2] if m["role"] == "assistant" and m.get("tool_calls")]
    assert json.loads(back[-1]["tool_calls"][1]["function"]["arguments"])["value"] == "hunter2"


@pytest.mark.asyncio
async def test_a_close_before_a_step_in_one_round_holds_the_ordinal(test_engine, tmp_path,
                                                                     monkeypatch):
    """形状 ④ 的同族（裁定 R-36）：`[browser_close, browser_step fill]`——值进不了表单，但仍会明文落库。

    `close` 之后的 step 必然被拒（`_step` 第一条拒绝出口：「还没有浏览器会话」），所以那个 value
    根本不会进表单，也**不会**进 `browser_log`（拒绝出口一律不写 run_state）。但落库那行写在派发
    **之前**，旧口径下 fill 是位次 1、target 在轮初清单里且非 sensitive → 原值照样进
    `message.tool_args` 与 WS `tool_call`。同一个计数器一并把 close 计入位次即可。

    这一支「两侧同结论」的形状与 open 那条不同，结果侧没有 log 条目可对，所以钉的是：
    参数侧遮断 + 那个值既没进 sidecar 也没进 browser_log（这一步确实什么都没做成）。
    """
    from app.agent import browser_session_tools as bst

    sid, aid, sf, seen = await _fill_env(test_engine, tmp_path, monkeypatch, bst, slug="pvsens6",
                                         dev_port=8311, prod_port=8310)
    stale = [{"ref": "e1", "role": "button", "name": "搜索", "sensitive": False},
             {"ref": "e2", "role": "textbox", "name": "搜索框", "sensitive": False},
             {"ref": "e3", "role": "textbox", "name": "备注", "sensitive": False}]

    async def fake_open(url, **kw):
        return {"ok": True, "session_id": "sid-1", "title": "首页", "text": "",
                "url": "http://192.168.65.254:8311/", "interactive": stale,
                "console": [], "failed_requests": [], "page_errors": [], "elapsed_ms": 1}, ""

    deleted = []

    async def fake_delete(sid_):
        deleted.append(sid_)
        return True

    monkeypatch.setattr(bst, "_sidecar_open", fake_open)
    monkeypatch.setattr(bst, "_sidecar_delete", fake_delete)
    captured = {}
    real_run = bst.run_browser_session_tool

    async def spy_run(ctx, name, args):
        captured["ctx"] = ctx
        return await real_run(ctx, name, args)

    monkeypatch.setattr(bst, "run_browser_session_tool", spy_run)

    turns = [done(None, [tool_call("c1", "browser_open", path="/")]),
             done(None, [tool_call("c2", "browser_close"),
                         tool_call("c3", "browser_step", action="fill", target="e2",
                                   value="hunter2")]),
             done("收工")]
    rec = Recorder()
    llm = ScriptedLLM(turns)
    await mk_runner(sf, llm, rec, tasks={}).run(sid, "填完就收口", asyncio.Event())

    # ① 消息表全表不含明文
    async with sf() as s:
        rows = (await s.execute(sql(
            "SELECT role, tool_args, tool_result FROM message WHERE session_id=:i ORDER BY seq"),
            {"i": sid})).all()
    blob = json.dumps([list(r) for r in rows], ensure_ascii=False)
    assert "hunter2" not in blob, "口令明文进了消息表：close 之后的 step 填不进去，但值照样落库"
    pair_row = next(list(r)[1] for r in rows
                    if isinstance(list(r)[1], dict)
                    and len(list(r)[1].get("tool_calls") or []) == 2)
    calls = pair_row["tool_calls"]
    assert [c["function"]["name"] for c in calls] == ["browser_close", "browser_step"]
    # close 自己不带 value：原样落库，结构不动
    assert calls[0]["id"] == "c2" and json.loads(calls[0]["function"]["arguments"]) == {}
    assert calls[1]["id"] == "c3"
    masked = json.loads(calls[1]["function"]["arguments"])
    assert masked["value"] == "<redacted:sensitive_input>"
    assert masked["action"] == "fill" and masked["target"] == "e2"

    # ② WS 推送那一份
    evs = [e for e in rec.events if e["type"] == "tool_call"]
    assert [e["name"] for e in evs] == ["browser_open", "browser_close", "browser_step"]
    assert evs[2]["args"]["value"] == "<redacted:sensitive_input>"
    assert "hunter2" not in json.dumps(evs, ensure_ascii=False)

    # ③ 这一步真的什么都没做成：会话被同一条消息里的 close 关掉、sidecar 没收到载荷、
    #    结果侧的 browser_log 里也没有 fill 这一条（拒绝出口不写 run_state）
    assert deleted == ["sid-1"]
    assert seen == []
    assert [s_.get("action") for s_ in captured["ctx"].run_state["browser_log"]] == ["open"]

    # ④ 当轮回注模型仍是原值（这一支没执行成功，模型要靠它重开会话再填一遍）
    back = [m for m in llm.received[2] if m["role"] == "assistant" and m.get("tool_calls")]
    assert json.loads(back[-1]["tool_calls"][1]["function"]["arguments"])["value"] == "hunter2"


@pytest.mark.asyncio
async def test_an_open_with_empty_arguments_in_one_round_holds_the_ordinal(test_engine, tmp_path,
                                                                           monkeypatch):
    """形状 ⑤（裁定 R-38）：`arguments` 为空串的 `browser_open` 不占位次，却**真的开了一页**。

    前三轮把「args 畸形、进不了 `rewrite` 因而不占位次」判成安全，理由是那种调用执行不下去。
    这一条把它证伪：`redact.py` 对 `arguments` 为空/不可解析的条目直接 continue（不调 `rewrite`、
    不占位次），而 `loop.py` 派发时把同一份 args 换成 `{}` **照样执行**。`browser_open` 的 schema
    是 `"required": []`，`_open(ctx, {})` 在 `normalize_path(None)` 拿到 `("/", "")`——path 缺省就是
    合法调用（既有 `test_open_binds_session_to_run_state_only` 正是用 `{}` 打开一页的）。于是

    ```
    轮 2 = [browser_open arguments="", browser_step fill e2 value=hunter2]
    ```

    空串 open 被 skip → fill 被数成位次 1 → 判「轮初清单里已知非 sensitive」（那份是首页的
    `e2=搜索框`）→ 原值进 `message.tool_args` 与 WS `tool_call`；而派发端 `_open({})` 真开了 `/`
    并把 `browser_refs` 覆写成新页清单（`e2=口令`，sensitive）→ `_step` 读到它 → 结果侧遮断，
    值还真被填进了口令框。与前三轮同一个根因（两侧看到不同时点的清单）、同一条验收违反
    （「两侧对同一个 value 给同一个结论」），而这一支不是攻击面，是模型发一次无参数 open 就会走到的
    日常路径。修法：位次按 `function.name` 命中名单就计数，与 args 能否解析无关。
    """
    from app.agent import browser_session_tools as bst

    sid, aid, sf, seen = await _fill_env(test_engine, tmp_path, monkeypatch, bst, slug="pvsens7",
                                         dev_port=8313, prod_port=8312)
    home = [{"ref": "e1", "role": "button", "name": "搜索", "sensitive": False},
            {"ref": "e2", "role": "textbox", "name": "搜索框", "sensitive": False},
            {"ref": "e3", "role": "textbox", "name": "备注", "sensitive": False}]
    reloaded = [{"ref": "e1", "role": "textbox", "name": "用户名", "sensitive": False},
                {"ref": "e2", "role": "textbox", "name": "口令", "sensitive": True},
                {"ref": "e3", "role": "textbox", "name": "备注", "sensitive": False}]
    opened = []

    async def fake_open(url, **kw):
        # 两次 open 之间页面重渲染，同一个 `e2` 换了人（规格 §6.2：ref 按采集顺序现编）。
        # 第二次就是那条空 args 的调用：`normalize_path(None)` → "/"，真开了一页。
        opened.append(url)
        interactive = reloaded if len(opened) > 1 else home
        return {"ok": True, "session_id": f"sid-{len(opened)}", "title": "页面", "text": "",
                "url": "http://192.168.65.254:8313/", "interactive": interactive,
                "console": [], "failed_requests": [], "page_errors": [], "elapsed_ms": 1}, ""

    async def fake_step(session_id, payload):
        seen.append((session_id, payload))
        return {"ok": True, "title": "登录", "text": "",
                "url": "http://192.168.65.254:8313/", "interactive": reloaded,
                "interactive_truncated": False, "dialog": None, "console_new": [],
                "failed_requests_new": [], "page_errors_new": [], "truncated": False,
                "timed_out": False, "steps_charged": True, "elapsed_ms": 1}, ""

    deleted = []

    async def fake_delete(sid_):
        deleted.append(sid_)
        return True

    monkeypatch.setattr(bst, "_sidecar_open", fake_open)
    monkeypatch.setattr(bst, "_sidecar_step", fake_step)
    monkeypatch.setattr(bst, "_sidecar_delete", fake_delete)
    captured = {}
    real_run = bst.run_browser_session_tool

    async def spy_run(ctx, name, args):
        captured["ctx"] = ctx
        return await real_run(ctx, name, args)

    monkeypatch.setattr(bst, "run_browser_session_tool", spy_run)

    turns = [done(None, [tool_call("c1", "browser_open", path="/")]),
             done(None, [raw_tool_call("c2", "browser_open", ""),
                         tool_call("c3", "browser_step", action="fill", target="e2",
                                   value="hunter2")]),
             done("填好了")]
    rec = Recorder()
    llm = ScriptedLLM(turns)
    await mk_runner(sf, llm, rec, tasks={}).run(sid, "登录一遍", asyncio.Event())

    # ① 消息表全表（tool_args + tool_result 一起 dump）不含明文——这条就是形状 ⑤ 本身
    async with sf() as s:
        rows = (await s.execute(sql(
            "SELECT role, tool_args, tool_result FROM message WHERE session_id=:i ORDER BY seq"),
            {"i": sid})).all()
    blob = json.dumps([list(r) for r in rows], ensure_ascii=False)
    assert "hunter2" not in blob, "口令明文进了消息表：空 args 的 open 照样被派发、照样换了清单"
    pair_row = next(list(r)[1] for r in rows
                    if isinstance(list(r)[1], dict)
                    and len(list(r)[1].get("tool_calls") or []) == 2)
    calls = pair_row["tool_calls"]
    assert [c["function"]["name"] for c in calls] == ["browser_open", "browser_step"]
    assert calls[0]["id"] == "c2" and calls[0]["function"]["arguments"] == ""   # 原条目原样落库
    assert calls[1]["id"] == "c3"
    masked = json.loads(calls[1]["function"]["arguments"])
    assert masked["value"] == "<redacted:sensitive_input>"
    assert masked["action"] == "fill" and masked["target"] == "e2"              # 结构读得出做了什么

    # ② WS 推送那一份：空 args 的 open 在推送侧被换成 `{}`（它确实被执行了），fill 是占位符
    evs = [e for e in rec.events if e["type"] == "tool_call"]
    assert [e["name"] for e in evs] == ["browser_open", "browser_open", "browser_step"]
    assert evs[1]["args"] == {}
    assert evs[2]["args"]["value"] == "<redacted:sensitive_input>"
    assert "hunter2" not in json.dumps(evs, ensure_ascii=False)

    # ③ 结果侧同一份账：`_step` 读到的是空 args 那次 open 换上的新清单，所以它遮
    logged = [s_ for s_ in captured["ctx"].run_state["browser_log"] if s_.get("action") == "fill"]
    assert len(logged) == 1
    assert logged[0]["value_redacted"] is True and "hunter2" not in str(logged[0])
    # 元素名取自动作前的清单，而它已经是重渲染后的「口令」——证据链闭合：
    # 那条空 args 的 open 确实改了 `browser_refs`，轮初那份（搜索框）在它之后不再权威
    assert logged[0]["target_name"] == "口令"

    # ④ 那条 open 真的打到了 sidecar（两次导航），且原值照样送达口令框（US-08 的登录填得进去）
    assert len(opened) == 2
    assert deleted == ["sid-1", "sid-2"]
    assert [p["value"] for _sid, p in seen if p["action"] == "fill"] == ["hunter2"]

    # ⑤ 当轮回注模型仍是原值（跨轮从净化库重建才是遮断版）
    back = [m for m in llm.received[2] if m["role"] == "assistant" and m.get("tool_calls")]
    assert json.loads(back[-1]["tool_calls"][1]["function"]["arguments"])["value"] == "hunter2"


@pytest.fixture
def pv_seams(monkeypatch):
    """预览重启链路的 Docker 侧替身：预检放行、探针 200、起栈像真引擎那样把终态写进库。"""
    from app.deploy import engine as eng

    async def preflight_ok():
        return None

    async def probe_ok(app, timeout=2):
        return True, ""

    async def start_ok(sf_, app, notify=None):
        await eng._set_status(sf_, app["id"], "running")
        if notify:
            notify(app["id"], {"app_id": app["id"], "type": "dev_log",
                               "line": "[zerocode] ✓ 预览已就绪"})
        return {"status": "running"}

    monkeypatch.setattr(eng, "preflight_docker", preflight_ok)
    monkeypatch.setattr(eng, "probe_dev", probe_ok)
    monkeypatch.setattr(eng, "start_dev", start_ok)
    monkeypatch.setattr(eng, "restart_dev", start_ok)
    return eng


@pytest.mark.asyncio
async def test_running_restart_asks_and_rejection_changes_nothing(test_engine, tmp_path, pv_seams):
    """US-21：running 时先确认；拒绝则一次启停都不发生，模型收到「用户拒绝重启预览」。"""
    sid, aid, sf = await mk_app_session(test_engine, tmp_path, status="running", slug="pvconf",
                                        dev_port=8277, prod_port=8276)
    rec, tasks = Recorder(), {}

    async def reject(session_id, payload):
        assert payload["tool"] == "preview_restart" and "预览应用" in payload["summary"]
        return False

    turns = [done(None, [tool_call("c1", "preview_restart")]), done("好，那不重启")]
    await mk_runner(sf, ScriptedLLM(turns), rec, confirm=reject, hub=DeployHub(),
                    tasks=tasks).run(sid, "重启一下", asyncio.Event())
    tr = next(e for e in rec.events if e["type"] == "tool_result")
    assert tr["result"]["ok"] is False and "用户拒绝重启预览" in tr["result"]["error"]
    cr = next(e for e in rec.events if e["type"] == "confirm_request")
    assert cr["tool"] == "preview_restart" and "正在运行" in cr["summary"]
    assert tasks == {}                                     # 拒绝后没有任务被登记


@pytest.mark.asyncio
async def test_rejected_confirm_result_carries_denied_flag(test_engine, tmp_path, pv_seams):
    """拒绝必须留机器可读标记：结果行带 confirm='denied'。

    已答态的确认卡只能读消息行（历史里没有事件），靠「用户拒绝重启预览」这类散文识别
    必然随文案漂移；三个需确认的工具各有一句不同的拒绝文案，更不可能逐个匹配。"""
    async def reject(session_id, payload):
        return False
    sid, aid, sf = await mk_app_session(test_engine, tmp_path, status="running", slug="pvdenied",
                                        dev_port=8283, prod_port=8282)
    rec = Recorder()
    turns = [done(None, [tool_call("c1", "preview_restart")]), done("好，那不重启")]
    await mk_runner(sf, ScriptedLLM(turns), rec, confirm=reject, hub=DeployHub(),
                    tasks={}).run(sid, "重启一下", asyncio.Event())
    tr = next(e for e in rec.events if e["type"] == "tool_result")
    assert tr["result"]["confirm"] == "denied"
    # 落库的结果行同样带标记：刷新后前端只读得到这一行
    async with sf() as s:
        row = (await s.execute(sql(
            "SELECT tool_result FROM message WHERE session_id=:i AND role='tool'"), {"i": sid})).scalar()
    assert row["confirm"] == "denied"


@pytest.mark.asyncio
async def test_approved_confirm_result_carries_confirmed_flag(test_engine, tmp_path, pv_seams):
    """批准同样要留标记：否则历史里「用户点头放行的写入」与普通工具调用无从区分。"""
    sid, aid, sf = await mk_app_session(test_engine, tmp_path, status="running", slug="pvokflag",
                                        dev_port=8285, prod_port=8284)
    rec = Recorder()
    turns = [done(None, [tool_call("c1", "preview_restart")]), done("已重启")]
    await mk_runner(sf, ScriptedLLM(turns), rec, hub=DeployHub(),
                    tasks={}).run(sid, "重启", asyncio.Event())
    tr = next(e for e in rec.events if e["type"] == "tool_result")
    assert tr["result"]["confirm"] == "approved"


@pytest.mark.asyncio
async def test_cancel_while_awaiting_confirm_records_cancelled_not_denied(test_engine, tmp_path, pv_seams):
    """点「停止」时挂起的确认要记成中断，不是拒绝。

    hub.cancel 把 confirm future 解成 False，_dispatch 只看这个布尔值就会写下
    「用户拒绝重启预览」——刷新后留痕说用户拒绝了，其实用户只是停了执行。
    提问侧早就区分了这两种（ask 解成 None → cancelled），确认侧要对齐。"""
    cancel = asyncio.Event()
    async def confirm_and_cancel(session_id, payload):
        cancel.set()
        return False
    sid, aid, sf = await mk_app_session(test_engine, tmp_path, status="running", slug="pvcancel",
                                        dev_port=8287, prod_port=8286)
    rec = Recorder()
    turns = [done(None, [tool_call("c1", "preview_restart")]), done("不该到达")]
    await mk_runner(sf, ScriptedLLM(turns), rec, confirm=confirm_and_cancel, hub=DeployHub(),
                    tasks={}).run(sid, "重启一下", cancel)
    tr = next(e for e in rec.events if e["type"] == "tool_result")
    assert tr["result"] == {"cancelled": True, "confirm": "cancelled"}
    async with sf() as s:
        row = (await s.execute(sql(
            "SELECT tool_result FROM message WHERE session_id=:i AND role='tool'"), {"i": sid})).scalar()
    assert row == {"cancelled": True, "confirm": "cancelled"}


@pytest.mark.asyncio
async def test_approved_write_file_carries_confirm_flag(test_engine, tmp_path):
    """写入确认批准后，结果里必须带 confirm='approved'。

    write_file/edit_file 分支此前**不设** confirmed（只有 preview_restart 与 db_write 设），
    于是最常见的那张确认卡——写入确认——批准之后前端拿不到任何标记：已答态认不出来，
    刷新后更认不出（历史里没有事件，只能靠结果行）。真实 LLM 端到端跑出来的结果行键是
    ['ok','error']，没有 confirm，才暴露这一处；单测只覆盖了 preview_restart 所以漏掉。"""
    async def approve(session_id, payload):
        assert payload["tool"] == "write_file"
        return True
    turns = [done(None, [tool_call("c1", "write_file", path="hello.txt", content="hi")]), done("写好了")]
    sid, status, evs, sf = await run_once(test_engine, tmp_path, turns, confirm=approve,
                                          write_confirm=True)
    assert status == "idle"
    tr = next(e for e in evs if e["type"] == "tool_result")
    assert tr["result"].get("confirm") == "approved", tr["result"]
    async with sf() as s:
        row = (await s.execute(sql(
            "SELECT tool_result FROM message WHERE session_id=:i AND role='tool'"), {"i": sid})).scalar()
    assert row["confirm"] == "approved"


@pytest.mark.asyncio
async def test_write_confirm_off_leaves_no_confirm_flag(test_engine, tmp_path):
    """开关关掉时没有确认这道闸，结果里就不该有 confirm 标记——否则前端会凭空画出确认卡。"""
    turns = [done(None, [tool_call("c1", "write_file", path="hello.txt", content="hi")]), done("写好了")]
    sid, status, evs, sf = await run_once(test_engine, tmp_path, turns, write_confirm=False)
    assert not [e for e in evs if e["type"] == "confirm_request"]
    tr = next(e for e in evs if e["type"] == "tool_result")
    assert "confirm" not in tr["result"], tr["result"]


@pytest.mark.asyncio
async def test_tool_round_persists_reasoning(test_engine, tmp_path):
    """工具轮的思考留痕也要落库。

    此前只有终答那一轮把 reasoning 存进去，工具轮存的是 NULL：实时流里思考块挂在
    该轮消息上（message_done 带 reasoning），刷新后却整段消失——与「实时 ≡ 历史」
    同构不变式直接冲突，症状和用户最初报的「问答内容找不到」是同一族。
    真实 LLM 端到端跑出来的 dump 就是这么叉开的。"""
    async def ask_ok(session_id, questions, timeout):
        return {"answers": [{"options": ["A"]}]}

    def done_r(content, reasoning, tool_calls=None):
        return {"type": "done", "message": {"role": "assistant", "content": content,
                                            "reasoning": reasoning, "tool_calls": tool_calls}}

    turns = [
        [{"type": "reasoning_delta", "content": "先想想"}, delta("让我看看"),
         done_r("让我看看", "先想想", [tool_call("c1", "ask_user", question="选哪个", options=["A"])])],
        [{"type": "reasoning_delta", "content": "再想想"}, delta("好了"), done_r("好了", "再想想")],
    ]
    sid, status, evs, sf = await run_once(test_engine, tmp_path, turns, ask=ask_ok)
    assert status == "idle"
    async with sf() as s:
        rows = (await s.execute(sql(
            "SELECT reasoning FROM message WHERE session_id=:i AND role='assistant' ORDER BY seq"),
            {"i": sid})).scalars().all()
    assert list(rows) == ["先想想", "再想想"]


@pytest.mark.asyncio
async def test_confirmed_restart_uses_the_shared_task_table(test_engine, tmp_path, pv_seams):
    """US-24：Agent 起的栈登记在 `app.state.dev_tasks` 里——与用户手点「启动预览」同一张表。"""
    sid, aid, sf = await mk_app_session(test_engine, tmp_path, status="running", slug="pvok",
                                        dev_port=8279, prod_port=8278)
    rec, tasks = Recorder(), {}
    turns = [done(None, [tool_call("c1", "preview_restart", wait_seconds=5)]), done("已重启")]
    await mk_runner(sf, ScriptedLLM(turns), rec, hub=DeployHub(), tasks=tasks).run(
        sid, "重启", asyncio.Event())
    tr = next(e for e in rec.events if e["type"] == "tool_result")
    assert tr["result"]["ok"] is True and tr["result"]["status"] == "running"
    assert tr["result"]["restarts_left"] == 2
    assert aid in tasks and tasks[aid].done()


@pytest.mark.asyncio
async def test_confirm_switch_off_never_opens_a_dialog(test_engine, tmp_path, pv_seams):
    sid, aid, sf = await mk_app_session(test_engine, tmp_path, status="running", slug="pvnc",
                                        dev_port=8281, prod_port=8280)
    rec, asked = Recorder(), []

    async def never(session_id, payload):
        asked.append(payload)
        return True

    turns = [done(None, [tool_call("c1", "preview_restart", wait_seconds=5)]), done("好")]
    await mk_runner(sf, ScriptedLLM(turns), rec, confirm=never, hub=DeployHub(), tasks={},
                    cfg=preview_cfg(**{"agent.preview_confirm_running": False})).run(
        sid, "重启", asyncio.Event())
    tr = next(e for e in rec.events if e["type"] == "tool_result")
    assert tr["result"]["ok"] is True and asked == []
    assert not any(e["type"] == "confirm_request" for e in rec.events)


@pytest.mark.asyncio
async def test_exhausted_cap_never_opens_a_dialog(test_engine, tmp_path, pv_seams):
    """§5.6/§5.7：上限是**单次 run** 的——同一轮里连着两次重启，第二次既不弹确认也不起任务。

    先问一次确认、再告诉他「次数用尽」是纯打扰，所以 `need_confirm` 里也带了次数判据。
    """
    sid, aid, sf = await mk_app_session(test_engine, tmp_path, status="running", slug="pvcap",
                                        dev_port=8289, prod_port=8288)
    rec, asked = Recorder(), []

    async def always(session_id, payload):
        asked.append(payload)
        return True

    turns = [done(None, [tool_call("c1", "preview_restart", wait_seconds=5)]),
             done(None, [tool_call("c2", "preview_restart", wait_seconds=5)]),
             done("好")]
    await mk_runner(sf, ScriptedLLM(turns), rec, confirm=always, hub=DeployHub(), tasks={},
                    cfg=preview_cfg(**{"agent.preview_max_restarts": 1})).run(
        sid, "重启", asyncio.Event())
    results = [e["result"] for e in rec.events if e["type"] == "tool_result"]
    assert results[0]["ok"] is True and results[0]["restarts_left"] == 0
    assert results[1]["ok"] is False and "已启动/重启预览 1 次" in results[1]["error"]
    assert len(asked) == 1
    assert len([e for e in rec.events if e["type"] == "confirm_request"]) == 1


@pytest.mark.asyncio
async def test_stopped_start_never_opens_a_dialog(test_engine, tmp_path, pv_seams):
    """US-21 的另一半：冷启动不中断任何人，不该弹确认。"""
    sid, aid, sf = await mk_app_session(test_engine, tmp_path, slug="pvcold",
                                        dev_port=8283, prod_port=8282)
    rec = Recorder()
    turns = [done(None, [tool_call("c1", "preview_restart", wait_seconds=5)]), done("好了")]
    await mk_runner(sf, ScriptedLLM(turns), rec, hub=DeployHub(), tasks={}).run(
        sid, "起预览", asyncio.Event())
    assert not any(e["type"] == "confirm_request" for e in rec.events)
    tr = next(e for e in rec.events if e["type"] == "tool_result")
    assert tr["result"]["ok"] is True


@pytest.mark.asyncio
async def test_tool_progress_arrives_before_tool_result(test_engine, tmp_path, pv_seams):
    """US-17/§8.1：进度是临时事件，但必须早于同一工具的结果到达，前端才画得出「正在重启」。"""
    sid, aid, sf = await mk_app_session(test_engine, tmp_path, slug="pvprog",
                                        dev_port=8285, prod_port=8284)
    rec = Recorder()
    turns = [done(None, [tool_call("c1", "preview_restart", wait_seconds=5)]), done("好了")]
    await mk_runner(sf, ScriptedLLM(turns), rec, hub=DeployHub(), tasks={}).run(
        sid, "起预览", asyncio.Event())
    types = [e["type"] for e in rec.events]
    assert "tool_progress" in types
    assert types.index("tool_progress") < types.index("tool_result")
    prog = next(e for e in rec.events if e["type"] == "tool_progress")
    assert prog["name"] == "preview_restart" and prog["line"].startswith("[zerocode]")


@pytest.mark.asyncio
async def test_tool_context_carries_preview_wiring(test_engine, tmp_path, pv_seams, monkeypatch):
    """ctx 注入是接线正确性的唯一靶心：应用行、配置、取消信号、hub、任务表都要到位。"""
    from app.agent import preview_tools as pt
    sid, aid, sf = await mk_app_session(test_engine, tmp_path, slug="pvctx",
                                        dev_port=8287, prod_port=8286)
    captured, hub, tasks = {}, DeployHub(), {}

    async def fake(ctx, name, args, *, confirmed=False):
        captured.update({"app_id": ctx.app["id"], "cfg": dict(ctx.cfg), "cancel": ctx.cancel,
                         "hub": ctx.deploy_hub, "tasks": ctx.deploy_tasks,
                         "run_state": ctx.run_state, "confirmed": confirmed, "name": name})
        return {"ok": True}

    monkeypatch.setattr(pt, "run_preview_tool", fake)
    cancel = asyncio.Event()
    turns = [done(None, [tool_call("c1", "preview_status")]), done("好")]
    runner = mk_runner(sf, ScriptedLLM(turns), Recorder(), hub=hub, tasks=tasks,
                       cfg=preview_cfg(**{"agent.preview_wait_seconds": 30}))
    await runner.run(sid, "看看状态", cancel)
    assert captured["app_id"] == aid and captured["name"] == "preview_status"
    assert captured["hub"] is hub and captured["tasks"] is tasks and captured["cancel"] is cancel
    assert captured["cfg"]["agent.preview_wait_seconds"] == 30
    assert captured["run_state"] == {} and captured["confirmed"] is False


@pytest.mark.asyncio
async def test_pending_deploy_task_never_opens_a_dialog(test_engine, tmp_path, pv_seams):
    """§5.6 的另一半「别白问一次」：任务表里有未完成任务时不发确认。

    这种时候 handler 只会回并发提示（没起新栈），先让用户点一次确认再告诉他做不了是纯打扰。
    这条同时钉住 `need_confirm` 读的是**共享**任务表：REST 那边（用户手点「启动预览」）起的
    在途任务，Agent 这条链路也看得见——看不见就会双栈。
    """
    sid, aid, sf = await mk_app_session(test_engine, tmp_path, status="running", slug="pvbusy",
                                        dev_port=8291, prod_port=8290)
    rec, asked, tasks = Recorder(), [], {}
    blocker = asyncio.ensure_future(asyncio.Event().wait())   # 永不完成：模拟在途的 REST 部署任务
    tasks[aid] = blocker
    try:
        async def approve(session_id, payload):
            asked.append(payload)
            return True

        turns = [done(None, [tool_call("c1", "preview_restart", wait_seconds=5)]), done("稍后再试")]
        await mk_runner(sf, ScriptedLLM(turns), rec, confirm=approve, hub=DeployHub(),
                        tasks=tasks).run(sid, "重启", asyncio.Event())
        assert asked == []
        assert not any(e["type"] == "confirm_request" for e in rec.events)
        tr = next(e for e in rec.events if e["type"] == "tool_result")
        assert tr["result"]["ok"] is False and "部署任务进行中" in tr["result"]["error"]
    finally:
        blocker.cancel()
        try:
            await blocker
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_starting_with_inflight_task_observes_instead_of_confirming(test_engine, tmp_path,
                                                                          pv_seams):
    """§5.6 四条腿里「已有未完成任务 → 不发确认」在 loop 层的看守：starting + 在途任务。

    这条与 `test_pending_deploy_task_never_opens_a_dialog`（running + 在途任务）合起来覆盖
    handler 会真的去观察的那一类在途状态：结果是等待/观察的产物（`hint` 讲「仍在启动」），
    既不该有确认框，也不该有「请重试并在确认框中选择确认」这类确认相关文案。
    说清看守范围：starting 本身也会被 `need_confirm` 的状态腿先挡下，单独那条 pending_task 腿由
    running 那批用例守着——这条钉的是 loop 层「这整个组合不弹框、结果是观察产物」不被人重构掉
    （把判据塌成「开关开 + 有应用」时，这条立刻变红）。
    在途任务用 `finally` 里 cancel + 排空，不留「Task was destroyed but it is pending」噪声。
    """
    sid, aid, sf = await mk_app_session(test_engine, tmp_path, status="starting", slug="pvstart",
                                        dev_port=8295, prod_port=8294)
    rec, asked, tasks = Recorder(), [], {}
    blocker = asyncio.ensure_future(asyncio.Event().wait())   # 永不完成：模拟在途的部署任务
    tasks[aid] = blocker
    try:
        async def approve(session_id, payload):
            asked.append(payload)
            return True

        # wait_seconds=1：观察循环到点自己收尾返回，不去真等一个永不完成的任务
        turns = [done(None, [tool_call("c1", "preview_restart", wait_seconds=1)]), done("继续观察")]
        await mk_runner(sf, ScriptedLLM(turns), rec, confirm=approve, hub=DeployHub(),
                        tasks=tasks).run(sid, "重启", asyncio.Event())
        assert asked == []
        assert not any(e["type"] == "confirm_request" for e in rec.events)
        tr = next(e for e in rec.events if e["type"] == "tool_result")
        result = tr["result"]
        assert result["ok"] is False and result["status"] == "starting"
        assert "预览仍在启动" in result["hint"]
        assert "确认" not in json.dumps(result, ensure_ascii=False)
    finally:
        blocker.cancel()
        try:
            await blocker
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_db_failure_reading_app_row_degrades_to_idle_turn(test_engine, tmp_path, pv_seams,
                                                                monkeypatch):
    """预览是增量能力：确认判定读应用行时炸了，只降级成「当作没读到」，不能把整轮打成 error。

    `preview_tools.fetch_app` 只吞 `DeployError`，一次连接抖动或驱动异常会从 `_dispatch` 的确认分支
    逃到 `run()` 的兜底 except，让整轮对话以 `{"type": "error"}` + status=error 收场。这条打桩点在
    `app.agent.loop.fetch_app`（loop 是按名字 import 的）而非 `preview_tools` 那份，于是 handler
    自己的重读仍是真实现——一条用例同时证明主路径没被 DB 异常打穿、且 handler 的确认兜底真接住了
    （竞态文案原样回到模型上下文，不是「工具执行异常」）。
    """
    from app.agent import loop as loop_module

    async def boom(ctx):
        raise RuntimeError("connection already closed")      # 不是 DeployError：真会往外逃

    sid, aid, sf = await mk_app_session(test_engine, tmp_path, status="running", slug="pvdberr",
                                        dev_port=8293, prod_port=8292)
    monkeypatch.setattr(loop_module, "fetch_app", boom)
    rec, asked = Recorder(), []

    async def approve(session_id, payload):
        asked.append(payload)
        return True

    turns = [done(None, [tool_call("c1", "preview_restart", wait_seconds=5)]), done("那先观察")]
    status = await mk_runner(sf, ScriptedLLM(turns), rec, confirm=approve, hub=DeployHub(),
                             tasks={}).run(sid, "重启", asyncio.Event())
    assert status == "idle"
    assert not any(e["type"] == "error" for e in rec.events)
    # 读不到应用行 → need_confirm 为 False → 一次确认都不该发（handler 侧的兜底负责拦下这次中断）
    assert asked == [] and not any(e["type"] == "confirm_request" for e in rec.events)
    tr = next(e for e in rec.events if e["type"] == "tool_result")
    assert isinstance(tr["result"], dict)
    assert tr["result"]["ok"] is False and tr["result"]["status"] == "running"
    assert "预览已在运行，请重试并在确认框中选择确认" in tr["result"]["error"]
    assert "工具执行异常" not in json.dumps(tr["result"], ensure_ascii=False)


# ---------- db_write 确认流（规格 2026-10-30 §3 US-12/US-15，ADR-0027） ----------

@pytest.mark.asyncio
async def test_db_write_always_confirms_and_passes_flag(test_engine, tmp_path, monkeypatch):
    """US-12/US-15：db_write 恒弹确认卡——mk_runner 默认 agent.write_confirm=False 也照弹；
    摘要逐条列 op 且无 WHERE 的 delete 标危险；批准后 execute_tool 收到 confirmed=True。"""
    from app.agent import db_tools
    sid, aid, sf = await mk_app_session(test_engine, tmp_path, slug="dbwok",
                                        dev_port=8297, prod_port=8296)
    rec, asked, captured = Recorder(), [], {}

    async def approve(session_id, payload):
        asked.append(payload)
        return True

    async def fake_run(ctx, name, args, *, confirmed=False):
        captured.update(name=name, confirmed=confirmed)
        return {"ok": True, "results": []}

    monkeypatch.setattr(db_tools, "run_db_tool", fake_run)
    turns = [done(None, [tool_call("c1", "db_write", statements=["delete from t"])]), done("好了")]
    await mk_runner(sf, ScriptedLLM(turns), rec, confirm=approve).run(
        sid, "清一下数据", asyncio.Event())
    ev = next(e for e in rec.events if e["type"] == "confirm_request")
    assert ev["tool"] == "db_write"
    assert "[delete]" in ev["summary"] and "危险" in ev["summary"]
    assert asked and asked[0]["tool"] == "db_write" and asked[0]["summary"] == ev["summary"]
    assert captured == {"name": "db_write", "confirmed": True}


@pytest.mark.asyncio
async def test_db_write_declined_never_executes(test_engine, tmp_path, monkeypatch):
    """US-12：用户拒绝 → 一条不执行，模型收到「用户拒绝」的正常结果继续对话。"""
    from app.agent import db_tools
    sid, aid, sf = await mk_app_session(test_engine, tmp_path, slug="dbwno",
                                        dev_port=8299, prod_port=8298)
    executed = []

    async def deny(session_id, payload):
        return False

    async def fake_run(ctx, name, args, *, confirmed=False):
        executed.append(name)
        return {"ok": True}

    monkeypatch.setattr(db_tools, "run_db_tool", fake_run)
    turns = [done(None, [tool_call("c1", "db_write",
                                   statements=["insert into t values (1)"])]), done("好吧")]
    rec = Recorder()
    await mk_runner(sf, ScriptedLLM(turns), rec, confirm=deny).run(
        sid, "灌点数据", asyncio.Event())
    assert executed == []
    tr = next(e for e in rec.events if e["type"] == "tool_result")
    assert tr["result"]["ok"] is False and "拒绝" in tr["result"]["error"]


@pytest.mark.asyncio
async def test_db_prompt_mounted_only_for_app_workspace(test_engine, tmp_path):
    """US-17：应用工作区随工具挂载 <database> 使用口径；指引与工具同生同灭。"""
    sid, aid, sf = await mk_app_session(test_engine, tmp_path, slug="dbpmt",
                                        dev_port=8301, prod_port=8300)
    llm = ScriptedLLM([[delta("好"), *done("好了")]])
    await mk_runner(sf, llm, Recorder()).run(sid, "看看数据", asyncio.Event())
    assert "<database>" in llm.received[0][0]["content"]


# ---------------------------------------------------------------- 需求澄清门禁
# 规格 docs/specs/2026-11-01-agent-clarify-mode.md（ADR-0032 / ADR-0033），逐条对齐用户故事。

def answered_ask(answer="按方案一"):
    """ask 桩：返回一条正常回答。返回 None 在中断路径里表示「已取消」，不是「没内容」。"""
    return lambda *a, **k: asyncio.sleep(0, {"answers": [{"answer": answer}]})


def declined_ask():
    """「不再提问，自行决定」的上行形状：hub 侧带机器标记 declined（不是靠文案识别）。"""
    return lambda *a, **k: asyncio.sleep(0, {"answers": [{"answer": "自行决定"}],
                                             "declined": True})


def approved_confirm():
    """确认桩必须是协程：`_dispatch` 会 await 它，run_once 的同步默认桩只在不走确认时安全。"""
    return lambda *a, **k: asyncio.sleep(0, True)


def results_of(evs):
    return [e for e in evs if e["type"] == "tool_result"]


@pytest.mark.asyncio
async def test_gate_blocks_write_without_dialogue(test_engine, tmp_path):
    """故事 1：一句话需求直接写文件 → 挡回，且磁盘上确实没写。"""
    turns = [done(None, [tool_call("c1", "write_file", path="a.txt", content="x")]),
             done("先去问用户")]
    sid, status, evs, sf = await run_once(test_engine, tmp_path, turns, clarify=True)
    assert status == "idle"
    tr = results_of(evs)[0]
    assert tr["name"] == "write_file"
    assert tr["result"].get("blocked_by") == "clarify_gate"
    assert "澄清" in tr["result"]["error"]
    assert not (tmp_path / "a.txt").exists()


@pytest.mark.asyncio
async def test_gate_allows_read_tools(test_engine, tmp_path):
    """故事 2：只读工具不受门禁影响，否则模型在门禁之内无法完成澄清本身。"""
    turns = [done(None, [tool_call("c1", "list_dir", path="")]), done("看完就说")]
    sid, status, evs, sf = await run_once(test_engine, tmp_path, turns, clarify=True)
    assert status == "idle"
    tr = results_of(evs)[0]
    assert tr["result"].get("blocked_by") is None
    assert "entries" in tr["result"] or tr["result"].get("ok") is not False


@pytest.mark.asyncio
async def test_gate_passes_after_ask_and_done(test_engine, tmp_path):
    """故事 3：问过 + 收口 → 放行。两个信号都必须在消息流里留痕。"""
    turns = [done(None, [tool_call("c1", "ask_user", question="要哪种形态")]),
             done(None, [tool_call("c2", "clarify_done", understanding="给首页加一个按钮")]),
             done(None, [tool_call("c3", "write_file", path="a.txt", content="x")]),
             done("写好了")]
    sid, status, evs, sf = await run_once(test_engine, tmp_path, turns,
                                          ask=answered_ask(), clarify=True)
    assert status == "idle"
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "x"
    assert [e["name"] for e in results_of(evs)] == ["ask_user", "clarify_done", "write_file"]
    done_row = results_of(evs)[1]["result"]
    assert done_row["understanding"] == "给首页加一个按钮"
    assert done_row["basis"] == "clarified"


@pytest.mark.asyncio
async def test_gate_blocks_when_asked_but_not_closed(test_engine, tmp_path):
    """故事 4：问过但没收口 → 仍拦（串联判据缺一即拦）。"""
    turns = [done(None, [tool_call("c1", "ask_user", question="q")]),
             done(None, [tool_call("c2", "write_file", path="a.txt", content="x")]),
             done("被挡回")]
    sid, status, evs, sf = await run_once(test_engine, tmp_path, turns,
                                          ask=answered_ask(), clarify=True)
    assert results_of(evs)[1]["result"].get("blocked_by") == "clarify_gate"
    assert not (tmp_path / "a.txt").exists()


@pytest.mark.asyncio
async def test_gate_blocks_declared_but_never_asked(test_engine, tmp_path):
    """故事 4：收了口但从没问过、也没声明豁免 → 拦，且引导语指向「去提问」而不是「重新收口」。"""
    turns = [done(None, [tool_call("c1", "clarify_done", understanding="自认为清楚了")]),
             done(None, [tool_call("c2", "write_file", path="a.txt", content="x")]),
             done("被挡回")]
    sid, status, evs, sf = await run_once(test_engine, tmp_path, turns, clarify=True)
    blocked = results_of(evs)[1]["result"]
    assert blocked.get("blocked_by") == "clarify_gate"
    assert "没有向用户提过任何问题" in blocked["error"]


@pytest.mark.asyncio
async def test_already_clear_basis_exempts_asking(test_engine, tmp_path):
    """故事 5/6：小任务声明 already_clear 即放行；understanding 落进结果行供前端回显。"""
    turns = [done(None, [tool_call("c1", "clarify_done", basis="already_clear",
                                   understanding="把 README 的一级标题改成 Hello")]),
             done(None, [tool_call("c2", "write_file", path="README.md", content="# Hello")]),
             done("改好了")]
    sid, status, evs, sf = await run_once(test_engine, tmp_path, turns, clarify=True)
    assert status == "idle"
    first = results_of(evs)[0]["result"]
    assert first["basis"] == "already_clear" and first["asked"] == 0
    assert "README" in first["understanding"]
    assert (tmp_path / "README.md").exists()


@pytest.mark.asyncio
async def test_clarify_done_requires_understanding(test_engine, tmp_path):
    """豁免的可审计性全在 understanding 上——缺它就退回「模型说了算」，必须拒。"""
    turns = [done(None, [tool_call("c1", "clarify_done", basis="already_clear")]), done("补问")]
    sid, status, evs, sf = await run_once(test_engine, tmp_path, turns, clarify=True)
    res = results_of(evs)[0]["result"]
    assert res["ok"] is False and "understanding" in res["error"]


@pytest.mark.asyncio
async def test_ask_notice_fires_at_threshold(test_engine, tmp_path):
    """故事 9：第 N 题在结果里附一条止损提醒，且只在第 N 题那一次出现。"""
    ask_turns = [done(None, [tool_call(f"c{i}", "ask_user", question=f"q{i}")]) for i in range(3)]
    turns = ask_turns + [done("收工")]
    sid, status, evs, sf = await run_once(test_engine, tmp_path, turns, ask=answered_ask(),
                                          clarify=True, **{"clarify.ask_remind_at": 3})
    rs = results_of(evs)
    assert "notice" not in rs[0]["result"] and "notice" not in rs[1]["result"]
    assert "停止追问" in rs[2]["result"]["notice"]


@pytest.mark.asyncio
async def test_declined_waives_gate_for_the_session_across_runs(test_engine, tmp_path):
    """故事 7：用户点「自行决定」后，同一个会话的下一次 run 不再被门禁拦（跨 run 生效）。"""
    sid = await mk_session(test_engine, tmp_path)
    sf = make_session_factory(test_engine)
    waived = {}

    def build(turns):
        rec = Recorder()
        return AgentRunner(sf, ScriptedLLM(turns), cfg_provider(clarify=True), rec,
                           declined_ask(), lambda *a, **k: True,
                           clarify_waived=waived), rec

    runner, rec1 = build([done(None, [tool_call("c1", "ask_user", question="要不要问")]),
                          done("按默认继续")])
    await runner.run(sid, "第一问", asyncio.Event())
    assert waived.get(sid) is True                     # 解除标记登记在 hub 那份表里
    assert results_of(rec1.events)[0]["result"]["answers"]

    # 新一轮（新 runner、新 run_state）：没问过也没收口，仍应放行
    runner2, rec2 = build([done(None, [tool_call("c2", "write_file", path="b.txt", content="y")]),
                           done("写好了")])
    await runner2.run(sid, "第二个任务", asyncio.Event())
    assert results_of(rec2.events)[0]["result"].get("blocked_by") is None
    assert (tmp_path / "b.txt").exists()


@pytest.mark.asyncio
async def test_clarify_tools_and_prompt_mounted_together(test_engine, tmp_path):
    """故事 10/20：工具与澄清段同生同灭——关掉开关的账户既看不见闸也看不见 clarify_done。"""
    sid = await mk_session(test_engine, tmp_path)
    sf = make_session_factory(test_engine)
    on = ScriptedLLM([[delta("好"), *done("好了")]])
    await AgentRunner(sf, on, cfg_provider(clarify=True), Recorder(),
                      lambda *a, **k: asyncio.sleep(0, None), lambda *a, **k: True
                      ).run(sid, "开着的会话", asyncio.Event())
    assert "clarify_done" in tool_names(on)
    assert "<clarify_mode>" in on.received[0][0]["content"]

    off = ScriptedLLM([[delta("好"), *done("好了")]])
    await AgentRunner(sf, off, cfg_provider(clarify=False), Recorder(),
                      lambda *a, **k: asyncio.sleep(0, None), lambda *a, **k: True
                      ).run(sid, "关掉的会话", asyncio.Event())
    assert "clarify_done" not in tool_names(off)
    assert "<clarify_mode>" not in off.received[0][0]["content"]


@pytest.mark.asyncio
async def test_new_doc_write_skips_second_confirmation(test_engine, tmp_path):
    """故事 14：docs/ 下的新建文档免二次确认；同轮的普通代码写入仍照旧弹确认卡。"""
    turns = [done(None, [tool_call("c1", "ask_user", question="要不要把计划写成文档")]),
             done(None, [tool_call("c2", "clarify_done", understanding="写计划文档")]),
             done(None, [tool_call("c3", "write_file", path="docs/specs/plan.md", content="# 计划")]),
             done(None, [tool_call("c4", "write_file", path="backend/app.py", content="print(1)")]),
             done("都写好了")]
    sid, status, evs, sf = await run_once(test_engine, tmp_path, turns, ask=answered_ask(),
                                          confirm=approved_confirm(),
                                          clarify=True, write_confirm=True)
    confirms = [e for e in evs if e["type"] == "confirm_request"]
    assert status == "idle", evs
    assert [c["args"]["path"] for c in confirms] == ["backend/app.py"]
    assert (tmp_path / "docs" / "specs" / "plan.md").exists()
    assert (tmp_path / "backend" / "app.py").exists()


@pytest.mark.asyncio
async def test_existing_doc_still_needs_confirmation(test_engine, tmp_path):
    """故事 14 的反向断言：用户点头的是「新建这一份」，覆盖已有文档仍要确认。"""
    (tmp_path / "docs" / "adr").mkdir(parents=True)
    (tmp_path / "docs" / "adr" / "0001-x.md").write_text("# 旧内容", encoding="utf-8")
    turns = [done(None, [tool_call("c1", "ask_user", question="更新这份 ADR 吗")]),
             done(None, [tool_call("c2", "clarify_done", understanding="更新 ADR")]),
             done(None, [tool_call("c3", "write_file", path="docs/adr/0001-x.md", content="# 新")]),
             done("好了")]
    # 用户拒绝：确认卡弹出去了，文件就必须保持原样——免确认分支没有偷偷写盘的后门
    sid, status, evs, sf = await run_once(test_engine, tmp_path, turns, ask=answered_ask(),
                                          confirm=lambda *a, **k: asyncio.sleep(0, False),
                                          clarify=True, write_confirm=True)
    assert [e["args"]["path"] for e in evs if e["type"] == "confirm_request"] == ["docs/adr/0001-x.md"]
    assert (tmp_path / "docs" / "adr" / "0001-x.md").read_text(encoding="utf-8") == "# 旧内容"


@pytest.mark.asyncio
async def test_app_decisions_injected_and_skipped_when_empty(test_engine, tmp_path):
    """故事 15/16：docs 有文档时注入 <app_decisions> 索引，目录为空时不注空块。"""
    sid = await mk_session(test_engine, tmp_path)
    sf = make_session_factory(test_engine)

    def _run():
        llm = ScriptedLLM([[delta("好"), *done("好了")]])
        runner = AgentRunner(sf, llm, cfg_provider(clarify=True), Recorder(),
                             lambda *a, **k: asyncio.sleep(0, None), lambda *a, **k: True)
        return llm, runner

    llm, runner = _run()
    await runner.run(sid, "接着上次做", asyncio.Event())
    assert "<app_decisions>" not in llm.received[0][0]["content"]

    (tmp_path / "docs" / "adr").mkdir(parents=True)
    (tmp_path / "docs" / "adr" / "0001-gate.md").write_text(
        "---\ntitle: 首轮澄清门禁\ndescription: 写文件前先对齐\n---\n", encoding="utf-8")
    llm2, runner2 = _run()
    await runner2.run(sid, "接着上次做", asyncio.Event())
    system = llm2.received[0][0]["content"]
    assert "<app_decisions>" in system and "docs/adr/0001-gate.md" in system
    assert "首轮澄清门禁" in system


@pytest.mark.asyncio
async def test_ask_user_options_survive_double_encoding(test_engine, tmp_path):
    """模型把 options 当 JSON 字符串发时（实跑撞上过的形状），必须解回来再交给提问卡。

    静默按「非 list 就丢」处理的话，卡片会退化成只剩输入框：用户看不出是模型的锅还是平台的锅，
    而拷问流程依赖的就是那几项可点的口径。
    """
    captured = {}

    async def spy_ask(session_id, questions, timeout):
        captured["questions"] = questions
        return {"answers": [{"answer": "甲"}]}

    opts = json.dumps([{"label": "甲", "description": "A"}, "乙"], ensure_ascii=False)
    turns = [done(None, [tool_call("c1", "ask_user", question="选哪个", options=opts)]), done("好了")]
    sid, status, evs, sf = await run_once(test_engine, tmp_path, turns, ask=spy_ask)
    assert status == "idle"
    q = captured["questions"][0]
    assert [o["label"] for o in q["options"]] == ["甲", "乙"]   # 裸字符串选项也包成 {label}


@pytest.mark.asyncio
async def test_ask_user_questions_array_survive_double_encoding(test_engine, tmp_path):
    """模型把整个 questions 数组塞成 JSON 字符串时也认（实跑形状），否则它会白烧一轮才重发。"""
    captured = {}

    async def spy_ask(session_id, questions, timeout):
        captured["questions"] = questions
        return {"answers": [{"answer": "甲"}]}

    payload = json.dumps([{"question": "选哪个", "options": [{"label": "甲"}]}], ensure_ascii=False)
    turns = [done(None, [tool_call("c1", "ask_user", questions=payload)]), done("好了")]
    sid, status, evs, sf = await run_once(test_engine, tmp_path, turns, ask=spy_ask)
    assert status == "idle"
    assert captured["questions"] == [{"question": "选哪个", "options": [{"label": "甲"}]}]


@pytest.mark.asyncio
async def test_ask_user_garbage_options_falls_back_to_free_answer(test_engine, tmp_path):
    """反向断言：解不出合法 JSON 的 options 就回落自由回答，不画假选项也不炸整轮。"""
    captured = {}

    async def spy_ask(session_id, questions, timeout):
        captured["questions"] = questions
        return {"answers": [{"answer": "随便"}]}

    turns = [done(None, [tool_call("c1", "ask_user", question="叫什么", options="不是 JSON")]), done("好了")]
    sid, status, evs, sf = await run_once(test_engine, tmp_path, turns, ask=spy_ask)
    assert status == "idle"
    assert captured["questions"][0]["options"] == []
