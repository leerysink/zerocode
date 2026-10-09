import dataclasses, difflib, json, os
from pathlib import Path
from typing import Any, Awaitable, Callable
from sqlalchemy import text

MAX_READ_BYTES = 200 * 1024
BINARY_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip", ".gz", ".exe", ".dll", ".so", ".bin", ".db", ".sqlite", ".mp3", ".mp4"}
# 技能母本受控读面文本白名单（故事11）：references/assets 常见形态，非此列表一律拒
SKILL_READ_EXTS = {".md", ".txt", ".json", ".csv", ".yaml", ".yml"}
# memory_write 的操作面与查重窗口（ticket 003 / D-4：查重只给建议，不硬拦）
MEMORY_OPS = ("create", "update", "delete")
MEMORY_DEDUPE_TOP_K = 3
MEMORY_PREVIEW_CHARS = 80

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


def resolve_skill_path(skills_root: Path | None, abs_path: str | None) -> Path | None:
    """技能目录受控读面（spec §5 / 故事11）：把 <available_skills> 给出的**绝对路径**
    收敛到当前用户技能根内。命中返回 Path；非绝对/根缺失/越出技能根/resolve 失败（死链接、
    reparse 点）一律返回 None，由调用方回落到既有工作区越界报错——绝不开第二读面（§4 双保险）。
    仅 read_file 使用；写类工具不调用本函数（母本对 Agent 只读）。
    """
    if not skills_root or not abs_path:
        return None
    p = Path(abs_path)
    if not p.is_absolute():
        return None
    try:
        root_r = Path(skills_root).resolve()
        full = p.resolve()
    except (OSError, RuntimeError, ValueError):  # 死链接/reparse 点/空字节路径都归 None
        return None
    if root_r != full and root_r not in full.parents:
        return None
    return full


def is_skill_scripts(path: Path, root: Path) -> bool:
    """技能母本 scripts/ 子树检测（故事11：脚本不在 v1 消费面）。

    只对**技能根内的相对段**判定，不看整条绝对路径（复审 I-1）——否则祖先
    目录（如仓库 clone 进名为 scripts 的父目录、或 ZEROCODE_DATA_DIR 含 scripts 段）
    同名会误拒整个读面。path 不在 root 内时返回 False（调用方已围栏，理论不可达）。
    """
    try:
        rel_parts = path.relative_to(root).parts
    except ValueError:
        return False
    return "scripts" in rel_parts


@dataclasses.dataclass
class ToolContext:
    root: Path
    session_id: int
    session_factory: Any
    send: Callable[[dict], Awaitable[None]]
    skills_root: Path | None = None  # 该用户技能母本根（受控读面）；None = 无技能读面，存量调用不受影响

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


def _memory_target_id(args: dict):
    """update / delete 的目标 id：缺字段直接拒，空串/畸形值留给服务层报「非法的记忆 id」。

    这里绝不抢先做 int 转换（大整数 id 全程按字符串传，全局红线），也不能把 None 悄悄当成
    合法值交给服务层——那会让报错文案指向不明的类型错误。
    """
    raw = args.get("id")
    if raw is None:
        raise ToolError("memory_write 的 update / delete 必须给出记忆 id（create 返回的字符串 id）")
    return raw

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
        rel = args.get("path")
        try:
            f = resolve_in_workspace(ctx.root, rel)
        except ToolError as ws_err:
            # 工作区解析失败：回落技能目录受控读面（<available_skills> 给的是绝对路径）；
            # 仍不在技能根内则维持原越界/绝对路径报错，不开第二读面（spec §4 双保险）
            sk = resolve_skill_path(ctx.skills_root, rel)
            if sk is None:
                raise ws_err
            if not sk.is_file():
                raise ToolError(f"文件不存在: {rel}")
            if is_skill_scripts(sk, Path(ctx.skills_root).resolve()):
                raise ToolError(f"技能 scripts/ 目录不在读取范围: {rel}")
            if sk.suffix.lower() not in SKILL_READ_EXTS:
                raise ToolError(
                    f"技能目录仅可读文本类文件（{'/'.join(sorted(SKILL_READ_EXTS))}）: {rel}")
            size = sk.stat().st_size
            if size > MAX_READ_BYTES:
                raise ToolError(f"文件超过 200KB（{size} 字节）: {rel}")
            return {"path": rel, "content": sk.read_text(encoding="utf-8", errors="replace"), "size": size}
        # M-3（终审 M43）：deploy/** 含凭据明文，AI 读面同样收口（口令不得进模型上下文/对话历史外发 LLM）；
        # 调用顺序与 write/edit 一致：先 resolve 拒越界，后 guard。app.json 读放行（契约要求 AI 看清单）。
        from app.deploy.guard import is_secret_read
        if is_secret_read(Path(ctx.root), rel):
            raise ToolError("deploy/ 为平台保留区，凭据不可读取")
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
        from app.deploy.guard import is_protected
        if is_protected(Path(ctx.root), rel):
            raise ToolError("app.json 与 deploy/ 由平台管理，AI 不可修改")
        if f == Path(ctx.root).resolve() or f.is_dir():
            raise ToolError(f"路径必须是文件: {args.get('path')}")
        content = str(args.get("content") or "")
        existed = f.exists()
        try:
            before = f.read_text(encoding="utf-8") if existed else ""
        except UnicodeDecodeError:
            raise ToolError(f"文件不是 UTF-8 编码，拒绝覆盖: {args.get('path')}")
        f.parent.mkdir(parents=True, exist_ok=True)
        # newline='' 字节保真，避免 Windows 行尾翻转污染目标文件
        f.write_text(content, encoding="utf-8", newline="")
        await _record_change(ctx, str(f.relative_to(ctx.root)), "edit" if existed else "create", before, content)
        return {"ok": True, "path": str(f.relative_to(ctx.root)), "bytes": len(content.encode("utf-8"))}

    if name == "edit_file":
        rel = args.get("path")
        f = resolve_in_workspace(ctx.root, rel)
        from app.deploy.guard import is_protected
        if is_protected(Path(ctx.root), rel):
            raise ToolError("app.json 与 deploy/ 由平台管理，AI 不可修改")
        if not f.is_file():
            raise ToolError(f"文件不存在: {args.get('path')}")
        if f.suffix.lower() in BINARY_EXTS:
            raise ToolError(f"不支持编辑二进制文件: {args.get('path')}")
        old, new = str(args.get("old_string")), str(args.get("new_string"))
        try:
            # newline='' 字节保真：读取保留原 \r\n 不做统一换行，替换后原样写回
            with open(f, "r", encoding="utf-8", newline="") as fh:
                content = fh.read()
        except UnicodeDecodeError:
            raise ToolError(f"文件不是 UTF-8 编码，拒绝编辑: {args.get('path')}")
        n = content.count(old)
        if n == 0:
            raise ToolError("old_string 未找到匹配")
        if n > 1:
            raise ToolError(f"old_string 匹配 {n} 处，必须唯一；请扩大上下文后重试")
        after = content.replace(old, new, 1)
        # newline='' 字节保真，避免 Windows 行尾翻转污染目标文件
        with open(f, "w", encoding="utf-8", newline="") as fh:
            fh.write(after)
        await _record_change(ctx, str(f.relative_to(ctx.root)), "edit", content, after)
        return {"ok": True, "path": str(f.relative_to(ctx.root))}

    if name == "todo_write":
        raw = args.get("todos")
        if not isinstance(raw, list):
            raise ToolError("todos 必须是数组")
        todos = []
        for t in raw:
            st = t.get("status")
            if st not in ("pending", "in_progress", "completed"):
                raise ToolError(f"非法任务状态: {st}")
            todos.append({"content": str(t.get("content", "")), "status": st})
        if ctx.session_factory is not None:
            async with ctx.session_factory() as s:
                await s.execute(text(
                    "UPDATE chat_session SET todo_state = CAST(:j AS JSONB) WHERE id = :i"),
                    {"j": json.dumps(todos, ensure_ascii=False), "i": ctx.session_id})
                await s.commit()
        await ctx.send({"type": "todo_sync", "todos": todos})
        return {"ok": True, "count": len(todos)}

    if name == "memory_write":
        # 记忆归属的工作区只能由 session 反查：模型递 workspace_id 就是跨工作区写入的入口（故事 18）
        if "workspace_id" in args:
            raise ToolError("memory_write 不接受 workspace_id：记忆归属由当前会话的工作区决定")
        # 只对大小写宽容（模型爱把枚举写成大写），首尾空白视为畸形参数明确拒绝，不做静默修正
        op = str(args.get("op") or "").lower()
        if op not in MEMORY_OPS:
            raise ToolError(f"非法的记忆操作 {op!r}，可选：{' / '.join(MEMORY_OPS)}")
        if ctx.session_factory is None:
            raise ToolError("memory_write 需要会话数据库上下文，当前工具上下文不具备")
        from app.deploy.errors import DeployError
        from app.services import memory_service as mem
        similar: list[dict] = []
        try:
            ws = await mem.workspace_id_of_session(ctx.session_factory, ctx.session_id)
            if ws is None:
                raise ToolError(f"当前会话不存在或未关联工作区（session_id={ctx.session_id}），记忆无处可存")
            content = args.get("content")
            title = args.get("title")
            secrets = neutralized = 0
            if op in ("create", "update"):
                # 工具层先过一道闸，再拿净化后的文本去查重：查重是带 content 做 SQL 查询的，
                # 顺序反了就等于把明文口令塞进绑定参数与语句日志里（服务层那一道是幂等的）。
                # 两类命中分开累加：遮凭据和改标签是两件事，合并计数会谎报「遮了密码」。
                # 非字符串原样透传（计数全 0），仍由服务层给「必须是字符串」的业务错误。
                content_outcome = mem.sanitize_memory_text(content)
                title_outcome = mem.sanitize_memory_text(title)
                content, title = content_outcome.text, title_outcome.text
                secrets = content_outcome.secrets + title_outcome.secrets
                neutralized = content_outcome.tags + title_outcome.tags
            if op == "create":
                # 查重在落库之前：命中的必然是既有条目；非字符串 content 不进切词，交服务层报业务错误
                if isinstance(content, str) and content.strip():
                    # 查重走 dedupe_policy，不吃 `memory.*` 用户配置：关掉注入（enabled=false）
                    # 只该停止往 prompt 里塞记忆，不该连带把防堆积建议一起关掉（故事 10）。
                    similar = await mem.recall_memories(
                        ctx.session_factory, ws, content,
                        policy=mem.dedupe_policy(MEMORY_DEDUPE_TOP_K))
                row = await mem.create(ctx.session_factory, ws, args.get("type"), content,
                                       title=title)
            elif op == "update":
                row = await mem.update(ctx.session_factory, ws, _memory_target_id(args),
                                       type=args.get("type"), content=content,
                                       title=title)
            else:
                row = await mem.delete(ctx.session_factory, ws, _memory_target_id(args))
        except DeployError as e:
            # 服务层的业务文案原样回给模型（含可选类型清单、"记忆不存在"），不能糊成「工具执行异常」
            raise ToolError(str(e)) from e
        result = {"ok": True, "op": op, "id": str(row["id"]),
                  "type": row["type"], "enabled": row["enabled"]}
        if similar:  # D-4：给出既有条目与改用 update 的建议，但新条照常入库
            result["similar"] = [{"id": str(m["id"]), "title": m["title"] or "",
                                  "preview": (m["content"] or "")[:MEMORY_PREVIEW_CHARS]}
                                 for m in similar]
            result["hint"] = ("已存在相似记忆；若只是修订请改用 op=update 并带上对应 id，"
                              "避免同一约定堆积多条")
        if secrets:  # 没遮任何东西就不冒这组字段，否则模型会以为自己的话被莫名改了
            result["redacted"] = secrets
            result["redaction_note"] = (f"已脱敏 {secrets} 处凭据（占位符 <redacted:kind>），"
                                        "原值不入库；需要凭据时从密钥服务或环境变量现取")
        if neutralized:  # 改标签≠遮凭据：文案里不能出现「凭据」，否则模型会误判自己写了密码
            result["neutralized"] = neutralized
            result["neutralization_note"] = (f"已中和 {neutralized} 处记忆块标记"
                                             "（</memories> 改写为 ＜/memories＞），"
                                             "避免单条记忆提前闭合注入块")
        return result

    raise ToolError(f"未知工具: {name}")
