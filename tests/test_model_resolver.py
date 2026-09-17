"""Task 9：会话级模型解析与回退链（session → default → global）。"""
from sqlalchemy import text as sql
from app.db import make_session_factory
from app.agent.model_resolver import resolve_model
from tests.test_agent_loop import mk_session

CFG = {"llm.base_url": "http://g/v1", "llm.api_key": "gk", "llm.model": "g-model"}

async def _mk_model(sf, **kw):
    async with sf() as s:
        mid = (await s.execute(sql(
            "INSERT INTO model_config (name, base_url, api_key, model, is_default) "
            "VALUES (:n, :b, :k, :m, :d) RETURNING id"),
            {"n": kw.get("name", "m"), "b": kw.get("base_url", "http://m/v1"),
             "k": kw.get("api_key", "mk"), "m": kw.get("model", "m-model"),
             "d": kw.get("is_default", False)})).scalar()
        await s.commit()
    return mid

async def _bind(sf, sid, mid):
    async with sf() as s:
        await s.execute(sql("UPDATE chat_session SET model_config_id=:m WHERE id=:i"), {"m": mid, "i": sid})
        await s.commit()

async def test_fallback_global(test_engine, tmp_path):
    sf = make_session_factory(test_engine)
    sid = await mk_session(test_engine, tmp_path)
    r = await resolve_model(sf, sid, CFG)
    assert r == {"base_url": "http://g/v1", "api_key": "gk", "model": "g-model", "source": "global"}

async def test_default_used_when_session_null(test_engine, tmp_path):
    sf = make_session_factory(test_engine)
    sid = await mk_session(test_engine, tmp_path)
    await _mk_model(sf, name="dft", is_default=True)
    r = await resolve_model(sf, sid, CFG)
    assert r["source"] == "default" and r["model"] == "m-model"

async def test_session_model_wins(test_engine, tmp_path):
    sf = make_session_factory(test_engine)
    sid = await mk_session(test_engine, tmp_path)
    await _mk_model(sf, name="dft", is_default=True)
    other = await _mk_model(sf, name="other", model="other-model")
    await _bind(sf, sid, other)
    r = await resolve_model(sf, sid, CFG)
    assert r["source"] == "session" and r["model"] == "other-model"
