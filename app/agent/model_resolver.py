"""会话级模型解析（spec §4.4）：session.model_config_id → model_config 行；
无绑定/行已删 → 默认模型；无默认 → 全局 app_config llm.*（老部署零迁移可用）。

隔离（Ticket 007，行为关键链）：会话经 workspace 取 owner，绑定行与默认行都按
owner 过滤——别人的档案即使被旁路直绑也读不到（落回自己的默认）；
IS NOT DISTINCT FROM 兼容无主遗留行（老库未承接前 uid 为 NULL）。

resolve_model_for_user 是「按账号」那两级回落的唯一实现：会话链路经 resolve_model 调它，
技能 AI 生成链路（app/api/skills.py）在已知 uid 时直接调它——回落逻辑不写平行实现。"""
from sqlalchemy import text


async def resolve_model_for_user(s, uid, cfg: dict) -> dict:
    """owner 默认模型行 → 全局 app_config llm.*。s 必须是已打开的会话。"""
    row = (await s.execute(text(
        "SELECT base_url, api_key, model FROM model_config "
        "WHERE is_default AND owner_user_id IS NOT DISTINCT FROM :o "
        "ORDER BY id LIMIT 1"), {"o": uid})).first()
    if row:
        return {"base_url": row[0], "api_key": row[1] or "", "model": row[2], "source": "default"}
    return {"base_url": cfg.get("llm.base_url"), "api_key": cfg.get("llm.api_key") or "",
            "model": cfg.get("llm.model"), "source": "global"}


async def resolve_model(sf, session_id: int, cfg: dict) -> dict:
    async with sf() as s:
        uid = (await s.execute(text(
            "SELECT w.owner_user_id FROM chat_session cs "
            "JOIN workspace w ON w.id = cs.workspace_id WHERE cs.id=:i"),
            {"i": session_id})).scalar()
        mid = (await s.execute(text(
            "SELECT model_config_id FROM chat_session WHERE id=:i"), {"i": session_id})).scalar()
        if mid is not None:
            row = (await s.execute(text(
                "SELECT base_url, api_key, model FROM model_config "
                "WHERE id=:i AND owner_user_id IS NOT DISTINCT FROM :o"),
                {"i": mid, "o": uid})).first()
            if row:
                return {"base_url": row[0], "api_key": row[1] or "", "model": row[2], "source": "session"}
        return await resolve_model_for_user(s, uid, cfg)
