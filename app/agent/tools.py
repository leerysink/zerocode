import asyncio, dataclasses, difflib, fnmatch, json, os, shutil
from pathlib import Path
from typing import Any, Awaitable, Callable
from sqlalchemy import text

from app.agent import registry
from app.agent.result_budget import apply_budget
from app import workspace_ignore

MAX_READ_BYTES = 200 * 1024
# 目录分页上限：默认一页 200，模型显式要求也只给到 500（再多的目录该用 search 收窄，不该翻页硬啃）。
DEFAULT_LIST_LIMIT = 200
MAX_LIST_LIMIT = 500
# 检索硬上限：命中 200 条 / 扫 5000 个文件后停止并明确告知——检索的价值在「快速定位」，不在「导出全库」。
MAX_SEARCH_MATCHES = 200
MAX_SEARCH_FILES = 5000
MAX_SEARCH_LINE_CHARS = 200
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
    # ---- 预览工具（规格 docs/specs/2026-10-29-agent-preview-tools.md §5）----
    # 全部带默认值：非应用工作区与既有构造点（loop 之外的测试）一律不受影响。
    app: dict | None = None          # run 开始时解析出的应用行；None = 预览工具不挂载
    cfg: dict = dataclasses.field(default_factory=dict)          # 本轮配置快照（agent.preview_* 阈值）
    cancel: asyncio.Event | None = None                          # 本轮取消信号：等待循环据此尽快返回
    deploy_hub: Any = None           # DeployHub：订阅 dev_log/dev_status 转成聊天进度
    deploy_tasks: dict = dataclasses.field(default_factory=dict)  # app.state.dev_tasks（与 REST 共用同一张表）
    run_state: dict = dataclasses.field(default_factory=dict)     # 单次 run 的计数（preview_restarts），不落库
    # 视觉诊断（规格 2026-10-31 §8）：当前 run 实际模型的能力位与实例；缺省 None 时
    # preview_page 跳过诊断并明确告知，不影响其余结果（既有构造点与单测零改动）。
    model_cfg: dict | None = None
    llm: Any = None

def _unified_diff(path: str, before: str, after: str) -> str:
    return "".join(difflib.unified_diff(
        before.splitlines(keepends=True), after.splitlines(keepends=True),
        fromfile=f"a/{path}", tofile=f"b/{path}"))

async def _record_change(ctx: ToolContext, path: str, action: str, before: str | None,
                         after: str | None, msg_id: int | None = None, diff: str | None = None):
    # 会话侧凭据遮断（Ticket 010 第四出口）：before/after/diff 会整体落平台库 file_change
    # 并把 diff 推给 WebSocket——这是 tool_args 三出口之外、把用户写进工作区的口令复制进
    # 平台侧的第四个明文出口。用户真正要写的明文留在工作区文件里（那是用户自己的域、
    # 且已在本收口之前写盘），平台侧历史/事件只保留可看懂的改动结构（键名、非敏感上下文）。
    # 复用同一张规则表（memory_service.redact_sensitive），不另抄正则；先遮 before/after
    # 再据此算 diff，保证三列互相对应、不会一列遮了一列没遮。
    from app.services.memory_service import redact_sensitive
    # before/after 为 None 表示「未存原文」（二进制/超限文件、不解析内容的 move）：
    # 三列一起留空，回滚侧据此拒绝——用空串冒充原文会让回滚把文件写成空的。
    if before is not None:
        before, _ = redact_sensitive(before)
    if after is not None:
        after, _ = redact_sensitive(after)
    if diff is None:
        diff = _unified_diff(path, before or "", after or "") if (before or after) else ""
    change_id = None
    if ctx.session_factory is not None:
        async with ctx.session_factory() as s:
            # RETURNING id：事件里要带上这一行的 id，聊天流里的内联 diff 才跳得进详情整页
            # （地址是 /sessions/{会话}/changes/{记录}）。不落库的调用点没有 id 可给，如实给 None——
            # 前端据此不渲染跳转入口，而不是给一个点了没反应的按钮。
            change_id = (await s.execute(text(
                "INSERT INTO file_change (session_id, message_id, path, action, before_content, after_content, diff) "
                "VALUES (:sid, :mid, :p, :a, :b, :af, :d) RETURNING id"),
                {"sid": ctx.session_id, "mid": msg_id, "p": path, "a": action,
                 "b": before, "af": after, "d": diff})).scalar()
            await s.commit()
    await ctx.send({"type": "file_change", "path": path, "action": action, "diff": diff,
                    "change_id": change_id})


def _memory_target_id(args: dict):
    """update / delete 的目标 id：缺字段直接拒，空串/畸形值留给服务层报「非法的记忆 id」。

    这里绝不抢先做 int 转换（大整数 id 全程按字符串传，全局红线），也不能把 None 悄悄当成
    合法值交给服务层——那会让报错文案指向不明的类型错误。
    """
    raw = args.get("id")
    if raw is None:
        raise ToolError("memory_write 的 update / delete 必须给出记忆 id（create 返回的字符串 id）")
    return raw

async def execute_tool(ctx: ToolContext, name: str, args: dict, *, confirmed: bool = False) -> dict:
    """按注册表分派并施加结果预算。

    这是工具结果的**唯一出口**：会话循环落库、推给前端、回注模型的三份内容都取自这里
    返回的同一个对象（ADR-0029 实时流与历史必须同形）。所以预算也放在这里做，
    任何 handler 都不必自己截断。
    """
    spec = registry.spec_of(name)
    handler = registry.resolve_handler(spec) if spec is not None else None
    if handler is None:
        # ask_user / clarify_done 由会话循环处理（要拿 ask/confirm/run_state），不在这里；
        # 未知名字同样落到这条——模型看得到工具清单，递上来的名字不该是别的。
        raise ToolError(f"未知工具: {name}")
    result = await handler(ctx, args, confirmed=confirmed)
    result, _info = apply_budget(result, spec.result_policy)
    return result


def _int_arg(args: dict, key: str, default: int | None) -> int | None:
    """可选整数参数：缺省/空串取默认，非法值明确报错。

    不能把写错的 offset 静默当成 0——那会让模型以为在读第 300 行、实际拿回第一段，
    并据此得出错误结论。
    """
    raw = args.get(key)
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise ToolError(f"{key} 必须是整数，收到 {raw!r}")


def _slice_lines(text: str, offset: int, limit: int | None):
    """按行切一段 → (content, total_lines, start_line, end_line, truncated_by_bytes, notice)。

    字节上限只在**行边界**切：否则返回的最后半行没有对应 offset，下一段会悄悄跳过它。
    单行自身就超过上限时才在行内硬切，并明说「剩余部分无法续读」。
    """
    lines = text.splitlines(keepends=True)
    total = len(lines)
    if offset < 1:
        raise ToolError(f"offset 必须从 1 开始（收到 {offset}）")
    if total == 0:
        if offset > 1:
            raise ToolError(f"offset {offset} 超出文件行数（文件为空）")
        return "", 0, 1, 0, False, None
    if offset > total:
        raise ToolError(f"offset {offset} 超出文件行数（共 {total} 行）")
    end = total if limit is None else min(total, offset + limit - 1)
    chunk = "".join(lines[offset - 1:end])
    if len(chunk.encode("utf-8")) > MAX_READ_BYTES:
        cut = chunk.encode("utf-8")[:MAX_READ_BYTES].decode("utf-8", errors="ignore")
        if "\n" in cut:
            chunk = cut[:cut.rfind("\n") + 1]
            end = offset + chunk.count("\n") - 1
            notice = (f"内容超过单次读取上限，已返回第 {offset}-{end} 行，共 {total} 行；"
                      f"继续请用 offset={end + 1}")
        else:
            # 单行超长：行内截断，没有可续读的下一行
            chunk, end = cut, offset
            notice = ("该行超过单次读取上限，已在行内截断；剩余部分无法用 offset 续读，"
                      "请用 search 定位具体片段或改用 edit_file 精确修改")
        return chunk, total, offset, end, True, notice
    notice = None
    if offset > 1 or end < total:
        notice = f"已返回第 {offset}-{end} 行，共 {total} 行；继续请用 offset={end + 1}"
    return chunk, total, offset, end, False, notice


async def _handle_list_dir(ctx: ToolContext, args: dict, *, confirmed: bool = False) -> dict:
    d = resolve_in_workspace(ctx.root, args.get("path"))
    if not d.is_dir():
        raise ToolError(f"目录不存在: {args.get('path')}")
    entries = []
    for child in sorted(d.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
        if child.name.startswith("."):
            continue
        entries.append({"name": child.name, "type": "dir" if child.is_dir() else "file",
                        "size": child.stat().st_size if child.is_file() else None})
    total = len(entries)
    offset = _int_arg(args, "offset", 0)
    limit = _int_arg(args, "limit", DEFAULT_LIST_LIMIT)
    if offset < 0:
        raise ToolError("offset 必须是非负整数")
    if limit < 1:
        raise ToolError("limit 必须是正整数")
    limit = min(limit, MAX_LIST_LIMIT)
    page = entries[offset:offset + limit]
    nxt = offset + len(page)
    return {"path": args.get("path") or ".", "entries": page, "total_entries": total,
            "next_offset": nxt if nxt < total else None}


def _iter_workspace_files(base: Path, root: Path, stats: dict):
    """遍历 base 下的文件并剪掉忽略目录；stats 记跳过的目录数（由调用方展示给模型）。"""
    for dirpath, dirnames, filenames in os.walk(base):
        rel_dir = Path(dirpath).relative_to(root)
        kept = []
        for name in sorted(dirnames):
            parts = (rel_dir / name).parts if rel_dir.parts else (name,)
            if name.startswith(".") or name == "deploy" or workspace_ignore.is_ignored_dir(parts):
                stats["skipped_dirs"] += 1
                continue
            kept.append(name)
        dirnames[:] = kept
        for name in sorted(filenames):
            if name.startswith("."):
                continue
            fp = Path(dirpath) / name
            yield fp, fp.relative_to(root).as_posix()


async def _handle_search(ctx: ToolContext, args: dict, *, confirmed: bool = False) -> dict:
    mode = str(args.get("mode") or "").strip()
    pattern = str(args.get("pattern") or "").strip()
    if mode not in ("content", "files"):
        raise ToolError("mode 只接受 content（搜内容）或 files（找文件）")
    if not pattern:
        raise ToolError("pattern 不能为空")
    base = resolve_in_workspace(ctx.root, args.get("path"))
    if base.is_file():
        raise ToolError("path 必须是目录；找单个文件请用 mode=files")
    if not base.is_dir():
        raise ToolError(f"目录不存在: {args.get('path')}")
    root = Path(ctx.root).resolve()
    base = base.resolve()
    needle = pattern.lower()
    matches: list = []
    stats = {"skipped_dirs": 0, "skipped_files": 0}
    notes: list[str] = []
    truncated = False
    scanned = 0
    for fp, rel in _iter_workspace_files(base, root, stats):
        if len(matches) >= MAX_SEARCH_MATCHES:
            truncated = True
            notes.append(f"命中超过 {MAX_SEARCH_MATCHES} 条已截断，请收窄 pattern 或 path")
            break
        if mode == "files":
            if (fnmatch.fnmatch(rel.lower(), needle)
                    or fnmatch.fnmatch(fp.name.lower(), needle)):
                matches.append(rel)
            continue
        try:
            size = fp.stat().st_size
        except OSError:
            stats["skipped_files"] += 1
            continue
        if fp.suffix.lower() in BINARY_EXTS or size > MAX_READ_BYTES:
            stats["skipped_files"] += 1
            continue
        scanned += 1
        if scanned > MAX_SEARCH_FILES:
            truncated = True
            notes.append(f"已扫描 {MAX_SEARCH_FILES} 个文件后停止，请用 path 收窄范围")
            break
        try:
            text = fp.read_text(encoding="utf-8", errors="replace")
        except OSError:
            stats["skipped_files"] += 1
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if needle in line.lower():
                matches.append({"path": rel, "line": i,
                                "text": line.strip()[:MAX_SEARCH_LINE_CHARS]})
                if len(matches) >= MAX_SEARCH_MATCHES:
                    break
    if stats["skipped_dirs"] or stats["skipped_files"]:
        notes.append(f"已按平台规则跳过 {stats['skipped_dirs']} 个目录、"
                     f"{stats['skipped_files']} 个文件（忽略表 / deploy/ / 二进制 / 超大文件）")
    return {"mode": mode, "pattern": pattern, "matches": matches, "count": len(matches),
            "truncated": truncated, "skipped_dirs": stats["skipped_dirs"],
            "skipped_files": stats["skipped_files"], "note": "；".join(notes)}



async def _handle_read_file(ctx: ToolContext, args: dict, *, confirmed: bool = False) -> dict:
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
    offset = _int_arg(args, "offset", 1)
    limit = _int_arg(args, "limit", None)
    if limit is not None and limit < 1:
        raise ToolError("limit 必须是正整数")
    size = f.stat().st_size
    content, total, start, end, truncated, notice = _slice_lines(
        f.read_text(encoding="utf-8", errors="replace"), offset, limit)
    out = {"path": args.get("path"), "content": content, "size": size,
           "total_lines": total, "start_line": start, "end_line": end,
           "remaining_lines": max(0, total - end), "truncated_by_bytes": truncated}
    if notice:
        out["notice"] = notice
    return out


async def _handle_write_file(ctx: ToolContext, args: dict, *, confirmed: bool = False) -> dict:
    rel = args.get("path")
    f = resolve_in_workspace(ctx.root, rel)
    from app.deploy.guard import is_protected
    if is_protected(Path(ctx.root), rel):
        raise ToolError("app.json 与 deploy/ 由平台管理，AI 不可修改")
    if f == Path(ctx.root).resolve() or f.is_dir():
        raise ToolError(f"路径必须是文件: {args.get('path')}")
    content = str(args.get("content") or "")
    # 第五出口衍生护栏（Ticket 010）：跨轮历史里的 tool_result 是净化版占位符，
    # 模型若把它当作真实内容写回，会把 <redacted:kind> 字面量静默写坏工作区文件。
    # 工具执行收的是原始 args，故只能在写盘入口拦下带平台保留标记的内容。
    from app.agent.redact import contains_redaction_marker
    if contains_redaction_marker(content):
        raise ToolError("content 含平台遮断占位符 <redacted:...>，那不是文件真实内容；"
                        "请先 read_file 重新读取该文件取原文后再写入")
    existed = f.exists()
    try:
        before = f.read_text(encoding="utf-8") if existed else ""
    except UnicodeDecodeError:
        raise ToolError(f"文件不是 UTF-8 编码，拒绝覆盖: {args.get('path')}")
    f.parent.mkdir(parents=True, exist_ok=True)
    # newline='' 字节保真，避免 Windows 行尾翻转污染目标文件
    f.write_text(content, encoding="utf-8", newline="")
    path_rel = _rel_of(ctx.root, f)
    await _record_change(ctx, path_rel, "edit" if existed else "create", before, content)
    return {"ok": True, "path": path_rel, "bytes": len(content.encode("utf-8"))}


async def _handle_edit_file(ctx: ToolContext, args: dict, *, confirmed: bool = False) -> dict:
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
    # 第五出口衍生护栏（Ticket 010）：old/new 任一侧含遮断占位符都拒绝——否则拿占位符
    # 去匹配真实内容必然错位，或把占位符字面量写进文件（跨轮历史里 tool_result 已净化）。
    from app.agent.redact import contains_redaction_marker
    if contains_redaction_marker(old) or contains_redaction_marker(new):
        raise ToolError("old_string/new_string 含平台遮断占位符 <redacted:...>，那不是文件真实内容；"
                        "请先 read_file 重新读取该文件取原文后再编辑")
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
    path_rel = _rel_of(ctx.root, f)
    await _record_change(ctx, path_rel, "edit", content, after)
    return {"ok": True, "path": path_rel}


def _rel_of(root: Path, f: Path) -> str:
    """记录/回参统一的相对路径口径：正斜杠（与模型输入、search 产出一致，跨平台可回查）。"""
    return f.relative_to(Path(root).resolve()).as_posix()


def _read_text_keep_eol(f: Path, limit: int | None = None) -> str | None:
    """按字节保真读文本（newline=''）；二进制/超限/非 UTF-8 一律 None = 未存原文。"""
    try:
        if limit is not None and f.stat().st_size > limit:
            return None
        with open(f, "r", encoding="utf-8", newline="") as fh:
            return fh.read()
    except (UnicodeDecodeError, OSError):
        return None


async def _handle_delete_file(ctx: ToolContext, args: dict, *, confirmed: bool = False) -> dict:
    rel = args.get("path")
    f = resolve_in_workspace(ctx.root, rel)
    from app.deploy.guard import is_protected
    if is_protected(Path(ctx.root), rel):
        raise ToolError("app.json 与 deploy/ 由平台管理，AI 不可删除")
    if f.is_dir():
        # 递归删除的爆炸半径不允许由一次工具调用决定；空目录也不删（不替用户做清理决定）。
        raise ToolError(f"只能删除文件，不递归删除目录: {rel}")
    if not f.is_file():
        raise ToolError(f"文件不存在: {rel}")
    before = None
    if f.suffix.lower() not in BINARY_EXTS:
        before = _read_text_keep_eol(f, MAX_READ_BYTES)
    f.unlink()
    path_rel = _rel_of(ctx.root, f)
    await _record_change(ctx, path_rel, "delete", before, "",
                         diff=None if before is not None else f"已删除 {path_rel}（未存原文，不可回滚）")
    out = {"ok": True, "path": path_rel, "revertible": before is not None}
    if before is None:
        out["notice"] = "二进制或超过 200KB 的文件未存原文，不可回滚"
    return out


async def _handle_move_file(ctx: ToolContext, args: dict, *, confirmed: bool = False) -> dict:
    old_rel, new_rel = args.get("old_path"), args.get("new_path")
    old = resolve_in_workspace(ctx.root, old_rel)
    new = resolve_in_workspace(ctx.root, new_rel)
    from app.deploy.guard import is_protected
    for rel in (old_rel, new_rel):
        if is_protected(Path(ctx.root), rel):
            raise ToolError("app.json 与 deploy/ 由平台管理，AI 不可移动")
    if old == Path(ctx.root).resolve() or old.is_dir():
        raise ToolError(f"只能移动文件，不移动目录: {old_rel}")
    if not old.is_file():
        raise ToolError(f"文件不存在: {old_rel}")
    if new.exists():
        raise ToolError(f"目标已存在，拒绝覆盖: {new_rel}（请先删除或换一个名字）")
    new.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(old), str(new))
    old_rel, new_rel = _rel_of(ctx.root, old), _rel_of(ctx.root, new)
    await _record_change(ctx, old_rel, "move", None, None,
                         diff=f"移动: {old_rel} → {new_rel}")
    return {"ok": True, "from": old_rel, "to": new_rel}


async def _handle_revert_change(ctx: ToolContext, args: dict, *, confirmed: bool = False) -> dict:
    rel = args.get("path")
    f = resolve_in_workspace(ctx.root, rel)
    from app.deploy.guard import is_protected
    if is_protected(Path(ctx.root), rel):
        raise ToolError("app.json 与 deploy/ 由平台管理，AI 不可回滚")
    if ctx.session_factory is None:
        raise ToolError("当前会话没有变更记录存储，无法回滚")
    path_rel = _rel_of(ctx.root, f)
    async with ctx.session_factory() as s:
        row = (await s.execute(text(
            "SELECT id, action, before_content, after_content FROM file_change "
            "WHERE session_id=:sid AND path=:p AND before_content IS NOT NULL "
            "ORDER BY id DESC LIMIT 1"),
            {"sid": ctx.session_id, "p": path_rel})).mappings().first()
    if row is None:
        raise ToolError(f"没有可回滚的改动记录: {rel}"
                        "（本会话没改过它，或那条记录未存原文→不可回滚）")
    after = row["after_content"] or ""
    current = _read_text_keep_eol(f) if f.is_file() else None
    if current is None and f.is_file():
        raise ToolError("文件不是 UTF-8 编码，无法比对，已拒绝回滚")
    # 写前比对：磁盘必须仍停在该记录的 after_content 上（删除后「不存在」等价于空串）。
    if (after == "" and current not in (None, "")) or (after != "" and current != after):
        raise ToolError("文件当前内容与该记录不一致（本会话之后又被改过或移动），"
                       "已拒绝回滚以免覆盖这些改动")
    before = row["before_content"]
    if before == "":
        # 撤销「新建」：原文是「文件不存在」，写回空串会留一个空壳文件，故直接删掉。
        if not f.exists():
            raise ToolError(f"磁盘上已没有该文件，无需回滚: {rel}")
        f.unlink()
        await _record_change(ctx, path_rel, "delete", current or "", "",
                             diff=f"回滚：撤销新建 {path_rel}")
        return {"ok": True, "path": path_rel, "change_id": row["id"], "deleted": True}
    existed = f.is_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    with open(f, "w", encoding="utf-8", newline="") as fh:
        fh.write(before)
    await _record_change(ctx, path_rel, "edit" if existed else "create", current or "", before)
    return {"ok": True, "path": path_rel, "change_id": row["id"], "deleted": False}


async def _handle_todo_write(ctx: ToolContext, args: dict, *, confirmed: bool = False) -> dict:
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


async def _handle_memory_write(ctx: ToolContext, args: dict, *, confirmed: bool = False) -> dict:
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

