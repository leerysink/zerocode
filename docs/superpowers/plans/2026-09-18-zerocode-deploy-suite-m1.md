# zerocode 部署发布套件 M1 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让平台能"新建应用 → 脚手架铺底 → 自动供给 dev 数据库 → 一键启动 dev 预览环境（含 sqlchange 自动迁移）"，并在界面可见可用（M1 = 发布/URL/回滚之前的完整开发侧闭环）。

**Architecture:** 新增 `app/deploy/` 后端模块（slug/端口分配/PG 供给/模板渲染/compose 渲染/dev 生命周期/sqlrunner），复用 v1 的 workspace/迁移执行器/工具体系；每个应用 = 含 `app.json` 的 workspace，dev 环境是平台按需渲染并 `docker compose up` 的三服务栈（migrate/backend/frontend），数据库复用 `zerocode-pg16` 同实例分库、经外部网络 `zcnet` 访问。

**Tech Stack:** FastAPI + SQLAlchemy2(asyncpg) + Docker CLI（async subprocess）+ docker compose v2 + Vue3/ant-design-vue（最小 UI）。

**Spec:** `docs/superpowers/specs/2026-09-18-zerocode-deploy-suite/`（00 总览、01 应用模型、02 技术栈模板、03 Dev 预览、05 §4.1 端口、06 数据库供给、07 sqlchange；M2/M3 范围——发布流水线/URL 引导/回滚/应用页完整版——不在本计划）

## Global Constraints

- 平台端口：后端 `8100`（127.0.0.1），PG 容器 `zerocode-pg16`（宿主 5433，容器网络内 5432）。
- 应用端口：`deploy.port_base=8210` ~ `deploy.port_max=8399`，**成对分配：prod=偶、dev=奇**（8210/8211 起）。
- slug 规则：`^[a-z][a-z0-9]{1,39}$`；库名/角色名 = `<slug>_dev` / `<slug>_prod`；密码 `secrets.token_urlsafe(18)`。
- 平台自身 DDL/DML 一律 `changelog/V<YYYYMMDD>_<两位序号>_<说明>.sql`；**迁移 SQL 注释中不得含 ASCII 分号 `;`**（执行器按 `;` 切分并剥离 `--` 行）；已有 `V20260917_01_init.sql`、`V20260917_02_interaction.sql`，本次从 `V20260918_01` 起。
- 应用侧迁移：应用根 `sqlchange/V<YYYYMMDD>_<两位序号>_<说明>.sql`，由镜像 `zerocode-sqlrunner:1`（psql 文件级事务）增量执行，历史表 `sqlchange_history`；平台 changelog/ 与应用 sqlchange/ 两套体系互不扫描。
- 数据库密码只存 `app_config` 的 `dbcred.*` 键与 `deploy/.env`；`/api/config` 与 agent 配置流**必须排除** `dbcred.*`；app.json 不含密码。
- AI 文件工具与 UI 保存对 `app.json` 与 `deploy/**` 写保护（平台唯一写入方）。
- compose 项目名：dev = `zc-<slug>-dev`（yaml 顶层 `name:`）；外部网络 `zcnet`（`docker network connect zcnet zerocode-pg16` 幂等接入）。
- 写文件统一 `newline=""` 字节保真（v1 踩坑：Windows LF→CRLF 翻转）。
- `CREATE DATABASE`/`CREATE ROLE` 用 `isolation_level="AUTOCOMMIT"`（PG 禁止事务内建库）；URL 转 str 用 `render_as_string(hide_password=False)`（SQLAlchemy 会掩码密码）。
- 后端包 `app/`，测试 `tests/`（pytest + pytest-asyncio，`asyncio_mode=auto`），测试库 `zerocode_test`（`ZEROCODE_TEST_DATABASE_URL`），测试间 TRUNCATE；前端 `frontend/` 用 npm。
- Docker 依赖测试（sqlrunner/compose/引擎集成）用 `pytest.mark.skipif(not docker_available(), ...)` 守卫（Docker Desktop 可能未启动）。
- 中文注释与文档；每完成一个任务 git commit；Windows `.bat` 一律 LF→CRLF 字节转换（本计划无 .bat）。
- 模板占位符 `{{KEY}}`，渲染后自检不得残留 `{{`；compose 渲染用 `__KEY__` 标记（避免与 compose 的 `${VAR}` 插值冲突）。

## File Structure（新增/修改全景）

```
changelog/V20260918_01_deploy_suite.sql        # 平台表 app_project + deploy.* 配置种子（Task 1）
app/deploy/__init__.py                          # 空包（Task 2）
app/deploy/errors.py                            # DeployError（Task 2）
app/deploy/slugs.py                             # slug 校验/建议（Task 2）
app/deploy/settings.py                          # deploy.* 配置读取 + apps_root 解析（Task 2）
app/deploy/ports.py                             # 端口成对分配器（Task 2）
app/deploy/provision.py                         # PG 分库供给/凭据/zcnet/删库（Task 3）
app/templates/vue3-fastapi/**                   # 脚手架模板（Task 4，11 个文件）
app/deploy/scaffold.py                          # 模板复制 + {{KEY}} 渲染 + 残留自检（Task 4）
app/services/app_service.py                     # 应用原子创建/回滚/列表/详情（Task 5）
app/api/apps.py                                 # /api/apps* REST（Task 6 创建，Task 12 扩 dev/sqlchange 端点）
app/deploy/guard.py                             # app.json 与 deploy/** 写保护判定（Task 7）
app/deploy/contract.py                          # 应用契约文本生成（Task 8）
app/agent/prompts.py  app/agent/loop.py         # 契约注入（Task 8 修改）
app/deploy/sqlrunner/{Dockerfile,run.sh}        # sqlrunner 镜像（Task 9）
app/deploy/sqlrun.py                            # 镜像构建/一次性执行封装（Task 9）
app/deploy/compose.py                           # dev-compose 渲染 + deploy/ 落盘（Task 10）
app/deploy/hub.py                               # DeployHub（app_id → 事件队列）（Task 11）
app/deploy/engine.py                            # dev 生命周期/孤儿清理/退出钩子（Task 11）
app/api/deploy_ws.py                            # WS /ws/deploy/{app_id}（Task 12）
app/main.py                                     # 挂路由 + lifespan 集成（Task 6/12 修改）
app/services/config_service.py                  # get_all 排除 dbcred.*（Task 3 修改）
app/agent/tools.py  app/api/files.py            # 写保护接入（Task 7 修改）
tests/test_deploy_migrations.py                 # Task 1
tests/test_deploy_basics.py                     # Task 2（slugs/ports/settings）
tests/test_provision.py                         # Task 3
tests/test_scaffold.py                          # Task 4
tests/test_app_service.py                       # Task 5
tests/test_apps_api.py                          # Task 6
tests/test_guard.py                             # Task 7
tests/test_contract.py                          # Task 8
tests/test_sqlrunner.py                         # Task 9（docker 守卫）
tests/test_compose_render.py                    # Task 10
tests/test_engine.py                            # Task 11（FakeRunner 单测 + docker 集成）
tests/test_apps_dev_api.py                      # Task 12
frontend/src/api/index.js                       # 追加 apps API（Task 13 修改）
frontend/src/views/AppsView.vue                 # 应用页（Task 13）
frontend/src/router/index.js  SideBar.vue       # 路由/入口（Task 13 修改）
README.md                                       # M1 使用说明（Task 14 修改）
```

---

### Task 1: 平台表 app_project 与 deploy.* 配置种子

**Files:**
- Create: `changelog/V20260918_01_deploy_suite.sql`
- Test: `tests/test_deploy_migrations.py`

**Interfaces:**
- Produces: 表 `app_project(id, workspace_id, name, slug, scale, stack, prod_port, dev_port, status_dev, status_prod, current_version, created_at, updated_at)`；`app_config` 键 `deploy.apps_root`(=`""`)、`deploy.port_base`(=8210)、`deploy.port_max`(=8399)、`deploy.keep_versions`(=5)、`deploy.health_timeout`(=60)、`deploy.auto_stop_dev_on_exit`(=true)。后续所有任务经 SQL 访问。

- [ ] **Step 1: 写迁移 SQL**（注意：注释不得含 `;`）

```sql
-- changelog/V20260918_01_deploy_suite.sql
-- 日期：2026-09-18  用途：部署发布套件 M1 —— 应用工程表与部署配置默认值  影响：app_project, app_config

CREATE TABLE IF NOT EXISTS app_project (
    id              BIGSERIAL PRIMARY KEY,
    workspace_id    BIGINT NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
    name            VARCHAR(100) NOT NULL,
    slug            VARCHAR(40)  NOT NULL UNIQUE,
    scale           VARCHAR(10)  NOT NULL,
    stack           VARCHAR(20)  NOT NULL,
    prod_port       INT NOT NULL UNIQUE,
    dev_port        INT NOT NULL UNIQUE,
    status_dev      VARCHAR(20) NOT NULL DEFAULT 'stopped',
    status_prod     VARCHAR(20) NOT NULL DEFAULT 'stopped',
    current_version VARCHAR(40),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO app_config (key, value) VALUES
    ('deploy.apps_root', '""'),
    ('deploy.port_base', '8210'),
    ('deploy.port_max', '8399'),
    ('deploy.keep_versions', '5'),
    ('deploy.health_timeout', '60'),
    ('deploy.auto_stop_dev_on_exit', 'true')
ON CONFLICT (key) DO NOTHING;
```

- [ ] **Step 2: 写失败测试**

```python
# tests/test_deploy_migrations.py
"""M1 迁移：app_project 表与 deploy.* 配置种子（依赖 test_engine fixture，见 tests/conftest.py）。"""
from sqlalchemy import text

async def test_app_project_exists(test_engine):
    async with test_engine.begin() as conn:
        r = await conn.execute(text("SELECT to_regclass('app_project')"))
        assert r.scalar() is not None

async def test_deploy_config_seeded(test_engine):
    async with test_engine.begin() as conn:
        rows = (await conn.execute(text(
            "SELECT key, value FROM app_config WHERE key LIKE 'deploy.%'"))).fetchall()
        cfg = dict(rows)
    assert cfg["deploy.port_base"] == 8210
    assert cfg["deploy.port_max"] == 8399
    assert cfg["deploy.keep_versions"] == 5
    assert cfg["deploy.health_timeout"] == 60
    assert cfg["deploy.auto_stop_dev_on_exit"] is True
    assert cfg["deploy.apps_root"] == ""

async def test_migrations_idempotent(test_engine):
    from app.db import run_migrations
    assert await run_migrations(test_engine) == []
```

- [ ] **Step 3: 运行测试确认失败**

Run: `python -m pytest tests/test_deploy_migrations.py -v`（确保 `zerocode-pg16` 容器存活）
Expected: FAIL（关系 app_project 不存在）

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_deploy_migrations.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add changelog/V20260918_01_deploy_suite.sql tests/test_deploy_migrations.py
git commit -m "feat(deploy): app_project 表与 deploy.* 配置种子"
```

---

### Task 2: deploy 包骨架 — DeployError / slug / 配置 / 端口分配器

**Files:**
- Create: `app/deploy/__init__.py`、`app/deploy/errors.py`、`app/deploy/slugs.py`、`app/deploy/settings.py`、`app/deploy/ports.py`
- Test: `tests/test_deploy_basics.py`

**Interfaces:**
- Produces:
  - `app.deploy.errors.DeployError(Exception)`（`.status` 属性默认 400）
  - `app.deploy.slugs.SLUG_RE`、`normalize_slug(name) -> str`、`validate_slug(slug) -> None`(抛 DeployError)、`suggest_slug(base, taken) -> str`
  - `app.deploy.settings.deploy_cfg(session_factory) -> dict`（合并默认值）；`resolve_apps_root(cfg) -> Path`（`deploy.apps_root` 为空 → 仓库根 `apps/`）
  - `app.deploy.ports.allocate_pair(session: AsyncSession, probe=None) -> tuple[int,int]`（prod 在前）；`port_free(port) -> bool`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_deploy_basics.py
import socket
import pytest
from app.deploy.errors import DeployError
from app.deploy import slugs, settings, ports

def test_slug_rules():
    assert slugs.SLUG_RE.fullmatch("lailiao2")
    assert not slugs.SLUG_RE.fullmatch("2abc")      # 数字开头
    assert not slugs.SLUG_RE.fullmatch("a" * 41)    # 超长
    assert not slugs.SLUG_RE.fullmatch("Abc")       # 大写
    with pytest.raises(DeployError):
        slugs.validate_slug("坏slug")

def test_suggest_slug_dedup():
    assert slugs.suggest_slug("app", set()) == "app"
    assert slugs.suggest_slug("app", {"app"}) == "app2"
    assert slugs.suggest_slug("app", {"app", "app2"}) == "app3"

async def test_deploy_cfg_defaults(test_engine):
    from app.db import make_session_factory
    sf = make_session_factory(test_engine)
    cfg = await settings.deploy_cfg(sf)
    assert cfg["deploy.port_base"] == 8210
    assert settings.resolve_apps_root(cfg).name == "apps"

async def test_allocate_pair_skips_used(test_engine):
    from app.db import make_session_factory
    from sqlalchemy import text
    sf = make_session_factory(test_engine)
    async with sf() as s:
        wid = (await s.execute(text(
            "INSERT INTO workspace (name, root_path) VALUES ('t', 'D:/__zc_basic__') RETURNING id"))).scalar()
        await s.execute(text(
            "INSERT INTO app_project (workspace_id, name, slug, scale, stack, prod_port, dev_port) "
            "VALUES (:w, 't', 'zcbasic', 'small', 'static', 8210, 8211)"), {"w": wid})
        await s.commit()
        assert await ports.allocate_pair(s, probe=lambda p: True) == (8212, 8213)

async def test_allocate_pair_skips_occupied(test_engine):
    from app.db import make_session_factory
    sf = make_session_factory(test_engine)
    async with sf() as s:
        # 8210/8211 空闲登记但 TCP 探测失败（被其他程序占用）→ 跳到下一对
        assert await ports.allocate_pair(s, probe=lambda p: p not in (8210, 8211)) == (8212, 8213)

async def test_allocate_pair_exhausted(test_engine):
    from app.db import make_session_factory
    from sqlalchemy import text as t2
    sf = make_session_factory(test_engine)
    async with sf() as s:
        await s.execute(t2("UPDATE app_config SET value='8210' WHERE key='deploy.port_base'"))
        await s.execute(t2("UPDATE app_config SET value='8210' WHERE key='deploy.port_max'"))
        await s.commit()
        with pytest.raises(DeployError):
            await ports.allocate_pair(s, probe=lambda p: True)
        await s.execute(t2("UPDATE app_config SET value='8210' WHERE key='deploy.port_base'"))
        await s.execute(t2("UPDATE app_config SET value='8399' WHERE key='deploy.port_max'"))
        await s.commit()

def test_port_free_local_udp_free_port():
    import random
    s = socket.socket(); s.bind(("127.0.0.1", 0)); free = s.getsockname()[1]; s.close()
    assert ports.port_free(free) is True
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_deploy_basics.py -v`
Expected: FAIL（ModuleNotFoundError: app.deploy）

- [ ] **Step 3: 实现**

```python
# app/deploy/__init__.py
# 部署发布套件（M1）：slug/端口/供给/模板/compose/dev 生命周期/sqlrunner
```

```python
# app/deploy/errors.py
class DeployError(Exception):
    """部署域业务错误；status 用于 REST 映射。"""
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status
```

```python
# app/deploy/slugs.py
import re
from app.deploy.errors import DeployError

SLUG_RE = re.compile(r"^[a-z][a-z0-9]{1,39}$")

def validate_slug(slug: str) -> None:
    if not SLUG_RE.fullmatch(slug or ""):
        raise DeployError(f"slug 非法（需 ^[a-z][a-z0-9]{{1,39}}$）: {slug}")

def normalize_slug(name: str) -> str:
    """从名称提取可用字符；中文等无法提取时返回空串由调用方要求手填。"""
    s = re.sub(r"[^a-z0-9]", "", (name or "").lower())
    return s[:40]

def suggest_slug(base: str, taken: set[str]) -> str:
    base = base or "app"
    if not SLUG_RE.fullmatch(base):
        base = "app"
    cand, i = base, 1
    while cand in taken:
        i += 1
        cand = f"{base}{i}"
    return cand
```

```python
# app/deploy/settings.py
from pathlib import Path
from sqlalchemy import text

DEFAULTS = {
    "deploy.apps_root": "",
    "deploy.port_base": 8210,
    "deploy.port_max": 8399,
    "deploy.keep_versions": 5,
    "deploy.health_timeout": 60,
    "deploy.auto_stop_dev_on_exit": True,
}
REPO_ROOT = Path(__file__).resolve().parents[2]  # app/deploy/settings.py → 仓库根

async def deploy_cfg(session_factory) -> dict:
    """读 deploy.* 键并与默认值合并（缺键回退默认，保证测试库可用）。"""
    cfg = dict(DEFAULTS)
    async with session_factory() as s:
        rows = (await s.execute(text(
            "SELECT key, value FROM app_config WHERE key LIKE 'deploy.%'"))).fetchall()
    for k, v in rows:
        cfg[k] = v
    return cfg

def resolve_apps_root(cfg: dict) -> Path:
    raw = cfg.get("deploy.apps_root") or ""
    return Path(raw) if raw else REPO_ROOT / "apps"
```

```python
# app/deploy/ports.py
import socket
from sqlalchemy import text
from app.deploy.errors import DeployError

def port_free(port: int, host: str = "127.0.0.1", timeout: float = 0.2) -> bool:
    """TCP connect 探测：连不上视为空闲（listen 探测语义，规格 05 §4.1）。"""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return False
    except OSError:
        return True

async def allocate_pair(session, probe=None) -> tuple[int, int]:
    """在 app_config 的端口段内成对分配（prod=偶, dev=奇）；跳过已登记与探测占用的端口。"""
    probe = probe or port_free
    base = int((await session.execute(text(
        "SELECT value FROM app_config WHERE key='deploy.port_base'"))).scalar() or 8210)
    top = int((await session.execute(text(
        "SELECT value FROM app_config WHERE key='deploy.port_max'"))).scalar() or 8399)
    if base < 1024 or top <= base:
        raise DeployError(f"端口段非法: {base}-{top}")
    used = set((await session.execute(text(
        "SELECT prod_port, dev_port FROM app_project"))).fetchall())
    used = {p for pair in used for p in pair}
    for prod in range(base if base % 2 == 0 else base + 1, top, 2):
        dev = prod + 1
        if prod not in used and dev not in used and probe(prod) and probe(dev):
            return prod, dev
    raise DeployError("端口段已用尽，请在设置中扩大端口范围", status=409)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_deploy_basics.py tests/test_deploy_migrations.py -v`
Expected: 全部 passed（注意 test_allocate_pair_exhausted 结束时已恢复端口段默认值，不留污染）

- [ ] **Step 5: Commit**

```bash
git add app/deploy tests/test_deploy_basics.py
git commit -m "feat(deploy): slug 校验、deploy 配置读取与端口成对分配器"
```

---

### Task 3: PG 分库供给 provision.py（角色/库/凭据/zcnet/删库）

**Files:**
- Create: `app/deploy/provision.py`
- Modify: `app/services/config_service.py`（`get_all` 排除 `dbcred.*`）
- Test: `tests/test_provision.py`

**Interfaces:**
- Consumes: `app.config.database_url()`、`app.db.make_engine`。
- Produces:
  - `provision.admin_url() -> str`（库切换为 postgres，密码不掩码）
  - `provision.provision_env(session_factory, slug, env) -> dict`（env ∈ {"dev","prod"}；返回 `{db,user,password}`；幂等：角色/库已存在且凭据已存则原样返回）
  - `provision.read_cred(session_factory, slug, env) -> dict | None`
  - `provision.drop_env(session_factory, slug, env) -> None`（断连接→删库→删角色，幂等）
  - `provision.ensure_zcnet() -> None`（docker CLI，幂等；docker 不可用抛 DeployError 503）

- [ ] **Step 1: 写失败测试**（真实 PG 容器，独立 slug，自带清理）

```python
# tests/test_provision.py
import pytest
from sqlalchemy import text
from app.db import make_engine, make_session_factory
from app.deploy import provision
from app.config import test_database_url  # 平台库连接串与测试库同实例同凭据，仅库名不同

pytestmark = pytest.mark.asyncio

SLUG = "zctask3"

async def _cleanup():
    await provision.drop_env(make_session_factory(make_engine()), SLUG, "dev")

@pytest.fixture(autouse=True)
async def _clean():
    await _cleanup()
    yield
    await _cleanup()

async def test_provision_creates_role_db_and_cred():
    sf = make_session_factory(make_engine())
    cred = await provision.provision_env(sf, SLUG, "dev")
    assert cred["db"] == f"{SLUG}_dev" and cred["user"] == f"{SLUG}_dev"
    assert len(cred["password"]) >= 20
    # 幂等：再次供给返回同一凭据
    cred2 = await provision.provision_env(sf, SLUG, "dev")
    assert cred2 == cred
    # 角色只能连自己的库：连 prod 库应失败（用 dev 凭据连 prod 库名）
    from sqlalchemy.ext.asyncio import create_async_engine
    bad = f"postgresql+asyncpg://{cred['user']}:{cred['password']}@localhost:5433/{SLUG}_prod"
    with pytest.raises(Exception):
        e = create_async_engine(bad)
        async with e.connect():
            pass
        await e.dispose()

async def test_read_cred_none_when_missing():
    sf = make_session_factory(make_engine())
    assert await provision.read_cred(sf, "nope9x", "dev") is None

async def test_config_get_all_hides_dbcred():
    """dbcred.* 凭据不得出现在通用配置读取（/api/config 与 agent 配置流共用 get_all）。"""
    sf = make_session_factory(make_engine())
    await provision.provision_env(sf, SLUG, "dev")
    from app.services import config_service
    cfg = await config_service.get_all(sf)
    assert not [k for k in cfg if k.startswith("dbcred.")]
    assert await provision.read_cred(sf, SLUG, "dev") is not None  # 专用通道仍可读
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_provision.py -v`
Expected: FAIL（ModuleNotFoundError: app.deploy.provision）

- [ ] **Step 3: 实现**

```python
# app/deploy/provision.py
"""应用分库供给（规格 06）：复用 zerocode-pg16 同实例，一应用两库两角色，最小权限。
凭据仅存 app_config 的 dbcred.<slug>.<env> 与 deploy/.env；通用配置读取必须排除 dbcred.*。
"""
import asyncio, json, secrets
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from app import config as app_config
from app.deploy.errors import DeployError

ENVS = ("dev", "prod")

def admin_url() -> str:
    url = make_url(app_config.database_url()).set(database="postgres")
    return url.render_as_string(hide_password=False)

def _pg_host_port() -> tuple[str, int]:
    url = make_url(app_config.database_url())
    return url.host or "localhost", url.port or 5433

async def _app_config_set(session_factory, key: str, value: dict) -> None:
    async with session_factory() as s:
        await s.execute(text(
            "INSERT INTO app_config (key, value) VALUES (:k, CAST(:v AS JSONB)) "
            "ON CONFLICT (key) DO UPDATE SET value = CAST(:v AS JSONB), updated_at = now()"),
            {"k": key, "v": json.dumps(value, ensure_ascii=False)})
        await s.commit()

async def read_cred(session_factory, slug: str, env: str) -> dict | None:
    async with session_factory() as s:
        row = (await s.execute(text(
            "SELECT value FROM app_config WHERE key = :k"),
            {"k": f"dbcred.{slug}.{env}"})).first()
    return dict(row[0]) if row else None

async def provision_env(session_factory, slug: str, env: str) -> dict:
    assert env in ENVS
    name, user = f"{slug}_{env}", f"{slug}_{env}"
    existing = await read_cred(session_factory, slug, env)
    # 角色与库已存在且凭据已登记 → 幂等返回
    engine = create_async_engine(admin_url(), isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            has_role = (await conn.execute(text(
                "SELECT 1 FROM pg_roles WHERE rolname = :n"), {"n": user})).first() is not None
            has_db = (await conn.execute(text(
                "SELECT 1 FROM pg_database WHERE datname = :n"), {"n": name})).first() is not None
        if has_role and has_db and existing:
            return existing
        password = (existing or {}).get("password") or secrets.token_urlsafe(18)
        async with engine.connect() as conn:
            if not has_role:
                pw = password.replace("'", "''")
                await conn.exec_driver_sql(f"CREATE ROLE {user} LOGIN PASSWORD '{pw}'")
            if not has_db:
                await conn.exec_driver_sql(f"CREATE DATABASE {name} OWNER {user}")
    finally:
        await engine.dispose()
    cred = {"db": name, "user": user, "password": password}
    await _app_config_set(session_factory, f"dbcred.{slug}.{env}", cred)
    return cred

async def drop_env(session_factory, slug: str, env: str) -> None:
    name, user = f"{slug}_{env}", f"{slug}_{env}"
    host, port = _pg_host_port()
    engine = create_async_engine(admin_url(), isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            # 断开活动连接（重试 3 次）
            for _ in range(3):
                r = await conn.execute(text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :n AND pid <> pg_backend_pid()"), {"n": name})
                if r.rowcount == 0:
                    break
            has_db = (await conn.execute(text(
                "SELECT 1 FROM pg_database WHERE datname = :n"), {"n": name})).first()
            if has_db:
                await conn.exec_driver_sql(f"DROP DATABASE {name}")
            has_role = (await conn.execute(text(
                "SELECT 1 FROM pg_roles WHERE rolname = :n"), {"n": user})).first()
            if has_role:
                await conn.exec_driver_sql(f"DROP ROLE {user}")
    finally:
        await engine.dispose()
    async with session_factory() as s:
        await s.execute(text("DELETE FROM app_config WHERE key = :k"),
                        {"k": f"dbcred.{slug}.{env}"})
        await s.commit()

async def ensure_zcnet() -> None:
    """创建外部网络 zcnet 并把 zerocode-pg16 接入（幂等；docker 不可用报 503）。"""
    async def docker(*args, ok=(0,)):
        p = await asyncio.create_subprocess_exec(
            "docker", *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, err = await p.communicate()
        if p.returncode not in ok:
            raise RuntimeError(err.decode(errors="replace").strip() or f"docker {' '.join(args)} 失败")
        return out.decode(errors="replace")
    try:
        await docker("network", "inspect", "zcnet")
    except RuntimeError:
        try:
            await docker("network", "create", "zcnet")
        except RuntimeError as e:
            raise DeployError(f"无法创建 docker 网络 zcnet（Docker 未启动？）: {e}", status=503)
    try:
        await docker("network", "connect", "zcnet", "zerocode-pg16")
    except RuntimeError as e:
        if "already exists" not in str(e):  # 容器已在网内 → 幂等通过
            raise DeployError(f"无法将 zerocode-pg16 接入 zcnet: {e}", status=503)
```

```python
# app/services/config_service.py —— get_all 排除 dbcred.*（其余不动）
async def get_all(session: AsyncSession) -> dict:
    rows = (await session.execute(text(
        "SELECT key, value FROM app_config WHERE key NOT LIKE 'dbcred.%'"))).fetchall()
    # asyncpg 对 JSONB 默认已自动解码为 Python 对象，无需再 json.loads
    return {k: v for k, v in rows}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_provision.py -v`
Expected: 3 passed（连错库用例抛认证失败属预期）

- [ ] **Step 5: Commit**

```bash
git add app/deploy/provision.py app/services/config_service.py tests/test_provision.py
git commit -m "feat(deploy): PG 分库供给（角色/库/凭据/zcnet/删库）与 dbcred 隔离"
```

---

### Task 4: 脚手架模板 vue3-fastapi 与渲染器

**Files:**
- Create: `app/templates/vue3-fastapi/`（frontend/backend/sqlchange/README，11 个文件，内容见下）
- Create: `app/deploy/scaffold.py`
- Test: `tests/test_scaffold.py`

**Interfaces:**
- Produces:
  - `scaffold.render(template: Path, dst: Path, mapping: dict) -> None`：递归复制并替换 `{{KEY}}`；生成 `sqlchange/V<YYYYMMDD>_01_init.sql`（mapping 取 `TODAY`）；完成后自检不得残留 `{{`
  - 模板占位符：`{{SLUG}} {{APP_NAME}} {{DEV_PORT}} {{PROD_PORT}} {{DEV_DB}} {{PROD_DB}} {{TODAY}}`
- Consumes（Task 5 调用）: `render(...)` 完成后目录内含可运行骨架 + Dockerfile×2 + sqlchange 初始迁移。

- [ ] **Step 1: 写模板文件**（全部为模板原文；`{{KEY}}` 由渲染器替换）

`app/templates/vue3-fastapi/frontend/package.json`:
```json
{
  "name": "{{SLUG}}-frontend",
  "private": true,
  "version": "0.1.0",
  "scripts": { "dev": "vite", "build": "vite build" },
  "dependencies": {
    "ant-design-vue": "^4.2.6",
    "axios": "^1.7.9",
    "pinia": "^2.2.6",
    "vue": "^3.4.38"
  },
  "devDependencies": {
    "@vitejs/plugin-vue": "^5.1.4",
    "vite": "^5.4.11"
  }
}
```

`app/templates/vue3-fastapi/frontend/vite.config.js`:
```js
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    proxy: { '/api': { target: 'http://backend:8000', changeOrigin: true } },
  },
})
```

`app/templates/vue3-fastapi/frontend/index.html`:
```html
<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>{{APP_NAME}}</title>
  </head>
  <body>
    <div id="app"></div>
    <script type="module" src="/src/main.js"></script>
  </body>
</html>
```

`app/templates/vue3-fastapi/frontend/src/main.js`:
```js
import { createApp } from 'vue'
import Antd from 'ant-design-vue'
import 'ant-design-vue/dist/reset.css'
import App from './App.vue'

createApp(App).use(Antd).mount('#app')
```

`app/templates/vue3-fastapi/frontend/src/App.vue`:
```vue
<script setup>
// 应用骨架首页：展示后端健康与示例数据，AI 在此基础上实现功能
import { onMounted, ref } from 'vue'
import axios from 'axios'

const health = ref('检测中…')
const notes = ref([])
const title = ref('')

async function load() {
  try {
    const h = await axios.get('/api/health')
    health.value = h.data.db ? '后端正常，数据库已连接' : '后端正常，数据库未连接'
    notes.value = (await axios.get('/api/notes')).data
  } catch {
    health.value = '后端未就绪（dev 环境启动中或代码有误）'
  }
}
async function add() {
  if (!title.value.trim()) return
  await axios.post('/api/notes', { title: title.value.trim() })
  title.value = ''
  await load()
}
onMounted(load)
</script>

<template>
  <a-card :title="'{{APP_NAME}}'" style="max-width: 720px; margin: 40px auto">
    <a-typography-paragraph>{{ health }}</a-typography-paragraph>
    <a-space-competer style="display:flex; gap:8px; margin-bottom:16px">
      <a-input v-model:value="title" placeholder="记点什么…" style="width: 300px" @pressEnter="add" />
      <a-button type="primary" @click="add">添加</a-button>
    </a-space-competer>
    <a-list :data-source="notes" bordered>
      <template #renderItem="{ item }">
        <a-list-item>{{ item.title }}</a-list-item>
      </template>
    </a-list>
  </a-card>
</template>
```

> 注意：上面 `<a-space-competer>` 为笔误高亮——实现时必须写 `<a-space-compact>`（测试不覆盖 UI 文本，靠冒烟验证）。此占位说明不是模板内容，模板文件里写正确标签。

`app/templates/vue3-fastapi/backend/requirements.txt`:
```
fastapi==0.115.6
uvicorn[standard]==0.30.6
sqlalchemy==2.0.36
asyncpg==0.29.0
pydantic-settings==2.6.1
```

`app/templates/vue3-fastapi/backend/app/__init__.py`:
```python
```

`app/templates/vue3-fastapi/backend/app/main.py`:
```python
"""{{APP_NAME}} 后端骨架：/api 前缀 + health + demo_notes 示例；数据库连接从环境变量读取。"""
import os
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

DATABASE_URL = os.environ.get("DATABASE_URL", "")
engine = create_async_engine(DATABASE_URL) if DATABASE_URL else None
Session = async_sessionmaker(engine, expire_on_commit=False) if engine else None

app = FastAPI(title="{{APP_NAME}}")

@app.get("/api/health")
async def health():
    db = False
    if Session:
        try:
            async with Session() as s:
                await s.execute(text("SELECT 1"))
            db = True
        except Exception:
            db = False
    return {"status": "ok", "db": db}

class NoteIn(BaseModel):
    title: str

@app.get("/api/notes")
async def list_notes():
    if not Session:
        raise HTTPException(500, "DATABASE_URL 未配置")
    async with Session() as s:
        rows = (await s.execute(text(
            "SELECT id, title, created_at FROM demo_notes ORDER BY id DESC LIMIT 100"))).mappings().all()
    return [dict(r) for r in rows]

@app.post("/api/notes")
async def add_note(body: NoteIn):
    if not Session:
        raise HTTPException(500, "DATABASE_URL 未配置")
    async with Session() as s:
        row = (await s.execute(text(
            "INSERT INTO demo_notes (title) VALUES (:t) RETURNING id, title, created_at"),
            {"t": body.title})).mappings().first()
        await s.commit()
    return dict(row)
```

`app/templates/vue3-fastapi/backend/Dockerfile`（发布用，M2 消费；先就位）:
```dockerfile
FROM python:3.12-slim
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
ENV APP_PORT=8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${APP_PORT}"]
```

`app/templates/vue3-fastapi/frontend/Dockerfile`（发布用）:
```dockerfile
FROM node:20-alpine AS build
WORKDIR /src
COPY package.json ./
RUN npm install
COPY . .
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=build /src/dist /usr/share/nginx/html
```

`app/templates/vue3-fastapi/sqlchange/README.md`:
```markdown
# sqlchange —— 数据库变更目录

- 一切建表/改表/初始化数据都必须成为本目录下的一个版本文件：`V<YYYYMMDD>_<两位序号>_<说明>.sql`
- 一个文件一个逻辑变更；写前查看目录取下一个序号；禁止修改已存在的文件
- dev 环境启动时自动执行；发布到生产时自动执行（见平台规格 07）
```

`app/templates/vue3-fastapi/README.md`:
```markdown
# {{APP_NAME}}

zerocode 应用（小型 · Vue3 + FastAPI）。

- 本机访问（dev 预览）：http://localhost:{{DEV_PORT}}
- 数据库：dev = {{DEV_DB}}，prod = {{PROD_DB}}（由平台自动供给）
- 数据变更：一律写入 `sqlchange/V<日期>_<序号>_<说明>.sql`
- `deploy/` 目录由 zerocode 平台生成与管理，请勿手改
```

> 说明：模板中**不含** `deploy/`、`app.json`、`sqlchange/V*.sql`（后两者由 scaffold 渲染器生成）。

- [ ] **Step 2: 写失败测试**

```python
# tests/test_scaffold.py
import re
from pathlib import Path
from app.deploy import scaffold

TEMPLATE = Path(__file__).resolve().parents[1] / "app" / "templates" / "vue3-fastapi"

def test_render_replaces_and_generates_init_sql(tmp_path):
    mapping = {"SLUG": "lailiao", "APP_NAME": "来料登记", "DEV_PORT": "8211", "PROD_PORT": "8210",
               "DEV_DB": "lailiao_dev", "PROD_DB": "lailiao_prod", "TODAY": "20260918"}
    scaffold.render(TEMPLATE, tmp_path, mapping)
    pkg = (tmp_path / "frontend" / "package.json").read_text(encoding="utf-8")
    assert '"name": "lailiao-frontend"' in pkg and "{{" not in pkg
    readme = (tmp_path / "README.md").read_text(encoding="utf-8")
    assert "来料登记" in readme and "8211" in readme
    sqls = list((tmp_path / "sqlchange").glob("V*.sql"))
    assert len(sqls) == 1 and re.fullmatch(r"V20260918_01_init\.sql", sqls[0].name)
    assert "CREATE TABLE IF NOT EXISTS demo_notes" in sqls[0].read_text(encoding="utf-8")
    # 全目录无残留占位符
    for p in tmp_path.rglob("*"):
        if p.is_file():
            assert "{{" not in p.read_text(encoding="utf-8", errors="replace"), p

def test_render_missing_key_raises(tmp_path):
    import pytest
    from app.deploy.errors import DeployError
    with pytest.raises(DeployError):
        scaffold.render(TEMPLATE, tmp_path, {"SLUG": "x"})  # 缺大部分键
```

- [ ] **Step 3: 运行测试确认失败**

Run: `python -m pytest tests/test_scaffold.py -v`
Expected: FAIL（ModuleNotFoundError: app.deploy.scaffold）

- [ ] **Step 4: 实现渲染器**

```python
# app/deploy/scaffold.py
"""脚手架铺底（规格 01 §4.5 / 02）：复制模板 → 替换 {{KEY}} → 生成初始迁移 → 残留自检。"""
import datetime, re, shutil
from pathlib import Path
from app.deploy.errors import DeployError

PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")
INIT_SQL = """-- V{TODAY}_01_init.sql
-- 日期：{TODAY_DASH}  用途：示例表 demo_notes（AI 可按需求替换或追加新文件）  影响：demo_notes

CREATE TABLE IF NOT EXISTS demo_notes (
    id         BIGSERIAL PRIMARY KEY,
    title      VARCHAR(200) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO demo_notes (title) VALUES ('欢迎使用 {{APP_NAME}}') ON CONFLICT DO NOTHING;
"""

def render(template: Path, dst: Path, mapping: dict) -> None:
    required = {"SLUG", "APP_NAME", "DEV_PORT", "PROD_PORT", "DEV_DB", "PROD_DB", "TODAY"}
    missing = required - set(mapping)
    if missing:
        raise DeployError(f"模板渲染缺少占位值: {sorted(missing)}")
    dst.mkdir(parents=True, exist_ok=False)
    for src in sorted(template.rglob("*")):
        rel = src.relative_to(template)
        target = dst / rel
        if src.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        text = src.read_text(encoding="utf-8")
        text = PLACEHOLDER.sub(lambda m: str(mapping[m.group(1)]), text)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="")
    today = mapping["TODAY"]
    init = dst / "sqlchange" / f"V{today}_01_init.sql"
    init.parent.mkdir(parents=True, exist_ok=True)
    init.write_text(INIT_SQL.format(
        TODAY=today,
        TODAY_DASH=f"{today[:4]}-{today[4:6]}-{today[6:]}",
        APP_NAME=mapping["APP_NAME"]), encoding="utf-8", newline="")
    # 残留自检：任何文件不得残留 {{
    for p in dst.rglob("*"):
        if p.is_file() and "{{" in p.read_text(encoding="utf-8", errors="replace"):
            raise DeployError(f"占位符渲染残留: {p.relative_to(dst)}")
```

- [ ] **Step 5: 运行测试确认通过**

Run: `python -m pytest tests/test_scaffold.py -v`
Expected: 2 passed

- [ ] **Step 6: Commit**

```bash
git add app/templates app/deploy/scaffold.py tests/test_scaffold.py
git commit -m "feat(deploy): vue3-fastapi 脚手架模板与渲染器"
```

---

### Task 5: 应用服务层 — 原子创建/回滚/列表/详情

**Files:**
- Create: `app/services/app_service.py`
- Test: `tests/test_app_service.py`

**Interfaces:**
- Consumes: Task 2 `slugs/ports/settings`、Task 3 `provision`、Task 4 `scaffold`、平台表 `workspace`/`app_project`。
- Produces:
  - `app_service.SCALES = ("small", "large")`；`app_service.small_stacks = ("vue3-fastapi", "vue3-node", "static")`；`LARGE_STACK = "vue3-springboot"`
  - `app_service.to_dict(row: Mapping) -> dict`（app_project 行 → JSON 安全 dict，datetime → isoformat）
  - `app_service.create_app_project(session_factory, *, name, slug, scale, stack, root=None) -> dict`（失败自动回滚并抛 DeployError；`root` 为空 = `apps_root/<slug>`）
  - `app_service.list_apps(session_factory) -> list[dict]`、`app_service.get_app(session_factory, app_id) -> dict`（不存在抛 DeployError 404）
  - `app_service.load_app_json(root: Path) -> dict`（缺失/损坏抛 DeployError）

- [ ] **Step 1: 写失败测试**

```python
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
ROOT = Path(f"D:/__zc_task5__/{SLUG}")

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
        sf, name="测试应用", slug=SLUG, scale="small", stack="vue3-fastapi")
    assert app["slug"] == SLUG and app["scale"] == "small"
    assert app["prod_port"] % 2 == 0 and app["dev_port"] == app["prod_port"] + 1
    # 目录产物
    assert (ROOT / "app.json").is_file()
    assert (ROOT / "backend" / "app" / "main.py").is_file()
    assert list((ROOT / "sqlchange").glob("V*.sql"))
    assert (ROOT / "deploy" / "dev-compose.yml").is_file()      # 由 ensure_deploy_files 生成（Task 10）
    assert (ROOT / "deploy" / ".env").is_file()
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
            sf, name="x", slug=SLUG, scale="middle", stack="vue3-fastapi")
    with pytest.raises(DeployError):
        await app_service.create_app_project(
            sf, name="x", slug=SLUG, scale="large", stack="vue3-fastapi")  # large 锁定 springboot

async def test_create_app_duplicate_slug():
    sf = make_session_factory(make_engine())
    await app_service.create_app_project(sf, name="一", slug=SLUG, scale="small", stack="vue3-fastapi")
    with pytest.raises(DeployError) as ei:
        await app_service.create_app_project(sf, name="二", slug=SLUG, scale="small", stack="vue3-fastapi")
    assert ei.value.status == 409

async def test_create_app_rollback_on_provision_failure(monkeypatch):
    sf = make_session_factory(make_engine())
    async def boom(*a, **k):
        raise RuntimeError("pg down")
    monkeypatch.setattr(app_service.provision, "provision_env", boom)
    with pytest.raises(DeployError):
        await app_service.create_app_project(sf, name="x", slug=SLUG, scale="small", stack="vue3-fastapi")
    assert not ROOT.exists()                                   # 目录已回滚
    async with sf() as s:
        assert (await s.execute(text(
            "SELECT count(*) FROM app_project WHERE slug=:s"), {"s": SLUG})).scalar() == 0
        assert (await s.execute(text(
            "SELECT count(*) FROM workspace WHERE root_path=:p"), {"p": str(ROOT)})).scalar() == 0
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_app_service.py -v`
Expected: FAIL（ModuleNotFoundError: app.services.app_service）

- [ ] **Step 3: 实现**

```python
# app/services/app_service.py
"""应用工程服务（规格 01）：原子创建（目录→app.json→平台记录→端口→供给→deploy 文件），失败全回滚。"""
import datetime, json, shutil
from pathlib import Path
from sqlalchemy import text
from app.deploy import provision, scaffold
from app.deploy.errors import DeployError
from app.deploy.ports import allocate_pair
from app.deploy.settings import deploy_cfg, resolve_apps_root

SCALES = ("small", "large")
SMALL_STACKS = ("vue3-fastapi", "vue3-node", "static")
LARGE_STACK = "vue3-springboot"
STACKS = SMALL_STACKS + (LARGE_STACK,)
TEMPLATES = Path(__file__).resolve().parents[1] / "app" / "templates"  # app/services → 仓库根/app/templates
HEALTH_BY_STACK = {"vue3-fastapi": "/api/health", "vue3-node": "/api/health",
                   "vue3-springboot": "/actuator/health", "static": ""}

def to_dict(row) -> dict:
    d = dict(row)
    for k in ("created_at", "updated_at"):
        if d.get(k) is not None:
            d[k] = d[k].isoformat()
    return d

def _check_scale_stack(scale: str, stack: str) -> None:
    if scale not in SCALES:
        raise DeployError(f"规模非法: {scale}（small/large）")
    if scale == "large" and stack != LARGE_STACK:
        raise DeployError("大型应用锁定 Vue3 + Spring Boot 3")
    if stack not in STACKS:
        raise DeployError(f"技术栈非法: {stack}")

def load_app_json(root: Path) -> dict:
    f = root / "app.json"
    if not f.is_file():
        raise DeployError(f"应用清单缺失: {root}")
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception as e:
        raise DeployError(f"app.json 损坏: {e}")

async def _insert_rows(session_factory, *, name, slug, scale, stack, root) -> dict:
    """workspace + app_project + 端口对，单事务提交。"""
    async with session_factory() as s:
        dup = (await s.execute(text("SELECT id FROM workspace WHERE root_path = :p"),
                               {"p": str(root)})).first()
        if dup:
            raise DeployError("该目录已被登记为工作区", status=409)
        dup_slug = (await s.execute(text("SELECT id FROM app_project WHERE slug = :s"),
                                    {"s": slug})).first()
        if dup_slug:
            raise DeployError(f"slug 已存在: {slug}", status=409)
        wid = (await s.execute(text(
            "INSERT INTO workspace (name, root_path) VALUES (:n, :p) RETURNING id"),
            {"n": name, "p": str(root)})).scalar()
        prod_port, dev_port = await allocate_pair(s)
        row = (await s.execute(text(
            "INSERT INTO app_project (workspace_id, name, slug, scale, stack, prod_port, dev_port) "
            "VALUES (:w, :n, :s, :sc, :st, :pp, :dp) "
            "RETURNING id, workspace_id, name, slug, scale, stack, prod_port, dev_port, "
            "status_dev, status_prod, current_version, created_at, updated_at"),
            {"w": wid, "n": name, "s": slug, "sc": scale, "st": stack,
             "pp": prod_port, "dp": dev_port})).mappings().first()
        await s.commit()
    return to_dict(row)

async def _delete_rows(session_factory, app_id: int) -> None:
    async with session_factory() as s:
        await s.execute(text("DELETE FROM app_project WHERE id = :i"), {"i": app_id})
        await s.commit()

async def create_app_project(session_factory, *, name: str, slug: str, scale: str,
                             stack: str, root: str | None = None) -> dict:
    if not (name or "").strip():
        raise DeployError("应用名称不能为空")
    from app.deploy.slugs import validate_slug
    validate_slug(slug)
    _check_scale_stack(scale, stack)
    cfg = await deploy_cfg(session_factory)
    base = resolve_apps_root(cfg)
    root_path = Path(root) if root else base / slug
    if root_path.exists() and any(root_path.iterdir()):
        raise DeployError(f"目录已存在且非空: {root_path}", status=409)
    today = datetime.date.today().strftime("%Y%m%d")
    mapping = {"SLUG": slug, "APP_NAME": name.strip(), "DEV_PORT": "", "PROD_PORT": "",
               "DEV_DB": f"{slug}_dev", "PROD_DB": f"{slug}_prod", "TODAY": today}
    app_id = None
    try:
        scaffold.render(TEMPLATES / stack, root_path, mapping)  # stack 模板必须存在
        app_id = None
    except DeployError:
        raise
    try:
        app = await _insert_rows(session_factory, name=name.strip(), slug=slug, scale=scale,
                                 stack=stack, root=root_path)
        app_id = app["id"]
        mapping["DEV_PORT"], mapping["PROD_PORT"] = str(app["dev_port"]), str(app["prod_port"])
        # app.json：不含密码；端口补齐后写最终版
        meta = {"schema": "zc-app/1", "name": name.strip(), "slug": slug, "scale": scale,
                "stack": stack, "ports": {"prod": app["prod_port"], "dev": app["dev_port"]},
                "database": {"dev": {"name": f"{slug}_dev", "user": f"{slug}_dev"},
                             "prod": {"name": f"{slug}_prod", "user": f"{slug}_prod"}},
                "health": {"frontend": "/", "backend": HEALTH_BY_STACK[stack]},
                "createdAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "platformMin": "1.1"}
        (root_path / "app.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="")
        cred = await provision.provision_env(session_factory, slug, "dev")
        from app.deploy.compose import ensure_deploy_files   # 延迟导入避免环
        ensure_deploy_files(root_path, meta, {"dev": cred})
        return app
    except DeployError:
        await _rollback(session_factory, root_path, slug, app_id, provisioned=True)
        raise
    except Exception as e:
        await _rollback(session_factory, root_path, slug, app_id, provisioned=True)
        raise DeployError(f"创建应用失败: {e}") from e

async def _rollback(session_factory, root_path: Path, slug: str, app_id: int | None,
                    provisioned: bool) -> None:
    if app_id:
        try:
            await _delete_rows(session_factory, app_id)
        except Exception:
            pass
    async with session_factory() as s:
        await s.execute(text("DELETE FROM workspace WHERE root_path = :p"), {"p": str(root_path)})
        await s.commit()
    if provisioned:
        try:
            await provision.drop_env(session_factory, slug, "dev")
        except Exception:
            pass
    shutil.rmtree(root_path, ignore_errors=True)

async def list_apps(session_factory) -> list[dict]:
    async with session_factory() as s:
        rows = (await s.execute(text(
            "SELECT id, workspace_id, name, slug, scale, stack, prod_port, dev_port, "
            "status_dev, status_prod, current_version, created_at, updated_at "
            "FROM app_project ORDER BY id DESC"))).mappings().all()
    return [to_dict(r) for r in rows]

async def get_app(session_factory, app_id: int) -> dict:
    async with session_factory() as s:
        row = (await s.execute(text(
            "SELECT id, workspace_id, name, slug, scale, stack, prod_port, dev_port, "
            "status_dev, status_prod, current_version, created_at, updated_at "
            "FROM app_project WHERE id = :i"), {"i": app_id})).mappings().first()
    if not row:
        raise DeployError("应用不存在", status=404)
    return to_dict(row)
```

> 依赖说明：`create_app_project` 引用了 Task 10 的 `compose.ensure_deploy_files`（写 `deploy/dev-compose.yml` 与 `deploy/.env`）与 Task 10 的测试。**执行顺序上 Task 5 的测试需在 Task 10 完成后全部转绿**——为保持任务独立可验证，Task 5 先创建 `app/deploy/compose.py` 的**最小占位实现不可取**（禁占位）；规定：**Task 5 与 Task 10 按 10→5 顺序执行**（先渲染器后服务层），Task 5 的 Step 2 失败信息将包含 `app.deploy.compose` 缺失，属预期。执行器按编号顺序调度时，将 Task 10 提到 Task 5 之前执行（控制器在派发时调整顺序，代码零依赖冲突：compose.py 只依赖 app.json dict 与 cred dict）。

- [ ] **Step 4: 运行测试确认通过**（在 Task 10 完成后执行）

Run: `python -m pytest tests/test_app_service.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add app/services/app_service.py tests/test_app_service.py
git commit -m "feat(deploy): 应用原子创建/回滚/列表/详情服务"
```

---

### Task 6: REST /api/apps（创建/校验/列表/详情）+ 挂路由

**Files:**
- Create: `app/api/apps.py`
- Modify: `app/main.py`（include apps router）
- Test: `tests/test_apps_api.py`

**Interfaces:**
- Consumes: Task 5 `app_service`、`DeployError`。
- Produces:
  - `POST /api/apps` `{name, slug, scale, stack}` → 201 app dict；错误按 `DeployError.status` 映射（400/409/404/503）
  - `POST /api/apps/validate-slug` `{slug}` → `{ok, suggest?}`
  - `GET /api/apps` → list；`GET /api/apps/{id}` → dict
  - 路由前缀 `/api`，注册方式与 `workspaces.router` 相同（`app.include_router(apps.router)`）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_apps_api.py
import shutil
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from app.main import create_app
from app.db import make_engine
from app.deploy import provision

pytestmark = pytest.mark.asyncio
SLUG = "zctask6"
ROOT = None  # 用平台默认 apps_root/<slug>

@pytest.fixture
async def client():
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c, app.router.lifespan_context(app):
        yield c

@pytest.fixture(autouse=True)
async def _clean():
    from app.db import make_session_factory
    sf = make_session_factory(make_engine())
    yield
    try:
        await provision.drop_env(sf, SLUG, "dev")
    except Exception:
        pass
    async with sf() as s:
        await s.execute(text("DELETE FROM app_project WHERE slug=:s"), {"s": SLUG})
        w = (await s.execute(text("SELECT id FROM workspace WHERE root_path LIKE :p"),
                             {"p": f"%\\{SLUG}"})).scalars().all()
        for wid in w:
            await s.execute(text("DELETE FROM workspace WHERE id=:i"), {"i": wid})
        await s.commit()
    shutil.rmtree(f"apps/{SLUG}", ignore_errors=True)

async def test_validate_slug(client):
    r = await client.post("/api/apps/validate-slug", json={"slug": "Bad Slug"})
    assert r.status_code == 200 and r.json()["ok"] is False and r.json()["suggest"]
    r = await client.post("/api/apps/validate-slug", json={"slug": SLUG})
    assert r.json()["ok"] is True

async def test_create_and_list(client):
    r = await client.post("/api/apps", json={
        "name": "API 测试应用", "slug": SLUG, "scale": "small", "stack": "vue3-fastapi"})
    assert r.status_code == 201, r.text
    app = r.json()
    assert app["slug"] == SLUG and app["status_dev"] == "stopped"
    r2 = await client.get("/api/apps")
    assert any(a["slug"] == SLUG for a in r2.json())
    r3 = await client.get(f"/api/apps/{app['id']}")
    assert r3.json()["name"] == "API 测试应用"
    r4 = await client.get("/api/apps/999999")
    assert r4.status_code == 404

async def test_create_conflict_and_bad_request(client):
    body = {"name": "x", "slug": SLUG, "scale": "small", "stack": "vue3-fastapi"}
    assert (await client.post("/api/apps", json=body)).status_code == 201
    assert (await client.post("/api/apps", json=body)).status_code == 409
    bad = dict(body, slug=SLUG, scale="middle")
    assert (await client.post("/api/apps", json=bad)).status_code == 400
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_apps_api.py -v`
Expected: FAIL（404：路由不存在）

- [ ] **Step 3: 实现**

```python
# app/api/apps.py
"""应用管理 REST（规格 01 §5 / 08 §4.4）。DeployError.status 直接映射 HTTP 状态码。"""
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from app.deploy.errors import DeployError
from app.deploy.slugs import SLUG_RE, suggest_slug, validate_slug
from app.services import app_service

router = APIRouter(prefix="/api/apps")

class AppIn(BaseModel):
    name: str
    slug: str
    scale: str
    stack: str

class SlugIn(BaseModel):
    slug: str

def _http(e: DeployError) -> HTTPException:
    return HTTPException(e.status, str(e))

@router.post("")
async def create_app(request: Request, body: AppIn):
    try:
        return await app_service.create_app_project(
            request.app.state.session_factory, name=body.name, slug=body.slug,
            scale=body.scale, stack=body.stack)
    except DeployError as e:
        raise _http(e)

@router.post("/validate-slug")
async def validate(request: Request, body: SlugIn):
    try:
        validate_slug(body.slug)
    except DeployError:
        return {"ok": False, "suggest": suggest_slug(body.slug.replace(" ", "").lower(), set())}
    taken = {a["slug"] for a in await app_service.list_apps(request.app.state.session_factory)}
    if body.slug in taken:
        return {"ok": False, "suggest": suggest_slug(body.slug, taken)}
    return {"ok": True}

@router.get("")
async def list_all(request: Request):
    return await app_service.list_apps(request.app.state.session_factory)

@router.get("/{app_id}")
async def detail(request: Request, app_id: int):
    try:
        return await app_service.get_app(request.app.state.session_factory, app_id)
    except DeployError as e:
        raise _http(e)
```

`app/main.py` 修改（在 `app.include_router(models.router)` 之后加一行）:

```python
    from app.api import apps as apps_api
    app.include_router(apps_api.router)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_apps_api.py -v`
Expected: 3 passed（依赖 Task 5/10；测试库与开发库共用 apps_root 时注意 _clean 已覆盖）

- [ ] **Step 5: Commit**

```bash
git add app/api/apps.py app/main.py tests/test_apps_api.py
git commit -m "feat(deploy): /api/apps REST（创建/校验/列表/详情）"
```

---

### Task 7: 写保护 — app.json 与 deploy/** 禁改

**Files:**
- Create: `app/deploy/guard.py`
- Modify: `app/agent/tools.py`（write_file/edit_file 两个分支入口）、`app/api/files.py`（files_save）
- Test: `tests/test_guard.py`

**Interfaces:**
- Produces: `guard.is_protected(root: Path, rel: str) -> bool` —— `root` 下存在 `app.json` 且 rel（posix 规范化）为 `app.json` 或以 `deploy/` 开头时为 True。
- 行为：AI 工具抛 `ToolError("app.json 与 deploy/ 由平台管理，AI 不可修改")`；UI 保存返回 403 同文案。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_guard.py
from pathlib import Path
import pytest
from app.deploy.guard import is_protected
from app.agent.tools import ToolContext, ToolError, execute_tool

def test_is_protected(tmp_path):
    (tmp_path / "app.json").write_text("{}", encoding="utf-8")
    assert is_protected(tmp_path, "app.json")
    assert is_protected(tmp_path, "deploy/.env")
    assert is_protected(tmp_path, "deploy\\dev-compose.yml")   # Windows 反斜杠归一
    assert not is_protected(tmp_path, "backend/app/main.py")
    plain = tmp_path / "plain"
    plain.mkdir()
    assert not is_protected(plain, "app.json")                 # 非应用工作区不保护

@pytest.mark.asyncio
async def test_write_tool_rejected(tmp_path):
    (tmp_path / "app.json").write_text("{}", encoding="utf-8")
    ctx = ToolContext(root=tmp_path, session_id=1, session_factory=None, send=None)
    with pytest.raises(ToolError):
        await execute_tool(ctx, "write_file", {"path": "deploy/.env", "content": "x"})
    with pytest.raises(ToolError):
        await execute_tool(ctx, "edit_file", {"path": "app.json", "old_string": "{", "new_string": "{"})
    # 非保护区正常
    r = await execute_tool(ctx, "write_file", {"path": "backend/x.py", "content": "ok"})
    assert r["ok"] is True
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_guard.py -v`
Expected: FAIL（ModuleNotFoundError: app.deploy.guard；edit_file 用例会因匹配不到而以别的错误通过防护外逻辑）

- [ ] **Step 3: 实现**

```python
# app/deploy/guard.py
"""平台保留区写保护（规格 01 §4.2 / 02 FR2-05）：app.json 与 deploy/** 平台唯一写入方。"""
from pathlib import Path

def is_protected(root: Path, rel: str) -> bool:
    if not (root / "app.json").is_file():
        return False
    norm = str(rel).replace("\\", "/").lstrip("/")
    return norm == "app.json" or norm.startswith("deploy/")
```

`app/agent/tools.py` 修改 —— 在 `if name == "write_file":` 分支 `f = resolve_in_workspace(...)` 之后、目录检查之前插入：

```python
        from app.deploy.guard import is_protected
        if is_protected(Path(ctx.root), rel):
            raise ToolError("app.json 与 deploy/ 由平台管理，AI 不可修改")
```

在 `if name == "edit_file":` 分支 `f = resolve_in_workspace(...)` 之后插入同样三行。

`app/api/files.py` 的 `files_save` 中 `f = resolve_in_workspace(root, body.path)` 之后插入：

```python
    from app.deploy.guard import is_protected
    if is_protected(root, body.path):
        raise HTTPException(403, "app.json 与 deploy/ 由平台管理，请在应用页操作")
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_guard.py tests/test_agent_tools.py -v`（v1 既有工具测试文件名以 tests/ 实际为准；跑全量 `python -m pytest tests -x` 亦可）
Expected: 全部 passed

- [ ] **Step 5: Commit**

```bash
git add app/deploy/guard.py app/agent/tools.py app/api/files.py tests/test_guard.py
git commit -m "feat(deploy): app.json 与 deploy/** 平台保留区写保护"
```

---

### Task 8: 应用契约注入（prompts + loop）

**Files:**
- Create: `app/deploy/contract.py`
- Modify: `app/agent/prompts.py`（build_system_prompt 增加可选参数）、`app/agent/loop.py`（_build_messages 注入）
- Test: `tests/test_contract.py`

**Interfaces:**
- Consumes: `app_service.load_app_json`（Task 5）。
- Produces: `contract.build_app_contract(root: Path) -> str | None`（root/app.json 存在且可解析 → 契约文本；否则 None）；`prompts.build_system_prompt(root: str, date: str, app_contract: str | None = None)`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_contract.py
import json
from pathlib import Path
from app.deploy.contract import build_app_contract
from app.agent.prompts import build_system_prompt

def test_contract_present(tmp_path):
    meta = {"schema": "zc-app/1", "name": "来料登记", "slug": "lailiao", "scale": "small",
            "stack": "vue3-fastapi", "ports": {"prod": 8210, "dev": 8211},
            "database": {"dev": {"name": "lailiao_dev", "user": "lailiao_dev"},
                         "prod": {"name": "lailiao_prod", "user": "lailiao_prod"}},
            "health": {"frontend": "/", "backend": "/api/health"}}
    (tmp_path / "app.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    c = build_app_contract(tmp_path)
    assert c and "lailiao" in c and "8211" in c and "sqlchange" in c and "deploy/" in c
    p = build_system_prompt(str(tmp_path), "2026-09-18", app_contract=c)
    assert "【应用契约】" in p

def test_contract_absent(tmp_path):
    assert build_app_contract(tmp_path) is None
    p = build_system_prompt(str(tmp_path), "2026-09-18")
    assert "【应用契约】" not in p

def test_contract_broken_json(tmp_path):
    (tmp_path / "app.json").write_text("{oops", encoding="utf-8")
    assert build_app_contract(tmp_path) is None   # 损坏不注入，不抛异常
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_contract.py -v`
Expected: FAIL（ModuleNotFoundError: app.deploy.contract）

- [ ] **Step 3: 实现**

```python
# app/deploy/contract.py
"""应用契约（规格 02 §5）：激活应用工作区时注入 AI 系统提示词的规范文本。"""
import json, logging
from pathlib import Path

logger = logging.getLogger(__name__)

STACK_LABEL = {"vue3-fastapi": "Vue3 + FastAPI", "vue3-node": "Vue3 + Express",
               "static": "纯前端静态页", "vue3-springboot": "Vue3 + Spring Boot 3"}
ENV_HINT = {"vue3-fastapi": "DATABASE_URL（SQLAlchemy asyncpg 连接串）与 APP_PORT",
            "vue3-node": "DATABASE_URL（pg 连接串）与 APP_PORT",
            "vue3-springboot": "SPRING_DATASOURCE_URL / SPRING_DATASOURCE_USERNAME / SPRING_DATASOURCE_PASSWORD / SERVER_PORT",
            "static": "（无后端）"}

def build_app_contract(root: Path) -> str | None:
    f = root / "app.json"
    if not f.is_file():
        return None
    try:
        meta = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("app.json 解析失败，契约未注入: %s", f)
        return None
    stack = meta.get("stack", "")
    ports = meta.get("ports", {})
    return (
        f"\n\n【应用契约】当前工作区是 zerocode 应用工程「{meta.get('name')}」（{meta.get('slug')}，"
        f"{STACK_LABEL.get(stack, stack)}，dev 预览端口 {ports.get('dev')}）。\n"
        "1) 目录：frontend/（Vue3+ant-design-vue）、backend/（后端工程，static 栈无）、sqlchange/、deploy/。\n"
        "2) 技术栈与版本以现有脚手架为准，只能追加依赖，不得替换或降级已锁定版本。\n"
        f"3) 一切数据库结构或数据变更必须写成 sqlchange/V<YYYYMMDD>_<两位序号>_<说明>.sql"
        "（写前查看目录取下一个序号；一个文件一个逻辑变更；文件头注明用途与影响表；禁止改写已存在的迁移文件）；"
        "代码里不得出现建表/改表语句，不得硬编码数据库连接。\n"
        f"4) 后端从环境变量读取 {ENV_HINT.get(stack, '平台注入的环境变量')}；必须保持 /api 前缀与 health 接口"
        f"（{meta.get('health', {}).get('backend', '')}）可用。\n"
        "5) 前端页面用 ant-design-vue，中文界面，表格用 columns+bodyCell 模式，弹窗表单 label 宽 120px 右对齐。\n"
        "6) deploy/ 与 app.json 由平台管理，禁止读写。\n"
        "7) 完成功能后告知用户：可点「预览」查看效果，正式使用请点「发布」。")
```

`app/agent/prompts.py` 修改签名与拼接：

```python
def build_system_prompt(root: str, date: str, app_contract: str | None = None) -> str:
    text = (
        f"你是 zerocode 编程助手，运行在本地 Web 应用中。今天是 {date}。\n"
        f"当前工作区根目录：{root}\n"
        "规则：\n"
        "1. 所有文件操作必须使用提供的工具，路径一律为相对该根目录的相对路径。\n"
        "2. 修改已有文件优先用 edit_file（old_string 必须唯一）；新文件或整体重写用 write_file。\n"
        "3. 动手前先 list_dir/read_file 了解现状；改完简要说明改了什么。\n"
        "4. 需求含糊、有多种合理方案或信息缺失时，用 ask_user 向用户提问，不要擅自假设。\n"
        "5. 生成代码请完整可运行，遵循目标项目已有风格。\n"
        "6. 多步任务先用 todo_write 建立任务清单（pending/in_progress/completed），随做随更、全量提交。\n"
        "7. 用户消息中的 @\"path\" 已内联为 <file> 块（大文件仅给出路径提示，用 read_file 分段读取）。\n"
        "8. 上下文可能被压缩：system 尾部 <context_summary> 是更早对话的摘要，据此衔接工作。")
    if app_contract:
        text += app_contract
    return text
```

`app/agent/loop.py` 的 `_build_messages` 中 `system_text = build_system_prompt(...)` 一行替换为：

```python
        from app.deploy.contract import build_app_contract
        system_text = build_system_prompt(str(root), datetime.date.today().isoformat(),
                                          app_contract=build_app_contract(root))
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_contract.py tests/test_contract_prompts.py -v 2>/dev/null || python -m pytest tests/test_contract.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add app/deploy/contract.py app/agent/prompts.py app/agent/loop.py tests/test_contract.py
git commit -m "feat(deploy): 应用契约注入 AI 系统提示词"
```

---

### Task 9: sqlrunner 镜像与执行封装

**Files:**
- Create: `app/deploy/sqlrunner/Dockerfile`、`app/deploy/sqlrunner/run.sh`
- Create: `app/deploy/sqlrun.py`
- Test: `tests/test_sqlrunner.py`

**Interfaces:**
- Consumes: Task 3 `provision`（建试验库）。
- Produces:
  - 镜像 `zerocode-sqlrunner:1`（postgres:16-alpine + /run.sh；输入 env：`TARGET_HOST`(默认 zerocode-pg16)/`TARGET_DB`/`TARGET_USER`/`TARGET_PASSWORD`；挂载 `/sql:ro`；退出码 0 成功 / 2 迁移失败 / 3 篡改）
  - `sqlrun.build_if_missing() -> None`（docker image inspect 失败则 build）
  - `sqlrun.run_sqlchange(app_dir: Path, env: str, cred: dict, on_log=None) -> dict`：`{ok, code, log}`；挂载 `<app_dir>/sqlchange`；网络 `zcnet`

- [ ] **Step 1: 写 run.sh 与 Dockerfile**

```sh
# app/deploy/sqlrunner/run.sh —— 规格 07 §4.3 执行算法
#!/bin/sh
set -u
: "${TARGET_DB:?TARGET_DB required}"
: "${TARGET_USER:?TARGET_USER required}"
export PGPASSWORD="${TARGET_PASSWORD}"
HOST="${TARGET_HOST:-zerocode-pg16}"
PSQL="psql -h $HOST -U $TARGET_USER -d $TARGET_DB -v ON_ERROR_STOP=1"

$PSQL -q -c "CREATE TABLE IF NOT EXISTS sqlchange_history (
  filename VARCHAR(200) PRIMARY KEY,
  checksum VARCHAR(32),
  success BOOLEAN NOT NULL,
  error TEXT,
  duration_ms INT,
  applied_at TIMESTAMPTZ NOT NULL DEFAULT now())" || exit 2

for f in $(ls /sql/V*.sql 2>/dev/null | sort); do
  base=$(basename "$f")
  sum=$(md5sum "$f" | cut -d' ' -f1)
  row=$($PSQL -tA -c "SELECT success::text || '|' || COALESCE(checksum,'') FROM sqlchange_history WHERE filename='$base'")
  if [ -n "$row" ]; then
    ok=${row%%|*}; oldsum=${row#*|}
    if [ "$ok" = "t" ]; then
      if [ -n "$oldsum" ] && [ "$oldsum" != "$sum" ]; then
        echo "TAMPER $base: 已应用文件被修改，拒绝继续（恢复原文件或人工清理 history）"
        exit 3
      fi
      echo "SKIP $base"
      continue
    fi
  fi
  start=$(date +%s)
  if $PSQL --single-transaction -q -f "$f"; then
    dur=$(( ($(date +%s) - start) * 1000 ))
    $PSQL -q -c "INSERT INTO sqlchange_history (filename, checksum, success, duration_ms)
      VALUES ('$base', '$sum', true, $dur)
      ON CONFLICT (filename) DO UPDATE SET checksum=EXCLUDED.checksum, success=true, error=NULL, applied_at=now()"
    echo "OK $base"
  else
    $PSQL -q -c "INSERT INTO sqlchange_history (filename, checksum, success, error)
      VALUES ('$base', '$sum', false, 'execution failed')
      ON CONFLICT (filename) DO UPDATE SET checksum=EXCLUDED.checksum, success=false, error='execution failed', applied_at=now()"
    echo "FAIL $base"
    exit 2
  fi
done
echo "ALL DONE"
exit 0
```

```dockerfile
# app/deploy/sqlrunner/Dockerfile
FROM postgres:16-alpine
COPY run.sh /run.sh
RUN chmod +x /run.sh
ENTRYPOINT ["/run.sh"]
```

> 注：filename 经平台/AI 命名规范约束（`V<YYYYMMDD>_<nn>_<说明>.sql`，写入 preflight 校验在 M2 发布线），此处 shell 注入面与平台自身文件系统一致，可接受；`--single-transaction` 下禁用 `CREATE INDEX CONCURRENTLY`（契约第 3 条已注明用普通 CREATE INDEX）。

- [ ] **Step 2: 写失败测试**（docker 守卫；真实 PG + 真实镜像构建）

```python
# tests/test_sqlrunner.py
import shutil
import pytest
from pathlib import Path
from app.db import make_engine, make_session_factory
from app.deploy import provision, sqlrun

pytestmark = [pytest.mark.asyncio,
              pytest.mark.skipif(not sqlrun.docker_available(), reason="docker 不可用")]

SLUG = "zctask9"
SQL_DIR = Path(f"D:/__zc_task9__/{SLUG}/sqlchange")

GOOD1 = """-- V20260918_01_init.sql
-- 用途：t1 表  影响：t1
CREATE TABLE IF NOT EXISTS t1 (id INT);
"""
GOOD2 = """-- V20260918_02_add.sql
-- 用途：t1 加列  影响：t1
ALTER TABLE t1 ADD COLUMN IF NOT EXISTS name VARCHAR(50);
"""
BAD = """-- V20260918_03_bad.sql
-- 用途：故意失败  影响：t1
THIS IS NOT SQL;
"""

def _write(files):
    shutil.rmtree(SQL_DIR.parent, ignore_errors=True)
    SQL_DIR.mkdir(parents=True)
    for name, text in files.items():
        (SQL_DIR / name).write_text(text, encoding="utf-8", newline="")

@pytest.fixture(scope="module", autouse=True)
async def _image():
    await sqlrun.build_if_missing()

@pytest.fixture(autouse=True)
async def _db():
    sf = make_session_factory(make_engine())
    await provision.drop_env(sf, SLUG, "dev")
    cred = await provision.provision_env(sf, SLUG, "dev")
    yield cred
    await provision.drop_env(sf, SLUG, "dev")
    shutil.rmtree(SQL_DIR.parent, ignore_errors=True)

async def _apply(cred):
    return await sqlrun.run_sqlchange(SQL_DIR.parent, "dev", cred)

async def test_apply_skip_and_history(cred):
    _write({"V20260918_01_init.sql": GOOD1, "V20260918_02_add.sql": GOOD2})
    r = await _apply(cred)
    assert r["ok"] is True and "OK V20260918_01_init.sql" in r["log"] and "OK V20260918_02_add.sql" in r["log"]
    r2 = await _apply(cred)
    assert r2["ok"] is True and "SKIP" in r2["log"] and "OK V20260918_01" not in r2["log"]
    # 历史表可查
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy import text
    eng = create_async_engine(
        f"postgresql+asyncpg://{cred['user']}:{cred['password']}@localhost:5433/{cred['db']}")
    async with eng.connect() as c:
        rows = (await c.execute(text("SELECT filename, success FROM sqlchange_history ORDER BY filename"))).fetchall()
    await eng.dispose()
    assert [tuple(r) for r in rows] == [("V20260918_01_init.sql", True), ("V20260918_02_add.sql", True)]

async def test_failure_exit2_and_reapply_after_fix(cred):
    _write({"V20260918_01_init.sql": GOOD1, "V20260918_03_bad.sql": BAD})
    r = await _apply(cred)
    assert r["ok"] is False and r["code"] == 2 and "FAIL V20260918_03_bad.sql" in r["log"]
    _write({"V20260918_01_init.sql": GOOD1, "V20260918_03_bad.sql": GOOD2.replace("02_add", "03_bad").replace("-- V20260918_02_add.sql", "-- V20260918_03_bad.sql")})
    r2 = await _apply(cred)
    assert r2["ok"] is True  # 失败文件修复后补跑成功，01 不重跑

async def test_tamper_exit3(cred):
    _write({"V20260918_01_init.sql": GOOD1})
    assert (await _apply(cred))["ok"] is True
    _write({"V20260918_01_init.sql": GOOD1 + "\n-- 改过一行\nSELECT 1;\n"})
    r = await _apply(cred)
    assert r["ok"] is False and r["code"] == 3 and "TAMPER" in r["log"]

async def test_empty_dir_ok(cred):
    _write({})
    r = await _apply(cred)
    assert r["ok"] is True
```

- [ ] **Step 3: 运行测试确认失败**

Run: `python -m pytest tests/test_sqlrunner.py -v`
Expected: FAIL（ModuleNotFoundError: app.deploy.sqlrun）

- [ ] **Step 4: 实现 sqlrun.py**

```python
# app/deploy/sqlrun.py
"""sqlrunner 镜像构建与一次性执行（规格 07 §4.3）。退出码：0 成功 / 2 迁移失败 / 3 篡改。"""
import asyncio, os
from pathlib import Path
from app.deploy.provision import ensure_zcnet

IMAGE = "zerocode-sqlrunner:1"
SQLRUNNER_DIR = Path(__file__).resolve().parent / "sqlrunner"

def docker_available() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    async def _check():
        p = await asyncio.create_subprocess_exec(
            "docker", "info", stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        return await p.wait() == 0
    try:
        return asyncio.run(_check())
    except Exception:
        return False

async def _docker(*args, cwd=None, on_line=None, timeout=600) -> int:
    p = await asyncio.create_subprocess_exec(
        "docker", *args, cwd=cwd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    lines: list[str] = []
    async def _pump():
        assert p.stdout
        while raw := await p.stdout.readline():
            line = raw.decode(errors="replace").rstrip()
            lines.append(line)
            if on_line:
                await on_line(line) if asyncio.iscoroutinefunction(on_line) else on_line(line)
    pump = asyncio.create_task(_pump())
    code = await asyncio.wait_for(p.wait(), timeout)
    pump.cancel()
    return code, "\n".join(lines[-200:])

async def build_if_missing() -> None:
    code, _ = await _docker("image", "inspect", IMAGE)
    if code == 0:
        return
    code, log = await _docker("build", "-t", IMAGE, ".", cwd=str(SQLRUNNER_DIR))
    if code != 0:
        raise RuntimeError(f"sqlrunner 镜像构建失败:\n{log}")

async def run_sqlchange(app_dir: Path, env: str, cred: dict, on_log=None) -> dict:
    """对 <app_dir>/sqlchange 执行一次性迁移容器；cred 为 provision 返回 {db,user,password}。"""
    await ensure_zcnet()
    sql_dir = app_dir / "sqlchange"
    code, log = await _docker(
        "run", "--rm", "--network", "zcnet",
        "-e", f"TARGET_DB={cred['db']}",
        "-e", f"TARGET_USER={cred['user']}",
        "-e", f"TARGET_PASSWORD={cred['password']}",
        "-v", f"{sql_dir}:/sql:ro",
        IMAGE, on_line=on_log)
    return {"ok": code == 0, "code": code, "log": log}
```

> 实现注意 1：`_docker` 返回 `(code, log)` 元组，`build_if_missing` 处解包；上面代码已是元组语义，检查 `code, _ = await _docker(...)`。Windows 下 `-v` 挂载路径传 `D:\...` 形式（Docker Desktop 自动转换，`str(sql_dir)` 原样传入即可）。实现注意 2：`docker_available()` 里 `_check` 不依赖外层 loop，`asyncio.run` 独立事件循环即可（若在已有 loop 内调用会抛 `RuntimeError`，已被 `except Exception` 兜住返回 False——仅用于 pytest.mark.skipif 的收集期判定，收集期无 loop，路径正确）。

- [ ] **Step 5: 运行测试确认通过**

Run: `python -m pytest tests/test_sqlrunner.py -v`
Expected: 4 passed（首次跑会构建镜像，约 10-30s）

- [ ] **Step 6: Commit**

```bash
git add app/deploy/sqlrunner app/deploy/sqlrun.py tests/test_sqlrunner.py
git commit -m "feat(deploy): sqlrunner 镜像（增量迁移/篡改检测）与执行封装"
```

---

### Task 10: dev compose 渲染与 deploy/ 落盘

**Files:**
- Create: `app/deploy/compose.py`
- Test: `tests/test_compose_render.py`

**Interfaces:**
- Consumes: Task 3 `provision.ensure_zcnet`（不直接用；渲染纯文本无 IO 依赖）。
- Produces:
  - `compose.ensure_deploy_files(app_root: Path, app_meta: dict, creds: dict) -> None`：写 `deploy/dev-compose.yml` 与 `deploy/.env`（`creds = {"dev": {...}}`；`PROD_PASSWORD` 留空串，M2 发布时补）；幂等（内容变化才重写）
  - `compose.render_dev_compose(app_meta: dict) -> str`、`compose.render_env(app_meta, creds) -> str`
  - compose 常量：项目名 `zc-<slug>-dev`（yaml `name:`）；static 栈仅 frontend 服务；`${DEV_*}` 由 compose 从 `deploy/.env` 自动插值（与 Task 9 sqlrunner 的 `TARGET_DB: ${DEV_DB}` 映射对接）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_compose_render.py
import pytest
from app.deploy import compose

META = {"schema": "zc-app/1", "name": "来料登记", "slug": "lailiao", "scale": "small",
        "stack": "vue3-fastapi", "ports": {"prod": 8210, "dev": 8211},
        "database": {"dev": {"name": "lailiao_dev", "user": "lailiao_dev"},
                     "prod": {"name": "lailiao_prod", "user": "lailiao_prod"}},
        "health": {"frontend": "/", "backend": "/api/health"}}
CRED = {"dev": {"db": "lailiao_dev", "user": "lailiao_dev", "password": "pw123"}}

def test_render_dev_compose_fastapi():
    y = compose.render_dev_compose(META)
    assert "name: zc-lailiao-dev" in y
    for svc in ("migrate:", "backend:", "frontend:"):
        assert svc in y
    assert "zerocode-sqlrunner:1" in y
    assert "TARGET_DB: ${DEV_DB}" in y and "TARGET_USER: ${DEV_USER}" in y
    assert "8211:5173" in y                      # 仅前端端口对外（规格 05 FR5-06）
    assert "zcnet" in y and "external: true" in y
    assert "service_completed_successfully" in y  # 迁移不过应用不起（规格 07 FR7-04）
    assert "${{DEV_DB}}" not in y                 # 渲染器不得吞掉 compose 插值语法

def test_render_dev_compose_static():
    import copy
    m = copy.deepcopy(META); m["stack"] = "static"
    y = compose.render_dev_compose(m)
    assert "backend:" not in y and "migrate:" not in y and "frontend:" in y

def test_render_env():
    e = compose.render_env(META, CRED)
    assert "DEV_PORT=8211" in e and "PROD_PORT=8210" in e
    assert "DEV_DB=lailiao_dev" in e and "DEV_USER=lailiao_dev" in e and "DEV_PASSWORD=pw123" in e
    assert "PROD_PASSWORD=" in e                  # M2 发布时补齐
    assert "pw123" not in compose.render_env(META, {"dev": CRED["dev"]}).split("DEV_PASSWORD")[0]

def test_ensure_deploy_files_idempotent(tmp_path):
    (tmp_path / "deploy").mkdir()
    compose.ensure_deploy_files(tmp_path, META, CRED)
    y1 = (tmp_path / "deploy" / "dev-compose.yml").read_text(encoding="utf-8")
    compose.ensure_deploy_files(tmp_path, META, CRED)
    y2 = (tmp_path / "deploy" / "dev-compose.yml").read_text(encoding="utf-8")
    assert y1 == y2
    env1 = (tmp_path / "deploy" / ".env").read_text(encoding="utf-8")
    assert "DEV_PASSWORD=pw123" in env1
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_compose_render.py -v`
Expected: FAIL（ModuleNotFoundError: app.deploy.compose）

- [ ] **Step 3: 实现**

```python
# app/deploy/compose.py
"""dev compose 渲染（规格 03 §4.1/§4.2）。compose 的 ${VAR} 插值由 deploy/.env 提供，
渲染器用 __KEY__ 标记避免与 ${} 冲突；产出 deploy/dev-compose.yml + deploy/.env。"""
from pathlib import Path

FE_VOLUME = "fe_node_modules:/src/node_modules"

_DEV_COMPOSE = """name: zc-__SLUG__-dev
networks:
  default: {}
  zcnet:
    external: true
services:
__MIGRATE_SVC__
  backend:
    image: python:3.12-slim
    working_dir: /src
    volumes:
      - ../backend:/src
    environment:
      DATABASE_URL: postgresql+asyncpg://${DEV_USER}:${DEV_PASSWORD}@zerocode-pg16:5432/${DEV_DB}
      APP_PORT: "8000"
    command: sh -c "pip install -r requirements.txt && uvicorn app.main:app --reload --host 0.0.0.0 --port 8000"
    networks: [default, zcnet]
    depends_on:
      migrate:
        condition: service_completed_successfully
  frontend:
    image: node:20-alpine
    working_dir: /src
    volumes:
      - ../frontend:/src
      - __FE_VOL__
    command: sh -c "npm install && npm run dev -- --host 0.0.0.0 --port 5173"
    ports:
      - "__DEV_PORT__:5173"
    depends_on:
      - backend
"""

_MIGRATE_SVC = """  migrate:
    image: zerocode-sqlrunner:1
    environment:
      TARGET_DB: ${DEV_DB}
      TARGET_USER: ${DEV_USER}
      TARGET_PASSWORD: ${DEV_PASSWORD}
    volumes:
      - ../sqlchange:/sql:ro
    networks: [zcnet]
    restart: "no"
"""

_FE_ONLY = """name: zc-__SLUG__-dev
services:
  frontend:
    image: node:20-alpine
    working_dir: /src
    volumes:
      - ../frontend:/src
      - __FE_VOL__
    command: sh -c "npm install && npm run dev -- --host 0.0.0.0 --port 5173"
    ports:
      - "__DEV_PORT__:5173"
"""

_ENV_TMPL = """DEV_PORT=__DEV_PORT__
PROD_PORT=__PROD_PORT__
DEV_DB=__DEV_DB__
DEV_USER=__DEV_USER__
DEV_PASSWORD=__DEV_PASSWORD__
PROD_DB=__PROD_DB__
PROD_USER=__PROD_USER__
PROD_PASSWORD=__PROD_PASSWORD__
"""

def render_dev_compose(meta: dict) -> str:
    slug, dev_port = meta["slug"], meta["ports"]["dev"]
    if meta["stack"] == "static":
        return (_FE_ONLY.replace("__SLUG__", slug)
                .replace("__DEV_PORT__", str(dev_port))
                .replace("__FE_VOL__", FE_VOLUME))
    return (_DEV_COMPOSE.replace("__SLUG__", slug)
            .replace("__MIGRATE_SVC__", _MIGRATE_SVC)
            .replace("__DEV_PORT__", str(dev_port))
            .replace("__FE_VOL__", FE_VOLUME))

def render_env(meta: dict, creds: dict) -> str:
    dev = creds.get("dev") or {}
    prod = creds.get("prod") or {}
    return (_ENV_TMPL
            .replace("__DEV_PORT__", str(meta["ports"]["dev"]))
            .replace("__PROD_PORT__", str(meta["ports"]["prod"]))
            .replace("__DEV_DB__", dev.get("db", ""))
            .replace("__DEV_USER__", dev.get("user", ""))
            .replace("__DEV_PASSWORD__", dev.get("password", ""))
            .replace("__PROD_DB__", prod.get("db", ""))
            .replace("__PROD_USER__", prod.get("user", ""))
            .replace("__PROD_PASSWORD__", prod.get("password", "")))

def ensure_deploy_files(app_root: Path, meta: dict, creds: dict) -> None:
    d = app_root / "deploy"
    d.mkdir(parents=True, exist_ok=True)
    y = render_dev_compose(meta)
    e = render_env(meta, creds)
    yf, ef = d / "dev-compose.yml", d / ".env"
    if not yf.exists() or yf.read_text(encoding="utf-8") != y:
        yf.write_text(y, encoding="utf-8", newline="")
    if not ef.exists() or ef.read_text(encoding="utf-8") != e:
        ef.write_text(e, encoding="utf-8", newline="")
```

> 说明：`FE_VOLUME` 与 compose v2 `name:` 字段即项目名（`docker compose` 自动采用，无需 `-p`）；`${DEV_USER}` 内含特殊字符时 compose 自动转义。`_ENV_TMPL` 的占位替换顺序注意 `PROD_PASSWORD=` 必须在 `DEV_PASSWORD` 替换之后仍保持独立（字符串 `.replace` 按整键匹配，无前缀污染）。

- [ ] **Step 4: 运行测试确认通过 + 回跑 Task 5**

Run: `python -m pytest tests/test_compose_render.py tests/test_app_service.py -v`
Expected: 全部 passed（Task 5 的 `ensure_deploy_files` 依赖就此闭合）

- [ ] **Step 5: Commit**

```bash
git add app/deploy/compose.py tests/test_compose_render.py
git commit -m "feat(deploy): dev compose 渲染与 deploy/ 落盘"
```

---

### Task 11: dev 生命周期引擎 engine.py + 事件 hub

**Files:**
- Create: `app/deploy/hub.py`、`app/deploy/engine.py`
- Test: `tests/test_engine.py`

**Interfaces:**
- Consumes: Task 3 `provision`、Task 9 `sqlrun`、Task 10 `compose`、Task 5 `app_service.get_app`。
- Produces:
  - `hub.DeployHub`：`subscribe(app_id) -> asyncio.Queue`、`unsubscribe(app_id, q)`、`publish(app_id, event) -> None`（put_nowait 广播）
  - `engine.docker(*args, cwd=None, on_line=None, timeout=600) -> tuple[int, str]`（async subprocess 封装）
  - `engine.preflight_docker() -> None`（失败抛 DeployError 503，中文引导）
  - `engine.start_dev(sf, app: dict, notify=None) -> dict`、`engine.stop_dev(sf, app, notify=None)`、`engine.restart_dev(sf, app, notify=None)`、`engine.dev_status(sf, app) -> dict`
  - `engine.cleanup_orphans(sf, notify=None)`、`engine.stop_all_dev_on_exit(sf, cfg, notify=None)`（供 main.py lifespan 调用）
  - 状态机（写 `app_project.status_dev`）：`stopped→starting→running/error`；事件 `{"type":"dev_status","app_id":…,"status":…}`、`{"type":"dev_log","app_id":…,"line":…}`、`{"type":"migrate_result",…}`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_engine.py
import pytest
from app.deploy import engine
from app.deploy.errors import DeployError
from app.deploy.hub import DeployHub

pytestmark = pytest.mark.asyncio

class FakeRunner:
    """替身 engine.docker：按命令前缀返回脚本化结果，记录调用。"""
    def __init__(self, script=None):
        self.calls = []
        self.script = script or {}

    async def __call__(self, *args, cwd=None, on_line=None, timeout=600):
        self.calls.append(args)
        key = " ".join(args[:2])
        handler = self.script.get(key)
        if handler is None:
            return 0, ""
        code, out = handler(args)
        if on_line and out:
            for line in out.splitlines():
                (await on_line(line)) if asyncio.iscoroutinefunction(on_line) else on_line(line)
        return code, out

import asyncio  # iscoroutinefunction 引用

APP = {"id": 1, "slug": "lailiao", "stack": "vue3-fastapi",
       "ports": {"dev": 8211}, "status_dev": "stopped"}

async def test_start_dev_happy_path(monkeypatch, test_engine):
    from app.db import make_session_factory
    sf = make_session_factory(test_engine)
    # 造 app 行（复用 test_app_service 的清理策略过于重——这里直接插最小行）
    from sqlalchemy import text as t
    async with sf() as s:
        wid = (await s.execute(t("INSERT INTO workspace (name, root_path) "
                                 "VALUES ('e', 'D:/__zc_eng__') RETURNING id"))).scalar()
        row = (await s.execute(t(
            "INSERT INTO app_project (workspace_id, name, slug, scale, stack, prod_port, dev_port) "
            "VALUES (:w, 'e', 'zceng', 'small', 'vue3-fastapi', 8298, 8299) "
            "RETURNING id"), {"w": wid})).scalar()
        await s.commit()
    events = []
    hub = DeployHub()
    hub.publish = lambda aid, ev: events.append(ev)  # 直接观察发布
    fake = FakeRunner({"compose up": lambda a: (0, "Started")})
    async def fake_sql(*a, **k):
        return {"ok": True, "code": 0, "log": "ALL DONE"}
    async def fake_preflight():
        return None
    monkeypatch.setattr(engine, "docker", fake)
    monkeypatch.setattr(engine, "run_sqlchange", fake_sql)
    monkeypatch.setattr(engine, "preflight_docker", fake_preflight)
    async def fake_health(port, timeout):
        return True
    monkeypatch.setattr(engine, "wait_frontend_health", fake_health)
    app = {"id": row, "slug": "zceng", "stack": "vue3-fastapi", "ports": {"dev": 8299}}
    res = await engine.start_dev(sf, app, notify=hub.publish)
    assert res["status"] == "running"
    statuses = [e["status"] for e in events if e["type"] == "dev_status"]
    assert statuses == ["starting", "running"]
    # 状态落库
    async with sf() as s:
        v = (await s.execute(t("SELECT status_dev FROM app_project WHERE id=:i"), {"i": row})).scalar()
    assert v == "running"
    # 清理
    async with sf() as s:
        await s.execute(t("DELETE FROM app_project WHERE id=:i"), {"i": row})
        await s.execute(t("DELETE FROM workspace WHERE id=:i"), {"i": wid})
        await s.commit()

async def test_start_dev_migrate_failure(monkeypatch, test_engine):
    from app.db import make_session_factory
    sf = make_session_factory(test_engine)
    from sqlalchemy import text as t
    async with sf() as s:
        wid = (await s.execute(t("INSERT INTO workspace (name, root_path) "
                                 "VALUES ('e2', 'D:/__zc_eng2__') RETURNING id"))).scalar()
        row = (await s.execute(t(
            "INSERT INTO app_project (workspace_id, name, slug, scale, stack, prod_port, dev_port) "
            "VALUES (:w, 'e2', 'zceng2', 'small', 'vue3-fastapi', 8296, 8297) "
            "RETURNING id"), {"w": wid})).scalar()
        await s.commit()
    async def fake_sql(*a, **k):
        return {"ok": False, "code": 2, "log": "FAIL x"}
    monkeypatch.setattr(engine, "docker", FakeRunner())
    monkeypatch.setattr(engine, "run_sqlchange", fake_sql)
    app = {"id": row, "slug": "zceng2", "stack": "vue3-fastapi", "ports": {"dev": 8297}}
    with pytest.raises(DeployError):
        await engine.start_dev(sf, app)
    async with sf() as s:
        v = (await s.execute(t("SELECT status_dev FROM app_project WHERE id=:i"), {"i": row})).scalar()
    assert v == "error"
    async with sf() as s:
        await s.execute(t("DELETE FROM app_project WHERE id=:i"), {"i": row})
        await s.execute(t("DELETE FROM workspace WHERE id=:i"), {"i": wid})
        await s.commit()

async def test_deploy_hub_pubsub():
    hub = DeployHub()
    q = hub.subscribe(7)
    hub.publish(7, {"type": "dev_status", "status": "starting"})
    assert await asyncio.wait_for(q.get(), 1) == {"type": "dev_status", "status": "starting"}
    hub.unsubscribe(7, q)
    hub.publish(7, {"type": "x"})          # 已退订，不抛不收
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_engine.py -v`
Expected: FAIL（ModuleNotFoundError: app.deploy.engine）

- [ ] **Step 3: 实现 hub.py 与 engine.py**

```python
# app/deploy/hub.py
"""DeployHub：按应用分槽的事件广播（对齐 ChatHub 分槽裁定，杜绝跨应用串扰）。"""
import asyncio

class DeployHub:
    def __init__(self):
        self.subs: dict[int, list[asyncio.Queue]] = {}

    def subscribe(self, app_id: int) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self.subs.setdefault(app_id, []).append(q)
        return q

    def unsubscribe(self, app_id: int, q: asyncio.Queue) -> None:
        lst = self.subs.get(app_id)
        if lst and q in lst:
            lst.remove(q)
            if not lst:
                self.subs.pop(app_id, None)

    def publish(self, app_id: int, event: dict) -> None:
        for q in list(self.subs.get(app_id, [])):
            if not q.full():
                q.put_nowait(event)
```

```python
# app/deploy/engine.py
"""dev 环境生命周期（规格 03）：preflight → 渲染 → 迁移 → compose up → 健康等待 → 状态落库+事件。
所有 docker 交互经 async docker() 封装，便于测试替身。"""
import asyncio, logging
from pathlib import Path
import httpx
from sqlalchemy import text
from app.deploy import compose as compose_mod, sqlrun
from app.deploy.errors import DeployError
from app.deploy.provision import read_cred
from app.deploy.settings import deploy_cfg

logger = logging.getLogger(__name__)

async def docker(*args, cwd=None, on_line=None, timeout=600) -> tuple[int, str]:
    p = await asyncio.create_subprocess_exec(
        "docker", *args, cwd=cwd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    lines: list[str] = []
    async def _pump():
        assert p.stdout
        while raw := await p.stdout.readline():
            line = raw.decode(errors="replace").rstrip()
            lines.append(line)
            if on_line:
                await on_line(line) if asyncio.iscoroutinefunction(on_line) else on_line(line)
    pump = asyncio.create_task(_pump())
    try:
        code = await asyncio.wait_for(p.wait(), timeout)
    except asyncio.TimeoutError:
        p.kill()
        raise DeployError(f"docker {' '.join(args[:3])}… 超时（{timeout}s）", status=504)
    pump.cancel()
    return code, "\n".join(lines[-300:])

async def preflight_docker() -> None:
    code, log = await docker("info", "--format", "ok")
    if code != 0:
        raise DeployError(
            "Docker 未启动或不可用：请打开 Docker Desktop（设置页「部署环境」可一键启动）", status=503)

async def _app_root(sf, app: dict) -> Path:
    async with sf() as s:
        row = (await s.execute(text(
            "SELECT w.root_path FROM app_project a JOIN workspace w ON w.id = a.workspace_id "
            "WHERE a.id = :i"), {"i": app["id"]})).first()
    if not row:
        raise DeployError("应用不存在", status=404)
    return Path(row[0])

async def _set_status(sf, app_id: int, status: str) -> None:
    async with sf() as s:
        await s.execute(text(
            "UPDATE app_project SET status_dev = :st, updated_at = now() WHERE id = :i"),
            {"st": status, "i": app_id})
        await s.commit()

async def _notify(notify, app_id, event):
    if notify:
        notify(app_id, {"app_id": app_id, **event})

async def wait_frontend_health(port: int, timeout: int) -> bool:
    async with httpx.AsyncClient(timeout=2.0) as c:
        for _ in range(timeout):
            try:
                if (await c.get(f"http://127.0.0.1:{port}/")).status_code == 200:
                    return True
            except Exception:
                pass
            await asyncio.sleep(1)
    return False

async def start_dev(sf, app: dict, notify=None) -> dict:
    app_id = app["id"]
    await _notify(notify, app_id, {"type": "dev_status", "status": "starting"})
    try:
        await _set_status(sf, app_id, "starting")
        await preflight_docker()
        root = await _app_root(sf, app)
        cred = await read_cred(sf, app["slug"], "dev")
        if not cred:
            raise DeployError("dev 数据库凭据缺失，请删除应用后重建（或检查 dbcred 配置）", status=409)
        compose_mod.ensure_deploy_files(root, app, {"dev": cred})
        on_log = None
        if notify:
            async def on_log(line):
                await _notify(notify, app_id, {"type": "dev_log", "line": line})
        mig = await sqlrun.run_sqlchange(root, "dev", cred, on_log=on_log)
        await _notify(notify, app_id, {"type": "migrate_result",
                                       "env": "dev", "ok": mig["ok"], "log": mig["log"]})
        if not mig["ok"]:
            raise DeployError(f"dev 迁移失败（sqlrunner 退出码 {mig['code']}），详情见迁移日志")
        code, log = await docker(
            "compose", "-f", str(root / "deploy" / "dev-compose.yml"), "up", "-d",
            cwd=str(root), on_line=on_log)
        if code != 0:
            raise DeployError(f"dev 栈启动失败:\n{log[-800:]}")
        ok = await wait_frontend_health(app["ports"]["dev"],
                                        int((await deploy_cfg(sf)).get("deploy.health_timeout", 60)))
        if not ok:
            raise DeployError(f"dev 前端在健康等待期内未就绪（端口 {app['ports']['dev']}）")
        await _set_status(sf, app_id, "running")
        await _notify(notify, app_id, {"type": "dev_status", "status": "running"})
        return {"status": "running", "url": f"http://localhost:{app['ports']['dev']}"}
    except DeployError as e:
        await _set_status(sf, app_id, "error")
        await _notify(notify, app_id, {"type": "dev_status", "status": "error",
                                       "message": str(e)})
        raise
    except Exception as e:
        await _set_status(sf, app_id, "error")
        await _notify(notify, app_id, {"type": "dev_status", "status": "error", "message": str(e)})
        raise DeployError(f"启动预览失败: {e}") from e

async def stop_dev(sf, app: dict, notify=None) -> dict:
    root = await _app_root(sf, app)
    await _notify(notify, app["id"], {"type": "dev_status", "status": "stopping"})
    code, log = await docker("compose", "-f", str(root / "deploy" / "dev-compose.yml"), "down",
                             cwd=str(root))
    await _set_status(sf, app["id"], "stopped")
    await _notify(notify, app["id"], {"type": "dev_status", "status": "stopped"})
    return {"status": "stopped"}

async def restart_dev(sf, app: dict, notify=None) -> dict:
    await stop_dev(sf, app, notify)
    return await start_dev(sf, app, notify)

async def dev_status(sf, app: dict) -> dict:
    root = await _app_root(sf, app)
    code, log = await docker("compose", "-f", str(root / "deploy" / "dev-compose.yml"), "ps",
                             "--format", "json", cwd=str(root))
    row = await get_status_dev(sf, app["id"])
    return {"status_dev": row, "compose_ps": log if code == 0 else "",
            "url": f"http://localhost:{app['ports']['dev']}"}

async def get_status_dev(sf, app_id: int) -> str:
    async with sf() as s:
        return (await s.execute(text(
            "SELECT status_dev FROM app_project WHERE id = :i"), {"i": app_id})).scalar() or "stopped"

async def cleanup_orphans(sf, notify=None) -> None:
    """平台启动时：所有应用 dev 栈一律 down（崩溃残留清理），状态复位 stopped（规格 03 §5）。"""
    async with sf() as s:
        rows = (await s.execute(text("SELECT id, slug FROM app_project"))).fetchall()
    for app_id, slug in rows:
        try:
            await docker("compose", "-p", f"zc-{slug}-dev", "down", timeout=120)
        except Exception:
            logger.exception("清理孤儿 dev 栈失败: %s", slug)
        await _set_status(sf, app_id, "stopped")
        await _notify(notify, app_id, {"type": "dev_status", "status": "stopped"})

async def stop_all_dev_on_exit(sf, cfg: dict, notify=None) -> None:
    if not cfg.get("deploy.auto_stop_dev_on_exit", True):
        return
    async with sf() as s:
        rows = (await s.execute(text(
            "SELECT id, slug FROM app_project WHERE status_dev = 'running'"))).fetchall()
    for app_id, slug in rows:
        try:
            await docker("compose", "-p", f"zc-{slug}-dev", "down", timeout=120)
        except Exception:
            logger.exception("退出清理 dev 栈失败: %s", slug)
```

> 实现注意：`start_dev` 内 `on_log` 在无 notify 时为 None；`sqlrun.run_sqlchange(on_log=…)` 接受协程函数（Task 9 已兼容）。FakeRunner 用例中 `engine.docker` 被 monkeypatch 为可调用对象——engine 内部一律 `await docker(...)` 调用模块级名字，替换生效。

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_engine.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add app/deploy/hub.py app/deploy/engine.py tests/test_engine.py
git commit -m "feat(deploy): dev 生命周期引擎与事件广播 hub"
```

---

### Task 12: dev/sqlchange REST 端点 + WS /ws/deploy/{app_id} + lifespan 集成

**Files:**
- Modify: `app/api/apps.py`（追加 dev/sqlchange 端点）
- Create: `app/api/deploy_ws.py`
- Modify: `app/main.py`（挂 deploy_ws 路由；lifespan 启动清理/构建镜像、退出停 dev 栈、`app.state.deploy_hub`）
- Test: `tests/test_apps_dev_api.py`

**Interfaces:**
- Consumes: Task 11 engine/hub、Task 9 sqlrun、Task 5 app_service。
- Produces:
  - `POST /api/apps/{id}/dev/start|stop|restart` → 202 `{"ok": true}`（后台任务执行，进度走 WS）
  - `GET /api/apps/{id}/dev/status` → engine.dev_status 结果
  - `POST /api/apps/{id}/sqlchange/apply` → 202；`GET /api/apps/{id}/sqlchange/records?env=dev`；`GET /api/apps/{id}/sqlchange/files`
  - WS `/ws/deploy/{app_id}`：订阅 DeployHub，下发 `dev_status/dev_log/migrate_result`；连接即回一条 `{"type":"hello","app_id":…}`
  - 长任务句柄：`app.state.dev_tasks: dict[int, asyncio.Task]`（重复启动前查 active 防并发）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_apps_dev_api.py
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from app.main import create_app
from app.db import make_engine, make_session_factory
from app.deploy import engine as eng_mod

pytestmark = pytest.mark.asyncio

@pytest.fixture
async def client_and_app():
    app = create_app()
    sf = make_session_factory(make_engine())
    async with sf() as s:
        wid = (await s.execute(text("INSERT INTO workspace (name, root_path) "
                                   "VALUES ('api', 'D:/__zc_api__') RETURNING id"))).scalar()
        aid = (await s.execute(text(
            "INSERT INTO app_project (workspace_id, name, slug, scale, stack, prod_port, dev_port) "
            "VALUES (:w, 'api', 'zcapi', 'small', 'vue3-fastapi', 8294, 8295) RETURNING id"),
            {"w": wid})).scalar()
        await s.commit()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c, \
            app.router.lifespan_context(app):
        yield c, aid, app
    async with sf() as s:
        await s.execute(text("DELETE FROM app_project WHERE id=:i"), {"i": aid})
        await s.execute(text("DELETE FROM workspace WHERE id=:i"), {"i": wid})
        await s.commit()

async def test_dev_start_202_and_status(client_and_app, monkeypatch):
    client, aid, app = client_and_app
    async def fake_start(sf, a, notify=None):
        return {"status": "running", "url": "http://localhost:8295"}
    monkeypatch.setattr(eng_mod, "start_dev", fake_start)
    r = await client.post(f"/api/apps/{aid}/dev/start")
    assert r.status_code == 202
    import asyncio
    await asyncio.sleep(0.05)                      # 让后台任务落地
    r2 = await client.get(f"/api/apps/{aid}/dev/status")
    assert r2.status_code == 200 and "url" in r2.json()

async def test_sqlchange_files_and_records(client_and_app):
    client, aid, app = client_and_app
    r = await client.get(f"/api/apps/{aid}/sqlchange/files")
    assert r.status_code == 200                    # 无 sqlchange 目录时返回空列表不报错
    r2 = await client.get(f"/api/apps/{aid}/sqlchange/records?env=dev")
    assert r2.status_code == 200

async def test_ws_deploy_hello(client_and_app):
    client, aid, app = client_and_app
    from starlette.testclient import TestClient
    with TestClient(app) as tc:
        with tc.websocket_connect(f"/ws/deploy/{aid}") as ws:
            msg = ws.receive_json()
            assert msg["type"] == "hello" and msg["app_id"] == aid
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_apps_dev_api.py -v`
Expected: FAIL（dev/start 404；WS 404）

- [ ] **Step 3: 实现**

`app/api/apps.py` 追加：

```python
import asyncio
from fastapi import Request
from app.deploy import engine, sqlrun
from app.deploy.errors import DeployError
from app.services import app_service

def _spawn(request: Request, app_id: int, coro_factory) -> None:
    tasks: dict[int, asyncio.Task] = request.app.state.dev_tasks
    old = tasks.get(app_id)
    if old and not old.done():
        raise DeployError("该应用有部署任务进行中，请稍候", status=409)
    tasks[app_id] = asyncio.create_task(coro_factory())

async def _run_app(sf, app_id: int, notify, fn):
    app = await app_service.get_app(sf, app_id)
    try:
        return await fn(sf, app, notify)
    finally:
        pass

def _app_or_404(e: DeployError):
    from fastapi import HTTPException
    return HTTPException(e.status, str(e))

@router.post("/{app_id}/dev/start")
async def dev_start(request: Request, app_id: int):
    sf = request.app.state.session_factory
    hub = request.app.state.deploy_hub
    try:
        app = await app_service.get_app(sf, app_id)
    except DeployError as e:
        raise _app_or_404(e)
    _spawn(request, app_id, lambda: engine.start_dev(sf, app, notify=hub.publish))
    return {"ok": True}

@router.post("/{app_id}/dev/stop")
async def dev_stop(request: Request, app_id: int):
    sf = request.app.state.session_factory
    hub = request.app.state.deploy_hub
    try:
        app = await app_service.get_app(sf, app_id)
    except DeployError as e:
        raise _app_or_404(e)
    _spawn(request, app_id, lambda: engine.stop_dev(sf, app, notify=hub.publish))
    return {"ok": True}

@router.post("/{app_id}/dev/restart")
async def dev_restart(request: Request, app_id: int):
    sf = request.app.state.session_factory
    hub = request.app.state.deploy_hub
    try:
        app = await app_service.get_app(sf, app_id)
    except DeployError as e:
        raise _app_or_404(e)
    _spawn(request, app_id, lambda: engine.restart_dev(sf, app, notify=hub.publish))
    return {"ok": True}

@router.get("/{app_id}/dev/status")
async def dev_status(request: Request, app_id: int):
    sf = request.app.state.session_factory
    try:
        app = await app_service.get_app(sf, app_id)
        return await engine.dev_status(sf, app)
    except DeployError as e:
        raise _app_or_404(e)

@router.post("/{app_id}/sqlchange/apply")
async def sql_apply(request: Request, app_id: int):
    sf = request.app.state.session_factory
    hub = request.app.state.deploy_hub
    try:
        app = await app_service.get_app(sf, app_id)
    except DeployError as e:
        raise _app_or_404(e)
    async def _job():
        from app.deploy.provision import read_cred
        from pathlib import Path
        cred = await read_cred(sf, app["slug"], "dev")
        if not cred:
            raise DeployError("dev 凭据缺失", status=409)
        root = await engine._app_root(sf, app)   # 引擎内部助手，应用页仅此复用
        res = await sqlrun.run_sqlchange(root, "dev", cred,
                                         on_log=lambda line: hub.publish(app_id, {"type": "dev_log", "line": line}))
        hub.publish(app_id, {"type": "migrate_result", "env": "dev", **res})
    _spawn(request, app_id, _job)
    return {"ok": True}

@router.get("/{app_id}/sqlchange/records")
async def sql_records(request: Request, app_id: int, env: str = "dev"):
    from app.deploy.provision import read_cred
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy import text as t
    from app.config import database_url as _du
    if env not in ("dev", "prod"):
        raise HTTPException(400, "env 仅支持 dev/prod")
    sf = request.app.state.session_factory
    try:
        app = await app_service.get_app(sf, app_id)
    except DeployError as e:
        raise _app_or_404(e)
    cred = await read_cred(sf, app["slug"], env)
    if not cred:
        return []
    host = "localhost" if env == "dev" else "localhost"
    url = f"postgresql+asyncpg://{cred['user']}:{cred['password']}@localhost:5433/{cred['db']}"
    eng = create_async_engine(url)
    try:
        async with eng.connect() as c:
            rows = (await c.execute(t(
                "SELECT filename, success, error, duration_ms, applied_at "
                "FROM sqlchange_history ORDER BY applied_at DESC LIMIT 200"))).mappings().all()
        return [dict(r) | {"applied_at": r["applied_at"].isoformat()} for r in rows]
    except Exception:
        return []            # 库未建/历史表未建：空列表（前端友好）
    finally:
        await eng.dispose()

@router.get("/{app_id}/sqlchange/files")
async def sql_files(request: Request, app_id: int):
    sf = request.app.state.session_factory
    try:
        app = await app_service.get_app(sf, app_id)
    except DeployError as e:
        raise _app_or_404(e)
    from app.deploy.provision import read_cred
    root = await engine._app_root(sf, app)
    d = root / "sqlchange"
    files = sorted(p.name for p in d.glob("V*.sql")) if d.is_dir() else []
    cred = await read_cred(sf, app["slug"], "dev")
    applied: dict[str, dict] = {}
    if cred:
        from sqlalchemy.ext.asyncio import create_async_engine
        from sqlalchemy import text as t
        eng = create_async_engine(
            f"postgresql+asyncpg://{cred['user']}:{cred['password']}@localhost:5433/{cred['db']}")
        try:
            async with eng.connect() as c:
                rows = (await c.execute(t(
                    "SELECT filename, success FROM sqlchange_history"))).fetchall()
            applied = {r[0]: {"success": r[1]} for r in rows}
        except Exception:
            applied = {}
        finally:
            await eng.dispose()
    return [{"filename": f, "applied": f in applied, "success": applied.get(f, {}).get("success")}
            for f in files]
```

> 实现注意：`sql_records` 中未用的 `host` 变量与 `_du` 导入删除（写计划时的推导残留，落地代码不保留未用符号——v1 终审同类问题 triage 为 MUST 习惯）。`HTTPException` 已在 apps.py 顶部 import（`from fastapi import ... HTTPException`），无需重复。

`app/api/deploy_ws.py`：

```python
"""WS /ws/deploy/{app_id}：订阅 DeployHub，实时下发部署事件（规格 03/04 WS 协议）。"""
import asyncio
from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect

router = APIRouter()

@router.websocket("/ws/deploy/{app_id}")
async def deploy_ws(ws: WebSocket, app_id: int):
    await ws.accept()
    hub = ws.app.state.deploy_hub
    q = hub.subscribe(app_id)
    try:
        await ws.send_json({"type": "hello", "app_id": app_id})
        while True:
            ev = await asyncio.wait_for(q.get(), timeout=30)
            await ws.send_json(ev)
    except asyncio.TimeoutError:
        await ws.send_json({"type": "ping"})       # 保活探测；前端无需响应
    except WebSocketDisconnect:
        pass
    finally:
        hub.unsubscribe(app_id, q)
        try:
            await ws.close()
        except Exception:
            pass
```

`app/main.py` 修改三处：

```python
    # ① create_app 内 include_router 区（apps router 之后）：
    from app.api import deploy_ws
    app.include_router(deploy_ws.router)
```

```python
    # ② lifespan 启动段（watchdog task 之前）：
        app.state.deploy_hub = ChatDeployHub()
        app.state.dev_tasks = {}
        try:
            from app.deploy import engine as deploy_engine, sqlrun
            await deploy_engine.cleanup_orphans(app.state.session_factory,
                                                notify=app.state.deploy_hub.publish)
            try:
                await sqlrun.build_if_missing()
            except Exception:
                logger.exception("sqlrunner 镜像构建失败（首次预览时会重试）")
        except Exception:
            logger.exception("部署环境启动清理失败（Docker 未启动？）")
```

```python
    # ③ lifespan 退出段（engine.dispose() 之前）：
        try:
            from app.deploy import engine as deploy_engine
            from app.deploy.settings import deploy_cfg
            cfg = await deploy_cfg(app.state.session_factory)
            await deploy_engine.stop_all_dev_on_exit(app.state.session_factory, cfg,
                                                     notify=app.state.deploy_hub.publish)
        except Exception:
            logger.exception("退出清理 dev 栈失败")
```

> `ChatDeployHub` 即 `app.deploy.hub.DeployHub`（在 main.py 顶部 `from app.deploy.hub import DeployHub as ChatDeployHub` 或直接命名 `deploy_hub`，落地时统一为 `DeployHub()` 原名）。

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_apps_dev_api.py tests/test_apps_api.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add app/api/apps.py app/api/deploy_ws.py app/main.py tests/test_apps_dev_api.py
git commit -m "feat(deploy): dev 生命周期 REST、sqlchange 端点与 WS 事件流"
```

---

### Task 13: 前端最小 UI — 应用页 / 向导 / 预览抽屉

**Files:**
- Create: `frontend/src/views/AppsView.vue`
- Modify: `frontend/src/api/index.js`（追加 apps API）、`frontend/src/router/index.js`（/apps 路由）、`frontend/src/components/SideBar.vue`（footer 加「应用」入口）
- Test: `frontend/src/views/apps.test.js`（vitest：向导表单校验逻辑纯函数抽到 `frontend/src/utils/appForm.js` 并测它）

**Interfaces:**
- Consumes: Task 6/12 的 REST（`/api/apps*`）。
- Produces（对 M2/M3）: 应用页卡片提供「发布」按钮挂点（M1 置灰提示"发布功能即将上线"）；`utils/appForm.js` 导出 `slugFromName(name)`、`validateForm({name, slug, scale, stack}) -> {ok, msg}`。

- [ ] **Step 1: 追加 API 封装（`frontend/src/api/index.js` 末尾）**

```js
// ---- 部署发布套件（应用管理） ----
export const listApps = () => http.get('/apps')
export const createApp = (data) => http.post('/apps', data) // {name, slug, scale, stack}
export const validateAppSlug = (slug) => http.post('/apps/validate-slug', { slug })
export const devStart = (id) => http.post(`/apps/${id}/dev/start`)
export const devStop = (id) => http.post(`/apps/${id}/dev/stop`)
export const devRestart = (id) => http.post(`/apps/${id}/dev/restart`)
export const devStatus = (id) => http.get(`/apps/${id}/dev/status`)
export const sqlFiles = (id) => http.get(`/apps/${id}/sqlchange/files`)
```

- [ ] **Step 2: 写失败测试（纯函数）**

```js
// frontend/src/utils/appForm.test.js
import { describe, expect, it } from 'vitest'
import { slugFromName, validateForm } from './appForm'

describe('slugFromName', () => {
  it('提取小写字母数字', () => {
    expect(slugFromName('My App2')).toBe('myapp2')
  })
  it('全中文时返回空串', () => {
    expect(slugFromName('来料登记')).toBe('')
  })
})

describe('validateForm', () => {
  const base = { name: '来料登记', slug: 'lailiao', scale: 'small', stack: 'vue3-fastapi' }
  it('合法通过', () => {
    expect(validateForm(base).ok).toBe(true)
  })
  it('slug 非法', () => {
    expect(validateForm({ ...base, slug: '2ab' }).ok).toBe(false)
  })
  it('large 锁定 springboot', () => {
    expect(validateForm({ ...base, scale: 'large', stack: 'vue3-fastapi' }).ok).toBe(false)
    expect(validateForm({ ...base, scale: 'large', stack: 'vue3-springboot' }).ok).toBe(true)
  })
})
```

Run: `cd frontend && npx vitest run src/utils/appForm.test.js`
Expected: FAIL（appForm 不存在）

- [ ] **Step 3: 实现 appForm.js 与 AppsView.vue**

```js
// frontend/src/utils/appForm.js
// 应用向导表单规则（与后端 slugs.py / app_service._check_scale_stack 对齐）
export const RE = /^[a-z][a-z0-9]{1,39}$/
export const SMALL_STACKS = [
  { value: 'vue3-fastapi', label: 'Vue3 + FastAPI（Python）' },
  { value: 'vue3-node', label: 'Vue3 + Node（Express）' },
  { value: 'static', label: '纯前端静态页' },
]

export function slugFromName(name) {
  return (name || '').toLowerCase().replace(/[^a-z0-9]/g, '').slice(0, 40)
}

export function validateForm({ name, slug, scale, stack }) {
  if (!name || !name.trim()) return { ok: false, msg: '请填写应用名称' }
  if (!RE.test(slug || '')) return { ok: false, msg: 'slug 需以小写字母开头，仅含小写字母/数字（2-40 位）' }
  if (scale === 'large' && stack !== 'vue3-springboot')
    return { ok: false, msg: '大型应用锁定 Vue3 + Spring Boot 3' }
  if (!stack) return { ok: false, msg: '请选择技术栈' }
  return { ok: true, msg: '' }
}
```

`frontend/src/views/AppsView.vue`（完整组件，素雅低饱和、middle 尺寸、中文）:

```vue
<script setup>
// 应用页（M1 最小版，规格 08 FR8-01/FR8-02 的基础实现）：卡片列表 + 三步向导 + 预览抽屉
import { onMounted, onUnmounted, reactive, ref } from 'vue'
import { message } from 'ant-design-vue'
import {
  AppstoreOutlined, CloudUploadOutlined, CopyOutlined, EyeOutlined,
  PlayCircleOutlined, PoweroffOutlined, ReloadOutlined, RocketOutlined,
} from '@ant-design/icons-vue'
import {
  createApp, devRestart, devStart, devStatus, devStop, listApps, validateAppSlug,
} from '../api'
import { SMALL_STACKS, slugFromName, validateForm } from '../utils/appForm'

const apps = ref([])
const loading = ref(true)
const showWizard = ref(false)
const current = ref(0)
const preview = reactive({ open: false, app: null, url: '', status: '', nonce: 0 })
const form = reactive({ name: '', slug: '', slugTouched: false, scale: 'small', stack: 'vue3-fastapi' })
let timer = null

const scaleCards = [
  { value: 'small', title: '小型', desc: '表单/台账/查询类，选一种轻量后端' },
  { value: 'large', title: '大型', desc: '多角色/流程类业务系统，Vue3 + Spring Boot 3' },
]

async function load() {
  loading.value = true
  try {
    apps.value = (await listApps()).data
  } catch {
    message.error('应用列表加载失败')
  } finally {
    loading.value = false
  }
}
onMounted(() => { load(); timer = setInterval(load, 5000) })
onUnmounted(() => clearInterval(timer))

function onName() {
  if (!form.slugTouched) form.slug = slugFromName(form.name)
}
async function onSlug() {
  form.slugTouched = true
  if (form.slug) {
    const r = (await validateAppSlug(form.slug)).data
    if (!r.ok) message.warning(`该 slug 不可用，建议：${r.suggest}`)
  }
}
function nextFrom1() {
  if (!form.name.trim()) return message.warning('请填写应用名称')
  if (!/^[a-z][a-z0-9]{1,39}$/.test(form.slug)) return message.warning('slug 非法')
  current.value = 1
}
function nextFrom2() {
  if (form.scale === 'large') form.stack = 'vue3-springboot'
  current.value = 2
}
async function submit() {
  const v = validateForm(form)
  if (!v.ok) return message.warning(v.msg)
  try {
    await createApp({ name: form.name.trim(), slug: form.slug, scale: form.scale, stack: form.stack })
    message.success('应用已创建')
    showWizard.value = false
    Object.assign(form, { name: '', slug: '', slugTouched: false, scale: 'small', stack: 'vue3-fastapi' })
    current.value = 0
    await load()
  } catch (e) {
    message.error(e?.response?.data?.detail || '创建失败')
  }
}

const statusColor = (s) => ({
  running: '#5b8a72', stopped: '#9aa0a6', starting: '#5b7a9d',
  publishing: '#5b7a9d', error: '#b96a6a', unhealthy: '#c08a3e',
}[s || 'stopped'])
const statusText = (s) => ({
  running: '运行中', stopped: '已停止', starting: '启动中…',
  publishing: '发布中…', error: '出错了', unhealthy: '体检未通过',
}[s || 'stopped'])

async function openPreview(a) {
  preview.open = true
  preview.app = a
  await refreshPreview(a)
}
async function refreshPreview(a) {
  const st = (await devStatus(a.id)).data
  preview.status = st.status_dev
  preview.url = st.url
  preview.nonce++
}
async function toggleDev(a) {
  try {
    if (a.status_dev === 'running') {
      await devStop(a.id)
      message.success('已停止预览')
    } else {
      await devStart(a.id)
      message.info('预览启动中，稍候自动刷新')
    }
    setTimeout(load, 1200)
  } catch (e) {
    message.error(e?.response?.data?.detail || '操作失败')
  }
}
async function restartDev(a) {
  try {
    await devRestart(a.id)
    message.info('重启中…')
    setTimeout(load, 1500)
  } catch (e) {
    message.error(e?.response?.data?.detail || '重启失败')
  }
}
async function copyUrl(url) {
  await navigator.clipboard.writeText(url)
  message.success('已复制')
}
</script>

<template>
  <div class="zc-apps">
    <div class="zc-head">
      <h2><appstore-outlined /> 我的应用</h2>
      <a-space>
        <a-button @click="load"><reload-outline v-if="false" /><reload-outlined /> 刷新</a-button>
        <a-button type="primary" @click="showWizard = true"><rocket-outlined /> 新建应用</a-button>
      </a-space>
    </div>

    <a-spin v-if="loading" class="zc-spin" />
    <a-empty v-else-if="!apps.length" description="还没有应用——点「新建应用」，用一句话描述你要的软件"
             style="margin-top: 80px" />
    <div v-else class="zc-grid">
      <a-card v-for="a in apps" :key="a.id" class="zc-card">
        <template #title>
          <span class="zc-dot" :style="{ background: statusColor(a.status_dev) }" />
          {{ a.name }}
        </template>
        <template #extra>
          <a-tag>{{ a.scale === 'large' ? '大型' : '小型' }} · {{ a.stack }}</a-tag>
        </template>
        <p class="zc-line">
          预览：{{ statusText(a.status_dev) }} · 端口 {{ a.dev_port }}
          <a v-if="a.status_dev === 'running'" :href="`http://localhost:${a.dev_port}`"
             target="_blank">打开</a>
        </p>
        <p class="zc-line sub">正式发布：M2 即将上线 <cloud-upload-outlined /></p>
        <a-space wrap>
          <a-button size="middle" @click="openPreview(a)"><eye-outlined /> 预览</a-button>
          <a-button size="middle" @click="toggleDev(a)">
            <poweroff-outlined v-if="a.status_dev === 'running'" />
            <play-circle-outlined v-else />
            {{ a.status_dev === 'running' ? '停止' : '启动预览' }}
          </a-button>
          <a-button size="middle" @click="restartDev(a)">重启</a-button>
        </a-space>
      </a-card>
    </div>

    <a-modal v-model:open="showWizard" title="新建应用" :footer="null" width="560px" destroy-on-close>
      <a-steps :current="current" size="small" style="margin-bottom: 20px"
               :items="[{ title: '起名字' }, { title: '选规模' }, { title: '选技术栈' }]" />
      <div v-if="current === 0">
        <a-form layout="horizontal" :label-col="{ style: { width: '120px' }, style: { textAlign: 'right' } }">
          <a-form-item label="应用名称" required>
            <a-input v-model:value="form.name" placeholder="如：来料登记" @input="onName" />
          </a-form-item>
          <a-form-item label="应用标识" required help="小写字母开头，仅小写字母/数字，2-40 位">
            <a-input v-model:value="form.slug" placeholder="如：lailiao" @blur="onSlug" />
          </a-form-item>
        </a-form>
        <div class="zc-wizard-foot">
          <a-button type="primary" @click="nextFrom1">下一步</a-button>
        </div>
      </div>
      <div v-else-if="current === 1">
        <div v-for="s in scaleCards" :key="s.value" class="zc-pick" :class="{ on: form.scale === s.value }"
             @click="form.scale = s.value">
          <b>{{ s.title }}</b><span>{{ s.desc }}</span>
        </div>
        <div class="zc-wizard-foot">
          <a-button style="margin-right: 8px" @click="current = 0">上一步</a-button>
          <a-button type="primary" @click="nextFrom2">下一步</a-button>
        </div>
      </div>
      <div v-else>
        <a-form layout="horizontal" :label-col="{ style: { width: '120px' }, style: { textAlign: 'right' } }">
          <a-form-item label="技术栈" required>
            <a-select v-model:value="form.stack" :options="form.scale === 'large'
              ? [{ value: 'vue3-springboot', label: 'Vue3 + Spring Boot 3（默认）', disabled: false }]
              : SMALL_STACKS" />
          </a-form-item>
        </a-form>
        <p class="zc-tip">数据库将自动使用 PostgreSQL（dev / prod 两套独立库）。</p>
        <div class="zc-wizard-foot">
          <a-button style="margin-right: 8px" @click="current = 1">上一步</a-button>
          <a-button type="primary" @click="submit">创建应用</a-button>
        </div>
      </div>
    </a-modal>

    <a-drawer v-model:open="preview.open" :title="`预览 · ${preview.app?.name || ''}`" width="80%">
      <a-space style="margin-bottom: 12px">
        <a-tag :color="preview.status === 'running' ? 'green' : 'default'">{{ statusText(preview.status) }}</a-tag>
        <a-button size="middle" @click="preview.app && refreshPreview(preview.app)"><reload-outlined /> 刷新</a-button>
        <a-button size="middle" v-if="preview.url" @click="copyUrl(preview.url)">
          <copy-outlined /> 复制地址
        </a-button>
        <a-button size="middle" v-if="preview.url" target="_blank" :href="preview.url">新窗口打开</a-button>
      </a-space>
      <iframe v-if="preview.status === 'running' && preview.url"
              :key="preview.nonce" :src="preview.url"
              style="width: 100%; height: calc(100vh - 160px); border: 1px solid #e5e7eb; border-radius: 4px" />
      <a-empty v-else description="预览未启动——点卡片上的「启动预览」，或稍候刷新" />
    </a-drawer>
  </div>
</template>

<style scoped>
.zc-apps { padding: 20px 24px; height: 100%; overflow-y: auto; box-sizing: border-box; }
.zc-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.zc-head h2 { font-size: 18px; font-weight: 600; color: var(--zc-text); margin: 0; }
.zc-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 14px; }
.zc-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 8px; }
.zc-line { margin: 0 0 6px; color: var(--zc-text); font-size: 13px; }
.zc-line.sub { color: var(--zc-text-sub); }
.zc-spin { display: block; margin: 60px auto; }
.zc-pick { border: 1px solid var(--zc-border); border-radius: 6px; padding: 10px 14px; margin-bottom: 10px;
           cursor: pointer; display: flex; flex-direction: column; gap: 4px; }
.zc-pick.on { border-color: var(--zc-primary); background: rgba(91, 122, 157, 0.08); }
.zc-pick span { color: var(--zc-text-sub); font-size: 12px; }
.zc-wizard-foot { display: flex; justify-content: flex-end; margin-top: 16px; }
.zc-tip { color: var(--zc-text-sub); font-size: 12px; margin: 8px 0 0; }
</style>
```

`frontend/src/router/index.js` 修改（routes 数组追加）:

```js
  {
    path: '/apps',
    name: 'apps',
    component: () => import('../views/AppsView.vue'),
  },
```

`frontend/src/components/SideBar.vue` 的 `.zc-footer` 模板修改为两个入口：

```html
    <div class="zc-footer">
      <div class="zc-footer-row">
        <router-link to="/apps"><appstore-outlined /> 应用</router-link>
        <router-link to="/settings"><setting-outlined /> 设置</router-link>
      </div>
    </div>
```

并在 `import { DeleteOutlined, FolderOutlined, SettingOutlined } from '@ant-design/icons-vue'` 中追加 `AppstoreOutlined`；`.zc-footer` 样式追加：

```css
.zc-footer-row { display: flex; justify-content: space-between; }
```

- [ ] **Step 4: 运行测试与构建**

Run: `cd frontend && npm run test -- --run`（若无 test 脚本则 `npx vitest run`）
Expected: 含 appForm 用例全部 passed
Run: `cd frontend && npm run build`
Expected: build 成功

- [ ] **Step 5: Commit**

```bash
git add frontend/src
git commit -m "feat(deploy): 应用页/向导/预览抽屉最小 UI"
```

---

### Task 14: 端到端冒烟与文档收尾

**Files:**
- Modify: `README.md`（追加「应用（部署预览）」一节）
- Test: 手工/浏览器冒烟清单（browser-skill），无新增自动化用例

**Interfaces:**
- Consumes: 全部前序任务；真实 Docker Desktop + zerocode-pg16。
- Produces: 冒烟报告（执行台账）+ README 使用说明。

- [ ] **Step 1: 全量回归**

Run: `python -m pytest tests -v`（确认 zerocode-pg16 与 Docker Desktop 存活）
Expected: 全部 passed（v1 76+ + M1 新增）

- [ ] **Step 2: 冒烟清单**（浏览器 + API 双通道）

1. 设置页确认 PG 状态正常；`docker network inspect zcnet` 显示 zerocode-pg16 已接入。
2. 应用页向导创建「冒烟演示」/ smoke-app / 小型 / Vue3+FastAPI。
3. `apps/smoke-app/` 出现完整骨架；`app.json` 合法；`deploy/.env` 有 DEV_PASSWORD；app.json 与 .env 无密码互通（密码只在 .env 与 dbcred）。
4. 应用页「启动预览」→ 状态 stopped→starting→running；sqlrunner 顺利完成 `V…_01_init.sql`；预览抽屉 iframe 打开骨架页，health 显示"数据库已连接"。
5. 经聊天页（选 smoke-app 工作区）发「把首页标题改成 来料登记系统」→ AI edit_file 成功；预览 HMR 自动更新标题。
6. AI 尝试写 `deploy/.env` → 工具拒绝并回传提示（对话中可见 tool_result error）。
7. 让 AI 新增 `sqlchange/V…_02_notes_type.sql`（demo_notes 加列）→ 应用页「重启」→ 迁移 01 SKIP + 02 OK，页面仍正常。
8. 停止 zerocode（Ctrl+C）→ docker ps 确认 dev 栈已停；重启 zerocode → 孤儿清理执行、状态复位 stopped → 再次启动预览可用。
9. 删除 smoke-app：dev 库/角色消失、目录删除、列表清空。

- [ ] **Step 3: README 更新**

在 README.md 追加一节「应用（部署预览）」：新建应用向导 → 聊天生成 → 启动预览 → sqlchange 规则一句话说明 → 「发布（M2）即将上线」。内容简洁面向零经验用户。

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: M1 应用预览使用说明"
```

---

## Self-Review 记录（计划完成时已执行）

1. **Spec 覆盖**：M1 范围内规格条目 → 任务映射：01 FR1-01/03/05（T5/T6）、FR1-08 部分（T12 孤儿清理）✓；02 FR2-01/02/03/04（T4/T8）+ FR2-05（T7）✓；03 FR3-01/02(抽屉版)/04/05/06(log 事件)（T10/T11/T12/T13）✓；05 FR5-01/02/06（T2/T10）✓；06 FR6-01/03/04（T3）✓；07 FR7-01/02/03/05（T9/T11）✓；08 FR8-01/02 最小版（T13）✓。缺口（有意）：08 FR8-04 详情抽屉、05 FR5-03 局域网 IP、07 FR7-06 UI 呈现增强 → M2/M3。
2. **占位符扫描**：无 TBD/TODO；App.vue 的 `a-space-competer` 为刻意标注的笔误警示（模板落地写 `a-space-compact`），sql_records 的未用变量已注明落地时删除。
3. **类型一致性**：`provision_env(sf, slug, env) -> {db,user,password}` ↔ T9 `run_sqlchange(app_dir, env, cred)` ↔ T10 `render_env(meta, creds={"dev": cred})` ↔ T11 `read_cred(sf, slug, "dev")` 命名一致；事件类型 `dev_status/dev_log/migrate_result` 在 T11/T12/规格 08 §4.4 一致；端口对 `(prod, dev)` 顺序在 T2 返回与 T5 使用一致。

## 执行交接

计划已保存到 `docs/superpowers/plans/2026-09-18-zerocode-deploy-suite-m1.md`。两种执行方式：

**1. Subagent-Driven（推荐）**——每任务派发全新子代理实现 + 独立审查，任务间快速迭代（使用 subagent-driven-development 技能）

**2. Inline 执行**——本会话内按 executing-plans 分批执行 + 检查点确认

选哪种？
