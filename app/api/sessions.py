import asyncio

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from app.agent.model_resolver import resolve_model
from app.services import attachment_service, browser_shot_service, config_service
from app.services.session_title_service import DEFAULT_TITLE

router = APIRouter(prefix="/api/sessions")

class SessionIn(BaseModel):
    workspace_id: int
    title: str | None = None

class TitleIn(BaseModel):
    title: str

class ModelIn(BaseModel):
    model_config_id: int | None = None

async def _require_ws(s, ws_id: int, uid: int) -> None:
    hit = (await s.execute(text(
        "SELECT 1 FROM workspace WHERE id = :i AND owner_user_id = :u"),
        {"i": ws_id, "u": uid})).first()
    if not hit:
        raise HTTPException(404, "工作区不存在")

async def _require_session(s, session_id: int, uid: int) -> None:
    """会话传递隔离（Ticket 005）：会话经 workspace 归属用户，不加列只 JOIN 收口；
    不存在与他人的行同一 404，不给探测留差异。"""
    hit = (await s.execute(text(
        "SELECT 1 FROM chat_session cs JOIN workspace w ON w.id = cs.workspace_id "
        "WHERE cs.id = :i AND w.owner_user_id = :u"),
        {"i": session_id, "u": uid})).first()
    if not hit:
        raise HTTPException(404, "会话不存在")

async def _effective_model_of(app, session_id: int, cfg: dict) -> dict:
    """前端只关心「这条会话最终会用哪个模型、会不会看图」：只回五个字段，
    base_url/api_key 不下发（档案内部凭证不出后端）。"""
    m = await resolve_model(app.state.session_factory, session_id, cfg)
    return {k: m.get(k) for k in ("id", "name", "model", "supports_vision", "source")}

async def _pending_steering_of(app, session_id: int, raw: list) -> list[dict]:
    """排队插话带上附件：先批量取被引用的附件，再按条目形状还原。

    hub.pending 里目前混着裸字符串（老接线）与 dict（带 client_msg_id/attachments），
    两种都要认——字符串按「无附件、无客户端 id」的老形状折叠。"""
    ids = [i for it in raw if isinstance(it, dict) for i in (it.get("attachment_ids") or [])]
    atts = await attachment_service.for_ids(app.state.session_factory, session_id, ids)
    out = []
    for it in raw:
        if isinstance(it, str):
            out.append({"content": it, "client_msg_id": None, "attachments": []})
            continue
        out.append({
            "content": it.get("content") or "",
            "client_msg_id": it.get("client_msg_id"),
            "attachments": [atts[i] for i in (it.get("attachment_ids") or []) if i in atts],
        })
    return out

@router.post("")
async def create_session(request: Request, body: SessionIn):
    uid = request.state.actor["id"]
    async with request.app.state.session_factory() as s:
        await _require_ws(s, body.workspace_id, uid)
        row = (await s.execute(text(
            "INSERT INTO chat_session (workspace_id, title) VALUES (:w, :t) RETURNING id, workspace_id, title, status, created_at, updated_at"),
            {"w": body.workspace_id, "t": body.title or DEFAULT_TITLE})).mappings().first()
        await s.commit()
    return dict(row)

@router.get("")
async def list_sessions(request: Request, workspace_id: int):
    uid = request.state.actor["id"]
    async with request.app.state.session_factory() as s:
        await _require_ws(s, workspace_id, uid)
        rows = (await s.execute(text(
            "SELECT id, workspace_id, title, status, created_at, updated_at FROM chat_session "
            "WHERE workspace_id=:w ORDER BY updated_at DESC"), {"w": workspace_id})).mappings().all()
    return [dict(r) for r in rows]

@router.get("/{session_id}")
async def session_detail(request: Request, session_id: int):
    uid = request.state.actor["id"]
    async with request.app.state.session_factory() as s:
        await _require_session(s, session_id, uid)
        row = (await s.execute(text(
            "SELECT id, workspace_id, title, status, todo_state, model_config_id "
            "FROM chat_session WHERE id=:i"), {"i": session_id})).mappings().first()
        cfg = await config_service.get_all(s, owner_user_id=uid)
    if not row:
        raise HTTPException(404, "会话不存在")
    out = dict(row)
    out["effective_model"] = await _effective_model_of(request.app, session_id, cfg)
    # 执行中排队、尚未注入的插话只活在 hub.pending（没落库）：带上它，刷新才不会
    # 把用户刚说出去的话变没（前端还原成「排队中」气泡）
    hub = getattr(request.app.state, "hub", None)
    out["pending_steering"] = await _pending_steering_of(
        request.app, session_id, list(hub.pending.get(session_id, [])) if hub else [])
    return out

@router.patch("/{session_id}")
async def rename_session(request: Request, session_id: int, body: TitleIn):
    uid = request.state.actor["id"]
    async with request.app.state.session_factory() as s:
        await _require_session(s, session_id, uid)
        r = await s.execute(text("UPDATE chat_session SET title=:t, updated_at=now() WHERE id=:i"), {"t": body.title, "i": session_id})
        await s.commit()
        if r.rowcount == 0:
            raise HTTPException(404, "会话不存在")
    return {"ok": True}

@router.patch("/{session_id}/model")
async def set_session_model(request: Request, session_id: int, body: ModelIn):
    uid = request.state.actor["id"]
    async with request.app.state.session_factory() as s:
        await _require_session(s, session_id, uid)
        if body.model_config_id is not None:
            # 档案存在性按当前账号验（Ticket 007）：别人的档案与不存在同口径 404，不泄露存在性
            # kind 必限 chat（Ticket 005）：会话只能绑对话档案，否则这里返回 ok、
            # resolve_model 随后按 kind='chat' 把它过滤掉静默回落——「已切换」是个假信号。
            ok = (await s.execute(text(
                "SELECT 1 FROM model_config WHERE id=:i AND kind = 'chat' AND owner_user_id=:u"),
                {"i": body.model_config_id, "u": uid})).first()
            if not ok:
                raise HTTPException(404, "模型不存在")
        r = await s.execute(text(
            "UPDATE chat_session SET model_config_id=:m WHERE id=:i"),
            {"m": body.model_config_id, "i": session_id})
        await s.commit()
        if r.rowcount == 0:
            raise HTTPException(404, "会话不存在")
        cfg = await config_service.get_all(s, owner_user_id=uid)
    return {"ok": True,
            "effective_model": await _effective_model_of(request.app, session_id, cfg)}

@router.delete("/{session_id}")
async def delete_session(request: Request, session_id: int):
    uid = request.state.actor["id"]
    async with request.app.state.session_factory() as s:
        await _require_session(s, session_id, uid)
        await s.execute(text("DELETE FROM chat_session WHERE id=:i"), {"i": session_id})
        await s.commit()
    # 兜底删除附件目录：commit 之后执行，remove_session_dir 自身吞掉 OSError，不影响删除响应
    await asyncio.to_thread(attachment_service.remove_session_dir, uid, session_id)
    # preview_page 截图同样随会话删除清理（规格 2026-10-31 §7）
    await asyncio.to_thread(browser_shot_service.remove_session_dir, session_id)
    return {"ok": True}

@router.get("/{session_id}/messages")
async def get_messages(request: Request, session_id: int):
    uid = request.state.actor["id"]
    async with request.app.state.session_factory() as s:
        await _require_session(s, session_id, uid)
        rows = (await s.execute(text(
            "SELECT id, seq, role, content, reasoning, tool_call_id, tool_name, tool_args, tool_result, created_at "
            "FROM message WHERE session_id=:i ORDER BY seq"), {"i": session_id})).mappings().all()
    by_msg = await attachment_service.for_messages(
        request.app.state.session_factory, session_id, [r["id"] for r in rows])
    return [{**dict(r), "attachments": by_msg.get(r["id"], [])} for r in rows]

@router.get("/{session_id}/file-changes")
async def list_file_changes(request: Request, session_id: int):
    uid = request.state.actor["id"]
    async with request.app.state.session_factory() as s:
        await _require_session(s, session_id, uid)
        rows = (await s.execute(text(
            "SELECT id, path, action, diff, created_at FROM file_change "
            "WHERE session_id=:i ORDER BY id DESC"), {"i": session_id})).mappings().all()
    return [dict(r) for r in rows]
