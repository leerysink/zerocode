# app/deploy/mpruntime.py
"""小程序前置条件与可读指引（规格 2026-11-09 US-22/US-25）。

小程序真机只接受**备案域名 + 有效 HTTPS 证书**（自签在真机不生效），且上传密钥要在微信
后台把平台**出网 IP** 加白。本模块只做「读现状 + 给原因」：没有一个跳过开关——前置条件
不满足就让预检直接失败，而不是制造能编译却打不开接口的假绿。
"""
import logging
import os
import time

import httpx

from app.deploy import nics

logger = logging.getLogger(__name__)

EGRESS_IP_ENV = "ZEROCODE_EGRESS_IP"
EGRESS_PROBE_URL = "https://api.ipify.org"
EGRESS_TTL_SECONDS = 600
EGRESS_TIMEOUT = 2.0
TLS_TIMEOUT = 3.0
CONSOLE_URL = "https://mp.weixin.qq.com/"

# 三步指引的文案键：正文由前端渲染，平台只声明「哪三步」。
STEPS = ("dev_version", "trial_version", "submit_review")

# 出站 transport：生产 None（真实网络），测试注入 httpx.MockTransport（同 skills 安装器口径）。
transport = None

_cache = {"ip": "", "at": None}

_REASONS = {
    "ssl_off": "网关未启用 HTTPS：小程序只接受备案域名 + 有效 HTTPS 证书（自签不行）",
    "no_domain": "缺少应用域名：请先配置 ZEROCODE_GATEWAY_DOMAIN 并使用已备案域名",
    "no_credentials": "尚未绑定小程序账号：请先绑定 AppID 与上传密钥",
    "tls_unreachable": "域名或证书不可用：HTTPS 握手失败——请确认域名已备案生效"
                       "且网关使用有效证书（自签证书在真机不生效）",
}


def request_domain(app: dict) -> str:
    """小程序 request 合法域名：只有 SSL 开且有 slug 才是备案 HTTPS 域名，否则空。"""
    slug = (app.get("slug") or "").strip()
    if not slug or not nics.ssl_enabled():
        return ""
    return f"https://{slug}.{nics.GATEWAY_DOMAIN}"


def api_base(app: dict) -> str:
    domain = request_domain(app)
    return f"{domain}/api" if domain else ""


async def probe_tls(url: str, timeout: float = TLS_TIMEOUT) -> bool:
    """任意 HTTP 状态都算可达（401/403 也证明 TLS 与应用在）；异常一律不可达。

    只在后台任务（预检）里调用，不进请求路径。
    """
    try:
        # trust_env=False：真机直连应用域名，平台侧环境代理既不代表真机路径，NO_PROXY 带
        # `[::1]` 时还会让 httpx 构造客户端即抛 InvalidURL（假报「域名或证书不可用」）
        async with httpx.AsyncClient(timeout=timeout, transport=transport,
                                     follow_redirects=False, trust_env=False) as c:
            await c.get(url)
        return True
    except Exception:  # noqa: BLE001
        return False


async def egress_ip(*, force: bool = False, timeout: float = EGRESS_TIMEOUT) -> str:
    """平台服务器出网 IP：env 优先，否则探测公网回显；失败返回空串（不阻断任何操作）。

    成功与失败都进 10 分钟进程内缓存——否则每 3 秒轮询的弹框都会卡在这条网络请求上。
    """
    env = os.environ.get(EGRESS_IP_ENV, "").strip()
    if env:
        return env
    now = time.monotonic()
    if not force and _cache["at"] is not None and now - _cache["at"] < EGRESS_TTL_SECONDS:
        return _cache["ip"]
    ip = ""
    try:
        # 上传由 builder 容器直连微信，白名单要的是宿主直连出口 IP，同样不取环境代理
        async with httpx.AsyncClient(timeout=timeout, transport=transport,
                                     trust_env=False) as c:
            r = await c.get(EGRESS_PROBE_URL)
        if r.status_code == 200:
            ip = r.text.strip()
    except Exception:  # noqa: BLE001
        logger.info("小程序出网 IP 探测失败（仅提示，不阻断绑定与上传）", exc_info=True)
    _cache.update(ip=ip, at=now)
    return ip


async def readiness(app: dict, binding: dict, *, probe: bool = False) -> dict:
    """汇总「现在还不能用」的原因。probe=True 时额外做 TLS 可达性检查（仅预检任务用）。"""
    reasons: list[str] = []
    domain = request_domain(app)
    if not nics.ssl_enabled():
        reasons.append("ssl_off")
    elif not domain:
        reasons.append("no_domain")
    if not (binding or {}).get("appid"):
        reasons.append("no_credentials")
    if probe and domain and not await probe_tls(domain):
        reasons.append("tls_unreachable")
    return {
        "ok": not reasons,
        "reasons": reasons,
        "request_domain": domain,
        "egress_ip": await egress_ip(),
    }


def reason_text(reasons: list[str]) -> str:
    return "；".join(_REASONS[r] for r in reasons if r in _REASONS)


def guidance(app: dict, *, egress_ip: str) -> dict:
    """弹框里的加白清单与三步指引；正文由前端按 steps 键渲染。"""
    return {
        "request_domain": request_domain(app),
        "egress_ip": egress_ip,
        "console_url": CONSOLE_URL,
        "steps": list(STEPS),
    }
