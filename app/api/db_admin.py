# app/api/db_admin.py
"""应用 DB 操作台 REST（设计 2026-09-28-app-db-admin-design.md §4）。

与 apps_api/metrics_api 同前缀 /api/apps、独立模块（沿用 metrics 隔离纪律）。每个 /db/* 端点：
先 _load_app 归属过滤（非归属/未知 404）→ env 白名单（非法 400）→ 委派 service。读端点补一条 struct_view 审计。
"""
import time

from fastapi import APIRouter, HTTPException, Request

from app.deploy.errors import DeployError
from app.services import app_service, db_admin_service as svc

router = APIRouter(prefix="/api/apps")


async def _load_app(request: Request, app_id: int) -> dict:
    actor = request.state.actor
    owner = None if actor.get("is_founder") else actor["id"]
    try:
        return await app_service.get_app(request.app.state.session_factory, app_id,
                                         owner_user_id=owner)
    except DeployError as e:
        raise HTTPException(e.status, str(e))


@router.get("/{app_id}/db/{env}/tables")
async def db_tables(request: Request, app_id: int, env: str):
    app = await _load_app(request, app_id)
    try:
        svc.validate_env(env)
        t0 = time.monotonic()
        tables = await svc.list_tables(request, app["slug"], env)
        await svc.log_operation(
            request.app.state.session_factory, app_id=app_id,
            actor_user_id=request.state.actor["id"], env=env, op_type="struct_view",
            sql_text="GET tables", target_table=None, status="success",
            affected=len(tables), duration_ms=int((time.monotonic() - t0) * 1000))
        return {"tables": tables}
    except DeployError as e:
        raise HTTPException(e.status, str(e))


@router.get("/{app_id}/db/{env}/tables/{table}/schema")
async def db_table_schema(request: Request, app_id: int, env: str, table: str):
    app = await _load_app(request, app_id)
    try:
        svc.validate_env(env)
        t0 = time.monotonic()
        schema = await svc.table_schema(request, app["slug"], env, table)
        await svc.log_operation(
            request.app.state.session_factory, app_id=app_id,
            actor_user_id=request.state.actor["id"], env=env, op_type="struct_view",
            sql_text="GET schema", target_table=table, status="success",
            duration_ms=int((time.monotonic() - t0) * 1000))
        return schema
    except DeployError as e:
        raise HTTPException(e.status, str(e))
