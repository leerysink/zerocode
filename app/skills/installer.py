"""技能安装器：把远端技能取回并解析成「可落盘的技能载荷」。

两条通道（spec §5）：
- 直链（001）：URL 指向 SKILL.md，本体必须成功，同目录相对引用尽力拉取；
  github.com 的 /blob/…/SKILL.md 自动改写到 raw.githubusercontent.com 再拉。
- GitHub 仓库（002）：trees API 扫描 SKILL.md，恰一个直装整目录、多个返回 candidates、
  subpath 精确定位；纯 HTTP 无 git 依赖；全通道大小/文件数守卫（spec §3 故事3）。

transport 参数为测试注入 httpx.MockTransport 预留。所有失败路径统一收敛到
SkillError（api 层 400），不让 httpx/文件系统异常裸外溢成 500。
重定向不交给 httpx 自动跟随：逐跳 Manual redirect，落点先过 SSRF 预检再发请求
（002 收口 001 复审 Minor-1 的「事后拦读」残留）。
"""
import ipaddress
import json
import posixpath
import re
from urllib.parse import quote, urljoin, urlparse

import httpx

from app.config import github_token
from app.skills.library import (TOTAL_MAX_BYTES, FILE_COUNT_MAX, MAX_FILE_BYTES,
                                SkillError, check_size_budget, parse_skill_md)

# markdown 链接 ](...) 里的相对引用（排除绝对 URL、根路径、锚点）
_LINK = re.compile(r"\]\(([^)\s]+)")

_URL_MAX = 1000   # user_skill.source_url VARCHAR(1000)
_NAME_MAX = 200   # user_skill.name VARCHAR(200)
_MAX_REDIRECTS = 5
_REDIR_STATUSES = {301, 302, 303, 307, 308}


class _RefMissing(Exception):
    """引用的附属文件取不到（非 200/网络异常）——尽力而为，跳过不阻塞安装。"""


def _reject_internal_host(host: str | None) -> None:
    """入站 URL 与重定向落点共用：拒内网/保留地址与混淆数字 IP 写法（审查 I-1/M-1）。

    字面 IP 直接判网段；纯数字/16 进制点分形态（127.1、2130706433、0x7f000001 等
    inet_aton 缩写）一律拒，防 Linux 解析回环。域名不做 DNS 解析校验（边界，
    防 DNS rebinding 的解析后校验见 spec §5「安全预检」，单机部署假定下不做）。
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
        return  # 域名：不做解析后校验
    if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
            or ip.is_unspecified or ip.is_multicast):
        raise SkillError("不允许指向内网/保留地址")


def _validate_http_url(url: str) -> None:
    """安装前安全预检：仅 http(s)、长度在 DDL 上限内、主机落在公网（spec §5 安全预检）。"""
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
    return posixpath.basename(urlparse(url).path).upper() == "SKILL.MD"


def _rawize_github_blob(url: str) -> str:
    """github.com/o/r/blob/<ref>/<path> → raw.githubusercontent.com 同位置直链。

    blob 页返回 HTML，会让 frontmatter 解析误报（001 复审遗留 #9）；raw 才是文件本体。
    """
    parsed = urlparse(url)
    if parsed.hostname in ("github.com", "www.github.com"):
        segs = [s for s in parsed.path.split("/") if s]
        if len(segs) >= 4 and segs[2] == "blob":
            rest = "/".join(segs[3:])
            return f"{parsed.scheme}://raw.githubusercontent.com/{segs[0]}/{segs[1]}/{rest}"
    return url


async def _get_limited(client: httpx.AsyncClient, url: str, label: str, *,
                       required: bool, max_bytes: int = MAX_FILE_BYTES,
                       _hops: int = 0) -> bytes:
    """流式下载并边下边限长；非 200 按 required 语义抛 SkillError/_RefMissing。

    重定向手动逐跳：Location 先过 SSRF 预检再发下一跳请求，内网落点根本不被连接。
    """
    async with client.stream("GET", url) as resp:
        if resp.status_code in _REDIR_STATUSES:
            loc = resp.headers.get("location")
            if not loc or _hops >= _MAX_REDIRECTS:
                if required:
                    raise SkillError(f"拉取 {label} 失败: HTTP {resp.status_code}"
                                     + ("" if loc else "（重定向缺少 Location）"))
                raise _RefMissing(label)
            target = urljoin(url, loc)
            tp = urlparse(target)
            if tp.scheme.lower() not in ("http", "https"):
                raise SkillError("重定向目标协议不允许")
            _reject_internal_host(tp.hostname)
            return await _get_limited(client, target, label, required=required,
                                      max_bytes=max_bytes, _hops=_hops + 1)
        if resp.status_code != 200:
            if required:
                raise SkillError(f"拉取 {label} 失败: HTTP {resp.status_code}")
            raise _RefMissing(label)
        declared = resp.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > max_bytes:
            raise SkillError(f"文件超过大小上限: {label}")
        chunks: list[bytes] = []
        total = 0
        async for chunk in resp.aiter_bytes():
            total += len(chunk)
            if total > max_bytes:
                raise SkillError(f"文件超过大小上限: {label}")
            chunks.append(chunk)
        return b"".join(chunks)


def _gh_client(transport=None) -> httpx.AsyncClient:
    """GitHub API 请求客户端：带可选 token（缓解 rate limit）。

    不传 follow_redirects（httpx 默认 False）：重定向交给 _get_limited 逐跳预检。
    """
    headers = {}
    tok = github_token()
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    return httpx.AsyncClient(transport=transport, timeout=15.0, headers=headers)


async def fetch_skill_from_url(url: str, *, transport=None) -> dict:
    """GET 指向 SKILL.md 的直链，返回 {name, description, files, source_url, subpath, source_type}。

    files 为 relpath→bytes，至少含 SKILL.md 本体。同目录相对引用尽力拉取、失败忽略。
    """
    _validate_http_url(url)
    if not is_skill_md_url(url):
        raise SkillError("直链必须指向 SKILL.md 文件")
    url = _rawize_github_blob(url)
    client = httpx.AsyncClient(transport=transport, timeout=15.0)
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
                    # 引用文件超限/其它领域错误同样降级为跳过，不凭远端内容一票否决
                    pass
        except httpx.HTTPError as e:
            raise SkillError(f"远端请求失败: {type(e).__name__}") from e
        return {"name": name, "description": desc, "files": files,
                "source_url": url, "subpath": None, "source_type": "url"}
    finally:
        await client.aclose()


# ---- GitHub 仓库通道（002）----

def parse_github_url(url: str) -> dict | None:
    """github.com/owner/repo[/tree/ref[/subpath]] → {owner, repo, ref, subpath}。

    非 github.com 主机或指向 SKILL.md 的直链返回 None（走直链通道）；形态不对的
    github URL 明确报错，不再静默当直链处理。
    """
    parsed = urlparse(url)
    if parsed.hostname not in ("github.com", "www.github.com"):
        return None
    if is_skill_md_url(url):
        return None
    segs = [s for s in parsed.path.split("/") if s]
    if len(segs) < 2:
        raise SkillError("GitHub URL 至少要有 owner/repo")
    repo = segs[1][:-4] if segs[1].endswith(".git") else segs[1]
    out = {"owner": segs[0], "repo": repo, "ref": None, "subpath": None}
    if len(segs) > 2:
        if segs[2] != "tree" or len(segs) < 4:
            raise SkillError("仅支持仓库根 URL 或 /tree/<ref>[/<subpath>] 形态")
        out["ref"] = segs[3]
        if len(segs) > 4:
            out["subpath"] = "/".join(segs[4:])
    return out


def _is_skill_md(path: str) -> bool:
    return posixpath.basename(path).upper() == "SKILL.MD"


async def fetch_skill_from_github(url: str, *, subpath: str | None = None,
                                  transport=None) -> dict:
    """GitHub 仓库扫描安装（spec §3 故事1）：返回技能载荷或 {"candidates": [...]}。

    恰好一个 SKILL.md → 其所在目录整包下载安装；多个 → 候选清单（目录相对路径，
    含根目录 ""），前端带 subpath 二次提交同一 install 端点。
    """
    _validate_http_url(url)
    spec = parse_github_url(url)
    if spec is None:
        raise SkillError("不是可扫描的 GitHub 仓库 URL")
    api = f"https://api.github.com/repos/{quote(spec['owner'])}/{quote(spec['repo'])}"
    client = _gh_client(transport)
    try:
        try:
            ref = spec["ref"] or await _default_branch(client, api)
            tree = json.loads(await _get_limited(
                client, f"{api}/git/trees/{quote(ref, safe='/')}?recursive=1",
                "仓库文件树", required=True))
            entries = tree.get("tree") or []
            if tree.get("truncated"):
                raise SkillError("仓库文件树过大（truncated），请用 subpath 指向具体技能目录")
            prefix = ((subpath if subpath is not None else spec["subpath"]) or "").strip("/")
            hits = [e["path"] for e in entries
                    if e.get("type") == "blob" and _is_skill_md(e["path"])
                    and (not prefix or posixpath.dirname(e["path"]) == prefix)]
            scope = f"目录 {prefix}/" if prefix else "仓库"
            if len(hits) > 1:
                return {"candidates": [{"path": posixpath.dirname(h)} for h in sorted(hits)]}
            if not hits:
                raise SkillError(f"{scope}内未找到 SKILL.md，请确认它是符合 Agent Skills 标准的技能")
            base_dir = posixpath.dirname(hits[0])
            base_pre = f"{base_dir}/" if base_dir else ""
            blobs = [e for e in entries
                     if e.get("type") == "blob" and e["path"].startswith(base_pre)]
            if len(blobs) > FILE_COUNT_MAX:
                raise SkillError(f"技能文件数超过上限: {len(blobs)} > {FILE_COUNT_MAX}")
            declared_total = sum(int(e.get("size") or 0) for e in blobs)
            if declared_total > TOTAL_MAX_BYTES:
                raise SkillError(f"技能总大小超过上限: {declared_total} > {TOTAL_MAX_BYTES} 字节")
            raw_base = (f"https://raw.githubusercontent.com/{quote(spec['owner'])}/"
                        f"{quote(spec['repo'])}/{quote(ref, safe='/')}/")
            files: dict[str, bytes] = {}
            for e in blobs:
                rel = e["path"][len(base_pre):]
                files[rel] = await _get_limited(client, raw_base + quote(e["path"]),
                                                rel, required=True)
            check_size_budget(files)  # trees 元数据可能缺 size，按实际字节复核
            name, desc = parse_skill_md(files["SKILL.md"].decode("utf-8", "replace"))
            if len(name) > _NAME_MAX:
                raise SkillError(f"技能 name 超过 {_NAME_MAX} 字符上限")
        except httpx.HTTPError as e:
            raise SkillError(f"远端请求失败: {type(e).__name__}") from e
        return {"name": name, "description": desc, "files": files,
                "source_url": url, "subpath": base_dir or None, "source_type": "github"}
    finally:
        await client.aclose()


async def _default_branch(client: httpx.AsyncClient, api: str) -> str:
    info = json.loads(await _get_limited(client, api, "仓库信息", required=True))
    branch = str(info.get("default_branch") or "").strip()
    if not branch:
        raise SkillError("仓库没有默认分支，请用 /tree/<ref> 形态指定分支")
    return branch
