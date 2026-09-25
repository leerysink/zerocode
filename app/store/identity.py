# app/store/identity.py
"""轻身份 actor（规格 M3-a §6.2 / D8）：昵称 + 随机 token，token 即设备标识，不鉴权。

本期刻意不做密码与登录：商店闭环只需要「谁申请的、这实例归谁」。
因此这里**不得**演化成授权依据——越权收口在 applications/install 侧按 owner_actor 与 origin 判。
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
