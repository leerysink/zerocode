# app/store/applications.py
"""申请-审批单（规格 M3-a §6.2）。审批通过即触发安装，安装失败置 failed 并留原因，
不回退到 pending：一次批准对应一次真实的资源创建（库/端口/目录），重复批准会撞唯一约束。"""
from datetime import datetime
from sqlalchemy import text
from app.deploy.errors import DeployError

OPEN = ("pending",)
DECIDED = ("approved", "rejected", "failed")


def _row(r) -> dict:
    d = dict(r)
    for k in ("created_at", "updated_at", "decided_at"):
        if d.get(k) is not None:
            d[k] = d[k].isoformat()
    return d


async def create(session_factory, *, release_id: int, applicant_id: int, want_name: str) -> dict:
    name = (want_name or "").strip()
    if not name or len(name) > 100:
        raise DeployError("应用名称需 1-100 字")
    async with session_factory() as s:
        rel = (await s.execute(text(
            "SELECT id, status, title FROM app_release WHERE id = :i"),
            {"i": release_id})).mappings().first()
        if not rel:
            raise DeployError("版本不存在", status=404)
        if rel["status"] != "published":
            raise DeployError("该版本已下架，无法申请", status=409)
        dup = (await s.execute(text(
            "SELECT id, status FROM release_application WHERE release_id = :r "
            "AND applicant_id = :a AND status = 'pending'"),
            {"r": release_id, "a": applicant_id})).mappings().first()
        if dup:
            return _row(dict(dup) | {"release_id": release_id, "want_name": name,
                                     "applicant_id": applicant_id})
        r = (await s.execute(text(
            "INSERT INTO release_application (release_id, applicant_id, want_name) "
            "VALUES (:r, :a, :n) RETURNING id, release_id, applicant_id, want_name, status, "
            "reason, created_at, updated_at"),
            {"r": release_id, "a": applicant_id, "n": name})).mappings().first()
        await s.commit()
    return _row(r)


async def get(session_factory, app_id: int) -> dict:
    async with session_factory() as s:
        r = (await s.execute(text(
            "SELECT q.id, q.release_id, q.applicant_id, q.want_name, q.status, q.reason, "
            "q.target_app_id, q.decided_by, q.created_at, q.updated_at, q.decided_at, "
            "r.slug, r.version, r.title, r.app_id AS source_app_id "
            "FROM release_application q JOIN app_release r ON r.id = q.release_id "
            "WHERE q.id = :i"), {"i": app_id})).mappings().first()
    if not r:
        raise DeployError("申请单不存在", status=404)
    return _row(r)


async def list_box(session_factory, *, box: str, actor_id: int,
                   is_founder: bool = False) -> list[dict]:
    """box=mine：我的申请；box=review：审批队列——审批权收口创始人后，
    创始人看**全部** pending（不再按版本归属过滤），非创始人拿到空列表
    （fail-safe 默认：调用方漏传 is_founder 也不会泄露审批队列）。"""
    async with session_factory() as s:
        if box == "mine":
            rows = (await s.execute(text(
                "SELECT q.id, q.release_id, q.want_name, q.status, q.reason, q.target_app_id, "
                "q.created_at, q.updated_at, r.title, r.version, r.slug, a.nickname AS publisher "
                "FROM release_application q JOIN app_release r ON r.id = q.release_id "
                "LEFT JOIN actor a ON a.id = r.created_by "
                "WHERE q.applicant_id = :a ORDER BY q.id DESC"), {"a": actor_id})).mappings().all()
        elif box == "review":
            if not is_founder:
                return []
            rows = (await s.execute(text(
                "SELECT q.id, q.release_id, q.want_name, q.status, q.reason, q.applicant_id, "
                "q.created_at, q.updated_at, r.title, r.version, r.slug, "
                "ac.nickname AS applicant, pub.nickname AS publisher "
                "FROM release_application q JOIN app_release r ON r.id = q.release_id "
                "LEFT JOIN actor ac ON ac.id = q.applicant_id "
                "LEFT JOIN actor pub ON pub.id = r.created_by "
                "WHERE q.status = 'pending' ORDER BY q.id DESC"))).mappings().all()
        else:
            raise DeployError(f"未知收件箱: {box}")
    return [_row(r) for r in rows]


async def decide(session_factory, *, app_id: int, approver_id: int, approver_is_founder: bool,
                 approve: bool, reason: str) -> dict:
    """只改状态与决议人；安装由调用方（端点）在返回后台执行，失败再回写 failed。

    权限：审批权收口到创始人（平台管理员）——approver_is_founder 由端点从会话 actor
    取用，不接外部输入；其他人（含版本发布者）一律 403（集中管控裁定，见 CONTEXT.md
    「创始人」词条）。批准前「目标 slug 会不会撞库」不在这里查——与 install 重复判定；
    冲突由 app_project.slug 的 UNIQUE 与 install 侧处理。
    """
    async with session_factory() as s:
        row = (await s.execute(text(
            "SELECT q.id, q.status, q.release_id "
            "FROM release_application q WHERE q.id = :i FOR UPDATE"), {"i": app_id}
        )).mappings().first()
        if not row:
            raise DeployError("申请单不存在", status=404)
        if not approver_is_founder:
            raise DeployError("只有平台管理员（创始人）能审批申请", status=403)
        if row["status"] not in OPEN:
            raise DeployError(f"申请已处理（{row['status']}），不可重复决策", status=409)
        if not approve and not (reason or "").strip():
            raise DeployError("驳回必须填写原因")
        status = "approved" if approve else "rejected"
        await s.execute(text(
            "UPDATE release_application SET status=:st, reason=:rs, decided_by=:d, "
            "decided_at=now(), updated_at=now() WHERE id=:i"),
            {"st": status, "rs": (reason or "").strip() or None, "d": approver_id, "i": app_id})
        await s.commit()
    return await get(session_factory, app_id)


async def mark_failed(session_factory, app_id: int, error: str) -> dict:
    async with session_factory() as s:
        await s.execute(text(
            "UPDATE release_application SET status='failed', reason=:e, updated_at=now() "
            "WHERE id=:i"), {"i": app_id, "e": error[:500]})
        await s.commit()
    return await get(session_factory, app_id)
