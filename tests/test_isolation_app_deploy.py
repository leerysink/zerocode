# tests/test_isolation_app_deploy.py
"""Ticket 006 隔离迁移·应用/部署域：A 的应用 B 不可见、不可操作，越权一律 404。

接缝＝app_service.get_app/list_apps 的 owner 过滤（HTTP 层传 uid，内部受控链路不传）；
deploy_record 等子资源经 app_project 传递隔离。行用直插造（真实创建链的覆盖在
test_apps_api/test_app_service，本文件只验隔离语义，不重复烧 docker）。
"""
import pytest
from sqlalchemy import text

pytestmark = pytest.mark.asyncio


async def _me(client) -> int:
    return (await client.get("/api/auth/me")).json()["id"]


async def _plant_app(app, owner_id: int, slug: str, port: int) -> dict:
    """直插 workspace+app_project 一对行（端口/slug 全局唯一，用例外分配互不冲突）。"""
    async with app.state.session_factory() as s:
        wid = (await s.execute(text(
            "INSERT INTO workspace (name, root_path, owner_user_id) "
            "VALUES (:n, :p, :o) RETURNING id"),
            {"n": f"隔离-{slug}", "p": f"D:/iso/{slug}", "o": owner_id})).scalar()
        app_id = (await s.execute(text(
            "INSERT INTO app_project (workspace_id, name, slug, scale, stack, prod_port, "
            "dev_port, owner_user_id) VALUES (:w, :n, :sl, 'small', 'vue3-fastapi', :pp, :dp, :o) "
            "RETURNING id"),
            {"w": wid, "n": f"隔离应用{slug}", "sl": slug, "pp": port, "dp": port + 1,
             "o": owner_id})).scalar()
        await s.commit()
    return {"id": app_id, "workspace_id": wid, "slug": slug}


async def test_B的应用列表看不到A的应用(two_auth_clients):
    alice, bob = two_auth_clients
    planted = await _plant_app(alice.app, await _me(alice), "isoapp01", 8510)
    listing = (await bob.get("/api/apps")).json()
    assert [a["id"] for a in listing] == []
    listing_a = (await alice.get("/api/apps")).json()
    assert [a["id"] for a in listing_a] == [planted["id"]]


async def test_B按id操作A的应用全部404(two_auth_clients):
    alice, bob = two_auth_clients
    planted = await _plant_app(alice.app, await _me(alice), "isoapp02", 8512)
    aid = planted["id"]
    assert (await bob.get(f"/api/apps/{aid}")).status_code == 404
    assert (await bob.patch(f"/api/apps/{aid}", json={"name": "夺舍"})).status_code == 404
    assert (await bob.delete(f"/api/apps/{aid}")).status_code == 404
    assert (await bob.get(f"/api/apps/{aid}/dev/status")).status_code == 404
    assert (await bob.get(f"/api/apps/{aid}/urls")).status_code == 404
    assert (await bob.get(f"/api/apps/{aid}/dev/logs")).status_code == 404
    assert (await bob.post(f"/api/apps/{aid}/dev/start")).status_code == 404
    assert (await bob.get(f"/api/apps/{aid}/sqlchange/records")).status_code == 404
    # A 自己的详情/状态一切正常（dev 未起，status 查询不依赖 docker 常驻态）
    assert (await alice.get(f"/api/apps/{aid}")).status_code == 200
    # B 的删除没有伤到行
    async with alice.app.state.session_factory() as s:
        assert (await s.execute(text(
            "SELECT 1 FROM app_project WHERE id = :i"), {"i": aid})).first()


async def test_slug查重保持全局_B不能借隔离撞A的slug(two_auth_clients):
    """隔离的是可见性，不是全局 UNIQUE 约束的预检——撞 A 的 slug 必须给出 taken。"""
    alice, bob = two_auth_clients
    await _plant_app(alice.app, await _me(alice), "isoapp03", 8514)
    r = await bob.post("/api/apps/validate-slug", json={"slug": "isoapp03"})
    assert r.status_code == 200 and r.json()["ok"] is False
