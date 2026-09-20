# app/api/apps.py
"""应用管理 REST（规格 01 §5 / 08 §4.4）。DeployError.status 直接映射 HTTP 状态码。

薄层：只做请求体解析 + 状态码映射，校验/创建/回滚等业务一律在 app.services.app_service。
"""
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from app.deploy.errors import DeployError
from app.deploy.slugs import suggest_slug, validate_slug
from app.services import app_service

router = APIRouter(prefix="/api/apps")


class AppIn(BaseModel):
    name: str
    slug: str
    scale: str
    stack: str


class SlugIn(BaseModel):
    slug: str


def _http(e: DeployError) -> HTTPException:
    return HTTPException(e.status, str(e))


@router.post("", status_code=201)   # brief Interfaces：创建成功返回 201（示例代码漏写，测试为契约）
async def create_app(request: Request, body: AppIn):
    try:
        return await app_service.create_app_project(
            request.app.state.session_factory, name=body.name, slug=body.slug,
            scale=body.scale, stack=body.stack)
    except DeployError as e:
        raise _http(e)


@router.post("/validate-slug")
async def validate(request: Request, body: SlugIn):
    try:
        validate_slug(body.slug)
    except DeployError:
        return {"ok": False, "suggest": suggest_slug(body.slug.replace(" ", "").lower(), set())}
    taken = {a["slug"] for a in await app_service.list_apps(request.app.state.session_factory)}
    if body.slug in taken:
        return {"ok": False, "suggest": suggest_slug(body.slug, taken)}
    return {"ok": True}


@router.get("")
async def list_all(request: Request):
    return await app_service.list_apps(request.app.state.session_factory)


@router.get("/{app_id}")
async def detail(request: Request, app_id: int):
    try:
        return await app_service.get_app(request.app.state.session_factory, app_id)
    except DeployError as e:
        raise _http(e)
