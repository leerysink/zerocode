"""会话级模型解析（spec §4.4）：session.model_config_id → model_config 行；
无绑定/行已删 → 默认模型；无默认 → 全局 app_config llm.*（老部署零迁移可用）。"""
from sqlalchemy import text

async def resolve_model(sf, session_id: int, cfg: dict) -> dict:
    async with sf() as s:
        mid = (await s.execute(text(
            "SELECT model_config_id FROM chat_session WHERE id=:i"), {"i": session_id})).scalar()
        if mid is not None:
            row = (await s.execute(text(
                "SELECT base_url, api_key, model FROM model_config WHERE id=:i"), {"i": mid})).first()
            if row:
                return {"base_url": row[0], "api_key": row[1] or "", "model": row[2], "source": "session"}
        row = (await s.execute(text(
            "SELECT base_url, api_key, model FROM model_config WHERE is_default ORDER BY id LIMIT 1"))).first()
    if row:
        return {"base_url": row[0], "api_key": row[1] or "", "model": row[2], "source": "default"}
    return {"base_url": cfg.get("llm.base_url"), "api_key": cfg.get("llm.api_key") or "",
            "model": cfg.get("llm.model"), "source": "global"}
