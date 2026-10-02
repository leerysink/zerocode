"""技能 HTTP 端点（spec §5 / Ticket 001~003）：列表 + 双通道安装 + 启用开关 + 删除。

鉴权沿用全局 guard（/api/skills 非白名单即要求会话）；归属一律按 request.state.actor["id"]
过滤，PATCH/DELETE 带 owner 条件、越权返 404（不区分 403，沿用登录项目裁定）。
"""
import asyncio
import logging
import shutil

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.agent.model_resolver import resolve_model_for_user
from app.services import config_service
from app.skills import generator, installer
from app.skills.library import (NAME_MAX, SkillError, delete_skill_dir, next_free_slug,
                                parse_skill_md, slugify, write_skill_dir)

router = APIRouter(prefix="/api/skills")
logger = logging.getLogger(__name__)

_COLUMNS = ("id, slug, name, description, source_type, source_url, "
            "subpath, skill_dir, generation_prompt, enabled, created_at, updated_at")


class InstallIn(BaseModel):
    url: str
    subpath: str | None = None  # 仅 GitHub 仓库通道有意义（monorepo 候选二次提交）


class PatchIn(BaseModel):
    enabled: bool


class GenerateIn(BaseModel):
    prompt: str


async def _persist_skill(request: Request, uid, payload: dict, *,
                         generation_prompt: str | None = None) -> dict:
    """母本落盘 + 技能行落库：install（远端取回）与 adopt（AI 草稿）两通道共用。

    抽出来的理由：孤儿目录清理是 001~003 审查轮盯出来的（Blocker #1），并发撞
    (owner, slug) 唯一约束的 409 + rmtree 语义必须只存在一处，复制一份最容易漏。

    name 超 DDL 列宽在这里拦成 400：否则 asyncpg 抛 StringDataRightTruncation
    （不是 IntegrityError），会走 except Exception 清完目录再原样上抛，冒成 500。
    """
    if len(payload["name"]) > NAME_MAX:
        raise HTTPException(400, f"技能 name 超过 {NAME_MAX} 字符上限")

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
                "source_type, source_url, subpath, skill_dir, generation_prompt) "
                "VALUES (:o,:sl,:n,:d,:st,:su,:sp,:sd,:gp) "
                f"RETURNING {_COLUMNS}"),
                {"o": uid, "sl": slug, "n": payload["name"], "d": payload["description"],
                 "st": payload["source_type"], "su": payload["source_url"],
                 "sp": payload["subpath"], "sd": str(dest),
                 "gp": generation_prompt})).mappings().first()
            await s.commit()
    except IntegrityError:
        shutil.rmtree(dest, ignore_errors=True)  # 撞唯一约束（并发竞态）→ 清半成品目录
        raise HTTPException(409, "技能 slug 冲突，请重试")
    except Exception:
        # 长度已在前置守卫拦下，其余 DB 异常也不能留永久孤儿母本（审查 Blocker #1）
        shutil.rmtree(dest, ignore_errors=True)
        raise
    return dict(row)


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

    return await _persist_skill(request, uid, payload)


@router.post("/generate")
async def generate(request: Request, body: GenerateIn):
    """用一句话描述让账户默认模型写一份 SKILL.md 草稿（spec §2）。

    零副作用：不碰磁盘、不碰 DB——用户在草稿态关掉页面，技能库里什么都不会多出来。
    错误按责任方分码：输入/未配模型 400，上游超时·异常·空返 502（可重试）。
    """
    uid = request.state.actor["id"]
    prompt = (body.prompt or "").strip()
    if not prompt:
        raise HTTPException(400, "请描述你想要的技能")
    if len(prompt) > generator.PROMPT_MAX:
        raise HTTPException(400, f"描述超过 {generator.PROMPT_MAX} 字符上限")

    async with request.app.state.session_factory() as s:
        cfg = await config_service.get_all(s, owner_user_id=uid)
        model_cfg = await resolve_model_for_user(s, uid, cfg)
    if not model_cfg.get("base_url") or not model_cfg.get("model"):
        # 全局种子恒存在，这里空只可能是用户自己把 llm.* 清了（spec §7 边界7）
        raise HTTPException(400, "未配置模型，请先到设置页配置")

    try:
        # timeout 在这里显式传：默认参数在函数定义时就绑定好了，改
        # generator.GENERATE_TIMEOUT_S 不重跑调用点不会生效（测试调小超时就依赖这点）。
        draft = await generator.generate_skill_draft(
            generator.build_llm(model_cfg), prompt, timeout=generator.GENERATE_TIMEOUT_S)
    except asyncio.TimeoutError as e:
        raise HTTPException(502, f"生成超时（{generator.GENERATE_TIMEOUT_S} 秒），请重试") from e
    except Exception as e:
        logger.warning("技能 AI 生成失败 uid=%s: %s", uid, e)
        raise HTTPException(502, f"模型调用失败: {type(e).__name__}") from e
    if not draft["content"].strip():
        raise HTTPException(502, "模型返回了空内容，请重试")
    return draft


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
        # 删母本失败是数据不一致信号（磁盘占用/DATA_DIR 漂移），留服务端线索（复审 M-2）
        logger.warning("删除技能母本失败 id=%s uid=%s: %s", skill_id, uid, e)
        raise HTTPException(400, str(e))
    async with request.app.state.session_factory() as s:
        await s.execute(text(
            "DELETE FROM user_skill WHERE id=:i AND owner_user_id=:u"),
            {"i": skill_id, "u": uid})
        await s.commit()
    return {"ok": True}
