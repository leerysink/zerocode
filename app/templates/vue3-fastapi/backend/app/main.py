"""{{APP_NAME}} 后端骨架：/api 前缀 + health + demo_notes 示例；数据库连接从环境变量读取。

启动日志：进程一起来就往 **stdout** 打一段 banner（应用、环境、端口、数据库与连通性、就绪耗时），
`docker compose logs` 与 zerocode 预览日志流采集的都是 stdout，容器起不来时第一眼看这里。
"""
import logging
import os
import sys
import time
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

APP_NAME = "{{APP_NAME}}"
APP_SLUG = "{{SLUG}}"

DATABASE_URL = os.environ.get("DATABASE_URL", "")
engine = create_async_engine(DATABASE_URL) if DATABASE_URL else None
Session = async_sessionmaker(engine, expire_on_commit=False) if engine else None


def _setup_logging() -> None:
    """启动日志统一到 stdout（容器日志/平台日志流同源），级别可由 LOG_LEVEL 覆盖。"""
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        stream=sys.stdout,
        format="%(asctime)s %(levelname)-7s [{{SLUG}}] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        force=True,   # 覆盖 uvicorn/依赖库先前装好的 handler，保证 banner 一定看得到
    )


_setup_logging()
logger = logging.getLogger("app.startup")


def _masked_db_url(url: str) -> str:
    """连接串脱敏：只留驱动/主机/端口/库名，账号与口令一律不落日志。"""
    if not url:
        return "未注入 DATABASE_URL"
    try:
        p = urlsplit(url)
        host = p.hostname or "?"
        if p.port:
            host += f":{p.port}"
        return f"{p.scheme}://{host}{p.path}"
    except Exception:
        return "无法解析（已隐去）"


async def _db_probe() -> tuple[bool, str]:
    """启动期连通性自检：SELECT 1 成功即「可连接」；失败只降级成一行提示，绝不阻断启动。"""
    if not Session:
        return False, "跳过（DATABASE_URL 为空）"
    try:
        async with Session() as s:
            await s.execute(text("SELECT 1"))
        return True, "可连接"
    except Exception as e:
        return False, f"不可用：{type(e).__name__}: {str(e).splitlines()[0][:160]}"


@asynccontextmanager
async def lifespan(_: FastAPI):
    """启动/停止时的 banner 日志：端口、数据库、连通性与就绪耗时。"""
    started = time.monotonic()
    port = os.environ.get("APP_PORT") or os.environ.get("PORT")
    env = os.environ.get("APP_ENV", "dev")
    logger.info("=" * 62)
    logger.info("%s（%s）后端启动", APP_NAME, APP_SLUG)
    logger.info("  运行环境 : %s", env)
    logger.info("  监听地址 : %s",
                f"http://0.0.0.0:{port}" if port else "由启动参数决定（平台未注入 APP_PORT）")
    logger.info("  健康检查 : /api/health")
    logger.info("  数据库   : %s", _masked_db_url(DATABASE_URL))
    ok, db_msg = await _db_probe()
    (logger.info if ok else logger.warning)("  连通性   : %s", db_msg)
    logger.info("  就绪耗时 : %.0f ms", (time.monotonic() - started) * 1000)
    logger.info("=" * 62)
    yield
    logger.info("%s 后端停止，本次运行 %.1f s", APP_NAME, time.monotonic() - started)
    if engine:
        await engine.dispose()


app = FastAPI(title="{{APP_NAME}}", lifespan=lifespan)

@app.get("/api/health")
async def health():
    db = False
    if Session:
        try:
            async with Session() as s:
                await s.execute(text("SELECT 1"))
            db = True
        except Exception:
            db = False
    return {"status": "ok", "db": db}

class NoteIn(BaseModel):
    title: str

@app.get("/api/notes")
async def list_notes():
    if not Session:
        raise HTTPException(500, "DATABASE_URL 未配置")
    async with Session() as s:
        rows = (await s.execute(text(
            "SELECT id, title, created_at FROM demo_notes ORDER BY id DESC LIMIT 100"))).mappings().all()
    return [dict(r) for r in rows]

@app.post("/api/notes")
async def add_note(body: NoteIn):
    if not Session:
        raise HTTPException(500, "DATABASE_URL 未配置")
    async with Session() as s:
        row = (await s.execute(text(
            "INSERT INTO demo_notes (title) VALUES (:t) RETURNING id, title, created_at"),
            {"t": body.title})).mappings().first()
        await s.commit()
    return dict(row)
