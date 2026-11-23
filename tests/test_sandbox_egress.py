"""沙箱出口过滤网段表（ADR-0066 / 规格 US-14）：与 guard.is_internal_ip 同源。

判定域等价是这里的唯一要点：只测「表里的都被 guard 拒」会漏掉「guard 拒了但表里没有」，
那种漏网就是规格 §5 点名的假绿——脚本 raw socket 直连一个没被覆盖的私网段。
"""
import ipaddress

from app.agent import sandbox_egress
from app.source.guard import is_internal_ip


def _covered(ip, nets):
    return any(ip in n for n in nets)


def test_ruleset_covers_exactly_what_guard_rejects():
    """跨地址空间取样：网段表覆盖 == guard 认定内网/保留，两个方向都要成立。"""
    nets = sandbox_egress.internal_networks()
    samples = []
    for n in nets:
        samples += [n.network_address, n[-1]]
    # 均匀扫 IPv4 空间（1024 点）+ 偏移一格，避开只测网络号的盲区
    for i in range(0, 2**32, 2**22):
        samples.append(ipaddress.ip_address(i))
        samples.append(ipaddress.ip_address(i + 1))
    samples += [ipaddress.ip_address(s) for s in (
        "8.8.8.8", "1.1.1.1", "93.184.216.34", "104.16.132.229",
        "2606:4700::1111", "2001:4860:4860::8888")]
    for ip in samples:
        assert _covered(ip, nets) == is_internal_ip(ip), str(ip)


def test_ruleset_text_drops_each_network_and_defers_dns_to_runtime():
    """生成的 nft 文本：每个网段一条 drop；DNS 例外留占位给 router 按实际 resolv.conf 填。"""
    text = sandbox_egress.build_nft_ruleset()
    for n in sandbox_egress.internal_networks():
        assert str(n) in text, str(n)
    assert "drop" in text
    # 解析器地址随环境变（Docker Desktop 在宿主网关上，不是 127.0.0.11），写死会掐死 DNS。
    # 占位必须排在所有 drop 之前，否则先被 loopback/私网段 drop 掉。
    assert sandbox_egress.DNS_ACCEPT_MARKER in text
    assert text.index(sandbox_egress.DNS_ACCEPT_MARKER) < text.index("drop")


def test_public_destinations_are_not_dropped():
    """反向断言：公网地址不落在任何 drop 网段里（否则 pip/curl 全废）。"""
    nets = sandbox_egress.internal_networks()
    for s in ("8.8.8.8", "1.1.1.1", "151.101.0.223", "2606:4700::1111"):
        assert not _covered(ipaddress.ip_address(s), nets), s


async def test_ensure_router_writes_ruleset_and_starts_container(monkeypatch, tmp_path):
    """启动接线：写规则文件 → 没在跑就起容器（持 NET_ADMIN、规则只读挂进去）。"""
    from app.deploy import engine

    calls = []

    async def spy(*a, **k):
        calls.append(a)
        if a[0] == "inspect":
            return 1, "Error: No such object"
        return 0, ""

    monkeypatch.setattr(engine, "docker", spy)
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path))
    await sandbox_egress.ensure_egress_router()
    p = tmp_path / "sandbox_egress" / "ruleset.nft"
    assert p.is_file() and "drop" in p.read_text(encoding="utf-8")
    run = next(c for c in calls if c[0] == "run")
    assert "--cap-add" in run and "NET_ADMIN" in run
    assert f"{p.parent}:/ruleset:ro" in run
    assert sandbox_egress.ROUTER_CONTAINER in run


async def test_ensure_router_reapplies_when_already_running(monkeypatch, tmp_path):
    """已在跑：重新应用规则（guard 口径可能变），不重复起容器。"""
    from app.deploy import engine

    calls = []

    async def spy(*a, **k):
        calls.append(a)
        if a[0] == "inspect":
            return 0, "true"
        return 0, ""

    monkeypatch.setattr(engine, "docker", spy)
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path))
    await sandbox_egress.ensure_egress_router()
    assert not any(c[0] == "run" for c in calls)
    assert any(c[0] == "exec" and "nft" in c for c in calls)