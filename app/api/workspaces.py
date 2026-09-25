import os
from pathlib import Path
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

router = APIRouter(prefix="/api")

class WsIn(BaseModel):
    name: str | None = None
    root_path: str

class BrowseIn(BaseModel):
    path: str | None = None

@router.post("/workspaces")
async def create_workspace(request: Request, body: WsIn):
    root = Path(body.root_path)
    if not root.is_dir():
        raise HTTPException(400, "目录不存在")
    # 查重-插入段：并发竞态时另一请求可能已插入同 (owner, root_path)，DB 联合 UNIQUE 抛 IntegrityError，统一转 409
    # 扩展态（Ticket 003）：查重按当前用户隔离，跨用户同目录各自登记（guard 已保证 actor 存在）
    owner_id = request.state.actor["id"]
    try:
        async with request.app.state.session_factory() as s:
            dup = (await s.execute(text(
                "SELECT id FROM workspace WHERE root_path = :p "
                "AND owner_user_id IS NOT DISTINCT FROM :o"),
                {"p": str(root), "o": owner_id})).first()
            if dup:
                raise HTTPException(409, "该目录已存在")
            name = body.name or root.name
            row = (await s.execute(text(
                "INSERT INTO workspace (name, root_path, owner_user_id) "
                "VALUES (:n, :p, :o) RETURNING id, name, root_path, created_at"),
                {"n": name, "p": str(root), "o": owner_id})).mappings().first()
            await s.commit()
    except IntegrityError:
        # async with 退出时自动回滚未 commit 的事务，直接转 409 即可
        raise HTTPException(409, "该目录已存在")
    return dict(row)

@router.get("/workspaces")
async def list_workspaces(request: Request):
    async with request.app.state.session_factory() as s:
        rows = (await s.execute(text(
            "SELECT w.id, w.name, w.root_path, w.created_at, "
            "(SELECT count(*) FROM chat_session cs WHERE cs.workspace_id = w.id) AS session_count "
            "FROM workspace w ORDER BY w.id DESC"))).mappings().all()
    return [dict(r) for r in rows]

@router.delete("/workspaces/{ws_id}")
async def delete_workspace(request: Request, ws_id: int):
    async with request.app.state.session_factory() as s:
        await s.execute(text("DELETE FROM workspace WHERE id = :i"), {"i": ws_id})
        await s.commit()
    return {"ok": True}

@router.post("/fs/browse")
async def fs_browse(request: Request, body: BrowseIn):
    import string
    raw = (body.path or "").strip()
    if not raw:
        drives = [f"{d}:\\" for d in string.ascii_uppercase if Path(f"{d}:\\").exists()]
        return {"path": "", "parent": None, "entries": [{"name": d, "path": d, "is_dir": True} for d in drives]}
    p = Path(raw)
    if not p.is_dir():
        raise HTTPException(400, "目录不存在")
    entries = []
    try:
        for child in sorted(p.iterdir(), key=lambda x: x.name.lower()):
            if child.is_dir():
                entries.append({"name": child.name, "path": str(child), "is_dir": True})
    except PermissionError:
        pass
    return {"path": str(p), "parent": str(p.parent) if p.parent != p else None, "entries": entries}
