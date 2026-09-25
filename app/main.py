import asyncio, logging, os, sys
from contextlib import asynccontextmanager, suppress
from fastapi import FastAPI
from sqlalchemy import bindparam, text as _sql
from app.db import make_engine, make_session_factory, run_migrations
from app.deploy.hub import DeployHub
from app.hub import ChatHub

logger = logging.getLogger(__name__)


def _harden_console_streams() -> None:
    """中文 Windows 控制台（GBK）打不出 `✓ ✗ ▶`：默认 strict 编码会让整条日志被 logging
    吞成「--- Logging error ---」，用户看到的是一坨 traceback 而不是启动步骤。
    把 errors 降级成 replace（那几个字符变 `?`，行不丢）；UTF-8 终端里仍然是原样字形。"""
    for stream in (sys.stdout, sys.stderr):
        with suppress(Exception):        # pytest/uvicorn 可能换成不支持 reconfigure 的对象
            stream.reconfigure(errors="replace")


_harden_console_streams()

# 部署流程的步骤日志（app.deploy.engine 里 INFO 级的「▶ 启动预览 / ✓ 迁移完成 / ✗ 失败」）要在
# backend 控制台窗口真的看得见：Python 的 lastResort handler 只放行 WARNING 及以上。
# 刻意不用 force——宿主（测试、uvicorn --log-config）已配置根 logger 时保持原样。
logging.basicConfig(level=os.environ.get("ZEROCODE_LOG_LEVEL", "INFO").upper(),
                    stream=sys.stdout,
                    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S")

async def _watchdog_tick(engine, hub) -> list[int]:
    """单次巡检：将 status='running' 且超 10 分钟无活动的会话标记为 error（spec §7 崩溃恢复）。

    ask/confirm future 仍挂起的会话属用户作答期间（G2 裁定 timeout=0 不限时，
    updated_at 停更是正常等待而非崩溃），跳过不标并记录日志。返回本次标记的会话 id。
    """
    async with engine.begin() as conn:
        stale = (await conn.execute(_sql(
            "SELECT id FROM chat_session "
            "WHERE status='running' AND updated_at < now() - interval '10 minutes'"))).scalars().all()
        to_mark = [sid for sid in stale
                   if sid not in hub.ask_futures and sid not in hub.confirm_futures]
        skipped = [sid for sid in stale if sid not in to_mark]
        if to_mark:
            await conn.execute(_sql(
                "UPDATE chat_session SET status='error' WHERE id IN :ids").bindparams(
                bindparam("ids", expanding=True)), {"ids": to_mark})
        if skipped:
            logger.info("watchdog 跳过挂起 ask/confirm 的会话（用户作答期间，非崩溃）: %s", skipped)
        return to_mark

async def _watchdog(engine, hub):
    """孤儿 running 会话恢复：每 60s 巡检一次，崩溃重启后超 10 分钟无活动的标记为 error。"""
    while True:
        await asyncio.sleep(60)
        try:
            await _watchdog_tick(engine, hub)
        except Exception:
            logger.exception("watchdog 巡检失败")

async def _deploy_startup(app: FastAPI) -> None:
    """部署环境的启动准备（Task 12 / 裁定 D-3）：孤儿 dev 栈清理 → zcnet → sqlrunner 镜像 →
    prod 启动对账（Task 8 / 04 §6）。

    各步兜底、全部非致命：Docker Desktop 没开只意味着「预览暂不可用」，绝不能把平台启动卡死
    或带崩——所以只在模块内按**属性名**取用（deploy_engine.cleanup_orphans 等），测试替身可拦。
    """
    from app.deploy import engine as deploy_engine, provision, sqlrun
    try:
        await deploy_engine.cleanup_orphans(app.state.session_factory,
                                            notify=app.state.deploy_hub.publish)
    except Exception:
        logger.exception("部署环境启动清理失败（Docker 未启动？）")
    try:
        await provision.ensure_zcnet()
    except Exception:
        logger.exception("部署环境启动准备失败：docker 网络 zcnet（首次预览时会重试）")
    try:
        await sqlrun.build_if_missing()
    except Exception:
        logger.exception("sqlrunner 镜像构建失败（首次预览时会重试）")
    # Task 8（04 §6）：中断发布记账收口 + prod 状态按容器实况复位。同样非致命——Docker 没开
    # 只意味着发布/正式环境暂不可用；只碰 prod 自己的状态列，不触发 dev 清理（cleanup_orphans 只管 dev 栈）。
    try:
        from app.deploy import prod as deploy_prod
        await deploy_prod.reconcile(app.state.session_factory,
                                    notify=app.state.deploy_hub.publish)
    except Exception:
        logger.exception("prod 启动对账失败（Docker 未就绪？）")


def create_app(database_url: str | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = make_engine(database_url)
        app.state.engine = engine
        app.state.session_factory = make_session_factory(engine)
        app.state.hub = ChatHub()
        # 部署发布套件（Task 12）：事件总线（WS /ws/deploy/{app_id} 的订阅源）+ 长任务句柄表
        app.state.deploy_hub = DeployHub()
        app.state.dev_tasks = {}
        await run_migrations(engine)
        await _deploy_startup(app)
        watchdog = asyncio.create_task(_watchdog(engine, app.state.hub))
        yield
        watchdog.cancel()
        with suppress(asyncio.CancelledError):
            await watchdog  # 等取消落地，避免事件循环关闭时报 pending task 警告
        # 优雅退出：停掉仍在跑的 dev 栈（prod 不受影响，FR3-04）；同样不得因 Docker 侧异常而卡住关闭
        try:
            from app.deploy import engine as deploy_engine
            from app.deploy.settings import deploy_cfg
            cfg = await deploy_cfg(app.state.session_factory)
            await deploy_engine.stop_all_dev_on_exit(app.state.session_factory, cfg,
                                                     notify=app.state.deploy_hub.publish)
        except Exception:
            logger.exception("退出清理 dev 栈失败")
        await engine.dispose()

    from app.api.config_api import router as config_router
    from app.api import workspaces, sessions, files, models
    from app.api.ws import router as ws_router
    app = FastAPI(title="zerocode", lifespan=lifespan)
    # 登录体系（Ticket 002 / ADR-0003）：/api/auth register/login/logout/me（guard 中间件在后续票接入）
    from app.api import auth as auth_api
    app.include_router(auth_api.router)
    app.include_router(config_router)
    app.include_router(workspaces.router)
    app.include_router(sessions.router)
    app.include_router(files.router)
    app.include_router(models.router)
    # 部署发布套件（Task 6）：/api/apps 创建/校验 slug/列表/详情
    from app.api import apps as apps_api
    app.include_router(apps_api.router)
    # 预览日志：GET /api/apps/{id}/dev/logs（页面「查看日志」的历史来源，与 apps_api 同前缀）
    from app.api import dev_logs
    app.include_router(dev_logs.router)
    # 部署发布套件（Task 12）：/ws/deploy/{app_id} 部署事件流（须在静态托管之前，路由不被 SPA 吞掉）
    from app.api import deploy_ws
    app.include_router(deploy_ws.router)
    # 部署发布套件（Task 5）：/api/system/nics 网卡候选（局域网地址选择的数据源）
    from app.api import system
    app.include_router(system.router)
    # 应用商店（M3-a）：/api/store 与上架入口 /api/apps/{id}/release
    from app.api import store as store_api
    app.include_router(store_api.router)
    app.include_router(store_api.apps_ext)
    app.include_router(ws_router)

    # ---- 全局登录 guard（Ticket 002 / ADR-0003）：/api/* 除 /api/auth/* 外一律要求会话 ----
    # /ws/* 属 WebSocket scope 不经 http 中间件，已在 Ticket 009 收口（ws/deploy_ws 端点内
    # 自行 by_token 鉴权，未登录拒 close 4401）；静态资源（SPA）不拦。
    # actor 挂 request.state.actor 供业务层取用；by_token 命中即续期（store 侧既有同款语义）。
    from fastapi.responses import JSONResponse

    @app.middleware("http")
    async def auth_guard(request, call_next):
        path = request.url.path
        if path.startswith("/api") and not path.startswith("/api/auth"):
            from app.store import identity
            actor = await identity.by_token(
                request.app.state.session_factory,
                request.cookies.get(identity.ACTOR_COOKIE))
            if actor is None:
                return JSONResponse({"detail": "未登录"}, status_code=401)
            request.state.actor = actor
        return await call_next(request)

    # ---- 前端静态托管（Task 15）：挂载 frontend/dist，SPA 回退 index.html ----
    import os
    from pathlib import Path
    from fastapi.responses import FileResponse, JSONResponse
    from fastapi.staticfiles import StaticFiles
    from starlette.exceptions import HTTPException as StarletteHTTPException
    dist = Path(os.environ.get("ZEROCODE_FRONTEND_DIST",
                               Path(__file__).resolve().parents[1] / "frontend" / "dist"))
    if dist.is_dir():
        app.mount("/", StaticFiles(directory=dist, html=True), name="spa")

        # SPA 回退：非 /api、/ws 的 GET 请求未命中静态文件时回退 index.html；
        # API/WS 路径保持 JSON 404，不吞掉后端自身的 404 语义。
        index_file = dist / "index.html"

        @app.exception_handler(404)
        async def spa_fallback(request, exc: StarletteHTTPException):
            if (request.method in ("GET", "HEAD") and index_file.is_file()
                    and not request.url.path.startswith(("/api", "/ws"))):
                return FileResponse(index_file)
            return JSONResponse({"detail": exc.detail}, status_code=404)
    return app

app = create_app()
