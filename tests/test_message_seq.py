"""add_message 的并发安全与归属列（Ticket 001）。

`seq` 由 `max(seq)+1` 现算：子 run 并行落库会在 `ux_message_session_seq` 上撞车。
本文件钉住服务层的两条契约——同会话并发写 seq 连续唯一、归属列可写可读（主 run 行恒空）。
"""
import asyncio

import pytest
from sqlalchemy import text

from app.db import make_session_factory
from app.services.message_service import add_message


async def _mk_session(test_engine, tmp_path) -> int:
    sf = make_session_factory(test_engine)
    async with sf() as s:
        ws = (await s.execute(text(
            "INSERT INTO workspace (name, root_path) VALUES ('t', :p) RETURNING id"),
            {"p": str(tmp_path)})).scalar()
        sid = (await s.execute(text(
            "INSERT INTO chat_session (workspace_id) VALUES (:w) RETURNING id"),
            {"w": ws})).scalar()
        await s.commit()
    return sid


@pytest.mark.asyncio
async def test_concurrent_writes_keep_seq_unique_and_gapless(test_engine, tmp_path):
    sid = await _mk_session(test_engine, tmp_path)
    sf = make_session_factory(test_engine)
    # 两轮各 10 条并发：单轮撞一次唯一索引就会整批抛 IntegrityError
    for _ in range(2):
        await asyncio.gather(*(add_message(sf, sid, "assistant", content=f"m{i}")
                               for i in range(10)))
    async with sf() as s:
        seqs = (await s.execute(text(
            "SELECT seq FROM message WHERE session_id=:i ORDER BY seq"),
            {"i": sid})).scalars().all()
    assert seqs == list(range(1, 21))


@pytest.mark.asyncio
async def test_attribution_columns_round_trip(test_engine, tmp_path):
    sid = await _mk_session(test_engine, tmp_path)
    sf = make_session_factory(test_engine)
    row = await add_message(sf, sid, "tool", tool_call_id="c1", tool_name="read_file",
                            tool_result={"ok": True},
                            parent_tool_call_id="call_9", subrun_index=2)
    assert row["parent_tool_call_id"] == "call_9"
    assert row["subrun_index"] == 2
    plain = await add_message(sf, sid, "user", content="hi")
    assert plain["parent_tool_call_id"] is None
    assert plain["subrun_index"] is None
