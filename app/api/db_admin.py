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


class SqlBatchIn(BaseModel):
    statements: list[str]


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
    try:
        if env is not None:
            svc.validate_env(env)
        items = await svc.list_audit(request.app.state.session_factory, app_id,
                                     env=env, op=op, limit=limit)
    except DeployError as e:
        raise HTTPException(e.status, str(e))
    return {"items": items}


@router.get("/{app_id}/db/{env}/tables")
async def db_tables(request: Request, app_id: int, env: str):
    app = await _load_app(request, app_id)
    actor_id = request.state.actor["id"]
    sf = request.app.state.session_factory
    try:
        svc.validate_env(env)
        t0 = time.monotonic()
        tables = await svc.list_tables(request, app["slug"], env)
        await svc.log_operation(
            sf, app_id=app_id, actor_user_id=actor_id, env=env, op_type="struct_view",
            sql_text="GET tables", target_table=None, status="success",
            affected=len(tables), duration_ms=int((time.monotonic() - t0) * 1000))
        return {"tables": tables}
    except DeployError as e:
        raise HTTPException(e.status, str(e))
    except Exception as e:  # 连接/权限/PG 未起 → 先留 error 审计再 503
        await svc.log_operation(sf, app_id=app_id, actor_user_id=actor_id, env=env,
                                op_type="struct_view", sql_text="GET tables",
                                target_table=None, status="error", error=str(e))
        raise HTTPException(503, f"数据库连接失败，请检查 zerocode-pg16：{e}")


@router.get("/{app_id}/db/{env}/tables/{table}/schema")
async def db_table_schema(request: Request, app_id: int, env: str, table: str):
    app = await _load_app(request, app_id)
    actor_id = request.state.actor["id"]
    sf = request.app.state.session_factory
    try:
        svc.validate_env(env)
        t0 = time.monotonic()
        schema = await svc.table_schema(request, app["slug"], env, table)
        await svc.log_operation(
            sf, app_id=app_id, actor_user_id=actor_id, env=env, op_type="struct_view",
            sql_text="GET schema", target_table=table, status="success",
            duration_ms=int((time.monotonic() - t0) * 1000))
        return schema
    except DeployError as e:
        raise HTTPException(e.status, str(e))
    except Exception as e:
        await svc.log_operation(sf, app_id=app_id, actor_user_id=actor_id, env=env,
                                op_type="struct_view", sql_text="GET schema",
                                target_table=table, status="error", error=str(e))
        raise HTTPException(503, f"数据库连接失败，请检查 zerocode-pg16：{e}")


@router.get("/{app_id}/db/{env}/tables/{table}/data")
async def db_table_data(request: Request, app_id: int, env: str, table: str,
                        page: int = 1, size: int = 50, sort: str | None = None, dir: str = "asc"):
    app = await _load_app(request, app_id)
    actor_id = request.state.actor["id"]
    sf = request.app.state.session_factory
    try:
        svc.validate_env(env)
        t0 = time.monotonic()
        data = await svc.table_data(request, app["slug"], env, table,
                                    page=page, size=size, sort=sort, dirn=dir)
        await svc.log_operation(
            sf, app_id=app_id, actor_user_id=actor_id, env=env, op_type="struct_view",
            sql_text="GET data", target_table=table, status="success",
            affected=len(data["rows"]), duration_ms=int((time.monotonic() - t0) * 1000))
        return data
    except DeployError as e:
        raise HTTPException(e.status, str(e))
    except Exception as e:
        await svc.log_operation(sf, app_id=app_id, actor_user_id=actor_id, env=env,
                                op_type="struct_view", sql_text="GET data",
                                target_table=table, status="error", error=str(e))
        raise HTTPException(503, f"数据库连接失败，请检查 zerocode-pg16：{e}")


@router.post("/{app_id}/db/{env}/sql")
async def db_sql_exec(request: Request, app_id: int, env: str, body: SqlIn):
    app = await _load_app(request, app_id)
    actor_id = request.state.actor["id"]
    sf = request.app.state.session_factory
    try:
        svc.validate_env(env)
    except DeployError as e:
        raise HTTPException(e.status, str(e))
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


@router.post("/{app_id}/db/{env}/sql/batch")
async def db_sql_batch(request: Request, app_id: int, env: str, body: SqlBatchIn):
    """批量执行：一个框里的多条语句在同一连接内按序跑，**逐条**写审计（一次批量 ≠ 一条留痕）。

    语句顺序即执行顺序；results[i].index 显式带回原数组下标（空白/纯注释段不进结果集），
    前端结果块不靠数组长度猜。

    状态码分界：connect() 之前/之外的失败走 HTTP（400 非法 env / 400 无可执行语句 /
    404 非归属 / 409 凭据缺失 / 503 建连失败）；connect() 之内逐条执行的失败走结果项、
    整体 200——否则拿不到前 i-1 条的结果。单条 /sql 一律 500 的旧行为故意保持不变。"""
    app = await _load_app(request, app_id)
    actor_id = request.state.actor["id"]
    sf = request.app.state.session_factory
    try:
        svc.validate_env(env)
    except DeployError as e:
        raise HTTPException(e.status, str(e))
    # 全空白/纯注释（含空数组）在建连前就拒：不把注释当语句送 PG，也不留痕。
    if not any(svc.has_executable_content(s) for s in body.statements):
        raise HTTPException(400, "没有可执行的语句")
    try:
        results = await svc.execute_batch(request, app["slug"], env, body.statements)
    except DeployError as e:
        raise HTTPException(e.status, str(e))
    except Exception as e:
        # 建连阶段就失败：实际执行了 0 条，没有逐条结果可展示，因此也不写审计。
        raise HTTPException(503, f"数据库连接失败，请检查 zerocode-pg16：{e}")
    for it in results:
        if it["status"] == "skipped":
            continue  # 没执行的事不留痕：记 success 或 error 都是凭空捏造一条执行记录
        ok = it["status"] == "ok"
        await svc.log_operation(
            sf, app_id=app_id, actor_user_id=actor_id, env=env,
            op_type=it["op"], sql_text=it["sql"], target_table=None,
            status="success" if ok else "error",
            affected=(it["affected"] if it["kind"] == "affected" else len(it["rows"])) if ok else None,
            error=None if ok else it["error"], duration_ms=it["duration_ms"])
    return {"results": results}
