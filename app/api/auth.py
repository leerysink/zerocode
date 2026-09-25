# app/api/auth.py
"""登录体系端点（Ticket 002 / ADR-0003）：register/login/logout/me。

actor 即登录主体：cookie `zc_actor` 语义从设备标识升级为会话凭据；
口令 bcrypt 落库，token 在注册/登录/登出时轮换（旧会话随之失效，本地场景裁定接受）。
"""
import secrets

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.store import identity
from app.store.identity import ACTOR_COOKIE, COOKIE_MAX_AGE, hash_password, verify_password

router = APIRouter(prefix="/api/auth")


class CredIn(BaseModel):
    nickname: str | None = None
    password: str | None = None


def _clean_credential(body: CredIn) -> tuple[str, str]:
    try:
        nickname = identity._clean(body.nickname)
    except identity.DeployError as e:
        raise HTTPException(400, str(e))
    password = body.password or ""
    if not password:
        raise HTTPException(400, "请设置口令")
    return nickname, password


def _issue_session(response: Response, token: str) -> None:
    response.set_cookie(ACTOR_COOKIE, token, max_age=COOKIE_MAX_AGE,
                        httponly=True, samesite="lax")


@router.post("/register")
async def register(request: Request, response: Response, body: CredIn):
    nickname, password = _clean_credential(body)
    token = secrets.token_urlsafe(24)
    try:
        async with request.app.state.session_factory() as s:
            # 创始人判定与承接同事务（Ticket 004 / ADR-0003）：首账号把无主存量划归自己，
            # 中途失败不留半划归。本地单实例并发注册撞车概率极低，不另加锁。
            is_first = (await s.execute(text("SELECT NOT EXISTS (SELECT 1 FROM actor)"))).scalar()
            row = (await s.execute(text(
                "INSERT INTO actor (nickname, token, password_hash, is_founder) "
                "VALUES (:n, :t, :p, :f) RETURNING id, nickname, is_founder"),
                {"n": nickname, "t": token, "p": hash_password(password),
                 "f": is_first})).mappings().first()
            if is_first:
                founder_id = row["id"]
                for table in ("workspace", "app_project", "model_config"):
                    # 表名白名单硬编码，不接外部输入
                    await s.execute(text(
                        f"UPDATE {table} SET owner_user_id = :i WHERE owner_user_id IS NULL"),
                        {"i": founder_id})
                # 全局 llm.* 配置划归（UPDATE 非复制）：后来用户看不到创始人密钥；
                # agent.*/deploy.*/store.*/dbcred.% 留 NULL = 平台级
                await s.execute(text(
                    "UPDATE app_config SET owner_user_id = :i "
                    "WHERE owner_user_id IS NULL AND key LIKE 'llm.%'"),
                    {"i": founder_id})
            await s.commit()
    except IntegrityError:
        raise HTTPException(409, "昵称已被占用")
    _issue_session(response, token)
    return dict(row)


@router.post("/login")
async def login(request: Request, response: Response, body: CredIn):
    nickname = (body.nickname or "").strip()
    async with request.app.state.session_factory() as s:
        row = (await s.execute(text(
            "SELECT id, nickname, password_hash FROM actor WHERE nickname = :n"),
            {"n": nickname})).mappings().first()
        if row is None or not verify_password(body.password or "", row["password_hash"]):
            # 用户不存在与口令错统一 401，不泄露昵称存在性；无口令存量行同理遭拒
            raise HTTPException(401, "昵称或口令不正确")
        # 登录即换发新会话凭据（踢掉旧设备会话：本地单人场景裁定接受）
        token = secrets.token_urlsafe(24)
        await s.execute(text("UPDATE actor SET token = :t, last_seen_at = now() WHERE id = :i"),
                        {"t": token, "i": row["id"]})
        await s.commit()
    _issue_session(response, token)
    return {"id": row["id"], "nickname": row["nickname"]}


@router.post("/logout")
async def logout(request: Request, response: Response):
    """登出：轮换 token 使旧凭据作废（而不只是删 cookie——Cookie 可能被复制）。"""
    actor = await identity.by_token(
        request.app.state.session_factory, request.cookies.get(ACTOR_COOKIE))
    if actor:
        async with request.app.state.session_factory() as s:
            await s.execute(text("UPDATE actor SET token = :t WHERE id = :i"),
                            {"t": secrets.token_urlsafe(24), "i": actor["id"]})
            await s.commit()
    response.delete_cookie(ACTOR_COOKIE)
    return {"ok": True}


@router.get("/me")
async def me(request: Request):
    actor = await identity.by_token(
        request.app.state.session_factory, request.cookies.get(ACTOR_COOKIE))
    if actor is None:
        raise HTTPException(401, "未登录")
    return {"id": actor["id"], "nickname": actor["nickname"],
            "is_founder": actor.get("is_founder", False)}
