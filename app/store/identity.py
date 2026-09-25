# app/store/identity.py
"""actor 存取（Ticket 008 合流 / ADR-0003）：actor 即登录用户，cookie `zc_actor`
是会话凭据而非设备标识；身份建立收编到 /api/auth/register，授权收口在各业务查询
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
    """建 actor 行（无口令）。面向前端的入口已退役（Ticket 008），仅留给测试与服务内部。"""
    name = _clean(nickname)
    async with session_factory() as s:
        r = (await s.execute(text(
            "INSERT INTO actor (nickname, token) VALUES (:n, :t) "
            "RETURNING id, nickname, token, created_at, last_seen_at"),
            {"n": name, "t": secrets.token_urlsafe(24)})).mappings().first()
        await s.commit()
    return _row_to_dict(r)


async def by_token(session_factory, token: str | None) -> dict | None:
    """命中即续期 last_seen_at；无 token / 未知 token 一律 None（由端点转 401）。

    用 `UPDATE ... RETURNING` 一次完成「查 + 续期」，副作用是续期即写库——只读 GET 上也接受
    一次写，这是刻意的，别改成 SELECT（那样长期不用的人会被误判为失效身份，而我们需要的是
    「这台设备还在用」）。
    """
    if not token:
        return None
    async with session_factory() as s:
        r = (await s.execute(text(
            "UPDATE actor SET last_seen_at = now() WHERE token = :t "
            "RETURNING id, nickname, token, created_at, last_seen_at, is_founder"),
            {"t": token})).mappings().first()
        await s.commit()
    return _row_to_dict(r) if r else None


async def sleep_a_bit() -> None:
    """测试用：制造可观测的时间差（now() 在同一语句内可能同值）。"""
    import asyncio
    await asyncio.sleep(0.02)
