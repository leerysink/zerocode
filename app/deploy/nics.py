# app/deploy/nics.py
"""局域网地址（规格 05 FR5-03/§4.2）：网卡枚举双途径 + default_nic 记忆 + README 发布块。
纯 socket 探测不发包（UDP connect 只选路）；平台不改防火墙（05 §4.3 安全边界）。"""
import ipaddress
import os
import re
import socket
from pathlib import Path

MARK_START, MARK_END = "<!-- zc:publish -->", "<!-- /zc:publish -->"
# 中央网关通配域名后缀（见 gateway/）：prod 访问地址统一为 https://{slug}.{GATEWAY_DOMAIN}。
# 换域名要同时改两处 env：平台侧（本变量）与网关侧 gateway/.env 的同名变量——compose 把它透传
# 成容器内的 GATEWAY_DOMAIN，lua 用它从 Host 头里剥出 slug（gateway/lua/router.lua），404 页文案
# 也用它（gateway/404.html）。两边对不上的症状是「控制台显示的地址看着正常，点进去全 404」。
# 证书要重签成对应域名；nginx.conf 的 server_name（catch-all）与证书文件名都与域名无关，不用改。
# 默认 zerocode.com，可用 env 覆盖便于测试/多环境。
GATEWAY_DOMAIN = os.environ.get("ZEROCODE_GATEWAY_DOMAIN", "zerocode.com")
# SSL 开关（CONTEXT「SSL 开关」）：部署根 .env 的全局地址投影开关，缺省开。
# 只有显式 false/0/no/off 才关；其余取值（含拼错）一律按 true——开关只有两态，不造第三个灰区。
# 关态用于域名未备案、443 被拦期间：网关照常运行，只是控制台不再展示它的域名地址。
SSL_ENABLE_ENV = "ZEROCODE_SSL_ENABLE"
_SSL_OFF = frozenset({"false", "0", "no", "off"})
# Docker 默认 bridge 网段：候选里出现它多半不是用户要的局域网地址，只标注不剔除。
# 模块级常量：lan_candidates 每次调用都重建 /12 网络是白花的开销（T4 审查移交）。
_DOCKER_NET = ipaddress.ip_network("172.16.0.0/12")

def _via_hostname() -> list[str]:
    try:
        return sorted({ai[4][0] for ai in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)})
    except OSError:
        return []

def _via_udp() -> str:
    """UDP「连接」8.8.8.8：不发包，只让内核选出默认出口网卡地址。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return ""
    finally:
        s.close()

def _usable(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return not (a.is_loopback or a.is_link_local or a.is_unspecified)

def lan_candidates() -> list[dict]:
    primary = _via_udp()
    ips = [primary] if primary else []
    ips += [i for i in _via_hostname() if i != primary]
    seen: list[dict] = []
    for ip in ips:
        if not _usable(ip) or any(s["ip"] == ip for s in seen):
            continue
        a = ipaddress.ip_address(ip)
        seen.append({"ip": ip, "primary": ip == primary, "docker_hint": a in _DOCKER_NET})
    return seen

async def default_nic(sf) -> str:
    from app.deploy.settings import deploy_cfg
    return (await deploy_cfg(sf)).get("deploy.default_nic", "") or ""

def ssl_enabled() -> bool:
    """应用访问地址走网关 HTTPS 还是 IP 直连（判定规则见 SSL_ENABLE_ENV 注释）。"""
    return os.environ.get(SSL_ENABLE_ENV, "").strip().lower() not in _SSL_OFF


def _app_port(app: dict):
    """prod 端口：兼容 get_app 的扁平列与 engine._compose_meta 归一后的 ports 字典。"""
    ports = app.get("ports")
    if isinstance(ports, dict) and ports.get("prod") is not None:
        return ports["prod"]
    return app.get("prod_port")


def app_url(app: dict) -> str:
    """单个应用的访问地址投影（同步纯函数，地址的唯一事实来源）。

    SSL 开 → https://{slug}.{域名}：域名是 slug 的稳定投影，不绑端口；真正可访问的前提是
    prod 处于 running（进 routes.json），否则网关给 404 提示页。
    SSL 关 → http://{ZEROCODE_PREVIEW_HOST}:{prod_port}：域名未备案、443 被拦期间的活路，
    宿主与预览地址共用同一个变量，不另立第二个 host。端口缺位回空串，不谎拼 http://host:None。
    """
    if not ssl_enabled():
        from app.deploy.settings import preview_base_host
        port = _app_port(app)
        return f"http://{preview_base_host()}:{port}" if port else ""
    slug = (app.get("slug") or "").strip()
    return f"https://{slug}.{GATEWAY_DOMAIN}" if slug else ""


async def app_urls(sf, app: dict) -> dict:
    """API 形状（`GET /apps/{id}/urls` 只回 domain 一项）：内容见 app_url。"""
    return {"domain": app_url(app)}

def _dominant_eol(text_: str) -> str:
    """首行探测文件主导行尾：第一个 LF 若被 CR 前导即 CRLF 文件（空文件/裸 LF 回落 "\\n"）。
    README 在应用工作区里，AI 与用户都可能按 Windows 习惯写成 CRLF，块内行尾必须跟着文件走。"""
    i = text_.find("\n")
    return "\r\n" if i > 0 and text_[i - 1] == "\r" else "\n"

def update_readme(root: Path, url: str, version: str) -> None:
    """README 顶部发布块（05 §4.2）：标记对之间整体替换，幂等；无 README 不报错（只跳过）。

    行尾全程字节保真（T5 审查 I-1）：读端 `newline=""` 关掉通用换行归一，块内行尾按主导行尾生成，
    正则边界同时吃 \\r\\n 与 \\n —— 否则读端归一 + 写端 `newline=""` 的净效果是把整份 README 翻成 LF，
    发布一次就给应用仓库带来全文件 diff 噪音（与 app/api/files.py、app/agent/tools.py 的 newline="" 同法）。
    读端用 `f.open(...)` 而非 `Path.read_text(...)`：后者到 3.13 才有 newline 形参，本仓跑 3.12。
    """
    f = root / "README.md"
    if not f.is_file():
        return
    with f.open("r", encoding="utf-8", newline="") as fh:
        text_ = fh.read()
    eol = _dominant_eol(text_)
    block = (f"{MARK_START}{eol}> 正式发布地址：{url}（版本 {version}，平台每次发布刷新）{eol}{MARK_END}{eol}")
    pat = re.compile(re.escape(MARK_START) + r".*?" + re.escape(MARK_END) + r"(?:\r\n|\n)?", re.S)
    if pat.search(text_):
        text_ = pat.sub(block, text_, count=1)
    else:
        text_ = block + eol + text_
    f.write_text(text_, encoding="utf-8", newline="")
