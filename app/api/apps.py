# app/api/apps.py
"""应用管理 REST（规格 01 §5 / 08 §4.4）。DeployError.status 直接映射 HTTP 状态码。

薄层：只做请求体解析 + 状态码映射，校验/创建/回滚等业务一律在 app.services.app_service。
Task 12 追加 dev 生命周期与 sqlchange 端点：长任务（起栈/迁移）一律 **202 受理 + 后台执行**，
进度经 DeployHub → /ws/deploy/{app_id} 下发（规格 03 §4.4）；同应用同时只允许一个部署任务。
预览启动（start/restart）在受理前**同步**预检 Docker：Task 11 的契约是「预检异常零事件、原样上抛
供 REST 映射 503」，异步化会让这个映射点消失——用户 Docker 没开就会拿到 202 + WS 全程静默（审查 I-1）。
Task 8 追加发布/回滚/prod 生命周期/发布历史端点（规格 04 §5）：与 dev 同一条 `_spawn` 单任务契约；
chat 闸门（FR4-09）的 409 detail 是**字典** `{code, message}`——前端要按 code 分支弹确认，不能只吃字符串。
"""
import asyncio
import logging
import re
from functools import partial

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from app.deploy import engine, prod, records, sqlrun
from app.deploy import provision
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


class PublishIn(BaseModel):
    force: bool = False
    confirmDestructive: bool = False


class RollbackIn(BaseModel):
    version: str


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


async def _dev_task(request: Request, app_id: int, action, *, docker_gate: bool = False,
                    env: str = "dev") -> dict:
    """生命周期端点共用一条链：取应用 →（启动类）同步 Docker 预检 → 起后台任务 → 202。

    `action` 在**请求时**按模块属性取出（engine.start_dev 等），monkeypatch 才拦得住。

    `docker_gate`（start/restart 开）：Task 11 的预检契约是「零事件、异常原样上抛**供 REST 映射 503**」，
    这个映射点只在同步调用时存在——放进 create_task 后用户只会看到 202 + WS 静默（审查 I-1，探针实证）。
    stop 关闸门：Docker 缺失时 `compose down` 本就无意义，更不该挡住用户关栈的意图
    （失败交后台 done 回调留痕 + 平台启动清理兜底）。

    `env`（Task 8）：prod 端点复用同一条链，但预检走 `prod.preflight_docker`——prod.py 的 D-1
    模块级名字纪律让测试替身拦得住这个属性；dev 行为（默认值）不动。

    终审 I-2：prod 启动类动作（start/restart，即 docker_gate 开的那批）加 publish 同款状态二次闸——
    `_spawn` 只挡本进程，`publishing` 可能是另一进程在跑/遗留，此时插一次 up 会互踩 current_version
    与容器；stop 不挡（停栈是逃生通道，发布卡死时用户总关得掉）。409 立答于 _spawn 之前，零事件。
    """
    sf = request.app.state.session_factory
    hub = request.app.state.deploy_hub
    try:
        app = await app_service.get_app(sf, app_id)
        if docker_gate and env == "prod" and app["status_prod"] == "publishing":
            raise DeployError("发布/回滚进行中，请等待完成", status=409)
        if docker_gate:
            if env == "prod":
                await prod.preflight_docker()
            else:
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

# 应用库连接串口径收口到锚点服务（Task 5）：两处各自维护一份早晚漂移。
from app.store.anchor import app_db_url as _app_db_url


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


async def _reset_dev(sf, app: dict, hub) -> None:
    """重建 dev 库：drop → 重新 provision → 从 sqlchange 文件全量 fold。
    用于消除 dev 漂移，使其重新等于 fold(文件)；失败以 dev_reset_result 事件收口。"""
    app_id = app["id"]
    slug = app["slug"]

    def on_log(line: str) -> None:
        hub.publish(app_id, {"app_id": app_id, "type": "dev_log", "line": line})

    try:
        await provision.drop_env(sf, slug, "dev")
        cred = await provision.provision_env(sf, slug, "dev")
        root = await engine._app_root(sf, app)
        res = await sqlrun.run_sqlchange(root, "dev", cred, on_log=on_log)
        ok = res["ok"]
    except Exception as e:
        ok, res = False, {"code": -1, "log": str(e)}
    hub.publish(app_id, {"app_id": app_id, "type": "dev_reset_result", "env": "dev", "ok": ok, **res})


@router.post("/{app_id}/dev/reset", status_code=202)
async def dev_reset(request: Request, app_id: int):
    """一键重建 dev 库（drop+provision+fold sqlchange）：202 受理 + 后台执行，进度走 dev_log/dev_reset_result。"""
    sf = request.app.state.session_factory
    hub = request.app.state.deploy_hub
    try:
        app = await app_service.get_app(sf, app_id)
        _spawn(request, app_id, lambda: _reset_dev(sf, app, hub))
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
    """待执行清单：文件名升序（= 执行顺序），并标注 dev 库里已记账的那些。

    P2 双轨：glob 由 `V*.sql` 放宽为 `*.sql`，让数据轨 `D*.sql`（与遗留 `V*.sql`）一并入待办清单；
    口径只影响本 UI 列表——商店上架锚点取自 sqlchange_history（DB），与本目录 glob 无关（不伤打包）。"""
    sf = request.app.state.session_factory
    try:
        app = await app_service.get_app(sf, app_id)
        root = await engine._app_root(sf, app)
    except DeployError as e:
        raise _http(e)
    d = root / "sqlchange"
    files = sorted(p.name for p in d.glob("*.sql")) if d.is_dir() else []
    cred = await read_cred(sf, app["slug"], "dev")
    applied: dict[str, dict] = {}
    if cred:
        applied = {r["filename"]: r for r in await _history_rows(
            _app_db_url(cred), "SELECT filename, success FROM sqlchange_history")}
    return [{"filename": f, "applied": f in applied, "success": applied.get(f, {}).get("success")}
            for f in files]


@router.post("/{app_id}/sqlchange/scaffold-backfill")
async def scaffold_backfill(request: Request, app_id: int):
    """破坏性结构变更的数据轨回填骨架生成（同步）：在 sqlchange/ 新增 D<日期>_<序号>_backfill.sql。

    与 store 打包无冲突：store 上架锚点取自 sqlchange_history（DB），未发布应用的半途 D 文件不进包；
    文件名 D<8位日期>_<2位序号>_backfill.sql 匹配 prod._SQL_NAME（[VD] 轨均合法），preflight 命名闸门放行。
    只新增绝不覆盖：序号取当日 D<today>_* 现有最大 +1。
    """
    from datetime import datetime
    from app.deploy import schemadiff
    sf = request.app.state.session_factory
    try:
        app = await app_service.get_app(sf, app_id)
        root = await engine._app_root(sf, app)
    except DeployError as e:
        raise _http(e)
    d = prod.compute_schema_diff(root, app.get("current_version"))
    if d is None:
        raise HTTPException(400, "无上一版结构基线可比对（未发布或缺 schema 快照），无法生成回填骨架")
    if not schemadiff.destructive_changes(d):
        raise HTTPException(400, "未检出破坏性变更，无需回填")
    content = schemadiff.backfill_skeleton(d, today=datetime.now().strftime("%Y%m%d"))
    scdir = root / "sqlchange"
    scdir.mkdir(exist_ok=True)
    today = datetime.now().strftime("%Y%m%d")
    prefix = f"D{today}_"
    seqs = [int(p.name[len(prefix):len(prefix) + 2]) for p in scdir.glob(prefix + "*.sql")
            if p.name[len(prefix):len(prefix) + 2].isdigit()]
    next_seq = (max(seqs) + 1) if seqs else 1
    if next_seq > 99:   # 两位序号上限：越界会产 3 位非法名、反被 preflight 命名闸门拦停
        raise HTTPException(409, f"当日回填脚手架已达上限（99），请次日再试或清理 {today} 的 D*.sql")
    nn = f"{next_seq:02d}"
    filename = f"{prefix}{nn}_backfill.sql"
    (scdir / filename).write_text(content, encoding="utf-8", newline="")
    return {"filename": filename, "content": content}

# ---------- 发布与 prod（Task 8 / 规格 04 §5）：202 受理 + 后台执行，进度走 publish_step 事件 ----------

# 版本号 `b<本地 YYYYMMDDHHmmss>`（04 §4.5）：脏输入在闸门处立答 400，不打扰 Docker 也不留任务
_VERSION_RE = re.compile(r"^b\d{14}$")


@router.post("/{app_id}/publish", status_code=202)
async def publish(request: Request, app_id: int, body: PublishIn):
    """一键发布（04 §5 / FR4-09 chat 闸门 + publishing 并发双保险）。

    闸门顺序：任务互斥（`_spawn` 契约）→ 状态并发闸 → chat 确认 → 同步预检 → 受理。
    chat 闸门只看**本应用工作区**的活跃会话（别的应用在改代码不该挡住这次发布）；
    409 detail 是字典 `{code, message}`——前端按 `code === 'chat_active'` 弹强制确认，
    不能只吃字符串（`_http` 的 str detail 通道给不了这个分支能力）。
    """
    sf = request.app.state.session_factory
    hub = request.app.state.deploy_hub
    try:
        app = await app_service.get_app(sf, app_id)
        _reject_if_task_running(request, app_id)
        if app["status_prod"] == "publishing":
            # _spawn 只挡本进程在跑的任务；publishing 可能是上一进程遗留，按状态再挡一道
            raise DeployError("发布进行中，请等待完成", status=409)
        if not body.force:
            active = list(request.app.state.hub.active_session_ids())
            if active:
                async with sf() as s:
                    hit = (await s.execute(text(
                        "SELECT 1 FROM chat_session WHERE workspace_id=:w AND id = ANY(:ids) LIMIT 1"),
                        {"w": app["workspace_id"], "ids": active})).first()
                if hit:
                    raise HTTPException(409, {
                        "code": "chat_active",
                        "message": "当前 AI 正在修改代码，发布的可能是不完整版本，是否继续？"})
        await prod.preflight_docker()
        _spawn(request, app_id, lambda: prod.publish(sf, app, notify=hub.publish,
                                                     force=body.force,
                                                     confirm_destructive=body.confirmDestructive))
    except DeployError as e:
        raise _http(e)
    return {"ok": True}


@router.post("/{app_id}/rollback", status_code=202)
async def rollback(request: Request, app_id: int, body: RollbackIn):
    """一键回滚（FR4-04）：只切程序，数据库永不回滚（07/D8）；镜像缺位由引擎在后台经 publish_done 事件收口。"""
    if not _VERSION_RE.match(body.version or ""):
        raise HTTPException(400, "版本号格式非法（应为 b<14位时间戳>）")
    sf = request.app.state.session_factory
    hub = request.app.state.deploy_hub
    try:
        app = await app_service.get_app(sf, app_id)
        _reject_if_task_running(request, app_id)
        if app["status_prod"] == "publishing":
            # 终审 I-2：与 publish 端点同款二次闸——_spawn 只挡本进程，publishing 可能是
            # 另一进程在跑/遗留，回滚插队会与在跑的发布互踩 current_version 与容器
            raise DeployError("发布/回滚进行中，请等待完成", status=409)
        await prod.preflight_docker()
        _spawn(request, app_id, lambda: prod.rollback(sf, app, body.version, notify=hub.publish))
    except DeployError as e:
        raise _http(e)
    return {"ok": True}


@router.post("/{app_id}/prod/start", status_code=202)
async def prod_start(request: Request, app_id: int):
    return await _dev_task(request, app_id, prod.prod_start, docker_gate=True, env="prod")


@router.post("/{app_id}/prod/stop", status_code=202)
async def prod_stop(request: Request, app_id: int):
    return await _dev_task(request, app_id, prod.prod_stop, env="prod")


@router.post("/{app_id}/prod/restart", status_code=202)
async def prod_restart(request: Request, app_id: int):
    return await _dev_task(request, app_id, prod.prod_restart, docker_gate=True, env="prod")


@router.get("/{app_id}/prod/status")
async def prod_status(request: Request, app_id: int):
    """直返引擎结果（含 urls 块）：端点不裁字段、不加包装（04 §5 接口表）。"""
    sf = request.app.state.session_factory
    try:
        app = await app_service.get_app(sf, app_id)
        return await prod.prod_status(sf, app)
    except DeployError as e:
        raise _http(e)


@router.get("/{app_id}/records")
async def list_records(request: Request, app_id: int, limit: int = 50):
    """发布历史（04 §4.5）：新→旧，UI 历史抽屉与排查入口共用。"""
    sf = request.app.state.session_factory
    try:
        await app_service.get_app(sf, app_id)
        return await records.list_for(sf, app_id, limit=limit)
    except DeployError as e:
        raise _http(e)


@router.delete("/{app_id}/records/{rid}")
async def delete_record(request: Request, app_id: int, rid: int):
    """FR4-08：删历史行 + 尽力 rmi 该版本两镜像。

    rmi 失败（Docker 未就绪/镜像被引用）只记 warning——删记录是用户清理历史的意图，
    不该被镜像侧的不确定性挡回去，且 keep-N 仍会兜底重试清理。rid 不属该应用 → 404
    （DELETE ... RETURNING 空集即不存在，不另发一次 SELECT）。
    """
    sf = request.app.state.session_factory
    try:
        app = await app_service.get_app(sf, app_id)
    except DeployError as e:
        raise _http(e)
    async with sf() as s:
        row = (await s.execute(text(
            "DELETE FROM deploy_record WHERE id = :r AND app_id = :a RETURNING version"),
            {"r": rid, "a": app_id})).first()
        await s.commit()
    if row is None:
        raise HTTPException(404, "发布记录不存在")
    version = row[0]
    if version:
        for role in ("frontend", "backend"):
            tag = f"zc-{app['slug']}-{role}:prod-{version}"
            try:
                code, log = await prod._docker("image", "rm", tag, timeout=120)
                if code != 0:
                    logger.warning("删发布记录时镜像移除失败（忽略）: %s %s", tag, log[-200:])
            except Exception as e:
                logger.warning("删发布记录时镜像移除异常（忽略）: %s %s", tag, e)
    return {"ok": True}
