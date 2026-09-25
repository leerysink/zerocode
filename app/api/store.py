# app/api/store.py
"""应用商店 REST（规格 M3-a §8）。DeployError.status 直接映射 HTTP 状态码，与 apps.py 同纪律。

两套前缀放一个文件：`/api/store/*` 是商店本体，`/api/apps/{id}/release` 是应用侧的上架入口，
后者复用 apps 的鉴权/闸门语境，但实现全在 app/store/*，这里仍是薄层。

身份（Ticket 008 合流 / ADR-0003）：actor 即登录用户——全局 guard（main.py）已把无会话请求
拦成 401 并把 actor 挂上 request.state；匿名 identify 建号端点已退役，前端的 401 统一
由 axios 拦截器引导到登录页，不再弹昵称框。
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import text
from app.deploy.errors import DeployError
from app.store import release as release_svc
from app.store import applications as apps_svc

router = APIRouter(prefix="/api/store")
apps_ext = APIRouter(prefix="/api/apps")


def _http(e: DeployError):
    return HTTPException(e.status, str(e))


async def current_actor(request: Request) -> dict:
    """身份依赖：直接取 guard 挂上的登录主体（Ticket 008 后与 /api/auth/* 同一人）。

    能走到这里的请求必已过全局 guard（无会话根本进不来），所以不再二次 by_token。
    """
    return request.state.actor


Actor = Depends(current_actor)


@router.get("/me")
async def me(actor: dict = Actor):
    return {"id": actor["id"], "nickname": actor["nickname"]}


@router.get("/releases")
async def list_releases(request: Request, actor: dict = Actor):
    """商店列表：每个 slug 只露最新 published 版本（申请人不需要翻历史）。

    installed 标记按当前 actor 的实例算：装过的版本卡片上显示「已安装」而非再给一个申请按钮。
    """
    sf = request.app.state.session_factory
    async with sf() as s:
        rows = (await s.execute(text(
            "SELECT r.id, r.app_id, r.slug, r.version, r.title, r.summary, r.stack, "
            "r.fingerprint, r.created_at, a.nickname AS publisher, "
            "(SELECT count(*) FROM release_application q WHERE q.release_id = r.id "
            " AND q.status <> 'cancelled') AS applied_count "
            "FROM app_release r "
            "LEFT JOIN actor a ON a.id = r.created_by "
            "WHERE r.status = 'published' "
            "AND r.id = (SELECT max(id) FROM app_release WHERE slug = r.slug "
            "            AND status = 'published') "
            "ORDER BY r.id DESC"))).mappings().all()
        mine = (await s.execute(text(
            "SELECT l.release_id, p.id AS app_id, p.name, p.status_prod, p.prod_port AS port "
            "FROM app_instance_link l JOIN app_project p ON p.id = l.app_id "
            "WHERE l.owner_actor = :a"), {"a": actor["id"]})).mappings().all()
    installed = {}
    for m in mine:
        installed.setdefault(m["release_id"], []).append(dict(m))
    out = []
    for r in rows:
        d = dict(r)
        d["created_at"] = d["created_at"].isoformat() if d.get("created_at") else None
        d["installed_apps"] = installed.get(d["id"], [])
        d["installed"] = bool(d["installed_apps"])
        d["updated_count"] = int(d.pop("applied_count"))   # 历史字段名保留给前端角标语义
        out.append(d)
    return out


class ReleaseIn(BaseModel):
    title: str
    summary: str = ""


@apps_ext.post("/{app_id}/release", status_code=201)
async def publish_to_store(request: Request, app_id: int, body: ReleaseIn,
                          actor: dict = Actor):
    """上架是长任务吗？不是：五道闸门 + 拷文件 + 一行插入，秒级完成，同步返回 201。

    刻意不套 `_spawn` 单任务闸门：它不碰容器，与 dev/prod 生命周期任务不互踩。
    """
    sf = request.app.state.session_factory
    try:
        return await release_svc.create_release(
            sf, app_id=app_id, title=body.title, summary=body.summary,
            actor_id=actor["id"])
    except DeployError as e:
        raise _http(e)


@router.post("/releases/{release_id}/withdraw")
async def withdraw(request: Request, release_id: int, actor: dict = Actor):
    """下架：只停止新申请，已装实例与升级线不受影响（规格 §6.6）。"""
    sf = request.app.state.session_factory
    async with sf() as s:
        row = (await s.execute(text(
            "SELECT r.id, r.created_by FROM app_release r WHERE r.id = :i"),
            {"i": release_id})).mappings().first()
        if not row:
            raise _http(DeployError("版本不存在", status=404))
        if row["created_by"] != actor["id"]:
            raise _http(DeployError("只能下架自己发布的版本", status=403))
        await s.execute(text("UPDATE app_release SET status='withdrawn' WHERE id=:i"),
                        {"i": release_id})
        await s.commit()
    return {"ok": True}


class ApplyIn(BaseModel):
    release_id: int
    want_name: str


class DecideIn(BaseModel):
    approve: bool
    reason: str = ""


@router.post("/applications", status_code=201)
async def apply(request: Request, body: ApplyIn, actor: dict = Actor):
    sf = request.app.state.session_factory
    try:
        return await apps_svc.create(sf, release_id=body.release_id,
                                     applicant_id=actor["id"], want_name=body.want_name)
    except DeployError as e:
        raise _http(e)


@router.get("/applications")
async def list_applications(request: Request, box: str = "mine", actor: dict = Actor):
    sf = request.app.state.session_factory
    try:
        return await apps_svc.list_box(sf, box=box, actor_id=actor["id"])
    except DeployError as e:
        raise _http(e)


@router.post("/applications/{aid}/approve", status_code=202)
async def approve(request: Request, aid: int, body: DecideIn, actor: dict = Actor):
    """批准 = 起后台安装（202）。长任务闸门复用 apps 的同应用单任务语义：
    安装会占一个新 app_id，但受理时还没有它，所以这里按 release 的**来源应用** id 挡互斥
    （语义是「同一来源应用的发布/安装串行」，不为这个改 `_spawn` 的签名）。"""
    from app.api.apps import _spawn   # 复用同一套单任务闸门
    sf = request.app.state.session_factory
    hub = request.app.state.deploy_hub
    try:
        a = await apps_svc.decide(sf, app_id=aid, approver_id=actor["id"],
                                  approve=body.approve, reason=body.reason)
        if not body.approve:
            return a
        from app.store import install
        _spawn(request, a["source_app_id"],
               lambda: install.run_install_for_application(sf, a, notify=hub.publish))
    except DeployError as e:
        raise _http(e)
    return {"ok": True, "application": a}


@router.post("/applications/{aid}/reject", status_code=200)
async def reject(request: Request, aid: int, body: DecideIn, actor: dict = Actor):
    sf = request.app.state.session_factory
    try:
        return await apps_svc.decide(sf, app_id=aid, approver_id=actor["id"],
                                     approve=False, reason=body.reason)
    except DeployError as e:
        raise _http(e)


@router.get("/link/{app_id}")
async def store_link(request: Request, app_id: int, actor: dict = Actor):
    """单实例的商店绑定详情。get_app 对不存在的应用抛 DeployError(404)，这里必须接住
    换成 HTTP 404（无全局 handler，漏接就是 500）。"""
    sf = request.app.state.session_factory
    from app.services import app_service
    try:
        app = await app_service.get_app(sf, app_id, owner_user_id=actor["id"])
        if not app.get("store"):
            raise DeployError("该应用不是商店实例", status=404)
    except DeployError as e:
        raise _http(e)
    # M3-b 的「可升级版本」在此接口补出，本期先给当前绑定与通道
    return {"app_id": app_id, **app["store"], "upgradable": False}
