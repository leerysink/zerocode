"""账号配额（规格 2026-11-23 / ADR-0068、ADR-0070）：额度解析与两把闸的判定。

额度住 `app_config` 的 `quota.` 前缀，读侧沿用既有的「平台默认打底 + 账号覆盖」两级回落，
不新建 quota 表、不往 `actor` 加列（ADR-0068）。

两条贯穿全模块的纪律：
1. **判定主体是应用所有者，不是当前操作者**——起的是他的栈、占的是他的资源。被共享者
   替所有者点发布（ADR-0050）时换了操作者，额度不能跟着换人算。
2. **读不出来的额度一律当「不限」**——脏值不是用户的错。宁可放行一次，不可因为一行
   写坏的 JSONB 把人永久锁死；反过来「不限」也必须是显式的（键缺失或值为 null），
   所以 0 是合法的「禁止」，不会被折叠成不限。
"""
from __future__ import annotations

from sqlalchemy import text

from app.deploy.errors import DeployError

KEY_MAX_APPS = "quota.max_apps"
KEY_MAX_STACKS = "quota.max_running_stacks"


def _sane(raw) -> int | None:
    """把落库值收敛成可用上限：非负整数才认，其余（含布尔、小数、字符串）当「没配」。"""
    if isinstance(raw, bool) or not isinstance(raw, int):
        return None
    return raw if raw >= 0 else None


async def resolve_limit(session, actor_id: int | None, key: str) -> int | None:
    """按账号取上限：管理员恒不限（ADR-0070），其余走「账号行 → 平台行 → None」回落。

    一次查询取回判定所需的全部事实，避免「权限看一次、读值看一次」中间被改动。
    落库值是 JSON `null` 时判成**不限**而不是回落平台值：那是显式的「这一档不设上限」，
    与「这个账号没有覆盖行」是两件事——后者才回落。
    """
    if actor_id is None:
        return None   # 无主路径（预置/内部调用）不套按人额度
    row = (await session.execute(text(
        "SELECT a.is_founder, (own.key IS NOT NULL) AS own_present, own.value AS own_value, "
        "plt.value AS platform_value "
        "FROM actor a "
        "LEFT JOIN app_config own ON own.key = :k AND own.owner_user_id = a.id "
        "LEFT JOIN app_config plt ON plt.key = :k AND plt.owner_user_id IS NULL "
        "WHERE a.id = :u"), {"k": key, "u": actor_id})).mappings().first()
    if row is None or row["is_founder"]:
        return None
    raw = row["own_value"] if row["own_present"] else row["platform_value"]
    return _sane(raw)


async def count_apps(session, owner_id: int) -> int:
    """名下应用行数。商店安装的实例也是 app_project 行，因此天然计入（不另开第三把闸）。"""
    return (await session.execute(text(
        "SELECT count(*) FROM app_project WHERE owner_user_id = :u"),
        {"u": owner_id})).scalar() or 0


async def ensure_app_creation_allowed(session_factory, owner_user_id: int | None) -> None:
    """创建闸：名下应用行数已达上限即拒。

    必须在分配端口**之前**调用：端口段是全局共享的稀缺资源（约 94 对，不分辨账号），
    先占再拒等于让每次越限尝试白吃一格。
    """
    if owner_user_id is None:
        return
    async with session_factory() as s:
        limit = await resolve_limit(s, owner_user_id, KEY_MAX_APPS)
        if limit is None:
            return
        used = await count_apps(s, owner_user_id)
    if used < limit:
        return
    raise DeployError(
        f"应用数已达上限 {limit}（当前 {used}），如需调整请联系管理员", status=409)


async def count_running_stacks(session, owner_id: int) -> int:
    """运行中的容器栈数：dev 与 prod **各算一格**（CONTEXT「实例」）。

    读的是 `app_project` 的状态列而不是 `docker ps`——它是平台记录，可能与宿主实况有偏差
    （容器被外部杀掉时库里仍写 running）。治理读数同口径，界面上不宣称是宿主实况。
    """
    return (await session.execute(text(
        "SELECT count(*) FILTER (WHERE status_dev = 'running') "
        "     + count(*) FILTER (WHERE status_prod = 'running') "
        "FROM app_project WHERE owner_user_id = :u"), {"u": owner_id})).scalar() or 0


async def ensure_can_start_stack(session_factory, app_id: int, env: str) -> None:
    """启动闸：这次动作若会**净增**一套 running 栈且已达上限，即拒。

    三条不显然的规则都在这一个函数里（ADR-0070）：
    1. **主体是应用所有者**，不是当前操作者——被共享者替所有者起栈占的是所有者的额度，
       管理员豁免也按所有者判，否则「普通账号替管理员点一下」就成了绕行面。
    2. **净增才占额**：本次要起的那一格已经是 running 就不新增占用。否则刚好跑满额度的人
       连重启自己已有的栈都做不到——那是锁死，不是治理。
    3. 归属为 NULL（预置应用的存量态）不套按人额度。
    """
    async with session_factory() as s:
        app = (await s.execute(text(
            "SELECT owner_user_id, status_dev, status_prod FROM app_project WHERE id = :i"),
            {"i": app_id})).mappings().first()
        if app is None or app["owner_user_id"] is None:
            return
        limit = await resolve_limit(s, app["owner_user_id"], KEY_MAX_STACKS)
        if limit is None:
            return
        cell = "status_dev" if env == "dev" else "status_prod"
        if app[cell] == "running":
            return
        used = await count_running_stacks(s, app["owner_user_id"])
        if used >= limit:
            raise DeployError(
                f"运行中的容器栈已达上限 {limit}（当前 {used}），请先停止一个", status=409)
