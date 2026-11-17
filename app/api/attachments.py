"""聊天附件上传/读取。

上传走原始字节（不引入 python-multipart）；类型以魔数为准，不信任客户端声明。
"""
from urllib.parse import quote

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy import text

from app.services import app_service, attachment_service as att

logger = logging.getLogger(__name__)

router = APIRouter()


async def _session_role(request: Request, session_id: int) -> str | None:
    """会话 → 工作区 → 角色的单点判权（Ticket 000）：附件按「所聊会话的可见性」判，
    会话本身经 workspace_role 收口，不在这里再拼一遍归属 JOIN。"""
    sf = request.app.state.session_factory
    async with sf() as s:
        wid = (await s.execute(text(
            "SELECT workspace_id FROM chat_session WHERE id=:i"),
            {"i": session_id})).scalar()
    if wid is None:
        return None
    return await app_service.workspace_role(sf, wid, request.state.actor["id"])


@router.post("/api/sessions/{session_id}/attachments", status_code=201)
async def upload_attachment(request: Request, session_id: int, filename: str = ""):
    uid = request.state.actor["id"]
    ctype = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    if not ctype.startswith("image/"):
        raise HTTPException(400, "仅支持图片文件")
    sf = request.app.state.session_factory
    async with sf() as s:
        role = await _session_role(request, session_id)
        if role is None:
            raise HTTPException(404, "会话不存在")
        if role != "owner":
            # 上传仅所有者（规格 §2.6/§5.4）：被共享者已可见会话 → 403 诚实口径
            raise HTTPException(403, "仅应用所有者可执行此操作")
        staged = await att.staged_bytes(sf, session_id)
    if staged >= att.MAX_MESSAGE_BYTES:
        raise HTTPException(413, "单条消息图片合计不能超过 10MB")

    chunks, total = [], 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > att.MAX_ATTACHMENT_BYTES:
            raise HTTPException(413, "单张图片不能超过 5MB")
        chunks.append(chunk)
    data = b"".join(chunks)
    if not data:
        raise HTTPException(400, "图片内容为空")
    mime = att.sniff_image_mime(data[:32])
    if mime is None:
        raise HTTPException(400, "不支持的文件类型（仅 PNG/JPEG/WebP/GIF）")
    if staged + len(data) > att.MAX_MESSAGE_BYTES:
        raise HTTPException(413, "单条消息图片合计不能超过 10MB")

    try:
        return await att.create_attachment(
            sf, uid, session_id, att.safe_filename(filename, mime), data, mime)
    except att.AttachmentError as e:
        raise HTTPException(e.status_code, str(e))
    except OSError:
        # 只记日志，不回显：OSError 文案带数据区绝对路径，泄露给 owner 也无必要
        logger.exception("附件落盘失败 session=%s", session_id)
        raise HTTPException(500, "数据区不可写，请稍后重试")


@router.get("/api/attachments/{attachment_id}")
async def get_attachment(request: Request, attachment_id: int):
    sf = request.app.state.session_factory
    async with sf() as s:
        row = (await s.execute(text(
            "SELECT a.storage_path, a.mime, a.filename, a.session_id FROM message_attachment a "
            "WHERE a.id=:i"), {"i": attachment_id})).mappings().first()
        role = await _session_role(request, row["session_id"]) if row else None
    if not row or role is None:
        raise HTTPException(404, "附件不存在")
    path = att.resolve_storage_path(row["storage_path"])
    if path is None or not path.is_file():
        raise HTTPException(404, "附件不存在")
    disposition = f"inline; filename*=UTF-8''{quote(row['filename'])}"
    return FileResponse(
        path, media_type=row["mime"],
        headers={
            "Content-Disposition": disposition,
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, max-age=31536000, immutable",
        })
