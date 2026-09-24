from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from app.services.session_title_service import DEFAULT_TITLE

router = APIRouter(prefix="/api/sessions")

class SessionIn(BaseModel):
    workspace_id: int
    title: str | None = None

class TitleIn(BaseModel):
    title: str

class ModelIn(BaseModel):
    model_config_id: int | None = None

@router.post("")
async def create_session(request: Request, body: SessionIn):
    async with request.app.state.session_factory() as s:
        ws = (await s.execute(text("SELECT id FROM workspace WHERE id=:i"), {"i": body.workspace_id})).first()
        if not ws:
            raise HTTPException(404, "工作区不存在")
        row = (await s.execute(text(
            "INSERT INTO chat_session (workspace_id, title) VALUES (:w, :t) RETURNING id, workspace_id, title, status, created_at, updated_at"),
            {"w": body.workspace_id, "t": body.title or DEFAULT_TITLE})).mappings().first()
        await s.commit()
    return dict(row)

@router.get("")
async def list_sessions(request: Request, workspace_id: int):
    async with request.app.state.session_factory() as s:
        rows = (await s.execute(text(
            "SELECT id, workspace_id, title, status, created_at, updated_at FROM chat_session "
            "WHERE workspace_id=:w ORDER BY updated_at DESC"), {"w": workspace_id})).mappings().all()
    return [dict(r) for r in rows]

@router.get("/{session_id}")
async def session_detail(request: Request, session_id: int):
    async with request.app.state.session_factory() as s:
        row = (await s.execute(text(
            "SELECT id, workspace_id, title, status, todo_state, model_config_id "
            "FROM chat_session WHERE id=:i"), {"i": session_id})).mappings().first()
    if not row:
        raise HTTPException(404, "会话不存在")
    out = dict(row)
    # 执行中排队、尚未注入的插话只活在 hub.pending（没落库）：带上它，刷新才不会
    # 把用户刚说出去的话变没（前端还原成「排队中」气泡）
    hub = getattr(request.app.state, "hub", None)
    out["pending_steering"] = list(hub.pending.get(session_id, [])) if hub else []
    return out

@router.patch("/{session_id}")
async def rename_session(request: Request, session_id: int, body: TitleIn):
    async with request.app.state.session_factory() as s:
        r = await s.execute(text("UPDATE chat_session SET title=:t, updated_at=now() WHERE id=:i"), {"t": body.title, "i": session_id})
        await s.commit()
        if r.rowcount == 0:
            raise HTTPException(404, "会话不存在")
    return {"ok": True}

@router.patch("/{session_id}/model")
async def set_session_model(request: Request, session_id: int, body: ModelIn):
    async with request.app.state.session_factory() as s:
        if body.model_config_id is not None:
            ok = (await s.execute(text("SELECT 1 FROM model_config WHERE id=:i"),
                                  {"i": body.model_config_id})).first()
            if not ok:
                raise HTTPException(400, "模型不存在")
        r = await s.execute(text(
            "UPDATE chat_session SET model_config_id=:m WHERE id=:i"),
            {"m": body.model_config_id, "i": session_id})
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
            "SELECT id, seq, role, content, reasoning, tool_call_id, tool_name, tool_args, tool_result, created_at "
            "FROM message WHERE session_id=:i ORDER BY seq"), {"i": session_id})).mappings().all()
    return [dict(r) for r in rows]

@router.get("/{session_id}/file-changes")
async def list_file_changes(request: Request, session_id: int):
    async with request.app.state.session_factory() as s:
        rows = (await s.execute(text(
            "SELECT id, path, action, diff, created_at FROM file_change "
            "WHERE session_id=:i ORDER BY id DESC"), {"i": session_id})).mappings().all()
    return [dict(r) for r in rows]
