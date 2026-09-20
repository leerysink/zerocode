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
ROOT2 = Path(f"D:/__zc_task5_b__/{SLUG}")  # 同 slug 的另一个空目录：用于绕过目录预检、精确命中 _insert_rows 分支

async def _wipe():
    sf = make_session_factory(make_engine())
    try:
        await provision.drop_env(sf, SLUG, "dev")
    except Exception:
        pass
    async with sf() as s:
        await s.execute(text("DELETE FROM app_project WHERE slug=:s"), {"s": SLUG})
        await s.execute(text("DELETE FROM workspace WHERE root_path=:a OR root_path=:b"),
                        {"a": str(ROOT), "b": str(ROOT2)})
        await s.commit()
    shutil.rmtree(ROOT.parent, ignore_errors=True)
    shutil.rmtree(ROOT2.parent, ignore_errors=True)

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

async def test_create_app_rejects_name_over_100():
    """M-2（终审 F-02）：150 字名此前穿透 VARCHAR(100) 抛 DBAPIError、被兜底包装成「创建应用失败: <SQLAlchemy 原文>」；
    入口必须按 strip 后 1..100 校验，中文 400 且不做任何渲染/落库动作（ROOT 不得出现半成品目录）。"""
    sf = make_session_factory(make_engine())
    with pytest.raises(DeployError) as ei:
        await app_service.create_app_project(
            sf, name="长" * 150, slug=SLUG, scale="small", stack="vue3-fastapi", root=str(ROOT))
    assert ei.value.status == 400
    msg = str(ei.value)
    assert "名称" in msg and "100" in msg and "VARCHAR" not in msg   # 中文引导，不外露驱动原文
    assert not ROOT.exists()
    # 空白名（strip 后为空）同样拒：长度判定基于 strip 后的值
    with pytest.raises(DeployError) as ei2:
        await app_service.create_app_project(
            sf, name="   ", slug=SLUG, scale="small", stack="vue3-fastapi", root=str(ROOT))
    assert ei2.value.status == 400


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

async def test_create_app_duplicate_slug_insert_row():
    """I-1：dup_slug 命中 _insert_rows 分支时（目录预检被不同空目录绕过），回滚绝不得销毁既有应用的库与凭据。"""
    sf = make_session_factory(make_engine())
    app = await app_service.create_app_project(
        sf, name="既有应用", slug=SLUG, scale="small", stack="vue3-fastapi", root=str(ROOT))
    cred = await provision.read_cred(sf, SLUG, "dev")
    assert cred and cred["db"] == f"{SLUG}_dev"            # 前置：A 的凭据键与 dev 库真实存在

    async def _select1():                                  # 用 A 自己的凭据连它的 dev 库
        from sqlalchemy.ext.asyncio import create_async_engine
        e = create_async_engine(
            f"postgresql+asyncpg://{cred['user']}:{cred['password']}@localhost:5433/{cred['db']}")
        try:
            async with e.connect() as c:
                return (await c.execute(text("SELECT 1"))).scalar()
        finally:
            await e.dispose()

    assert await _select1() == 1

    with pytest.raises(DeployError) as ei:                 # 同 slug、不同空目录 → 必进 _insert_rows
        await app_service.create_app_project(
            sf, name="撞车应用", slug=SLUG, scale="small", stack="vue3-fastapi", root=str(ROOT2))
    assert ei.value.status == 409 and "slug 已存在" in str(ei.value)
    # 关键断言：A 零损伤（回滚只允许删本流自己建的东西）
    assert (ROOT / "app.json").is_file()                   # A 的目录未被 rmtree
    assert await provision.read_cred(sf, SLUG, "dev") == cred   # dbcred 键未被 drop_env 抹掉
    assert await _select1() == 1                           # A 的 dev 库未被 DROP
    rows = await app_service.list_apps(sf)
    assert len(rows) == 1 and rows[0]["id"] == app["id"]   # 恰 1 行：既没误删 A，也没留下撞车行
    assert not ROOT2.exists()                              # 本次半成品目录仍应被清理
    async with sf() as s:                                  # 本次未提交任何 workspace 行
        assert (await s.execute(text(
            "SELECT count(*) FROM workspace WHERE root_path=:p"), {"p": str(ROOT2)})).scalar() == 0
