"""需求澄清模式（规格 docs/specs/2026-11-01-agent-clarify-mode.md，ADR-0032 / ADR-0033）。

门禁判定、提问预算提醒、`docs/` 写入围栏、决策索引扫描都收在这里做成**纯函数**：
loop 只负责把「本轮事实」喂进来、把结论发出去。判定本身不碰 DB、不发事件，
于是「本轮发生过什么 → 放行还是挡回」这张矩阵能一次单测跑完，不必靠拼对话模拟状态。

`run_state`（ToolContext 里的单次 run 计数字典）是本轮事实的唯一载体，与
`preview_restarts`/`browser_checks` 同一性质：不落库、run 结束即失效。会话级的
「自行决定」解除标记不放这里——那要跨 run 生效，由 ChatHub 存（内存态，进程重启即失效，
与挂起点同一条既有代价）。
"""
from __future__ import annotations

from pathlib import Path

# 受门禁约束的工具：只有会改动工作区的写入动作需要「先澄清」。只读工具与提问/声明类
# 工具一律不拦，否则模型在门禁之内无法完成澄清本身。
WRITE_TOOLS = ("write_file", "edit_file")

# clarify_done 的两种收口依据：问过了 / 声明需求本已明确（豁免通道，ADR-0032）
BASIS_CLARIFIED = "clarified"
BASIS_ALREADY_CLEAR = "already_clear"

# run_state 里的键
ASKS_KEY = "clarify_asks"
DONE_KEY = "clarify_done"
WAIVED_KEY = "clarify_waived"

# 默认值只应有一处（与 deploy/settings.py 同规矩）
DEFAULT_ENABLED = True
DEFAULT_REMIND_AT = 6
DEFAULT_INDEX_MAX = 12
SUMMARY_MAX_CHARS = 90

DOC_ROOT = "docs"
DECISION_DIRS = ("docs/adr", "docs/specs")


def enabled(cfg: dict | None) -> bool:
    """缺行即开：平台级默认靠代码兜底，DB 行只用于显式下调。"""
    return bool((cfg or {}).get("clarify.enabled", DEFAULT_ENABLED))


def remind_at(cfg: dict | None) -> int:
    """止损提醒的触发题数；<=0 视为关闭提醒（与超时参数 <=0 不限制的约定一致）。"""
    try:
        return int((cfg or {}).get("clarify.ask_remind_at", DEFAULT_REMIND_AT))
    except (TypeError, ValueError):
        return DEFAULT_REMIND_AT


def note_ask(state: dict, *, declined: bool = False) -> int:
    """记一次提问并返回本轮累计题数（解读确认卡也算第 1 题）。"""
    n = int(state.get(ASKS_KEY) or 0) + 1
    state[ASKS_KEY] = n
    if declined:
        state[WAIVED_KEY] = True
    return n


def note_decline(state: dict) -> None:
    """用户点「不再提问，自行决定」→ 本轮与后续都不再强制澄清。"""
    state[WAIVED_KEY] = True


def note_done(state: dict, basis: str | None) -> str:
    """记录收口依据；非法值按「问过了」处理，不给模型一个能蒙混的第三种状态。"""
    normalized = basis if basis in (BASIS_CLARIFIED, BASIS_ALREADY_CLEAR) else BASIS_CLARIFIED
    state[DONE_KEY] = normalized
    return normalized


def satisfied(state: dict) -> bool:
    """串联判据：声明过收口，且（本轮问过 **或** 声明的是「需求本已明确」）。

    「自行决定」是用户侧闸门，优先级最高——用户已经放弃澄清流程，再拦就是跟用户作对。
    """
    if state.get(WAIVED_KEY):
        return True
    basis = state.get(DONE_KEY)
    if not basis:
        return False
    return int(state.get(ASKS_KEY) or 0) >= 1 or basis == BASIS_ALREADY_CLEAR


def gate_block(state: dict):
    """门禁判定：返回 None 表示放行，返回字符串表示挡回并把它作为工具失败结果回给模型。

    引导文本分两种说法是有必要的：模型「声明过收口但没问过」与「压根没声明」要走的路径不同，
    给同一句话会让前者以为要重新声明一次收口。
    """
    if satisfied(state):
        return None
    if state.get(DONE_KEY):
        return ("澄清门禁未通过：你已调用 clarify_done 声明需求对齐，但本轮没有向用户提过任何问题，"
                "也没有说明「需求本已明确」。请先用 ask_user 问出最关键的歧义点，或直接调用 "
                "clarify_done(basis=\"already_clear\", understanding=\"…\") 讲清你的理解。")
    return ("澄清门禁未通过：动手改文件前必须先完成需求澄清。"
            "请先输出对需求的结构化解读（你的理解、歧义词的候选含义、你推测的动机），"
            "用 ask_user 请用户确认有无偏差，再调用 clarify_done 收口。"
            "若需求本身已明确无歧义，可直接调用 clarify_done(basis=\"already_clear\", understanding=\"…\")"
            "并说明你的理解。")


def ask_notice(state: dict, cfg: dict | None):
    """第 N 题时的止损提醒；不拒绝调用，只在结果里附一句。

    硬上限的失败模式是模型改用纯文字提问绕过工具（提问卡统计失真、用户还得自己打字），
    所以这道闸只做「提醒」，方向是劝停而不是禁止（ADR-0032）。
    """
    thr = remind_at(cfg)
    n = int(state.get(ASKS_KEY) or 0)
    if thr <= 0 or n != thr:
        return None
    return (f"你已在本轮提问 {n} 次。除非仍有决定性歧义未解决，否则请停止追问，"
            "按最合理默认开工，并在 clarify_done 里列出你采用了哪些假设。")


def is_new_doc_write(root: Path, rel: str | None) -> bool:
    """`docs/` 下的**新建**文件——写入免二次确认的硬围栏（ADR-0033 故事 14）。

    三条同时成立才算：路径首段是 docs、不是 docs 目录本身、文件此前不存在。
    归一化交给工作区唯一的围栏函数 `resolve_in_workspace`，`docs/../backend/x` 会被解析成
    工作区里的真实位置、首段随之不再是 docs——这条围栏不可能被参数拼写绕过。
    覆盖已有文档仍要确认：用户点头的是「新建这份计划」，不是「以后这份文档随便改」。
    """
    from app.agent.tools import ToolError, resolve_in_workspace

    if not isinstance(rel, str) or not rel.strip():
        return False
    try:
        full = resolve_in_workspace(root, rel)
        parts = full.relative_to(root.resolve()).parts
    except (ToolError, OSError, ValueError, RuntimeError):
        return False
    return len(parts) >= 2 and parts[0] == DOC_ROOT and not full.exists()


def collect_decisions(root: Path, *, limit: int = DEFAULT_INDEX_MAX) -> list[str]:
    """扫工作区应用过程文档，产出「相对路径 ｜ 标题 ｜ 摘要」索引行。

    只读文件头部（frontmatter 与首个正文段落），不读全文：这是渐进披露的索引，
    细节由模型自己 read_file。任何单份文档读不动都只跳过它，绝不让一次 IO 抖动
    把整轮对话打成错误。
    """
    lines: list[str] = []
    for rel_dir in DECISION_DIRS:
        d = root / rel_dir
        if not d.is_dir():
            continue
        try:
            files = sorted(p for p in d.glob("*.md") if p.is_file())
        except OSError:
            continue
        for f in files:
            title, summary = _doc_head(f)
            # 空文档也要列出来：索引的价值是「这里有一份决策」，读不出标题就退化成文件名，
            # 悄悄漏掉的那一条模型永远不知道存在。
            lines.append(f"{f.relative_to(root).as_posix()} | {title or f.name} | {summary}")
            if len(lines) >= limit:
                return lines
    return lines


def render_decisions(lines: list[str]) -> str:
    """渲染注入段；空清单返回空串（宁可不注入，也不让空标签污染 system，同 <memories> 规矩）。"""
    if not lines:
        return ""
    return ("\n\n<app_decisions>\n"
            "以下是当前工作区已沉淀的需求决策与应用过程文档（只给了索引，细节用 read_file 读全文）。\n"
            "继续同方向的工作前，先按这些既有决策，不要重新发明已经定过的口径；"
            "要与某条冲突时，先向用户说明再改。\n"
            + "\n".join(lines)
            + "\n</app_decisions>")


def _doc_head(path: Path) -> tuple[str, str]:
    """取标题与摘要：frontmatter 的 title/description 优先，其次首个 `# ` 标题与首个正文行。

    整段截断到 SUMMARY_MAX_CHARS：摘要行每轮都发，不能随文档变长。
    """
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return "", ""
    title = ""
    summary = ""
    lines = text.splitlines()
    # 首行的 --- 是**起始**delimiter，必须跳过后再从剩余行找闭合；拿整份逐行判定会让首行
    # 直接被当成闭合，frontmatter 里的 title/description 一个也读不到。
    in_front = bool(lines) and lines[0].strip() == "---"
    body: list[str] = []
    for ln in (lines[1:] if in_front else lines):
        s = ln.strip()
        if in_front:
            if s == "---":
                in_front = False
                continue
            low = s.lower()
            if not title and (low.startswith("title:") or low.startswith("name:")):
                title = s.split(":", 1)[1].strip().strip("\"'")
            elif not summary and (low.startswith("description:") or low.startswith("summary:")):
                summary = s.split(":", 1)[1].strip().strip("\"'")
            continue
        if not s:
            continue
        if s.startswith("#") and not title:
            title = s.lstrip("#").strip()
            continue
        if s.startswith(("```", "|", ">")):
            continue
        body.append(s)
        if len(body) >= 2:
            break
    if not summary:
        summary = (body[0] if body else "")
    return _clip(title), _clip(summary)


def _clip(s: str) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= SUMMARY_MAX_CHARS else s[:SUMMARY_MAX_CHARS - 1] + "…"
