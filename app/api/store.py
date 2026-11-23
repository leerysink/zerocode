# app/api/store.py
"""应用商店 REST（规格 M3-a §8）。DeployError.status 直接映射 HTTP 状态码，与 apps.py 同纪律。

两套前缀放一个文件：`/api/store/*` 是商店本体，`/api/apps/{id}/release` 是应用侧的上架入口，
后者复用 apps 的鉴权/闸门语境，但实现全在 app/store/*，这里仍是薄层。

身份（Ticket 008 合流 / ADR-0003）：actor 即登录用户——全局 guard（main.py）已把无会话请求
拦成 401 并把 actor 挂上 request.state；匿名 identify 建号端点已退役，前端的 401 统一
由 axios 拦截器引导到登录页，不再弹昵称框。
"""
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from app.deploy.errors import DeployError
from app.deploy.settings import preview_url
from app.api.gates import require_admin
from app.store import release as release_svc
from app.store import applications as apps_svc
from app.store import categories
from app.store import links
from app.store import shots

router = APIRouter(prefix="/api/store")
apps_ext = APIRouter(prefix="/api/apps")


def _http(e: DeployError):
    return HTTPException(e.status, str(e))


def _require_founder(actor: dict) -> None:
    """运营台门禁：商店管理与分类字典的写操作只对创始人开放（ADR-0061）。

    谓词与治理台共用 `gates.is_admin`（ADR-0067：管理员真身就是 is_founder）；
    文案原样保留——`StoreAdminView` 与它的测试钉着这句。
    """
    require_admin(actor, "商店管理仅创始人可见")


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
async def list_releases(request: Request, actor: dict = Actor,
                        q: str = "", category: str = "", sort: str = "latest"):
    """商店列表：每个 slug 只露最新 published 版本（申请人不需要翻历史）。

    installed 标记按当前 actor 的实例算：装过的版本卡片上显示「已安装」而非再给一个申请按钮。
    q 命中标题/简介（ILIKE）；category 精确过滤（空＝全部，非法 400）；sort=installs 按安装量降序。
    """
    if sort not in ("latest", "installs"):
        raise _http(DeployError("sort 只支持 latest|installs", status=400))
    sf = request.app.state.session_factory
    where = ["r.status = 'published'",
             "r.id = (SELECT max(id) FROM app_release WHERE slug = r.slug "
             "AND status = 'published')"]
    params: dict = {}
    if q.strip():
        where.append("(r.title ILIKE :q OR COALESCE(r.summary, '') ILIKE :q)")
        params["q"] = f"%{q.strip()}%"
    if category:
        try:
            # 过滤口径认停用分类（存量条目还挂着它），写入口径只认启用项——两套语义见 categories.py
            params["cat"] = await categories.normalize_filter(sf, category)
        except DeployError as e:
            raise _http(e)
        where.append("COALESCE(r.category, '其他') = :cat")
    order = "install_count DESC, r.id DESC" if sort == "installs" else "r.id DESC"
    async with sf() as s:
        rows = (await s.execute(text(
            "SELECT r.id, r.app_id, r.slug, r.version, r.title, r.summary, r.stack, r.category, "
            "r.screenshots, r.created_by, "
            "r.fingerprint, r.created_at, a.nickname AS publisher, "
            "src.status_dev AS src_status_dev, src.dev_port AS src_dev_port, "
            "(SELECT count(*) FROM release_application q WHERE q.release_id = r.id "
            " AND q.status = 'approved') AS install_count "
            "FROM app_release r "
            "LEFT JOIN actor a ON a.id = r.created_by "
            "LEFT JOIN app_project src ON src.id = r.app_id "
            f"WHERE {' AND '.join(where)} ORDER BY {order}"), params)).mappings().all()
        mine = (await s.execute(text(
            "SELECT l.release_id, p.id AS app_id, p.name, p.slug, p.status_prod, p.prod_port AS port "
            "FROM app_instance_link l JOIN app_project p ON p.id = l.app_id "
            "WHERE l.owner_actor = :a"), {"a": actor["id"]})).mappings().all()
    installed = {}
    from app.deploy import nics
    for m in mine:
        d = dict(m)
        d["prod_url"] = nics.app_url({"slug": d["slug"], "prod_port": d["port"]})
        installed.setdefault(d["release_id"], []).append(d)
    out = []
    for r in rows:
        d = dict(r)
        d["created_at"] = d["created_at"].isoformat() if d.get("created_at") else None
        d["installed_apps"] = installed.get(d["id"], [])
        d["installed"] = bool(d["installed_apps"])
        d["install_count"] = int(d["install_count"])   # 批准安装次数（卸载不回扣）
        d["screenshots"] = shots.shot_urls(d["id"], d["screenshots"])
        # 在线试用（ADR-0057）：只认来源应用 dev 栈 running，地址复用 preview_url 投影
        dev_port = d.pop("src_dev_port")
        trial_on = d.pop("src_status_dev") == "running" and bool(dev_port)
        d["trial_available"] = trial_on
        d["trial_url"] = preview_url(dev_port) if trial_on else ""
        out.append(d)
    return out


@router.get("/admin/releases")
async def admin_releases(request: Request, actor: dict = Actor, status: str = ""):
    """商店管理列表（仅创始人）：含 withdrawn 的全量版本行，管理页按 status 切 tab。

    与发现页「每 slug 只露最新 published」刻意不同：管理要看每一行的状态，
    故全量行按 id 倒序；预览地址优先 prod 在服，回落 dev 在服。
    """
    _require_founder(actor)
    if status not in ("", "published", "withdrawn"):
        raise _http(DeployError("status 只支持 published|withdrawn", status=400))
    sf = request.app.state.session_factory
    where = ""
    params: dict = {}
    if status:
        where = "WHERE r.status = :st"
        params["st"] = status
    async with sf() as s:
        rows = (await s.execute(text(
            "SELECT r.id, r.app_id, r.slug, r.version, r.title, r.summary, r.stack, r.category, "
            "r.status, r.screenshots, r.created_at, r.updated_at, r.updated_by, "
            "a.nickname AS publisher, ed.nickname AS editor, "
            "src.status_dev AS src_status_dev, src.dev_port AS src_dev_port, "
            "src.status_prod AS src_status_prod, src.prod_port AS src_prod_port, "
            "(SELECT count(*) FROM release_application q WHERE q.release_id = r.id "
            " AND q.status = 'approved') AS install_count, "
            "(SELECT count(*) FROM release_application q WHERE q.release_id = r.id) "
            " AS application_count, "
            "(SELECT count(*) FROM app_release c WHERE c.prev_release_id = r.id) "
            " AS child_release_count, "
            "(SELECT count(*) FROM app_instance_link l WHERE l.release_id = r.id) "
            " AS instance_link_count, "
            # 能不能硬删由服务端判，UI 只照着置灰：让前端自己加总四个数迟早加错一个
            "((SELECT count(*) FROM release_application q WHERE q.release_id = r.id) = 0 "
            " AND (SELECT count(*) FROM app_release c WHERE c.prev_release_id = r.id) = 0 "
            " AND (SELECT count(*) FROM app_instance_link l WHERE l.release_id = r.id) = 0) "
            " AS deletable "
            "FROM app_release r "
            "LEFT JOIN actor a ON a.id = r.created_by "
            "LEFT JOIN actor ed ON ed.id = r.updated_by "
            "LEFT JOIN app_project src ON src.id = r.app_id "
            f"{where} ORDER BY r.id DESC"), params)).mappings().all()
    from app.deploy import nics
    out = []
    for r in rows:
        d = dict(r)
        d["created_at"] = d["created_at"].isoformat() if d.get("created_at") else None
        d["updated_at"] = d["updated_at"].isoformat() if d.get("updated_at") else None
        d["install_count"] = int(d["install_count"])
        d["application_count"] = int(d["application_count"])
        d["child_release_count"] = int(d["child_release_count"])
        d["screenshots"] = shots.shot_urls(d["id"], d["screenshots"])
        st_dev, dev_port = d.pop("src_status_dev"), d.pop("src_dev_port")
        st_prod, prod_port = d.pop("src_status_prod"), d.pop("src_prod_port")
        if st_prod == "running" and prod_port:
            d["preview_url"] = nics.app_url({"slug": d["slug"], "prod_port": prod_port})
        elif st_dev == "running" and dev_port:
            d["preview_url"] = preview_url(dev_port)
        else:
            d["preview_url"] = ""
        out.append(d)
    return out


@router.post("/releases/{release_id}/screenshots", status_code=201)
async def add_screenshot(request: Request, release_id: int,
                         file: UploadFile = File(...), actor: dict = Actor):
    sf = request.app.state.session_factory
    try:
        data = await file.read(shots.MAX_BYTES + 1)
        return await shots.add_shot(sf, release_id=release_id, actor_id=actor["id"], data=data)
    except DeployError as e:
        raise _http(e)


@router.delete("/releases/{release_id}/screenshots/{index}")
async def del_screenshot(request: Request, release_id: int, index: int, actor: dict = Actor):
    sf = request.app.state.session_factory
    try:
        return await shots.remove_shot(sf, release_id=release_id, actor_id=actor["id"], index=index)
    except DeployError as e:
        raise _http(e)


@router.get("/releases/{release_id}/screenshots/{index}")
async def get_screenshot(request: Request, release_id: int, index: int):
    """登录守卫内的文件读（全局 guard 已挡匿名）；不挂公共静态目录。"""
    sf = request.app.state.session_factory
    try:
        p = await shots.shot_path(sf, release_id=release_id, index=index)
    except DeployError as e:
        raise _http(e)
    return FileResponse(p)


class ReleaseIn(BaseModel):
    title: str
    summary: str = ""
    category: str | None = None


@apps_ext.post("/{app_id}/release", status_code=201)
async def publish_to_store(request: Request, app_id: int, body: ReleaseIn,
                          actor: dict = Actor):
    """上架是长任务吗？不是：五道闸门 + 拷文件 + 一行插入，秒级完成，同步返回 201。

    刻意不套 `_spawn` 单任务闸门：它不碰容器，与 dev/prod 生命周期任务不互踩。
    """
    sf = request.app.state.session_factory
    from app.services import app_service
    try:
        # 分类校验是全链最便宜的闸（规格 §4.8）：先于所有权与五道闸门，错分类直接 400
        category = await categories.normalize(sf, body.category)
        # 上架仅所有者（规格 2026-11-16 §5.4）：release 服务按 id 直取应用，判权必须在这里先过接缝
        _app, role = await app_service.get_app_for_user(sf, app_id, actor["id"])
        app_service.require_owner(role)
        return await release_svc.create_release(
            sf, app_id=app_id, title=body.title, summary=body.summary,
            category=category, actor_id=actor["id"])
    except DeployError as e:
        raise _http(e)


class UpgradeIn(BaseModel):
    to_release_id: int


class ForkIn(BaseModel):
    choice: str


@apps_ext.post("/{app_id}/store/fork")
async def fork_store_instance(request: Request, app_id: int, body: ForkIn, actor: dict = Actor):
    """fork 问人（M3-a §6.5）：保留定制（detached）或丢弃定制回官方（库保留）。"""
    from app.api.apps import _reject_if_task_running
    from app.services import app_service
    from app.store import baseline
    sf = request.app.state.session_factory
    try:
        _reject_if_task_running(request, app_id)
        app, role = await app_service.get_app_for_user(sf, app_id, actor["id"])
        app_service.require_owner(role)
        if not app.get("store"):
            raise DeployError("该应用不是商店实例", status=404)
        return await baseline.resolve_fork(sf, app, body.choice)
    except DeployError as e:
        raise _http(e)


@apps_ext.post("/{app_id}/store/upgrade", status_code=202)
async def upgrade_store_instance(request: Request, app_id: int, body: UpgradeIn,
                                 actor: dict = Actor):
    """手动升级（M3-a §6.4）：受理即 202，进度走 deploy WS；同应用单任务闸门。"""
    from app.api.apps import _spawn, _reject_if_task_running
    from app.services import app_service
    from app.store import upgrade
    sf = request.app.state.session_factory
    hub = request.app.state.deploy_hub
    try:
        _reject_if_task_running(request, app_id)
        app, role = await app_service.get_app_for_user(sf, app_id, actor["id"])
        app_service.require_owner(role)
        if not app.get("store"):
            raise DeployError("该应用不是商店实例", status=404)
        _spawn(request, app_id, lambda: upgrade.run_upgrade(
            sf, app=app, to_release_id=body.to_release_id, notify=hub.publish))
    except DeployError as e:
        raise _http(e)
    return {"ok": True}


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
        if row["created_by"] != actor["id"] and not actor.get("is_founder"):
            raise _http(DeployError("只能下架自己发布的版本（创始人可代管）", status=403))
        await s.execute(text("UPDATE app_release SET status='withdrawn' WHERE id=:i"),
                        {"i": release_id})
        await s.commit()
    return {"ok": True}


@router.post("/releases/{release_id}/republish")
async def republish(request: Request, release_id: int, actor: dict = Actor):
    """重新上架：withdrawn → published。所有者管自己的版本，创始人商店管理可代管。"""
    sf = request.app.state.session_factory
    async with sf() as s:
        row = (await s.execute(text(
            "SELECT r.id, r.created_by, r.status FROM app_release r WHERE r.id = :i"),
            {"i": release_id})).mappings().first()
        if not row:
            raise _http(DeployError("版本不存在", status=404))
        if row["created_by"] != actor["id"] and not actor.get("is_founder"):
            raise _http(DeployError("只能重新上架自己发布的版本（创始人可代管）", status=403))
        if row["status"] != "withdrawn":
            raise _http(DeployError("只有已下架的版本能重新上架", status=409))
        await s.execute(text("UPDATE app_release SET status='published' WHERE id=:i"),
                        {"i": release_id})
        await s.commit()
    return {"ok": True}


# 运营编辑的字段面固定为描述性元数据（ADR-0061 §1）：slug/version 是安装标识与包目录名、
# stack 决定栈、status 是审批状态机、app_id 是所有权——放开任一条都牵连已装实例与一致性双轨。
_OPS_EDITABLE = ("title", "summary", "category")


@router.patch("/releases/{release_id}")
async def ops_edit(request: Request, release_id: int, actor: dict = Actor):
    """运营编辑（仅创始人）：改已上架条目的文案与分类，不新增版本行、不重走审批。

    白名单外的字段出现即 400，不静默忽略——否则运营会以为自己也把 slug 改了。
    """
    sf = request.app.state.session_factory
    # 权限最先（同 withdraw/republish 的口径）：非创始人连字段校验都不该走到
    _require_founder(actor)
    try:
        body = await request.json()
    except Exception:
        raise _http(DeployError("请求体必须是 JSON 对象", status=400))
    if not isinstance(body, dict):
        raise _http(DeployError("请求体必须是 JSON 对象", status=400))
    unknown = sorted(set(body) - set(_OPS_EDITABLE))
    if unknown:
        raise _http(DeployError(
            f"这些字段不可编辑：{'/'.join(unknown)}（可编辑：{'/'.join(_OPS_EDITABLE)}）", status=400))
    if not body:
        raise _http(DeployError("没有要改的字段", status=400))
    try:
        patch: dict = {}
        if "title" in body:
            title = (body["title"] or "").strip()
            if not title:
                raise DeployError("标题不能为空", status=400)
            if len(title) > 100:
                raise DeployError("标题最长 100 字", status=400)
            patch["title"] = title
        if "summary" in body:
            patch["summary"] = (body["summary"] or "").strip()
        # 键取自 _OPS_EDITABLE 白名单，拼进 SET 子句的不是用户输入
        async with sf() as s:
            row = (await s.execute(text(
                "SELECT id, category FROM app_release WHERE id = :i"),
                {"i": release_id})).mappings().first()
            if not row:
                raise DeployError("版本不存在", status=404)
            if "category" in body:
                # allow 传当前值：分类被停用后，挂着它的条目还得改得动标题（否则停用=锁死编辑）
                patch["category"] = await categories.normalize(
                    sf, body["category"], allow=row["category"])
            sets = ", ".join(f"{k} = :{k}" for k in patch)
            saved = (await s.execute(text(
                f"UPDATE app_release SET {sets}, updated_at = now(), updated_by = :u "
                "WHERE id = :i "
                "RETURNING id, title, summary, category, status, updated_at"),
                {**patch, "u": actor["id"], "i": release_id})).mappings().first()
            await s.commit()
    except DeployError as e:
        raise _http(e)
    return dict(saved)


@router.delete("/releases/{release_id}")
async def purge(request: Request, release_id: int, actor: dict = Actor):
    """硬删（ADR-0061 §3）：只删没人引用过的版本行，有引用一律 409 并把计数码回去。

    app_release(id) 被 prev_release_id / release_application.release_id /
    app_instance_link.release_id 三处 NOT NULL 引用且都没有 ON DELETE CASCADE：
    放开级联等于运营一次误点就抹掉别人的申请历史与实例溯源，所以计数先查、非空即拒。
    已下架（withdrawn）的行同样受这道约束——状态不是免死金牌。
    """
    sf = request.app.state.session_factory
    _require_founder(actor)
    async with sf() as s:
        row = (await s.execute(text(
            "SELECT "
            "(SELECT count(*) FROM app_release c WHERE c.prev_release_id = r.id) AS child_count, "
            "(SELECT count(*) FROM release_application q WHERE q.release_id = r.id) AS app_count, "
            "(SELECT count(*) FROM release_application q WHERE q.release_id = r.id "
            " AND q.status = 'approved') AS approved_count, "
            "(SELECT count(*) FROM app_instance_link l WHERE l.release_id = r.id) AS link_count "
            "FROM app_release r WHERE r.id = :i"), {"i": release_id})).mappings().first()
        if not row:
            raise _http(DeployError("版本不存在", status=404))
        # 三个「被用过」的口径分开数：install_count 与列表同口径（批准的安装申请），
        # instance_link_count 是真实存在的实例链接，两者不是一回事，混报会让运营看不懂拒绝原因
        refs = {"child_release_count": row["child_count"], "application_count": row["app_count"],
                "install_count": row["approved_count"], "instance_link_count": row["link_count"]}
        if any(refs.values()):
            raise _http(DeployError(
                f"这条版本被引用着（{refs['install_count']} 次安装、"
                f"{refs['application_count']} 条申请、{refs['instance_link_count']} 个已装实例链接、"
                f"{refs['child_release_count']} 个后继版本），"
                "不能硬删；请先下架，让它停止接受新申请", status=409))
        try:
            await s.execute(text("DELETE FROM app_release WHERE id = :i"), {"i": release_id})
            await s.commit()
        except IntegrityError:
            # 上面查完到真删之间被人抢先装上/申请了：FK 会拦下来，这里翻成同一句 409 口径，
            # 不把数据库报错原文甩给运营。
            await s.rollback()
            raise _http(DeployError("这条版本刚被引用，未删除；请刷新后改走下架", status=409)) from None
    return {"ok": True, **refs}


class CategoryIn(BaseModel):
    key: str
    label: str = ""
    sort: int = 0


class CategoryPatchIn(BaseModel):
    label: str | None = None
    sort: int | None = None
    enabled: bool | None = None


@router.get("/categories")
async def list_categories(request: Request):
    """分类字典全量（含停用）：登录即可读。

    管理侧要看停用项上还挂着多少存量条目，所以不在这里过滤 enabled；
    上架与运营编辑的可选面由前端按 `enabled` 自己收窄（ADR-0061 §4）。
    """
    return await categories.all_rows(request.app.state.session_factory)


@router.post("/categories", status_code=201)
async def create_category(request: Request, body: CategoryIn, actor: dict = Actor):
    _require_founder(actor)
    sf = request.app.state.session_factory
    try:
        return await categories.create(sf, key=body.key, label=body.label, sort=body.sort)
    except DeployError as e:
        raise _http(e)


@router.patch("/categories/{key}")
async def patch_category(request: Request, key: str, body: CategoryPatchIn, actor: dict = Actor):
    """改名称/排序/启停。key 改不了：它是条目里存的值，改 key 等于批量改存量条目。"""
    _require_founder(actor)
    sf = request.app.state.session_factory
    try:
        return await categories.update(sf, key=key, label=body.label, sort=body.sort,
                                       enabled=body.enabled)
    except DeployError as e:
        raise _http(e)


@router.delete("/categories/{key}")
async def delete_category(request: Request, key: str, actor: dict = Actor):
    """只删没条目引用的分类；「其他」是兜底值，任何情况下都不给删。"""
    _require_founder(actor)
    sf = request.app.state.session_factory
    try:
        return await categories.remove(sf, key=key)
    except DeployError as e:
        raise _http(e)


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
        return await apps_svc.list_box(sf, box=box, actor_id=actor["id"],
                                       is_founder=bool(actor.get("is_founder")))
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
                                  approver_is_founder=bool(actor.get("is_founder")),
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
                                     approver_is_founder=bool(actor.get("is_founder")),
                                     approve=False, reason=body.reason)
    except DeployError as e:
        raise _http(e)


@router.post("/applications/{aid}/cancel", status_code=200)
async def cancel_application(request: Request, aid: int, actor: dict = Actor):
    sf = request.app.state.session_factory
    try:
        return await apps_svc.cancel(sf, app_id=aid, actor_id=actor["id"])
    except DeployError as e:
        raise _http(e)


@router.get("/link/{app_id}")
async def store_link(request: Request, app_id: int, actor: dict = Actor):
    """单实例的商店绑定详情。get_app 对不存在的应用抛 DeployError(404)，这里必须接住
    换成 HTTP 404（无全局 handler，漏接就是 500）。"""
    sf = request.app.state.session_factory
    from app.services import app_service
    from app.store import baseline
    try:
        app, role = await app_service.get_app_for_user(sf, app_id, actor["id"])
        app_service.require_owner(role)   # 商店绑定不对被共享者开放（规格 §5.4 仅 owner）
        if not app.get("store"):
            raise DeployError("该应用不是商店实例", status=404)
        return await baseline.upgrade_info(sf, app)
    except DeployError as e:
        raise _http(e)


@router.post("/link/{app_id}/unlink")
async def unlink_instance(request: Request, app_id: int, actor: dict = Actor):
    """解绑：断 link + 清 origin，应用转普通自有（内容全留）。"""
    from app.api.apps import _reject_if_task_running
    sf = request.app.state.session_factory
    try:
        _reject_if_task_running(request, app_id)
        return await links.unlink(sf, app_id=app_id, actor_id=actor["id"])
    except DeployError as e:
        raise _http(e)


@router.delete("/link/{app_id}")
async def uninstall_instance(request: Request, app_id: int, actor: dict = Actor):
    """卸载：停栈 → 删库 → 删记录 → 删目录（高危，前端走 danger 确认）。"""
    from app.api.apps import _reject_if_task_running
    sf = request.app.state.session_factory
    try:
        _reject_if_task_running(request, app_id)
        return await links.uninstall(sf, app_id=app_id, actor_id=actor["id"])
    except DeployError as e:
        raise _http(e)
