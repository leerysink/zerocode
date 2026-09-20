# tests/test_apps_rename_delete.py
"""侧栏应用治理：应用改名（PATCH /api/apps/{id}）与删除应用（DELETE /api/apps/{id}）。

契约（2026-09-20 用户需求 + 规格 01 FR1-07 / 08 FR8-08 的 M1 子集，逐条裁定见设计对话）：
  - 改名同步写 app_project.name 与 workspace.name：侧栏分组名与文件面板面包屑读的是 workspace，
    只改一边会出现「应用页新名、侧栏旧名」的裂相。slug/端口/栈一律不动（目录名、库名、镜像名都挂在 slug）。
  - 名称 strip 后 1..100 字，否则中文 400（不穿透 asyncpg 的 StringDataRightTruncation 冒 500）。
  - 删除 = 删平台记录 + 尽力停 dev 栈：删 workspace 行，靠既有 ON DELETE CASCADE 带走
    app_project / chat_session / message / file_change；**磁盘工程目录与应用数据库保留**（M1 裁定）。
  - 端口随记录消失即释放（规格 05：删除应用后端口对可被新应用复用）。
  - 发布中（status_prod=publishing）拒绝删除 → 409（规格 08 §5.4 并发防护）。
  - 不存在的 id → 404。
"""
import shutil

import pytest
from sqlalchemy import text

from app import config as app_config
from app.db import make_engine, make_session_factory
from app.deploy import engine, provision
from app.deploy.settings import REPO_ROOT

pytestmark = pytest.mark.asyncio

SLUG = "zcrename"
ALT_SLUG = "zcaltdel"
APP_DIRS = [REPO_ROOT / "apps" / s for s in (SLUG, ALT_SLUG)]
BODY = {"name": "改名前", "slug": SLUG, "scale": "small", "stack": "vue3-fastapi"}


async def _wipe():
    """清掉本文件全部产物：dev 库/角色/凭据 + 表行 + 工程目录（仿 test_apps_api）。"""
    eng = make_engine(app_config.test_database_url())
    sf = make_session_factory(eng)
    try:
        for slug in (SLUG, ALT_SLUG):
            try:
                await provision.drop_env(sf, slug, "dev")
            except Exception:
                pass
        async with sf() as s:
            await s.execute(text("DELETE FROM app_project WHERE slug = ANY(:s)"), {"s": [SLUG, ALT_SLUG]})
            for slug in (SLUG, ALT_SLUG):
                rows = (await s.execute(text("SELECT id FROM workspace WHERE root_path LIKE :p"),
                                        {"p": f"%\\{slug}"})).scalars().all()
                for wid in rows:
                    await s.execute(text("DELETE FROM workspace WHERE id = :i"), {"i": wid})
            await s.commit()
        for d in APP_DIRS:
            shutil.rmtree(d, ignore_errors=True)
    finally:
        await eng.dispose()


@pytest.fixture(autouse=True)
async def _clean(test_engine):
    await _wipe()
    yield
    await _wipe()


async def _create(client, **over):
    r = await client.post("/api/apps", json={**BODY, **over})
    assert r.status_code == 201, r.text
    return r.json()


async def _scalar(client, sql, **params):
    async with client.app.state.session_factory() as s:
        return (await s.execute(text(sql), params)).first()


async def test_rename_syncs_app_and_workspace_name(client):
    """改名两张表同时生效，且 GET 详情回显新名。"""
    app = await _create(client)
    r = await client.patch(f"/api/apps/{app['id']}", json={"name": "  改名后  "})
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "改名后"                       # 入口 strip 后再落库
    assert (await _scalar(client, "SELECT name FROM app_project WHERE id=:i",
                          i=app["id"]))[0] == "改名后"
    assert (await _scalar(client, "SELECT name FROM workspace WHERE id=:i",
                          i=app["workspace_id"]))[0] == "改名后"
    assert (await client.get(f"/api/apps/{app['id']}")).json()["name"] == "改名后"


async def test_rename_keeps_slug_ports_and_directory(client):
    """slug/端口是目录名、库名、镜像名的锚点，改名绝不能碰。"""
    app = await _create(client)
    r = await client.patch(f"/api/apps/{app['id']}", json={"name": "换个名"})
    body = r.json()
    for k in ("slug", "dev_port", "prod_port", "stack", "scale", "workspace_id"):
        assert body[k] == app[k], k
    assert (REPO_ROOT / "apps" / SLUG / "app.json").is_file()   # 工程目录仍在原位


async def test_rename_rejects_blank_and_overlong(client):
    app = await _create(client)
    for bad in ("", "   ", "a" * 101):
        r = await client.patch(f"/api/apps/{app['id']}", json={"name": bad})
        assert r.status_code == 400, f"{bad[:8]!r} → {r.status_code}"
        assert "应用名称" in r.json()["detail"]                  # 中文可执行提示，不是驱动原文
    assert (await _scalar(client, "SELECT name FROM app_project WHERE id=:i",
                          i=app["id"]))[0] == "改名前"           # 失败不得留下半改状态


async def test_rename_missing_app_is_404(client):
    r = await client.patch("/api/apps/999999", json={"name": "不存在"})
    assert r.status_code == 404, r.text


async def test_delete_removes_platform_rows_but_keeps_files(client):
    """删除只清平台记录：工程目录留在磁盘（M1 裁定），删的是 workspace → 应用行 CASCADE 跟走。"""
    app = await _create(client)
    root = REPO_ROOT / "apps" / SLUG
    assert root.is_dir()
    r = await client.delete(f"/api/apps/{app['id']}")
    assert r.status_code == 200, r.text
    assert (await client.get("/api/apps")).json() == []
    assert await _scalar(client, "SELECT id FROM workspace WHERE id=:i", i=app["workspace_id"]) is None
    assert root.is_dir(), "M1 删除不得动磁盘工程目录"
    assert (await _scalar(client, "SELECT 1 FROM pg_database WHERE datname=:d", d=f"{SLUG}_dev"))


async def test_delete_cascades_sessions_and_messages(client):
    """应用下的会话随 workspace 行一起消失（CASCADE），不留孤儿会话。"""
    app = await _create(client)
    sid = (await client.post("/api/sessions", json={"workspace_id": app["workspace_id"]})).json()["id"]
    await client.delete(f"/api/apps/{app['id']}")
    assert await _scalar(client, "SELECT id FROM chat_session WHERE id=:i", i=sid) is None
    assert await _scalar(client, "SELECT id FROM message WHERE session_id=:i", i=sid) is None


async def test_delete_frees_the_port_pair_for_reuse(client):
    """规格 05：删除应用即释放端口对，新应用可复用同一对。"""
    app = await _create(client)
    await client.delete(f"/api/apps/{app['id']}")
    fresh = await _create(client, name="复用端口", slug=ALT_SLUG)
    assert fresh["dev_port"] == app["dev_port"] and fresh["prod_port"] == app["prod_port"]


async def test_delete_stops_running_dev_stack_before_removing_rows(client, monkeypatch):
    """停栈前置：running 的应用要先走 stop_dev，否则 Docker 里留一只没人认领的容器。"""
    app = await _create(client)
    async with client.app.state.session_factory() as s:
        await s.execute(text("UPDATE app_project SET status_dev='running' WHERE id=:i"), {"i": app["id"]})
        await s.commit()
    calls = []

    async def fake_stop(sf, target, notify=None):
        calls.append(target["id"])
        return {"status": "stopped"}

    monkeypatch.setattr(engine, "stop_dev", fake_stop)
    assert (await client.delete(f"/api/apps/{app['id']}")).status_code == 200
    assert calls == [app["id"]]


async def test_delete_survives_stop_failure(client, monkeypatch):
    """尽力而为：Docker 没开 / 停栈失败不能把应用永久钉在列表里（记录必须照样删掉）。"""
    app = await _create(client)
    async with client.app.state.session_factory() as s:
        await s.execute(text("UPDATE app_project SET status_dev='running' WHERE id=:i"), {"i": app["id"]})
        await s.commit()

    async def boom(sf, target, notify=None):
        raise RuntimeError("docker 不可用")

    monkeypatch.setattr(engine, "stop_dev", boom)
    r = await client.delete(f"/api/apps/{app['id']}")
    assert r.status_code == 200, r.text
    assert (await client.get("/api/apps")).json() == []


async def test_delete_skips_stop_when_already_stopped(client, monkeypatch):
    """stopped 应用不再敲 docker：删除保持快路径。"""
    app = await _create(client)
    called = []

    async def spy_stop(sf, target, notify=None):
        called.append(target["id"])
        return {"status": "stopped"}

    monkeypatch.setattr(engine, "stop_dev", spy_stop)
    assert (await client.delete(f"/api/apps/{app['id']}")).status_code == 200
    assert called == []


async def test_delete_refuses_while_publishing(client):
    """发布进行中删记录会留下半成品版本目录与在跑容器 → 409 让用户等一等。"""
    app = await _create(client)
    async with client.app.state.session_factory() as s:
        await s.execute(text("UPDATE app_project SET status_prod='publishing' WHERE id=:i"), {"i": app["id"]})
        await s.commit()
    r = await client.delete(f"/api/apps/{app['id']}")
    assert r.status_code == 409, r.text
    assert "发布" in r.json()["detail"]
    assert len((await client.get("/api/apps")).json()) == 1      # 记录完好


async def test_delete_refuses_while_dev_task_in_flight(client):
    """同应用有后台部署任务在跑 → 409：沿用 _spawn「一次一个任务」的既有契约，不新增竞态面。"""
    import asyncio
    app = await _create(client)
    task = asyncio.create_task(asyncio.sleep(30))
    client.app.state.dev_tasks[app["id"]] = task
    try:
        r = await client.delete(f"/api/apps/{app['id']}")
        assert r.status_code == 409, r.text
        assert len((await client.get("/api/apps")).json()) == 1
    finally:
        task.cancel()


async def test_delete_reports_what_is_kept(client):
    """确认框要能讲清「什么还在」：返回值带上工程目录与保留的库名。"""
    app = await _create(client)
    r = await client.delete(f"/api/apps/{app['id']}")
    kept = r.json()["kept"]
    assert kept["root_path"].endswith(SLUG)
    assert kept["databases"] == [f"{SLUG}_dev", f"{SLUG}_prod"]


async def test_list_apps_carries_root_path_for_the_sidebar(client):
    """侧栏分组行改由 /api/apps 驱动，需要带出 workspace.root_path：
    确认文案要把「工程目录还在」的具体路径说清楚，前端不该为此再查一次工作区。"""
    app = await _create(client)
    row = next(a for a in (await client.get("/api/apps")).json() if a["id"] == app["id"])
    assert row["root_path"].replace("\\", "/").endswith(SLUG)
    assert row["workspace_id"] == app["workspace_id"]
