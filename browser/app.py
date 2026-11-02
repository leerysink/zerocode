"""浏览器 sidecar：FastAPI + Playwright Chromium。

独立容器运行（ADR-0031）：不持有 docker.sock、不注入平台库凭据、非 root、只读根文件系统。
外部只暴露两个接口：/healthz 与 /inspect。目标 URL 由后端构造并校验，本服务只接受
host 命中允许清单的 http(s) 地址；每次请求新建 BrowserContext，不复用任何用户状态。
"""
import asyncio
import base64
import os
import socket
import time

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from playwright.async_api import async_playwright

from .inspect_core import (allow_request, host_of, is_ip_literal, origin_of,
                           parse_allowed_hosts, pick_connect_ip, rewrite_url_host,
                           shape_result, validate_target)
from .session_core import INTERACTIVE_JS, INTERACTIVE_MAX_DEFAULT, shape_interactive

MAX_CONCURRENCY = 2
DEFAULT_WAIT_MS = 1200
WAIT_MS_MAX = 5000
DEFAULT_TIMEOUT_MS = 45000
TIMEOUT_MS_MAX = 120000

ALLOWED_HOSTS = parse_allowed_hosts(os.environ.get("BROWSER_ALLOWED_HOSTS"))
app = FastAPI(title="zerocode-browser", docs_url=None, redoc_url=None)
_playwright = None
_browser = None
_sem = asyncio.Semaphore(MAX_CONCURRENCY)


class InspectRequest(BaseModel):
    url: str
    wait_ms: int = DEFAULT_WAIT_MS
    timeout_ms: int = DEFAULT_TIMEOUT_MS


def _clamp(value, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(int(value), hi))
    except (TypeError, ValueError):
        return default


@app.on_event("startup")
async def _startup():
    global _playwright, _browser
    _playwright = await async_playwright().start()
    # 容器本身就是隔离边界（非 root + cap_drop + read_only）；Chromium 自带 sandbox 在这种
    # 文件系统与 seccomp 组合下起不来，关掉的是「容器内再套一层」，不是容器隔离。
    _browser = await _playwright.chromium.launch(
        headless=True,
        args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
    )


@app.on_event("shutdown")
async def _shutdown():
    global _playwright, _browser
    try:
        if _browser is not None:
            await _browser.close()
        if _playwright is not None:
            await _playwright.stop()
    finally:
        _browser = None
        _playwright = None


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
        viewport={"width": 1280, "height": 800},
        device_scale_factor=1,
        locale="zh-CN",
        ignore_https_errors=False,
    )
    try:
        return await _inspect_in_context(context, connect_url, wait_ms, timeout_ms, started)
    finally:
        await context.close()


class Collectors:
    """一次页面访问期间的 console / pageerror / 失败请求收集。

    增量账（`new_only` 用的 seen 集合）由 session 自己持有：同一个监听器被多个动作复用，
    账要跟着会话走，不能跟着一次采集走。`/inspect` 一次性检查不读它，全量即增量。
    """

    def __init__(self):
        self.console: list[dict] = []
        self.page_errors: list[str] = []
        self.failed: list[dict] = []
        self.blocked_urls: set[str] = set()

    def snapshot(self) -> dict:
        """session 路径每步取一次的全量视图；`/inspect` 一次性检查直接读字段，不走这里。"""
        return {"console": list(self.console), "page_errors": list(self.page_errors),
                "failed": list(self.failed)}


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

    await page.route("**/*", on_route)
    page.on("console", on_console)
    page.on("pageerror", on_page_error)
    page.on("response", on_response)
    page.on("requestfailed", on_request_failed)
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
    nodes = await page.evaluate(INTERACTIVE_JS)
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
        result["screenshot_width"] = 1280
        result["screenshot_height"] = 800
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
