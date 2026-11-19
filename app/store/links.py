# app/store/links.py
"""商店实例的退出动作（规格 2026-11-19 §4.4，CONTEXT「卸载与解绑」）。

解绑＝只断 link + 清 origin，应用转普通自有（容器/库/源码全留）。
卸载＝停栈 → 删库删角色 → 删平台记录（link/会话随 CASCADE）→ 删源码目录。
与安装回滚路径（install._teardown 吞错 best-effort）不同：卸载是用户主动操作，
删库/删目录的硬失败必须聚合回报，不静默假绿；停栈告警随结果 surfaced 但不阻断清理。
"""
import logging
import shutil
from sqlalchemy import text
from app.deploy import engine, provision
from app.deploy.errors import DeployError
from app.services import app_service

logger = logging.getLogger(__name__)


async def _owned_store_app(sf, app_id: int, actor_id: int) -> dict:
    app, role = await app_service.get_app_for_user(sf, app_id, actor_id)
    app_service.require_owner(role)
    if not app.get("store"):
        raise DeployError("该应用不是商店实例", status=404)
    return app


async def unlink(sf, *, app_id: int, actor_id: int) -> dict:
    await _owned_store_app(sf, app_id, actor_id)
    async with sf() as s:
        await s.execute(text("DELETE FROM app_instance_link WHERE app_id=:i"), {"i": app_id})
        await s.execute(text("UPDATE app_project SET origin=NULL WHERE id=:i"), {"i": app_id})
        await s.commit()
    return {"ok": True}


async def uninstall(sf, *, app_id: int, actor_id: int) -> dict:
    app = await _owned_store_app(sf, app_id, actor_id)
    slug = app["slug"]
    warnings = await engine.down_app_stacks(slug)
    problems = []
    for env in ("dev", "prod"):
        try:
            await provision.drop_env(sf, slug, env)
        except Exception as e:
            problems.append(f"删除 {env} 库失败: {e}")
    # 删记录失败（如发布中）直接抛：记录还在，用户可重试；此时不删目录
    snap = await app_service.delete_app(sf, app_id)
    root = snap.get("root_path")
    if root:
        try:
            shutil.rmtree(root)
        except Exception as e:
            problems.append(f"删除源码目录失败: {e}")
    if problems:
        raise DeployError("卸载未完全成功：" + "；".join(problems), status=500)
    return {"ok": True, "warnings": warnings}
