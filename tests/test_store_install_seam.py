# tests/test_store_install_seam.py
"""materialize 接缝：商店安装用自己的铺源逻辑，但仍走 create_app_project 的建表/端口/回滚。"""
import pytest
from sqlalchemy import text
from app.deploy.errors import DeployError
from app.services import app_service


async def test_materialize_replaces_template_render(test_engine):
    sf = app_service.make_session_factory(test_engine) if hasattr(app_service, "make_session_factory") \
        else (lambda: test_engine)
    seen = {}

    async def _noop_sf():
        pass

    from app.db import make_session_factory
    sf = make_session_factory(test_engine)
    marker = "由商店铺出来的"

    def materialize(root, mapping):
        root.mkdir(parents=True, exist_ok=True)
        (root / "backend").mkdir()
        (root / "backend" / "main.py").write_text(marker, encoding="utf-8")
        (root / "sqlchange").mkdir()
        (root / "sqlchange" / "V20260101_01_init.sql").write_text("SELECT 1;", encoding="utf-8")
        seen["mapping"] = dict(mapping)

    name = f"seam{_u()}"
    app = await app_service.create_app_project(
        sf, name="接缝应用", slug=name, scale="small", stack="vue3-fastapi",
        origin="store:7", materialize=materialize)
    try:
        # 1) 没跑模板：目录里只有 materialize 写进去的东西
        root = await _root_of(sf, app)
        assert (root / "backend" / "main.py").read_text(encoding="utf-8") == marker
        assert not (root / "frontend").exists(), "模板被渲染了，说明 materialize 没生效"
        # 2) 占位符 mapping 仍然完整下发（商店也要用同一对端口/库名）
        assert seen["mapping"]["SLUG"] == name
        assert seen["mapping"]["DEV_DB"] == f"{name}_dev"
        # 3) app.json 由平台照常生成（slug/端口/库名对齐）
        meta = app_service.load_app_json(await _root_of(sf, app))
        assert meta["slug"] == name
        # 4) origin 落库
        async with sf() as s:
            got = (await s.execute(text("SELECT origin FROM app_project WHERE id=:i"),
                                   {"i": app["id"]})).scalar()
        assert got == "store:7"
    finally:
        await _cleanup(sf, app)


def _u():
    import secrets
    return secrets.token_hex(3)


async def _root_of(sf, app):
    from pathlib import Path
    async with sf() as s:
        p = (await s.execute(text("SELECT root_path FROM workspace WHERE id=:i"),
                             {"i": app["workspace_id"]})).scalar()
    return Path(p)


async def _cleanup(sf, app):
    from app.deploy import provision
    root = await _root_of(sf, app)
    slug = app["slug"]
    for env in ("dev", "prod"):
        try:
            await provision.drop_env(sf, slug, env)
        except Exception:
            pass
    await app_service.delete_app(sf, app["id"])
    import shutil
    shutil.rmtree(root, ignore_errors=True)
