# app/deploy/compose.py
"""dev/prod compose 渲染（规格 03 §4.1/§4.2）。compose 的 ${VAR} 插值由 deploy/.env 提供，
渲染器用 __KEY__ 标记避免与 ${} 冲突；产出 deploy/dev-compose.yml + deploy/.env，
发布时追加 deploy/prod-compose.yml 并把 .env 的 PROD_*/PROD_VERSION 补成真值。"""
from pathlib import Path
import re
import secrets

from app.deploy.errors import DeployError

VOLUME_NAME = "fe_node_modules"   # 具名卷唯一事实源：挂载条目与顶层 volumes: 声明都由它派生
FE_MOUNT_PATH = "/src/node_modules"


def _fe_volume() -> str:
    """按调用时的 VOLUME_NAME 组合服务挂载条目，确保与顶层声明同源（审查 I-1）。"""
    return f"{VOLUME_NAME}:{FE_MOUNT_PATH}"


FE_VOLUME = _fe_volume()          # 派生常量，等价于 _fe_volume()；改名只动 VOLUME_NAME

M2_VOLUME_NAME = "m2"             # Maven 本地仓库：per-app（compose 自动加项目前缀），共享会被无锁并发写坏
M2_MOUNT_PATH = "/root/.m2"


def _m2_volume() -> str:
    """与 _fe_volume() 同构：挂载条目与顶层声明同源。"""
    return f"{M2_VOLUME_NAME}:{M2_MOUNT_PATH}"


M2_VOLUME = _m2_volume()

# 栈私有运行密钥：缺则生成、有则保留。口令不进数据库、不进仓库、AI 禁读（deploy/ 是禁区）。
# 唯一事实源：.env 的密钥位与生成循环都从 STACK_SECRETS 派生，
# 不再额外模板化一份占位行（那会让「加一个密钥」变成两处改一处、静默漏位）。
STACK_SECRETS = {"vue3-springboot": ("JWT_SECRET", "REDIS_PASSWORD")}

_MARKER_RE = re.compile(r"__[A-Z][A-Z0-9_]*__")   # 只认渲染器自己的标记形态，不误伤 yaml 合法内容

_DEV_COMPOSE = """name: zc-__SLUG__-dev
networks:
  default: {}
  zcnet:
    external: true
services:
__MIGRATE_SVC__
  backend:
    image: python:3.12-slim
    working_dir: /src
    volumes:
      - ../backend:/src
    environment:
      DATABASE_URL: postgresql+asyncpg://${DEV_USER}:${DEV_PASSWORD}@zerocode-pg16:5432/${DEV_DB}
      APP_PORT: "8000"
    command: sh -c "pip install -r requirements.txt && uvicorn app.main:app --reload --host 0.0.0.0 --port 8000"
    networks: [default, zcnet]
    depends_on:
      migrate:
        condition: service_completed_successfully
  frontend:
    image: node:20-alpine
    working_dir: /src
    volumes:
      - ../frontend:/src
      - __FE_VOL__
    command: sh -c "npm install && npm run dev -- --host 0.0.0.0 --port 5173"
    ports:
      - "__DEV_PORT__:5173"
    depends_on:
      - backend
volumes:
  __FE_VOL_NAME__: {}
"""

_MIGRATE_SVC = """  migrate:
    image: zerocode-sqlrunner:1
    environment:
      TARGET_DB: ${DEV_DB}
      TARGET_USER: ${DEV_USER}
      TARGET_PASSWORD: ${DEV_PASSWORD}
    volumes:
      - ../schema:/schema:ro
      - ../sqlchange:/sql:ro
    networks: [zcnet]
    restart: "no"
"""

# 大型栈的 dev 预览：后端不打包、源码挂进去现场 mvn spring-boot:run（依赖走 per-app .m2 卷）；
# Redis 是本栈的私有运行时，只在 compose 内部网、不对外暴端口（口令走 .env 的 ${REDIS_PASSWORD}）。
_DEV_COMPOSE_SB = """name: zc-__SLUG__-dev
networks:
  default: {}
  zcnet:
    external: true
services:
__MIGRATE_SVC__
  redis:
    image: redis:7-alpine
    command: ["redis-server", "--requirepass", "${REDIS_PASSWORD}"]
    networks: [default]
  backend:
    image: maven:3.9-eclipse-temurin-17
    working_dir: /src
    volumes:
      - ../backend:/src
      - __M2_VOL__
    environment:
      SPRING_DATASOURCE_URL: jdbc:postgresql://zerocode-pg16:5432/${DEV_DB}?currentSchema=public&stringtype=unspecified
      SPRING_DATASOURCE_USERNAME: ${DEV_USER}
      SPRING_DATASOURCE_PASSWORD: ${DEV_PASSWORD}
      SPRING_DATA_REDIS_HOST: redis
      SPRING_DATA_REDIS_PASSWORD: ${REDIS_PASSWORD}
      SERVER_PORT: "8080"
      JWT_SECRET: ${JWT_SECRET}
      UPLOAD_DIR: /src/uploads
    command: mvn -B spring-boot:run
    networks: [default, zcnet]
    depends_on:
      migrate:
        condition: service_completed_successfully
      redis:
        condition: service_started
  frontend:
    image: node:20-alpine
    working_dir: /src
    volumes:
      - ../frontend:/src
      - __FE_VOL__
    command: sh -c "npm install && npm run dev -- --host 0.0.0.0 --port 5173"
    ports:
      - "__DEV_PORT__:5173"
    depends_on:
      - backend
volumes:
  __FE_VOL_NAME__: {}
  __M2_VOL_NAME__: {}
"""

_FE_ONLY = """name: zc-__SLUG__-dev
services:
  frontend:
    image: node:20-alpine
    working_dir: /src
    volumes:
      - ../frontend:/src
      - __FE_VOL__
    command: sh -c "npm install && npm run dev -- --host 0.0.0.0 --port 5173"
    ports:
      - "__DEV_PORT__:5173"
volumes:
  __FE_VOL_NAME__: {}
"""

_ENV_TMPL = """DEV_PORT=__DEV_PORT__
PROD_PORT=__PROD_PORT__
DEV_DB=__DEV_DB__
DEV_USER=__DEV_USER__
DEV_PASSWORD=__DEV_PASSWORD__
PROD_DB=__PROD_DB__
PROD_USER=__PROD_USER__
PROD_PASSWORD=__PROD_PASSWORD__
"""

_PROD_COMPOSE = """name: zc-__SLUG__-prod
networks:
  default: {}
  zcnet:
    external: true
services:
__MIGRATE_PROD__
  backend:
    image: zc-__SLUG__-backend:prod-${PROD_VERSION}
    build: ../backend
    environment:
      DATABASE_URL: postgresql+asyncpg://${PROD_USER}:${PROD_PASSWORD}@zerocode-pg16:5432/${PROD_DB}
      APP_PORT: "8000"
    networks: [default, zcnet]
    restart: unless-stopped
    depends_on:
      migrate:
        condition: service_completed_successfully
  frontend:
    image: zc-__SLUG__-frontend:prod-${PROD_VERSION}
    build: ../frontend
    networks: [default]
    ports:
      - "__PROD_PORT__:80"
    restart: unless-stopped
    depends_on:
      - backend
"""

_MIGRATE_PROD = """  migrate:
    image: zerocode-sqlrunner:1
    environment:
      TARGET_DB: ${PROD_DB}
      TARGET_USER: ${PROD_USER}
      TARGET_PASSWORD: ${PROD_PASSWORD}
    volumes:
      - ../schema:/schema:ro
      - ../sqlchange:/sql:ro
    networks: [zcnet]
    restart: "no"
"""

# 凭据键：单向推进语义的适用范围（端口/版本不在此列，由 meta 与发布流程各自决定）
_CRED_KEYS = ("DEV_DB", "DEV_USER", "DEV_PASSWORD", "PROD_DB", "PROD_USER", "PROD_PASSWORD")

def _markers_of(templates: str) -> set:
    """模板（含被注入的片段）里出现过的标记集合——残留自检的已知标记来源。"""
    return set(_MARKER_RE.findall(templates))


def render_dev_compose(meta: dict) -> str:
    slug, dev_port = meta["slug"], meta["ports"]["dev"]
    stack = meta.get("stack")
    if stack == "static":
        tpl, fragments = _FE_ONLY, ()
    elif stack == "vue3-fastapi":
        tpl, fragments = _DEV_COMPOSE, (_MIGRATE_SVC,)
    elif stack == "vue3-springboot":
        tpl, fragments = _DEV_COMPOSE_SB, (_MIGRATE_SVC,)   # 迁移闸门完全复用（同库同轨同镜像）
    else:
        raise DeployError(f"栈模板未支持: {stack}", status=500)
    sources = tpl + "".join(fragments)          # 本次渲染用到的全部标记面
    y = (tpl
         .replace("__MIGRATE_SVC__", _MIGRATE_SVC)
         .replace("__DEV_PORT__", str(dev_port))
         .replace("__FE_VOL__", _fe_volume())
         .replace("__FE_VOL_NAME__", VOLUME_NAME)
         .replace("__M2_VOL__", _m2_volume())
         .replace("__M2_VOL_NAME__", M2_VOLUME_NAME)
         .replace("__SLUG__", slug))
    # 自检：模板声明过的标记必须全部被替换（与 scaffold 的 {{ 残留自检同构，审查 I-3）
    residue = sorted(m for m in _markers_of(sources) if m in y)
    if residue:
        raise DeployError(f"compose 渲染标记残留: {residue}", status=500)
    return y

def render_prod_compose(meta: dict) -> str:
    """prod 栈（规格 03 §4.3 / 07）：镜像即产物，不挂源码卷、无 dev server 命令；
    仅前端对外（prod 端口 -> 容器 80，backend 经 nginx 反代，T3b）；migrate 闸门指 PROD_*。"""
    stack = meta.get("stack")
    if stack != "vue3-fastapi":
        raise DeployError(f"栈发布未支持（M2 范围）: {stack}", status=400)
    sources = _PROD_COMPOSE + _MIGRATE_PROD     # 本次渲染用到的全部标记面
    y = (_PROD_COMPOSE
         .replace("__MIGRATE_PROD__", _MIGRATE_PROD)
         .replace("__PROD_PORT__", str(meta["ports"]["prod"]))
         .replace("__SLUG__", meta["slug"]))
    residue = sorted(m for m in _markers_of(sources) if m in y)
    if residue:
        raise DeployError(f"compose 渲染标记残留: {residue}", status=500)
    return y

def render_env(meta: dict, creds: dict) -> str:
    dev = creds.get("dev") or {}
    prod = creds.get("prod") or {}
    e = (_ENV_TMPL
            .replace("__DEV_PORT__", str(meta["ports"]["dev"]))
            .replace("__PROD_PORT__", str(meta["ports"]["prod"]))
            .replace("__DEV_DB__", dev.get("db", ""))
            .replace("__DEV_USER__", dev.get("user", ""))
            .replace("__DEV_PASSWORD__", dev.get("password", ""))
            .replace("__PROD_DB__", prod.get("db", ""))
            .replace("__PROD_USER__", prod.get("user", ""))
            .replace("__PROD_PASSWORD__", prod.get("password", "")))
    # 密钥位先留空行（渲染态就是空，真值由 ensure_deploy_files 补），保证健在与否只看栈、不看调用方
    keys = STACK_SECRETS.get(meta.get("stack"))
    if keys:
        e += "".join(f"{k}=\n" for k in keys)
    return e

def _env_get(text_: str, key: str) -> str | None:
    """.env 里某个键的当前值（无此键返回 None，空值返回空串）。"""
    for line in text_.splitlines():
        if line.startswith(key + "="):
            return line.split("=", 1)[1]
    return None

def _env_set(text_: str, key: str, val: str) -> str:
    """改写（没有则追加）某个键，保证全文件只有一行（不堆重复行）。"""
    return "\n".join(l for l in text_.splitlines() if not l.startswith(key + "=")) + f"\n{key}={val}\n"

def _write_if_changed(f: Path, content: str) -> None:
    """内容一致就不落盘（幂等；三处写盘样板的收口）。"""
    if not f.exists() or f.read_text(encoding="utf-8") != content:
        f.write_text(content, encoding="utf-8", newline="")

def ensure_deploy_files(app_root: Path, meta: dict, creds: dict, version: str | None = None) -> None:
    """渲染并落盘 deploy/{dev-compose.yml,.env}；栈支持发布时追加 prod-compose.yml。

    .env 的凭据是「单向推进」的（D-T4）：本次没带/带空的一侧（creds.dev 或 creds.prod）
    保留既有非空值，不冲空——创建流程只传 dev 不冲 PROD_*，发布流程 dev 供给缺位也不冲 DEV_*。
    version=None 时保留既有 PROD_VERSION（发布流程显式传新值才更新）。

    prod-compose.yml 的落盘条件只看栈（T4 波裁定）：文件内容只依赖 meta，凭据全走 .env 的
    `${}` 插值——首次发布 build 时 prod 凭据还没供给，也必须已经有这份文件可 build。
    """
    d = app_root / "deploy"
    d.mkdir(parents=True, exist_ok=True)
    y = render_dev_compose(meta)
    e = render_env(meta, creds)
    ef = d / ".env"
    prev = ef.read_text(encoding="utf-8") if ef.exists() else ""
    # PROD_VERSION 是唯一「渲染态」键：不随 meta/creds 生成，只被发布流程显式更新
    pv = version if version is not None else _env_get(prev, "PROD_VERSION")
    if pv is not None:
        e = e + f"PROD_VERSION={pv}\n"
    # 栈私有密钥：本次没带值 → 沿用 .env 既有值 → 还没有就现场生成
    # （发布/重启都会重渲染 .env，冲空在跑栈的密钥会让 Redis 与 token 全线失配）
    for k in STACK_SECRETS.get(meta.get("stack"), ()):
        if not _env_get(e, k):
            e = _env_set(e, k, _env_get(prev, k) or secrets.token_urlsafe(32))
    # 已有 .env 的凭据若新 creds 没带（只传 dev / 只传 prod 的调用），保留旧值不冲空
    for k in _CRED_KEYS:
        old = _env_get(prev, k)
        if old and not _env_get(e, k):
            e = _env_set(e, k, old)
    _write_if_changed(d / "dev-compose.yml", y)
    _write_if_changed(ef, e)
    if meta.get("stack") == "vue3-fastapi":
        _write_if_changed(d / "prod-compose.yml", render_prod_compose(meta))
