# app/api/apps.py
"""应用管理 REST（规格 01 §5 / 08 §4.4）。DeployError.status 直接映射 HTTP 状态码。

薄层：只做请求体解析 + 状态码映射，校验/创建/回滚等业务一律在 app.services.app_service。
Task 12 追加 dev 生命周期与 sqlchange 端点：长任务（起栈/迁移）一律 **202 受理 + 后台执行**，
进度经 DeployHub → /ws/deploy/{app_id} 下发（规格 03 §4.4）；同应用同时只允许一个部署任务。
预览启动（start/restart）在受理前**同步**预检 Docker：Task 11 的契约是「预检异常零事件、原样上抛
供 REST 映射 503」，异步化会让这个映射点消失——用户 Docker 没开就会拿到 202 + WS 全程静默（审查 I-1）。
"""
import asyncio
import logging
from functools import partial

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

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/apps")


class AppIn(BaseModel):
    name: str
    slug: str
    scale: str
    stack: str


class SlugIn(BaseModel):
    slug: str


class AppRenameIn(BaseModel):
    name: str


def _http(e: DeployError) -> HTTPException:
    return HTTPException(e.status, str(e))


def _reject_if_task_running(request: Request, app_id: int) -> None:
    """与 `_spawn` 同一条「同应用同时只允许一个部署任务」的契约，用在删除入口上。

    删记录时后台起栈/迁移任务还在跑，会留下一只没人认领的容器（行没了，状态回执无处可写），
    所以直接 409 让用户等一等，而不是在这里发明一套取消协议。
    """
    task: asyncio.Task | None = getattr(request.app.state, "dev_tasks", {}).get(app_id)
    if task and not task.done():
        raise DeployError("该应用有部署任务进行中，请稍候", status=409)


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


@router.patch("/{app_id}")
async def rename(request: Request, app_id: int, body: AppRenameIn):
    """应用改名（只改显示名）：slug 不可改——它是工程目录名、库名与镜像名的共同锚点。"""
    try:
        return await app_service.rename_app(request.app.state.session_factory, app_id, body.name)
    except DeployError as e:
        raise _http(e)


@router.delete("/{app_id}")
async def remove(request: Request, app_id: int):
    """删除应用（侧栏「⋯ → 删除应用」的落点）：尽力停 dev 栈 + 删平台记录。

    M1 裁定：磁盘工程目录与应用数据库**保留**，响应把保留位置回给前端写进确认文案（kept）。
    停栈按「尽力而为」处理——Docker 没开或 compose 失败都只记 warning，不把应用永久钉在列表里：
    记录删不掉才是用户真正没法自救的状态。发布中 / 有部署任务在跑则 409 拒删（规格 08 §5.4）。
    """
    sf = request.app.state.session_factory
    try:
        app = await app_service.get_app(sf, app_id)
        app_service.ensure_deletable(app)
        _reject_if_task_running(request, app_id)
        if app["status_dev"] in app_service.STOPPABLE:
            try:
                await engine.stop_dev(sf, app, notify=request.app.state.deploy_hub.publish)
            except Exception as e:
                logger.warning("删除应用时停止 dev 栈失败（继续删记录）: app_id=%s %s", app_id, e)
        snap = await app_service.delete_app(sf, app_id)
    except DeployError as e:
        raise _http(e)
    return {"ok": True, "kept": {"root_path": snap["root_path"], "slug": snap["slug"],
                                 "databases": snap["databases"]}}


# ---------- dev 生命周期（Task 12 / 规格 03 §4.4）：202 受理 + 后台执行，进度走 WS ----------

def _log_task_crash(app_id: int, task: asyncio.Task) -> None:
    """`_spawn` 的 done 回调：只记真抛了异常的任务，取消与正常返回（exception() is None）一律不动。

    先 `task.cancelled()` 再取 `exception()`：对已取消的任务调 `exception()` 会抛 CancelledError，
    在回调里再抛一次只会给日志添乱。
    """
    if task.cancelled():
        return
    exc = task.exception()
    if exc is None:
        return
    logger.exception("部署后台任务异常退出（app_id=%s）", app_id, exc_info=exc)


def _spawn(request: Request, app_id: int, coro_factory) -> None:
    """长任务句柄表：同一应用同时只允许一个部署任务（重复受理 → 409，而不是排队双跑）。

    202 之后任务异常没有 HTTP 出口，done 回调负责 retrieve + 记堆栈：既消掉 asyncio 的
    「Task exception was never retrieved」噪音（审查 I-1 配套 / Minor-3），也给「引擎事件之后才失败」
    的路子（如 CLI 缺失时的后台 stop）留下一条可读现场。
    """
    tasks: dict[int, asyncio.Task] = request.app.state.dev_tasks
    old = tasks.get(app_id)
    if old and not old.done():
        raise DeployError("该应用有部署任务进行中，请稍候", status=409)
    task = asyncio.create_task(coro_factory())
    task.add_done_callback(partial(_log_task_crash, app_id))
    tasks[app_id] = task


async def _dev_task(request: Request, app_id: int, action, *, docker_gate: bool = False) -> dict:
    """dev 端点共用一条链：取应用 →（启动类）同步 Docker 预检 → 起后台任务 → 202。

    `action` 在**请求时**按模块属性取出（engine.start_dev 等），monkeypatch 才拦得住。

    `docker_gate`（start/restart 开）：Task 11 的预检契约是「零事件、异常原样上抛**供 REST 映射 503**」，
    这个映射点只在同步调用时存在——放进 create_task 后用户只会看到 202 + WS 静默（审查 I-1，探针实证）。
    stop 关闸门：Docker 缺失时 `compose down` 本就无意义，更不该挡住用户关栈的意图
    （失败交后台 done 回调留痕 + 平台启动清理兜底）。
    """
    sf = request.app.state.session_factory
    hub = request.app.state.deploy_hub
    try:
        app = await app_service.get_app(sf, app_id)
        if docker_gate:
            await engine.preflight_docker()
        _spawn(request, app_id, lambda: action(sf, app, notify=hub.publish))
    except DeployError as e:
        raise _http(e)
    return {"ok": True}


@router.post("/{app_id}/dev/start", status_code=202)
async def dev_start(request: Request, app_id: int):
    return await _dev_task(request, app_id, engine.start_dev, docker_gate=True)


@router.post("/{app_id}/dev/stop", status_code=202)
async def dev_stop(request: Request, app_id: int):
    return await _dev_task(request, app_id, engine.stop_dev)


@router.post("/{app_id}/dev/restart", status_code=202)
async def dev_restart(request: Request, app_id: int):
    return await _dev_task(request, app_id, engine.restart_dev, docker_gate=True)


@router.get("/{app_id}/dev/status")
async def dev_status(request: Request, app_id: int):
    sf = request.app.state.session_factory
    try:
        app = await app_service.get_app(sf, app_id)
        return await engine.dev_status(sf, app)
    except DeployError as e:
        raise _http(e)


@router.get("/{app_id}/urls")
async def app_urls(request: Request, app_id: int):
    """应用访问地址（规格 05 §5）：本机 + 局域网 + 网卡候选，无副作用只读。"""
    sf = request.app.state.session_factory
    try:
        app = await app_service.get_app(sf, app_id)
        from app.deploy import nics
        return await nics.app_urls(sf, app)
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
