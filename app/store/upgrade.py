# app/store/upgrade.py
"""手动升级（规格 M3-a §6.4）。只作用 <slug>_prod，dev 库不动。

① 前缀校验（包外文件/checksum 异/非前缀 → detached 问人）② advisory lock 防并发
③ 影子克隆 ④ 同凭据预演 + 行数不下降断言 ⑤ 影子改名留回滚点 ⑥ 真实迁移
⑦ 程序切换（build 目标镜像 + prod_start）⑧ 成功回写 link/记录/keep-1 清旧 prev。
失败分两类：⑥ 真迁移失败 → 不动库、程序仍跑旧版；⑦ 体检失败 → 程序回退旧 tag、库保持前进、
prev 留作人工复原入口（不做自动换名回滚）。
"""
import logging
import re
from pathlib import Path
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from app.deploy import compose, engine as deploy_engine, prod as deploy_prod
from app.deploy import provision, records, shadow, sqlrun
from app.deploy.errors import DeployError
from app.deploy.settings import deploy_cfg, build_timeout
from app.services import app_service
from app.store import anchor as anchor_mod, baseline, packages
from app.store import release as release_svc

logger = logging.getLogger(__name__)


async def _release_by_id(sf, release_id: int) -> dict:
    async with sf() as s:
        row = (await s.execute(text(
            "SELECT id, app_id, slug, version, title, stack, anchor, fingerprint, status "
            "FROM app_release WHERE id=:i"), {"i": release_id})).mappings().first()
    if not row:
        raise DeployError("目标版本不存在", status=404)
    return dict(row)


async def _public_counts(url: str) -> dict:
    eng = create_async_engine(url)
    try:
        async with eng.connect() as c:
            tables = [r[0] for r in await c.execute(text(
                "SELECT tablename FROM pg_tables WHERE schemaname='public'"))]
            out = {}
            for t in tables:
                out[t] = (await c.execute(text(f'SELECT count(*) FROM "{t}"'))).scalar()
            return out
    finally:
        await eng.dispose()


def _allow_deletion_tables(pkg: Path, diff: list) -> set:
    """迁移文件内 `-- zc:allow-row-deletion <table>` 白名单：显式声明允许的删除。"""
    allowed = set()
    pat = re.compile(r"--\s*zc:allow-row-deletion\s+(\S+)")
    for e in diff:
        f = pkg / "sqlchange" / e["filename"]
        if f.is_file():
            allowed.update(pat.findall(f.read_text(encoding="utf-8", errors="replace")))
    return allowed


def _prefix_ok(hist: list, anchor: list) -> bool:
    """实例 history 必须是目标 anchor 的前缀，且共有文件 checksum 一致（纪律 1）。"""
    hn, an = anchor_mod.names_of(hist), anchor_mod.names_of(anchor)
    if not anchor_mod.is_prefix(hn, an):
        return False
    amap = {e["filename"]: e["checksum"] for e in anchor}
    return all(amap.get(e["filename"]) == e["checksum"] for e in hist)


async def _mark_detached(sf, app_id: int, reason: str) -> None:
    async with sf() as s:
        await s.execute(text(
            "UPDATE app_instance_link SET channel='detached', detach_reason=:r WHERE app_id=:i"),
            {"r": reason, "i": app_id})
        await s.commit()


async def run_upgrade(sf, *, app: dict, to_release_id: int, notify=None) -> dict:
    app_id = app["id"]
    slug = app["slug"]
    link = await baseline._link_row(sf, app_id)
    if not link:
        raise DeployError("该应用不是商店实例", status=404)
    if link["channel"] != "managed":
        raise DeployError("已定制实例不可升级，请先选择保留定制或丢弃定制回官方版", status=409)
    target = await _release_by_id(sf, to_release_id)
    if target["status"] != "published":
        raise DeployError("目标版本已下架", status=409)
    if target["slug"] != link["release_slug"]:
        raise DeployError("目标版本与实例来源不一致", status=409)
    if target["id"] == link["release_id"]:
        raise DeployError("已是最新版本", status=409)

    cfg = await deploy_cfg(sf)
    pkg = packages.package_dir_for(target, release_svc.store_root(cfg))
    manifest = packages.read_manifest(pkg)
    anchor = manifest["anchor"]
    cred_prod = await provision.read_cred(sf, slug, "prod")
    if not cred_prod:
        raise DeployError("prod 凭据缺失", status=409)
    cred_dev = await provision.read_cred(sf, slug, "dev") or {}
    root = await deploy_engine._app_root(sf, app)
    old_version = app.get("current_version")
    rid = await records.begin(sf, app_id, "upgrade", target["version"])

    def on_log(line: str) -> None:
        if notify:
            notify(app_id, {"app_id": app_id, "type": "dev_log", "line": line})

    admin = create_async_engine(provision.admin_url(), isolation_level="AUTOCOMMIT")
    prev = None
    shadow_db = None
    try:
        async with admin.connect() as lock_conn:
            await lock_conn.execute(text("SELECT pg_advisory_lock(hashtext(:k))"),
                                    {"k": f"zc-mig:{slug}"})
            try:
                # ① 前缀校验
                await records.set_step(sf, rid, "preflight")
                hist = await anchor_mod.read_history(anchor_mod.app_db_url(cred_prod))
                if not _prefix_ok(hist, anchor):
                    await _mark_detached(sf, app_id, "sql_diverged")
                    raise DeployError("实例数据序列与目标版本分叉（包外文件或历史被改），"
                                      "已标记为已定制，请选择保留定制或丢弃定制回官方版", status=409)
                hist_names = set(anchor_mod.names_of(hist))
                diff = [e for e in anchor if e["filename"] not in hist_names]
                if not diff:
                    raise DeployError("已是最新版本", status=409)

                # ③ 影子克隆 + ④ 同凭据预演
                await records.set_step(sf, rid, "rehearse")
                shadow_db = await shadow.clone_prod(slug)
                shadow_cred = {**cred_prod, "db": shadow_db}
                before = await _public_counts(anchor_mod.app_db_url(shadow_cred))
                res = await sqlrun.run_sqlchange(pkg, "prod", shadow_cred, on_log=on_log)
                if not res["ok"]:
                    raise DeployError(f"影子预演失败（退出码 {res['code']}）：{res['log'][-600:]}")
                after = await _public_counts(anchor_mod.app_db_url(shadow_cred))
                allowed = _allow_deletion_tables(pkg, diff)
                dropped = [t for t, n in before.items()
                           if t not in allowed and after.get(t, 0) < n]
                if dropped:
                    raise DeployError(f"预演发现行数下降（未声明允许删除）: {dropped}")

                # ⑤ 改名留回滚点
                prev = await shadow.rename_to_prev(slug, target["version"])
                shadow_db = None   # 已改名，不再以影子名存在

                # ⑥ 真实迁移（单文件原子；失败库停在最后成功文件，程序仍跑旧版）
                await records.set_step(sf, rid, "migrate")
                res2 = await sqlrun.run_sqlchange(pkg, "prod", cred_prod, on_log=on_log)
                if not res2["ok"]:
                    raise DeployError(f"真实迁移失败（退出码 {res2['code']}）：{res2['log'][-600:]}")

                # ⑦ 程序切换：build 目标镜像 → 起栈体检
                await records.set_step(sf, rid, "deploy")
                meta = app_service.load_app_json(root)
                compose.ensure_deploy_files(root, meta, {"dev": cred_dev, "prod": cred_prod},
                                            version=target["version"])
                code, log = await deploy_engine.docker(
                    "compose", "-f", str(root / "deploy" / "prod-compose.yml"), "build",
                    cwd=str(root), on_log=on_log,
                    timeout=build_timeout(cfg, app.get("stack", "")))
                if code != 0:
                    raise DeployError(f"目标镜像构建失败：{log[-600:]}")
                await _set_version(sf, app_id, target["version"])
                try:
                    await deploy_prod.prod_start(sf, await app_service.get_app(sf, app_id),
                                                 notify=notify)
                except DeployError:
                    # 体检失败：程序回退旧 tag，库保持前进，prev 留作人工复原
                    await _rollback_program(sf, app, root, meta, cred_dev, cred_prod, old_version)
                    raise

                # ⑧ 成功回写
                await _write_upgraded_link(sf, app_id=app_id, target=target, manifest=manifest,
                                           anchor=anchor, root=root)
                await records.finish(sf, rid, "success", detail={
                    "release_id": target["id"], "diff": [e["filename"] for e in diff],
                    "prev_db": prev, "version": target["version"]})
                await _cleanup_old_prev(sf, slug, keep=prev)
                return {"ok": True, "version": target["version"], "prev_db": prev}
            finally:
                await lock_conn.execute(text("SELECT pg_advisory_unlock(hashtext(:k))"),
                                        {"k": f"zc-mig:{slug}"})
    except DeployError as e:
        await records.finish(sf, rid, "failed", error=str(e))
        raise
    except Exception as e:
        await records.finish(sf, rid, "failed", error=str(e))
        raise DeployError(f"升级失败: {e}") from e
    finally:
        if shadow_db:
            try:
                await shadow.discard(shadow_db)
            except Exception:
                logger.warning("升级失败后影子库清理失败 slug=%s", slug, exc_info=True)
        await admin.dispose()


async def _set_version(sf, app_id: int, version: str) -> None:
    async with sf() as s:
        await s.execute(text("UPDATE app_project SET current_version=:v, updated_at=now() "
                             "WHERE id=:i"), {"v": version, "i": app_id})
        await s.commit()


async def _rollback_program(sf, app, root, meta, cred_dev, cred_prod, old_version) -> None:
    if not old_version:
        return
    try:
        await _set_version(sf, app["id"], old_version)
        compose.ensure_deploy_files(root, meta, {"dev": cred_dev, "prod": cred_prod},
                                    version=old_version)
        await deploy_engine.docker("compose", "-f", str(root / "deploy" / "prod-compose.yml"),
                                   "up", "-d", cwd=str(root))
    except Exception:
        logger.warning("升级体检失败后程序回退旧版亦失败 app_id=%s", app["id"], exc_info=True)


async def _write_upgraded_link(sf, *, app_id, target, manifest, anchor, root) -> None:
    async with sf() as s:
        await s.execute(text(
            "UPDATE app_instance_link SET release_id=:r, applied_fingerprint=:f, "
            "baseline_manifest=CAST(:m AS JSONB), baseline_hash=:h, last_upgraded_at=now() "
            "WHERE app_id=:i"),
            {"r": target["id"],
             "f": manifest.get("fingerprint") or anchor_mod.fingerprint(anchor),
             "m": install_json(anchor), "h": packages.source_hash(root), "i": app_id})
        await s.commit()


def install_json(v) -> str:
    import json
    return json.dumps(v, ensure_ascii=False)


async def _cleanup_old_prev(sf, slug: str, keep: str) -> None:
    """keep-1：只留本次 prev，清掉该实例更早的 <slug>_prev_*（与镜像 keep-5 对齐）。"""
    eng = create_async_engine(provision.admin_url(), isolation_level="AUTOCOMMIT")
    try:
        async with eng.connect() as c:
            rows = await c.execute(text(
                "SELECT datname FROM pg_database WHERE datname LIKE :p"),
                {"p": f"{slug}_prev_%"})
            for (name,) in rows.fetchall():
                if name != keep:
                    await shadow.discard(name)
    finally:
        await eng.dispose()
