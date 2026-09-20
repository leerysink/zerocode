# app/api/apps.py
"""应用管理 REST（规格 01 §5 / 08 §4.4）。DeployError.status 直接映射 HTTP 状态码。

薄层：只做请求体解析 + 状态码映射，校验/创建/回滚等业务一律在 app.services.app_service。
Task 12 追加 dev 生命周期与 sqlchange 端点：长任务（起栈/迁移）一律 **202 受理 + 后台执行**，
进度经 DeployHub → /ws/deploy/{app_id} 下发（规格 03 §4.4）；同应用同时只允许一个部署任务。
"""
import asyncio
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from app import config as app_config
from app.deploy import engine, sqlrun
from app.deploy.errors import DeployError
from app.deploy.provision import read_cred
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


# ---------- dev 生命周期（Task 12 / 规格 03 §4.4）：202 受理 + 后台执行，进度走 WS ----------

def _spawn(request: Request, app_id: int, coro_factory) -> None:
    """长任务句柄表：同一应用同时只允许一个部署任务（重复受理 → 409，而不是排队双跑）。"""
    tasks: dict[int, asyncio.Task] = request.app.state.dev_tasks
    old = tasks.get(app_id)
    if old and not old.done():
        raise DeployError("该应用有部署任务进行中，请稍候", status=409)
    tasks[app_id] = asyncio.create_task(coro_factory())


async def _dev_task(request: Request, app_id: int, action) -> dict:
    """dev 端点共用一条链：取应用 → 起后台任务（引擎自己发 starting/running/error 事件）→ 202。

    `action` 在**请求时**按模块属性取出（engine.start_dev 等），monkeypatch 才拦得住。
    """
    sf = request.app.state.session_factory
    hub = request.app.state.deploy_hub
    try:
        app = await app_service.get_app(sf, app_id)
        _spawn(request, app_id, lambda: action(sf, app, notify=hub.publish))
    except DeployError as e:
        raise _http(e)
    return {"ok": True}


@router.post("/{app_id}/dev/start", status_code=202)
async def dev_start(request: Request, app_id: int):
    return await _dev_task(request, app_id, engine.start_dev)


@router.post("/{app_id}/dev/stop", status_code=202)
async def dev_stop(request: Request, app_id: int):
    return await _dev_task(request, app_id, engine.stop_dev)


@router.post("/{app_id}/dev/restart", status_code=202)
async def dev_restart(request: Request, app_id: int):
    return await _dev_task(request, app_id, engine.restart_dev)


@router.get("/{app_id}/dev/status")
async def dev_status(request: Request, app_id: int):
    sf = request.app.state.session_factory
    try:
        app = await app_service.get_app(sf, app_id)
        return await engine.dev_status(sf, app)
    except DeployError as e:
        raise _http(e)


# ---------- sqlchange（Task 12 / 规格 07）：应用侧迁移的执行、记账与待办清单 ----------

def _app_db_url(cred: dict) -> str:
    """应用库连接串：复用平台 PG 实例的 host/port（生产由 ZEROCODE_DATABASE_URL 决定，不写死 5433），
    只换库名/角色/密码。SQLAlchemy 默认掩码密码，取串必须 render_as_string(hide_password=False)。"""
    base = make_url(app_config.database_url())
    return base.set(database=cred["db"], username=cred["user"],
                    password=cred["password"]).render_as_string(hide_password=False)


async def _history_rows(url: str, sql: str) -> list[dict]:
    """读应用库的 sqlchange_history；库没供给/历史表没建（应用刚创建还没迁移过）→ 空列表而非 500。"""
    eng = create_async_engine(url)
    try:
        async with eng.connect() as c:
            return [dict(r) for r in (await c.execute(text(sql))).mappings().all()]
    except Exception:
        return []
    finally:
        await eng.dispose()


async def _run_sqlchange(sf, app: dict, hub) -> None:
    """后台迁移任务：受理时已经回了 202，所以任何失败都必须以 migrate_result 事件收口，
    抛出去只会被 asyncio 吞成「Task exception was never retrieved」，前端永远停在「迁移中」。"""
    app_id = app["id"]

    def on_log(line: str) -> None:
        hub.publish(app_id, {"app_id": app_id, "type": "dev_log", "line": line})

    try:
        cred = await read_cred(sf, app["slug"], "dev")
        if not cred:
            raise DeployError("dev 数据库凭据缺失，无法执行 sqlchange 迁移", status=409)
        root = await engine._app_root(sf, app)     # 引擎内部助手：应用页读工作区根的唯一复用点
        res = await sqlrun.run_sqlchange(root, "dev", cred, on_log=on_log)
    except Exception as e:
        res = {"ok": False, "code": -1, "log": str(e)}
    hub.publish(app_id, {"app_id": app_id, "type": "migrate_result", "env": "dev", **res})


@router.post("/{app_id}/sqlchange/apply", status_code=202)
async def sql_apply(request: Request, app_id: int):
    sf = request.app.state.session_factory
    hub = request.app.state.deploy_hub
    try:
        app = await app_service.get_app(sf, app_id)
        _spawn(request, app_id, lambda: _run_sqlchange(sf, app, hub))
    except DeployError as e:
        raise _http(e)
    return {"ok": True}


@router.get("/{app_id}/sqlchange/records")
async def sql_records(request: Request, app_id: int, env: str = "dev"):
    if env not in ("dev", "prod"):
        raise HTTPException(400, "env 仅支持 dev/prod")
    sf = request.app.state.session_factory
    try:
        app = await app_service.get_app(sf, app_id)
    except DeployError as e:
        raise _http(e)
    cred = await read_cred(sf, app["slug"], env)
    if not cred:
        return []
    rows = await _history_rows(
        _app_db_url(cred),
        "SELECT filename, success, error, duration_ms, applied_at "
        "FROM sqlchange_history ORDER BY applied_at DESC LIMIT 200")
    for r in rows:
        if r.get("applied_at") is not None:
            r["applied_at"] = r["applied_at"].isoformat()
    return rows


@router.get("/{app_id}/sqlchange/files")
async def sql_files(request: Request, app_id: int):
    """待执行清单：文件名升序（= 执行顺序），并标注 dev 库里已记账的那些。"""
    sf = request.app.state.session_factory
    try:
        app = await app_service.get_app(sf, app_id)
        root = await engine._app_root(sf, app)
    except DeployError as e:
        raise _http(e)
    d = root / "sqlchange"
    files = sorted(p.name for p in d.glob("V*.sql")) if d.is_dir() else []
    cred = await read_cred(sf, app["slug"], "dev")
    applied: dict[str, dict] = {}
    if cred:
        applied = {r["filename"]: r for r in await _history_rows(
            _app_db_url(cred), "SELECT filename, success FROM sqlchange_history")}
    return [{"filename": f, "applied": f in applied, "success": applied.get(f, {}).get("success")}
            for f in files]
