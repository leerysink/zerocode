"""模型库 CRUD：多模型配置（名称/接入点/密钥/模型名/默认标记/类型）。api_key 永远掩码回显；
PUT 传掩码哨兵 "sk-***" 表示保留原密钥；is_default=true 时清除同 owner 同 kind 的其他默认。

隔离（Ticket 007）：全部查询按当前账号 owner 过滤，越权按 id 直取一律 404；
每用户每种类型各自唯一默认（DB 侧由部分唯一索引 ux_model_default_per_owner_kind 兜底）。

kind（Ticket 005 / D-2）：'chat' 对话档案 | 'embedding' 向量档案。刻意不新增一张表：
两种档案的字段与鉴权形态完全一致，多一个列就能复用 owner 过滤、掩码与默认位这一整套纪律。
"""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import text

router = APIRouter(prefix="/api/models")
MASK = "sk-***"
# 取值与 V20261008_02 的 kind 列注释一致。CHECK 没进迁移（ADD CONSTRAINT 不幂等），
# 就必须在这一层和 model_resolver 的查询条件两头把关。
MODEL_KINDS = ("chat", "embedding")
DEFAULT_KIND = "chat"

class ModelIn(BaseModel):
    name: str
    base_url: str
    api_key: str = ""
    model: str
    is_default: bool = False
    # 缺省不进字段默认值而留 None：PUT 不带 kind 必须是「不改类型」，
    # 默认 'chat' 会把一条 embedding 档案悄悄刷回 chat，向量召回当场失效。
    kind: str | None = None


def _check_kind(kind) -> str:
    """非法类型当场 400，不能让 'vl' 这种脏值落库后变成「谁都查不到的档案」。"""
    if kind not in MODEL_KINDS:
        raise HTTPException(400, f"未知的模型类型 {kind!r}，可选：{' / '.join(MODEL_KINDS)}")
    return kind

def _row_out(r) -> dict:
    d = dict(r)
    d["api_key"] = MASK if d["api_key"] else ""
    return d

_SELECT = ("SELECT id, name, base_url, api_key, model, kind, is_default, created_at "
           "FROM model_config")

@router.get("")
async def list_models(request: Request, kind: str | None = None):
    """kind 为空返回全部（设置页自己分列展示）；给了就只取那一类（对话模型下拉只认 chat）。"""
    uid = request.state.actor["id"]
    where = ["owner_user_id = :u"]
    params: dict = {"u": uid}
    if kind is not None:
        where.append("kind = :k")
        params["k"] = _check_kind(kind)
    async with request.app.state.session_factory() as s:
        rows = (await s.execute(text(_SELECT + " WHERE " + " AND ".join(where)
                                        + " ORDER BY is_default DESC, id"),
                                    params)).mappings().all()
    return [_row_out(r) for r in rows]

@router.post("")
async def create_model(request: Request, body: ModelIn):
    # 扩展态（Ticket 003）：默认位清除按当前用户隔离，跨用户默认互不干扰
    # （DB 侧由部分唯一索引 ux_model_default_per_owner_kind 兜底）
    owner_id = request.state.actor["id"]
    kind = _check_kind(body.kind) if body.kind is not None else DEFAULT_KIND
    async with request.app.state.session_factory() as s:
        if body.is_default:
            # 清默认位必须带上 kind：少了它，设默认 embedding 会把同一用户的默认 chat 一并清掉
            await s.execute(text(
                "UPDATE model_config SET is_default=FALSE "
                "WHERE is_default AND kind=:k AND owner_user_id IS NOT DISTINCT FROM :o"),
                {"o": owner_id, "k": kind})
        row = (await s.execute(text(
            "INSERT INTO model_config (name, base_url, api_key, model, is_default, owner_user_id, kind) "
            "VALUES (:n, :b, :k, :m, :d, :o, :kind) RETURNING "
            "id, name, base_url, api_key, model, kind, is_default, created_at"),
            {"n": body.name, "b": body.base_url, "k": body.api_key, "m": body.model,
             "d": body.is_default, "o": owner_id, "kind": kind})).mappings().first()
        await s.commit()
    return _row_out(row)

@router.put("/{model_id}")
async def update_model(request: Request, model_id: int, body: ModelIn):
    uid = request.state.actor["id"]
    async with request.app.state.session_factory() as s:
        old = (await s.execute(text("SELECT api_key, kind FROM model_config "
                                    "WHERE id=:i AND owner_user_id=:u"),
                                    {"i": model_id, "u": uid})).first()
        if not old:
            raise HTTPException(404, "模型不存在")
        new_key = old[0] if body.api_key == MASK else body.api_key
        kind = _check_kind(body.kind) if body.kind is not None else old[1]
        if body.is_default:
            # 默认位竞争只发生在「同一归属用户 + 同一类型」内：按目标行的 owner 清位（扩展态，Ticket 003），
            # kind 用改后的新类型（chat→embedding 的改名要把 embedding 那一类的默认位抢过来）
            await s.execute(text(
                "UPDATE model_config SET is_default=FALSE "
                "WHERE is_default AND id <> :i AND kind=:k "
                "AND owner_user_id IS NOT DISTINCT FROM (SELECT owner_user_id FROM model_config WHERE id = :i)"),
                {"i": model_id, "k": kind})
        await s.execute(text(
            "UPDATE model_config SET name=:n, base_url=:b, api_key=:k, model=:m, "
            "is_default=:d, kind=:kind "
            "WHERE id=:i AND owner_user_id=:u"),
            {"n": body.name, "b": body.base_url, "k": new_key, "m": body.model,
             "d": body.is_default, "kind": kind, "i": model_id, "u": uid})
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
