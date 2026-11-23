import asyncio

from fastapi import APIRouter, HTTPException, Request
from app.agent.llm import OpenAICompatLLM
from app.services import config_service

router = APIRouter(prefix="/api/config")

@router.get("")
async def get_config(request: Request):
    # 用户视图（Ticket 007）：平台级键打底 + 自己的 llm.* 覆盖，别人的 llm.* 永不外显
    uid = request.state.actor["id"]
    async with request.app.state.session_factory() as s:
        return config_service.mask(await config_service.get_all(s, owner_user_id=uid))

@router.put("")
async def put_config(request: Request, body: dict):
    """账号自己的配置写入。平台级键与 `quota.` 由 `set_many` 整批拒绝（ADR-0068）；
    管理员在这里改的仍是**他自己**的覆盖行——平台默认走 /api/admin/config。"""
    actor = request.state.actor
    uid = actor["id"]
    body = {k: v for k, v in body.items() if not (k == "llm.api_key" and v == "sk-***")}
    try:
        async with request.app.state.session_factory() as s:
            await config_service.set_many(s, body, owner_user_id=uid,
                                          actor_is_admin=bool(actor.get("is_founder")))
            await s.commit()
    except config_service.ConfigPermissionError as e:
        raise HTTPException(403, str(e))
    return {"ok": True}

@router.get("/meta")
async def config_meta():
    """哪些前缀是账号级可写的（ADR-0068）。

    前端设置页需要按键决定「可编辑 / 由管理员统一维护」，而保存是一条 PUT 全量 34 键的链——
    前端自己抄一份前缀清单迟早与后端分叉，分叉的代价是普通账号一点保存就整批 403。
    所以这里把真源交出去，前端只认这一份。
    """
    return {"user_prefixes": list(config_service.USER_PREFIXES)}


@router.get("/pg-status")
async def pg_status(request: Request):
    from sqlalchemy import text
    try:
        async with request.app.state.session_factory() as s:
            version = (await s.execute(text("SELECT version()"))).scalar()
        return {"ok": True, "version": version}
    except Exception as e:  # pragma: no cover
        return {"ok": False, "error": str(e)}

@router.post("/test-llm")
async def test_llm(body: dict):
    async def collect():
        parts = []
        llm = OpenAICompatLLM(body.get("base_url"), body.get("api_key"), body.get("model"))
        async for ev in llm.chat_stream(
                [{"role": "user", "content": "ping，请只回复 pong"}], tools=[]):
            if ev["type"] == "content_delta":
                parts.append(ev["content"])
            elif ev["type"] == "done" and ev["message"].get("content"):
                return "".join(parts) or ev["message"]["content"]
        return "".join(parts)
    try:
        reply = await asyncio.wait_for(collect(), timeout=10)
        return {"ok": True, "reply": reply}
    except Exception as e:
        return {"ok": False, "error": str(e)}
