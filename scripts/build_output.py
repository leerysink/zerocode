# scripts/build_output.py
"""build-output 打包器（ADR-0013 同路径挂载 / ADR-0014 init.sql 由 changelog 合成 / ADR-0018 网关入包）。

一次调用产出可上服务器的完整交付目录：后端源码 + Dockerfile、前端 dist、合成的 init.sql、
docker-compose.yml、.env.example、部署说明、中央网关配置（`gateway/`），以及预置应用源码
与其元数据种子行。

三条硬约束（都在打包期 fail-closed，不留到演示现场才发现）：
1. **重放安全**——init.sql 每次发布都要在存量库上重跑，故 changelog 里一旦出现无 ON CONFLICT 的
   INSERT 或任何 DELETE/TRUNCATE/DROP TABLE，直接拒绝出包（ADR-0014「只补不覆盖」）。
2. **凭据不出包**——预置应用只带源码，`deploy/`（内含 .env 明文口令）一律剔除；网关只带配置，
   `certs/`（TLS 私钥）与现场生成的 `routes.json` 一律剔除；出包前再扫一遍 .env 兜底。
3. **端口不撞号**——`app_project.prod_port` / `dev_port` 都是 UNIQUE 列，撞号会让种子 INSERT 当场失败。

本脚本刻意不 import app.*：它要在任何一台装了 Python 的机器上跑（打包机不一定是开发机），
且 app/db.py 那套朴素语句切分的前提（changelog 内禁止 $$ 与含分号的字符串字面量）在这里同样成立。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# 默认只打平台，不带预置应用：apps/ 下绝大多数目录是开发期试出来的残留，打进演示包既没意义
# 又拖慢出包。要预置就显式点名：build-output.bat --slugs test1,sqlc002
DEFAULT_SLUGS = ()
DEFAULT_DEPLOY_ROOT = "/opt/zerocode"
# init.sql 种子段里部署根的占位符：打包期不知道部署根最终是什么（ADR-0034），
# 由 apply-init.sh 在执行前按 .env 的 DEPLOY_ROOT 渲染。两侧字面量在 tests 里各自钉死。
DEPLOY_ROOT_PLACEHOLDER = "{{DEPLOY_ROOT}}"
DEFAULT_OUT_NAME = "build-output"

# 网关域名的默认值必须与 app/deploy/nics.py 里 os.environ.get 的回退值、以及
# gateway/docker-compose.yml 的 ${ZEROCODE_GATEWAY_DOMAIN:-…} **逐字相同**（tests 里有 canary
# 同时钉这三处）。漂开之后的症状是「只改对了一半」：不带参数出的包写的域名，与容器读不到
# env 时的回退值不同，两边各自都「看着对」，而没有任何一处会去核对另一处。
DEFAULT_GATEWAY_DOMAIN = "zerocode.com"

# 打包机环境变量：给「下次我还要这么搞」用——setx 一次，之后每次发布自动带上，不必记参数。
# 其中网关域名、PyPI、npm 这三个名字**同时是容器内的变量名**：打包值经 compose 的
# `${VAR:-打包值}` 烘成默认，运维在服务器 .env 里改一次就永久生效，平台容器里的
# app/deploy/compose.py 再把同一个值转注进应用 dev 栈——一次命名贯通三段。
# apt 只作用于镜像构建期（Dockerfile 里换源），没有运行期消费方，故不进容器 env。
GATEWAY_DOMAIN_ENV = "ZEROCODE_GATEWAY_DOMAIN"
# 网关连应用 prod 宿主端口的上游地址。仓库里的 gateway/docker-compose.yml 默认
# host.docker.internal（本机 Docker Desktop 的内嵌 DNS 认识它），但**出包目标恒为 Linux
# 服务器**，那里这个别名只写在 /etc/hosts，而 nginx 的变量式 proxy_pass 只查 resolver、
# 不读 hosts → 必然 NXDOMAIN，症状是所有应用域名 502。故打包时烘成 docker0 默认网关的
# IP 字面量；宿主自定义过 docker0 时用 gateway/.env 覆盖。
GATEWAY_UPSTREAM_ENV = "ZEROCODE_GATEWAY_UPSTREAM"
GATEWAY_UPSTREAM_BAKED = "172.17.0.1"
APT_MIRROR_ENV = "ZEROCODE_APT_MIRROR"
PYPI_MIRROR_ENV = "ZEROCODE_PYPI_MIRROR"
NPM_REGISTRY_ENV = "ZEROCODE_NPM_REGISTRY"
# 预览端口的两个宿主地址（消费方 app/deploy/settings.py）。这里烘的探测默认值与代码回退值
# **刻意不同**：代码回退 127.0.0.1 服务的是「平台跑在宿主进程」的形态，而本包里的平台永远在容器里，
# 应用端口发布在宿主上，127.0.0.1 那一侧没有监听——健康探针会等满整个超时窗口再报「未就绪」。
PREVIEW_PROBE_HOST_ENV = "ZEROCODE_PREVIEW_PROBE_HOST"
PREVIEW_HOST_ENV = "ZEROCODE_PREVIEW_HOST"
PREVIEW_PROBE_HOST_BAKED = "host.docker.internal"
PREVIEW_HOST_BAKED = "localhost"
# SSL 开关（消费方 app/deploy/nics.py）：true=应用地址走网关通配域名；显式 false/0/no/off
# 时改投影成 http://<PREVIEW_HOST>:<prod 端口>，用于域名未备案、443 被拦期间。
# 它只改地址展示，不停网关、不碰证书——开着网关不用没有任何代价。
SSL_ENABLE_ENV = "ZEROCODE_SSL_ENABLE"
SSL_ENABLE_BAKED = "true"
# 浏览器 sidecar（ADR-0031）：backend 在 compose 网络里按服务名访问它；sidecar 自己再经
# host-gateway 访问宿主上发布的应用 dev 端口。两个地址都留成运行期可覆盖位：把 sidecar
# 拆到另一台机器、或应用端口发布在别的宿主上时，服务器 .env 改一次即可。
BROWSER_URL_ENV = "ZEROCODE_BROWSER_URL"
BROWSER_URL_BAKED = "http://browser:8101"
BROWSER_TARGET_HOST_ENV = "ZEROCODE_BROWSER_TARGET_HOST"
BROWSER_TARGET_HOST_BAKED = "host.docker.internal"
# sidecar 的目标白名单（服务端读取）。改了上面的目标宿主就必须同步改这里，否则每次检查
# 都会被 sidecar 以「host 不在白名单」拒绝——后端只会看到一句 unavailable。
BROWSER_ALLOWED_HOSTS_ENV = "BROWSER_ALLOWED_HOSTS"
BROWSER_ALLOWED_HOSTS_BAKED = "host.docker.internal,127.0.0.1,localhost"
# 可交互会话的资源闸（ADR-0037）：活页面留在 sidecar 进程内存里，没有上限与空闲回收就会在
# mem_limit 1g 的容器里攒僵尸 context 直到 OOM。必须在交付包里显式写出来，而不靠代码默认值——
# 默认值漂了没有任何地方看得见，而这两个数就是运维要看得见的那两个数。
BROWSER_MAX_SESSIONS_ENV = "ZEROCODE_BROWSER_MAX_SESSIONS"
BROWSER_MAX_SESSIONS_BAKED = "2"
BROWSER_SESSION_TTL_ENV = "ZEROCODE_BROWSER_SESSION_TTL_SECONDS"
BROWSER_SESSION_TTL_BAKED = "120"
# Chromium 在**镜像构建期**下载；只进 browser 服务的 build args，不进容器运行期 env。
PLAYWRIGHT_DOWNLOAD_HOST_ENV = "PLAYWRIGHT_DOWNLOAD_HOST"
PLAYWRIGHT_DOWNLOAD_HOST_BAKED = "https://cdn.npmmirror.com/binaries/playwright"

# 预置应用复制时的剔除名单：deploy/ 是凭据禁区（compose.py 的 STACK_SECRETS 就落在那里），
# 其余都是可再生的依赖/产物目录——带上它们只会让包体膨胀几十倍且把本机路径带过去。
#
# 这些名字在**任意深度**都会被剔（copytree 的 ignore 回调对每层目录都调一次），这是刻意的：
#   - `node_modules` / `__pycache__` / `.git` / `target` / `dist` 在任何层级出现都是噪音
#     （多模块 maven 工程每层都有 target，monorepo 每层都可能有 node_modules）；
#   - `deploy` 宁可剔错也不剔漏：漏剔一个深层 deploy/.env 就是把凭据发到服务器，而误剔
#     一个同名源码目录至多是应用跑不起来、在演示前就能发现。两类代价不对等。
# 代价要写在这里：若将来某个预置应用在深层放了名为 deploy/dist/target 的**合法源码**目录，
# 它会被静默剔掉。真遇到时应当改应用侧的目录命名，而不是把这里的剔除改成只限根层。
# copy_preset_app 拷完还会用 rglob(".env") 做一次全深度兜底（见出包终检）。
EXCLUDE_DIRS = frozenset({
    "deploy", "node_modules", "fe_node_modules", ".venv", "venv", "__pycache__",
    "uploads", "dist", "target", ".git", ".pytest_cache", ".idea", ".vscode", ".m2",
})
EXCLUDE_SUFFIXES = frozenset({".pyc", ".pyo", ".log"})

# 平台代码复制时的剔除名单（app/ 下的运行时缓存与本地试验目录）。
# 注意它**不含** deploy：app/deploy/ 是平台自己的部署模块（engine/compose/provision/settings），
# 必须进镜像；与预置应用的 deploy/（凭据目录）同名但完全不是一回事。
BACKEND_EXCLUDE_DIRS = frozenset({"__pycache__", ".pytest_cache", "uploads"})
BROWSER_EXCLUDE_DIRS = frozenset({"__pycache__", ".pytest_cache"})

# 网关目录的剔除名单（ADR-0018）：
#   - certs/ 里是 TLS 私钥（gateway.key）。它一旦随包发出去，任何拿到包的人都能冒充整个
#     *.域名的 HTTPS；服务器上的证书必须现场签发（acme.sh / make-cert），不能从开发机搬。
#   - routes/ 是运行期投影，开发机上可能残留着真实应用端口。出包时重置为空对象 {}，
#     避免把「开发机上恰好 running 的应用」当成服务器事实。
#   - .env 是本机覆盖值（域名/上游），打包时域名已经烘进 compose 的默认值，不需要带上它；
#     带上还会撞上出包终检的 .env 扫描。
GATEWAY_EXCLUDE_DIRS = frozenset({"certs", "routes"})
GATEWAY_EXCLUDE_FILES = frozenset({".env"})

# 网关入包必须齐全的文件：缺任何一个都意味着一份起不来的网关配置，宁可拒包。
GATEWAY_REQUIRED = ("docker-compose.yml", "nginx.conf", "lua/router.lua", "404.html",
                    "make-cert.sh", "make-cert.ps1")

# 浏览器 sidecar 入包必须齐全的文件：compose 会在服务器上按这个目录 build 镜像，
# 少任何一个都以「镜像构建失败」的形式推迟到部署时才炸，所以打包期直接拒。
# 名单必须盖住 browser/ 下每一个 .py 源文件（tests/test_build_output.py 遍历目录钉这条）：
# `browser/Dockerfile` 按文件名逐个 COPY，改名或删一个源文件的表现是容器起来即崩、
# preview_page 与 browser_step 恒回「浏览器检查服务不可用」，离根因很远。
BROWSER_REQUIRED = ("Dockerfile", "requirements.txt", "app.py", "inspect_core.py",
                    "session_core.py", "sessions.py", "__init__.py")

# 网关 compose 里两个默认值的形态（与平台 compose 的 ${VAR:-打包值} 同一副面孔）：
# 域名 fallback 换成实际域名，上游 fallback 换成 Linux 可用的 IP。
# 各自出现 0 次或多次都拒包——前者说明源文件改了形状、替换会静默不生效，后者说明判定域有歧义。
_GATEWAY_DOMAIN_TOKEN_RE = re.compile(r"\$\{" + GATEWAY_DOMAIN_ENV + r":-[^}]*\}")
_GATEWAY_UPSTREAM_TOKEN_RE = re.compile(r"\$\{" + GATEWAY_UPSTREAM_ENV + r":-[^}]*\}")



class BuildError(RuntimeError):
    """打包期可预期失败：一律中文、带可执行的下一步，由 main() 收口成退出码 1。"""


@dataclass(frozen=True)
class Preset:
    """一个预置应用的全部事实，来源只有 apps/<slug>/app.json（不连数据库）。"""
    slug: str
    name: str
    scale: str
    stack: str
    prod_port: int
    dev_port: int
    src: Path


@dataclass(frozen=True)
class Mirrors:
    """依赖镜像源三元组。三个字段各自独立可空是刻意的：只给其中一个，就只注入那一个。

    None 的语义是「不注入」——产物里连对应的行都不出现，于是什么都不传的打包与加这个
    功能之前**逐字一致**（这是本参数组的默认承诺）。

    三个字段的形态不同也是刻意的：apt 存裸主机名（源行要同时覆盖 http/https 两种写法与
    deb822 形态，替换的只有主机名）；pypi / npm 存完整 URL（它们要被写成容器里的 ENV，
    由 pip/npm 自己解析，`/pypi/simple` 这类路径是地址的一部分，丢掉就等于指到镜像站根目录）。
    """
    apt: str | None = None
    pypi: str | None = None
    npm: str | None = None


DEFAULT_MIRRORS = Mirrors()


# ---------------------------------------------------------------- 可选打包参数：三级取值与校验

# DNS 标签：首尾必须是字母或数字，中间可有连字符。下划线不在表内（DNS 不认它），
# 非 ASCII 也不在表内——nics.py 与网关那侧都不做 punycode 转换，这里转了反而两边不一致。
_DOMAIN_LABEL_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?")
_HOSTNAME_RE = re.compile(rf"{_DOMAIN_LABEL_RE.pattern}(?:\.{_DOMAIN_LABEL_RE.pattern})*")


def _setting(explicit: str | None, env_name: str) -> str | None:
    """三级取值的前两级：显式参数 > 打包机环境变量；返回 None 表示「这一路都没给」。

    两边的空值处置刻意不对称：显式传参是人的动作，`--gateway-domain` 后面漏了值必须拦下来
    （退回默认值等于把这次发布的地址全写错，而打包还报成功）；而 shell / CI 里 `set FOO=`
    与「没设」通行等价，为一个无关的空变量拦下一次正常打包是纯粹的伤害。
    """
    if explicit is not None:
        return explicit
    return (os.environ.get(env_name) or "").strip() or None


def _resolved_setting(explicit: str | None, env_name: str, normalize, default):
    """三级取值的完整链条：flag > 打包机 env > 默认。归一化只在「确实给了值」时发生。"""
    raw = _setting(explicit, env_name)
    return default if raw is None else normalize(raw)


def normalize_gateway_domain(raw: str) -> str:
    """网关域名规范化：去首尾空白、转小写，并按 DNS 主机名规则校验。

    这个值被拼成控制台里 `https://应用名.<域名>` 的地址，由 app/deploy/nics.py 在**容器内**
    读取。非法值留到那时的表现是「地址看着正常、点进去全 404」（ADR-0016），而日志里没有
    任何一行指向真因，所以必须在打包期就拒掉。

    四道闸门按「总长 → 标签数 → 逐标签字符 → 全数字」推进：
    - 总长 253 与单标签 63 是 DNS 硬上限，边界值本身要放过（63/253 过、64/254 拒）；
    - 至少两个标签：单标签（`localhost`、`com`）签不到泛解析证书，`应用名.单标签` 也拼不出可用的域名；
    - 逐标签字符：下划线、空格、`@`、`:`、`/`、`*`、`$`、反引号、引号都在这里挡掉，顺带也就
      挡住了 `http://…` 这类「把整条 URL 当域名填」的常见手滑；
    - 全数字单独拒（`1.2.3.4`）：拼出来是 `https://demo1.1.2.3.4`，浏览器按域名解析必然失败，
      而它每一段看着都「像」合法标签。`127.0.0.1.nip.io` 这类通配解析服务不受影响。
    """
    text = (raw or "").strip().lower()
    if not text:
        raise BuildError(f"网关域名不能为空：要默认值 {DEFAULT_GATEWAY_DOMAIN} 就别传这个参数，"
                         f"收到：{raw!r}")
    if len(text) > 253:
        raise BuildError(f"网关域名总长 {len(text)} 字符，超过 DNS 整名上限 253：{raw!r}")
    labels = text.split(".")
    if len(labels) < 2:
        raise BuildError(f"网关域名至少要有两个标签（填泛解析域名本体，形如 example.com），"
                         f"单标签签不到通配证书：{raw!r}")
    for label in labels:
        if not label:
            raise BuildError(f"网关域名里有空标签（连续两点，或首尾带了点）：{raw!r}")
        if len(label) > 63:
            raise BuildError(f"网关域名单个标签 {len(label)} 字符，超过 DNS 标签上限 63：{raw!r}")
        if not _DOMAIN_LABEL_RE.fullmatch(label):
            raise BuildError(f"网关域名的标签 {label!r} 非法：只允许字母、数字与居中的连字符，"
                             f"不带协议/端口/路径/通配符，也不支持中文与下划线：{raw!r}")
    if all(label.isdigit() for label in labels):
        raise BuildError(f"网关域名不能是 IP 形态（拼出来会是 https://应用名.{text}）：{raw!r}")
    return text


def normalize_apt_mirror(raw: str) -> str:
    """apt 镜像源规范化为**裸主机名**（`mirrors.example.com`），丢掉协议、路径与端口。

    只填主机名是刻意的：apt 的源行同时有 http 与 https 两种写法，Debian 12+ 还把 sources.list
    换成了 deb822 的 `debian.sources`，四处都沿用官方目录路径。镜像站保留的正是这套路径，
    所以要替换的只有主机名——把整条 URL 塞进去会在替换那一步对不上，或把路径拼重。
    """
    text = (raw or "").strip().lower()
    if not text:
        raise BuildError(f"apt 镜像源不能为空：要用官方源就别传这个参数，收到：{raw!r}")
    host = re.sub(r"^[a-z][a-z0-9+.-]*://", "", text).rstrip("/")
    if not _HOSTNAME_RE.fullmatch(host):
        raise BuildError(f"apt 镜像源必须是裸主机名（形如 mirrors.example.com，"
                         f"不带协议、路径、端口与空白）：{raw!r}")
    return host


def _normalize_mirror_url(raw: str, label: str) -> str:
    """pip / npm 的镜像地址规范化：必须带 http(s) 协议，去掉尾斜杠，保留端口与路径。

    与 apt 相反，这两个值要原样写进容器 ENV（`PIP_INDEX_URL` / `NPM_CONFIG_REGISTRY`），由
    pip 与 npm 自己按 URL 解析，所以这里必须是完整地址；查询串与锚点一律拒（带 `?token=`
    的地址会被原样烘进交付包，那玩意儿是凭据）。
    """
    text = (raw or "").strip().lower()
    if not text:
        raise BuildError(f"{label}不能为空：要用官方源就别传这个参数，收到：{raw!r}")
    m = re.match(r"^(https?)://([^/]+)(/[^?#\s]*)?$", text)
    if not m:
        raise BuildError(f"{label}必须是 http(s):// 开头的完整地址，不带查询串与空白"
                         f"（形如 https://mirrors.example.com/pypi/simple）：{raw!r}")
    host, _, port = m.group(2).partition(":")
    if not _HOSTNAME_RE.fullmatch(host):
        raise BuildError(f"{label}的主机名 {host!r} 非法（只允许字母、数字、点与连字符）：{raw!r}")
    if port and not re.fullmatch(r"\d{1,5}", port):
        raise BuildError(f"{label}的端口 {port!r} 非法：{raw!r}")
    return f"{m.group(1)}://{m.group(2)}{m.group(3) or ''}".rstrip("/")


def normalize_pypi_mirror(raw: str) -> str:
    return _normalize_mirror_url(raw, "PyPI 镜像地址")


def normalize_npm_registry(raw: str) -> str:
    return _normalize_mirror_url(raw, "npm 镜像源地址")


def normalize_playwright_download_host(raw: str) -> str:
    """Playwright 浏览器下载源：完整 http(s) 地址，规则与 pip/npm 镜像相同。"""
    return _normalize_mirror_url(raw, "Playwright 下载源")


def resolve_mirrors(apt_mirror: str | None = None, pypi_mirror: str | None = None,
                    npm_registry: str | None = None) -> Mirrors:
    """三个镜像源的三级取值（flag > 打包机 env > 不给）。不给就是 None，即「零注入」。"""
    return Mirrors(
        apt=_resolved_setting(apt_mirror, APT_MIRROR_ENV, normalize_apt_mirror, None),
        pypi=_resolved_setting(pypi_mirror, PYPI_MIRROR_ENV, normalize_pypi_mirror, None),
        npm=_resolved_setting(npm_registry, NPM_REGISTRY_ENV, normalize_npm_registry, None),
    )


# ---------------------------------------------------------------- 部署根与预置应用装载

def normalize_deploy_root(raw: str) -> str:
    """部署根规范化：必须是 Linux 绝对路径、posix 分隔、无尾斜杠。

    它同时是宿主路径与容器内路径（ADR-0013 的同路径挂载），所以 Windows 风格路径在这里就是错的——
    与其生成一份 root_path 全是 `D:\\...` 的 init.sql 让服务器上的应用集体失联，不如现在拦住。
    """
    text = (raw or "").replace("\\", "/").strip()
    if not text.startswith("/"):
        raise BuildError(f"部署根必须是 Linux 绝对路径（形如 /opt/zerocode），收到：{raw!r}")
    trimmed = re.sub(r"/{2,}", "/", "/" + text.strip("/"))     # 收敛重复斜杠，不把它带进 root_path
    if trimmed == "/":
        raise BuildError("部署根不能是文件系统根 /（会把整台机器的目录当应用根）")
    return trimmed


def load_presets(apps_root: Path, slugs) -> list[Preset]:
    """从 app.json 装载预置应用清单；任一应用不合规即整包失败（不做「跳过坏的继续打」）。

    跳过一个坏应用会产出「看起来成功、演示时少一个应用」的包——那比直接失败难查得多。
    """
    presets: list[Preset] = []
    for slug in slugs:
        src = apps_root / slug
        if not src.is_dir():
            raise BuildError(f"预置应用目录不存在：{src}")
        meta_file = src / "app.json"
        if not meta_file.is_file():
            raise BuildError(f"预置应用缺 app.json（无法生成元数据种子行）：{src}")
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise BuildError(f"app.json 不是合法 JSON：{src}\\{e}") from e
        if meta.get("slug") != slug:
            raise BuildError(f"app.json 的 slug={meta.get('slug')!r} 与目录名 {slug!r} 不一致：{src}")
        for key in ("name", "scale", "stack"):
            if not meta.get(key):
                raise BuildError(f"app.json 缺字段 {key!r}：{src}")
        ports = meta.get("ports") or {}
        for env in ("prod", "dev"):
            if not isinstance(ports.get(env), int) or ports[env] <= 0:
                raise BuildError(f"app.json 的 ports.{env} 不是正整数：{src}")
        # dev/prod 迁移与镜像构建都要这两个目录，缺了等于服务器上点预览必挂
        for need in ("backend", "frontend", "schema", "sqlchange"):
            if not (src / need).is_dir():
                raise BuildError(f"预置应用缺 {need}/ 目录（dev/prod 迁移与构建都要它）：{src}")
        presets.append(Preset(slug=slug, name=str(meta["name"]), scale=str(meta["scale"]),
                              stack=str(meta["stack"]), prod_port=int(ports["prod"]),
                              dev_port=int(ports["dev"]), src=src))
    return presets


def detect_port_conflicts(presets) -> list[str]:
    """端口撞号检测：app_project 的 prod_port / dev_port 各自 UNIQUE，同值种子必然失败。

    存量 app.json 里确实有重复端口（不同时期分配的残留），所以这不是理论防御。
    """
    problems: list[str] = []
    for field in ("prod_port", "dev_port"):
        seen: dict[int, str] = {}
        for p in presets:
            value = getattr(p, field)
            if value in seen:
                problems.append(f"{field}={value} 被 {seen[value]} 与 {p.slug} 同时占用")
            else:
                seen[value] = p.slug
    for p in presets:
        if p.prod_port == p.dev_port:
            problems.append(f"{p.slug} 的 prod 与 dev 端口相同（{p.prod_port}）")
    return problems


# ---------------------------------------------------------------- init.sql 合成

def _strip_sql_comments(sql: str) -> str:
    """剥掉 `--` 注释（整行与行尾），返回只剩可执行 SQL 的文本。

    审计与兜底检查都必须看剥过注释的文本：changelog 的注释里会合法地引用
    `ON CONFLICT (列…)` 这类片段来说明仲裁目标，在全文上跑正则会把它误判成残留。
    """
    lines = []
    for line in sql.splitlines():
        if line.strip().startswith("--"):
            continue
        lines.append(line.split("--", 1)[0])
    return "\n".join(lines)


def split_statements(sql: str) -> list[str]:
    """朴素语句切分，与 app/db.py::split_statements 同款：剥 `--` 注释后按 `;` 切。

    成立前提同样是 changelog 内禁止 `$$` 块与含分号的字符串字面量（各迁移文件头都写了这条）。
    只用于**审计**，不用于改写原文，故切分粒度粗一点无妨。
    """
    return [s.strip() for s in _strip_sql_comments(sql).split(";") if s.strip()]


# DROP COLUMN 单列出来：它不像 DROP INDEX 那样只是「重放会报错」，而是真会删掉服务器上的
# 列与列里的数据。真实 changelog 目前只在注释里提到它（V20260917_02 的收尾建议），故一律拒包、
# 由人工确认——需要删列时应当改写成 DO 块并显式登记，而不是让它随全量重放静默执行。
_DESTRUCTIVE = ("DELETE FROM", "TRUNCATE", "DROP TABLE", "DROP DATABASE", "DROP SCHEMA",
                "DROP COLUMN")

# `ON CONFLICT ... DO UPDATE` 无法靠剔仲裁目标变成重放安全（DO UPDATE 必须有目标），
# 且它的语义就是「覆盖」，与 ADR-0014 的只补不覆盖相左。
_CONFLICT_UPDATE_RE = re.compile(r"ON\s+CONFLICT[\s\S]*?DO\s+UPDATE", re.IGNORECASE)

# `ON CONFLICT ON CONSTRAINT <名>` 指名道姓地绑定某个约束，与带列清单的仲裁目标是同一个坑
# （后续迁移重塑约束后重放即报错），但 strip_conflict_targets 只认 `(列…)` 形态、剔不掉它，
# 故直接拒包。真实 changelog 目前没有这种写法，这条闸门是为将来兜底。
_CONFLICT_CONSTRAINT_RE = re.compile(r"ON\s+CONFLICT\s+ON\s+CONSTRAINT", re.IGNORECASE)


def audit_replay_safety(sql: str, origin: str) -> list[str]:
    """重放安全审计：找出会在存量库上洗数据、覆盖既有值或产生重复行的语句。

    「每次发布都重放全量 init.sql」这个需求成立的前提，就是包里没有会破坏既有数据的语句。
    返回问题清单（不抛），由调用方决定是否 fail-closed——合成入口一律拒绝出包。
    """
    problems: list[str] = []
    for st in split_statements(sql):
        upper = st.upper()
        # 按「语句里出现 INSERT INTO」判，而不是按前缀：CTE 形态 `WITH q AS (…) INSERT INTO …`
        # 不以 INSERT 开头，只看前缀会放它过去，重放就插重复行。
        if "INSERT INTO" in upper and "ON CONFLICT" not in upper:
            problems.append(f"{origin}: INSERT 缺 ON CONFLICT，重放会插重复行 → {st[:60]}…")
        if _CONFLICT_UPDATE_RE.search(st):
            problems.append(f"{origin}: 含 ON CONFLICT … DO UPDATE，重放会覆盖服务器既有值 → {st[:60]}…")
        if _CONFLICT_CONSTRAINT_RE.search(st):
            problems.append(f"{origin}: 含 ON CONFLICT ON CONSTRAINT，指名约束在后续迁移重塑后重放即报错，"
                            f"且无法自动剔除 → {st[:60]}…")
        # 必须按语句前缀判 UPDATE：列名 `updated_at` 含子串 UPDATE，真 changelog 里六个建表语句
        # 都带这一列，用子串匹配会把它们全部误杀。
        if upper.startswith("UPDATE") and _update_disposition(st) is None:
            problems.append(f"{origin}: 裸 UPDATE，重放会把服务器既有值拍回本次打包时的值 → {st[:60]}…")
        for word in _DESTRUCTIVE:
            if word in upper:
                problems.append(f"{origin}: 含 {word}，重放会洗掉服务器既有数据 → {st[:60]}…")
    return problems


# 仲裁目标剔除：`ON CONFLICT (key) DO NOTHING` 写的是它诞生当时的 schema。后续迁移会改约束
# （V20260925_02 就把 app_config 的主键 (key) 换成了 (owner_user_id, key) 联合唯一索引），
# 于是全量重放到第二遍时，前面那句 `ON CONFLICT (key)` 已找不到匹配的唯一约束，直接报
# 「there is no unique or exclusion constraint matching the ON CONFLICT specification」。
# 增量迁移轨永远不会踩到这个坑（每个文件只执行一次），全量重放轨则必然撞上。
# 剔掉目标后，`ON CONFLICT DO NOTHING` 不依赖任何特定索引，对「任意唯一冲突」都跳过，
# 正好就是只补不覆盖的语义；changelog 原文依旧不动（后端启动迁移靠它）。
_CONFLICT_TARGET_RE = re.compile(
    r"ON\s+CONFLICT\s*\([^)]*\)\s*DO\s+NOTHING", re.IGNORECASE)


def strip_conflict_targets(sql: str) -> tuple[str, int]:
    """把 `ON CONFLICT (列…) DO NOTHING` 归一为无目标的 `ON CONFLICT DO NOTHING`，返回 (新文本, 处数)。

    无目标形态不依赖仲裁索引，因此后续迁移怎么重塑唯一约束都不会让重放报错。
    代价：被创始人事务「划归」过的 llm.* 行（owner 由 NULL 变成账号 id）在重放时会补回一条
    NULL 属主的平台默认行。这不会覆盖任何人：config_service.get_all 是 {**glob, **mine}，
    用户自己的行永远胜出；后来的用户看到空密钥则与全新安装一致（ADR-0003 本就如此设计）。

    逐行处理且跳过整行注释：changelog 的注释里也会引用 `ON CONFLICT (列…)` 来说明仲裁目标
    （如 V20260925_02），那是给人看的文档，改写成 DO NOTHING 会造出误导性说明。
    跳行的代价是跨行书写的 ON CONFLICT 剔不掉——由 synth_init_sql 的兜底检查 fail-closed 拦下。
    """
    out: list[str] = []
    total = 0
    for line in sql.splitlines(keepends=True):
        if line.lstrip().startswith("--"):
            out.append(line)
            continue
        new, hits = _CONFLICT_TARGET_RE.subn("ON CONFLICT DO NOTHING", line)
        total += hits
        out.append(new)
    return "".join(out), total


# ---------------------------------------------------------------- 幂等守卫补齐

# changelog 是增量轨的产物，每条语句只需在「当时的库」上成立一次（run_migrations 靠
# schema_migration 记账，每个文件永不重跑）。全量重放轨则要求每条 DDL 在已建好的库上
# 再跑一遍也不报错。现有 changelog 里确实存在一批 CREATE TABLE / CREATE INDEX / ADD COLUMN
# 未带 IF NOT EXISTS（V20260917_02 最集中；增量轨下永远不会踩到，故一直未暴露），
# 合成时必须补齐。具体条数会随 changelog 增长而变，以产物 footer 的合计为准。
# 每条规则只匹配单行内的关键字前缀，故可逐行套用（与仲裁目标剔除同一处理方式）。
#
# 表约束关键字（CONSTRAINT / PRIMARY / UNIQUE / FOREIGN / CHECK / EXCLUDE）一律排除：
# PG 没给它们 IF NOT EXISTS 形态，插上去就是语法错误，该由 audit_idempotence 拒包。
_TABLE_CONSTRAINT_WORDS = r"CONSTRAINT\b|PRIMARY\b|UNIQUE\b|FOREIGN\b|CHECK\b|EXCLUDE\b"

_GUARD_RULES = (
    (re.compile(r"\b(CREATE\s+TABLE\s+)(?!IF\s+NOT\s+EXISTS\b)", re.IGNORECASE), "IF NOT EXISTS "),
    (re.compile(r"\b(CREATE\s+(?:UNIQUE\s+)?INDEX\s+(?:CONCURRENTLY\s+)?)(?!IF\s+NOT\s+EXISTS\b)",
                re.IGNORECASE), "IF NOT EXISTS "),
    (re.compile(r"\b(CREATE\s+SEQUENCE\s+)(?!IF\s+NOT\s+EXISTS\b)", re.IGNORECASE), "IF NOT EXISTS "),
    (re.compile(r"\b(CREATE\s+EXTENSION\s+)(?!IF\s+NOT\s+EXISTS\b)", re.IGNORECASE), "IF NOT EXISTS "),
    # 一句 ALTER 里可以有多个 ADD COLUMN 动作（V20260917_02 的 message 就是），逐个都要补
    (re.compile(r"\b(ADD\s+COLUMN\s+)(?!IF\s+NOT\s+EXISTS\b)", re.IGNORECASE), "IF NOT EXISTS "),
    # COLUMN 关键字在 PG 里可以省（`ADD foo int`），省了同样需要守卫
    (re.compile(r"\b(ADD\s+)(?!COLUMN\b|IF\s+NOT\s+EXISTS\b|" + _TABLE_CONSTRAINT_WORDS + ")",
                re.IGNORECASE), "IF NOT EXISTS "),
)


def add_idempotence_guards(sql: str) -> tuple[str, int]:
    """给 CREATE TABLE/INDEX/SEQUENCE/EXTENSION 与 ADD COLUMN 补上 IF NOT EXISTS，返回 (新文本, 处数)。

    刻意不改 changelog 原文：已应用的迁移文件就是历史，改它等于重写历史；而平台自己的
    run_migrations 只跑一次，本来也不需要这些守卫（ADR-0014）。
    """
    out: list[str] = []
    total = 0
    for line in sql.splitlines(keepends=True):
        if line.lstrip().startswith("--"):
            out.append(line)
            continue
        for rx, insertion in _GUARD_RULES:
            line, hits = rx.subn(lambda m, ins=insertion: m.group(1) + ins, line)
            total += hits
        out.append(line)
    return "".join(out), total


# 报错信息里要把跨行语句压成单行，故单立一个空白折叠正则（避免在 f-string 里写字面量反斜杠）
_WS_RE = re.compile(r"\s+")

# 幂等审计表：(语句识别正则, 违规残留正则, 人读名称, 处置建议)。先用识别正则命中语句类型，
# 再用违规正则复核它是否真的已守卫。违规正则为 None 表示命中即违规（PG 根本没有对应的
# IF [NOT] EXISTS 形态，无法自动补齐）。命中时一律 fail-closed 拒包，而不是一厢情愿地改写
# 语义（例如把 ADD CONSTRAINT 改成先 DROP 再 ADD，那会在重放窗口里短暂丢掉约束）。
# 逐条都查、不 break：一句 ALTER 可以同时 ADD COLUMN 与 ADD CONSTRAINT，两者要分开判。
_NO_GUARD_YET = "规范化后仍缺守卫（多为关键字跨行书写，请把它们挤到同一行）"
_REPLAY_DDL_RULES = (
    (r"^CREATE\s+TABLE\b", r"^CREATE\s+TABLE\s+(?!IF\s+NOT\s+EXISTS\b)",
     "CREATE TABLE", _NO_GUARD_YET),
    (r"^CREATE\s+(?:UNIQUE\s+)?INDEX\b",
     r"^CREATE\s+(?:UNIQUE\s+)?INDEX\s+(?:CONCURRENTLY\s+)?(?!IF\s+NOT\s+EXISTS\b)",
     "CREATE INDEX", _NO_GUARD_YET),
    (r"^CREATE\s+SEQUENCE\b", r"^CREATE\s+SEQUENCE\s+(?!IF\s+NOT\s+EXISTS\b)",
     "CREATE SEQUENCE", _NO_GUARD_YET),
    (r"^CREATE\s+EXTENSION\b", r"^CREATE\s+EXTENSION\s+(?!IF\s+NOT\s+EXISTS\b)",
     "CREATE EXTENSION", _NO_GUARD_YET),
    # 违规正则不锚行首：一句 ALTER 里的每个 ADD COLUMN 动作都得各自带守卫，
    # 只看「全文出现过一次 IF NOT EXISTS」会漏掉多动作里的第二个（V20260917_02 的 message 就是）。
    (r"^ALTER\s+TABLE\b[\s\S]*?\bADD\s+COLUMN\b",
     r"\bADD\s+COLUMN\s+(?!IF\s+NOT\s+EXISTS\b)",
     "ALTER TABLE … ADD COLUMN", _NO_GUARD_YET),
    # 省掉 COLUMN 关键字的 `ADD foo int` 同样需要守卫，否则重放会因列已存在而报错
    (r"^ALTER\s+TABLE\b[\s\S]*?\bADD\s+(?!COLUMN\b|" + _TABLE_CONSTRAINT_WORDS + ")",
     r"\bADD\s+(?!IF\s+NOT\s+EXISTS\b)(?!COLUMN\b|" + _TABLE_CONSTRAINT_WORDS + ")",
     "ALTER TABLE … ADD（省略 COLUMN）", _NO_GUARD_YET),
    (r"^ALTER\s+TABLE\b[\s\S]*?\bADD\s+CONSTRAINT\b", None,
     "ALTER TABLE … ADD CONSTRAINT", "PG 无 IF NOT EXISTS 形态；请包 DO 块先查 pg_constraint 再 ADD"),
    (r"^CREATE\s+TYPE\b", None,
     "CREATE TYPE", "PG 无 IF NOT EXISTS 形态；请包 DO 块先查 pg_type 再 CREATE"),
    (r"^ALTER\s+TABLE\b[\s\S]*?\bRENAME\b", None,
     "ALTER TABLE … RENAME", "重放会因对象已改名而失败；请包 DO 块先判旧名是否存在"),
    (r"^ALTER\s+TABLE\b[\s\S]*?\bALTER\s+COLUMN\b[\s\S]*?\bTYPE\b", None,
     "ALTER COLUMN … TYPE", "重放等于全表重写 + 持锁；请像向量列那样包 DO 块按实际类型判断"),
    (r"^CREATE\s+(?:VIEW|MATERIALIZED\s+VIEW|FUNCTION|PROCEDURE)\b",
     r"^CREATE\s+(?!OR\s+REPLACE\b)",
     "CREATE VIEW/FUNCTION", "缺 OR REPLACE；若列集会变，OR REPLACE 也会失败，请人工确认"),
    # DROP 系列的 IF EXISTS：缺了它，第二遍重放会因对象已删而直接报错。
    # DROP TABLE / DROP SCHEMA / DROP COLUMN 不在这里：它们会洗数据，已由重放安全审计一律拒包。
    (r"^DROP\s+(?:INDEX|TYPE|VIEW|MATERIALIZED\s+VIEW|FUNCTION|PROCEDURE|SEQUENCE)\b",
     r"^DROP\s+\S+\s+(?!IF\s+EXISTS\b)",
     "DROP INDEX/TYPE/VIEW/…", "缺 IF EXISTS，重放会因对象已删而报错"),
)


def audit_idempotence(sql: str, origin: str) -> list[str]:
    """对**已规范化**的文本做幂等复核：找出重放到存量库仍会报错的 DDL。

    这是 add_idempotence_guards 的兵对兵校验：正则漏匹配、跨行书写、或将来新增了无法自动
    补齐的语句类型，都在这里 fail-closed，而不是等到服务器上 psql 报错才发现。
    向量定维/varchar 加宽已由 guard_column_type_alter 换成 DO 块（不再以 ALTER TABLE 开头），故不会被误报。
    """
    problems: list[str] = []
    for st in split_statements(sql):
        for pat, violation, label, remedy in _REPLAY_DDL_RULES:
            if not re.search(pat, st, re.IGNORECASE):
                continue
            if violation is not None and not re.search(violation, st, re.IGNORECASE):
                continue                      # 已守卫，过
            problems.append(f"{origin}: {label} 重放不安全——{remedy}\n"
                            f"      语句：{_WS_RE.sub(' ', st)[:90]}…")
    return problems


# 无守卫的列改型（向量定维、varchar 加宽）：PG 不提供 IF-TYPE 形态，重放它 = 全表重写 +
# ACCESS EXCLUSIVE 锁，目标维度/宽度不符时还会直接失败。合成产物里换成 DO 块按实际类型判断
# （changelog 原文保持不动，因为 app/db.py 的切分器不认 $$，改原文会拖挂后端启动迁移）。
_ALTER_TYPE_RE = re.compile(
    r"ALTER\s+TABLE\s+(?P<tbl>[A-Za-z_]\w*)\s+ALTER\s+COLUMN\s+(?P<col>[A-Za-z_]\w*)\s+"
    r"TYPE\s+(?P<type>vector\s*\(\s*\d+\s*\)|varchar\s*\(\s*\d+\s*\))\s*;",
    re.IGNORECASE)

_ALTER_TYPE_GUARD_TMPL = """DO $zcalter$
DECLARE
    tbl regclass;
    cur text;
BEGIN
    tbl := to_regclass('{tbl}');
    IF tbl IS NULL THEN
        RETURN;                                   -- 表还没建（理论上不会走到）：交给前面的 DDL
    END IF;
    SELECT format_type(atttypid, atttypmod) INTO cur
      FROM pg_attribute
     WHERE attrelid = tbl AND attname = '{col}' AND attnum > 0 AND NOT attisdropped;
    IF cur IS NULL THEN
        RETURN;                                   -- 列不存在：同上，不猜
    END IF;
    IF cur IS DISTINCT FROM '{expected}' THEN
        EXECUTE format('ALTER TABLE %s ALTER COLUMN {col} TYPE {type_sql}', tbl);
    END IF;
END
$zcalter$;"""


def _expected_type_label(type_sql: str) -> str:
    """`format_type` 给 varchar(N) 的展示名是 `character varying(N)`，以它为比较基准。"""
    m = re.fullmatch(r"varchar\s*\(\s*(\d+)\s*\)", type_sql, re.IGNORECASE)
    if m:
        return f"character varying({m.group(1)})"
    return type_sql


def guard_column_type_alter(sql: str) -> tuple[str, int]:
    """把无守卫的 `ALTER COLUMN … TYPE vector(N)/varchar(N)` 换成类型判断 DO 块。

    返回 (新文本, 替换数)。`IS DISTINCT FROM` 用 format_type 的实际类型做比较：已是目标
    类型时整句空转，重放只剩一次系统目录查询，不再全表重写。
    """
    def _sub(m: re.Match) -> str:
        type_sql = _WS_RE.sub(" ", m.group("type")).strip()
        return _ALTER_TYPE_GUARD_TMPL.format(
            tbl=m.group("tbl"), col=m.group("col"), type_sql=type_sql,
            expected=_expected_type_label(type_sql))

    return _ALTER_TYPE_RE.subn(_sub, sql)


# ---------------------------------------------------------------- 裸 UPDATE 的显式处置表

# 为什么需要这张表：changelog 是增量轨的产物，一条 `UPDATE … SET 默认值` 只需在当时的库上
# 生效一次（run_migrations 每个文件永不重跑）；全量重放轨里它会**每次发布都执行一遍**，
# 把运维在服务器上改过的值静默拍回本地值。这类回退没有任何报错，只会在下一次现场
# 预览超时时才暴露。deploy.* 不在 config_service.USER_PREFIXES 里，所以任何登录账号改的都是
# owner NULL 的平台行，而 deploy_cfg 读的正是这一行——影响链是闭合的。
#
# 表外的一律 fail-closed 拒包，不做「猜测式改写」：改错语义的代价比不出包大得多。
# 每项：(归一化后的原句, 归一化后的改写句 或 None 表示原句自限故保留, 处置理由)。
_UPDATE_DISPOSITIONS = (
    # V20260920_01：dev 预览健康等待默认值 60 → 240。加上「仅当仍是旧默认 60」的值守卫后，
    # 抬升只会在从未被人工调过的库上发生一次；此后 WHERE 再不命中，运维调成的 900 也不会被碰。
    ("UPDATE app_config SET value = '240' WHERE key = 'deploy.health_timeout'",
     "UPDATE app_config SET value = '240' WHERE key = 'deploy.health_timeout' AND value::text = '60'",
     "默认值抬升加值守卫"),
    # V20260925_01：给存量重名昵称追加 #id 后缀，为紧随其后的 UNIQUE 索引让位。
    # 保留原句：它的 WHERE（id 不属于每个昵称的最小 id）本身就是自限的——跑过一遍后不再
    # 有重名行，第二遍命中 0 行，不依赖 ux_actor_nickname 是否已建。改写它反而可能弄坏去重语义。
    ("UPDATE actor SET nickname = nickname || '#' || id "
     "WHERE id NOT IN (SELECT MIN(id) FROM actor GROUP BY nickname)",
     None,
     "昵称去重（WHERE 自限）"),
)


def _normalize_statement(st: str) -> str:
    """把语句压成单行并去掉尾分号，用于与处置表做精确比较。"""
    return _WS_RE.sub(" ", st).strip().rstrip(";").strip()


def _update_disposition(st: str):
    """已登记的裸 UPDATE 返回 (原句, 改写句, 理由)，未登记返回 None。

    用精确相等而不是子串/正则包含：语句一旦改了形——哪怕只是把 240 改成 300——就必须
    重新过人工评审，不能让守卫在没人知道的情况下继续「放行」。
    """
    norm = _normalize_statement(st)
    for unsafe, safe, reason in _UPDATE_DISPOSITIONS:
        if norm == unsafe or (safe is not None and norm == safe):
            return unsafe, safe, reason
    return None


def _loose_re(normalized: str) -> re.Pattern:
    """把归一化语句变成「空白可跨行」的原文匹配正则，末尾锚在分号前。

    锚分号是为了幂等：改写后的句子比原句长，若不锚定，第二次调用会把已改写句子的
    前半段再匹配一次，叠出 `AND value::text = '60' AND value::text = '60'`。
    """
    body = r"\s+".join(re.escape(part) for part in normalized.split(" "))
    return re.compile(body + r"\s*(?=;)", re.IGNORECASE)


def rewrite_unsafe_updates(sql: str) -> tuple[str, int]:
    """把处置表里登记为「需改写」的裸 UPDATE 换成重放安全形态，返回 (新文本, 处数)。

    与另两处规范化一样只动生成物、不动 changelog 原文（增量轨靠它，且它每个文件只跑一次）。
    处置表里 safe 为 None 的条目是自限语句，原样保留、不计数。
    """
    out = sql
    total = 0
    for unsafe, safe, _reason in _UPDATE_DISPOSITIONS:
        if safe is None:
            continue
        out, hits = _loose_re(unsafe).subn(lambda _m, s=safe: s, out)
        total += hits
    return out, total


def _q(value: str) -> str:
    """SQL 单引号字面量转义（预置应用名可能含引号；只处理 ' 即可，PG 不支持反斜杠转义）。"""
    return "'" + str(value).replace("'", "''") + "'"


def preset_seed_sql(presets) -> str:
    """预置应用的元数据种子段：workspace + app_project，root_path 写成部署根占位符。

    - `root_path` 是 `{{DEPLOY_ROOT}}/apps/<slug>` 而非打包期那个真实路径（ADR-0034）：部署根
      现在由部署现场输入，打包期写死会让换目录后的预置应用集体失联。渲染归 apply-init.sh。
    - `owner_user_id` 刻意不写（留 NULL）：服务器上第一个注册的账号按 register 的创始人事务
      自动承接这些行，所以演示账号不需要进 SQL（ADR-0014）。
    - 每个应用包在一个 DO 块里，以 slug 是否已存在为总闸：已存在就整段跳过，不会留下
      「workspace 插了、app_project 因端口撞号没插」的孤儿行。
    - workspace.id 靠 `RETURNING … INTO` 拿，不靠 root_path 反查：V20260925_02 已把 root_path
      的全局 UNIQUE 换成 (owner_user_id, root_path) 联合唯一，同路径不同属主的两行是合法的，
      反查会命中多行、把同一个 slug 插两遍。
    - 端口撞号时让 INSERT 真失败（配合 psql 的 ON_ERROR_STOP=1 当场报错），而不是静默跳过——
      静默跳过会产出「包打成功了、应用却没出现」的谜团。
    """
    if not presets:
        return ""
    parts = ["-- ===== 预置应用种子（build-output 生成，勿手改）=====",
             "-- owner_user_id 留 NULL：首个注册账号按创始人事务自动承接（ADR-0014）",
             "-- 重放安全：以 slug 存在性为总闸，已存在则整段跳过"]
    for p in presets:
        root_path = f"{DEPLOY_ROOT_PLACEHOLDER}/apps/{p.slug}"
        parts.append(f"""DO $zcpreset$
DECLARE
    wsid BIGINT;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM app_project WHERE slug = {_q(p.slug)}) THEN
        INSERT INTO workspace (name, root_path)
        VALUES ({_q(p.name)}, {_q(root_path)})
        RETURNING id INTO wsid;

        INSERT INTO app_project (workspace_id, name, slug, scale, stack, prod_port, dev_port)
        VALUES (wsid, {_q(p.name)}, {_q(p.slug)}, {_q(p.scale)}, {_q(p.stack)},
                {p.prod_port}, {p.dev_port});
    END IF;
END
$zcpreset$;""")
    return "\n\n".join(parts) + "\n"


def synth_init_sql(changelog_dir: Path, presets, deploy_root: str,
                   generated_at: str | None = None) -> str:
    """把 changelog/V*.sql 按序合成全量 init.sql，并追加预置应用种子段。

    文件名以 `V<日期>_<序号>_` 起头，故字典序即时间序（与 app/db.py::run_migrations 同一假设）。
    """
    files = sorted(changelog_dir.glob("V*.sql"))
    if not files:
        raise BuildError(f"{changelog_dir} 下没有任何 V*.sql，拒绝产出空 init.sql")

    stamp = generated_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    slugs = ", ".join(p.slug for p in presets) or "（无）"
    # 只打平台（无预置应用）时压根不产出种子段，头部也就不许提它——否则 init.sql 会宣称自己
    # 含有一段实际不存在的内容，将来排查「预置应用哪去了」时白找一圈。
    sources = f"changelog/V*.sql（{len(files)} 个文件，按序）"
    if presets:
        sources += " + 预置应用种子段"
    header = f"""-- init.sql —— zerocode 平台库全量初始化脚本（生成物，勿手改）
-- 合成自：{sources}
-- 生成时间：{stamp}    预置应用：{slugs}
-- 部署根：本文件里的 root_path 写成 {DEPLOY_ROOT_PLACEHOLDER} 占位符，由 apply-init.sh 按
--         .env 的 DEPLOY_ROOT 在执行前渲染（ADR-0034）；打包默认值是 {deploy_root}。
-- 重放语义：只补不覆盖（结构段幂等 DDL；数据段一律 ON CONFLICT DO NOTHING 或存在性门，
--           服务器既有数据永不被洗）。为达此语义，合成时对 changelog 原文做了四处等价改写：
--           1) 给 CREATE TABLE/INDEX/SEQUENCE/EXTENSION 与 ADD COLUMN 补 IF NOT EXISTS
--           2) 剔除 ON CONFLICT 的仲裁目标（后续迁移会重塑唯一约束，带目标重放会报错）
--           3) 无守卫的 ALTER COLUMN TYPE vector(N)/varchar(N) 换成按实际类型判断的 DO 块
--           4) 会覆盖服务器既有值的裸 UPDATE 按处置表改写（仅当仍是旧默认值时才生效）
--           四处均只存在于本生成物，changelog/ 原文不动（它是增量轨的历史，且
--           app/db.py 的语句切分器不认 $$）。无法自动幂等化的语句会在打包期直接拒包。
-- 执行方式：只能 `bash apply-init.sh`（本文件含 DO $$ 块，且占位符要在那里渲染）；
--           **禁止**放进 changelog/——app/db.py 的语句切分器不认 $$，会把后端启动迁移拖挂。
"""

    problems: list[str] = []
    body: list[str] = []
    guarded = 0
    stripped = 0
    idem = 0
    rewritten = 0
    for f in files:
        raw = f.read_text(encoding="utf-8")
        problems.extend(audit_replay_safety(raw, f.name))
        text, hits = guard_column_type_alter(raw)
        guarded += hits
        text, hits = strip_conflict_targets(text)
        stripped += hits
        text, hits = add_idempotence_guards(text)
        idem += hits
        text, hits = rewrite_unsafe_updates(text)
        rewritten += hits
        # 幂等复核跑在规范化后的文本上：漏补的、以及 PG 根本没有守卫形态的，都在这里拦下
        problems.extend(audit_idempotence(text, f.name))
        body.append(f"-- ===== {f.name} =====\n{text.rstrip()}\n")
    if problems:
        raise BuildError("changelog 存在重放不安全的语句，拒绝出包（ADR-0014「只补不覆盖」）：\n  - "
                         + "\n  - ".join(problems))

    merged = header + "\n" + "\n".join(body)
    # 兜底：合成后不得残留无守卫的列改型，也不得残留带仲裁目标的 ON CONFLICT。
    # 查的是剥过注释的文本：注释里合法保留着 `ON CONFLICT (列…)` 这类说明性引用。
    active = _strip_sql_comments(merged)
    leftover = _ALTER_TYPE_RE.search(active)
    if leftover:
        raise BuildError(f"合成后仍残留无守卫的列改型语句：{leftover.group(0)[:80]}")
    leftover = _CONFLICT_TARGET_RE.search(active)
    if leftover:
        raise BuildError(
            "合成后仍残留带仲裁目标的 ON CONFLICT，重放到存量库会报「no unique or exclusion "
            f"constraint matching」：{leftover.group(0)[:80]}\n"
            "  常见原因：该句跨行书写（剔除逐行进行）。请把 ON CONFLICT … DO NOTHING 写在同一行。")
    # 处置表里的改写必须真的落到了产物上：原文写法一旦漂离 _loose_re 能匹配的形状（多了个
    # 换行内的注释、改了引号风格、漏了结尾分号），改写会静默不生效。
    #
    # 判定必须与 audit 同源：audit 走 _normalize_statement（会 rstrip 分号），而 _loose_re 末尾
    # 锚在分号前（那是改写幂等的保证，不能改）。两者判定域不同就会出现 fail-open：一条登记过的
    # 语句只要漏了结尾分号，就会「审计认为已登记 → 放行」+「改写与兜底都匹配不到 → 静默」，
    # 无守卫的原句直接进产物，footer 还写「改写 0 处」——看上去完全合法。
    final = {_normalize_statement(s) for s in split_statements(active)}
    for unsafe, safe, reason in _UPDATE_DISPOSITIONS:
        if safe is not None and unsafe in final:
            raise BuildError(
                f"合成后仍残留未改写的裸 UPDATE（{reason}）：{unsafe[:70]}…\n"
                "  常见原因：该句在 changelog 里的写法与处置表登记的形状已不一致"
                "（行内注释、缺结尾分号、引号风格等）。请重新评审并更新 _UPDATE_DISPOSITIONS。")

    seed = preset_seed_sql(presets)
    if seed:
        merged += "\n" + seed
    merged += (f"\n-- 合成完成：结构段 {len(files)} 个来源文件，幂等守卫补齐 {idem} 处，"
               f"ON CONFLICT 仲裁目标剔除 {stripped} 处，向量改型守卫 {guarded} 处，"
               f"裸 UPDATE 改写 {rewritten} 处，预置应用 {len(presets)} 个\n")
    return merged


# ---------------------------------------------------------------- 交付文件模板

def _compose_override(key: str, value: str) -> str:
    """compose 的 environment 行形态：`${VAR:-打包值}`——打包值只是默认，不是终值。

    部署根（见 compose_yaml）与域名/镜像源用的是同一种形态，但改生效的**动作**不同：
    域名与镜像源是单点值，改完 `up -d` 就生效；部署根还喂着镜像的 `WORKDIR`，改完必须
    `up -d --build` 才不会让挂载点与容器内路径分家（ADR-0034）。两者都落在 `.env` 里，
    因为 README §4 承诺后续发布不覆盖服务器上的 `.env`——写成裸值就等于永远改不动它。
    """
    return f"{key}: ${{{key}:-{value}}}"


def _backend_overlay_env(gateway_domain: str, mirrors: Mirrors) -> str:
    """backend 的 environment 段要追加的行（自带 6 空格缩进，可直接插在上一行之后）。

    只列**有运行期消费方**的项：网关域名与 SSL 开关由 app/deploy/nics.py 在容器里读，PyPI / npm 由
    app/deploy/compose.py 转注进应用的 dev 栈（于是服务器上改一次 `.env` 就同时管住平台自己
    与它起出来的每一个应用）。apt 不进这里：它只作用于镜像构建期，容器里没人读它。
    """
    lines = [
        "      # 以下几行打包时烘的是默认值，可在服务器 .env 里用同名变量覆盖（改完 up -d 就生效，",
        "      # 不必重跑打包）。同一批变量名也读打包机环境变量，优先级：显式参数 > 打包机 env > 默认。",
        "      " + _compose_override(GATEWAY_DOMAIN_ENV, gateway_domain),
        # 预览的两个宿主地址：探测值指向宿主（容器里的 127.0.0.1 连不到应用发布端口），
        # 展示值是用户浏览器里的那个主机（服务器部署时填 IP 或域名，否则复制到的地址打不开）。
        "      " + _compose_override(PREVIEW_PROBE_HOST_ENV, PREVIEW_PROBE_HOST_BAKED),
        "      " + _compose_override(PREVIEW_HOST_ENV, PREVIEW_HOST_BAKED),
        # SSL 开关只影响地址投影（控制台/发布记录/应用 README），网关不必停机：
        # 域名没备案时把它设成 false，应用就改以 http://<展示宿主>:<prod 端口> 露出来。
        "      " + _compose_override(SSL_ENABLE_ENV, SSL_ENABLE_BAKED),
        # 浏览器检查 sidecar：平台在 compose 网络里按服务名访问；sidecar 再经自己的
        # extra_hosts（host-gateway）访问宿主上发布的应用 dev 端口。
        "      " + _compose_override(BROWSER_URL_ENV, BROWSER_URL_BAKED),
        "      " + _compose_override(BROWSER_TARGET_HOST_ENV, BROWSER_TARGET_HOST_BAKED),
    ]
    if mirrors.pypi:
        lines.append("      " + _compose_override(PYPI_MIRROR_ENV, mirrors.pypi))
    if mirrors.npm:
        lines.append("      " + _compose_override(NPM_REGISTRY_ENV, mirrors.npm))
    return "\n".join(lines)


def _apt_source_block(apt_host: str | None) -> str:
    """把 Debian 官方源整体换到镜像站；不给镜像站就返回空串（产物逐字不变）。

    两条源路径一起改是必需的：Debian 12 起的官方镜像用 deb822 格式的
    `/etc/apt/sources.list.d/debian.sources`，老镜像仍是 `/etc/apt/sources.list`；只改一处
    会出现「主源通了、另一半继续走 deb.debian.org 超时」这种更难查的半死状态。用 glob 兜
    整个 `.sources` 目录，顺带也不会因上游新增文件而漏掉；`[ -e "$f" ] || continue` 同时
    接住「glob 没匹配到任何东西、被当作字面量」这一路。

    `security.debian.org` 要单独列一次：它不以 `deb.debian.org` 为子串，只换前者的话
    `apt-get update` 仍会为了安全源去连官方站点——本次故障现场报的
    `E: Unable to locate package curl` 正是这一步超时后的形态（不是包名写错）。
    """
    if not apt_host:
        return ""
    # 结尾自带空行：换源块与下面的 docker CLI 注释之间要有分隔，而没注入时一个字符都不留
    # （`{apt_block}` 贴在注释行行首，不占独立模板行——否则默认产物会平白多出一个空行）。
    return "\n".join([
        "RUN set -eux; \\",
        " for f in /etc/apt/sources.list /etc/apt/sources.list.d/*.sources; do \\",
        '   [ -e "$f" ] || continue; \\',
        f"   sed -i -e 's|deb.debian.org|{apt_host}|g' -e 's|security.debian.org|{apt_host}|g' \"$f\"; \\",
        " done",
    ]) + "\n\n"


def _pip_index_block(pypi_url: str | None) -> str:
    """在 `pip install` 之前把 index-url 指到镜像站；不给就返回空串（产物逐字不变）。

    写成 `ENV` 而不是 `RUN pip install -i …`：ENV 留在镜像里，容器内后续任何一次 pip 安装
    都跟着走镜像站，不必记着给每条命令补参数。
    """
    if not pypi_url:
        return ""
    return ("# 依赖镜像源（打包时烘进镜像）：本镜像的构建期与容器内后续的 pip 安装都走这里\n"
            f"ENV PIP_INDEX_URL={pypi_url}\n")


def _env_example_overlay(gateway_domain: str, mirrors: Mirrors,
                         playwright_download_host: str = PLAYWRIGHT_DOWNLOAD_HOST_BAKED) -> str:
    """`.env.example` 里的运行期覆盖位。一律写**裸值**，绝不写 `${VAR:-…}`。

    同一个变量名在 compose 里是 `${VAR:-打包值}`、在这里是打包值本体，形态不同是必然的：
    compose 读 `.env` 时只做一层插值，`${…}` 写在 `.env` 里会被当成一个字面值传进容器
    （nics.py 会读到字面量 `${ZEROCODE_GATEWAY_DOMAIN}`，拼出更离谱的地址）。
    """
    lines = [
        "",
        "# 网关域名：控制台里应用地址的后缀（https://<应用slug>.<这个域名>）。",
        "# 改这里只需重跑 docker compose up -d，不必重跑打包；留空会退回打包时的值。",
        f"{GATEWAY_DOMAIN_ENV}={gateway_domain}",
        "",
        "# 预览的两个宿主地址。探测地址是平台容器连应用端口用的（保持 host.docker.internal 即可，",
        "# 除非应用与平台不在同一台机器）；展示地址是用户在浏览器里打开预览用的，**服务器部署时",
        "# 请填服务器的 IP 或域名**，否则控制台里的「打开/复制预览地址」指向访问者自己的机器。",
        f"{PREVIEW_PROBE_HOST_ENV}={PREVIEW_PROBE_HOST_BAKED}",
        f"{PREVIEW_HOST_ENV}={PREVIEW_HOST_BAKED}",
        "",
        "# 应用访问地址形态：true（默认）= 网关通配域名 https://<应用slug>.<域名>；",
        "# 显式 false/0/no/off = 改用 IP 直连 http://<上面的预览展示地址>:<prod 端口>。",
        "# 域名未备案、443 被拦截时设为 false，网关不必停机——控制台、发布记录与",
        "# 应用 README 里的应用地址会一起切换。",
        f"{SSL_ENABLE_ENV}={SSL_ENABLE_BAKED}",
        "",
        "# 浏览器检查 sidecar（Agent 的 preview_page）。平台容器经 compose 服务名访问它；",
        "# 只有把 sidecar 拆到另一台机器时才需要改地址。",
        f"{BROWSER_URL_ENV}={BROWSER_URL_BAKED}",
        "# sidecar 连应用 dev 端口的宿主地址。应用端口发布在平台所在宿主上，保持",
        "# host.docker.internal 即可（compose 已给 sidecar 配 host-gateway）。",
        f"{BROWSER_TARGET_HOST_ENV}={BROWSER_TARGET_HOST_BAKED}",
        "# sidecar 只检查白名单里的 host。若把上一行改成 IP/域名，必须把它加进这里，",
        "# 否则 preview_page 会固定返回「浏览器检查服务不可用」。",
        f"{BROWSER_ALLOWED_HOSTS_ENV}={BROWSER_ALLOWED_HOSTS_BAKED}",
        "# 可交互会话（Agent 的 browser_step）的资源闸：同时能留几个活页面、空闲多久回收。",
        "# 超限的新会话是被直接拒绝而不是排队；调大要同步看 sidecar 的 mem_limit（默认 1g）。",
        f"{BROWSER_MAX_SESSIONS_ENV}={BROWSER_MAX_SESSIONS_BAKED}",
        f"{BROWSER_SESSION_TTL_ENV}={BROWSER_SESSION_TTL_BAKED}",
        "# browser 镜像构建期的 Chromium 下载源；改完要 docker compose build browser 才生效。",
        f"{PLAYWRIGHT_DOWNLOAD_HOST_ENV}={playwright_download_host}",
    ]
    if mirrors.pypi:
        lines += ["", "# 应用预览环境（dev 栈）装 Python 依赖用的镜像源，打包时烘的是默认值。",
                  f"{PYPI_MIRROR_ENV}={mirrors.pypi}"]
    if mirrors.npm:
        lines += ["", "# 应用预览环境（dev 栈）装前端依赖用的镜像源，同上。",
                  f"{NPM_REGISTRY_ENV}={mirrors.npm}"]
    return "\n".join(lines) + "\n"


def compose_yaml(deploy_root: str, gateway_domain: str = DEFAULT_GATEWAY_DOMAIN,
                 mirrors: Mirrors = DEFAULT_MIRRORS,
                 playwright_download_host: str = PLAYWRIGHT_DOWNLOAD_HOST_BAKED) -> str:
    """平台 compose：pg（pgvector）+ backend（挂 docker.sock 与同路径 apps/data/store）。

    - PG 容器名写死 `zerocode-pg16`：app/deploy/provision.py 的 ensure_zcnet 与应用栈的连接串
      都按这个名字找库，改名等于全线失联。
    - `zcnet` 用 `name: zcnet` 由本项目创建（而不是 external: true）：应用栈声明的是
      external，必须有人在它们之前把网络建出来，平台 compose 就是那个人。
    - 同路径挂载（ADR-0013）：`<部署根>/apps` 在容器内挂到**完全相同**的绝对路径，
      平台在容器里用宿主路径调 docker compose 时，宿主 daemon 才解析得到。
    - gateway 是独立 compose 项目（ADR-0018），本文件只负责把它的**输入**接好：把
      `<部署根>/gateway/routes` 同路径挂进 backend，并让 `ZEROCODE_GATEWAY_ROUTES` 指过去。
      挂目录而不是单个文件：后端用 `.tmp + os.replace` 原子替换 routes.json，单文件 bind mount
      在替换后仍钉着旧 inode，网关会一直读到第一次的内容。
    - 部署根走 `${DEPLOY_ROOT:-打包值}` 运行期插值（ADR-0034）：挂载两侧是同一个变量，
      镜像 `WORKDIR` 由同名 build arg 决定，两者都取自同一份 `.env`——所以运行期改它不再会让
      挂载点与 REPO_ROOT 分家。代价是**换根必须重跑 `up -d --build`**（WORKDIR 是构建期的），
      只 `up -d` 会让镜像停在旧根，症状仍是「新建应用落进容器可写层」。
    - 前端 dist 由后端直接托管（app/main.py 的 SPA 挂载），不再起 nginx 容器。
    - backend 的 environment 段尾部追加 `ZEROCODE_*` 运行期覆盖位（见 _backend_overlay_env）。
    """
    dr = f"${{DEPLOY_ROOT:-{deploy_root}}}"
    extra_env = _backend_overlay_env(gateway_domain, mirrors)
    browser_apt = mirrors.apt or ""
    browser_pypi = mirrors.pypi or ""
    return f"""# zerocode 平台（演示部署）—— 由 scripts/build_output.py 生成，勿手改
# 一条命令起全栈：docker compose up -d --build
name: zerocode

services:
  pg:
    image: pgvector/pgvector:pg16          # 必须带 pgvector：workspace_memory.embedding + HNSW 索引
    container_name: zerocode-pg16          # 名字被平台代码写死，不要改
    restart: unless-stopped
    environment:
      POSTGRES_USER: ${{POSTGRES_USER:-zerocode}}
      POSTGRES_PASSWORD: ${{POSTGRES_PASSWORD:?请在 .env 里设置 POSTGRES_PASSWORD}}
      POSTGRES_DB: zerocode
    ports:
      - "${{PG_PORT:-5433}}:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
    networks: [default, zcnet]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U $${{POSTGRES_USER:-zerocode}} -d zerocode"]
      interval: 5s
      timeout: 5s
      retries: 20

  backend:
    build:
      context: .                      # 上下文取整包，Dockerfile 才能同时 COPY backend/ 与 frontend/dist
      dockerfile: backend/Dockerfile
      args:
        # WORKDIR 与下面的挂载点同源（都取 .env 的 DEPLOY_ROOT，ADR-0034）。换部署根必须重跑
        # `up -d --build`——只 up -d 不重建，镜像的 WORKDIR 还停在旧根，新建应用会落进可写层。
        DEPLOY_ROOT: {dr}
    image: zerocode-platform:demo
    container_name: zerocode-backend
    restart: unless-stopped
    depends_on:
      pg:
        condition: service_healthy
      browser:
        condition: service_started
    environment:
      # 平台库连接账号需具备建库建角色权限（要供给应用分库）；镜像默认用户即超级用户
      ZEROCODE_DATABASE_URL: postgresql+asyncpg://${{POSTGRES_USER:-zerocode}}:${{POSTGRES_PASSWORD}}@zerocode-pg16:5432/zerocode
      ZEROCODE_DATA_DIR: {dr}/data
      ZEROCODE_LOG_LEVEL: INFO
      # 网关路由表：与 gateway 容器共用的宿主目录（见上）。app/deploy/gateway_routes.py
      # 往这里写 {{slug: prod_port}}，网关只读它；写不进去时业务照常，只是域名访问 404。
      ZEROCODE_GATEWAY_ROUTES: {dr}/gateway/routes/routes.json
{extra_env}
    ports:
      - "${{PLATFORM_PORT:-8100}}:8100"      # 控制台：http://<服务器IP>:8100
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock   # 平台要在宿主起应用 dev/prod 栈
      # 下面四个挂载点与镜像的 WORKDIR 逐字相同（ADR-0013），两侧同一个变量、同一次插值
      # （ADR-0034）：换部署根改 .env 即可，但要配 `up -d --build` 让 WORKDIR 跟上。
      - {dr}/apps:{dr}/apps
      - {dr}/data:{dr}/data
      - {dr}/store:{dr}/store
      # 挂目录不是挂文件：routes.json 由后端原子替换，单文件 bind mount 会钉在旧 inode 上
      - {dr}/gateway/routes:{dr}/gateway/routes
    # 应用栈把 dev/prod 端口发布在宿主上，平台容器要探得到它们（网关用同一条 host-gateway）。
    # 没有这行的时候 host.docker.internal 在 Linux 上根本不解析，探针只能连不到任何地址。
    extra_hosts:
      - "host.docker.internal:host-gateway"
    networks: [default, zcnet]

  # 浏览器检查 sidecar（ADR-0031）：preview_page 的隔离执行环境。不持有 docker.sock、
  # 不注入平台库凭据；也不发布宿主端口——只有 compose 内的 backend 能访问它。
  browser:
    build:
      context: ./browser
      dockerfile: Dockerfile
      args:
        # 构建期换源与 Chromium 下载源：变量名与打包机同一套（三级取值的中间那级留在服务器
        # .env 里覆盖），改完要 docker compose build browser（up --build 也会重建）。
        ZEROCODE_APT_MIRROR: ${{ZEROCODE_APT_MIRROR:-{browser_apt}}}
        ZEROCODE_PYPI_MIRROR: ${{ZEROCODE_PYPI_MIRROR:-{browser_pypi}}}
        PLAYWRIGHT_DOWNLOAD_HOST: ${{PLAYWRIGHT_DOWNLOAD_HOST:-{playwright_download_host}}}
    image: zerocode-browser:demo
    container_name: zerocode-browser
    restart: unless-stopped
    environment:
      # 只接受这些 host 的检查目标；改了 ZEROCODE_BROWSER_TARGET_HOST 要同步加进来。
      BROWSER_ALLOWED_HOSTS: ${{BROWSER_ALLOWED_HOSTS:-{BROWSER_ALLOWED_HOSTS_BAKED}}}
      # 可交互会话的资源闸（ADR-0037）：超限直接拒绝新会话而不是排队，空闲超时回收 context。
      # 显式写出来而不是靠代码默认值——默认值漂了这里看不见，而容器只有 mem_limit 1g。
      ZEROCODE_BROWSER_MAX_SESSIONS: ${{ZEROCODE_BROWSER_MAX_SESSIONS:-{BROWSER_MAX_SESSIONS_BAKED}}}
      ZEROCODE_BROWSER_SESSION_TTL_SECONDS: ${{ZEROCODE_BROWSER_SESSION_TTL_SECONDS:-{BROWSER_SESSION_TTL_BAKED}}}
    # sidecar 在容器里，应用 dev 端口发布在宿主上：host-gateway 是它唯一的出口。
    extra_hosts:
      - "host.docker.internal:host-gateway"
    # 运行期根文件系统只读；Chromium 需要的可写路径全在 tmpfs 里。
    read_only: true
    tmpfs:
      - /tmp:size=256m
      - /dev/shm:size=512m
    cap_drop:
      - ALL
    security_opt:
      - "no-new-privileges:true"
    mem_limit: 1g
    cpus: 1.0
    pids_limit: 256
    networks: [default]

volumes:
  pgdata: {{}}

networks:
  default: {{}}
  zcnet:
    name: zcnet          # 应用栈以 external: true 引用它，故由本项目按这个确切名字建出来
"""


def backend_dockerfile(deploy_root: str, mirrors: Mirrors = DEFAULT_MIRRORS) -> str:
    """平台后端镜像：python:3.12-slim + docker CLI/compose/buildx plugin（ADR-0013）。

    WORKDIR 必须等于部署根：settings.py 的 REPO_ROOT 由本文件自身位置推出，在镜像里就是
    WORKDIR，于是 `deploy.apps_root` 留空时的兜底恰好就是同路径挂载点，新建应用直接落到
    共享目录里。写死而部署根是别的值时，兜底会指向容器可写层里一个宿主根本不存在的路径，
    新建的应用看起来成功了、起预览时却必挂。

    部署根经 `ARG DEPLOY_ROOT` 传入（ADR-0034）：默认值是打包时那个，compose 的 build.args
    又把运行期 `.env` 里的同一个变量喂进来，于是挂载与 WORKDIR 只有一个取值来源。

    `mirrors` 只影响两处构建期换源（Debian 源 + docker CE 仓库地址）与 pip 的 index-url；
    三个都不传时本文件与加这个功能之前**逐字一致**。apt 不进 compose 的容器 env：
    它只作用于镜像构建期，容器运行期没有任何一方读它。
    """
    apt_block = _apt_source_block(mirrors.apt)
    pip_block = _pip_index_block(mirrors.pypi)
    # 镜像站按官方约定路径 mirrors/<host>/docker-ce/linux/debian 原样镜像了 docker 的 apt 仓库，
    # 所以换仓库只需换主机名段；gpg 与 deb 两处必须同源，否则密钥来自 A 站、包来自 B 站。
    docker_repo = (f"https://{mirrors.apt}/docker-ce/linux/debian" if mirrors.apt
                   else "https://download.docker.com/linux/debian")
    return f"""# zerocode 平台后端镜像 —— 由 scripts/build_output.py 生成，勿手改
FROM python:3.12-slim
{apt_block}# docker CLI + compose/buildx plugin：平台在容器内通过挂进来的 docker.sock 起应用 dev/prod 栈（ADR-0013）。
# buildx 不能省：compose 找不到 buildx 插件会退回 classic builder，而 classic 不支持 RUN --mount=type=cache，
# SpringBoot 模板的后端 Dockerfile 正是靠 cache mount 复用 m2 依赖，缺它直接构建失败。
RUN apt-get update \\
 && apt-get install -y --no-install-recommends ca-certificates curl gnupg \\
 && install -m 0755 -d /etc/apt/keyrings \\
 && curl -fsSL {docker_repo}/gpg -o /etc/apt/keyrings/docker.asc \\
 && chmod a+r /etc/apt/keyrings/docker.asc \\
 && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \\
{docker_repo} $(. /etc/os-release && echo $VERSION_CODENAME) stable" \\
      > /etc/apt/sources.list.d/docker.list \\
 && apt-get update \\
 && apt-get install -y --no-install-recommends docker-ce-cli docker-compose-plugin docker-buildx-plugin \\
 && rm -rf /var/lib/apt/lists/*

# 部署根由 compose 的 build.args 传入（ADR-0034）；默认值是打包时烘的那个，
# 与 compose 的同路径挂载取自同一个变量，两边不会分家。换根后要重跑 `up -d --build`。
ARG DEPLOY_ROOT={deploy_root}
WORKDIR ${{DEPLOY_ROOT}}

{pip_block}COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
COPY frontend/dist ./frontend/dist

EXPOSE 8100
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8100"]
"""


def dockerignore() -> str:
    """构建上下文瘦身：预置应用源码、网关配置、挂载点与交付文档都不该进镜像层。

    没有它，`docker compose build` 每次会把整个 apps/（含两个应用的全部源码）传给 daemon——
    慢且毫无意义，因为它们运行期靠同路径挂载进容器，不是烘进镜像的。gateway/ 同理：它由
    独立的 compose 项目在宿主上挂载自己的配置文件，镜像是平台后端，不是网关。
    """
    return """# 由 scripts/build_output.py 生成：只让 backend/ 与 frontend/dist 进构建上下文
apps
browser
data
store
gateway
init.sql
apply-init.sh
README-deploy.md
.env
.env.example
**/__pycache__
**/*.pyc
"""


def env_example(deploy_root: str, gateway_domain: str = DEFAULT_GATEWAY_DOMAIN,
                mirrors: Mirrors = DEFAULT_MIRRORS,
                playwright_download_host: str = PLAYWRIGHT_DOWNLOAD_HOST_BAKED) -> str:
    """`.env` 模板：部署根、口令位、端口位，以及打包值可覆盖的 `ZEROCODE_*` 运行期位。

    `DEPLOY_ROOT` 是运行期输入项（ADR-0034）：compose 挂载与镜像 WORKDIR 都读它，所以改这里
    真的能换目录——前提是跟着重跑一次 `up -d --build`。打包时的值只是这里的默认值。
    """
    overlay = _env_example_overlay(gateway_domain, mirrors, playwright_download_host)
    return f"""# 复制成 .env 后按需修改（docker compose 会自动读取同目录 .env）
#   cp .env.example .env

# 部署根：本目录实际所在的绝对路径，同时是容器内路径（同路径挂载，ADR-0013）。
# 打包时烘进去的默认值是 {deploy_root}；改它就能换目录，但**必须重跑 `up -d --build`**
# ——镜像的 WORKDIR 是构建期取的，只 up -d 会让它停在旧根，症状是新建应用落进容器可写层。
# Windows 上走 WSL 部署时，这里填的是 WSL 发行版内部的 Linux 路径，不要填 D:\\（跨系统慢盘）。
DEPLOY_ROOT={deploy_root}

# 平台库口令。POSTGRES_USER 必须是超级用户——平台要建库建角色来供给各应用的分库。
# 口令请只用字母、数字与 - _ ：它会被原样拼进 asyncpg 连接串
# （postgresql+asyncpg://用户:口令@主机:5432/库），含 @ : / # % & ? 会把它截断。
# 注意：首次 up 之后口令写进了 pgdata 卷，改这里不会改库里已有的口令。
POSTGRES_USER=zerocode
POSTGRES_PASSWORD=change-me-please

# 对外端口：控制台 8100；平台库 5433（演示不需要对外，可删掉 compose 里的 ports）
PLATFORM_PORT=8100
PG_PORT=5433
{overlay}"""


def apply_init_sh(deploy_root: str = DEFAULT_DEPLOY_ROOT) -> str:
    """init.sql 执行脚本：LF 行尾（bash 不认 CRLF），ON_ERROR_STOP=1 让失败当场可见。

    它在把 SQL 送进 psql 之前渲染 `{{DEPLOY_ROOT}}` 占位符（ADR-0034），且**只在管道里渲染**——
    包内的 init.sql 必须与源逐字一致，否则下次升级时「包 vs 现场」的差异比对就失去意义。
    """
    token = DEPLOY_ROOT_PLACEHOLDER
    return f"""#!/usr/bin/env bash
# 执行全量 init.sql（可重复执行：只补不覆盖）。用法：bash apply-init.sh
set -euo pipefail
cd "$(dirname "$0")"

: "${{POSTGRES_USER:=zerocode}}"
# 打包时的那个根只作为兜底；真正生效的是部署根 .env 里的 DEPLOY_ROOT（ADR-0034）。
DEPLOY_ROOT="{deploy_root}"
if [ -f .env ]; then
  # 只取需要的两个键，绝不 source 整个 .env（那会把明文口令带进本进程环境）
  POSTGRES_USER="$(grep -E '^POSTGRES_USER=' .env | tail -n1 | cut -d= -f2- | tr -d '\\r' || echo zerocode)"
  DR="$(grep -E '^DEPLOY_ROOT=' .env | tail -n1 | cut -d= -f2- | tr -d '\\r' || true)"
  [ -n "$DR" ] && DEPLOY_ROOT="$DR"
fi
DEPLOY_ROOT="${{DEPLOY_ROOT%/}}"
case "$DEPLOY_ROOT" in
  /*) ;;
  *) printf '✗ DEPLOY_ROOT 必须是 Linux 绝对路径（收到 %s）：它是 init.sql 里 root_path 的唯一来源\\n' "$DEPLOY_ROOT" >&2; exit 1 ;;
esac

printf '→ 对 zerocode 库执行 init.sql（用户 %s，部署根 %s）\\n' "$POSTGRES_USER" "$DEPLOY_ROOT"
# 占位符只在这条管道里渲染，不回写包内文件。sed 用 | 作分隔符：路径里不会出现竖线，斜杠则到处都是。
sed "s|{token}|$DEPLOY_ROOT|g" init.sql \\
  | docker compose exec -T pg psql -U "${{POSTGRES_USER}}" -d zerocode -v ON_ERROR_STOP=1
printf '✓ init.sql 执行完成\\n'
"""


def readme_md(presets, deploy_root: str, generated_at: str, dist_stamp: str,
              gateway_domain: str = DEFAULT_GATEWAY_DOMAIN,
              mirrors: Mirrors = DEFAULT_MIRRORS) -> str:
    app_rows = "\n".join(
        f"| {p.slug} | {p.name} | {p.stack} | `{deploy_root}/apps/{p.slug}` | "
        f"http://<IP>:{p.prod_port} | http://<IP>:{p.dev_port} |"
        for p in presets) or "| （本次未预置应用） | | | | | |"
    slug_list = ", ".join(p.slug for p in presets) or "（无）"
    # 预置应用必须逐个 slug 同步，且把 deploy/ 锚在传输根上排除：包里本来就没有 deploy/，
    # 不排除的话 --delete 会把服务器上现场生成的 dev/prod 口令当成「源里没有的东西」删掉。
    preset_rsync = "\n".join(
        f"rsync -a --delete --exclude '/deploy/' build-output/apps/{p.slug}/ "
        f"{deploy_root}/apps/{p.slug}/"
        for p in presets) or "# （本次未预置应用，跳过这一步）"
    return f"""# zerocode 演示部署包

生成时间：{generated_at}　前端 dist 构建时间：{dist_stamp}
打包时的建议部署根：`{deploy_root}`（真正的部署根在第 1 问里输入，见 §1）
预置应用：{slug_list}　网关域名：`{gateway_domain}`

> 本目录整体是**生成物**（由仓库根的 `build-output.bat` 产出），不要手改其中任何文件——
> 下次打包会整目录重建。要改就改仓库里的 `changelog/`、`app/`、`frontend/`、`gateway/`、
> `scripts/deploy.sh`（交互内核）、`scripts/deploy_lib.sh`（被内核 source 的判定层，桩测全打在它身上）、
> `scripts/deploy.ps1`（Windows 侧的 WSL2 皮）、`scripts/deploy.bat`（双击皮用的入口，只转发不判定），
> 或者改打包脚本——包里的这四份只是拷贝。
> `gateway/certs/`（TLS 私钥）与 `gateway/routes/routes.json`（运行期路由）是
> 服务器现场产物，不在包内。

## 1. 前置条件

- **一台 Linux 机器**，或一台能跑 WSL2 的 Windows 机器（走 §2 的 `deploy.bat` / `deploy.ps1`：
  皮把包推进发行版，再调同一份 `deploy.sh`，部署判定一条都不留在 PowerShell 里，ADR-0035）。
- **Docker 不用预先装好**：`deploy.sh` 第 2 步「环境保障」缺什么装什么——Docker Engine、
  `docker compose` v2 插件、把 daemon 拉起来、（境内机）可选的 registry 镜像加速、把当前用户
  加进 `docker` 组，连 `rsync` 缺了也一并装。它**不装 Docker Desktop**（ADR-0035：它的 license
  与「`docker` CLI 指向谁」都会跟这条路打架）。
- **脚本不认任何命令行参数**（D-11）：`--check`、`--no-build`、`--gateway`、`--takeover`、
  `--fresh-db` 全部改成了屏幕上的问题，敲进去会被当场拒收并逐条说明现在去哪儿答。代价是
  三台机器要答三遍题——`.deploy-answers` 只让后两遍变成一路回车，那是默认值复用，不是无人值守。
- **部署根在部署时输入**（ADR-0034）：上面那行 `{deploy_root}` 只是打包时的建议默认值，
  第 1 问就能改成别的路径。compose 的同路径挂载、`.env` 的 `DEPLOY_ROOT`、init.sql 里预置应用的
  `root_path` 三处跟着这一次输入走（种子段带的是 `{{DEPLOY_ROOT}}` 占位符，起栈前才换成真值）。
  换根的代价不是重打包，而是**重建镜像**（镜像 `WORKDIR` 变了）——脚本自己判定并强制 `--build`；
  这台机器上已经跑过应用时，还要人工核对新根 `apps/` 里既有工程的位置与库里的 `root_path`
  一致（ADR-0013）。
- 服务器能访问 Docker Hub 与 **PyPI**：平台与应用栈的基础镜像（`pgvector/pgvector:pg16`、
  `python:3.12-slim`、`node:20-alpine`、`maven`、`openresty` 等）**在服务器上在线拉取**，
  本包不含镜像 tar；`docker compose build` 还要在镜像里跑 `pip install -r requirements.txt`。
  网络受限时可以在打包时就配好镜像源（`build-output.bat --apt-mirror mirrors.aliyun.com`
  `--pypi-mirror https://mirrors.aliyun.com/pypi/simple`
  `--npm-registry https://registry.npmmirror.com`，三个可只给其一、默认全不给；docker-ce 仓库
  走镜像站的 `/docker-ce/linux/debian` 约定路径），那只是**默认值**：pypi 与 npm 两个源在 `deploy.sh`
  第 4 步还能现场改，改一次 `.env` 就同时管住平台自己与它起出来的每个应用栈；apt 那一项只作用于
  浏览器 sidecar 镜像的**构建期**（compose 里 `browser` 服务的 `build.args`），改了必须重建那个镜像
  才看得见（`docker compose build browser && docker compose up -d browser`）。第 7 步的预热 `docker pull`
  只保平台基础镜像，应用构建期的 pip/npm 依赖是另一条通道。浏览器 sidecar 镜像还要在构建期下载
  Chromium（约数百 MB），默认走 npmmirror；服务器访问不了时用 `--playwright-download-host`
  或 `PLAYWRIGHT_DOWNLOAD_HOST` 环境变量指到可达的地址。
- 网关要用真域名访问时，DNS 里必须有 `*.<域名>`（以及裸域名）的 A/AAAA 记录指向这台服务器，
  且宿主 **80/443 空闲**——中央网关占用它们（ADR-0018）。只用 `http://<IP>:8100` 直连控制台
  时，网关可以先不起（第 4 步回答 n 即可，Windows 侧的建议默认值就是 n）。
- 平台库账号需具备建库建角色权限（镜像默认的 `zerocode` 就是超级用户）。
- 口令请只用字母、数字与 `- _`：它会被原样拼进 asyncpg 连接串，含 `@ : / # % & ?` 会把它截断。
- TLS 证书**不在包里**（私钥绝不进包）：选了起网关，第 9 步缺证书会用包内 `make-cert.sh` 自签；
  生产请换成 acme.sh 签的泛域名真证书（§2 末尾「网关」小节）。

## 2. 首次部署：一条命令

```bash
# 把整个包传到目标机器（放哪儿都行，deploy.sh 以「当前目录就是包目录」为前提）
cd <包所在目录>
bash deploy.sh          # 要 root：机器上有 sudo 会自己提权，没有就 su - 再跑
```

Windows 上做同一件事（不需要预先装 Docker，也不需要预先有发行版）。两种方式等价：

```powershell
# 双击：在资源管理器里双击包根的 deploy.bat（双击 .ps1 只会用编辑器打开，所以才需要这个 .bat）
# 命令行：
powershell -ExecutionPolicy Bypass -File .\\deploy.ps1
```

`deploy.bat` 只是皮的一个启动器：切控制台编码、拉起同目录的 `deploy.ps1`、把退出码原样回传，
结束时 `pause` 让双击的窗口停在能读完的地方。它自己只认 `--no-pause`（给脚本与 CI 用，
与 `build-output.bat` 同一条约定），其余参数一律转给皮——皮按 D-11 当场拒收并说明每个旧 flag
现在的去处，所以 `deploy.bat --fresh-db` 不会静默开始一次交互部署。

皮只负责把环境弄成能跑 Linux 的样子：保证 WSL2 可用、要一个 v2 发行版（v1 没有 systemd，
docker daemon 起不来）、保证发行版 `/etc/wsl.conf` 里 `[boot] systemd=true`、把包推进发行版，
然后调用同一份 `deploy.sh`。要重启 Windows 功能或 `wsl --shutdown` 才能生效时，它**只给指令、
不代劳**（D-10：不自动重启你的机器）。

`deploy.sh` 一共九步，问题按这个顺序问，每步都先把要动什么说出来再动：

| 步 | 问什么 | 你该知道什么 |
|---|---|---|
| 1 新增/升级 | 这次是新增还是升级；部署根（Linux 绝对路径） | 不做自动探测（D-05），误选也不拦（D-07）；唯一后路是写 `.env` 前静默备份 `.env.bak-<UTC>`，只留最近 5 份 |
| 2 环境保障 | 装 Docker Engine + compose v2（境内机再问要不要 registry 镜像加速）、要不要把你加进 `docker` 组 | 每条安装命令都先原样打印再执行；组变更要**重新登录**才生效（本次部署已以 root 在跑，不受影响） |
| 3 腾地方 | 新增模式下机器上已有 `pgdata` 卷：要连库一起重做吗 | 要删得逐字输入 `yes`（老 `--fresh-db` 的去处）。升级模式不问、也永不删卷 |
| 4 运行参数 | `POSTGRES_USER`、`POSTGRES_PASSWORD`、`PLATFORM_PORT`、`PG_PORT`、`ZEROCODE_PREVIEW_HOST`、PyPI / npm / apt 三个镜像源、要不要起中央网关（答「起」才接着问网关域名与 `ZEROCODE_SSL_ENABLE`） | 口令**只进 `.env`**，不进 `.deploy-answers`；升级时直接回车就沿用 `.env` 里的既有值。`ZEROCODE_PREVIEW_HOST` 的打包默认值是 `localhost`（本机演示用的），脚本发现它没被改就换成探测到的宿主 IP |
| 5 宿主端口 | 控制台端口（起网关时还有 80/443）被占了怎么办 | 占用者是**容器**才问「stop 它腾地方？」（只 stop 不删，`docker start <名字>` 可恢复）；是**宿主进程**就报 PID 让你自己确认再 kill。脚本不代劳 kill，也不劝你改 `PLATFORM_PORT` 买单 |
| 6 落盘 | 不问：合并 `.env`、铺包或按升级合并、把答案写进 `.deploy-answers` | 升级模式只镜像 `backend/`、`browser/`、`frontend/` 三棵，八项现场产物一个不碰（§4） |
| 7 起平台栈 | 不问 | 先核 compose 里声明了 `browser` 服务，再预热 `docker pull` 基础镜像——拉不动就当场停，不等 build 卡二十分钟；`DEPLOY_ROOT` 变了自动带 `--build` |
| 8 重放与自检 | 不问：`bash apply-init.sh`，然后从后端容器实探探针与 sidecar | `compose 里有 browser 服务` 不等于 `Chromium 起得来`，自检探的是 `$ZEROCODE_BROWSER_URL/healthz` |
| 9 中央网关 | 升级时问「要按新的 `gateway/docker-compose.yml` 重建吗」 | 缺证书就自签（生产换 acme.sh）；`gateway/certs/`、`gateway/routes/`、`gateway/.env` 是现场产物，不覆盖也不删 |

`.env` 由脚本从包里的 `.env.example` 起步生成、再合并本次答案，既有键与注释一律保留——**不要**
手工 `cp .env.example .env`，那等于把服务器现场填的配置顶掉（第 3 步的删库闸门也救不了你，
因为 pgdata 卷里的口令不会跟着 `.env` 改）。

跑完最后打印控制台地址、预览地址基座、本次部署根与应答文件位置。打开控制台：
`http://<服务器IP>:<PLATFORM_PORT>`（默认 8100）。

浏览器 sidecar 是 Agent 的 `preview_page` 工具的执行环境：它**不发布宿主端口**，只有
compose 网络里的 backend 能访问（`ZEROCODE_BROWSER_URL`，打包默认 `http://browser:8101`）；
它再经 `host-gateway` 访问宿主上发布的应用 dev 端口（`ZEROCODE_BROWSER_TARGET_HOST`，
默认 `host.docker.internal`；这个 host 必须同时在 sidecar 的 `BROWSER_ALLOWED_HOSTS` 白名单里）。
sidecar 未启动或崩溃时，`preview_page` 会返回「浏览器检查服务不可用」，
预览的启动/日志/重启不受影响。截图落在 `<部署根>/data/browser_shots/`（运行期数据，
不在包里，也不要被同步命令的 `--delete` 扫掉）。

可交互会话（Agent 的 `browser_open` / `browser_step` / `browser_close`）是**有状态**的：活页面
（Playwright context）留在 sidecar 进程内存里，后端只持绑定与收口责任。两个键管着它的上限，
`.env.example` 与 compose 里都显式写着默认值——靠代码默认值的话，默认值漂了运维这边看不见：

- `ZEROCODE_BROWSER_MAX_SESSIONS`（默认 2）：同时能留几个活页面。**超限是拒绝而不是排队**——
  新的 `browser_open` 当场拿回一句「浏览器服务已满」，Agent 会先 `browser_close` 收口或稍后重试，
  不会把用户晾在一个卡住的请求上。同一个 run 只保留一个会话（重复 open 会先关旧的），
  所以正常情况下这个数用不尽；撞限通常是上一条 run 被杀、会话还没被关掉。
- `ZEROCODE_BROWSER_SESSION_TTL_SECONDS`（默认 120）：一个会话**空闲**多久后被回收（巡检每 10s 一跳）。
  打开中的等待与每一步动作都会把空闲时钟拨回现在，所以刚开好的会话不会下一步就撞上 TTL。
  正常路径由后端在 run 收口时发 `DELETE` 关掉，TTL 兜的是容器重启、后端被杀那类没人关的 context。

调大这两个数要连着看 `browser` 服务的 `mem_limit: 1g`：一个活会话就是一个 Chromium context，
两个数一起放大等于在 1g 里攒 OOM。调小 TTL 是另一头的代价——聊到一半会话先没了，Agent 拿到的
那句是「浏览器会话已结束，请重新 browser_open」，步骤卡会把那一步画成失败。

### 网关（通配域名 HTTPS）

包的 `gateway/` 下是一个**独立的 OpenResty compose 项目**：终止 TLS，并按
`https://<slug>.<域名>` 反代到应用的 prod 宿主端口。第 4 步回答「起」就会把它带上，
下面是它单独成立时的手工路径（以及换证书时的操作）。

先签证书，输出必须是 `gateway/certs/gateway.crt` 与 `gateway/certs/gateway.key`（文件名与
域名无关，换域名/换 CA 都不用改 nginx 配置）：

- **生产（推荐）**：用 acme.sh 走 DNS-01 签泛域名，再用 `--install-cert` 写到上面这对文件名。
  通配证书要同时申请 `-d '<域名>' -d '*.<域名>'`，DNS 里也要有对应的 `*` 解析。
- **内网/本机自签**：第 9 步缺证书时脚本就跑的这条命令（在 openresty 镜像里跑 openssl；
  自签 CA 需要客户端手动信任）：
  ```bash
  docker run --rm -v "$PWD/gateway/certs:/out" -v "$PWD/gateway/make-cert.sh:/mk.sh" \\
    openresty/openresty:alpine sh -c "tr -d '\\r' < /mk.sh > /tmp/mk.sh; sh /tmp/mk.sh '{gateway_domain}'"
  # 然后把 gateway/certs/ca.crt 导入客户端「受信任的根证书颁发机构」
  ```

证书就位后启动网关：

```bash
cd <部署根>
docker compose -f gateway/docker-compose.yml up -d
docker compose -f gateway/docker-compose.yml ps
```

网关默认从 `{GATEWAY_UPSTREAM_BAKED}`（Linux 的 docker0 网关）连各应用的 prod 宿主端口，
这是打包时烘进 `gateway/docker-compose.yml` 的默认值。**不要**指望 `extra_hosts` 里的
`host.docker.internal`：nginx 的变量式 `proxy_pass` 只查 `resolver`（Docker 内嵌 DNS），
不读 `/etc/hosts`，这个名字在 Linux 上解析不了——症状是所有应用域名 502、网关日志里
`host.docker.internal could not be resolved (3: Host not found)`。宿主 docker0 不是
`{GATEWAY_UPSTREAM_BAKED}` 时，在 `gateway/.env` 里设 `{GATEWAY_UPSTREAM_ENV}=<实际网关 IP>`
再重建网关；本机 Docker Desktop 演示才用 `host.docker.internal`。

80 上的请求会 301 到 https；两个端口都必须空闲。

### 网关域名与路由表

控制台里 `https://{{slug}}.<域名>` 这个地址由 `app/deploy/nics.py` 在容器内读
`ZEROCODE_GATEWAY_DOMAIN` 拼出，打包时烘进的默认值是 `{gateway_domain}`。

换域名要**同时**改两处，两边必须一致：部署根 `.env` 的 `ZEROCODE_GATEWAY_DOMAIN`，以及
`gateway/.env` 里的同名变量。`gateway/.env` 是可选的——不建就用打包时烘进
`gateway/docker-compose.yml` 的默认值；要改就从 `gateway/.env.example` 复制。两边不一致的症状
是「控制台显示的地址看着正常、点进去全 404」（ADR-0016），日志里没有一行指向真因。

路由表是平台写给网关的唯一契约：后端把 `status_prod=='running'` 的应用投影成
`{{slug: prod_port}}` 写进 `<部署根>/gateway/routes/routes.json`。平台 compose 已把该目录
同路径挂进 backend（`ZEROCODE_GATEWAY_ROUTES` 指向文件），网关以只读方式挂同一个宿主目录，
所以发布/停机后路由自动生效，不需要重启网关。包里的初值是空对象 `{{}}`。

网关或证书没就位之前，控制台里应用地址看着正常、点进去全 404。

域名暂时不能对外提供时（例如境内服务器上域名还没备案、443 的 TLS 握手被中间设备重置），
**不必拆网关**：把部署根 `.env` 的 `ZEROCODE_SSL_ENABLE` 设成 `false` 再 `docker compose up -d`
（第 4 步也会问这一项），控制台、发布记录与应用 README 里的地址就改成
`http://<ZEROCODE_PREVIEW_HOST>:<prod 端口>`直连（宿主防火墙要放行应用端口段，默认 8210-8399）。
它只改地址投影，网关照常跑；备案或证书就绪后改回 `true` 即恢复域名地址。
dev 预览地址本来就是 IP 直连，不受这个开关影响。

**第一个注册的账号自动成为创始人**，并接管所有预置应用（它们的 `owner_user_id` 在 init.sql 里
刻意留 NULL）。演示就注册 `tsadmin` / `tsadmin`。

## 3. 预置应用

| slug | 名称 | 技术栈 | 工程目录 | 正式环境 | 预览环境 |
|---|---|---|---|---|---|
{app_rows}

预置应用是**未发布**状态，而且 init.sql 只种了元数据行、**没有数据库凭据**（凭据一律不进包）。
所以顺序是固定的：**先点一次「预览」**——平台会现场供给它的分库（`{{slug}}_dev`）、建角色、
跑 schema 投影、起 dev 栈，并把凭据登记进平台库；**之后**才能点「发布」——正式环境复用 dev
的凭据，直接点发布会被 500 拦下并提示「请先在预览环境启动一次再发布」。
首次预览要在容器里装依赖（fastapi 栈约 1~3 分钟）；发布则在服务器上现场 build 应用镜像
（几分钟，Java 栈更久）。

## 4. 以后每次发布

开发机重跑打包，把新的 `build-output/` 传到部署根的**同级**目录（或随便一个新目录），然后
在服务器上只做一件事：

```bash
cd <新包所在目录>
bash deploy.sh          # 第 1 问选 upgrade
```

升级模式的动作是**收敛过的**（D-06），逐条列在这里，方便你核对它到底动了什么：

- 只**镜像**三棵目录：`backend/`、`browser/`、`frontend/`——这三棵里没有服务器现场产生的东西，
  所以带 `--delete`；全场只有这三棵带 `--delete`。
- 顶层生成物与 `gateway/` 的配置/脚本按文件名**覆盖**，不删别的：`docker-compose.yml`、`init.sql`、
  `apply-init.sh`、`deploy.sh`、`deploy_lib.sh`、`deploy.ps1`、`deploy.bat`、`.env.example`、
  `README-deploy.md`、`.dockerignore`，以及
  `gateway/nginx.conf`、`gateway/docker-compose.yml`、`gateway/make-cert.sh`、
  `gateway/make-cert.ps1`、`gateway/404.html`、`gateway/lua/router.lua`、`gateway/.env.example`。
- 八项现场产物一个不碰（下表）。
- `.env` 合并写：只动本次问到的键，既有键与注释原样保留；动之前静默备份 `.env.bak-<UTC>`（留 5 份）。
- 起栈：`DEPLOY_ROOT` 没变就 `docker compose up -d`，变了自动 `--build`；随后 `bash apply-init.sh`
  重放全量 init.sql（只补不覆盖）。

`apps/`、`data/`、`store/`、`gateway/certs/`、`gateway/routes/`、`gateway/.env`、`.env`、
`.deploy-answers` 加 `pgdata` 卷，**任何一步都不许带 `--delete` 去碰**：包里刻意不含它们。
这也回答了「为什么不能用一条 `rsync -a --delete build-output/ <部署根>/` 搞定」——`--delete` 的语义是
「删掉目标里源没有的东西」，一条命令就会在**每次发布**时把这八类现场产物全删掉。这不是理论风险：
`.env`、`apps/<slug>/deploy/`、`data/`、`store/` 与 `gateway/certs/` 里的内容在首次部署跑完后就已经
存在，所以**第一次**执行就会全部中招；只有「服务器上新建的应用」那一项取决于你到底建没建：

| 会被删掉的 | 后果 |
|---|---|
| `<部署根>/.env` | 里面是 `POSTGRES_PASSWORD`；紧接着的 `docker compose up` 会被 `${{POSTGRES_PASSWORD:?}}` 当场拒收。若照 §2 重新生成 `.env` 并填了**新**口令，pgdata 卷里仍是旧口令 → 后端连不上库，且现象与「口令写错」无法区分 |
| `<部署根>/apps/<服务器上新建的应用>/` | 控制台建的应用工程整棵消失，而 `workspace.root_path` 行还在库里（init.sql 只补不覆盖），列表里点进去全挂 |
| `<部署根>/apps/<slug>/deploy/` | dev/prod 的 `.env` 明文口令与已渲染 compose；丢了之后预览/发布会静默走偏 |
| `<部署根>/data/`、`<部署根>/store/` 下的内容 | 运行期落盘的技能库与商店包（本包这两个目录只有 `.gitkeep`） |
| `<部署根>/gateway/certs/` | 网关的 TLS 证书与私钥；删了网关下次重启直接起不来，重签期间 HTTPS 全断 |
| `<部署根>/gateway/routes/` | 运行期路由表；删了要等下一次发布/停机动作才会重建，期间所有应用域名 404 |
| `<部署根>/gateway/.env` | 网关侧域名覆盖；丢了会退回打包默认域名，与平台不一致时全 404 |
| `<部署根>/.deploy-answers` | 下次部署的默认值没了一整份，一路回车变成逐题重答（不致命，但你会以为是新机器） |

预置应用的源码**不在**升级模式的镜像范围内——`apps/` 整棵在保护名单里，脚本不替你做这个决定。
只有当你确实改了预置应用、要把它推上服务器时，才手动逐 slug 同步，并把现场生成的 `deploy/`
锚在传输根上排除（应用工程里也可能有别处叫 `deploy` 的目录，少一个前导斜杠就是两种语义）：

```bash
{preset_rsync}
```

`init.sql` 每次都是**全量**的（由 `changelog/` 全部迁移按序合成），所以不需要记「上次发到哪了」；
它的语义是「只补不覆盖」——服务器上现场产生的对话、新建的应用、改过的配置都不会被洗掉。
上面这套合并动作，就是让**文件层**也遵守同一条语义。

## 5. 已知代价（不是 bug）

- **`apply-init.sh`（第 8 步会跑它）是每次发布不可省的唯一 schema 通道**：镜像里根本没有 `changelog/`
  （`docker compose build` 只 COPY 了 `backend/app` 与 `frontend/dist`），所以 backend 首启时
  `app/db.py::run_migrations` 面对不存在的目录会拿空迭代器**静默空转**，不报错也不建表。
  忘了跑它的后果是「新表没出现、但日志里一切正常」。刻意不把 `changelog/` 烘进镜像：原始迁移
  没有幂等守卫（守卫是合成期补的），在 init.sql 已建好的库上重放会因 `CREATE TABLE` 撞名而让
  backend 启动失败（ADR-0014）。同理，`schema_migration` 表在演示部署上永远是空的，这是预期行为。
- **`llm.*` 会补回一条属主为空的平台默认行**：为使重放不依赖「诞生当时」的唯一约束，合成时把
  `ON CONFLICT (列…) DO NOTHING` 归一成无目标形态；而 `app_config` 的仲裁索引后来变成了
  `(owner_user_id, key)`，创始人事务把 `llm.*` 划归自己后，NULL 属主那一行就「缺失」了，
  重放会把它按默认值补回来。这不会覆盖任何人：配置读取是「用户自己的行 覆盖 平台行」，
  创始人看到的仍是自己填的密钥；新用户看到空密钥则与全新安装一致。
- **后端容器持有宿主 docker.sock**，等价于宿主 root 权限。演示机不要跑不可信工作区。
- **浏览器 sidecar 运行 headless Chromium**：镜像约 1GB，compose 限 1g 内存 / 1 CPU；
  它没有 docker.sock、没有平台库凭据、根文件系统只读。页面文本、控制台与失败请求在进模型前
  会做凭据遮断，但**截图是像素，无法可靠遮断**——演示机仍只应开给可信工作区。
- **网关是独立 compose 项目**：平台起栈只管平台，网关要 `-f gateway/docker-compose.yml`
  单独启停（第 9 步会做，升级时是否重建会问你）；它占用宿主 80/443，且证书/私钥只在服务器
  现场生成（`gateway/certs/` 不进包）。acme.sh 续期后要重启（或 reload）网关让 nginx 读到新证书。
- **部署根可以现场输入，但换根有代价**：镜像 `WORKDIR` 与 `ZEROCODE_GATEWAY_ROUTES` 在构建期定值，
  换根必然触发一次 `--build`（脚本自动判定，见第 7 步）；机器上已经跑过应用时还要人工核对
  `apps/` 里既有工程与库里的 `root_path` 一致。
- **没有无人值守入口**（D-11 的代价）：批量部署几台机器就要答几遍题。`bash deploy.sh < /dev/null` 也变不出无人值守——一路回车取的是默认值，而平台库口令没有默认值可取，读不到就停（这是刻意的：口令只认现场输入）。将来真需要非交互，
  给 `deploy.sh` 一个 `--answers` 文件重放入口，别往回加行为 flag。
- **Windows 侧有两处未验证的赌注**：WSL 发行版内 `systemd` 拉起 docker daemon 的可用性，以及
  `/var/run/docker.sock` 与 external 网络 `zcnet` 在 WSL2 里的行为。脚本的分支逻辑有桩测，这两条只有
  实跑才算数——第一次用 `deploy.ps1` 时请留人在旁边。
- `deploy.apps_root` 留空时兜底为 `REPO_ROOT/apps`，而镜像的 `WORKDIR` 就是部署根，故兜底值
  恰好等于同路径挂载点 `<部署根>/apps`，不需要在控制台里额外设。只有当你把应用工程
  放到部署根之外时，才需要去「设置 → 部署环境」里显式指定应用根目录。

## 6. 排障

| 现象 | 原因与处置 |
|---|---|
| 控制台打得开但应用列表空 | 还没注册账号，或注册的不是第一个账号（预置应用归创始人） |
| 点预览报「dev 分库供给失败」 | 平台库账号无建库权限，或 pg 容器没起：`docker compose logs pg` |
| 点预览长时间无响应 | 首次要在容器内装 npm/pip 依赖；看控制台「查看日志」 |
| 预览等满健康窗口报「未就绪」，而容器日志里前端明明已 ready | 探针连的是 `.env` 的 `ZEROCODE_PREVIEW_PROBE_HOST`（默认 `host.docker.internal`，需要 compose 里 backend 的 `extra_hosts: host-gateway`）。改成 `127.0.0.1` 或删掉 extra_hosts 就会全轮连接拒绝——`deploy.sh` 第 4 步会把这两种填法当场拦下 |
| 控制台里「打开/复制预览地址」指向 localhost | 那是 `ZEROCODE_PREVIEW_HOST` 的打包默认值（本机演示用的）。第 4 步改成服务器 IP 或域名；另外宿主防火墙与安全组要放行应用端口（默认 8210-8399） |
| 应用栈起不来、报路径不存在 | 部署根与容器内路径不一致（ADR-0013）：核对 `.env` 的 `DEPLOY_ROOT`、实际目录、容器里 `printenv DEPLOY_ROOT` 三者；第 7 步的自检会打印这一项 |
| 发布后新表/新列没出现 | 没跑 `apply-init.sh`（升级/新增的第 8 步会自动跑）——镜像里没有 changelog，backend 不会自己补 schema |
| 拉镜像超时 / 第 7 步报「基础镜像拉不动」 | 服务器访问不了 Docker Hub。第 7 步停下来后会**当场问**要不要配 registry mirror（写 `/etc/docker/daemon.json` 并重启 docker，然后重拉）；机器上早就装着 docker 时也要走这一步——环境保障那步的加速提问只在真的要装 docker 时才出现。也可以手工写 daemon.json 后 `systemctl restart docker` 再重跑，或换可达网络。这里停下来是刻意的：让它跑到 build 里卡二十分钟不报错更难查 |
| 发布报 `docker compose -f …prod-compose.yml… 超时（600s）`、记账停在 build 步 | 应用的 prod 镜像要在**构建期**装依赖（`pip install` / `npm install`），境内机直连官方源会等到超时——dev 预览正常不代表发布能过，两条通道不同。在第 4 步改 `ZEROCODE_PYPI_MIRROR` 与 `ZEROCODE_NPM_REGISTRY`，平台会把同一份源经 `build.args` 注进每个应用的 prod 构建（ADR-0025）；`deploy.build_timeout` 调大只是把 10 分钟换成 30 分钟，不是修法 |
| 控制台里应用地址点进去全 404 | 逐项核对：① 网关起了吗（`docker compose -f gateway/docker-compose.yml ps`）；② 平台 `.env` 与 `gateway/.env` 的域名是否一致；③ 应用 prod 是否 `running`（进没进 `gateway/routes/routes.json`）；④ 浏览器是否信任了自签 CA |
| 域名打不开（未备案 / 443 被拦），想先走 IP | 把部署根 `.env` 的 `ZEROCODE_SSL_ENABLE` 设成 `false` 并 `docker compose up -d`：控制台、发布记录、应用 README 的应用地址一起改成 `http://<ZEROCODE_PREVIEW_HOST>:<prod 端口>`，网关不用停；宿主防火墙要放行应用端口段（默认 8210-8399）。备案或证书就绪后改回 `true` |
| Agent 报「浏览器检查服务不可用」 | 九成是 sidecar 根本没部署：宿主的 `docker-compose.yml` 比包旧（只重传 `backend/` 时的常态），整块没有 `browser` 服务，后端于是回落连容器自己的 `127.0.0.1:8101`。先 `grep -c browser docker-compose.yml`——为 0 就是这一种。已在跑的用 `docker compose ps browser` 看是否反复重启、`docker compose logs browser` 看 Chromium 启动错误。sidecar 不发布宿主端口，别从宿主访问 8101；若改过 `ZEROCODE_BROWSER_TARGET_HOST`，确认同一 host 已加进 `.env` 的 `BROWSER_ALLOWED_HOSTS`（默认只放行 `host.docker.internal,127.0.0.1,localhost`）。`bash deploy.sh` 现在两道都拦：预检要求 compose 声明 `browser` 服务，自检要求后端容器里探得通 `/healthz` |
| `preview_page` 能开页面但截图为空 / 视觉诊断为「未启用」 | 图像缺失是截图落盘失败（看后端日志与 `<部署根>/data/browser_shots/` 权限）；「未启用」表示当前会话模型没有声明视觉能力，属正常降级 |
| Agent 反复说「浏览器服务已满」/「浏览器会话已结束」 | 前者是活会话上限 `ZEROCODE_BROWSER_MAX_SESSIONS`（默认 2）用尽：超限的新会话被直接拒绝而不是排队，先 `docker compose logs browser` 看还留着几个没关的 context，别一上来调大——容器只有 `mem_limit: 1g`。后者多半是 `ZEROCODE_BROWSER_SESSION_TTL_SECONDS`（默认 120）到点回收，或 sidecar 中途重启过（内存里的会话全丢，重启容器不会「接回」它们）；两种都是正常降级出口，Agent 会重新 `browser_open`，用户数据不受影响 |
| 应用域名 502 Bad Gateway | 先看 `docker compose -f gateway/docker-compose.yml logs gateway`：出现 `host.docker.internal could not be resolved` 就是上游地址在 Linux 上解析不了（nginx 不读 `/etc/hosts`），把 `gateway/.env` 的 `ZEROCODE_GATEWAY_UPSTREAM` 设成宿主 docker0 网关（默认 `172.17.0.1`）后重建网关；若日志是 `connection refused`，核对应用 prod 栈与 `routes.json` 的端口是否一致 |
| 网关容器起不来 / 反复重启 | `docker compose -f gateway/docker-compose.yml logs gateway`：多为 `gateway/certs/gateway.crt|key` 缺失，或 80/443 被占用 |
| 发布成功但域名仍是 404 | 检查 `<部署根>/gateway/routes/routes.json` 里有没有该 slug；没有就看 backend 日志里 `gateway routes.json 刷新失败` 一行（该失败只告警、不阻断发布） |
| 端口被占用 | 第 5 步会点名占用者并给出处置：是**容器**就问你要不要 stop（只 stop 不删，`docker start <名字>` 可恢复，老 `--takeover` 的去处）；是**宿主进程**只报 PID，要你确认它不是别的服务后自己 kill。stop 完端口仍被占说明占用者不止一个，按它打印的 PID 手工腾。不建议改 `PLATFORM_PORT`：它是对外的控制台地址，还要与云安全组放行的一致；应用端口在控制台改不了，需重打包 |
| 想连库一起重做（换口令 / 清数据） | 选**新增**：第 3 步发现已有 `pgdata` 卷时会要求逐字输入 `yes` 才删（老 `--fresh-db` 的去处）。升级模式不问也不删卷——只改 `.env` 里的口令是**无效**的，卷里仍是旧口令 |
| `deploy.sh` 说「缺 xx——这份 lib 比 deploy.sh 旧」 | 只传了内核没传判定层（或反之）。三份脚本必须整套上传：包里那份是仓库 `scripts/` 的拷贝，别手改 |
| Windows 侧：发行版里 docker daemon 起不来 | `/etc/wsl.conf` 缺 `[boot] systemd=true`。跑 `deploy.ps1` 会代劳追加，但改完要**你手工**执行 `wsl --shutdown` 再重跑（脚本不自动重启机器，D-10） |
| Windows 侧：部署根填了 `D:\\...` | 皮把包推进 **WSL 发行版内部**，部署根要 Linux 绝对路径（如 `/opt/zerocode`）。想从 Windows 资源管理器看那些文件，用 `\\\\wsl.localhost\\<发行版>\\opt\\zerocode` |

> 打包期的端口检测只管两件事：预置应用之间不得互撞 `prod_port`/`dev_port`（它们是 UNIQUE 列），
> 且同一个应用的 prod 与 dev 不得相同。它**不**探测服务器上已占用的端口，也不管预置应用与
> `PLATFORM_PORT`/`PG_PORT` 是否相撞（默认值 8210+ 与 8100/5433 差得够远）。真撞上了会由
> 第 5 步的宿主端口预检点名，或最终在 `docker compose up` 时报 `port is already allocated`。

"""


# ---------------------------------------------------------------- 复制与装配

def _ignore_names(exclude_dirs: frozenset):
    def _ignore(_dir, names):
        return [n for n in names
                if n in exclude_dirs or Path(n).suffix in EXCLUDE_SUFFIXES]
    return _ignore


def _count_files(root: Path) -> int:
    return sum(1 for p in root.rglob("*") if p.is_file())


def copy_preset_app(src: Path, dst: Path) -> int:
    """复制一个预置应用的源码（剔除 deploy/ 与依赖目录），返回文件数。

    出包前扫两遍兜底：`deploy/` 目录与任何 `.env` 文件都不许出现在包里——那是明文口令，
    一旦跟着包上传到服务器就等于把凭据交出去。
    """
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=_ignore_names(EXCLUDE_DIRS))
    if (dst / "deploy").exists():
        raise BuildError(f"预置应用里仍带着 deploy/（凭据禁区）：{dst}")
    leaked = [str(p.relative_to(dst)) for p in dst.rglob(".env")]
    if leaked:
        raise BuildError(f"预置应用里混入了凭据文件，拒绝出包：{dst} → {leaked}")
    return _count_files(dst)


def copy_backend(repo_root: Path, out_backend: Path, deploy_root: str,
                 mirrors: Mirrors = DEFAULT_MIRRORS) -> int:
    """复制平台后端源码（app/ + requirements.txt）并写 Dockerfile。

    刻意不复制 changelog/：镜像里的 run_migrations 找不到该目录会拿空迭代器静默空转，
    schema 的唯一通道是手工执行 init.sql（ADR-0014）。把 changelog 烘进去反而有害：
    原始迁移没有幂等守卫（守卫是合成期补的），在 init.sql 已建好的库上重放会因
    CREATE TABLE 撞名而让 backend 启动失败。
    """
    src_app = repo_root / "app"
    if not src_app.is_dir():
        raise BuildError(f"仓库里找不到后端源码目录：{src_app}")
    req = repo_root / "requirements.txt"
    if not req.is_file():
        raise BuildError(f"仓库里找不到 requirements.txt：{req}")
    if out_backend.exists():
        shutil.rmtree(out_backend)
    out_backend.mkdir(parents=True)
    shutil.copytree(src_app, out_backend / "app", ignore=_ignore_names(BACKEND_EXCLUDE_DIRS))
    shutil.copyfile(req, out_backend / "requirements.txt")
    _write(out_backend / "Dockerfile", backend_dockerfile(deploy_root, mirrors))
    return _count_files(out_backend)


BROWSER_LF_NAMES = frozenset({"Dockerfile"})
BROWSER_LF_SUFFIXES = frozenset({".py", ".txt", ".yml", ".yaml"})


def copy_browser(repo_root: Path, dst: Path) -> int:
    """复制浏览器 sidecar 源码（browser/），返回文件数。

    与网关配置同理，入包文件缺失时直接拒包：平台 compose 会在服务器上 build 这个目录，
    缺一个文件只会变成「镜像构建失败」，把打包期就能发现的问题拖到部署现场。

    Dockerfile / compose / Python 一律剥掉 CRLF（与 deploy.sh 同口径）：Windows 上
    `core.autocrlf=true` 检出的副本自带 \\r，而 Dockerfile 里「续行反斜杠 + \\r」会让那条
    RUN 断成两条指令。`.bat` 与 `.md` 原样保留——cmd.exe 对只有 LF 的 goto 标签并不可靠。
    """
    src = repo_root / "browser"
    missing = [rel for rel in BROWSER_REQUIRED if not (src / rel).is_file()]
    if missing:
        raise BuildError(f"浏览器 sidecar 源码缺 {missing}（{src}）："
                         f"preview_page 与可交互走查都依赖它，缺文件不出包")
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=_ignore_names(BROWSER_EXCLUDE_DIRS))
    for path in dst.rglob("*"):
        if path.is_file() and (path.name in BROWSER_LF_NAMES
                               or path.suffix.lower() in BROWSER_LF_SUFFIXES):
            raw = path.read_bytes()
            if b"\r\n" in raw:
                path.write_bytes(raw.replace(b"\r\n", b"\n"))
    return _count_files(dst)


def _bake_fallback(text: str, token_re: re.Pattern, env_name: str, value: str) -> str:
    """把 compose 里 `${ENV:-任意默认值}` 的默认值换成打包值，命中数不是 1 就拒包。"""
    new, hits = token_re.subn(lambda _m: "${" + env_name + ":-" + value + "}", text)
    if hits != 1:
        raise BuildError(
            f"gateway/docker-compose.yml 里 `${{{env_name}:-…}}` 应恰好出现 1 次，"
            f"实际 {hits} 次——打包器无法把它烘成默认值。")
    return new


def gateway_compose_text(text: str, gateway_domain: str) -> str:
    """把网关 compose 的两个 fallback 烘成打包值（ADR-0018）。

    网关是一个独立 compose 项目，它的 `.env` 在 `gateway/` 下而不是部署根。不烘默认值时：
    - 域名：自定义域名必须额外记得 `cp gateway/.env.example gateway/.env`，忘掉的表现是
      「平台地址是 <自定义域名>、网关仍按 zerocode.com 判 Host」——控制台看着正常，点进去全 404；
    - 上游：`host.docker.internal` 在 Linux 上只有 /etc/hosts 认识，nginx 的变量式
      proxy_pass 只查 resolver，于是所有已路由应用 502。包里必须换成 IP 字面量
      （docker0 默认网关）；仓库版本保留 host.docker.internal 供本机 Docker Desktop 演示。

    判定域故意收得很窄（只认 `${ENV:-…}` 这一种写法），且必须各自恰好命中一次：
    源文件将来若改成裸值或改写变量名，这里直接拒包，而不是产出一份默认值不对的包。
    """
    text = _bake_fallback(text, _GATEWAY_DOMAIN_TOKEN_RE, GATEWAY_DOMAIN_ENV, gateway_domain)
    return _bake_fallback(text, _GATEWAY_UPSTREAM_TOKEN_RE, GATEWAY_UPSTREAM_ENV,
                          GATEWAY_UPSTREAM_BAKED)


def gateway_env_example(gateway_domain: str) -> str:
    """`gateway/.env.example`：只在换域名/改上游时才需要复制成 `gateway/.env`。

    与平台 `.env.example` 相反，这里必须写明「本文件在 gateway/ 下」：gateway 是独立 compose
    项目，`docker compose -f gateway/docker-compose.yml up -d` 读的是 gateway/.env，不是部署根
    的 .env。两处域名不一致的症状正是 ADR-0016 记过的「地址正常、点进去全 404」。
    上游那一段同理必须写明「Linux 用 IP、本机才用别名」，否则 502 会以「网关坏了」的面目出现。
    """
    return f"""# gateway 是独立的 compose 项目：本文件要复制成同目录的 .env 才会被读取
# （不是部署根下的 .env）。打包时域名已烘进 docker-compose.yml 的默认值，
# 只有要用另一个域名时才需要：cp .env.example .env 后改下面这一行，
# 并同步修改部署根 .env 里的同名变量——两边不一致会让应用域名全部 404。
{GATEWAY_DOMAIN_ENV}={gateway_domain}

# 网关从哪个宿主地址连应用 prod 端口。包里默认 {GATEWAY_UPSTREAM_BAKED}（Linux 的 docker0
# 网关）：nginx 的变量式 proxy_pass 只查 Docker DNS、不读 /etc/hosts，而 extra_hosts 只写
# hosts，所以 host.docker.internal 在 Linux 上会 NXDOMAIN——症状是所有应用域名 502，
# 网关日志里「host.docker.internal could not be resolved」。
# 宿主自定义过 docker0 时改成实际网关 IP；本机 Docker Desktop 演示才用 host.docker.internal。
# 不要用 127.0.0.1：桥接容器里的回环是容器自己，那里没有应用在监听。
{GATEWAY_UPSTREAM_ENV}={GATEWAY_UPSTREAM_BAKED}
"""


def copy_gateway(repo_root: Path, dst: Path, gateway_domain: str) -> int:
    """复制网关配置到交付包（ADR-0018），返回文件数。

    不带 `certs/` 与 `routes/`（见 GATEWAY_EXCLUDE_DIRS），但把两个目录**建出来**：
    compose 把 `./certs`、`./routes` 以 bind mount 挂进容器，目录缺失时 Docker 会以 root
    属主自动创建，之后宿主用户再签证书/改路由就要 sudo；包里先放好 `.gitkeep` 可以避免。
    `routes.json` 的初值是空对象——它是运行期投影，不是打包输入。
    """
    src = repo_root / "gateway"
    for rel in GATEWAY_REQUIRED:
        if not (src / rel).is_file():
            raise BuildError(f"网关配置缺 {rel}（{src}）：网关已纳入交付包，缺文件不出包")
    if dst.exists():
        shutil.rmtree(dst)

    def _ignore(_dir, names):
        return [n for n in names
                if n in GATEWAY_EXCLUDE_DIRS or n in GATEWAY_EXCLUDE_FILES
                or Path(n).suffix in EXCLUDE_SUFFIXES]

    shutil.copytree(src, dst, ignore=_ignore)
    _write(dst / "docker-compose.yml",
           gateway_compose_text((dst / "docker-compose.yml").read_text(encoding="utf-8"),
                                gateway_domain))
    _write(dst / "certs" / ".gitkeep", "")
    _write(dst / "routes" / "routes.json", "{}\n")
    _write(dst / "routes" / ".gitkeep", "")
    _write(dst / ".env.example", gateway_env_example(gateway_domain))
    return _count_files(dst)


def copy_deploy_scripts(repo_root: Path, out: Path) -> None:
    """把仓库里的四份部署脚本搬进包根：deploy.sh（内核）、deploy_lib.sh（判定层）、
    deploy.ps1（WSL 皮）、deploy.bat（Windows 侧双击入口）。

    它们与 apply-init.sh 相反：那个是生成物，这几份是交付物的**源**——要在服务器上手改排障，
    所以住在仓库里（`scripts/`），包只是搬运工。缺任一文件不出包，理由同网关配置：
    deploy.sh 会 source deploy_lib.sh，缺一份就是让服务器上跑到一半才炸；皮与内核缺一份，
    另一份就是废纸。

    行尾与编码按扩展名分道，三类各有各的理由（US-26 / US-27）：
    - `.sh` 剥 `\\r`：内核自带一条 CRLF 自检，带着 \\r 出包等于把失败推迟到服务器上才炸。
    - `.ps1` 补 UTF-8 BOM 且用 LF：Windows PowerShell 5.1 在 GBK 代码页下读无 BOM 的中文
      脚本会解析失败。
    - `.bat` 出 CRLF 且必须纯 ASCII：cmd.exe 对只有 LF 的 goto 标签并不可靠，而在 cp65001
      下读带多字节字符的批处理会丢字节同步、把 echo 文本当成命令执行（build-output.bat 同条
      NOTE）。所以这里不放宽成 UTF-8，而是非 ASCII 直接不出包——中文提示交给 deploy.ps1。
    """
    for name in ("deploy.sh", "deploy_lib.sh", "deploy.ps1", "deploy.bat"):
        src = repo_root / "scripts" / name
        if not src.is_file():
            raise BuildError(f"缺部署脚本 {src}：{name} 已纳入交付包，缺文件不出包")
        text = src.read_text(encoding="utf-8").replace("\r\n", "\n").lstrip("\ufeff")
        dst = out / name
        if name.endswith(".ps1"):
            _write(dst, "\ufeff" + text)
        elif name.endswith(".bat"):
            _write_bat(dst, text)
        else:
            _write(dst, text)


def check_frontend_dist(repo_root: Path, allow_stale: bool = False) -> tuple[Path, str]:
    """校验前端 dist：必须存在，且不能比 src 旧（陈旧 dist 是「功能莫名缺失」的头号原因）。

    返回 (dist 路径, 构建时间字符串)。打包脚本刻意不自己跑 npm——构建交给 build-output.bat，
    这样打包机不装 node 也能重跑（只拷已有 dist）。
    """
    dist = repo_root / "frontend" / "dist"
    index = dist / "index.html"
    if not index.is_file():
        raise BuildError(f"前端未构建：找不到 {index}。请先跑 build.bat（或 build-output.bat 会自动构建）")
    src = repo_root / "frontend" / "src"
    if src.is_dir():
        newest_src = max((p.stat().st_mtime for p in src.rglob("*") if p.is_file()), default=0)
        if newest_src > index.stat().st_mtime and not allow_stale:
            raise BuildError(
                f"前端 dist 比 src 旧（dist={_fmt_ts(index.stat().st_mtime)}，"
                f"src 最新={_fmt_ts(newest_src)}）：打进去的会是旧界面。\n"
                f"  请重跑 build.bat；确要用旧 dist 就加 --allow-stale-dist")
    return dist, _fmt_ts(index.stat().st_mtime)


def _fmt_ts(mtime: float) -> str:
    return datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")


def copy_frontend_dist(dist: Path, out_frontend: Path) -> int:
    if out_frontend.exists():
        shutil.rmtree(out_frontend)
    out_frontend.mkdir(parents=True)
    shutil.copytree(dist, out_frontend / "dist")
    return _count_files(out_frontend)


def _write(path: Path, content: str) -> None:
    """统一写盘：UTF-8 + LF。CRLF 会让 apply-init.sh 在 bash 下直接报错，SQL 里也不该有 \\r。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def _write_bat(path: Path, content: str) -> None:
    """Windows 侧 .bat 的写盘：CRLF 行尾 + 纯 ASCII，非 ASCII 直接拒包。

    两条都是 cmd.exe 的脾气，不是风格偏好：只有 LF 的批处理里 goto 标签解析不可靠，而控制台切到
    cp65001 后 cmd.exe 读多字节行会丢字节同步，把 echo 的文本拆成假命令去执行。同一份搬运代码
    对 `.sh` 反向要求 LF，正是因为这条不对称——判据不是「哪种行尾好」，而是「谁来读这个文件」。
    所以中文提示一律住在 deploy.ps1 里，这里遇到非 ASCII 就停下——出包含乱码的入口脚本，
    比缺一个入口脚本更难诊断。
    """
    bad = sorted({c for c in content if ord(c) > 127})
    if bad:
        raise BuildError(
            f"{path.name} 含非 ASCII 字符 {bad[:6]}：cmd.exe 在 cp65001 下会把这一行读坏。"
            f"入口脚本只负责转发，中文提示请写进 scripts/deploy.ps1")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content.replace("\n", "\r\n").encode("ascii"))


# Windows 上目录改名会被杀软、Windows Search 索引器、资源管理器预览窗格和 IDE 的文件监听
# 短暂持有的句柄打断，报 [WinError 5] 拒绝访问 / [WinError 32] 共享冲突。这类占用是毫秒级的，
# 重试几次就过；不重试的代价是让人在演示前手动重跑整个打包（实测发生过：交付目录里的文件正被
# 编辑器打开时换包失败）。线性退避 0.2 / 0.4 / 0.6 / 0.8，最坏多等 2 秒。
_SWAP_RETRIES = 5
_SWAP_RETRY_DELAY = 0.2


def _replace_dir(src: Path, dst: Path) -> None:
    """`os.replace` 的目录版本，对瞬时占用做有界重试。

    只对 PermissionError 重试：磁盘满（WinError 112）、路径过长、跨卷这类错误重试多少次都是
    一样的结果，白等只会把真正的失败信息推迟。目录改名本身是原子的——失败时什么
    都没动，所以重试不会造成半移动状态。
    """
    for attempt in range(1, _SWAP_RETRIES + 1):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == _SWAP_RETRIES:
                raise
            time.sleep(_SWAP_RETRY_DELAY * attempt)


def swap_into_place(staging: Path, out: Path) -> None:
    """把装配好的暂存目录原子地换成正式交付目录，任何一步失败都回到「旧包完好」。

    Windows 上 `os.replace` 不能拿目录覆盖已存在的目录，故走两步：旧包先改名成
    `<out>.old` 暂留，再把暂存目录改名成 `<out>`。两步都是同卷重命名，对外只暴露
    「旧包」与「新包」两种完整状态。

    整个序列（含回收上次残留的 .old、以及第一步改名）都在保护里：磁盘满、目录被杀软
    或索引器占用、权限不足，在 Windows 上都不罕见，而它们抛的是 OSError——不接就会
    变成裸 traceback，还会把一份可能含凭据的半成品脏在交付目录旁边。

    只有两次改名重试，回收 `.old` 的 rmtree 刻意不重试：那条路径只在上一轮硬崩后才会
    走到，失败时同样是安全中止（旧包完好、报错清楚），重跑一次即可，不值得为它再引一层。
    """
    backup = out.parent / (out.name + ".old")
    try:
        if backup.exists():
            shutil.rmtree(backup)
        if out.exists():
            _replace_dir(out, backup)
        _replace_dir(staging, out)
    except BaseException:
        # 宁可这次不发布，也不留下一半的包。回退本身也可能失败（同一个占用原因），
        # 那就忽略它：不能拿一个新异常把真正的原因盖掉。
        try:
            if not out.exists() and backup.exists():
                _replace_dir(backup, out)
        except OSError:
            pass
        shutil.rmtree(staging, ignore_errors=True)
        raise
    shutil.rmtree(backup, ignore_errors=True)


def build(repo_root: Path = REPO_ROOT, out: Path | None = None, slugs=DEFAULT_SLUGS,
          deploy_root: str = DEFAULT_DEPLOY_ROOT, generated_at: str | None = None,
          allow_stale_dist: bool = False, gateway_domain: str | None = None,
          apt_mirror: str | None = None, pypi_mirror: str | None = None,
          npm_registry: str | None = None,
          playwright_download_host: str | None = None) -> dict:
    """装配整个 build-output 目录，返回摘要（供 main 打印与单测断言）。

    四个可选参数（域名 + 三个镜像源）都是三级取值：显式参数 > 打包机环境变量 > 默认值。
    它们的存在是为了把「打包机上手动改过的约定」记进产物：那些改动只活在打包机的用户级配置里
    （npm 的 .npmrc、pip 的 pip.conf、apt 的 sources.list），容器一个都继承不到——于是
    「本地装得上、服务器上装不上」，而仓库里没有任何一处痕迹。
    """
    repo_root = Path(repo_root)
    out = Path(out) if out else repo_root / DEFAULT_OUT_NAME
    root = normalize_deploy_root(deploy_root)
    # 与部署根同级的前置校验，必须在暂存目录创建之前：坏值要是先进了模板，轻则在交付目录
    # 旁边留一份半成品被 rsync 一起传上服务器，重则把上一个可部署的旧包一起毁掉。
    domain = _resolved_setting(gateway_domain, GATEWAY_DOMAIN_ENV, normalize_gateway_domain,
                               DEFAULT_GATEWAY_DOMAIN)
    mirrors = resolve_mirrors(apt_mirror, pypi_mirror, npm_registry)
    pw_host = _resolved_setting(playwright_download_host, PLAYWRIGHT_DOWNLOAD_HOST_ENV,
                                normalize_playwright_download_host,
                                PLAYWRIGHT_DOWNLOAD_HOST_BAKED)
    presets = load_presets(repo_root / "apps", slugs)
    conflicts = detect_port_conflicts(presets)
    if conflicts:
        raise BuildError("预置应用端口冲突（app_project 的 prod_port/dev_port 是 UNIQUE 列）：\n  - "
                         + "\n  - ".join(conflicts))

    dist, dist_stamp = check_frontend_dist(repo_root, allow_stale=allow_stale_dist)

    # 先装配到同卷的暂存目录，全部闸门过了才原子换包。直接在 out 上先 rmtree 再装配的话，
    # 最可能失败的 synth_init_sql（重放安全 / 幂等审计拒包）会把上一个**可部署**的包一起毁掉，
    # 甚至把带凭据的半成品留在原地（出包终检就是在拷完之后才扫 .env）。
    staging = out.parent / (out.name + ".staging")
    if staging.exists():
        shutil.rmtree(staging)          # 上一次 Ctrl-C 的残留：回收它，否则 mkdir 会撞
    staging.mkdir(parents=True)

    try:
        summary = _assemble(repo_root, staging, presets, root, dist, dist_stamp, generated_at,
                            gateway_domain=domain, mirrors=mirrors,
                            playwright_download_host=pw_host)
    except OSError as e:
        shutil.rmtree(staging, ignore_errors=True)
        raise BuildError(f"装配时遇到文件系统错误（磁盘满 / 路径过长 / 文件被占用？）：{e}") from e
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)     # 半成品一律不得留在交付目录旁边
        raise

    # 必须在换包前取：换包过程中 out 会被改名成 .old，事后再问就分不出「本来就没有旧包」
    # 与「旧包被移走了」，而这两种情况该报的话完全不同。
    had_previous = out.exists()
    try:
        swap_into_place(staging, out)
    except OSError as e:
        shutil.rmtree(staging, ignore_errors=True)
        # 文案里的每个事实都得能兑现，否则会把运维引向错的排查方向：
        # ① 只有 PermissionError 真的重试过——磁盘满这类一次都不会重试，说「重试过」会让人
        #    以为再跑一次也许就过；② 首次打包时既没旧包也没 .old，不能宣称「回退失败、旧包在 .old」。
        attempt = (f"已对瞬时占用连续尝试 {_SWAP_RETRIES} 次"
                   if isinstance(e, PermissionError) else "不可重试的错误，未重试")
        backup = out.parent / (out.name + ".old")
        if out.exists():
            state = f"  上一个交付包已原样放回：{out}"
        elif had_previous and backup.exists():
            state = f"  警告：回退也失败了，{out} 目前不存在；旧包仍在 {backup}"
        else:
            state = f"  本次之前没有可用的交付包，{out} 未生成"
        raise BuildError(
            f"换包失败（{attempt}；磁盘满 / 目录被杀软或索引器占用 / 权限不足？）：{e}\n"
            f"{state}") from e
    summary["out"] = out
    return summary


def _assemble(repo_root: Path, staging: Path, presets, root: str,
              dist: Path, dist_stamp: str, generated_at: str | None,
              gateway_domain: str = DEFAULT_GATEWAY_DOMAIN,
              mirrors: Mirrors = DEFAULT_MIRRORS,
              playwright_download_host: str = PLAYWRIGHT_DOWNLOAD_HOST_BAKED) -> dict:
    """往暂存目录里装配整份交付包，返回摘要；任何失败由 build() 负责丢弃暂存目录。"""
    stamp = generated_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    backend_files = copy_backend(repo_root, staging / "backend", root, mirrors)
    browser_files = copy_browser(repo_root, staging / "browser")
    frontend_files = copy_frontend_dist(dist, staging / "frontend")
    gateway_files = copy_gateway(repo_root, staging / "gateway", gateway_domain)
    preset_files = {p.slug: copy_preset_app(p.src, staging / "apps" / p.slug) for p in presets}
    for shared in ("apps", "data", "store"):     # 同路径挂载点：先建出来，避免 compose 建出 root 属主
        (staging / shared).mkdir(parents=True, exist_ok=True)
        _write(staging / shared / ".gitkeep", "")

    init_sql = synth_init_sql(repo_root / "changelog", presets, root, generated_at=stamp)
    _write(staging / "init.sql", init_sql)
    _write(staging / "docker-compose.yml",
           compose_yaml(root, gateway_domain, mirrors, playwright_download_host))
    _write(staging / ".dockerignore", dockerignore())
    _write(staging / ".env.example",
           env_example(root, gateway_domain, mirrors, playwright_download_host))
    _write(staging / "apply-init.sh", apply_init_sh(root))
    copy_deploy_scripts(repo_root, staging)
    _write(staging / "README-deploy.md",
           readme_md(presets, root, stamp, dist_stamp, gateway_domain, mirrors))

    # 出包终检：整个交付目录里不许有任何 .env（.env.example 是模板，名字不同、内容是占位符）
    leaked = [str(p.relative_to(staging)) for p in staging.rglob(".env")]
    if leaked:
        raise BuildError(f"交付包里混入了 .env 凭据文件，已中止：{leaked}")

    return {
        "out": staging,
        "deploy_root": root,
        "gateway_domain": gateway_domain,
        "apt_mirror": mirrors.apt,
        "pypi_mirror": mirrors.pypi,
        "npm_registry": mirrors.npm,
        "presets": presets,
        "preset_files": preset_files,
        "backend_files": backend_files,
        "browser_files": browser_files,
        "frontend_files": frontend_files,
        "gateway_files": gateway_files,
        "init_sql_bytes": len(init_sql.encode("utf-8")),
        "dist_stamp": dist_stamp,
        "generated_at": stamp,
    }


def _widen_redirected_streams() -> None:
    """输出被重定向（非 tty）时把 stdout/stderr 收成 UTF-8。

    Windows 上重定向的流默认按本机代码页（cp936）编码，而 cp936 里没有 '✗'（U+2717），
    stderr 的 backslashreplace 会把它写成字面量 `\\u2717`，落到日志里就是一串乱码。

    交互控制台一律不碰：那种情况下代码页由调用方决定（build-output.bat 会 chcp 65001），
    在这里强行改反而会跟终端的实际编码对不上，把本来正常的中文弄成乱码。
    分不清是哪种（流上没有 isatty）时也不碰：猜错方向的代价比不优化更大。
    """
    for stream in (sys.stdout, sys.stderr):
        if getattr(stream, "isatty", lambda: True)():
            continue
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError, OSError):
            pass            # 已被换成不支持 reconfigure 的对象（StringIO、已关闭的流）：忽略


def main(argv=None) -> int:
    _widen_redirected_streams()
    ap = argparse.ArgumentParser(description="生成 build-output 演示部署包（ADR-0013 / ADR-0014）")
    ap.add_argument("--out", default=None, help="输出目录（默认 仓库根/build-output）")
    ap.add_argument("--slugs", default=",".join(DEFAULT_SLUGS),
                    help=f"预置应用 slug，逗号分隔；空串表示只打平台"
                         f"（默认 {'、'.join(DEFAULT_SLUGS) or '只打平台，不带预置应用'}）")
    ap.add_argument("--deploy-root", default=DEFAULT_DEPLOY_ROOT,
                    help=f"写进包里的部署根**默认值**，须为 Linux 绝对路径（默认 {DEFAULT_DEPLOY_ROOT}）；"
                         f"部署时可由 deploy 脚本现场输入覆盖（ADR-0034），不必为此重打包")
    ap.add_argument("--allow-stale-dist", action="store_true",
                    help="允许前端 dist 比 src 旧（默认拒绝，避免打出旧界面）")
    ap.add_argument("--gateway-domain", default=None,
                    help=f"网关泛解析域名本体（不带协议/端口/路径；默认取环境变量 {GATEWAY_DOMAIN_ENV}，"
                         f"都没有则 {DEFAULT_GATEWAY_DOMAIN}）")
    ap.add_argument("--apt-mirror", default=None,
                    help=f"Debian 与 docker CE 仓库的镜像站**主机名**（形如 mirrors.example.com，不带协议与"
                         f"路径；默认不换源，环境变量 {APT_MIRROR_ENV}）")
    ap.add_argument("--pypi-mirror", default=None,
                    help=f"pip 镜像完整地址（形如 https://mirrors.example.com/pypi/simple；默认不注入，"
                         f"环境变量 {PYPI_MIRROR_ENV}）")
    ap.add_argument("--npm-registry", default=None,
                    help=f"npm 镜像源完整地址（形如 https://registry.npmmirror.com；默认不注入，"
                         f"环境变量 {NPM_REGISTRY_ENV}）")
    ap.add_argument("--playwright-download-host", default=None,
                    help=f"浏览器 sidecar 镜像构建期的 Chromium 下载源（完整 http(s) 地址；"
                         f"默认取环境变量 {PLAYWRIGHT_DOWNLOAD_HOST_ENV}，"
                         f"都没有则 {PLAYWRIGHT_DOWNLOAD_HOST_BAKED}）")
    args = ap.parse_args(argv)

    slugs = tuple(s.strip() for s in (args.slugs or "").split(",") if s.strip())
    try:
        summary = build(out=Path(args.out) if args.out else None, slugs=slugs,
                        deploy_root=args.deploy_root, allow_stale_dist=args.allow_stale_dist,
                        gateway_domain=args.gateway_domain, apt_mirror=args.apt_mirror,
                        pypi_mirror=args.pypi_mirror, npm_registry=args.npm_registry,
                        playwright_download_host=args.playwright_download_host)
    except BuildError as e:
        print(f"✗ 打包失败：{e}", file=sys.stderr)
        return 1
    except OSError as e:
        # build() 已把可预见的文件系统错误收成了 BuildError，这里接的是它漏网的部分
        # （比如写日志本身失败）。总之不能让双击 bat 的人只看到一堆栈。
        print(f"✗ 打包失败（文件系统错误）：{e}", file=sys.stderr)
        return 1

    out = summary["out"]
    print(f"✓ 交付包已生成：{out}")
    print(f"  部署根        {summary['deploy_root']}（容器内外同路径，ADR-0013）")
    print(f"  网关域名      {summary['gateway_domain']}（平台 .env 与 gateway/.env 可用同名变量覆盖）")
    given = [("apt", summary["apt_mirror"]), ("pypi", summary["pypi_mirror"]),
             ("npm", summary["npm_registry"])]
    print("  依赖镜像源    " + ("、".join(f"{k}={v}" for k, v in given if v)
                              or "未注入（全部走官方源）"))
    print(f"  后端          {summary['backend_files']} 个文件（app/ + requirements.txt + Dockerfile）")
    print(f"  浏览器 sidecar {summary['browser_files']} 个文件（browser/；服务名 browser，不发布宿主端口）")
    print(f"  前端 dist     {summary['frontend_files']} 个文件（构建于 {summary['dist_stamp']}）")
    print(f"  网关配置      {summary['gateway_files']} 个文件（gateway/；certs/ 与运行期 routes.json 不入包）")
    print(f"  init.sql      {summary['init_sql_bytes']:,} 字节（changelog 全量合成 + 预置应用种子）")
    if summary["presets"]:
        for p in summary["presets"]:
            print(f"  预置应用      {p.slug:<12} {p.name:<12} {p.stack:<16} "
                  f"prod={p.prod_port} dev={p.dev_port} "
                  f"({summary['preset_files'][p.slug]} 个文件)")
    else:
        print("  预置应用      （无）")
    print("  下一步        上传到服务器 → cp .env.example .env → docker compose up -d --build → bash apply-init.sh")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
