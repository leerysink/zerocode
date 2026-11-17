# app/source/guard.py
"""出站抓取的安全守卫：URL 预检、SSRF 判据、流式限量与逐跳重定向预检。

唯一真值：技能安装（app/skills/installer.py）与仓库快照摄入（app/source/ingest.py）
都调这里的判据。判据本身不做 DNS 解析校验——沿用 001/002 两轮复审在单机部署假定下
下的裁定，想收紧到防 DNS rebinding 必须先立 ADR，别在这里顺手加解析。

重定向不交给 httpx 自动跟随：逐跳 Manual redirect，落点先过 SSRF 预检再发下一跳，
内网落点根本不被连接；跨主机落点按请求摘掉 authorization（不永久突变共享 client）。
"""
import ipaddress
import re
from urllib.parse import urljoin, urlparse

MAX_URL_LEN = 1000      # user_skill.source_url VARCHAR(1000)：两条通道共用同一上限
MAX_REDIRECTS = 5
_REDIR_STATUSES = {301, 302, 303, 307, 308}


class GuardError(Exception):
    """出站抓取领域错误；可附 status_code（远端 HTTP 状态）与 oversize（体积/条数守卫）。"""

    status_code: int | None = None
    oversize: bool = False


class RefMissing(Exception):
    """尽力而为的引用取不到（非 200/网络异常）——调用方按语义跳过，不当失败。"""


def reject_internal_host(host: str | None) -> None:
    """拒内网/保留地址与混淆数字 IP 写法（127.1、2130706433、0x7f000001 等 inet_aton 缩写）。

    字面 IP 直接判网段；域名放行不做解析校验。方括号与尾点先归一，否则 `127.1.` 会被当域名放过。
    """
    if not host:
        raise GuardError("URL 缺少主机名")
    h = host.strip("[]").rstrip(".")
    if h == "localhost" or h.endswith(".localhost"):
        raise GuardError("不允许指向内网地址")
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        parts = h.split(".")
        if parts and all(re.fullmatch(r"0[xX][0-9a-fA-F]+|\d+", p) for p in parts):
            raise GuardError("不允许 IP 缩写/混淆写法")
        return
    if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
            or ip.is_unspecified or ip.is_multicast):
        raise GuardError("不允许指向内网/保留地址")


def validate_http_url(url: str, *, max_len: int = MAX_URL_LEN) -> None:
    """仅 http/https、长度在上限内、主机落在公网。"""
    if len(url) > max_len:
        raise GuardError(f"URL 超过 {max_len} 字符上限")
    parsed = urlparse(url)
    if parsed.scheme.lower() not in ("http", "https"):
        raise GuardError("仅支持 http/https 直链")
    reject_internal_host(parsed.hostname)


async def fetch_limited(client, url: str, label: str, *, required: bool,
                        max_bytes: int, headers: dict | None = None,
                        hops: int = 0) -> bytes:
    """流式下载并边下边限长；非 200 按 required 语义抛 GuardError / RefMissing。"""
    async with client.stream("GET", url, headers=headers) as resp:
        if resp.status_code in _REDIR_STATUSES:
            loc = resp.headers.get("location")
            if not loc or hops >= MAX_REDIRECTS:
                if required:
                    err = GuardError(f"拉取 {label} 失败: HTTP {resp.status_code}"
                                     + ("" if loc else "（重定向缺少 Location）"))
                    err.status_code = resp.status_code
                    raise err
                raise RefMissing(label)
            target = urljoin(url, loc)
            tp = urlparse(target)
            if tp.scheme.lower() not in ("http", "https"):
                raise GuardError("重定向目标协议不允许")
            reject_internal_host(tp.hostname)
            hop_hdrs = dict(headers) if headers is not None else {}
            if tp.hostname != urlparse(url).hostname:   # 跨主机落点不带凭证
                hop_hdrs["authorization"] = ""
            return await fetch_limited(client, target, label, required=required,
                                       max_bytes=max_bytes, headers=hop_hdrs, hops=hops + 1)
        if resp.status_code != 200:
            if required:
                err = GuardError(f"拉取 {label} 失败: HTTP {resp.status_code}")
                err.status_code = resp.status_code
                raise err
            raise RefMissing(label)
        declared = resp.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > max_bytes:
            raise _oversize(f"文件超过大小上限: {label}")
        chunks: list[bytes] = []
        total = 0
        async for chunk in resp.aiter_bytes():
            total += len(chunk)
            if total > max_bytes:
                raise _oversize(f"文件超过大小上限: {label}")
            chunks.append(chunk)
        return b"".join(chunks)


def _oversize(msg: str) -> GuardError:
    """体积/条数守卫专用标记：调用方按 oversize 反推超预算，不靠「有没有 status_code」猜。"""
    err = GuardError(msg)
    err.oversize = True
    return err
