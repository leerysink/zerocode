import asyncio
from contextlib import asynccontextmanager, suppress
from fastapi import FastAPI
from sqlalchemy import text as _sql
from app.db import make_engine, make_session_factory, run_migrations
from app.hub import ChatHub

async def _watchdog(app: FastAPI):
    """孤儿 running 会话恢复：进程崩溃重启后超过 10 分钟无活动的标记为 error（spec §7 崩溃恢复）。"""
    while True:
        await asyncio.sleep(60)
        try:
            async with app.state.session_factory() as s:
                await s.execute(_sql(
                    "UPDATE chat_session SET status='error' "
                    "WHERE status='running' AND updated_at < now() - interval '10 minutes'"))
                await s.commit()
        except Exception:
            pass

def create_app(database_url: str | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = make_engine(database_url)
        app.state.engine = engine
        app.state.session_factory = make_session_factory(engine)
        app.state.hub = ChatHub()
        await run_migrations(engine)
        watchdog = asyncio.create_task(_watchdog(app))
        yield
        watchdog.cancel()
        with suppress(asyncio.CancelledError):
            await watchdog  # 等取消落地，避免事件循环关闭时报 pending task 警告
        await engine.dispose()

    from app.api.config_api import router as config_router
    from app.api import workspaces, sessions
    from app.api.ws import router as ws_router
    app = FastAPI(title="zerocode", lifespan=lifespan)
    app.include_router(config_router)
    app.include_router(workspaces.router)
    app.include_router(sessions.router)
    app.include_router(ws_router)
    return app

app = create_app()
