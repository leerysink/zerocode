from fastapi import APIRouter, Request
from app.services import config_service

router = APIRouter(prefix="/api/config")

@router.get("")
async def get_config(request: Request):
    async with request.app.state.session_factory() as s:
        return config_service.mask(await config_service.get_all(s))

@router.put("")
async def put_config(request: Request, body: dict):
    body = {k: v for k, v in body.items() if not (k == "llm.api_key" and v == "sk-***")}
    async with request.app.state.session_factory() as s:
        await config_service.set_many(s, body)
        await s.commit()
    return {"ok": True}

@router.get("/pg-status")
async def pg_status(request: Request):
    from sqlalchemy import text
    try:
        async with request.app.state.session_factory() as s:
            version = (await s.execute(text("SELECT version()"))).scalar()
        return {"ok": True, "version": version}
    except Exception as e:  # pragma: no cover
        return {"ok": False, "error": str(e)}
