import dataclasses, difflib, json, os
from pathlib import Path
from typing import Any, Awaitable, Callable
from sqlalchemy import text

MAX_READ_BYTES = 200 * 1024
BINARY_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip", ".gz", ".exe", ".dll", ".so", ".bin", ".db", ".sqlite", ".mp3", ".mp4"}

class ToolError(Exception):
    pass

def resolve_in_workspace(root: Path, rel: str | None) -> Path:
    if rel is None or str(rel).strip() in ("", "."):
        return root
    p = Path(rel)
    if p.is_absolute():
        raise ToolError(f"必须是相对工作区的相对路径: {rel}")
    full = (root / p).resolve()
    root_r = root.resolve()
    if full != root_r and root_r not in full.parents:
        raise ToolError(f"路径越界: {rel}")
    return full

@dataclasses.dataclass
class ToolContext:
    root: Path
    session_id: int
    session_factory: Any
    send: Callable[[dict], Awaitable[None]]

def _unified_diff(path: str, before: str, after: str) -> str:
    return "".join(difflib.unified_diff(
        before.splitlines(keepends=True), after.splitlines(keepends=True),
        fromfile=f"a/{path}", tofile=f"b/{path}"))

async def _record_change(ctx: ToolContext, path: str, action: str, before: str, after: str, msg_id: int | None = None):
    diff = _unified_diff(path, before, after)
    if ctx.session_factory is not None:
        async with ctx.session_factory() as s:
            await s.execute(text(
                "INSERT INTO file_change (session_id, message_id, path, action, before_content, after_content, diff) "
                "VALUES (:sid, :mid, :p, :a, :b, :af, :d)"),
                {"sid": ctx.session_id, "mid": msg_id, "p": path, "a": action,
                 "b": before, "af": after, "d": diff})
            await s.commit()
    await ctx.send({"type": "file_change", "path": path, "action": action, "diff": diff})

async def execute_tool(ctx: ToolContext, name: str, args: dict) -> dict:
    if name == "list_dir":
        d = resolve_in_workspace(ctx.root, args.get("path"))
        if not d.is_dir():
            raise ToolError(f"目录不存在: {args.get('path')}")
        entries = []
        for child in sorted(d.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            if child.name.startswith("."):
                continue
            entries.append({"name": child.name, "type": "dir" if child.is_dir() else "file",
                            "size": child.stat().st_size if child.is_file() else None})
        return {"path": args.get("path") or ".", "entries": entries[:200]}

    if name == "read_file":
        f = resolve_in_workspace(ctx.root, args.get("path"))
        if not f.is_file():
            raise ToolError(f"文件不存在: {args.get('path')}")
        if f.suffix.lower() in BINARY_EXTS:
            raise ToolError(f"不支持读取二进制文件: {args.get('path')}")
        size = f.stat().st_size
        if size > MAX_READ_BYTES:
            raise ToolError(f"文件超过 200KB（{size} 字节），请用 edit_file 分段处理")
        return {"path": args.get("path"), "content": f.read_text(encoding="utf-8", errors="replace"), "size": size}

    if name == "write_file":
        rel = args.get("path")
        f = resolve_in_workspace(ctx.root, rel)
        if f == Path(ctx.root).resolve() or f.is_dir():
            raise ToolError(f"路径必须是文件: {args.get('path')}")
        content = str(args.get("content") or "")
        existed = f.exists()
        try:
            before = f.read_text(encoding="utf-8") if existed else ""
        except UnicodeDecodeError:
            raise ToolError(f"文件不是 UTF-8 编码，拒绝覆盖: {args.get('path')}")
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content, encoding="utf-8")
        await _record_change(ctx, str(f.relative_to(ctx.root)), "edit" if existed else "create", before, content)
        return {"ok": True, "path": str(f.relative_to(ctx.root)), "bytes": len(content.encode("utf-8"))}

    if name == "edit_file":
        rel = args.get("path")
        f = resolve_in_workspace(ctx.root, rel)
        if not f.is_file():
            raise ToolError(f"文件不存在: {args.get('path')}")
        if f.suffix.lower() in BINARY_EXTS:
            raise ToolError(f"不支持编辑二进制文件: {args.get('path')}")
        old, new = str(args.get("old_string")), str(args.get("new_string"))
        try:
            content = f.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            raise ToolError(f"文件不是 UTF-8 编码，拒绝编辑: {args.get('path')}")
        n = content.count(old)
        if n == 0:
            raise ToolError("old_string 未找到匹配")
        if n > 1:
            raise ToolError(f"old_string 匹配 {n} 处，必须唯一；请扩大上下文后重试")
        after = content.replace(old, new, 1)
        f.write_text(after, encoding="utf-8")
        await _record_change(ctx, str(f.relative_to(ctx.root)), "edit", content, after)
        return {"ok": True, "path": str(f.relative_to(ctx.root))}

    raise ToolError(f"未知工具: {name}")
