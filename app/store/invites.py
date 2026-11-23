"""邀请码存取（ADR-0069）：关门后平台唯一的入口凭据。

一码一号、不过期、可作废——三态全在 `used_at` / `revoked_at` 两个时间戳上，不存枚举列，
免得「状态列与时间戳哪个是真的」这种二次真源出现。

核销刻意做成**条件更新**（`WHERE used_at IS NULL AND revoked_at IS NULL`）而不是
「先 SELECT 再 UPDATE」：后者两人同抢一枚码时会同时通过预检，最后插进两个账号。
调用方必须把 `redeem` 与建号放在**同一个事务**里——失败即整体回滚，才谈得上
「失败不烧码」。
"""
import secrets

from sqlalchemy import text

from app.deploy.errors import DeployError

CODE_BYTES = 16          # 32 位十六进制：与 actor_session.token 同一量级
MASK_TAIL = 4            # 列表只留尾 4 位够人认出是哪一枚，完整明文只出现在签发响应里


def new_code() -> str:
    return secrets.token_hex(CODE_BYTES)


def mask(code: str) -> str:
    return f"····{str(code)[-MASK_TAIL:]}"


async def issue(session, *, created_by: int) -> dict:
    """签一枚码。**明文只在这一次返回**，之后所有读面都走 `mask`。"""
    code = new_code()
    row = (await session.execute(text(
        "INSERT INTO invite_code (code, created_by) VALUES (:c, :u) "
        "RETURNING id, created_at"), {"c": code, "u": created_by})).mappings().first()
    return {"id": row["id"], "code": code, "created_at": row["created_at"]}


async def redeem(session, code: str | None, used_by: int) -> bool:
    """核销一枚码给刚建出来的账号；返回是否抢到。空码/错码/已用/已作废都返回 False。

    不抛异常：调用方要区分「没带码」和「码无效」两种提示的成本，高于这里的便利性——
    注册端点自己按情况出文案，本函数只回答抢没抢到。
    """
    if not code:
        return False
    hit = (await session.execute(text(
        "UPDATE invite_code SET used_by = :u, used_at = now() "
        "WHERE code = :c AND used_at IS NULL AND revoked_at IS NULL RETURNING id"),
        {"c": code, "u": used_by})).first()
    return hit is not None


async def revoke(session, invite_id: int) -> dict:
    """作废一枚未使用的码。已使用的不可作废——那是历史，不是名额。"""
    row = (await session.execute(text(
        "SELECT id, used_at, revoked_at FROM invite_code WHERE id = :i"),
        {"i": invite_id})).mappings().first()
    if row is None:
        raise DeployError("邀请码不存在", status=404)
    if row["used_at"] is not None:
        raise DeployError("该码已被使用，无法作废", status=409)
    if row["revoked_at"] is not None:
        return {"id": row["id"], "status": "revoked"}
    await session.execute(text(
        "UPDATE invite_code SET revoked_at = now() WHERE id = :i"), {"i": invite_id})
    return {"id": row["id"], "status": "revoked"}


async def list_recent(session, limit: int = 50) -> list[dict]:
    """码列表：只回掩码，绝不回明文（泄露明文列表等于把入口重新敞开）。"""
    rows = (await session.execute(text(
        "SELECT i.id, i.code, i.created_at, i.used_at, i.revoked_at, u.nickname AS used_by "
        "FROM invite_code i LEFT JOIN actor u ON u.id = i.used_by "
        "ORDER BY i.created_at DESC, i.id DESC LIMIT :l"), {"l": limit})).mappings().all()
    out = []
    for r in rows:
        if r["used_at"] is not None:
            status = "used"
        elif r["revoked_at"] is not None:
            status = "revoked"
        else:
            status = "unused"
        out.append({"id": r["id"], "code_masked": mask(r["code"]), "status": status,
                    "created_at": r["created_at"], "used_at": r["used_at"],
                    "used_by_nickname": r["used_by"]})
    return out
