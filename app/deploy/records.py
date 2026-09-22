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
            # 终审 I-1 记账层守卫：已 success 的行不可被降级为 failed——keep-N 名额只认「有 success
            # 记录」，success 被 failed 覆盖会让在服版本的镜像被当失败版清掉（晋升是不可撤销点）。
            # 但 success→success 的 detail 再合并（如补写 health/url）仍合法，故只挡降级方向。
            "WHERE id = :i AND (status <> 'success' OR :st = 'success')"),
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
    """平台重启对账（04 §6）：上次崩溃遗留的 running 发布/回滚/商店记录标 failed+interrupted。

    install/release 与 publish/rollback 同性质——都是 _spawn 后台任务，中断后不收回执就会
    永远 running，把该应用的互斥闸门（_spawn 409）和版本清理名额一起卡死。"""
    async with sf() as s:
        res = await s.execute(text(
            "UPDATE deploy_record SET status='failed', step='interrupted', finished_at=now(), "
            "error='平台重启中断' WHERE status='running' "
            "AND kind IN ('publish','rollback','install','release')"))
        await s.commit()
        return res.rowcount


async def versions_for_cleanup(sf, app_id: int, keep: int) -> list[str]:
    """可清理的旧版本 tag 清单（字典序=时间序）：只数 publish 过的版本，rollback/start 记录不算数。

    口径（审查 I-1 裁定）：`keep` 名额**只给发布成功过的版本**——失败版若占名额，每个
    「preflight 就失败、从没 build 出镜像」的版本都会挤掉一个真实可回滚的镜像；只有失败
    记录的发布版本**无条件入清理清单**（镜像无用）。同一版本可能既有 failed 又有 success
    （重发），以「是否有 success 记录」为准归好版本（取并语义）。
    仍在 running 的版本既不占名额也不交出去（镜像可能正在 build）；它终会收口成 success/
    failed——崩溃遗留由 mark_interrupted 兜底为 failed，之后即可被清理。
    """
    async with sf() as s:
        rows = (await s.execute(text(
            "SELECT DISTINCT version, status FROM deploy_record "
            "WHERE app_id = :a AND kind = 'publish' AND version IS NOT NULL "
            "ORDER BY version DESC"), {"a": app_id})).all()
    statuses: dict[str, set[str]] = {}          # 按 version DESC 插入，迭代序即时间倒序
    for version, status in rows:
        statuses.setdefault(version, set()).add(status)
    good = [v for v, st in statuses.items() if "success" in st]
    kept = set(good[:max(int(keep), 1)])        # keep<=0 仍至少留一版，永不清光
    in_flight = {v for v, st in statuses.items() if "running" in st}
    return [v for v in statuses if v not in kept and v not in in_flight]
