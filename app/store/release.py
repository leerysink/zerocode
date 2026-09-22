# app/store/release.py
"""上架（规格 M3-a §6.1）：把「真实跑过的 prod 状态」凝固成不可变版本包。

闸门顺序刻意先查便宜的后查贵的；任何一道不过即抛 DeployError 且不产生包目录。
"""
import json
import logging
from pathlib import Path
from sqlalchemy import text
from app.deploy import prod, records
from app.deploy.errors import DeployError
from app.deploy.provision import read_cred
from app.deploy.settings import REPO_ROOT, deploy_cfg
from app.services import app_service
from app.store import anchor as anchor_mod
from app.store import packages

logger = logging.getLogger(__name__)


def store_root(cfg: dict) -> Path:
    raw = cfg.get("store.root") or ""
    return Path(raw) if raw else REPO_ROOT / "store"


async def _app_root(sf, app: dict) -> Path:
    """取工程目录：复用 apps_root 解析与 workspace 登记，与部署引擎同一口径。"""
    from app.deploy import engine as deploy_engine
    return await deploy_engine._app_root(sf, app)


async def latest_release(sf, slug: str) -> dict | None:
    async with sf() as s:
        r = (await s.execute(text(
            "SELECT id, slug, version, anchor, fingerprint, status FROM app_release "
            "WHERE slug = :s AND status = 'published' ORDER BY id DESC LIMIT 1"),
            {"s": slug})).mappings().first()
    return dict(r) if r else None


async def create_release(sf, *, app_id: int, title: str, summary: str,
                         actor_id: int) -> dict:
    """上架当前在服 prod 版本。返回 app_release 行（含 id/anchor/fingerprint）。"""
    app = await app_service.get_app(sf, app_id)
    slug = app["slug"]
    version = app.get("current_version")
    rid = await records.begin(sf, app_id, "release", version)
    try:
        # 闸门 1：必须是「真实在服的 prod 版本」——没发布过就没有可信的库状态与镜像
        if not version:
            raise DeployError("该应用还没有正式发布过，请先点「发布」再上架商店")
        async with sf() as s:
            ok = (await s.execute(text(
                "SELECT 1 FROM deploy_record WHERE app_id=:a AND kind='publish' "
                "AND status='success' AND version=:v LIMIT 1"),
                {"a": app_id, "v": version})).first()
        if not ok:
            raise DeployError(f"版本 {version} 没有成功的发布记录，不能上架")
        # 闸门 2：镜像在位（包只存源码，但首次启动的加速与回滚目标都指望它）
        for part in ("backend", "frontend"):
            tag = f"zc-{slug}-{part}:prod-{version}"
            if not await prod.image_exists(tag):
                raise DeployError(f"镜像缺失: {tag}（发布后被清理，请重新发布再上架）")
        # 闸门 3：锚点取自 prod 库 history
        cred = await read_cred(sf, slug, "prod")
        if not cred:
            raise DeployError("prod 数据库凭据缺失，无法读取迁移历史", status=409)
        try:
            hist = await anchor_mod.read_history(anchor_mod.app_db_url(cred))
        except Exception as e:
            raise DeployError(f"prod 库迁移历史不可读: {e}")
        if not hist:
            raise DeployError("prod 库没有任何已成功的数据变更文件，无可上架内容")
        # 闸门 4：盘上文件必须与 history 记的 checksum 一致（纪律 1）
        root = await _app_root(sf, app)
        sql_dir = root / "sqlchange"
        on_disk = {e["filename"]: packages_path_md5(sql_dir, e["filename"])
                   for e in hist}
        bad = anchor_mod.mismatches(hist, on_disk)
        if bad:
            raise DeployError(f"以下迁移已应用但被改了/删了，违反「迁移只追加」纪律，"
                              f"请恢复原文件后重试: {bad}（不一致）")
        # 闸门 5：与上一版构成前缀（约束 L）
        prev = await latest_release(sf, slug)
        if prev and not anchor_mod.is_prefix(anchor_mod.names_of(prev["anchor"]),
                                            anchor_mod.names_of(hist)):
            raise DeployError("版本序列不是上一版的前缀（分叉），不能上架；"
                              "如需并行演进请发布为新应用")
        # 通过全部闸门才落包
        cfg = await deploy_cfg(sf)
        manifest_extra = {
            "app_id": app_id, "title": (title or app["name"])[:100],
            "summary": summary or "", "stack": app["stack"],
            "health": app_service.load_app_json(root).get("health", {}),
            "prev_release_id": prev["id"] if prev else None,
        }
        pkg = packages.write_package(
            out_root=store_root(cfg), slug=slug, version=version, app_root=root,
            anchor=hist, manifest_extra=manifest_extra)
        async with sf() as s:
            r = (await s.execute(text(
                "INSERT INTO app_release (app_id, slug, version, title, summary, stack, anchor, "
                "fingerprint, prev_release_id, created_by) VALUES (:a, :sl, :v, :ti, :su, :st, "
                "CAST(:an AS JSONB), :fp, :pv, :cb) "
                "RETURNING id, app_id, slug, version, title, summary, stack, anchor, "
                "fingerprint, prev_release_id, status, created_at"),
                {"a": app_id, "sl": slug, "v": version, "ti": manifest_extra["title"],
                 "su": manifest_extra["summary"], "st": app["stack"],
                 "an": json.dumps(hist, ensure_ascii=False),
                 "fp": anchor_mod.fingerprint(hist),
                 "pv": prev["id"] if prev else None, "cb": actor_id})).mappings().first()
            await s.commit()
        rel = dict(r)
        rel["package_dir"] = str(pkg)
        await records.finish(sf, rid, "success", detail={
            "release_id": rel["id"], "files": len(hist),
            "fingerprint": rel["fingerprint"], "package": str(pkg)})
        return rel
    except DeployError as e:
        await records.finish(sf, rid, "failed", error=str(e))
        raise
    except Exception as e:
        await records.finish(sf, rid, "failed", error=str(e))
        raise DeployError(f"上架失败: {e}") from e


def packages_path_md5(sql_dir: Path, filename: str) -> str:
    """盘上 md5；文件不存在时返回哨兵串，交给 mismatches() 报「文件已不在 sqlchange/」。"""
    f = sql_dir / filename
    return anchor_mod.file_md5(f) if f.is_file() else "__missing__"
