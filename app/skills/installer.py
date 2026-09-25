"""技能安装器：把远端 SKILL.md 取回并解析成「可落盘的技能载荷」。

本票（001）只做直链通道：URL 必须指向 SKILL.md，本体必须成功，同目录引用尽力拉取。
GitHub 仓库扫描通道在 002 追加。transport 参数为测试注入 httpx.MockTransport 预留。
所有失败路径统一收敛到 SkillError（api 层 400），不让 httpx/文件系统异常裸外溢成 500。
"""
import ipaddress
import posixpath
import re
from urllib.parse import urlparse

import httpx

from app.skills.library import MAX_FILE_BYTES, SkillError, parse_skill_md

# markdown 链接 ](...) 里的相对引用（排除绝对 URL、根路径、锚点）
_LINK = re.compile(r"\]\(([^)\s]+)")

_URL_MAX = 1000   # user_skill.source_url VARCHAR(1000)
_NAME_MAX = 200   # user_skill.name VARCHAR(200)


class _RefMissing(Exception):
    """引用的附属文件取不到（非 200/网络异常）——尽力而为，跳过不阻塞安装。"""


def _reject_internal_host(host: str | None) -> None:
    """入站 URL 与重定向落点共用：拒内网/保留地址与混淆数字 IP 写法（审查 I-1/M-1）。

    字面 IP 直接判网段；纯数字/16 进制点分形态（127.1、2130706433、0x7f000001 等
    inet_aton 缩写）一律拒，防 Linux 解析回环。域名不做 DNS 解析校验（001 边界，
    防 DNS rebinding 的解析后校验如需要后续票补）。
    """
    if not host:
        raise SkillError("URL 缺少主机名")
    h = host.strip("[]").rstrip(".")  # IPv6 包裹方括号 + 尾点 FQDN 形态归一（127.1. ≠ 域名）
    if h == "localhost" or h.endswith(".localhost"):
        raise SkillError("不允许指向内网地址")
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        parts = h.split(".")
        if parts and all(re.fullmatch(r"0[xX][0-9a-fA-F]+|\d+", p) for p in parts):
            raise SkillError("不允许 IP 缩写/混淆写法")
        return  # 域名：本票不做解析后校验
    if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
            or ip.is_unspecified or ip.is_multicast):
        raise SkillError("不允许指向内网/保留地址")


def _validate_direct_url(url: str) -> None:
    """安装前安全预检：仅 http(s)、长度在 DDL 上限内、主机落在公网（spec §6 SSRF 最小化）。"""
    if len(url) > _URL_MAX:
        raise SkillError(f"URL 超过 {_URL_MAX} 字符上限")
    parsed = urlparse(url)
    if parsed.scheme.lower() not in ("http", "https"):
        raise SkillError("仅支持 http/https 直链")
    _reject_internal_host(parsed.hostname)


def _relative_refs(md: str) -> list[str]:
    out: list[str] = []
    for m in _LINK.finditer(md):
        ref = m.group(1).strip().split("#", 1)[0].split("?", 1)[0]
        if (not ref or ref.startswith("/") or "\\" in ref
                or re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", ref)):
            continue
        norm = posixpath.normpath(ref)
        # 越出技能目录的引用（../）直接忽略：不拉取也不让整次安装被写盘守卫打死
        if norm == "." or norm.startswith("..") or posixpath.isabs(norm):
            continue
        out.append(norm)
    return out


def is_skill_md_url(url: str) -> bool:
    return urlparse(url).path.upper().endswith("/SKILL.MD") or \
        urlparse(url).path.upper() == "SKILL.MD"


async def _get_limited(client: httpx.AsyncClient, url: str, label: str, *,
                       required: bool) -> bytes:
    """流式下载并边下边限长；非 200 按 required 语义抛 SkillError/_RefMissing。"""
    async with client.stream("GET", url) as resp:
        # 重定向落点复查：公网 URL 借 302 跳内网的绕过口子（审查 I-1）
        _reject_internal_host(resp.url.host)
        for redir in resp.history:
            _reject_internal_host(redir.url.host)
        if resp.status_code != 200:
            if required:
                raise SkillError(f"拉取 {label} 失败: HTTP {resp.status_code}")
            raise _RefMissing(label)
        declared = resp.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > MAX_FILE_BYTES:
            raise SkillError(f"文件超过 1MB 上限: {label}")
        chunks: list[bytes] = []
        total = 0
        async for chunk in resp.aiter_bytes():
            total += len(chunk)
            if total > MAX_FILE_BYTES:
                raise SkillError(f"文件超过 1MB 上限: {label}")
            chunks.append(chunk)
        return b"".join(chunks)


async def fetch_skill_from_url(url: str, *, transport=None) -> dict:
    """GET 指向 SKILL.md 的直链，返回 {name, description, files, source_url, subpath}。

    files 为 relpath→bytes，至少含 SKILL.md 本体。同目录相对引用尽力拉取、失败忽略。
    """
    _validate_direct_url(url)
    if not is_skill_md_url(url):
        raise SkillError("直链必须指向 SKILL.md 文件")
    client = httpx.AsyncClient(transport=transport, timeout=15.0, follow_redirects=True)
    try:
        try:
            raw = await _get_limited(client, url, "SKILL.md", required=True)
            content = raw.decode("utf-8", "replace")
            name, desc = parse_skill_md(content)
            if len(name) > _NAME_MAX:
                raise SkillError(f"技能 name 超过 {_NAME_MAX} 字符上限")
            files: dict[str, bytes] = {"SKILL.md": raw}
            base = url[: url.rfind("/") + 1]
            for rel in _relative_refs(content):
                try:
                    files[rel] = await _get_limited(client, base + rel, rel, required=False)
                except _RefMissing:
                    pass  # 尽力而为：引用文件缺失不阻塞安装
                except (httpx.HTTPError, SkillError):
                    # 引用文件超限/其它领域错误同样降级为跳过，不凭远端内容一票否决（审查 M-2）
                    pass
        except httpx.HTTPError as e:
            raise SkillError(f"远端请求失败: {type(e).__name__}") from e
        return {"name": name, "description": desc, "files": files,
                "source_url": url, "subpath": None}
    finally:
        await client.aclose()
