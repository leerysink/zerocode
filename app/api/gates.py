"""平台管理员门禁（ADR-0067）：治理面唯一的特权谓词。

管理员的真身就是 `actor.is_founder`——不设 role 列、不设 is_admin 布尔位（ADR-0067），
所以运营台与治理台共用同一个谓词，只换文案。文案由各调用方原样传入：
「商店管理仅创始人可见」这句在 StoreAdminView 与其测试里被钉住，不在这里顺手改掉。
"""
from fastapi import HTTPException


def is_admin(actor: dict) -> bool:
    return bool(actor.get("is_founder"))


def require_admin(actor: dict, message: str) -> None:
    if not is_admin(actor):
        raise HTTPException(403, message)
