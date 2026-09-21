# app/deploy/records.py
"""deploy_record 记账（规格 04 §4.5）：发布/回滚/启停全程留痕，UI 历史与 keep-N 清理的数据源。"""
import json
from sqlalchemy import text


async def begin(sf, app_id: int, kind: str, version: str | None, step: str = "preflight") -> int:
    # detail 落库即为 `'{}'::jsonb`：UI 历史/清理逻辑可无条件取键，finish 的 jsonb 合并
    # 也因此不必处理「本行从未有过 detail」。COALESCE 仍保留，兜住历史遗留的 NULL 行。
    async with sf() as s:
        rid = (await s.execute(text(
            "INSERT INTO deploy_record (app_id, kind, version, status, step, detail) "
            "VALUES (:a, :k, :v, 'running', :st, '{}'::jsonb) RETURNING id"),
            {"a": app_id, "k": kind, "v": version, "st": step})).scalar()
        await s.commit()
    return rid


async def set_step(sf, rid: int, step: str) -> None:
    async with sf() as s:
        await s.execute(text("UPDATE deploy_record SET step = :st WHERE id = :i"),
                        {"st": step, "i": rid})
        await s.commit()


async def finish(sf, rid: int, status: str, *, step: str | None = None,
                 error: str | None = None, detail: dict | None = None) -> None:
    if status not in ("success", "failed"):
        raise ValueError(f"finish 状态非法: {status}")
    async with sf() as s:
        # 裁定版合并 SQL。注意 text() 里不能写 `:dt::jsonb`——SQLAlchemy 会把 `:dt` 后的
        # `::` 连同名字一起误读（绑定名退化为 'd'），原样留给驱动 → asyncpg 语法错；
        # 故强制类型转换用 CAST(:dt AS jsonb) 写法，语义与裁定式等价。
        await s.execute(text(
            "UPDATE deploy_record SET status = :st, finished_at = now(), "
            "step = COALESCE(:stp, step), error = COALESCE(:err, error), "
            "detail = CASE WHEN CAST(:dt AS jsonb) = '{}'::jsonb THEN detail "
            "ELSE COALESCE(detail, '{}'::jsonb) || CAST(:dt AS jsonb) END "
            "WHERE id = :i"),
            {"st": status, "stp": step, "err": error,
             "dt": json.dumps(detail or {}), "i": rid})
        await s.commit()


async def list_for(sf, app_id: int, limit: int = 50) -> list[dict]:
    async with sf() as s:
        rows = (await s.execute(text(
            "SELECT id, app_id, kind, version, status, step, detail, error, "
            "started_at, finished_at FROM deploy_record WHERE app_id = :a "
            "ORDER BY started_at DESC LIMIT :l"), {"a": app_id, "l": int(limit)})).mappings().all()
    out = []
    for r in rows:
        d = dict(r)
        for k in ("started_at", "finished_at"):
            if d.get(k) is not None:
                d[k] = d[k].isoformat()
        out.append(d)
    return out


async def mark_interrupted(sf) -> int:
    """平台重启对账（04 §6）：上次崩溃遗留的 running 发布/回滚记录标 failed+interrupted。"""
    async with sf() as s:
        res = await s.execute(text(
            "UPDATE deploy_record SET status='failed', step='interrupted', finished_at=now(), "
            "error='平台重启中断' WHERE status='running' AND kind IN ('publish','rollback')"))
        await s.commit()
        return res.rowcount


async def versions_for_cleanup(sf, app_id: int, keep: int) -> list[str]:
    """可清理的旧版本 tag 清单：只数 publish 过的版本（字典序=时间序），rollback/start 记录不算数。"""
    async with sf() as s:
        rows = (await s.execute(text(
            "SELECT DISTINCT version FROM deploy_record "
            "WHERE app_id = :a AND kind = 'publish' AND version IS NOT NULL "
            "ORDER BY version DESC"), {"a": app_id})).scalars().all()
    return [v for v in rows[max(int(keep), 1):]]
