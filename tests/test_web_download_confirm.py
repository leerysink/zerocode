"""web_download 确认流与变更记录回滚口径（ADR-0054，US-09/13）。

helper 复用 test_agent_loop / test_web_tools 的既有桩；单独成文件，不改有并行会话 WIP 的
test_agent_loop.py。网络一律替身，确认闸验的是注册表 CONFIRM_WRITE 通用分支对新工具生效。
"""
import asyncio

import pytest
from sqlalchemy import text as sql

from app.agent.llm import ScriptedLLM
from app.agent.loop import AgentRunner
from app.agent.tools import ToolContext, ToolError, execute_tool
from app.db import make_session_factory
from tests.test_agent_loop import Recorder, cfg_provider, done, mk_session, tool_call
from tests.test_web_tools import _Client, _Resp, _patch_httpx


def _turns():
    return [done(None, [tool_call("c1", "web_download",
                                  url="https://example.com/a.png", path="a.png")]),
            done("下好了")]


async def _run(test_engine, tmp_path, monkeypatch, *, write_confirm, confirm):
    _patch_httpx(monkeypatch, _Client([_Resp(200, {"content-type": "image/png"}, body=b"png")]))
    sid = await mk_session(test_engine, tmp_path)
    sf = make_session_factory(test_engine)
    rec = Recorder()
    runner = AgentRunner(sf, ScriptedLLM(_turns()), cfg_provider(write_confirm=write_confirm),
                         rec, lambda *a, **k: asyncio.sleep(0, None), confirm)
    status = await runner.run(sid, "开始", asyncio.Event())
    return sid, status, rec.events, sf


@pytest.mark.asyncio
async def test_download_confirm_approved_writes_file(test_engine, tmp_path, monkeypatch):
    seen = []

    async def confirm_yes(session_id, payload):
        seen.append(payload)
        return True

    sid, status, evs, sf = await _run(test_engine, tmp_path, monkeypatch,
                                      write_confirm=True, confirm=confirm_yes)
    assert status == "idle"
    assert seen and seen[0]["tool"] == "web_download"
    assert any(e["type"] == "confirm_request" and e["tool"] == "web_download" for e in evs)
    assert (tmp_path / "a.png").read_bytes() == b"png"
    tr = next(e for e in evs if e["type"] == "tool_result")
    assert tr["result"]["ok"] is True and tr["result"].get("confirm") == "approved"


@pytest.mark.asyncio
async def test_download_confirm_denied_leaves_no_file(test_engine, tmp_path, monkeypatch):
    async def confirm_no(session_id, payload):
        return False

    sid, status, evs, sf = await _run(test_engine, tmp_path, monkeypatch,
                                      write_confirm=True, confirm=confirm_no)
    assert status == "idle"
    assert not (tmp_path / "a.png").exists()
    assert any(e["type"] == "confirm_request" for e in evs)
    tr = next(e for e in evs if e["type"] == "tool_result")
    assert tr["result"]["ok"] is False and tr["result"].get("confirm") == "denied"


@pytest.mark.asyncio
async def test_download_confirm_off_writes_without_card(test_engine, tmp_path, monkeypatch):
    sid, status, evs, sf = await _run(test_engine, tmp_path, monkeypatch,
                                      write_confirm=False,
                                      confirm=lambda *a, **k: asyncio.sleep(0, None))
    assert status == "idle"
    assert not any(e["type"] == "confirm_request" for e in evs)
    assert (tmp_path / "a.png").read_bytes() == b"png"


@pytest.mark.asyncio
async def test_download_change_record_is_not_revertible(test_engine, tmp_path, monkeypatch):
    sid, status, evs, sf = await _run(test_engine, tmp_path, monkeypatch,
                                      write_confirm=False,
                                      confirm=lambda *a, **k: asyncio.sleep(0, None))
    async with sf() as s:
        row = (await s.execute(sql(
            "SELECT action, before_content, after_content, diff FROM file_change "
            "WHERE session_id=:i AND path='a.png'"), {"i": sid})).mappings().first()
    assert row is not None and row["action"] == "create"
    assert row["before_content"] is None and row["after_content"] is None
    assert row["diff"] == ""
    ctx = ToolContext(root=tmp_path, session_id=sid, session_factory=sf,
                      send=lambda ev: asyncio.sleep(0, None))
    with pytest.raises(ToolError, match="没有可回滚的改动记录"):
        await execute_tool(ctx, "revert_change", {"path": "a.png"})
    assert (tmp_path / "a.png").read_bytes() == b"png"