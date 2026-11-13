# app/deploy/compose.py
"""dev/prod compose 渲染（规格 03 §4.1/§4.2）。compose 的 ${VAR} 插值由 deploy/.env 提供，
渲染器用 __KEY__ 标记避免与 ${} 冲突；产出 deploy/dev-compose.yml + deploy/.env，
发布时追加 deploy/prod-compose.yml 并把 .env 的 PROD_*/PROD_VERSION 补成真值。"""
from pathlib import Path
import os
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

# pip 下载缓存卷。dev 后端把依赖装进容器可写层，而停预览走 `compose down`（不带 -v）——容器一销毁
# 包和缓存同归于尽，下一次预览只能重新联网全量下载。这里只救缓存（下载不重复），不把卷直接挂到
# /usr/local/lib/python3.12/site-packages：那会连镜像自带的 pip/setuptools 一起遮住，卷初为空时
# 连 `pip` 命令本身都不在了。
PIP_VOLUME_NAME = "pip_cache"
PIP_MOUNT_PATH = "/root/.cache/pip"


def _pip_volume() -> str:
    """与 _fe_volume() 同构：挂载条目与顶层声明同源。"""
    return f"{PIP_VOLUME_NAME}:{PIP_MOUNT_PATH}"


PIP_VOLUME = _pip_volume()

# Node 小栈 backend 的依赖卷。挂载点与前端那个一样，但**必须是另一个卷**：
# 同一个具名卷挂到两个服务的 /src/node_modules，后起来的会把前一套依赖树盖掉（同 MOBILE_VOLUME_NAME 的理由）。
BE_NODE_VOLUME_NAME = "be_node_modules"
BE_NODE_MOUNT_PATH = "/src/node_modules"


def _be_node_volume() -> str:
    """与 _fe_volume() 同构：挂载条目与顶层声明同源。"""
    return f"{BE_NODE_VOLUME_NAME}:{BE_NODE_MOUNT_PATH}"


BE_NODE_VOLUME = _be_node_volume()

# 移动栈（vue3-springboot-mobile）dev 期的 uni-app H5 dev server：第二个内部服务。
# 它**没有 ports:**，一应用一对端口是平台地基（端口池 8210-8399、routes.json 一条、网关取一级子域），
# 手机访问的仍是 dev_port 上的管理端 vite，由它把 /h5 代理过来（规格 2026-11-03 D-8 / US-05）。
MOBILE_STACK = "vue3-springboot-mobile"
MOBILE_VOLUME_NAME = "mobile_node_modules"   # 与 fe_node_modules 同挂载点、不同服务不同卷：共用会把两套依赖树盖在一起
MOBILE_MOUNT_PATH = "/src/node_modules"


def _mobile_volume() -> str:
    """与 _fe_volume() 同构：挂载条目与顶层声明同源。"""
    return f"{MOBILE_VOLUME_NAME}:{MOBILE_MOUNT_PATH}"


MOBILE_VOLUME = _mobile_volume()

UPLOADS_VOLUME = "uploads"          # 上传件卷：镜像重建不丢历史文件（prod 唯一的文件持久化位）
REDIS_DATA_VOLUME = "redis-data"    # Redis 命名卷：prod 开 AOF，重启不丢会话/缓存基座

# prod-compose.yml 的落盘与渲染闸门：一个事实源，两处共用（写死单栈会让新栈发布必挂）
# 移动栈（vue3-springboot-mobile）与大栈同形：同一种 prod 拓扑，只多一层 H5 静态产物。
# static 也在名单里：它只有一个纯 nginx 服务（_PROD_COMPOSE_STATIC），发布链路按 prod_roles 走。
PUBLISHABLE_STACKS = ("vue3-fastapi", "vue3-node", "vue3-springboot", MOBILE_STACK, "static")

# 一个 role = 一个应用镜像 tag（zc-{slug}-{role}:prod-{version}）。写死两处的表征不在发布期
# （compose build 只看文件里的服务），在之后：static 的回滚永远报「backend 镜像已清理」、
# 删记录时多 rmi 一个不存在的 tag、keep-N 清理白跑一轮。
_ROLES_BY_STACK = {"static": ("frontend",)}
DEFAULT_PROD_ROLES = ("frontend", "backend")


def prod_roles(stack: str | None) -> tuple[str, ...]:
    """该栈在 prod 侧产出应用镜像的服务角色（回滚体检 / 记账 / keep-N 清理 / 删记录的遍历面）。"""
    return _ROLES_BY_STACK.get(stack, DEFAULT_PROD_ROLES)

# 栈私有运行密钥：缺则生成、有则保留。口令不进数据库、不进仓库、AI 禁读（deploy/ 是禁区）。
# 唯一事实源：.env 的密钥位与生成循环都从 STACK_SECRETS 派生，
# 不再额外模板化一份占位行（那会让「加一个密钥」变成两处改一处、静默漏位）。
STACK_SECRETS = {"vue3-springboot": ("JWT_SECRET", "REDIS_PASSWORD"),
                 MOBILE_STACK: ("JWT_SECRET", "REDIS_PASSWORD")}

_MARKER_RE = re.compile(r"__[A-Z][A-Z0-9_]*__")   # 只认渲染器自己的标记形态，不误伤 yaml 合法内容

# 依赖镜像源：从打包时烘进平台容器的环境变量读，零注入承诺——没设就不写对应行，compose 字节不变。
# dev 栈走 environment（pip/npm 在运行期装）；prod 栈的 pip/npm 跑在**构建容器**里，environment 对它
# 无效，所以走 build.args（ADR-0025，见 _prod_build_block）。两条通道都只认同一份打包参数。
_MIRROR_URL_RE = re.compile(r"https?://[^\s]+")


def _mirror_env(name: str) -> str | None:
    """从 os.environ 读镜像源地址；空值视同未设，畸形值整包失败（不静默丢弃）。"""
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return None
    if not _MIRROR_URL_RE.fullmatch(raw):
        raise DeployError(f"环境变量 {name}={raw!r} 形态非法（须 http(s):// 开头的完整地址）",
                          status=500)
    return raw


def _pip_env_block() -> str:
    """dev 栈 backend 的 PIP_INDEX_URL 注入块；没设时返回空串，compose 字节不变。"""
    v = _mirror_env("ZEROCODE_PYPI_MIRROR")
    return f"\n      PIP_INDEX_URL: {v}" if v else ""


def _npm_env_block() -> str:
    """dev 栈 frontend 的 NPM_CONFIG_REGISTRY 注入块；没设时返回空串，compose 字节不变。

    替换串自带前导 `\\n    environment:\\n      `：原本 frontend 服务没有 environment 段，
    注入块要自己开；默认空串时不留空行、不破 golden。
    """
    v = _mirror_env("ZEROCODE_NPM_REGISTRY")
    return f"\n    environment:\n      NPM_CONFIG_REGISTRY: {v}" if v else ""


def _npm_env_line() -> str:
    """**已有** environment: 块的服务补一行 NPM_CONFIG_REGISTRY（与 _pip_env_block 同形）。

    不能复用 _npm_env_block()：它自带 `environment:` 头，落在已有该键的服务里就是一个重复键，
    yaml 后一个覆盖前一个，dev 后端会静默丢掉 DATABASE_URL。
    """
    v = _mirror_env("ZEROCODE_NPM_REGISTRY")
    return f"\n      NPM_CONFIG_REGISTRY: {v}" if v else ""

def _prod_build_block(context: str, arg_name: str | None, url: str | None) -> str:
    """prod 栈一个 build 标记的渲染值：没有可注的参数时逐字回退到 `build: ../<context>` 标量形态。

    `arg_name=None` 表示这个构建上下文根本没有镜像源消费方（大栈后端是 maven），与「消费方在但
    平台没配源」走同一条回退：产物字节都得与 ADR-0025 之前一致。
    返回值不带行尾换行——模板里标记独占一行，行尾由模板自己提供，这样未注入分支与旧模板字节相同
    （ADR-0017「注入块不得独占一行」的约束在这里换了形态：块永不空，所以标记可以独占一行）。
    消费方是应用自己的 Dockerfile（`ARG` + `ENV`）；没声明 ARG 的应用只会收到 BuildKit 的一条
    「build-arg not consumed」警告，不影响构建。
    """
    if not url or not arg_name:
        return f"    build: ../{context}"
    return (f"    build:\n"
            f"      context: ../{context}\n"
            f"      args:\n"
            f"        {arg_name}: {url}")

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
      - __PIP_VOL__
    environment:
      DATABASE_URL: postgresql+asyncpg://${DEV_USER}:${DEV_PASSWORD}@zerocode-pg16:5432/${DEV_DB}
      APP_PORT: "8000"__PIP_ENV__
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
    command: sh -c "npm install && npm run dev -- --host 0.0.0.0 --port 5173"__NPM_ENV__
    ports:
      - "__DEV_PORT__:5173"
    depends_on:
      - backend
volumes:
  __FE_VOL_NAME__: {}
  __PIP_VOL_NAME__: {}
"""

# Node 小栈（Express 5 + TS + pg-promise）的 dev 栈。与 _DEV_COMPOSE 的三处刻意差异：
# ① DATABASE_URL 是**纯 postgresql://**——pg-promise 不认 SQLAlchemy 的 `+asyncpg` 方言前缀，
#    注错不在创建期报，而是预览起来、后端连不上库；
# ② 后端起 `npm run dev`（脚本即事实源，dev 里跑什么由工程自己的 package.json 定，与前端同待遇）；
# ③ 依赖卷是 be_node_modules 而非 pip 缓存卷。其余（migrate 闸门、端口对外、网络）逐字节同形。
_DEV_COMPOSE_NODE = """name: zc-__SLUG__-dev
networks:
  default: {}
  zcnet:
    external: true
services:
__MIGRATE_SVC__
  backend:
    image: node:20-alpine
    working_dir: /src
    volumes:
      - ../backend:/src
      - __BE_NODE_VOL__
    environment:
      DATABASE_URL: postgresql://${DEV_USER}:${DEV_PASSWORD}@zerocode-pg16:5432/${DEV_DB}
      APP_PORT: "8000"__NPM_ENV_LINE__
    command: sh -c "npm install && npm run dev"
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
    command: sh -c "npm install && npm run dev -- --host 0.0.0.0 --port 5173"__NPM_ENV__
    ports:
      - "__DEV_PORT__:5173"
    depends_on:
      - backend
volumes:
  __FE_VOL_NAME__: {}
  __BE_NODE_VOL_NAME__: {}
"""

_MIGRATE_SVC = """  migrate:
    image: zerocode-sqlrunner:2
    environment:
      TARGET_DB: ${DEV_DB}
      TARGET_USER: ${DEV_USER}
      TARGET_PASSWORD: ${DEV_PASSWORD}
      TARGET_ENV: dev
    volumes:
      - ../schema:/schema:ro
      - ../sqlchange:/sql:ro
    networks: [zcnet]
    restart: "no"
"""

# 大型栈的 dev 预览：后端不打包、源码挂进去现场 mvn spring-boot:run（依赖走 per-app .m2 卷）。
# Redis 是本栈私有运行时，只挂内部网、不对外暴端口；服务名故意不叫 redis——backend 同时上了全平台
# 共享的 zcnet，那里若出现另一个同名容器，短名解析就要在两个网络之间赌一个。
# 三个密钥位都带 `:?`：空值下 compose 直接拒收，而不是起一个 `--requirepass ""` 的无密码 Redis
# （render_env 产出的就是空位，真值靠 ensure_deploy_files 回填——万一有人手工清过 .env，要响）。
_DEV_COMPOSE_SB = """name: zc-__SLUG__-dev
networks:
  default: {}
  zcnet:
    external: true
services:
__MIGRATE_SVC__
  appredis:
    image: redis:7-alpine
    command: ["redis-server", "--requirepass", "${REDIS_PASSWORD:?not backfilled, see deploy/.env}"]
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
      SPRING_DATA_REDIS_HOST: appredis
      SPRING_DATA_REDIS_PASSWORD: ${REDIS_PASSWORD:?not backfilled, see deploy/.env}
      SERVER_PORT: "8080"
      JWT_SECRET: ${JWT_SECRET:?not backfilled, see deploy/.env}
      UPLOAD_DIR: /src/uploads
    command: mvn -B spring-boot:run
    networks: [default, zcnet]
    depends_on:
      migrate:
        condition: service_completed_successfully
      appredis:
        condition: service_started
  frontend:
    image: node:20-alpine
    working_dir: /src
    volumes:
      - ../frontend:/src
      - __FE_VOL__
    command: sh -c "npm install && npm run dev -- --host 0.0.0.0 --port 5173"__NPM_ENV__
    ports:
      - "__DEV_PORT__:5173"
    depends_on:
      - backend
__MOBILE_SVC__volumes:
  __FE_VOL_NAME__: {}
  __M2_VOL_NAME__: {}
__MOBILE_VOL_DECL__"""

# 移动栈 dev 的第二个内部服务：bind 挂 frontend/mobile（uni-app 工程住在管理端工程的子目录里，
# prod 才能由同一个前端镜像构建上下文把 H5 烘进 html/h5，见 ADR-0039 与 frontend/Dockerfile 覆盖层）。
# node_modules 走独立具名卷：宿主机那份永远是空的，prod 构建上下文因此不会被整套依赖树拖住。
# 关键：这一条没有 ports:——对外仍只有 __DEV_PORT__ 一个端口，手机经管理端 vite 的 /h5 代理打进来。
_MOBILE_SVC = """  mobile:
    image: node:20-alpine
    working_dir: /src
    volumes:
      - ../frontend/mobile:/src
      - __MOBILE_VOL__
    command: sh -c "npm install && npm run dev:h5"__NPM_ENV__
    networks: [default]
    depends_on:
      - backend
"""

_FE_ONLY = """name: zc-__SLUG__-dev
services:
  frontend:
    image: node:20-alpine
    working_dir: /src
    volumes:
      - ../frontend:/src
      - __FE_VOL__
    command: sh -c "npm install && npm run dev -- --host 0.0.0.0 --port 5173"__NPM_ENV__
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

# 小栈 prod 后端的两处栈差异（其余拓扑逐字节相同）：
#   (数据库连接串方言, 构建期 build arg 名, 该参数读哪个平台环境变量)
# 方言不是可选项：pg-promise 只认纯 postgresql://，SQLAlchemy 的 +asyncpg 前缀对它非法，而注错的
# 表征不在创建期——prod 栈照样起，只是 /api/health 的 db 永远 false。
# maven 栈不在表里：它的数据源是 SPRING_DATASOURCE_*，仓库镜像也没进本次参数范围。
_PROD_SMALL_BACKEND = {
    "vue3-fastapi": ("postgresql+asyncpg://${PROD_USER}:${PROD_PASSWORD}@zerocode-pg16:5432/${PROD_DB}",
                     "PIP_INDEX_URL", "ZEROCODE_PYPI_MIRROR"),
    "vue3-node": ("postgresql://${PROD_USER}:${PROD_PASSWORD}@zerocode-pg16:5432/${PROD_DB}",
                  "NPM_CONFIG_REGISTRY", "ZEROCODE_NPM_REGISTRY"),
}

_PROD_COMPOSE = """name: zc-__SLUG__-prod
networks:
  default: {}
  zcnet:
    external: true
services:
__MIGRATE_PROD__
  backend:
    image: zc-__SLUG__-backend:prod-${PROD_VERSION}
__BUILD_BACKEND__
    environment:
      DATABASE_URL: __BACKEND_DB_URL__
      APP_PORT: "8000"
    networks: [default, zcnet]
    restart: unless-stopped
    depends_on:
      migrate:
        condition: service_completed_successfully
  frontend:
    image: zc-__SLUG__-frontend:prod-${PROD_VERSION}
__BUILD_FRONTEND__
    networks: [default]
    ports:
      - "__PROD_PORT__:80"
    restart: unless-stopped
    depends_on:
      - backend
"""

# static 的 prod：一个纯 nginx 站点。三处「没有」都有代价，不是省字。
#   ① 没有 migrate——本栈不铺数据轨（scaffold.has_data_track("static") 为假），产物里没有 sqlchange/，
#      run_sqlchange 的 fail-closed 闸门见不到目录就 ok=False，挂着它发布必红在迁移步；
#   ② 没有 backend——prod.py 按 prod_roles("static") 只认 frontend 一个 tag；
#   ③ 没有 zcnet——外部网络不存在时 compose up 直接失败，而静态站点没有任何进程要访 zerocode-pg16。
# 只在 default 网络上：网关按宿主端口（__PROD_PORT__ -> 80）路由，不经容器网络。
_PROD_COMPOSE_STATIC = """name: zc-__SLUG__-prod
networks:
  default: {}
services:
  frontend:
    image: zc-__SLUG__-frontend:prod-${PROD_VERSION}
__BUILD_FRONTEND__
    networks: [default]
    ports:
      - "__PROD_PORT__:80"
    restart: unless-stopped
"""

_MIGRATE_PROD = """  migrate:
    image: zerocode-sqlrunner:2
    environment:
      TARGET_DB: ${PROD_DB}
      TARGET_USER: ${PROD_USER}
      TARGET_PASSWORD: ${PROD_PASSWORD}
      TARGET_ENV: prod
    volumes:
      - ../schema:/schema:ro
      - ../sqlchange:/sql:ro
    networks: [zcnet]
    restart: "no"
"""

_PROD_COMPOSE_SB = """name: zc-__SLUG__-prod
networks:
  default: {}
  zcnet:
    external: true
services:
__MIGRATE_PROD__
  appredis:
    image: redis:7-alpine
    command: ["redis-server", "--requirepass", "${REDIS_PASSWORD:?not backfilled, see deploy/.env}", "--appendonly", "yes"]
    volumes:
      - __REDIS_DATA_VOL__:/data
    networks: [default]
    restart: unless-stopped
  backend:
    image: zc-__SLUG__-backend:prod-${PROD_VERSION}
__BUILD_BACKEND__
    environment:
      SPRING_DATASOURCE_URL: jdbc:postgresql://zerocode-pg16:5432/${PROD_DB}?currentSchema=public&stringtype=unspecified
      SPRING_DATASOURCE_USERNAME: ${PROD_USER}
      SPRING_DATASOURCE_PASSWORD: ${PROD_PASSWORD}
      SPRING_DATA_REDIS_HOST: appredis
      SPRING_DATA_REDIS_PASSWORD: ${REDIS_PASSWORD:?not backfilled, see deploy/.env}
      SERVER_PORT: "8080"
      JWT_SECRET: ${JWT_SECRET:?not backfilled, see deploy/.env}
      UPLOAD_DIR: /uploads
    volumes:
      - __UPLOADS_VOL__:/uploads
    networks: [default, zcnet]
    restart: unless-stopped
    depends_on:
      migrate:
        condition: service_completed_successfully
      appredis:
        condition: service_started
  frontend:
    image: zc-__SLUG__-frontend:prod-${PROD_VERSION}
__BUILD_FRONTEND__
    networks: [default]
    ports:
      - "__PROD_PORT__:80"
    restart: unless-stopped
    depends_on:
      - backend
volumes:
  __UPLOADS_VOL_NAME__: {}
  __REDIS_DATA_VOL_NAME__: {}
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
    elif stack == "vue3-node":
        tpl, fragments = _DEV_COMPOSE_NODE, (_MIGRATE_SVC,)   # 迁移闸门逐字复用（同库同轨同镜像）
    elif stack == "vue3-springboot":
        tpl, fragments = _DEV_COMPOSE_SB, (_MIGRATE_SVC,)   # 迁移闸门完全复用（同库同轨同镜像）
    elif stack == MOBILE_STACK:
        tpl, fragments = _DEV_COMPOSE_SB, (_MIGRATE_SVC, _MOBILE_SVC)   # 只多一个不对外发布的 dev server
    else:
        raise DeployError(f"栈模板未支持: {stack}", status=500)
    sources = tpl + "".join(fragments)          # 本次渲染用到的全部标记面
    y = (tpl
         .replace("__MIGRATE_SVC__", _MIGRATE_SVC)
         # 注入必须早于 __MOBILE_VOL__：片段自己带着别的标记，顺序反了就留一个未替换的 __MOBILE_VOL__。
         # 标记与 volumes: 同行（不是独立一行）：非移动栈替换成空串时不留空行，大栈产物字节不变。
         .replace("__MOBILE_SVC__", _MOBILE_SVC if stack == MOBILE_STACK else "")
         .replace("__MOBILE_VOL_DECL__",
                  f"  {MOBILE_VOLUME_NAME}: {{}}\n" if stack == MOBILE_STACK else "")
         .replace("__DEV_PORT__", str(dev_port))
         .replace("__FE_VOL__", _fe_volume())
         .replace("__FE_VOL_NAME__", VOLUME_NAME)
         .replace("__MOBILE_VOL__", _mobile_volume())
         .replace("__M2_VOL__", _m2_volume())
         .replace("__M2_VOL_NAME__", M2_VOLUME_NAME)
         .replace("__PIP_VOL__", _pip_volume())
         .replace("__PIP_VOL_NAME__", PIP_VOLUME_NAME)
         .replace("__BE_NODE_VOL__", _be_node_volume())
         .replace("__BE_NODE_VOL_NAME__", BE_NODE_VOLUME_NAME)
         .replace("__SLUG__", slug)
         .replace("__PIP_ENV__", _pip_env_block())
         .replace("__NPM_ENV__", _npm_env_block())
         .replace("__NPM_ENV_LINE__", _npm_env_line()))
    # 自检：模板声明过的标记必须全部被替换（与 scaffold 的 {{ 残留自检同构，审查 I-3）
    residue = sorted(m for m in _markers_of(sources) if m in y)
    if residue:
        raise DeployError(f"compose 渲染标记残留: {residue}", status=500)
    return y

def render_prod_compose(meta: dict) -> str:
    """prod 栈（规格 03 §4.3 / 07）：镜像即产物，不挂源码卷、无 dev server 命令；
    仅前端对外（prod 端口 -> 容器 80，backend 经 nginx 反代，T3b）；migrate 闸门指 PROD_*。"""
    stack = meta.get("stack")
    if stack == "static":
        tpl, fragments = _PROD_COMPOSE_STATIC, ()
        db_url, be_arg, be_env = "", None, None
    elif stack in _PROD_SMALL_BACKEND:
        tpl, fragments = _PROD_COMPOSE, (_MIGRATE_PROD,)
        db_url, be_arg, be_env = _PROD_SMALL_BACKEND[stack]
    elif stack in ("vue3-springboot", MOBILE_STACK):
        # 移动栈的 prod 与大栈逐字节同形：H5 是前端镜像里的一层静态产物（html/h5），
        # 不是第三个可发布服务——prod.py 的镜像记账/回滚体检只认 frontend+backend 两个 role。
        tpl, fragments = _PROD_COMPOSE_SB, (_MIGRATE_PROD,)   # migrate 片段逐字复用（同库同轨同镜像）
        db_url, be_arg, be_env = "", None, None               # maven 后端：SPRING_* 数据源，无仓库镜像参数可注
    else:
        raise DeployError(f"栈发布未支持: {stack}", status=400)
    sources = tpl + "".join(fragments)              # 本次渲染用到的全部标记面
    # build.args 只给真有消费方的构建上下文：小栈后端按 _PROD_SMALL_BACKEND 各自的源、两栈前端是 npm；
    # 大型栈后端是 maven（be_arg=None），标记存在但渲染回标量形态，产物与 ADR-0025 之前逐字节相同。
    backend_url = _mirror_env(be_env) if be_env else None
    y = (tpl
         .replace("__MIGRATE_PROD__", _MIGRATE_PROD)
         .replace("__BACKEND_DB_URL__", db_url)
         .replace("__BUILD_BACKEND__", _prod_build_block("backend", be_arg, backend_url))
         .replace("__BUILD_FRONTEND__", _prod_build_block(
             "frontend", "NPM_CONFIG_REGISTRY", _mirror_env("ZEROCODE_NPM_REGISTRY")))
         .replace("__PROD_PORT__", str(meta["ports"]["prod"]))
         .replace("__UPLOADS_VOL__", UPLOADS_VOLUME)
         .replace("__UPLOADS_VOL_NAME__", UPLOADS_VOLUME)
         .replace("__REDIS_DATA_VOL__", REDIS_DATA_VOLUME)
         .replace("__REDIS_DATA_VOL_NAME__", REDIS_DATA_VOLUME)
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
    if meta.get("stack") in PUBLISHABLE_STACKS:
        _write_if_changed(d / "prod-compose.yml", render_prod_compose(meta))
