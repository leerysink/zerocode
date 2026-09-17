"""模型库 CRUD：多模型配置（名称/接入点/密钥/模型名/默认标记）。api_key 永远掩码回显；
PUT 传掩码哨兵 "sk-***" 表示保留原密钥；is_default=true 时清除其他默认（部分唯一索引兜底）。"""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import text

router = APIRouter(prefix="/api/models")
MASK = "sk-***"

class ModelIn(BaseModel):
    name: str
    base_url: str
    api_key: str = ""
    model: str
    is_default: bool = False

def _row_out(r) -> dict:
    d = dict(r)
    d["api_key"] = MASK if d["api_key"] else ""
    return d

_SELECT = ("SELECT id, name, base_url, api_key, model, is_default, created_at "
           "FROM model_config")

@router.get("")
async def list_models(request: Request):
    async with request.app.state.session_factory() as s:
        rows = (await s.execute(text(_SELECT + " ORDER BY is_default DESC, id"))).mappings().all()
    return [_row_out(r) for r in rows]

@router.post("")
async def create_model(request: Request, body: ModelIn):
    async with request.app.state.session_factory() as s:
        if body.is_default:
            await s.execute(text("UPDATE model_config SET is_default=FALSE WHERE is_default"))
        row = (await s.execute(text(
            "INSERT INTO model_config (name, base_url, api_key, model, is_default) "
            "VALUES (:n, :b, :k, :m, :d) RETURNING id, name, base_url, api_key, model, is_default, created_at"),
            {"n": body.name, "b": body.base_url, "k": body.api_key, "m": body.model,
             "d": body.is_default})).mappings().first()
        await s.commit()
    return _row_out(row)

@router.put("/{model_id}")
async def update_model(request: Request, model_id: int, body: ModelIn):
    async with request.app.state.session_factory() as s:
        old = (await s.execute(text("SELECT api_key FROM model_config WHERE id=:i"), {"i": model_id})).first()
        if not old:
            raise HTTPException(404, "模型不存在")
        new_key = old[0] if body.api_key == MASK else body.api_key
        if body.is_default:
            await s.execute(text("UPDATE model_config SET is_default=FALSE WHERE is_default AND id <> :i"),
                            {"i": model_id})
        await s.execute(text(
            "UPDATE model_config SET name=:n, base_url=:b, api_key=:k, model=:m, is_default=:d WHERE id=:i"),
            {"n": body.name, "b": body.base_url, "k": new_key, "m": body.model,
             "d": body.is_default, "i": model_id})
        await s.commit()
    return {"ok": True}

@router.delete("/{model_id}")
async def delete_model(request: Request, model_id: int):
    async with request.app.state.session_factory() as s:
        await s.execute(text("DELETE FROM model_config WHERE id=:i"), {"i": model_id})
        await s.commit()
    return {"ok": True}
