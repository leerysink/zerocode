"""技能 HTTP 端点（spec §5 / Ticket 001）：列表 + 直链安装。

鉴权沿用全局 guard（/api/skills 非白名单即要求会话）；归属一律按 request.state.actor["id"]
过滤，越权语义（PATCH/DELETE）在 003 兑现。启用开关/删除/PATCH 后续票补。
"""
import shutil

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.skills import installer
from app.skills.library import (SkillError, next_free_slug, slugify,
                                write_skill_dir)

router = APIRouter(prefix="/api/skills")

_COLUMNS = ("id, slug, name, description, source_type, source_url, "
            "subpath, skill_dir, enabled, created_at, updated_at")


class InstallIn(BaseModel):
    url: str
    # subpath 入参随 002 的 GitHub 通道一并兑现，本票不自作主张接了丢弃（审查 #7）


@router.get("")
async def list_skills(request: Request):
    uid = request.state.actor["id"]
    async with request.app.state.session_factory() as s:
        rows = (await s.execute(text(
            f"SELECT {_COLUMNS} FROM user_skill WHERE owner_user_id=:u ORDER BY id"),
            {"u": uid})).mappings().all()
    return [dict(r) for r in rows]


@router.post("/install", status_code=201)
async def install(request: Request, body: InstallIn):
    uid = request.state.actor["id"]
    url = (body.url or "").strip()
    if not url:
        raise HTTPException(400, "请提供 url")
    # 001 只做直链；GitHub 仓库 URL 明确占位报错，002 兑现（避免被误当直链拒绝）
    if "github.com" in url and not installer.is_skill_md_url(url):
        raise HTTPException(400, "GitHub 仓库安装将在下一票支持，请暂用 SKILL.md 直链")
    transport = getattr(request.app.state, "skills_http_transport", None)
    try:
        payload = await installer.fetch_skill_from_url(url, transport=transport)
    except SkillError as e:
        raise HTTPException(400, str(e))

    # 逐用户解析空闲 slug（读与写分两个会话，避免磁盘 IO 期间挂着连接）
    async with request.app.state.session_factory() as s:
        taken = {r[0] for r in (await s.execute(text(
            "SELECT slug FROM user_skill WHERE owner_user_id=:u"), {"u": uid})).all()}
    slug = next_free_slug(taken, slugify(payload["name"]))
    try:
        dest = write_skill_dir(uid, slug, payload["files"])
    except SkillError as e:
        raise HTTPException(400, str(e))

    try:
        async with request.app.state.session_factory() as s:
            row = (await s.execute(text(
                "INSERT INTO user_skill (owner_user_id, slug, name, description, "
                "source_type, source_url, subpath, skill_dir) "
                "VALUES (:o,:sl,:n,:d,:st,:su,:sp,:sd) "
                f"RETURNING {_COLUMNS}"),
                {"o": uid, "sl": slug, "n": payload["name"], "d": payload["description"],
                 "st": "url", "su": payload["source_url"], "sp": payload["subpath"],
                 "sd": str(dest)})).mappings().first()
            await s.commit()
    except IntegrityError:
        shutil.rmtree(dest, ignore_errors=True)  # 撞唯一约束（并发竞态）→ 清半成品目录
        raise HTTPException(409, "技能 slug 冲突，请重试")
    except Exception:
        # 长度已在 installer 按 DDL 预校验，其余 DB 异常也不能留永久孤儿母本（审查 Blocker #1）
        shutil.rmtree(dest, ignore_errors=True)
        raise
    return dict(row)
