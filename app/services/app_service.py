# app/services/app_service.py
"""应用工程服务（规格 01）：原子创建（校验→端口→模板→app.json→平台记录→供给→deploy 文件），失败全回滚。

端口分配前置于模板渲染（裁定 R-A）：真实端口先进 mapping，README/模板与库内登记用同一对端口，
不再出现「空端口渲染 + 只回填不重渲染」的坏产物。并发创建同端口竞争由 app_project.prod_port UNIQUE 兜底。
"""
import datetime, json, re, shutil
from pathlib import Path
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from app.deploy import provision, scaffold
from app.deploy import gateway_routes
from app.deploy.errors import DeployError
from app.deploy.nics import app_url
from app.deploy.ports import allocate_pair
from app.deploy.settings import REPO_ROOT, deploy_cfg, preview_url, resolve_apps_root
from app.deploy.slugs import validate_slug

SCALES = ("small", "large")
SMALL_STACKS = ("vue3-fastapi", "vue3-node", "static")
LARGE_STACK = "vue3-springboot"
MOBILE_STACK = "vue3-springboot-mobile"   # 大型档 + uni-app 移动端（ADR-0039）
MOBILE_OVERLAY = "uniapp-overlay"         # 移动栈的模板覆盖层目录名，不是独立栈目录
LARGE_STACKS = (LARGE_STACK, MOBILE_STACK)
STACKS = SMALL_STACKS + LARGE_STACKS
TEMPLATES = REPO_ROOT / "app" / "templates"   # 仓库根/app/templates（同 scaffold 测试的 TEMPLATE 常量）
# 栈 → 模板层序列：基底在前、覆盖层在后，依次渲染后者胜。
# 移动栈刻意不复制整份 springboot 模板——两份后端代码修一处忘一处（ADR-0039）。
TEMPLATE_LAYERS = {MOBILE_STACK: (LARGE_STACK, MOBILE_OVERLAY)}
HEALTH_BY_STACK = {"vue3-fastapi": "/api/health", "vue3-node": "/api/health",
                   "vue3-springboot": "/api/health", "vue3-springboot-mobile": "/api/health",
                   "static": ""}


def template_layers_of(stack: str) -> tuple[str, ...]:
    """该栈要依次渲染的模板目录名（多数栈就是自己那一层）。"""
    return TEMPLATE_LAYERS.get(stack, (stack,))

NAME_MAX = 100    # workspace.name / app_project.name 同为 VARCHAR(100)
# 名字会被内插进生成工程的源码与配置（pom.xml 的 <name>、Java 启动横幅的字符串字面量、main.py 的字符串字面量、
# index.html 的 <title>），单引号安全（SQL 字面量路径由 scaffold 做 '→'' 转义），故只拒写坏产物的那几个。
# % 也不拒（「50% 折扣」是正常业务名）：模板侧保证名字只作格式化实参出现，不进格式串（见 BaseApplication 横幅）。
NAME_UNSAFE = re.compile(r"[<>\"\\&\x00-\x1f\x7f]")


def check_name(name: str) -> str:
    """应用名统一入口校验：strip 后 1..NAME_MAX，返回落库用的干净值。

    创建与改名共用一处（M-2① 终审 F-02 的延伸）：超限直接中文 400，不穿透到 asyncpg 的
    StringDataRightTruncation 冒 500 外露驱动原文。
    """
    stripped = (name or "").strip()
    if not stripped:
        raise DeployError("应用名称不能为空")
    if NAME_UNSAFE.search(stripped):
        # 这些字符在 Java 字面量 / XML / Python 字面量 / HTML 文本里的转义规则各不相同，无通用转义，
        # 放它过入库就是“创建成功但预览/发布必碎”且无从溯源；入口一条 400 最便宜。
        raise DeployError('应用名称不能包含 < > & " \\ 或换行等控制字符（会写进生成的源码里）')
    if len(stripped) > NAME_MAX:
        raise DeployError(f"应用名称过长（上限 {NAME_MAX} 字），请缩短后重试")
    return stripped


def to_dict(row) -> dict:
    """app_project 行 → JSON 安全 dict（datetime → isoformat）。"""
    d = dict(row)
    for k in ("created_at", "updated_at"):
        if d.get(k) is not None:
            d[k] = d[k].isoformat()
    return d


def _check_scale_stack(scale: str, stack: str) -> None:
    if scale not in SCALES:
        raise DeployError(f"规模非法: {scale}（small/large）")
    if scale == "large" and stack not in LARGE_STACKS:
        raise DeployError("大型应用锁定 Vue3 + Spring Boot 3")
    if scale == "small" and stack not in SMALL_STACKS:
        # 反方向也要守：只挡「large 必须 springboot 系」时，small + 移动栈会渲染出大型栈目录、
        # 却在 scale 列上写着 small，两边语义从此打架。
        raise DeployError(f"该技术栈不属于小型档: {stack}")
    if stack not in STACKS:
        raise DeployError(f"技术栈非法: {stack}")
    # 裁定 R-C：模板未交付的栈在此提前 400（不放行则 scaffold.render 会冒「栈模板不存在 500」）。
    # 判据本身保留：五个栈目录现在都在位，但「向导里有 = 必须过闸门」是规范，不是临时兜底。
    # 叠加层的存在性一并在这里判——漏判会把「覆盖层没交付」推迟到渲染中途，留下半套目录。
    if any(not (TEMPLATES / layer).is_dir() for layer in template_layers_of(stack)):
        raise DeployError(f"该技术栈尚未开放: {stack}（即将上线）", status=400)


def load_app_json(root: Path) -> dict:
    f = Path(root) / "app.json"
    if not f.is_file():
        raise DeployError(f"应用清单缺失: {root}")
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception as e:
        raise DeployError(f"app.json 损坏: {e}")


async def _insert_rows(session_factory, *, name, slug, scale, stack, root,
                       prod_port: int, dev_port: int, origin: str | None = None,
                       owner_user_id: int | None = None) -> dict:
    """workspace + app_project 单事务提交；端口由调用方前置分配后显式传入（裁定 R-A）。

    UNIQUE 冲突（并发创建的 slug/端口/目录竞态）→ 409，由上层统一回滚。
    owner_user_id（Ticket 003 扩展态）：新建行盖当前登录用户，目录查重按同归属隔离。
    """
    async with session_factory() as s:
        dup = (await s.execute(text(
            "SELECT id FROM workspace WHERE root_path = :p "
            "AND owner_user_id IS NOT DISTINCT FROM :o"),
            {"p": str(root), "o": owner_user_id})).first()
        if dup:
            raise DeployError("该目录已被登记为工作区", status=409)
        dup_slug = (await s.execute(text("SELECT id FROM app_project WHERE slug = :s"),
                                    {"s": slug})).first()
        if dup_slug:
            raise DeployError(f"slug 已存在: {slug}", status=409)
        try:
            # workspace 与 app_project 同一条 UNIQUE 兜底路径：并发抢走同目录时同样映射 409（m2）
            wid = (await s.execute(text(
                "INSERT INTO workspace (name, root_path, owner_user_id) VALUES (:n, :p, :o) RETURNING id"),
                {"n": name, "p": str(root), "o": owner_user_id})).scalar()
            row = (await s.execute(text(
                "INSERT INTO app_project (workspace_id, name, slug, scale, stack, prod_port, "
                "dev_port, origin, owner_user_id) VALUES (:w, :n, :s, :sc, :st, :pp, :dp, :og, :o) "
                "RETURNING id, workspace_id, name, slug, scale, stack, prod_port, dev_port, "
                "status_dev, status_prod, current_version, created_at, updated_at"),
                {"w": wid, "n": name, "s": slug, "sc": scale, "st": stack,
                 "pp": prod_port, "dp": dev_port, "og": origin, "o": owner_user_id})).mappings().first()
            await s.commit()
        except IntegrityError as e:
            # 只读探测与插入之间的竞态（另一路创建占走同一目录/端口/slug）：事务已废，交给上层回滚
            raise DeployError("工作区目录、端口或标识已被占用，请重试", status=409) from e
    return to_dict(row)


async def _delete_rows(session_factory, app_id: int) -> None:
    async with session_factory() as s:
        await s.execute(text("DELETE FROM app_project WHERE id = :i"), {"i": app_id})
        await s.commit()


async def create_app_project(session_factory, *, name: str, slug: str, scale: str,
                             stack: str, root: str | None = None,
                             origin: str | None = None, materialize=None,
                             owner_user_id: int | None = None) -> dict:
    """原子创建一个应用工程：任一步失败即回滚（平台记录 + 供给 + 目录），抛 DeployError。

    `materialize(root_path, mapping)`：铺源钩子。默认走栈模板渲染；商店安装传自己的闭包
    （从包 copy 源码 + 锚点 sqlchange），从而复用本函数的端口分配、app.json、原子回滚。
    `origin`：安装来源标记（`store:<release_id>`），自建应用为 NULL。
    `owner_user_id`：归属用户（登录后的 actor id），新建行落此列（Ticket 003 扩展态）。
    """
    name_stripped = check_name(name)
    validate_slug(slug)
    _check_scale_stack(scale, stack)
    cfg = await deploy_cfg(session_factory)
    base = resolve_apps_root(cfg)
    root_path = Path(root) if root else base / slug
    if root_path.exists() and any(root_path.iterdir()):
        raise DeployError(f"目录已存在且非空: {root_path}", status=409)
    # R-A：先分配端口对（只读探测），真实端口进 mapping 再渲染模板
    async with session_factory() as s:
        prod_port, dev_port = await allocate_pair(s)
    today = datetime.date.today().strftime("%Y%m%d")
    mapping = {"SLUG": slug, "APP_NAME": name_stripped,
               "DEV_PORT": str(dev_port), "PROD_PORT": str(prod_port),
               "DEV_DB": f"{slug}_dev", "PROD_DB": f"{slug}_prod", "TODAY": today}
    app_id = None
    workspace_id = None
    provisioned_ok = False   # 本流是否真的供给过 dev 库：回滚只允许删本流自己建的东西（I-1）
    try:
        render = materialize or (
            lambda rp, mp: scaffold.render_layers(
                [TEMPLATES / layer for layer in template_layers_of(stack)], rp, mp,
                scaffold.has_data_track(stack)))
        render(root_path, mapping)   # R-C 已保证模板目录存在
        app = await _insert_rows(session_factory, name=name_stripped, slug=slug, scale=scale,
                                 stack=stack, root=root_path, prod_port=prod_port,
                                 dev_port=dev_port, origin=origin, owner_user_id=owner_user_id)
        app_id = app["id"]
        workspace_id = app["workspace_id"]
        # app.json：不含密码；端口来自同一次分配，与库内登记一致
        meta = {"schema": "zc-app/1", "name": name_stripped, "slug": slug, "scale": scale,
                "stack": stack, "ports": {"prod": app["prod_port"], "dev": app["dev_port"]},
                "database": {"dev": {"name": f"{slug}_dev", "user": f"{slug}_dev"},
                             "prod": {"name": f"{slug}_prod", "user": f"{slug}_prod"}},
                "health": {"frontend": "/", "backend": HEALTH_BY_STACK[stack]},
                "createdAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "platformMin": "1.1"}
        (root_path / "app.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="")
        cred = await provision.provision_env(session_factory, slug, "dev")
        provisioned_ok = True   # 自此本流才是 dev 库的建造者；此后任何失败才允许 drop_env
        # 台账裁定（Task 10 审查遗留）：调用方守卫——凭据不全自动即回滚，不静默产出坏栈
        if not (cred or {}).get("db") or not (cred or {}).get("password"):
            raise DeployError("数据库供给结果不完整", status=500)
        from app.deploy.compose import ensure_deploy_files   # 延迟导入避免环
        ensure_deploy_files(root_path, meta, {"dev": cred})
        return app
    except DeployError:
        await _rollback(session_factory, root_path, slug, app_id, workspace_id,
                        provisioned=provisioned_ok)
        raise
    except Exception as e:
        await _rollback(session_factory, root_path, slug, app_id, workspace_id,
                        provisioned=provisioned_ok)
        raise DeployError(f"创建应用失败: {e}") from e


async def _rollback(session_factory, root_path: Path, slug: str, app_id: int | None,
                    workspace_id: int | None, provisioned: bool) -> None:
    """失败回滚：各步互不阻断。删除范围严格限定本流自己的产物——
    app_project 按 id、workspace 按本流登记的 workspace_id（不按 root_path，避免删掉
    并发窗口内他人插入的同路径行）、库/角色/凭据仅在本流真的供给过时才 drop_env。"""
    if app_id:
        try:
            await _delete_rows(session_factory, app_id)
        except Exception:
            pass
    if workspace_id:
        try:
            async with session_factory() as s:
                await s.execute(text("DELETE FROM workspace WHERE id = :i"), {"i": workspace_id})
                await s.commit()
        except Exception:
            pass
    if provisioned:
        try:
            await provision.drop_env(session_factory, slug, "dev")
        except Exception:
            pass
    shutil.rmtree(root_path, ignore_errors=True)


def _with_public_urls(row: dict) -> dict:
    """回表补两个对外地址：`preview_url`（dev 栈）与 `prod_url`（正式发布地址）。

    硬拼出来的地址在远程部署下指向的是访问者自己的机器，端口倒是没错——错的是宿主。
    prod 侧同理，而且它的域名/端口口径只在 `nics.app_url` 一处投影（ADR-0016），这里只是
    把那一份结果顺带进列表与详情，前端不再自拼、也不必为渲染一个按钮多点一次接口。
    """
    port = row.get("dev_port")
    row["preview_url"] = preview_url(port) if port else ""
    row["prod_url"] = app_url(row)
    return row


async def list_apps(session_factory, *, owner_user_id: int | None = None) -> list[dict]:
    """应用列表（应用页与侧栏分组共用）。带上 workspace.root_path：
    侧栏一行就是一个应用，确认删除时要当场把「工程目录还在哪儿」说清楚。

    `owner_user_id`（Ticket 006）：HTTP 层传当前账号做隔离，不传保持全局语义——
    validate-slug 的 taken 预检读的是全局 UNIQUE，必须看全量。

    store 键：商店实例的绑定摘要（来源包/通道/已应用指纹），列表卡片角标与升级入口靠它；
    一次性拉全部 link 再拼，不在循环里查库。"""
    async with session_factory() as s:
        sql = ("SELECT a.id, a.workspace_id, a.name, a.slug, a.scale, a.stack, a.prod_port, a.dev_port, "
               "a.status_dev, a.status_prod, a.current_version, a.origin, a.created_at, a.updated_at, w.root_path "
               "FROM app_project a LEFT JOIN workspace w ON w.id = a.workspace_id ")
        params: dict = {}
        if owner_user_id is not None:
            sql += "WHERE a.owner_user_id = :u "
            params["u"] = owner_user_id
        rows = (await s.execute(text(sql + "ORDER BY a.id DESC"), params)).mappings().all()
        links = (await s.execute(text(
            "SELECT l.app_id, l.release_id, l.channel, l.detach_reason, l.applied_fingerprint, "
            "l.last_upgraded_at, r.version AS release_version, r.slug AS release_slug, "
            "r.title AS release_title FROM app_instance_link l "
            "JOIN app_release r ON r.id = l.release_id"))).mappings().all()
    by_app = {int(l["app_id"]): to_dict(l) for l in links}
    out = []
    for r in rows:
        d = to_dict(r)
        d["store"] = by_app.get(int(d["id"]))
        out.append(_with_public_urls(d))
    return out


async def get_app(session_factory, app_id: int, *, owner_user_id: int | None = None) -> dict:
    """单应用详情：与 list_apps 同形状携带 origin/store，前端一处判定逻辑两处复用。

    `owner_user_id`（Ticket 006）：HTTP 层传当前账号，别人的行按 404 处理（不区分 403——
    存在性本身就是要藏的信息）；内部受控链路（install/publish/rollback 引擎侧）不传。"""
    async with session_factory() as s:
        sql = ("SELECT id, workspace_id, name, slug, scale, stack, prod_port, dev_port, "
               "status_dev, status_prod, current_version, origin, created_at, updated_at "
               "FROM app_project WHERE id = :i")
        params: dict = {"i": app_id}
        if owner_user_id is not None:
            sql += " AND owner_user_id = :u"
            params["u"] = owner_user_id
        row = (await s.execute(text(sql), params)).mappings().first()
        if not row:
            raise DeployError("应用不存在", status=404)
        link = (await s.execute(text(
            "SELECT l.app_id, l.release_id, l.channel, l.detach_reason, l.applied_fingerprint, "
            "l.last_upgraded_at, r.version AS release_version, r.slug AS release_slug, "
            "r.title AS release_title FROM app_instance_link l "
            "JOIN app_release r ON r.id = l.release_id WHERE l.app_id = :i"),
            {"i": app_id})).mappings().first()
    d = to_dict(row)
    d["store"] = to_dict(link) if link else None
    return _with_public_urls(d)


async def get_app_for_user(session_factory, app_id: int,
                           user_id: int | None) -> tuple[dict, str]:
    """按 app_id 取应用的**唯一判权入口**：返回 (应用详情, 角色)。

    角色：`owner`（`app_project.owner_user_id`）或 `collaborator`（`app_share` 命中，
    规格 §2.5）。两者都不是与不存在**同一 404、同一句文案**——存在性本身就是要藏的信息。
    `user_id=None` 是创始人/内部链路的既有 bypass（与 `get_app(**owner_user_id=None)`
    同一语义）：不筛归属，角色按 owner 返回。
    """
    async with session_factory() as s:
        owner_row = (await s.execute(text(
            "SELECT owner_user_id FROM app_project WHERE id = :i"),
            {"i": app_id})).first()
        if owner_row is None:
            raise DeployError("应用不存在", status=404)
        if user_id is None or owner_row[0] == user_id:
            return await get_app(session_factory, app_id), "owner"
        shared = (await s.execute(text(
            "SELECT 1 FROM app_share WHERE app_id = :i AND user_id = :u"),
            {"i": app_id, "u": user_id})).first()
    if shared is None:
        raise DeployError("应用不存在", status=404)
    return await get_app(session_factory, app_id), "collaborator"


async def list_shared_apps(session_factory, user_id: int) -> list[dict]:
    """「共享给我」列表（规格 §5.3）：与 `list_apps` 同形状，另带 `owner_nickname`。

    被共享者只拿到自己那一行 `app_share` 命中的应用；排序与自有列表同口径（新→旧）。
    """
    async with session_factory() as s:
        rows = (await s.execute(text(
            "SELECT a.id, a.workspace_id, a.name, a.slug, a.scale, a.stack, a.prod_port, a.dev_port, "
            "a.status_dev, a.status_prod, a.current_version, a.origin, a.created_at, a.updated_at, "
            "w.root_path, o.nickname AS owner_nickname "
            "FROM app_share sh "
            "JOIN app_project a ON a.id = sh.app_id "
            "JOIN actor o ON o.id = a.owner_user_id "
            "LEFT JOIN workspace w ON w.id = a.workspace_id "
            "WHERE sh.user_id = :u ORDER BY a.id DESC"), {"u": user_id})).mappings().all()
        links = (await s.execute(text(
            "SELECT l.app_id, l.release_id, l.channel, l.detach_reason, l.applied_fingerprint, "
            "l.last_upgraded_at, r.version AS release_version, r.slug AS release_slug, "
            "r.title AS release_title FROM app_instance_link l "
            "JOIN app_release r ON r.id = l.release_id"))).mappings().all()
    by_app = {int(l["app_id"]): to_dict(l) for l in links}
    out = []
    for r in rows:
        d = to_dict(r)
        d["store"] = by_app.get(int(d["id"]))
        out.append(_with_public_urls(d))
    return out


# ---------- 共享关系 CRUD（规格 §5.1）：端点只做状态码映射，判据都在这里 ----------

async def list_share_members(session_factory, app_id: int) -> list[dict]:
    async with session_factory() as s:
        rows = (await s.execute(text(
            "SELECT sh.user_id, ac.nickname, sh.created_at FROM app_share sh "
            "JOIN actor ac ON ac.id = sh.user_id WHERE sh.app_id = :i "
            "ORDER BY sh.created_at, sh.id"), {"i": app_id})).mappings().all()
    out = []
    for r in rows:
        d = dict(r)
        d["created_at"] = d["created_at"].isoformat()
        out.append(d)
    return out


async def add_share(session_factory, *, app_id: int, user_id: int,
                    created_by: int) -> dict:
    """共享给某个账号：自共享 400、用户不存在 404、已共享 409（规格 §5.1/§5.6）。

    并发重复由 `UNIQUE (app_id, user_id)` 兜底——预检只是把常见路径的文案说清楚。
    """
    if user_id == created_by:
        raise DeployError("不能共享给应用所有者自己", status=400)
    async with session_factory() as s:
        nickname = (await s.execute(text(
            "SELECT nickname FROM actor WHERE id = :u"), {"u": user_id})).scalar()
        if nickname is None:
            raise DeployError("用户不存在", status=404)
        if (await s.execute(text(
                "SELECT 1 FROM app_share WHERE app_id = :i AND user_id = :u"),
                {"i": app_id, "u": user_id})).first():
            raise DeployError("该用户已在共享列表中", status=409)
        try:
            created_at = (await s.execute(text(
                "INSERT INTO app_share (app_id, user_id, created_by) VALUES (:i, :u, :c) "
                "RETURNING created_at"), {"i": app_id, "u": user_id, "c": created_by})).scalar()
            await s.commit()
        except IntegrityError:
            await s.rollback()
            raise DeployError("该用户已在共享列表中", status=409)
    return {"user_id": user_id, "nickname": nickname, "created_at": created_at.isoformat()}


async def remove_share(session_factory, *, app_id: int, user_id: int) -> None:
    """撤销共享（所有者）或主动退出（被共享者）：同一行、同一判据；未共享 404。

    只删 `app_share` 行，不删会话/文件/发布记录（规格 §8）——重新共享即恢复可见。
    同一事务里把该用户在此应用上**待审**的发布申请置 cancelled（S13）：审批入口随共享
    一起消失，pending 不能悬空；已批准/已驳回的历史申请不动（审批节点已越过）。
    """
    async with session_factory() as s:
        hit = (await s.execute(text(
            "DELETE FROM app_share WHERE app_id = :i AND user_id = :u RETURNING id"),
            {"i": app_id, "u": user_id})).first()
        if hit is None:
            raise DeployError("共享关系不存在", status=404)
        await s.execute(text(
            "UPDATE app_publish_request SET status='cancelled' "
            "WHERE app_id = :i AND requester_id = :u AND status = 'pending'"),
            {"i": app_id, "u": user_id})
        await s.commit()


async def workspace_role(session_factory, workspace_id: int,
                         user_id: int | None) -> str | None:
    """按 workspace_id 判权的唯一入口（Ticket 000）：返回 owner / collaborator / None。

    `None` = 不可见（不存在、他人、无主遗留行同一口径），调用方统一 404。
    `user_id=None` 是创始人 bypass（创始人「看全部」目前只有指标域在用，见 metrics._load_app）。
    被共享者经 `app_share → app_project.workspace_id` 命中（Ticket 005）：共享以应用为锚，
    工作区本身没有独立的共享表。
    """
    async with session_factory() as s:
        row = (await s.execute(text(
            "SELECT owner_user_id FROM workspace WHERE id = :i"),
            {"i": workspace_id})).first()
        if row is None:
            return None
        if user_id is None or row[0] == user_id:
            return "owner"
        hit = (await s.execute(text(
            "SELECT 1 FROM app_share sh JOIN app_project a ON a.id = sh.app_id "
            "WHERE a.workspace_id = :w AND sh.user_id = :u"),
            {"w": workspace_id, "u": user_id})).first()
    return "collaborator" if hit else None


def require_owner(role: str) -> None:
    """端点级增权（Ticket 000 起由写端点调用）：已可见但非所有者 → 403。

    读路径的不可见用 404（藏存在性）；走到这里说明对方已经看得见这个资源，
    403 是诚实口径，也是被共享者唯一能拿到的「为什么点不动」的反馈（规格 §2.5/§5.6）。
    """
    if role != "owner":
        raise DeployError("仅应用所有者可执行此操作", status=403)


async def get_app_by_workspace(session_factory, workspace_id: int, *,
                               owner_user_id: int | None = None) -> dict | None:
    """按工作区反查唯一应用行（Agent 预览工具的身份来源，规格 2026-10-29 §5.1）。

    与 `get_app` 同形状——因为它就是**委托** `get_app` 取的行：两处各自维护一份列清单与
    store 子查询早晚漂移，而漂移的后果是下游拿到静默的 None（规格要求两者可互换使用）。

    - 0 行 → None：不是应用工作区，预览工具不挂载；
    - 多行 → 抛 409：`app_project.workspace_id` 没有 UNIQUE 约束，历史上可能手工插入重复行，
      这时不猜、也不挂载工具（fail closed），由调用方记 warning 并向模型说明原因；
    - 行在两查之间被删 → `get_app` 的 404 原样上抛：调用方要把它与 409 的「多个应用」
      分开处理（前者是「应用不可用」，后者是「无法确定预览目标」）；
    - `owner_user_id` 过滤与 `list_apps` 同一条判据：会话已过 WS 所有权校验，这里是第二道闸；
      存量 NULL 归属的应用在应用列表里同样不可见，不制造新的不一致。
    """
    async with session_factory() as s:
        sql = "SELECT id FROM app_project WHERE workspace_id = :w"
        params: dict = {"w": workspace_id}
        if owner_user_id is not None:
            sql += " AND owner_user_id = :u"
            params["u"] = owner_user_id
        ids = [r[0] for r in (await s.execute(text(sql + " ORDER BY id LIMIT 2"), params)).all()]
    if not ids:
        return None
    if len(ids) > 1:
        raise DeployError("当前工作区绑定了多个应用，无法确定预览目标", status=409)
    return await get_app(session_factory, ids[0])


async def rename_app(session_factory, app_id: int, name: str) -> dict:
    """应用改名：app_project.name 与 workspace.name 同事务更新，slug/端口/目录不动。

    两张表都写是这里唯一的非显然处：应用页读 app_project.name，侧栏分组名与文件面板面包屑读
    workspace.name（GET /workspaces 直接取自该列），只改一边就会出现「应用页新名、侧栏旧名」。

    归属不在本函数判：调用方（apps.rename）先过 `get_app_for_user` 的单点判权，
    这里按 id 直写，避免「服务层一道闸、端点又一道闸」的双份实现（Ticket 000 收口）。
    """
    new_name = check_name(name)
    async with session_factory() as s:
        wid = (await s.execute(text(
            "SELECT workspace_id FROM app_project WHERE id = :i"), {"i": app_id})).scalar()
        if wid is None:
            raise DeployError("应用不存在", status=404)
        await s.execute(text("UPDATE app_project SET name = :n, updated_at = now() WHERE id = :i"),
                        {"n": new_name, "i": app_id})
        await s.execute(text("UPDATE workspace SET name = :n WHERE id = :i"), {"n": new_name, "i": wid})
        await s.commit()
    return await get_app(session_factory, app_id)


def ensure_deletable(app: dict) -> None:
    """删除闸门（规格 08 §5.4）：发布进行中不允许删记录，否则留下半成品版本目录与在跑容器。"""
    if (app.get("status_prod") or "") == "publishing":
        raise DeployError("该应用正在发布中，请等发布结束后再删除", status=409)


async def delete_app(session_factory, app_id: int) -> dict:
    """删除应用的平台记录，返回被删应用快照（含 root_path / slug，供前端说明「什么还留着」）。

    M1 裁定：只删记录，**不删磁盘工程目录、不 drop 数据库**（01 FR1-07 的全删链含删容器/镜像/
    删库勾选项，留给 M2）。删的是 workspace 行——app_project 的 FK 与 chat_session 都是
    ON DELETE CASCADE，一次删除即清掉应用 + 会话 + 消息 + 文件变更，不留孤儿；
    prod_port/dev_port 的 UNIQUE 行随之消失，端口对即被释放（规格 05）——**前提是调用方已把该应用的
    dev/prod 栈收口**（`engine.down_app_stacks`）：端口在库里释放而容器还在跑，等于把同一个端口埋给
    下一个应用，它起栈时只会拿到 daemon 的 port is already allocated。
    """
    async with session_factory() as s:
        row = (await s.execute(text(
            "SELECT a.id, a.workspace_id, a.name, a.slug, a.status_dev, a.status_prod, w.root_path "
            "FROM app_project a JOIN workspace w ON w.id = a.workspace_id WHERE a.id = :i"),
            {"i": app_id})).mappings().first()
        if not row:
            raise DeployError("应用不存在", status=404)
        snap = dict(row)
        ensure_deletable(snap)
        await s.execute(text("DELETE FROM workspace WHERE id = :i"), {"i": snap["workspace_id"]})
        await s.commit()
    # 应用行已级联删除，刷新网关路由表把该 slug 从 routes.json 摘掉（失败只告警）
    await gateway_routes.refresh(session_factory)
    snap["databases"] = [f"{snap['slug']}_dev", f"{snap['slug']}_prod"]
    return snap


# ---------- 发布申请（规格 §2.3/§5.2，ADR-0050）：状态机与判据只在这里 ----------

_REQUEST_COLS = ("id, app_id, requester_id, status, reason, decided_by, decided_at, created_at")


def _request_row(r) -> dict:
    d = dict(r)
    for k in ("decided_at", "created_at"):
        if d.get(k) is not None:
            d[k] = d[k].isoformat()
    return d


async def get_publish_request(session_factory, *, app_id: int, request_id: int) -> dict:
    """按 (app_id, rid) 取一条申请：路径上的 app 与 rid 不匹配即当不存在（不给探测留差异）。"""
    async with session_factory() as s:
        row = (await s.execute(text(
            f"SELECT {_REQUEST_COLS} FROM app_publish_request "
            "WHERE id = :r AND app_id = :a"),
            {"r": request_id, "a": app_id})).mappings().first()
    if not row:
        raise DeployError("发布申请不存在", status=404)
    return _request_row(row)


async def pending_request_of_app(session_factory, app_id: int) -> dict | None:
    """该应用当前的待审申请（含申请人昵称）；共享管理弹框的待审区块数据源。"""
    async with session_factory() as s:
        row = (await s.execute(text(
            "SELECT r.id, r.requester_id, ac.nickname AS requester_nickname, r.created_at "
            "FROM app_publish_request r JOIN actor ac ON ac.id = r.requester_id "
            "WHERE r.app_id = :i AND r.status = 'pending'"), {"i": app_id})).mappings().first()
    return _request_row(row) if row else None


async def apps_with_pending_requests(session_factory, app_ids: list[int]) -> set[int]:
    """一次查出哪些应用有待审申请（自有列表的待审角标，S3；避免逐应用查库）。"""
    if not app_ids:
        return set()
    async with session_factory() as s:
        rows = (await s.execute(text(
            "SELECT DISTINCT app_id FROM app_publish_request "
            "WHERE status = 'pending' AND app_id = ANY(:ids)"),
            {"ids": list(app_ids)})).all()
    return {int(r[0]) for r in rows}


async def latest_requests_of_user(session_factory, app_ids: list[int],
                                  user_id: int) -> dict[int, dict]:
    """每个应用里**当前用户最近一条**申请（共享卡片的状态机与驳回理由来源，S12）。

    「共享给我」的 pending_publish 按本人算：别人还有待审申请不该让我的卡片显示「待审批」。
    带上 `id`：撤回（`DELETE .../publish-requests/{rid}`）需要它，列表不再多点一次接口。
    """
    if not app_ids:
        return {}
    async with session_factory() as s:
        rows = (await s.execute(text(
            "SELECT DISTINCT ON (app_id) id, app_id, status, reason, created_at "
            "FROM app_publish_request WHERE requester_id = :u AND app_id = ANY(:ids) "
            "ORDER BY app_id, created_at DESC, id DESC"),
            {"u": user_id, "ids": list(app_ids)})).mappings().all()
    out: dict[int, dict] = {}
    for r in rows:
        d = _request_row(r)
        app_id = int(d.pop("app_id"))
        out[app_id] = d
    return out


async def create_publish_request(session_factory, *, app_id: int,
                                 requester_id: int) -> dict:
    """被共享者发起发布申请：发布中 409、已有待审 409（S12/S18）。

    并发双申请由 `ux_app_publish_request_pending` 兜底：两条同时过预检时，
    第二条撞索引走同一句 409 文案——预检只说清楚常见路径，唯一性才是判据。
    """
    async with session_factory() as s:
        status_prod = (await s.execute(text(
            "SELECT status_prod FROM app_project WHERE id = :i"), {"i": app_id})).scalar()
        if status_prod is None:
            raise DeployError("应用不存在", status=404)
        if status_prod == "publishing":
            raise DeployError("应用正在发布中，请稍后再申请", status=409)
        if (await s.execute(text(
                "SELECT 1 FROM app_publish_request WHERE app_id = :i AND status = 'pending'"),
                {"i": app_id})).first():
            raise DeployError("已有待审批的发布申请，请等待处理", status=409)
        try:
            row = (await s.execute(text(
                "INSERT INTO app_publish_request (app_id, requester_id) VALUES (:a, :u) "
                "RETURNING id, status, created_at"),
                {"a": app_id, "u": requester_id})).mappings().first()
            await s.commit()
        except IntegrityError:
            await s.rollback()
            raise DeployError("已有待审批的发布申请，请等待处理", status=409)
    d = _request_row(row)
    return {"id": d["id"], "status": d["status"], "created_at": d["created_at"]}


async def decide_publish_request(session_factory, *, request_id: int, status: str,
                                 decided_by: int, reason: str | None = None) -> dict:
    """审批收口（approved / rejected）：条件 UPDATE 只允许 pending 被改，并发双批输者 409。

    调用方（approve 端点）必须在**发布闸门通过之后**才调这里：规格 §2.3 要求闸门失败时
    申请保持 pending，可重试（D15）。
    """
    async with session_factory() as s:
        row = (await s.execute(text(
            "UPDATE app_publish_request SET status = :st, reason = COALESCE(:rs, reason), "
            "decided_by = :u, decided_at = now() WHERE id = :r AND status = 'pending' "
            f"RETURNING {_REQUEST_COLS}"),
            {"st": status, "rs": reason, "u": decided_by, "r": request_id})).mappings().first()
        await s.commit()
    if not row:
        raise DeployError("该申请已处理，无法重复审批", status=409)
    return _request_row(row)


async def reopen_publish_request(session_factory, request_id: int) -> None:
    """把刚批准的申请退回 pending：`_spawn` 在闸门与受理之间的竞态里失败时，
    不能留下「已批准但从未发布」的假状态（闸门失败保持 pending 的延伸）。"""
    async with session_factory() as s:
        await s.execute(text(
            "UPDATE app_publish_request SET status = 'pending', decided_by = NULL, "
            "decided_at = NULL WHERE id = :r AND status = 'approved'"), {"r": request_id})
        await s.commit()


async def reject_publish_request(session_factory, *, app_id: int, request_id: int,
                                 reason: str, decided_by: int) -> dict:
    """驳回必填理由（S4）：空理由 400、非 pending 409。理由落库供申请人查看。"""
    req = await get_publish_request(session_factory, app_id=app_id, request_id=request_id)
    if req["status"] != "pending":
        raise DeployError("该申请已处理，无法重复审批", status=409)
    clean = (reason or "").strip()
    if not clean:
        raise DeployError("驳回理由不能为空", status=400)
    return await decide_publish_request(session_factory, request_id=request_id,
                                        status="rejected", decided_by=decided_by,
                                        reason=clean)


async def withdraw_publish_request(session_factory, *, app_id: int, request_id: int,
                                   user_id: int) -> dict:
    """申请人撤回（S12）：非本人 403、非 pending 409；只改状态，不删行（留痕）。"""
    req = await get_publish_request(session_factory, app_id=app_id, request_id=request_id)
    if req["requester_id"] != user_id:
        raise DeployError("只能撤回自己发起的发布申请", status=403)
    if req["status"] != "pending":
        raise DeployError("只有待审批的申请可以撤回", status=409)
    async with session_factory() as s:
        row = (await s.execute(text(
            "UPDATE app_publish_request SET status = 'withdrawn', decided_by = :u, "
            "decided_at = now() WHERE id = :r AND status = 'pending' "
            f"RETURNING {_REQUEST_COLS}"),
            {"u": user_id, "r": request_id})).mappings().first()
        await s.commit()
    if not row:
        raise DeployError("只有待审批的申请可以撤回", status=409)
    return _request_row(row)
