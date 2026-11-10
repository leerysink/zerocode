"""Agent 工具度量（2026-11-10）：每次工具调用落一行，聚合查询回看。

度量是**旁路**：任何写入失败都不能影响对话本身，所以服务层吞异常只记日志——
这条纪律用 `test_record_is_best_effort` 钉住，别让后来的重构把 try 拆掉。
"""
import asyncio
import json

import pytest
from sqlalchemy import text as sql

from app.agent.llm import ScriptedLLM
from app.agent.loop import AgentRunner
from app.db import make_session_factory
from app.services import tool_metric_service as tm


def done(content, tool_calls=None):
    return [{"type": "done", "message": {"role": "assistant", "content": content,
                                         "tool_calls": tool_calls}}]


def tool_call(id_, name, **args):
    return {"id": id_, "function": {"name": name,
                                    "arguments": json.dumps(args, ensure_ascii=False)}}


def cfg_provider(write_confirm=False, clarify=False, **over):
    async def _p():
        return {"agent.write_confirm": write_confirm, "agent.ask_timeout_seconds": 0,
                "agent.max_tool_rounds": 30, "agent.history_window": 40,
                "clarify.enabled": clarify, **over}
    return _p


async def _mk_workspace_session(sf, tmp_path, *, owner=None, name="t") -> int:
    async with sf() as s:
        wid = (await s.execute(sql(
            "INSERT INTO workspace (name, root_path, owner_user_id) VALUES (:n, :p, :o) "
            "RETURNING id"), {"n": name, "p": str(tmp_path), "o": owner})).scalar()
        sid = (await s.execute(sql(
            "INSERT INTO chat_session (workspace_id) VALUES (:w) RETURNING id"), {"w": wid})).scalar()
        await s.commit()
    return sid


async def _rows(sf, session_id=None):
    async with sf() as s:
        q = ("SELECT tool_name, ok, duration_ms, result_chars, truncated, denied, round_no "
             "FROM agent_tool_metric ")
        params = {}
        if session_id is not None:
            q += "WHERE session_id = :i "
            params["i"] = session_id
        q += "ORDER BY id"
        return (await s.execute(sql(q), params)).mappings().all()


# ---------- 服务层 ----------

async def test_record_inserts_one_row(test_engine, tmp_path):
    sf = make_session_factory(test_engine)
    sid = await _mk_workspace_session(sf, tmp_path)
    await tm.record(sf, session_id=sid, tool_name="read_file", ok=True, duration_ms=12,
                    result_chars=345, truncated=False, denied=False, round_no=1)
    rows = await _rows(sf, sid)
    assert len(rows) == 1
    r = rows[0]
    assert (r["tool_name"], r["ok"], r["duration_ms"], r["result_chars"],
            r["truncated"], r["denied"], r["round_no"]) == ("read_file", True, 12, 345, False, False, 1)


async def test_record_is_best_effort(test_engine):
    """会话已删/库抖动都不能把一次工具调用打成异常：度量失败只记日志。"""
    class Boom:
        def __call__(self):
            raise RuntimeError("db down")

    await tm.record(Boom(), session_id=1, tool_name="list_dir", ok=True, duration_ms=1,
                    result_chars=2, truncated=False, denied=False, round_no=0)


async def test_summary_groups_by_tool_and_counts_outcomes(test_engine, tmp_path):
    sf = make_session_factory(test_engine)
    sid = await _mk_workspace_session(sf, tmp_path)
    for name, ok, ms, chars, trunc, denied in [
            ("read_file", True, 10, 100, False, False),
            ("read_file", False, 30, 20, False, False),
            ("db_query", True, 20, 500, True, False),
            ("write_file", False, 4, 0, False, True)]:
        await tm.record(sf, session_id=sid, tool_name=name, ok=ok, duration_ms=ms,
                        result_chars=chars, truncated=trunc, denied=denied, round_no=0)
    out = await tm.summary(sf, window="1h")
    t = out["totals"]
    assert (t["calls"], t["ok"], t["failed"], t["denied"], t["truncated"]) == (4, 2, 2, 1, 1)
    assert t["avg_ms"] == pytest.approx(16.0)
    by = {r["tool"]: r for r in out["by_tool"]}
    assert set(by) == {"read_file", "db_query", "write_file"}
    assert by["read_file"]["calls"] == 2 and by["read_file"]["failed"] == 1
    assert by["read_file"]["avg_ms"] == pytest.approx(20.0)
    assert by["db_query"]["truncated"] == 1


async def test_summary_window_excludes_old_rows(test_engine, tmp_path):
    sf = make_session_factory(test_engine)
    sid = await _mk_workspace_session(sf, tmp_path)
    await tm.record(sf, session_id=sid, tool_name="list_dir", ok=True, duration_ms=1,
                    result_chars=2, truncated=False, denied=False, round_no=0)
    async with sf() as s:
        await s.execute(sql(
            "INSERT INTO agent_tool_metric (session_id, tool_name, ok, duration_ms, "
            "result_chars, round_no, created_at) "
            "VALUES (:i, 'list_dir', TRUE, 1, 2, 0, now() - make_interval(hours => 3))"),
            {"i": sid})
        await s.commit()
    assert (await tm.summary(sf, window="1h"))["totals"]["calls"] == 1
    assert (await tm.summary(sf, window="24h"))["totals"]["calls"] == 2


async def test_summary_owner_scope(test_engine, tmp_path):
    """owner_user_id 给定时只看自己工作区下的会话（founder 传 None 看全部）。"""
    sf = make_session_factory(test_engine)
    async with sf() as s:
        alice = (await s.execute(sql(
            "INSERT INTO actor (nickname, token) VALUES ('a', 'tok-a') RETURNING id"))).scalar()
        bob = (await s.execute(sql(
            "INSERT INTO actor (nickname, token) VALUES ('b', 'tok-b') RETURNING id"))).scalar()
        await s.commit()
    sid_a = await _mk_workspace_session(sf, tmp_path / "a", owner=alice, name="wa")
    sid_b = await _mk_workspace_session(sf, tmp_path / "b", owner=bob, name="wb")
    for sid, name in ((sid_a, "read_file"), (sid_b, "db_query")):
        await tm.record(sf, session_id=sid, tool_name=name, ok=True, duration_ms=1,
                        result_chars=2, truncated=False, denied=False, round_no=0)
    mine = await tm.summary(sf, window="1h", owner_user_id=alice)
    assert {r["tool"] for r in mine["by_tool"]} == {"read_file"}
    everything = await tm.summary(sf, window="1h")
    assert {r["tool"] for r in everything["by_tool"]} == {"read_file", "db_query"}


# ---------- REST ----------

async def test_api_returns_aggregation(client, tmp_path):
    sf = client.app.state.session_factory
    uid = (await client.get("/api/auth/me")).json()["id"]
    sid = await _mk_workspace_session(sf, tmp_path, owner=uid)
    await tm.record(sf, session_id=sid, tool_name="read_file", ok=True, duration_ms=7,
                    result_chars=10, truncated=False, denied=False, round_no=0)
    r = await client.get("/api/agent/tool-metrics", params={"window": "24h"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["window"] == "24h" and body["since"]
    assert body["totals"]["calls"] == 1
    assert body["by_tool"][0]["tool"] == "read_file"


async def test_api_rejects_unknown_window(client):
    r = await client.get("/api/agent/tool-metrics", params={"window": "5m"})
    assert r.status_code == 400


async def test_api_isolates_other_owners_and_founder_sees_all(two_auth_clients, tmp_path):
    alice, bob = two_auth_clients   # 先注册的 alice 是创始人
    sf = alice.app.state.session_factory
    bob_uid = (await bob.get("/api/auth/me")).json()["id"]
    sid = await _mk_workspace_session(sf, tmp_path, owner=bob_uid)
    await tm.record(sf, session_id=sid, tool_name="read_file", ok=True, duration_ms=1,
                    result_chars=2, truncated=False, denied=False, round_no=0)
    # bob 看自己的：1 条；创始人 alice 看全部：也是 1 条（但来源是 bypass 而不是归属）
    assert (await bob.get("/api/agent/tool-metrics")).json()["totals"]["calls"] == 1
    assert (await alice.get("/api/agent/tool-metrics")).json()["totals"]["calls"] == 1
    # 第三个账号（非创始人、无自己数据）：看不到 bob 的行
    await _assert_third_sees_nothing(alice)


async def _assert_third_sees_nothing(client):
    """注册第三个账号并自查其度量表为空（非创始人只看到自己工作区下的行）。"""
    from httpx import AsyncClient, ASGITransport
    c = AsyncClient(transport=ASGITransport(app=client.app), base_url="http://t")
    try:
        r = await c.post("/api/auth/register", json={"nickname": "carol", "password": "pw-c"})
        assert r.status_code == 200, r.text
        assert (await c.get("/api/agent/tool-metrics")).json()["totals"]["calls"] == 0
    finally:
        await c.aclose()


# ---------- 循环接线 ----------

async def _run_loop(test_engine, tmp_path, turns, **cfg):
    sf = make_session_factory(test_engine)
    sid = await _mk_workspace_session(sf, tmp_path)
    events = []

    async def send(ev):
        events.append(ev)

    runner = AgentRunner(sf, ScriptedLLM(turns), cfg_provider(**cfg), send,
                         lambda *a, **k: asyncio.sleep(0, None),
                         cfg.get("_confirm", lambda *a, **k: True))
    status = await runner.run(sid, "开始", asyncio.Event())
    return sid, status, events, sf


async def test_loop_records_one_metric_per_tool_call(test_engine, tmp_path):
    turns = [done(None, [tool_call("c1", "list_dir", path=".")]), done("好了")]
    sid, status, _ev, sf = await _run_loop(test_engine, tmp_path, turns)
    assert status == "idle"
    rows = await _rows(sf, sid)
    assert len(rows) == 1
    r = rows[0]
    assert r["tool_name"] == "list_dir" and r["ok"] is True
    assert r["round_no"] == 0 and r["duration_ms"] >= 0 and r["result_chars"] > 0
    assert r["truncated"] is False and r["denied"] is False


async def test_loop_records_denied_write(test_engine, tmp_path):
    turns = [done(None, [tool_call("c1", "write_file", path="a.txt", content="hi")]), done("没写")]
    sid, status, _ev, sf = await _run_loop(
        test_engine, tmp_path, turns, write_confirm=True,
        _confirm=lambda *a, **k: asyncio.sleep(0, False))
    assert status == "idle"
    rows = await _rows(sf, sid)
    assert len(rows) == 1
    assert rows[0]["tool_name"] == "write_file"
    assert rows[0]["denied"] is True and rows[0]["ok"] is False
