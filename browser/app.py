"""浏览器 sidecar：FastAPI + Playwright Chromium。

独立容器运行（ADR-0031）：不持有 docker.sock、不注入平台库凭据、非 root、只读根文件系统。
两条调用面：一次性检查（`/healthz` + `/inspect`，`preview_page` 用）和有状态的会话服务
（`/sessions*`，Agent 交互走查用）。目标 URL 由后端构造并校验，本服务只接受 host 命中允许
清单的 http(s) 地址；一次性检查每次新建 BrowserContext 用完即关，会话则把干净 context 留在
本进程内存里（规格 §6.1/§6.2）——两者都不复用宿主浏览器的 cookie 与登录态。
"""
import asyncio
import base64
import logging
import os
import socket
import time

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from playwright.async_api import async_playwright

from .inspect_core import (allow_request, clean_text, host_of, is_ip_literal, origin_of,
                           parse_allowed_hosts, pick_connect_ip, rewrite_url_host,
                           shape_result, validate_target)
from .session_core import (INTERACTIVE_JS, INTERACTIVE_MAX_DEFAULT, can_open, new_ref_tokens,
                           shape_interactive)
from .sessions import (ACTIONS, SESSIONS, SETTLE_MS_MAX, STEP_TIMEOUT_MS_DEFAULT,
                       STEP_TIMEOUT_MS_MAX, Session, active_summary, await_expect, charge,
                       drop, max_sessions, navigation_url, new_session_id, origin_url,
                       perform_action, ref_is_present, resolve_target, snapshot_state,
                       sweep_expired, take_dialog, take_new_items, touch)

MAX_CONCURRENCY = 2
DEFAULT_WAIT_MS = 1200
WAIT_MS_MAX = 5000
DEFAULT_TIMEOUT_MS = 45000
TIMEOUT_MS_MAX = 120000
# 视口与截图尺寸共用一个来源：`collect_page_state` 回传的宽高必须就是这次截图的宽高，
# 两处各写一遍 1280/800 的话，前端按宽高做坐标换算迟早和图对不上。
VIEWPORT_WIDTH = 1280
VIEWPORT_HEIGHT = 800
# 给模型的错误说明长度：Playwright 的原文带着整段选择器与调用栈提示，全量进上下文只是噪声。
ERROR_TEXT_MAX = 300
SESSION_GONE_MESSAGE = "浏览器会话已结束，请重新 browser_open"
SESSION_FULL_MESSAGE = "浏览器服务已满（活会话达上限），请等前一个走查结束"
# 「上一轮的句柄」与「这个元素点不动」必须是两句话（规格 §5.3 的两行）：前者要模型去新清单里
# 换一个 ref，后者要它去看遮挡。形状非法与「形状合法但页面上查不到」是同一件事的两种命中方式，
# 共用这一句——令牌每次扫描整批换发，旧的那个在这一轮 DOM 里必然不存在（规格 §6.2）。
REF_STALE_MESSAGE = "ref 无效或页面已变化，请用最新清单里的 ref"
# 会话巡检周期（规格 §6.2 第二条不变式）：10s 是「空闲 120s 回收」这条上限的分辨率，
# 再长会让超限的 context 多活几十秒，再短只是白烧 CPU。
SWEEP_INTERVAL_SECONDS = 10

ALLOWED_HOSTS = parse_allowed_hosts(os.environ.get("BROWSER_ALLOWED_HOSTS"))
logger = logging.getLogger(__name__)
app = FastAPI(title="zerocode-browser", docs_url=None, redoc_url=None)
_playwright = None
_browser = None
_sem = asyncio.Semaphore(MAX_CONCURRENCY)
# 巡检任务句柄只活在运行中的那个事件循环里，所以建在 _startup、关在 _shutdown：
# 模块期建锁/任务会在 TestClient 换循环时炸出「bound to a different event loop」，
# 而忘了 cancel 则每次进程收口都留一个 pending 任务在日志里刷屏。
_sweeper_task = None


class InspectRequest(BaseModel):
    url: str
    wait_ms: int = DEFAULT_WAIT_MS
    timeout_ms: int = DEFAULT_TIMEOUT_MS


class OpenRequest(BaseModel):
    """`POST /sessions` 的请求体（规格 §6.1）。`expect` 是「首屏等到这句话出现再回」。"""

    url: str
    timeout_ms: int = DEFAULT_TIMEOUT_MS
    expect: dict | None = None
    settle_ms: int = 0
    interactive_max: int = INTERACTIVE_MAX_DEFAULT


class StepRequest(BaseModel):
    """`POST /sessions/{sid}/step` 的请求体。

    `timeout_ms` 一次管两件事（动作可点等待与断言轮询预算），因为规格里它们本就是同一个
    `agent.browser_expect_timeout_ms`；`ref` 与 `path` 按 action 二选一，非法形状在端点里各自
    拒绝，不在模型层抛异常。
    """

    action: str
    ref: str = ""
    value: str = ""
    path: str = ""
    expect: dict | None = None
    settle_ms: int = 0
    timeout_ms: int = STEP_TIMEOUT_MS_DEFAULT
    interactive_max: int = INTERACTIVE_MAX_DEFAULT


def _clamp(value, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(int(value), hi))
    except (TypeError, ValueError):
        return default


def _interactive_limit(raw) -> int:
    """交互清单条数：只允许往下调。

    40 这个上限是规格钉死的（清单进模型上下文，条数就是 token），把它做成可由请求放大的
    旋钮等于让调用方自己决定泄漏多少。
    """
    return _clamp(raw, INTERACTIVE_MAX_DEFAULT, 1, INTERACTIVE_MAX_DEFAULT)


def _short(err: Exception) -> str:
    """异常原文裁到 300 字并洗掉控制字符：够看清哪里坏了，不至于把堆栈整段喂给模型。"""
    return clean_text(str(err))[:ERROR_TEXT_MAX]


# 「这个会话物理上已经不存在」的形状：Playwright 在 page/context/browser 关掉后统一抛
# 「Target page, context or browser has been closed」，Chromium 自己走的是 crashed 那句。
# 必须和「这一步做砸了」分开——后者是常态结果（点不动、断言不过），前者要让后端按 §5.3
# 回 session_gone 并释放额度，否则一个死会话会带着注册表里那行直到 TTL 才消失。
_GONE_MARKERS = ("has been closed", "target page, context or browser", "crashed")


def _page_is_gone(err: Exception) -> bool:
    text = str(err).lower()
    return any(marker in text for marker in _GONE_MARKERS)


@app.on_event("startup")
async def _startup():
    global _playwright, _browser, _sweeper_task
    _playwright = await async_playwright().start()
    # 容器本身就是隔离边界（非 root + cap_drop + read_only）；Chromium 自带 sandbox 在这种
    # 文件系统与 seccomp 组合下起不来，关掉的是「容器内再套一层」，不是容器隔离。
    _browser = await _playwright.chromium.launch(
        headless=True,
        args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
    )
    _sweeper_task = asyncio.create_task(_sweeper())


@app.on_event("shutdown")
async def _shutdown():
    global _playwright, _browser, _sweeper_task
    if _sweeper_task is not None:
        _sweeper_task.cancel()
        _sweeper_task = None
    try:
        if _browser is not None:
            await _browser.close()
        if _playwright is not None:
            await _playwright.stop()
    finally:
        _browser = None
        _playwright = None


async def _sweeper():
    """空闲会话巡检（规格 §6.2 第二条不变式）。

    死了不如容器死了：一次异常把任务摘掉之后，注册表里那些 context 就只能等进程退出才回收，
    mem_limit 1g 会先一步到——所以循环不退出，只记账式地打日志。
    """
    while True:
        try:
            await asyncio.sleep(SWEEP_INTERVAL_SECONDS)
            await sweep_expired()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("session 巡检异常")


@app.get("/healthz")
async def healthz():
    return {"ok": True}


@app.post("/inspect")
async def inspect(req: InspectRequest):
    url, err = validate_target(req.url, ALLOWED_HOSTS)
    if err:
        raise HTTPException(400, err)
    if _browser is None:
        raise HTTPException(503, "浏览器尚未就绪")
    wait_ms = _clamp(req.wait_ms, DEFAULT_WAIT_MS, 0, WAIT_MS_MAX)
    timeout_ms = _clamp(req.timeout_ms, DEFAULT_TIMEOUT_MS, 10000, TIMEOUT_MS_MAX)
    async with _sem:
        try:
            return await asyncio.wait_for(_inspect(url, wait_ms, timeout_ms),
                                          timeout=timeout_ms / 1000 + 5)
        except asyncio.TimeoutError:
            return {"ok": False, "timed_out": True, "error": "页面检查超时"}
        except Exception as e:  # noqa: BLE001 - 任何浏览器异常都必须是可读结果
            return {"ok": False, "error": f"页面检查失败: {e}"}


async def _connect_url(url: str) -> str:
    """把 host 别名换成直连 IP 后再交给浏览器。

    Vite 5+ 的 `server.allowedHosts` 只放行 localhost 与 IP 字面量：以
    `Host: host.docker.internal` 访问应用 dev 端口会拿到 403 拦截页，浏览器渲染的是
    拦截文案而不是用户的页面——工具会「成功」返回一页无关内容。IP 侧两者都放行，
    且容器里 host.docker.internal 本来就是靠 DNS 指到宿主网关，换过去不改变目标。
    解析失败按原 URL 走（尽力而为，不把可用链路换成硬失败）。
    """
    host = host_of(url)
    origin = origin_of(url)
    if not host or origin is None or is_ip_literal(host):
        return url
    try:
        info = await asyncio.get_running_loop().getaddrinfo(
            host, origin[2], type=socket.SOCK_STREAM)
    except (socket.gaierror, OSError, UnicodeError):
        return url
    ip = pick_connect_ip(info)
    return rewrite_url_host(url, ip) if ip else url


async def _inspect(url: str, wait_ms: int, timeout_ms: int) -> dict:
    connect_url = await _connect_url(url)
    started = time.monotonic()
    context = await _browser.new_context(
        viewport={"width": VIEWPORT_WIDTH, "height": VIEWPORT_HEIGHT},
        device_scale_factor=1,
        locale="zh-CN",
        ignore_https_errors=False,
    )
    try:
        return await _inspect_in_context(context, connect_url, wait_ms, timeout_ms, started)
    finally:
        await context.close()


class Collectors:
    """一次页面访问期间的 console / pageerror / 失败请求 / 原生弹窗收集。

    增量账（`new_only` 用的 seen 集合）由 session 自己持有：同一个监听器被多个动作复用，
    账要跟着会话走，不能跟着一次采集走。`/inspect` 一次性检查不读它，全量即增量。
    `dialog` 是唯一「读一次即清」的字段（弹窗属于哪一步由 sessions.take_dialog 判定）。
    """

    def __init__(self):
        self.console: list[dict] = []
        self.page_errors: list[str] = []
        self.failed: list[dict] = []
        self.blocked_urls: set[str] = set()
        self.dialog: dict | None = None

    def snapshot(self) -> dict:
        """session 路径每步取一次的全量视图；`/inspect` 一次性检查直接读字段，不走这里。"""
        return {"console": list(self.console), "page_errors": list(self.page_errors),
                "failed": list(self.failed), "dialog": self.dialog}


async def attach_listeners(page, connect_url: str) -> Collectors:
    """挂上放行与收集监听。

    console 条目刻意只有 `level` / `text`（不带 `url`）：`session_core.new_only` 用
    「有没有 url」区分 console 条目与失败请求条目，补一个 url 字段会让 console 静默按
    请求指纹记账，会话路径每步都回同一批旧错误。
    """
    c = Collectors()

    async def on_route(route):
        request = route.request
        if request.url.startswith(("data:", "blob:", "about:")):
            await route.continue_()
            return
        is_main = request.is_navigation_request() and request.frame == page.main_frame
        if allow_request(connect_url, request.url, is_main_navigation=is_main):
            await route.continue_()
            return
        c.blocked_urls.add(request.url)
        c.failed.append({"method": request.method, "url": request.url, "status": 0,
                         "type": request.resource_type, "blocked": True})
        await route.abort()

    def on_console(msg):
        if msg.type in ("error", "warning"):
            c.console.append({"level": msg.type, "text": msg.text})

    def on_page_error(err):
        c.page_errors.append(str(err))

    def on_response(resp):
        if not resp.url.startswith(("http://", "https://")):
            return
        if resp.status >= 400:
            c.failed.append({"method": resp.request.method, "url": resp.url,
                             "status": resp.status, "type": resp.request.resource_type})

    def on_request_failed(request):
        if request.url in c.blocked_urls or request.url.startswith(("data:", "blob:", "about:")):
            return
        c.failed.append({"method": request.method, "url": request.url, "status": 0,
                         "type": request.resource_type})

    async def on_dialog(dialog):
        # 一律按取消处理并在结果里显式告知。吞掉弹窗不回报的后果是：Agent 点了删除、
        # 弹窗被取消、它以为删除成功了。取 dismiss 而不是 accept，是因为 accept 会替
        # Agent 按下「确认删除」这类不可撤销的操作——宁可这一步看起来没生效。
        c.dialog = {"type": dialog.type, "message": (dialog.message or "")[:200],
                    "handled": "dismissed"}
        await dialog.dismiss()

    await page.route("**/*", on_route)
    page.on("console", on_console)
    page.on("pageerror", on_page_error)
    page.on("response", on_response)
    page.on("requestfailed", on_request_failed)
    page.on("dialog", on_dialog)
    return c


async def collect_page_state(page, collectors: Collectors, *, started: float,
                             timed_out: bool, interactive_limit: int = INTERACTIVE_MAX_DEFAULT,
                             include_screenshot: bool = False) -> dict:
    """采集一次页面状态：标题、可见文本、交互元素（顺带打标）、console、失败请求。

    快检与 session 走查共用这一个采集器：各写一遍「title + innerText + INTERACTIVE_JS +
    shape_interactive」的组合，同一页的同一眼状态就会在两条路径里给出两种裁剪口径。
    截图策略的分歧（`/inspect` 必给、step 默认不给）用参数表达，不靠复制函数。

    返回的 `url` 是重写后的 connect_url（`host.docker.internal` 已被换成容器可达 IP，
    见 `_connect_url`），不是给用户看的地址：要展示得另转 display_url。把它原样进模型
    上下文就是内部 IP 泄漏。
    """
    title = await page.title()
    text = await page.evaluate("document.body ? document.body.innerText : ''")
    # 令牌在 Python 侧生成后整份传给脚本（`session_core.new_ref_tokens`）：唯一性与形状策略
    # 只在纯函数层定义一处，JS 不摇随机数。每次采集都是全新的一批，所以上一轮发出去的旧句柄
    # 在这一页上必然查不到——「旧 ref 作废」由此变成机制能检测到的事，而不只是一句约定。
    nodes = await page.evaluate(INTERACTIVE_JS, new_ref_tokens())
    interactive, inter_truncated = shape_interactive(nodes, interactive_limit)
    # 截图取在裁剪之前：elapsed_ms 原本就含截图耗时，挪到之后会让这个数凭空变小。
    shot = await page.screenshot(type="png", full_page=False) if include_screenshot else None
    result = shape_result(title=title, text=text, console=collectors.console,
                          page_errors=collectors.page_errors, failed_requests=collectors.failed,
                          elapsed_ms=_ms(started), timed_out=timed_out)
    result["interactive"] = interactive
    # 交互清单的截断另开一个字段：`truncated` 是 preview_page 的既有语义（文本/控制台/请求
    # 触顶），把元素触顶并进去会让快检在元素多、其余没超限时凭空报「结果已按预算截断」。
    result["interactive_truncated"] = inter_truncated
    # connect_url（重写后的地址，含容器可达 IP），非 display_url：对外展示前必须再转一次。
    result["url"] = page.url
    if shot is not None:
        result["screenshot_b64"] = base64.b64encode(shot).decode("ascii")
        result["screenshot_width"] = VIEWPORT_WIDTH
        result["screenshot_height"] = VIEWPORT_HEIGHT
    return result


async def _inspect_in_context(context, connect_url: str, wait_ms: int, timeout_ms: int,
                              started: float) -> dict:
    page = await context.new_page()
    collectors = await attach_listeners(page, connect_url)
    timed_out = False
    try:
        await page.goto(connect_url, wait_until="domcontentloaded", timeout=timeout_ms)
    except Exception as e:  # noqa: BLE001 - 超时后仍尽量取回已渲染内容
        if "timeout" not in str(e).lower():
            return {"ok": False, "error": f"页面打开失败: {e}", "elapsed_ms": _ms(started)}
        timed_out = True
    try:
        if wait_ms:
            await page.wait_for_timeout(wait_ms)
        if not timed_out:
            try:
                await page.wait_for_load_state("networkidle", timeout=min(3000, timeout_ms))
            except Exception:  # noqa: BLE001 - networkidle 只是尽力而为
                pass
        # 截图失败要落进同一个「页面读取失败」分支——重构前的语义是读不出页面就整体失败。
        return await collect_page_state(page, collectors, started=started, timed_out=timed_out,
                                        include_screenshot=True)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"页面读取失败: {e}", "timed_out": timed_out,
                "elapsed_ms": _ms(started)}


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


# ---------------------------------------------------------------------------
# session 端点（规格 §6.1/§6.2）：把注册表与采集串起来，本身不留任何状态。
#
# 刻意**不**过 `_sem`：那个信号量是给 `/inspect` 这种「几十秒内结束」的一次性检查用的，
# 一个走查会话能活几分钟，占住全局槽位会把 preview_page 整条路径饿死。会话的并发上界由
# `max_sessions()`（活会话数）来管——超限是响亮拒绝，不是排队（§6.2 第一条不变式）。
# ---------------------------------------------------------------------------


# 三种响应形状，Task 4 的后端要按 status_code 分支读（读串了会拿到空错误串）：
#   400 / 409 / 503 → {"detail": str}     —— 拒绝，走 HTTPException 的固定外壳，没有 ok/error
#   422             → {"detail": [dict]}  —— 请求体形状不合法（pydantic 校验失败），
#                                          detail 是**列表**不是字符串，和上面两种别当同一种解析
#   200             → {"ok": bool, ...}   —— 常态结果：失败也留在 200 里带 ok:false 和原因
@app.post("/sessions")
async def open_session(req: OpenRequest):
    """开一个有状态会话：干净 context → 打开目标页 → 回首轮完整状态。

    不复用宿主浏览器的任何登录态（US-18）：context 每次新建，cookie 跟着它一起生灭。
    """
    url, err = validate_target(req.url, ALLOWED_HOSTS)
    if err:
        raise HTTPException(400, err)
    if _browser is None:
        raise HTTPException(503, "浏览器尚未就绪")
    if not can_open(len(SESSIONS), max_sessions()):
        raise HTTPException(409, SESSION_FULL_MESSAGE)
    timeout_ms = _clamp(req.timeout_ms, DEFAULT_TIMEOUT_MS, 10000, TIMEOUT_MS_MAX)
    connect_url = await _connect_url(url)
    context = await _browser.new_context(
        viewport={"width": VIEWPORT_WIDTH, "height": VIEWPORT_HEIGHT},
        device_scale_factor=1,
        locale="zh-CN",
    )
    page = await context.new_page()
    collectors = await attach_listeners(page, connect_url)
    s = Session(session_id=new_session_id(), context=context, page=page,
                origin=origin_url(connect_url), collectors=collectors)
    # 上限的二次判定与登记之间没有任何 await：单线程事件循环里这一对是原子的。缺了它，
    # 两个并发 open 会在同一刻都读到「还差一个」，然后一起往 mem_limit 1g 里塞第三个 context。
    if not can_open(len(SESSIONS), max_sessions()):
        await _abandon(context)
        raise HTTPException(409, SESSION_FULL_MESSAGE)
    SESSIONS[s.session_id] = s
    # 打开中不算空闲（见 `Session.opening` 的字段注释）。只在各 await 边界后 touch 是不够的：
    # 最长的 await 就是 goto 本身（timeout_ms 上限 120000），边界补不到中间那一百多秒。
    s.opening = True
    try:
        return await asyncio.wait_for(_open_step(s, connect_url, req, timeout_ms),
                                      timeout=timeout_ms / 1000 + 5)
    except asyncio.TimeoutError:
        await drop(s.session_id)
        return {"ok": False, "error": "页面打开超时", "timed_out": True, "session_gone": True}
    except Exception as e:  # noqa: BLE001 - 打不开必须是可读结果，不是给后端的 500
        await drop(s.session_id)
        return {"ok": False, "error": f"页面打开失败: {_short(e)}", "session_gone": True}
    finally:
        # 清标记要走 finally 覆盖每一条出口（正常返回 / 超时 / 异常 / 被 wait_for cancel）：
        # 漏一次这个会话就永远不进巡检，注册表里的 context 能一直占到进程退出——比「早杀一步」糟。
        s.opening = False
        # 打开本身就能耗掉一整个 TTL，所以收尾时把空闲时钟拨回现在：刚打开完的会话不该在
        # 下一步动作时就撞上「空闲 120s」，那才是这条不变式要保护的场景。
        touch(s)


async def _abandon(context) -> None:
    """关掉一个没登记成功的 context：关不掉也不能把「服务已满」变成 500。"""
    try:
        await context.close()
    except Exception:  # noqa: BLE001
        pass


async def _open_step(s: Session, connect_url: str, req: OpenRequest, timeout_ms: int) -> dict:
    """首轮：goto → 可选等待 → 采集 → 报状态。打开失败由 `open_session` 收口成 ok:false。"""
    started = time.monotonic()
    timed_out = False
    try:
        await s.page.goto(connect_url, wait_until="domcontentloaded", timeout=timeout_ms)
    except Exception as e:  # noqa: BLE001 - 超时后仍把已渲染的内容读回去，比一句错误有用
        if "timeout" not in str(e).lower():
            raise
        timed_out = True
    limit = _interactive_limit(req.interactive_max)
    if req.settle_ms:
        await s.page.wait_for_timeout(_clamp(req.settle_ms, 0, 0, SETTLE_MS_MAX))
    if req.expect:
        # 打开时的 expect 只给 step 那一档轮询预算（规格 §6.3 默认 3000ms），不跟着导航超时走：
        # timeout_ms 默认 45000，拿它轮询等于把「首屏没等到这句文案」拖成一次 45s 的工具调用。
        passed, note, snap = await await_expect(s, req.expect, timeout_ms=STEP_TIMEOUT_MS_DEFAULT,
                                                interactive_limit=limit, started=started)
    else:
        await _idle_wait(s, timeout_ms)
        passed, note, snap = None, None, await snapshot_state(s, limit, started=started)
    state = dict(snap)
    # timed_out 归「这次打开」所有，快照那边恒 False，所以在这里覆盖而不是往采集器里传参
    state["timed_out"] = bool(timed_out) or bool(state.get("timed_out"))
    state["session_id"] = s.session_id
    state["dialog"] = take_dialog(s)
    # 首轮已经把全量报给模型了，账要从这里开始记：不记的话第二步会把首屏那批旧错误当成
    # 「本步新增」重播一遍，US-04 那个「这个错是不是我这一步搞出来的」的信号就没了。
    take_new_items(s)
    if passed is not None:
        state["passed"] = passed
        state["note"] = "" if passed else note
    return state


async def _idle_wait(s: Session, timeout_ms: int) -> None:
    """没给 expect 时的默认等待：一次短 networkidle（规格 §6.3），拿当下状态而不是硬等。"""
    try:
        await s.page.wait_for_load_state("networkidle", timeout=min(3000, timeout_ms))
    except Exception:  # noqa: BLE001 - dev 页面常挂着 HMR 长连接，networkidle 只是尽力而为
        pass


def _step_state(s: Session, snap: dict) -> dict:
    """step 响应的状态部分：与 open 同形，但 console / 失败请求 / 未捕获异常换成「本步新增」（US-04）。

    增量条目也过 `shape_result` 那把尺：预算（40 条 / 单条 1000 字 / 失败请求 30 条 /
    page_errors 10 条）只在那里定义一处，绕开它自己裁就等于允许增量把整页 console 灌进模型上下文。

    字段名的不对称是有意的，别「统一」掉它（裁定 R-27）：`/inspect`（`preview_page`）是
    一次性全量检查，它的既有契约叫 `page_errors`（Task 2 起前端与后端都在读这个名字）；
    session 路径每步只报新增，所以叫 `page_errors_new`——与同一条响应里的 `console_new` /
    `failed_requests_new` 成族。把这里改回 `page_errors` 会让后端的存在性判定退回全量，
    每一步都重播首屏那次崩溃。
    """
    console_new, failed_new, page_errors_new = take_new_items(s)
    shaped = shape_result(title="", text="", console=console_new, page_errors=page_errors_new,
                          failed_requests=failed_new, elapsed_ms=0)
    state = {k: v for k, v in snap.items()
             if k not in ("console", "failed_requests", "page_errors")}
    state["console_new"] = shaped["console"]
    state["failed_requests_new"] = shaped["failed_requests"]
    state["page_errors_new"] = shaped["page_errors"]
    # 截断有两个来源：整份采集触顶（快照带来的 truncated）与本步增量触顶。前者不覆盖后者
    # 的话，「本步 900 条错误只报了 40 条」会被读成一份完整清单。
    state["truncated"] = bool(state.get("truncated")) or bool(shaped["truncated"])
    state["dialog"] = take_dialog(s)
    return state


async def _rescan(s: Session, limit: int, started: float) -> dict:
    """只重采清单、不执行动作——ref 失效时当场把最新清单附在错误里（US-09，省一次往返）。"""
    return await snapshot_state(s, limit, started=started)


async def _after_action(s: Session, req: StepRequest, limit: int, timeout_ms: int,
                        started: float):
    """动作成功之后的等待与采集，返回 `(passed, note, snapshot)`；没给 expect 时 passed 为 None。"""
    if req.settle_ms:
        await s.page.wait_for_timeout(_clamp(req.settle_ms, 0, 0, SETTLE_MS_MAX))
    if req.expect:
        return await await_expect(s, req.expect, timeout_ms=timeout_ms,
                                  interactive_limit=limit, started=started)
    if not req.settle_ms:
        await _idle_wait(s, timeout_ms)
    return None, None, await snapshot_state(s, limit, started=started)


def _step_result(passed, note, s: Session, snap: dict) -> dict:
    """成功路径的响应：`passed` 只在给了 expect 时出现，且通过时不带上那句失败措辞。"""
    result = {**_step_state(s, snap), "steps_charged": True}
    if passed is not None:
        result["passed"] = passed
        result["note"] = "" if passed else note
    return result


async def _action_failure(s: Session, limit: int, started: float, error: str,
                          exc: Exception | None = None) -> dict:
    """动作层面的失败（ref 失效 / 点不动 / 导航被阻断）：扣步数，并附最新交互清单与本步增量。

    进来先分一次诊：异常原文说明「page/context/browser 已经没了」时原样往上抛，由端点收口成
    `session_gone`。把这当成「点不动」的后果是 Agent 被告知换个 ref 再试，而那个会话早就死了。

    裁定 R-29：失败响应复用 `_step_state`，所以也带 `console_new` / `page_errors_new` /
    `failed_requests_new`。「点下去把页面点崩了、但动作本身报失败」是真机里最常见的一格
    （点击触发的脚本抛异常，Playwright 等不到那一步的稳定态）——只给一张图和一句原因，
    模型只能靠图猜；文字信号缺席的话它还要多走一步才浮出，而那一步会把这次的崩溃算成
    「本步新增」，归因就错位了。
    形状仍是 `ok:false` 且**没有 `passed` 键**：`_step_state` 里不产生 passed/note（那是
    `_step_result` 的事），所以这里不会把「点不动」写成「断言未通过」，两本账天然分家。
    增量在这里就记账（`take_new_items`），下一步不重播。
    """
    if exc is not None and _page_is_gone(exc):
        raise exc
    fresh = await _rescan(s, limit, started)
    charge(s)
    return {**_step_state(s, fresh), "ok": False, "error": error, "steps_charged": True}


async def _do_navigate(s: Session, req: StepRequest, limit: int, timeout_ms: int,
                       started: float) -> dict:
    """同源导航：path 必须是单个 `/` 开头的相对路径，拼出来必然落在会话 origin 上（US-17）。"""
    url = navigation_url(s.origin, req.path)
    if url is None:
        charge(s)
        return {"ok": False, "error": "path 必须是同源相对路径（以 / 开头）",
                "dialog": take_dialog(s), "steps_charged": True}
    timed_out = False
    try:
        await s.page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
    except Exception as e:  # noqa: BLE001
        if "timeout" not in str(e).lower():
            # 页面里那段脚本把主文档跳到外站时，route 会 abort 这次导航、错误从这里冒出来；
            # 被阻断的地址已经由 attach_listeners 记进 failed，下一步能看到它。
            return await _action_failure(s, limit, started, f"导航失败: {_short(e)}", e)
        timed_out = True
    charge(s)
    passed, note, snap = await _after_action(s, req, limit, timeout_ms, started)
    snap["timed_out"] = bool(timed_out) or bool(snap.get("timed_out"))
    return _step_result(passed, note, s, snap)


@app.post("/sessions/{sid}/step")
async def step(sid: str, req: StepRequest):
    """在已有会话里执行一个动作并回新状态（规格 §5.2）。"""
    s = SESSIONS.get(sid)
    if s is None:
        # 会话没了不扣步数（US-12）：Agent 什么也没做成，扣账只会让它更早撞上限。
        return {"ok": False, "error": SESSION_GONE_MESSAGE, "session_gone": True,
                "steps_charged": False}
    touch(s)
    if req.action not in ACTIONS:
        return {"ok": False, "error": f"未知动作: {req.action or '（空）'}", "steps_charged": False}
    limit = _interactive_limit(req.interactive_max)
    timeout_ms = _clamp(req.timeout_ms, STEP_TIMEOUT_MS_DEFAULT, 500, STEP_TIMEOUT_MS_MAX)
    started = time.monotonic()
    steps_before = s.steps
    try:
        if req.action == "navigate":
            return await _do_navigate(s, req, limit, timeout_ms, started)
        selector = resolve_target(req.ref)
        if selector is None:
            return await _action_failure(s, limit, started, REF_STALE_MESSAGE)
        if not await ref_is_present(s, selector):
            # 形状合法、页面上查不到 = 拿的是上一轮的句柄。在这里当场判出来，不拿一次
            # 3s 的可点超时去换同一个结论（`ref_is_present` 只数不等待）。
            return await _action_failure(s, limit, started, REF_STALE_MESSAGE)
        try:
            await perform_action(s, req.action, selector, req.value, timeout_ms)
        except Exception as e:  # noqa: BLE001 - 点不动是常态结果，不是工具异常
            return await _action_failure(s, limit, started,
                                         f"元素不可点击或动作失败: {_short(e)}", e)
        charge(s)
        passed, note, snap = await _after_action(s, req, limit, timeout_ms, started)
        return _step_result(passed, note, s, snap)
    except Exception as e:  # noqa: BLE001
        if _page_is_gone(e):
            await drop(sid)
            return {"ok": False, "error": "浏览器会话异常终止，请重新 browser_open",
                    "session_gone": True, "steps_charged": False}
        # 「动作成了、随后读不回」是常态而不是意外：点一下触发 SPA 重新渲染/主文档导航时，
        # evaluate 抛的是「Execution context was destroyed」——既不是 gone 也不是 timeout。
        # 这里裸 raise 的后果是 FastAPI 回一个空壳 500，而 `charge` 已经落过账：sidecar 记了
        # 一步、后端只拿到一个没有原因的错误，重试还是不重试全凭猜（全局约束「工具失败一律是
        # 带原因的正常结果」）。
        # 也可能是还没记账的那条出口（`_action_failure` 里重采就炸了），这里补上，让
        # 「回 steps_charged:true」与注册表里多出的那一步始终一一对应。
        if s.steps == steps_before:
            charge(s)
        result = {"ok": False, "error": f"步骤执行后页面读取失败: {_short(e)}",
                  "steps_charged": True}
        try:
            # 尽力再采一次：这类页面多半只是正在导航，这一帧过去清单就又读得出来了（US-09）。
            fresh = await _rescan(s, limit, started)
        except Exception:  # noqa: BLE001 - 重采再败就只回原因，别再把它变成 500
            return result
        result["interactive"] = fresh["interactive"]
        result["interactive_truncated"] = fresh["interactive_truncated"]
        return result


@app.post("/sessions/{sid}/screenshot")
async def session_screenshot(sid: str):
    """断言失败时后端替模型补一张现场图（裁定 R-4）。

    不进 `ACTIONS`：它不是模型能挑的动作，而是后端在 `passed:false` 之后自己追的一次调用，
    做成 action 会让它出现在给模型的动作枚举里。这里既不 `touch` 也不计步——截图既不该给
    空闲会话续命，也不该花掉模型的步数额度。
    """
    s = SESSIONS.get(sid)
    if s is None:
        return {"ok": False, "error": SESSION_GONE_MESSAGE, "session_gone": True}
    try:
        shot = await s.page.screenshot(type="png", full_page=False)
    except Exception as e:  # noqa: BLE001 - 补图失败不该把已经拿到手的断言结论一起带走
        if _page_is_gone(e):
            await drop(sid)
            return {"ok": False, "error": "浏览器会话异常终止，请重新 browser_open",
                    "session_gone": True}
        return {"ok": False, "error": f"截图失败: {_short(e)}"}
    return {"ok": True, "screenshot_b64": base64.b64encode(shot).decode("ascii"),
            "screenshot_width": VIEWPORT_WIDTH, "screenshot_height": VIEWPORT_HEIGHT}


@app.delete("/sessions/{sid}")
async def close_session(sid: str):
    """关闭会话。未知 id 也回 ok（幂等）：run 收口时很可能已经被 TTL 回收过了。"""
    await drop(sid)
    return {"ok": True}


@app.get("/sessions")
async def list_sessions():
    """诊断视图（不进后端调用面）：内存里现在攒着哪些会话、各自闲了多久。"""
    return {"active": active_summary()}
