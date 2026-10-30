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

def cfg_provider(write_confirm=False, ask_timeout=0, max_rounds=30):
    async def _p():
        return {"agent.write_confirm": write_confirm, "agent.ask_timeout_seconds": ask_timeout,
                "agent.max_tool_rounds": max_rounds, "agent.history_window": 40}
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
                "agent.compact_keep_recent": 8}
    runner = AgentRunner(sf, llm, _p, rec,
                         lambda *a, **k: asyncio.sleep(0, None), lambda *a, **k: True)
    status = await runner.run(sid, "继续", asyncio.Event())
    assert status == "idle"
    system_msg = llm.received[1][0]
    assert system_msg["role"] == "system" and "<context_summary>" in system_msg["content"]
    assert "摘要：历史已压缩" in system_msg["content"]

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

async def _mk_owned_session(test_engine, tmp_path, token="tok-loop") -> tuple[int, int]:
    """建 actor + 带 owner_user_id 的 workspace + chat_session，返回 (sid, uid)。"""
    sf = make_session_factory(test_engine)
    async with sf() as s:
        uid = (await s.execute(sql(
            "INSERT INTO actor (nickname, token) VALUES ('loop-owner', :t) RETURNING id"),
            {"t": token})).scalar()
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
    sid, uid = await _mk_owned_session(test_engine, tmp_path, token="tok-2")
    sf = make_session_factory(test_engine)
    await _add_skill(test_engine, uid, "on", "启用技能", "在用", str(tmp_path / "a"), enabled=True)
    await _add_skill(test_engine, uid, "off", "停用技能", "停用", str(tmp_path / "b"), enabled=False)
    c = await _system_content(test_engine, sf, sid)
    assert "启用技能" in c
    assert "停用技能" not in c


@pytest.mark.asyncio
async def test_no_available_skills_block_without_enabled(test_engine, tmp_path):
    # 无启用技能（全部停用）不注入空块，与 current_todos 空守卫同口径
    sid, uid = await _mk_owned_session(test_engine, tmp_path, token="tok-3")
    sf = make_session_factory(test_engine)
    await _add_skill(test_engine, uid, "off", "停用技能", "停用", str(tmp_path / "b"), enabled=False)
    c = await _system_content(test_engine, sf, sid)
    assert "<available_skills>" not in c


@pytest.mark.asyncio
async def test_skill_injection_is_user_scoped(test_engine, tmp_path):
    # 隔离：会话归属 alice，只注入 alice 的技能；bob 的同 owner 表技能不得泄露
    sid, alice = await _mk_owned_session(test_engine, tmp_path, token="tok-alice")
    sf = make_session_factory(test_engine)
    async with sf() as s:
        bob = (await s.execute(sql(
            "INSERT INTO actor (nickname, token) VALUES ('bob', 'tok-bob') RETURNING id"))).scalar()
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
                "agent.max_tool_rounds": 10, "agent.history_window": 40}
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
    assert tool_names(llm)[-6:] == ["preview_status", "preview_logs", "preview_restart",
                                    "db_query", "db_write", "preview_page"]
    assert "<preview>" in llm.received[0][0]["content"]
    assert "<browser>" in llm.received[0][0]["content"]
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
    assert "<preview>" not in llm.received[0][0]["content"]
    assert "<database>" not in llm.received[0][0]["content"]


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
    assert "<database>" not in llm.received[0][0]["content"]
    assert "<browser>" not in llm.received[0][0]["content"]
    assert "绑定了多个应用" in llm.received[0][0]["content"]


@pytest.mark.asyncio
async def test_preview_prompt_follows_the_stack(test_engine, tmp_path):
    sid, aid, sf = await mk_app_session(test_engine, tmp_path, stack="vue3-springboot",
                                        slug="pvloopj", dev_port=8275, prod_port=8274)
    llm = ScriptedLLM([[delta("好"), *done("好了")]])
    await mk_runner(sf, llm, Recorder()).run(sid, "改个后端", asyncio.Event())
    c = llm.received[0][0]["content"]
    assert "必须 preview_restart" in c and "uvicorn" not in c     # 按栈差异化，不串味
    assert c.rstrip().endswith("</browser>")                      # 块在 system 尾部（契约/技能之后）
    assert "</preview>" in c and "<browser>" in c                 # 浏览器指引跟在预览块之后


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
