# app/source/ingest.py
"""把公开 Gitee 仓库抓成「内存快照载荷」：全树一次列路径，正文逐个走 raw。

钻取范式照搬技能安装 002（app/skills/installer.py 的 trees?recursive=1 + 逐 blob 下载），
区别只在筛选目标：那边找 SKILL.md，这边按文本白名单收全部正文。

预算为什么是这个数：
- MAX_FILE_BYTES = 200 KB 对齐 agent 的 read_file 单次上限（app/agent/tools.py:10），
  快照里的每个文件都能一次读完，不必让模型翻页；
- TOTAL_MAX_BYTES = 8 MB 比技能的 10 MB 保守，因为快照要烘进应用镜像（Dockerfile 的
  `COPY . .`），镜像里塞几十 MB 噪声是给发布链添堵；
- FILE_COUNT_MAX = 400：一次官网文档站够装几百篇，再多就是整仓 mirror，不是素材快照。

不出网的部分一律前置：URL 预检、host 支持判定、ref/subpath 的路径形态闸、树元数据的声明体积
都在第一个字节下载之前，所以「失败零副作用」是结构保证，不是靠 try/finally 兜出来的。
形态闸为什么也在这里：`..` 不是「路径怪一点」，而是会被服务端的点段归一改写成同域另一条 API
路径，脏值还会冻进 manifest 的 ref/subpath 一路流到 README 的出处行。
"""
from __future__ import annotations

import datetime
import json
import posixpath
import re

import httpx

from app.source import guard, hosting
from app.source.guard import GuardError
from app.workspace_ignore import IGNORE_DIRS

TREE_MAX_BYTES = 8 * 1024 * 1024
MAX_FILE_BYTES = 200 * 1024
TOTAL_MAX_BYTES = 8 * 1024 * 1024
FILE_COUNT_MAX = 400
_SUBPATH_MAX = 500

TEXT_EXTS = {".md", ".mdx", ".txt", ".rst", ".json", ".yaml", ".yml", ".csv", ".toml", ".ini"}
LOCKFILE_NAMES = {"package-lock.json", "pnpm-lock.yaml", "yarn.lock", "npm-shrinkwrap.json",
                  "poetry.lock", "uv.lock", "composer.lock", "Cargo.lock", "Gemfile.lock",
                  "go.sum", "Pipfile.lock", "mix.lock"}
_HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")


class SourceError(Exception):
    """快照摄入领域错误；api 层统一映射 HTTP 400，可带远端 status_code。"""

    status_code: int | None = None


def is_eligible(path: str) -> tuple[bool, str | None]:
    """这个仓库路径要不要进快照：返回 (收/不收, 原因)。

    远端树里什么都可能有（`.git/config`、`a\\b.md`、`../x`），先判形态再判内容；
    反斜杠与越界路径不收，因为落盘侧还要再过一次同一道闸（materialize 的 _safe_rel）。
    """
    if not path or path.startswith("/") or "\\" in path:
        return False, "unsafe-path"
    norm = posixpath.normpath(path)
    if norm.startswith("..") or norm == ".":
        return False, "unsafe-path"
    parts = norm.split("/")
    if any(p in IGNORE_DIRS for p in parts[:-1]) or parts[-1] in IGNORE_DIRS:
        return False, "ignored-dir"
    if parts[-1] in LOCKFILE_NAMES:
        return False, "lockfile"
    if posixpath.splitext(parts[-1])[1].lower() not in TEXT_EXTS:
        return False, "not-text"
    return True, None


def first_heading(text: str) -> str | None:
    """取正文第一个 ATX 标题做卡片标题；跳过代码围栏里的伪标题。"""
    in_fence = False
    for line in text.splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = _HEADING.match(line)
        if m:
            # 形态判定全在正则里：`#{1,6}\s+` 已经要求井号后跟空白，`#x` 根本匹配不上。
            # 原先这里还跟着一个「整串比 group(1) 长 1 以上」的长度比较，匹配成功就恒为真，
            # 而且注释给的理由（挡 `#x`）也是错的 —— 死条件删掉，别再教错原因。
            return m.group(1)
    return None


def _require_safe_shape(value: str | None, label: str) -> None:
    """路径形态闸：`.`/`..`/空段/反斜杠一律拒，判据与 parse_repo_url 用的是同一条。

    为什么必须在拼 URL 之前：`quote(x, safe='/')` 原样放行斜杠，于是 `git/trees/..` 经服务端
    点段归一会把 `/git/trees/` 吃掉一截，一条用户粘的 URL 就能打到同域任意 API 路径；这些脏值
    还会被冻进 manifest 的 ref/subpath，Task 4 直接把它写进 README 的出处行。
    """
    bad = hosting.unsafe_path_segment(value)
    if bad is not None:
        raise SourceError(f"{label}{hosting.UNSAFE_SHAPE_HINT}（问题段 {bad!r}：{value!r}）")


def _client(transport=None) -> httpx.AsyncClient:
    """不传 follow_redirects：重定向交给 guard.fetch_limited 逐跳预检。"""
    return httpx.AsyncClient(transport=transport, timeout=20.0)


async def _get_json(client, url: str, label: str, *, max_bytes: int = TREE_MAX_BYTES) -> dict:
    raw = await guard.fetch_limited(client, url, label, required=True, max_bytes=max_bytes)
    try:
        data = json.loads(raw)
    except ValueError as e:
        raise SourceError(f"{label}响应不是合法 JSON") from e
    if not isinstance(data, dict):
        raise SourceError(f"{label}响应不是 JSON 对象")
    return data


async def fetch_repo_snapshot(url: str, *, subpath: str | None = None,
                              transport=None) -> dict:
    """抓 Gitee 仓库文本快照，返回 {"manifest": {...}, "files": {...}}。"""
    try:
        guard.validate_http_url(url)
    except GuardError as e:
        raise SourceError(str(e)) from e
    if subpath is not None and len(subpath) > _SUBPATH_MAX:
        raise SourceError(f"subpath 超过 {_SUBPATH_MAX} 字符上限")
    try:
        ref = hosting.parse_repo_url(url)
    except hosting.HostingError as e:
        # 「看着像仓库但形态不对」也要收敛成 SourceError：api 层只认 SourceError→400，
        # 漏一条 HostingError 出去就是一次 500（Task 6 的映射表只有一行）。
        raise SourceError(str(e)) from e
    if ref is None:
        raise SourceError(f"只支持公开托管仓库，当前接入：{hosting.SUPPORTED_HOST_LABEL}")
    # 三种意图互不相同，别把空串折叠成 None（口径同 installer 审查 B-1）：
    # 显式 subpath="" 是「就要仓库根」，URL 里的 /tree/<ref>/<subpath> 是「钻这个目录」，
    # 两者都没有才是全域；显式参数优先于 URL 带的路径。
    scoped = (subpath if subpath is not None else (ref.subpath or "")).strip("/")
    # 上限对两个来源一视同仁：原来只卡 subpath 参数，URL 里带的 /tree/<ref>/<subpath> 走的是
    # 同一份拼接却不受限，归一到 scoped 之后再判才封得住。
    if len(scoped) > _SUBPATH_MAX:
        raise SourceError(f"subpath 超过 {_SUBPATH_MAX} 字符上限：{len(scoped)} 字符")
    # 形态闸排在拼第一个 URL 之前。ref.ref / ref.subpath 由 parse_repo_url 判（那里拒的是「这条
    # URL 本身不是合法仓库形态」，两条通道都受益），这里补上解析层看不到的一半：显式 subpath 参数
    # 归并出来的 scoped，加上后面远端 JSON 给的默认分支与合并候选。
    _require_safe_shape(scoped, "子路径")
    client = _client(transport)
    try:
        branch = ref.ref or hosting.default_branch_of(
            await _get_json(client, hosting.api_repo_url(ref), "仓库信息"))
        _require_safe_shape(branch, "分支")   # default_branch 是远端返回的，一样不许带点段
        tree_json = await _fetch_tree(client, ref, branch)
        if tree_json is None:
            # 含斜杠分支名（feature/x）在 URL 里天然歧义：404 时把已判定的子目录并进 ref 再试一次。
            # 显式 subpath 参数走同一条回退——候选二次提交时它也带着 subpath（照抄 installer 的裁定）。
            if scoped:
                base_branch = branch
                merged = f"{branch}/{scoped}"
                # scoped 与 branch 各自在上面都过了闸，这是拼 URL 前的最后一道回手闸：
                # 判的是「真正要进 /git/trees/ 和 /raw/ 的那个值」本身，日后这里若换了来源，
                # 漏判也不会逃出去（同 RefMissing 那条兜底的口径，注释里说清是回手闸而不是活闸）。
                _require_safe_shape(merged, "分支与子路径的合并结果")
                # 合并只许追加、不许替换：候选必须以原分支开头，否则 manifest["ref"] 就不再是用户
                # 点名的那个分支（Task 4 的出处行把它当事实引用）。今天这一条由上一行的拼接保证，
                # 留在这里是把契约钉住，真正干活的是上面那道形态判。
                if not merged.startswith(f"{base_branch}/"):
                    raise SourceError(
                        f"分支与子路径合并结果异常：{merged!r} 不以 {base_branch!r} 为前缀")
                branch = merged
                tree_json = await _fetch_tree(client, ref, branch)
                # 子路径已经并进 ref：命中与否都不再单独提它，否则 404 文案重复拼一遍、
                # 命中时又会把同一个目录当 subpath 二次过滤。
                scoped = ""
            if tree_json is None:
                # branch 就是最后一次请求的那个 ref（合并过就自带子路径），所以文案只报它：
                # 插回 subpath 参数的话，URL 带进来的 /tree/<ref>/<subpath> 会整段从用户看得见
                # 的文本里消失（终审 Minor 1）。
                raise SourceError(f"获取仓库文件树失败：分支或路径不存在（HTTP 404）：{branch}")
        entries = [e for e in (tree_json.get("tree") or []) if isinstance(e, dict)]
        if tree_json.get("truncated"):
            raise SourceError("仓库文件树被截断（truncated），请用 /tree/<ref>/<subpath> 或 subpath 参数收窄范围")
        candidates = []
        skipped: list[dict] = []
        for e in entries:
            path = str(e.get("path") or "")
            if e.get("type") != "blob":
                continue
            ok, reason = is_eligible(path)
            if not ok:
                skipped.append({"path": path, "reason": reason})
                continue
            if scoped and posixpath.dirname(path) != scoped and not path.startswith(scoped + "/"):
                continue      # 域外文件不进快照，也不算「跳过」——它从来不在本次范围里
            try:
                declared = int(str(e.get("size") or 0))
            except ValueError:
                raise SourceError("仓库文件树元数据异常：size 非数值") from None
            candidates.append((path, declared))
        # 声明体积的总量预估排在单文件上限剔除之前（同 installer 的 264-270 行口径）：
        # 树自报的在范围内的文本总量已经越过整个快照预算，这次目标的体量就不是「素材快照」，
        # 不值得为它发几百次请求再逐文件挑掉——当场收工，一个字节都不下载。
        declared_total = sum(size for _, size in candidates)
        if declared_total > TOTAL_MAX_BYTES:
            # 文案带阶段：这条是「按树里声明的 size 预估」撞的，和下载中按实际字节撞的那条
            # 是同一种超预算、不同阶段，支持侧只看文案就要分得清是哪种。
            raise SourceError(
                f"文本总大小超过上限（按文件树声明预估）: {declared_total} > {TOTAL_MAX_BYTES} 字节")
        picked = []
        for path, declared in candidates:
            if declared > MAX_FILE_BYTES:
                skipped.append({"path": path, "reason": "too-large"})
                continue
            picked.append((path, declared))
        if len(picked) > FILE_COUNT_MAX:
            raise SourceError(f"文件数超过上限: {len(picked)} > {FILE_COUNT_MAX}")
        files: dict[str, bytes] = {}
        titles: list[dict] = []
        running = 0
        for path, _ in picked:
            try:
                data = await guard.fetch_limited(
                    client, hosting.raw_file_url(ref, branch, path), path,
                    required=True, max_bytes=MAX_FILE_BYTES)
            except GuardError as e:
                # 单文件级降级：树里列着但 raw 取不到（改名/子模块/5xx）不该一票否决整篇摄入。
                # required=True 时守卫抛的是 GuardError(status_code)，不是 RefMissing，
                # 所以 404 判在这里、不另设 except RefMissing（那条永远不触发，是 installer 可选引用的形态）。
                if e.oversize:              # 声明体积与实际不符：跳过而不是打死整次摄入
                    skipped.append({"path": path, "reason": "too-large"})
                    continue
                if e.status_code in (404, 410):
                    skipped.append({"path": path, "reason": "not-found"})
                    continue
                if e.status_code is not None and e.status_code >= 500:
                    skipped.append({"path": path, "reason": "server-error"})
                    continue
                raise _remote_error(e) from e   # 安全类拒绝与配额/权限原样上报，别用「跳过」掩盖配置错误
            running += len(data)
            if running > TOTAL_MAX_BYTES:
                raise SourceError(
                    f"文本总大小超过上限（下载中按实际字节累计）: {running} > {TOTAL_MAX_BYTES} 字节")
            files[path] = data
            text = data.decode("utf-8", "replace")
            titles.append({"path": path, "bytes": len(data),
                           "title": first_heading(text), "dir": posixpath.dirname(path)})
        return {
            "manifest": {
                "schema": "zc-source/1",
                "host": "gitee", "owner": ref.owner, "repo": ref.repo, "ref": branch,
                "sourceUrl": url,
                "fetchedAt": datetime.datetime.now(datetime.timezone.utc)
                    .replace(microsecond=0).isoformat(),
                "subpath": scoped or None,
                # 恒为 False：树自报 truncated 在上面就已经抛错中止，能走到这里的都是没截断的树。
                # 字段是 brief 冻结的载荷契约（Task 4 按字段名落盘），别把它当成信息读、也别删。
                "truncated": bool(tree_json.get("truncated")),
                "counts": {"listed": len(entries), "snapshotted": len(files),
                           "skipped": len(skipped)},
                "files": sorted(titles, key=lambda f: f["path"]),
                "skipped": sorted(skipped, key=lambda s: s["path"])[:200],
            },
            "files": files,
        }
    except GuardError as e:
        raise _remote_error(e) from e
    except hosting.HostingError as e:
        raise SourceError(str(e)) from e
    except guard.RefMissing as e:
        # 全通道 required=True，这条今天走不到；留在这里是「守卫的两类异常一律不外泄」的兜底，
        # 免得日后有人把某个引用改成尽力而为时，把 RefMissing 直接递给 api 层变成 500。
        raise SourceError(f"远端引用取不到: {e}") from e
    except httpx.HTTPError as e:
        raise SourceError(f"远端请求失败: {type(e).__name__}") from e
    finally:
        await client.aclose()


def _remote_error(e: GuardError) -> SourceError:
    """把守卫的通用文案换成能决定下一步的话：401 是权限、403 在 Gitee 上是配额或拒绝。

    匿名配额实测 `X-RateLimit-Limit: 60`（次/小时）——不点破这一层，用户会以为仓库不存在，
    反复重试同一件事。oversize 与 SSRF 拒绝没有 status_code，原样带上，别改文案掩盖真因。
    """
    code = e.status_code
    if code in (401, 403):
        msg = ("Gitee API 配额已用尽或访问被拒（匿名 60 次/小时），请稍后重试" if code == 403
               else "仓库不可读（私有或需要授权），本能力只接公开仓库")
    else:
        msg = str(e)
    err = SourceError(msg)
    err.status_code = code
    return err


async def _fetch_tree(client, ref: hosting.RepoRef, branch: str) -> dict | None:
    """拉解析后的树 JSON；404 返 None 给含斜杠分支名（feature/x）的回退重试当信号。

    判据是 `GuardError.status_code`，不是异常类型——`guard.fetch_limited` 抛的是 GuardError，
    这里若照抄「except SourceError」就是死分支：403 配额外溢成通用文案、404 永远等不到回退重试。
    超预算（oversize）与 SSRF/协议拒绝各有专属文案，不折叠成「分支不存在」。
    """
    try:
        return await _get_json(client, hosting.api_tree_url(ref, branch), "仓库文件树")
    except GuardError as e:
        if e.status_code == 404:
            return None
        if e.oversize:
            raise SourceError(
                f"仓库文件树响应超过 {TREE_MAX_BYTES // (1024 * 1024)}MB，"
                "请用 /tree/<ref>/<subpath> 或 subpath 参数收窄范围") from e
        raise
