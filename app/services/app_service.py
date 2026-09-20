# app/services/app_service.py
"""应用工程服务（规格 01）：原子创建（校验→端口→模板→app.json→平台记录→供给→deploy 文件），失败全回滚。

端口分配前置于模板渲染（裁定 R-A）：真实端口先进 mapping，README/模板与库内登记用同一对端口，
不再出现「空端口渲染 + 只回填不重渲染」的坏产物。并发创建同端口竞争由 app_project.prod_port UNIQUE 兜底。
"""
import datetime, json, shutil
from pathlib import Path
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from app.deploy import provision, scaffold
from app.deploy.errors import DeployError
from app.deploy.ports import allocate_pair
from app.deploy.settings import REPO_ROOT, deploy_cfg, resolve_apps_root
from app.deploy.slugs import validate_slug

SCALES = ("small", "large")
SMALL_STACKS = ("vue3-fastapi", "vue3-node", "static")
LARGE_STACK = "vue3-springboot"
STACKS = SMALL_STACKS + (LARGE_STACK,)
TEMPLATES = REPO_ROOT / "app" / "templates"   # 仓库根/app/templates（同 scaffold 测试的 TEMPLATE 常量）
HEALTH_BY_STACK = {"vue3-fastapi": "/api/health", "vue3-node": "/api/health",
                   "vue3-springboot": "/actuator/health", "static": ""}


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
    if scale == "large" and stack != LARGE_STACK:
        raise DeployError("大型应用锁定 Vue3 + Spring Boot 3")
    if stack not in STACKS:
        raise DeployError(f"技术栈非法: {stack}")
    # 裁定 R-C：M1 仅交付 vue3-fastapi 模板（vue3-node/static 属 M2/M3），
    # 不放行则 scaffold.render 会以「栈模板不存在 500」拒绝——这里提前给 400 友好提示。
    if not (TEMPLATES / stack).is_dir():
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
                       prod_port: int, dev_port: int) -> dict:
    """workspace + app_project 单事务提交；端口由调用方前置分配后显式传入（裁定 R-A）。

    UNIQUE 冲突（并发创建的 slug/端口/目录竞态）→ 409，由上层统一回滚。
    """
    async with session_factory() as s:
        dup = (await s.execute(text("SELECT id FROM workspace WHERE root_path = :p"),
                               {"p": str(root)})).first()
        if dup:
            raise DeployError("该目录已被登记为工作区", status=409)
        dup_slug = (await s.execute(text("SELECT id FROM app_project WHERE slug = :s"),
                                    {"s": slug})).first()
        if dup_slug:
            raise DeployError(f"slug 已存在: {slug}", status=409)
        try:
            # workspace 与 app_project 同一条 UNIQUE 兜底路径：并发抢走同目录时同样映射 409（m2）
            wid = (await s.execute(text(
                "INSERT INTO workspace (name, root_path) VALUES (:n, :p) RETURNING id"),
                {"n": name, "p": str(root)})).scalar()
            row = (await s.execute(text(
                "INSERT INTO app_project (workspace_id, name, slug, scale, stack, prod_port, dev_port) "
                "VALUES (:w, :n, :s, :sc, :st, :pp, :dp) "
                "RETURNING id, workspace_id, name, slug, scale, stack, prod_port, dev_port, "
                "status_dev, status_prod, current_version, created_at, updated_at"),
                {"w": wid, "n": name, "s": slug, "sc": scale, "st": stack,
                 "pp": prod_port, "dp": dev_port})).mappings().first()
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
                             stack: str, root: str | None = None) -> dict:
    """原子创建一个应用工程：任一步失败即回滚（平台记录 + 供给 + 目录），抛 DeployError。"""
    name_stripped = (name or "").strip()
    if not name_stripped:
        raise DeployError("应用名称不能为空")
    # M-2①（终审 F-02）：app_project.name VARCHAR(100)——入口即校验 strip 后 1..100，
    # 超限/空直接中文 400，不再穿透到 asyncpg StringDataRightTruncation 冒 500 外露驱动原文。
    if len(name_stripped) > 100:
        raise DeployError("应用名称过长（上限 100 字），请缩短后重试")
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
    mapping = {"SLUG": slug, "APP_NAME": name.strip(),
               "DEV_PORT": str(dev_port), "PROD_PORT": str(prod_port),
               "DEV_DB": f"{slug}_dev", "PROD_DB": f"{slug}_prod", "TODAY": today}
    app_id = None
    workspace_id = None
    provisioned_ok = False   # 本流是否真的供给过 dev 库：回滚只允许删本流自己建的东西（I-1）
    try:
        scaffold.render(TEMPLATES / stack, root_path, mapping)   # R-C 已保证模板目录存在
        app = await _insert_rows(session_factory, name=name.strip(), slug=slug, scale=scale,
                                 stack=stack, root=root_path, prod_port=prod_port, dev_port=dev_port)
        app_id = app["id"]
        workspace_id = app["workspace_id"]
        # app.json：不含密码；端口来自同一次分配，与库内登记一致
        meta = {"schema": "zc-app/1", "name": name.strip(), "slug": slug, "scale": scale,
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


async def list_apps(session_factory) -> list[dict]:
    async with session_factory() as s:
        rows = (await s.execute(text(
            "SELECT id, workspace_id, name, slug, scale, stack, prod_port, dev_port, "
            "status_dev, status_prod, current_version, created_at, updated_at "
            "FROM app_project ORDER BY id DESC"))).mappings().all()
    return [to_dict(r) for r in rows]


async def get_app(session_factory, app_id: int) -> dict:
    async with session_factory() as s:
        row = (await s.execute(text(
            "SELECT id, workspace_id, name, slug, scale, stack, prod_port, dev_port, "
            "status_dev, status_prod, current_version, created_at, updated_at "
            "FROM app_project WHERE id = :i"), {"i": app_id})).mappings().first()
    if not row:
        raise DeployError("应用不存在", status=404)
    return to_dict(row)
