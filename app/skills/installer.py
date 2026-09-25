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
                    err = SkillError(f"拉取 {label} 失败: HTTP {resp.status_code}"
                                     + ("" if loc else "（重定向缺少 Location）"))
                    err.status_code = resp.status_code
                    raise err
                raise _RefMissing(label)
            target = urljoin(url, loc)
            tp = urlparse(target)
            if tp.scheme.lower() not in ("http", "https"):
                raise SkillError("重定向目标协议不允许")
            _reject_internal_host(tp.hostname)
            if tp.hostname != urlparse(url).hostname:  # 跨主机落点不带凭证（复审 F-6）
                client.headers.pop("authorization", None)
            return await _get_limited(client, target, label, required=required,
                                      max_bytes=max_bytes, _hops=_hops + 1)
        if resp.status_code != 200:
            if required:
                err = SkillError(f"拉取 {label} 失败: HTTP {resp.status_code}")
                err.status_code = resp.status_code
                raise err
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
            running = len(raw)
            base = url[: url.rfind("/") + 1]
            for rel in _relative_refs(content):
                try:
                    blob = await _get_limited(client, base + rel, rel, required=False)
                except _RefMissing:
                    pass  # 尽力而为：引用文件缺失不阻塞安装
                except (httpx.HTTPError, SkillError):
                    # 引用文件超限/其它领域错误同样降级为跳过，不凭远端内容一票否决
                    pass
                else:
                    # 复审 F-2：预算守卫搬进循环，缓冲/请求数不再由远端链接条数线性放大
                    running += len(blob)
                    if running > TOTAL_MAX_BYTES:
                        raise SkillError(f"技能总大小超过上限: > {TOTAL_MAX_BYTES} 字节")
                    if len(files) >= FILE_COUNT_MAX:
                        raise SkillError(f"技能文件数超过上限: > {FILE_COUNT_MAX}")
                    files[rel] = blob
            check_size_budget(files)  # 直链通道整包守卫（审缺 M-4：引用可无限累积）
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
    repo = segs[1][:-4] if segs[1].lower().endswith(".git") else segs[1]
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
    if subpath is not None and len(subpath) > 500:  # user_skill.subpath VARCHAR(500)
        raise SkillError("subpath 超过 500 字符上限")
    spec = parse_github_url(url)
    if spec is None:
        raise SkillError("不是可扫描的 GitHub 仓库 URL")
    api = f"https://api.github.com/repos/{quote(spec['owner'])}/{quote(spec['repo'])}"
    client = _gh_client(transport)
    try:
        try:
            ref = spec["ref"] or await _default_branch(client, api)
            tree = await _fetch_tree(client, api, ref)
            if tree is None:
                # 含斜杠分支名（feature/x）的 URL 天然歧义：404 时把 subpath 并入 ref 重试一次；
                # 与显式 subpath 参数共存——候选二次提交同样需回退（复审 F-1）
                if spec["subpath"]:
                    ref = f"{ref}/{spec['subpath']}"
                    spec["subpath"] = None
                    tree = await _fetch_tree(client, api, ref)
                if tree is None:
                    raise SkillError("获取仓库文件树失败：分支或路径不存在（HTTP 404）")
            entries = [e for e in (tree.get("tree") or []) if isinstance(e, dict)]
            if tree.get("truncated"):
                raise SkillError("仓库文件树过大（truncated），请用 subpath 指向具体技能目录")
            # 审查 B-1：显式 subpath（含空串「仓库根」候选）与未指定扫描全域语义不同，不再把 "" 折叠成 None
            scoped = subpath is not None or spec["subpath"] is not None
            prefix = ((subpath if subpath is not None else spec["subpath"]) or "").strip("/")
            hits = [str(e.get("path") or "") for e in entries
                    if e.get("type") == "blob" and _is_skill_md(str(e.get("path") or ""))
                    and (not scoped or posixpath.dirname(str(e.get("path") or "")) == prefix)]
            hits = [h for h in hits if h]
            if len(hits) > 1:
                return {"candidates": [{"path": posixpath.dirname(h)} for h in sorted(hits)]}
            if not hits:
                scope = f"目录 {prefix or '（仓库根）'}" if scoped else "仓库"
                raise SkillError(f"{scope}内未找到 SKILL.md，请确认它是符合 Agent Skills 标准的技能")
            base_dir = posixpath.dirname(hits[0])
            if len(base_dir) > 500:  # user_skill.subpath VARCHAR(500)，落库的是远端推导路径（复审 F-4）
                raise SkillError("技能路径超过 500 字符上限")
            base_pre = f"{base_dir}/" if base_dir else ""
            # base_dir=="" 时 startswith("") 恒真=整树，与「未指定 subpath 单命中装全仓」语义一致
            blobs = [e for e in entries
                     if e.get("type") == "blob" and str(e.get("path") or "").startswith(base_pre)]
            if len(blobs) > FILE_COUNT_MAX:
                raise SkillError(f"技能文件数超过上限: {len(blobs)} > {FILE_COUNT_MAX}")
            try:  # 远端 shape 异常不能外溢成 500（复审 F-3）
                declared_total = sum(int(str(e.get("size") or 0)) for e in blobs)
            except ValueError:
                raise SkillError("仓库文件树元数据异常：size 非数值") from None
            if declared_total > TOTAL_MAX_BYTES:
                raise SkillError(f"技能总大小超过上限: {declared_total} > {TOTAL_MAX_BYTES} 字节")
            raw_base = (f"https://raw.githubusercontent.com/{quote(spec['owner'])}/"
                        f"{quote(spec['repo'])}/{quote(ref, safe='/')}/")
            files: dict[str, bytes] = {}
            running = 0
            for e in blobs:
                path = str(e.get("path") or "")
                if not path:
                    continue
                rel = path[len(base_pre):]
                data = await _get_limited(client, raw_base + quote(path), rel, required=True)
                running += len(data)  # 元数据可能缺失，下载中按实际字节边接边拒（审查 I-2）
                if running > TOTAL_MAX_BYTES:
                    raise SkillError(f"技能总大小超过上限: > {TOTAL_MAX_BYTES} 字节")
                files[rel] = data
            check_size_budget(files)
            # 命中判定大小写不敏感，回取也按同一规则（审查 I-1：防 skill.md 变体 KeyError→500）
            key = next((k for k in files if _is_skill_md(k)), None)
            if key is None:
                raise SkillError("技能目录内未找到 SKILL.md，无法解析 frontmatter")
            name, desc = parse_skill_md(files[key].decode("utf-8", "replace"))
            if len(name) > _NAME_MAX:
                raise SkillError(f"技能 name 超过 {_NAME_MAX} 字符上限")
        except httpx.HTTPError as e:
            raise SkillError(f"远端请求失败: {type(e).__name__}") from e
        return {"name": name, "description": desc, "files": files,
                "source_url": url, "subpath": base_dir or None, "source_type": "github"}
    finally:
        await client.aclose()


async def _fetch_tree(client: httpx.AsyncClient, api: str, ref: str) -> dict | None:
    """拉解析后的 trees JSON；404 返 None（给含斜杠分支的回退重试作信号），其余失败直报。"""
    try:
        raw = await _get_limited(
            client, f"{api}/git/trees/{quote(ref, safe='/')}?recursive=1",
            "仓库文件树", required=True, max_bytes=5 * 1024 * 1024)
    except SkillError as e:
        code = getattr(e, "status_code", None)
        if code == 404:  # 按语义属性判，不盯文案（复审 F-8）
            return None
        if code is not None:
            raise  # 403/5xx 等 HTTP 错误原样上报
        # 无 status_code = 响应体超预算（复审 F-5：独立文案，不误报普通 HTTP 错）
        raise SkillError("仓库过大（文件树响应超过 5MB），请用 /tree/<ref>/<subpath> 指向具体目录") from e
    return _json_or_error(raw, "仓库文件树")


def _json_or_error(raw: bytes, label: str) -> dict:
    try:
        data = json.loads(raw)
    except ValueError as e:
        raise SkillError(f"{label}响应不是合法 JSON") from e
    if not isinstance(data, dict):
        raise SkillError(f"{label}响应不是 JSON 对象")
    return data


async def _default_branch(client: httpx.AsyncClient, api: str) -> str:
    info = _json_or_error(await _get_limited(client, api, "仓库信息", required=True), "仓库信息")
    branch = str(info.get("default_branch") or "").strip()
    if not branch:
        raise SkillError("仓库没有默认分支，请用 /tree/<ref> 形态指定分支")
    return branch
