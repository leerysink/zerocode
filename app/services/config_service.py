import json
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# 用户级键前缀（Ticket 007）：llm.* 按账号各存一份；其余（agent.*/deploy.*/store.*）
# 维持平台级共享（owner NULL）——拷问阶段裁定。
USER_PREFIXES = ("llm.",)


def _is_user_key(k: str) -> bool:
    return k.startswith(USER_PREFIXES)


async def get_all(session: AsyncSession, owner_user_id: int | None = None) -> dict:
    """配置读取（dbcred.* 永不外显）。

    传 owner_user_id 时给出「用户视图」：平台级行（owner NULL）打底，
    用户自己的 llm.* 行覆盖——用户行存在用之，否则回落全局（工单 007 优先级）。
    不传保持平台级视图（只有 owner NULL 行）。"""
    rows = (await session.execute(text(
        "SELECT key, value, owner_user_id FROM app_config "
        "WHERE key NOT LIKE 'dbcred.%' AND "
        "(owner_user_id IS NULL OR owner_user_id = :u)"),
        {"u": owner_user_id})).fetchall()
    # asyncpg 对 JSONB 默认已自动解码为 Python 对象，无需再 json.loads
    glob = {k: v for k, v, o in rows if o is None}
    mine = {k: v for k, v, o in rows if o is not None}
    return {**glob, **mine}

async def set_many(session: AsyncSession, values: dict,
                   owner_user_id: int | None = None) -> None:
    """写入按键分流（Ticket 007）：llm.* 落到 owner 行（传了账号才落），
    其余键恒写 owner NULL 平台行——普通用户改 agent.* 影响全平台是既定取舍。"""
    for k, v in values.items():
        owner = owner_user_id if _is_user_key(k) else None
        # 仲裁目标跟 ux_app_config_owner_key（Ticket 003）：owner 缺省 NULL = 平台级键
        await session.execute(text(
            "INSERT INTO app_config (key, value, owner_user_id) VALUES (:k, CAST(:v AS JSONB), :o) "
            "ON CONFLICT (owner_user_id, key) DO UPDATE SET value = CAST(:v AS JSONB), updated_at = now()"),
            {"k": k, "v": json.dumps(v, ensure_ascii=False), "o": owner})

def mask(cfg: dict) -> dict:
    out = dict(cfg)
    key = out.get("llm.api_key")
    out["llm.api_key"] = "sk-***" if key else ""
    return out
