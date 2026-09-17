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
    async def ask_ok(session_id, question, options, timeout):
        return {"option": "方案A", "answer": "方案A"}
    turns = [done(None, [tool_call("c1", "ask_user", question="选哪个", options=["方案A", "方案B"])]),
             done("好的按方案A")]
    sid, status, evs, sf = await run_once(test_engine, tmp_path, turns, ask=ask_ok)
    assert status == "idle"
    # 校验模型第二轮收到了 tool 结果（含 方案A）
    async with sf() as s:
        row = (await s.execute(sql(
            "SELECT tool_result FROM message WHERE session_id=:i AND role='tool'"), {"i": sid})).scalar()
    assert row["option"] == "方案A"

@pytest.mark.asyncio
async def test_ask_user_cancelled(test_engine, tmp_path):
    async def ask_none(session_id, question, options, timeout):
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
    async def ask_and_cancel(session_id, question, options, timeout):
        cancel.set()
        return None
    turns = [done(None, [tool_call("c1", "ask_user", question="?")]), done("不该到达")]
    sid, status, evs, sf = await run_once(test_engine, tmp_path, turns, ask=ask_and_cancel, cancel=cancel)
    assert status == "idle"
    assert any(e["type"] == "error" and e["message"] == "已取消" for e in evs)
