import json
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

async def get_all(session: AsyncSession) -> dict:
    rows = (await session.execute(text(
        "SELECT key, value FROM app_config WHERE key NOT LIKE 'dbcred.%'"))).fetchall()
    # asyncpg 对 JSONB 默认已自动解码为 Python 对象，无需再 json.loads
    return {k: v for k, v in rows}

async def set_many(session: AsyncSession, values: dict) -> None:
    for k, v in values.items():
        await session.execute(text(
            "INSERT INTO app_config (key, value) VALUES (:k, CAST(:v AS JSONB)) "
            "ON CONFLICT (key) DO UPDATE SET value = CAST(:v AS JSONB), updated_at = now()"),
            {"k": k, "v": json.dumps(v, ensure_ascii=False)})

def mask(cfg: dict) -> dict:
    out = dict(cfg)
    key = out.get("llm.api_key")
    out["llm.api_key"] = "sk-***" if key else ""
    return out
