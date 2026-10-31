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

async def test_rows_after_reads_context_content(test_engine, tmp_path):
    # Fix 2（spec §4.3）：压缩统计与摘要 digest 与对话窗口（loop._history）同口径——
    # 用户消息优先读 @mention 展开后的 context_content，不得按裸 content 少计
    sf = make_session_factory(test_engine)
    sid = await mk_session(test_engine, tmp_path)
    from app.services.message_service import add_message
    expanded = "展开后的文件内容" + "x" * 200
    await add_message(sf, sid, "user", content="@a.txt", context_content=expanded)
    rows = await comp.rows_after(sf, sid, 0)
    assert rows[0]["content"] == expanded  # SELECT 读 COALESCE(context_content, content)
    assert comp.estimate_chars(rows) >= len(expanded)  # 估算按展开后口径计字

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
    rec = _Rec()
    assert await comp.compact(sf, llm, sid, rec, keep_recent=8) is None
    assert llm.turns == []
    # skip 也是终态：必须下发 notice 事件，前端才能退出 compacting（fix-round Fix 1）
    assert [(e["type"], e.get("text")) for e in rec.events] == [("notice", "历史消息不足，暂无需压缩")]

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


async def test_estimate_counts_images_as_4000_chars(test_engine, tmp_path, monkeypatch):
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path))
    from app.services import attachment_service as att
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path))
    sf = make_session_factory(test_engine)
    sid = await mk_session(test_engine, tmp_path)
    a = await att.create_attachment(sf, 7, sid, "a.png", b"\x89PNG\r\n\x1a\n" + b"\0" * 8, "image/png")
    b = await att.create_attachment(sf, 7, sid, "b.png", b"\x89PNG\r\n\x1a\n" + b"\0" * 8, "image/png")
    from app.services.message_service import add_message
    await add_message(sf, sid, "user", content="看图", attachment_ids=[a["id"], b["id"]])
    rows = await comp.rows_after(sf, sid, 0)
    assert rows[0]["attachment_count"] == 2
    assert "a.png" in rows[0]["attachment_names"] and "b.png" in rows[0]["attachment_names"]
    assert comp.estimate_chars(rows) == len("看图") + 8000


async def test_compact_chains_previous_summary(test_engine, tmp_path):
    # ADR-0030：第二次压缩必须把上一份摘要并进输入，否则最早一段历史被顶掉
    sf = make_session_factory(test_engine)
    sid = await mk_session(test_engine, tmp_path)
    await _seed_messages(sf, sid, 12)
    first = ScriptedLLM([[{"type": "content_delta", "content": "第一摘要：目标A"},
                          {"type": "done", "message": {"role": "assistant", "content": "第一摘要：目标A",
                                                       "tool_calls": None}}]])
    assert await comp.compact(sf, first, sid, _Rec(), keep_recent=8)
    from app.services.message_service import add_message
    for i in range(12):
        await add_message(sf, sid, "assistant", content=f"新增{i}")
    second = ScriptedLLM([[{"type": "done", "message": {"role": "assistant",
                                                        "content": "第二摘要：目标A+B",
                                                        "tool_calls": None}}]])
    assert await comp.compact(sf, second, sid, _Rec(), keep_recent=8)
    digest = second.received[0][1]["content"]
    assert "第一摘要：目标A" in digest
    assert await comp.latest(sf, sid) == ("第二摘要：目标A+B", 16)


async def test_compact_summary_capped(test_engine, tmp_path):
    # 摘要链会累积内容，必须封顶，防止 system 尾部无限膨胀
    sf = make_session_factory(test_engine)
    sid = await mk_session(test_engine, tmp_path)
    await _seed_messages(sf, sid, 12)
    llm = ScriptedLLM([[{"type": "done", "message": {"role": "assistant",
                                                     "content": "长" * 9000,
                                                     "tool_calls": None}}]])
    assert await comp.compact(sf, llm, sid, _Rec(), keep_recent=8)
    summary, _ = await comp.latest(sf, sid)
    assert len(summary) == comp.SUMMARY_MAX_CHARS


async def test_compact_digest_contains_image_note_but_not_bytes(test_engine, tmp_path, monkeypatch):
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path))
    from app.services import attachment_service as att
    sf = make_session_factory(test_engine)
    sid = await mk_session(test_engine, tmp_path)
    a = await att.create_attachment(sf, 7, sid, "a.png", b"\x89PNG\r\n\x1a\n" + b"\0" * 8, "image/png")
    b = await att.create_attachment(sf, 7, sid, "b.png", b"\x89PNG\r\n\x1a\n" + b"\0" * 8, "image/png")
    from app.services.message_service import add_message
    await add_message(sf, sid, "user", content="看图", attachment_ids=[a["id"], b["id"]])
    for i in range(11):
        await add_message(sf, sid, "assistant", content=f"答{i}")
    llm = ScriptedLLM([[{"type": "content_delta", "content": "摘要"},
                        {"type": "done", "message": {"role": "assistant", "content": "摘要", "tool_calls": None}}]])
    rec = _Rec()
    assert await comp.compact(sf, llm, sid, rec, keep_recent=8)
    digest = llm.received[0][1]["content"]
    assert "[图片 ×2：a.png, b.png]" in digest
    assert "base64" not in digest
    assert "image_url" not in digest
