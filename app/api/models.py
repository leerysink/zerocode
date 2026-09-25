"""模型库 CRUD：多模型配置（名称/接入点/密钥/模型名/默认标记）。api_key 永远掩码回显；
PUT 传掩码哨兵 "sk-***" 表示保留原密钥；is_default=true 时清除其他默认。

隔离（Ticket 007）：全部查询按当前账号 owner 过滤，越权按 id 直取一律 404；
每用户各自唯一默认（DB 侧由部分唯一索引 ux_model_default_per_owner 兜底）。"""
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
    uid = request.state.actor["id"]
    async with request.app.state.session_factory() as s:
        rows = (await s.execute(text(_SELECT + " WHERE owner_user_id = :u "
                                    "ORDER BY is_default DESC, id"),
                                    {"u": uid})).mappings().all()
    return [_row_out(r) for r in rows]

@router.post("")
async def create_model(request: Request, body: ModelIn):
    # 扩展态（Ticket 003）：默认位清除按当前用户隔离，跨用户默认互不干扰
    # （DB 侧由部分唯一索引 ux_model_default_per_owner 兜底）
    owner_id = request.state.actor["id"]
    async with request.app.state.session_factory() as s:
        if body.is_default:
            await s.execute(text(
                "UPDATE model_config SET is_default=FALSE "
                "WHERE is_default AND owner_user_id IS NOT DISTINCT FROM :o"),
                {"o": owner_id})
        row = (await s.execute(text(
            "INSERT INTO model_config (name, base_url, api_key, model, is_default, owner_user_id) "
            "VALUES (:n, :b, :k, :m, :d, :o) RETURNING id, name, base_url, api_key, model, is_default, created_at"),
            {"n": body.name, "b": body.base_url, "k": body.api_key, "m": body.model,
             "d": body.is_default, "o": owner_id})).mappings().first()
        await s.commit()
    return _row_out(row)

@router.put("/{model_id}")
async def update_model(request: Request, model_id: int, body: ModelIn):
    uid = request.state.actor["id"]
    async with request.app.state.session_factory() as s:
        old = (await s.execute(text("SELECT api_key FROM model_config "
                                    "WHERE id=:i AND owner_user_id=:u"),
                                    {"i": model_id, "u": uid})).first()
        if not old:
            raise HTTPException(404, "模型不存在")
        new_key = old[0] if body.api_key == MASK else body.api_key
        if body.is_default:
            # 默认位竞争只发生在同一归属用户内：按目标行的 owner 清位（扩展态，Ticket 003）
            await s.execute(text(
                "UPDATE model_config SET is_default=FALSE "
                "WHERE is_default AND id <> :i "
                "AND owner_user_id IS NOT DISTINCT FROM (SELECT owner_user_id FROM model_config WHERE id = :i)"),
                {"i": model_id})
        await s.execute(text(
            "UPDATE model_config SET name=:n, base_url=:b, api_key=:k, model=:m, is_default=:d "
            "WHERE id=:i AND owner_user_id=:u"),
            {"n": body.name, "b": body.base_url, "k": new_key, "m": body.model,
             "d": body.is_default, "i": model_id, "u": uid})
        await s.commit()
    return {"ok": True}

@router.delete("/{model_id}")
async def delete_model(request: Request, model_id: int):
    uid = request.state.actor["id"]
    async with request.app.state.session_factory() as s:
        hit = (await s.execute(text(
            "DELETE FROM model_config WHERE id=:i AND owner_user_id=:u RETURNING id"),
            {"i": model_id, "u": uid})).first()
        await s.commit()
    if not hit:
        raise HTTPException(404, "模型不存在")
    return {"ok": True}
