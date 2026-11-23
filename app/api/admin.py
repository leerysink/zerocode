"""账号治理端点（规格 2026-11-23 / ADR-0067~0070）：`/admin` 治理台的唯一数据源。

四块职能共用一个前缀：账号名册与封禁、按人配额、平台默认、邀请码。
全部端点先过 `require_admin`——普通账号绕开前端直接调也是 403。

不提供任何改动 `is_founder` 的动作：管理员不可移交、不可增员（ADR-0067），
所以这里连一个「设管理员」的口子都没有。
"""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import text

from app.api.gates import require_admin
from app.deploy import engine, prod, quota
from app.deploy import tasks as deploy_tasks
from app.deploy.errors import DeployError
from app.services import app_service, config_service

router = APIRouter(prefix="/api/admin")

# 出库字段白名单：口令散列、会话 token、max_sessions 一概不出库（同 /api/users 的口径）。
_ACCOUNT_COLUMNS = ("id, nickname, is_founder, created_at, banned_at, banned_reason")


class BanIn(BaseModel):
    reason: str | None = None
    stop_stacks: bool = False


async def _stop_owner_stacks(request: Request, owner_id: int) -> tuple[int, int]:
    """对该账号正在跑的栈逐个下停止动作，返回 (已受理格数, 因占用而跳过的应用数)。

    复用既有停止实现与任务互斥（`deploy_tasks.spawn`），不新造停止路径。
    **一个应用只有一个后台任务位**（`tasks.py` 的互斥按 app_id 记账），所以同一个应用的
    dev 与 prod 两格必须在同一个任务里先后停——分开 spawn 的话第二发会被自己的第一发挡成 busy。
    停栈是异步受理的：端点不陪着 docker 一起等，也不假装已经停完。
    """
    sf = request.app.state.session_factory
    hub = request.app.state.deploy_hub
    async with sf() as s:
        ids = (await s.execute(text(
            "SELECT id FROM app_project WHERE owner_user_id = :u "
            "AND (status_dev = 'running' OR status_prod = 'running')"),
            {"u": owner_id})).scalars().all()

    stopped_cells = skipped_apps = 0
    for app_id in ids:
        app = await app_service.get_app(sf, app_id)
        # 属性在调用时现取，而不是在模块顶部绑死：测试替身与后续换实现都拦得住
        cells = [(engine.stop_dev, "status_dev"), (prod.prod_stop, "status_prod")]
        wanted = [(action, key) for action, key in cells if app[key] == "running"]
        if not wanted:
            continue
        try:
            deploy_tasks.spawn(
                request.app.state.dev_tasks, app_id,
                lambda a=app, w=wanted: _stop_cells(sf, hub, a, w))
            stopped_cells += len(wanted)
        except DeployError:
            skipped_apps += 1   # 那套应用上有发布/迁移在跑，等它自己收口，不硬插一刀
    return stopped_cells, skipped_apps


async def _stop_cells(sf, hub, app: dict, wanted) -> dict:
    """同一个应用的多个栈按 dev → prod 顺序停；任一失败都记进返回体，由前端如实播报。"""
    results = {}
    for action, key in wanted:
        try:
            results[key] = await action(sf, app, notify=hub.publish)
        except Exception as e:  # noqa: BLE001 —— 后台任务没有 HTTP 出口，异常必须自己收住
            results[key] = {"ok": False, "error": str(e)}
    return results


async def _load_account(sf, actor_id: int) -> dict:
    async with sf() as s:
        row = (await s.execute(text(
            f"SELECT {_ACCOUNT_COLUMNS} FROM actor WHERE id=:u"), {"u": actor_id})
        ).mappings().first()
    if row is None:
        raise HTTPException(404, "账号不存在")
    return dict(row)


@router.get("/accounts")
async def list_accounts(request: Request):
    """账号名册：谁在册、谁被封、用了多少应用与几套栈、各自的上限是多少。

    只给治理台读（`/api/users` 那份任一登录账号可读的最小名单维持不变，
    见规格 §7「另开 /api/admin/accounts，不把封禁与配额挂到公共名单上」）。

    用量是 `app_project` 的实时投影，不建计数表也不加缓存：计数口径只有一处真源
    （`quota.count_running_stacks`），多一个缓存就多一处会与库里状态说不一样的地方。
    """
    require_admin(request.state.actor, "治理台仅管理员可见")
    sf = request.app.state.session_factory
    async with sf() as s:
        rows = (await s.execute(text(
            "SELECT a.id, a.nickname, a.is_founder, a.created_at, a.banned_at, a.banned_reason, "
            "       (SELECT count(*) FROM app_project p WHERE p.owner_user_id = a.id) AS apps_used, "
            "       (SELECT count(*) FILTER (WHERE p.status_dev = 'running') "
            "              + count(*) FILTER (WHERE p.status_prod = 'running') "
            "          FROM app_project p WHERE p.owner_user_id = a.id) AS stacks_running "
            "FROM actor a ORDER BY a.id"))).mappings().all()
        out = []
        for r in rows:
            d = dict(r)
            d["apps_limit"] = await quota.resolve_limit(s, d["id"], quota.KEY_MAX_APPS)
            d["stacks_limit"] = await quota.resolve_limit(s, d["id"], quota.KEY_MAX_STACKS)
            out.append(d)
    return out


@router.put("/accounts/{actor_id}/quota")
async def put_account_quota(request: Request, actor_id: int, body: dict):
    """按人设额度：写该账号的 `quota.*` 覆盖行。

    手写校验而不用 pydantic 的 int 强转，有两个理由：
    - 值域里有真语义——`0` 是「一个都不许」（关人时要它），`null` 是「不限」，
      负数与小数才是非法。`int | None` 会把 1.5 悄悄削成 1，也会把非法输入报成 422 结构错误。
    - 一批两个键必须**全验后一次写**：不能出现「apps 改成了、stacks 因为非法没改」的半态。
    """
    require_admin(request.state.actor, "治理台仅管理员可见")
    await _load_account(request.app.state.session_factory, actor_id)

    fields = (("max_apps", quota.KEY_MAX_APPS), ("max_running_stacks", quota.KEY_MAX_STACKS))
    values: dict = {}
    for name, key in fields:
        if name not in body:
            continue          # 没带的字段不动：允许只调一把闸
        raw = body[name]
        if raw is not None and (isinstance(raw, bool) or not isinstance(raw, int) or raw < 0):
            raise HTTPException(
                400, f"{name} 需要非负整数，或 null 表示不限；收到 {raw!r}")
        values[key] = raw
    if not values:
        return {"ok": True, "written": []}

    async with request.app.state.session_factory() as s:
        await config_service.set_many(s, values, owner_user_id=actor_id, actor_is_admin=True)
        await s.commit()
    return {"ok": True, "written": sorted(values)}


@router.post("/accounts/{actor_id}/ban")
async def ban_account(request: Request, actor_id: int, body: BanIn):
    """封禁：写封禁位 + 作废全部会话。

    两件事都做，因为它们是两种保证：删会话让旧凭据彻底不存在（并腾出 max_sessions 的名额），
    而 `by_token` 上的封禁判定才是权威——它覆盖「封禁落地时仍活着的连接」，
    也让解封前不可能再开出新会话。
    """
    require_admin(request.state.actor, "治理台仅管理员可见")
    me = request.state.actor["id"]
    await _load_account(request.app.state.session_factory, actor_id)
    if actor_id == me:
        # 管理员不可移交（ADR-0067），自封 = 把整个平台锁死，后端同口径拒绝而非只藏按钮
        raise HTTPException(400, "不能封禁自己")

    reason = (body.reason or "").strip()[:200] or None
    sf = request.app.state.session_factory
    async with sf() as s:
        await s.execute(text(
            "UPDATE actor SET banned_at = now(), banned_reason = :r WHERE id = :u"),
            {"r": reason, "u": actor_id})
        await s.execute(text("DELETE FROM actor_session WHERE actor_id = :u"),
                        {"u": actor_id})
        await s.commit()
    account = await _load_account(sf, actor_id)
    # 先封后停：顺序反了会出现「栈停完的几秒里他还能点发布把栈起回来」
    if body.stop_stacks:
        stopped, skipped = await _stop_owner_stacks(request, actor_id)
        account["stacks_stopped"], account["stacks_skipped"] = stopped, skipped
    else:
        account["stacks_stopped"], account["stacks_skipped"] = 0, 0
    return account


@router.post("/accounts/{actor_id}/unban")
async def unban_account(request: Request, actor_id: int):
    """解封 = 只恢复入口：不重启任何栈、不改任何额度、不动他的数据。"""
    require_admin(request.state.actor, "治理台仅管理员可见")
    await _load_account(request.app.state.session_factory, actor_id)
    sf = request.app.state.session_factory
    async with sf() as s:
        await s.execute(text(
            "UPDATE actor SET banned_at = NULL, banned_reason = NULL WHERE id = :u"),
            {"u": actor_id})
        await s.commit()
    return await _load_account(sf, actor_id)


# ---- 平台默认（ADR-0068）：治理台是 owner NULL 行的唯一可写视图 ----

@router.get("/config")
async def get_platform_config(request: Request):
    """平台打底行视图（只看 owner NULL）：不含任何账号的覆盖行，秘密照旧掩码。"""
    require_admin(request.state.actor, "治理台仅管理员可见")
    async with request.app.state.session_factory() as s:
        return config_service.mask(await config_service.get_all(s))


@router.put("/config")
async def put_platform_config(request: Request, body: dict):
    """写平台默认：连用户级前缀（`llm.*` 等）也一律落 owner NULL 行。

    与 `PUT /api/config` 的区别只有属主：那条恒写调用者自己的覆盖行，这条恒写平台行。
    填在这里的 `llm.api_key` 由**全平台共用**——所有未自配密钥的账号都会拿它去请求。
    """
    require_admin(request.state.actor, "治理台仅管理员可见")
    # 掩码哨兵不回写：前端读到的 sk-*** 不是新值（与 /api/config 同一条纪律）
    body = {k: v for k, v in body.items() if not (k == "llm.api_key" and v == "sk-***")}
    try:
        async with request.app.state.session_factory() as s:
            await config_service.set_many(s, body, owner_user_id=None, actor_is_admin=True)
            await s.commit()
    except config_service.ConfigPermissionError as e:  # pragma: no cover —— 管理员不会越权
        raise HTTPException(403, str(e))
    return {"ok": True}
