"""陌如玉 后端：/api 前缀 + health + 桌面设置 + 便签记事本。

数据库连接串一律来自环境变量 DATABASE_URL（平台注入），代码中不硬编码任何连接信息与库名。

启动日志：进程一起来就往 **stdout** 打一段 banner（应用、环境、端口、数据库与连通性、就绪耗时），
`docker compose logs` 与 zerocode 预览日志流采集的都是 stdout，容器起不来时第一眼看这里。
"""
import json
import logging
import os
import sys
import time
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

APP_NAME = "陌如玉"
APP_SLUG = "test1"

DATABASE_URL = os.environ.get("DATABASE_URL", "")


def _make_engine(url: str):
    """连接串异常（如平台注入的口令含未转义字符）时返回 None：后端照常启动，接口回落默认值。"""
    if not url:
        return None
    try:
        return create_async_engine(url, pool_pre_ping=True)
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("app.startup").error("数据库连接串无效，已以无数据库模式启动：%s", type(exc).__name__)
        return None


engine = _make_engine(DATABASE_URL)
Session = async_sessionmaker(engine, expire_on_commit=False) if engine else None


def _setup_logging() -> None:
    """启动日志统一到 stdout（容器日志/平台日志流同源），级别可由 LOG_LEVEL 覆盖。"""
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        stream=sys.stdout,
        format="%(asctime)s %(levelname)-7s [test1] %(message)s",
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


app = FastAPI(title="陌如玉", lifespan=lifespan)

# 桌面配置的默认值：数据库不可用（如迁移尚未执行）时兜底，保证前端始终可渲染
DEFAULT_SETTINGS: dict[str, Any] = {
    "theme": "light",
    "accent": "#0078d4",
    "wallpaper": "bloom",
    "taskbarAlign": "center",
    "iconLayout": {},
    "showDesktopIcons": True,
    "brightness": 100,
    "nightLight": False,
}


async def db_ok() -> bool:
    if not Session:
        return False
    try:
        async with Session() as s:
            await s.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


@app.get("/api/health")
async def health():
    return {"status": "ok", "db": await db_ok()}


# --------------------------------------------------------------------------
# 桌面配置（主题 / 壁纸 / 强调色 / 图标位置 / 任务栏对齐）
# --------------------------------------------------------------------------
class SettingsIn(BaseModel):
    patch: dict[str, Any] = Field(default_factory=dict)


def _as_dict(raw: Any) -> dict[str, Any]:
    """asyncpg 下 JSONB 可能返回 dict 或 str，统一成 dict。"""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, (str, bytes, bytearray)):
        try:
            data = json.loads(raw)
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}
    return {}


async def _read_settings() -> dict[str, Any]:
    cfg = dict(DEFAULT_SETTINGS)
    if not Session:
        return cfg
    async with Session() as s:
        row = (await s.execute(text(
            "SELECT value FROM desktop_settings WHERE key = 'desktop'"))).mappings().first()
    cfg.update(_as_dict(row["value"]) if row else {})
    return cfg


@app.get("/api/settings")
async def get_settings():
    if not Session:
        return {"source": "default", "data": dict(DEFAULT_SETTINGS)}
    try:
        return {"source": "db", "data": await _read_settings()}
    except Exception as exc:  # 表尚未创建等异常不阻断前端
        return {"source": "default", "data": dict(DEFAULT_SETTINGS), "error": str(exc)}


@app.put("/api/settings")
async def put_settings(body: SettingsIn):
    if not Session:
        raise HTTPException(503, "DATABASE_URL 未配置，桌面设置无法持久化")
    async with Session() as s:
        row = (await s.execute(text(
            "SELECT value FROM desktop_settings WHERE key = 'desktop' FOR UPDATE"))).mappings().first()
        merged = _as_dict(row["value"]) if row else {}
        merged.update(body.patch)
        if row:
            await s.execute(text(
                "UPDATE desktop_settings SET value = CAST(:v AS JSONB), updated_at = now() WHERE key = 'desktop'"),
                {"v": _dumps(merged)})
        else:
            await s.execute(text(
                "INSERT INTO desktop_settings (key, value) VALUES ('desktop', CAST(:v AS JSONB))"),
                {"v": _dumps(merged)})
        await s.commit()
    return {"source": "db", "data": await _read_settings()}


def _dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)


# --------------------------------------------------------------------------
# 记事本（便签）
# --------------------------------------------------------------------------
class MemoIn(BaseModel):
    title: str = Field(default="未命名便签", max_length=200)
    content: str = ""
    pinned: bool = False


def _memo_dict(row) -> dict:
    d = dict(row)
    if d.get("created_at") is not None:
        d["created_at"] = d["created_at"].isoformat()
    if d.get("updated_at") is not None:
        d["updated_at"] = d["updated_at"].isoformat()
    return d


@app.get("/api/memos")
async def list_memos():
    if not Session:
        raise HTTPException(503, "DATABASE_URL 未配置")
    async with Session() as s:
        rows = (await s.execute(text(
            "SELECT id, title, content, pinned, created_at, updated_at FROM desktop_memos "
            "ORDER BY pinned DESC, updated_at DESC LIMIT 200"))).mappings().all()
    return [_memo_dict(r) for r in rows]


@app.get("/api/memos/{memo_id}")
async def get_memo(memo_id: int):
    if not Session:
        raise HTTPException(503, "DATABASE_URL 未配置")
    async with Session() as s:
        row = (await s.execute(text(
            "SELECT id, title, content, pinned, created_at, updated_at FROM desktop_memos WHERE id = :i"),
            {"i": memo_id})).mappings().first()
    if not row:
        raise HTTPException(404, "便签不存在")
    return _memo_dict(row)


@app.post("/api/memos")
async def create_memo(body: MemoIn):
    if not Session:
        raise HTTPException(503, "DATABASE_URL 未配置")
    async with Session() as s:
        row = (await s.execute(text(
            "INSERT INTO desktop_memos (title, content, pinned) VALUES (:t, :c, :p) "
            "RETURNING id, title, content, pinned, created_at, updated_at"),
            {"t": body.title.strip() or "未命名便签", "c": body.content, "p": body.pinned}
        )).mappings().first()
        await s.commit()
    return _memo_dict(row)


@app.put("/api/memos/{memo_id}")
async def update_memo(memo_id: int, body: MemoIn):
    if not Session:
        raise HTTPException(503, "DATABASE_URL 未配置")
    async with Session() as s:
        row = (await s.execute(text(
            "UPDATE desktop_memos SET title = :t, content = :c, pinned = :p, updated_at = now() "
            "WHERE id = :i RETURNING id, title, content, pinned, created_at, updated_at"),
            {"i": memo_id, "t": body.title.strip() or "未命名便签", "c": body.content, "p": body.pinned}
        )).mappings().first()
        if not row:
            await s.rollback()
            raise HTTPException(404, "便签不存在")
        await s.commit()
    return _memo_dict(row)


@app.delete("/api/memos/{memo_id}")
async def delete_memo(memo_id: int):
    if not Session:
        raise HTTPException(503, "DATABASE_URL 未配置")
    async with Session() as s:
        res = await s.execute(text("DELETE FROM desktop_memos WHERE id = :i"), {"i": memo_id})
        await s.commit()
        if res.rowcount == 0:
            raise HTTPException(404, "便签不存在")
    return {"ok": True}


# --------------------------------------------------------------------------
# 兼容旧骨架的示例接口（demo_notes）
# --------------------------------------------------------------------------
class NoteIn(BaseModel):
    title: str


@app.get("/api/notes")
async def list_notes():
    if not Session:
        raise HTTPException(503, "DATABASE_URL 未配置")
    async with Session() as s:
        rows = (await s.execute(text(
            "SELECT id, title, created_at FROM demo_notes ORDER BY id DESC LIMIT 100"))).mappings().all()
    return [dict(r) for r in rows]


@app.post("/api/notes")
async def add_note(body: NoteIn):
    if not Session:
        raise HTTPException(503, "DATABASE_URL 未配置")
    async with Session() as s:
        row = (await s.execute(text(
            "INSERT INTO demo_notes (title) VALUES (:t) RETURNING id, title, created_at"),
            {"t": body.title})).mappings().first()
        await s.commit()
    return dict(row)
