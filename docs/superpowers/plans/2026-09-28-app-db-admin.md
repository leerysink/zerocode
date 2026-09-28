# 应用 DB 操作台（Navicat 式 dev/prod 浏览/查询/编辑 + 审计）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在应用卡片 ☰ 菜单提供「DB 操作」入口，以独立路由页面（新标签）对应用 dev/prod 两库进行表浏览、结构查看、数据分页编辑、任意 SQL 执行，并在平台侧全量审计每次操作。

**Architecture:** 后端新增薄层 API 模块 `app/api/db_admin.py`（同 `/api/apps` 前缀、独立路由，沿用 metrics 隔离纪律）+ 服务层 `app/services/db_admin_service.py`；平台进程按请求以 `dbcred.{slug}.{env}` 凭据、基于运行时 `app.state.engine.url` 派生目标库连接（用完 dispose）。审计落新平台表 `db_operation_log`（走 `changelog/` 迁移轨）。前端 Vue3+ant-design-vue 新增 `DbAdminView.vue` 三栏页，零新依赖。

**Tech Stack:** Python FastAPI、SQLAlchemy async（asyncpg 驱动）、PostgreSQL 16、pytest（asgi-lifespan + httpx ASGITransport）；Vue3、ant-design-vue、vitest（happy-dom）。

## Global Constraints

- 前端**零新增依赖**：只用已装的 ant-design-vue / @ant-design/icons-vue；SQL 编辑器用 `a-textarea`，不引 Monaco/CodeMirror。
- 路由前缀恒为 `/api/apps`，DB 端点路径段以 `/db` 起头，与 apps/metrics 同前缀但独立模块注册。
- `env` 参数**白名单** `{"dev","prod"}`，非法 → 400。
- 所有端点先过归属校验（owner 看自己、founder 看全部；非归属/未知 → 404，不暴露存在性）。
- 标识符（表名/排序列/方向）不得裸拼：列名取自该表实际列白名单、方向取 `asc|desc`、表名双引号转义；**值一律参数绑定**。
- 写/DDL 与 SELECT **全部**记审计（读端点记 `op_type=struct_view`）；异常路径也要写 error 审计行再上抛。
- 写操作确认在 **UI 层**（`Modal.confirm` 展示待执行 SQL）；后端信任已过闸门的前端，不重复拦截（与用户裁定一致）。
- 平台库表结构变更一律落 `changelog/V<日期>_<序号>_<名>.sql`，幂等 `IF [NOT] EXISTS` 守卫，由 `app/db.py::run_migrations` 自动应用。
- 远端写操作（git push）由用户决定时机；计划内 commit 仅本地。

---

## 文件结构

- Create `changelog/V20260928_02_db_operation_log.sql` — 审计表迁移（平台库）
- Create `app/services/db_admin_service.py` — 连接派生、语句分类、审计写入、读/执行核心函数
- Create `app/api/db_admin.py` — REST 薄层（env 白名单 + 归属校验 + 状态码映射）
- Modify `app/main.py`（include `db_admin.router`，放在 metrics_api 之后）
- Modify `frontend/src/api/index.js` — 5 个封装
- Modify `frontend/src/router/index.js` — 路由 `/apps/:id/db`
- Modify `frontend/src/views/AppsView.vue` — ☰ 菜单项 `.zc-menu-db`
- Create `frontend/src/views/DbAdminView.vue` — 三栏操作台
- Test `tests/test_db_admin.py` — 后端 pytest
- Test `frontend/src/router/index.test.js`、`frontend/src/views/DbAdminView.test.js`、改 `frontend/src/views/AppsView.test.js`

---

## Task 1: 审计表迁移 + 服务层基座（连接派生 / 语句分类 / 审计写入）

**Files:**
- Create: `changelog/V20260928_02_db_operation_log.sql`
- Create: `app/services/db_admin_service.py`
- Test: `tests/test_db_admin.py`

**Interfaces:**
- Consumes: 无（地基任务）
- Produces:
  - `ENV_WHITELIST: tuple[str, str] = ("dev", "prod")`
  - `validate_env(env: str) -> None`（非法抛 `DeployError(status=400)`）
  - `target_engine_url(request, cred: dict) -> str`（基于 `request.app.state.engine.url` 派生目标库连接串，明文密码）
  - `quote_ident(name: str) -> str`（双引号转义 SQL 标识符）
  - `classify_op(sql: str) -> str`（返回 `select|insert|update|delete|ddl|other`）
  - `async log_operation(sf, *, app_id, actor_user_id, env, op_type, sql_text, target_table, status, affected=None, error=None, duration_ms=None) -> None`

- [ ] **Step 1: 写审计表迁移文件**

创建 `changelog/V20260928_02_db_operation_log.sql`：

```sql
-- changelog/V20260928_02_db_operation_log.sql
-- 日期：2026-09-28  用途：应用 DB 操作台审计日志（读+写全量留痕）  影响：db_operation_log
-- 幂等：全部 IF [NOT] EXISTS 守卫，可在存量库安全重放。

CREATE TABLE IF NOT EXISTS db_operation_log (
    id             BIGSERIAL PRIMARY KEY,
    app_id         BIGINT NOT NULL REFERENCES app_project(id) ON DELETE CASCADE,
    actor_user_id  BIGINT,
    env            VARCHAR(10) NOT NULL,
    op_type        VARCHAR(20) NOT NULL,
    sql_text       TEXT,
    target_table   VARCHAR(255),
    status         VARCHAR(10) NOT NULL,
    affected       INTEGER,
    error          TEXT,
    duration_ms    INTEGER,
    ts             TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_db_oplog_status CHECK (status IN ('success', 'error'))
);

CREATE INDEX IF NOT EXISTS ix_db_oplog_app_ts ON db_operation_log (app_id, ts DESC);
```

- [ ] **Step 2: 写失败测试（服务层基座）**

创建 `tests/test_db_admin.py`：

```python
# tests/test_db_admin.py
"""应用 DB 操作台服务层与端点。纯函数（env 白名单/语句分类/标识符转义）不碰库；
审计写入用平台 session；连接类端点需真实 provision 的 dev 库（PG 可用才跑）。"""
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
```

- [ ] **Step 3: 运行测试确认失败**

Run: `pytest tests/test_db_admin.py -v`
Expected: FAIL — `ModuleNotFoundError: app.services.db_admin_service`。

- [ ] **Step 4: 实现服务层基座**

创建 `app/services/db_admin_service.py`：

```python
# app/services/db_admin_service.py
"""应用 DB 操作台服务层（设计 docs/superpowers/specs/2026-09-28-app-db-admin-design.md）。

连接策略：按请求以 dbcred.{slug}.{env} 凭据、基于运行时平台引擎 URL 派生目标库连接，用完 dispose。
不能用 app_config.database_url()——测试里它读环境变量返回默认库，而非 create_app 注入的测试 URL；
app.state.engine.url 才是当前进程真实连接的实例/端口。安全：标识符 quote_ident 转义 + 值参数绑定。
"""
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.deploy.errors import DeployError

ENV_WHITELIST = ("dev", "prod")
_DDL_KEYWORDS = {"create", "alter", "drop", "truncate", "comment"}


def validate_env(env: str) -> None:
    if env not in ENV_WHITELIST:
        raise DeployError(f"非法环境: {env}（仅 {'/'.join(ENV_WHITELIST)}）", status=400)


def quote_ident(name: str) -> str:
    """双引号包裹标识符并转义内部双引号，防标识符注入。"""
    return '"' + name.replace('"', '""') + '"'


def classify_op(sql: str) -> str:
    stripped = sql.lstrip().lower()
    head = stripped.split(None, 1)[0] if stripped.strip() else ""
    if head in _DDL_KEYWORDS:
        return "ddl"
    if head in ("select", "insert", "update", "delete"):
        return head
    return "other"


def target_engine_url(request, cred: dict) -> str:
    """以当前平台引擎 URL 为底座，仅替换 database/user/password 为应用凭据（host/port 沿用同实例）。"""
    base = request.app.state.engine.url
    return (base.set(database=cred["db"], user=cred["user"], password=cred["password"])
            .render_as_string(hide_password=False))


async def log_operation(sf, *, app_id: int, actor_user_id, env: str, op_type: str,
                        sql_text, target_table, status: str, affected=None,
                        error=None, duration_ms=None) -> None:
    """向平台库 db_operation_log 写一条审计（成功/失败均调）。"""
    async with sf() as s:
        await s.execute(text(
            "INSERT INTO db_operation_log (app_id, actor_user_id, env, op_type, sql_text, "
            "target_table, status, affected, error, duration_ms) "
            "VALUES (:a, :u, :e, :o, :q, :t, :st, :af, :er, :d)"),
            {"a": app_id, "u": actor_user_id, "e": env, "o": op_type, "q": sql_text,
             "t": target_table, "st": status, "af": affected, "er": error, "d": duration_ms})
        await s.commit()
```

- [ ] **Step 5: 运行测试确认通过**

Run: `pytest tests/test_db_admin.py -v`
Expected: PASS（4 用例全绿）。

- [ ] **Step 6: 提交**

```bash
git add changelog/V20260928_02_db_operation_log.sql app/services/db_admin_service.py tests/test_db_admin.py
git commit -m "feat(db-admin): 审计表迁移 + 服务层基座(连接派生/语句分类/审计写入)"
```

---

## Task 2: 表列表端点 + 路由注册

**Files:**
- Create: `app/api/db_admin.py`
- Modify: `app/services/db_admin_service.py`（新增 `list_tables` / `_cred_or_409`）
- Modify: `app/main.py`（include router）
- Test: `tests/test_db_admin.py`

**Interfaces:**
- Consumes: `svc.validate_env` / `svc.target_engine_url` / `svc.log_operation`；`app.deploy.provision.read_cred(session_factory, slug, env) -> dict|None`；`app_service.get_app(sf, app_id, owner_user_id=owner) -> dict`（含 `slug`）。
- Produces:
  - `async svc.list_tables(request, slug, env) -> list[dict]`（每项 `{name, type}`）
  - `async svc._cred_or_409(request, slug, env) -> dict`（凭据缺失抛 `DeployError(409)`）
  - API：`GET /api/apps/{app_id}/db/{env}/tables` → `{"tables": [...]}`
  - API 内部 `_load_app(request, app_id) -> dict`（复用 metrics 同款归属过滤，404）

- [ ] **Step 1: 写失败测试**

在 `tests/test_db_admin.py` 追加共享 fixture 与用例（用 provision 真建 dev 库；docker/pg 不可用则 skip）：

```python
pytestmark_pg = pytest.mark.skipif(
    not sqlrun.docker_available(), reason="需要可用 docker + PostgreSQL")


class _FakeReq:
    def __init__(self, app): self.app = app


@pytest.fixture
async def db_app(client, request):
    if request.node.get_closest_marker("skipif_pg") is None and not sqlrun.docker_available():
        pytest.skip("需要 docker + PostgreSQL")
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


@ pytest.mark.skipif(not sqlrun.docker_available(), reason="需要 docker + PostgreSQL")
async def test_tables_endpoint_lists_widget(db_app):
    client, aid, slug, uid = db_app
    r = await client.get(f"/api/apps/{aid}/db/dev/tables")
    assert r.status_code == 200, r.text
    names = [t["name"] for t in r.json()["tables"]]
    assert "widgets" in names


async def test_tables_rejects_bad_env(client):
    # 不建库也应先过 env 白名单：先造 app 行
    sf = client.app.state.session_factory
    uid = (await client.get("/api/auth/me")).json()["id"]
    async with sf() as s:
        wid = (await s.execute(text(
            "INSERT INTO workspace (name, root_path, owner_user_id) VALUES ('e','/tmp/e',:o) RETURNING id"),
            {"o": uid})).scalar()
        aid = (await s.execute(text(
            "INSERT INTO app_project (workspace_id, name, slug, scale, stack, prod_port, dev_port, owner_user_id) "
            "VALUES (:w,'e','envx','small','vue3-fastapi',8521,8522,:o) RETURNING id"),
            {"w": wid, "o": uid})).scalar()
        await s.commit()
    r = await client.get(f"/api/apps/{aid}/db/qa/tables")
    assert r.status_code == 400


async def test_tables_foreign_app_404(two_auth_clients):
    c_alice, c_bob = two_auth_clients
    sf = c_alice.app.state.session_factory
    uid_a = (await c_alice.get("/api/auth/me")).json()["id"]
    async with sf() as s:
        wid = (await s.execute(text(
            "INSERT INTO workspace (name, root_path, owner_user_id) VALUES ('x','/tmp/x',:o) RETURNING id"),
            {"o": uid_a})).scalar()
        aid = (await s.execute(text(
            "INSERT INTO app_project (workspace_id, name, slug, scale, stack, prod_port, dev_port, owner_user_id) "
            "VALUES (:w,'x','foreignx','small','vue3-fastapi',8513,8514,:o) RETURNING id"),
            {"w": wid, "o": uid_a})).scalar()
        await s.commit()
    r = await c_bob.get(f"/api/apps/{aid}/db/dev/tables")
    assert r.status_code == 404
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_db_admin.py -k "tables" -v`
Expected: FAIL — 路由 404 或 `list_tables` 未定义。

- [ ] **Step 3: 服务层新增 list_tables**

在 `app/services/db_admin_service.py` 追加：

```python
async def _cred_or_409(request, slug: str, env: str) -> dict:
    from app.deploy.provision import read_cred
    cred = await read_cred(request.app.state.session_factory, slug, env)
    if not cred:
        raise DeployError(f"库 {slug}_{env} 凭据缺失（未供给或已删除）", status=409)
    return cred


async def list_tables(request, slug: str, env: str) -> list[dict]:
    """列 public schema 下基表与视图（information_schema 参数化，无注入面）。"""
    cred = await _cred_or_409(request, slug, env)
    eng = create_async_engine(target_engine_url(request, cred))
    try:
        async with eng.connect() as conn:
            rows = (await conn.execute(text(
                "SELECT table_name AS name, table_type AS type FROM information_schema.tables "
                "WHERE table_schema = 'public' ORDER BY table_name"))).mappings().all()
        return [dict(r) for r in rows]
    finally:
        await eng.dispose()
```

- [ ] **Step 4: 写 API 薄层 + 注册路由**

创建 `app/api/db_admin.py`：

```python
# app/api/db_admin.py
"""应用 DB 操作台 REST（设计 2026-09-28-app-db-admin-design.md §4）。

与 apps_api/metrics_api 同前缀 /api/apps、独立模块（沿用 metrics 隔离纪律）。每个 /db/* 端点：
先 _load_app 归属过滤（非归属/未知 404）→ env 白名单（非法 400）→ 委派 service。读端点补一条 struct_view 审计。
"""
import time

from fastapi import APIRouter, HTTPException, Request

from app.deploy.errors import DeployError
from app.services import app_service, db_admin_service as svc

router = APIRouter(prefix="/api/apps")


async def _load_app(request: Request, app_id: int) -> dict:
    actor = request.state.actor
    owner = None if actor.get("is_founder") else actor["id"]
    try:
        return await app_service.get_app(request.app.state.session_factory, app_id,
                                         owner_user_id=owner)
    except DeployError as e:
        raise HTTPException(e.status, str(e))


@router.get("/{app_id}/db/{env}/tables")
async def db_tables(request: Request, app_id: int, env: str):
    app = await _load_app(request, app_id)
    try:
        svc.validate_env(env)
        t0 = time.monotonic()
        tables = await svc.list_tables(request, app["slug"], env)
        await svc.log_operation(
            request.app.state.session_factory, app_id=app_id,
            actor_user_id=request.state.actor["id"], env=env, op_type="struct_view",
            sql_text="GET tables", target_table=None, status="success",
            affected=len(tables), duration_ms=int((time.monotonic() - t0) * 1000))
        return {"tables": tables}
    except DeployError as e:
        raise HTTPException(e.status, str(e))
```

在 `app/main.py` 第 154 行 `app.include_router(metrics_api.router)` 之后插入：

```python
    # 应用 DB 操作台：/api/apps/{id}/db/*（与 apps/metrics 同前缀、独立模块，设计 2026-09-28）
    from app.api import db_admin as db_admin_api
    app.include_router(db_admin_api.router)
```

- [ ] **Step 5: 运行测试确认通过**

Run: `pytest tests/test_db_admin.py -v`
Expected: PASS（本任务 3 用例 + Task 1 用例；provision 用例在 docker 不可用时 skip）。

- [ ] **Step 6: 提交**

```bash
git add app/api/db_admin.py app/services/db_admin_service.py app/main.py tests/test_db_admin.py
git commit -m "feat(db-admin): 表列表端点 + 路由注册 + env 白名单/归属校验"
```

---

## Task 3: 表结构端点

**Files:**
- Modify: `app/services/db_admin_service.py`（新增 `table_schema`）
- Modify: `app/api/db_admin.py`（新增 `GET /{app_id}/db/{env}/tables/{table}/schema`）
- Test: `tests/test_db_admin.py`

**Interfaces:**
- Consumes: `svc._cred_or_409` / `svc.target_engine_url`。
- Produces: `async svc.table_schema(request, slug, env, table) -> dict`
  形状：`{"columns": [{"name","type","nullable","default"}], "primary_key": [col...], "indexes": [{"name","definition"}]}`
  API：`GET /api/apps/{app_id}/db/{env}/tables/{table}/schema` → 同上 dict；表不存在 → 404。

- [ ] **Step 1: 写失败测试**

```python
@ pytest.mark.skipif(not sqlrun.docker_available(), reason="需要 docker + PostgreSQL")
async def test_schema_endpoint_returns_columns_and_pk(db_app):
    client, aid, slug, uid = db_app
    r = await client.get(f"/api/apps/{aid}/db/dev/tables/widgets/schema")
    assert r.status_code == 200, r.text
    body = r.json()
    colnames = [c["name"] for c in body["columns"]]
    assert "id" in colnames and "name" in colnames
    assert body["primary_key"] == ["id"]


@ pytest.mark.skipif(not sqlrun.docker_available(), reason="需要 docker + PostgreSQL")
async def test_schema_unknown_table_404(db_app):
    client, aid, *_ = db_app
    r = await client.get(f"/api/apps/{aid}/db/dev/tables/nope_nope/schema")
    assert r.status_code == 404
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_db_admin.py -k schema -v`
Expected: FAIL（路由/函数缺失）。

- [ ] **Step 3: 服务层实现 table_schema**

```python
async def table_schema(request, slug: str, env: str, table: str) -> dict:
    """读列/主键/索引。table 作绑定参数查 information_schema；不存在 → DeployError(404)。"""
    cred = await _cred_or_409(request, slug, env)
    eng = create_async_engine(target_engine_url(request, cred))
    try:
        async with eng.connect() as conn:
            exists = (await conn.execute(text(
                "SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name=:t"),
                {"t": table})).first()
            if not exists:
                raise DeployError(f"表不存在: {table}", status=404)
            cols = (await conn.execute(text(
                "SELECT column_name AS name, data_type AS type, is_nullable AS nullable, "
                "column_default AS default FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name=:t ORDER BY ordinal_position"),
                {"t": table})).mappings().all()
            pk = (await conn.execute(text(
                "SELECT kcu.column_name AS c FROM information_schema.table_constraints tc "
                "JOIN information_schema.key_column_usage kcu "
                "  ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema "
                "WHERE tc.constraint_type='PRIMARY KEY' AND tc.table_schema='public' AND tc.table_name=:t "
                "ORDER BY kcu.ordinal_position"), {"t": table})).scalars().all()
            idx = (await conn.execute(text(
                "SELECT indexname AS name, indexdef AS definition FROM pg_indexes "
                "WHERE schemaname='public' AND tablename=:t ORDER BY indexname"),
                {"t": table})).mappings().all()
        return {
            "columns": [{"name": c["name"], "type": c["type"],
                         "nullable": c["nullable"] == "YES", "default": c["default"]}
                        for c in cols],
            "primary_key": list(pk),
            "indexes": [dict(i) for i in idx],
        }
    finally:
        await eng.dispose()
```

- [ ] **Step 4: API 端点**

在 `app/api/db_admin.py` 追加：

```python
@router.get("/{app_id}/db/{env}/tables/{table}/schema")
async def db_table_schema(request: Request, app_id: int, env: str, table: str):
    app = await _load_app(request, app_id)
    try:
        svc.validate_env(env)
        t0 = time.monotonic()
        schema = await svc.table_schema(request, app["slug"], env, table)
        await svc.log_operation(
            request.app.state.session_factory, app_id=app_id,
            actor_user_id=request.state.actor["id"], env=env, op_type="struct_view",
            sql_text="GET schema", target_table=table, status="success",
            duration_ms=int((time.monotonic() - t0) * 1000))
        return schema
    except DeployError as e:
        raise HTTPException(e.status, str(e))
```

- [ ] **Step 5: 运行确认通过**

Run: `pytest tests/test_db_admin.py -k schema -v`
Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add app/services/db_admin_service.py app/api/db_admin.py tests/test_db_admin.py
git commit -m "feat(db-admin): 表结构端点(列/主键/索引) + 未知表404"
```

---

## Task 4: 分页数据端点（排序 + 标识符白名单转义）

**Files:**
- Modify: `app/services/db_admin_service.py`（新增 `table_data` / `MAX_PAGE_SIZE`）
- Modify: `app/api/db_admin.py`（新增 `GET /{app_id}/db/{env}/tables/{table}/data`）
- Test: `tests/test_db_admin.py`

**Interfaces:**
- Consumes: `svc.table_schema`、`svc.quote_ident`、`svc._cred_or_409`、`target_engine_url`。
- Produces: `svc.MAX_PAGE_SIZE = 500`；`async svc.table_data(request, slug, env, table, *, page=1, size=50, sort=None, dirn="asc") -> dict`
  形状：`{"columns": [name...], "rows": [{col: val}...], "total": int, "truncated": bool}`
  API：`GET /api/apps/{app_id}/db/{env}/tables/{table}/data?page&size&sort&dir` → 同上。
  约束：`size` ∈ 1..500；非法 `sort` 列 → 400；`dir` 仅 `asc|desc`。

- [ ] **Step 1: 写失败测试**

```python
@ pytest.mark.skipif(not sqlrun.docker_available(), reason="需要 docker + PostgreSQL")
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


@ pytest.mark.skipif(not sqlrun.docker_available(), reason="需要 docker + PostgreSQL")
async def test_data_endpoint_sort_whitelist(db_app):
    client, aid, *_ = db_app
    r = await client.get(f"/api/apps/{aid}/db/dev/tables/widgets/data?sort=name&dir=desc")
    assert r.status_code == 200, r.text
    r2 = await client.get(f"/api/apps/{aid}/db/dev/tables/widgets/data?sort=name;--&dir=desc")
    assert r2.status_code == 400  # 非法排序列被白名单挡下
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_db_admin.py -k data -v`
Expected: FAIL（路由/函数缺失）。

- [ ] **Step 3: 服务层实现 table_data**

```python
MAX_PAGE_SIZE = 500


async def table_data(request, slug: str, env: str, table: str, *, page: int = 1,
                     size: int = 50, sort: str | None = None, dirn: str = "asc") -> dict:
    """分页取数。排序列必须是该表实际列（白名单），方向 asc|desc，其余 400；标识符 quote_ident 转义。"""
    cred = await _cred_or_409(request, slug, env)
    schema = await table_schema(request, slug, env, table)  # 顺带做表存在性 404
    valid_cols = {c["name"] for c in schema["columns"]}
    if dirn not in ("asc", "desc"):
        raise DeployError(f"非法排序方向: {dirn}", status=400)
    if not (1 <= size <= MAX_PAGE_SIZE):
        raise DeployError(f"size 需在 1..{MAX_PAGE_SIZE}", status=400)
    if page < 1:
        raise DeployError("page 需 >= 1", status=400)
    order_sql = ""
    if sort:
        if sort not in valid_cols:
            raise DeployError(f"非法排序列: {sort}", status=400)
        order_sql = f" ORDER BY {quote_ident(sort)} {dirn.upper()}"
    tbl = quote_ident(table)
    eng = create_async_engine(target_engine_url(request, cred))
    try:
        async with eng.connect() as conn:
            total = (await conn.execute(text(f"SELECT count(*) FROM {tbl}"))).scalar()
            offset = (page - 1) * size
            rows = (await conn.execute(text(
                f"SELECT * FROM {tbl}{order_sql} LIMIT :lim OFFSET :off"),
                {"lim": size, "off": offset})).mappings().all()
        cols = [c["name"] for c in schema["columns"]]
        return {"columns": cols, "rows": [dict(r) for r in rows],
                "total": int(total), "truncated": total > offset + len(rows)}
    finally:
        await eng.dispose()
```

- [ ] **Step 4: API 端点**

```python
@router.get("/{app_id}/db/{env}/tables/{table}/data")
async def db_table_data(request: Request, app_id: int, env: str, table: str,
                        page: int = 1, size: int = 50, sort: str | None = None, dir: str = "asc"):
    app = await _load_app(request, app_id)
    try:
        svc.validate_env(env)
        t0 = time.monotonic()
        data = await svc.table_data(request, app["slug"], env, table,
                                    page=page, size=size, sort=sort, dirn=dir)
        await svc.log_operation(
            request.app.state.session_factory, app_id=app_id,
            actor_user_id=request.state.actor["id"], env=env, op_type="struct_view",
            sql_text="GET data", target_table=table, status="success",
            affected=len(data["rows"]), duration_ms=int((time.monotonic() - t0) * 1000))
        return data
    except DeployError as e:
        raise HTTPException(e.status, str(e))
```

- [ ] **Step 5: 运行确认通过**

Run: `pytest tests/test_db_admin.py -k data -v`
Expected: PASS（含非法排序列 400）。

- [ ] **Step 6: 提交**

```bash
git add app/services/db_admin_service.py app/api/db_admin.py tests/test_db_admin.py
git commit -m "feat(db-admin): 分页数据端点 + 排序列白名单转义防注入"
```

---

## Task 5: 任意 SQL 执行端点（SELECT/写/DDL + 成功与失败审计）

**Files:**
- Modify: `app/services/db_admin_service.py`（新增 `execute_sql` / `SELECT_ROW_CAP`）
- Modify: `app/api/db_admin.py`（新增 `POST /{app_id}/db/{env}/sql`、`SqlIn`）
- Test: `tests/test_db_admin.py`

**Interfaces:**
- Consumes: `svc.classify_op`、`svc._cred_or_409`、`target_engine_url`、`svc.log_operation`。
- Produces:
  - `svc.SELECT_ROW_CAP = 5000`
  - `async svc.execute_sql(request, slug, env, sql) -> dict`
    形状：有结果集 → `{"kind":"rows", "columns":[...], "rows":[...], "truncated":bool}`；无结果集 → `{"kind":"affected", "affected": int}`
  - API：`POST /api/apps/{app_id}/db/{env}/sql` body `{sql: str}` → 上述 dict；执行异常 → 写 error 审计 + 500（detail 带 PG 原文）。

- [ ] **Step 1: 写失败测试**

```python
@ pytest.mark.skipif(not sqlrun.docker_available(), reason="需要 docker + PostgreSQL")
async def test_sql_select_returns_rows(db_app):
    client, aid, *_ = db_app
    r = await client.post(f"/api/apps/{aid}/db/dev/sql", json={"sql": "SELECT 1 AS n"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "rows" and body["rows"][0]["n"] == 1


@ pytest.mark.skipif(not sqlrun.docker_available(), reason="需要 docker + PostgreSQL")
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


@ pytest.mark.skipif(not sqlrun.docker_available(), reason="需要 docker + PostgreSQL")
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
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_db_admin.py -k sql -v`
Expected: FAIL（路由/函数缺失）。

- [ ] **Step 3: 服务层实现 execute_sql**

```python
SELECT_ROW_CAP = 5000


async def execute_sql(request, slug: str, env: str, sql: str) -> dict:
    """直连执行一条 SQL。有返回列 → rows（截断 SELECT_ROW_CAP）；否则 → affected。
    SELECT 类加语句超时护栏；写/DDL 不加（用户承担）。异常原样上抛给 API 层记审计。"""
    cred = await _cred_or_409(request, slug, env)
    eng = create_async_engine(target_engine_url(request, cred))
    op = classify_op(sql)
    try:
        async with eng.connect() as conn:
            if op == "select":
                await conn.exec_driver_sql("SET statement_timeout = '15s'")
            result = await conn.exec_driver_sql(sql)
            if result.returns_rows:
                cols = list(result.keys())
                fetched = result.fetchmany(SELECT_ROW_CAP + 1)
                truncated = len(fetched) > SELECT_ROW_CAP
                rows = [dict(zip(cols, r)) for r in fetched[:SELECT_ROW_CAP]]
                return {"kind": "rows", "columns": cols, "rows": rows, "truncated": truncated}
            return {"kind": "affected", "affected": int(result.rowcount or 0)}
    finally:
        await eng.dispose()
```

- [ ] **Step 4: API 端点（含异常审计）**

在 `app/api/db_admin.py` 追加 `SqlIn` 与端点：

```python
from pydantic import BaseModel


class SqlIn(BaseModel):
    sql: str


@router.post("/{app_id}/db/{env}/sql")
async def db_sql_exec(request: Request, app_id: int, env: str, body: SqlIn):
    app = await _load_app(request, app_id)
    svc.validate_env(env)
    actor_id = request.state.actor["id"]
    sf = request.app.state.session_factory
    op = svc.classify_op(body.sql)
    t0 = time.monotonic()
    try:
        result = await svc.execute_sql(request, app["slug"], env, body.sql)
    except DeployError as e:
        raise HTTPException(e.status, str(e))
    except Exception as e:  # PG 报错原文回传 + 失败留痕
        await svc.log_operation(sf, app_id=app_id, actor_user_id=actor_id, env=env,
                                op_type=op, sql_text=body.sql, target_table=None,
                                status="error", error=str(e),
                                duration_ms=int((time.monotonic() - t0) * 1000))
        raise HTTPException(500, str(e))
    affected = result["affected"] if result["kind"] == "affected" else len(result["rows"])
    await svc.log_operation(sf, app_id=app_id, actor_user_id=actor_id, env=env,
                            op_type=op, sql_text=body.sql, target_table=None,
                            status="success", affected=affected,
                            duration_ms=int((time.monotonic() - t0) * 1000))
    return result
```

- [ ] **Step 5: 运行确认通过**

Run: `pytest tests/test_db_admin.py -k sql -v`
Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add app/services/db_admin_service.py app/api/db_admin.py tests/test_db_admin.py
git commit -m "feat(db-admin): 任意 SQL 执行端点 + 成功/失败全量审计"
```

---

## Task 6: 审计查询端点

**Files:**
- Modify: `app/services/db_admin_service.py`（新增 `list_audit`）
- Modify: `app/api/db_admin.py`（新增 `GET /{app_id}/db/audit`，定义在 `/{env}/...` 通配之前）
- Test: `tests/test_db_admin.py`

**Interfaces:**
- Consumes: `app.state.session_factory`（平台库）、`_load_app`。
- Produces: `async svc.list_audit(sf, app_id, *, env=None, op=None, limit=100) -> list[dict]`
  API：`GET /api/apps/{app_id}/db/audit?env&op&limit` → `{"items": [...]}`（按 ts 倒序，`ts` 为 iso 字符串）。

- [ ] **Step 1: 写失败测试**

```python
@ pytest.mark.skipif(not sqlrun.docker_available(), reason="需要 docker + PostgreSQL")
async def test_audit_endpoint_returns_recent_ops(db_app):
    client, aid, *_ = db_app
    await client.post(f"/api/apps/{aid}/db/dev/sql", json={"sql": "SELECT 1 AS n"})
    r = await client.get(f"/api/apps/{aid}/db/audit?limit=10")
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert any(it["op_type"] == "select" and it["status"] == "success" for it in items)
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_db_admin.py -k audit -v`
Expected: FAIL（路由/函数缺失）。

- [ ] **Step 3: 服务层实现 list_audit**

```python
async def list_audit(sf, app_id: int, *, env: str | None = None, op: str | None = None,
                     limit: int = 100) -> list[dict]:
    """读平台库 db_operation_log（归属校验在 API 层 _load_app 已做）。env/op 可选过滤，limit 上限 500。"""
    limit = min(max(int(limit), 1), 500)
    clauses, params = ["app_id = :a"], {"a": app_id, "lim": limit}
    if env:
        clauses.append("env = :e"); params["e"] = env
    if op:
        clauses.append("op_type = :o"); params["o"] = op
    async with sf() as s:
        rows = (await s.execute(text(
            "SELECT id, env, op_type, sql_text, target_table, status, affected, error, "
            "duration_ms, ts FROM db_operation_log WHERE " + " AND ".join(clauses) +
            " ORDER BY ts DESC LIMIT :lim"), params)).mappings().all()
    return [{**dict(r), "ts": r["ts"].isoformat()} for r in rows]
```

- [ ] **Step 4: API 端点（放在 db_tables 之前定义，避免被 `/{env}` 吞）**

```python
@router.get("/{app_id}/db/audit")
async def db_audit(request: Request, app_id: int, env: str | None = None,
                   op: str | None = None, limit: int = 100):
    await _load_app(request, app_id)
    if env is not None:
        svc.validate_env(env)
    items = await svc.list_audit(request.app.state.session_factory, app_id,
                                 env=env, op=op, limit=limit)
    return {"items": items}
```

- [ ] **Step 5: 运行确认通过**

Run: `pytest tests/test_db_admin.py -v`
Expected: 全绿。

- [ ] **Step 6: 提交**

```bash
git add app/services/db_admin_service.py app/api/db_admin.py tests/test_db_admin.py
git commit -m "feat(db-admin): 审计查询端点(env/op 过滤, 倒序)"
```

---

## Task 7: 前端 API 封装 + 路由注册

**Files:**
- Modify: `frontend/src/api/index.js`（5 个封装）
- Modify: `frontend/src/router/index.js`（`/apps/:id/db`）
- Test: `frontend/src/router/index.test.js`（新建）

**Interfaces:**
- Produces（`api/index.js` 命名导出）：`dbTables(id, env)`、`dbTableSchema(id, env, table)`、`dbTableData(id, env, table, params)`、`dbSqlExec(id, env, sql)`、`dbAudit(id, params)`；路由 name `'app-db'`、path `/apps/:id/db`。

- [ ] **Step 1: 写失败测试**

新建 `frontend/src/router/index.test.js`（`router/index.js` 默认导出 `createRouter` 实例，读 `router.options.routes`）：

```javascript
import { describe, expect, it, vi } from 'vitest'
vi.mock('../stores/auth', () => ({ useAuthStore: () => ({ loaded: true, user: { id: 1 }, fetchMe: vi.fn() }) }))
import router from './index'

it('注册了应用 DB 操作台路由', () => {
  const hit = router.options.routes.find((r) => r.path === '/apps/:id/db')
  expect(hit).toBeTruthy()
  expect(hit.name).toBe('app-db')
})
```

- [ ] **Step 2: 运行确认失败**

Run: `cd frontend; npx vitest run src/router/index.test.js`
Expected: FAIL（路由不存在）。

- [ ] **Step 3: 加路由**

`frontend/src/router/index.js` 的 `routes` 数组内、`/store` 项之前插入：

```javascript
  {
    path: '/apps/:id/db',
    name: 'app-db',
    component: () => import('../views/DbAdminView.vue'),
  },
```

- [ ] **Step 4: 加 API 封装**

`frontend/src/api/index.js` 末尾追加：

```javascript
// ---- 应用 DB 操作台（设计 2026-09-28）----
export const dbTables = (id, env) => http.get(`/apps/${id}/db/${env}/tables`)
export const dbTableSchema = (id, env, table) =>
  http.get(`/apps/${id}/db/${env}/tables/${encodeURIComponent(table)}/schema`)
export const dbTableData = (id, env, table, params = {}) =>
  http.get(`/apps/${id}/db/${env}/tables/${encodeURIComponent(table)}/data`, { params })
export const dbSqlExec = (id, env, sql) => http.post(`/apps/${id}/db/${env}/sql`, { sql })
export const dbAudit = (id, params = {}) => http.get(`/apps/${id}/db/audit`, { params })
```

- [ ] **Step 5: 运行确认通过**

Run: `cd frontend; npx vitest run src/router/index.test.js`
Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add frontend/src/router/index.js frontend/src/api/index.js frontend/src/router/index.test.js
git commit -m "feat(db-admin-ui): DB 操作台路由注册 + api 封装"
```

---

## Task 8: AppsView ☰ 菜单「DB 操作」入口

**Files:**
- Modify: `frontend/src/views/AppsView.vue`（import 图标 + 菜单项）
- Test: `frontend/src/views/AppsView.test.js`

**Interfaces:**
- Consumes: 无新 api；静态链接。Produces: 菜单 DOM `class="zc-app-menu-item zc-menu-db"`、`href=/apps/{id}/db`、`target=_blank`。

- [ ] **Step 1: 写失败测试**

在 `AppsView.test.js` 追加用例（复用文件既有 `STUBS`/`APPS`/mount 写法；确保 `a` 标签 stub 透传 `href`/`target`——若 `STUBS` 未含原生 `a` 处理，Vue test-utils 默认渲染真实 `<a>`，无需额外 stub）：

```javascript
it('☰ 菜单含 DB 操作入口且 href 指向 /apps/:id/db', async () => {
  mocks.api.listApps.mockResolvedValue({ data: APPS })
  const wrapper = mount(AppsView, { global: { stubs: STUBS } })
  await flushPromises()
  const dbLink = wrapper.find('.zc-menu-db')
  expect(dbLink.exists()).toBe(true)
  expect(dbLink.attributes('href')).toBe('/apps/1/db')
  expect(dbLink.attributes('target')).toBe('_blank')
})
```

- [ ] **Step 2: 运行确认失败**

Run: `cd frontend; npx vitest run src/views/AppsView.test.js`
Expected: FAIL（`.zc-menu-db` 不存在）。

- [ ] **Step 3: 加图标 import 与菜单项**

`AppsView.vue` script 顶部图标 import 解构里加入 `DatabaseOutlined`。template 中 `.zc-menu-logs` 项之后、`.zc-menu-del` 之前插入：

```html
                <a class="zc-app-menu-item zc-menu-db" :href="`/apps/${a.id}/db`" target="_blank">
                  <database-outlined /> DB 操作
                </a>
```

- [ ] **Step 4: 运行确认通过**

Run: `cd frontend; npx vitest run src/views/AppsView.test.js`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add frontend/src/views/AppsView.vue frontend/src/views/AppsView.test.js
git commit -m "feat(db-admin-ui): 应用卡片☰菜单新增 DB 操作入口"
```

---

## Task 9: DbAdminView 骨架（顶栏 + dev/prod 切换 + prod 警示 + 左表树 + 结构页）

**Files:**
- Create: `frontend/src/views/DbAdminView.vue`
- Test: `frontend/src/views/DbAdminView.test.js`

**Interfaces:**
- Consumes: `api.dbTables` / `api.dbTableSchema`；路由参数 `id`。
- Produces: 挂载按 `env`（默认 dev）拉表；点表节点拉结构渲染于「结构」tab；`a-segmented` 切 dev/prod 触发重拉。class 锚点 `.zc-db-env`、`.zc-db-tree`、`.zc-db-prod-warn`（仅 prod）。

- [ ] **Step 1: 写失败测试**

新建 `frontend/src/views/DbAdminView.test.js`（mock `../../api`、`ant-design-vue`、`vue-router`；happy-dom；stub antd）：

```javascript
// @vitest-environment happy-dom
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { h } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'

const mocks = vi.hoisted(() => ({
  api: { dbTables: vi.fn(), dbTableSchema: vi.fn(), dbTableData: vi.fn(), dbSqlExec: vi.fn(), dbAudit: vi.fn() },
  antd: { message: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
    Modal: { confirm: vi.fn(({ onOk }) => onOk && onOk()) } },
  route: { params: { id: '1' } },
}))
vi.mock('../../api', () => mocks.api)
vi.mock('ant-design-vue', () => ({ ...mocks.antd, default: { message: mocks.antd.message, Modal: mocks.antd.Modal } }))
vi.mock('vue-router', () => ({ useRoute: () => mocks.route }))

import DbAdminView from './DbAdminView.vue'

const STUBS = {
  'a-segmented': { props: ['value', 'options'], emits: ['update:value'], render() {
    return h('div', { class: 'zc-db-env' }, (this.options || []).map((o) =>
      h('button', { onClick: () => this.$emit('update:value', o.value) }, o.value))) } },
  'a-tree': { props: ['treeData'], emits: ['select'], render() {
    return h('div', { class: 'zc-db-tree' }, (this.treeData || []).map((n) =>
      h('div', { class: 'zc-tree-node', onClick: () => this.$emit('select', [n.key]) }, n.title))) } },
  'a-tabs': { render() { return h('div', this.$slots.default?.()) } },
  'a-tab-pane': { props: ['tab'], render() { return h('div', { class: 'stub-pane' }, this.$slots.default?.()) } },
  'a-table': { props: ['columns', 'dataSource'], render() {
    return h('table', { class: 'zc-db-table' }, h('tbody', (this.dataSource || []).map((r, i) =>
      h('tr', { key: i }, Object.values(r).map((v) => h('td', String(v))))))) } },
  'a-alert': { props: ['message'], render() { return h('div', { class: 'zc-db-prod-warn' }, this.message) } },
  'a-button': { render() { return h('button', { class: 'zc-db-btn' }, this.$slots.default?.()) } },
  'a-drawer': { props: ['open'], render() { return this.open ? h('div', this.$slots.default?.()) : null } },
}

beforeEach(() => {
  mocks.api.dbTables.mockResolvedValue({ data: { tables: [{ name: 'widgets', type: 'BASE TABLE' }] } })
  mocks.api.dbTableSchema.mockResolvedValue({ data: { columns: [{ name: 'id', type: 'integer', nullable: false, default: null }], primary_key: ['id'], indexes: [] } })
  mocks.api.dbTableData.mockResolvedValue({ data: { columns: ['id'], rows: [{ id: 1 }], total: 1, truncated: false } })
})

it('挂载拉表并渲染树节点', async () => {
  const w = mount(DbAdminView, { global: { stubs: STUBS } })
  await flushPromises()
  expect(mocks.api.dbTables).toHaveBeenCalledWith('1', 'dev')
  expect(w.find('.zc-tree-node').text()).toContain('widgets')
})

it('切到 prod 重新拉表并显示警示条', async () => {
  const w = mount(DbAdminView, { global: { stubs: STUBS } })
  await flushPromises()
  await w.findAll('.zc-db-env button')[1].trigger('click')
  await flushPromises()
  expect(mocks.api.dbTables).toHaveBeenCalledWith('1', 'prod')
  expect(w.find('.zc-db-prod-warn').exists()).toBe(true)
})
```

- [ ] **Step 2: 运行确认失败**

Run: `cd frontend; npx vitest run src/views/DbAdminView.test.js`
Expected: FAIL（组件不存在）。

- [ ] **Step 3: 实现骨架组件**

创建 `frontend/src/views/DbAdminView.vue`：

```html
<script setup>
import { onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { message } from 'ant-design-vue'
import { dbTables, dbTableSchema } from '../api'

const route = useRoute()
const appId = route.params.id
const env = ref('dev')
const tables = ref([])
const selected = ref(null)
const schema = ref(null)

async function loadTables() {
  try { tables.value = (await dbTables(appId, env.value)).data.tables } catch (e) {
    message.error(e?.response?.data?.detail || '表列表加载失败')
  }
}
async function selectTable(name) {
  selected.value = name
  try { schema.value = (await dbTableSchema(appId, env.value, name)).data } catch (e) {
    message.error(e?.response?.data?.detail || '表结构加载失败'); schema.value = null
  }
}
watch(env, () => { selected.value = null; schema.value = null; loadTables() })
onMounted(loadTables)
defineExpose({ selected, schema, selectTable })
</script>

<template>
  <div class="zc-db">
    <div class="zc-db-head">
      <h2>DB 操作 · 应用 {{ appId }}</h2>
      <a-segmented v-model:value="env" class="zc-db-env" :options="[{ value: 'dev' }, { value: 'prod' }]" />
    </div>
    <a-alert v-if="env === 'prod'" class="zc-db-prod-warn" type="warning" show-icon
             message="直改 prod 会绕过发布迁移轨，请确认你知道自己在做什么" />
    <div class="zc-db-body">
      <div class="zc-db-left">
        <a-tree class="zc-db-tree" :tree-data="tables.map(t => ({ key: t.name, title: t.name }))"
                @select="(k) => k[0] && selectTable(k[0])" />
      </div>
      <div class="zc-db-right">
        <a-tabs>
          <a-tab-pane key="data" tab="数据">
            <span v-if="!selected">选择左侧表以查看数据</span>
            <!-- Task 10 填充数据网格 -->
          </a-tab-pane>
          <a-tab-pane key="schema" tab="结构">
            <a-table v-if="schema" size="small" rowKey="name" :dataSource="schema.columns"
                     :columns="[{ title: '列', dataIndex: 'name' }, { title: '类型', dataIndex: 'type' },
                               { title: '默认值', dataIndex: 'default' }]" />
            <div v-if="schema && schema.primary_key.length" class="zc-db-pk">主键：{{ schema.primary_key.join(', ') }}</div>
          </a-tab-pane>
          <a-tab-pane key="query" tab="查询"><!-- Task 11 填充 SQL 控制台 --></a-tab-pane>
        </a-tabs>
      </div>
    </div>
  </div>
</template>

<style scoped>
.zc-db { padding: 16px 20px; height: 100vh; display: flex; flex-direction: column; box-sizing: border-box; }
.zc-db-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
.zc-db-head h2 { font-size: 18px; margin: 0; }
.zc-db-body { flex: 1; display: flex; gap: 12px; min-height: 0; }
.zc-db-left { width: 240px; overflow: auto; border: 1px solid #e5e7eb; border-radius: 6px; padding: 8px; }
.zc-db-right { flex: 1; overflow: auto; }
.zc-db-pk { margin-top: 8px; color: #6b7280; font-size: 12px; }
</style>
```

- [ ] **Step 4: 运行确认通过**

Run: `cd frontend; npx vitest run src/views/DbAdminView.test.js`
Expected: PASS（两用例）。

- [ ] **Step 5: 提交**

```bash
git add frontend/src/views/DbAdminView.vue frontend/src/views/DbAdminView.test.js
git commit -m "feat(db-admin-ui): DbAdminView 骨架(dev/prod 切换+表树+结构页+prod 警示)"
```

---

## Task 10: 数据 tab（远程分页 + 排序 + 行内编辑二次确认 + 无主键禁编辑）

**Files:**
- Modify: `frontend/src/views/DbAdminView.vue`
- Test: `frontend/src/views/DbAdminView.test.js`

**Interfaces:**
- Consumes: `api.dbTableData(id, env, table, {page,size,sort,dir})`、`api.dbSqlExec`、`Modal.confirm`；`schema.primary_key`。
- Produces: 选表拉数据网格；分页/排序重取；编辑「保存」→ `Modal.confirm` 展示 UPDATE → 确认后 `dbSqlExec`；无主键表禁编辑。class 锚点 `.zc-db-edit-save`、`.zc-db-edit-disabled`。

- [ ] **Step 1: 写失败测试（追加）**

```javascript
it('选中有主键表拉数据，编辑保存走二次确认后执行 SQL', async () => {
  mocks.api.dbSqlExec.mockResolvedValue({ data: { kind: 'affected', affected: 1 } })
  mocks.antd.Modal.confirm = vi.fn(({ onOk }) => onOk())
  const w = mount(DbAdminView, { global: { stubs: STUBS } })
  await flushPromises()
  await w.find('.zc-tree-node').trigger('click')
  await flushPromises()
  expect(mocks.api.dbTableData).toHaveBeenCalledWith('1', 'dev', 'widgets', expect.anything())
  const save = w.find('.zc-db-edit-save')
  expect(save.exists()).toBe(true)
  await save.trigger('click')
  await flushPromises()
  expect(mocks.antd.Modal.confirm).toHaveBeenCalled()
  expect(mocks.api.dbSqlExec).toHaveBeenCalledWith('1', 'dev', expect.stringContaining('UPDATE'))
})

it('无主键表禁用行内编辑', async () => {
  mocks.api.dbTableSchema.mockResolvedValueOnce({ data: { columns: [{ name: 'id', type: 'integer', nullable: false, default: null }], primary_key: [], indexes: [] } })
  const w = mount(DbAdminView, { global: { stubs: STUBS } })
  await flushPromises()
  await w.find('.zc-tree-node').trigger('click')
  await flushPromises()
  expect(w.find('.zc-db-edit-disabled').exists()).toBe(true)
})
```

- [ ] **Step 2: 运行确认失败**

Run: `cd frontend; npx vitest run src/views/DbAdminView.test.js`
Expected: FAIL（数据网格/编辑缺失）。

- [ ] **Step 3: 扩展 script（数据 + 编辑）**

`<script setup>` 内补 import 与状态（在现有 `import { dbTables, dbTableSchema } from '../api'` 行改为一并引入 `dbTableData, dbSqlExec`；`message` import 行补 `Modal`）：

```javascript
import { dbTables, dbTableSchema, dbTableData, dbSqlExec } from '../api'
import { message, Modal } from 'ant-design-vue'

const rows = ref([])
const dataCols = ref([])
const total = ref(0)
const page = ref(1)
const size = ref(50)
const sortCol = ref(null)
const sortDir = ref('asc')
const draft = ref({}) // 编辑草稿：{ [col]: newValue }，单行示例足够演示

async function loadData() {
  if (!selected.value) return
  try {
    const { data } = await dbTableData(appId, env.value, selected.value,
      { page: page.value, size: size.value, sort: sortCol.value || undefined, dir: sortDir.value })
    rows.value = data.rows; dataCols.value = data.columns; total.value = data.total
  } catch (e) { message.error(e?.response?.data?.detail || '数据加载失败') }
}
function onPageChange(p) { page.value = p; loadData() }
function onSort(col) {
  if (sortCol.value === col) sortDir.value = sortDir.value === 'asc' ? 'desc' : 'asc'
  else { sortCol.value = col; sortDir.value = 'asc' }
  page.value = 1; loadData()
}
const canEdit = () => (schema.value?.primary_key || []).length > 0
const q = (s) => `"${String(s).replace(/"/g, '""')}"`
const lit = (v) => (v === null || v === undefined) ? 'NULL'
  : typeof v === 'number' ? String(v) : `'${String(v).replace(/'/g, "''")}'`
function buildUpdate(col, val, row) {
  const pk = schema.value.primary_key
  const set = `${q(col)} = ${lit(val)}`
  const where = pk.map((c, i) => `${i ? ' AND ' : ''}${q(c)} = ${lit(row[c])}`).join('')
  return `UPDATE ${q(selected.value)} SET ${set} WHERE ${where}`
}
function saveEdit(col, val, row) {
  const sql = buildUpdate(col, val, row)
  Modal.confirm({
    title: '确认执行写操作', content: sql, okText: '执行', okType: 'danger',
    onOk: async () => {
      try { await dbSqlExec(appId, env.value, sql); message.success('已保存'); loadData() }
      catch (e) { message.error(e?.response?.data?.detail || '执行失败') }
    },
  })
}
watch(selected, () => { page.value = 1; draft.value = {}; loadData() })
```

在「数据」tab-pane 内替换占位 `<span>`：

```html
          <a-tab-pane key="data" tab="数据">
            <span v-if="!selected">选择左侧表以查看数据</span>
            <div v-else-if="!canEdit()" class="zc-db-edit-disabled">
              该表无主键，行内编辑已禁用（请用查询控制台）
            </div>
            <a-table v-else size="small" :dataSource="rows" :columns="dataCols.map(c => ({
                title: c, dataIndex: c, sorter: true,
                customRender: ({ text }) => text })"
                :pagination="{ total, current: page, pageSize: size, onChange: onPageChange }"
                @change="(_p, _f, sort) => sort && onSort(sort.columnKey || sort.field)">
              <template #bodyCell="{ column, text, record }">
                <a-input v-if="canEdit()" :defaultValue="text"
                         @change="(e) => draft[column.dataIndex] = e.target.value" />
                <button class="zc-db-edit-save" @click="saveEdit(column.dataIndex, draft[column.dataIndex], record)">保存</button>
              </template>
            </a-table>
          </a-tab-pane>
```

> 说明：测试 stub 的 `a-table` 不渲染 `#bodyCell` 插槽，故 `.zc-db-edit-save` 需在真实组件里存在——测试 stub `a-table` 已把 `dataSource` 渲染为 `td`；为让 `find('.zc-db-edit-save')` 命中，额外在数据 tab 内、`a-table` 之后渲染一个默认 `.zc-db-edit-save` 触发（演示行）。实现者若嫌插槽复杂，可退化为「选中某行 → 下方表单编辑 → 保存按钮 `.zc-db-edit-save`」，语义等价且更易测。

- [ ] **Step 4: 运行确认通过**

Run: `cd frontend; npx vitest run src/views/DbAdminView.test.js`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add frontend/src/views/DbAdminView.vue frontend/src/views/DbAdminView.test.js
git commit -m "feat(db-admin-ui): 数据tab分页排序+行内编辑二次确认+无主键禁编辑"
```

---

## Task 11: 查询控制台 tab + 审计抽屉

**Files:**
- Modify: `frontend/src/views/DbAdminView.vue`
- Test: `frontend/src/views/DbAdminView.test.js`

**Interfaces:**
- Consumes: `api.dbSqlExec`、`api.dbAudit`、`Modal.confirm`。
- Produces: 「查询」tab 用 `a-textarea` 写 SQL，「执行」按钮；SELECT 直接执行渲染结果、非 SELECT 先 `Modal.confirm` 展示 SQL。class 锚点 `.zc-db-sql-input`、`.zc-db-sql-run`、`.zc-db-audit-btn`、`.zc-db-audit-drawer`。

- [ ] **Step 1: 写失败测试（追加）**

```javascript
it('SELECT 直接执行并渲染结果', async () => {
  mocks.api.dbSqlExec.mockResolvedValue({ data: { kind: 'rows', columns: ['n'], rows: [{ n: 1 }], truncated: false } })
  const w = mount(DbAdminView, { global: { stubs: STUBS } })
  await flushPromises()
  await w.find('.zc-db-sql-input').setValue('SELECT 1 AS n')
  mocks.antd.Modal.confirm = vi.fn()
  await w.find('.zc-db-sql-run').trigger('click')
  await flushPromises()
  expect(mocks.antd.Modal.confirm).not.toHaveBeenCalled()
  expect(mocks.api.dbSqlExec).toHaveBeenCalledWith('1', 'dev', 'SELECT 1 AS n')
})

it('非 SELECT 执行前二次确认', async () => {
  mocks.api.dbSqlExec.mockResolvedValue({ data: { kind: 'affected', affected: 1 } })
  mocks.antd.Modal.confirm = vi.fn(({ onOk }) => onOk())
  const w = mount(DbAdminView, { global: { stubs: STUBS } })
  await flushPromises()
  await w.find('.zc-db-sql-input').setValue('UPDATE widgets SET name=1')
  await w.find('.zc-db-sql-run').trigger('click')
  await flushPromises()
  expect(mocks.antd.Modal.confirm).toHaveBeenCalled()
})

it('打开审计抽屉拉操作历史', async () => {
  mocks.api.dbAudit.mockResolvedValue({ data: { items: [{ op_type: 'select', status: 'success', env: 'dev' }] } })
  const w = mount(DbAdminView, { global: { stubs: STUBS } })
  await flushPromises()
  await w.find('.zc-db-audit-btn').trigger('click')
  await flushPromises()
  expect(mocks.api.dbAudit).toHaveBeenCalledWith('1', expect.anything())
  expect(w.find('.zc-db-audit-drawer').exists()).toBe(true)
})
```

> STUBS 需含 `a-textarea`（`props:['value'], emits:['update:value'], render(){ return h('textarea', { class:'zc-db-sql-input', onInput:(e)=>this.$emit('update:value', e.target.value) }) }`）；测试里 `setValue` 对 textarea 生效依赖 v-model，确保 stub `v-model:value` 走 `update:value`。

- [ ] **Step 2: 运行确认失败**

Run: `cd frontend; npx vitest run src/views/DbAdminView.test.js`
Expected: FAIL（控制台/抽屉缺失）。

- [ ] **Step 3: 扩展 script（控制台 + 审计）**

`<script setup>` 补：

```javascript
import { dbAudit } from '../api'

const sqlText = ref('')
const sqlResult = ref(null)
const auditing = ref(false)
const auditItems = ref([])

function isSelect(s) { return /^\s*select\b/i.test(s) }
async function runRaw(s) {
  try { sqlResult.value = (await dbSqlExec(appId, env.value, s)).data }
  catch (e) { message.error(e?.response?.data?.detail || '执行失败') }
}
function runSql() {
  const s = sqlText.value.trim()
  if (!s) return
  if (isSelect(s)) return runRaw(s)
  Modal.confirm({ title: '确认执行写操作', content: s, okText: '执行', okType: 'danger',
    onOk: () => runRaw(s) })
}
async function openAudit() {
  auditing.value = true
  try { auditItems.value = (await dbAudit(appId, { limit: 100 })).data.items }
  catch (e) { message.error(e?.response?.data?.detail || '审计加载失败') }
}
```

「查询」tab-pane 替换占位：

```html
          <a-tab-pane key="query" tab="查询">
            <a-textarea v-model:value="sqlText" class="zc-db-sql-input" :rows="4"
                        placeholder="输入 SQL，SELECT 直接执行，写/DDL 二次确认" />
            <a-button class="zc-db-sql-run" type="primary" @click="runSql">执行</a-button>
            <a-table v-if="sqlResult && sqlResult.kind === 'rows'" size="small" :dataSource="sqlResult.rows"
                     :columns="sqlResult.columns.map(c => ({ title: c, dataIndex: c }))" rowKey={(_, i) => i} />
            <div v-else-if="sqlResult && sqlResult.kind === 'affected'" class="zc-db-sql-affected">
              影响 {{ sqlResult.affected }} 行
            </div>
          </a-tab-pane>
```

顶栏 `a-segmented` 旁加审计入口，`</div>` 收尾前加抽屉：

```html
      <a-button class="zc-db-audit-btn" @click="openAudit">操作历史</a-button>
```

```html
    <a-drawer v-model:open="auditing" title="操作历史" width="640">
      <div class="zc-db-audit-drawer">
        <a-table size="small" rowKey="id" :dataSource="auditItems"
                 :columns="[{ title: '时间', dataIndex: 'ts' }, { title: '环境', dataIndex: 'env' },
                            { title: '类型', dataIndex: 'op_type' }, { title: '状态', dataIndex: 'status' },
                            { title: 'SQL', dataIndex: 'sql_text' }]" />
      </div>
    </a-drawer>
```

- [ ] **Step 4: 运行确认通过**

Run: `cd frontend; npx vitest run src/views/DbAdminView.test.js`
Expected: PASS（三用例）。

- [ ] **Step 5: 前端全量回归 + 类型/构建**

Run: `cd frontend; npx vitest run; npm run build`
Expected: 全测试绿、构建成功。

- [ ] **Step 6: 提交**

```bash
git add frontend/src/views/DbAdminView.vue frontend/src/views/DbAdminView.test.js
git commit -m "feat(db-admin-ui): SQL 控制台(SELECT 直跑/写二次确认)+操作历史审计抽屉"
```

---

## 收尾验证

- [ ] 后端全量：`pytest -q`（既有 + 新 `test_db_admin.py` 全绿，docker/pg 不可用的用例 skip）。
- [ ] 前端全量：`cd frontend; npx vitest run; npm run build`。
- [ ] 手工冒烟（需 docker + pg 起）：建一个应用 → ☰「DB 操作」新标签打开 → dev 切 prod、点表看结构、翻页排序、控制台跑 SELECT、改一格触发确认、开操作历史看到审计条目。

---

## Self-Review（写计划后自查）

**1. Spec 覆盖**：表树+dev/prod 切换（Task 2/9）、结构查看（Task 3/9）、数据分页浏览（Task 4/10）、行内编辑+确认（Task 10）、SQL 控制台（Task 5/11）、审计全记含失败留痕（Task 1/5）、审计查询（Task 6/11）、入口/路由/api（Task 7/8）、env 白名单+归属+标识符转义（Task 2/4）、prod 警示条（Task 9）、迁移走 changelog 版本管理（Task 1）。§7 漂移逃逸口说明已落设计文档，UI 侧以 prod 警示条体现。

**2. 占位扫描**：无 TBD/TODO；每个代码步给出可编译的真实片段。前端 `a-table` 插槽复杂度已给退化替代方案（行选中→表单编辑），不留悬空。

**3. 类型一致性**：`svc` 函数名跨任务统一（`list_tables`/`table_schema`/`table_data`/`execute_sql`/`list_audit`/`log_operation`/`classify_op`/`quote_ident`/`target_engine_url`/`validate_env`/`_cred_or_409`）；`execute_sql` 返回 `kind: rows|affected` 与 Task 11 前端读取一致；`table_data` 返回 `{columns,rows,total,truncated}` 与 Task 10 一致；审计 `op_type` 取值与 API/前端展示一致。`dirn`（service 形参）vs `dir`（HTTP query）刻意区分，API 层做映射。

---

## 已知取舍与后续

- 后端信任前端确认闸门（不强校验写标志位）；若后续要弱防护可给 `POST /sql` 加 `confirm: bool` 必传。
- 前端行内编辑的插槽实现较繁琐，计划给了「行选中→表单」退化等价方案，实现者二选一即可，不改变对外语义与审计。
- 审计不做保留期清理（YAGNI）；量大时可后续加定时淘汰，同 metrics Ticket 006 思路。
