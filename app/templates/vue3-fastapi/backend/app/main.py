"""{{APP_NAME}} 后端骨架：/api 前缀 + health + demo_notes 示例；数据库连接从环境变量读取。"""
import os
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

DATABASE_URL = os.environ.get("DATABASE_URL", "")
engine = create_async_engine(DATABASE_URL) if DATABASE_URL else None
Session = async_sessionmaker(engine, expire_on_commit=False) if engine else None

app = FastAPI(title="{{APP_NAME}}")

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
