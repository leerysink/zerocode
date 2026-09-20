# tests/test_app_service.py
import shutil
import pytest
from pathlib import Path
from sqlalchemy import text
from app.db import make_engine, make_session_factory
from app.deploy import provision
from app.deploy.errors import DeployError
from app.services import app_service

pytestmark = pytest.mark.asyncio

SLUG = "zctask5"
ROOT = Path(f"D:/__zc_task5__/{SLUG}")   # 隔离目录：不落仓库 apps/（裁定 R-B：create 调用一律显式传 root）

async def _wipe():
    sf = make_session_factory(make_engine())
    try:
        await provision.drop_env(sf, SLUG, "dev")
    except Exception:
        pass
    async with sf() as s:
        await s.execute(text("DELETE FROM app_project WHERE slug=:s"), {"s": SLUG})
        await s.execute(text("DELETE FROM workspace WHERE root_path=:p"), {"p": str(ROOT)})
        await s.commit()
    shutil.rmtree(ROOT.parent, ignore_errors=True)

@pytest.fixture(autouse=True)
async def _clean():
    await _wipe()
    yield
    await _wipe()

async def test_create_app_happy_path():
    sf = make_session_factory(make_engine())
    app = await app_service.create_app_project(
        sf, name="测试应用", slug=SLUG, scale="small", stack="vue3-fastapi", root=str(ROOT))
    assert app["slug"] == SLUG and app["scale"] == "small"
    assert app["prod_port"] % 2 == 0 and app["dev_port"] == app["prod_port"] + 1
    # 目录产物
    assert (ROOT / "app.json").is_file()
    assert (ROOT / "backend" / "app" / "main.py").is_file()
    assert list((ROOT / "sqlchange").glob("V*.sql"))
    assert (ROOT / "deploy" / "dev-compose.yml").is_file()      # 由 ensure_deploy_files 生成（Task 10）
    assert (ROOT / "deploy" / ".env").is_file()
    # 裁定 R-A：端口分配前置于 render —— README 必须渲染出真实端口，不得是空端口
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert f"http://localhost:{app['dev_port']}" in readme
    # 数据库供给
    cred = await provision.read_cred(sf, SLUG, "dev")
    assert cred and cred["db"] == f"{SLUG}_dev"
    # 平台记录
    rows = await app_service.list_apps(sf)
    assert any(a["slug"] == SLUG for a in rows)
    detail = await app_service.get_app(sf, app["id"])
    assert detail["workspace_id"] > 0
    import json
    meta = json.loads((ROOT / "app.json").read_text(encoding="utf-8"))
    assert meta["schema"] == "zc-app/1" and meta["database"]["dev"]["name"] == f"{SLUG}_dev"
    assert "password" not in meta

async def test_create_app_rejects_bad_scale_stack():
    sf = make_session_factory(make_engine())
    with pytest.raises(DeployError):
        await app_service.create_app_project(
            sf, name="x", slug=SLUG, scale="middle", stack="vue3-fastapi", root=str(ROOT))
    with pytest.raises(DeployError):
        await app_service.create_app_project(
            sf, name="x", slug=SLUG, scale="large", stack="vue3-fastapi", root=str(ROOT))  # large 锁定 springboot
    # 裁定 R-C：模板尚未交付的栈以 400 友好拒绝（M1 仅 vue3-fastapi），且不得留下半成品目录
    with pytest.raises(DeployError) as ei:
        await app_service.create_app_project(
            sf, name="x", slug=SLUG, scale="small", stack="vue3-node", root=str(ROOT))
    assert ei.value.status == 400 and "尚未开放" in str(ei.value)
    assert not ROOT.exists()

async def test_create_app_duplicate_slug():
    sf = make_session_factory(make_engine())
    await app_service.create_app_project(
        sf, name="一", slug=SLUG, scale="small", stack="vue3-fastapi", root=str(ROOT))
    with pytest.raises(DeployError) as ei:
        await app_service.create_app_project(
            sf, name="二", slug=SLUG, scale="small", stack="vue3-fastapi", root=str(ROOT))
    assert ei.value.status == 409

async def test_create_app_rollback_on_provision_failure(monkeypatch):
    sf = make_session_factory(make_engine())
    async def boom(*a, **k):
        raise RuntimeError("pg down")
    monkeypatch.setattr(app_service.provision, "provision_env", boom)
    with pytest.raises(DeployError):
        await app_service.create_app_project(
            sf, name="x", slug=SLUG, scale="small", stack="vue3-fastapi", root=str(ROOT))
    assert not ROOT.exists()                                   # 目录已回滚
    async with sf() as s:
        assert (await s.execute(text(
            "SELECT count(*) FROM app_project WHERE slug=:s"), {"s": SLUG})).scalar() == 0
        assert (await s.execute(text(
            "SELECT count(*) FROM workspace WHERE root_path=:p"), {"p": str(ROOT)})).scalar() == 0

async def test_create_app_port_race_falls_back_to_unique(monkeypatch):
    """裁定 R-A 兜底：只读探测与行插入之间的端口竞态，由 app_project.prod_port UNIQUE 拦下 → 409 且全回滚。"""
    sf = make_session_factory(make_engine())
    seed_root = "D:/__zc_task5__seed"
    async with sf() as s:                                      # 占位应用先占走 8210/8211
        wid = (await s.execute(text(
            "INSERT INTO workspace (name, root_path) VALUES ('占位', :p) RETURNING id"),
            {"p": seed_root})).scalar()
        await s.execute(text(
            "INSERT INTO app_project (workspace_id, name, slug, scale, stack, prod_port, dev_port) "
            "VALUES (:w, '占位', 'zctask5seed', 'small', 'static', 8210, 8211)"), {"w": wid})
        await s.commit()
    try:
        async def fake_pair(session, probe=None):              # 探测被绕过 → 模拟并发抢走同端口
            return 8210, 8211
        monkeypatch.setattr(app_service, "allocate_pair", fake_pair)
        with pytest.raises(DeployError) as ei:
            await app_service.create_app_project(
                sf, name="竞态", slug=SLUG, scale="small", stack="vue3-fastapi", root=str(ROOT))
        assert ei.value.status == 409
        assert not ROOT.exists()                               # 已渲染的半成品目录一并回滚
        async with sf() as s:
            assert (await s.execute(text(
                "SELECT count(*) FROM app_project WHERE slug=:s"), {"s": SLUG})).scalar() == 0
            assert (await s.execute(text(
                "SELECT count(*) FROM workspace WHERE root_path=:p"), {"p": str(ROOT)})).scalar() == 0
    finally:
        async with sf() as s:
            await s.execute(text("DELETE FROM app_project WHERE slug='zctask5seed'"))
            await s.execute(text("DELETE FROM workspace WHERE root_path=:p"), {"p": seed_root})
            await s.commit()
