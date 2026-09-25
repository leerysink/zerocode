"""技能 HTTP 端点（spec §5 / Ticket 001~003）：列表 + 双通道安装 + 启用开关 + 删除。

鉴权沿用全局 guard（/api/skills 非白名单即要求会话）；归属一律按 request.state.actor["id"]
过滤，PATCH/DELETE 带 owner 条件、越权返 404（不区分 403，沿用登录项目裁定）。
"""
import shutil

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.skills import installer
from app.skills.library import (SkillError, delete_skill_dir, next_free_slug,
                                slugify, write_skill_dir)

router = APIRouter(prefix="/api/skills")

_COLUMNS = ("id, slug, name, description, source_type, source_url, "
            "subpath, skill_dir, enabled, created_at, updated_at")


class InstallIn(BaseModel):
    url: str
    subpath: str | None = None  # 仅 GitHub 仓库通道有意义（monorepo 候选二次提交）


class PatchIn(BaseModel):
    enabled: bool


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
    transport = getattr(request.app.state, "skills_http_transport", None)
    try:
        if installer.parse_github_url(url) is not None:
            payload = await installer.fetch_skill_from_github(
                url, subpath=body.subpath, transport=transport)
        else:
            if (body.subpath or "").strip():
                raise HTTPException(400, "subpath 仅对 GitHub 仓库 URL 有意义")
            payload = await installer.fetch_skill_from_url(url, transport=transport)
    except SkillError as e:
        raise HTTPException(400, str(e))

    # monorepo 多命中：200 候选清单（spec §5），前端选后带 subpath 二次提交同端点
    if "candidates" in payload:
        return JSONResponse(status_code=200, content={"candidates": payload["candidates"]})

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
                 "st": payload["source_type"], "su": payload["source_url"],
                 "sp": payload["subpath"], "sd": str(dest)})).mappings().first()
            await s.commit()
    except IntegrityError:
        shutil.rmtree(dest, ignore_errors=True)  # 撞唯一约束（并发竞态）→ 清半成品目录
        raise HTTPException(409, "技能 slug 冲突，请重试")
    except Exception:
        # 长度已在 installer 按 DDL 预校验，其余 DB 异常也不能留永久孤儿母本（审查 Blocker #1）
        shutil.rmtree(dest, ignore_errors=True)
        raise
    return dict(row)


@router.patch("/{skill_id}")
async def patch_skill(request: Request, skill_id: int, body: PatchIn):
    """启用/停用翻转（spec §3 故事7）：owner 条件更新，越权/不存在一律 404。"""
    uid = request.state.actor["id"]
    async with request.app.state.session_factory() as s:
        row = (await s.execute(text(
            "UPDATE user_skill SET enabled=:e, updated_at=NOW() "
            "WHERE id=:i AND owner_user_id=:u "
            f"RETURNING {_COLUMNS}"),
            {"e": body.enabled, "i": skill_id, "u": uid})).mappings().first()
        await s.commit()
    if row is None:
        raise HTTPException(404, "技能不存在")
    return dict(row)


@router.delete("/{skill_id}")
async def delete_skill(request: Request, skill_id: int):
    """删除技能（spec §3 故事8）：先删磁盘母本成功、再删 DB 行；目录删不掉则不动行。

    顺序刻意反常规：行是目录的清单，先删行会造清单外的孤儿母本；残行+已删目录
    可由再次删除自愈（delete_skill_dir 不存在视为已删）。
    """
    uid = request.state.actor["id"]
    async with request.app.state.session_factory() as s:
        row = (await s.execute(text(
            "SELECT skill_dir FROM user_skill WHERE id=:i AND owner_user_id=:u"),
            {"i": skill_id, "u": uid})).first()
    if row is None:
        raise HTTPException(404, "技能不存在")
    try:
        delete_skill_dir(uid, row[0])
    except SkillError as e:
        raise HTTPException(400, str(e))
    async with request.app.state.session_factory() as s:
        await s.execute(text(
            "DELETE FROM user_skill WHERE id=:i AND owner_user_id=:u"),
            {"i": skill_id, "u": uid})
        await s.commit()
    return {"ok": True}
