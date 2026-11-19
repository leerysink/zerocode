# app/store/reconcile.py
"""商店启动对账（规格 M3-a §6.6/§7）。

崩溃遗留的 running upgrade 记录由 records.mark_interrupted（白名单已含 upgrade）兜底；
本模块收口另外两件：残留影子库清理（_shadow 是瞬态，遗留即丢）、受管实例代码分叉对账。
prev 库是有意保留的回滚点，不在此清理（keep-1 由 upgrade 成功路径负责）。
"""
import logging
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from app.deploy import engine, provision, shadow
from app.services import app_service
from app.store import packages

logger = logging.getLogger(__name__)


async def reconcile_store(sf) -> dict:
    dropped = []
    eng = create_async_engine(provision.admin_url(), isolation_level="AUTOCOMMIT")
    try:
        async with eng.connect() as c:
            rows = await c.execute(text(
                "SELECT datname FROM pg_database WHERE datname LIKE '%_shadow'"))
            names = [r[0] for r in rows.fetchall()]
        for name in names:
            try:
                await shadow.discard(name)
                dropped.append(name)
            except Exception:
                logger.warning("残留影子库清理失败: %s", name, exc_info=True)
    finally:
        await eng.dispose()

    detached = []
    async with sf() as s:
        rows = (await s.execute(text(
            "SELECT l.app_id, l.baseline_hash FROM app_instance_link l "
            "WHERE l.channel='managed'"))).mappings().all()
    for r in rows:
        try:
            app = await app_service.get_app(sf, r["app_id"])
            root = await engine._app_root(sf, app)
            if packages.source_hash(root) != r["baseline_hash"]:
                async with sf() as s:
                    await s.execute(text(
                        "UPDATE app_instance_link SET channel='detached', "
                        "detach_reason='code_diverged' WHERE app_id=:i"), {"i": r["app_id"]})
                    await s.commit()
                detached.append(r["app_id"])
        except Exception:
            logger.warning("商店代码分叉对账失败 app_id=%s", r["app_id"], exc_info=True)
    return {"dropped_shadows": dropped, "detached": detached}
