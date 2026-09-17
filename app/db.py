import re
from pathlib import Path
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from app import config as app_config

CHANGELOG_DIR = Path(__file__).resolve().parents[1] / "changelog"  # app/db.py → 仓库根/changelog

def make_engine(url: str | None = None) -> AsyncEngine:
    return create_async_engine(url or app_config.database_url())

def make_session_factory(engine: AsyncEngine) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False)

def split_statements(sql: str) -> list[str]:
    lines = []
    for line in sql.splitlines():
        if line.strip().startswith("--"):
            continue
        lines.append(line.split("--", 1)[0])
    clean = "\n".join(lines)
    return [s.strip() for s in clean.split(";") if s.strip()]

async def run_migrations(engine: AsyncEngine) -> list[str]:
    applied_now: list[str] = []
    async with engine.begin() as conn:
        await conn.exec_driver_sql(
            "CREATE TABLE IF NOT EXISTS schema_migration "
            "(filename TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())")
        rows = await conn.execute(text("SELECT filename FROM schema_migration"))
        applied = {r[0] for r in rows.fetchall()}
    for f in sorted(CHANGELOG_DIR.glob("V*.sql")):
        if f.name in applied:
            continue
        statements = split_statements(f.read_text(encoding="utf-8"))
        async with engine.begin() as conn:
            for st in statements:
                await conn.execute(text(st))
            safe = f.name.replace("'", "''")
            await conn.execute(text(f"INSERT INTO schema_migration (filename) VALUES ('{safe}')"))
        applied_now.append(f.name)
    return applied_now
