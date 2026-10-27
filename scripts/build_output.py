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

DEFAULT_SLUGS = ("test1", "sqlc002")
DEFAULT_DEPLOY_ROOT = "/opt/zerocode"
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

# 网关 compose 里域名默认值的形态（与平台 compose 的 ${VAR:-打包值} 同一副面孔）。
# 打包时把这个 fallback 换成实际域名，使「不建 gateway/.env 也能与平台指向同一个域名」；
# 出现 0 次或多次都拒包——前者说明源文件改了形状、替换会静默不生效，后者说明判定域有歧义。
_GATEWAY_DOMAIN_TOKEN_RE = re.compile(r"\$\{" + GATEWAY_DOMAIN_ENV + r":-[^}]*\}")



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
    向量改型已由 guard_vector_alter 换成 DO 块（不再以 ALTER TABLE 开头），故不会被误报。
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


# 无守卫的向量列改型：PG 不提供 IF-TYPE 形态，重放它 = 全表重写 + ACCESS EXCLUSIVE 锁，
# 维度不符时还会直接失败。合成产物里换成 DO 块按实际类型判断（changelog 原文保持不动，
# 因为 app/db.py 的切分器不认 $$，改原文会拖挂后端启动迁移）。
_VECTOR_TYPE_RE = re.compile(
    r"ALTER\s+TABLE\s+(?P<tbl>[A-Za-z_]\w*)\s+ALTER\s+COLUMN\s+(?P<col>[A-Za-z_]\w*)\s+"
    r"TYPE\s+vector\s*\(\s*(?P<dim>\d+)\s*\)\s*;",
    re.IGNORECASE)

_VECTOR_GUARD_TMPL = """DO $zcvec$
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
    IF cur IS DISTINCT FROM 'vector({dim})' THEN
        EXECUTE format('ALTER TABLE %s ALTER COLUMN {col} TYPE vector({dim})', tbl);
    END IF;
END
$zcvec$;"""


def guard_vector_alter(sql: str) -> tuple[str, int]:
    """把无守卫的 `ALTER COLUMN … TYPE vector(N)` 换成类型判断 DO 块，返回 (新文本, 替换数)。"""
    def _sub(m: re.Match) -> str:
        return _VECTOR_GUARD_TMPL.format(tbl=m.group("tbl"), col=m.group("col"), dim=m.group("dim"))

    return _VECTOR_TYPE_RE.subn(_sub, sql)


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


def preset_seed_sql(presets, deploy_root: str) -> str:
    """预置应用的元数据种子段：workspace + app_project，root_path 重写为部署根下的路径。

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
        root_path = f"{deploy_root}/apps/{p.slug}"
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
-- 生成时间：{stamp}    部署根：{deploy_root}    预置应用：{slugs}
-- 重放语义：只补不覆盖（结构段幂等 DDL；数据段一律 ON CONFLICT DO NOTHING 或存在性门，
--           服务器既有数据永不被洗）。为达此语义，合成时对 changelog 原文做了四处等价改写：
--           1) 给 CREATE TABLE/INDEX/SEQUENCE/EXTENSION 与 ADD COLUMN 补 IF NOT EXISTS
--           2) 剔除 ON CONFLICT 的仲裁目标（后续迁移会重塑唯一约束，带目标重放会报错）
--           3) 无守卫的 ALTER COLUMN TYPE vector(N) 换成按实际类型判断的 DO 块
--           4) 会覆盖服务器既有值的裸 UPDATE 按处置表改写（仅当仍是旧默认值时才生效）
--           四处均只存在于本生成物，changelog/ 原文不动（它是增量轨的历史，且
--           app/db.py 的语句切分器不认 $$）。无法自动幂等化的语句会在打包期直接拒包。
-- 执行方式：只能用 psql（本文件含 DO $$ 块）；**禁止**放进 changelog/——
--           app/db.py 的语句切分器不认 $$，会把后端启动迁移拖挂。
--   docker compose exec -T pg psql -U "$POSTGRES_USER" -d zerocode -v ON_ERROR_STOP=1 < init.sql
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
        text, hits = guard_vector_alter(raw)
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
    # 兜底：合成后不得残留无守卫的向量改型，也不得残留带仲裁目标的 ON CONFLICT。
    # 查的是剥过注释的文本：注释里合法保留着 `ON CONFLICT (列…)` 这类说明性引用。
    active = _strip_sql_comments(merged)
    leftover = _VECTOR_TYPE_RE.search(active)
    if leftover:
        raise BuildError(f"合成后仍残留无守卫的向量改型语句：{leftover.group(0)[:80]}")
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

    seed = preset_seed_sql(presets, deploy_root)
    if seed:
        merged += "\n" + seed
    merged += (f"\n-- 合成完成：结构段 {len(files)} 个来源文件，幂等守卫补齐 {idem} 处，"
               f"ON CONFLICT 仲裁目标剔除 {stripped} 处，向量改型守卫 {guarded} 处，"
               f"裸 UPDATE 改写 {rewritten} 处，预置应用 {len(presets)} 个\n")
    return merged


# ---------------------------------------------------------------- 交付文件模板

def _compose_override(key: str, value: str) -> str:
    """compose 的 environment 行形态：`${VAR:-打包值}`——打包值只是默认，不是终值。

    与部署根相反的处置是刻意的，两边都得钉住（tests 里有同名用例）：部署根被 compose 挂载、
    镜像 WORKDIR、init.sql 的 root_path 三处同源共用，运行期改它会让他们对不上且全程不报错，
    所以钉死；域名与镜像源相反——它们是单点值，而 README §4 承诺后续发布不覆盖服务器上的
    `.env`，只有落在那里才活得过下一次发布。写成裸值就等于在服务器上永远改不动它。
    """
    return f"{key}: ${{{key}:-{value}}}"


def _backend_overlay_env(gateway_domain: str, mirrors: Mirrors) -> str:
    """backend 的 environment 段要追加的行（自带 6 空格缩进，可直接插在上一行之后）。

    只列**有运行期消费方**的三项：网关域名由 app/deploy/nics.py 在容器里读，PyPI / npm 由
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


def _env_example_overlay(gateway_domain: str, mirrors: Mirrors) -> str:
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
    ]
    if mirrors.pypi:
        lines += ["", "# 应用预览环境（dev 栈）装 Python 依赖用的镜像源，打包时烘的是默认值。",
                  f"{PYPI_MIRROR_ENV}={mirrors.pypi}"]
    if mirrors.npm:
        lines += ["", "# 应用预览环境（dev 栈）装前端依赖用的镜像源，同上。",
                  f"{NPM_REGISTRY_ENV}={mirrors.npm}"]
    return "\n".join(lines) + "\n"


def compose_yaml(deploy_root: str, gateway_domain: str = DEFAULT_GATEWAY_DOMAIN,
                 mirrors: Mirrors = DEFAULT_MIRRORS) -> str:
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
    - 挂载路径**烘死**、不走 `${DEPLOY_ROOT:-…}` 运行期覆盖：WORKDIR 是构建期定下来的，
      运行期改 .env 只会让挂载点与 REPO_ROOT 分家（新建应用落进容器可写层，宿主 daemon
      找不到路径），而且全程不报错。换部署根的唯一正确做法是重跑打包。
    - 前端 dist 由后端直接托管（app/main.py 的 SPA 挂载），不再起 nginx 容器。
    - backend 的 environment 段尾部追加 `ZEROCODE_*` 运行期覆盖位（见 _backend_overlay_env）。
    """
    extra_env = _backend_overlay_env(gateway_domain, mirrors)
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
    image: zerocode-platform:demo
    container_name: zerocode-backend
    restart: unless-stopped
    depends_on:
      pg:
        condition: service_healthy
    environment:
      # 平台库连接账号需具备建库建角色权限（要供给应用分库）；镜像默认用户即超级用户
      ZEROCODE_DATABASE_URL: postgresql+asyncpg://${{POSTGRES_USER:-zerocode}}:${{POSTGRES_PASSWORD}}@zerocode-pg16:5432/zerocode
      ZEROCODE_DATA_DIR: {deploy_root}/data
      ZEROCODE_LOG_LEVEL: INFO
      # 网关路由表：与 gateway 容器共用的宿主目录（见上）。app/deploy/gateway_routes.py
      # 往这里写 {{slug: prod_port}}，网关只读它；写不进去时业务照常，只是域名访问 404。
      ZEROCODE_GATEWAY_ROUTES: {deploy_root}/gateway/routes/routes.json
{extra_env}
    ports:
      - "${{PLATFORM_PORT:-8100}}:8100"      # 控制台：http://<服务器IP>:8100
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock   # 平台要在宿主起应用 dev/prod 栈
      # 下面四个挂载点与镜像的 WORKDIR 逐字相同（ADR-0013）。刻意不写成 ${{DEPLOY_ROOT:-…}}：
      # WORKDIR 是构建期烘死的，运行期改 .env 只会把两者弄得不一致，且没有任何报错。
      - {deploy_root}/apps:{deploy_root}/apps
      - {deploy_root}/data:{deploy_root}/data
      - {deploy_root}/store:{deploy_root}/store
      # 挂目录不是挂文件：routes.json 由后端原子替换，单文件 bind mount 会钉在旧 inode 上
      - {deploy_root}/gateway/routes:{deploy_root}/gateway/routes
    # 应用栈把 dev/prod 端口发布在宿主上，平台容器要探得到它们（网关用同一条 host-gateway）。
    # 没有这行的时候 host.docker.internal 在 Linux 上根本不解析，探针只能连不到任何地址。
    extra_hosts:
      - "host.docker.internal:host-gateway"
    networks: [default, zcnet]

volumes:
  pgdata: {{}}

networks:
  default: {{}}
  zcnet:
    name: zcnet          # 应用栈以 external: true 引用它，故由本项目按这个确切名字建出来
"""


def backend_dockerfile(deploy_root: str, mirrors: Mirrors = DEFAULT_MIRRORS) -> str:
    """平台后端镜像：python:3.12-slim + docker CLI/compose plugin（ADR-0013）。

    WORKDIR 必须等于部署根：settings.py 的 REPO_ROOT 由本文件自身位置推出，在镜像里就是
    WORKDIR，于是 `deploy.apps_root` 留空时的兜底恰好就是同路径挂载点，新建应用直接落到
    共享目录里。写死 /opt/zerocode 而部署根是别的值时，兜底会指向容器可写层里一个宿主
    根本不存在的路径，新建的应用看起来成功了、起预览时却必挂。

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
{apt_block}# docker CLI + compose plugin：平台在容器内通过挂进来的 docker.sock 起应用 dev/prod 栈（ADR-0013）
RUN apt-get update \\
 && apt-get install -y --no-install-recommends ca-certificates curl gnupg \\
 && install -m 0755 -d /etc/apt/keyrings \\
 && curl -fsSL {docker_repo}/gpg -o /etc/apt/keyrings/docker.asc \\
 && chmod a+r /etc/apt/keyrings/docker.asc \\
 && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \\
{docker_repo} $(. /etc/os-release && echo $VERSION_CODENAME) stable" \\
      > /etc/apt/sources.list.d/docker.list \\
 && apt-get update \\
 && apt-get install -y --no-install-recommends docker-ce-cli docker-compose-plugin \\
 && rm -rf /var/lib/apt/lists/*

# 与 compose 的同路径挂载点逐字相同；换部署根请重跑打包（它是构建期烘死的）
WORKDIR {deploy_root}

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
                mirrors: Mirrors = DEFAULT_MIRRORS) -> str:
    """`.env` 模板：口令位、端口位，以及打包值可覆盖的 `ZEROCODE_*` 运行期位。

    刻意**不**给 DEPLOY_ROOT（见正文注释）：改它换不了部署根，只会让挂载点与 WORKDIR 分家。
    """
    overlay = _env_example_overlay(gateway_domain, mirrors)
    return f"""# 复制成 .env 后按需修改（docker compose 会自动读取同目录 .env）
#   cp .env.example .env

# 本包的部署根已在打包时烘死为：{deploy_root}
# compose 的挂载路径、镜像的 WORKDIR、init.sql 里预置应用的 root_path 三处都由它算出，
# 所以这里刻意**不提供** DEPLOY_ROOT 变量：改 .env 换不了部署根，只会让三者对不上。
# 要换目录请在开发机上重跑打包：build-output.bat --deploy-root /新的/绝对路径

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


def apply_init_sh() -> str:
    """init.sql 执行脚本：LF 行尾（bash 不认 CRLF），ON_ERROR_STOP=1 让失败当场可见。"""
    return """#!/usr/bin/env bash
# 执行全量 init.sql（可重复执行：只补不覆盖）。用法：bash apply-init.sh
set -euo pipefail
cd "$(dirname "$0")"

: "${POSTGRES_USER:=zerocode}"
if [ -f .env ]; then
  # 只取 POSTGRES_USER，不 source 整个 .env（避免把口令带进本进程环境）
  POSTGRES_USER="$(grep -E '^POSTGRES_USER=' .env | tail -n1 | cut -d= -f2- || echo zerocode)"
fi

echo "→ 对 zerocode 库执行 init.sql（用户 ${POSTGRES_USER}）"
docker compose exec -T pg psql -U "${POSTGRES_USER}" -d zerocode -v ON_ERROR_STOP=1 < init.sql
echo "✓ init.sql 执行完成"
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

生成时间：{generated_at}　前端 dist 构建时间：{dist_stamp}　部署根：`{deploy_root}`
预置应用：{slug_list}　网关域名：`{gateway_domain}`

> 本目录整体是**生成物**（由仓库根的 `build-output.bat` 产出），不要手改其中任何文件——
> 下次打包会整目录重建。要改就改仓库里的 `changelog/`、`app/`、`frontend/`、`gateway/`
> 或打包脚本。`gateway/certs/`（TLS 私钥）与 `gateway/routes/routes.json`（运行期路由）是
> 服务器现场产物，不在包内。

## 1. 前置条件

- Linux 服务器 + Docker 24+（含 `docker compose` v2 插件）
- 服务器能访问 Docker Hub 与 **PyPI**：平台与应用栈的基础镜像（`pgvector/pgvector:pg16`、
  `python:3.12-slim`、`node:20-alpine`、`maven`、`openresty` 等）**在服务器上在线拉取**，
  本包不含镜像 tar；`docker compose build` 还要在镜像里跑 `pip install -r requirements.txt`。
  网络受限时先配镜像源再打包（`build-output.bat --apt-mirror mirrors.aliyun.com`
  `--pypi-mirror https://mirrors.aliyun.com/pypi/simple`
  `--npm-registry https://registry.npmmirror.com`，三个可只给其一、默认全不给；
  docker-ce 仓库走镜像站的 `/docker-ce/linux/debian` 约定路径），否则首次 build
  会卡在拉依赖。
- 本目录必须放在 `{deploy_root}`。这个路径在打包时已烘死到三处（compose 的同路径挂载、
  镜像的 `WORKDIR`、init.sql 里预置应用的 `root_path`），`.env` 里改不了它；要换目录请重跑
  打包（`build-output.bat --deploy-root /新的路径`）。路径不一致会导致预置应用失联、新建应用
  落进容器可写层（见 ADR-0013）。
- 网关要用真域名访问时，DNS 里必须有 `*.<域名>`（以及裸域名）的 A/AAAA 记录指向这台服务器，
  且宿主 **80/443 空闲**——中央网关占用它们（ADR-0018）。只用 `http://<IP>:8100` 直连控制台
  时，网关可以先不起。
- 平台库账号需具备建库建角色权限（镜像默认的 `zerocode` 就是超级用户）。
- 口令请只用字母、数字与 `- _`：它会被原样拼进 asyncpg 连接串，含 `@ : / # % & ?` 会把它截断。
- TLS 证书**不在包里**（私钥绝不进包）：启动网关前要按 §2 把 `gateway.crt` / `gateway.key`
  放到 `gateway/certs/`。生产用 acme.sh 签泛域名；内网演示可用包内的 `make-cert.sh` 自签。

## 2. 首次部署

```bash
cd {deploy_root}
cp .env.example .env          # 改掉 POSTGRES_PASSWORD
mkdir -p apps data store      # 同路径挂载点（compose 也会自动建，先建可避免属主是 root）
docker compose up -d --build  # 起 pg + backend（首次要 build 平台镜像）
docker compose ps             # 等 pg healthy、backend running
bash apply-init.sh            # 执行全量 init.sql（结构 + 默认配置 + 预置应用行）
```

打开控制台：`http://<服务器IP>:8100`

### 网关（通配域名 HTTPS）

本包在 `gateway/` 下带一个**独立的 OpenResty compose 项目**：终止 TLS，并按
`https://<slug>.<域名>` 反代到应用的 prod 宿主端口。它不在上面那条 `docker compose up` 的
范围内，要单独起。

先签证书，输出必须是 `gateway/certs/gateway.crt` 与 `gateway/certs/gateway.key`（文件名与
域名无关，换域名/换 CA 都不用改 nginx 配置）：

- **生产（推荐）**：用 acme.sh 走 DNS-01 签泛域名，再用 `--install-cert` 写到上面这对文件名。
  通配证书要同时申请 `-d '<域名>' -d '*.<域名>'`，DNS 里也要有对应的 `*` 解析。
- **内网/本机自签**：用包内的脚本（在 openresty 镜像里跑 openssl；自签 CA 需要客户端手动信任）：
  ```bash
  docker run --rm -v "$PWD/gateway/certs:/out" -v "$PWD/gateway/make-cert.sh:/mk.sh" \\
    openresty/openresty:alpine sh -c "tr -d '\\r' < /mk.sh > /tmp/mk.sh; sh /tmp/mk.sh '{gateway_domain}'"
  # 然后把 gateway/certs/ca.crt 导入客户端「受信任的根证书颁发机构」
  ```

证书就位后启动网关：

```bash
docker compose -f gateway/docker-compose.yml up -d
docker compose -f gateway/docker-compose.yml ps
```

80 上的请求会 301 到 https；两个端口都必须空闲。

### 网关域名与路由表

控制台里 `https://{{slug}}.<域名>` 这个地址由 `app/deploy/nics.py` 在容器内读
`ZEROCODE_GATEWAY_DOMAIN` 拼出，打包时烘进的默认值是 `{gateway_domain}`。

换域名要**同时**改两处，两边必须一致：部署根 `.env` 的 `ZEROCODE_GATEWAY_DOMAIN`，以及
`gateway/.env` 里的同名变量。`gateway/.env` 是可选的——不建就用打包时烘进
`gateway/docker-compose.yml` 的默认值；要改就从 `gateway/.env.example` 复制。两边不一致的症状
是「控制台显示的地址看着正常、点进去全 404」（ADR-0016），日志里没有一行指向真因。

路由表是平台写给网关的唯一契约：后端把 `status_prod=='running'` 的应用投影成
`{{slug: prod_port}}` 写进 `{deploy_root}/gateway/routes/routes.json`。平台 compose 已把该目录
同路径挂进 backend（`ZEROCODE_GATEWAY_ROUTES` 指向文件），网关以只读方式挂同一个宿主目录，
所以发布/停机后路由自动生效，不需要重启网关。包里的初值是空对象 `{{}}`。

网关或证书没就位之前，控制台里应用地址看着正常、点进去全 404。

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

在开发机上重跑打包，再**分目录**上传、重建、重放 init.sql。

传输落点：包内容必须直接落在 `{deploy_root}` 本身（compose 的同路径挂载、镜像
`WORKDIR`、init.sql 的 `root_path` 三处都依赖这个约定）。`build-output/*` 的 `*`
**不匹配点开头的文件**——`scp -r build-output/*` 会静默漏掉 `.env.example` 与
`.dockerignore`，首次部署就会缺文件。稳妥写法是 `build-output/.`（或
`rsync -a build-output/ {{deploy_root}}/`，末尾斜杠成对）：

```bash
# 开发机（Windows）：打包，并把整个目录传到部署根的**同级**
build-output.bat
scp -r build-output <user>@<server>:{deploy_root}/../

# 服务器：下面五步都在部署根的同级目录里执行
cd {deploy_root}/..

# ① 平台代码与前端 dist：这两个目录里没有服务器现场产生的东西，可以整目录镜像
rsync -a --delete build-output/backend/  {deploy_root}/backend/
rsync -a --delete build-output/frontend/ {deploy_root}/frontend/

# ② 顶层文件：只覆盖、不删（{deploy_root}/.env 是服务器现场填的，绝不能删）
rsync -a build-output/init.sql build-output/docker-compose.yml build-output/.dockerignore \\
         build-output/.env.example build-output/apply-init.sh build-output/README-deploy.md \\
         {deploy_root}/

# ③ 网关配置：certs/（私钥）、routes/（运行期路由表）与 .env（现场域名覆盖）必须排除，
#    否则 --delete 会把服务器现场产物删掉；被排除的项也不会被传输/覆盖
rsync -a --delete --exclude '/certs/' --exclude '/routes/' --exclude '/.env' \\
         build-output/gateway/ {deploy_root}/gateway/
docker compose -f {deploy_root}/gateway/docker-compose.yml up -d

# ④ 预置应用源码：按 slug 镜像，deploy/ 排除（里面是服务器现场生成的 dev/prod 口令）
{preset_rsync}

# ⑤ 重建并全量重放
cd {deploy_root}
docker compose up -d --build                       # 平台代码走镜像重建
bash apply-init.sh                                 # 新表自动建上，既有数据不动
```

**为什么不能用一条 `rsync -a --delete build-output/ {deploy_root}/` 搞定**：`--delete` 的语义是
「删掉目标里源没有的东西」，而本包**刻意不含**下面几类服务器现场产物，一条命令就会在
**每次发布**时把它们全删掉。这不是理论风险：`.env`、`apps/<slug>/deploy/`、`data/`、`store/`
与 `gateway/certs/` 里的内容在首次部署跑完后就已经存在，所以**第一次**执行本节命令就会全部
中招；只有「服务器上新建的应用」那一项取决于你到底建没建：

| 会被删掉的 | 后果 |
|---|---|
| `{deploy_root}/.env` | 里面是 `POSTGRES_PASSWORD`；紧接着的 `docker compose up` 会被 `${{POSTGRES_PASSWORD:?}}` 当场拒收。若照 §2 重新 `cp .env.example .env` 并填了**新**口令，pgdata 卷里仍是旧口令 → 后端连不上库，且现象与「口令写错」无法区分 |
| `{deploy_root}/apps/<服务器上新建的应用>/` | 控制台建的应用工程整棵消失，而 `workspace.root_path` 行还在库里（init.sql 只补不覆盖），列表里点进去全挂 |
| `{deploy_root}/apps/<slug>/deploy/` | dev/prod 的 `.env` 明文口令与已渲染 compose；丢了之后预览/发布会静默走偏 |
| `{deploy_root}/data/`、`{deploy_root}/store/` 下的内容 | 运行期落盘的技能库与商店包（本包这两个目录只有 `.gitkeep`） |
| `{deploy_root}/gateway/certs/` | 网关的 TLS 证书与私钥；删了网关下次重启直接起不来，重签期间 HTTPS 全断 |
| `{deploy_root}/gateway/routes/` | 运行期路由表；删了要等下一次发布/停机动作才会重建，期间所有应用域名 404 |
| `{deploy_root}/gateway/.env` | 网关侧域名覆盖；丢了会退回打包默认域名，与平台不一致时全 404 |

一句话：`.env`、`data/`、`store/`、`gateway/certs/`、`gateway/routes/`、`gateway/.env`、
以及服务器上新建的应用，**上面五步里任何一步都不要碰**。

`init.sql` 每次都是**全量**的（由 `changelog/` 全部迁移按序合成），所以不需要记「上次发到哪了」；
它的语义是「只补不覆盖」——服务器上现场产生的对话、新建的应用、改过的配置都不会被洗掉。
上面这套分目录上传，就是让**文件层**也遵守同一条语义。

## 5. 已知代价（不是 bug）

- **`bash apply-init.sh` 是每次发布不可省的唯一 schema 通道**：镜像里根本没有 `changelog/`
  （`docker compose build` 只 COPY 了 `backend/app` 与 `frontend/dist`），所以 backend 首启时
  `app/db.py::run_migrations` 面对不存在的目录会拿空迭代器**静默空转**，不报错也不建表。
  忘了跑 apply-init.sh 的后果是「新表没出现、但日志里一切正常」。刻意不把 `changelog/`
  烘进镜像：原始迁移没有幂等守卫（守卫是合成期补的），在 init.sql 已建好的库上重放
  会因 `CREATE TABLE` 撞名而让 backend 启动失败（ADR-0014）。同理，`schema_migration` 表
  在演示部署上永远是空的，这是预期行为。
- **`llm.*` 会补回一条属主为空的平台默认行**：为使重放不依赖「诞生当时」的唯一约束，合成时把
  `ON CONFLICT (列…) DO NOTHING` 归一成无目标形态；而 `app_config` 的仲裁索引后来变成了
  `(owner_user_id, key)`，创始人事务把 `llm.*` 划归自己后，NULL 属主那一行就「缺失」了，
  重放会把它按默认值补回来。这不会覆盖任何人：配置读取是「用户自己的行 覆盖 平台行」，
  创始人看到的仍是自己填的密钥；新用户看到空密钥则与全新安装一致。
- **后端容器持有宿主 docker.sock**，等价于宿主 root 权限。演示机不要跑不可信工作区。
- **网关是独立 compose 项目**：`docker compose up -d` 只管平台，网关要 `-f gateway/docker-compose.yml`
  单独启停；它占用宿主 80/443，且证书/私钥只在服务器现场生成（`gateway/certs/` 不进包）。
  acme.sh 续期后要重启（或 reload）网关让 nginx 读到新证书。
- **部署根被钉死**：换目录要重打包（`root_path`、同路径挂载、镜像 `WORKDIR` 与
  `ZEROCODE_GATEWAY_ROUTES` 都依赖它）。
- `deploy.apps_root` 留空时兜底为 `REPO_ROOT/apps`，而镜像的 `WORKDIR` 就是部署根，故兜底值
  恰好等于同路径挂载点 `{deploy_root}/apps`，不需要在控制台里额外设。只有当你把应用工程
  放到部署根之外时，才需要去「设置 → 部署环境」里显式指定应用根目录。

## 6. 排障

| 现象 | 原因与处置 |
|---|---|
| 控制台打得开但应用列表空 | 还没注册账号，或注册的不是第一个账号（预置应用归创始人） |
| 点预览报「dev 分库供给失败」 | 平台库账号无建库权限，或 pg 容器没起：`docker compose logs pg` |
| 点预览长时间无响应 | 首次要在容器内装 npm/pip 依赖；看控制台「查看日志」 |
| 预览等满健康窗口报「未就绪」，而容器日志里前端明明已 ready | 探针连的是 `.env` 的 `ZEROCODE_PREVIEW_PROBE_HOST`（默认 `host.docker.internal`，需要 compose 里 backend 的 `extra_hosts: host-gateway`）。改成 `127.0.0.1` 或删掉 extra_hosts 就会全轮连接拒绝 |
| 控制台里「打开/复制预览地址」指向 localhost | 那是 `ZEROCODE_PREVIEW_HOST` 的打包默认值。服务器上把它改成服务器 IP 或域名并 `docker compose up -d`；另外宿主防火墙要放行应用端口（默认 8210-8399） |
| 应用栈起不来、报路径不存在 | 部署根与容器内路径不一致（ADR-0013）：核对本目录是否就在 `{deploy_root}` |
| 发布后新表/新列没出现 | 忘了跑 `bash apply-init.sh`——镜像里没有 changelog，backend 不会自己补 schema |
| 拉镜像超时 | 服务器访问不了 Docker Hub：配 registry mirror 后 `docker compose up -d --build` 重试 |
| 控制台里应用地址点进去全 404 | 逐项核对：① 网关起了吗（`docker compose -f gateway/docker-compose.yml ps`）；② 平台 `.env` 与 `gateway/.env` 的域名是否一致；③ 应用 prod 是否 `running`（进没进 `gateway/routes/routes.json`）；④ 浏览器是否信任了自签 CA |
| 网关容器起不来 / 反复重启 | `docker compose -f gateway/docker-compose.yml logs gateway`：多为 `gateway/certs/gateway.crt|key` 缺失，或 80/443 被占用 |
| 发布成功但域名仍是 404 | 检查 `{deploy_root}/gateway/routes/routes.json` 里有没有该 slug；没有就看 backend 日志里 `gateway routes.json 刷新失败` 一行（该失败只告警、不阻断发布） |
| 端口被占用 | 改 `.env` 的 `PLATFORM_PORT`；应用端口在控制台改不了，需重打包；网关的 80/443 被占只能腾端口 |

> 打包期的端口检测只管两件事：预置应用之间不得互撞 `prod_port`/`dev_port`（它们是 UNIQUE 列），
> 且同一个应用的 prod 与 dev 不得相同。它**不**探测服务器上已占用的端口，也不管预置应用与
> `PLATFORM_PORT`/`PG_PORT` 是否相撞（默认值 8210+ 与 8100/5433 差得够远）。真撞上了会在
> `docker compose up` 时报 `port is already allocated`，按上表处置。
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


def gateway_compose_text(text: str, gateway_domain: str) -> str:
    """把网关 compose 里 `GATEWAY_DOMAIN` 的 fallback 换成打包域名（ADR-0018）。

    网关是一个独立 compose 项目，它的 `.env` 在 `gateway/` 下而不是部署根。不烘默认值时，
    自定义域名必须额外记得 `cp gateway/.env.example gateway/.env`，忘掉的表现是
    「平台地址是 <自定义域名>、网关仍按 zerocode.com 判 Host」——控制台看着正常，点进去全 404。

    判定域故意收得很窄（只认 `${ZEROCODE_GATEWAY_DOMAIN:-…}` 这一种写法），且必须恰好命中一次：
    源文件将来若改成裸值或改写变量名，这里直接拒包，而不是产出一份域名没换的包。
    """
    new, hits = _GATEWAY_DOMAIN_TOKEN_RE.subn(
        lambda _m: "${" + GATEWAY_DOMAIN_ENV + ":-" + gateway_domain + "}", text)
    if hits != 1:
        raise BuildError(
            f"gateway/docker-compose.yml 里 `${{{GATEWAY_DOMAIN_ENV}:-…}}` 应恰好出现 1 次，"
            f"实际 {hits} 次——打包器无法把网关域名烘成默认值。")
    return new


def gateway_env_example(gateway_domain: str) -> str:
    """`gateway/.env.example`：只在换域名/改上游时才需要复制成 `gateway/.env`。

    与平台 `.env.example` 相反，这里必须写明「本文件在 gateway/ 下」：gateway 是独立 compose
    项目，`docker compose -f gateway/docker-compose.yml up -d` 读的是 gateway/.env，不是部署根
    的 .env。两处域名不一致的症状正是 ADR-0016 记过的「地址正常、点进去全 404」。
    """
    return f"""# gateway 是独立的 compose 项目：本文件要复制成同目录的 .env 才会被读取
# （不是部署根下的 .env）。打包时域名已烘进 docker-compose.yml 的默认值，
# 只有要用另一个域名时才需要：cp .env.example .env 后改下面这一行，
# 并同步修改部署根 .env 里的同名变量——两边不一致会让应用域名全部 404。
{GATEWAY_DOMAIN_ENV}={gateway_domain}

# 可选：网关从哪个宿主地址连应用 prod 端口（默认 host.docker.internal）。
# 容器化部署不要改成 127.0.0.1：桥接容器里的回环是它自己，那里没有应用在监听。
# ZEROCODE_GATEWAY_UPSTREAM=host.docker.internal
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
          npm_registry: str | None = None) -> dict:
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
                            gateway_domain=domain, mirrors=mirrors)
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
              mirrors: Mirrors = DEFAULT_MIRRORS) -> dict:
    """往暂存目录里装配整份交付包，返回摘要；任何失败由 build() 负责丢弃暂存目录。"""
    stamp = generated_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    backend_files = copy_backend(repo_root, staging / "backend", root, mirrors)
    frontend_files = copy_frontend_dist(dist, staging / "frontend")
    gateway_files = copy_gateway(repo_root, staging / "gateway", gateway_domain)
    preset_files = {p.slug: copy_preset_app(p.src, staging / "apps" / p.slug) for p in presets}
    for shared in ("apps", "data", "store"):     # 同路径挂载点：先建出来，避免 compose 建出 root 属主
        (staging / shared).mkdir(parents=True, exist_ok=True)
        _write(staging / shared / ".gitkeep", "")

    init_sql = synth_init_sql(repo_root / "changelog", presets, root, generated_at=stamp)
    _write(staging / "init.sql", init_sql)
    _write(staging / "docker-compose.yml", compose_yaml(root, gateway_domain, mirrors))
    _write(staging / ".dockerignore", dockerignore())
    _write(staging / ".env.example", env_example(root, gateway_domain, mirrors))
    _write(staging / "apply-init.sh", apply_init_sh())
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
                    help=f"预置应用 slug，逗号分隔；传空串表示只打平台（默认 {','.join(DEFAULT_SLUGS)}）")
    ap.add_argument("--deploy-root", default=DEFAULT_DEPLOY_ROOT,
                    help=f"服务器部署根，须为 Linux 绝对路径（默认 {DEFAULT_DEPLOY_ROOT}）")
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
    args = ap.parse_args(argv)

    slugs = tuple(s.strip() for s in (args.slugs or "").split(",") if s.strip())
    try:
        summary = build(out=Path(args.out) if args.out else None, slugs=slugs,
                        deploy_root=args.deploy_root, allow_stale_dist=args.allow_stale_dist,
                        gateway_domain=args.gateway_domain, apt_mirror=args.apt_mirror,
                        pypi_mirror=args.pypi_mirror, npm_registry=args.npm_registry)
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
