from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

router = APIRouter(prefix="/api/sessions")

class SessionIn(BaseModel):
    workspace_id: int
    title: str | None = None

class TitleIn(BaseModel):
    title: str

@router.post("")
async def create_session(request: Request, body: SessionIn):
    async with request.app.state.session_factory() as s:
        ws = (await s.execute(text("SELECT id FROM workspace WHERE id=:i"), {"i": body.workspace_id})).first()
        if not ws:
            raise HTTPException(404, "工作区不存在")
        row = (await s.execute(text(
            "INSERT INTO chat_session (workspace_id, title) VALUES (:w, :t) RETURNING id, workspace_id, title, status, created_at, updated_at"),
            {"w": body.workspace_id, "t": body.title or "新会话"})).mappings().first()
        await s.commit()
    return dict(row)

@router.get("")
async def list_sessions(request: Request, workspace_id: int):
    async with request.app.state.session_factory() as s:
        rows = (await s.execute(text(
            "SELECT id, workspace_id, title, status, created_at, updated_at FROM chat_session "
            "WHERE workspace_id=:w ORDER BY updated_at DESC"), {"w": workspace_id})).mappings().all()
    return [dict(r) for r in rows]

@router.patch("/{session_id}")
async def rename_session(request: Request, session_id: int, body: TitleIn):
    async with request.app.state.session_factory() as s:
        r = await s.execute(text("UPDATE chat_session SET title=:t, updated_at=now() WHERE id=:i"), {"t": body.title, "i": session_id})
        await s.commit()
        if r.rowcount == 0:
            raise HTTPException(404, "会话不存在")
    return {"ok": True}

@router.delete("/{session_id}")
async def delete_session(request: Request, session_id: int):
    async with request.app.state.session_factory() as s:
        await s.execute(text("DELETE FROM chat_session WHERE id=:i"), {"i": session_id})
        await s.commit()
    return {"ok": True}

@router.get("/{session_id}/messages")
async def get_messages(request: Request, session_id: int):
    async with request.app.state.session_factory() as s:
        rows = (await s.execute(text(
            "SELECT id, seq, role, content, tool_call_id, tool_name, tool_args, tool_result, created_at "
            "FROM message WHERE session_id=:i ORDER BY seq"), {"i": session_id})).mappings().all()
    return [dict(r) for r in rows]

@router.get("/{session_id}/file-changes")
async def list_file_changes(request: Request, session_id: int):
    async with request.app.state.session_factory() as s:
        rows = (await s.execute(text(
            "SELECT id, path, action, diff, created_at FROM file_change "
            "WHERE session_id=:i ORDER BY id DESC"), {"i": session_id})).mappings().all()
    return [dict(r) for r in rows]
