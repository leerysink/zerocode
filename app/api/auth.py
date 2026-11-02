# app/api/auth.py
"""登录体系端点（Ticket 002 / ADR-0003）：register/login/logout/me。

actor 即登录主体：cookie `zc_actor` 是会话凭据，一次登录一条会话（identity.open_session）；
口令 bcrypt 落库。同一账号允许多人同时在线，上限 actor.max_sessions（默认 10），坐满时挤掉
最久未活动的那条；登出只作废当前会话。
"""

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
    try:
        async with request.app.state.session_factory() as s:
            # 创始人判定与承接同事务（Ticket 004 / ADR-0003）：首账号把无主存量划归自己，
            # 中途失败不留半划归。本地单实例并发注册撞车概率极低，不另加锁。
            is_first = (await s.execute(text("SELECT NOT EXISTS (SELECT 1 FROM actor)"))).scalar()
            row = (await s.execute(text(
                "INSERT INTO actor (nickname, password_hash, is_founder) "
                "VALUES (:n, :p, :f) RETURNING id, nickname, is_founder"),
                {"n": nickname, "p": hash_password(password),
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
    _issue_session(response, await identity.open_session(
        request.app.state.session_factory, row["id"]))
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
        actor_id = row["id"]
    # 每次登录新增一条会话，其他人照常在线（上限见 identity.open_session）
    _issue_session(response, await identity.open_session(
        request.app.state.session_factory, actor_id))
    return {"id": actor_id, "nickname": row["nickname"]}


@router.post("/logout")
async def logout(request: Request, response: Response):
    """登出：删掉当前这一条会话（而不只是清 cookie——Cookie 可能被复制）。"""
    actor = await identity.by_token(
        request.app.state.session_factory, request.cookies.get(ACTOR_COOKIE))
    if actor:
        await identity.close_session(request.app.state.session_factory, actor["token"])
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
