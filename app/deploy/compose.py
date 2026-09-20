# app/deploy/compose.py
"""dev compose 渲染（规格 03 §4.1/§4.2）。compose 的 ${VAR} 插值由 deploy/.env 提供，
渲染器用 __KEY__ 标记避免与 ${} 冲突；产出 deploy/dev-compose.yml + deploy/.env。"""
from pathlib import Path

FE_VOLUME = "fe_node_modules:/src/node_modules"

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

def render_dev_compose(meta: dict) -> str:
    slug, dev_port = meta["slug"], meta["ports"]["dev"]
    if meta["stack"] == "static":
        return (_FE_ONLY.replace("__SLUG__", slug)
                .replace("__DEV_PORT__", str(dev_port))
                .replace("__FE_VOL__", FE_VOLUME))
    return (_DEV_COMPOSE.replace("__SLUG__", slug)
            .replace("__MIGRATE_SVC__", _MIGRATE_SVC)
            .replace("__DEV_PORT__", str(dev_port))
            .replace("__FE_VOL__", FE_VOLUME))

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
