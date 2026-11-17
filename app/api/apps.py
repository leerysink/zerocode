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
import logging
import re
from contextlib import suppress

from fastapi import APIRouter, Request, HTTPException, File, Form, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from sqlalchemy import text
from app.deploy import apkbuild, compose, engine, mobile, mpbuild, mpcreds, prod, records, shell, sqlrun
from app.deploy import provision
from app.deploy import tasks as deploy_tasks
from app.deploy.errors import DeployError
from app.deploy.provision import read_cred
from app.deploy.slugs import suggest_slug, validate_slug
from app.services import app_service
from app.source import ingest as source_ingest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/apps")


class SourceIn(BaseModel):
    url: str
    subpath: str | None = None


class AppIn(BaseModel):
    name: str
    slug: str
    scale: str
    stack: str
    source: SourceIn | None = None


class SlugIn(BaseModel):
    slug: str


class AppRenameIn(BaseModel):
    name: str


class PublishIn(BaseModel):
    force: bool = False
    confirmDestructive: bool = False


class DecideRequestIn(BaseModel):
    force: bool = False
    confirmDestructive: bool = False


class RejectRequestIn(BaseModel):
    reason: str = ""


class RollbackIn(BaseModel):
    version: str


def _http(e: DeployError) -> HTTPException:
    return HTTPException(e.status, str(e))


async def _require_app(request: Request, app_id: int) -> tuple[dict, str]:
    """app_id 取应用的唯一入口（Ticket 000）：返回 (应用, 角色)，判权全在接缝里。

    端点不再各自拼 `get_app(..., owner_user_id=...)`：不可见/不存在同一 404 由
    `app_service.get_app_for_user` 一处给出；角色留给后续票的 `require_owner` 增权。
    """
    return await app_service.get_app_for_user(
        request.app.state.session_factory, app_id, request.state.actor["id"])


async def _require_app_owner(request: Request, app_id: int) -> dict:
    """仅所有者可用的端点的取应用入口：可见（含被共享）但非所有者 → 403。

    与 `_require_app` 同一条判权接缝，只多一步 `require_owner`——被共享者已在「共享给我」
    里看得见应用，403 是诚实口径（规格 §5.6）；后续票逐条放开的端点改回 `_require_app`。
    """
    app, role = await _require_app(request, app_id)
    app_service.require_owner(role)
    return app


def _reject_if_task_running(request: Request, app_id: int) -> None:
    """与 `_spawn` 同一条「同应用同时只允许一个部署任务」的契约，用在删除入口上。

    删记录时后台起栈/迁移任务还在跑，会留下一只没人认领的容器（行没了，状态回执无处可写），
    所以直接 409 让用户等一等，而不是在这里发明一套取消协议。
    """
    if deploy_tasks.task_running(request.app.state.dev_tasks, app_id):
        raise DeployError(deploy_tasks.BUSY_MESSAGE, status=409)


# 判权收口（Ticket 000）：以下端点取应用一律经 `_require_app`（单点判权），
# validate-slug 的 taken 例外——slug 全局 UNIQUE，预检必须看全量。


@router.post("", status_code=201)   # brief Interfaces：创建成功返回 201（示例代码漏写，测试为契约）
async def create_app(request: Request, body: AppIn):
    # 快照先抓后建：抓取失败时端口未分配、目录未建、库未供给，零副作用是顺序给的保证，
    # 不是靠回滚兜出来的（建应用一旦落库就要 teardown dev/prod，别让摄入替它背）。
    # 空串/全空格的 url 一律当「没填」：既不是一次网络请求，也不许拦住正常创建；
    # 形状不对的 source（缺 url）由 SourceIn 在 schema 层拒掉，够不到接缝。
    source = None
    origin = None
    raw_url = (body.source.url or "").strip() if body.source else ""
    if raw_url:
        try:
            source = await source_ingest.fetch_repo_snapshot(
                raw_url, subpath=(body.source.subpath or None),
                transport=getattr(request.app.state, "source_http_transport", None))
        except source_ingest.SourceError as e:
            raise HTTPException(400, str(e))
        origin = f"repo:{source['manifest']['host']}"   # VARCHAR(60)：只放短标记，URL 全文在 manifest 里
    try:
        return await app_service.create_app_project(
            request.app.state.session_factory, name=body.name, slug=body.slug,
            scale=body.scale, stack=body.stack, source=source, origin=origin,
            owner_user_id=request.state.actor["id"])   # 扩展态落归属（Ticket 003）
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
    sf = request.app.state.session_factory
    items = await app_service.list_apps(sf, owner_user_id=request.state.actor["id"])
    pending = await app_service.apps_with_pending_requests(sf, [a["id"] for a in items])
    for a in items:
        a["role"] = "owner"
        # 待审角标（S3）按应用算：存在任何成员的 pending 申请，所有者卡片就亮角标
        a["pending_publish"] = a["id"] in pending
    return items


@router.get("/shared")
async def list_shared(request: Request):
    """「共享给我」（规格 §5.3）：与自有列表同形状，另带 owner_nickname / role。

    必须声明在 `/{app_id}` 之前：否则 "shared" 会被当成 app_id 去解析。
    """
    sf = request.app.state.session_factory
    uid = request.state.actor["id"]
    items = await app_service.list_shared_apps(sf, uid)
    # 卡片状态机按**本人**的最近一条申请算：别人的待审申请不该让我的卡片显示「待审批」
    latest = await app_service.latest_requests_of_user(
        sf, [a["id"] for a in items], uid)
    for a in items:
        a["role"] = "collaborator"
        req = latest.get(a["id"])
        a["publish_request"] = req
        a["pending_publish"] = bool(req and req["status"] == "pending")
    return items


# ---------- 共享管理（规格 §5.1）：入口仅所有者，被共享者可自退 ----------

class ShareIn(BaseModel):
    user_id: int


@router.get("/{app_id}/share")
async def share_list(request: Request, app_id: int):
    sf = request.app.state.session_factory
    try:
        await _require_app_owner(request, app_id)
        return {
            "members": await app_service.list_share_members(sf, app_id),
            # 待审区块（S4）：申请人昵称 + 申请时间 + 批准/驳回入口
            "pending_request": await app_service.pending_request_of_app(sf, app_id),
        }
    except DeployError as e:
        raise _http(e)


@router.post("/{app_id}/share", status_code=201)
async def share_add(request: Request, app_id: int, body: ShareIn):
    sf = request.app.state.session_factory
    try:
        await _require_app_owner(request, app_id)
        return await app_service.add_share(sf, app_id=app_id, user_id=body.user_id,
                                           created_by=request.state.actor["id"])
    except DeployError as e:
        raise _http(e)


@router.post("/{app_id}/share/leave")
async def share_leave(request: Request, app_id: int):
    """被共享者主动退出：只认 collaborator——所有者没有共享行，自然 404（规格 §5.1）。

    退出即收口（S13）：待审申请作废（remove_share 同事务），正在旁观该应用的连接
    由 hub 按 (工作区, 本人) 精准关闭（4404 + share_revoked），他人的连接不受影响。
    """
    sf = request.app.state.session_factory
    try:
        app, role = await _require_app(request, app_id)
        if role != "collaborator":
            raise DeployError("共享关系不存在", status=404)
        await app_service.remove_share(sf, app_id=app_id,
                                       user_id=request.state.actor["id"])
        await request.app.state.hub.close_watchers_for(
            workspace_id=app["workspace_id"], user_id=request.state.actor["id"])
    except DeployError as e:
        raise _http(e)
    return {"ok": True}


@router.delete("/{app_id}/share/{user_id}")
async def share_remove(request: Request, app_id: int, user_id: int):
    """所有者撤销共享：与自退同一条收口（待审申请作废 + 关闭该用户的旁观连接）。"""
    sf = request.app.state.session_factory
    try:
        app = await _require_app_owner(request, app_id)
        await app_service.remove_share(sf, app_id=app_id, user_id=user_id)
        await request.app.state.hub.close_watchers_for(
            workspace_id=app["workspace_id"], user_id=user_id)
    except DeployError as e:
        raise _http(e)
    return {"ok": True}


# ---------- 发布申请与审批（规格 §2.3/§5.2，ADR-0050）：被共享者申请，所有者批准即执行 ----------

@router.post("/{app_id}/publish-requests", status_code=201)
async def create_publish_request(request: Request, app_id: int):
    """被共享者发起发布申请：待审唯一（S18）、发布中不收（409）；所有者无需申请（403）。"""
    sf = request.app.state.session_factory
    try:
        _app, role = await _require_app(request, app_id)
        if role != "collaborator":
            raise DeployError("所有者可直接发布，无需申请", status=403)
        return await app_service.create_publish_request(
            sf, app_id=app_id, requester_id=request.state.actor["id"])
    except DeployError as e:
        raise _http(e)


@router.post("/{app_id}/publish-requests/{request_id}/approve", status_code=202)
async def approve_publish_request(request: Request, app_id: int, request_id: int,
                                  body: DecideRequestIn | None = None):
    """批准即执行（S4/D4）：先跑与直接发布**同一份**闸门（Ticket 001 的 `_publish_gates`），
    通过后才置 approved 并异步发布；闸门失败申请保持 pending，可重试（D15/S19）。

    `_spawn` 的受理竞态与「闸门失败保 pending」同理：发布没能受理就把申请退回 pending，
    不留「已批准但从未发布」的假状态。
    """
    sf = request.app.state.session_factory
    hub = request.app.state.deploy_hub
    body = body or DecideRequestIn()
    try:
        app = await _require_app_owner(request, app_id)
        req = await app_service.get_publish_request(sf, app_id=app_id, request_id=request_id)
        if req["status"] != "pending":
            raise DeployError("该申请已处理，无法重复审批", status=409)
        # chat_active 的 409 detail 是字典：原样上抛，前端按 code 弹强制确认（与直接发布同形状）
        await _publish_gates(request, app, force=body.force)
        decided = await app_service.decide_publish_request(
            sf, request_id=request_id, status="approved",
            decided_by=request.state.actor["id"])
        try:
            _spawn(request, app_id, lambda: prod.publish(
                sf, app, notify=hub.publish, force=body.force,
                confirm_destructive=body.confirmDestructive,
                on_success=lambda: _after_publish(request, app_id)))
        except DeployError:
            await app_service.reopen_publish_request(sf, request_id)
            raise
    except DeployError as e:
        raise _http(e)
    return {"ok": True, "request": decided}


@router.post("/{app_id}/publish-requests/{request_id}/reject")
async def reject_publish_request(request: Request, app_id: int, request_id: int,
                                 body: RejectRequestIn):
    """驳回必须填理由（S4）：空理由 400、非 pending 409；理由落库供申请人查看。"""
    sf = request.app.state.session_factory
    try:
        await _require_app_owner(request, app_id)
        return await app_service.reject_publish_request(
            sf, app_id=app_id, request_id=request_id, reason=body.reason,
            decided_by=request.state.actor["id"])
    except DeployError as e:
        raise _http(e)


@router.delete("/{app_id}/publish-requests/{request_id}")
async def withdraw_publish_request(request: Request, app_id: int, request_id: int):
    """申请人撤回（S12）：非本人 403、非 pending 409。"""
    sf = request.app.state.session_factory
    try:
        await _require_app(request, app_id)
        return await app_service.withdraw_publish_request(
            sf, app_id=app_id, request_id=request_id,
            user_id=request.state.actor["id"])
    except DeployError as e:
        raise _http(e)


@router.get("/{app_id}")
async def detail(request: Request, app_id: int):
    try:
        app, role = await _require_app(request, app_id)
        app["role"] = role
        return app
    except DeployError as e:
        raise _http(e)


@router.patch("/{app_id}")
async def rename(request: Request, app_id: int, body: AppRenameIn):
    """应用改名（只改显示名）：slug 不可改——它是工程目录名、库名与镜像名的共同锚点。

    名字进壳参数（APK launcher 标签与自动图标），移动栈改名后 prod 正在运行就立即重出包；
    prod 未运行时只留下 params_stale，下一次发布会重建（ADR-0042）。
    """
    try:
        await _require_app_owner(request, app_id)
        app = await app_service.rename_app(request.app.state.session_factory, app_id, body.name)
        with suppress(Exception):
            await request.app.state.apk_builder.enqueue(app_id, app=app)
        with suppress(Exception):
            await request.app.state.mp_builder.enqueue(app_id, kind="precheck", app=app)
        return app
    except DeployError as e:
        raise _http(e)


@router.delete("/{app_id}")
async def remove(request: Request, app_id: int):
    """删除应用（侧栏「⋯ → 删除应用」的落点）：尽力停 dev 栈 + 删平台记录。

    M1 裁定：磁盘工程目录与应用数据库**保留**，响应把保留位置回给前端写进确认文案（kept）。
    停栈按「尽力而为」处理——Docker 没开或 compose 失败都只记 warning，不把应用永久钉在列表里：
    记录删不掉才是用户真正没法自救的状态。发布中 / 有部署任务在跑则 409 拒删（规格 08 §5.4）。

    停栈不看状态列、dev 与 prod 一起收（规格 05）：记录一删端口即在库里释放，容器还留着就是把
    同一个端口原封不动地埋给下一个应用。
    """
    sf = request.app.state.session_factory
    try:
        app = await _require_app_owner(request, app_id)
        app_service.ensure_deletable(app)
        _reject_if_task_running(request, app_id)
        for warning in await engine.down_app_stacks(app["slug"]):
            logger.warning("删除应用时停栈未净（继续删记录）: app_id=%s %s", app_id, warning)
        snap = await app_service.delete_app(sf, app_id)
    except DeployError as e:
        raise _http(e)
    # 平台侧 APK 构建临时文件随记录清理；prod 卷里的产物归属应用环境，不在删除链里
    with suppress(Exception):
        apkbuild.cleanup_app(snap["slug"])
    with suppress(Exception):
        mpcreds.cleanup_app(snap["slug"])
    with suppress(Exception):
        mpbuild.cleanup_app(snap["slug"])
    return {"ok": True, "kept": {"root_path": snap["root_path"], "slug": snap["slug"],
                                 "databases": snap["databases"]}}


# ---------- dev 生命周期（Task 12 / 规格 03 §4.4）：202 受理 + 后台执行，进度走 WS ----------

def _spawn(request: Request, app_id: int, coro_factory) -> None:
    """长任务句柄表：同一应用同时只允许一个部署任务（重复受理 → 409，而不是排队双跑）。

    实现与 Agent 预览工具共用 `app.deploy.tasks`（同应用单栈的判据只有一份）；
    本函数只负责把 `request.app.state.dev_tasks` 递过去，行为与状态码不变。
    """
    deploy_tasks.spawn(request.app.state.dev_tasks, app_id, coro_factory)


async def _dev_task(request: Request, app_id: int, action, *, docker_gate: bool = False,
                    env: str = "dev", owner_only: bool = True) -> dict:
    """生命周期端点共用一条链：取应用 →（启动类）同步 Docker 预检 → 起后台任务 → 202。

    `action` 在**请求时**按模块属性取出（engine.start_dev 等），monkeypatch 才拦得住。

    `owner_only=False`（dev 起停/重启与 prod 启停/重启）对被共享者放行（Ticket 003/004）：
    起停预览不产生版本、不改代码，prod 运维同属「可上手操作」；发布/回滚仍是所有者动作。

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
        if owner_only:
            app = await _require_app_owner(request, app_id)
        else:
            app, _role = await _require_app(request, app_id)
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
    return await _dev_task(request, app_id, engine.start_dev, docker_gate=True, owner_only=False)


@router.post("/{app_id}/dev/stop", status_code=202)
async def dev_stop(request: Request, app_id: int):
    return await _dev_task(request, app_id, engine.stop_dev, owner_only=False)


@router.post("/{app_id}/dev/restart", status_code=202)
async def dev_restart(request: Request, app_id: int):
    return await _dev_task(request, app_id, engine.restart_dev, docker_gate=True, owner_only=False)


@router.get("/{app_id}/dev/status")
async def dev_status(request: Request, app_id: int):
    sf = request.app.state.session_factory
    try:
        app, _role = await _require_app(request, app_id)
        return await engine.dev_status(sf, app)
    except DeployError as e:
        raise _http(e)


@router.get("/{app_id}/urls")
async def app_urls(request: Request, app_id: int):
    """应用 prod 访问地址（规格 05 §5 / 通配域名网关）：只回 domain=https://{slug}.zerocode.com，无副作用只读。"""
    sf = request.app.state.session_factory
    try:
        app, _role = await _require_app(request, app_id)
        from app.deploy import nics
        return await nics.app_urls(sf, app)
    except DeployError as e:
        raise _http(e)


# ---------- 移动端分发（规格 2026-11-03 H5 + 规格 2026-11-05 APK 壳） ----------

@router.get("/{app_id}/mobile")
async def mobile_info(request: Request, app_id: int):
    """移动端弹框数据源：dev/prod 两条 H5 地址 + APK 构建状态/壳版本/图标（只读）。"""
    sf = request.app.state.session_factory
    try:
        # Ticket 004：被共享者可看弹框并重打 APK；图标覆盖/小程序写入仍仅所有者。
        app, _role = await _require_app(request, app_id)
        return await mobile.info(sf, app)
    except DeployError as e:
        raise _http(e)


@router.post("/{app_id}/mobile/apk/rebuild", status_code=202)
async def mobile_rebuild_apk(request: Request, app_id: int):
    """手动重建 APK（ADR-0042）：无论参数是否变化都重出包并递增壳版本。

    同步闸门只查「移动栈 + prod 正在运行」——构建本身异步，进度由弹框轮询 GET /mobile。
    """
    sf = request.app.state.session_factory
    try:
        app, _role = await _require_app(request, app_id)
        if not mobile.is_mobile(app):
            raise DeployError("仅移动端应用支持 APK 构建", status=400)
        if app.get("status_prod") != "running":
            raise DeployError("正式环境未运行：请先发布并启动正式环境，再构建 APK", status=409)
        result = await request.app.state.apk_builder.enqueue(app_id, force=True, app=app)
        return {"ok": True, "status": "building", "deduped": result == "building"}
    except DeployError as e:
        raise _http(e)


@router.get("/{app_id}/mobile/icon")
async def mobile_icon(request: Request, app_id: int):
    """图标预览：自定义优先，否则按应用名实时生成 SVG。cookie 鉴权随同源请求自动带上。"""
    sf = request.app.state.session_factory
    try:
        # 只读预览随弹框一起放行；覆盖/恢复自动图标（PUT/DELETE）仍仅所有者。
        app, _role = await _require_app(request, app_id)
        if not mobile.is_mobile(app):
            raise DeployError("仅移动端应用支持移动端分发", status=400)
        path = apkbuild.custom_icon_path(app["slug"])
        if path is not None:
            media = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
            return FileResponse(path, media_type=media, headers={"Cache-Control": "no-store"})
        return Response(content=shell.auto_icon_svg(app.get("name") or "", app["slug"]),
                        media_type="image/svg+xml", headers={"Cache-Control": "no-store"})
    except DeployError as e:
        raise _http(e)


@router.put("/{app_id}/mobile/icon")
async def mobile_upload_icon(request: Request, app_id: int, file: UploadFile = File(...)):
    """上传图标覆盖自动图标：PNG/JPEG 魔数 + ≤2MB；成功即触发重建（prod 未运行则留到下次发布）。"""
    sf = request.app.state.session_factory
    try:
        app = await _require_app_owner(request, app_id)
        if not mobile.is_mobile(app):
            raise DeployError("仅移动端应用支持移动端分发", status=400)
        data = await file.read(shell.MOBILE_ICON_MAX_BYTES + 1)
        if len(data) > shell.MOBILE_ICON_MAX_BYTES:
            raise DeployError(f"图标不能超过 {shell.MOBILE_ICON_MAX_BYTES // (1024 * 1024)}MB",
                              status=413)
        if not data:
            raise DeployError("图标文件为空", status=400)
        ext = shell.detect_icon_ext(data)
        if ext is None:
            raise DeployError("只支持 PNG/JPEG 图标", status=400)
        apkbuild.save_icon(app["slug"], data, ext)
        result = await request.app.state.apk_builder.enqueue(app_id, app=app)
        return {"ok": True, "build": result}
    except DeployError as e:
        raise _http(e)


@router.delete("/{app_id}/mobile/icon")
async def mobile_reset_icon(request: Request, app_id: int):
    """恢复自动图标（删掉自定义覆盖），同样触发重建。"""
    sf = request.app.state.session_factory
    try:
        app = await _require_app_owner(request, app_id)
        if not mobile.is_mobile(app):
            raise DeployError("仅移动端应用支持移动端分发", status=400)
        removed = apkbuild.delete_icon(app["slug"])
        result = await request.app.state.apk_builder.enqueue(app_id, app=app)
        return {"ok": True, "removed": removed, "build": result}
    except DeployError as e:
        raise _http(e)


# ---------- 小程序凭据（规格 2026-11-09 / ADR-0045）：密钥只进不出 ----------

@router.put("/{app_id}/mobile/miniprogram/credentials")
async def miniprogram_bind(request: Request, app_id: int, appid: str = Form(...),
                           file: UploadFile = File(...)):
    """绑定/换绑小程序账号：AppID + 代码上传密钥；绑定成功后由任务器跑一次预检。

    密钥只写入平台数据区（0600），响应与任何 GET 都不回显原文；AppID 全局唯一，
    一应用一账号（换绑先解绑）。
    """
    sf = request.app.state.session_factory
    try:
        app = await _require_app_owner(request, app_id)
        if not mobile.is_mobile(app):
            raise DeployError("仅移动端应用支持小程序分发", status=400)
        data = await file.read(mpcreds.KEY_MAX_BYTES + 1)
        if not data:
            raise DeployError("上传密钥文件为空", status=400)
        if len(data) > mpcreds.KEY_MAX_BYTES:
            raise DeployError(f"上传密钥不能超过 {mpcreds.KEY_MAX_BYTES // 1024}KB", status=413)
        result = await mpcreds.bind(sf, app, appid, data)
        # 绑定后异步预检一次：编译 + preview，不产生开发版（ADR-0045）；入队失败不推翻绑定
        try:
            await request.app.state.mp_builder.enqueue(app_id, kind="precheck", app=app)
        except Exception:  # noqa: BLE001
            logger.warning("小程序预检入队失败 app_id=%s", app_id, exc_info=True)
        return {"ok": True, **result}
    except DeployError as e:
        raise _http(e)


@router.delete("/{app_id}/mobile/miniprogram/credentials")
async def miniprogram_unbind(request: Request, app_id: int):
    """解绑小程序账号：清 DB 行与密钥文件；正在跑的任务按当前轮失败收口。"""
    sf = request.app.state.session_factory
    try:
        app = await _require_app_owner(request, app_id)
        if not mobile.is_mobile(app):
            raise DeployError("仅移动端应用支持小程序分发", status=400)
        await mpcreds.unbind(sf, app)
        return {"ok": True}
    except DeployError as e:
        raise _http(e)


@router.post("/{app_id}/mobile/miniprogram/preview", status_code=202)
async def miniprogram_preview(request: Request, app_id: int):
    """按需生成预览码：只跑 preview，不产生开发版（ADR-0045）。

    码经鉴权接口读取、短时有效；任务进行中再点不并行，返回 deduped。
    """
    sf = request.app.state.session_factory
    try:
        app = await _require_app_owner(request, app_id)
        if not mobile.is_mobile(app):
            raise DeployError("仅移动端应用支持小程序分发", status=400)
        binding = await mpcreds.get_binding(sf, app_id)
        if not binding.get("appid"):
            raise DeployError("尚未绑定小程序账号：请先绑定 AppID 与上传密钥", status=409)
        result = await request.app.state.mp_builder.enqueue(app_id, kind="preview", app=app)
        return {"ok": True, "status": "building", "deduped": result == "building"}
    except DeployError as e:
        raise _http(e)


@router.post("/{app_id}/mobile/miniprogram/upload", status_code=202)
async def miniprogram_upload(request: Request, app_id: int):
    """手动重传开发版：不改代码、不重发布也能覆盖一次（版本号仍取当前发布版本）。

    与发布后的自动上传共用串行队列；`force=True` 跳过任何「上次已传过」的判断，
    但进行中不并行——返回 `deduped`，本轮结束后补跑。
    """
    sf = request.app.state.session_factory
    try:
        app = await _require_app_owner(request, app_id)
        if not mobile.is_mobile(app):
            raise DeployError("仅移动端应用支持小程序分发", status=400)
        binding = await mpcreds.get_binding(sf, app_id)
        if not binding.get("appid"):
            raise DeployError("尚未绑定小程序账号：请先绑定 AppID 与上传密钥", status=409)
        if app.get("status_prod") != "running":
            raise DeployError("正式环境未运行：请先发布并启动正式环境，再上传小程序", status=409)
        result = await request.app.state.mp_builder.enqueue(
            app_id, kind="upload", force=True, app=app)
        return {"ok": True, "status": "building", "deduped": result == "building"}
    except DeployError as e:
        raise _http(e)


@router.get("/{app_id}/mobile/miniprogram/preview")
async def miniprogram_preview_image(request: Request, app_id: int):
    """预览码图片：同源 <img> 自带会话 cookie；不落公开目录，未生成时 404。"""
    sf = request.app.state.session_factory
    try:
        # 已生成的预览码是只读展示；生成（POST preview）与上传仍仅所有者。
        app, _role = await _require_app(request, app_id)
        if not mobile.is_mobile(app):
            raise DeployError("仅移动端应用支持小程序分发", status=400)
        path = mpbuild.preview_path(app["slug"])
        if not path.is_file():
            raise DeployError("预览码尚未生成：请先点「生成预览码」", status=404)
        return FileResponse(path, media_type="image/jpeg",
                            headers={"Cache-Control": "no-store"})
    except DeployError as e:
        raise _http(e)


# ---------- sqlchange（Task 12 / 规格 07）：应用侧迁移的执行、记账与待办清单 ----------

# 应用库连接串口径收口到锚点服务（Task 5）：两处各自维护一份早晚漂移。
from app.store.anchor import app_db_url as _app_db_url


async def _history_rows(url: str, sql: str) -> list[dict]:
    """应用库记账读取：实现收口在 sqlrun.history_rows（REST 与 Agent 共用同一份读法）。"""
    return await sqlrun.history_rows(url, sql)


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
        app = await _require_app_owner(request, app_id)
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
        app = await _require_app_owner(request, app_id)
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
        app, _role = await _require_app(request, app_id)
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
    """dev 视角的待执行清单：公共段 + dev 段，按真实执行顺序给出**相对 sqlchange/ 的路径**，
    并标注 dev 库里已记账的那些（ADR-0040：prod 段的文件不在本清单里）。

    P2 双轨：glob 由 `V*.sql` 放宽为 `*.sql`，让数据轨 `D*.sql`（与遗留 `V*.sql`）一并入待办清单；
    口径只影响本 UI 列表——商店上架锚点取自 sqlchange_history（DB），与本目录 glob 无关（不伤打包）。"""
    sf = request.app.state.session_factory
    try:
        app, _role = await _require_app(request, app_id)
        root = await engine._app_root(sf, app)
    except DeployError as e:
        raise _http(e)
    d = root / "sqlchange"
    files = sqlrun.segment_files(d, "dev") if d.is_dir() else []
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
        app = await _require_app_owner(request, app_id)
        root = await engine._app_root(sf, app)
    except DeployError as e:
        raise _http(e)
    d = prod.compute_schema_diff(root, app.get("current_version"))
    if d is None:
        raise HTTPException(400, "无上一版结构基线可比对（未发布或缺 schema 快照），无法生成回填骨架")
    if not schemadiff.destructive_changes(d):
        raise HTTPException(400, "未检出破坏性变更，无需回填")
    today = datetime.now().strftime("%Y%m%d")   # 算一次：骨架表头注释与文件名日期跨年/跨零点保持一致
    content = schemadiff.backfill_skeleton(d, today=today)
    scdir = root / "sqlchange"
    scdir.mkdir(exist_ok=True)
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


async def _after_publish(request: Request, app_id: int) -> None:
    """发布成功后的附加产物（ADR-0042/0045）：APK 构建与小程序上传各自入队、互不阻塞。

    两个任务器都自己从库里重读应用并判闸门（非移动栈/未绑定 → skipped）；这里逐项兜错，
    一家抛异常不得连累另一家，更不能反噬已成功的发布（引擎侧还有一层兜底）。
    """
    for name, enqueue in (
        ("APK", lambda: request.app.state.apk_builder.enqueue(app_id)),
        ("小程序", lambda: request.app.state.mp_builder.enqueue(app_id, kind="upload")),
    ):
        try:
            await enqueue()
        except Exception:  # noqa: BLE001
            logger.exception("发布成功后的%s入队失败（不影响发布结果） app_id=%s", name, app_id)


async def _publish_gates(request: Request, app: dict, *, force: bool) -> None:
    """发布闸门单源（Ticket 001）：任务互斥 → publishing 并发闸 → chat 确认 → 同步预检。

    直接发布（本文件 `publish`）与「批准发布申请」（Ticket 009）共用这一处判据，
    两条路径都不许复制判词。chat 闸门只看**本应用工作区**的活跃会话（别的应用在改
    代码不该挡住这次发布）；409 detail 是字典 `{code, message}`——前端按
    `code === 'chat_active'` 弹强制确认，不能只吃字符串（`_http` 的 str detail
    通道给不了这个分支能力）。
    """
    app_id = app["id"]
    _reject_if_task_running(request, app_id)
    if app["status_prod"] == "publishing":
        # _spawn 只挡本进程在跑的任务；publishing 可能是上一进程遗留，按状态再挡一道
        raise DeployError("发布进行中，请等待完成", status=409)
    if not force:
        active = list(request.app.state.hub.running_session_ids())
        if active:
            async with request.app.state.session_factory() as s:
                hit = (await s.execute(text(
                    "SELECT 1 FROM chat_session WHERE workspace_id=:w AND id = ANY(:ids) LIMIT 1"),
                    {"w": app["workspace_id"], "ids": active})).first()
            if hit:
                raise HTTPException(409, {
                    "code": "chat_active",
                    "message": "当前 AI 正在修改代码，发布的可能是不完整版本，是否继续？"})
    await prod.preflight_docker()


@router.post("/{app_id}/publish", status_code=202)
async def publish(request: Request, app_id: int, body: PublishIn):
    """一键发布（04 §5 / FR4-09 chat 闸门 + publishing 并发双保险）。

    闸门序列（任务互斥 → 状态并发闸 → chat 确认 → 同步预检）全在 `_publish_gates`：
    与「批准发布申请」共用，这里只做取应用 + 受理。
    """
    sf = request.app.state.session_factory
    hub = request.app.state.deploy_hub
    try:
        app = await _require_app_owner(request, app_id)
        await _publish_gates(request, app, force=body.force)
        _spawn(request, app_id, lambda: prod.publish(sf, app, notify=hub.publish,
                                                     force=body.force,
                                                     confirm_destructive=body.confirmDestructive,
                                                     on_success=lambda: _after_publish(request, app_id)))
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
        app = await _require_app_owner(request, app_id)
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
    return await _dev_task(request, app_id, prod.prod_start, docker_gate=True, env="prod",
                           owner_only=False)


@router.post("/{app_id}/prod/stop", status_code=202)
async def prod_stop(request: Request, app_id: int):
    return await _dev_task(request, app_id, prod.prod_stop, env="prod", owner_only=False)


@router.post("/{app_id}/prod/restart", status_code=202)
async def prod_restart(request: Request, app_id: int):
    return await _dev_task(request, app_id, prod.prod_restart, docker_gate=True, env="prod",
                           owner_only=False)


@router.get("/{app_id}/prod/status")
async def prod_status(request: Request, app_id: int):
    """直返引擎结果（含 urls 块）：端点不裁字段、不加包装（04 §5 接口表）。"""
    sf = request.app.state.session_factory
    try:
        app, _role = await _require_app(request, app_id)
        return await prod.prod_status(sf, app)
    except DeployError as e:
        raise _http(e)


@router.get("/{app_id}/records")
async def list_records(request: Request, app_id: int, limit: int = 50):
    """发布历史（04 §4.5）：新→旧，UI 历史抽屉与排查入口共用。"""
    sf = request.app.state.session_factory
    try:
        await _require_app(request, app_id)
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
        app = await _require_app_owner(request, app_id)
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
        for role in compose.prod_roles(app.get("stack")):
            tag = f"zc-{app['slug']}-{role}:prod-{version}"
            try:
                code, log = await prod._docker("image", "rm", tag, timeout=120)
                if code != 0:
                    logger.warning("删发布记录时镜像移除失败（忽略）: %s %s", tag, log[-200:])
            except Exception as e:
                logger.warning("删发布记录时镜像移除异常（忽略）: %s %s", tag, e)
    return {"ok": True}
