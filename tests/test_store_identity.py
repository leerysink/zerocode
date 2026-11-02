# tests/test_store_identity.py
import pytest
from app.deploy.errors import DeployError
from app.store import identity


async def test_create_actor_makes_unique_token(test_engine):
    from app.db import make_session_factory
    sf = make_session_factory(test_engine)
    # 昵称自 Ticket 002 起全局 UNIQUE（登录锚点），本用例测的是 token 随机性，名字错开
    a = await identity.create_actor(sf, "里恩")
    b = await identity.create_actor(sf, "里恩的替身")
    assert a["id"] != b["id"]
    assert a["token"] != b["token"] and len(a["token"]) >= 24


@pytest.mark.parametrize("bad", ["", "   ", "x" * 51, None])
async def test_nickname_rejected(test_engine, bad):
    from app.db import make_session_factory
    sf = make_session_factory(test_engine)
    with pytest.raises(DeployError):
        await identity.create_actor(sf, bad)


async def test_lookup_by_token_touches_last_seen(test_engine):
    from app.db import make_session_factory
    sf = make_session_factory(test_engine)
    a = await identity.create_actor(sf, "路人")
    await identity.sleep_a_bit()
    hit = await identity.by_token(sf, a["token"])
    assert hit["id"] == a["id"]
    assert hit["last_seen_at"] > a["last_seen_at"], "命中即续期，否则长期不用的人会被当成丢失"
    assert await identity.by_token(sf, "no-such-token") is None
    assert await identity.by_token(sf, None) is None


async def test_sessions_are_independent_per_device(test_engine):
    """一次登录一条会话：同一账号的两条凭据各自在线，删一条不影响另一条。"""
    from app.db import make_session_factory
    sf = make_session_factory(test_engine)
    a = await identity.create_actor(sf, "两台设备")
    second = await identity.open_session(sf, a["id"])
    assert second != a["token"]
    await identity.close_session(sf, a["token"])
    assert await identity.by_token(sf, a["token"]) is None
    assert (await identity.by_token(sf, second))["id"] == a["id"]


async def test_open_session_evicts_least_recently_active_beyond_cap(test_engine):
    """上限来自 actor.max_sessions（默认 10）：第 11 条上线挤掉最久未活动的那条。"""
    from app.db import make_session_factory
    sf = make_session_factory(test_engine)
    a = await identity.create_actor(sf, "十人长")
    tokens = [a["token"]]
    for _ in range(9):
        await identity.sleep_a_bit()
        tokens.append(await identity.open_session(sf, a["id"]))
    assert len(tokens) == 10
    assert all([await identity.by_token(sf, t) for t in tokens])
    await identity.sleep_a_bit()
    eleventh = await identity.open_session(sf, a["id"])
    assert await identity.by_token(sf, tokens[0]) is None, "最久未活动的出局"
    assert (await identity.by_token(sf, tokens[1]))["id"] == a["id"]
    assert (await identity.by_token(sf, eleventh))["id"] == a["id"]
