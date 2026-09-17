"""Task 8：压缩服务（水位/估算/摘要落库/失败不阻断）。"""
from sqlalchemy import text as sql
from app.db import make_session_factory
from app.agent.llm import ScriptedLLM
from app.services import compaction_service as comp
from tests.test_agent_loop import mk_session

class _Rec:
    def __init__(self): self.events = []
    async def __call__(self, ev): self.events.append(ev)

async def _seed_messages(sf, sid, n):
    from app.services.message_service import add_message
    for i in range(n):
        await add_message(sf, sid, "user" if i % 2 == 0 else "assistant", content=f"消息{i}内容")

async def test_latest_and_rows_after(test_engine, tmp_path):
    sf = make_session_factory(test_engine)
    sid = await mk_session(test_engine, tmp_path)
    assert await comp.latest(sf, sid) == (None, 0)
    await _seed_messages(sf, sid, 3)
    rows = await comp.rows_after(sf, sid, 0)
    assert [r["seq"] for r in rows] == [1, 2, 3]
    assert comp.estimate_chars(rows) > 0

async def test_compact_writes_and_emits(test_engine, tmp_path):
    sf = make_session_factory(test_engine)
    sid = await mk_session(test_engine, tmp_path)
    await _seed_messages(sf, sid, 12)
    rec = _Rec()
    llm = ScriptedLLM([[{"type": "content_delta", "content": "目标：测试"},
                        {"type": "done", "message": {"role": "assistant", "content": "目标：测试", "tool_calls": None}}]])
    res = await comp.compact(sf, llm, sid, rec, keep_recent=8)
    assert res["up_to_seq"] == 4  # 12 条保留 8 条 → 摘到 seq=4
    assert await comp.latest(sf, sid) == ("目标：测试", 4)
    assert any(e["type"] == "compaction_done" and e["summary"] == "目标：测试" for e in rec.events)

async def test_compact_skips_when_few(test_engine, tmp_path):
    sf = make_session_factory(test_engine)
    sid = await mk_session(test_engine, tmp_path)
    await _seed_messages(sf, sid, 5)
    llm = ScriptedLLM([])  # 不应被消费
    assert await comp.compact(sf, llm, sid, _Rec(), keep_recent=8) is None
    assert llm.turns == []

async def test_compact_failure_emits_error_only(test_engine, tmp_path):
    sf = make_session_factory(test_engine)
    sid = await mk_session(test_engine, tmp_path)
    await _seed_messages(sf, sid, 12)

    class Boom:
        async def chat_stream(self, messages, tools):
            raise RuntimeError("llm down")
            yield  # pragma: no cover
    rec = _Rec()
    assert await comp.compact(sf, Boom(), sid, rec, keep_recent=8) is None
    assert any(e["type"] == "error" and "压缩失败" in e["message"] for e in rec.events)
    async with sf() as s:
        n = (await s.execute(sql("SELECT count(*) FROM compaction"), {})).scalar()
    assert n == 0
