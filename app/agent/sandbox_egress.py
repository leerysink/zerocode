"""沙箱出口过滤（ADR-0066 决策 3 / 规格 US-12..16）。

机制：一个常驻 egress router 容器持 NET_ADMIN，把 nft 规则装在**自己的 netns** 里；
沙箱以 `--network container:<router>` 共享该 netns，规则因此对沙箱生效，而沙箱自身没有
NET_ADMIN、改不了规则。宿主防火墙一行不动——爆炸半径收在这两个容器内。

网段表**不另抄一份判定**：候选网段先过 `guard.is_internal_ip` 筛，口径只有 guard 一处。
`tests/test_sandbox_egress.py` 用跨地址空间取样钉住两个方向的等价（表覆盖 == guard 拒），
只钉一个方向会漏掉「guard 拒了但表里没有」——那正是 raw socket 能钻过去的假绿。
"""
from __future__ import annotations

import ipaddress
import os
from pathlib import Path

from app.source.guard import is_internal_ip

ROUTER_CONTAINER = "zerocode-sandbox-egress"
EGRESS_IMAGE_ENV = "ZEROCODE_SANDBOX_EGRESS_IMAGE"
DEFAULT_EGRESS_IMAGE = "zerocode-sandbox-egress:dev"

# 候选私网/保留网段。**不是**判定来源——下面按 guard 筛过才算数。
_CANDIDATES = (
    "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8",
    "169.254.0.0/16", "172.16.0.0/12", "192.0.0.0/24", "192.0.2.0/24",
    "192.88.99.0/24", "192.168.0.0/16", "198.18.0.0/15", "198.51.100.0/24",
    "203.0.113.0/24", "224.0.0.0/4", "240.0.0.0/4", "255.255.255.255/32",
    "::/128", "::1/128", "::ffff:0:0/96", "64:ff9b::/96", "100::/64",
    "fc00::/7", "fe80::/10", "ff00::/8", "2001::/32", "2001:db8::/32", "2002::/16",
)

# DNS 例外**不能写死**：传统 Linux 上容器解析器是 127.0.0.11，而 Docker Desktop 用的是
# 宿主网关上的代理（实测 192.168.65.7，落在 192.168/16 里）。所以平台只留占位，
# 由 router 在应用规则时按容器实际 /etc/resolv.conf 放行——见 sandbox/egress/entrypoint.sh。
DNS_ACCEPT_MARKER = "__DNS_ACCEPT__"


def internal_networks() -> list:
    """guard 认定内网/保留的网段（候选表按 guard 筛，不另立判定）。"""
    return [n for n in (ipaddress.ip_network(c) for c in _CANDIDATES)
            if is_internal_ip(n.network_address)]


def build_nft_ruleset() -> str:
    """router 在自己 netns 里应用的规则文本。DNS 占位必须排在所有 drop 之前。"""
    nets = internal_networks()
    v4 = ", ".join(str(n) for n in nets if n.version == 4)
    v6 = ", ".join(str(n) for n in nets if n.version == 6)
    lines = [
        "table inet zc_sandbox_egress {",
        "  chain out {",
        "    type filter hook output priority 0; policy accept;",
        f"    {DNS_ACCEPT_MARKER}",
        f"    ip daddr {{ {v4} }} drop",
    ]
    if v6:
        lines.append(f"    ip6 daddr {{ {v6} }} drop")
    lines += ["  }", "}", ""]
    return "\n".join(lines)


def ruleset_path() -> Path:
    """挂给 router 的是**目录**，规则文件住在这个子目录里（单文件 bind mount 会钉旧 inode）。"""
    from app.config import data_dir
    return data_dir() / "sandbox_egress" / "ruleset.nft"


def _egress_image() -> str:
    return os.environ.get(EGRESS_IMAGE_ENV) or DEFAULT_EGRESS_IMAGE


async def ensure_egress_router() -> None:
    """启动时把 router 起好并应用规则（沙箱运行时只引用容器名，不再多花 docker 调用）。

    规则文件用 write_text 原地截断写，**不做原子替换**：单文件 bind mount 钉在 inode 上，
    换 inode 会让容器里看到的还是旧规则。
    """
    from app.deploy import engine

    p = ruleset_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(build_nft_ruleset(), encoding="utf-8", newline="\n")
    code, log = await engine.docker("inspect", "-f", "{{.State.Running}}", ROUTER_CONTAINER)
    if code == 0 and log.strip().endswith("true"):
        await engine.docker("exec", ROUTER_CONTAINER, "nft", "-f", "/ruleset/ruleset.nft")
        return
    await engine.docker("rm", "-f", ROUTER_CONTAINER)
    await engine.docker("run", "-d", "--name", ROUTER_CONTAINER,
                        "--cap-add", "NET_ADMIN",
                        "--restart", "unless-stopped",
                        "-v", f"{p.parent}:/ruleset:ro",
                        _egress_image())