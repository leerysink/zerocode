# app/store/install.py
"""安装（规格 M3-a §6.3）：把不可变包落成一份独立实例。

六步：铺源码 → 建平台记录与分库 → dev/prod 基线重放 → 自有镜像 build → prod 起栈体检
→ 写绑定关系。前三步失败全部 teardown（不留孤儿库/行/目录）；到了起栈阶段就**只报错不删**，
因为库里可能已经有用户数据了。

镜像不共享：必须用实例自己的 slug build（keep-5 清理与删实例都按 zc-<slug>-* 前缀匹配，
共享源 tag 会互删在服容器）。
"""
import logging
import re
from pathlib import Path
from sqlalchemy import text
from app.deploy import compose, engine as deploy_engine
from app.deploy import prod as deploy_prod, provision, records, sqlrun
from app.deploy.errors import DeployError
from app.deploy.settings import deploy_cfg, build_timeout
from app.deploy.slugs import suggest_slug
from app.services import app_service
from app.store import anchor as anchor_mod, packages
from app.store import release as release_svc

logger = logging.getLogger(__name__)

_last_slug: str | None = None      # 仅供失败用例断言 teardown 对象；不参与业务逻辑


def _instance_slug(base: str, taken: set[str]) -> str:
    """来源 slug + 'i' 后缀（^[a-z][a-z0-9]{1,39}$，撞了自动加序号）。"""
    stem = re.sub(r"[^a-z0-9]", "", (base or "").lower())[:34] or "app"
    return suggest_slug(stem + "i", taken)


async def _store_dir(sf) -> Path:
    return release_svc.store_root(await deploy_cfg(sf))


async def _taken_slugs(sf) -> set[str]:
    async with sf() as s:
        return {r[0] for r in await s.execute(text("SELECT slug FROM app_project"))}


async def _set_current_version(sf, app_id: int, version: str) -> None:
    async with sf() as s:
        await s.execute(text("UPDATE app_project SET current_version=:v, updated_at=now() "
                             "WHERE id=:i"), {"v": version, "i": app_id})
        await s.commit()


def _materializer(pkg: Path, rel: dict):
    """包 → 工程目录：src 整拍 + sqlchange 换成锚点快照（覆盖模板铺法）。"""
    def materialize(root: Path, mapping: dict) -> None:
        shutil_copytree(pkg / "src", root)
        sql = root / "sqlchange"
        if sql.exists():
            import shutil
            shutil.rmtree(sql)
        shutil_copytree(pkg / "sqlchange", sql)
        readme = (root / "README.md")
        readme.write_text(
            f"# {mapping['APP_NAME']}\n\n"
            f"来自应用商店版本 {rel['slug']}@{rel['version']}。\n\n"
            f"- 本机预览：http://localhost:{mapping['DEV_PORT']}\n"
            f"- 数据库：dev = {mapping['DEV_DB']}，prod = {mapping['PROD_DB']}\n"
            f"- 数据变更：一律写到 `sqlchange/V<日期>_<序号>_<说明>.sql`，**已应用的文件不得修改**\n",
            encoding="utf-8", newline="")
    return materialize


def shutil_copytree(src: Path, dst: Path) -> None:
    import shutil
    shutil.copytree(src, dst, dirs_exist_ok=True)


async def install_release(sf, *, release_row: dict, applicant_actor_id: int,
                          want_name: str, notify=None) -> dict:
    """返回新实例的 app dict（含 url）。抛 DeployError 时已完成 teardown。"""
    global _last_slug
    _last_slug = None
    pkg = packages.package_path(await _store_dir(sf), release_row["slug"],
                                release_row["version"])
    if not pkg.is_dir():
        raise DeployError(f"版本包缺失: {pkg}（包不可重建，请重新上架该应用）", status=500)
    manifest = packages.read_manifest(pkg)
    anchor = manifest["anchor"]
    if not anchor:
        raise DeployError("版本包锚点为空，无法初始化实例库", status=500)
    app = None
    slug = None
    rid = None
    try:
        slug = _instance_slug(release_row["slug"], await _taken_slugs(sf))
        _last_slug = slug
        app = await app_service.create_app_project(
            sf, name=want_name, slug=slug, scale="small",
            stack=manifest.get("stack") or "vue3-fastapi",
            origin=f"store:{release_row['id']}", materialize=_materializer(pkg, release_row),
            owner_user_id=applicant_actor_id)   # 安装者即归属人（Ticket 003 扩展态）
        rid = await records.begin(sf, app["id"], "install", release_row["version"])

        def on_log(line: str) -> None:
            if notify:
                notify(app["id"], {"app_id": app["id"], "type": "dev_log", "line": line})

        cred_dev = await provision.read_cred(sf, slug, "dev")
        cred_prod = await provision.provision_env(sf, slug, "prod")
        if not cred_dev:
            raise DeployError("dev 凭据缺失（供给异常），请重试安装", status=500)
        root = await deploy_engine._app_root(sf, app)
        # 基线重放：空库按序全跑（run.sh 的「跳过已应用」在空库上自然成立）
        for env, cred in (("dev", cred_dev), ("prod", cred_prod)):
            await records.set_step(sf, rid, f"migrate:{env}")
            res = await sqlrun.run_sqlchange(root, env, cred, on_log=on_log)
            if not res["ok"]:
                raise DeployError(f"{env} 库基线迁移失败（退出码 {res['code']}）："
                                  f"{res['log'][-600:]}")
        # 自有镜像：按包版本号 build，再交给 prod_start 做 up + 体检
        await records.set_step(sf, rid, "build")
        meta = app_service.load_app_json(root)
        compose.ensure_deploy_files(root, meta, {"dev": cred_dev, "prod": cred_prod},
                                    version=release_row["version"])
        code, log = await deploy_engine.docker(
            "compose", "-f", str(root / "deploy" / "prod-compose.yml"), "build",
            cwd=str(root), on_line=on_log,
            # 按栈取超时（与 prod 发布链同族）：大型栈包的 build 要在构建期下整套 Maven 依赖，
            # 吃 engine 默认 600 会把成功构建误杀成「镜像构建失败」。
            timeout=build_timeout(await deploy_cfg(sf), app.get("stack", "")))
        if code != 0:
            raise DeployError(f"实例镜像构建失败：{log[-600:]}")
        await _set_current_version(sf, app["id"], release_row["version"])
        await records.set_step(sf, rid, "deploy")
        await deploy_prod.prod_start(sf, app, notify=notify)
        await _write_link(sf, app=app, release_row=release_row, manifest=manifest,
                          anchor=anchor, applicant_actor_id=applicant_actor_id, root=root)
        await records.finish(sf, rid, "success", detail={
            "release_id": release_row["id"], "instance_slug": slug,
            "files": len(anchor)})
        app = await app_service.get_app(sf, app["id"])
        app["url"] = f"http://localhost:{app['prod_port']}"
        if notify:
            notify(app["id"], {"app_id": app["id"], "type": "install_done", "ok": True,
                               "target_app_id": app["id"], "url": app["url"]})
        return app
    except DeployError as e:
        if app:
            await _teardown(sf, app["id"], slug)
            if rid:
                await records.finish(sf, rid, "failed", error=str(e))
        if notify and app:
            notify(app["id"], {"app_id": app["id"], "type": "install_done", "ok": False,
                               "target_app_id": None, "error": str(e)})
        raise
    except Exception as e:
        if app:
            await _teardown(sf, app["id"], slug)
            if rid:
                await records.finish(sf, rid, "failed", error=str(e))
        raise DeployError(f"安装失败: {e}") from e


async def _write_link(sf, *, app: dict, release_row: dict, manifest: dict, anchor: list,
                      applicant_actor_id: int, root: Path) -> None:
    from app.store import packages as pk
    async with sf() as s:
        await s.execute(text(
            "INSERT INTO app_instance_link (app_id, release_id, channel, baseline_manifest, "
            "baseline_hash, applied_fingerprint, owner_actor) VALUES (:a, :r, 'managed', "
            "CAST(:m AS JSONB), :h, :f, :o)"),
            {"a": app["id"], "r": release_row["id"],
             "m": json_dumps(anchor), "h": pk.source_hash(root),
             "f": manifest.get("fingerprint") or anchor_mod.fingerprint(anchor),
             "o": applicant_actor_id})
        await s.commit()


def json_dumps(v) -> str:
    import json
    return json.dumps(v, ensure_ascii=False)


async def _teardown(sf, app_id: int, slug: str | None) -> None:
    """只收本流自己的产物：库/角色按 slug 删，平台记录与目录按 id/root_path 删。各步不互阻断。"""
    if slug:
        for env in ("dev", "prod"):
            try:
                await provision.drop_env(sf, slug, env)
            except Exception:
                logger.warning("商店安装回滚：drop_env %s/%s 失败", slug, env, exc_info=True)
    try:
        snap = await app_service.delete_app(sf, app_id)
        import shutil
        if snap.get("root_path"):
            shutil.rmtree(snap["root_path"], ignore_errors=True)
    except Exception:
        logger.warning("商店安装回滚：删记录/目录失败 app_id=%s", app_id, exc_info=True)


async def run_install_for_application(sf, application: dict, notify=None) -> dict:
    """端点入口（202 后执行）：成功回写 target_app_id，失败回写 failed。不向上抛。

    为什么不抛：`_spawn` 的 done 回调只记日志，抛出去前端就停在「安装中」；事件全部按
    **来源应用 id** 下发（新实例 id 在受理时还不存在，审批人正看着来源应用页）。
    """
    from app.store import applications as apps_svc
    try:
        rel = await _release_by_id(sf, application["release_id"])
        app = await install_release(sf, release_row=rel,
                                    applicant_actor_id=application["applicant_id"],
                                    want_name=application["want_name"], notify=notify)
        async with sf() as s:
            await s.execute(text("UPDATE release_application SET target_app_id=:t, "
                                 "updated_at=now() WHERE id=:i"),
                            {"t": app["id"], "i": application["id"]})
            await s.commit()
        return app
    except Exception as e:
        logger.warning("商店安装失败 application=%s", application.get("id"), exc_info=True)
        try:
            await apps_svc.mark_failed(sf, application["id"], str(e))
        except Exception:
            logger.exception("回写 failed 状态也失败 application=%s", application.get("id"))
        return {"ok": False, "error": str(e)}


async def _release_by_id(sf, release_id: int) -> dict:
    async with sf() as s:
        r = (await s.execute(text(
            "SELECT id, app_id, slug, version, title, stack, anchor, fingerprint, status "
            "FROM app_release WHERE id = :i"), {"i": release_id})).mappings().first()
    if not r:
        raise DeployError("版本不存在", status=404)
    return dict(r)
