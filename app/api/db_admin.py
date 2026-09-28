# app/api/db_admin.py
"""应用 DB 操作台 REST（设计 2026-09-28-app-db-admin-design.md §4）。

与 apps_api/metrics_api 同前缀 /api/apps、独立模块（沿用 metrics 隔离纪律）。每个 /db/* 端点：
先 _load_app 归属过滤（非归属/未知 404）→ env 白名单（非法 400）→ 委派 service。读端点补一条 struct_view 审计。
"""
import time

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.deploy.errors import DeployError
from app.services import app_service, db_admin_service as svc

router = APIRouter(prefix="/api/apps")


class SqlIn(BaseModel):
    sql: str


async def _load_app(request: Request, app_id: int) -> dict:
    actor = request.state.actor
    owner = None if actor.get("is_founder") else actor["id"]
    try:
        return await app_service.get_app(request.app.state.session_factory, app_id,
                                         owner_user_id=owner)
    except DeployError as e:
        raise HTTPException(e.status, str(e))


@router.get("/{app_id}/db/audit")
async def db_audit(request: Request, app_id: int, env: str | None = None,
                   op: str | None = None, limit: int = 100):
    await _load_app(request, app_id)
    if env is not None:
        svc.validate_env(env)
    items = await svc.list_audit(request.app.state.session_factory, app_id,
                                 env=env, op=op, limit=limit)
    return {"items": items}


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


@router.get("/{app_id}/db/{env}/tables/{table}/data")
async def db_table_data(request: Request, app_id: int, env: str, table: str,
                        page: int = 1, size: int = 50, sort: str | None = None, dir: str = "asc"):
    app = await _load_app(request, app_id)
    try:
        svc.validate_env(env)
        t0 = time.monotonic()
        data = await svc.table_data(request, app["slug"], env, table,
                                    page=page, size=size, sort=sort, dirn=dir)
        await svc.log_operation(
            request.app.state.session_factory, app_id=app_id,
            actor_user_id=request.state.actor["id"], env=env, op_type="struct_view",
            sql_text="GET data", target_table=table, status="success",
            affected=len(data["rows"]), duration_ms=int((time.monotonic() - t0) * 1000))
        return data
    except DeployError as e:
        raise HTTPException(e.status, str(e))


@router.post("/{app_id}/db/{env}/sql")
async def db_sql_exec(request: Request, app_id: int, env: str, body: SqlIn):
    app = await _load_app(request, app_id)
    svc.validate_env(env)
    actor_id = request.state.actor["id"]
    sf = request.app.state.session_factory
    op = svc.classify_op(body.sql)
    t0 = time.monotonic()
    try:
        result = await svc.execute_sql(request, app["slug"], env, body.sql)
    except DeployError as e:
        raise HTTPException(e.status, str(e))
    except Exception as e:  # PG 报错原文回传 + 失败留痕
        await svc.log_operation(sf, app_id=app_id, actor_user_id=actor_id, env=env,
                                op_type=op, sql_text=body.sql, target_table=None,
                                status="error", error=str(e),
                                duration_ms=int((time.monotonic() - t0) * 1000))
        raise HTTPException(500, str(e))
    affected = result["affected"] if result["kind"] == "affected" else len(result["rows"])
    await svc.log_operation(sf, app_id=app_id, actor_user_id=actor_id, env=env,
                            op_type=op, sql_text=body.sql, target_table=None,
                            status="success", affected=affected,
                            duration_ms=int((time.monotonic() - t0) * 1000))
    return result
