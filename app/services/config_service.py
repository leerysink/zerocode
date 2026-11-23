import json
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# 用户级键前缀（Ticket 007）：llm.* 按账号各存一份；其余（agent.*/deploy.*/store.*）
# 维持平台级共享（owner NULL）——拷问阶段裁定。
# memory.* 走同一套（Ticket 006）：召回上限决定「往 prompt 里塞多少自己的内容」，
# 与 llm.* 一样是各人可调、不该互相影响的开关。
# clarify.* 同理（ADR-0032）：澄清模式是「问不问我的活」，被拷问的人得能自己关掉。
# 另立新前缀而不是把 agent.* 整体改成账户级——「影响全平台」这个作用域至今没变；
# 变的是写权限：自 ADR-0068 起平台级键只有管理员能写（见 set_many 的 actor_is_admin）。
# quota.* 是账号级**限额**（ADR-0068）：每账号一行，覆盖平台打底值。它是用户级前缀里的例外——
# 其余前缀是「我自己可调」，quota. 的语义是「管理员给我的额度」，写权限不收口就成了自提额。
USER_PREFIXES = ("llm.", "memory.", "clarify.", "subrun.", "quota.")

# 形态上是账号级（每账号一行、按 owner 回落），语义上却是治理权：额度是「管理员给我的上限」，
# 放开写就等于让被限的人自己改表（ADR-0068）。
GOVERNED_PREFIXES = ("quota.",)


class ConfigPermissionError(Exception):
    """越权写配置：非管理员改动了会影响全平台、或属于治理面的键。"""

    def __init__(self, keys):
        self.keys = sorted(keys)
        super().__init__("平台级配置仅管理员可修改：" + "、".join(self.keys))


def _is_user_key(k: str) -> bool:
    return k.startswith(USER_PREFIXES)


def _writable_by_caller(k: str, actor_is_admin: bool) -> bool:
    if actor_is_admin:
        return True
    return _is_user_key(k) and not k.startswith(GOVERNED_PREFIXES)


async def get_all(session: AsyncSession, owner_user_id: int | None = None) -> dict:
    """配置读取（dbcred.* 永不外显）。

    传 owner_user_id 时给出「用户视图」：平台级行（owner NULL）打底，
    用户自己的 USER_PREFIXES 行覆盖——用户行存在用之，否则回落全局（工单 007 优先级）。
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
                   owner_user_id: int | None = None,
                   *, actor_is_admin: bool = False) -> None:
    """写入按键分流：USER_PREFIXES 里的键落到 owner 行（传了账号才落），其余键写 owner NULL 平台行。

    写权限（ADR-0068，**推翻** Ticket 007「普通用户改 agent.* 影响全平台是既定取舍」）：
    非管理员只能写自己前缀内的覆盖行，碰到平台级键或 `quota.` 一律整批拒绝——
    先验权限再落笔，不存在「一半改成功了」的中间态。
    """
    denied = [k for k in values if not _writable_by_caller(k, actor_is_admin)]
    if denied:
        raise ConfigPermissionError(denied)
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
