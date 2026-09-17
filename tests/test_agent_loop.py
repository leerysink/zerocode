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
