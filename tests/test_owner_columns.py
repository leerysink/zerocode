# tests/test_owner_columns.py
"""Ticket 003 宽重构·扩展：资源表加 owner_user_id 归属维度（列只写不读）。

验收靶心（spec §5 / ADR-0003 扩展态）：
- 同一 root_path 允许两个用户各注册一条 workspace（双 cookie 验证）；
- 新建 workspace / app_project / model_config 落当前登录用户的 owner_user_id；
- 每用户一份默认模型互不冲突（默认唯一性从全局收敛到按用户）。
读侧不过滤是扩展态定义：列表接口此处仍返回全量，隔离在 005-007 收缩。
"""
import shutil

import pytest
from sqlalchemy import text

from app import config as app_config     # 不可 from app.config import test_database_url（会被收集为用例）
from app.db import make_engine, make_session_factory
from app.deploy import provision
from app.deploy.settings import REPO_ROOT

pytestmark = pytest.mark.asyncio

OWNER_SLUG = "zcown03"
OWNER_APP_DIR = REPO_ROOT / "apps" / OWNER_SLUG


async def _me(client) -> int:
    """当前会话的 actor id（owner_user_id 断言的独立真相源）。"""
    r = await client.get("/api/auth/me")
    assert r.status_code == 200
    return r.json()["id"]


async def _scalar(client, sql: str, **params):
    async with client.app.state.session_factory() as s:
        return (await s.execute(text(sql), params)).first()


async def test_两个用户可各自把同一根目录注册为工作区(two_auth_clients, tmp_path):
    """扩展态核心：root_path 唯一性从全局收敛到 (owner, root_path)。"""
    alice, bob = two_auth_clients
    root = str(tmp_path)
    r1 = await alice.post("/api/workspaces", json={"root_path": root})
    assert r1.status_code == 200, r1.text
    r2 = await bob.post("/api/workspaces", json={"root_path": root})
    assert r2.status_code == 200, r2.text
    n = await _scalar(alice, "SELECT count(*) FROM workspace WHERE root_path = :p", p=root)
    assert n[0] == 2


async def test_新建workspace落入当前用户id(client, tmp_path):
    r = await client.post("/api/workspaces", json={"root_path": str(tmp_path)})
    assert r.status_code == 200, r.text
    row = await _scalar(client,
                        "SELECT owner_user_id FROM workspace WHERE root_path = :p",
                        p=str(tmp_path))
    assert row[0] == await _me(client)


async def test_新建模型档案落入当前用户id(client):
    r = await client.post("/api/models", json={
        "name": "归属探针", "base_url": "https://example.invalid/v1",
        "api_key": "sk-x", "model": "m1", "is_default": False})
    assert r.status_code == 200, r.text
    row = await _scalar(client, "SELECT owner_user_id FROM model_config WHERE id = :i",
                        i=r.json()["id"])
    assert row[0] == await _me(client)


async def test_两个用户的默认模型互不清除(two_auth_clients):
    """默认唯一性按用户部分唯一：bob 设默认不得动 alice 的默认位。"""
    alice, bob = two_auth_clients
    body = {"base_url": "https://example.invalid/v1", "api_key": "sk-x", "model": "m1"}
    ra = await alice.post("/api/models", json={**body, "name": "a-def", "is_default": True})
    rb = await bob.post("/api/models", json={**body, "name": "b-def", "is_default": True})
    assert ra.status_code == 200 and rb.status_code == 200
    async with alice.app.state.session_factory() as s:
        defaults = (await s.execute(text(
            "SELECT is_default FROM model_config WHERE name IN ('a-def','b-def') "
            "ORDER BY name"))).scalars().all()
    assert defaults == [True, True]


async def _wipe_owner_app():
    """复刻 test_apps_api 的清场：dev 库/角色/dbcred + 行 + 目录，异常残骸不串场。"""
    eng = make_engine(app_config.test_database_url())
    sf = make_session_factory(eng)
    try:
        try:
            await provision.drop_env(sf, OWNER_SLUG, "dev")
        except Exception:
            pass
        async with sf() as s:
            await s.execute(text("DELETE FROM app_project WHERE slug = :s"), {"s": OWNER_SLUG})
            w = (await s.execute(text("SELECT id FROM workspace WHERE root_path LIKE :p"),
                                 {"p": f"%\\{OWNER_SLUG}"})).scalars().all()
            for wid in w:
                await s.execute(text("DELETE FROM workspace WHERE id = :i"), {"i": wid})
            await s.commit()
        shutil.rmtree(OWNER_APP_DIR, ignore_errors=True)
    finally:
        await eng.dispose()


@pytest.fixture(autouse=True)
async def _clean_owner_app(test_engine):
    await _wipe_owner_app()
    yield
    await _wipe_owner_app()


async def test_新建应用工程落入当前用户id(client):
    """创建链盖 workspace+app_project 两行，且 provision_env 的 dbcred 写入
    须在新唯一索引下仍可冲突更新（ON CONFLICT 适配的活体证明）。"""
    r = await client.post("/api/apps", json={
        "name": "归属探针应用", "slug": OWNER_SLUG, "scale": "small", "stack": "vue3-fastapi"})
    assert r.status_code == 201, r.text
    me = await _me(client)
    app_row = await _scalar(client, "SELECT owner_user_id, workspace_id FROM app_project WHERE id = :i",
                            i=r.json()["id"])
    assert app_row[0] == me
    ws_row = await _scalar(client, "SELECT owner_user_id FROM workspace WHERE id = :i",
                           i=app_row[1])
    assert ws_row[0] == me
