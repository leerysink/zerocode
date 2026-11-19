# app/store/shots.py
"""商店截图（规格 2026-11-19 §4.3）：作者上传的应用展示图。

落盘 store/releases/<release_id>/shot-<n>.<ext>，DB 只存相对路径列表（JSONB）。
截图住 store 目录、不进应用工作区/包/source_hash——补图不触发分叉误报
（同 backend/uploads 判运行时数据的教训）。URL 里的序号是列表位置，不是文件名。
"""
import json
from pathlib import Path
from sqlalchemy import text
from app.deploy import shell
from app.deploy.errors import DeployError
from app.deploy.settings import deploy_cfg
from app.store.release import store_root

MAX_SHOTS = 5
MAX_BYTES = shell.MOBILE_ICON_MAX_BYTES


def shot_urls(release_id: int, paths: list | None) -> list[str]:
    return [f"/api/store/releases/{release_id}/screenshots/{i}"
            for i in range(len(paths or []))]


async def _release_row(sf, release_id: int) -> dict:
    async with sf() as s:
        row = (await s.execute(text(
            "SELECT id, created_by, screenshots FROM app_release WHERE id=:i"),
            {"i": release_id})).mappings().first()
    if not row:
        raise DeployError("版本不存在", status=404)
    return dict(row)


def _require_owner(row: dict, actor_id: int) -> None:
    if row["created_by"] != actor_id:
        raise DeployError("只能管理自己上架版本的截图", status=403)


async def _save_list(sf, release_id: int, paths: list) -> None:
    async with sf() as s:
        await s.execute(text(
            "UPDATE app_release SET screenshots=CAST(:p AS JSONB) WHERE id=:i"),
            {"p": json.dumps(paths), "i": release_id})
        await s.commit()


async def add_shot(sf, *, release_id: int, actor_id: int, data: bytes) -> dict:
    row = await _release_row(sf, release_id)
    _require_owner(row, actor_id)
    paths = list(row["screenshots"] or [])
    if len(paths) >= MAX_SHOTS:
        raise DeployError(f"截图最多 {MAX_SHOTS} 张", status=400)
    if len(data) > MAX_BYTES:
        raise DeployError("截图不能超过 2MB", status=413)
    if not data:
        raise DeployError("截图文件为空", status=400)
    ext = shell.detect_icon_ext(data)
    if ext is None:
        raise DeployError("只支持 PNG/JPEG 截图", status=400)
    cfg = await deploy_cfg(sf)
    rel_dir = store_root(cfg) / "releases" / str(release_id)
    rel_dir.mkdir(parents=True, exist_ok=True)
    name = f"shot-{len(paths)}.{ext}"
    (rel_dir / name).write_bytes(data)
    paths.append(f"releases/{release_id}/{name}")
    await _save_list(sf, release_id, paths)
    return {"screenshots": shot_urls(release_id, paths)}


async def remove_shot(sf, *, release_id: int, actor_id: int, index: int) -> dict:
    row = await _release_row(sf, release_id)
    _require_owner(row, actor_id)
    paths = list(row["screenshots"] or [])
    if not 0 <= index < len(paths):
        raise DeployError("截图不存在", status=404)
    rel = paths.pop(index)
    cfg = await deploy_cfg(sf)
    (store_root(cfg) / rel).unlink(missing_ok=True)
    await _save_list(sf, release_id, paths)
    return {"screenshots": shot_urls(release_id, paths)}


async def shot_path(sf, *, release_id: int, index: int) -> Path:
    row = await _release_row(sf, release_id)
    paths = list(row["screenshots"] or [])
    if not 0 <= index < len(paths):
        raise DeployError("截图不存在", status=404)
    cfg = await deploy_cfg(sf)
    p = store_root(cfg) / paths[index]
    if not p.is_file():
        raise DeployError("截图文件缺失", status=404)
    return p
