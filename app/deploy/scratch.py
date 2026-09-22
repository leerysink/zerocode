# app/deploy/scratch.py
"""发布校验专用临时库：名字固定 <slug>_verify，建/删走 provision 原语。

先删后建，保证每次都从零 fold（避免上一次校验残留对象把差异吃进 SKIP）。
"""
from app.deploy import provision


def verify_db_name(slug: str) -> str:
    return f"{slug}_verify"


async def ensure_scratch(sf, slug: str) -> dict:
    await provision.drop_db(sf, verify_db_name(slug))          # 先清残留，保证从零 fold
    return await provision.create_db_with_role(sf, verify_db_name(slug))


async def cleanup_scratch(sf, slug: str) -> None:
    await provision.drop_db(sf, verify_db_name(slug))
