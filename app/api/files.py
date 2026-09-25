import subprocess
import sys
from pathlib import Path
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from app.agent.tools import BINARY_EXTS, MAX_READ_BYTES, resolve_in_workspace

router = APIRouter(prefix="/api")
IGNORE_DIRS = {".git", "node_modules", "__pycache__", "dist", ".venv", "build"}
# 大型栈 dev 预览的必然产物：compose 把 backend/ 挂进容器就地跑 mvn，编译产物与 logback 日志
# 直接落在应用工作区里（fastapi 栈的 uvicorn 不产这些）。不收它们，一次预览之后文件树就会被
# 几千个 .class 与滚动的日志页满。
# 按路径尾屏蔽而非裸名：target / logs 都是业务通用词（目标管理、日志页面），按名屏蔽会把用户
# 自己的 src/views/target 一类目录在文件管理器里永久藏掉。这两个产物的落点由「dev 容器把 ../backend
# 挂成 working_dir」与模板里 logging.path 的默认值共同决定（${user.dir}/logs 即 backend/logs）；
# 两者任一变动（拆多模块、改日志目录）都要回来对一眼这两份名单。
# 本名单刻意不含 backend/uploads：上传件不进包也不进指纹（packages.py 的裁定），但用户得在文件
# 管理器里看见自己传上去的东西。“不收”与“不可见”是两件事，想顺手把两边对齐前先读 packages.py 那段。
IGNORE_PATH_TAILS = (("backend", "target"), ("backend", "logs"))


def _is_ignored_dir(rel_parts: tuple) -> bool:
    """某个目录节点是否不进文件树：按名屏蔽的通用名单，加按路径尾屏蔽的构建产物。

    这里判的是目录节点本身（而非 packages.py 那样判文件）：一旦 backend/target 被剪掉，
    它下面的任何东西都递不到，所以只需尾匹配。代价是一条口径差：若 backend/ 下真出现一个叫
    target 的**文件**（maven 不造这种东西），文件树看得见、而包与 source_hash 不收。
    """
    return (rel_parts[-1] in IGNORE_DIRS
            or any(rel_parts[-len(t):] == t for t in IGNORE_PATH_TAILS))

async def _root_of(request: Request, ws_id: int) -> Path:
    """工作区根目录统一接缝（fs_tree/fs_ls/download/read/save 共用）：
    隔离收口（Ticket 005）——不属于自己的工作区与不存在同一 404。"""
    from sqlalchemy import text
    uid = request.state.actor["id"]
    async with request.app.state.session_factory() as s:
        row = (await s.execute(text(
            "SELECT root_path FROM workspace WHERE id=:i AND owner_user_id=:u"),
            {"i": ws_id, "u": uid})).first()
    if not row:
        raise HTTPException(404, "工作区不存在")
    return Path(row[0])

@router.get("/fs/tree")
async def fs_tree(request: Request, workspace_id: int):
    root = await _root_of(request, workspace_id)
    from app.deploy.guard import is_secret_read  # 应用工作区隐藏平台保留区（终审 M-3），普通工作区恒 False 零影响

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
                if _is_ignored_dir(child.relative_to(root).parts) or is_secret_read(root, str(child.relative_to(root))):
                    continue
                out.append({"name": child.name, "path": str(child.relative_to(root)),
                            "type": "dir", "children": build(child, depth + 1)})
            else:
                out.append({"name": child.name, "path": str(child.relative_to(root)), "type": "file"})
        return out

    return build(root, 0)


def _rel_of(p: Path, root: Path) -> str:
    """相对工作区根的 posix 路径（根自身为空串）。前端面包屑按 / 逐级切，不吃 Windows 反斜杠。"""
    return "" if p == root else p.relative_to(root).as_posix()


@router.get("/fs/ls")
async def fs_ls(request: Request, workspace_id: int, path: str = ""):
    """内置「类桌面」文件管理器的数据源：只列单层（不递归、不拉全树），目录优先 + 名称升序。

    忽略判据与 /fs/tree 共用 `_is_ignored_dir`；path 必须是工作区内相对路径，越界/绝对路径 400。
    """
    root = await _root_of(request, workspace_id)
    from app.deploy.guard import is_secret_read
    if is_secret_read(root, path):
        raise HTTPException(403, "deploy/ 由平台管理，凭据不可读取")
    try:
        d = resolve_in_workspace(root, path)
    except Exception as e:
        raise HTTPException(400, str(e))
    if not d.exists():
        raise HTTPException(404, "目录不存在")
    if not d.is_dir():
        raise HTTPException(400, "不是目录")
    try:
        children = sorted(d.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))
    except (PermissionError, OSError):
        children = []  # 无权读时给空列表，与 /fs/tree 同口径（不抛 500）
    entries = []
    for child in children:
        is_dir = child.is_dir()
        if is_dir and (_is_ignored_dir(child.relative_to(root).parts)
                       or is_secret_read(root, _rel_of(child, root))):
            continue
        try:
            st = child.stat()
        except OSError:
            continue  # 悬空软链等读不到元信息的条目直接跳过，不让一行坏数据打挂整页
        entries.append({"name": child.name, "path": _rel_of(child, root), "is_dir": is_dir,
                        "size": 0 if is_dir else st.st_size, "mtime": st.st_mtime})
    return {"path": _rel_of(d, root), "parent": None if d == root else _rel_of(d.parent, root),
            "entries": entries}


@router.get("/fs/download")
async def fs_download(request: Request, workspace_id: int, path: str):
    """单文件下载。与预览不同：这里不限二进制、不限 200KB——那两条限制只服务于文本查看。"""
    root = await _root_of(request, workspace_id)
    from app.deploy.guard import is_secret_read
    if is_secret_read(root, path):
        raise HTTPException(403, "deploy/ 由平台管理，凭据不可下载")
    try:
        f = resolve_in_workspace(root, path)
    except Exception as e:
        raise HTTPException(400, str(e))
    if not f.is_file():  # 目录与不存在统一 404，不给探测留差异
        raise HTTPException(404, "文件不存在")
    return FileResponse(f, filename=f.name)


def open_in_file_manager(target: Path) -> None:
    """分平台调起本机文件管理器。

    刻意用 Popen 且不等退出码：explorer.exe 即便打开成功也返回非 0，等退出码只会把成功判成失败。
    单独成函数是为了让端点测试可替换（见 tests/test_files_api.py），不必真开窗口。
    """
    if sys.platform.startswith("win"):
        subprocess.Popen(["explorer", str(target)])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(target)])
    else:
        subprocess.Popen(["xdg-open", str(target)])


class OpenFolderIn(BaseModel):
    workspace_id: int


@router.post("/fs/open-folder")
async def fs_open_folder(request: Request, body: OpenFolderIn):
    """在后端本机的文件管理器里打开工作区根目录。

    只认 workspace_id（不接受任意路径入参），能打开的目录限于平台已登记的工作区根。
    前提是浏览器与后端同机——这点由前端判定（frontend/src/utils/openFolder.js），
    判错或本机没有图形会话时，前端据本端点的非 2xx 降级到内置文件管理器。
    """
    root = await _root_of(request, body.workspace_id)
    if not root.is_dir():
        raise HTTPException(400, "工作区目录不存在或已被移除")
    try:
        open_in_file_manager(root)
    except OSError as e:
        raise HTTPException(500, f"打开文件夹失败：{e}")
    return {"ok": True, "path": str(root)}

@router.get("/files/read")
async def files_read(request: Request, workspace_id: int, path: str):
    root = await _root_of(request, workspace_id)
    from app.deploy.guard import is_secret_read
    if is_secret_read(root, path):
        raise HTTPException(403, "deploy/ 由平台管理，凭据不可读取")
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
    from app.deploy.guard import is_protected
    if is_protected(root, body.path):
        raise HTTPException(403, "app.json 与 deploy/ 由平台管理，请在应用页操作")
    f.parent.mkdir(parents=True, exist_ok=True)
    # newline='' 字节保真，避免 Windows 行尾翻转污染目标文件
    f.write_text(body.content, encoding="utf-8", newline="")
    return {"ok": True}
