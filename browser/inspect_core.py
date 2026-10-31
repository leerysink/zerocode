"""浏览器 sidecar 的纯函数层：URL 校验、请求放行判定、结果裁剪。

刻意不 import playwright——单测可以在没有浏览器、没有 sidecar 依赖的机器上直接跑。
"""
import ipaddress
import re
from urllib.parse import urlsplit

DEFAULT_ALLOWED_HOSTS = ("host.docker.internal", "127.0.0.1", "localhost")
TEXT_MAX_CHARS = 8000
CONSOLE_MAX = 40
CONSOLE_TEXT_MAX = 1000
PAGE_ERRORS_MAX = 10
FAILED_REQUESTS_MAX = 30
URL_MAX = 500

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_PRIVATE_SUFFIXES = (".internal", ".local", ".localhost")
_CONTAINER_PREFIXES = ("zerocode-",)


def parse_allowed_hosts(raw: str | None) -> tuple[str, ...]:
    hosts = tuple(h.strip().lower().rstrip(".") for h in (raw or "").split(",") if h.strip())
    return hosts or DEFAULT_ALLOWED_HOSTS


def validate_target(url: str, allowed_hosts: tuple[str, ...] = DEFAULT_ALLOWED_HOSTS) -> tuple[str | None, str]:
    """校验后端传来的绝对 URL；返回 `(规整后的 url, 错误文案)`，错误时 url 为 None。"""
    if not isinstance(url, str) or not url.strip():
        return None, "url 不能为空"
    raw = url.strip()
    try:
        parts = urlsplit(raw)
    except ValueError:
        return None, "url 无法解析"
    if parts.scheme not in ("http", "https"):
        return None, "url 只支持 http/https"
    if not parts.hostname:
        return None, "url 缺少 host"
    if parts.username or parts.password:
        return None, "url 不能包含用户名或密码"
    try:
        parts.port  # 畸形端口会在这里抛 ValueError
    except ValueError:
        return None, "url 端口非法"
    host = parts.hostname.lower().rstrip(".")
    if host not in allowed_hosts:
        return None, f"host 不在允许清单: {host}"
    return raw, ""


def host_of(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""


def origin_of(url: str) -> tuple[str, str, int | None] | None:
    try:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            return None
        port = parts.port
        if port is None:
            port = 80 if parts.scheme == "http" else 443
        return parts.scheme, parts.hostname.lower().rstrip("."), port
    except ValueError:
        return None


def is_private_host(host: str) -> bool:
    """私网 / 本机 / 容器别名判定；用于阻断页面子资源对内网和平台的探测。"""
    name = (host or "").lower().strip("[]").rstrip(".")
    if not name:
        return True
    if name in ("localhost",) or name.endswith(_PRIVATE_SUFFIXES):
        return True
    if name.startswith(_CONTAINER_PREFIXES):
        return True
    try:
        ip = ipaddress.ip_address(name)
    except ValueError:
        return False
    return (ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_reserved or ip.is_multicast or ip.is_unspecified)


def allow_request(target_url: str, request_url: str, *, is_main_navigation: bool) -> bool:
    """一次网络请求是否放行。

    - 主文档：只允许目标 origin（页面重定向到别的 host 一律阻断）；
    - 子资源：同源放行；公网 http(s) 放行；私网/本机/容器地址阻断；
    - data/blob/about 等非网络协议不经过这里（由调用方直接 continue）。
    """
    target = origin_of(target_url)
    req = origin_of(request_url)
    if req is None:
        return True
    if is_main_navigation:
        return target is not None and req == target
    if target is not None and req == target:
        return True
    return not is_private_host(req[1])


def clean_text(raw) -> str:
    text = _ANSI_RE.sub("", str(raw or ""))
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return _CTRL_RE.sub("", text)


def _clip(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[:limit] + "…（已截断）", True


def trim_text(raw, limit: int = TEXT_MAX_CHARS) -> tuple[str, bool]:
    return _clip(clean_text(raw), limit)


def shape_result(*, title: str, text: str, console, page_errors, failed_requests,
                 elapsed_ms: int, timed_out: bool = False) -> dict:
    """把原始采集结果裁到固定预算。所有上限只在这里定义一处。"""
    text_out, text_truncated = trim_text(text)
    console_out = []
    for item in list(console or []):
        level = str(item.get("level") or "").lower()
        if level not in ("error", "warning"):
            continue
        body, clipped = _clip(clean_text(item.get("text")), CONSOLE_TEXT_MAX)
        console_out.append({"level": level, "text": body, "truncated": clipped})
        if len(console_out) >= CONSOLE_MAX:
            break
    errors_out = [_clip(clean_text(e), CONSOLE_TEXT_MAX)[0] for e in list(page_errors or [])[:PAGE_ERRORS_MAX]]
    failed_out, seen = [], set()
    for item in list(failed_requests or []):
        url = _clip(clean_text(item.get("url")), URL_MAX)[0]
        key = (str(item.get("method") or "").upper(), url, item.get("status") or 0,
               bool(item.get("blocked")))
        if key in seen:
            continue
        seen.add(key)
        failed_out.append({
            "method": str(item.get("method") or "GET").upper(),
            "url": url,
            "status": int(item.get("status") or 0),
            "type": clean_text(item.get("type"))[:40] or "other",
            "blocked": bool(item.get("blocked")),
        })
        if len(failed_out) >= FAILED_REQUESTS_MAX:
            break
    truncated = (text_truncated or len(console_out) >= CONSOLE_MAX
                 or len(errors_out) >= PAGE_ERRORS_MAX or len(failed_out) >= FAILED_REQUESTS_MAX)
    return {
        "ok": True,
        "title": clean_text(title)[:300],
        "text": text_out,
        "text_truncated": text_truncated,
        "console": console_out,
        "page_errors": errors_out,
        "failed_requests": failed_out,
        "truncated": truncated,
        "timed_out": bool(timed_out),
        "elapsed_ms": int(elapsed_ms),
    }
