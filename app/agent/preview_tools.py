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
from app.deploy import tasks as deploy_tasks
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
DEFAULT_CONFIRM_RUNNING = True         # agent.preview_confirm_running（中断运行中的预览前要用户确认）
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


_TRUE_FORMS = ("1", "true", "yes", "y")
_FALSE_FORMS = ("0", "false", "no", "n")


def _cfg_bool(raw, default: bool) -> bool:
    """配置位取值：JSON 布尔直接用；字符串按常见真/假词解析；认不出来回落默认。

    `app_config.value` 是 JSONB，正常存的是布尔；但 `agent.write_confirm` 一类的历史读法
    把配置当字符串比过（`cfg.get(...) in ("true","1",True)`），这里收口成一个明确的判据。
    """
    if isinstance(raw, bool):
        return raw
    text_ = "" if raw is None else str(raw).strip().lower()
    if text_ in _TRUE_FORMS:
        return True
    if text_ in _FALSE_FORMS:
        return False
    return default


def confirm_required(ctx) -> bool:
    """中断运行中的预览前是否要用户确认（`agent.preview_confirm_running`，默认要）。"""
    return _cfg_bool((getattr(ctx, "cfg", None) or {}).get("agent.preview_confirm_running"),
                     DEFAULT_CONFIRM_RUNNING)


def _max_restarts(cfg: dict | None) -> int:
    """单次 run 的启动/重启上限；`<=0` 表示不限（与超时参数 `<=0` 不限制的约定一致）。"""
    try:
        return int((cfg or {}).get("agent.preview_max_restarts", DEFAULT_MAX_RESTARTS))
    except (TypeError, ValueError):
        return DEFAULT_MAX_RESTARTS


def _restarts_used(ctx) -> int:
    try:
        return int((getattr(ctx, "run_state", None) or {}).get("preview_restarts", 0))
    except (TypeError, ValueError):
        return 0


def _restarts_left(ctx) -> int:
    """本次 run 还剩几次；不限时返回 -1（模型据此知道「没有额度限制」）。"""
    limit = _max_restarts(getattr(ctx, "cfg", None))
    if limit <= 0:
        return -1
    return max(limit - _restarts_used(ctx), 0)


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


async def _emit(ctx, line: str) -> None:
    await ctx.send({"type": "tool_progress", "name": "preview_restart", "line": line})


async def _drain_progress(ctx, q, last_emit: float, seen: list[str],
                          *, flush: bool = False) -> tuple[float, list[str]]:
    """把订阅队列里的事件塑成聊天进度：只转发平台步骤行与状态变化，并再过一遍遮断。

    **不转发** pip/npm 的成百上千行容器输出（那属于 `preview_logs`），也不照单全收：
    250ms 内的多条只投递最新一条，进度是给中人看的读数，不是日志流。`seen` 始终累积全量，
    供超时/失败结果直接取用（模型不用再调一次 preview_logs 就能看到现场）。

    `flush=True` 只用在任务结束后的那一次收尾：节流的意义是「等待期间不把聊天刷成日志流」，
    而任务已经结束了，再把这一批里除最新一条之外的步骤行丢掉，用户看到的流程就断在半路
    （引擎常把「→ 迁移」「预览状态：starting」在同一瞬间连发，落在同一次排空里）。
    收尾仍限 `RESTART_LOG_LINES` 条——与结果里 `logs` 的行数同口径，几十条 `[zerocode]` 行
    攒在一次排空里时，结束瞬间不该把它们全灌进聊天。
    """
    if q is None:
        return last_emit, seen
    fresh: list[str] = []
    while not q.empty():
        ev = q.get_nowait()
        if ev.get("type") == "dev_log":
            line = str(ev.get("line") or "")
            if not line.startswith(STEP_PREFIX):
                continue
        elif ev.get("type") == "dev_status":
            line = f"{STEP_PREFIX} 预览状态：{ev.get('status')}"
        else:
            continue
        line, _ = redact_sensitive(engine.strip_ansi(line))
        if not seen or seen[-1] != line:
            seen.append(line)
            fresh.append(line)
    now = time.monotonic()
    if flush:
        for line in fresh[-RESTART_LOG_LINES:]:   # 收尾也限长：病态的一大批行不该在结束瞬间灌进聊天
            await _emit(ctx, line)
        return now, seen
    if fresh and now - last_emit >= PROGRESS_INTERVAL:
        await _emit(ctx, fresh[-1])
        return now, seen
    return last_emit, seen


async def _wait(ctx, app: dict, task, wait_seconds: int, q) -> dict:
    """有界等待一个部署任务，把它的过程与终态塑成工具结果。

    只等不催：**永不取消部署任务**——聊天取消与等待超时都只停止等待，因为栈的起停在引擎侧是
    独立事务，中途掐掉只会留下一半的容器和一半的状态。也不在这里 stop_dev：工具的动作面
    只有「观察」「启动」「重启」三种。取消检查每 0.5s 一次，保证用户点「停止」后一个轮询周期内返回。
    """
    started = time.monotonic()
    deadline = None if wait_seconds <= 0 else started + wait_seconds
    last_emit, last_beat, seen = 0.0, started, []
    while not task.done():
        last_emit, seen = await _drain_progress(ctx, q, last_emit, seen)
        now = time.monotonic()
        if now - last_beat >= HEARTBEAT_INTERVAL:
            last_beat = now
            await _emit(ctx, f"{STEP_PREFIX} 预览仍在启动，已等待 {now - started:.0f}s")
        if getattr(ctx, "cancel", None) is not None and ctx.cancel.is_set():
            fresh = await fetch_app(ctx) or app
            return {"ok": True, "cancelled": True, "status": fresh.get("status_dev") or "starting",
                    "url": fresh.get("preview_url") or "", "elapsed_s": round(now - started, 1),
                    "hint": "用户已停止本轮；预览仍在后台继续启动", "restarts_left": _restarts_left(ctx)}
        if deadline is not None and now >= deadline:
            fresh = await fetch_app(ctx) or app
            return {"ok": False, "status": fresh.get("status_dev") or "starting",
                    "url": fresh.get("preview_url") or "", "elapsed_s": round(now - started, 1),
                    "error": "", "logs": seen[-RESTART_LOG_LINES:],
                    "hint": "预览仍在启动，用 preview_status / preview_logs 继续观察；不要连续重启",
                    "restarts_left": _restarts_left(ctx)}
        await asyncio.sleep(POLL_INTERVAL)
    # 收尾这一次全量投递：等待期间被合并掉的最后几条步骤行同样是流程的一部分，
    # 任务已经结束了，再节流它们就等于把用户看到的流程截断在半路。
    _, seen = await _drain_progress(ctx, q, last_emit, seen, flush=True)
    elapsed = round(time.monotonic() - started, 1)
    fresh = await fetch_app(ctx) or app
    status = fresh.get("status_dev") or "stopped"
    url = fresh.get("preview_url") or ""
    left = _restarts_left(ctx)
    if task.cancelled():
        return {"ok": False, "status": status, "url": url, "elapsed_s": elapsed,
                "error": "预览任务已被取消", "logs": seen[-RESTART_LOG_LINES:], "restarts_left": left}
    exc = task.exception()
    if exc is not None:
        # 两支都是引擎/第三方异常的原文，和 note 同类的自由文本出口：进模型上下文前先过遮断闸
        error = _safe_text(str(exc)) if isinstance(exc, DeployError) \
            else _safe_text(f"预览任务异常: {exc}")
        return {"ok": False, "status": status, "url": url, "elapsed_s": elapsed,
                "error": error, "logs": seen[-RESTART_LOG_LINES:], "restarts_left": left,
                "hint": "先看 logs 定位失败原因，再决定是否重试"}
    healthy, probe = (False, "应用没有 dev 端口") if not url else await engine.probe_dev(fresh, timeout=2)
    return {"ok": True, "status": status, "healthy": healthy, "probe": _safe_text(probe), "url": url,
            "elapsed_s": elapsed, "app": {"slug": fresh.get("slug"), "stack": fresh.get("stack")},
            "restarts_left": left, "note": "", "logs": seen[-RESTART_LOG_LINES:]}


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


async def _restart(ctx, args: dict, *, confirmed: bool = False) -> dict:
    """启动或重启 dev 预览（§5.5 策略表）：动作面只有引擎既有的 `start_dev` / `restart_dev`。

    顺序是有意的，每一道都在打扰用户之前：并发判定 → 次数上限 → 确认兜底 → Docker 预检 → 登记任务。
    后两道只属于「没有任务」的分支：有任务时观察它，观察一次都没起栈，因而不受上限约束、也不发确认。
    `confirmed` 由 `AgentRunner._dispatch` 传入（它才是 `confirm_request` 的唯一发出方）；
    这里只做兜底：需要确认却没拿到确认时，宁可让模型重试一次，也不执行未确认的中断。
    """
    app = await fetch_app(ctx)
    if app is None:
        return {"ok": False, "error": "应用已被删除或不可用，预览工具无法使用"}
    app_id = app["id"]
    status = app.get("status_dev") or "stopped"
    url = app.get("preview_url") or ""
    left = _restarts_left(ctx)
    task = pending_task(ctx, app_id)
    if task is not None:
        # starting/stopping 期间有任务：观察它，不另起一个栈。
        # 观察**不受次数上限约束**：上限管的是「模型又起了一次栈」，而观察一次都没起。
        # 额度用尽时把观察也挡掉，模型就只剩 preview_status 一条路，容易改判成「再试一次」。
        if status not in ("starting", "stopping"):
            return {"ok": False, "status": status, "url": url, "restarts_left": left,
                    "error": "该应用有部署任务进行中（可能正在发布），请稍后再试"}
    else:
        if left == 0:
            return {"ok": False, "restarts_left": 0,
                    "error": f"本次任务已启动/重启预览 {_max_restarts(ctx.cfg)} 次，请先检查失败原因再继续"}
        if status == "running" and confirm_required(ctx) and not confirmed:
            return {"ok": False, "status": status, "url": url, "restarts_left": left,
                    "error": "预览已在运行，请重试并在确认框中选择确认"}
    hub = getattr(ctx, "deploy_hub", None)
    q = hub.subscribe(app_id) if hub is not None else None    # 先订阅再 spawn，避免漏掉最早的事件
    try:
        if task is None:
            try:
                await engine.preflight_docker()               # 失败不登记任务（与 REST 的 503 同文案）
            except DeployError as e:
                return {"ok": False, "status": status, "url": url, "restarts_left": left,
                        "error": _safe_text(str(e)), "hint": "未登记后台任务，Docker 就绪后重试"}
            action = engine.restart_dev if status == "running" else engine.start_dev
            notify = hub.publish if hub is not None else None
            try:
                task = deploy_tasks.spawn(ctx.deploy_tasks, app_id,
                                          lambda: action(ctx.session_factory, app, notify=notify))
            except DeployError as e:                          # 另一条通道刚抢先起了任务：不排队、不双跑
                return {"ok": False, "status": status, "url": url, "restarts_left": left,
                        "error": str(e)}
            ctx.run_state["preview_restarts"] = _restarts_used(ctx) + 1   # 只有真起任务才计次
        return await _wait(ctx, app, task, wait_seconds_of(ctx.cfg, args), q)
    finally:
        if hub is not None and q is not None:
            hub.unsubscribe(app_id, q)


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
        if name == "preview_restart":
            return await _restart(ctx, args, confirmed=confirmed)
        return {"ok": False, "error": f"未知预览工具: {name}"}
    except Exception as e:
        logger.exception("预览工具 %s 执行异常", name)
        return {"ok": False, "error": _safe_text(f"预览工具执行异常: {e}")}


_STACK_HINTS = {
    "vue3-fastapi": "后端 Python 改动由 uvicorn --reload 自动重载；requirements.txt 改动必须重启。",
    "vue3-springboot": "后端 Java 改动不会热更新，必须 preview_restart；前端改动仍走 Vite 热更新。",
    "vue3-node": "无后端，前端刷新即可；依赖清单改动才需要重启。",
    "static": "静态页，刷新即可；依赖清单改动才需要重启。",
}


def build_preview_prompt(stack: str) -> str:
    """`<preview>` 指引：只在应用解析成功（工具已挂载）时注入，位置在应用契约之后。

    工具描述随每次请求发送、刻意保持一句话，教程写在这里。核心是「不要每改一行就重启」——
    可热更新的场景走成重启，代价是几十秒到几分钟的等待和一次用户确认；
    以及「healthy 只代表 HTTP 可达」，否则模型会把探针 200 当成改动已编译通过。
    次数上限是配置项，提示词只说「有上限」，不写死数字（写死就会与配置漂移）。
    """
    lines = [
        "\n\n<preview>",
        f"当前工作区是可运行的应用工程（技术栈：{stack or '未知'}）。"
        "改完代码要验证，不要仅凭阅读宣称“已完成”。",
        "- 纯前端/后端代码改动通常会自动热更新：先调 preview_status，healthy=true 时让用户刷新页面。",
        "- 改了依赖清单（package.json / requirements.txt / pom.xml）、环境变量或启动配置，"
        "或预览不是 running：调 preview_restart。",
        f"- preview_restart 默认最多等 {DEFAULT_WAIT_SECONDS} 秒；返回 starting 表示仍在后台启动，"
        "用 preview_status / preview_logs 继续观察，不要连续重启。",
        "- healthy 只表示 HTTP 可达，不表示新改动编译通过；"
        "前端编译错误、后端 traceback 看 preview_logs。",
        "- 同一任务内不要反复启动/重启（平台有次数上限）；重启会中断用户正在进行的预览调试，"
        "运行中的重启需要用户确认。",
        "- 不要尝试停止预览、发布、操作数据库或执行任意命令——你没有这些工具。",
    ]
    hint = _STACK_HINTS.get(stack or "")
    if hint:
        lines.append(f"- {hint}")
    lines.append("</preview>")
    return "\n".join(lines)
