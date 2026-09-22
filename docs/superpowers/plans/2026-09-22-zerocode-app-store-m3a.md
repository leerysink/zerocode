# 应用商店 M3-a（商店闭环）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让发布者把已发布的应用上架成不可变版本包，其他人在同一平台内申请、发布者审批后自动装出一份独立实例（独立代码目录 + 独立两库 + 独立端口/容器），开箱即用。

**Architecture:** 程序轴（镜像 tag）与数据轴（`sqlchange_history` 有序序列）解耦；包的锚点只取发布者 **prod 库**的成功迁移序列，安装即把该序列在全新空库上按序重放。复用 M1/M2 已有能力（provision 分库、sqlrunner 一次性迁移、compose 渲染、DeployHub 事件流），不新增 env、不改 `run.sh` 算法。

**Tech Stack:** FastAPI + SQLAlchemy(async)/asyncpg + PostgreSQL 16（容器 `zerocode-pg16`）+ Docker Compose；Vue 3 + ant-design-vue 4 + vitest；后端 pytest（`asyncio_mode=auto`）。

**规格来源:** [docs/superpowers/specs/2026-09-22-zerocode-app-store-design.md](../specs/2026-09-22-zerocode-app-store-design.md) §2~§6.3、§8、§9。本计划只做 **M3-a**：轻身份 + 上架 + 申请审批 + 安装。升级（§6.4）、fork 检测（§6.5）、影子克隆属 M3-b。

**运行前提:** Docker Desktop 运行中、`zerocode-pg16` 容器已启动（见根 README）。后端测试统一用 `.venv\Scripts\python -m pytest`（PowerShell 分隔符是 `;`，不是 `&&`）。

---

## 文件结构

| 动作 | 路径 | 职责 |
| --- | --- | --- |
| Create | `changelog/V20260922_01_marketplace.sql` | 商店 4 张表 + `app_project.origin` |
| Create | `app/store/__init__.py` | 包标记 |
| Create | `app/store/identity.py` | 轻身份 actor：建/查/昵称校验 |
| Create | `app/store/anchor.py` | 数据轴真相：读 prod `sqlchange_history`、指纹、前缀判定、md5 |
| Create | `app/store/packages.py` | 不可变包布局：排除表、写包（临时目录→原子 rename）、manifest 读写、源码 hash |
| Create | `app/store/release.py` | 上架闸门 + 落库 |
| Create | `app/store/install.py` | 安装流水线：铺源码 → 分库 → 锚点重放 → 建镜像起栈 → 绑定关系 |
| Create | `app/store/applications.py` | 申请单读写与审批状态机 |
| Create | `app/api/store.py` | REST：`/api/store/*` 与扩展 `/api/apps/{id}/release` |
| Modify | `app/main.py` | include 两个 router |
| Modify | `app/services/app_service.py:114-158` | 加 `origin` / `materialize` 接缝（商店铺源码，不再走模板） |
| Modify | `app/deploy/records.py:117-124` | `mark_interrupted` 的 kind 白名单加 `install`/`release` |
| Modify | `tests/conftest.py:26-29` | 清表清单加商店 4 张表 |
| Modify | `frontend/src/api/index.js` | 商店接口封装 |
| Modify | `frontend/src/router/index.js`、`frontend/src/components/SideBar.vue:189` | `/store` 路由与侧栏入口 |
| Create | `frontend/src/views/StoreView.vue`、`frontend/src/components/ActorNameModal.vue` | 商店页（列表/我的发布/待审批）与昵称弹窗 |
| Create | `tests/test_store_{migration,identity,anchor,packages,release,applications,install,api}.py` | 后端用例 |
| Create | `frontend/src/views/StoreView.test.js` | 前端用例 |
| Modify | `README.md`、`app/templates/vue3-fastapi/README.md`、`app/agent/prompts.py` | 迁移纪律：只追加、破坏性变更两步走 |

依赖顺序：Task 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10 → 11 → 12。Task 7/8 依赖 5+6；Task 10 依赖 2+6。

---

## Task 1: 数据库版本文件与测试清表清单

**Files:**
- Create: `changelog/V20260922_01_marketplace.sql`
- Modify: `tests/conftest.py:26-29`
- Test: `tests/test_store_migration.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_store_migration.py`：

```python
# tests/test_store_migration.py
from sqlalchemy import text


async def test_marketplace_tables_exist(test_engine):
    async with test_engine.connect() as c:
        for t in ("actor", "app_release", "release_application", "app_instance_link"):
            row = (await c.execute(text(
                "SELECT 1 FROM pg_tables WHERE tablename = :t"), {"t": t})).first()
            assert row, f"缺少表 {t}"


async def test_app_project_has_origin_column(test_engine):
    async with test_engine.connect() as c:
        row = (await c.execute(text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'app_project' AND column_name = 'origin'"))).first()
        assert row, "app_project.origin 未创建"


async def test_release_version_unique_per_slug(test_engine):
    """同一 slug 的同一版本只能有一条——包不可等的落库兜底。"""
    async with test_engine.begin() as c:
        await c.execute(text("INSERT INTO actor (nickname, token) VALUES ('t', 'tk-mig-1')"))
        aid = (await c.execute(text("SELECT id FROM actor WHERE token='tk-mig-1'"))).scalar()
        await c.execute(text(
            "INSERT INTO app_release (app_id, slug, version, title, anchor, fingerprint, created_by) "
            "VALUES (0, 'demo', 'b1', 'Demo', '[]'::jsonb, 'fp1', :a)"), {"a": aid})
        try:
            await c.execute(text(
                "INSERT INTO app_release (app_id, slug, version, title, anchor, fingerprint, created_by) "
                "VALUES (0, 'demo', 'b1', 'Demo2', '[]'::jsonb, 'fp2', :a)"), {"a": aid})
            raise AssertionError("重复 slug+version 应被 UNIQUE 拒绝")
        except Exception as e:
            assert "unique" in str(e).lower() or "duplicate" in str(e).lower()
```

> `app_release.app_id` 写 0 是故意的：本用例只验唯一约束，不想为一个假应用建行。因此 **Task 1 的建表语句里 `app_id` 不加 FK 约束**（Step 3 已按此写）。

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv\Scripts\python -m pytest tests/test_store_migration.py -v`
Expected: FAIL —`SELECT 1 FROM pg_tables WHERE tablename='actor'` 返回 None（表不存在）

- [ ] **Step 3: 写迁移 SQL**

创建 `changelog/V20260922_01_marketplace.sql`（**只新增，不动已有版本文件**；由 `app/db.run_migrations` 按文件名序自动执行）：

```sql
-- changelog/V20260922_01_marketplace.sql
-- 日期：2026-09-22  用途：应用商店 M3-a（轻身份 / 版本包 / 申请审批 / 实例绑定）
-- 影响：actor, app_release, release_application, app_instance_link, app_project

-- actor：轻身份。本期不做账号体系（规格 D8），token 走 cookie，仅用于标识申请人与实例归属，不鉴权。
CREATE TABLE IF NOT EXISTS actor (
    id           BIGSERIAL PRIMARY KEY,
    nickname     VARCHAR(50) NOT NULL,
    token        VARCHAR(64) NOT NULL UNIQUE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- app_release：不可变版本包。anchor 是有序 [{filename, checksum}]，取自来源应用的 prod 库。
-- app_id 刻意不加 FK：来源应用被删时包必须留在（已装实例的升级线、审批历史都要引用它）。
CREATE TABLE IF NOT EXISTS app_release (
    id              BIGSERIAL PRIMARY KEY,
    app_id          BIGINT NOT NULL,
    slug            VARCHAR(40) NOT NULL,
    version         VARCHAR(40) NOT NULL,
    title           VARCHAR(100) NOT NULL,
    summary         TEXT,
    stack           VARCHAR(20) NOT NULL DEFAULT 'vue3-fastapi',
    anchor          JSONB NOT NULL,
    fingerprint     VARCHAR(64) NOT NULL,
    prev_release_id BIGINT REFERENCES app_release(id),
    status          VARCHAR(20) NOT NULL DEFAULT 'published',
    created_by      BIGINT REFERENCES actor(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (slug, version)
);
CREATE INDEX IF NOT EXISTS ix_app_release_slug ON app_release(slug, id DESC);

CREATE TABLE IF NOT EXISTS release_application (
    id            BIGSERIAL PRIMARY KEY,
    release_id    BIGINT NOT NULL REFERENCES app_release(id),
    applicant_id  BIGINT NOT NULL REFERENCES actor(id),
    want_name     VARCHAR(100),
    target_app_id BIGINT REFERENCES app_project(id),
    status        VARCHAR(20) NOT NULL DEFAULT 'pending',
    reason        TEXT,
    decided_by    BIGINT REFERENCES actor(id),
    decided_at    TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_application_status ON release_application(status, id DESC);

-- app_instance_link：受管实例 ↔ 包。baseline_* 是 M3-b 做 fork 判定的基线，本一期就要写对。
CREATE TABLE IF NOT EXISTS app_instance_link (
    app_id            BIGINT PRIMARY KEY REFERENCES app_project(id) ON DELETE CASCADE,
    release_id        BIGINT NOT NULL REFERENCES app_release(id),
    channel           VARCHAR(20) NOT NULL DEFAULT 'managed',
    detach_reason     VARCHAR(40),
    baseline_manifest JSONB NOT NULL,
    baseline_hash     VARCHAR(64) NOT NULL,
    applied_fingerprint VARCHAR(64),
    owner_actor       BIGINT REFERENCES actor(id),
    installed_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_upgraded_at  TIMESTAMPTZ
);

ALTER TABLE app_project ADD COLUMN IF NOT EXISTS origin VARCHAR(60);

INSERT INTO app_config (key, value) VALUES
    ('store.enabled', 'true'),
    ('store.keep_prev', '1')
ON CONFLICT (key) DO NOTHING;
```

- [ ] **Step 4: 扩清表清单**

改 `tests/conftest.py`，把商店 4 张表并入既有 TRUNCATE（**不新开一条语句**，保持"一次清干净"的原有形状）：

```python
    async with engine.begin() as conn:
        await conn.execute(text(
            "TRUNCATE message, file_change, compaction, chat_session, workspace, app_project, "
            "model_config, deploy_record, app_instance_link, release_application, app_release, actor "
            "RESTART IDENTITY CASCADE"))
```

- [ ] **Step 5: 跑测试确认通过**

Run: `.venv\Scripts\python -m pytest tests/test_store_migration.py tests/test_migrations.py -v`
Expected: PASS（连同既有 `test_migrations.py` 一起跑，确认新文件没破坏平台自身的迁移幂等）

- [ ] **Step 6: 提交**

```bash
git add changelog/V20260922_01_marketplace.sql tests/conftest.py tests/test_store_migration.py
git commit -m "feat(store): 商店 M3-a 表结构与迁移版本文件"
```

---

## Task 2: app_service 的商店接缝（materialize / origin）

安装要走「从包铺源码」而不是「从模板渲染」，但**必须复用 `create_app_project` 已有的原子回滚**（`_rollback`：删行 + 删目录 + `drop_env`）。所以给它开一个物化钩子，而不是复制一份创建流程。

**Files:**
- Modify: `app/services/app_service.py:74-105`（`_insert_rows`）、`:114-158`（`create_app_project`）
- Test: `tests/test_store_install_seam.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_store_install_seam.py`：

```python
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
        root = _root_of(sf, app)
        assert (root / "backend" / "main.py").read_text(encoding="utf-8") == marker
        assert not (root / "frontend").exists(), "模板被渲染了，说明 materialize 没生效"
        # 2) 占位符 mapping 仍然完整下发（商店也要用同一对端口/库名）
        assert seen["mapping"]["SLUG"] == name
        assert seen["mapping"]["DEV_DB"] == f"{name}_dev"
        # 3) app.json 由平台照常生成（slug/端口/库名对齐）
        meta = app_service.load_app_json(root)
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
    slug = app["slug"]
    for env in ("dev", "prod"):
        try:
            await provision.drop_env(sf, slug, env)
        except Exception:
            pass
    await app_service.delete_app(sf, app["id"])
    import shutil
    shutil.rmtree(await _root_of(sf, app), ignore_errors=True)
```

上面 `_cleanup`/`_root_of` 是测试自己的收尾夹具，`_u()` 保证 slug 不与共享测试库里的历史行撞车。**测试必须自己清干净**（既有纪律：`app_project.slug` 与端口是 UNIQUE，残留会毒化后续用例）。

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv\Scripts\python -m pytest tests/test_store_install_seam.py -v`
Expected: FAIL —`TypeError: create_app_project() got an unexpected keyword argument 'origin'`

- [ ] **Step 3: 加参数与钩子**

`app/services/app_service.py` 的 `create_app_project` 签名与内部渲染处改为：

```python
async def create_app_project(session_factory, *, name: str, slug: str, scale: str,
                             stack: str, root: str | None = None,
                             origin: str | None = None, materialize=None) -> dict:
    """原子创建一个应用工程：任一步失败即回滚（平台记录 + 供给 + 目录），抛 DeployError。

    `materialize(root_path, mapping)`：铺源钩子。默认走栈模板渲染；商店安装传自己的闭包
    （从包 copy 源码 + 锚点 sqlchange），从而复用本函数的端口分配、app.json、原子回滚。
    `origin`：安装来源标记（`store:<release_id>`），自建应用为 NULL。
    """
```

函数体内这一行：

```python
        scaffold.render(TEMPLATES / stack, root_path, mapping)   # R-C 已保证模板目录存在
```

替换为：

```python
        render = materialize or (lambda rp, mp: scaffold.render(TEMPLATES / stack, rp, mp))
        render(root_path, mapping)   # R-C 已保证模板目录存在
```

`_insert_rows` 加 `origin` 形参并写进 INSERT：

```python
async def _insert_rows(session_factory, *, name, slug, scale, stack, root,
                       prod_port: int, dev_port: int, origin: str | None = None) -> dict:
```

```python
            row = (await s.execute(text(
                "INSERT INTO app_project (workspace_id, name, slug, scale, stack, prod_port, "
                "dev_port, origin) VALUES (:w, :n, :s, :sc, :st, :pp, :dp, :og) "
                "RETURNING id, workspace_id, name, slug, scale, stack, prod_port, dev_port, "
                "status_dev, status_prod, current_version, created_at, updated_at"),
                {"w": wid, "n": name_stripped, "s": slug, "sc": scale, "st": stack,
                 "pp": prod_port, "dp": dev_port, "og": origin})).mappings().first()
```

调用处同步传参：

```python
        app = await _insert_rows(session_factory, name=name_stripped, slug=slug, scale=scale,
                                 stack=stack, root=root_path, prod_port=prod_port,
                                 dev_port=dev_port, origin=origin)
```

- [ ] **Step 4: 跑测试确认通过（含既有回归）**

Run: `.venv\Scripts\python -m pytest tests/test_store_install_seam.py tests/test_scaffold.py tests/test_app_service.py tests/test_apps_api.py -v`
Expected: PASS。默认路径（不传 `materialize`）行为必须零变化——这是本次改造的验收线。

- [ ] **Step 5: 提交**

```bash
git add app/services/app_service.py tests/test_store_install_seam.py
git commit -m "feat(store): create_app_project 开 materialize/origin 接缝供商店安装复用"
```

---

## Task 3: 轻身份 actor 服务

**Files:**
- Create: `app/store/__init__.py`、`app/store/identity.py`
- Test: `tests/test_store_identity.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_store_identity.py`：

```python
# tests/test_store_identity.py
import pytest
from app.deploy.errors import DeployError
from app.store import identity


async def test_create_actor_makes_unique_token(test_engine):
    from app.db import make_session_factory
    sf = make_session_factory(test_engine)
    a = await identity.create_actor(sf, "里恩")
    b = await identity.create_actor(sf, "里恩")
    assert a["id"] != b["id"]
    assert a["token"] != b["token"] and len(a["token"]) >= 24


@pytest.mark.parametrize("bad", ["", "   ", "x" * 51, None])
async def test_nickname_rejected(test_engine, bad):
    from app.db import make_session_factory
    sf = make_session_factory(test_engine)
    with pytest.raises(DeployError):
        await identity.create_actor(sf, bad)


async def test_lookup_by_token_touches_last_seen(test_engine):
    from app.db import make_session_factory
    sf = make_session_factory(test_engine)
    a = await identity.create_actor(sf, "路人")
    await identity.sleep_a_bit()
    hit = await identity.by_token(sf, a["token"])
    assert hit["id"] == a["id"]
    assert hit["last_seen_at"] > a["last_seen_at"], "命中即续期，否则长期不用的人会被当成丢失"
    assert await identity.by_token(sf, "no-such-token") is None
    assert await identity.by_token(sf, None) is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv\Scripts\python -m pytest tests/test_store_identity.py -v`
Expected: FAIL —`ModuleNotFoundError: No module named 'app.store'`

- [ ] **Step 3: 实现**

```python
# app/store/__init__.py
```

```python
# app/store/identity.py
"""轻身份 actor（规格 M3-a §6.2 / D8）：昵称 + 随机 token，token 即设备标识，不鉴权。

本期刻意不做密码与登录：商店闭环只需要「谁申请的、这实例归谁」。
因此这里**不得**演化成授权依据——越权收口在 applications/install 侧按 owner_actor 与 origin 判。
"""
import datetime, secrets
from sqlalchemy import text
from app.deploy.errors import DeployError

NICK_MAX = 50


def _clean(nickname) -> str:
    s = (nickname or "").strip()
    if not s:
        raise DeployError("请留下名字")
    if len(s) > NICK_MAX:
        raise DeployError(f"名字过长（上限 {NICK_MAX} 字）")
    return s


def _row_to_dict(r) -> dict:
    d = dict(r)
    for k in ("created_at", "last_seen_at"):
        if d.get(k) is not None:
            d[k] = d[k].isoformat()
    return d


async def create_actor(session_factory, nickname: str) -> dict:
    name = _clean(nickname)
    async with session_factory() as s:
        r = (await s.execute(text(
            "INSERT INTO actor (nickname, token) VALUES (:n, :t) "
            "RETURNING id, nickname, token, created_at, last_seen_at"),
            {"n": name, "t": secrets.token_urlsafe(24)})).mappings().first()
        await s.commit()
    return _row_to_dict(r)


async def by_token(session_factory, token: str | None) -> dict | None:
    """命中即续期 last_seen_at；无 token / 未知 token 一律 None（由端点转 401）。"""
    if not token:
        return None
    async with session_factory() as s:
        r = (await s.execute(text(
            "UPDATE actor SET last_seen_at = now() WHERE token = :t "
            "RETURNING id, nickname, token, created_at, last_seen_at"),
            {"t": token})).mappings().first()
        await s.commit()
    return _row_to_dict(r) if r else None


async def sleep_a_bit() -> None:
    """测试用：制造可观测的时间差（now() 在同一语句内可能同值）。"""
    import asyncio
    await asyncio.sleep(0.02)
```

> `by_token` 用 `UPDATE ... RETURNING` 一次完成「查 + 续期」，不必先查后写；副作用是续期即写库，所以**必须在只读 GET 上也接受一次写**——这是刻意的，别改成 SELECT（那样长期不用的人会被误判为失效身份，而我们需要的是「这台设备还在用」）。

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv\Scripts\python -m pytest tests/test_store_identity.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add app/store/__init__.py app/store/identity.py tests/test_store_identity.py
git commit -m "feat(store): 轻身份 actor（昵称 + token，不鉴权）"
```

---

## Task 4: identify 端点与 cookie 会话

**Files:**
- Create: `app/api/store.py`（本任务先建 router + 两个身份相关端点）
- Modify: `app/main.py:139`（include）
- Test: `tests/test_store_api.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_store_api.py`：

```python
# tests/test_store_api.py
"""商店 REST：身份 cookie、商店列表、申请审批。Docker 不参与（安装用例见 test_store_install.py）。"""
from sqlalchemy import text


async def _sf(client):
    return client.app.state.session_factory


async def test_store_requires_identity(client):
    r = await client.get("/api/store/me")
    assert r.status_code == 401
    assert r.json()["code"] == "identity_required"


async def test_identify_sets_cookie_and_reuses_actor(client):
    r = await client.post("/api/store/identify", json={"nickname": "陌如玉的用户"})
    assert r.status_code == 200, r.text
    actor = r.json()
    assert actor["nickname"] == "陌如玉的用户"
    assert "zc_actor" in r.cookies or client.cookies.get("zc_actor")
    # 同一 cookie 再访问 → 同一个人（cookie 即设备标识，不重复建行）
    r2 = await client.get("/api/store/me")
    assert r2.status_code == 200
    assert r2.json()["id"] == actor["id"]


async def test_identify_rejects_blank(client):
    r = await client.post("/api/store/identify", json={"nickname": "  "})
    assert r.status_code == 400


async def test_releases_list_only_published(client):
    """未上架时是空列表而不是 404；withdrawn 不出现。"""
    await client.post("/api/store/identify", json={"nickname": "看客"})
    r = await client.get("/api/store/releases")
    assert r.status_code == 200
    assert r.json() == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv\Scripts\python -m pytest tests/test_store_api.py -v`
Expected: FAIL —404（路由不存在）

- [ ] **Step 3: 实现端点与身份依赖**

```python
# app/api/store.py
"""应用商店 REST（规格 M3-a §8）。DeployError.status 直接映射 HTTP 状态码，与 apps.py 同纪律。

两套前缀放一个文件：`/api/store/*` 是商店本体，`/api/apps/{id}/release` 是应用侧的上架入口，
后者复用 apps 的鉴权/闸门语境，但实现全在 app/store/*，这里仍是薄层。
"""
from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel
from sqlalchemy import text
from app.deploy.errors import DeployError
from app.store import identity

router = APIRouter(prefix="/api/store")
apps_ext = APIRouter(prefix="/api/apps")


def _http(e: DeployError):
    from fastapi import HTTPException
    return HTTPException(e.status, str(e))


class IdentifyIn(BaseModel):
    nickname: str


async def current_actor(request: Request) -> dict:
    """身份依赖：cookie 无效即 401 + code=identity_required，前端据此弹昵称框。

    401 而不是 403：本期没有任何授权语义，只是「我不知道你是谁」。
    """
    sf = request.app.state.session_factory
    actor = await identity.by_token(sf, request.cookies.get(identity.ACTOR_COOKIE))
    if actor is None:
        raise DeployError("请先留下名字", status=401)
    return actor


Actor = Depends(current_actor)


@router.post("/identify")
async def identify(request: Request, response: Response, body: IdentifyIn):
    """一次调用既建身份又下发 cookie。已带有效 cookie 时不新建（刷新页面不该多出一堆人）。"""
    sf = request.app.state.session_factory
    existing = await identity.by_token(sf, request.cookies.get(identity.ACTOR_COOKIE))
    actor = existing or await identity.create_actor(sf, body.nickname)
    if not existing:
        response.set_cookie(identity.ACTOR_COOKIE, actor["token"],
                            max_age=identity.COOKIE_MAX_AGE, httponly=True, samesite="lax")
    return {"id": actor["id"], "nickname": actor["nickname"]}


@router.get("/me")
async def me(actor: dict = Actor):
    return {"id": actor["id"], "nickname": actor["nickname"]}
```

在 `app/store/identity.py` 顶部补两个常量（测试与端点都要引用同一处）：

```python
ACTOR_COOKIE = "zc_actor"
COOKIE_MAX_AGE = 60 * 60 * 24 * 365
```

`app/main.py` 在 `app.include_router(system.router)` 之后追加：

```python
    # 应用商店（M3-a）：/api/store 与上架入口 /api/apps/{id}/release
    from app.api import store as store_api
    app.include_router(store_api.router)
    app.include_router(store_api.apps_ext)
```

- [ ] **Step 4: 给 401 带上 code**

`DeployError` 只有 `detail: str`，而测试要 `code`。在 `store.py` 里用一个异常处理器成本高（要区分端点），改走既有更简单的形状：把 `current_actor` 的抛出换成 `HTTPException`，detail 为 dict——与 M2 chat 闸门 `409 detail 是 {code, message}` 的既有约定一致（`app/api/apps.py:10` 注释已确立这条纪律）。

```python
from fastapi import HTTPException


class IdentityRequired(HTTPException):
    def __init__(self):
        super().__init__(401, {"code": "identity_required", "message": "请先留下名字"})
```

`current_actor` 内 `raise DeployError("请先留下名字", status=401)` 改为 `raise IdentityRequired()`。

- [ ] **Step 5: 跑测试确认通过**

Run: `.venv\Scripts\python -m pytest tests/test_store_api.py -v`
Expected: PASS（4 条）

- [ ] **Step 6: 提交**

```bash
git add app/api/store.py app/store/identity.py app/main.py tests/test_store_api.py
git commit -m "feat(store): identify 端点与轻身份 cookie 依赖（401 code 契约）"
```

---

## Task 5: 锚点服务 anchor（数据轴真相）

锚点是这套方案最核心的一个读数：版本的 schema 状态 = 该库 `sqlchange_history` 里 `success=true` 的**有序**文件集，**不是** `sqlchange/` 目录的文件列表。

**Files:**
- Create: `app/store/anchor.py`
- Test: `tests/test_store_anchor.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_store_anchor.py`：

```python
# tests/test_store_anchor.py
"""锚点：读 prod 库 sqlchange_history 的有序成功集 + 指纹 + 前缀 + md5 比对。"""
import hashlib
import pytest
from sqlalchemy import text
from app.store import anchor


async def _seed_history(test_engine, rows):
    async with test_engine.begin() as c:
        await c.execute(text("DROP TABLE IF EXISTS sqlchange_history"))
        await c.execute(text(
            "CREATE TABLE sqlchange_history (filename VARCHAR(200) PRIMARY KEY,"
            " checksum VARCHAR(32), success BOOLEAN NOT NULL, applied_at TIMESTAMPTZ DEFAULT now())"))
        for f, sum_, ok in rows:
            await c.execute(text("INSERT INTO sqlchange_history (filename, checksum, success) "
                                 "VALUES (:f, :s, :o)"), {"f": f, "s": sum_, "o": ok})


async def test_reads_only_success_ordered(test_engine):
    from app.db import make_session_factory  # noqa: F401  (保持与其它用例同样的取用习惯)
    url = test_engine.url.render_as_string(hide_password=False)
    await _seed_history(test_engine, [
        ("V20260920_03_c.sql", "c", True),
        ("V20260920_01_a.sql", "a", True),
        ("V20260920_02_bad.sql", "x", False),
    ])
    got = await anchor.read_history(url)
    assert [e["filename"] for e in got] == ["V20260920_01_a.sql", "V20260920_03_c.sql"]
    assert [e["checksum"] for e in got] == ["a", "c"]


async def test_missing_history_table_is_not_swallowed(test_engine):
    """表不存在必须抛，不能返 []：返 [] 会让上架闸门以为「锚点为空」而给出错误原因。"""
    url = test_engine.url.render_as_string(hide_password=False)
    async with test_engine.begin() as c:
        await c.execute(text("DROP TABLE IF EXISTS sqlchange_history"))
    with pytest.raises(Exception):
        await anchor.read_history(url)


def test_fingerprint_changes_with_checksum_only():
    a = [{"filename": "1.sql", "checksum": "x"}]
    b = [{"filename": "1.sql", "checksum": "y"}]
    assert anchor.fingerprint(a) != anchor.fingerprint(b)
    assert anchor.fingerprint(a) == anchor.fingerprint([{"filename": "1.sql", "checksum": "x"}])


def test_is_prefix():
    assert anchor.is_prefix(["a", "b"], ["a", "b", "c"])
    assert anchor.is_prefix([], ["a"])
    assert not anchor.is_prefix(["a", "c"], ["a", "b", "c"])     # 中间缺文件 = 分叉
    assert not anchor.is_prefix(["b", "a"], ["a", "b", "c"])      # 顺序不同 = 分叉


def test_mismatches_detects_edited_history_file(tmp_path):
    f = tmp_path / "sqlchange"
    f.mkdir()
    (f / "V1_01_a.sql").write_text("SELECT 1;", encoding="utf-8", newline="")
    good = [{"filename": "V1_01_a.sql", "checksum": hashlib.md5(b"SELECT 1;").hexdigest()}]
    disk = {"V1_01_a.sql": anchor.file_md5(f / "V1_01_a.sql")}
    assert anchor.mismatches(good, disk) == []
    (f / "V1_01_a.sql").write_text("SELECT 2;", encoding="utf-8", newline="")
    assert anchor.mismatches(good, {"V1_01_a.sql": anchor.file_md5(f / "V1_01_a.sql")}) == \
        ["V1_01_a.sql"]
    # 文件被删掉也算 mismatch（比 [] 更有信息量）
    assert anchor.mismatches(good, {}) == ["V1_01_a.sql（文件已不在 sqlchange/）"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv\Scripts\python -m pytest tests/test_store_anchor.py -v`
Expected: FAIL —`ModuleNotFoundError: No module named 'app.store.anchor'`

- [ ] **Step 3: 实现**

```python
# app/store/anchor.py
"""数据轴真相（规格 M3-a §2.3 / §6.1）。

锚点取自库的 sqlchange_history，而非 apps/<slug>/sqlchange/ 的目录列表：后者里混着 dev
跑过但从未发布的半途迁移，进包就等于把垃圾推给所有申请人。
"""
import hashlib
from pathlib import Path
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from app import config as app_config

HISTORY_SQL = ("SELECT filename, checksum FROM sqlchange_history WHERE success "
               "ORDER BY filename")


def app_db_url(cred: dict) -> str:
    """应用库连接串：复用平台实例的 host/port（不写死 5433），只换库名/角色/密码。

    SQLAlchemy 默认掩码密码，取串必须 hide_password=False。
    """
    base = make_url(app_config.database_url())
    return base.set(database=cred["db"], username=cred["user"],
                    password=cred["password"]).render_as_string(hide_password=False)


async def read_history(url: str) -> list[dict]:
    """不吞异常：表不存在/库不可达都要如实上抛，由上架闸门给出准确原因。"""
    eng = create_async_engine(url)
    try:
        async with eng.connect() as c:
            rows = (await c.execute(text(HISTORY_SQL))).mappings().all()
        return [{"filename": r["filename"], "checksum": r["checksum"] or ""} for r in rows]
    finally:
        await eng.dispose()


def file_md5(path) -> str:
    """与 run.sh 的 md5sum 同口径：原始字节，绝不做换行归一（CRLF 差异会误判篡改）。"""
    return hashlib.md5(Path(path).read_bytes()).hexdigest()


def names_of(anchor: list[dict]) -> list[str]:
    return [e["filename"] for e in anchor]


def fingerprint(anchor: list[dict]) -> str:
    h = hashlib.sha256()
    for e in anchor:
        h.update(f"{e['filename']}:{e['checksum']}\n".encode("utf-8"))
    return h.hexdigest()


def is_prefix(short: list[str], long: list[str]) -> bool:
    """约束 L：短序列必须是长序列的前缀（文件名升序即时间序，与 run.sh 的 ls|sort 同判定）。"""
    return list(long[:len(short)]) == list(short)


def mismatches(anchor: list[dict], on_disk: dict[str, str]) -> list[str]:
    """已应用文件在盘上被改过/删掉 → 违反纪律 1，拒绝上架（§6.1 闸门 3）。"""
    out = []
    for e in anchor:
        if e["filename"] not in on_disk:
            out.append(f"{e['filename']}（文件已不在 sqlchange/）")
        elif on_disk[e["filename"]] != e["checksum"]:
            out.append(e["filename"])
    return out
```

- [ ] **Step 4: 把既有重复实现收口到一处**

`app/api/apps.py:247-252` 的 `_app_db_url` 与上面完全同源，删掉本地实现改为委托（避免两份口径漂移）：

```python
from app.store.anchor import app_db_url as _app_db_url
```

- [ ] **Step 5: 跑测试确认通过（含 apps 回归）**

Run: `.venv\Scripts\python -m pytest tests/test_store_anchor.py tests/test_apps_dev_api.py tests/test_console_streams.py -v`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add app/store/anchor.py app/api/apps.py tests/test_store_anchor.py
git commit -m "feat(store): 锚点服务（prod history 真相 + 约束 L 前缀判定）"
```

---

## Task 6: 不可变包 packages

**Files:**
- Create: `app/store/packages.py`
- Test: `tests/test_store_packages.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_store_packages.py`：

```python
# tests/test_store_packages.py
"""包 = 物理快照（不引用活目录）+ 只读 + 同 slug+version 不可覆盖。"""
import json
import pytest
from app.deploy.errors import DeployError
from app.store import packages


@pytest.fixture
def app_tree(tmp_path):
    """造一棵带地雷的应用目录：凭据、依赖、构建产物都不许进包。"""
    root = tmp_path / "apps" / "demo"
    for rel in ("backend/app/main.py", "backend/requirements.txt",
                "frontend/src/App.vue", "frontend/node_modules/dep/index.js",
                "frontend/dist/assets/a.js", "deploy/prod-compose.yml", "deploy/.env",
                "sqlchange/V20260920_01_init.sql", "sqlchange/V20260920_02_more.sql"):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"# {rel}\n", encoding="utf-8", newline="")
    (root / "app.json").write_text(json.dumps({"slug": "demo", "stack": "vue3-fastapi"}),
                                   encoding="utf-8")
    (root / "README.md").write_text("# demo\n", encoding="utf-8")
    return root


def _anchor():
    return [{"filename": "V20260920_01_init.sql", "checksum": "aa"}]


def test_write_package_excludes_secrets_and_artifacts(tmp_path, app_tree):
    pkg = packages.write_package(
        out_root=tmp_path / "store", slug="demo", version="b1", app_root=app_tree,
        anchor=_anchor(), manifest_extra={"title": "演示", "stack": "vue3-fastapi"})
    files = {p.relative_to(pkg).as_posix() for p in pkg.rglob("*") if p.is_file()}
    assert "manifest.json" in files
    assert "sqlchange/V20260920_01_init.sql" in files
    assert "src/backend/app/main.py" in files
    assert not any(f.endswith(".env") for f in files), "凭据外泄：包里有 .env"
    assert not any("/deploy/" in f or f.startswith("deploy/") for f in files), "包里有 deploy/"
    assert not any("node_modules" in f for f in files)
    assert not any("/dist/" in f for f in files)
    assert "sqlchange/V20260920_02_more.sql" not in files, "未应用的迁移不得进包"


def test_package_is_immutable(tmp_path, app_tree):
    args = dict(out_root=tmp_path / "store", slug="demo", version="b1", app_root=app_tree,
                anchor=_anchor(), manifest_extra={"title": "演示", "stack": "vue3-fastapi"})
    first = packages.write_package(**args)
    (first / "sqlchange" / "V20260920_01_init.sql").write_text("-- 改了\n", encoding="utf-8")
    with pytest.raises(DeployError) as e:
        packages.write_package(**args)
    assert "已存在" in str(e.value)
    # 失败的第二趟不许把第一趟的包目录连带删掉
    assert first.is_dir()


def test_manifest_round_trip(tmp_path, app_tree):
    pkg = packages.write_package(
        out_root=tmp_path / "store", slug="demo", version="b1", app_root=app_tree,
        anchor=_anchor(), manifest_extra={"title": "演示", "stack": "vue3-fastapi"})
    m = packages.read_manifest(pkg)
    assert m["schema"] == "zc-release/1"
    assert m["slug"] == "demo" and m["version"] == "b1"
    assert m["anchor"] == _anchor()
    assert m["fingerprint"] == packages.manifest_fingerprint(_anchor())
    assert pkg.is_dir() and not (pkg.parent / "b1.tmp").exists(), "临时目录必须被 rename 收掉"


def test_source_hash_sensitive_to_src_only(tmp_path, app_tree):
    h1 = packages.source_hash(app_tree)
    (app_tree / "backend" / "app" / "main.py").write_text("# changed\n", encoding="utf-8")
    assert packages.source_hash(app_tree) != h1
    h2 = packages.source_hash(app_tree)
    (app_tree / "deploy" / ".env").write_text("SECRET=x\n", encoding="utf-8")
    assert packages.source_hash(app_tree) == h2, "平台生成物变化不该算作分叉"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv\Scripts\python -m pytest tests/test_store_packages.py -v`
Expected: FAIL —`ModuleNotFoundError: No module named 'app.store.packages'`

- [ ] **Step 3: 实现**

```python
# app/store/packages.py
"""不可变版本包（规格 M3-a §4）。

包必须是从未是活目录的物理 copy：上架后作者继续让 AI 改代码，不能波及已上架版本——
`run.sh` 的 checksum 闸门只保护作者自己的库，保护不了已分发出去的包。
"""
import hashlib, json, os, shutil
from datetime import datetime, timezone
from pathlib import Path
from app.deploy.errors import DeployError
from app.store import anchor as anchor_mod

SCHEMA = "zc-release/1"
# 顶层不进 src 的：平台生成物（deploy 含明文凭据）、独立进包的 sqlchange、清单与说明
EXCLUDE_TOP = {"deploy", "sqlchange", "app.json", "README.md"}
EXCLUDE_DIRS = {"node_modules", ".venv", "dist", "__pycache__", ".pytest_cache", ".vite",
                "venv", "build"}
EXCLUDE_FILES = {".env", ".DS_Store", "Thumbs.db"}


def package_path(out_root, slug: str, version: str) -> Path:
    return Path(out_root) / slug / version


def _iter_src(app_root: Path):
    """有序产出 (rel_posix, Path)：排序保证 hash 稳定，跳过规则集中在这一处。"""
    for p in sorted(app_root.rglob("*")):
        if not p.is_file():
            continue
        parts = p.relative_to(app_root).parts
        if parts[0] in EXCLUDE_TOP:
            continue
        if any(part in EXCLUDE_DIRS for part in parts):
            continue
        if parts[-1] in EXCLUDE_FILES:
            continue
        yield "/".join(parts), p


def source_hash(app_root: Path) -> str:
    """源码指纹（M3-b 的 fork 判定基线）：排除平台生成物与依赖产物，只认人的改动。"""
    h = hashlib.sha256()
    for rel, p in _iter_src(Path(app_root)):
        h.update(f"{rel}:{anchor_mod.file_md5(p)}\n".encode("utf-8"))
    return h.hexdigest()


def manifest_fingerprint(anchor: list[dict]) -> str:
    return anchor_mod.fingerprint(anchor)


def write_package(*, out_root, slug: str, version: str, app_root: Path,
                  anchor: list[dict], manifest_extra: dict) -> Path:
    """临时目录 → 校验无凭据 → 原子 rename。失败即清临时目录，绝不留半成品正式版。"""
    pkg = package_path(out_root, slug, version)
    if pkg.exists():
        raise DeployError(f"版本包已存在（{slug}@{version}），包不可覆盖", status=409)
    tmp = pkg.parent / f"{version}.tmp{os.getpid()}"
    shutil.rmtree(tmp, ignore_errors=True)
    try:
        (tmp / "src").mkdir(parents=True)
        sql_dir = tmp / "sqlchange"
        sql_dir.mkdir()
        src_sql = Path(app_root) / "sqlchange"
        for e in anchor:                       # 只拷锚点序列内的文件，逐个存在性校验
            f = src_sql / e["filename"]
            if not f.is_file():
                raise DeployError(f"锚点文件缺失: {e['filename']}")
            if anchor_mod.file_md5(f) != e["checksum"]:
                raise DeployError(f"锚点文件与库内记录不一致: {e['filename']}")
            shutil.copy2(f, sql_dir / e["filename"])
        for rel, p in _iter_src(Path(app_root)):
            dst = tmp / "src" / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, dst)
        manifest = {
            "schema": SCHEMA, "slug": slug, "version": version,
            "anchor": anchor, "fingerprint": manifest_fingerprint(anchor),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "exclude_top": sorted(EXCLUDE_TOP), "exclude_dirs": sorted(EXCLUDE_DIRS),
            **manifest_extra,
        }
        (tmp / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8", newline="")
        # 落盘前的最后一道：包内任何位置都不许出现明文凭据
        for p in tmp.rglob("*"):
            if p.is_file() and p.name in EXCLUDE_FILES:
                raise DeployError(f"包内混入敏感文件: {p.relative_to(tmp)}")
        pkg.parent.mkdir(parents=True, exist_ok=True)
        os.replace(tmp, pkg)                   # 同盘原子改名；目标存在则整体失败（前面已判）
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return pkg


def read_manifest(pkg_dir: Path) -> dict:
    f = Path(pkg_dir) / "manifest.json"
    if not f.is_file():
        raise DeployError(f"版本包缺 manifest.json: {pkg_dir}", status=500)
    return json.loads(f.read_text(encoding="utf-8"))


def package_dir_for(session_factory_row: dict, store_root) -> Path:
    """由 app_release 行定位包目录（install/upgrade 共用，别在调用方各自拼路径）。"""
    return package_path(store_root, session_factory_row["slug"], session_factory_row["version"])
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv\Scripts\python -m pytest tests/test_store_packages.py -v`
Expected: PASS（4 条）

- [ ] **Step 5: 提交**

```bash
git add app/store/applications.py app/api/store.py tests/test_store_applications.py
git commit -m "feat(store): 申请单状态机与审批端点（批准转后台安装）"
```

---

## Task 10: 安装流水线

**Files:**
- Create: `app/store/install.py`
- Modify: `app/deploy/records.py:117-124`（`mark_interrupted` 的 kind 白名单）
- Test: `tests/test_store_install_flow.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_store_install_flow.py`。前两条把 Docker 侧全桩掉，只验编排与回滚；第三条 `skipif` 真跑一次基线重放。

```python
# tests/test_store_install_flow.py
"""安装编排：铺包源码 → 分两库 → 锚点全量重放 → 自有镜像起栈 → 写绑定关系。

失败必须 teardown（删本流建的库/行/目录），但一旦进到「起栈」阶段就保留实例（不能删数据）。
"""
import json
import secrets
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from app.db import make_session_factory
from app.deploy import engine as deploy_engine
from app.deploy import prod as deploy_prod
from app.deploy import provision, sqlrun
from app.deploy.errors import DeployError
from app.store import anchor as anchor_mod, install, packages, release as release_svc

pytestmark = pytest.mark.asyncio


def _slug():
    return "ins" + secrets.token_hex(3)


@pytest.fixture
async def sf(test_engine):
    return make_session_factory(test_engine)


@pytest.fixture
def release_row(sf, tmp_path, monkeypatch):
    """直接造一个已上架版本（包落在 tmp store/），不绕 Task 7 的闸门。"""
    async def _mk(anchor=None):
        slug = _slug()
        version = "b20260922000001"
        anchor = anchor or [{"filename": "V20260920_01_init.sql", "checksum": "aa"}]
        src = tmp_path / "apps" / slug
        (src / "backend").mkdir(parents=True)
        (src / "backend" / "main.py").write_text("# hello\n", encoding="utf-8", newline="")
        (src / "frontend").mkdir()
        (src / "frontend" / "index.html").write_text("<html></html>\n", encoding="utf-8")
        (src / "sqlchange").mkdir()
        for e in anchor:
            (src / "sqlchange" / e["filename"]).write_text("SELECT 1;\n", encoding="utf-8")
        pkg_root = tmp_path / "store"
        monkeypatch.setattr(release_svc, "store_root", lambda cfg: pkg_root)
        packages.write_package(out_root=pkg_root, slug=slug, version=version, app_root=src,
                               anchor=anchor, manifest_extra={"title": "演示", "stack":
                                   "vue3-fastapi", "app_id": 0, "health": {}})
        async with sf() as s:
            rid = (await s.execute(text(
                "INSERT INTO app_release (app_id, slug, version, title, stack, anchor, "
                "fingerprint) VALUES (0, :sl, :v, '演示', 'vue3-fastapi', CAST(:a AS JSONB), :f) "
                "RETURNING id, app_id, slug, version, title, stack, anchor, fingerprint"),
                {"sl": slug, "v": version, "a": json.dumps(anchor),
                 "f": anchor_mod.fingerprint(anchor)})).mappings().first()
            await s.commit()
        return dict(rid)
    return _mk


@pytest.fixture
def stubs(monkeypatch, sf):
    """把 Docker 侧四件事换成可观察的假人；真 PG 供给不动（它只依赖 pg16 容器）。"""
    calls = {"migrate": [], "build": [], "start": []}

    async def fake_run_sqlchange(app_dir, env, cred, on_log=None):
        calls["migrate"].append((str(app_dir), env, cred["db"]))
        return {"ok": True, "code": 0, "log": "ALL DONE"}

    async def fake_docker(*args, cwd=None, on_line=None, timeout=600):
        calls["build"].append(args)
        return 0, "build ok"

    async def fake_prod_start(session_factory, app, notify=None):
        calls["start"].append(app["id"])
        return {"status": "running"}

    async def fake_image_exists(tag):
        return True

    monkeypatch.setattr(sqlrun, "run_sqlchange", fake_run_sqlchange)
    monkeypatch.setattr(deploy_engine, "docker", fake_docker)
    monkeypatch.setattr(deploy_prod, "prod_start", fake_prod_start)
    monkeypatch.setattr(deploy_prod, "image_exists", fake_image_exists)
    return calls


async def test_install_creates_instance_and_link(sf, stubs, release_row):
    from app.store import identity
    actor = await identity.create_actor(sf, "装机人")
    rel = await release_row()
    app = await install.install_release(sf, release_row=rel, applicant_actor_id=actor["id"],
                                        want_name="我的演示")
    try:
        assert app["slug"].startswith(rel["slug"]) and app["slug"] != rel["slug"]
        assert app["origin"] == f"store:{rel['id']}"
        # 铺的是包里的源码，不是栈模板（模板会带 frontend/src/App.vue 与 demo_notes 迁移）
        root = await deploy_engine._app_root(sf, app)
        assert (root / "backend" / "main.py").read_text(encoding="utf-8") == "# hello\n"
        assert (root / "sqlchange" / "V20260920_01_init.sql").is_file()
        assert not any(p.name == "App.vue" for p in root.rglob("*")), "被模板渲染了"
        # 两库都供给了，两库都重放了
        assert sorted(e for _, e, _ in stubs["migrate"]) == ["dev", "prod"]
        async with sf() as s:
            cred_prod = (await s.execute(text(
                "SELECT value FROM app_config WHERE key=:k"),
                {"k": f"dbcred.{app['slug']}.prod"})).first()
            link = (await s.execute(text(
                "SELECT release_id, channel, applied_fingerprint, owner_actor, baseline_hash, "
                "baseline_manifest FROM app_instance_link WHERE app_id=:i"),
                {"i": app["id"]})).mappings().first()
        assert cred_prod and cred_prod[0]["user"] == f"{app['slug']}_prod"
        assert link["channel"] == "managed"
        assert link["applied_fingerprint"] == rel["fingerprint"]
        assert link["owner_actor"] == actor["id"]
        assert link["baseline_hash"] and len(link["baseline_hash"]) == 64
        assert [e["filename"] for e in link["baseline_manifest"]] == \
            [e["filename"] for e in rel["anchor"]]
        # current_version 已晋升，prod_start 才能起栈
        assert app["current_version"] == rel["version"]
        assert stubs["start"] == [app["id"]]
    finally:
        await install._teardown(sf, app["id"], app["slug"])


async def test_migrate_failure_rolls_back_everything(sf, monkeypatch, release_row):
    from app.store import identity
    actor = await identity.create_actor(sf, "装机人2")
    rel = await release_row()

    async def boom(app_dir, env, cred, on_log=None):
        return {"ok": False, "code": 2, "log": "FAIL V20260920_01_init.sql"}
    monkeypatch.setattr(sqlrun, "run_sqlchange", boom)
    dropped = []
    real_drop = provision.drop_env

    async def spy_drop(session_factory, slug, env):
        dropped.append(env)
        return await real_drop(session_factory, slug, env)
    monkeypatch.setattr(provision, "drop_env", spy_drop)

    with pytest.raises(DeployError) as e:
        await install.install_release(sf, release_row=rel, applicant_actor_id=actor["id"],
                                      want_name="失败重试")
    assert "迁移" in str(e.value)
    slug = install._last_slug
    assert slug and sorted(dropped) == ["dev", "prod"], "两库都要收回去"
    async with sf() as s:
        assert not (await s.execute(text("SELECT 1 FROM app_project WHERE slug=:s"),
                                    {"s": slug})).first()
        assert not (await s.execute(text("SELECT 1 FROM app_instance_link WHERE app_id IN "
                                          "(SELECT id FROM app_project WHERE slug=:s)"),
                                    {"s": slug})).first()


@pytest.mark.skipif(not sqlrun.docker_available(), reason="需要 Docker 与 zerocode-pg16")
async def test_baseline_replay_real(sf, release_row):
    """真跑一次 sqlrunner：空库重放后，实例 prod 库的 history 必须逐字等于锚点。"""
    from app.store import identity
    actor = await identity.create_actor(sf, "真跑人")
    body = "CREATE TABLE demo_x (id serial PRIMARY KEY, t text);\n"
    import hashlib
    rel = await release_row(anchor=[{"filename": "V20260920_01_init.sql",
                                     "checksum": hashlib.md5(body.encode()).hexdigest()}])
    # 把包里的快照换成带真实 DDL 的内容（write_package 已校 checksum，这里同步重算）
    app = await install.install_release(sf, release_row=rel, applicant_actor_id=actor["id"],
                                        want_name="真跑实例")
    try:
        cred = await provision.read_cred(sf, app["slug"], "prod")
        eng = create_async_engine(anchor_mod.app_db_url(cred))
        try:
            async with eng.connect() as c:
                rows = (await c.execute(text(
                    "SELECT filename, success FROM sqlchange_history ORDER BY filename"))).all()
                tbl = (await c.execute(text(
                    "SELECT 1 FROM information_schema.tables WHERE table_name='demo_x'"))).first()
        finally:
            await eng.dispose()
        assert [r[0] for r in rows] == ["V20260920_01_init.sql"] and rows[0][1] is True
        assert tbl, "锚点没真的执行"
    finally:
        await install._teardown(sf, app["id"], app["slug"])
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv\Scripts\python -m pytest tests/test_store_install_flow.py -v`
Expected: FAIL —`ModuleNotFoundError: No module named 'app.store.install'`

- [ ] **Step 3: 实现**

```python
# app/store/install.py
"""安装（规格 M3-a §6.3）：把不可变包落成一份独立实例。

六步：铺源码 → 建平台记录与分库 → dev/prod 基线重放 → 自有镜像 build → prod 起栈体检
→ 写绑定关系。前三步失败全部 teardown（不留孤儿库/行/目录）；到了起栈阶段就**只报错不删**，
因为库里可能已经有用户数据了。

镜像不共享：必须用实例自己的 slug build（keep-5 清理与删实例都按 zc-<slug>-* 前缀匹配，
共享源 tag 会互删在服容器）。
"""
import logging
import re
from pathlib import Path
from sqlalchemy import text
from app.deploy import compose, engine as deploy_engine
from app.deploy import prod as deploy_prod, provision, records, sqlrun
from app.deploy.errors import DeployError
from app.deploy.settings import deploy_cfg
from app.deploy.slugs import suggest_slug
from app.services import app_service
from app.store import anchor as anchor_mod, packages
from app.store import release as release_svc

logger = logging.getLogger(__name__)

_last_slug: str | None = None      # 仅供失败用例断言 teardown 对象；不参与业务逻辑


def _instance_slug(base: str, taken: set[str]) -> str:
    """来源 slug + 'i' 后缀（^[a-z][a-z0-9]{1,39}$，撞了自动加序号）。"""
    stem = re.sub(r"[^a-z0-9]", "", (base or "").lower())[:34] or "app"
    return suggest_slug(stem + "i", taken)


async def _store_dir(sf) -> Path:
    return release_svc.store_root(await deploy_cfg(sf))


async def _taken_slugs(sf) -> set[str]:
    async with sf() as s:
        return {r[0] for r in await s.execute(text("SELECT slug FROM app_project"))}


async def _set_current_version(sf, app_id: int, version: str) -> None:
    async with sf() as s:
        await s.execute(text("UPDATE app_project SET current_version=:v, updated_at=now() "
                             "WHERE id=:i"), {"v": version, "i": app_id})
        await s.commit()


def _materializer(pkg: Path, rel: dict):
    """包 → 工程目录：src 整拍 + sqlchange 换成锚点快照（覆盖模板铺法）。"""
    def materialize(root: Path, mapping: dict) -> None:
        shutil_copytree(pkg / "src", root)
        sql = root / "sqlchange"
        if sql.exists():
            import shutil
            shutil.rmtree(sql)
        shutil_copytree(pkg / "sqlchange", sql)
        readme = (root / "README.md")
        readme.write_text(
            f"# {mapping['APP_NAME']}\n\n"
            f"来自应用商店版本 {rel['slug']}@{rel['version']}。\n\n"
            f"- 本机预览：http://localhost:{mapping['DEV_PORT']}\n"
            f"- 数据库：dev = {mapping['DEV_DB']}，prod = {mapping['PROD_DB']}\n"
            f"- 数据变更：一律写到 `sqlchange/V<日期>_<序号>_<说明>.sql`，**已应用的文件不得修改**\n",
            encoding="utf-8", newline="")
    return materialize


def shutil_copytree(src: Path, dst: Path) -> None:
    import shutil
    shutil.copytree(src, dst, dirs_exist_ok=True)


async def install_release(sf, *, release_row: dict, applicant_actor_id: int,
                          want_name: str, notify=None) -> dict:
    """返回新实例的 app dict（含 url）。抛 DeployError 时已完成 teardown。"""
    global _last_slug
    _last_slug = None
    pkg = packages.package_path(await _store_dir(sf), release_row["slug"],
                                release_row["version"])
    if not pkg.is_dir():
        raise DeployError(f"版本包缺失: {pkg}（包不可重建，请重新上架该应用）", status=500)
    manifest = packages.read_manifest(pkg)
    anchor = manifest["anchor"]
    if not anchor:
        raise DeployError("版本包锚点为空，无法初始化实例库", status=500)
    app = None
    slug = None
    rid = None
    try:
        slug = _instance_slug(release_row["slug"], await _taken_slugs(sf))
        _last_slug = slug
        app = await app_service.create_app_project(
            sf, name=want_name, slug=slug, scale="small",
            stack=manifest.get("stack") or "vue3-fastapi",
            origin=f"store:{release_row['id']}", materialize=_materializer(pkg, release_row))
        rid = await records.begin(sf, app["id"], "install", release_row["version"])

        def on_log(line: str) -> None:
            if notify:
                notify(app["id"], {"app_id": app["id"], "type": "dev_log", "line": line})

        cred_dev = await provision.read_cred(sf, slug, "dev")
        cred_prod = await provision.provision_env(sf, slug, "prod")
        if not cred_dev:
            raise DeployError("dev 凭据缺失（供给异常），请重试安装", status=500)
        root = await deploy_engine._app_root(sf, app)
        # 基线重放：空库按序全跑（run.sh 的「跳过已应用」在空库上自然成立）
        for env, cred in (("dev", cred_dev), ("prod", cred_prod)):
            await records.set_step(sf, rid, f"migrate:{env}")
            res = await sqlrun.run_sqlchange(root, env, cred, on_log=on_log)
            if not res["ok"]:
                raise DeployError(f"{env} 库基线迁移失败（退出码 {res['code']}）："
                                  f"{res['log'][-600:]}")
        # 自有镜像：按包版本号 build，再交给 prod_start 做 up + 体检
        await records.set_step(sf, rid, "build")
        meta = app_service.load_app_json(root)
        compose.ensure_deploy_files(root, meta, {"dev": cred_dev, "prod": cred_prod},
                                    version=release_row["version"])
        code, log = await deploy_engine.docker(
            "compose", "-f", str(root / "deploy" / "prod-compose.yml"), "build",
            cwd=str(root), on_line=on_log)
        if code != 0:
            raise DeployError(f"实例镜像构建失败：{log[-600:]}")
        await _set_current_version(sf, app["id"], release_row["version"])
        await records.set_step(sf, rid, "deploy")
        await deploy_prod.prod_start(sf, app, notify=notify)
        await _write_link(sf, app=app, release_row=release_row, manifest=manifest,
                          anchor=anchor, applicant_actor_id=applicant_actor_id, root=root)
        await records.finish(sf, rid, "success", detail={
            "release_id": release_row["id"], "instance_slug": slug,
            "files": len(anchor)})
        app = await app_service.get_app(sf, app["id"])
        app["url"] = f"http://localhost:{app['prod_port']}"
        if notify:
            notify(app["id"], {"app_id": app["id"], "type": "install_done", "ok": True,
                               "target_app_id": app["id"], "url": app["url"]})
        return app
    except DeployError as e:
        if app:
            await _teardown(sf, app["id"], slug)
            if rid:
                await records.finish(sf, rid, "failed", error=str(e))
        if notify and app:
            notify(app["id"], {"app_id": app["id"], "type": "install_done", "ok": False,
                               "target_app_id": None, "error": str(e)})
        raise
    except Exception as e:
        if app:
            await _teardown(sf, app["id"], slug)
            if rid:
                await records.finish(sf, rid, "failed", error=str(e))
        raise DeployError(f"安装失败: {e}") from e


async def _write_link(sf, *, app: dict, release_row: dict, manifest: dict, anchor: list,
                      applicant_actor_id: int, root: Path) -> None:
    from app.store import packages as pk
    async with sf() as s:
        await s.execute(text(
            "INSERT INTO app_instance_link (app_id, release_id, channel, baseline_manifest, "
            "baseline_hash, applied_fingerprint, owner_actor) VALUES (:a, :r, 'managed', "
            "CAST(:m AS JSONB), :h, :f, :o)"),
            {"a": app["id"], "r": release_row["id"],
             "m": json_dumps(anchor), "h": pk.source_hash(root),
             "f": manifest.get("fingerprint") or anchor_mod.fingerprint(anchor),
             "o": applicant_actor_id})
        await s.commit()


def json_dumps(v) -> str:
    import json
    return json.dumps(v, ensure_ascii=False)


async def _teardown(sf, app_id: int, slug: str | None) -> None:
    """只收本流自己的产物：库/角色按 slug 删，平台记录与目录按 id/root_path 删。各步不互阻断。"""
    if slug:
        for env in ("dev", "prod"):
            try:
                await provision.drop_env(sf, slug, env)
            except Exception:
                logger.warning("商店安装回滚：drop_env %s/%s 失败", slug, env, exc_info=True)
    try:
        snap = await app_service.delete_app(sf, app_id)
        import shutil
        if snap.get("root_path"):
            shutil.rmtree(snap["root_path"], ignore_errors=True)
    except Exception:
        logger.warning("商店安装回滚：删记录/目录失败 app_id=%s", app_id, exc_info=True)


async def run_install_for_application(sf, application: dict, notify=None) -> dict:
    """端点入口（202 后执行）：成功回写 target_app_id，失败回写 failed。不向上抛。

    为什么不抛：`_spawn` 的 done 回调只记日志，抛出去前端就停在「安装中」；事件全部按
    **来源应用 id** 下发（新实例 id 在受理时还不存在，审批人正看着来源应用页）。
    """
    from app.store import applications as apps_svc
    try:
        rel = await _release_by_id(sf, application["release_id"])
        app = await install_release(sf, release_row=rel,
                                    applicant_actor_id=application["applicant_id"],
                                    want_name=application["want_name"], notify=notify)
        async with sf() as s:
            await s.execute(text("UPDATE release_application SET target_app_id=:t, "
                                 "updated_at=now() WHERE id=:i"),
                            {"t": app["id"], "i": application["id"]})
            await s.commit()
        return app
    except Exception as e:
        logger.warning("商店安装失败 application=%s", application.get("id"), exc_info=True)
        try:
            await apps_svc.mark_failed(sf, application["id"], str(e))
        except Exception:
            logger.exception("回写 failed 状态也失败 application=%s", application.get("id"))
        return {"ok": False, "error": str(e)}


async def _release_by_id(sf, release_id: int) -> dict:
    async with sf() as s:
        r = (await s.execute(text(
            "SELECT id, app_id, slug, version, title, stack, anchor, fingerprint, status "
            "FROM app_release WHERE id = :i"), {"i": release_id})).mappings().first()
    if not r:
        raise DeployError("版本不存在", status=404)
    return dict(r)
```

`app/deploy/records.py` 的 `mark_interrupted` 白名单扩列（否则平台重启会留下永运 `running` 的安装/上架记录，`_spawn` 永久 409）：

```python
            "UPDATE deploy_record SET status='failed', step='interrupted', finished_at=now(), "
            "error='平台重启中断' WHERE status='running' "
            "AND kind IN ('publish','rollback','install','release')"))
```

同步改该函数 docstring 与 `tests/test_records.py` 里对 kind 白名单的既有断言（若有）。

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv\Scripts\python -m pytest tests/test_store_install_flow.py tests/test_records.py -v`
Expected: PASS。无 Docker 环境下第三条用例 skip，前两条必须绿（它们才是编排与 teardown 的契约）。

- [ ] **Step 5: 提交**

```bash
git add app/store/install.py app/deploy/records.py tests/test_store_install_flow.py
git commit -m "feat(store): 安装流水线（包铺源 + 锚点重放 + 自有镜像 + teardown）"
```

---

## Task 11: 实例归属与 store-link 端点

**Files:**
- Modify: `app/api/store.py`、`app/services/app_service.py`（`list_apps`/`get_app` 带出 origin）
- Test: `tests/test_store_link.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_store_link.py
"""应用列表要能说出「这实例来自哪个包、跟不跟版本」，卡片角标与升级入口都靠它。"""
import json
import secrets
import pytest
from sqlalchemy import text
from app.db import make_session_factory


@pytest.fixture
async def sf(test_engine):
    return make_session_factory(test_engine)


async def _mk(sf, **kw):
    from app.services import app_service
    slug = "lk" + secrets.token_hex(3)

    def mz(root, mapping):
        (root / "backend").mkdir(parents=True, exist_ok=True)
        (root / "backend" / "main.py").write_text("#\n", encoding="utf-8")
    app = await app_service.create_app_project(sf, name="链应", slug=slug, scale="small",
                                               stack="vue3-fastapi", materialize=mz, **kw)
    return app


async def test_list_apps_carries_origin_and_link(sf):
    app = await _mk(sf, origin="store:12")
    try:
        async with sf() as s:
            aid = (await s.execute(text(
                "INSERT INTO app_release (app_id, slug, version, title, stack, anchor, "
                "fingerprint) VALUES (0, 'demo', 'b1', '演示', 'vue3-fastapi', '[]'::jsonb, "
                "'fp1') RETURNING id"))).scalar()
            await s.execute(text(
                "INSERT INTO app_instance_link (app_id, release_id, channel, baseline_manifest, "
                "baseline_hash, applied_fingerprint) VALUES (:a, :r, 'managed', '[]'::jsonb, "
                "'h', 'fp1')"), {"a": app["id"], "r": aid})
            await s.commit()
        from app.services import app_service
        rows = {a["id"]: a for a in await app_service.list_apps(sf)}
        assert rows[app["id"]]["origin"] == "store:12"
        assert rows[app["id"]]["store"]["channel"] == "managed"
        assert rows[app["id"]]["store"]["release_version"] == "b1"
    finally:
        from app.deploy import provision
        for env in ("dev", "prod"):
            try:
                await provision.drop_env(sf, app["slug"], env)
            except Exception:
                pass
        await app_service.delete_app(sf, app["id"])


async def test_store_link_endpoint_404_for_non_store_app(client):
    await client.post("/api/store/identify", json={"nickname": "看客"})
    r = await client.get("/api/store/link/999999")
    assert r.status_code == 404
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv\Scripts\python -m pytest tests/test_store_link.py -v`
Expected: FAIL —`list_apps` 结果里没 `origin`/`store` 键

- [ ] **Step 3: 实现**

`app_service.list_apps` 的 SELECT 加 `a.origin`，并在返回前一次性拼 store（**不在循环里查库**）：

```python
async def list_apps(session_factory) -> list[dict]:
    async with session_factory() as s:
        rows = (await s.execute(text(
            "SELECT a.id, a.workspace_id, a.name, a.slug, a.scale, a.stack, a.prod_port, "
            "a.dev_port, a.status_dev, a.status_prod, a.current_version, a.origin, "
            "a.created_at, a.updated_at, w.root_path "
            "FROM app_project a LEFT JOIN workspace w ON w.id = a.workspace_id "
            "ORDER BY a.id DESC"))).mappings().all()
        links = (await s.execute(text(
            "SELECT l.app_id, l.release_id, l.channel, l.detach_reason, l.applied_fingerprint, "
            "l.last_upgraded_at, r.version AS release_version, r.slug AS release_slug, "
            "r.title AS release_title FROM app_instance_link l "
            "JOIN app_release r ON r.id = l.release_id"))).mappings().all()
    by_app = {int(l["app_id"]): dict(l) for l in links}
    out = []
    for r in rows:
        d = to_dict(r)
        d["store"] = by_app.get(int(d["id"]))
        out.append(d)
    return out
```

`get_app` 同样带 store（单行查询，直接多一次 LEFT JOIN 即可），否则详情页与列表页字段不一致。

`app/api/store.py` 追加：

```python
@router.get("/link/{app_id}")
async def store_link(request: Request, app_id: int, actor: dict = Actor):
    sf = request.app.state.session_factory
    from app.services import app_service
    app = await app_service.get_app(sf, app_id)
    if not app.get("store"):
        raise _http(DeployError("该应用不是商店实例", status=404))
    # M3-b 的「可升级版本」在此接口补出，本期先给当前绑定与通道
    return {"app_id": app_id, **app["store"], "upgradable": False}
```

- [ ] **Step 4: 跑测试确认通过（含应用页回归）**

Run: `.venv\Scripts\python -m pytest tests/test_store_link.py tests/test_apps_api.py tests/test_records.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add app/api/store.py app/services/app_service.py tests/test_store_link.py
git commit -m "feat(store): 应用列表/详情携带商店绑定与 store-link 端点"
```

---

## Task 12: 前端（商店页 / 昵称弹窗 / 卡片角标）

**Files:**
- Modify: `frontend/src/api/index.js`、`frontend/src/router/index.js`、`frontend/src/components/SideBar.vue:189`
- Create: `frontend/src/views/StoreView.vue`、`frontend/src/components/ActorNameModal.vue`
- Test: `frontend/src/views/StoreView.test.js`

- [ ] **Step 1: 写失败测试**

创建 `frontend/src/views/StoreView.test.js`（与 `AppsView.test.js` 同模式：`vi.mock('../api')` 后 mount）：

```js
// frontend/src/views/StoreView.test.js
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import Antd from 'ant-design-vue'
import StoreView from './StoreView.vue'
import * as api from '../api'

vi.mock('../api')

const releases = [
  { id: 1, slug: 'test1', version: 'b2', title: '陌如玉', summary: '桌面备忘', stack: 'vue3-fastapi',
    publisher: '作者', installed: false, installed_apps: [], updated_count: 0 },
]
const applications = [
  { id: 9, release_id: 1, want_name: '我的备忘', status: 'pending', title: '陌如玉', version: 'b2' },
]

function mountView () {
  return mount(StoreView, { global: { plugins: [Antd] }, attachTo: document.body })
}

beforeEach(() => {
  vi.clearAllMocks()
  api.storeMe.mockResolvedValue({ data: { id: 1, nickname: '我' } })
  api.storeReleases.mockResolvedValue({ data: releases })
  api.storeApplications.mockResolvedValue({ data: applications })
  api.storeMyReleases.mockResolvedValue({ data: [] })
})

describe('StoreView', () => {
  it('渲染商店卡片：标题/发布者/版本与申请按钮', async () => {
    const w = mountView()
    await flushPromises()
    expect(w.text()).toContain('陌如玉')
    expect(w.text()).toContain('作者')
    expect(w.findAll('button').some((b) => b.text().includes('申请'))).toBe(true)
  })

  it('已安装的卡片不再给申请按钮，而是给出实例入口', async () => {
    api.storeReleases.mockResolvedValue({ data: [{ ...releases[0], installed: true,
      installed_apps: [{ app_id: 7, name: '我的备忘', status_prod: 'running' }] }] })
    const w = mountView()
    await flushPromises()
    expect(w.text()).toContain('我的备忘')
    expect(w.findAll('button').some((b) => b.text().includes('申请'))).toBe(false)
  })

  it('未登录（401 identity_required）弹昵称框，提交后重新拉列表', async () => {
    api.storeMe.mockRejectedValue({ response: { status: 401 } })
    api.storeIdentify.mockResolvedValue({ data: { id: 2, nickname: '新人' } })
    const w = mountView()
    await flushPromises()
    expect(w.find('[data-test="actor-modal"]').exists()).toBe(true)
    await w.find('[data-test="actor-name"]').setValue('新人')
    await w.find('[data-test="actor-submit"]').trigger('click')
    await flushPromises()
    expect(api.storeIdentify).toHaveBeenCalledWith('新人')
    expect(api.storeReleases).toHaveBeenCalled()
  })

  it('审批：驳回必须填原因才可调接口', async () => {
    const w = mountView()
    await flushPromises()
    await w.find('[data-test="tab-review"]').trigger('click')
    await w.find('[data-test="reject-btn"]').trigger('click')
    await flushPromises()
    expect(api.storeReject).not.toHaveBeenCalled()
    await w.find('[data-test="reject-reason"]').setValue('名字不合适')
    await w.find('[data-test="reject-confirm"]').trigger('click')
    await flushPromises()
    expect(api.storeReject).toHaveBeenCalledWith(9, '名字不合适')
  })
})
```

- [ ] **Step 2: 跑测试确认失败**

Run（在 `frontend` 目录下）: `npx vitest run src/views/StoreView.test.js`
Expected: FAIL —`Failed to resolve import './StoreView.vue'`

- [ ] **Step 3: API 封装**

`frontend/src/api/index.js` 末尾追加：

```js
// ---- 应用商店 M3-a（轻身份 / 上架 / 申请审批）----
export const storeIdentify = (nickname) => http.post('/store/identify', { nickname })
export const storeMe = () => http.get('/store/me')
export const storeReleases = () => http.get('/store/releases')
export const storeWithdraw = (id) => http.post(`/store/releases/${id}/withdraw`)
export const storeApplications = (box = 'mine') => http.get('/store/applications', { params: { box } })
export const storeMyReleases = () => http.get('/applications', { params: { box: 'review' } })
export const storeApply = (releaseId, wantName) =>
  http.post('/store/applications', { release_id: releaseId, want_name: wantName })
export const storeApprove = (id) => http.post(`/store/applications/${id}/approve`, { approve: true })
export const storeReject = (id, reason) =>
  http.post(`/store/applications/${id}/reject`, { approve: false, reason })
export const releaseToStore = (appId, data) => http.post(`/apps/${appId}/release`, data)
export const storeLink = (appId) => http.get(`/store/link/${appId}`)
```

> `storeMyReleases` 那行路径错了（少了 `/store` 前缀）——**不要改测试去适配它**，改成 `http.get('/store/applications', { params: { box: 'review' } })`。

- [ ] **Step 4: 组件**

`frontend/src/components/ActorNameModal.vue`：

```vue
<script setup>
// 轻身份弹窗（规格 M3-a §6.2）：无密码、无邮箱，一个名字。cookie 即设备标识。
import { ref, watch } from 'vue'

const props = defineProps({ open: Boolean })
const emit = defineEmits(['submit', 'update:open'])
const name = ref('')
const busy = ref(false)
watch(() => props.open, (v) => { if (v) name.value = '' })

function submit () {
  const v = name.value.trim()
  if (!v) return
  busy.value = true
  emit('submit', v)
  emit('update:open', false)
  busy.value = false
}
</script>

<template>
  <a-modal :open="open" title="留下你的名字" :footer="null" @cancel="emit('update:open', false)">
    <div data-test="actor-modal">
      <p class="zc-actor-tip">商店用这个名字记录「谁申请的、这台设备是谁」，不需要密码。</p>
      <a-input v-model:value="name" data-test="actor-name" placeholder="昵称（1-50 字）" />
      <a-button type="primary" data-test="actor-submit" @click="submit">进商店</a-button>
    </div>
  </a-modal>
</template>

<style scoped>
.zc-actor-tip { color: rgba(0, 0, 0, 0.55); font-size: 13px; }
</style>
```

`frontend/src/views/StoreView.vue`（三个 tab：商店 / 我的发布 / 待审批；卡片沿用应用页同一形状与 `.zc-card-tag` 样式）：

```vue
<script setup>
// 应用商店（规格 M3-a §9）：列表/申请/审批一页三视图。身份缺失弹昵称框，不进登录页。
import { onMounted, ref } from 'vue'
import { message } from 'ant-design-vue'
import { AppstoreOutlined, PlusOutlined, SendOutlined } from '@ant-design/icons-vue'
import * as api from '../api'
import ActorNameModal from '../components/ActorNameModal.vue'

const tab = ref('store')
const releases = ref([])
const mine = ref([])
const review = ref([])
const me = ref(null)
const actorOpen = ref(false)
const applyOpen = ref(false)
const applyTarget = ref(null)
const applyName = ref('')
const reasonDraft = ref({})

async function load () {
  releases.value = (await api.storeReleases()).data
  mine.value = (await api.storeApplications('mine')).data
  review.value = (await api.storeApplications('review')).data
}

onMounted(async () => {
  try {
    me.value = (await api.storeMe()).data
    await load()
  } catch (e) {
    if (e?.response?.status === 401) actorOpen.value = true
    else message.error('商店加载失败：' + (e?.message || e))
  }
})

async function onIdentify (nickname) {
  me.value = (await api.storeIdentify(nickname)).data
  await load()
}

function openApply (r) { applyTarget.value = r; applyName.value = r.title; applyOpen.value = true }

async function submitApply () {
  const name = applyName.value.trim()
  if (!name) { message.warning('给应用起个名字'); return }
  await api.storeApply(applyTarget.value.id, name)
  applyOpen.value = false
  message.success('已提交申请，等发布者审批')
  await load()
}

async function approve (row) {
  await api.storeApprove(row.id)
  message.success('已通过，正在安装（进度看来源应用的日志）')
  await load()
}

async function reject (row) {
  const reason = (reasonDraft.value[row.id] || '').trim()
  if (!reason) { message.warning('驳回请写原因'); return }
  await api.storeReject(row.id, reason)
  await load()
}

function gotoApps () { window.location.href = '/apps' }
</script>

<template>
  <div class="zc-store">
    <header class="zc-store-head">
      <h2><appstore-outlined /> 应用商店</h2>
      <a-space>
        <a-button @click="load">刷新</a-button>
        <a-button type="primary" @click="gotoApps"><plus-outlined /> 新建应用</a-button>
      </a-space>
    </header>

    <a-tabs v-model:activeKey="tab">
      <a-tab-pane key="store" tab="商店">
        <a-empty v-if="!releases.length" description="还没有应用上架" />
        <a-card v-for="r in releases" :key="r.id" class="zc-card">
          <template #title>
            {{ r.title }}
            <a-tag class="zc-card-tag">{{ r.stack }}</a-tag>
            <a-tag v-if="r.installed" class="zc-card-tag">已安装</a-tag>
          </template>
          <div class="zc-store-meta">发布者 {{ r.publisher || '未知' }} · 版本 {{ r.version }}</div>
          <p class="zc-store-summary">{{ r.summary || '作者没写简介' }}</p>
          <a-space wrap>
            <a-button v-if="!r.installed" type="primary" @click="openApply(r)">
              <send-outlined /> 申请使用
            </a-button>
            <a-button v-for="i in r.installed_apps" :key="i.app_id" :href="`http://localhost:${i.port}`">
              打开 {{ i.name }}
            </a-button>
          </a-space>
        </a-card>
      </a-tab-pane>

      <a-tab-pane key="mine" tab="我的申请">
        <a-empty v-if="!mine.length" description="还没有提过申请" />
        <a-table v-else :data-source="mine" :pagination="false" row-key="id"
          :columns="[{ title: '应用', dataIndex: 'title' }, { title: '我起的名字', dataIndex: 'want_name' },
                     { title: '状态', dataIndex: 'status' }, { title: '说明', dataIndex: 'reason' }]" />
      </a-tab-pane>

      <a-tab-pane key="review" tab="待我审批">
        <a-tab-pane-anchor data-test="tab-review" />
        <a-empty v-if="!review.length" description="没有待审批的申请" />
        <div v-for="row in review" :key="row.id" class="zc-review-row">
          <div><b>{{ row.want_name }}</b> · 来自 {{ row.title }} {{ row.version }}</div>
          <a-space>
            <a-button data-test="approve-btn" type="primary" @click="approve(row)">通过并安装</a-button>
            <a-input data-test="reject-reason" v-model:value="reasonDraft[row.id]"
                     placeholder="驳回原因" style="width: 200px" />
            <a-button data-test="reject-btn" @click="reject(row)">驳回</a-button>
          </a-space>
        </div>
      </a-tab-pane>
    </a-tabs>

    <a-modal v-model:open="applyOpen" title="申请使用" :footer="null">
      <a-input v-model:value="applyName" placeholder="给你的实例起个名字" />
      <p class="zc-store-tip">获批后会为你单独建一套代码目录与数据库，数据与发布者无关。</p>
      <a-button type="primary" @click="submitApply">提交申请</a-button>
    </a-modal>

    <ActorNameModal v-model:open="actorOpen" @submit="onIdentify" />
  </div>
</template>

<style scoped>
.zc-store { padding: 16px 20px; }
.zc-store-head { display: flex; align-items: center; justify-content: space-between; }
.zc-store-meta { color: rgba(0, 0, 0, 0.55); font-size: 13px; }
.zc-store-summary { margin: 8px 0 12px; }
.zc-store-tip { color: rgba(0, 0, 0, 0.55); font-size: 13px; }
.zc-review-row { display: flex; align-items: center; justify-content: space-between;
  padding: 10px 0; border-bottom: 1px solid rgba(0, 0, 0, 0.06); }
</style>
```

两处与既有规范对齐（不要“顺手改”）：类型标签紧跟应用名、用 `.zc-card-tag`（8px 左间距、常规字重、与文本垂直居中）。

> 上面模板里 `<a-tab-pane-anchor data-test="tab-review" />` 是**错的**（不存在的组件）：改成给 tab 标签本身挂属性——`<a-tab-pane key="review">` 的 `tab` 插槽换成 `<template #tab><span data-test="tab-review">待我审批</span></template>`。测试里点击 `[data-test="tab-review"]` 才能命中。

路由与入口：

```js
// frontend/src/router/index.js 的 routes 数组里追加
  {
    path: '/store',
    name: 'store',
    component: () => import('../views/StoreView.vue'),
  },
```

```html
<!-- frontend/src/components/SideBar.vue:189 附近，与「应用」「设置」并列 -->
<router-link to="/store"><shop-outlined /> 商店</router-link>
```

并在 `SideBar.vue` 的图标 import 里补 `ShopOutlined`。

- [ ] **Step 5: 跑测试确认通过**

Run（`frontend` 目录）: `npx vitest run src/views/StoreView.test.js src/views/AppsView.test.js`
Expected: PASS（新 4 条 + 既有应用页用例不回归）

- [ ] **Step 6: 提交**

```bash
git add frontend/src/api/index.js frontend/src/router/index.js frontend/src/components/SideBar.vue frontend/src/components/ActorNameModal.vue frontend/src/views/StoreView.vue frontend/src/views/StoreView.test.js
git commit -m "feat(store): 商店页（列表/我的申请/待审批）与轻身份弹窗"
```

---

## Task 13: 纪律入库与全量回归

上架闸门只能拦「已经造成的违规」，「迁移只追加、破坏性变更两步走」必须写进 AI 提示词与应用 README，否则约束 L 靠人记不住。

**Files:**
- Modify: `app/agent/prompts.py`、`app/templates/vue3-fastapi/README.md`、`app/templates/vue3-fastapi/sqlchange/README.md`、`README.md`
- Test: `tests/test_prompts.py`（追加）、`tests/test_scaffold.py`（追加）

- [ ] **Step 1: 写失败测试**

```python
# 追加到 tests/test_prompts.py
async def test_prompt_carries_sqlchange_append_only_discipline():
    from app.agent import prompts
    text_ = prompts.system_prompt_for_app(scale="small", stack="vue3-fastapi")
    assert "只追加" in text_ or "不得修改" in text_
    assert "两步" in text_
```

> 若 `prompts.py` 里没有 `system_prompt_for_app` 这个入口，**不要新建一个**：读 `app/agent/prompts.py` 找到现有的应用上下文拼装函数，把断言换到那个函数上（本任务只改提示词文本，不改函数形状）。

```python
# 追加到 tests/test_scaffold.py
def test_app_readme_states_append_only(tmp_path):
    from app.deploy import scaffold
    from pathlib import Path
    root = tmp_path / "a"
    scaffold.render(Path("app/templates/vue3-fastapi"), root, {
        "SLUG": "a", "APP_NAME": "甲", "DEV_PORT": "8300", "PROD_PORT": "8301",
        "DEV_DB": "a_dev", "PROD_DB": "a_prod", "TODAY": "20260922"})
    text_ = (root / "README.md").read_text(encoding="utf-8")
    assert "不得修改" in text_ and "两步" in text_
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv\Scripts\python -m pytest tests/test_prompts.py tests/test_scaffold.py -v`
Expected: FAIL（断言的纪律文本还不存在）

- [ ] **Step 3: 写纪律文本**

`app/templates/vue3-fastapi/README.md` 的「数据变更」行下追加：

```markdown
- 已应用到库的 `V*.sql` **不得修改也不得删除**（平台按文件名+校验和记账，改了会被拒启动预览）。
- 需要改列类型/拆表/删列时**分两个版本两步走**：第一版加新列并双写，第二版迁数据后删旧列。
- 上架到应用商店后，本应用的迁移序列就是其他实例的升级基线，破坏性变更未走两步会让订阅实例无法升级。
```

`app/templates/vue3-fastapi/sqlchange/README.md` 补同一条「只追加」与两步走口径（这页是 AI 改数据时先看的地方）。

`app/agent/prompts.py` 在应用上下文提示词里追加一段（措辞与上面一致，避免两套说法）：

```text
数据变更约束：sqlchange 下的文件一经应用不得修改也不得删除，只能追加新文件。
涉及改列类型/拆表/删列的破坏性变更，拆成两个迁移文件分两步：先加新列并双写，再迁数据与删旧列。
若本应用已上架商店，你的改动会决定别人能不能升级，宁可分两步。
```

根 `README.md` 新增一节（插在「发布上线」之后）：

```markdown
## 应用商店

把发布好的应用上架，同事在商店里申请，你审批后平台自动为他建一份独立实例（独立代码目录 + 独立数据库 + 独立地址）。

1. **上架**：应用卡片「发布历史」旁的「发布到商店」，填名称与简介。平台取的是**正式库真实执行过的数据变更序列**，本地预览里试过又没发布的变更不会进包。
2. **申请与审批**：对方在侧栏「商店」首次使用留个名字→ 点「申请使用」→ 你在「待我审批」点「通过并安装」，安装进度在日志里看。
3. **数据各自一份**：新实例从空库起步，按上架版本的结构自动建表；你看不见对方的数据，对方也拿不到你的。
4. **升级**：使用者在自己卡片上点「升级」，平台先在真数据的克隆库上预演一遍再动真库（M3-b）。
5. **纪律**：上架后的应用，数据变更文件只能追加；改列类型这类破坏性变更要分两步（先加新列双写，下个版本再删旧列），否则订阅实例无法安全跟随升级。
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv\Scripts\python -m pytest tests/test_prompts.py tests/test_scaffold.py -v`
Expected: PASS

- [ ] **Step 5: 全量回归**

Run: `.venv\Scripts\python -m pytest tests -v`
Expected: 全绿（无 Docker 环境下 `test_store_install_flow.py` 的实跑用例 skip，其余不得失败）

Run（`frontend` 目录）: `npx vitest run`
Expected: 全绿

Run: `.\build.bat`
Expected: 前端构建成功（商店页进入路由分包）

- [ ] **Step 6: 手工冒烟（必须做，这一步验的是编排而不是单元行为）**

1. 启 PG 容器与 `dev.bat`，确保 `test1`（陌如玉）已发布过 prod。
2. 用另一个浏览器（或隐私窗口）开 `/store` → 留名字 → 此时列表应为空。
3. 回主窗口，在陌如玉卡片上点「发布到商店」→ 商店列表出现该应用。
4. 隐私窗口点「申请使用」→ 主窗口「待我审批」出现 → 点「通过并安装」。
5. 验收点：① 应用列表里多了一个「陌如玉i」（角标「来自商店」），② `docker ps` 里能看到 `zc-test1i-prod-*` 容器，③ 新开浏览器访问它，**里面是空的**（没你的备忘数据），④ 它改一条数据，陌如玉原应用纹丝不动。
6. 回滚验证（故意弄坏）：把 `store/test1/<ver>/sqlchange/V*.sql` 中任意一个改成非法 SQL → 重走一次安装 → 应失败且 `docker ps`、应用列表、`store/` 目录三处都无残留。

- [ ] **Step 7: 提交**

```bash
git add app/agent/prompts.py app/templates/vue3-fastapi/README.md app/templates/vue3-fastapi/sqlchange/README.md README.md tests/test_prompts.py tests/test_scaffold.py
git commit -m "docs(store): 迁移只追加与破坏性变更两步走纪律入库"
```

---

## 验收标准（M3-a 整体）

1. 未发布过的应用上不了架；dev 里试过但未发布的迁移不会混进包。
2. 包内永远没有 `deploy/` 与 `.env`；同 slug+version 不能重复上架；上架后改作者目录的文件不影响已生成的包。
3. 审批通过 → 自动得到独立实例（独立代码目录、`<newslug>_dev`/`<newslug>_prod` 两库两角色、独立端口对、自有镜像），开箱可访问且**数据为空**。
4. 安装的任一前置步失败 → 库、平台记录、磁盘目录三处无残留；到了起栈阶段失败 → 实例与数据保留，只报错。
5. 实例与其他实例、与发布者原应用之间，没有任何写入路径；角色只能连自己的库（沿用规格 06 的 `REVOKE ... FROM PUBLIC`）。
6. 平台重启不会留下永久 `running` 的安装/上架记录（kind 白名单已含 `install`/`release`）。
7. 无账号体系下的越权收口：非发布者的审批请求 403；未留身份的一切商店请求 401 + `code: identity_required`。
8. `ENVS`、`run.sh` 执行算法、`sqlchange_history` 结构、M2 七步发布语义——本一期全部零改动。

## 本计划不包含（属 M3-b / M4）

升级与影子克隆预演（§6.4）、fork 检测与「问人」（§6.5）、`schema_drift` 启动对账、prev 库 keep-1 清理、账号体系与真鉴权、商店实例的应用页「升级」按钮与 `upgradable` 计算。

## Task 7: 上架服务 release

**Files:**
- Create: `app/store/release.py`
- Modify: `app/deploy/settings.py:4-12`（`store.root` 默认值）
- Test: `tests/test_store_release.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_store_release.py`。关键手法：**不跑 Docker**，直接往应用的 prod 库里种 `sqlchange_history`（这正是真流水线跑完 `run.sh` 后的产物形状），再验上架闸门；`prod.image_exists` 用 monkeypatch 替身。

```python
# tests/test_store_release.py
"""上架闸门（规格 §6.1）：锚点只取 prod history，五道闸任一不过即拒绝且不产生包目录。"""
import secrets
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from app.deploy import prod as deploy_prod
from app.deploy import provision
from app.deploy.errors import DeployError
from app.db import make_session_factory
from app.services import app_service
from app.store import anchor, packages, release


def _slug():
    return "rel" + secrets.token_hex(3)


def _materialize(sql_files: dict[str, str]):
    def mz(root, mapping):
        (root / "backend").mkdir(parents=True, exist_ok=True)
        (root / "backend" / "main.py").write_text("# app\n", encoding="utf-8", newline="")
        d = root / "sqlchange"
        d.mkdir(parents=True, exist_ok=True)
        for name, body in sql_files.items():
            (d / name).write_text(body, encoding="utf-8", newline="")
    return mz


async def _make_app(sf, *, sql_files, publish_ok=True, version="b20260922000001"):
    """造一个「已发布过 prod」的应用：app_project.current_version + publish success 记录。"""
    slug = _slug()
    app = await app_service.create_app_project(
        sf, name="上架测试", slug=slug, scale="small", stack="vue3-fastapi",
        materialize=_materialize(sql_files))
    cred = await provision.provision_env(sf, slug, "prod")
    url = anchor.app_db_url(cred)
    eng = create_async_engine(url)
    try:
        async with eng.begin() as c:
            await c.execute(text(
                "CREATE TABLE IF NOT EXISTS sqlchange_history (filename VARCHAR(200) PRIMARY KEY,"
                " checksum VARCHAR(32), success BOOLEAN NOT NULL DEFAULT true)"))
            for name, body in sql_files.items():
                await c.execute(text("INSERT INTO sqlchange_history (filename, checksum) "
                                     "VALUES (:f, :s) ON CONFLICT DO NOTHING"),
                                {"f": name, "s": _md5(body)})
    finally:
        await eng.dispose()
    async with sf() as s:
        await s.execute(text("UPDATE app_project SET current_version=:v WHERE id=:i"),
                        {"v": version, "i": app["id"]})
        if publish_ok:
            await s.execute(text("INSERT INTO deploy_record (app_id, kind, version, status) "
                                 "VALUES (:a, 'publish', :v, 'success')"),
                            {"a": app["id"], "v": version})
        await s.commit()
    return app


def _md5(body: str) -> str:
    import hashlib
    return hashlib.md5(body.encode("utf-8")).hexdigest()


@pytest.fixture
async def sf(test_engine):
    return make_session_factory(test_engine)


@pytest.fixture
def store_root(tmp_path, monkeypatch):
    async def _cfg(session_factory):
        return {"store.root": str(tmp_path / "store")}
    monkeypatch.setattr(release, "deploy_cfg", _cfg)
    return tmp_path / "store"


@pytest.fixture
def images_ok(monkeypatch):
    async def _yes(tag):
        return True
    monkeypatch.setattr(deploy_prod, "image_exists", _yes)


@pytest.fixture
def actor(sf):
    from app.store import identity
    async def _mk():
        return (await identity.create_actor(sf, "发布者"))["id"]
    return _mk


async def _release(sf, store_root, app_id, actor_id, title="陌如玉", summary=""){" ": None}
```

上面最后那行是故意留的反例——**不要这么写**。真正的公共调用段用下面的 helper，各用例复用它：

```python
async def _do_release(sf, app_id, actor_id, *, title="演示应用", summary="简介"):
    return await release.create_release(sf, app_id=app_id, title=title, summary=summary,
                                        actor_id=actor_id)


async def test_happy_path_writes_package_and_row(sf, store_root, images_ok, actor):
    files = {"V20260920_01_init.sql": "CREATE TABLE a(id int);\n",
             "V20260920_02_b.sql": "ALTER TABLE a ADD c text;\n"}
    app = await _make_app(sf, sql_files=files)
    rel = await _do_release(sf, app["id"], await actor())
    assert [e["filename"] for e in rel["anchor"]] == sorted(files)   # 锚点=有序成功集
    pkg = packages.package_path(store_root, app["slug"], rel["version"])
    assert (pkg / "manifest.json").is_file()
    assert (pkg / "sqlchange" / "V20260920_01_init.sql").is_file()
    async with sf() as s:
        n = (await s.execute(text("SELECT count(*) FROM deploy_record WHERE app_id=:a "
                                  "AND kind='release' AND status='success'"),
                             {"a": app["id"]})).scalar()
    assert n == 1


async def test_dev_only_sql_not_in_anchor(sf, store_root, images_ok, actor):
    """目录里多一个从未 prod 应用过的文件 → 不进包（锚点取自 history，不取自目录）。"""
    files = {"V20260920_01_init.sql": "SELECT 1;\n"}
    app = await _make_app(sf, sql_files=files)
    root = await release._app_root(sf, app)
    (root / "sqlchange" / "V20260921_09_halfway.sql").write_text("DROP TABLE a;\n",
                                                                encoding="utf-8")
    rel = await _do_release(sf, app["id"], await actor())
    assert [e["filename"] for e in rel["anchor"]] == ["V20260920_01_init.sql"]
    pkg = packages.package_path(store_root, app["slug"], rel["version"])
    assert not (pkg / "sqlchange" / "V20260921_09_halfway.sql").exists()


async def test_edited_history_file_rejected(sf, store_root, images_ok, actor):
    files = {"V20260920_01_init.sql": "SELECT 1;\n"}
    app = await _make_app(sf, sql_files=files)
    root = await release._app_root(sf, app)
    (root / "sqlchange" / "V20260920_01_init.sql").write_text("SELECT 9;\n", encoding="utf-8")
    with pytest.raises(DeployError) as e:
        await _do_release(sf, app["id"], await actor())
    assert "改了历史" in str(e.value) or "不一致" in str(e.value)


async def test_never_published_rejected(sf, store_root, images_ok, actor):
    app = await _make_app(sf, sql_files={"V20260920_01_init.sql": "SELECT 1;\n"},
                          publish_ok=False)
    with pytest.raises(DeployError):
        await _do_release(sf, app["id"], await actor())


async def test_missing_image_rejected(sf, store_root, monkeypatch, actor):
    async def _no(tag):
        return False
    monkeypatch.setattr(deploy_prod, "image_exists", _no)
    app = await _make_app(sf, sql_files={"V20260920_01_init.sql": "SELECT 1;\n"})
    with pytest.raises(DeployError):
        await _do_release(sf, app["id"], await actor())


async def test_prefix_violation_rejected(sf, store_root, images_ok, actor):
    """第二个版本删掉了第一个版本的文件 → 违反约束 L，拒上架。"""
    app = await _make_app(sf, sql_files={"V20260920_01_init.sql": "SELECT 1;\n",
                                         "V20260920_02_b.sql": "SELECT 2;\n"})
    await _do_release(sf, app["id"], await actor())
    root = await release._app_root(sf, app)
    # 作者把已上架的第二个文件从 prod history 里手工清掉（模拟分叉）
    cred = await provision.read_cred(sf, app["slug"], "prod")
    eng = create_async_engine(anchor.app_db_url(cred))
    async with eng.begin() as c:
        await c.execute(text("DELETE FROM sqlchange_history WHERE filename=:f"),
                        {"f": "V20260920_02_b.sql"})
    await eng.dispose()
    (root / "sqlchange" / "V20260920_02_b.sql").unlink()
    async with sf() as s:   # 换一个新版本号，绕开 slug+version 唯一约束
        await s.execute(text("UPDATE app_project SET current_version='b20260923000001' "
                             "WHERE id=:i"), {"i": app["id"]})
        await s.execute(text("INSERT INTO deploy_record (app_id, kind, version, status) "
                             "VALUES (:a, 'publish', 'b20260923000001', 'success')"),
                        {"a": app["id"]})
        await s.commit()
    with pytest.raises(DeployError) as e:
        await _do_release(sf, app["id"], await actor())
    assert "前缀" in str(e.value) or "分叉" in str(e.value)


async def test_empty_anchor_rejected(sf, store_root, images_ok, actor):
    app = await _make_app(sf, sql_files={})
    with pytest.raises(DeployError):
        await _do_release(sf, app["id"], await actor())
```

> 用例结束靠 `test_engine` 的 TRUNCATE 清平台表；应用自己建的 `<slug>_prod` 库与目录是共享 PG 上的真产物，每个用例结尾调 `_drop_app(sf, app)`（见 Step 3 的 `_app_root`/清理约定，与 `tests/test_provision.py` 现有纪律一致）。

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv\Scripts\python -m pytest tests/test_store_release.py -v`
Expected: FAIL —`ModuleNotFoundError: No module named 'app.store.release'`

- [ ] **Step 3: 实现**

`app/deploy/settings.py` 的 `DEFAULTS` 加一行（商店包是平台资产，默认落在仓库根 `store/`，与 `apps/` 同级；测试靠覆盖它指向 tmp 目录）：

```python
    "store.root": "",
```

```python
# app/store/release.py
"""上架（规格 M3-a §6.1）：把「真实跑过的 prod 状态」凝固成不可变版本包。

闸门顺序刻意先查便宜的后查贵的；任何一道不过即抛 DeployError 且不产生包目录。
"""
import json
import logging
from pathlib import Path
from sqlalchemy import text
from app.deploy import prod, records, sqlrun
from app.deploy.errors import DeployError
from app.deploy.provision import read_cred
from app.deploy.settings import REPO_ROOT, deploy_cfg, resolve_apps_root
from app.services import app_service
from app.store import anchor as anchor_mod
from app.store import packages

logger = logging.getLogger(__name__)


def store_root(cfg: dict) -> Path:
    raw = cfg.get("store.root") or ""
    return Path(raw) if raw else REPO_ROOT / "store"


async def _app_root(sf, app: dict) -> Path:
    """取工程目录：复用 apps_root 解析与 workspace 登记，与部署引擎同一口径。"""
    from app.deploy import engine as deploy_engine
    return await deploy_engine._app_root(sf, app)


async def latest_release(sf, slug: str) -> dict | None:
    async with sf() as s:
        r = (await s.execute(text(
            "SELECT id, slug, version, anchor, fingerprint, status FROM app_release "
            "WHERE slug = :s AND status = 'published' ORDER BY id DESC LIMIT 1"),
            {"s": slug})).mappings().first()
    return dict(r) if r else None


async def create_release(sf, *, app_id: int, title: str, summary: str,
                         actor_id: int) -> dict:
    """上架当前在服 prod 版本。返回 app_release 行（含 id/anchor/fingerprint）。"""
    app = await app_service.get_app(sf, app_id)
    slug = app["slug"]
    version = app.get("current_version")
    rid = await records.begin(sf, app_id, "release", version)
    try:
        # 闸门 1：必须是「真实在服的 prod 版本」——没发布过就没有可信的库状态与镜像
        if not version:
            raise DeployError("该应用还没有正式发布过，请先点「发布」再上架商店")
        async with sf() as s:
            ok = (await s.execute(text(
                "SELECT 1 FROM deploy_record WHERE app_id=:a AND kind='publish' "
                "AND status='success' AND version=:v LIMIT 1"),
                {"a": app_id, "v": version})).first()
        if not ok:
            raise DeployError(f"版本 {version} 没有成功的发布记录，不能上架")
        # 闸门 2：镜像在位（包只存源码，但首次启动的加速与回滚目标都指望它）
        for part in ("backend", "frontend"):
            tag = f"zc-{slug}-{part}:prod-{version}"
            if not await prod.image_exists(tag):
                raise DeployError(f"镜像缺失: {tag}（发布后被清理，请重新发布再上架）")
        # 闸门 3：锚点取自 prod 库 history
        cred = await read_cred(sf, slug, "prod")
        if not cred:
            raise DeployError("prod 数据库凭据缺失，无法读取迁移历史", status=409)
        try:
            hist = await anchor_mod.read_history(anchor_mod.app_db_url(cred))
        except Exception as e:
            raise DeployError(f"prod 库迁移历史不可读: {e}")
        if not hist:
            raise DeployError("prod 库没有任何已成功的数据变更文件，无可上架内容")
        # 闸门 4：盘上文件必须与 history 记的 checksum 一致（纪律 1）
        root = await _app_root(sf, app)
        sql_dir = root / "sqlchange"
        on_disk = {e["filename"]: packages_path_md5(sql_dir, e["filename"])
                   for e in hist}
        bad = anchor_mod.mismatches(hist, on_disk)
        if bad:
            raise DeployError(f"以下迁移已应用但被改了/删了，违反「迁移只追加」纪律，"
                              f"请恢复原文件后重试: {bad}（不一致）")
        # 闸门 5：与上一版构成前缀（约束 L）
        prev = await latest_release(sf, slug)
        if prev and not anchor_mod.is_prefix(anchor_mod.names_of(prev["anchor"]),
                                            anchor_mod.names_of(hist)):
            raise DeployError("版本序列不是上一版的前缀（分叉），不能上架；"
                              "如需并行演进请发布为新应用")
        # 通过全部闸门才落包
        cfg = await deploy_cfg(sf)
        manifest_extra = {
            "app_id": app_id, "title": (title or app["name"])[:100],
            "summary": summary or "", "stack": app["stack"],
            "health": app_service.load_app_json(root).get("health", {}),
            "prev_release_id": prev["id"] if prev else None,
        }
        pkg = packages.write_package(
            out_root=store_root(cfg), slug=slug, version=version, app_root=root,
            anchor=hist, manifest_extra=manifest_extra)
        async with sf() as s:
            r = (await s.execute(text(
                "INSERT INTO app_release (app_id, slug, version, title, summary, stack, anchor, "
                "fingerprint, prev_release_id, created_by) VALUES (:a, :sl, :v, :ti, :su, :st, "
                "CAST(:an AS JSONB), :fp, :pv, :cb) "
                "RETURNING id, app_id, slug, version, title, summary, stack, anchor, "
                "fingerprint, prev_release_id, status, created_at"),
                {"a": app_id, "sl": slug, "v": version, "ti": manifest_extra["title"],
                 "su": manifest_extra["summary"], "st": app["stack"],
                 "an": json.dumps(hist, ensure_ascii=False),
                 "fp": anchor_mod.fingerprint(hist),
                 "pv": prev["id"] if prev else None, "cb": actor_id})).mappings().first()
            await s.commit()
        rel = dict(r)
        rel["package_dir"] = str(pkg)
        await records.finish(sf, rid, "success", detail={
            "release_id": rel["id"], "files": len(hist),
            "fingerprint": rel["fingerprint"], "package": str(pkg)})
        return rel
    except DeployError as e:
        await records.finish(sf, rid, "failed", error=str(e))
        raise
    except Exception as e:
        await records.finish(sf, rid, "failed", error=str(e))
        raise DeployError(f"上架失败: {e}") from e


def packages_path_md5(sql_dir: Path, filename: str) -> str:
    """盘上 md5；文件不存在时返回哨兵串，交给 mismatches() 报「文件已不在 sqlchange/」。"""
    f = sql_dir / filename
    return anchor_mod.file_md5(f) if f.is_file() else "__missing__"
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv\Scripts\python -m pytest tests/test_store_release.py -v`
Expected: PASS（7 条）。若 `_make_app` 建的 prod 库泄漏，检查用例结尾是否调了清理（共享 PG 上残留库会让下次 `provision_env` 幂等复用旧密码，不报错但脏）。

- [ ] **Step 5: 提交**

```bash
git add app/store/release.py app/deploy/settings.py tests/test_store_release.py
git commit -m "feat(store): 上架五道闸门与不可变包落库"
```

---

## Task 8: 上架与商店列表端点

**Files:**
- Modify: `app/api/store.py`
- Test: `tests/test_store_api.py`（追加）

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_store_api.py`）

```python
async def test_release_endpoint_requires_identity_and_publish(client):
    r = await client.post("/api/apps/1/release", json={"title": "x", "summary": ""})
    assert r.status_code == 401 and r.json()["detail"]["code"] == "identity_required"


async def test_store_releases_lists_published(client, monkeypatch):
    """列表不触发 Docker：直接往 app_release 插行，验形状与 withdrawn 过滤。"""
    await client.post("/api/store/identify", json={"nickname": "看客"})
    sf = client.app.state.session_factory
    from sqlalchemy import text
    async with sf() as s:
        aid = (await s.execute(text("SELECT id FROM actor LIMIT 1"))).scalar()
        for version, status in (("b1", "published"), ("b0", "withdrawn")):
            await s.execute(text(
                "INSERT INTO app_release (app_id, slug, version, title, stack, anchor, "
                "fingerprint, status, created_by) VALUES (0, 'demo', :v, '演示应用', "
                "'vue3-fastapi', '[]'::jsonb, :f, :st, :a)"),
                {"v": version, "f": "fp" + version, "st": status, "a": aid})
        await s.commit()
    r = await client.get("/api/store/releases")
    assert r.status_code == 200
    body = r.json()
    assert [x["version"] for x in body] == ["b1"], "下架版本不得出现在商店列表"
    assert body[0]["title"] == "演示应用" and "updated_count" in body[0]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv\Scripts\python -m pytest tests/test_store_api.py -v`
Expected: FAIL —`/api/apps/1/release` 返 405/404；列表端点不存在

- [ ] **Step 3: 实现**

在 `app/api/store.py` 追加（`apps_ext` 已在 Task 4 建好）：

```python
from app.store import release as release_svc


class ReleaseIn(BaseModel):
    title: str
    summary: str = ""


@apps_ext.post("/{app_id}/release", status_code=201)
async def publish_to_store(request: Request, app_id: int, body: ReleaseIn,
                          actor: dict = Actor):
    """上架是长任务吗？不是：五道闸门 + 拷文件 + 一行插入，秒级完成，同步返回 201。

    刻意不套 `_spawn` 单任务闸门：它不碰容器，与 dev/prod 生命周期任务不互踩。
    """
    sf = request.app.state.session_factory
    try:
        return await release_svc.create_release(
            sf, app_id=app_id, title=body.title, summary=body.summary,
            actor_id=actor["id"])
    except DeployError as e:
        raise _http(e)


@router.get("/releases")
async def list_releases(request: Request, actor: dict = Actor):
    """商店列表：每个 slug 只露最新 published 版本（申请人不需要翻历史）。

    installed 标记按当前 actor 的实例算：装过的版本卡片上显示「已安装」而非再给一个申请按钮。
    """
    sf = request.app.state.session_factory
    async with sf() as s:
        rows = (await s.execute(text(
            "SELECT r.id, r.app_id, r.slug, r.version, r.title, r.summary, r.stack, "
            "r.fingerprint, r.created_at, a.nickname AS publisher, "
            "(SELECT count(*) FROM release_application q WHERE q.release_id = r.id "
            " AND q.status <> 'cancelled') AS applied_count "
            "FROM app_release r "
            "LEFT JOIN actor a ON a.id = r.created_by "
            "WHERE r.status = 'published' "
            "AND r.id = (SELECT max(id) FROM app_release WHERE slug = r.slug "
            "            AND status = 'published') "
            "ORDER BY r.id DESC"))).mappings().all()
        mine = (await s.execute(text(
            "SELECT l.release_id, p.id AS app_id, p.name, p.status_prod "
            "FROM app_instance_link l JOIN app_project p ON p.id = l.app_id "
            "WHERE l.owner_actor = :a"), {"a": actor["id"]})).mappings().all()
    installed = {}
    for m in mine:
        installed.setdefault(m["release_id"], []).append(dict(m))
    out = []
    for r in rows:
        d = dict(r)
        d["created_at"] = d["created_at"].isoformat() if d.get("created_at") else None
        d["installed_apps"] = installed.get(d["id"], [])
        d["installed"] = bool(d["installed_apps"])
        d["updated_count"] = int(d.pop("applied_count"))   # 历史字段名保留给前端角标语义
        out.append(d)
    return out


@router.post("/releases/{release_id}/withdraw")
async def withdraw(request: Request, release_id: int, actor: dict = Actor):
    """下架：只停止新申请，已装实例与升级线不受影响（规格 §6.6）。"""
    sf = request.app.state.session_factory
    async with sf() as s:
        row = (await s.execute(text(
            "SELECT r.id, r.created_by FROM app_release r WHERE r.id = :i"),
            {"i": release_id})).mappings().first()
        if not row:
            raise _http(DeployError("版本不存在", status=404))
        if row["created_by"] != actor["id"]:
            raise _http(DeployError("只能下架自己发布的版本", status=403))
        await s.execute(text("UPDATE app_release SET status='withdrawn' WHERE id=:i"),
                        {"i": release_id})
        await s.commit()
    return {"ok": True}
```

> `updated_count` 这个键名是刻意保留的：前端卡片角标要显示「N 人申请过」，语义与商店的「可升级数」无关，改名成本低于误名成本。若不喜欢，前端与这里一起改成 `applied_count`，**不要只改一边**。

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv\Scripts\python -m pytest tests/test_store_api.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add app/api/store.py tests/test_store_api.py
git commit -m "feat(store): 上架/商店列表/下架端点"
```

---

## Task 9: 申请与审批

**Files:**
- Create: `app/store/applications.py`
- Modify: `app/api/store.py`
- Test: `tests/test_store_applications.py`、`tests/test_store_api.py`（追加）

- [ ] **Step 1: 写失败测试**

创建 `tests/test_store_applications.py`：

```python
# tests/test_store_applications.py
"""申请单状态机：pending → approved/rejected，单向不可回头；只有发布者能审。"""
import secrets
import pytest
from sqlalchemy import text
from app.deploy.errors import DeployError
from app.db import make_session_factory
from app.store import applications as apps_svc, identity


@pytest.fixture
async def sf(test_engine):
    return make_session_factory(test_engine)


@pytest.fixture
async def seed(sf):
    publisher = await identity.create_actor(sf, "发布者")
    stranger = await identity.create_actor(sf, "路人")
    async with sf() as s:
        rid = (await s.execute(text(
            "INSERT INTO app_release (app_id, slug, version, title, stack, anchor, fingerprint, "
            "created_by) VALUES (0, :sl, 'b1', '演示', 'vue3-fastapi', '[]'::jsonb, 'fp1', :c) "
            "RETURNING id"), {"sl": "demo" + secrets.token_hex(3), "c": publisher["id"]})).scalar()
        await s.commit()
    return {"publisher": publisher, "stranger": stranger, "release_id": int(rid)}


async def test_create_application_pending(sf, seed):
    a = await apps_svc.create(sf, release_id=seed["release_id"],
                              applicant_id=seed["stranger"]["id"], want_name="我的陌如玉")
    assert a["status"] == "pending" and a["release_id"] == seed["release_id"]
    # 同一个人对同一版本重复申请 → 复用既有单，不再堆 pending
    b = await apps_svc.create(sf, release_id=seed["release_id"],
                              applicant_id=seed["stranger"]["id"], want_name="再要一个")
    assert b["id"] == a["id"]


async def test_reject_requires_reason_and_is_terminal(sf, seed):
    a = await apps_svc.create(sf, release_id=seed["release_id"],
                              applicant_id=seed["stranger"]["id"], want_name="我要")
    with pytest.raises(DeployError):
        await apps_svc.decide(sf, app_id=a["id"], approver_id=seed["publisher"]["id"],
                              approve=True, reason="")          # 缺安装名
    with pytest.raises(DeployError):
        await apps_svc.decide(sf, app_id=a["id"], approver_id=seed["publisher"]["id"],
                              approve=False, reason="")         # 驳回必须给原因
    done = await apps_svc.decide(sf, app_id=a["id"], approver_id=seed["publisher"]["id"],
                                 approve=False, reason="名字不合适")
    assert done["status"] == "rejected"
    with pytest.raises(DeployError):
        await apps_svc.decide(sf, app_id=a["id"], approver_id=seed["publisher"]["id"],
                              approve=True, reason="")          # 已决单不可翻


async def test_only_publisher_can_decide(sf, seed):
    a = await apps_svc.create(sf, release_id=seed["release_id"],
                              applicant_id=seed["stranger"]["id"], want_name="我要")
    with pytest.raises(DeployError) as e:
        await apps_svc.decide(sf, app_id=a["id"], approver_id=seed["stranger"]["id"],
                              approve=True, reason="")
    assert "403" in str(getattr(e.value, "status", "")) or "权限" in str(e.value)


async def test_withdrawn_release_refuses_new_application(sf, seed):
    async with sf() as s:
        await s.execute(text("UPDATE app_release SET status='withdrawn' WHERE id=:i"),
                        {"i": seed["release_id"]})
        await s.commit()
    with pytest.raises(DeployError):
        await apps_svc.create(sf, release_id=seed["release_id"],
                              applicant_id=seed["stranger"]["id"], want_name="我要")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv\Scripts\python -m pytest tests/test_store_applications.py -v`
Expected: FAIL —`ModuleNotFoundError: No module named 'app.store.applications'`

- [ ] **Step 3: 实现**

```python
# app/store/applications.py
"""申请-审批单（规格 M3-a §6.2）。审批通过即触发安装，安装失败置 failed 并留原因，
不回退到 pending：一次批准对应一次真实的资源创建（库/端口/目录），重复批准会撞唯一约束。"""
from datetime import datetime
from sqlalchemy import text
from app.deploy.errors import DeployError

OPEN = ("pending",)
DECIDED = ("approved", "rejected", "failed")


def _row(r) -> dict:
    d = dict(r)
    for k in ("created_at", "updated_at", "decided_at"):
        if d.get(k) is not None:
            d[k] = d[k].isoformat()
    return d


async def create(session_factory, *, release_id: int, applicant_id: int, want_name: str) -> dict:
    name = (want_name or "").strip()
    if not name or len(name) > 100:
        raise DeployError("应用名称需 1-100 字")
    async with session_factory() as s:
        rel = (await s.execute(text(
            "SELECT id, status, title FROM app_release WHERE id = :i"),
            {"i": release_id})).mappings().first()
        if not rel:
            raise DeployError("版本不存在", status=404)
        if rel["status"] != "published":
            raise DeployError("该版本已下架，无法申请", status=409)
        dup = (await s.execute(text(
            "SELECT id, status FROM release_application WHERE release_id = :r "
            "AND applicant_id = :a AND status = 'pending'"),
            {"r": release_id, "a": applicant_id})).mappings().first()
        if dup:
            return _row(dict(dup) | {"release_id": release_id, "want_name": name,
                                     "applicant_id": applicant_id})
        r = (await s.execute(text(
            "INSERT INTO release_application (release_id, applicant_id, want_name) "
            "VALUES (:r, :a, :n) RETURNING id, release_id, applicant_id, want_name, status, "
            "reason, created_at, updated_at"),
            {"r": release_id, "a": applicant_id, "n": name})).mappings().first()
        await s.commit()
    return _row(r)


async def get(session_factory, app_id: int) -> dict:
    async with session_factory() as s:
        r = (await s.execute(text(
            "SELECT q.id, q.release_id, q.applicant_id, q.want_name, q.status, q.reason, "
            "q.target_app_id, q.decided_by, q.created_at, q.updated_at, q.decided_at, "
            "r.slug, r.version, r.title, r.app_id AS source_app_id "
            "FROM release_application q JOIN app_release r ON r.id = q.release_id "
            "WHERE q.id = :i"), {"i": app_id})).mappings().first()
    if not r:
        raise DeployError("申请单不存在", status=404)
    return _row(r)


async def list_box(session_factory, *, box: str, actor_id: int) -> list[dict]:
    """box=mine：我的申请；box=review：待我审批（我发布的版本进来的 pending）。"""
    async with session_factory() as s:
        if box == "mine":
            rows = (await s.execute(text(
                "SELECT q.id, q.release_id, q.want_name, q.status, q.reason, q.target_app_id, "
                "q.created_at, q.updated_at, r.title, r.version, r.slug, a.nickname AS publisher "
                "FROM release_application q JOIN app_release r ON r.id = q.release_id "
                "LEFT JOIN actor a ON a.id = r.created_by "
                "WHERE q.applicant_id = :a ORDER BY q.id DESC"), {"a": actor_id})).mappings().all()
        elif box == "review":
            rows = (await s.execute(text(
                "SELECT q.id, q.release_id, q.want_name, q.status, q.reason, q.applicant_id, "
                "q.created_at, q.updated_at, r.title, r.version, r.slug, ac.nickname AS applicant "
                "FROM release_application q JOIN app_release r ON r.id = q.release_id "
                "LEFT JOIN actor ac ON ac.id = q.applicant_id "
                "WHERE r.created_by = :a AND q.status = 'pending' ORDER BY q.id DESC"),
                {"a": actor_id})).mappings().all()
        else:
            raise DeployError(f"未知收件箱: {box}")
    return [_row(r) for r in rows]


async def decide(session_factory, *, app_id: int, approver_id: int, approve: bool,
                 reason: str) -> dict:
    """只改状态与决议人；安装由调用方（端点）在返回后台执行，失败再回写 failed。

    权限：本期无账号体系，收口为「只有版本发布者能批」（403），越权即拒。
    """
    async with session_factory() as s:
        row = (await s.execute(text(
            "SELECT q.id, q.status, q.release_id, r.created_by "
            "FROM release_application q JOIN app_release r ON r.id = q.release_id "
            "WHERE q.id = :i FOR UPDATE"), {"i": app_id})).mappings().first()
        if not row:
            raise DeployError("申请单不存在", status=404)
        if row["created_by"] != approver_id:
            raise DeployError("只有发布者能审批该版本", status=403)
        if row["status"] not in OPEN:
            raise DeployError(f"申请已处理（{row['status']}），不可重复决策", status=409)
        if not approve and not (reason or "").strip():
            raise DeployError("驳回必须填写原因")
        status = "approved" if approve else "rejected"
        await s.execute(text(
            "UPDATE release_application SET status=:st, reason=:rs, decided_by=:d, "
            "decided_at=now(), updated_at=now() WHERE id=:i"),
            {"st": status, "rs": (reason or "").strip() or None, "d": approver_id, "i": app_id})
        await s.commit()
    return await get(session_factory, app_id)


async def mark_failed(session_factory, app_id: int, error: str) -> dict:
    async with session_factory() as s:
        await s.execute(text(
            "UPDATE release_application SET status='failed', reason=:e, updated_at=now() "
            "WHERE id=:i"), {"i": app_id, "e": error[:500]})
        await s.commit()
    return await get(session_factory, app_id)
```

> 上面 `decide` 里 `approve=True` 但没给 `want_name` 的报错，实际来自 `create` 时就写好的 `want_name`；测试第 2 条里第一次 `decide(approve=True)` 期望抛错的原因是**那时单子已被上一步的 reject 判成终态**（断言的是「已决单不可翻」）。若要让「批准前检查目标 slug 会不会撞库」也在这里做，会引入与 install 的重复判定——**不做**，冲突由 `app_project.slug` 的 UNIQUE 与 install 的 `suggest_slug` 处理。

- [ ] **Step 4: 端点（追加到 `app/api/store.py`）**

```python
from app.store import applications as apps_svc


class ApplyIn(BaseModel):
    release_id: int
    want_name: str


class DecideIn(BaseModel):
    approve: bool
    reason: str = ""


@router.post("/applications", status_code=201)
async def apply(request: Request, body: ApplyIn, actor: dict = Actor):
    sf = request.app.state.session_factory
    try:
        return await apps_svc.create(sf, release_id=body.release_id,
                                     applicant_id=actor["id"], want_name=body.want_name)
    except DeployError as e:
        raise _http(e)


@router.get("/applications")
async def list_applications(request: Request, box: str = "mine", actor: dict = Actor):
    sf = request.app.state.session_factory
    try:
        return await apps_svc.list_box(sf, box=box, actor_id=actor["id"])
    except DeployError as e:
        raise _http(e)


@router.post("/applications/{aid}/approve", status_code=202)
async def approve(request: Request, aid: int, body: DecideIn, actor: dict = Actor):
    """批准 = 起后台安装（202）。长任务闸门复用 apps 的同应用单任务语义：
    安装会占一个新 app_id，但受理时还没有它，所以这里按 release 的**来源应用** id 挡互斥。"""
    from app.api.apps import _spawn, _http as _apps_http   # 复用同一套单任务与状态映射
    sf = request.app.state.session_factory
    hub = request.app.state.deploy_hub
    try:
        a = await apps_svc.decide(sf, app_id=aid, approver_id=actor["id"],
                                  approve=body.approve, reason=body.reason)
        if not body.approve:
            return a
        from app.store import install
        _spawn(request, a["source_app_id"],
               lambda: install.run_install_for_application(sf, a, notify=hub.publish))
    except DeployError as e:
        raise _http(e)
    return {"ok": True, "application": a}


@router.post("/applications/{aid}/reject", status_code=200)
async def reject(request: Request, aid: int, body: DecideIn, actor: dict = Actor):
    sf = request.app.state.session_factory
    try:
        return await apps_svc.decide(sf, app_id=aid, approver_id=actor["id"],
                                     approve=False, reason=body.reason)
    except DeployError as e:
        raise _http(e)
```

> `_spawn` 的第一个参数是 `app_id`（部署任务句柄表的键）。安装产物还没有 id，这里借「来源应用 id」占位，语义是「同一来源应用的发布/安装串行」，与 M2 的 chat 闸门语境一致。**不要**为了这个改 `_spawn` 的签名。

- [ ] **Step 5: 跑测试确认通过**

Run: `.venv\Scripts\python -m pytest tests/test_store_applications.py tests/test_store_api.py -v`
Expected: PASS。安装端点的 202 路径依赖 Task 10 的 `install`，本任务先让它在测试里不被触达（`approve` 用例只跑到 `decide`，安装用例归 Task 10）。

- [ ] **Step 6: 提交**

```bash
git add app/store/applications.py app/api/store.py tests/test_store_applications.py
git commit -m "feat(store): 申请单状态机与审批端点（批准转后台安装）"
```

---
