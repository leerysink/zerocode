"""preview_page 截图读取（规格 2026-10-31 §7）：按会话归属授权，不靠文件名不可猜。"""
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy import text

from app.services import browser_shot_service as shots

router = APIRouter()


@router.get("/api/browser/shots/{session_id}/{filename}")
async def get_browser_shot(request: Request, session_id: int, filename: str):
    uid = request.state.actor["id"]
    async with request.app.state.session_factory() as s:
        owns = (await s.execute(text(
            "SELECT 1 FROM chat_session cs JOIN workspace w ON w.id = cs.workspace_id "
            "WHERE cs.id=:i AND w.owner_user_id=:u"), {"i": session_id, "u": uid})).first()
    path = shots.resolve(session_id, filename) if owns else None
    if path is None:
        raise HTTPException(404, "截图不存在")
    return FileResponse(
        path, media_type="image/png",
        headers={"Cache-Control": "private, max-age=31536000, immutable",
                 "X-Content-Type-Options": "nosniff"})
