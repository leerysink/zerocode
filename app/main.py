from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.db import make_engine, make_session_factory, run_migrations

def create_app(database_url: str | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = make_engine(database_url)
        app.state.engine = engine
        app.state.session_factory = make_session_factory(engine)
        await run_migrations(engine)
        yield
        await engine.dispose()

    return FastAPI(title="zerocode", lifespan=lifespan)

app = create_app()
