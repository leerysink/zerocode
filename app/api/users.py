# app/api/users.py
"""账号列表 `GET /api/users`（规格 2026-11-16 §5.1）：共享管理选人用。

只暴露 id / nickname / is_founder（D7：全量昵称列表）；口令、会话凭据一概不出库。
任一登录账号可读——这份名单不泄露归属关系，只回答「平台里有哪些人」。
"""
from fastapi import APIRouter, Request
from sqlalchemy import text

router = APIRouter(prefix="/api")


@router.get("/users")
async def list_users(request: Request):
    async with request.app.state.session_factory() as s:
        rows = (await s.execute(text(
            "SELECT id, nickname, is_founder FROM actor ORDER BY nickname, id"))).mappings().all()
    return [dict(r) for r in rows]
