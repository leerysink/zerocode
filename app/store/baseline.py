# app/store/baseline.py
"""M3-b fork 判定与可升级判定（规格 M3-a §6.5）。

代码分叉：实例源码 hash（packages.source_hash，同一排除表）≠ link.baseline_hash → detached。
可升级：managed 且来源 slug 存在更新的 published 版本。判定不靠人记，机器读 link 与盘上 hash。
"""
from sqlalchemy import text
from app.deploy import engine, provision
from app.deploy.errors import DeployError
from app.deploy.settings import deploy_cfg
from app.store import anchor as anchor_mod
from app.store import packages
from app.store import release as release_svc


async def _link_row(sf, app_id: int) -> dict | None:
    async with sf() as s:
        row = (await s.execute(text(
            "SELECT l.app_id, l.release_id, l.channel, l.detach_reason, l.baseline_hash, "
            "l.baseline_manifest, l.applied_fingerprint, r.slug AS release_slug, "
            "r.version AS release_version FROM app_instance_link l "
            "JOIN app_release r ON r.id = l.release_id WHERE l.app_id = :i"),
            {"i": app_id})).mappings().first()
    return dict(row) if row else None


async def upgrade_info(sf, app: dict) -> dict:
    link = await _link_row(sf, app["id"])
    if not link:
        raise DeployError("该应用不是商店实例", status=404)
    channel, reason = link["channel"], link["detach_reason"]
    if channel == "managed":
        root = await engine._app_root(sf, app)
        if packages.source_hash(root) != link["baseline_hash"]:
            channel, reason = "detached", "code_diverged"
            async with sf() as s:
                await s.execute(text(
                    "UPDATE app_instance_link SET channel='detached', detach_reason='code_diverged' "
                    "WHERE app_id=:i"), {"i": app["id"]})
                await s.commit()
    latest = await release_svc.latest_release(sf, link["release_slug"])
    upgradable = (channel == "managed" and latest is not None
                  and latest["id"] != link["release_id"])
    # schema_drift（§7）：实例 prod history 指纹与所跟版本对不上但代码没动 → 黄条警示
    schema_drift = False
    if channel == "managed":
        try:
            cred = await provision.read_cred(sf, app["slug"], "prod")
            if cred:
                hist = await anchor_mod.read_history(anchor_mod.app_db_url(cred))
                schema_drift = anchor_mod.fingerprint(hist) != link["applied_fingerprint"]
        except Exception:
            schema_drift = False
    return {
        "app_id": app["id"], "channel": channel, "detach_reason": reason,
        "release_id": link["release_id"], "release_version": link["release_version"],
        "release_slug": link["release_slug"], "upgradable": bool(upgradable),
        "schema_drift": schema_drift,
        "latest_release": {"id": latest["id"], "version": latest["version"]} if upgradable else None,
    }


async def resolve_fork(sf, app: dict, choice: str) -> dict:
    """fork「问人」的两个出口（规格 M3-a §6.5）。不提供合并升级第三种。"""
    import shutil
    link = await _link_row(sf, app["id"])
    if not link:
        raise DeployError("该应用不是商店实例", status=404)
    if choice == "keep_custom":
        async with sf() as s:
            await s.execute(text(
                "UPDATE app_instance_link SET channel='detached', detach_reason='choice_keep_custom' "
                "WHERE app_id=:i"), {"i": app["id"]})
            await s.commit()
        return {"ok": True, "channel": "detached"}
    if choice == "reset_to_official":
        latest = await release_svc.latest_release(sf, link["release_slug"])
        if not latest:
            raise DeployError("没有可用的官方版本", status=409)
        cfg = await deploy_cfg(sf)
        pkg = packages.package_dir_for(latest, release_svc.store_root(cfg))
        if not pkg.is_dir():
            raise DeployError(f"官方版本包缺失: {pkg}", status=500)
        manifest = packages.read_manifest(pkg)
        root = await engine._app_root(sf, app)
        # 用包 src/ 覆盖代码目录（保留库）；sqlchange 换成目标锚点快照
        shutil.copytree(pkg / "src", root, dirs_exist_ok=True)
        sql = root / "sqlchange"
        shutil.rmtree(sql, ignore_errors=True)
        if (pkg / "sqlchange").is_dir():
            shutil.copytree(pkg / "sqlchange", sql)
        anchor = manifest["anchor"]
        async with sf() as s:
            await s.execute(text(
                "UPDATE app_instance_link SET channel='managed', detach_reason=NULL, release_id=:r, "
                "baseline_manifest=CAST(:m AS JSONB), baseline_hash=:h, applied_fingerprint=:f "
                "WHERE app_id=:i"),
                {"r": latest["id"], "m": _json(anchor), "h": packages.source_hash(root),
                 "f": manifest.get("fingerprint") or latest["fingerprint"], "i": app["id"]})
            await s.commit()
        return {"ok": True, "channel": "managed", "release_id": latest["id"]}
    raise DeployError("choice 只支持 keep_custom|reset_to_official", status=400)


def _json(v) -> str:
    import json
    return json.dumps(v, ensure_ascii=False)
