# tests/test_db_admin.py
"""应用 DB 操作台服务层与端点。纯函数（env 白名单/语句分类/标识符转义）不碰库；
审计写入用平台 session；连接类端点需真实 provision 的 dev 库（docker+PG 可用才跑）。"""
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.db import make_session_factory
from app.deploy import provision, sqlrun
from app.deploy.errors import DeployError
from app.services import db_admin_service as svc


@pytest.fixture
async def sf(test_engine):
    return make_session_factory(test_engine)


def test_validate_env_ok_and_reject():
    svc.validate_env("dev")
    svc.validate_env("prod")
    with pytest.raises(DeployError) as e:
        svc.validate_env("test")
    assert e.value.status == 400


def test_quote_ident_escapes_double_quote():
    assert svc.quote_ident('orders') == '"orders"'
    assert svc.quote_ident('a"b') == '"a""b"'


def test_classify_op_by_first_keyword():
    assert svc.classify_op("  SELECT * FROM t") == "select"
    assert svc.classify_op("insert into t values (1)") == "insert"
    assert svc.classify_op("UPDATE t SET a=1") == "update"
    assert svc.classify_op("DELETE FROM t") == "delete"
    assert svc.classify_op("CREATE TABLE t(a int)") == "ddl"
    assert svc.classify_op("EXPLAIN SELECT 1") == "other"


async def test_log_operation_writes_row(sf):
    async with sf() as s:
        wid = (await s.execute(text(
            "INSERT INTO workspace (name, root_path) VALUES ('a', '/tmp/a') RETURNING id"))).scalar()
        aid = (await s.execute(text(
            "INSERT INTO app_project (workspace_id, name, slug, scale, stack, prod_port, dev_port) "
            "VALUES (:w,'a','oplogx','small','vue3-fastapi',8501,8502) RETURNING id"),
            {"w": wid})).scalar()
        await s.commit()
    await svc.log_operation(sf, app_id=aid, actor_user_id=7, env="dev",
                            op_type="select", sql_text="SELECT 1", target_table=None,
                            status="success", affected=1, duration_ms=3)
    async with sf() as s:
        row = (await s.execute(text(
            "SELECT env, op_type, status, actor_user_id FROM db_operation_log WHERE app_id=:a"),
            {"a": aid})).mappings().first()
    assert row["env"] == "dev" and row["op_type"] == "select" and row["status"] == "success"
    assert row["actor_user_id"] == 7


# ---------- Task 2: 表列表端点 ----------

class _FakeReq:
    def __init__(self, app): self.app = app


@pytest.fixture
async def db_app(client):
    """注册账号 + 造 app + provision 真实 dev 库并建一张表。返 (client, app_id, slug, uid)。

    仅需 PG（conftest 已要求）、不需 docker：provision_env/drop_env 直连实例建删库。"""
    sf = client.app.state.session_factory
    uid = (await client.get("/api/auth/me")).json()["id"]
    slug = "dbt1"
    async with sf() as s:
        wid = (await s.execute(text(
            "INSERT INTO workspace (name, root_path, owner_user_id) VALUES (:n,:r,:o) RETURNING id"),
            {"n": slug, "r": f"/tmp/{slug}", "o": uid})).scalar()
        aid = (await s.execute(text(
            "INSERT INTO app_project (workspace_id, name, slug, scale, stack, prod_port, dev_port, owner_user_id) "
            "VALUES (:w,:n,:s,'small','vue3-fastapi',8511,8512,:o) RETURNING id"),
            {"w": wid, "n": slug, "s": slug, "o": uid})).scalar()
        await s.commit()
    cred = await provision.provision_env(sf, slug, "dev")
    eng = create_async_engine(svc.target_engine_url(_FakeReq(client.app), cred))
    async with eng.begin() as conn:
        await conn.exec_driver_sql("CREATE TABLE IF NOT EXISTS widgets (id int primary key, name text)")
    await eng.dispose()
    yield client, aid, slug, uid
    await provision.drop_env(sf, slug, "dev")


async def _make_app_row(sf, uid, slug, prod_port, dev_port):
    async with sf() as s:
        wid = (await s.execute(text(
            "INSERT INTO workspace (name, root_path, owner_user_id) VALUES (:n,:r,:o) RETURNING id"),
            {"n": slug, "r": f"/tmp/{slug}", "o": uid})).scalar()
        aid = (await s.execute(text(
            "INSERT INTO app_project (workspace_id, name, slug, scale, stack, prod_port, dev_port, owner_user_id) "
            "VALUES (:w,:n,:s,'small','vue3-fastapi',:pp,:dp,:o) RETURNING id"),
            {"w": wid, "n": slug, "s": slug, "pp": prod_port, "dp": dev_port, "o": uid})).scalar()
        await s.commit()
    return aid


async def test_tables_endpoint_lists_widget(db_app):
    client, aid, slug, uid = db_app
    r = await client.get(f"/api/apps/{aid}/db/dev/tables")
    assert r.status_code == 200, r.text
    names = [t["name"] for t in r.json()["tables"]]
    assert "widgets" in names


async def test_tables_rejects_bad_env(client):
    sf = client.app.state.session_factory
    uid = (await client.get("/api/auth/me")).json()["id"]
    aid = await _make_app_row(sf, uid, "envx", 8521, 8522)
    r = await client.get(f"/api/apps/{aid}/db/qa/tables")
    assert r.status_code == 400


async def test_tables_foreign_app_404(two_auth_clients):
    c_alice, c_bob = two_auth_clients
    sf = c_alice.app.state.session_factory
    uid_a = (await c_alice.get("/api/auth/me")).json()["id"]
    aid = await _make_app_row(sf, uid_a, "foreignx", 8513, 8514)
    r = await c_bob.get(f"/api/apps/{aid}/db/dev/tables")
    assert r.status_code == 404


# ---------- Task 3: 表结构端点 ----------

async def test_schema_endpoint_returns_columns_and_pk(db_app):
    client, aid, slug, uid = db_app
    r = await client.get(f"/api/apps/{aid}/db/dev/tables/widgets/schema")
    assert r.status_code == 200, r.text
    body = r.json()
    colnames = [c["name"] for c in body["columns"]]
    assert "id" in colnames and "name" in colnames
    assert body["primary_key"] == ["id"]


async def test_schema_unknown_table_404(db_app):
    client, aid, *_ = db_app
    r = await client.get(f"/api/apps/{aid}/db/dev/tables/nope_nope/schema")
    assert r.status_code == 404


# ---------- Task 4: 分页数据端点 ----------

async def test_data_endpoint_pages_and_totals(db_app):
    client, aid, slug, uid = db_app
    sf = client.app.state.session_factory
    cred = await provision.read_cred(sf, slug, "dev")
    eng = create_async_engine(svc.target_engine_url(_FakeReq(client.app), cred))
    async with eng.begin() as conn:
        await conn.exec_driver_sql("INSERT INTO widgets(id,name) VALUES (1,'a'),(2,'b'),(3,'c')")
    await eng.dispose()
    r = await client.get(f"/api/apps/{aid}/db/dev/tables/widgets/data?page=1&size=2")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 3
    assert len(body["rows"]) == 2
    assert set(body["columns"]) >= {"id", "name"}


async def test_data_endpoint_sort_whitelist(db_app):
    client, aid, *_ = db_app
    r = await client.get(f"/api/apps/{aid}/db/dev/tables/widgets/data?sort=name&dir=desc")
    assert r.status_code == 200, r.text
    r2 = await client.get(f"/api/apps/{aid}/db/dev/tables/widgets/data?sort=name;--&dir=desc")
    assert r2.status_code == 400  # 非法排序列被白名单挡下


# ---------- Task 5: 任意 SQL 执行端点 ----------

async def test_sql_select_returns_rows(db_app):
    client, aid, *_ = db_app
    r = await client.post(f"/api/apps/{aid}/db/dev/sql", json={"sql": "SELECT 1 AS n"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "rows" and body["rows"][0]["n"] == 1


async def test_sql_write_and_audit_success(db_app):
    client, aid, *_ = db_app
    r = await client.post(f"/api/apps/{aid}/db/dev/sql",
                          json={"sql": "INSERT INTO widgets(id,name) VALUES (99,'z')"})
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == "affected" and r.json()["affected"] == 1
    sf = client.app.state.session_factory
    async with sf() as s:
        row = (await s.execute(text(
            "SELECT status FROM db_operation_log WHERE app_id=:a AND op_type='insert' "
            "ORDER BY id DESC LIMIT 1"), {"a": aid})).mappings().first()
    assert row and row["status"] == "success"


async def test_sql_error_writes_error_audit(db_app):
    client, aid, *_ = db_app
    r = await client.post(f"/api/apps/{aid}/db/dev/sql", json={"sql": "SELECT * FROM not_a_table"})
    assert r.status_code == 500
    sf = client.app.state.session_factory
    async with sf() as s:
        row = (await s.execute(text(
            "SELECT status, error FROM db_operation_log WHERE app_id=:a AND status='error' "
            "ORDER BY id DESC LIMIT 1"), {"a": aid})).mappings().first()
    assert row and row["error"]


# ---------- Task 6: 审计查询端点 ----------

async def test_audit_endpoint_returns_recent_ops(db_app):
    client, aid, *_ = db_app
    await client.post(f"/api/apps/{aid}/db/dev/sql", json={"sql": "SELECT 1 AS n"})
    r = await client.get(f"/api/apps/{aid}/db/audit?limit=10")
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert any(it["op_type"] == "select" and it["status"] == "success" for it in items)
