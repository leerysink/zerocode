import asyncio
import pytest
from sqlalchemy import text as sql

from app.agent.llm import ScriptedLLM
from app.agent.loop import AgentRunner
from app.db import make_session_factory
from app.services import attachment_service as att
from app.services.message_service import add_message
from tests.test_agent_loop import Recorder, cfg_provider, done, mk_session, tool_call

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def _cfg():
    async def _p():
        return {"agent.write_confirm": False, "agent.ask_timeout_seconds": 0,
                "agent.max_tool_rounds": 30, "agent.history_window": 40}
    return _p


async def _run(sf, sid, text_, ids=None):
    llm = ScriptedLLM([done("看到了")])
    rec = Recorder()
    runner = AgentRunner(sf, llm, _cfg(), rec,
                         lambda *a, **k: asyncio.sleep(0, None),
                         lambda *a, **k: True)
    await runner.run(sid, text_, asyncio.Event(), attachment_ids=ids)
    return llm.received[-1]


def _last_user(messages):
    return next(m for m in reversed(messages) if m["role"] == "user")


async def test_current_message_with_image_uses_parts(test_engine, tmp_path, monkeypatch):
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path))
    sf = make_session_factory(test_engine)
    sid = await mk_session(test_engine, tmp_path)
    a = await att.create_attachment(sf, 7, sid, "a.png", PNG, "image/png")
    msgs = await _run(sf, sid, "这是什么", [a["id"]])
    user = _last_user(msgs)
    assert isinstance(user["content"], list)
    assert user["content"][0] == {"type": "text", "text": "这是什么"}
    assert user["content"][1]["type"] == "image_url"
    assert user["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")


async def test_pure_image_message_has_image_placeholder_text(test_engine, tmp_path, monkeypatch):
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path))
    sf = make_session_factory(test_engine)
    sid = await mk_session(test_engine, tmp_path)
    a = await att.create_attachment(sf, 7, sid, "a.png", PNG, "image/png")
    msgs = await _run(sf, sid, "", [a["id"]])
    user = _last_user(msgs)
    assert user["content"][0] == {"type": "text", "text": "[图片]"}


async def test_only_last_two_user_messages_with_images_inline(
        test_engine, tmp_path, monkeypatch):
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path))
    sf = make_session_factory(test_engine)
    sid = await mk_session(test_engine, tmp_path)
    a = await att.create_attachment(sf, 7, sid, "old.png", PNG, "image/png")
    b = await att.create_attachment(sf, 7, sid, "mid.png", PNG, "image/png")
    c = await att.create_attachment(sf, 7, sid, "new.png", PNG, "image/png")
    await add_message(sf, sid, "user", content="第一", attachment_ids=[a["id"]])
    await add_message(sf, sid, "assistant", content="好")
    await add_message(sf, sid, "user", content="第二", attachment_ids=[b["id"]])
    await add_message(sf, sid, "assistant", content="好")
    await add_message(sf, sid, "user", content="第三", attachment_ids=[c["id"]])
    await add_message(sf, sid, "assistant", content="好")
    msgs = await _run(sf, sid, "第四")
    user_msgs = [m for m in msgs if m["role"] == "user"]
    assert isinstance(user_msgs[0]["content"], str)
    assert "较早的图片已省略" in user_msgs[0]["content"]
    # 第二、第三条带图消息内联
    assert isinstance(user_msgs[1]["content"], list)
    assert isinstance(user_msgs[2]["content"], list)


async def test_inline_budget_drops_oldest_history_keeps_current(
        test_engine, tmp_path, monkeypatch):
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(att, "MAX_INLINE_BYTES", 70)
    sf = make_session_factory(test_engine)
    sid = await mk_session(test_engine, tmp_path)
    big = PNG + b"x" * 50  # 每张 66 字节
    a = await att.create_attachment(sf, 7, sid, "a.png", big, "image/png")
    b = await att.create_attachment(sf, 7, sid, "b.png", big, "image/png")
    c = await att.create_attachment(sf, 7, sid, "c.png", big, "image/png")
    await add_message(sf, sid, "user", content="一", attachment_ids=[a["id"]])
    await add_message(sf, sid, "assistant", content="好")
    await add_message(sf, sid, "user", content="二", attachment_ids=[b["id"]])
    await add_message(sf, sid, "assistant", content="好")
    msgs = await _run(sf, sid, "三", [c["id"]])
    user_msgs = [m for m in msgs if m["role"] == "user"]
    assert isinstance(user_msgs[-1]["content"], list)  # 当前消息保留图片
    assert isinstance(user_msgs[-2]["content"], str)
    assert "较早的图片已省略" in user_msgs[-2]["content"]


async def test_missing_file_degrades_to_placeholder(test_engine, tmp_path, monkeypatch):
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path))
    sf = make_session_factory(test_engine)
    sid = await mk_session(test_engine, tmp_path)
    a = await att.create_attachment(sf, 7, sid, "a.png", PNG, "image/png")
    next((tmp_path / "users" / "7" / "chat_uploads" / str(sid)).glob("*.png")).unlink()
    msgs = await _run(sf, sid, "看图", [a["id"]])
    user = _last_user(msgs)
    assert user["content"][0]["text"].endswith("[图片缺失：a.png]")


async def test_missing_file_does_not_consume_inline_budget(
        test_engine, tmp_path, monkeypatch):
    """外部删掉的文件不进内联账本：不能因为一张已经不发的图把有效旧图挤成占位。"""
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(att, "MAX_INLINE_BYTES", 100)
    sf = make_session_factory(test_engine)
    sid = await mk_session(test_engine, tmp_path)
    small = PNG + b"x" * 16  # 40 字节
    seen = await att.create_attachment(sf, 7, sid, "seen.png", small, "image/png")
    lost = await att.create_attachment(sf, 7, sid, "lost.png", small, "image/png")
    cur = await att.create_attachment(sf, 7, sid, "cur.png", small, "image/png")
    await add_message(sf, sid, "user", content="旧",
                      attachment_ids=[seen["id"], lost["id"]])
    await add_message(sf, sid, "assistant", content="好")
    await add_message(sf, sid, "user", content="新", attachment_ids=[cur["id"]])
    await add_message(sf, sid, "assistant", content="好")
    async with sf() as s:
        lost_path = (await s.execute(sql(
            "SELECT storage_path FROM message_attachment WHERE id=:i"),
            {"i": lost["id"]})).scalar()
    att.resolve_storage_path(lost_path).unlink()

    runner = AgentRunner(sf, ScriptedLLM([]), _cfg(), Recorder(),
                         lambda *a, **k: asyncio.sleep(0, None),
                         lambda *a, **k: True)
    msgs, _ = await runner._history(sid, 40)
    user_msgs = [m for m in msgs if m["role"] == "user"]
    # 正确账本 40(seen) + 40(cur) = 80 ≤ 100 → 旧消息保留内联；
    # 若把缺失的 lost 也算进去（120 > 100），旧消息会被整条降级成占位。
    assert runner._inline_used == 2 * len(small)
    assert isinstance(user_msgs[0]["content"], list)
    assert [p["type"] for p in user_msgs[0]["content"]] == ["text", "image_url"]
    assert user_msgs[0]["content"][0]["text"].endswith("[图片缺失：lost.png]")
    assert isinstance(user_msgs[1]["content"], list)


def _pop_third(items):
    """只在第 3 次弹出时返回插话：第 1 次是 _inner 预清理，第 2 次是 round 0 边界。"""
    calls = {"n": 0}

    async def _pop(session_id):
        calls["n"] += 1
        return list(items) if calls["n"] == 3 else []

    return _pop


async def _run_with_steering(sf, sid, text_, ids, steering_items):
    llm = ScriptedLLM([done(None, [tool_call("t1", "list_dir")]), done("好")])
    rec = Recorder()
    runner = AgentRunner(sf, llm, _cfg(), rec,
                         lambda *a, **k: asyncio.sleep(0, None),
                         lambda *a, **k: True,
                         pop_pending=_pop_third(steering_items))
    await runner.run(sid, text_, asyncio.Event(), attachment_ids=ids)
    return llm, rec


async def test_steering_image_inlines_within_budget(test_engine, tmp_path, monkeypatch):
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path))
    sf = make_session_factory(test_engine)
    sid = await mk_session(test_engine, tmp_path)
    cur = await att.create_attachment(sf, 7, sid, "cur.png", PNG, "image/png")
    steer = await att.create_attachment(sf, 7, sid, "插话.png", PNG, "image/png")
    llm, rec = await _run_with_steering(sf, sid, "看图", [cur["id"]], [
        {"content": "再看这张", "attachment_ids": [steer["id"]],
         "client_msg_id": "s1"}])
    user = _last_user(llm.received[1])
    assert isinstance(user["content"], list)
    assert user["content"][0] == {"type": "text", "text": "再看这张"}
    assert user["content"][1]["type"] == "image_url"
    assert user["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert any(e["type"] == "steering_done" and e["client_msg_id"] == "s1"
               for e in rec.events)


async def test_steering_image_over_budget_falls_back_to_placeholder(
        test_engine, tmp_path, monkeypatch):
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(att, "MAX_INLINE_BYTES", 70)
    sf = make_session_factory(test_engine)
    sid = await mk_session(test_engine, tmp_path)
    small = PNG + b"x" * 16  # 40 字节：单张进预算，历史 40 + 插话 40 > 70
    cur = await att.create_attachment(sf, 7, sid, "cur.png", small, "image/png")
    steer = await att.create_attachment(sf, 7, sid, "插话.png", small, "image/png")
    llm, rec = await _run_with_steering(sf, sid, "看图", [cur["id"]], [
        {"content": "再看这张", "attachment_ids": [steer["id"]],
         "client_msg_id": "s2"}])
    user = _last_user(llm.received[1])
    assert isinstance(user["content"], str)
    assert "较早的图片已省略" in user["content"]
    assert "插话.png" in user["content"]
    # 预算先到先得：历史里的当前消息仍带着图
    user_msgs = [m for m in llm.received[1] if m["role"] == "user"]
    assert isinstance(user_msgs[0]["content"], list)
    # 送达不因降级中断
    assert any(e["type"] == "steering_done" and e["client_msg_id"] == "s2"
               for e in rec.events)
