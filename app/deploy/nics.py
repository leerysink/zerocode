# app/deploy/nics.py
"""局域网地址（规格 05 FR5-03/§4.2）：网卡枚举双途径 + default_nic 记忆 + README 发布块。
纯 socket 探测不发包（UDP connect 只选路）；平台不改防火墙（05 §4.3 安全边界）。"""
import ipaddress
import re
import socket
from pathlib import Path

MARK_START, MARK_END = "<!-- zc:publish -->", "<!-- /zc:publish -->"
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

async def pick_lan_ip(sf) -> str:
    dflt = await default_nic(sf)
    cands = lan_candidates()
    if dflt and any(c["ip"] == dflt for c in cands):
        return dflt
    return cands[0]["ip"] if cands else ""

async def app_urls(sf, app: dict) -> dict:
    ports = app.get("ports") if isinstance(app.get("ports"), dict) else {}
    prod = ports.get("prod", app.get("prod_port"))
    lan_ip = await pick_lan_ip(sf)
    return {"localhost": f"http://localhost:{prod}",
            "lan": (f"http://{lan_ip}:{prod}" if lan_ip else ""),
            "lanCandidates": lan_candidates(),
            "defaultNic": await default_nic(sf)}

def update_readme(root: Path, url: str, version: str) -> None:
    """README 顶部发布块（05 §4.2）：标记对之间整体替换，幂等；无 README 不报错（只跳过）。"""
    f = root / "README.md"
    block = (f"{MARK_START}\n> 正式发布地址：{url}（版本 {version}，平台每次发布刷新）\n{MARK_END}\n")
    if not f.is_file():
        return
    text_ = f.read_text(encoding="utf-8")
    pat = re.compile(re.escape(MARK_START) + r".*?" + re.escape(MARK_END) + r"\n?", re.S)
    if pat.search(text_):
        text_ = pat.sub(block, text_, count=1)
    else:
        text_ = block + "\n" + text_
    f.write_text(text_, encoding="utf-8", newline="")
