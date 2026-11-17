# 仓库源码摄入（Gitee URL → 官网内容）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan 逐 task 执行。步骤用 checkbox（`- [ ]`）跟踪。

**Goal:** 让用户在建应用时粘一个 Gitee 仓库地址，平台在**构建期**把该仓库的文本内容抓成快照烘进产物，生成的站点既能被 AI 当素材读，也能在发布后自己 fetch 快照做「往下钻取」。

**Architecture:** 三件事串起来——① `app/source/` 新包做托管站适配与快照抓取（复用 `app/skills/installer.py` 已验证的 trees 递归 + raw 逐文件范式，SSRF 判据抽成共享模块）；② 摄入产物通过 `create_app_project` **既有的 `materialize` 接缝**落进工作区（商店安装用的同一条缝，不新增铺源路径），快照落在 `frontend/public/source/`，Vite 原样拷进 `dist`，dev 与 prod 都按 `/source/**` 直出，不改 nginx/vite 配置；③ 建应用端点加可选 `source` 字段，抓取**先于**建应用发生，失败即 400 且零副作用。

**Tech Stack:** Python 3 + FastAPI + httpx（`MockTransport` 注入）+ pytest；Vue 3 + ant-design-vue + Vite + vitest；PostgreSQL（**本票不改表结构**）。

**Spec:** `docs/specs/2026-11-17-repo-source-ingest.md`（由 Task 8 交付；本文 §1 的缺口清单就是它的规格来源，执行到 Task 8 前以 §1 为准）。

## 1. 缺口分析（规格来源）

本轮实测 `https://gitee.com/leerys/zerocode`（公开仓、匿名可读）的四条通道，全部返回 200：

| 通道 | 实测 | 结论 |
|---|---|---|
| 仓库 HTML 页 | 200 / 22 KB | 前端渲染，正文不全 —— **不用** |
| API v5 仓库元数据 `/api/v5/repos/{o}/{r}` | 200 / 6.6 KB | `default_branch="master"`，`private=false` |
| API v5 递归文件树 `/api/v5/repos/{o}/{r}/git/trees/{ref}?recursive=1` | 200 / 253 KB / 1002 条 | 条目含 `path/mode/type/sha/size/url`，`type ∈ {blob,tree}`，顶层含 `truncated` —— **一次调用拿到全仓库路径，就是「往下钻取」的入口** |
| raw 正文 `/raw/{ref}/{path}` | 200 / 21 KB（README.md） | 真 markdown，不吃 API 配额 |

匿名 API 配额实测 `X-RateLimit-Limit: 60`、`Remaining: 55`（次/小时）。

对照产品现状的四段缺口，与本票的处置：

| # | 缺口 | 现状证据 | 本票处置 |
|---|---|---|---|
| 1 | 没有仓库 URL 入口 | `AppIn` 只有 `name/slug/scale/stack`（`app/api/apps.py:33-37,105-113`）；附件只收图片（`app/api/attachments.py:35-37`） | Task 6 加 `source` 字段 |
| 2 | 抓取/钻取只服务 GitHub 技能 | `app/skills/installer.py:248` hostname 写死 github.com，`:385` 已有 `?recursive=1` 全树范式 | Task 1~3 抽共享守卫 + 新建 Gitee 适配 |
| 3 | 内容进不了工作区（只有模板渲染） | `app/services/app_service.py:175-179` 默认 `scaffold.render_layers`；`:151-153` 已声明 `materialize` 铺源钩子 | Task 4~5 走既有钩子，不另起一套 |
| 4 | 站点读不到内容 | `static` 模板无 `public/`、无 router（`app/templates/static/frontend/package.json`）；`vue3-springboot` 已有 `frontend/public/` 先例 | Task 4 把快照落 `frontend/public/source/`，Vite 天然直出 |
## 2. Global Constraints（每个 task 都隐含遵守）

- 后端测试命令：`.venv/Scripts/python.exe -m pytest tests/<file> -q`（`pytest.ini` 已设 `asyncio_mode=auto`、`testpaths=tests`；口径见 `docs/developer-guide.md:126,149`）。**定向跑，不要顺手全量**：全量 pytest 会 down 掉宿主上正在跑的 dev 预览栈，且必须串行（共享 `zerocode_test` 库并发 TRUNCATE 会死锁）。
- 模块级 `pytestmark = pytest.mark.asyncio` 的口径（2026-11-17 裁定，先前派单口头传过但没落文）：`asyncio_mode=auto` 下加不加都能跑。**含同步用例的文件不要加**——每个纯同步用例会多刷一条 PytestWarning（`tests/test_source_guard.py`、`tests/test_source_ingest.py`、`tests/test_source_hosting.py`、`tests/test_source_materialize.py` 依此，注释就地写明）。**全异步的文件跟随同目录既有写法**——`tests/test_apps_api.py:24` 带着这行，同域的 `tests/test_apps_source_api.py` 保持一致，不为零收益制造分裂。
- 前端测试命令：`cd frontend && npx vitest run <file>`；全量加 `--maxWorkers=4`（`docs/developer-guide.md:149`）。
- **不改平台表结构、不加迁移**。`app_project.origin` 是 `VARCHAR(60)`（`changelog/V20260917_01_init.sql`），只放短标记 `repo:gitee`，URL 全文留在快照 manifest 里。
- 产物写盘一律 UTF-8 + `newline=""`（对齐 `app/deploy/scaffold.py:44`）；`nginx.conf` 类配置文件换行必须 LF。
- **不改 agent 工具面**：`app/agent/registry.py` 的 `SPECS` 与 `tests/test_agent_registry.py` 同名同序钉死，本票一个工具都不加。AI 读快照走既有 `list_dir`/`read_file`/`search`。
- **不回退技能安装通道**：`tests/test_skills_api.py` + `tests/test_skills_github_api.py` 在 Task 1 前后都必须全绿，报错文案逐字不变。
- 中文提交信息，**不带 AI 协作者署名**；未经明确指示不 `git push`、不 merge、不开 PR。
- 真实副作用（建应用、起 dev 预览、发布）等用户下令：Task 9 整票是 gated 的。
- 本票**明确不做**（记入 ADR-0051）：私有仓库 token、GitHub 快照通道、agent 侧任意 URL 抓取工具、运行时反代 Gitee、docs 站的 SSR/sitemap/robots、自定义域名、应用卡片来源角标。

## 3. File Structure

```
app/source/                      ← 新建包（本票主体）
  __init__.py                    新建：包声明（对齐 app/skills/ 与 app/store/ 都带 __init__.py）
  guard.py                       新建：SSRF 判据 + URL 预检 + 流式限量下载（唯一真值）
  hosting.py                     新建：托管站端点表 + URL→RepoRef 解析（纯函数）
  ingest.py                      新建：全树抓取→快照载荷（预算/白名单/标题）
  materialize.py                 新建：快照落 frontend/public/source + README 溯源段
app/skills/installer.py          修改：三个出站辅助函数改薄包装，判据下沉到 guard
app/services/app_service.py      修改：create_app_project 增 source 参数，组合进 render
app/api/apps.py                  修改：AppIn 增 source 子模型 + 抓取先于建应用
frontend/src/utils/appSource.js  新建：向导载荷组装（纯函数，可单测）
frontend/src/utils/appSource.test.js  新建
frontend/src/views/AppsView.vue  修改：第三步加「仓库地址」输入 + submit 带 source
docs/adr/0051-repo-source-ingest-boundary.md      新建
docs/specs/2026-11-17-repo-source-ingest.md       新建
CONTEXT.md                       修改：新增「托管源/仓库快照/烘快照」术语
tests/test_source_guard.py       新建
tests/test_source_hosting.py     新建
tests/test_source_ingest.py      新建
tests/test_source_materialize.py 新建
tests/test_source_seam.py        新建
tests/test_apps_source_api.py    新建
```

---

## Phase A — 摄入内核（无 UI、无 DB）

### Task 1: 抽出共享出站守卫 `app/source/guard.py`

把 `app/skills/installer.py` 里三段「与安全相关、抄一份就会漂移」的逻辑（网段判据、URL 预检、逐跳重定向 + 流式限量）下沉成唯一真值；installer 改成薄包装，公开行为与文案逐字不变。**recursion/refs 语义（`_RefMissing`、跨主机剥 authorization、`oversize`/`status_code` 标记）必须原样保住**——它们都是 001/002 两轮复审钉出来的。

**Files:**
- Create: `app/source/__init__.py`
- Create: `app/source/guard.py`
- Modify: `app/skills/installer.py:14-37`（删导入、`_URL_MAX`/`_MAX_REDIRECTS`/`_REDIR_STATUSES` 常量与 `class _RefMissing` 定义——它现在由 `_RefMissing = guard.RefMissing` 别名取代）、`:40-71`（两个校验函数体）、`:127-175`（`_get_limited` 函数体）
- Test: `tests/test_source_guard.py`

**Interfaces:**
- Produces:
  - `class GuardError(Exception)`，属性 `status_code: int | None = None`、`oversize: bool = False`
  - `class RefMissing(Exception)`
  - `MAX_URL_LEN: int = 1000`
  - `reject_internal_host(host: str | None) -> None`
  - `validate_http_url(url: str, *, max_len: int = MAX_URL_LEN) -> None`
  - `async fetch_limited(client, url, label, *, required: bool, max_bytes: int, headers: dict | None = None, hops: int = 0) -> bytes`
- Consumes: 无（只依赖 stdlib + httpx）

- [ ] **Step 1: 建包并写守卫模块**

`app/source/__init__.py`：

```python
"""仓库源码摄入：把公开托管站（当前 Gitee）的文本内容抓成快照，烘进应用产物。"""
```

`app/source/guard.py`：

```python
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
```

- [ ] **Step 2: 写守卫的失败测试**

`tests/test_source_guard.py`：

```python
# tests/test_source_guard.py
"""共享出站守卫：SSRF 判据、URL 预检、逐跳重定向预检与流式限量。

判据清单与 app/skills/installer.py 的既有用例同源——Task 1 把判据下沉到这里后，
同一批敌意输入必须在两边得到同一个结论，否则两条通道会各自漂移。
"""
import httpx
import pytest

from app.skills import installer
from app.source import guard

pytestmark = pytest.mark.asyncio

HOSTILE = [
    ("http://localhost/skill", "内网"),
    ("http://a.localhost/skill", "内网"),
    ("http://127.0.1/skill", "混淆"),
    ("http://2130706433/skill", "混淆"),
    ("http://0x7f000001/skill", "混淆"),
    ("http://10.0.0.5/skill", "内网"),
    ("http://169.254.169.254/skill", "内网"),
    ("ftp://example.com/skill", "协议"),
]


@pytest.mark.parametrize("url", [u for u, _ in HOSTILE])
def test_validate_http_url_rejects(url):
    with pytest.raises(guard.GuardError):
        guard.validate_http_url(url)


@pytest.mark.parametrize("url", [u for u, _ in HOSTILE])
def test_installer_and_guard_agree(url):
    """两条通道同一个判据：installer 的包装必须把敌意 URL 同样拦下。"""
    with pytest.raises(installer.SkillError):
        installer._validate_http_url(url)


def test_url_length_cap():
    with pytest.raises(guard.GuardError) as e:
        guard.validate_http_url("https://example.com/" + "a" * guard.MAX_URL_LEN)
    assert "字符上限" in str(e.value)


def test_public_host_passes():
    guard.validate_http_url("https://gitee.com/leerys/zerocode")


async def test_fetch_limited_streams_and_caps_size():
    seen = []

    async def handler(request):
        seen.append(request)
        return httpx.Response(200, content=b"x" * 100)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    assert await guard.fetch_limited(client, "https://example.com/f", "f",
                                     required=True, max_bytes=100) == b"x" * 100
    with pytest.raises(guard.GuardError) as e:
        await guard.fetch_limited(client, "https://example.com/f", "f",
                                  required=True, max_bytes=99)
    assert e.value.oversize is True
    await client.aclose()


async def test_redirect_rechecks_target_before_connecting():
    calls = []

    async def handler(request):
        calls.append(str(request.url))
        if request.url.host == "example.com":
            return httpx.Response(302, headers={"location": "http://127.0.0.1/evil"})
        return httpx.Response(200, content=b"never")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(guard.GuardError):
        await guard.fetch_limited(client, "https://example.com/a", "a",
                                  required=True, max_bytes=10)
    assert calls == ["https://example.com/a"], "内网落点被连接了：预检没跑在请求之前"
    await client.aclose()


async def test_cross_host_redirect_drops_authorization_per_request():
    seen = []

    async def handler(request):
        seen.append((request.url.host, request.headers.get("authorization", "")))
        if request.url.host == "a.example":
            return httpx.Response(302, headers={"location": "https://b.example/f"})
        return httpx.Response(200, content=b"ok")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    await guard.fetch_limited(client, "https://a.example/f", "f", required=True,
                              max_bytes=10, headers={"authorization": "Bearer tok"})
    assert seen == [("a.example", "Bearer tok"), ("b.example", "")]
    # 不永久突变共享 client：同一次任务里后续同源调用仍带 token
    assert client.headers.get("authorization") == "Bearer tok"
    await client.aclose()


async def test_optional_ref_missing_is_separate_exception():
    async def handler(request):
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(guard.RefMissing):
        await guard.fetch_limited(client, "https://example.com/x", "x",
                                  required=False, max_bytes=10)
    await client.aclose()
```

- [ ] **Step 3: 跑测试确认守卫模块失败（installer 侧还没接上）**

Run: `.venv/Scripts/python.exe -m pytest tests/test_source_guard.py -q`
Expected: `test_installer_and_guard_agree` 里 `installer._validate_http_url` 若仍自带判据也应通过；若收集不到 `guard` 模块则整文件 ERROR——先确认失败原因只剩「installer 尚未复用」。

- [ ] **Step 4: 把 installer 改成薄包装**

`app/skills/installer.py` 删掉 `:40-71`（`_reject_internal_host`、`_validate_http_url`）与 `:127-175`（`_get_limited`）三个函数体，改成：

```python
from app.source import guard
from app.source.guard import MAX_URL_LEN as _URL_MAX

_RefMissing = guard.RefMissing


def _reject_internal_host(host: str | None) -> None:
    try:
        guard.reject_internal_host(host)
    except guard.GuardError as e:
        raise SkillError(str(e)) from e


def _validate_http_url(url: str) -> None:
    try:
        guard.validate_http_url(url, max_len=_URL_MAX)
    except guard.GuardError as e:
        raise SkillError(str(e)) from e


async def _get_limited(client: httpx.AsyncClient, url: str, label: str, *,
                       required: bool, max_bytes: int = MAX_FILE_BYTES,
                       _hdrs: dict | None = None) -> bytes:
    try:
        return await guard.fetch_limited(client, url, label, required=required,
                                         max_bytes=max_bytes, headers=_hdrs)
    except guard.GuardError as e:
        err = SkillError(str(e))
        err.status_code = e.status_code
        err.oversize = e.oversize
        raise err from e
    except guard.RefMissing as e:
        raise _RefMissing(label) from e
```

同文件里删掉不再使用的 `import ipaddress`、`_MAX_REDIRECTS`、`_REDIR_STATUSES`，并把 `_fetch_blob` 调用点的 `_hdrs=` 关键字保持不变（签名同名）。`_oversize_error` 保留，但改为翻译守卫标记：

```python
def _oversize_error(msg: str) -> SkillError:
    err = SkillError(msg)
    err.oversize = True
    return err
```

> `_hops` 参数从 `_get_limited` 签名去掉：递归已下沉到 guard，installer 内所有调用点只传 `required`/`max_bytes`/`_hdrs`（原文 `:156` 的自递归是唯一用到它的地方，已随函数体一起删掉）。

- [ ] **Step 5: 跑守卫测试 + 两条技能通道回归**

Run: `.venv/Scripts/python.exe -m pytest tests/test_source_guard.py tests/test_skills_api.py tests/test_skills_github_api.py -q`
Expected: 全 PASS。技能两个文件是本轮的护栏——文案、`oversize` 专属提示、候选清单、token 注入、404 回退任何一条变红，就是 Step 4 移丢了语义，回去补，不要改测试期望。

- [ ] **Step 6: 提交**

```bash
git add app/source/__init__.py app/source/guard.py tests/test_source_guard.py app/skills/installer.py
git commit -m "refactor(source): 出站 SSRF 判据与流式限量下沉为共享守卫，技能通道改薄包装"
```

---

### Task 2: 托管站适配表 `app/source/hosting.py`

只做两件事：把 Gitee 仓库 URL 解析成结构化引用，把每个站的四种端点形态收敛成一张表。纯函数、不碰网络，所以先写。

**Files:**
- Create: `app/source/hosting.py`
- Test: `tests/test_source_hosting.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class RepoRef: host: str; owner: str; repo: str; ref: str | None; subpath: str | None`
  - `parse_repo_url(url: str) -> RepoRef | None`（非已接入站返回 `None`；形态错误抛 `HostingError`）
  - `class HostingError(Exception)`
  - `api_repo_url(ref: RepoRef) -> str` / `api_tree_url(ref: RepoRef, branch: str) -> str` / `raw_file_url(ref: RepoRef, branch: str, path: str) -> str` / `default_branch_of(repo_json: dict) -> str`
  - `SUPPORTED_HOST_LABEL: str = "gitee.com"`
- Consumes: `app.source.guard`（URL 预检由调用方 Task 3 做，本模块只解析）

- [ ] **Step 1: 写解析失败测试**

`tests/test_source_hosting.py`：

```python
# tests/test_source_hosting.py
"""Gitee 仓库 URL 解析与端点形态（URL 全部对照 2026-11-17 实测 200 的真实端点写）。"""
import pytest

from app.source import hosting

pytestmark = pytest.mark.asyncio


def test_repo_root_url():
    ref = hosting.parse_repo_url("https://gitee.com/leerys/zerocode")
    assert (ref.host, ref.owner, ref.repo, ref.ref, ref.subpath) == (
        "gitee.com", "leerys", "zerocode", None, None)


def test_dot_git_suffix_stripped():
    assert hosting.parse_repo_url("https://gitee.com/leerys/zerocode.git").repo == "zerocode"


def test_tree_ref_and_subpath():
    ref = hosting.parse_repo_url("https://gitee.com/leerys/zerocode/tree/master/docs/adr")
    assert (ref.ref, ref.subpath) == ("master", "docs/adr")


def test_www_host_normalized_to_gitee():
    assert hosting.parse_repo_url("https://www.gitee.com/leerys/zerocode").host == "gitee.com"


def test_unsupported_host_returns_none():
    assert hosting.parse_repo_url("https://github.com/owner/repo") is None
    assert hosting.parse_repo_url("https://gitlab.com/owner/repo") is None


def test_skill_md_direct_link_is_not_a_repo():
    assert hosting.parse_repo_url(
        "https://gitee.com/leerys/x/raw/master/SKILL.md") is None
    # 文件页形态（/blob/）同样是指向单个文件，不是仓库
    assert hosting.parse_repo_url(
        "https://gitee.com/leerys/x/blob/master/SKILL.md") is None


def test_skill_md_under_tree_dir_is_still_a_repo():
    """`/tree/<ref>/<path>` 指的是目录，末段叫 SKILL.md 也仍是仓库地址：
    浏览器里复制目录链接容易带上这一层，把它判成「不是仓库」会把真仓库拒在门外。"""
    ref = hosting.parse_repo_url("https://gitee.com/o/r/tree/master/skills/foo/SKILL.md")
    assert (ref.ref, ref.subpath) == ("master", "skills/foo/SKILL.md")


@pytest.mark.parametrize("bad", [
    "https://gitee.com/onlyowner",
    "https://gitee.com/leerys/zerocode/branch/master",
    "https://gitee.com/leerys/zerocode/tree",
])
def test_malformed_repo_url_raises(bad):
    with pytest.raises(hosting.HostingError):
        hosting.parse_repo_url(bad)


def test_tree_with_slashed_ref_is_resolvable_by_caller():
    """含斜杠分支名靠调用方回退重试，这里先按「ref=master、subpath=dev/x」给出初判。"""
    ref = hosting.parse_repo_url("https://gitee.com/o/r/tree/master/dev/x")
    assert (ref.ref, ref.subpath) == ("master", "dev/x")


def test_endpoint_shapes_match_measured_working_urls():
    ref = hosting.parse_repo_url("https://gitee.com/leerys/zerocode")
    assert hosting.api_repo_url(ref) == "https://gitee.com/api/v5/repos/leerys/zerocode"
    assert hosting.api_tree_url(ref, "master") == (
        "https://gitee.com/api/v5/repos/leerys/zerocode/git/trees/master?recursive=1")
    assert hosting.raw_file_url(ref, "master", "docs/adr/0001-x.md") == (
        "https://gitee.com/leerys/zerocode/raw/master/docs/adr/0001-x.md")


def test_raw_path_quotes_reserved_chars():
    ref = hosting.parse_repo_url("https://gitee.com/o/r")
    assert hosting.raw_file_url(ref, "master", "a b/目录/c#1.md") == (
        "https://gitee.com/o/r/raw/master/a%20b/%E7%9B%AE%E5%BD%95/c%231.md")


def test_default_branch_from_repo_json():
    assert hosting.default_branch_of({"default_branch": "master"}) == "master"
    with pytest.raises(hosting.HostingError):
        hosting.default_branch_of({})
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_source_hosting.py -q`
Expected: FAIL —— `ModuleNotFoundError: No module named 'app.source.hosting'`

- [ ] **Step 3: 写适配表**

`app/source/hosting.py`：

```python
# app/source/hosting.py
"""公开托管站的 URL 形态与端点表（当前只接 Gitee）。

四个端点形态对照 2026-11-17 实测 200 的真实地址写死，不是猜的：
- 仓库元数据  https://gitee.com/api/v5/repos/{owner}/{repo}            → default_branch
- 递归文件树  https://gitee.com/api/v5/repos/{owner}/{repo}/git/trees/{ref}?recursive=1
- raw 正文    https://gitee.com/{owner}/{repo}/raw/{ref}/{path}
递归树一次调用就给全仓库路径（实测 1002 条 / 253 KB），这就是「往下钻取」的入口；
正文走 raw，不占 API 配额（匿名 API 只有 60 次/小时）。

刻意不做的事（详见 ADR-0051）：不接 GitHub/GitLab（形态相近但配额与鉴权语义不同，
等真需要时另票）、不带 token（私有仓不在本票范围）、不解析 blob 网页形态
（`/-/blob/` 与 `/blob/` 返回 HTML，正文一律走 raw）。
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote, urlparse

SUPPORTED_HOST_LABEL = "gitee.com"
_GITEE_HOSTS = ("gitee.com", "www.gitee.com")
_API_ROOT = "https://gitee.com/api/v5/repos"


class HostingError(Exception):
    """托管站 URL 形态或端点解析失败；api 层映射为 400。"""


@dataclass(frozen=True)
class RepoRef:
    host: str
    owner: str
    repo: str
    ref: str | None
    subpath: str | None


def parse_repo_url(url: str) -> RepoRef | None:
    """gitee.com/owner/repo[/tree/<ref>[/<subpath>]] → RepoRef；非 Gitee 返回 None。

    返回 None 是「这不是仓库，走别的通道」；抛 HostingError 是「这看着像仓库但形态不对」——
    静默把坏形态当直链处理，用户只会看到一个看不懂的成功。
    """
    parsed = urlparse(url)
    if parsed.hostname not in _GITEE_HOSTS:
        return None
    segs = [s for s in parsed.path.split("/") if s]
    # 只有「文件直链」形态才让路给技能直链通道：`/raw/` 与 `/blob/` 指向单个文件，
    # 末段是 SKILL.md 时判成非仓库（口径同 installer.is_skill_md_url）。
    # `/tree/<ref>/<path>` 是目录形态，末段叫 SKILL.md 也仍然是仓库地址——浏览器复制目录链接
    # 常常带到最后一段，把它判成「不是仓库」会把真仓库拒在门外（终审 Important 的裁定）。
    if (len(segs) >= 5 and segs[2] in ("raw", "blob")
            and segs[-1].upper() == "SKILL.MD"):
        return None
    if len(segs) < 2:
        raise HostingError("Gitee 仓库地址至少要有 owner/repo")
    repo = segs[1][:-4] if segs[1].lower().endswith(".git") else segs[1]
    out = RepoRef(host="gitee.com", owner=segs[0], repo=repo, ref=None, subpath=None)
    if len(segs) > 2:
        if segs[2] != "tree" or len(segs) < 4:
            raise HostingError("仅支持仓库根 URL 或 /tree/<ref>[/<subpath>] 形态")
        out = RepoRef(host=out.host, owner=out.owner, repo=out.repo,
                      ref=segs[3], subpath="/".join(segs[4:]) or None)
    return out


def api_repo_url(ref: RepoRef) -> str:
    return f"{_API_ROOT}/{quote(ref.owner)}/{quote(ref.repo)}"


def api_tree_url(ref: RepoRef, branch: str) -> str:
    return (f"{_API_ROOT}/{quote(ref.owner)}/{quote(ref.repo)}"
            f"/git/trees/{quote(branch, safe='/')}?recursive=1")


def raw_file_url(ref: RepoRef, branch: str, path: str) -> str:
    return (f"https://{ref.host}/{quote(ref.owner)}/{quote(ref.repo)}"
            f"/raw/{quote(branch, safe='/')}/{quote(path)}")


def default_branch_of(repo_json: dict) -> str:
    branch = str(repo_json.get("default_branch") or "").strip()
    if not branch:
        raise HostingError("仓库没有默认分支，请用 /tree/<ref> 指定分支")
    return branch
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest tests/test_source_hosting.py -q`
Expected: PASS（14 项 = 11 个单例 + `test_malformed_repo_url_raises` 的 3 个参数化用例）。
`test_skill_md_direct_link_is_not_a_repo` 与 `test_skill_md_under_tree_dir_is_still_a_repo` 是一对方向相反的闸：前者要求 `/raw/`、`/blob/` 两种文件形态让路给技能直链通道，后者要求 `/tree/<ref>/…/SKILL.md` 这种目录形态**仍然**判成仓库——两头都不许靠放宽测试解决。`test_malformed_repo_url_raises` 的三项必须是真坏形态（缺 repo 段、`/branch/`、裸 `/tree`）；`/tree/<ref>` 单项是合法形态，不在这一列（Task 3 的 `.../tree/dev` 与 `.../tree/nope` 用例依赖它解析成功）。

- [ ] **Step 5: 提交**

```bash
git add app/source/hosting.py tests/test_source_hosting.py
git commit -m "feat(source): Gitee 仓库 URL 解析与端点表，形态对照实测地址"
```

---

### Task 3: 快照抓取 `app/source/ingest.py`

全树 → 筛选 → 逐文件 raw，产出「载荷」（内存里的 `files` + `manifest`），不落盘。预算全部在执行前用树里声明的 `size` 预估一次、下载中按实际字节再守一次（远端元数据可能缺）。

**Files:**
- Create: `app/source/ingest.py`
- Test: `tests/test_source_ingest.py`

**Interfaces:**
- Produces:
  - `class SourceError(Exception)`，属性 `status_code: int | None = None`
  - `fetch_repo_snapshot(url: str, *, subpath: str | None = None, transport=None) -> dict`（`async`）
  - 载荷契约（Task 4/5/6/9 全靠它，字段名冻结）：

```python
{
  "manifest": {
    "schema": "zc-source/1",
    "host": "gitee", "owner": "leerys", "repo": "zerocode", "ref": "master",
    "sourceUrl": "https://gitee.com/leerys/zerocode",
    "fetchedAt": "2026-11-17T09:12:00+00:00",
    "subpath": None,
    "truncated": False,
    "counts": {"listed": 1002, "snapshotted": 187, "skipped": 815},
    "files": [{"path": "README.md", "bytes": 21770, "title": "ZeroCode", "dir": ""}],
    "skipped": [{"path": "frontend/package-lock.json", "reason": "lockfile"}],
  },
  "files": {"README.md": b"...", "docs/user-manual.md": b"..."},   # relpath → bytes
}
```

  - `first_heading(text: str) -> str | None`
  - `is_eligible(path: str) -> tuple[bool, str | None]`（第二元素是跳过原因）
- Consumes: Task 1 `guard.fetch_limited` / `guard.validate_http_url` / `guard.GuardError` / `guard.RefMissing`；Task 2 `hosting.*`

- [ ] **Step 1: 写抓取失败测试**

`tests/test_source_ingest.py`：

```python
# tests/test_source_ingest.py
"""仓库快照抓取：全树一次拿路径、正文走 raw、预算与白名单、失败语义。

transport 是 httpx.MockTransport，伪造 gitee.com 与 api/v5 两个 host，
真网络一个都不碰（真仓端到端验收在 Task 9，且要用户下令）。
"""
import json
import pytest

from app.source import ingest

pytestmark = pytest.mark.asyncio

URL = "https://gitee.com/leerys/zerocode"


def _blob(path, size=100):
    return {"path": path, "mode": "100644", "type": "blob", "sha": "s" + path[:6], "size": size}


def _tree_entry(path):
    return {"path": path, "mode": "40000", "type": "tree", "sha": "t" + path[:6], "size": 0}


def _transport(tree, files, *, default_branch="master", truncated=False,
                trees_status=200, repo_status=200, requests=None, bad_branch=None):
    import httpx

    async def handler(request):
        if requests is not None:
            requests.append(str(request.url))
        host, p = request.url.host, request.url.path
        if host == "gitee.com" and p.startswith("/api/v5/repos/"):
            if p.endswith("/git/trees") or "/git/trees/" in p:
                if trees_status != 200:
                    return httpx.Response(trees_status, json={"message": "rate limited"})
                ref = p.split("/git/trees/")[1].split("?")[0]
                if bad_branch and ref == bad_branch:
                    return httpx.Response(404, json={"message": "Not Found"})
                return httpx.Response(200, json={"tree": tree, "truncated": truncated})
            if repo_status != 200:
                return httpx.Response(repo_status, json={"message": "Forbidden"})
            return httpx.Response(200, json={"default_branch": default_branch})
        if host == "gitee.com" and p.startswith("/leerys/zerocode/raw/"):
            fp = "/".join(p.split("/")[4:])
            if fp in files:
                return httpx.Response(200, content=files[fp])
            return httpx.Response(404)
        return httpx.Response(404)

    return httpx.MockTransport(handler)


TREE = [_blob("README.md"), _tree_entry("docs"), _blob("docs/user-manual.md"),
        _blob("frontend/package-lock.json"), _blob("docs/images/a.png"),
        _blob("node_modules/evil/index.md"), _blob(".git/config")]
FILES = {"README.md": b"# ZeroCode\n\nbody",
         "docs/user-manual.md": b"# 使用手册\n\nbody",
         "frontend/package-lock.json": b"{}",
         "docs/images/a.png": b"\x89PNG",
         "node_modules/evil/index.md": b"# nope",
         ".git/config": b"[core]"}


async def test_happy_path_pulls_tree_then_raw_bodies():
    calls = []
    t = _transport(TREE, FILES, requests=calls)
    out = await ingest.fetch_repo_snapshot(URL, transport=t)
    assert set(out["files"]) == {"README.md", "docs/user-manual.md"}
    m = out["manifest"]
    assert (m["host"], m["repo"], m["ref"]) == ("gitee", "zerocode", "master")
    assert m["counts"]["listed"] == len(TREE)
    assert m["counts"]["snapshotted"] == 2
    assert {f["path"] for f in m["files"]} == {"README.md", "docs/user-manual.md"}
    assert calls[0].endswith("/api/v5/repos/leerys/zerocode")     # 先问默认分支
    assert "/git/trees/master?recursive=1" in calls[1]           # 再一次拿全树
    assert sum(1 for c in calls if "/raw/" in c) == 2            # 正文只下白名单内的


async def test_titles_come_from_first_heading():
    out = await ingest.fetch_repo_snapshot(URL, transport=_transport(TREE, FILES))
    by_path = {f["path"]: f for f in out["manifest"]["files"]}
    assert by_path["README.md"]["title"] == "ZeroCode"
    assert by_path["README.md"]["dir"] == ""
    assert by_path["docs/user-manual.md"]["dir"] == "docs"


async def test_skip_reasons_are_recorded_not_silent():
    out = await ingest.fetch_repo_snapshot(URL, transport=_transport(TREE, FILES))
    reasons = {s["path"]: s["reason"] for s in out["manifest"]["skipped"]}
    assert reasons["frontend/package-lock.json"] == "lockfile"
    assert reasons["docs/images/a.png"] == "not-text"
    assert reasons["node_modules/evil/index.md"] == "ignored-dir"
    assert reasons[".git/config"] == "ignored-dir"


async def test_explicit_ref_skips_default_branch_call():
    calls = []
    await ingest.fetch_repo_snapshot("https://gitee.com/leerys/zerocode/tree/dev",
                                     transport=_transport(TREE, FILES, requests=calls))
    assert not any(c.endswith("/api/v5/repos/leerys/zerocode") for c in calls)


async def test_subpath_narrows_snapshot_scope():
    out = await ingest.fetch_repo_snapshot(URL, subpath="docs",
                                           transport=_transport(TREE, FILES))
    assert set(out["files"]) == {"docs/user-manual.md"}
    assert out["manifest"]["subpath"] == "docs"


async def test_truncated_tree_asks_for_subpath():
    with pytest.raises(ingest.SourceError) as e:
        await ingest.fetch_repo_snapshot(URL, transport=_transport(TREE, FILES, truncated=True))
    assert "truncated" in str(e.value) or "过大" in str(e.value)


async def test_rate_limited_tree_reports_quota():
    with pytest.raises(ingest.SourceError) as e:
        await ingest.fetch_repo_snapshot(URL, transport=_transport(TREE, FILES, trees_status=403))
    assert "配额" in str(e.value)
    assert e.value.status_code == 403


async def test_private_repo_reports_unreadable():
    with pytest.raises(ingest.SourceError) as e:
        await ingest.fetch_repo_snapshot(URL, transport=_transport(TREE, FILES, repo_status=401))
    assert "不可读" in str(e.value)


async def test_unknown_branch_reports_404():
    with pytest.raises(ingest.SourceError):
        await ingest.fetch_repo_snapshot("https://gitee.com/leerys/zerocode/tree/nope",
                                         transport=_transport(TREE, FILES, bad_branch="nope"))


async def test_total_budget_aborts_before_downloading():
    big = [_blob(f"d{i}.md", size=ingest.TOTAL_MAX_BYTES) for i in range(2)]
    with pytest.raises(ingest.SourceError) as e:
        await ingest.fetch_repo_snapshot(URL, transport=_transport(big, {}))
    assert "总大小" in str(e.value)


async def test_oversize_single_file_is_skipped_not_fatal():
    tree = [_blob("big.md", size=ingest.MAX_FILE_BYTES + 1), _blob("ok.md", size=10)]
    files = {"big.md": b"x" * (ingest.MAX_FILE_BYTES + 1), "ok.md": b"# ok"}
    out = await ingest.fetch_repo_snapshot(URL, transport=_transport(tree, files))
    assert set(out["files"]) == {"ok.md"}
    assert out["manifest"]["skipped"][0]["reason"] == "too-large"


async def test_file_count_cap_enforced():
    tree = [_blob(f"f{i}.md") for i in range(ingest.FILE_COUNT_MAX + 1)]
    files = {f"f{i}.md": b"# x" for i in range(ingest.FILE_COUNT_MAX + 1)}
    with pytest.raises(ingest.SourceError) as e:
        await ingest.fetch_repo_snapshot(URL, transport=_transport(tree, files))
    assert "文件数" in str(e.value)


async def test_internal_url_rejected_before_any_request():
    for bad in ("https://127.0.0.1/o/r", "https://localhost/o/r", "http://10.0.0.1/o/r"):
        with pytest.raises(ingest.SourceError):
            await ingest.fetch_repo_snapshot(bad, transport=_transport([], {}))


async def test_unsupported_host_says_which_hosts_work():
    with pytest.raises(ingest.SourceError) as e:
        await ingest.fetch_repo_snapshot("https://github.com/o/r", transport=_transport([], {}))
    assert "gitee.com" in str(e.value)


async def test_manifest_is_json_safe():
    out = await ingest.fetch_repo_snapshot(URL, transport=_transport(TREE, FILES))
    json.dumps(out["manifest"], ensure_ascii=False)
    assert out["manifest"]["schema"] == "zc-source/1"
    assert out["manifest"]["fetchedAt"].startswith("20")


def test_first_heading_ignores_code_fence_and_prefixed_lines():
    assert ingest.first_heading("# T\n\n```text\n# not me\n```") == "T"
    assert ingest.first_heading("nope\n## Sub") == "Sub"
    assert ingest.first_heading("#x") is None
    assert ingest.first_heading("") is None


def test_is_eligible_extension_and_lockfile_and_dir():
    assert ingest.is_eligible("docs/a.md") == (True, None)
    assert ingest.is_eligible("a.yaml") == (True, None)
    assert ingest.is_eligible("a.png")[1] == "not-text"
    assert ingest.is_eligible("package-lock.json")[1] == "lockfile"
    assert ingest.is_eligible(".git/config")[1] == "ignored-dir"
    assert ingest.is_eligible("../escape.md")[1] == "unsafe-path"
    assert ingest.is_eligible("/abs.md")[1] == "unsafe-path"
    assert ingest.is_eligible("a\\b.md")[1] == "unsafe-path"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_source_ingest.py -q`
Expected: FAIL —— `ModuleNotFoundError: No module named 'app.source.ingest'`

- [ ] **Step 3: 写抓取实现**

`app/source/ingest.py`：

```python
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

不出网的部分一律前置：URL 预检、host 支持判定、树元数据的声明体积都在第一个字节下载之前，
所以「失败零副作用」是结构保证，不是靠 try/finally 兜出来的。
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
        if m and len(m.group(0)) > len(m.group(1)) + 1:   # `#x` 不是标题，`# x` 才是
            return m.group(1)
    return None


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
    ref = hosting.parse_repo_url(url)
    if ref is None:
        raise SourceError(f"只支持公开托管仓库，当前接入：{hosting.SUPPORTED_HOST_LABEL}")
    scoped = (subpath if subpath is not None
              else (ref.subpath or "")).strip("/")
    client = _client(transport)
    try:
        branch = ref.ref or hosting.default_branch_of(
            await _get_json(client, hosting.api_repo_url(ref), "仓库信息"))
        tree_json = await _fetch_tree(client, ref, branch)
        if tree_json is None:
            # 含斜杠分支名（feature/x）在 URL 里天然歧义：404 时把已判定的子目录并进 ref 再试一次。
            # 显式 subpath 参数走同一条回退——候选二次提交时它也带着 subpath（照抄 installer 的裁定）。
            if scoped:
                branch = f"{branch}/{scoped}"
                tree_json = await _fetch_tree(client, ref, branch)
                if tree_json is not None:
                    scoped = ""
            if tree_json is None:
                raise SourceError(f"获取仓库文件树失败：分支或路径不存在（HTTP 404）：{branch}"
                                  f"{'/' + subpath if subpath else ''}")
        entries = [e for e in (tree_json.get("tree") or []) if isinstance(e, dict)]
        if tree_json.get("truncated"):
            raise SourceError("仓库文件树被截断（truncated），请用 /tree/<ref>/<subpath> 或 subpath 参数收窄范围")
        picked = []
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
                continue
            try:
                declared = int(str(e.get("size") or 0))
            except ValueError:
                raise SourceError("仓库文件树元数据异常：size 非数值") from None
            if declared > MAX_FILE_BYTES:
                skipped.append({"path": path, "reason": "too-large"})
                continue
            picked.append((path, declared))
        if len(picked) > FILE_COUNT_MAX:
            raise SourceError(f"文件数超过上限: {len(picked)} > {FILE_COUNT_MAX}")
        if sum(size for _, size in picked) > TOTAL_MAX_BYTES:
            raise SourceError(
                f"文本总大小超过上限: {sum(size for _, size in picked)} > {TOTAL_MAX_BYTES} 字节")
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
                raise SourceError(f"文本总大小超过上限: {running} > {TOTAL_MAX_BYTES} 字节")
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
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest tests/test_source_ingest.py -q`
Expected: PASS。逐条对着失败信息改实现，**不要改测试期望**——这些用例是从实测端点形态反推的。`test_rate_limited_tree_reports_quota` 若红，先看 `_remote_error` 是否把 403 落到了配额文案（403=配额/拒绝，401=权限，两条不能并成一句）；`test_unknown_branch_reports_404` 若红，多半是 `_fetch_tree` 的 404→None 与外层「分支或路径不存在」那条没接上。`test_skip_reasons_are_recorded_not_silent` 若红，检查 `is_eligible` 的判定顺序（形态 → 忽略目录 → lockfile → 扩展名）。

- [ ] **Step 5: 提交**

```bash
git add app/source/ingest.py tests/test_source_ingest.py
git commit -m "feat(source): Gitee 全树快照抓取，预算/白名单/失败语义齐备"
```

---

## Phase B — 接入建应用链（复用既有 materialize 接缝）

### Task 4: 快照落盘 `app/source/materialize.py`

`create_app_project` 早就留了铺源钩子（`app/services/app_service.py:151-153` 的 docstring 明说商店安装用它）。本票照商店安装的形状再做一个，先渲染模板层、再写快照、最后给 README 追加溯源段。**落点选 `frontend/public/source/`**：Vite 把 `public/` 原样拷进 `dist`，dev 预览与发布后的 nginx 静态站点都按 `/source/**` 直出，`app/templates/static/frontend/nginx.conf:13` 已有的 `try_files $uri $uri/ /index.html;` 回落不干扰真实文件——不改 vite 配置、不改 nginx、不引 router。

**Files:**
- Create: `app/source/materialize.py`
- Test: `tests/test_source_materialize.py`

**Interfaces:**
- Consumes: Task 3 的载荷 dict；`app.deploy.scaffold.render_layers`
- Produces:
  - `SOURCE_ROOT_PARTS = ("frontend", "public", "source")`
  - `MANIFEST_REL = "frontend/public/source/manifest.json"`
  - `FILES_REL_PREFIX = "frontend/public/source/files/"`
  - `write_snapshot(root: Path, payload: dict) -> None`
  - `append_readme_provenance(root: Path, manifest: dict) -> None`
  - `compose_with_source(base_render, payload: dict): Callable[[Path, dict], None]`

- [ ] **Step 1: 写落盘失败测试**

`tests/test_source_materialize.py`：

```python
# tests/test_source_materialize.py
"""快照落盘：模板仍在、快照在 frontend/public/source、README 溯源、越界路径被拦。

纯文件系统断言（tmp_path），不碰 DB 也不起容器；接缝级验证在 Task 5，端到端在 Task 9。
"""
import json
from pathlib import Path

import pytest

from app.deploy import errors as deploy_errors
from app.source import materialize

pytestmark = pytest.mark.asyncio


def _fake_template(tmp_path: Path) -> Path:
    t = tmp_path / "tpl" / "static"
    (t / "frontend" / "src").mkdir(parents=True)
    (t / "frontend" / "package.json").write_text('{"name":"{{SLUG}}-frontend"}',
                                                  encoding="utf-8", newline="")
    (t / "frontend" / "src" / "App.vue").write_text("<template>{{APP_NAME}}</template>",
                                                     encoding="utf-8", newline="")
    (t / "README.md").write_text("# {{APP_NAME}}\n\n模板正文\n", encoding="utf-8", newline="")
    return t


def _payload(**over):
    m = {"schema": "zc-source/1", "host": "gitee", "owner": "leerys", "repo": "zerocode",
         "ref": "master", "sourceUrl": "https://gitee.com/leerys/zerocode",
         "fetchedAt": "2026-11-17T09:12:00+00:00", "subpath": None, "truncated": False,
         "counts": {"listed": 4, "snapshotted": 2, "skipped": 2},
         "files": [{"path": "README.md", "bytes": 3, "title": "T", "dir": ""},
                   {"path": "docs/a.md", "bytes": 3, "title": "A", "dir": "docs"}],
         "skipped": [{"path": "x.png", "reason": "not-text"}]}
    m.update(over)
    return {"manifest": m, "files": {"README.md": b"# T\n", "docs/a.md": b"# A\n"}}


def test_write_snapshot_lands_under_frontend_public(tmp_path):
    materialize.write_snapshot(tmp_path, _payload())
    assert (tmp_path / "frontend/public/source/manifest.json").is_file()
    assert (tmp_path / "frontend/public/source/files/README.md").read_bytes() == b"# T\n"
    assert (tmp_path / "frontend/public/source/files/docs/a.md").read_bytes() == b"# A\n"


def test_manifest_written_as_utf8_lf(tmp_path):
    materialize.write_snapshot(tmp_path, _payload())
    raw = (tmp_path / "frontend/public/source/manifest.json").read_bytes()
    assert b"\r" not in raw, "换行必须是 LF"
    assert json.loads(raw.decode("utf-8"))["schema"] == "zc-source/1"
    assert "\\u" not in raw.decode("utf-8"), "中文不该被转义成 \\uXXXX"


def test_unsafe_remote_path_is_rejected_not_written(tmp_path):
    for bad in ("../escape.md", "/abs.md", "a\\b.md"):
        with pytest.raises(materialize.SnapshotError):
            materialize.write_snapshot(tmp_path, {"manifest": _payload()["manifest"],
                                                  "files": {bad: b"x"}})
        assert not (tmp_path / "frontend" / "public" / "source" / "files" / bad).exists()


def test_path_traversal_cannot_escape_snapshot_dir(tmp_path):
    """`docs/../oops.md` 归一后仍在快照目录内，但归一前的形态必须被拒。"""
    with pytest.raises(materialize.SnapshotError):
        materialize.write_snapshot(tmp_path, {"manifest": _payload()["manifest"],
                                              "files": {"docs/../../escape.md": b"x"}})


def test_readme_gets_provenance_section_and_keeps_template_text(tmp_path):
    materialize.write_snapshot(tmp_path, _payload())
    materialize.append_readme_provenance(tmp_path, _payload()["manifest"])
    text = (tmp_path / "README.md").read_text(encoding="utf-8")
    assert "模板正文" in text, "溯源段把模板 README 抹了"
    assert "https://gitee.com/leerys/zerocode" in text
    assert "frontend/public/source/" in text
    assert "manifest.json" in text


def test_compose_with_source_renders_then_writes(tmp_path):
    from app.deploy import scaffold
    tpl = _fake_template(tmp_path)
    root = tmp_path / "app"
    payload = _payload()
    fn = materialize.compose_with_source(
        lambda r, m: scaffold.render_layers([tpl], r, m, data_track=False), payload)
    fn(root, {"SLUG": "s1", "APP_NAME": "名字", "DEV_PORT": "5173", "PROD_PORT": "8080",
              "DEV_DB": "s1_dev", "PROD_DB": "s1_prod", "TODAY": "20261117"})
    assert json.loads((root / "frontend/package.json").read_text(encoding="utf-8"))["name"] == "s1-frontend"
    assert (root / "frontend/public/source/files/docs/a.md").is_file()
    readme = (root / "README.md").read_text(encoding="utf-8")
    assert "名字" in readme and "manifest.json" in readme


def test_snapshot_body_with_placeholder_bytes_does_not_break_creation(tmp_path):
    """远端正文里带 `{{APP_NAME}}` 是常事（它本身就是模板仓库）：残留自检跑在渲染期，
    快照在渲染之后落盘，所以既不该被替换、也不该把创建打成 500。"""
    from app.deploy import scaffold
    tpl = _fake_template(tmp_path)
    payload = _payload()
    payload["files"]["docs/a.md"] = "{{APP_NAME}} 和 {{SLUG}} 原样留着".encode("utf-8")
    payload["manifest"]["files"][1]["bytes"] = len(payload["files"]["docs/a.md"])
    root = tmp_path / "app"
    fn = materialize.compose_with_source(
        lambda r, m: scaffold.render_layers([tpl], r, m, data_track=False), payload)
    fn(root, {"SLUG": "s1", "APP_NAME": "名字", "DEV_PORT": "5173", "PROD_PORT": "8080",
              "DEV_DB": "s1_dev", "PROD_DB": "s1_prod", "TODAY": "20261117"})
    body = (root / "frontend/public/source/files/docs/a.md").read_text(encoding="utf-8")
    assert "{{APP_NAME}}" in body
    assert "名字" in (root / "frontend/src/App.vue").read_text(encoding="utf-8")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_source_materialize.py -q`
Expected: FAIL —— `ImportError` / `ModuleNotFoundError: No module named 'app.source.materialize'`

- [ ] **Step 3: 写落盘实现**

`app/source/materialize.py`：

```python
# app/source/materialize.py
"""把内存快照落成工作区文件：模板照常渲染，快照落在 frontend/public/source/。

为什么落点是 frontend/public/source/：Vite 把 `public/` 原样拷进 `dist`，dev 预览与发布后的
nginx 静态站点都能按 `/source/**` 直出（static 栈的 try_files 回落只兜真实文件不存在的情况）。
于是「站点读自己的快照、按 manifest 往下钻取」既不用后端、也不用 router、不用改 nginx 配置。
先例是 app/templates/vue3-springboot/frontend/public/。

接缝形状抄 app/store/install.py 的 _materializer：铺源逻辑换掉，端口/app.json/回滚仍归
create_app_project 一处，别在这儿另起一套。

README 是追加不是覆盖：模板那份写着本栈能力边界（static 的「没有后端、没有数据库」），
抹掉它等于把 AI 的起步说明书删了。
"""
from __future__ import annotations

import json
import posixpath
from pathlib import Path
from typing import Callable

SOURCE_ROOT_PARTS = ("frontend", "public", "source")
MANIFEST_REL = "/".join(SOURCE_ROOT_PARTS + ("manifest.json",))
FILES_REL_PREFIX = "/".join(SOURCE_ROOT_PARTS + ("files",)) + "/"

_PROVENANCE = """

## 仓库源码快照（{source_url}）

- 来源：{host}/{owner}/{repo}@{ref}{subpath_note}　抓取时间：{fetched_at}
- 树里 {listed} 条，进快照 {snapshotted} 篇文本，跳过 {skipped} 条（清单见 manifest）
- 正文：`frontend/public/source/files/<仓库原路径>`　索引：`frontend/public/source/manifest.json`
- 发布后按 `/source/manifest.json` 与 `/source/files/<路径>` 直接可取，页面自己 fetch 就能做钻取
- 这是**构建期快照**，不是实时代理：仓库更新了要重建应用，页面里不要写「正在读 Gitee」
- 做官网/文档站请从这里取真正文，别自己编目录；标题与分组读 manifest 的 `files[].title/dir`
"""


class SnapshotError(Exception):
    """快照落盘失败；由 create_app_project 的回滚兜住，最终映射成 400。"""


def _safe_rel(path: str) -> str:
    """远端路径归一后必须仍在快照目录内：越界、绝对、反斜杠形态一律拒。

    远端树是不可信输入，`docs/../../x` 归一后会往上爬，绝对路径会直接指到别人家目录。
    """
    if not path or path.startswith("/") or "\\" in path:
        raise SnapshotError(f"快照路径形态非法: {path!r}")
    norm = posixpath.normpath(path)
    if norm.startswith("..") or norm == "." or posixpath.isabs(norm):
        raise SnapshotError(f"快照路径越界: {path!r}")
    return norm


def write_snapshot(root: Path, payload: dict) -> None:
    base = Path(root).joinpath(*SOURCE_ROOT_PARTS)
    files_dir = base / "files"
    for path in payload.get("files") or {}:
        rel = _safe_rel(path)
        target = files_dir / rel
        try:
            target.relative_to(files_dir)
        except ValueError:
            raise SnapshotError(f"快照路径越界: {path!r}") from None
    (base / "manifest.json").write_text(
        json.dumps(payload["manifest"], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8", newline="")
    for path, data in (payload.get("files") or {}).items():
        target = files_dir / _safe_rel(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def append_readme_provenance(root: Path, manifest: dict) -> None:
    readme = Path(root) / "README.md"
    section = _PROVENANCE.format(
        source_url=manifest["sourceUrl"], host=manifest["host"], owner=manifest["owner"],
        repo=manifest["repo"], ref=manifest["ref"],
        subpath_note=f"（子目录 {manifest['subpath']}）" if manifest.get("subpath") else "",
        fetched_at=manifest["fetchedAt"], listed=manifest["counts"]["listed"],
        snapshotted=manifest["counts"]["snapshotted"], skipped=manifest["counts"]["skipped"])
    if readme.is_file():
        original = readme.read_text(encoding="utf-8")
        readme.write_text(original.rstrip("\n") + "\n" + section, encoding="utf-8", newline="")
    else:
        readme.write_text(f"# 仓库源码快照\n{section}", encoding="utf-8", newline="")


def compose_with_source(base_render, payload: dict) -> Callable[[Path, dict], None]:
    """基础铺源（模板渲染或商店包复制）跑完后追加快照落盘与 README 溯源段。"""
    def materialize(root: Path, mapping: dict) -> None:
        base_render(root, mapping)
        write_snapshot(root, payload)
        append_readme_provenance(root, payload["manifest"])
    return materialize
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest tests/test_source_materialize.py -q`
Expected: PASS。`test_unsafe_remote_path_is_rejected_not_written` 若红，说明预检循环漏了一个形态；`test_snapshot_body_with_placeholder_bytes_does_not_break_creation` 若红，说明快照写在了 `render_layers` 之前（自检会把远端正文里的 `{{APP_NAME}}` 当残留打死）——顺序不能调。

- [ ] **Step 5: 提交**

```bash
git add app/source/materialize.py tests/test_source_materialize.py
git commit -m "feat(source): 快照落 frontend/public/source 并追加 README 溯源段"
```

---

### Task 5: `create_app_project` 增 `source` 参数（组合进既有钩子）

`create_app_project` 现在这样选铺源函数（`app/services/app_service.py:175-179`）：`render = materialize or (lambda ...render_layers...)`。本票在它后面加一层组合，使「模板/商店包 + 快照」两件事正交，`materialize` 参数语义不变（商店安装不受影响）。

**Files:**
- Modify: `app/services/app_service.py:11`（import）、`:145-155`（签名与 docstring）、`:175-179`（组合）
- Test: `tests/test_source_seam.py`

**Interfaces:**
- Consumes: Task 4 `source_materialize.compose_with_source`
- Produces: `create_app_project(..., source: dict | None = None)` 关键字参数

- [ ] **Step 1: 写接缝失败测试**

`tests/test_source_seam.py`：

```python
# tests/test_source_seam.py
"""create_app_project 的 source 参数：模板照常渲染 + 快照照常落盘，失败时零残留。

对照 tests/test_store_install_seam.py 的形状写：那条钉「materialize 生效」，
这条钉「source 与 materialize 正交」——两者同时给时，包铺完源还要有快照。
"""
import shutil

import pytest
from sqlalchemy import text

from app import config as app_config
from app.db import make_engine, make_session_factory
from app.deploy.errors import DeployError
from app.deploy.settings import REPO_ROOT
from app.services import app_service
from app.source import materialize as src_mat

pytestmark = pytest.mark.asyncio
SLUG = "zcsrcseam"
APP_DIR = REPO_ROOT / "apps" / SLUG


def _payload():
    m = {"schema": "zc-source/1", "host": "gitee", "owner": "o", "repo": "r", "ref": "master",
         "sourceUrl": "https://gitee.com/o/r", "fetchedAt": "2026-11-17T00:00:00+00:00",
         "subpath": None, "truncated": False,
         "counts": {"listed": 1, "snapshotted": 1, "skipped": 0},
         "files": [{"path": "a.md", "bytes": 2, "title": "A", "dir": ""}], "skipped": []}
    return {"manifest": m, "files": {"a.md": b"#A"}}


async def _wipe():
    eng = make_engine(app_config.test_database_url())
    sf = make_session_factory(eng)
    try:
        from app.deploy import provision
        for env in ("dev", "prod"):
            try:
                await provision.drop_env(sf, SLUG, env)
            except Exception:
                pass
        async with sf() as s:
            await s.execute(text("DELETE FROM app_project WHERE slug=:s"), {"s": SLUG})
            w = (await s.execute(text("SELECT id FROM workspace WHERE root_path LIKE :p"),
                                 {"p": f"%\\{SLUG}"})).scalars().all()
            for wid in w:
                await s.execute(text("DELETE FROM workspace WHERE id=:i"), {"i": wid})
            await s.commit()
        shutil.rmtree(APP_DIR, ignore_errors=True)
    finally:
        await eng.dispose()


@pytest.fixture(autouse=True)
async def _clean(test_engine):
    await _wipe()
    yield
    await _wipe()


async def test_static_app_gets_template_and_snapshot(test_engine):
    sf = make_session_factory(test_engine)
    app = await app_service.create_app_project(
        sf, name="快照应用", slug=SLUG, scale="small", stack="static",
        source=_payload(), origin="repo:gitee", owner_user_id=None)
    try:
        assert (APP_DIR / "frontend" / "package.json").is_file(), "模板没渲染"
        assert (APP_DIR / src_mat.MANIFEST_REL).is_file()
        assert (APP_DIR / "frontend/public/source/files/a.md").read_bytes() == b"#A"
        assert "https://gitee.com/o/r" in (APP_DIR / "README.md").read_text(encoding="utf-8")
        assert not (APP_DIR / "sqlchange").exists(), "static 不该被铺数据轨"
        meta = app_service.load_app_json(APP_DIR)
        assert meta["stack"] == "static" and meta["slug"] == SLUG
        async with sf() as s:
            got = (await s.execute(text("SELECT origin FROM app_project WHERE id=:i"),
                                   {"i": app["id"]})).scalar()
        assert got == "repo:gitee"
    finally:
        await _wipe()


async def test_source_composes_on_top_of_store_materialize(test_engine):
    """两者同时给：商店包铺完源还要有快照（正交，不是互斥）。"""
    sf = make_session_factory(test_engine)
    seen = {}

    def materialize(root, mapping):
        root.mkdir(parents=True, exist_ok=True)
        seen["called"] = True
        (root / "MARKER").write_text("包铺的", encoding="utf-8")

    app = await app_service.create_app_project(
        sf, name="正交应用", slug=SLUG, scale="small", stack="static",
        origin="store:7", materialize=materialize, source=_payload())
    try:
        assert seen.get("called") is True
        assert (APP_DIR / "MARKER").is_file()
        assert (APP_DIR / src_mat.MANIFEST_REL).is_file(), "source 被 materialize 顶掉了"
    finally:
        await _wipe()


async def test_snapshot_failure_rolls_back_and_writes_nothing(test_engine):
    sf = make_session_factory(test_engine)
    bad = _payload()
    bad["files"]["../../etc/x.md"] = b"boom"
    with pytest.raises(DeployError):
        await app_service.create_app_project(
            sf, name="坏快照", slug=SLUG, scale="small", stack="static", source=bad)
    assert not APP_DIR.exists(), "失败后留下了半个工作区"
    async with sf() as s:
        assert (await s.execute(text("SELECT count(*) FROM app_project WHERE slug=:s"),
                                {"s": SLUG})).scalar() == 0, "失败后留下了 app 行"


async def test_no_source_is_byte_identical_to_today(test_engine):
    """回归闸：不传 source 时行为不许变。"""
    sf = make_session_factory(test_engine)
    app = await app_service.create_app_project(
        sf, name="无快照", slug=SLUG, scale="small", stack="static")
    try:
        assert not (APP_DIR / "frontend" / "public").exists()
        assert "仓库源码快照" not in (APP_DIR / "README.md").read_text(encoding="utf-8")
    finally:
        await _wipe()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_source_seam.py -q`
Expected: FAIL —— `TypeError: create_app_project() got an unexpected keyword argument 'source'`

- [ ] **Step 3: 加参数并组合**

`app/services/app_service.py` 的 `:11` 改成同时导入快照铺源（放在既有 `from app.deploy import ...` 之后，避免与 `app.source` 的循环：`app/source/materialize.py` 只依赖 `app.deploy.scaffold`）：

```python
from app.source import materialize as source_materialize
```

`create_app_project` 签名增一个关键字参数（放在 `materialize` 之后）：

```python
async def create_app_project(session_factory, *, name: str, slug: str, scale: str,
                             stack: str, root: str | None = None,
                             origin: str | None = None, materialize=None,
                             source: dict | None = None,
                             owner_user_id: int | None = None) -> dict:
```

docstring 追加一行：

```python
    `source`：仓库快照载荷（app/source/ingest.py 的返回）。给出时**先**按 materialize（或栈模板）
    铺源，**再**把快照落到 frontend/public/source/ 并给 README 追加溯源段——顺序不能反，
    scaffold 的占位符残留自检跑在铺源里，快照正文里的 `{{APP_NAME}}` 不该被它判成残留。
```

把 `:175-179` 的铺源选择改成：

```python
        render = materialize or (
            lambda rp, mp: scaffold.render_layers(
                [TEMPLATES / layer for layer in template_layers_of(stack)], rp, mp,
                scaffold.has_data_track(stack)))
        if source is not None:
            render = source_materialize.compose_with_source(render, source)
        render(root_path, mapping)   # R-C 已保证模板目录存在
```

（原实现是 `materialize or (lambda ...)`，改动只是把默认 lambda 提成显式 `lambda rp, mp:` 并删掉外层括号调用；`render(root_path, mapping)` 保持在最后一行。）

- [ ] **Step 4: 跑接缝测试**

Run: `.venv/Scripts/python.exe -m pytest tests/test_source_seam.py tests/test_store_install_seam.py -q`
Expected: 全 PASS。`test_store_install_seam.py` 一起跑是因为 Step 3 动了它依赖的那三行。

- [ ] **Step 5: 跑建应用与模板回归**

Run: `.venv/Scripts/python.exe -m pytest tests/test_apps_api.py tests/test_scaffold.py tests/test_small_stacks_template.py tests/test_spa_static.py -q`
Expected: 全 PASS。

- [ ] **Step 6: 提交**

```bash
git add app/services/app_service.py tests/test_source_seam.py
git commit -m "feat(stack): create_app_project 接受仓库快照，与 materialize 正交组合"
```

---

### Task 6: `POST /api/apps` 的 `source` 字段

抓取发生在 `create_app_project` **之前**：抓取失败就 400 返回，此时端口未分配、目录未建、库未供给——零副作用是结构性的（`app/api/apps.py:105-113` 是薄层裁定，业务仍留在 service）。

**Files:**
- Modify: `app/api/apps.py:33-37`（`AppIn`）、`:105-113`（`create_app`）
- Test: `tests/test_apps_source_api.py`

**Interfaces:**
- Consumes: Task 3 `ingest.fetch_repo_snapshot` 与 `ingest.SourceError`（`SourceError` 定义在 `app/source/ingest.py`）、Task 5 的 `source=` / `origin=` 参数
- Produces: 请求体 `{"name","slug","scale","stack","source":{"url","subpath"?}}`；`origin` 落库值 `repo:gitee`

- [ ] **Step 1: 写端点失败测试**

`tests/test_apps_source_api.py`：

```python
# tests/test_apps_source_api.py
"""建应用带仓库地址：先抓快照再建应用，失败 400 且零副作用。

transport 经 app.state.source_http_transport 注入（照 app.state.skills_http_transport 的形状，
见 tests/test_skills_github_api.py:73），真网络一个都不碰。
"""
import shutil

import httpx
import pytest
from sqlalchemy import text

from app import config as app_config
from app.db import make_engine, make_session_factory
from app.deploy import provision
from app.deploy.settings import REPO_ROOT

pytestmark = pytest.mark.asyncio
SLUG = "zcesrcapi"
APP_DIR = REPO_ROOT / "apps" / SLUG
URL = "https://gitee.com/leerys/zerocode"


def _blob(p, size=20):
    return {"path": p, "mode": "100644", "type": "blob", "sha": "s" + p[:5], "size": size}


def _transport(tree, files, *, status=200, requests=None):
    async def handler(request):
        if requests is not None:
            requests.append(str(request.url))
        p = request.url.path
        if request.url.host != "gitee.com":
            return httpx.Response(404)
        if "/git/trees/" in p:
            return httpx.Response(status, json={"tree": tree, "truncated": False}
                                  if status == 200 else {"message": "no"})
        if p == "/api/v5/repos/leerys/zerocode":
            return httpx.Response(status, json={"default_branch": "master"}
                                  if status == 200 else {"message": "no"})
        if p.startswith("/leerys/zerocode/raw/"):
            fp = "/".join(p.split("/")[4:])
            return httpx.Response(200, content=files.get(fp, b"")) if fp in files \
                else httpx.Response(404)
        return httpx.Response(404)
    return httpx.MockTransport(handler)


TREE = [_blob("README.md"), _blob("docs/a.md")]
FILES = {"README.md": b"# ZeroCode\n", "docs/a.md": b"# A\n"}


async def _wipe():
    eng = make_engine(app_config.test_database_url())
    sf = make_session_factory(eng)
    try:
        for env in ("dev", "prod"):
            try:
                await provision.drop_env(sf, SLUG, env)
            except Exception:
                pass
        async with sf() as s:
            await s.execute(text("DELETE FROM app_project WHERE slug=:s"), {"s": SLUG})
            w = (await s.execute(text("SELECT id FROM workspace WHERE root_path LIKE :p"),
                                 {"p": f"%\\{SLUG}"})).scalars().all()
            for wid in w:
                await s.execute(text("DELETE FROM workspace WHERE id=:i"), {"i": wid})
            await s.commit()
        shutil.rmtree(APP_DIR, ignore_errors=True)
    finally:
        await eng.dispose()


@pytest.fixture(autouse=True)
async def _clean(test_engine):
    await _wipe()
    yield
    await _wipe()


def _body(**over):
    b = {"name": "官网摄入", "slug": SLUG, "scale": "small", "stack": "static",
         "source": {"url": URL}}
    b.update(over)
    return b


async def test_create_with_source_snapshots_into_workspace(client):
    client.app.state.source_http_transport = _transport(TREE, FILES)
    r = await client.post("/api/apps", json=_body())
    assert r.status_code == 201, r.text
    assert (APP_DIR / "frontend/public/source/manifest.json").is_file()
    assert (APP_DIR / "frontend/public/source/files/docs/a.md").read_bytes() == b"# A\n"
    async with make_session_factory(test_engine := client.app.state.session_factory)() as s:
        got = (await s.execute(text("SELECT origin FROM app_project WHERE slug=:s"),
                               {"s": SLUG})).scalar()
    assert got == "repo:gitee"


async def test_rate_limited_gitee_is_400_and_no_app_created(client):
    before = (await client.get("/api/apps")).json()
    client.app.state.source_http_transport = _transport(TREE, FILES, status=403)
    r = await client.post("/api/apps", json=_body())
    assert r.status_code == 400
    assert "配额" in r.json()["detail"]
    assert not APP_DIR.exists(), "摄入失败却建了工作区"
    after = (await client.get("/api/apps")).json()
    assert {a["slug"] for a in after} == {a["slug"] for a in before}


async def test_non_gitee_url_says_what_is_supported(client):
    client.app.state.source_http_transport = _transport(TREE, FILES)
    r = await client.post("/api/apps", json=_body(source={"url": "https://example.com/o/r"}))
    assert r.status_code == 400 and "gitee.com" in r.json()["detail"]


async def test_internal_url_rejected(client):
    client.app.state.source_http_transport = _transport(TREE, FILES)
    for bad in ("https://127.0.0.1/o/r", "https://localhost/o/r"):
        r = await client.post("/api/apps", json=_body(source={"url": bad}))
        assert r.status_code == 400, bad


async def test_empty_source_url_is_ignored(client):
    """空串当没填：不许把一个空格变成一次网络请求，也不许报错拦住正常创建。"""
    r = await client.post("/api/apps", json=_body(source={"url": "   "}))
    assert r.status_code == 201, r.text
    assert not (APP_DIR / "frontend" / "public").exists()


async def test_subpath_narrows_the_snapshot(client):
    client.app.state.source_http_transport = _transport(TREE, FILES)
    r = await client.post("/api/apps", json=_body(source={"url": URL, "subpath": "docs"}))
    assert r.status_code == 201, r.text
    import json
    m = json.loads((APP_DIR / "frontend/public/source/manifest.json").read_text(encoding="utf-8"))
    assert m["subpath"] == "docs"
    assert {f["path"] for f in m["files"]} == {"docs/a.md"}


async def test_explicit_empty_subpath_means_repo_root_not_scan_all(client):
    """三态不许在 HTTP 层被折叠（Task 3 在 ingest.py 里把这条写成契约）：
    `subpath=""` = 就要仓库根，缺省 = 全域扫描，URL 自带 /tree/<ref>/<subpath> = 钻那个目录。
    仓库根形态与全域恰好同产物，所以这里断言的是「参数原样传下去、没被 `or None` 折成缺省」。"""
    client.app.state.source_http_transport = _transport(TREE, FILES)
    r = await client.post("/api/apps", json=_body(source={"url": URL, "subpath": ""}))
    assert r.status_code == 201, r.text
    import json
    m = json.loads((APP_DIR / "frontend/public/source/manifest.json").read_text(encoding="utf-8"))
    assert m["subpath"] is None                     # 仓库根归一成 None（快照不是一条子目录）
    assert {f["path"] for f in m["files"]} == {"README.md", "docs/a.md"}


async def test_request_without_source_field_still_creates_normally(client):
    r = await client.post("/api/apps", json={"name": "无快照", "slug": SLUG,
                                             "scale": "small", "stack": "static"})
    assert r.status_code == 201, r.text
    assert not (APP_DIR / "frontend" / "public").exists()


async def test_source_on_unsupported_stack_reports_clearly(client):
    """large 栈带后端：快照照落，但 README 段要说清前端目录在哪——这里钉住不因栈被拦死。"""
    client.app.state.source_http_transport = _transport(TREE, FILES)
    r = await client.post("/api/apps", json=_body(stack="vue3-springboot", scale="large",
                                                  source={"url": URL}))
    assert r.status_code == 201, r.text
    assert (APP_DIR / "frontend/public/source/files/README.md").is_file()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_apps_source_api.py -q`
Expected: FAIL —— 带 `source` 的请求 422（`AppIn` 还没这个字段），或 201 但断言快照文件红。

- [ ] **Step 3: 加子模型与抓取**

`app/api/apps.py` 在 `class AppIn` 之前加：

```python
class SourceIn(BaseModel):
    url: str
    subpath: str | None = None
```

`AppIn` 追加一行：

```python
class AppIn(BaseModel):
    name: str
    slug: str
    scale: str
    stack: str
    source: SourceIn | None = None
```

文件顶部导入区加（与其它 `from app...` 同段）：

```python
from app.source import ingest as source_ingest
```

`create_app` 改成：

```python
@router.post("", status_code=201)
async def create_app(request: Request, body: AppIn):
    # 快照先抓后建：抓取失败时端口未分配、目录未建、库未供给，零副作用是顺序给的保证，
    # 不是靠回滚兜出来的（建应用一旦落库就要 teardown dev/prod，别让摄入替它背）。
    source = None
    origin = None
    raw_url = (body.source.url or "").strip() if body.source else ""
    if raw_url:
        try:
            source = await source_ingest.fetch_repo_snapshot(
                raw_url, subpath=body.source.subpath,
                transport=getattr(request.app.state, "source_http_transport", None))
        except source_ingest.SourceError as e:
            raise HTTPException(400, str(e))
        origin = f"repo:{source['manifest']['host']}"   # VARCHAR(60)：只放短标记，URL 全文在 manifest 里
    try:
        return await app_service.create_app_project(
            request.app.state.session_factory, name=body.name, slug=body.slug,
            scale=body.scale, stack=body.stack, source=source, origin=origin,
            owner_user_id=request.state.actor["id"])   # 扩展态落归属（Ticket 003）
    except DeployError as e:
        raise _http(e)
```

- [ ] **Step 4: 跑端点测试**

Run: `.venv/Scripts/python.exe -m pytest tests/test_apps_source_api.py -q`
Expected: PASS。`test_empty_source_url_is_ignored` 若红，是 Step 3 的 `raw_url` strip 判定写在了 `subpath` 之后；`test_rate_limited_gitee_is_400_and_no_app_created` 若红，是 `SourceError` 没被 `except` 接住而是冒成了 500。

- [ ] **Step 4b: 把三态契约钉在它的归属层（`tests/test_source_ingest.py`）**

Task 3 把三态写成契约注释（`app/source/ingest.py:136-139`：显式 `subpath=""` 是「就要仓库根」，URL 里的 `/tree/<ref>/<subpath>` 是「钻这个目录」，两者都没有才是全域，显式参数优先），但**一条测试都没落**——所以 Step 3 那句 `or None` 折叠能一路绿灯通过前几轮评审。补下面这一对，加在 `test_subpath_narrows_snapshot_scope` 之后；两条只差一个参数，产物却必须不同，这才叫把两态分开：

```python
async def test_explicit_empty_subpath_overrides_url_carried_scope():
    """显式 "" = 就要仓库根，必须盖掉 URL 带进来的 /tree/master/docs。"""
    out = await ingest.fetch_repo_snapshot(f"{URL}/tree/master/docs", subpath="",
                                           transport=_transport(TREE, FILES))
    assert out["manifest"]["subpath"] is None
    assert set(out["files"]) == {"README.md", "docs/user-manual.md"}


async def test_absent_subpath_falls_back_to_url_carried_scope():
    """不传 subpath 时才用 URL 里的目录——与上一条只差 `subpath=""`，产物必须不同。"""
    out = await ingest.fetch_repo_snapshot(f"{URL}/tree/master/docs",
                                           transport=_transport(TREE, FILES))
    assert out["manifest"]["subpath"] == "docs"
    assert set(out["files"]) == {"docs/user-manual.md"}
```

这两条与 Step 1 的 API 级 `test_explicit_empty_subpath_means_repo_root_not_scan_all` 一起构成闸门：前者钉语义，后者钉「HTTP 层不折叠」。任何一处退回 `or None`，至少一条会红。

- [ ] **Step 5: 跑建应用与技能回归**

Run: `.venv/Scripts/python.exe -m pytest tests/test_apps_api.py tests/test_apps_rename_delete.py tests/test_skills_api.py -q`
Expected: 全 PASS（`origin` 新增取值不许打断既有创建/列表/删除用例）。

- [ ] **Step 6: 提交**

```bash
git add app/api/apps.py tests/test_apps_source_api.py
git commit -m "feat(api): 建应用接受 Gitee 仓库地址，先抓快照再建应用、失败零副作用"
```

---

## Phase C — 前端入口与文档

### Task 7: 向导加「仓库地址」输入

新建应用向导是三步（`frontend/src/views/AppsView.vue:880` 的 `a-steps`），本票在第三步「技术栈」下面加一个可选输入，载荷组装抽成纯函数好单测（视图里塞 URL 判定逻辑是没法测的）。

**Files:**
- Create: `frontend/src/utils/appSource.js`
- Create: `frontend/src/utils/appSource.test.js`
- Modify: `frontend/src/views/AppsView.vue:67`（`form` 加字段）、`:204-214`（`submit`：调用点 + **成功后重置**）、第三步表单块（`:907-912` 技术栈那一项之后）
- Modify: `frontend/src/api/index.js:90`（注释补 `source` 形状）

**Interfaces:**
- Consumes: 后端 `POST /api/apps` 的 `source` 字段（Task 6）
- Produces: `sourcePayload(rawUrl, rawSubpath?) -> {url, subpath?} | undefined`

- [ ] **Step 1: 写 util 失败测试**

`frontend/src/utils/appSource.test.js`：

```js
// 向导载荷里的「仓库地址」段：空/空白 → 整个字段缺席（不许把空格变成一次网络请求）；
// 超长 → 拒（后端 URL 上限 1000，前端先拦一次，给用户一句能看懂的话）
import { describe, expect, it } from 'vitest'
import { MAX_SOURCE_URL, sourcePayload } from './appSource'

describe('sourcePayload', () => {
  it('空值不产出字段', () => {
    expect(sourcePayload('')).toBeUndefined()
    expect(sourcePayload('   ')).toBeUndefined()
    expect(sourcePayload(undefined)).toBeUndefined()
    expect(sourcePayload(null)).toBeUndefined()
  })

  it('纯地址带 url，已去首尾空白', () => {
    expect(sourcePayload('  https://gitee.com/o/r  ')).toEqual({
      url: 'https://gitee.com/o/r',
    })
  })

  it('子目录空串不落字段（后端把空串读成「仓库根」是有意的，缺省才是「全域扫描」）', () => {
    expect(sourcePayload('https://gitee.com/o/r', '')).toEqual({ url: 'https://gitee.com/o/r' })
    expect(sourcePayload('https://gitee.com/o/r', '  docs/adr ')).toEqual({
      url: 'https://gitee.com/o/r', subpath: 'docs/adr',
    })
  })

  it('超长直接报错而不是发出去吃 400', () => {
    expect(() => sourcePayload('https://gitee.com/o/' + 'r'.repeat(MAX_SOURCE_URL)))
      .toThrow(/上限/)
  })
})
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npx vitest run src/utils/appSource.test.js`
Expected: FAIL —— 模块不存在

- [ ] **Step 3: 写 util**

`frontend/src/utils/appSource.js`：

```js
// 新建应用向导的「仓库地址」载荷组装。判据留在前端一次只为把话说明白，
// 真拦在后端（app/source/ingest.py 的 validate_http_url）。
export const MAX_SOURCE_URL = 1000   // 与后端 guard.MAX_URL_LEN 同值

export function sourcePayload(rawUrl, rawSubpath) {
  const url = String(rawUrl ?? '').trim()
  if (!url) return undefined
  if (url.length > MAX_SOURCE_URL) {
    throw new Error(`仓库地址超过 ${MAX_SOURCE_URL} 字符上限`)
  }
  const subpath = String(rawSubpath ?? '').trim()
  return subpath ? { url, subpath } : { url }
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd frontend && npx vitest run src/utils/appSource.test.js`
Expected: PASS（4 项）

- [ ] **Step 5: 接进向导视图**

`frontend/src/views/AppsView.vue`：

`:67` 的 `form` 加两个字段：

```js
const form = reactive({ name: '', slug: '', slugTouched: false, scale: 'small',
  stack: 'vue3-fastapi', sourceUrl: '', sourceSubpath: '' })
```

`:14` 附近的 util import 行加上（与既有 `appForm`、`publishableStacks` 同段）：

```js
import { sourcePayload } from '../utils/appSource'
```

`:204-214` 的 `submit` 里，`createApp` 调用（`:208`）改成：

```js
    let source
    try {
      source = sourcePayload(form.sourceUrl, form.sourceSubpath)
    } catch (e) {
      message.error(e.message)
      return
    }
    await createApp({ name: form.name.trim(), slug: form.slug, scale: form.scale,
                      stack: form.stack, source })
```

**同函数 `:211` 的成功重置必须一起改**——它现在是 `Object.assign(form, { name: '', slug: '', slugTouched: false, scale: 'small', stack: 'vue3-fastapi' })`，不加这两个字段的话，第二次创建会静默带上上一个仓库地址（用户看不见，却真的又抓了一次远程）：

```js
    Object.assign(form, { name: '', slug: '', slugTouched: false, scale: 'small',
      stack: 'vue3-fastapi', sourceUrl: '', sourceSubpath: '' })
```

第三步表单块（`:907` 的 `a-form-item label="技术栈"` 之后、`:912` 的 `</a-form>` 之前）插入：

```vue
          <a-form-item label="仓库地址"
                       help="可选：Gitee 公开仓库地址，创建时把仓库文本内容烘成快照，站点可从 /source/ 读">
            <a-input v-model:value="form.sourceUrl" allow-clear
                     placeholder="https://gitee.com/owner/repo 或 …/tree/master/docs" />
          </a-form-item>
          <a-form-item label="子目录" help="可选：只收这个目录下的正文，留空则全域扫描">
            <a-input v-model:value="form.sourceSubpath" allow-clear placeholder="如 docs" />
          </a-form-item>
```

`frontend/src/api/index.js:90` 的注释同步成：

```js
export const createApp = (data) => http.post('/apps', data) // {name, slug, scale, stack, source?:{url, subpath?}}
```

- [ ] **Step 6: 跑前端相关回归**

Run: `cd frontend && npx vitest run src/utils/appSource.test.js src/utils/appForm.test.js`
Expected: PASS（`appForm.js` 是栈/规模选项，`source` 不该影响它）。

- [ ] **Step 7: 提交**

```bash
git add frontend/src/utils/appSource.js frontend/src/utils/appSource.test.js frontend/src/views/AppsView.vue frontend/src/api/index.js
git commit -m "feat(apps): 新建向导第三步加 Gitee 仓库地址与子目录（可选）"
```

---

### Task 8: 规格、ADR 与术语

**Files:**
- Create: `docs/specs/2026-11-17-repo-source-ingest.md`
- Create: `docs/adr/0051-repo-source-ingest-boundary.md`
- Modify: `CONTEXT.md`（术语段，参照 `:134-135` 的技能来源表述位置）

- [ ] **Step 1: 写规格文件**

`docs/specs/2026-11-17-repo-source-ingest.md` 的内容直接取自本文 §1 的缺口表 + 下面这些必须落笔的验收条款（照仓库既有 spec 的章节写法：`## 1. 背景` `## 3. 用户故事` `## 5. 接口` `## 7. 边界`）：

```markdown
# 仓库源码摄入（Gitee URL → 应用内容快照）

**状态**：Accepted（随 2026-11-17 实施计划交付）
**计划**：docs/superpowers/plans/2026-11-17-repo-source-insert.md 见 §1 缺口分析

## 1. 背景
用户粘一个公开 Gitee 仓库地址，期望平台读它、往下钻取、把内容烘成应用素材。
实测四条通道（HTML 页 / API 元数据 / 递归文件树 / raw 正文）全部 200，
匿名 API 配额 60 次/小时；产品侧四段缺口见实施计划 §1。

## 3. 用户故事
1. 建应用时填 Gitee 仓库地址 → 应用工作区里有 `frontend/public/source/`，
   含 `manifest.json` 与逐篇正文；README 多一段溯源说明。
2. 填子目录 → 只收该目录下的正文，manifest.counts 仍报全树条目数。
3. 抓取失败（限流 / 私有 / 分支不存在 / 非支持站点）→ 400 一句中文，
   **不创建应用、不分配端口、不建目录**。
4. 发布后打开站点 → `/source/manifest.json` 可 fetch，页面据此列目录并逐篇取正文。

## 5. 接口
`POST /api/apps` body 增可选 `source: {url, subpath?}`；`url` 上限 1000 字符；
`origin` 落库为 `repo:gitee`（VARCHAR(60)，URL 全文只留在 manifest）。
载荷契约 `zc-source/1` 字段清单见计划 Task 3。

## 7. 边界与安全
- 判据唯一真值：`app/source/guard.py`（SSRF 网段判定 + URL 形态 + 流式限量 + 逐跳重定向预检），
  技能安装与快照摄入共用；不做 DNS 解析校验（沿用 001/002 裁定的单机部署假定）。
- 只收公开仓库；私有仓库需要 token，本票不做（ADR-0051）。
- 预算：单文件 200 KB（对齐 agent read_file 上限）、总量 8 MB、文件数 400、树响应 8 MB。
- 白名单：`.md .mdx .txt .rst .json .yaml .yml .csv .toml .ini`；
  跳 lockfile、跳 `workspace_ignore.IGNORE_DIRS` 命中的目录、跳非白名单扩展名。
- 远端路径落盘前二次归一（`_safe_rel`），越界/绝对/反斜杠形态一律拒。
- 快照是**构建期烘入**，不是运行时代理：页面里不得声称「正在读取 Gitee」。
```

（文件名以 `2026-11-17` 起头，与 `docs/specs/` 既有 20 份同族；上面计划路径笔误要在落笔时按实际文件名改正——`.../plans/2026-11-17-repo-source-ingest.md`。）

- [ ] **Step 2: 写 ADR-0051**

`docs/adr/0051-repo-source-ingest-boundary.md`：

```markdown
# ADR-0051：仓库源码摄入的边界——构建期烘快照，不开放运行时出网

**状态**：Accepted
**日期**：2026-11-17
**取代/关联**：与 docs/specs/2026-09-26-user-skills.md §7「明确不做 Gitee/GitLab 仓库扫描」不冲突（那条管技能通道）；
关联 ADR-0037/0038（应用身份由服务端解析）、ADR-0047（vue3-node 栈选型）

## 背景
用户要「给个 Gitee URL 就生成官网，站点能读该 URL 内容并往下钻取」。
平台已有可复用的抓取范式（app/skills/installer.py 的 trees?recursive=1 + 逐 blob 下载），
容器出网事实上没被禁（default + zcnet，无 internal/iptables；生成应用也不设 CSP），
所以真问题不是「通不通」，而是「把出网开在哪一层」。

## 决策
1. **只在构建期抓**：摄入发生在 `POST /api/apps` 落库之前，产物是工作区里的静态快照。
   运行时不代理 Gitee，agent 也不获得任意 URL 抓取工具。
2. **落点 `frontend/public/source/`**：借 Vite `public/` 的原样拷贝特性直出 `/source/**`，
   不改 vite.config、不改 nginx.conf、不给 static 栈加 router。
3. **走 `create_app_project` 既有 materialize 接缝**（商店安装同款），不新增第二条铺源路径。
4. **判据下沉为共享模块**（`app/source/guard.py`），技能通道改薄包装，文案与语义逐字不变。
5. **只接公开 Gitee 仓库**：GitHub/GitLab、私有仓 token、卡片来源角标、docs 站 SSR/sitemap/robots、
   自定义域名一律不在本票范围。

## 后果
- 快照会随仓库更新而变旧，产品口径要写清「重建应用才更新」，README 溯源段落是这条口径的载体。
- Gitee 匿名配额 60 次/小时：一次摄入只花 2 次 API 调用（默认分支 + 全树），正文走 raw 不吃配额；
  单机部署够用，多用户并发要加 token 时才需要另立一票。
- `build-output/` 与本票无关：那是平台交付物目录（scripts/build_output.py 重生成），
  不是应用产物，别把快照往里放。
- 技能安装仍是 GitHub-only（app/skills/installer.py:248 的 hostname 判定不动）：
  两条通道语义不同，合并会把技能的安全文案与候选清单协议一起拖进未知改动。
```

- [ ] **Step 3: 更新 CONTEXT.md 术语**

在 `CONTEXT.md` 的技能来源表述（`:134-135`）之后新增一段：

```markdown
## 仓库源码摄入（2026-11-17，ADR-0051）

- **托管源**：公开代码托管站的一个仓库坐标（owner/repo/ref[/subpath]），当前只接 Gitee。
- **仓库快照**：构建期抓下来的文本正文 + `manifest.json` 索引，落在工作区
  `frontend/public/source/`。术语说「快照」不说「镜像」——镜像留给 Docker 产物。
- **烘快照**：把快照随 `vite build` 原样拷进 `dist`、由静态站点按 `/source/**` 直出的动作。
- **钻取**：两级——服务端一次递归全树（不逐层点击），站点侧按 manifest 逐文件 fetch。
  浏览器里没有多页爬虫，所以「钻取」不指运行时抓取。
- 载荷契约名：`zc-source/1`（与 `app.json` 的 `zc-app/1` 同族命名）。
```

- [ ] **Step 4: 自检文档与代码口径一致**

逐条核对：ADR 里每条决策都能在 Task 1~6 的实现里找到对应文件；spec §7 的每个预算数字都能在 `app/source/ingest.py` 顶部找到同名常量；CONTEXT 的「钻取」两级定义与 `ingest._fetch_tree` + `manifest.json` 的实现一致。发现不一致以代码为准改文档。

- [ ] **Step 5: 提交**

```bash
git add docs/specs/2026-11-17-repo-source-ingest.md docs/adr/0051-repo-source-ingest-boundary.md CONTEXT.md
git commit -m "docs(source): 仓库摄入规格、ADR-0051 边界与 CONTEXT 术语"
```

---

## Phase D — 端到端验收（gated：等用户下令再跑）

### Task 9: 用真实 Gitee 仓库造一个可钻取的官网

这一步**会真的建应用、起 dev 预览、可能发布**，按仓库规矩等明确指示后执行。它的价值是把 Task 1~8 的单测串成一次用户能看见的事实：内容是真从 Gitee 来的，站点自己 fetch 得动。

**Files:**
- Create: `apps/<验收 slug>/`（平台建应用产物，gitignored 工作区，不进提交）
- 只读参考：`frontend/public/source/manifest.json`（验收应用内）

**Interfaces:**
- Consumes: Task 6 的 `POST /api/apps` + `source`；Task 4 的落点
- Produces: 一条验收结论（写进 spec 的验收小节，Task 8 已留位）

- [ ] **Step 1: 确认前置**

```bash
curl -sS -o /dev/null -w "gitee=%{http_code}\n" "https://gitee.com/api/v5/repos/leerys/zerocode"
curl -sS -o /dev/null -w "tree=%{http_code}\n" "https://gitee.com/api/v5/repos/leerys/zerocode/git/trees/master?recursive=1"
```
Expected: 两个都 200。任一非 200 → 停手告诉用户托管站在变慢/被限，别改端口改地址绕（仓库规矩：预检失败先查原因）。

- [ ] **Step 2: 建应用（走 UI 或 API 二选一，UI 能顺手验 Task 7）**

用 UI 时：新建应用 → 名称「ZeroCode 官网验收」、标识 `zcofficial`、小型档、栈 `static`、
仓库地址 `https://gitee.com/leerys/zerocode/tree/master/docs`（先用 `docs` 收窄，验证快；全域作为第二轮）。

用 API 时：

```bash
curl -sS -X POST "http://127.0.0.1:8100/api/apps" \
  -H "Content-Type: application/json" \
  -d '{"name":"ZeroCode 官网验收","slug":"zcofficial","scale":"small","stack":"static","source":{"url":"https://gitee.com/leerys/zerocode","subpath":"docs"}}'
```
Expected: 201 且响应里 `slug` 为 `zcofficial`。

- [ ] **Step 3: 验工作区落盘**

```bash
python -c "
import json,pathlib
r=pathlib.Path('D:/project/zerocode/apps/zcofficial')
m=json.loads((r/'frontend/public/source/manifest.json').read_text(encoding='utf-8'))
print('ref',m['ref'],'counts',m['counts'],'subpath',m['subpath'])
print('sample',[f['path'] for f in m['files'][:5]])
print('readme_ok','仓库源码快照' in (r/'README.md').read_text(encoding='utf-8'))
print('appjson',json.loads((r/'app.json').read_text(encoding='utf-8'))['stack'])
"
```
Expected: `snapshotted > 0`、sample 里都是 `docs/...` 真路径、`readme_ok True`、`appjson static`。

- [ ] **Step 4: 起 dev 预览并验快照可取**

先按仓库口径起预览（UI「打开预览」或既有 `dev.bat` 流程），探活后用 `localhost` 而非 `127.0.0.1`：

```bash
curl -sS -o /dev/null -w "manifest=%{http_code}\n" "http://localhost:<DEV_PORT>/source/manifest.json"
curl -sS "http://localhost:<DEV_PORT>/source/files/user-manual.md" | head -3
```
Expected: 200 与真正文首行。404 → 十有八九是 `public/` 落点被写歪了（回查 Task 4 的 `SOURCE_ROOT_PARTS`），别改 nginx 去兜。

- [ ] **Step 5: 让 AI 做官网页面（这一步是产品演示，不是平台代码改动）**

在应用对话里说：「按 `frontend/public/source/manifest.json` 的目录做一个文档浏览页：左侧目录树按 `files[].dir` 分组、标题取 `files[].title`，点击右侧 fetch `/source/files/<path>` 渲染 markdown；首页放一段来自 README 的项目介绍」。
然后让 agent 用既有 `read_file` 读快照、`write_file` 写页面（本票没给它任何新工具，这里要的就是这个事实）。
预览里点两篇不同的文档，确认钻取真的换内容而不是同一篇缓存。

- [ ] **Step 6: 发布并验生产侧**

发布走应用卡片既有流程（含体检）。发布后：

```bash
curl -sSI "https://zcofficial.zerocode.com/source/manifest.json" | head -1
```
Expected: 200。这一步验的是「快照确实随 `vite build` 进了镜像」——`build-output` 不参与（那是平台交付目录）。
若走真机浏览器验证，按仓库规矩用外置 Chrome，不用内置 Browser。

- [ ] **Step 7: 把验收结论写回 spec 并提交**

在 `docs/specs/2026-11-17-repo-source-ingest.md` 的验收小节记：全域/收窄两种输入的 `counts`、耗时、
`truncated` 是否触发、`/source/**` 两个环境的实测码。不清理演示应用（用户口径「保留夹具」=别删演示数据）。

```bash
git add docs/specs/2026-11-17-repo-source-ingest.md
git commit -m "docs(source): 记录 Gitee 仓库快照的端到端验收结果"
```

---

## Self-Review 检查点（执行完每 Phase 回看一次）

1. **覆盖**：四段缺口 → Task 6（入口）、Task 1~3（抓取与钻取）、Task 5（进工作区）、Task 4+9（站点读得到）。缺一个都不算完。
2. **无占位**：全文无「适当处理/后续补」；`<DEV_PORT>` 与 `apps/zcofficial` 是 Task 9 Step 2 现场产出的值，不是留白。
3. **契约一致**：`fetch_repo_snapshot` 返回的 `manifest` 字段名 ↔ Task 4 的 `_PROVENANCE` 取键 ↔ Task 9 Step 3 打印的键；`SOURCE_ROOT_PARTS` ↔ `MANIFEST_REL` ↔ 测试里的 `frontend/public/source/...` 路径；`guard.fetch_limited` 参数名 ↔ installer 包装传的关键字。
4. **回归面**：`test_skills_api.py`、`test_skills_github_api.py`、`test_store_install_seam.py`、`test_apps_api.py`、`test_agent_registry.py`（未改工具面，应当零改动通过）。
5. **不做**：运行时代理、agent 出网工具、私有仓 token、GitHub 通道、DDL 迁移——谁在评审里提出来，答案是 ADR-0051。
