# app/deploy/compose.py
"""dev compose 渲染（规格 03 §4.1/§4.2）。compose 的 ${VAR} 插值由 deploy/.env 提供，
渲染器用 __KEY__ 标记避免与 ${} 冲突；产出 deploy/dev-compose.yml + deploy/.env。"""
from pathlib import Path
import re

from app.deploy.errors import DeployError

VOLUME_NAME = "fe_node_modules"   # 具名卷唯一事实源：挂载条目与顶层 volumes: 声明都由它派生
FE_MOUNT_PATH = "/src/node_modules"


def _fe_volume() -> str:
    """按调用时的 VOLUME_NAME 组合服务挂载条目，确保与顶层声明同源（审查 I-1）。"""
    return f"{VOLUME_NAME}:{FE_MOUNT_PATH}"


FE_VOLUME = _fe_volume()          # 派生常量，等价于 _fe_volume()；改名只动 VOLUME_NAME

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
      - ../sqlchange:/sql:ro
    networks: [zcnet]
    restart: "no"
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
    else:
        raise DeployError(f"栈模板未支持: {stack}", status=500)
    sources = tpl + "".join(fragments)          # 本次渲染用到的全部标记面
    y = (tpl
         .replace("__MIGRATE_SVC__", _MIGRATE_SVC)
         .replace("__DEV_PORT__", str(dev_port))
         .replace("__FE_VOL__", _fe_volume())
         .replace("__FE_VOL_NAME__", VOLUME_NAME)
         .replace("__SLUG__", slug))
    # 自检：模板声明过的标记必须全部被替换（与 scaffold 的 {{ 残留自检同构，审查 I-3）
    residue = sorted(m for m in _markers_of(sources) if m in y)
    if residue:
        raise DeployError(f"compose 渲染标记残留: {residue}", status=500)
    return y

def render_env(meta: dict, creds: dict) -> str:
    dev = creds.get("dev") or {}
    prod = creds.get("prod") or {}
    return (_ENV_TMPL
            .replace("__DEV_PORT__", str(meta["ports"]["dev"]))
            .replace("__PROD_PORT__", str(meta["ports"]["prod"]))
            .replace("__DEV_DB__", dev.get("db", ""))
            .replace("__DEV_USER__", dev.get("user", ""))
            .replace("__DEV_PASSWORD__", dev.get("password", ""))
            .replace("__PROD_DB__", prod.get("db", ""))
            .replace("__PROD_USER__", prod.get("user", ""))
            .replace("__PROD_PASSWORD__", prod.get("password", "")))

def ensure_deploy_files(app_root: Path, meta: dict, creds: dict) -> None:
    d = app_root / "deploy"
    d.mkdir(parents=True, exist_ok=True)
    y = render_dev_compose(meta)
    e = render_env(meta, creds)
    yf, ef = d / "dev-compose.yml", d / ".env"
    if not yf.exists() or yf.read_text(encoding="utf-8") != y:
        yf.write_text(y, encoding="utf-8", newline="")
    if not ef.exists() or ef.read_text(encoding="utf-8") != e:
        ef.write_text(e, encoding="utf-8", newline="")
