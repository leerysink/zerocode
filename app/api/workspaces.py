import logging, os
from pathlib import Path
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.deploy import engine, gateway_routes
from app.deploy.errors import DeployError
from app.services import app_service

logger = logging.getLogger(__name__)

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
    """自有 + 被共享工作区（规格 2026-11-16 §5.3）：每项带 role。

    被共享以应用为锚（`app_share → app_project.workspace_id`），与 `workspace_role` 同一判据；
    列表用一处 EXISTS 判完，不逐行调 `workspace_role` 变成 N+1。`role` 让内置文件管理器
    刷新后也能自己进只读模式，不依赖从应用页跳转时带参数。
    """
    uid = request.state.actor["id"]
    async with request.app.state.session_factory() as s:
        rows = (await s.execute(text(
            "SELECT w.id, w.name, w.root_path, w.created_at, "
            "(SELECT count(*) FROM chat_session cs WHERE cs.workspace_id = w.id) AS session_count, "
            "CASE WHEN w.owner_user_id = :u THEN 'owner' ELSE 'collaborator' END AS role "
            "FROM workspace w "
            "WHERE w.owner_user_id = :u OR EXISTS ("
            "  SELECT 1 FROM app_share sh JOIN app_project a ON a.id = sh.app_id "
            "  WHERE a.workspace_id = w.id AND sh.user_id = :u) "
            "ORDER BY w.id DESC"),
            {"u": uid})).mappings().all()
    return [dict(r) for r in rows]

@router.delete("/workspaces/{ws_id}")
async def delete_workspace(request: Request, ws_id: int):
    """删工作区；绑定了应用的走与 DELETE /api/apps 同一套收口（ADR-0019）。

    app_project 行靠 FK CASCADE 跟走，端口随记录在库里释放——容器不跟着停就是把同一个端口原封不动
    埋给下一个应用。prod 栈一停网关路由必须同步摘，否则那个域名从「还在跑」变成 502；发布中的应用
    照旧拒删（那条路径正在往这台栈上落版本），闸门与 apps 侧共用 `ensure_deletable`。

    判权走 `workspace_role` 单点（Ticket 000）：删除仅所有者——不可见/不存在/无主同一 404；
    已可见但非所有者（002 起的被共享者）403，与规格 §5.6「已可见无权执行」同口径。
    """
    uid = request.state.actor["id"]
    sf = request.app.state.session_factory
    role = await app_service.workspace_role(sf, ws_id, uid)
    if role is None:
        raise HTTPException(404, "工作区不存在")
    if role != "owner":
        raise HTTPException(403, "仅应用所有者可执行此操作")
    async with sf() as s:
        row = (await s.execute(text(
            "SELECT a.slug, a.status_prod FROM app_project a WHERE a.workspace_id = :i"),
            {"i": ws_id})).mappings().first()
        try:
            app_service.ensure_deletable(dict(row) if row else {})
        except DeployError as e:
            raise HTTPException(e.status, str(e))
        r = await s.execute(text("DELETE FROM workspace WHERE id = :i"), {"i": ws_id})
        await s.commit()
        if r.rowcount == 0:
            # 闸门与删除之间的并发删除：与从未存在同一 404
            raise HTTPException(404, "工作区不存在")
    if row:
        for warning in await engine.down_app_stacks(row["slug"]):
            logger.warning("删除工作区时停栈未净（记录已删）: ws_id=%s %s", ws_id, warning)
        await gateway_routes.refresh(sf)
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
