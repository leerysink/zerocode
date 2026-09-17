from pathlib import Path
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from app.agent.tools import BINARY_EXTS, MAX_READ_BYTES, resolve_in_workspace

router = APIRouter(prefix="/api")
IGNORE_DIRS = {".git", "node_modules", "__pycache__", "dist", ".venv", "build"}

async def _root_of(request: Request, ws_id: int) -> Path:
    from sqlalchemy import text
    async with request.app.state.session_factory() as s:
        row = (await s.execute(text("SELECT root_path FROM workspace WHERE id=:i"), {"i": ws_id})).first()
    if not row:
        raise HTTPException(404, "工作区不存在")
    return Path(row[0])

@router.get("/fs/tree")
async def fs_tree(request: Request, workspace_id: int):
    root = await _root_of(request, workspace_id)

    def build(d: Path, depth: int) -> list:
        if depth > 8:
            return []
        out = []
        try:
            children = sorted(d.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))
        except (PermissionError, OSError):
            return []
        for child in children[:500]:
            if child.is_dir():
                if child.name in IGNORE_DIRS:
                    continue
                out.append({"name": child.name, "path": str(child.relative_to(root)),
                            "type": "dir", "children": build(child, depth + 1)})
            else:
                out.append({"name": child.name, "path": str(child.relative_to(root)), "type": "file"})
        return out

    return build(root, 0)

@router.get("/files/read")
async def files_read(request: Request, workspace_id: int, path: str):
    root = await _root_of(request, workspace_id)
    try:
        f = resolve_in_workspace(root, path)
    except Exception as e:
        raise HTTPException(400, str(e))
    if not f.is_file():
        raise HTTPException(404, "文件不存在")
    if f.suffix.lower() in BINARY_EXTS or f.stat().st_size > MAX_READ_BYTES:
        raise HTTPException(400, "不支持读取的文件（二进制或超过200KB）")
    return {"path": path, "content": f.read_text(encoding="utf-8", errors="replace")}

class SaveIn(BaseModel):
    workspace_id: int
    path: str
    content: str

@router.put("/files/save")
async def files_save(request: Request, body: SaveIn):
    root = await _root_of(request, body.workspace_id)
    try:
        f = resolve_in_workspace(root, body.path)
    except Exception as e:
        raise HTTPException(400, str(e))
    f.parent.mkdir(parents=True, exist_ok=True)
    # newline='' 字节保真，避免 Windows 行尾翻转污染目标文件
    f.write_text(body.content, encoding="utf-8", newline="")
    return {"ok": True}
