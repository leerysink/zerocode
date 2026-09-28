# tests/test_db_admin.py
"""应用 DB 操作台服务层与端点。纯函数（env 白名单/语句分类/标识符转义）不碰库；
审计写入用平台 session；连接类端点需真实 provision 的 dev 库（docker+PG 可用才跑）。"""
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.db import make_session_factory
from app.deploy import provision
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


async def test_sql_write_actually_persists(db_app):
    """回归：写必须真落库（防 execute_sql 漏 commit 静默回滚）。写后用独立连接直查 count。"""
    client, aid, slug, uid = db_app
    r = await client.post(f"/api/apps/{aid}/db/dev/sql",
                          json={"sql": "INSERT INTO widgets(id,name) VALUES (123,'persist')"})
    assert r.status_code == 200, r.text
    sf = client.app.state.session_factory
    cred = await provision.read_cred(sf, slug, "dev")
    eng = create_async_engine(svc.target_engine_url(_FakeReq(client.app), cred))
    try:
        async with eng.connect() as conn:
            n = (await conn.exec_driver_sql(
                "SELECT count(*) FROM widgets WHERE id=123")).scalar()
    finally:
        await eng.dispose()
    assert n == 1


async def test_sql_illegal_env_400(db_app):
    client, aid, *_ = db_app
    r = await client.post(f"/api/apps/{aid}/db/qa/sql", json={"sql": "SELECT 1"})
    assert r.status_code == 400


async def test_audit_illegal_env_400(db_app):
    client, aid, *_ = db_app
    r = await client.get(f"/api/apps/{aid}/db/audit?env=qa")
    assert r.status_code == 400


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


# ---------- Ticket 001: 结果集护栏（行上限截断 + 语句超时）----------

async def test_sql_select_row_cap_truncates(db_app, monkeypatch):
    """边界：命中 SELECT_ROW_CAP 时库侧即截断，返回恰为上限行且 truncated=true。"""
    monkeypatch.setattr(svc, "SELECT_ROW_CAP", 5)
    client, aid, *_ = db_app
    r = await client.post(f"/api/apps/{aid}/db/dev/sql",
                          json={"sql": "SELECT generate_series(1,20) AS n"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "rows"
    assert len(body["rows"]) == 5
    assert body["truncated"] is True


async def test_sql_select_under_cap_not_truncated(db_app, monkeypatch):
    """边界：行数未达上限时 truncated=false，不丢行。"""
    monkeypatch.setattr(svc, "SELECT_ROW_CAP", 5)
    client, aid, *_ = db_app
    r = await client.post(f"/api/apps/{aid}/db/dev/sql",
                          json={"sql": "SELECT generate_series(1,3) AS n"})
    body = r.json()
    assert len(body["rows"]) == 3
    assert body["truncated"] is False


async def test_sql_select_timeout_enforced(db_app, monkeypatch):
    """边界：超长 SELECT 被 statement_timeout 中断 → /sql 返回 500 且写 error 审计。"""
    monkeypatch.setattr(svc, "STATEMENT_TIMEOUT", "1s")
    client, aid, *_ = db_app
    r = await client.post(f"/api/apps/{aid}/db/dev/sql", json={"sql": "SELECT pg_sleep(5)"})
    assert r.status_code == 500
    sf = client.app.state.session_factory
    async with sf() as s:
        row = (await s.execute(text(
            "SELECT status, error FROM db_operation_log WHERE app_id=:a AND status='error' "
            "ORDER BY id DESC LIMIT 1"), {"a": aid})).mappings().first()
    assert row and row["error"]


# ---------- Ticket 005: 后端边界用例补齐 ----------

async def _seed_widgets(client, slug, pairs):
    """往 provision 好的 dev 库 widgets 表插行（独立连接，与端点无关）。"""
    eng = await _dev_eng(client, slug)
    try:
        async with eng.begin() as conn:
            for i, n in pairs:
                await conn.exec_driver_sql(f"INSERT INTO widgets(id,name) VALUES ({i},'{n}')")
    finally:
        await eng.dispose()


async def _dev_eng(client, slug):
    sf = client.app.state.session_factory
    cred = await provision.read_cred(sf, slug, "dev")
    return create_async_engine(svc.target_engine_url(_FakeReq(client.app), cred))


async def test_sql_update_and_delete_report_affected_rows(db_app):
    """边界：UPDATE/DELETE 返回真实影响行数（且真落库）。"""
    client, aid, slug, _ = db_app
    await _seed_widgets(client, slug, [(1, "a"), (2, "b"), (3, "c")])
    r = await client.post(f"/api/apps/{aid}/db/dev/sql",
                          json={"sql": "UPDATE widgets SET name='x' WHERE id < 3"})
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == "affected" and r.json()["affected"] == 2
    r2 = await client.post(f"/api/apps/{aid}/db/dev/sql",
                           json={"sql": "DELETE FROM widgets WHERE id < 3"})
    assert r2.json()["affected"] == 2
    eng = await _dev_eng(client, slug)
    try:
        async with eng.connect() as conn:
            left = (await conn.exec_driver_sql("SELECT count(*) FROM widgets")).scalar()
    finally:
        await eng.dispose()
    assert left == 1  # 仅 id=3 存活，证实 DELETE 已提交


async def test_sql_ddl_persists_and_affected_non_negative(db_app):
    """边界：DDL 执行后对象确实变更；rowcount=-1 被钳为非负 0。"""
    client, aid, slug, _ = db_app
    r = await client.post(f"/api/apps/{aid}/db/dev/sql",
                          json={"sql": "CREATE TABLE probe_ddl (x int)"})
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == "affected" and r.json()["affected"] >= 0
    eng = await _dev_eng(client, slug)
    try:
        async with eng.connect() as conn:
            exists = (await conn.exec_driver_sql(
                "SELECT 1 FROM information_schema.tables WHERE table_name='probe_ddl'")).first()
    finally:
        await eng.dispose()
    assert exists is not None


async def test_data_endpoint_injection_table_name_not_500(db_app):
    """边界：恶意表名（含引号/分号/空格）作为绑定参数查不到→非500，不改变语句、不注入。"""
    client, aid, slug, _ = db_app
    await _seed_widgets(client, slug, [(1, "a")])
    evil = 'widgets"; DROP TABLE widgets; --'
    r = await client.get(f"/api/apps/{aid}/db/dev/tables/{evil}/data")
    assert r.status_code in (404, 400)
    # 关键：widgets 表仍在（注入未执行 DROP）
    eng = await _dev_eng(client, slug)
    try:
        async with eng.connect() as conn:
            still = (await conn.exec_driver_sql(
                "SELECT 1 FROM information_schema.tables WHERE table_name='widgets'")).first()
    finally:
        await eng.dispose()
    assert still is not None


async def test_data_endpoint_readable_without_pk(db_app):
    """边界：无主键表的 data 端仍可浏览（禁编辑不影响读取）。"""
    client, aid, slug, _ = db_app
    eng = await _dev_eng(client, slug)
    async with eng.begin() as conn:
        await conn.exec_driver_sql("CREATE TABLE nopk (a int, b text)")
        await conn.exec_driver_sql("INSERT INTO nopk VALUES (1,'x'),(2,'y')")
    await eng.dispose()
    r = await client.get(f"/api/apps/{aid}/db/dev/tables/nopk/data")
    assert r.status_code == 200, r.text
    assert len(r.json()["rows"]) == 2
    rs = await client.get(f"/api/apps/{aid}/db/dev/tables/nopk/schema")
    assert rs.json()["primary_key"] == []  # 无主键→前端禁编辑的依据


async def test_audit_foreign_app_404(two_auth_clients):
    """边界：非归属账号查他人 app 审计→404（不暴露存在性）。"""
    c_alice, c_bob = two_auth_clients
    sf = c_alice.app.state.session_factory
    uid_a = (await c_alice.get("/api/auth/me")).json()["id"]
    aid = await _make_app_row(sf, uid_a, "auditforeign", 8531, 8532)
    r = await c_bob.get(f"/api/apps/{aid}/db/audit")
    assert r.status_code == 404


# ---------- db-sql-console Ticket 002: 批量端点（示踪弹） ----------

async def _audit_count(sf, aid):
    async with sf() as s:
        return (await s.execute(text(
            "SELECT count(*) FROM db_operation_log WHERE app_id=:a"), {"a": aid})).scalar()


async def test_sql_batch_two_selects_returns_two_results(db_app):
    """示踪弹主流程：一个框两条 SELECT → 两个结果块，顺序与输入一致。"""
    client, aid, *_ = db_app
    r = await client.post(f"/api/apps/{aid}/db/dev/sql/batch",
                          json={"statements": ["SELECT 1 AS n", "SELECT 2 AS n"]})
    assert r.status_code == 200, r.text
    results = r.json()["results"]
    assert len(results) == 2
    assert [it["index"] for it in results] == [0, 1]
    assert [it["status"] for it in results] == ["ok", "ok"]
    assert [it["op"] for it in results] == ["select", "select"]
    assert results[0]["rows"] == [{"n": 1}] and results[1]["rows"] == [{"n": 2}]
    assert all(isinstance(it["duration_ms"], int) for it in results)


async def test_sql_batch_mixed_writes_and_select_in_order(db_app):
    """边界：写-写-读混合段按序生效，affected/rows 两种形态共存（US-08）。"""
    client, aid, slug, _ = db_app
    await _seed_widgets(client, slug, [(1, "a")])
    r = await client.post(f"/api/apps/{aid}/db/dev/sql/batch", json={"statements": [
        "INSERT INTO widgets(id,name) VALUES (11,'k1')",
        "INSERT INTO widgets(id,name) VALUES (12,'k2')",
        "SELECT id FROM widgets ORDER BY id",
    ]})
    assert r.status_code == 200, r.text
    results = r.json()["results"]
    assert [it["kind"] for it in results] == ["affected", "affected", "rows"]
    assert [it["affected"] for it in results[:2]] == [1, 1]
    assert [row["id"] for row in results[2]["rows"]] == [1, 11, 12]  # 前面的写已提交可见


async def test_sql_batch_uses_one_engine(db_app, monkeypatch):
    """边界：N 条批量只建一次引擎/一条连接（防每条语句重连，US-09 的代价来源）。"""
    client, aid, *_ = db_app
    calls = []
    real = svc.create_async_engine

    def counting(url, *a, **kw):
        calls.append(url)
        return real(url, *a, **kw)

    monkeypatch.setattr(svc, "create_async_engine", counting)
    r = await client.post(f"/api/apps/{aid}/db/dev/sql/batch",
                          json={"statements": ["SELECT 1", "SELECT 2", "SELECT 3"]})
    assert r.status_code == 200, r.text
    assert len(calls) == 1


async def test_sql_batch_writes_one_audit_per_statement(db_app):
    """边界：一次 N 条批量 → 审计新增 N 条（逐条留痕，不是整段一条，US-14）。"""
    client, aid, *_ = db_app
    sf = client.app.state.session_factory
    before = await _audit_count(sf, aid)
    r = await client.post(f"/api/apps/{aid}/db/dev/sql/batch",
                          json={"statements": ["SELECT 1", "SELECT 2"]})
    assert r.status_code == 200, r.text
    assert await _audit_count(sf, aid) - before == 2
    async with sf() as s:
        rows = (await s.execute(text(
            "SELECT op_type, status, sql_text FROM db_operation_log WHERE app_id=:a "
            "ORDER BY id DESC LIMIT 2"), {"a": aid})).mappings().all()
    assert {row["sql_text"] for row in rows} == {"SELECT 1", "SELECT 2"}
    assert all(row["status"] == "success" and row["op_type"] == "select" for row in rows)


async def test_sql_batch_foreign_app_404(two_auth_clients):
    """边界：非归属账号调批量端点→404（与单条 /sql 同一归属过滤）。"""
    c_alice, c_bob = two_auth_clients
    sf = c_alice.app.state.session_factory
    uid_a = (await c_alice.get("/api/auth/me")).json()["id"]
    aid = await _make_app_row(sf, uid_a, "batchforeign", 8541, 8542)
    r = await c_bob.post(f"/api/apps/{aid}/db/dev/sql/batch", json={"statements": ["SELECT 1"]})
    assert r.status_code == 404


async def test_sql_batch_illegal_env_400(db_app):
    """边界：批量端点非法 env → 400（不建连）。"""
    client, aid, *_ = db_app
    r = await client.post(f"/api/apps/{aid}/db/qa/sql/batch", json={"statements": ["SELECT 1"]})
    assert r.status_code == 400


# ---------- db-sql-console Ticket 002 边界: 行数护栏包裹 vs 行尾注释 ----------
# 复现的 bug：SELECT 被包成 `SELECT * FROM (<sql>) AS _zc_cap LIMIT n` 时，
# 若 <sql> 以 `--` 行注释收尾，右括号与 LIMIT 会被注释吃掉 → syntax error at end of input。
# 浏览器实测（Ticket 002 手工冒烟）踩中，单条与批量两条路径同源（都走 execute_one）。

async def test_sql_select_trailing_line_comment_survives_row_cap(db_app):
    """边界：单条 SELECT 以行注释收尾 → 包裹换行终止注释，仍正常返回 rows（不再 500）。"""
    client, aid, *_ = db_app
    r = await client.post(f"/api/apps/{aid}/db/dev/sql", json={"sql": "SELECT 1 AS n -- x"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "rows"
    assert body["rows"] == [{"n": 1}]
    assert body["truncated"] is False


async def test_sql_batch_select_trailing_line_comment_keeps_later_statements(db_app):
    """边界：批量首条带行尾注释不吞掉后续语句——两条都要 ok（回归即整批 500）。"""
    client, aid, *_ = db_app
    r = await client.post(f"/api/apps/{aid}/db/dev/sql/batch", json={"statements": [
        "SELECT 1 AS n -- x",
        "SELECT 2 AS m",
    ]})
    assert r.status_code == 200, r.text
    results = r.json()["results"]
    assert [it["status"] for it in results] == ["ok", "ok"]
    assert results[0]["rows"] == [{"n": 1}] and results[1]["rows"] == [{"m": 2}]


async def test_sql_multiline_select_with_line_comments_on_every_line(db_app):
    """边界：多行 SELECT 每行都带行尾注释（最贴近真实手写脚本），列名与取值不丢。"""
    client, aid, slug, _ = db_app
    await _seed_widgets(client, slug, [(1, "a"), (2, "b")])
    sql = "SELECT id,   -- 主键\n       name  -- 名称\nFROM widgets  -- 全表\n"
    r = await client.post(f"/api/apps/{aid}/db/dev/sql", json={"sql": sql})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "rows"
    assert body["columns"] == ["id", "name"]
    assert [row["id"] for row in body["rows"]] == [1, 2]


async def test_sql_select_comment_variants_still_capped(db_app, monkeypatch):
    """边界：行注释带分号收尾 / 块注释收尾 / 中文全角注释，三种写法都不破语法且截断仍生效。"""
    monkeypatch.setattr(svc, "SELECT_ROW_CAP", 2)
    client, aid, *_ = db_app
    for sql in (
        "SELECT generate_series(1,10) AS n -- x;",        # 注释后有分号
        "SELECT generate_series(1,10) AS n /* x */",      # 块注释收尾（原本就合法）
        "SELECT generate_series(1,10) AS n -- 注释（全角）",
    ):
        r = await client.post(f"/api/apps/{aid}/db/dev/sql", json={"sql": sql})
        assert r.status_code == 200, f"{sql!r} → {r.status_code} {r.text}"
        body = r.json()
        assert body["kind"] == "rows", sql
        assert len(body["rows"]) == 2 and body["truncated"] is True, sql
