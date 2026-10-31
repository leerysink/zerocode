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


async def _inspect_in_context(context, connect_url: str, wait_ms: int, timeout_ms: int,
                              started: float) -> dict:
    page = await context.new_page()
    console, page_errors, failed = [], [], []
    blocked_urls = set()

    async def on_route(route):
        request = route.request
        if request.url.startswith(("data:", "blob:", "about:")):
            await route.continue_()
            return
        is_main = request.is_navigation_request() and request.frame == page.main_frame
        if allow_request(connect_url, request.url, is_main_navigation=is_main):
            await route.continue_()
            return
        blocked_urls.add(request.url)
        failed.append({"method": request.method, "url": request.url, "status": 0,
                       "type": request.resource_type, "blocked": True})
        await route.abort()

    def on_console(msg):
        if msg.type in ("error", "warning"):
            console.append({"level": msg.type, "text": msg.text})

    def on_page_error(err):
        page_errors.append(str(err))

    def on_response(resp):
        if not resp.url.startswith(("http://", "https://")):
            return
        if resp.status >= 400:
            failed.append({"method": resp.request.method, "url": resp.url,
                           "status": resp.status, "type": resp.request.resource_type})

    def on_request_failed(request):
        if request.url in blocked_urls or request.url.startswith(("data:", "blob:", "about:")):
            return
        failed.append({"method": request.method, "url": request.url, "status": 0,
                       "type": request.resource_type})

    await page.route("**/*", on_route)
    page.on("console", on_console)
    page.on("pageerror", on_page_error)
    page.on("response", on_response)
    page.on("requestfailed", on_request_failed)

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
        title = await page.title()
        text = await page.evaluate("document.body ? document.body.innerText : ''")
        shot = await page.screenshot(type="png", full_page=False)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"页面读取失败: {e}", "timed_out": timed_out,
                "elapsed_ms": _ms(started)}

    result = shape_result(title=title, text=text, console=console, page_errors=page_errors,
                          failed_requests=failed, elapsed_ms=_ms(started),
                          timed_out=timed_out)
    result["screenshot_b64"] = base64.b64encode(shot).decode("ascii")
    result["screenshot_width"] = 1280
    result["screenshot_height"] = 800
    return result


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)
