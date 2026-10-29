# app/agent/preview_tools.py
"""Agent 的预览验证工具（规格 docs/specs/2026-10-29-agent-preview-tools.md）。

三个工具、零命令面：preview_status / preview_logs 只读，preview_restart 只调引擎既有的
start_dev / restart_dev。应用身份来自服务端解析（ctx.app），schema 里没有 app 参数，
模型既指不到别的应用也指不到别人的应用。

刻意不 import app.agent.tools（ToolContext / ToolError）：tools.py 在分派分支里惰性 import
本模块，反向依赖会成环。handler 只读 ctx 的字段，所有失败都作为**正常结果**返回
（{"ok": False, "error": ...}）而不抛异常——模型需要读到原因并向用户解释。
"""
import asyncio
import logging
import re
import time
from datetime import datetime

from app.deploy import engine
from app.deploy.errors import DeployError
from app.deploy.logs import collect_dev_logs
from app.services import app_service
from app.services.memory_service import redact_sensitive

logger = logging.getLogger(__name__)

STEP_PREFIX = engine.LOG_PREFIX        # "[zerocode]"：只有平台步骤行进聊天进度流
DEFAULT_WAIT_SECONDS = 240             # agent.preview_wait_seconds 的代码默认值
WAIT_SECONDS_MAX = 900
DEFAULT_LOG_LINES = 80                 # agent.preview_log_lines
LOG_LINES_MIN = 10
LOG_LINES_MAX = 300
DEFAULT_MAX_RESTARTS = 3               # agent.preview_max_restarts（<=0 不限）
TOOL_LOG_TIMEOUT = 15                  # 工具读容器日志的超时（REST 面板仍是 60）
RESTART_LOG_LINES = 20                 # 失败/超时结果里附带的最近日志行数
LOG_LINE_CHARS = 500                   # 单行上限：一条 traceback 不该吃掉整个上下文窗口
LOG_TOTAL_BYTES = 16 * 1024            # 总量上限
PROGRESS_INTERVAL = 0.25               # 进度节流：同一时刻最多一条/250ms
HEARTBEAT_INTERVAL = 30.0              # 长等待期间每 30s 补一句「仍在启动」
POLL_INTERVAL = 0.5                    # 等待循环周期，也是取消检查的粒度

_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[T ](\d{2}:\d{2}:\d{2})")


async def resolve_session_app(session_factory, workspace_id, *,
                              owner_user_id=None) -> tuple[dict | None, str]:
    """每次 run 开始时解析一次应用身份。返回 `(app, note)`。

    - app 为 dict：应用工作区，预览工具挂载；
    - app 为 None 且 note 为空：非应用工作区，静默不挂载（普通工作区不该看到一句解释）；
    - app 为 None 且 note 非空：fail closed（工作区绑定了多个应用），note 是给模型转述给用户的一句话。

    解析过程本身出错（数据库异常）按非应用工作区处理并记 warning——绝不让整轮对话失败。
    """
    if workspace_id is None:
        return None, ""
    try:
        app = await app_service.get_app_by_workspace(
            session_factory, workspace_id, owner_user_id=owner_user_id)
    except DeployError as e:
        # 409 = 工作区绑定了多个应用（fail closed，但要给模型一句能转述给用户的原因）；
        # 404 = 探测到行之后、委托回读之前应用被删（另一个标签页），那是「应用不可用」，
        # 不是「绑定了多个应用」——把前者说成后者等于对用户撒谎
        if e.status == 409:
            logger.warning("预览工具未挂载：工作区 %s 绑定了多个应用", workspace_id)
            return None, "当前工作区绑定了多个应用，无法使用预览工具"
        logger.warning("预览工具未挂载：工作区 %s 的应用解析被拒（%s）", workspace_id, e)
        return None, ""
    except Exception:
        logger.warning("预览工具未挂载：工作区 %s 的应用解析失败", workspace_id, exc_info=True)
        return None, ""
    return app, ""


async def fetch_app(ctx) -> dict | None:
    """每次工具调用都重新读一遍应用行：run 开始时那份快照里的状态永远不可信。

    应用被删（另一个标签页操作）→ None，handler 据此回「应用已被删除或不可用」而不是 500。
    """
    app = getattr(ctx, "app", None)
    if not app:
        return None
    try:
        return await app_service.get_app(ctx.session_factory, app["id"])
    except DeployError:
        return None


def pending_task(ctx, app_id: int):
    """该应用在共享任务表里的未完成任务（与 REST 同一张 `app.state.dev_tasks`）。"""
    task = (getattr(ctx, "deploy_tasks", None) or {}).get(app_id)
    return task if task is not None and not task.done() else None


def _clamp_int(raw, default: int, lo: int, hi: int) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = default
    return max(lo, min(value, hi))


def wait_seconds_of(cfg: dict | None, args: dict) -> int:
    """preview_restart 的等待上限：参数优先，缺省读 `agent.preview_wait_seconds`，一律夹在 0..900。

    `<=0` 表示等到任务结束（仍可被「停止」取消等待）。畸形值回落默认，不把异常抛给模型。
    """
    cfg_value = _clamp_int((cfg or {}).get("agent.preview_wait_seconds", DEFAULT_WAIT_SECONDS),
                           DEFAULT_WAIT_SECONDS, 0, WAIT_SECONDS_MAX)
    if args.get("wait_seconds") is None:
        return cfg_value
    return _clamp_int(args.get("wait_seconds"), cfg_value, 0, WAIT_SECONDS_MAX)


def tail_of(cfg: dict | None, args: dict) -> int:
    """preview_logs 的行数：参数优先，缺省读 `agent.preview_log_lines`，夹在 10..300。"""
    cfg_value = _clamp_int((cfg or {}).get("agent.preview_log_lines", DEFAULT_LOG_LINES),
                           DEFAULT_LOG_LINES, LOG_LINES_MIN, LOG_LINES_MAX)
    if args.get("tail") is None:
        return cfg_value
    return _clamp_int(args.get("tail"), cfg_value, LOG_LINES_MIN, LOG_LINES_MAX)


def _clock(ts) -> str:
    """归并行的时间 → 本地 `HH:MM:SS`（模型只需要时刻，日期在结果里另有出处）。"""
    raw = str(ts or "")
    try:
        return datetime.fromisoformat(raw).astimezone().strftime("%H:%M:%S")
    except ValueError:
        m = _TS_RE.match(raw)
        return m.group(1) if m else "--:--:--"


def _source_of(row: dict) -> str:
    if row.get("source") == "platform":
        return "平台"
    return row.get("service") or "容器"


def format_log_lines(rows, *, limit_lines: int = LOG_LINES_MAX, limit_chars: int = LOG_TOTAL_BYTES,
                     limit_line_chars: int = LOG_LINE_CHARS) -> tuple[list[str], bool, int]:
    """归并行 → 模型可读的短行 `时间 [来源] 正文`；返回 `(lines, truncated, redacted)`。

    出口收口（§6）：逐行剥 ANSI、过 `redact_sensitive`、单行限长；总量超限时**从最旧的行开始丢**
    （模型要的是「最近发生了什么」），丢过就标 truncated=True，让它知道还有更早的没给。
    """
    out: list[str] = []
    redacted = 0
    kept = rows[-max(int(limit_lines), 1):]
    for row in kept:
        body = engine.strip_ansi(str(row.get("line") or "")).rstrip()
        if not body:
            continue
        body, hits = redact_sensitive(body)
        redacted += hits
        if len(body) > limit_line_chars:
            body = body[:limit_line_chars] + "…（已截断）"
        out.append(f"{_clock(row.get('ts'))} [{_source_of(row)}] {body}")
    # truncated 只对「真的扣下了内容」负责：行数被 limit_lines 切掉、或总量超预算从最旧开始丢。
    # 空正文行被跳过不算——容器打空行时 compose 的行形态是 `backend-1 |`，
    # parse_compose_logs 会无条件 append，把跳过空行也算截断会让这个信号恒真、沦为噪声。
    truncated = len(rows) > len(kept)
    total = sum(len(line) for line in out)
    while out and total > limit_chars:
        total -= len(out.pop(0))
        truncated = True
    return out, truncated, redacted


def _safe_text(raw, limit: int = 200) -> str:
    """任何要送进模型上下文的自由文本都过这一道：剥 ANSI、遮凭据、限长。

    规格 §6 的红线字面只管步骤行与容器输出，但 note / 异常原文 / 探针判定串同样会进模型，
    而 loop 的 `redact_tool_result` 只净化落库与推送那一份——当轮回注模型用的是原文。
    200 字与 `engine.container_logs` 的既有口径同形。
    """
    text_, _ = redact_sensitive(engine.strip_ansi(str(raw or "")))
    return text_[:limit]


def _status_hint(status: str, deploy_task: bool) -> str:
    if deploy_task or status == "starting":
        return "预览正在启动，稍后再用 preview_status 查看，不要重复启动"
    if status == "error":
        return "上次启动失败：用 preview_logs 看错误原文，再决定是否 preview_restart"
    return "预览未运行：需要验证改动时用 preview_restart 启动"


async def _status(ctx, args: dict) -> dict:
    """只读状态：数据库状态 + 一次 2 秒探针。刻意不调 engine.dev_status（那会起 compose ps 子进程）。"""
    app = await fetch_app(ctx)
    if app is None:
        return {"ok": False, "error": "应用已被删除或不可用，预览工具无法使用"}
    status = app.get("status_dev") or "stopped"
    url = app.get("preview_url") or ""
    base = {"ok": True, "app": {"slug": app.get("slug"), "stack": app.get("stack")},
            "status": status, "url": url,
            "deploy_task": pending_task(ctx, app["id"]) is not None}
    if not url:
        return {**base, "healthy": False, "probe": "", "hint": "应用没有 dev 端口，无法给出预览地址"}
    if status != "running":
        return {**base, "healthy": False, "probe": "",
                "hint": _status_hint(status, base["deploy_task"])}
    healthy, probe = await engine.probe_dev(app, timeout=2)
    hint = "" if healthy else ("端口探测未通过：healthy 只代表 HTTP 可达，"
                               "编译错误与 traceback 要看 preview_logs")
    return {**base, "healthy": healthy, "probe": _safe_text(probe), "hint": hint}


async def _logs(ctx, args: dict) -> dict:
    """只读日志：与应用页面板同一个收集器，但限长 + 遮断后才进模型上下文。"""
    app = await fetch_app(ctx)
    if app is None:
        return {"ok": False, "error": "应用已被删除或不可用，预览工具无法使用"}
    collected = await collect_dev_logs(ctx.session_factory, app, ctx.deploy_hub,
                                       tail=tail_of(ctx.cfg, args),
                                       container_timeout=TOOL_LOG_TIMEOUT)
    lines, truncated, redacted = format_log_lines(collected["lines"])
    return {"ok": True, "status": app.get("status_dev") or "stopped",
            "url": app.get("preview_url") or "", "lines": lines, "truncated": truncated,
            "note": _safe_text(collected.get("note")), "redacted": redacted}


async def run_preview_tool(ctx, name: str, args: dict, *, confirmed: bool = False) -> dict:
    """三个预览工具的统一入口（`tools.execute_tool` 在分派分支里惰性 import 本函数）。

    任何失败都作为正常结果返回，不抛异常：loop 的兜底 except 会把它糊成「工具执行异常」，
    模型就读不到「Docker 没开」「已有部署任务」这类可行动的原因了。
    """
    args = args if isinstance(args, dict) else {}
    try:
        if name == "preview_status":
            return await _status(ctx, args)
        if name == "preview_logs":
            return await _logs(ctx, args)
        return {"ok": False, "error": f"未知预览工具: {name}"}
    except Exception as e:
        logger.exception("预览工具 %s 执行异常", name)
        return {"ok": False, "error": _safe_text(f"预览工具执行异常: {e}")}
