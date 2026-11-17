"""preview_page 截图读取（规格 2026-10-31 §7）：按会话归属授权，不靠文件名不可猜。

判权走 `app_service.workspace_role` 单点（Ticket 000）：截图随会话可见性走——
会话不可见（他人/不存在）与截图不存在同一 404；002 起被共享者读历史时同一条通路生效。
"""
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy import text

from app.services import app_service, browser_shot_service as shots

router = APIRouter()


@router.get("/api/browser/shots/{session_id}/{filename}")
async def get_browser_shot(request: Request, session_id: int, filename: str):
    uid = request.state.actor["id"]
    sf = request.app.state.session_factory
    async with sf() as s:
        wid = (await s.execute(text(
            "SELECT workspace_id FROM chat_session WHERE id=:i"), {"i": session_id})).scalar()
    role = await app_service.workspace_role(sf, wid, uid) if wid is not None else None
    path = shots.resolve(session_id, filename) if role is not None else None
    if path is None:
        raise HTTPException(404, "截图不存在")
    return FileResponse(
        path, media_type="image/png",
        headers={"Cache-Control": "private, max-age=31536000, immutable",
                 "X-Content-Type-Options": "nosniff"})
