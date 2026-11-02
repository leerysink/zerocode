# app/store/identity.py
"""actor 与登录会话存取（Ticket 008 合流 / ADR-0003）：actor 即登录用户，一次登录在
actor_session 落一行凭据，cookie `zc_actor` 带的就是这一行；同一账号可多人同时在线，
上限由 actor.max_sessions 决定。身份建立收编到 /api/auth/register，授权收口在各业务查询
的 owner_user_id 过滤（Tickets 005-007）——本模块只提供「由 token 得人」的认证入口。

历史注脚：M3-a 期商店的「弹层留名」轻身份已退役（匿名 identify 端点删除）；
create_actor 仅剩测试/内部初始化可用，不得再接入任何面向前端的建号路径。
"""
import datetime, secrets
import bcrypt
from sqlalchemy import text
from app.deploy.errors import DeployError

NICK_MAX = 50
ACTOR_COOKIE = "zc_actor"
COOKIE_MAX_AGE = 60 * 60 * 24 * 365
BCRYPT_KEY_MAX_BYTES = 72  # bcrypt 算法截断线，哈希/验证两侧必须同口径，故显式截断而非抛错


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8")[:BCRYPT_KEY_MAX_BYTES], bcrypt.gensalt()).decode("ascii")


def verify_password(plain: str, hashed: str | None) -> bool:
    if not hashed:
        return False
    try:
        return bcrypt.checkpw(plain.encode("utf-8")[:BCRYPT_KEY_MAX_BYTES], hashed.encode("ascii"))
    except ValueError:
        return False


def _clean(nickname) -> str:
    s = (nickname or "").strip()
    if not s:
        raise DeployError("请留下名字")
    if len(s) > NICK_MAX:
        raise DeployError(f"名字过长（上限 {NICK_MAX} 字）")
    return s


def _row_to_dict(r) -> dict:
    d = dict(r)
    for k in ("created_at", "last_seen_at"):
        if d.get(k) is not None:
            d[k] = d[k].isoformat()
    return d


async def create_actor(session_factory, nickname: str) -> dict:
    """建 actor 行（无口令）并附一条会话。面向前端的入口已退役（Ticket 008），仅留给测试与服务内部。"""
    name = _clean(nickname)
    async with session_factory() as s:
        r = (await s.execute(text(
            "INSERT INTO actor (nickname) VALUES (:n) "
            "RETURNING id, nickname, created_at, last_seen_at"),
            {"n": name})).mappings().first()
        await s.commit()
    d = _row_to_dict(r)
    d["token"] = await open_session(session_factory, d["id"])
    return d


async def open_session(session_factory, actor_id: int) -> str:
    """为新登录发一条会话凭据；超出 actor.max_sessions 时挤掉最久未活动的那条。

    修剪用「保留最近 N 条」的 NOT IN 一次完成，登录请求因此永不因「账号坐满」失败——
    演示站多人共用一个账号时，代价由最久没动的人承担，而不是让新访客吃闭门羹。
    """
    token = secrets.token_urlsafe(24)
    async with session_factory() as s:
        limit = (await s.execute(text(
            "SELECT max_sessions FROM actor WHERE id = :i"), {"i": actor_id})).scalar()
        await s.execute(text("INSERT INTO actor_session (actor_id, token) VALUES (:i, :t)"),
                        {"i": actor_id, "t": token})
        await s.execute(text(
            "DELETE FROM actor_session WHERE actor_id = :i AND id NOT IN ("
            "  SELECT id FROM actor_session WHERE actor_id = :i"
            "   ORDER BY last_seen_at DESC, id DESC LIMIT :m)"),
            {"i": actor_id, "m": limit})
        await s.execute(text("UPDATE actor SET last_seen_at = now() WHERE id = :i"),
                        {"i": actor_id})
        await s.commit()
    return token


async def close_session(session_factory, token: str) -> None:
    """只作废当前这一条会话；同账号其他人的会话不受影响。"""
    async with session_factory() as s:
        await s.execute(text("DELETE FROM actor_session WHERE token = :t"), {"t": token})
        await s.commit()


async def by_token(session_factory, token: str | None) -> dict | None:
    """命中即续期 last_seen_at；无 token / 未知 token 一律 None（由端点转 401）。

    用 `UPDATE ... FROM ... RETURNING` 一次完成「查 + 续期」，副作用是续期即写库——只读 GET 上
    也接受一次写，这是刻意的，别改成 SELECT（那样长期不用的人会被误判为失效身份，而我们需要的是
    「这台设备还在用」）；这列同时是 LRU 淘汰的排序依据。
    """
    if not token:
        return None
    async with session_factory() as s:
        r = (await s.execute(text(
            "UPDATE actor_session AS ses SET last_seen_at = now() FROM actor a "
            "WHERE ses.token = :t AND a.id = ses.actor_id "
            "RETURNING a.id, a.nickname, ses.token, a.created_at, ses.last_seen_at, a.is_founder"),
            {"t": token})).mappings().first()
        await s.commit()
    return _row_to_dict(r) if r else None


async def sleep_a_bit() -> None:
    """测试用：制造可观测的时间差（now() 在同一语句内可能同值）。"""
    import asyncio
    await asyncio.sleep(0.02)
