import asyncio, logging
from contextlib import asynccontextmanager, suppress
from fastapi import FastAPI
from sqlalchemy import bindparam, text as _sql
from app.db import make_engine, make_session_factory, run_migrations
from app.hub import ChatHub

logger = logging.getLogger(__name__)

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

def create_app(database_url: str | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = make_engine(database_url)
        app.state.engine = engine
        app.state.session_factory = make_session_factory(engine)
        app.state.hub = ChatHub()
        await run_migrations(engine)
        watchdog = asyncio.create_task(_watchdog(engine, app.state.hub))
        yield
        watchdog.cancel()
        with suppress(asyncio.CancelledError):
            await watchdog  # 等取消落地，避免事件循环关闭时报 pending task 警告
        await engine.dispose()

    from app.api.config_api import router as config_router
    from app.api import workspaces, sessions, files
    from app.api.ws import router as ws_router
    app = FastAPI(title="zerocode", lifespan=lifespan)
    app.include_router(config_router)
    app.include_router(workspaces.router)
    app.include_router(sessions.router)
    app.include_router(files.router)
    app.include_router(ws_router)
    return app

app = create_app()
