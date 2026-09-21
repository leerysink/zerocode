# app/api/system.py
"""系统级只读信息（规格 05 §5）：网卡候选。"""
from fastapi import APIRouter, Request
from app.deploy import nics

router = APIRouter(prefix="/api/system")


@router.get("/nics")
async def get_nics(request: Request):
    return {"candidates": nics.lan_candidates(),
            "defaultNic": await nics.default_nic(request.app.state.session_factory)}
