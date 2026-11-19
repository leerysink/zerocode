# app/source/guard.py
"""出站抓取的安全守卫：URL 预检、SSRF 判据、流式限量与逐跳重定向预检。

唯一真值：技能安装（app/skills/installer.py）与仓库快照摄入（app/source/ingest.py）
都调这里的判据。判据本身不做 DNS 解析校验——沿用 001/002 两轮复审在单机部署假定下
下的裁定，想收紧到防 DNS rebinding 必须先立 ADR，别在这里顺手加解析。

重定向不交给 httpx 自动跟随：逐跳 Manual redirect，落点先过 SSRF 预检再发下一跳，
内网落点根本不被连接；跨主机落点按请求摘掉 authorization（不永久突变共享 client）。

**`GuardError` 是这里的唯一业务出口**：裸 `ValueError`（urlparse 的坏形态）之类的标准库异常一律
在守卫内部翻成 `GuardError`，绝不外泄。调用方的包装层（`installer._validate_http_url`/`_get_limited`
→ `SkillError`，`ingest.fetch_repo_snapshot` → `SourceError`）只翻译 `GuardError`，漏一条出去
就是一次 HTTP 500 —— 两条通道的 400 映射表都是按「守卫只会抛 GuardError/RefMissing」写的。
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


def is_internal_ip(ip) -> bool:
    """私网/保留地址判定——URL 闸（reject_internal_host）与沙箱网络层出口过滤共用同一份（ADR-0056）。"""
    return (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
            or ip.is_unspecified or ip.is_multicast)


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
    if is_internal_ip(ip):
        raise GuardError("不允许指向内网/保留地址")


def validate_http_url(url: str, *, max_len: int = MAX_URL_LEN) -> None:
    """仅 http/https、长度在上限内、主机落在公网、**URL 里不带内嵌凭证**。"""
    if len(url) > max_len:
        raise GuardError(f"URL 超过 {max_len} 字符上限")
    try:
        parsed = urlparse(url)
    except ValueError as e:
        # urlparse 对未闭合的 IPv6 字面量（`http://[::1/x`）抛的是**裸 ValueError**，而它排在调用方
        # 自己的 try 之前——漏出去就是 HTTP 500 + 一条 traceback，把「只往外抛领域错误」这条不变量
        # 从守卫这里破掉。守卫是两条通道唯一的入口，收口在这里，映射表才仍然是全函数。
        raise GuardError("URL 形态非法：这个地址解析不出主机名（方括号或主机部分不完整？）") from e
    if parsed.scheme.lower() not in ("http", "https"):
        raise GuardError("仅支持 http/https 直链")
    # 内嵌凭证一律拒收（终审 F1，安全）。为什么 SSRF 判据看不见它：`urlparse().hostname` 按定义
    # 不含 userinfo，于是 `https://oauth2:TOKEN@gitee.com/o/r` 一路通过；而后面每个端点都按硬编码
    # host 重拼、从不带鉴权头，所以那个 token **一个请求都不生效**，却会被原样冻进 manifest.sourceUrl
    # 与 README 的出处行——manifest 正是发布后公开站点上的一个文件（本特性的全部意义就是它公开可读）。
    # 平台侧永远匿名抓取，所以「拒绝」不损失任何能力，只关掉一条把用户凭证挂到公网的路径。
    # 文案刻意不回显 URL：400 detail 会进响应体与访问日志，把凭证从快照搬到这里等于没修。
    if parsed.username is not None or parsed.password is not None:
        raise GuardError("URL 里不允许携带账号或访问令牌（在主机名前面写用户名的那种写法）："
                         "本能力只匿名读取公开仓库，凭证不会生效，却会随快照泄漏到公开站点")
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
            # 落点由远端 Response 决定，比用户粘的串更不可信，所以它也在「守卫不外泄非领域异常」
            # 这条之内：未闭合的 IPv6 会让 urljoin/urlparse 抛裸 ValueError（urljoin 内部也解析
            # ref），两句一起包。今天 httpx 先把这个形态判成 RemoteProtocolError（其父类
            # httpx.HTTPError 由两条通道各自翻译），这里是 httpx 放宽校验后接管的第二层保险。
            try:
                target = urljoin(url, loc)
                tp = urlparse(target)
            except ValueError as e:
                raise GuardError("重定向目标形态非法：解析不出主机名") from e
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
