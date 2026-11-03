"""sidecar 的 session 注册表（ADR-0037）。

活页面只能留在本进程内存里——Playwright 的 Page 对象不能跨进程。三条不变式：
上限即拒绝（不排队）、空闲必回收、close 幂等。缺一条就会在 mem_limit 1g 的容器里
攒出僵尸 context，把整个 sidecar 拖进 OOM。

刻意把注册表语义（上限/TTL/幂等）与 Playwright 调用分开：这一层的测试不需要浏览器，
端点只负责把它们串起来。
"""
import asyncio
import time
import uuid
from dataclasses import dataclass, field

from .inspect_core import origin_of
from .session_core import (INTERACTIVE_MAX_DEFAULT, expired, judge_expect, new_only,
                           selector_for)

MAX_SESSIONS_DEFAULT = 2
TTL_SECONDS_DEFAULT = 120
STEP_TIMEOUT_MS_DEFAULT = 3000
STEP_TIMEOUT_MS_MAX = 8000
SETTLE_MS_MAX = 5000
ACTIONS = ("click", "fill", "press", "select", "scroll_into_view", "navigate")
# 一次 open 的最坏预算（秒）：导航超时上限（`app.TIMEOUT_MS_MAX` 120000ms 换算成 120s）再套上
# `asyncio.wait_for` 的宽限（`app.SIDECAR_WAIT_GRACE_SECONDS` 5s）。`sweep_expired` 给 `opening`
# 会话的容忍就按这个数放大。两个常量各自的定义处在 app.py，这里按字面量写死是因为本模块不能反向
# import app（app 在模块期就 import 本文件，反向 import 会成环）；有一条测试把这三个数钉在一起，
# 改任何一处都会红，不会再出现「app 那边把宽限调到 30s、这里还在按 125s 判」的静默漂移。
OPEN_BUDGET_SECONDS = 125


@dataclass
class Session:
    """一个 run 一个活页面。

    `origin` 在创建时固化（规格 §6.2 第三条不变式）：`navigate` 只能回到这个 origin，
    而 `console_seen` / `failed_seen` / `page_errors_seen` 是**增量账**——监听器收集的是全量，
    每步要报的是「自上次以来新增」，账必须跟着会话走，不能跟着一次采集走。
    """

    session_id: str
    context: object
    page: object
    origin: str
    collectors: object = None
    console_seen: set = field(default_factory=set)
    failed_seen: set = field(default_factory=set)
    # 未捕获异常（pageerror）与 console 同款记账（裁定 R-27）：它是「这一步把页面点崩了」
    # 最强的信号，Vue 运行时抛的异常多半只进这里、不进 console，不记账就每步重播首屏崩溃。
    page_errors_seen: set = field(default_factory=set)
    last_used: float = field(default_factory=time.monotonic)
    steps: int = 0
    dialog: dict | None = None
    # 「正在打开」标记：打开期间 `last_used` 停在登记那一刻，而一次 open 的预算比 TTL 还长
    # （timeout_ms 上限 120000，外面还套了 wait_for 的 +5s 宽限 = 125s > TTL 120s，巡检每 10s
    # 一跳必然落在中间）。让它算「空闲」的后果是在 goto 还没返回时把 context 关掉，最终报出去
    # 的「页面打开失败」就成了失真文案——真相是被自家巡检杀的。
    # 放宽不是豁免：超出 `OPEN_BUDGET_SECONDS` 的 `opening` 会话照旧回收（裁定 R-43）。
    opening: bool = False


SESSIONS: dict[str, Session] = {}


def new_session_id() -> str:
    return uuid.uuid4().hex


def env_int(key: str, default: int) -> int:
    import os
    try:
        return int((os.environ.get(key) or "").strip() or default)
    except ValueError:
        return default


def max_sessions() -> int:
    return env_int("ZEROCODE_BROWSER_MAX_SESSIONS", MAX_SESSIONS_DEFAULT)


def session_ttl() -> int:
    return env_int("ZEROCODE_BROWSER_SESSION_TTL_SECONDS", TTL_SECONDS_DEFAULT)


def touch(s: Session) -> None:
    s.last_used = time.monotonic()


async def drop(session_id: str) -> bool:
    """关闭并移除。未知 id 也回 True——幂等是后端的依赖：run 收口时可能已经过期回收了。"""
    s = SESSIONS.pop(str(session_id or ""), None)
    if s is None:
        return True
    try:
        await s.context.close()
    except Exception:  # noqa: BLE001 - 关不掉也不能让收口卡住
        pass
    return True


async def sweep_expired(now: float | None = None, ttl_seconds: int | None = None) -> list[str]:
    """回收空闲超阈值的 session，返回被回收的 id。由 app 的后台巡检每 10s 调一次。

    `opening` 的会话**阈值放大**而不是整条跳过巡检（裁定 R-43）。原先这里写的是「打开路径自己带
    超时与 `drop`，不会靠巡检兜底」——那句只在正常协程里成立：
    `app.open_session` 的 `finally` 负责清 `opening`，而一个真卡死的 Playwright 调用对
    `CancelledError` 不敏感时 `finally` 永不执行，那个标记就永远留在那儿，这条出口于是再没有
    任何回收路径。后果是整功能级 outage：`max_sessions` 默认 2，两次 wedge 之后 sidecar 永久
    回「浏览器服务已满」，直到人工重启容器；后端 `close_browser_session` 只对**已绑定**的 sid 发
    DELETE，而 wedge 发生在绑定之前，所以连自愈的入口都没有。
    放宽的方向也要保住：一次 open 的最坏预算（`OPEN_BUDGET_SECONDS`）本来就比 TTL 长，
    在它之内仍然算「在打开、不算空闲」——否则就是拿巡检去杀一个正在等响应的页面，
    报出去的原因还会失真成「页面打开失败」（见 `Session.opening` 的字段注释）。
    """
    moment = time.monotonic() if now is None else now
    ttl = session_ttl() if ttl_seconds is None else ttl_seconds
    gone = [sid for sid, s in SESSIONS.items()
            if expired(s.last_used, moment,
                       ttl + OPEN_BUDGET_SECONDS if s.opening else ttl)]
    for sid in gone:
        await drop(sid)
    return gone


def active_summary(now: float | None = None) -> list[dict]:
    """`GET /sessions` 的诊断视图：不进后端调用面，只给「有会话卡在内存里」这类排障看。"""
    moment = time.monotonic() if now is None else now
    return [{"session_id": s.session_id, "steps": s.steps,
             "idle_seconds": round(max(0.0, moment - s.last_used), 3)}
            for s in SESSIONS.values()]


def charge(s: Session) -> int:
    """扣一步，返回累计步数。

    凡回 `steps_charged: true` 的路径都要经过这里——包括 ref 失效和点不动的那两次。
    规格 §5.2 的口径是「本步是否扣了额度」，Agent 确实发起了一次动作就该记账；反过来
    （session 已失效）不扣，因为它什么也没做成。`steps` 与 `steps_charged` 不同步的话，
    `GET /sessions` 的排障读数会和后端扣掉的额度对不上。
    """
    s.steps += 1
    return s.steps


def resolve_target(ref) -> str | None:
    """模型给的 ref → 属性选择器。非法形状返回 None，绝不拼进选择器。"""
    return selector_for(ref)


async def ref_is_present(s: Session, selector: str) -> bool:
    """这个句柄在当前页面上还找得到吗——**只数一次，不等待**。

    它是「旧 ref 全部作废」这句话的机制（规格 §6.2）：令牌每次扫描整批换发，上一轮发出去的那一个
    在这一轮的 DOM 里必然查不到，而 `selector_for` 只看形状、看不出「查不到」。缺了这一层，
    拿旧令牌去点会花满可点等待、落进「元素不可点击或动作失败」——同一件事被报成两种原因，
    而模型对两者的下一步不一样（该换句柄 vs 该去看是不是被遮挡）。

    数本身出错（页面正在导航、执行上下文被换掉）时**放行**，交给动作去撞它自己的超时：
    这条只是把一句准话提前，不该顺手新增一种失败模式。
    """
    try:
        return (await s.page.locator(selector).count()) > 0
    except Exception:  # noqa: BLE001 - 判据读不出来就当在场，语义退回改动前
        return True


def origin_url(url: str) -> str:
    """把任意 URL 折成 `scheme://host[:port]` 形式的 origin 字符串。

    会话固化的是 origin（规格 §6.2 第三条不变式）而不是打开时那条带 path 的完整地址：
    `navigate` 要用它拼新路径，比对也要用它。存完整 URL 会让「同源」判定跟着首轮 path 走。
    """
    parts = origin_of(url)
    if parts is None:
        return ""
    scheme, host, port = parts
    netloc = f"[{host}]" if ":" in host else host
    if port != (80 if scheme == "http" else 443):
        netloc = f"{netloc}:{port}"
    return f"{scheme}://{netloc}"


def navigation_url(origin: str, path) -> str | None:
    """把会话 origin 与模型给的相对 path 拼成绝对 URL；非法形状返回 None。

    只接受以单个 `/` 开头的相对路径：`//host/` 是 protocol-relative（等价于换 host），
    `http:` 开头是绝对地址——两者都能把主文档导航带出 origin。后端还会用
    `normalize_path` 再校一遍（规格 §5.3），但 sidecar 不能假定调用方一定守规矩。
    控制字符单独拒：`\r\n` 混进 URL 是经典的换行注入形状（`javascript:` 之类会借它绕过
    前缀检查），而 Playwright 的 goto 只会把它当成一个奇怪的地址发出去。
    """
    raw = (path or "").strip()
    if not raw.startswith("/") or raw.startswith("//") or raw.startswith("\\"):
        return None
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in raw):
        return None
    base = origin_url(origin)
    return f"{base}{raw}" if base else None


async def perform_action(s: Session, action: str, selector: str | None, value, timeout_ms: int):
    """执行一个动作。Playwright 的 actionability 等待是内建的：元素被遮挡会等到超时，
    而不是点空——所以「超时」在这里是可点性失败，不是网络慢。"""
    if action == "click":
        await s.page.click(selector, timeout=timeout_ms)
    elif action == "fill":
        await s.page.fill(selector, str(value or ""), timeout=timeout_ms)
    elif action == "press":
        await s.page.press(selector, str(value or "Enter"), timeout=timeout_ms)
    elif action == "select":
        await s.page.select_option(selector, str(value or ""))
    elif action == "scroll_into_view":
        await s.page.scroll_into_view_if_needed(selector)
    elif action == "navigate":
        # 导航改的是文档而不是元素，走 app 的 _do_navigate（要重挂 origin 校验与超时）。
        # 到这里说明分派漏了一层，说「未知动作」会把排查带歪。
        raise ValueError("navigate 不经动作分派，请走导航分支")
    else:
        raise ValueError(f"未知动作: {action or '（空）'}")


def pending_console(s: Session) -> list[dict]:
    """本步新增、尚未报账的 console 条目——**只看不记账**。

    `new_only` 会把指纹写进传入的集合，所以给它一份副本：轮询会反复调用这里，看一次记一次
    的话，同一批错误会在第一轮被当成新增、之后每轮都算旧账，最后 `app._step_state`
    报本步增量的时候反而什么都报不出来。
    """
    c = s.collectors
    if c is None:
        return []
    fresh, _ = new_only(set(s.console_seen), list(c.console))
    return fresh


def take_new_items(s: Session) -> tuple[list[dict], list[dict], list[str]]:
    """算出本步新增的 console / 失败请求 / 未捕获异常**并记账**，返回三者。

    只有 step 收尾（以及 open 首轮报完全量之后）走这里：账记的是收集器里的**原始条目**
    指纹，不是 `shape_result` 裁剪后的形状——两边同一份口径才能对上（裁定 R-11）。
    open 调它但丢掉返回值，作用是「首轮已经整份报给模型了，别再在第二步重播一遍」。

    `page_errors` 收集器里是裸字符串，而 `new_only` 按 dict 取字段做指纹，所以套一层
    `{"text": e}` 的薄壳记账、取完摊平回字符串——返回形状与 `/inspect` 那份 `page_errors`
    一致，后端与前端两条路径看到的都是 list[str]。
    """
    c = s.collectors
    if c is None:
        return [], [], []
    console, s.console_seen = new_only(s.console_seen, list(c.console))
    failed, s.failed_seen = new_only(s.failed_seen, list(c.failed))
    wrapped, s.page_errors_seen = new_only(s.page_errors_seen, [{"text": e} for e in c.page_errors])
    return console, failed, [str(i["text"]) for i in wrapped]


def take_dialog(s: Session) -> dict | None:
    """读一次弹窗并**读一次即清**，同时挂到 `s.dialog`（最近一步的现场）。

    为什么在读取处清、而不是动作执行前统一清零：弹窗是异步的，`setTimeout` 里弹出的框
    可能落在两步之间。动作前清零会把这一份**吞掉**——而 US-11 要防的恰恰是「Agent 不知道
    有框被取消了」；宁可让它出现在下一步的账上（偏晚但看得见），不要静默消失。
    不清的话第一步的 confirm 会在之后每一步重播，模型读成「每一步都弹了一次框」。
    """
    c = s.collectors
    s.dialog = getattr(c, "dialog", None) if c is not None else None
    if c is not None:
        c.dialog = None
    return s.dialog


async def snapshot_state(s: Session, interactive_limit: int = INTERACTIVE_MAX_DEFAULT,
                         started: float | None = None) -> dict:
    """采一次快照（不截图）。await_expect 轮询时反复调它，所以刻意便宜。

    它是 `app.collect_page_state` 的薄封装（裁定 R-1）：快检与走查共用同一个采集与裁剪
    口径，这里不再自己拼一遍 title/innerText/INTERACTIVE_JS——两份并排必然漂移。
    延迟导入是因为 `app.py` 在模块期就 import 本文件，顶层互导会成环。
    """
    from .app import collect_page_state
    return await collect_page_state(s.page, s.collectors,
                                    started=time.monotonic() if started is None else started,
                                    timed_out=False, interactive_limit=interactive_limit,
                                    include_screenshot=False)


async def await_expect(s: Session, expect: dict, *, timeout_ms: int,
                       interactive_limit: int = INTERACTIVE_MAX_DEFAULT,
                       started: float | None = None,
                       interval_ms: int = 200, clock=None, sleeper=None):
    """轮询到断言满足或超时。这是本功能真正修掉的东西：固定 sleep 与任意应用的
    启动时序没有因果关系，按条件等有。

    返回 `(passed, note, snapshot)`。最后一轮无论判定如何都用它的快照——
    失败时模型要看的是「现在到底长啥样」，不是第一次采到的中间态。
    """
    loop = asyncio.get_running_loop() if clock is None else clock
    nap = asyncio.sleep if sleeper is None else sleeper
    moment = time.monotonic() if started is None else started
    deadline = loop.time() + max(timeout_ms, 0) / 1000
    snap = await snapshot_state(s, interactive_limit, started=moment)
    passed, note = judge_expect(expect, snap, pending_console(s))
    while not passed and loop.time() < deadline:
        await nap(interval_ms / 1000)
        snap = await snapshot_state(s, interactive_limit, started=moment)
        passed, note = judge_expect(expect, snap, pending_console(s))
    return passed, note, snap
