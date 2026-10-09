"""Task 9：会话级模型解析与回退链（session → default → global）。"""
from sqlalchemy import text as sql
from app.db import make_session_factory
from app.agent.model_resolver import (
    resolve_embedding_for_user, resolve_model, resolve_model_for_user)
from tests.test_agent_loop import mk_session

CFG = {"llm.base_url": "http://g/v1", "llm.api_key": "gk", "llm.model": "g-model"}

async def _mk_model(sf, **kw):
    async with sf() as s:
        mid = (await s.execute(sql(
            "INSERT INTO model_config (name, base_url, api_key, model, kind, is_default, owner_user_id) "
            "VALUES (:n, :b, :k, :m, :kd, :d, :o) RETURNING id"),
            {"n": kw.get("name", "m"), "b": kw.get("base_url", "http://m/v1"),
             "k": kw.get("api_key", "mk"), "m": kw.get("model", "m-model"),
             "kd": kw.get("kind", "chat"),
             "d": kw.get("is_default", False), "o": kw.get("owner_user_id")})).scalar()
        await s.commit()
    return mid

async def _bind(sf, sid, mid):
    async with sf() as s:
        await s.execute(sql("UPDATE chat_session SET model_config_id=:m WHERE id=:i"), {"m": mid, "i": sid})
        await s.commit()

async def _mk_actor(sf, nickname):
    """model_config.owner_user_id 有 FK 到 actor，隔使用例必须先有真账号。"""
    async with sf() as s:
        uid = (await s.execute(sql(
            "INSERT INTO actor (nickname, token) VALUES (:n, :t) RETURNING id"),
            {"n": nickname, "t": "tok-" + nickname})).scalar()
        await s.commit()
    return uid

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


# ---- resolve_model_for_user：按账号两级回落（技能 AI 生成链路直接消费）----
async def test_for_user_returns_owner_default_row(test_engine):
    """主流程：已知 uid 命中自己的默认行，不经过任何 session。"""
    sf = make_session_factory(test_engine)
    uid = await _mk_actor(sf, "mine-owner")
    await _mk_model(sf, name="mine", model="mine-model", is_default=True, owner_user_id=uid)
    async with sf() as s:
        r = await resolve_model_for_user(s, uid, CFG)
    assert r == {"base_url": "http://m/v1", "api_key": "mk",
                 "model": "mine-model", "source": "default"}


async def test_for_userIgnoresAnotherOwnersDefaultAsGlobal(test_engine):
    """边界（隔离）：只有别人的默认行 → 落全局，不得跨账号拿到别人的 api_key。"""
    sf = make_session_factory(test_engine)
    mine = await _mk_actor(sf, "me")
    theirs = await _mk_actor(sf, "theirs")
    await _mk_model(sf, name="theirs", api_key="secret", is_default=True, owner_user_id=theirs)
    async with sf() as s:
        r = await resolve_model_for_user(s, mine, CFG)
    assert r["source"] == "global" and r["api_key"] == "gk" and r["model"] == "g-model"


async def test_for_userMatchesLegacyNullOwnerWhenUidNone(test_engine):
    """边界（遗留数据）：uid 为 None 时 IS NOT DISTINCT FROM 仍命中无主默认行。"""
    sf = make_session_factory(test_engine)
    await _mk_model(sf, name="legacy", model="legacy-model", is_default=True)
    async with sf() as s:
        r = await resolve_model_for_user(s, None, CFG)
    assert r["source"] == "default" and r["model"] == "legacy-model"


async def test_for_userKeepsEmptyApiKeyAsEmptyString(test_engine):
    """边界（空值）：api_key 为空串时不会被全局 cfg 的 key 替掉（列 NOT NULL，只能存空串）。"""
    sf = make_session_factory(test_engine)
    uid = await _mk_actor(sf, "nokey-owner")
    await _mk_model(sf, name="nokey", api_key="", is_default=True, owner_user_id=uid)
    async with sf() as s:
        r = await resolve_model_for_user(s, uid, CFG)
    assert r["source"] == "default" and r["api_key"] == ""


async def test_for_userWithEmptyGlobalConfigYieldsNoneModel(test_engine):
    """边界（未配置）：既无默认行又无全局 llm.* → 字段回 None/""，由调用方判 400。"""
    sf = make_session_factory(test_engine)
    uid = await _mk_actor(sf, "nobody-owner")
    async with sf() as s:
        r = await resolve_model_for_user(s, uid, {})
    assert r == {"base_url": None, "api_key": "", "model": None, "source": "global"}


# ---- kind（Ticket 005 / D-2）：对话链路不吃 embedding 档案，embedding 也不回落全局 ----
async def test_resolveModelSkipsEmbeddingProfileBoundToSession(test_engine, tmp_path):
    """越界绑定：把向量档案直绑到会话也不能当对话模型用（拿它发 chat 请求必 400）。"""
    sf = make_session_factory(test_engine)
    sid = await mk_session(test_engine, tmp_path)
    vec = await _mk_model(sf, name="vec", kind="embedding", model="e-vec")
    await _bind(sf, sid, vec)
    r = await resolve_model(sf, sid, CFG)
    assert r["source"] == "global" and r["model"] == "g-model"


async def test_forUserDefaultRowSkipsEmbeddingDefault(test_engine):
    """只有「默认 + embedding」一行时，对话回落必须跳过它（否则默认向量档案会顶掉全局对话）。"""
    sf = make_session_factory(test_engine)
    uid = await _mk_actor(sf, "vec-only")
    await _mk_model(sf, name="vec", kind="embedding", model="e-vec",
                    is_default=True, owner_user_id=uid)
    async with sf() as s:
        r = await resolve_model_for_user(s, uid, CFG)
    assert r["source"] == "global" and r["model"] == "g-model"


async def test_resolveEmbeddingReturnsOwnerDefaultProfile(test_engine):
    """主流程：同一 owner 同时有默认 chat 和默认 embedding，各自命中各自那一类。"""
    sf = make_session_factory(test_engine)
    uid = await _mk_actor(sf, "both-owner")
    await _mk_model(sf, name="chat", is_default=True, owner_user_id=uid)
    await _mk_model(sf, name="vec", kind="embedding", model="text-embedding-v3",
                    is_default=True, owner_user_id=uid)
    async with sf() as s:
        r = await resolve_embedding_for_user(s, uid)
    assert r == {"base_url": "http://m/v1", "api_key": "mk",
                 "model": "text-embedding-v3", "source": "embedding"}


async def test_resolveEmbeddingDoesNotFallBackToGlobalLlm(test_engine):
    """降级红线：没有 embedding 档案就返回 None，绝不拿全局 llm.* 去发一次必然失败的请求。"""
    sf = make_session_factory(test_engine)
    uid = await _mk_actor(sf, "no-vec-owner")
    await _mk_model(sf, name="chat", is_default=True, owner_user_id=uid)
    async with sf() as s:
        assert await resolve_embedding_for_user(s, uid) is None


async def test_resolveEmbeddingPrefersDefaultThenNewestId(test_engine):
    """边界（多条无默认）：取最新一条；一旦有默认位就反转（默认位优先于新鲜度）。"""
    sf = make_session_factory(test_engine)
    uid = await _mk_actor(sf, "two-vec-owner")
    old = await _mk_model(sf, name="old", kind="embedding", model="old-vec", owner_user_id=uid)
    await _mk_model(sf, name="new", kind="embedding", model="new-vec", owner_user_id=uid)
    async with sf() as s:
        r = await resolve_embedding_for_user(s, uid)
    assert r["model"] == "new-vec"
    async with sf() as s:
        await s.execute(sql("UPDATE model_config SET is_default=TRUE WHERE id=:i"), {"i": old})
        await s.commit()
    async with sf() as s:
        r = await resolve_embedding_for_user(s, uid)
    assert r["model"] == "old-vec"


async def test_resolveEmbeddingIgnoresAnotherOwnersProfile(test_engine):
    """隔离：别人的向量档案即使设了默认也拿不到（那里面是别人的 api_key）。"""
    sf = make_session_factory(test_engine)
    mine = await _mk_actor(sf, "me-vec")
    theirs = await _mk_actor(sf, "them-vec")
    await _mk_model(sf, name="t", kind="embedding", api_key="secret",
                    model="t-vec", is_default=True, owner_user_id=theirs)
    async with sf() as s:
        assert await resolve_embedding_for_user(s, mine) is None


async def test_resolveEmbeddingMatchesLegacyNullOwner(test_engine):
    """边界（遗留数据）：uid 为 None 时 IS NOT DISTINCT FROM 仍命中无主向量档案。"""
    sf = make_session_factory(test_engine)
    await _mk_model(sf, name="legacy-vec", kind="embedding", model="legacy-vec",
                    is_default=True)
    async with sf() as s:
        r = await resolve_embedding_for_user(s, None)
    assert r["source"] == "embedding" and r["model"] == "legacy-vec"


async def test_resolveEmbeddingKeepsEmptyApiKeyAsEmptyString(test_engine):
    """边界（空值）：本地向量服务（Ollama 类）就是没有 key，空串不能变成 None。"""
    sf = make_session_factory(test_engine)
    uid = await _mk_actor(sf, "nokey-vec")
    await _mk_model(sf, name="local", kind="embedding", api_key="",
                    model="nomic", is_default=True, owner_user_id=uid)
    async with sf() as s:
        r = await resolve_embedding_for_user(s, uid)
    assert r["api_key"] == "" and r["source"] == "embedding"
