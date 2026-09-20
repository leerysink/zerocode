# zerocode 部署发布套件 M2（Prod 打包发布）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 M1 的 dev 预览链路延伸到生产：一键发布（构建镜像→prod 库供给→sqlchange 迁移→起栈→体检→记账出 URL）、版本管理（保留 5 版）、一键回滚（仅回程序不回数据库）、局域网 URL 与网卡选择、prod 栈日常管理。

**Architecture:** 复用 M1 的 app/deploy/ 包与 DeployHub 事件流：新增 `prod.py`（发布状态机 7 步 + 回滚 + prod 生命周期）、`records.py`（deploy_record 记账服务）、`nics.py`（局域网地址）、`compose.py` 扩展 prod 渲染；前端新增 `useDeploy` WS composable，发布进度/历史/回滚全部挂在应用页。发布走「202 受理 + 后台任务 + WS publish_step 事件」，与 dev 同一条 `_spawn` 单任务契约。

**Tech Stack:** FastAPI + SQLAlchemy(async) + PostgreSQL 16（容器）+ Docker Compose（镜像 build/up）+ Vue3 + Ant Design Vue + vitest。

**Spec:** `docs/superpowers/specs/2026-09-18-zerocode-deploy-suite/`（主依据 04-Prod打包发布、05-访问URL与端口管理；00 总览 D2/D8；07 sqlchange；08 UI）——计划从规格论证，执行时两文对读。

## Global Constraints

- **执行隔离（本计划最高优先）**：主工作区 `D:\project\zerocode` 有**并行会话的未提交 WIP**（apps.py/engine/hub/AppsView/SideBar 等 948 行「重命名/删除应用+日志面板」在途）。M2 全部施工必须在**独立 worktree** 进行（`using-git-worktrees`：`git worktree add <repo>\..worktrees\m2 feature/zerocode-deploy-m2` 基于届时最新 HEAD；他会话提交会被纳入基线，其**未提交**部分永远不属基线）。任何提交显式列文件，**严禁 `git add -A`/整目录 add**。
- 分支/提交：中文提交信息 `feat(deploy):`/`fix(deploy):`/`test(deploy):`/`docs:`；一任务一提交；文件 UTF-8 无 BOM、LF（`.gitattributes` 已钉 `*.sh eol=lf`，本波新增 `*.conf text eol=lf` 一并入 T3）。
- 平台端口：后端 `8100`（127.0.0.1，不扩界——FR5-06/§4.4）；PG 容器 `zerocode-pg16`（宿主 5433，zcnet 内 5432）；应用端口对 prod=偶/dev=奇（8210-8399），**仅前端端口对外**（prod 绑 `"<prod_port>:80"`，局域网可达）。
- 平台 DDL/DML 一律 `changelog/V<YYYYMMDD>_<两位序号>_<说明>.sql`；**注释不得含 ASCII 分号**；执行器幂等（IF NOT EXISTS / ON CONFLICT DO NOTHING）。已有 V20260917_01/02、V20260918_01、V20260920_01；本波从 **V20260920_02** 起。
- 版本语义：版本号 `b<YYYYMMDDHHmmss>`（本地时间，字符串字典序=时间序）；镜像 tag `zc-<slug>-frontend:prod-<version>` / `zc-<slug>-backend:prod-<version>`；保留最近 `deploy.keep_versions`=5 版镜像（更老 rmi）；**回滚与卸载都不碰 `<slug>_prod` 库**（07/00 D8：数据库永不回滚）。
- 凭据：密码只在 `app_config.dbcred.<slug>.<env>` 与 `deploy/.env`；prod 供给复用 M1 `provision.provision_env(sf, slug, "prod")`；`ensure_deploy_files` 写 .env 时 PROD_* 才补真值（创建时留空的既有行为不变）；任何 API 响应/WS 事件/日志**不得含口令**。
- AI/UI 写保护：`app.json`、`deploy/**` 平台唯一写入方（读写皆禁——M1 终审已封四通道）；本波**不得削弱** `guard.is_protected/is_secret_read`。
- compose 渲染：`__KEY__` 标记 + `${VAR}` 由 deploy/.env 插值的分工不变；prod-compose 顶层 `name: zc-<slug>-prod`、`zcnet: {external: true}`；migrate 服务 `zerocode-sqlrunner:1` 指 PROD_*，backend/frontend `depends_on.migrate: service_completed_successfully`。
- 迁移：prod 用 `sqlrun.run_sqlchange(root, "prod", cred_prod)`（prod 库自有 `sqlchange_history`，增量语义同 dev）；命名规范扫描在 preflight（`V<8位日期>_<2位序号>_<无分号安全名>.sql`）。
- 长任务：一律 `_spawn`（同应用单任务，重复 409）+ 202 受理；publish/rollback 前置**同步** `preflight_docker()`（Task 11/12 契约：预检异常零事件、503 立答）；后台失败必须经事件收口（`publish_done ok:false`），不留悬空状态。
- 状态机：`status_prod: stopped | publishing | running | unhealthy | error`；`publishing` 中禁止第二次发布（_spawn 已挡 + 显式 409 文案「发布进行中」）；平台重启对账：`publishing` 记录标 failed(step=interrupted)，`running` 以 docker 实际容器态核对（04 §6）。
- 测试：pytest（asyncio_mode=auto）+ Docker 依赖用例 `docker_available()` 守卫；**端口断言一律相对化**（不钉死 8210——共享 dev 库可能有其他应用行，本波含 T1 硬化）；全量回归基线以执行时 HEAD 实跑数为准并记台账。
- 前端：中文 UI、素雅低饱和、middle 尺寸；a-table 用 `:columns`+`#bodyCell`；错误提示走 `{detail}` 统一通道；vitest + `npm run build` 是任务门槛。
- 环境（本机踩坑记录）：Docker Desktop 会话中会死（第 4-6 次）——每任务派发前 `docker ps` 确认，修复序列：杀残留进程→`wsl --shutdown`→重启 Desktop→`docker start zerocode-pg16`。

## File Structure（M2 增量）

```
app/deploy/
  records.py        新  deploy_record 读写服务（begin/set_step/finish/list/mark_interrupted）
  nics.py           新  局域网 IPv4 枚举 + default_nic + app_urls 组装
  prod.py           新  发布状态机 publish/rollback + prod 生命周期 + 对账 + keep5 清理 + 镜像存在性
  compose.py        改  +render_prod_compose / ensure_deploy_files 支持 prod creds 与 PROD_VERSION
app/api/
  apps.py           改  +publish/rollback/prod/*/records 端点 + urls + chat_active 闸门
  config_api.py     不变（deploy.default_nic 走既有 PUT /config）
  main.py           改  lifespan 接入 prod.reconcile
app/hub.py          改  ChatHub.active_session_ids()（FR4-09 探测，只增方法）
app/templates/vue3-fastapi/
  frontend/nginx.conf         新  生产反代（/api→backend:8000 + SPA try_files）
  frontend/Dockerfile         改  COPY nginx.conf /etc/nginx/conf.d/default.conf
changelog/
  V20260920_02_deploy_record.sql  新  deploy_record + ix + default_nic 种子
frontend/src/
  composables/useDeploy.js    新  /ws/deploy/{id} 事件流 + 重连 + 事件分发
  views/AppsView.vue          改  发布按钮/进度弹层/完成弹层(URL+排查)/历史抽屉/回滚/网卡选择
  api/index.js                改  +publish/rollback/prod*/records/urls/nics 封装
tests/
  test_deploy_portability.py  新  T1 端口相对化硬化（改造既有用例的辅助断言）
  test_records.py / test_nics.py / test_compose_prod.py(并入 T4 文件) / test_prod_engine.py / test_publish_api.py / test_deploy_migrations.py(追加)
```

---

### Task 1: 测试端口无关化硬化（并行环境护栏）

**Files:**
- Modify: `tests/test_deploy_basics.py`、`tests/test_app_service.py`、`tests/test_apps_dev_api.py`
- Create: （无）

**Interfaces:**
- Consumes: 现状测试。
- Produces: 后续所有任务的门禁可信——任何用例不再因共享 dev 库里**其他应用行存在**而假失败。

背景：M1 执行与冒烟期间三次因并行会话/冒烟残留的 app_project 行踩「固定端口断言」（`== (8210, 8211)`、`8298` 等直接猜位）。修法统一：**期望值从当前 DB 状态推导**。

- [ ] **Step 1: 全量跑一遍，定位所有钉死端口的断言**

Run: `python -m pytest tests -q -x --deselect tests/test_app_service.py::test_create_app_port_race` 前先造干扰：
```sql
INSERT INTO app_project (workspace_id, name, slug, scale, stack, prod_port, dev_port)
VALUES ((SELECT id FROM workspace ORDER BY id LIMIT 1), '占位', 'zportguard', 'static', 'static', 8210, 8211);
```
Expected: `test_deploy_basics::test_allocate_pair_skips_used`、`test_app_service::test_create_app_happy_path`（若断言了具体端口）等**变红**——记录全清单。
测完删除占位行（`DELETE FROM app_project WHERE slug='zportguard'`）。

**Step 2 修正模式（逐一应用，不留清单外用例）**：

1. `test_deploy_basics.py::test_allocate_pair_skips_used`：现状「插 8210/8211 → 期望下一对」改为**先查最小可用对再推导期望**：
```python
async def test_allocate_pair_skips_used(test_engine):
    from app.db import make_session_factory
    sf = make_session_factory(test_engine)
    async with sf() as s:
        # 用真实 workspace 行占住段首两对
        wid = (await s.execute(text(
            "INSERT INTO workspace (name, root_path) VALUES ('guard', 'D:/__zc_guard__') RETURNING id"))).scalar()
        await s.execute(text(
            "INSERT INTO app_project (workspace_id, name, slug, scale, stack, prod_port, dev_port) "
            "VALUES (:w,'g1','zcg1','static','static',8210,8211),( :w,'g2','zcg2','static','static',8212,8213)"),
            {"w": wid})
        p, d = await ports.allocate_pair(s)
        assert (p, d) == (8214, 8215)          # 从登记状态推导，而不是从空库假设
        await s.rollback()                       # 不 commit——TRUNCATE 会清，双保险
```
   （若原用例已近似如此，只需去掉对**全局首对**的隐含假设；conftest TRUNCATE 已含 app_project，跨运行不残留。）
2. `test_app_service.py`：`assert app["prod_port"] % 2 == 0 and app["dev_port"] == app["prod_port"] + 1` 形态保留（本就相对）；**新增用例不引入绝对端口**。若存在 `== 8210` 类断言，改为 `>= 8210` + 奇偶/成对不变式。
3. `test_apps_dev_api.py`：fixture 插入的固定 8294/8295 等高位对**改为段尾探测推导**：
```python
async def _free_pair(sf):
    async with sf() as s:
        p, d = await ports.allocate_pair(s)     # 真实可用对（避开一切已登记/占用）
    return p, d
```
   插 app_project 行时用 `_free_pair` 的结果；dev/start 相关断言只用 `app["ports"]["dev"]`，不写字面量。

- [ ] **Step 3: 复造干扰验证硬化生效**

重复 Step 1 的占位行插入 → `python -m pytest tests -q` → **全部原样绿**（除真实环境失败）。删除占位行。

- [ ] **Step 4: Commit**

```bash
git add tests/test_deploy_basics.py tests/test_app_service.py tests/test_apps_dev_api.py
git commit -m "test(deploy): 端口断言相对化——共享库存在他方应用行时门禁不假红"
```

---

### Task 2: deploy_record 表 + deploy.default_nic 种子

**Files:**
- Create: `changelog/V20260920_02_deploy_record.sql`
- Test: `tests/test_deploy_migrations.py`（追加）

**Interfaces:**
- Produces: 表 `deploy_record(id, app_id, kind, version, status, step, detail, error, started_at, finished_at)` + 索引 `ix_deploy_record_app`；配置键 `deploy.default_nic`（默认空串）。
- Consumes: 既有 `app_project`（FK）。

- [ ] **Step 1: 写失败测试（追加到 tests/test_deploy_migrations.py）**

```python
async def test_deploy_record_table(test_engine):
    async with test_engine.begin() as conn:
        r = await conn.execute(text("SELECT to_regclass('deploy_record')"))
        assert r.scalar() is not None
        cols = (await conn.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'deploy_record'"))).scalars().all()
        assert {"app_id", "kind", "version", "status", "step", "detail", "error",
                "started_at", "finished_at"} <= set(cols)
        idx = (await conn.execute(text(
            "SELECT count(*) FROM pg_indexes WHERE indexname = 'ix_deploy_record_app'"))).scalar()
        assert idx == 1

async def test_default_nic_seeded(test_engine):
    async with test_engine.begin() as conn:
        v = (await conn.execute(text(
            "SELECT value FROM app_config WHERE key = 'deploy.default_nic'"))).scalar()
    assert v == ""
```

- [ ] **Step 2: 跑确认失败**

Run: `python -m pytest tests/test_deploy_migrations.py -v` → 两用例 FAIL（表/键不存在）

- [ ] **Step 3: 实现 changelog/V20260920_02_deploy_record.sql**

```sql
-- changelog/V20260920_02_deploy_record.sql
-- 日期：2026-09-20  用途：M2 发布历史表与默认网卡配置  影响：deploy_record, app_config
-- kind：publish|rollback|start|stop  status：running|success|failed
-- version：b<YYYYMMDDHHmmss> 回滚记录 version=目标旧版  detail 含镜像与迁移清单

CREATE TABLE IF NOT EXISTS deploy_record (
    id          BIGSERIAL PRIMARY KEY,
    app_id      BIGINT NOT NULL REFERENCES app_project(id) ON DELETE CASCADE,
    kind        VARCHAR(10) NOT NULL,
    version     VARCHAR(40),
    status      VARCHAR(20) NOT NULL,
    step        VARCHAR(20),
    detail      JSONB,
    error       TEXT,
    started_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS ix_deploy_record_app ON deploy_record(app_id, started_at DESC);

INSERT INTO app_config (key, value) VALUES ('deploy.default_nic', '""')
ON CONFLICT (key) DO NOTHING;
```

> 注意（M1 教训原样继承）：注释行零 ASCII 分号；`'""'` 是 JSONB 空串，与 M1 的 `deploy.apps_root` 种子同型。

- [ ] **Step 4: 跑确认通过 + 全量**

Run: `python -m pytest tests/test_deploy_migrations.py tests/test_deploy_basics.py -v` 全绿；全量零回归。

- [ ] **Step 5: Commit**

```bash
git add changelog/V20260920_02_deploy_record.sql tests/test_deploy_migrations.py
git commit -m "feat(deploy): deploy_record 发布历史表与默认网卡种子"
```

---

### Task 3: records 记账服务（deploy_record 读写）

**Files:**
- Create: `app/deploy/records.py`
- Test: `tests/test_records.py`

**Interfaces:**
- Consumes: T2 表。
- Produces（prod.py/T8/终审波都靠这些签名）:
  - `records.begin(sf, app_id: int, kind: str, version: str | None, step: str = "preflight") -> int`（status=running 入表，返回 record id）
  - `records.set_step(sf, rid: int, step: str) -> None`
  - `records.finish(sf, rid: int, status: str, *, step: str | None = None, error: str | None = None, detail: dict | None = None) -> None`（status ∈ success|failed；写 finished_at；detail 合并已累积键）
  - `records.list_for(sf, app_id: int, limit: int = 50) -> list[dict]`（started_at 倒序，to_dict 形态 datetime→isoformat）
  - `records.mark_interrupted(sf) -> int`（全表 status=running 的 publish/rollback 记录 → failed, step='interrupted'，返回条数；供 lifespan 对账）
  - `records.versions_for_cleanup(sf, app_id: int, keep: int) -> list[str]`（历史 distinct version 按字符串倒序，跳过前 keep 个返回其余——tag 字典序=时间序）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_records.py
import pytest
from sqlalchemy import text
from app.db import make_engine, make_session_factory
from app.deploy import records, provision

pytestmark = pytest.mark.asyncio

SLUG = "zcrec"

async def _mk_app(sf):
    async with sf() as s:
        wid = (await s.execute(text(
            "INSERT INTO workspace (name, root_path) VALUES ('r', 'D:/__zc_rec__') RETURNING id"))).scalar()
        aid = (await s.execute(text(
            "INSERT INTO app_project (workspace_id, name, slug, scale, stack, prod_port, dev_port) "
            "VALUES (:w,'r','zcrec','small','vue3-fastapi', 8990, 8991) RETURNING id"), {"w": wid})).scalar()
        await s.commit()
    return aid

@pytest.fixture(autouse=True)
async def _clean():
    sf = make_session_factory(make_engine())
    async with sf() as s:
        await s.execute(text("DELETE FROM deploy_record WHERE app_id IN "
                             "(SELECT id FROM app_project WHERE slug=:s)"), {"s": SLUG})
        await s.execute(text("DELETE FROM app_project WHERE slug=:s"), {"s": SLUG})
        await s.execute(text("DELETE FROM workspace WHERE root_path='D:/__zc_rec__'"))
        await s.commit()
    yield
    async with sf() as s:
        await s.execute(text("DELETE FROM deploy_record WHERE app_id IN "
                             "(SELECT id FROM app_project WHERE slug=:s)"), {"s": SLUG})
        await s.execute(text("DELETE FROM app_project WHERE slug=:s"), {"s": SLUG})
        await s.execute(text("DELETE FROM workspace WHERE root_path='D:/__zc_rec__'"))
        await s.commit()

async def test_lifecycle_begin_step_finish():
    sf = make_session_factory(make_engine())
    aid = await _mk_app(sf)
    rid = await records.begin(sf, aid, "publish", "b20260920120000")
    await records.set_step(sf, rid, "build")
    await records.finish(sf, rid, "success", step="record",
                         detail={"images": ["zc-zcrec-frontend:prod-b20260920120000"],
                                 "sqlApplied": ["V20260920_01_init.sql"], "durationMs": 123})
    rows = await records.list_for(sf, aid)
    assert len(rows) == 1 and rows[0]["status"] == "success"
    assert rows[0]["detail"]["durationMs"] == 123
    assert rows[0]["finished_at"] is not None and "step" in rows[0]

async def test_failed_finish_keeps_error_and_step():
    sf = make_session_factory(make_engine())
    aid = await _mk_app(sf)
    rid = await records.begin(sf, aid, "publish", "b20260920120100")
    await records.set_step(sf, rid, "migrate")
    await records.finish(sf, rid, "failed", error="sqlrunner 退出码 2")
    r = (await records.list_for(sf, aid))[0]
    assert r["status"] == "failed" and r["step"] == "migrate" and "退出码" in r["error"]

async def test_mark_interrupted():
    sf = make_session_factory(make_engine())
    aid = await _mk_app(sf)
    rid = await records.begin(sf, aid, "publish", "b20260920120200")
    n = await records.mark_interrupted(sf)
    assert n >= 1
    assert (await records.list_for(sf, aid))[0]["step"] == "interrupted"
    assert await records.mark_interrupted(sf) == 0      # 幂等：无 running 可标

async def test_versions_for_cleanup():
    sf = make_session_factory(make_engine())
    aid = await _mk_app(sf)
    for v in ["b20260920120000", "b20260920130000", "b20260920140000"]:
        rid = await records.begin(sf, aid, "publish", v)
        await records.finish(sf, rid, "success")
    await records.begin(sf, aid, "rollback", "b20260920120000")   # rollback 记录不参与版本清单
    out = await records.versions_for_cleanup(sf, aid, keep=2)
    assert out == ["b20260920120000"]                             # 三个 publish 版本只留最新 2
```

- [ ] **Step 2: RED 确认**（ModuleNotFoundError: app.deploy.records）

- [ ] **Step 3: 实现**

```python
# app/deploy/records.py
"""deploy_record 记账（规格 04 §4.5）：发布/回滚/启停全程留痕，UI 历史与 keep-N 清理的数据源。"""
import json
from sqlalchemy import text

async def begin(sf, app_id: int, kind: str, version: str | None, step: str = "preflight") -> int:
    async with sf() as s:
        rid = (await s.execute(text(
            "INSERT INTO deploy_record (app_id, kind, version, status, step) "
            "VALUES (:a, :k, :v, 'running', :st) RETURNING id"),
            {"a": app_id, "k": kind, "v": version, "st": step})).scalar()
        await s.commit()
    return rid

async def set_step(sf, rid: int, step: str) -> None:
    async with sf() as s:
        await s.execute(text("UPDATE deploy_record SET step = :st WHERE id = :i"),
                        {"st": step, "i": rid})
        await s.commit()

async def finish(sf, rid: int, status: str, *, step: str | None = None,
                 error: str | None = None, detail: dict | None = None) -> None:
    if status not in ("success", "failed"):
        raise ValueError(f"finish 状态非法: {status}")
    async with sf() as s:
        await s.execute(text(
            "UPDATE deploy_record SET status = :st, finished_at = now(), "
            "step = COALESCE(:stp, step), error = COALESCE(:err, error), "
            "detail = CASE WHEN :dt::jsonb IS NULL THEN detail ELSE detail || :dt::jsonb END "
            "WHERE id = :i"),
            {"st": status, "stp": step, "err": error,
             "dt": json.dumps(detail or {}), "i": rid})
        await s.commit()

async def list_for(sf, app_id: int, limit: int = 50) -> list[dict]:
    async with sf() as s:
        rows = (await s.execute(text(
            "SELECT id, app_id, kind, version, status, step, detail, error, "
            "started_at, finished_at FROM deploy_record WHERE app_id = :a "
            "ORDER BY started_at DESC LIMIT :l"), {"a": app_id, "l": int(limit)})).mappings().all()
    out = []
    for r in rows:
        d = dict(r)
        for k in ("started_at", "finished_at"):
            if d.get(k) is not None:
                d[k] = d[k].isoformat()
        out.append(d)
    return out

async def mark_interrupted(sf) -> int:
    """平台重启对账（04 §6）：上次崩溃遗留的 running 发布/回滚记录标 failed+interrupted。"""
    async with sf() as s:
        res = await s.execute(text(
            "UPDATE deploy_record SET status='failed', step='interrupted', finished_at=now(), "
            "error='平台重启中断' WHERE status='running' AND kind IN ('publish','rollback')"))
        await s.commit()
        return res.rowcount

async def versions_for_cleanup(sf, app_id: int, keep: int) -> list[str]:
    """可清理的旧版本 tag 清单：只数 publish 成功过的版本（字典序=时间序），rollback/start 记录不算数。"""
    async with sf() as s:
        rows = (await s.execute(text(
            "SELECT DISTINCT version FROM deploy_record "
            "WHERE app_id = :a AND kind = 'publish' AND version IS NOT NULL "
            "ORDER BY version DESC"), {"a": app_id})).scalars().all()
    return [v for v in rows[max(int(keep), 1):]]
```

> 实现注意：`detail || :dt::jsonb` 的合并语义要求 begin 时 detail 非 NULL 或先初始化——`||` 遇 NULL 返回 NULL，begin 未写 detail（默认 NULL），finish 首并即 NULL 吞数据。处理：finish 用 `COALESCE(detail,'{}'::jsonb) || :dt::jsonb`，落地时改上式（此处是计划自检发现，直接写对：把 `"detail = CASE ..."` 分支换成 `"detail = COALESCE(detail,'{}'::jsonb) || :dt::jsonb"` 并允许 detail 为空 dict 时保留原值——即 `WHERE ... CASE WHEN :dt::jsonb = '{}'::jsonb THEN detail ELSE COALESCE(detail,'{}'::jsonb) || :dt::jsonb END`）。

- [ ] **Step 4: GREEN + 全量**

Run: `python -m pytest tests/test_records.py tests/test_deploy_migrations.py -v` 全绿；全量零回归。

- [ ] **Step 5: Commit**

```bash
git add app/deploy/records.py tests/test_records.py
git commit -m "feat(deploy): deploy_record 记账服务（begin/step/finish/对账/keep-N 清单）"
```

---

### Task 3b: 模板生产化接缝（nginx 反代 + Dockerfile）

**Files:**
- Modify: `app/templates/vue3-fastapi/frontend/Dockerfile`
- Create: `app/templates/vue3-fastapi/frontend/nginx.conf`
- Modify: `.gitattributes`（+`*.conf text eol=lf`）
- Test: `tests/test_scaffold.py`（追加）+ `app/deploy/scaffold.py`（INIT 铺底清单自动含新文件——rglob 天然覆盖，核实即可）

**Interfaces:**
- Produces: 模板产物含 `frontend/nginx.conf`；prod 前端镜像（nginx:1.27-alpine）具备 `/api` 反代与 SPA try_files。prod 体检因此可以单 URL（`http://<prod_port>/api/health`）判定前后端联合健康（04 §4.3「取最简可靠路径」的落点）。
- Consumes: 无。

- [ ] **Step 1: 写失败测试**

```python
def test_template_nginx_prod_proxy(tmp_path):
    from app.deploy import scaffold
    mapping = {"SLUG": "lailiao", "APP_NAME": "来料登记", "DEV_PORT": "8211", "PROD_PORT": "8210",
               "DEV_DB": "lailiao_dev", "PROD_DB": "lailiao_prod", "TODAY": "20260920"}
    scaffold.render(TEMPLATE, tmp_path, mapping)
    ng = (tmp_path / "frontend" / "nginx.conf").read_text(encoding="utf-8")
    assert "proxy_pass http://backend:8000" in ng and "try_files" in ng
    dk = (tmp_path / "frontend" / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY nginx.conf /etc/nginx/conf.d/default.conf" in dk
```

- [ ] **Step 2: RED**（渲染产物无 nginx.conf）

- [ ] **Step 3: 实现**

`app/templates/vue3-fastapi/frontend/nginx.conf`：

```nginx
server {
    listen 80;
    server_name _;
    root /usr/share/nginx/html;
    index index.html;

    # 应用后端反代：生产栈里前端不再依赖 vite dev proxy（规格 04 §4.2）
    location /api/ {
        proxy_pass http://backend:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }

    # SPA 路由回落（history 模式刷新不 404）
    location / {
        try_files $uri $uri/ /index.html;
    }
}
```

`app/templates/vue3-fastapi/frontend/Dockerfile` 第二段改为：

```dockerfile
FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /src/dist /usr/share/nginx/html
```

`.gitattributes` 追加一行 `*.conf text eol=lf`（nginx 在 Linux 容器里吃 CRLF 注释行无碍，但 conf 文件同样按 LF 钉死保一致）。

- [ ] **Step 4: GREEN + scaffold 全量 + 全量回归**

- [ ] **Step 5: Commit**

```bash
git add app/templates/vue3-fastapi/frontend/nginx.conf app/templates/vue3-fastapi/frontend/Dockerfile .gitattributes tests/test_scaffold.py
git commit -m "feat(deploy): 模板生产化接缝——nginx 反代 /api 与 SPA 回落"
```

---

### Task 4: prod compose 渲染 + .env PROD_* 补齐

**Files:**
- Modify: `app/deploy/compose.py`
- Test: `tests/test_compose_render.py`（追加）

**Interfaces:**
- Consumes: T3b（backend 经 nginx 反代后 prod 前端对外单端口 80）、既有 `render_env/ensure_deploy_files` 模式。
- Produces:
  - `compose.render_prod_compose(meta) -> str`（vue3-fastapi 栈；static 抛 DeployError「未支持」——与 dev 同口径，static 发布 M3）
  - `compose.ensure_deploy_files(root, meta, creds, version: str | None = None) -> None`：**签名向后兼容**（version 缺省不写 PROD_VERSION 行为不变）；有 prod creds 时把 `.env` 的 PROD_* 补真值，并渲染/更新 `deploy/prod-compose.yml`；`PROD_VERSION=<version>` 行写入或更新（version=None 时保留既有值）
  - `.env` 新键：`PROD_VERSION`

- [ ] **Step 1: 写失败测试（追加到 tests/test_compose_render.py）**

```python
def test_render_prod_compose_fastapi():
    import yaml
    y = compose.render_prod_compose(META)
    doc = yaml.safe_load(y)
    assert doc["name"] == "zc-lailiao-prod"
    assert doc["networks"]["zcnet"] == {"external": True}
    svc = doc["services"]
    assert set(svc) == {"migrate", "backend", "frontend"}
    assert svc["migrate"]["image"] == "zerocode-sqlrunner:1"
    assert svc["migrate"]["environment"]["TARGET_DB"] == "${PROD_DB}"       # 闸门指 prod 库
    assert svc["backend"]["image"] == "zc-lailiao-backend:prod-${PROD_VERSION}"
    assert svc["backend"]["build"] == "../backend"
    assert svc["backend"]["restart"] == "unless-stopped"
    assert "${PROD_PASSWORD}" in svc["backend"]["environment"]["DATABASE_URL"]
    assert svc["backend"]["depends_on"]["migrate"]["condition"] == "service_completed_successfully"
    assert svc["frontend"]["image"] == "zc-lailiao-frontend:prod-${PROD_VERSION}"
    assert svc["frontend"]["build"] == "../frontend"
    assert svc["frontend"]["ports"] == ["8210:80"]                          # 仅前端对外，prod 端口
    for name in ("backend", "frontend"):
        assert "ports" not in svc[name] or name == "frontend"
    assert "volumes" not in (svc["frontend"] if isinstance(svc["frontend"], dict) else {}) or True
    # prod 栈不挂源码卷、无 dev server 命令（镜像即产物）
    assert "bind" not in y and "npm run dev" not in y and "uvicorn --reload" not in y

def test_ensure_deploy_files_prod_updates_env(tmp_path):
    (tmp_path / "deploy").mkdir()
    compose.ensure_deploy_files(tmp_path, META, {"dev": CRED["dev"]})          # 创建态：PROD_* 空
    compose.ensure_deploy_files(tmp_path, META, {"dev": CRED["dev"], "prod": PROD_CRED},
                                version="b20260920120000")
    env = (tmp_path / "deploy" / ".env").read_text(encoding="utf-8")
    assert "PROD_DB=lailiao_prod" in env and "PROD_USER=lailiao_prod" in env
    assert "PROD_PASSWORD=pwprod" in env and "PROD_VERSION=b20260920120000" in env
    assert (tmp_path / "deploy" / "prod-compose.yml").is_file()
    # 再次发布换版本：PROD_VERSION 被更新、其余键不重复堆行
    compose.ensure_deploy_files(tmp_path, META, {"dev": CRED["dev"], "prod": PROD_CRED},
                                version="b20260920130000")
    env2 = (tmp_path / "deploy" / ".env").read_text(encoding="utf-8")
    assert env2.count("PROD_VERSION=") == 1 and "PROD_VERSION=b20260920130000" in env2
    assert "DEV_PASSWORD=pw123" in env2                                       # dev 键不丢

PROD_CRED = {"db": "lailiao_prod", "user": "lailiao_prod", "password": "pwprod"}
```

- [ ] **Step 2: RED 确认**

- [ ] **Step 3: 实现**

```python
# ---- compose.py 追加（模块常量区 + 新函数 + 改 ensure_deploy_files）----

_PROD_COMPOSE = """name: zc-__SLUG__-prod
networks:
  default: {}
  zcnet:
    external: true
services:
__MIGRATE_PROD__
  backend:
    image: zc-__SLUG__-backend:prod-${PROD_VERSION}
    build: ../backend
    environment:
      DATABASE_URL: postgresql+asyncpg://${PROD_USER}:${PROD_PASSWORD}@zerocode-pg16:5432/${PROD_DB}
      APP_PORT: "8000"
    networks: [default, zcnet]
    restart: unless-stopped
    depends_on:
      migrate:
        condition: service_completed_successfully
  frontend:
    image: zc-__SLUG__-frontend:prod-${PROD_VERSION}
    build: ../frontend
    networks: [default]
    ports:
      - "__PROD_PORT__:80"
    restart: unless-stopped
    depends_on:
      - backend
"""

_MIGRATE_PROD = """  migrate:
    image: zerocode-sqlrunner:1
    environment:
      TARGET_DB: ${PROD_DB}
      TARGET_USER: ${PROD_USER}
      TARGET_PASSWORD: ${PROD_PASSWORD}
    volumes:
      - ../sqlchange:/sql:ro
    networks: [zcnet]
    restart: "no"
"""

def render_prod_compose(meta: dict) -> str:
    stack = meta.get("stack")
    if stack != "vue3-fastapi":
        raise DeployError(f"栈发布未支持（M2 范围）: {stack}", status=400)
    sources = _PROD_COMPOSE + _MIGRATE_PROD
    y = (sources.replace("__MIGRATE_PROD__", _MIGRATE_PROD)
         .replace("__PROD_PORT__", str(meta["ports"]["prod"]))
         .replace("__SLUG__", meta["slug"]))
    residue = sorted(m for m in _markers_of(sources) if m in y)
    if residue:
        raise DeployError(f"compose 渲染标记残留: {residue}", status=500)
    return y
```

`ensure_deploy_files` 重写（保持 dev 行为逐字节不变是硬要求——既有断言不红）：

```python
def ensure_deploy_files(app_root: Path, meta: dict, creds: dict, version: str | None = None) -> None:
    d = app_root / "deploy"
    d.mkdir(parents=True, exist_ok=True)
    y = render_dev_compose(meta)
    e = render_env(meta, creds)
    ef = d / ".env"
    if ef.exists():
        prev = ef.read_text(encoding="utf-8")
        # PROD_VERSION 是唯一「渲染态」键：不随 meta/creds 生成，只被发布流程显式更新
        pv = _env_get(prev, "PROD_VERSION")
        if version is not None:
            pv = version
        if pv is not None:
            e = e + f"PROD_VERSION={pv}\n"
        # 已有 .env 的 PROD_* 若新 creds 没带（只传 dev 的调用），保留旧值不冲空
        for k in ("PROD_DB", "PROD_USER", "PROD_PASSWORD"):
            old = _env_get(prev, k)
            if old and not _env_get(e, k):
                e = _env_set(e, k, old)
    _write_if_changed(d / "dev-compose.yml", y)
    _write_if_changed(ef, e)
    if (meta.get("stack") == "vue3-fastapi"
            and (creds.get("prod") or {}).get("password")):
        _write_if_changed(d / "prod-compose.yml", render_prod_compose(meta))
```

辅助（文件内私有，`newline=""` 纪律不变）：

```python
def _env_get(text_: str, key: str) -> str | None:
    for line in text_.splitlines():
        if line.startswith(key + "="):
            return line.split("=", 1)[1]
    return None

def _env_set(text_: str, key: str, val: str) -> str:
    return "\n".join(l for l in text_.splitlines() if not l.startswith(key + "=")) + f"\n{key}={val}\n"

def _write_if_changed(f: Path, content: str) -> None:
    if not f.exists() or f.read_text(encoding="utf-8") != content:
        f.write_text(content, encoding="utf-8", newline="")
```

> 实现注意：①`render_env` 对 prod 空 creds 产出 `PROD_DB=`（空值）——`_env_get(prev,...)` 判「truthy」保留旧值、空串不触发保留，正好让「创建态空占位 → 发布态真值」单向推进；②prod-compose 的落盘条件锁「prod creds 齐 + 栈支持」，创建流程（无 prod creds）行为与 M1 完全一致；③`_write_if_changed` 收口三处重复的写盘样板（DRY）。

- [ ] **Step 4: GREEN + 既有 compose 用例不红 + 全量**

Run: `python -m pytest tests/test_compose_render.py -v` 全绿；全量零回归（Task 5 app_service 对 ensure_deploy_files 的旧调用签名必须原样工作——它是向后兼容的试金石）。

- [ ] **Step 5: Commit**

```bash
git add app/deploy/compose.py tests/test_compose_render.py
git commit -m "feat(deploy): prod compose 渲染与 PROD_*/PROD_VERSION 环境补齐"
```

---

### Task 5: 局域网地址服务（nics + urls + README 刷新）

**Files:**
- Create: `app/deploy/nics.py`
- Modify: `app/api/apps.py`（+GET /{id}/urls、GET /api/system/nics 放 config_api? 否——新文件 app/api/system.py 挂 /api/system 前缀）
- Create: `app/api/system.py`
- Modify: `app/main.py`（include system router）
- Test: `tests/test_nics.py`

**Interfaces:**
- Produces:
  - `nics.lan_candidates() -> list[dict]`：`[{ip, primary: bool, docker_hint: bool}]`；双途径并集（getaddrinfo(hostname) + UDP 8.8.8.8 出口探测），过滤 127.*/169.254.*，172.16-31 标 `docker_hint`
  - `nics.default_nic(sf) -> str`（读 `deploy.default_nic`，可空）
  - `nics.pick_lan_ip(sf) -> str`（default_nic 优先，其次 primary，都无 → ""）
  - `nics.app_urls(sf, app) -> dict`：`{localhost, lan, lanCandidates, defaultNic}`（lan 空串=无可用网卡）
  - `nics.update_readme(root: Path, url: str, version: str) -> None`：README 顶部标记块（幂等替换）
- Consumes: deploy_cfg（T settings 已有）。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_nics.py
import pytest
from app.deploy import nics
from app.db import make_engine, make_session_factory

pytestmark = pytest.mark.asyncio

def test_candidates_shape_and_filters(monkeypatch):
    monkeypatch.setattr(nics, "_via_hostname", lambda: ["10.1.2.3", "127.0.0.1", "169.254.9.9", "172.28.5.1"])
    monkeypatch.setattr(nics, "_via_udp", lambda: "10.1.2.3")
    cands = nics.lan_candidates()
    ips = [c["ip"] for c in cands]
    assert "10.1.2.3" in ips and "127.0.0.1" not in ips and "169.254.9.9" not in ips
    assert next(c for c in cands if c["ip"] == "10.1.2.3")["primary"] is True
    assert next(c for c in cands if c["ip"] == "172.28.5.1")["docker_hint"] is True

def test_update_readme_marker_block(tmp_path):
    (tmp_path / "README.md").write_text("# 应用\n\n正文\n", encoding="utf-8")
    nics.update_readme(tmp_path, "http://10.1.2.3:8210", "b20260920120000")
    t = (tmp_path / "README.md").read_text(encoding="utf-8")
    assert "<!-- zc:publish -->" in t and "http://10.1.2.3:8210" in t and t.count("# 应用") == 1
    nics.update_readme(tmp_path, "http://10.1.2.3:8210", "b20260920130000")   # 幂等刷新不堆叠
    t2 = (tmp_path / "README.md").read_text(encoding="utf-8")
    assert t2.count("<!-- zc:publish -->") == 2 and "b20260920130000" in t2 and "b20260920120000" not in t2

async def test_pick_and_urls(monkeypatch):
    sf = make_session_factory(make_engine())
    monkeypatch.setattr(nics, "_via_hostname", lambda: ["192.168.1.50"])
    monkeypatch.setattr(nics, "_via_udp", lambda: "192.168.1.50")
    from sqlalchemy import text as t
    async with sf() as s:
        await s.execute(t("INSERT INTO app_config (key, value) VALUES ('deploy.default_nic', '\"192.168.1.50\"') "
                          "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"))
        await s.commit()
    assert nics.pick_lan_ip.__wrapped__ if hasattr(nics.pick_lan_ip, "__wrapped__") else True
    urls = await nics.app_urls(sf, {"ports": {"prod": 8210}, "prod_port": 8210})
    assert urls["localhost"] == "http://localhost:8210"
    assert urls["lan"] == "http://192.168.1.50:8210"
    assert any(c["ip"] == "192.168.1.50" for c in urls["lanCandidates"])
```

- [ ] **Step 2: RED** → **Step 3: 实现**

```python
# app/deploy/nics.py
"""局域网地址（规格 05 FR5-03/§4.2）：网卡枚举双途径 + default_nic 记忆 + README 发布块。
纯 socket 探测不发包（UDP connect 只选路）；平台不改防火墙（05 §4.3 安全边界）。"""
import ipaddress
import re
import socket
from pathlib import Path

MARK_START, MARK_END = "<!-- zc:publish -->", "<!-- /zc:publish -->"

def _via_hostname() -> list[str]:
    try:
        return sorted({ai[4][0] for ai in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)})
    except OSError:
        return []

def _via_udp() -> str:
    """UDP「连接」8.8.8.8：不发包，只让内核选出默认出口网卡地址。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return ""
    finally:
        s.close()

def _usable(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return not (a.is_loopback or a.is_link_local or a.is_unspecified)

def lan_candidates() -> list[dict]:
    primary = _via_udp()
    ips = [primary] if primary else []
    ips += [i for i in _via_hostname() if i != primary]
    seen: list[dict] = []
    for ip in ips:
        if not _usable(ip) or any(s["ip"] == ip for s in seen):
            continue
        a = ipaddress.ip_address(ip)
        seen.append({"ip": ip, "primary": ip == primary,
                     "docker_hint": a in ipaddress.ip_network("172.16.0.0/12")})
    return seen

async def default_nic(sf) -> str:
    from app.deploy.settings import deploy_cfg
    return (await deploy_cfg(sf)).get("deploy.default_nic", "") or ""

async def pick_lan_ip(sf) -> str:
    dflt = await default_nic(sf)
    cands = lan_candidates()
    if dflt and any(c["ip"] == dflt for c in cands):
        return dflt
    return cands[0]["ip"] if cands else ""

async def app_urls(sf, app: dict) -> dict:
    ports = app.get("ports") if isinstance(app.get("ports"), dict) else {}
    prod = ports.get("prod", app.get("prod_port"))
    lan_ip = await pick_lan_ip(sf)
    return {"localhost": f"http://localhost:{prod}",
            "lan": (f"http://{lan_ip}:{prod}" if lan_ip else ""),
            "lanCandidates": lan_candidates(),
            "defaultNic": await default_nic(sf)}

def update_readme(root: Path, url: str, version: str) -> None:
    """README 顶部发布块（05 §4.2）：标记对之间整体替换，幂等；无 README 不报错（只跳过）。"""
    f = root / "README.md"
    block = (f"{MARK_START}\n> 正式发布地址：{url}（版本 {version}，平台每次发布刷新）\n{MARK_END}\n")
    if not f.is_file():
        return
    text_ = f.read_text(encoding="utf-8")
    pat = re.compile(re.escape(MARK_START) + r".*?" + re.escape(MARK_END) + r"\n?", re.S)
    if pat.search(text_):
        text_ = pat.sub(block, text_, count=1)
    else:
        text_ = block + "\n" + text_
    f.write_text(text_, encoding="utf-8", newline="")
```

`app/api/system.py`：

```python
"""系统级只读信息（规格 05 §5）：网卡候选。"""
from fastapi import APIRouter, Request
from app.deploy import nics

router = APIRouter(prefix="/api/system")

@router.get("/nics")
async def get_nics(request: Request):
    return {"candidates": nics.lan_candidates(), "defaultNic": await nics.default_nic(request.app.state.session_factory)}
```

`app/api/apps.py` 追加：

```python
@router.get("/{app_id}/urls")
async def app_urls(request: Request, app_id: int):
    sf = request.app.state.session_factory
    try:
        app = await app_service.get_app(sf, app_id)
        from app.deploy import nics
        return await nics.app_urls(sf, app)
    except DeployError as e:
        raise _http(e)
```

`app/main.py` include 区（deploy_ws 旁）：`from app.api import system` + `app.include_router(system.router)`。

- [ ] **Step 4: GREEN + 全量** → **Step 5: Commit**

```bash
git add app/deploy/nics.py app/api/system.py app/api/apps.py app/main.py tests/test_nics.py
git commit -m "feat(deploy): 局域网地址服务（网卡枚举+default_nic+urls 端点+README 发布块）"
```

---

### Task 6: prod 生命周期（启停/状态/对账/镜像存在性）

**Files:**
- Create: `app/deploy/prod.py`
- Test: `tests/test_prod_engine.py`

**Interfaces:**
- Consumes: `engine.docker/preflight_docker/_app_root/_compose_meta/_notify/wait_frontend_health`（模块级复用，不复制实现——终审「engine.docker 是范例」的落点）、`compose.ensure_deploy_files(…, version=)`（T4）、`provision.read_cred`、`records.mark_interrupted`（T3）、`nics.app_urls`（T5）。
- Produces（T7/T8 依赖的精确签名）:
  - `prod.STATUS_COL = "status_prod"`；合法值 `stopped|starting|publishing|running|unhealthy|error`（04 §4.1 五态 + 采纳 `starting`——恢复场景需要进行中反馈，T11 规格勘误波回写 04）
  - `prod.set_status(sf, app_id, status)`；`prod.get_status(sf, app_id) -> str`
  - `prod.prod_start(sf, app, notify=None) -> dict` / `prod_stop` / `prod_restart` / `prod_status(sf, app) -> dict`
  - `prod.wait_prod_health(app_dir, root, port, timeout, stack) -> tuple[bool, dict]`（`{"frontend": bool, "api": bool}`——static 栈 api 恒 True）
  - `prod.image_exists(tag: str) -> bool`（async）
  - `prod.reconcile(sf, notify=None) -> None`（lifespan 启动对账）
- 事件（经 notify=hub.publish）：`prod_status {status}`、`dev_log`（步骤行复用 `_step` 形态，前端日志面板同源）、`compose_ps` 文本仅经端点回，不进事件。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_prod_engine.py
import asyncio, pytest
from app.deploy import prod, engine
from app.deploy.errors import DeployError

pytestmark = pytest.mark.asyncio

APP = {"id": 1, "slug": "zcdemo", "stack": "vue3-fastapi",
       "ports": {"prod": 8210, "dev": 8211}, "prod_port": 8210, "status_prod": "stopped"}

class Seams:
    """替身集中营：prod.py 对 engine/provision/compose/records 的调用一律模块级名字（D-1 惯例）。"""
    def __init__(self, monkeypatch, up_code=0):
        self.calls, self.events = [], []
        self.up_code = up_code
        async def docker(*a, **k):
            self.calls.append(a)
            return (self.up_code if a[:3] == ("compose", "-f", "x") else 0, "ok")
        async def preflight():
            self.calls.append(("preflight",))
        async def app_root(sf, app):
            from pathlib import Path
            return Path("D:/__zc_prod_test__")
        async def health(root, port, timeout, stack="vue3-fastapi"):
            self.calls.append(("health", port))
            return True, {"frontend": True, "api": True}
        async def set_status(sf, aid, st):
            self.calls.append(("status", st))
        monkeypatch.setattr(prod, "_docker", docker)
        monkeypatch.setattr(prod, "preflight_docker", preflight)
        monkeypatch.setattr(prod, "_app_root", app_root)
        monkeypatch.setattr(prod, "wait_prod_health", health)
        monkeypatch.setattr(prod, "set_status", set_status)
    def notify(self, app_id, ev):
        self.events.append(ev)

async def test_start_stop_events_and_sequence(monkeypatch, test_engine):
    from app.db import make_session_factory
    from sqlalchemy import text as t
    sf = make_session_factory(test_engine)
    async with sf() as s:
        wid = (await s.execute(t("INSERT INTO workspace (name, root_path) "
                                 "VALUES ('p','D:/__zc_prod_test__') RETURNING id"))).scalar()
        aid = (await s.execute(t("INSERT INTO app_project (workspace_id, name, slug, scale, stack,"
                                 " prod_port, dev_port) VALUES (:w,'p','zcdemo','small','vue3-fastapi',"
                                 " 8998, 8999) RETURNING id"), {"w": wid})).scalar()
        await s.commit()
    app = dict(APP, id=aid, prod_port=8998)
    seams = Seams(monkeypatch)
    # creds 齐：monkeypatch read_cred 与 ensure_deploy_files
    async def cred(sf, slug, env):
        return {"db": "zcdemo_prod", "user": "zcdemo_prod", "password": "x"}
    def ensure(root, meta, creds, version=None):
        seams.calls.append(("ensure", version))
    monkeypatch.setattr(prod, "read_cred", cred)
    monkeypatch.setattr(prod, "ensure_deploy_files", ensure)
    res = await prod.prod_start(sf, app, notify=seams.notify)
    assert res["status"] == "running"
    seq = [c for c in seams.calls if c[0] == "status"]
    assert seq == [("status", "starting"), ("status", "running")]
    assert seams.events[0]["type"] == "prod_status" and seams.events[-1]["status"] == "running"
    async with sf() as s:
        st = (await s.execute(t("SELECT status_prod FROM app_project WHERE id=:i"), {"i": aid})).scalar()
    assert st == "running"          # 真写库（set_status 未被 seam 时）——见下方注：seam 里 set_status 被替换，
                                     # 这条断言走的是「真实 set_status」——若 Seams 替换了它则删此断言，保事件序即可
    await prod.prod_stop(sf, app, notify=seams.notify)
    assert ("status", "stopped") in [c for c in seams.calls if c[0] == "status"]
    async with sf() as s:
        await s.execute(t("DELETE FROM app_project WHERE id=:i"), {"i": aid})
        await s.execute(t("DELETE FROM workspace WHERE id=:i"), {"i": wid})
        await s.commit()

async def test_start_requires_prod_cred(monkeypatch, test_engine):
    from app.db import make_session_factory
    sf = make_session_factory(test_engine)
    async def no_cred(sf, slug, env):
        return None
    async def root(sf, app):
        from pathlib import Path
        return Path("D:/none")
    monkeypatch.setattr(prod, "preflight_docker", lambda: asyncio.sleep(0))
    monkeypatch.setattr(prod, "_app_root", root)
    monkeypatch.setattr(prod, "read_cred", no_cred)
    with pytest.raises(DeployError) as ei:
        await prod.prod_start(sf, {"id": 999, **APP}, notify=None)
    assert ei.value.status == 409 and "凭据" in str(ei.value)

async def test_reconcile_marks_publishing_and_matches_docker(monkeypatch, test_engine):
    from app.db import make_session_factory
    from sqlalchemy import text as t
    sf = make_session_factory(test_engine)
    async with sf() as s:
        wid = (await s.execute(t("INSERT INTO workspace (name, root_path) "
                                 "VALUES ('r','D:/__zc_rec2__') RETURNING id"))).scalar()
        aid = (await s.execute(t("INSERT INTO app_project (workspace_id, name, slug, scale, stack,"
                                 " prod_port, dev_port, status_prod) VALUES (:w,'r','zcrec2','small',"
                                 " 'vue3-fastapi', 8996, 8997, 'publishing') RETURNING id"),
                                 {"w": wid})).scalar()
        await s.commit()
    calls = []
    async def docker(*a, **k):
        calls.append(a)
        return 0, ""                 # ps -q 输出空 = 无容器
    monkeypatch.setattr(prod, "_docker", docker)
    async def no_int(sf_):
        return 0
    monkeypatch.setattr(prod.records, "mark_interrupted", no_int)
    await prod.reconcile(sf, notify=None)
    async with sf() as s:
        st = (await s.execute(t("SELECT status_prod FROM app_project WHERE id=:i"), {"i": aid})).scalar()
    assert st == "stopped"           # publishing 且无容器 → stopped（发布要重发）
    async with sf() as s:
        await s.execute(t("DELETE FROM app_project WHERE id=:i"), {"i": aid})
        await s.execute(t("DELETE FROM workspace WHERE id=:i"), {"i": wid})
        await s.commit()
```

> 实现注意：Seams 的 `set_status` 替换与「真写库」断言二选一——**测试以实现形态为准调整**：推荐 `set_status` 不 seam（它只做一件事且便宜），Seams 移除该替换；上方用例据此删注。派发时以「事件与调用序完整、断言不恒真」为准绳自查。

- [ ] **Step 2: RED**（ModuleNotFoundError: app.deploy.prod）

- [ ] **Step 3: 实现**

```python
# app/deploy/prod.py
"""prod 生命周期与发布（规格 04）：状态机 stopped|starting|publishing|running|unhealthy|error。
docker 交互全部转 engine.docker（单一实现，终审 M-资源面）；事件走 notify（hub.publish）。
prod 起栈**不做迁移**（07：迁移只发生在发布与预览启动两个入口，日常重启不重放）。"""
import asyncio, time
from pathlib import Path
import httpx
from sqlalchemy import text
from app.deploy import engine, records
from app.deploy.compose import ensure_deploy_files
from app.deploy.errors import DeployError
from app.deploy.engine import (preflight_docker, _app_root, _compose_meta, _notify,
                               LOG_PREFIX)
from app.deploy.provision import read_cred
from app.deploy.settings import deploy_cfg

def _prod_port(app: dict):
    ports = app.get("ports")
    if isinstance(ports, dict) and ports.get("prod") is not None:
        return ports["prod"]
    return app.get("prod_port")

async def set_status(sf, app_id: int, status: str) -> None:
    async with sf() as s:
        await s.execute(text(
            "UPDATE app_project SET status_prod = :st, updated_at = now() WHERE id = :i"),
            {"st": status, "i": app_id})
        await s.commit()

async def get_status(sf, app_id: int) -> str:
    async with sf() as s:
        return (await s.execute(text(
            "SELECT status_prod FROM app_project WHERE id = :i"), {"i": app_id})).scalar() or "stopped"

async def _step(app_id, notify, message: str, *, since=None, level=None):
    import logging
    (logging.getLogger("app.deploy.prod")).log(level or logging.INFO, "%s", f"{LOG_PREFIX} {message}")
    await _notify(notify, app_id, {"type": "dev_log", "line": f"{LOG_PREFIX} {message}"})

async def _docker(*args, cwd=None, on_line=None, timeout=600):
    return await engine.docker(*args, cwd=cwd, on_line=on_line, timeout=timeout)

async def image_exists(tag: str) -> bool:
    code, _ = await _docker("image", "inspect", tag, timeout=30)
    return code == 0

async def wait_prod_health(root: Path, port: int, timeout: int,
                           stack: str = "vue3-fastapi") -> tuple[bool, dict]:
    """联合健康（04 §4.3 最简路径）：GET / 200 且（非 static）GET /api/health 200——
    /api 由 T3b 的 nginx 反代进后端，单端口即前后端联合判定。"""
    checks = {"frontend": False, "api": stack == "static"}
    async with httpx.AsyncClient(timeout=3.0) as c:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                checks["frontend"] = (await c.get(f"http://127.0.0.1:{port}/")).status_code == 200
            except Exception:
                checks["frontend"] = False
            if checks["frontend"] and stack != "static":
                try:
                    checks["api"] = (await c.get(f"http://127.0.0.1:{port}/api/health")).status_code == 200
                except Exception:
                    checks["api"] = False
            if checks["frontend"] and checks["api"]:
                return True, checks
            await asyncio.sleep(1)
    return False, checks

def _prod_compose_args(root: Path):
    return ("compose", "-f", str(root / "deploy" / "prod-compose.yml"))

async def prod_start(sf, app: dict, notify=None) -> dict:
    app_id = app["id"]
    slug = app.get("slug") or f"app#{app_id}"
    await preflight_docker()          # 预检前置于状态变更（Task 11/12 契约）
    t0 = time.monotonic()
    await _step(app_id, notify, f"▶ 启动正式环境 {slug}")
    try:
        await set_status(sf, app_id, "starting")
        await _notify(notify, app_id, {"type": "prod_status", "status": "starting"})
        app = _compose_meta(app)
        root = await _app_root(sf, app)
        cred = await read_cred(sf, app["slug"], "prod")
        if not cred:
            raise DeployError("尚未发布过：prod 数据库凭据不存在，请先点「发布」", status=409)
        from app.services import app_service
        cur = (await app_service.get_app(sf, app_id)).get("current_version")
        if not cur:
            raise DeployError("应用还没有发布版本，无法启动正式环境", status=409)
        ensure_deploy_files(root, app, {"dev": {}, "prod": cred}, version=cur)
        code, log = await _docker(*_prod_compose_args(root), "up", "-d", cwd=str(root))
        if code != 0:
            raise DeployError(f"正式栈启动失败:\n{log[-800:]}")
        timeout = int((await deploy_cfg(sf)).get("deploy.health_timeout", 240))
        ok, checks = await wait_prod_health(root, _prod_port(app), timeout, app.get("stack", ""))
        if not ok:
            raise DeployError(f"正式环境体检未通过（{checks}），容器保持运行供排查")
        await set_status(sf, app_id, "running")
        await _notify(notify, app_id, {"type": "prod_status", "status": "running"})
        await _step(app_id, notify, f"✓ 正式环境已就绪 http://localhost:{_prod_port(app)}", since=t0)
        return {"status": "running"}
    except DeployError as e:
        await set_status(sf, app_id, "error")
        await _notify(notify, app_id, {"type": "prod_status", "status": "error", "message": str(e)})
        await _step(app_id, notify, f"✗ 启动失败：{str(e).splitlines()[0][:200]}", since=t0)
        raise

async def prod_stop(sf, app: dict, notify=None) -> dict:
    app_id = app["id"]
    t0 = time.monotonic()
    await _step(app_id, notify, f"▶ 停止正式环境 {app.get('slug') or app_id}")
    root = await _app_root(sf, _compose_meta(app))
    code, log = await _docker(*_prod_compose_args(root), "down", cwd=str(root))
    if code != 0:
        import logging
        logging.getLogger("app.deploy.prod").warning("prod 栈停止返回非零（仍按 stopped 收口）: %s", log[-300:])
    await set_status(sf, app_id, "stopped")
    await _notify(notify, app_id, {"type": "prod_status", "status": "stopped"})
    await _step(app_id, notify, "✓ 正式环境已停止（数据保留在平台数据库中）", since=t0)
    return {"status": "stopped"}

async def prod_restart(sf, app: dict, notify=None) -> dict:
    try:
        await prod_stop(sf, app, notify)
    except Exception:
        pass
    return await prod_start(sf, app, notify)

async def prod_status(sf, app: dict) -> dict:
    root = await _app_root(sf, _compose_meta(app))
    code, ps = await _docker(*_prod_compose_args(root), "ps", "--format", "json", cwd=str(root))
    from app.deploy import nics
    return {"status_prod": await get_status(sf, app["id"]),
            "compose_ps": ps if code == 0 else "",
            **(await nics.app_urls(sf, _compose_meta(app)))}

async def reconcile(sf, notify=None) -> None:
    """平台启动对账（04 §6）：中断的发布记录标 failed；publishing/starting 残留态按容器实况复位。"""
    n = await records.mark_interrupted(sf)
    if n:
        import logging
        logging.getLogger("app.deploy.prod").warning("上次发布中断，已标记 %d 条", n)
    async with sf() as s:
        rows = (await s.execute(text(
            "SELECT id, slug FROM app_project WHERE status_prod IN "
            "('publishing','starting','running','unhealthy','error')"))).fetchall()
    for app_id, slug in rows:
        try:
            code, out = await _docker("compose", "-p", f"zc-{slug}-prod", "ps", "-q", timeout=60)
            up = bool(out.strip())
        except Exception:
            up = False
        new = "running" if up else "stopped"
        await set_status(sf, app_id, new)
        await _notify(notify, app_id, {"type": "prod_status", "status": new})
```

- [ ] **Step 4: GREEN + 全量** → **Step 5: Commit**

```bash
git add app/deploy/prod.py tests/test_prod_engine.py
git commit -m "feat(deploy): prod 生命周期与启动对账（启停/状态/联合体检/镜像存在性）"
```

---

### Task 7: 发布流水线（7 步状态机 + 自动回退 + 回滚 + keep-5）

**Files:**
- Modify: `app/deploy/prod.py`（追加 publish/rollback/清理）
- Modify: `app/hub.py`（+`active_session_ids()` 只增方法）
- Test: `tests/test_prod_engine.py`（追加）

**Interfaces:**
- Consumes: T6 全部；T3 records；T4 compose；`sqlrun.run_sqlchange(root, "prod", cred)`；`provision.provision_env`；T5 nics/update_readme；`engine.docker`。
- Produces:
  - `prod.next_version() -> str`（`b<本地 YYYYMMDDHHmmss>`）
  - `prod.publish(sf, app, notify=None, *, force=False) -> dict`（返回 `{"status","version","url","recordId"}`；`force` 绕过 chat 闸门由 **端点层**判 409，引擎不查 chat——分层干净）
  - `prod.rollback(sf, app, version: str, notify=None) -> dict`
  - `prod.cleanup_images(sf, app_id, slug, notify=None) -> list[str]`（rmi 尽力而为，返回实删 tag）
  - `hub.ChatHub.active_session_ids() -> list[int]`
  - 事件补全：`publish_step {step,state}`（state: running|ok|failed）、`build_log {line}`、`prod_status`、`publish_done {ok, url, version, error}`（04 §5 协议逐字）
  - 迁移文件名规范扫描：`prod.scan_sqlchange(root) -> list[str]`（违规清单：`V<8数>_<2数>_<安全名>.sql`，安全名=`[\w\-.\u4e00-\u9fff]+` 且不含分号）

- [ ] **Step 1: 写失败测试（追加）**

```python
async def test_publish_success_sequence(monkeypatch, test_engine):
    from app.db import make_session_factory
    from sqlalchemy import text as t
    sf = make_session_factory(test_engine)
    async with sf() as s:
        wid = (await s.execute(t("INSERT INTO workspace (name, root_path) "
                                 "VALUES ('pb','D:/__zc_pub__') RETURNING id"))).scalar()
        aid = (await s.execute(t("INSERT INTO app_project (workspace_id, name, slug, scale, stack,"
                                 " prod_port, dev_port) VALUES (:w,'pb','zcpub','small','vue3-fastapi',"
                                 " 8992, 8993) RETURNING id"), {"w": wid})).scalar()
        await s.commit()
    ev = []
    calls = []
    async def docker(*a, cwd=None, on_line=None, timeout=600):
        calls.append(a[0] if isinstance(a[0], str) else str(a[:2]))
        return 0, "OK V20260920_01_init.sql"
    async def cred(sf_, slug, env):
        return {"db": f"{slug}_prod", "user": f"{slug}_prod", "password": "x"}
    async def prov(sf_, slug, env):
        return await cred(sf_, slug, env)
    async def mig(root, env, cr, on_log=None):
        calls.append(("migrate", env))
        return {"ok": True, "code": 0, "log": "ALL DONE\nOK V20260920_01_init.sql"}
    async def health(root, port, timeout, stack=""):
        return True, {"frontend": True, "api": True}
    def ensure(root, meta, creds, version=None):
        calls.append(("ensure", version))
    monkeypatch.setattr(prod, "_docker", docker)
    monkeypatch.setattr(prod, "preflight_docker", lambda: asyncio.sleep(0))
    async def root_(sf_, app_):
        return Path("D:/__zc_pub__")
    monkeypatch.setattr(prod, "_app_root", root_)
    monkeypatch.setattr(prod, "read_cred", cred)
    monkeypatch.setattr(prod, "provision_env", prov)
    monkeypatch.setattr(prod, "run_sqlchange", mig)
    monkeypatch.setattr(prod, "wait_prod_health", health)
    monkeypatch.setattr(prod, "ensure_deploy_files", ensure)
    monkeypatch.setattr(prod, "image_exists", lambda tag: asyncio.sleep(0, True))
    (Path("D:/__zc_pub__") / "sqlchange").mkdir(parents=True, exist_ok=True)
    (Path("D:/__zc_pub__") / "frontend").mkdir(exist_ok=True)
    res = await prod.publish(sf, {"id": aid, "slug": "zcpub", "stack": "vue3-fastapi",
                                  "ports": {"prod": 8992, "dev": 8993}},
                             notify=lambda i, e: ev.append(e), force=True)
    assert res["version"].startswith("b20") and res["status"] == "running"
    steps = [e["step"] for e in ev if e["type"] == "publish_step" and e["state"] == "ok"]
    assert steps == ["preflight", "build", "provision", "migrate", "deploy", "health", "record"]
    assert ev[-1]["type"] == "publish_done" and ev[-1]["ok"] is True
    async with sf() as s:
        cv, st = (await s.execute(t("SELECT current_version, status_prod FROM app_project "
                                     "WHERE id=:i"), {"i": aid})).first()
        assert cv == res["version"] and st == "running"
        kind, rk = (await s.execute(t("SELECT kind, status FROM deploy_record "
                                       "WHERE app_id=:i ORDER BY id DESC LIMIT 1"), {"i": aid})).first()
        assert (kind, rk) == ("publish", "success")
        await s.execute(t("DELETE FROM deploy_record WHERE app_id=:i"), {"i": aid})
        await s.execute(t("DELETE FROM app_project WHERE id=:i"), {"i": aid})
        await s.execute(t("DELETE FROM workspace WHERE id=:i"), {"i": wid})
        await s.commit()

async def test_publish_health_fail_autorollback(monkeypatch, test_engine):
    """已有 v1 在线 → 发 v2 体检挂 → 自动 up 旧 tag，旧版继续服务（04 §4.3/S-04-2）。"""
    ...（形态同上：monkeypatch 后第一次 health False；current_version 预置 b20260920000000；
    image_exists 恒 True。断言：publish_done.ok False、error 含「已回退」、up 的第二次调用带
    旧 PROD_VERSION、status_prod=running、current_version 仍= 旧版、deploy_record 该条 failed step=health）

async def test_rollback_missing_image(monkeypatch, test_engine):
    async def no_image(tag):
        return False
    monkeypatch.setattr(prod, "image_exists", no_image)
    with pytest.raises(DeployError) as ei:
        await prod.rollback(make_session_factory(make_engine()), {"id": 1, **APP},
                            "b20200101000000", notify=None)
    assert "镜像已清理" in str(ei.value)

def test_scan_sqlchange_flags_bad_names(tmp_path):
    d = tmp_path / "sqlchange"; d.mkdir()
    (d / "V20260920_01_ok.sql").write_text("SELECT 1", encoding="utf-8")
    (d / "notes.sql").write_text("x", encoding="utf-8")
    (d / "V2026_02_short.sql").write_text("x", encoding="utf-8")
    bad = prod.scan_sqlchange(tmp_path)
    assert sorted(bad) == ["V2026_02_short.sql", "notes.sql"]
```

（第二个用例的省略号处，实现者按 test_publish_success_sequence 同款 Seams 写全——**断言清单已给全，属转写工作**。）

- [ ] **Step 2: RED** → **Step 3: 实现（prod.py 追加）**

```python
import re as _re
from datetime import datetime

STEPLIST = ("preflight", "build", "provision", "migrate", "deploy", "health", "record")
_SQL_NAME = _re.compile(r"^V\d{8}_\d{2}_[\w\-.\u4e00-\u9fff]+\.sql$")

def next_version() -> str:
    return datetime.now().strftime("b%Y%m%d%H%M%S")

def scan_sqlchange(root: Path) -> list[str]:
    d = root / "sqlchange"
    if not d.is_dir():
        return []
    return sorted(p.name for p in d.iterdir()
                  if p.suffix == ".sql" and not _SQL_NAME.match(p.name))

async def _step_ev(app_id, notify, rid_step_cb, step, state):
    await _notify(notify, app_id, {"type": "publish_step", "step": step, "state": state})
    if rid_step_cb:
        await rid_step_cb(step)

async def _ensure_prod_proxy_conf(root: Path) -> bool:
    """存量应用补齐 frontend/nginx.conf（T3b 前创建的工程没有它，build 必挂）：平台唯一写入方补文件。"""
    fe = root / "frontend"
    conf = fe / "nginx.conf"
    if not fe.is_dir() or conf.is_file():
        return False
    from app.deploy.scaffold import TEMPLATES_DIR   # T3 若未导出则以 app/templates 路径就地构造
    src = TEMPLATES_DIR / "vue3-fastapi" / "frontend" / "nginx.conf"
    conf.write_text(src.read_text(encoding="utf-8"), encoding="utf-8", newline="")
    return True

async def publish(sf, app: dict, notify=None, *, force: bool = False) -> dict:
    app_id = app["id"]
    slug = app["slug"]
    await preflight_docker()
    app = _compose_meta(app)
    root = await _app_root(sf, app)
    version = next_version()
    from app.services import app_service
    prev_version = (await app_service.get_app(sf, app_id)).get("current_version")
    rid = await records.begin(sf, app_id, "publish", version)
    t0 = time.monotonic()

    async def rst(step):
        await records.set_step(sf, rid, step)

    def done_payload(ok, error=""):
        urls = {"localhost": f"http://localhost:{_prod_port(app)}"}
        return {"type": "publish_done", "ok": ok, "version": version if ok else prev_version,
                "url": urls["localhost"], "error": error}

    try:
        await set_status(sf, app_id, "publishing")
        await _notify(notify, app_id, {"type": "prod_status", "status": "publishing"})
        # 1 preflight
        await _step_ev(app_id, notify, rst, "preflight", "running")
        bad = scan_sqlchange(root)
        if bad:
            raise DeployError(f"sqlchange 文件名不规范：{bad}（应为 V<日期>_<两位序号>_<说明>.sql）")
        if not (root / "app.json").is_file():
            raise DeployError("app.json 缺失，无法发布")
        from app.deploy.scaffold import render as _unused  # noqa: F401  # 目视锚：不 import 具体符号也可，执行时删此行
        fixed = await _ensure_prod_proxy_conf(root)
        if fixed:
            await _step(app_id, notify, "✓ 已为存量应用补齐 frontend/nginx.conf（生产反代）")
        await _step_ev(app_id, notify, rst, "preflight", "ok")
        # 2 build（旧容器不动——04 §4.1）
        await _step_ev(app_id, notify, rst, "build", "running")
        async def blog(line):
            await _notify(notify, app_id, {"type": "build_log", "line": line})
        compose_file = str(root / "deploy" / "prod-compose.yml")
        cred0 = await read_cred(sf, slug, "prod")
        ensure_deploy_files(root, app, {"dev": {}, "prod": cred0 or {}}, version=version)
        code, log = await _docker("compose", "-f", compose_file, "build", cwd=str(root), on_line=blog)
        if code != 0:
            raise DeployError(f"镜像构建失败:\n{log[-1500:]}")
        await _step_ev(app_id, notify, rst, "build", "ok")
        # 3 provision（幂等）
        await _step_ev(app_id, notify, rst, "provision", "running")
        cred = await provision_env(sf, slug, "prod")
        ensure_deploy_files(root, app, {"dev": {}, "prod": cred}, version=version)
        await _step_ev(app_id, notify, rst, "provision", "ok")
        # 4 migrate
        await _step_ev(app_id, notify, rst, "migrate", "running")
        mig = await run_sqlchange(root, "prod", cred, on_log=blog)
        if not mig["ok"]:
            raise DeployError(f"prod 迁移失败（退出码 {mig['code']}），旧版本继续服务")
        sql_applied = [ln.split()[1] for ln in mig["log"].splitlines()
                       if ln.startswith("OK ") and len(ln.split()) > 1]
        await _step_ev(app_id, notify, rst, "migrate", "ok")
        # 5 deploy
        await _step_ev(app_id, notify, rst, "deploy", "running")
        code, log = await _docker("compose", "-f", compose_file, "up", "-d", cwd=str(root),
                                  on_line=blog)
        if code != 0:
            raise DeployError(f"切换新版本失败:\n{log[-800:]}")
        await _step_ev(app_id, notify, rst, "deploy", "ok")
        # 6 health（失败自动回退旧 tag）
        await _step_ev(app_id, notify, rst, "health", "running")
        timeout = int((await deploy_cfg(sf)).get("deploy.health_timeout", 240))
        ok, checks = await wait_prod_health(root, _prod_port(app), timeout, app.get("stack", ""))
        if not ok:
            if prev_version and await image_exists(f"zc-{slug}-frontend:prod-{prev_version}"):
                await _step(app_id, notify, "✗ 新版本体检未通过，自动回退上一版本", level=30)
                ensure_deploy_files(root, app, {"dev": {}, "prod": cred}, version=prev_version)
                await _docker("compose", "-f", compose_file, "up", "-d", cwd=str(root))
                await set_status(sf, app_id, "running")
                await _notify(notify, app_id, {"type": "prod_status", "status": "running"})
            else:
                await set_status(sf, app_id, "unhealthy")
                await _notify(notify, app_id, {"type": "prod_status", "status": "unhealthy"})
            await records.finish(sf, rid, "failed", step="health",
                                 error=f"体检未通过 {checks}；"
                                       + ("已回退上一版本" if prev_version else "首次发布无旧版可回退"))
            await _notify(notify, app_id, done_payload(
                False, f"新版本体检未通过（{checks}），"
                       + ("已保持上一版本运行" if prev_version else "首版无回退目标，标 unhealthy 供排查")))
            return {"status": "unhealthy" if not prev_version else "running",
                    "version": prev_version, "url": "", "recordId": rid}
        await _step_ev(app_id, notify, rst, "health", "ok")
        # 7 record
        await _step_ev(app_id, notify, rst, "record", "running")
        async with sf() as s:
            await s.execute(text(
                "UPDATE app_project SET current_version=:v, status_prod='running', "
                "updated_at=now() WHERE id=:i"), {"v": version, "i": app_id})
            await s.commit()
        from app.deploy import nics
        urls = await nics.app_urls(sf, app)
        nics.update_readme(root, urls["lan"] or urls["localhost"], version)
        await records.finish(sf, rid, "success", step="record", detail={
            "images": [f"zc-{slug}-frontend:prod-{version}", f"zc-{slug}-backend:prod-{version}"],
            "sqlApplied": sql_applied, "durationMs": int((time.monotonic() - t0) * 1000),
            "url": urls["lan"] or urls["localhost"]})
        await cleanup_images(sf, app_id, slug, notify=notify)
        await _step_ev(app_id, notify, None, "record", "ok")
        await _notify(notify, app_id, {"type": "prod_status", "status": "running"})
        await _step(app_id, notify,
                    f"✓ 发布完成 {version}，访问 {urls['lan'] or urls['localhost']}"
                    f"（耗时 {time.monotonic() - t0:.1f}s）")
        out = done_payload(True)
        out["url"] = urls["lan"] or urls["localhost"]
        await _notify(notify, app_id, out)
        return {"status": "running", "version": version, "url": out["url"], "recordId": rid}
    except DeployError as e:
        await records.finish(sf, rid, "failed", error=str(e)[:2000])
        cur = await get_status(sf, app_id)
        if cur == "publishing":
            await set_status(sf, app_id, "error")
            await _notify(notify, app_id, {"type": "prod_status", "status": "error",
                                           "message": str(e)})
        await _step(app_id, notify, f"✗ 发布失败：{str(e).splitlines()[0][:200]}", level=30)
        await _notify(notify, app_id, done_payload(False, str(e)[:500]))
        raise
    except Exception as e:
        await records.finish(sf, rid, "failed", error=f"{type(e).__name__}: {e}")
        await set_status(sf, app_id, "error")
        await _notify(notify, app_id, {"type": "prod_status", "status": "error", "message": str(e)})
        await _notify(notify, app_id, done_payload(False, str(e)[:500]))
        raise DeployError(f"发布失败: {e}") from e

async def rollback(sf, app: dict, version: str, notify=None) -> dict:
    app_id = app["id"]
    slug = app["slug"]
    await preflight_docker()
    app = _compose_meta(app)
    for role in ("frontend", "backend"):
        if not await image_exists(f"zc-{slug}-{role}:prod-{version}"):
            raise DeployError(f"该版本镜像已清理（{role}），无法回滚", status=400)
    root = await _app_root(sf, app)
    cred = await read_cred(sf, slug, "prod")
    if not cred:
        raise DeployError("prod 凭据缺失，无法回滚", status=409)
    rid = await records.begin(sf, app_id, "rollback", version)
    try:
        await set_status(sf, app_id, "publishing")
        await _notify(notify, app_id, {"type": "prod_status", "status": "publishing"})
        ensure_deploy_files(root, app, {"dev": {}, "prod": cred}, version=version)
        code, log = await _docker("compose", "-f", str(root / "deploy" / "prod-compose.yml"),
                                  "up", "-d", cwd=str(root))
        if code != 0:
            raise DeployError(f"回滚起栈失败:\n{log[-800:]}")
        timeout = int((await deploy_cfg(sf)).get("deploy.health_timeout", 240))
        ok, checks = await wait_prod_health(root, _prod_port(app), timeout, app.get("stack", ""))
        async with sf() as s:
            await s.execute(text("UPDATE app_project SET current_version=:v, status_prod=:st, "
                                 "updated_at=now() WHERE id=:i"),
                                 {"v": version, "st": "running" if ok else "unhealthy", "i": app_id})
            await s.commit()
        await records.finish(sf, rid, "success" if ok else "failed", step="health",
                             error=None if ok else f"回滚后体检未通过 {checks}")
        await _notify(notify, app_id, {"type": "prod_status",
                                       "status": "running" if ok else "unhealthy"})
        from app.deploy import nics
        urls = await nics.app_urls(sf, app)
        await _notify(notify, app_id, {"type": "publish_done", "ok": ok, "version": version,
                                       "url": urls["lan"] or urls["localhost"],
                                       "error": "" if ok else f"体检未通过 {checks}"})
        return {"status": "running" if ok else "unhealthy", "version": version}
    except DeployError as e:
        await records.finish(sf, rid, "failed", error=str(e)[:2000])
        await set_status(sf, app_id, "error")
        await _notify(notify, app_id, {"type": "publish_done", "ok": False, "version": version,
                                       "url": "", "error": str(e)[:500]})
        raise

async def cleanup_images(sf, app_id: int, slug: str, notify=None) -> list[str]:
    keep = int((await deploy_cfg(sf)).get("deploy.keep_versions", 5))
    removed: list[str] = []
    for v in await records.versions_for_cleanup(sf, app_id, keep):
        for role in ("frontend", "backend"):
            tag = f"zc-{slug}-{role}:prod-{v}"
            code, _ = await _docker("image", "rm", tag, timeout=120)
            if code == 0:
                removed.append(tag)
    if removed:
        await _step(app_id, notify, f"✓ 已清理旧镜像 {len(removed)} 个（保留最近 {keep} 版）")
    return removed
```

`app/hub.py` 追加（ChatHub 类内）：

```python
    def active_session_ids(self) -> list:
        """当前有活跃流的会话（发布闸门 FR4-09 数据源）。emit 登记表键集快照。"""
        return list(getattr(self, "emits", {}) or {})
```

> 实现注意：①`hub.ChatHub.__init__` 里 emit 表的真实属性名执行时读文件核定（13-18 行构造），方法内部相应替换；②`provision_env`/`run_sqlchange` 在 prod.py 顶部按模块级名字 import（engine 同款惯例：`from app.deploy.provision import provision_env, read_cred`；`from app.deploy.sqlrun import run_sqlchange`），monkeypatch 才生效；③`_step_ev` 中 state failed 不发（失败统一走 publish_done + 记录），保持事件精简；④`from app.deploy.scaffold import render as _unused` 那行是计划自检残留的锚注释——**执行时删除**，scaffold 的模板目录常量若无导出，用 `Path(engine.__file__).parents[1] / "app" / "templates"` 或 app_service.TEMPLATES 复用（读现状定，单一事实源优先复用 app_service.TEMPLATES）。

- [ ] **Step 4: GREEN + 全量** → **Step 5: Commit**

```bash
git add app/deploy/prod.py app/hub.py tests/test_prod_engine.py
git commit -m "feat(deploy): 发布流水线七步与自动回退、回滚与 keep-5 镜像清理"
```

---

### Task 8: 发布 REST 端点 + chat 闸门 + lifespan 对账接线

**Files:**
- Modify: `app/api/apps.py`
- Modify: `app/main.py`（lifespan 挂 reconcile）
- Test: `tests/test_publish_api.py`

**Interfaces:**
- Consumes: T7 引擎、`_dev_task/_spawn/_reject_if_task_running`（apps.py 现成）、hub.active_session_ids。
- Produces（04 §5 接口表逐一对应）:
  - `POST /api/apps/{id}/publish`（body `{force: bool=false}`）→ chat 活跃且非 force：**409 + dict detail `{"code":"chat_active","message":…}`**；否则同步 preflight + _spawn → 202 `{"ok":true}`
  - `POST /api/apps/{id}/rollback`（body `{version}`）→ 版本格式校验（`^b\d{14}$`）→ 202
  - `POST /api/apps/{id}/prod/start|stop|restart` → 202（start/restart 同步 docker 预检——与 dev 同契约）
  - `GET /api/apps/{id}/prod/status` → prod_status 结果（含 urls 块）
  - `GET /api/apps/{id}/records?limit=` → records.list_for；`DELETE /api/apps/{id}/records/{rid}` → 删行 + 尽力 rmi 该 version 两镜像（FR4-08）
- lifespan：`_deploy_startup` 尾部 `await deploy_prod.reconcile(...)`（非致命 try）；**注意不新增 dev 清理对 prod 的误伤**（cleanup_orphans 只管 dev 栈——现状已如此）。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_publish_api.py（client fixture 用法与 test_apps_api.py 同款：conftest client）
import asyncio, pytest
from sqlalchemy import text
pytestmark = pytest.mark.asyncio

async def _mk(client):
    r = await client.post("/api/apps", json={"name": "发布口", "slug": "zcpubapi",
                                             "scale": "small", "stack": "vue3-fastapi"})
    assert r.status_code == 201, r.text
    return r.json()["id"]

async def _wipe(client):
    async with client.app.state.session_factory() as s:
        rows = (await s.execute(text("SELECT id FROM app_project WHERE slug='zcpubapi'"))).scalars().all()
        if rows:
            await s.execute(text("DELETE FROM deploy_record WHERE app_id = ANY(:ids)"), {"ids": rows})
            await s.execute(text("DELETE FROM app_project WHERE slug='zcpubapi'"))
        await s.execute(text("DELETE FROM workspace WHERE root_path LIKE '%zcpubapi%'"))
        await s.commit()

async def test_publish_chat_gate_and_spawn_shape(client, monkeypatch):
    aid = await _mk(client)
    try:
        from app.deploy import prod as prod_mod
        called = []
        async def fake_publish(sf, app, notify=None, *, force=False):
            called.append(force)
            return {"status": "running", "version": "b20260920123456", "url": "http://x", "recordId": 1}
        monkeypatch.setattr(prod_mod, "publish", fake_publish)
        async def no_preflight():
            return None
        monkeypatch.setattr(prod_mod, "preflight_docker", no_preflight)
        # chat 活跃：把 app.state.hub 的 active_session_ids 打到非空 + 该应用 workspace 有会话
        async with client.app.state.session_factory() as s:
            wsid = (await s.execute(text("SELECT workspace_id FROM app_project WHERE id=:i"),
                                    {"i": aid})).scalar()
            sid = (await s.execute(text("INSERT INTO chat_session (workspace_id, title) "
                                         "VALUES (:w,'t') RETURNING id"), {"w": wsid})).scalar()
            await s.commit()
        fake_hub_ids = lambda: [sid]
        monkeypatch.setattr(client.app.state.hub, "active_session_ids", fake_hub_ids)
        r = await client.post(f"/api/apps/{aid}/publish", json={"force": False})
        assert r.status_code == 409 and r.json()["detail"]["code"] == "chat_active"
        assert not called
        r = await client.post(f"/api/apps/{aid}/publish", json={"force": True})
        assert r.status_code == 202
        await asyncio.sleep(0.05)
        assert called == [True]
        # 记录接口形状
        rr = await client.get(f"/api/apps/{aid}/records")
        assert rr.status_code == 200 and isinstance(rr.json(), list)
    finally:
        await _wipe(client)

async def test_rollback_bad_version_400(client):
    aid = await _mk(client)
    try:
        r = await client.post(f"/api/apps/{aid}/rollback", json={"version": "latest"})
        assert r.status_code == 400
    finally:
        await _wipe(client)

async def test_prod_status_shape(client, monkeypatch):
    aid = await _mk(client)
    try:
        async def fake_status(sf, app):
            return {"status_prod": "stopped", "compose_ps": "", "localhost": "http://localhost:1",
                    "lan": "", "lanCandidates": [], "defaultNic": ""}
        from app.deploy import prod as prod_mod
        monkeypatch.setattr(prod_mod, "prod_status", fake_status)
        r = await client.get(f"/api/apps/{aid}/prod/status")
        assert r.status_code == 200 and r.json()["status_prod"] == "stopped"
    finally:
        await _wipe(client)
```

> 注：`chat_session` 表列以现状为准（执行时 `\d` 读 app 模型；若无 `title` 列按现状调整）；`client.app.state.hub` 属性名读 main.py 核定（v1 为 hub，执行时验证）。`monkeypatch.setattr(client.app.state…)` 对**测试内 lifespan 实例**生效，不污染真实平台。

- [ ] **Step 2: RED** → **Step 3: 实现（apps.py 追加）**

```python
@router.post("/{app_id}/publish", status_code=202)
async def publish(request: Request, app_id: int, body: PublishIn):
    sf = request.app.state.session_factory
    hub = request.app.state.deploy_hub
    try:
        app = await app_service.get_app(sf, app_id)
        _reject_if_task_running(request, app_id)
        if app["status_prod"] == "publishing":
            raise DeployError("发布进行中，请等待完成", status=409)
        if not body.force:
            # FR4-09：该应用的工程工作区有活跃 AI 会话时要求显式确认
            active = set(request.app.state.hub.active_session_ids())
            if active:
                async with sf() as s:
                    hit = (await s.execute(text(
                        "SELECT 1 FROM chat_session WHERE workspace_id=:w AND id = ANY(:ids) LIMIT 1"),
                        {"w": app["workspace_id"], "ids": list(active)})).first()
                if hit:
                    raise HTTPException(409, {
                        "code": "chat_active",
                        "message": "当前 AI 正在修改代码，发布的可能是不完整版本，是否继续？"})
        await prod.preflight_docker()
        _spawn(request, app_id, lambda: prod.publish(sf, app, notify=hub.publish,
                                                     force=body.force))
    except DeployError as e:
        raise _http(e)
    return {"ok": True}
```

（rollback/prod 端点同型：rollback 校验 `^b\d{14}$` + _spawn；prod start/restart 走 `_dev_task` 的 prod 变体（抽公共 `_task(action, docker_gate)` 或复制三行——**优先复用 `_dev_task` 加 env 参数**，不动既有 dev 行为）；records GET/DELETE 直查 records 服务 + DELETE 附 `docker image rm`（engine._docker 或 subprocess——用 prod._docker，失败忽略）。）

`app/main.py`：`_deploy_startup` 尾部加：

```python
        try:
            from app.deploy import prod as deploy_prod
            await deploy_prod.reconcile(app.state.session_factory,
                                        notify=app.state.deploy_hub.publish)
        except Exception:
            logger.exception("prod 启动对账失败（Docker 未就绪？）")
```

- [ ] **Step 4: GREEN + 全量** → **Step 5: Commit**

```bash
git add app/api/apps.py app/main.py tests/test_publish_api.py
git commit -m "feat(deploy): 发布/回滚/prod 端点与 chat 闸门、lifespan 对账"
```

---

<!-- PLAN-CONTINUES-3 -->

---

### Task 9: 前端部署事件流（useDeploy composable + API 封装）

**Files:**
- Create: `frontend/src/composables/useDeploy.js`
- Modify: `frontend/src/api/index.js`
- Test: `frontend/src/composables/useDeploy.test.js`

**Interfaces:**
- Consumes: 后端 WS `/ws/deploy/{app_id}`（M1 已有，事件透传）、REST 新端点（T8）。
- Produces（T10 组件依赖）:
  - `useDeploy(appId)` → `{ connected, steps, buildTail, prodStatus, lastDone, logs, connect(), close(), on(type, handler) }`
    - `steps`：`{preflight: 'wait'|'running'|'ok'|'failed', ...}`（publish_step 事件累积，reset 于 publish 首事件）
    - `buildTail`：`build_log` + `dev_log` 合并尾部 300 行（`{line, ts}` 简单对象）
    - `lastDone`：最近一次 `publish_done`（`{ok, url, version, error}`）
    - `prodStatus`：最近 `prod_status.status`
    - `on(type, handler)`：注册 `deploy_dev_status/deploy_build_log/deploy_migrate_result/deploy_publish_step/deploy_publish_done/deploy_prod_status/deploy_hello` 前缀化事件回调（type 未加前缀的用底层名自动加）
    - 断线重连：`setTimeout` 指数退避（1s/2s/4s…上限 15s），`close()` 后不再重连；页面隐藏时不断连（简单优先）
  - `api/index.js` 追加：`publishApp(id, force)`、`rollbackApp(id, version)`、`prodStart/Stop/Restart(id)`、`prodStatus(id)`、`appRecords(id, limit=50)`、`deleteRecord(id, rid)`、`appUrls(id)`、`systemNics()`
- WS URL 构造复用既有 `wsUrl()` helper（api/index.js 现状）。

- [ ] **Step 1: 写失败测试（vitest，FakeWebSocket 注入）**

```js
// frontend/src/composables/useDeploy.test.js
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { useDeploy } from './useDeploy'

class FakeWS {
  static instances = []
  constructor(url) { this.url = url; this.listeners = {}; FakeWS.instances.push(this) }
  addEventListener(t, fn) { (this.listeners[t] ||= []).push(fn) }
  open() { this.listeners.open?.forEach(f => f()) }
  recv(obj) { this.listeners.message?.forEach(f => f({ data: JSON.stringify(obj) })) }
  close() { this.listeners.close?.forEach(f => f()) }
}

beforeEach(() => { vi.useFakeTimers(); globalThis.WebSocket = FakeWS })
afterEach(() => { vi.useRealTimers() })

describe('useDeploy', () => {
  it('publish_step 累积到 steps 并保留顺序', () => {
    const d = useDeploy(7)
    d.connect()
    const ws = FakeWS.instances.at(-1)
    ws.open()
    ws.recv({ type: 'publish_step', step: 'build', state: 'running' })
    ws.recv({ type: 'publish_step', step: 'build', state: 'ok' })
    expect(d.steps.value.build).toBe('ok')
    expect(d.prodStatus.value).toBeNull?.() || true
  })
  it('build_log/dev_log 进尾部缓冲且封顶 300', () => {
    const d = useDeploy(7)
    d.connect(); FakeWS.instances.at(-1).open()
    const ws = FakeWS.instances.at(-1)
    for (let i = 0; i < 320; i++) ws.recv({ type: 'build_log', line: `l${i}` })
    expect(d.buildTail.value.length).toBe(300)
    expect(d.buildTail.value.at(-1).line).toBe('l319')
  })
  it('publish_done 落 lastDone；断线自动重连（指数退避）', async () => {
    const d = useDeploy(7)
    d.connect()
    FakeWS.instances.at(-1).open()
    FakeWS.instances.at(-1).recv({ type: 'publish_done', ok: true, url: 'http://x', version: 'b1', error: '' })
    expect(d.lastDone.value.url).toBe('http://x')
    FakeWS.instances.at(-1).close()
    vi.advanceTimersByTime(1000)
    expect(FakeWS.instances.length).toBe(2)     // 已重连
  })
  it('on(publish_step) 回调与状态并存；close 后不再重连', () => {
    const d = useDeploy(7)
    const seen = []
    d.on('publish_step', (ev) => seen.push(ev.step + ':' + ev.state))
    d.connect(); FakeWS.instances.at(-1).open()
    FakeWS.instances.at(-1).recv({ type: 'publish_step', step: 'migrate', state: 'ok' })
    expect(seen).toEqual(['migrate:ok'])
    d.close()
    FakeWS.instances.at(-1).close()
    vi.advanceTimersByTime(20000)
    expect(FakeWS.instances.length).toBe(1)
  })
})
```

Run: `npx vitest run src/composables/useDeploy.test.js` → FAIL（模块不存在）

- [ ] **Step 2: 实现 useDeploy.js**

```js
// frontend/src/composables/useDeploy.js
// 部署事件流：/ws/deploy/{appId} 的订阅封装。状态即数据——steps/buildTail/lastDone
// 全部 ref，组件直接渲染；on() 只作副作用通道（弹通知等），两处消费同一事件源不互斥。
import { ref } from 'vue'
import { wsUrl } from '../api'

const STEPS = ['preflight', 'build', 'provision', 'migrate', 'deploy', 'health', 'record']
const TAIL_MAX = 300

export function useDeploy(appId) {
  const connected = ref(false)
  const steps = ref(Object.fromEntries(STEPS.map(s => [s, 'wait'])))
  const buildTail = ref([])
  const prodStatus = ref(null)
  const devStatus = ref(null)
  const lastDone = ref(null)
  const migrateResult = ref(null)
  const handlers = new Map()
  let ws = null, timer = null, backoff = 1000, closedByUs = false

  function pushTail(line, kind) {
    buildTail.value = [...buildTail.value.slice(-(TAIL_MAX - 1)), { line, kind, ts: Date.now() }]
  }
  function resetSteps() { steps.value = Object.fromEntries(STEPS.map(s => [s, 'wait'])) }

  function on(type, fn) {
    const key = type.startsWith('deploy_') ? type : 'deploy_' + type
    if (!handlers.has(key)) handlers.set(key, [])
    handlers.get(key).push(fn)
  }
  function fire(ev) {
    const key = 'deploy_' + ev.type
    ;(handlers.get(key) || []).forEach(fn => { try { fn(ev) } catch (e) { console.error('useDeploy handler', e) } })
  }

  function connect() {
    if (ws || !appId) return
    closedByUs = false
    ws = new WebSocket(wsUrl(`/ws/deploy/${appId}`))
    ws.addEventListener('open', () => { connected.value = true; backoff = 1000 })
    ws.addEventListener('message', (m) => {
      let ev; try { ev = JSON.parse(m.data) } catch { return }
      if (ev.type === 'ping') return
      switch (ev.type) {
        case 'publish_step':
          steps.value = { ...steps.value, [ev.step]: ev.state }
          break
        case 'build_log': pushTail(ev.line, 'build'); break
        case 'dev_log': pushTail(ev.line, 'step'); break
        case 'migrate_result': migrateResult.value = ev; pushTail(`迁移 ${ev.ok ? '成功' : '失败'}（${ev.env}）`, 'step'); break
        case 'dev_status': devStatus.value = ev.status; if (ev.message) pushTail(`✗ ${ev.message}`, 'step'); break
        case 'prod_status': prodStatus.value = ev.status; if (ev.message) pushTail(`✗ ${ev.message}`, 'step'); break
        case 'publish_done': lastDone.value = ev; break
      }
      fire(ev)
    })
    ws.addEventListener('close', () => {
      ws = null; connected.value = false
      if (!closedByUs) { timer = setTimeout(connect, backoff); backoff = Math.min(backoff * 2, 15000) }
    })
    ws.addEventListener('error', () => { /* close 会跟在 error 后触发，统一在 close 处理 */ })
  }
  function close() {
    closedByUs = true
    if (timer) clearTimeout(timer)
    ws?.close(); ws = null; connected.value = false
  }
  return { connected, steps, buildTail, prodStatus, devStatus, lastDone, migrateResult, connect, close, on }
}
```

`frontend/src/api/index.js` 追加（注释分区「部署发布套件 M2」）：

```js
// ---- 发布（M2） ----
export const publishApp = (id, force = false) => http.post(`/apps/${id}/publish`, { force })
export const rollbackApp = (id, version) => http.post(`/apps/${id}/rollback`, { version })
export const prodStart = (id) => http.post(`/apps/${id}/prod/start`)
export const prodStop = (id) => http.post(`/apps/${id}/prod/stop`)
export const prodRestart = (id) => http.post(`/apps/${id}/prod/restart`)
export const prodStatus = (id) => http.get(`/apps/${id}/prod/status`)
export const appRecords = (id, limit = 50) => http.get(`/apps/${id}/records`, { params: { limit } })
export const deleteRecord = (id, rid) => http.delete(`/apps/${id}/records/${rid}`)
export const appUrls = (id) => http.get(`/apps/${id}/urls`)
export const systemNics = () => http.get('/system/nics')
```

- [ ] **Step 3: GREEN**（`npx vitest run src/composables/useDeploy.test.js`）+ 全量 vitest + build → **Step 4: Commit**

```bash
git add frontend/src/composables/useDeploy.js frontend/src/composables/useDeploy.test.js frontend/src/api/index.js
git commit -m "feat(deploy): useDeploy 事件流 composable 与 M2 API 封装"
```

---

### Task 10: 应用页发布 UI（进度弹层/完成弹层/历史回滚/排查指引）

**Files:**
- Modify: `frontend/src/views/AppsView.vue`
- Create: `frontend/src/components/PublishModal.vue`
- Create: `frontend/src/components/PublishHistory.vue`
- Test: `frontend/src/components/PublishModal.test.js`、`frontend/src/components/PublishHistory.test.js`
- Modify: `frontend/src/views/AppsView.test.js`（若存在则追加，否则新建最小挂载断言）

**Interfaces:**
- Consumes: T9 useDeploy/api；后端 409 `detail.code==='chat_active'`；`detail.code==='stack_unsupported'`（M2 仅 vue3-fastapi 可发布——**T8 端点未拦，UI 拦**：static/其他栈按钮置灰 title「该栈发布支持即将上线」）。
- Produces: 用户故事 S-04-1/2/3/5 全落 UI。

**组件规格（实现按此，样式守全局素雅约定）：**

`PublishModal.vue`（props: `app`，`open` v-model；emits: `done`）——

1. 顶部：应用名 + 「发布将构建镜像并上线到本应用专属端口（当前版本 {{app.current_version || '未发布'}}）」说明行。
2. 7 步清单（`a-steps` 竖排或自绘行）：`useDeploy(app.id).steps` 映射——`wait` 灰点、`running` 蓝点转（`LoadingOutlined`）、`ok` 绿勾、`failed` 红叉。**发布启动时先 reset**：steps 全 wait 是组件挂载时状态，publish 按钮点击后调 `publishApp(id, force)`。
3. 构建日志折叠区（`a-collapse`，默认收起）：`buildTail` 滚动到底部渲染（等宽字体小字号；kind 图标区分 build/step）。
4. 底栏按钮态：`publishing` 中禁重复点击（按钮 loading）；`publish_done` 到达后：ok→进入完成态（见 5）；fail→红条 `lastDone.error` 前 200 字 + 两个按钮：「重试发布」「把错误日志发给 AI」——后者 `localStorage.setItem('zc_publish_error', JSON.stringify({appId, text: buildTail 尾部 80 行摘要}))` + `router.push('/')`（聊天页 ChatView 消费在 T10b 内做——**若 ChatView 读取逻辑未接，按钮仅复制剪贴板**，报告说明现状取舍）。
5. 完成弹层内容（`publish_done.ok`）：大号局域网 URL（`lastDone.url`，点击复制）+ 本机 URL 次要行 + 网卡候选下拉（多网卡时 `systemNics()`，选择 `PUT /api/config` 写 `deploy.default_nic` 后重取 urls）+「复制给同事」按钮 + 折叠的「打不开？三步排查」卡：①同网段（两机 IP 前三段一致）②防火墙（netsh 命令按 `slug+prod_port` 现场生成，代码块 + 复制按钮，注明「需管理员运行/可转给 IT」）③地址完整性（含 `http://` 与端口）。
6. FR4-09：`publishApp(id,false)` 得 409 detail.code==='chat_active' → `Modal.confirm({title:'AI 正在修改代码', content: detail.message, okText:'继续发布', cancelText:'等一等'})`，确认后 `publishApp(id, true)`。
7. WS 生命周期：`open` 变 true 才 `connect()`；watch open=false → `close()`（不泄漏连接——单测断言 close 调用）。

`PublishHistory.vue`（props: `app`）——

1. `a-table :columns` + `#bodyCell`（全局约定）：时间（started_at 本地化）、类型（kind：发布/回滚）、版本、状态（success 绿点/failed 红点/running 蓝点 + step 名）、耗时（durationMs 或 finished-started 现算）、操作列 `#bodyCell`：「查看日志」（弹该条 detail.sqlApplied + error 只读窗）、「回滚到此版本」（kind=publish 且非当前版才可点）、删除记录（Popconfirm，deleteRecord）。
2. 回滚点击 → `Modal.confirm` 固定警示（04 §4.4 文案逐字：「回滚只恢复程序版本，数据库结构与数据保持当前状态；若新版本改过表结构，旧程序可能读写异常，请联系 AI 生成兼容处理。」）→ `rollbackApp` → 复用 PublishModal 的进度通道（同 WS 订阅，steps 走 rollback 同样协议——**rollback 端点复用 publish_step 事件**，见 T7 注）。
3. 空态：「还没有发布记录——点上方『发布』上线你的应用」。

`AppsView.vue` 修改——

1. 卡片操作行加「发布」主按钮（primary ghost）：栈非 vue3-fastapi → `disabled` + `title` 说明；`status_prod==='publishing'` → loading。
2. 卡片生产状态行：`status_prod` 点 + 文字（复用 statusText 映射扩 prod 态）+ `current_version` + 「正式地址」复制（appUrls 懒加载，hover 才拉）。
3. 「启动/停止正式环境」按钮（stopped↔running 切换，202 后靠 5s 轮询 + WS prod_status 双通道刷新——`useDeploy` 已在卡片级共享？**单 WS 实例决策**：每卡片一个 useDeploy 太重；apps 列表同时最多开一个 PublishModal（弹窗内 useDeploy）+ prod_status 靠轮询。抽屉预览保持 M1 dev 通道不动）。
4. 「发布历史」入口（卡片 ⋯ 或按钮）→ 开 `PublishHistory` 抽屉。

- [ ] **Step 1: 写失败测试**

`PublishModal.test.js`（挂载 stub `a-*` 全局组件的浅挂载策略照抄 AppsView 既有测试/仓库惯例；核心断言）：

```js
// 关键断言（实现者按仓库测试基建补全 mock，思路如下）：
// 1. WS 生命周期：open 变化驱动 connect/close（vi.mock useDeploy，断言被调用）
// 2. steps 渲染：注入 steps.value = {preflight:'ok', build:'running', ...}，
//    组件文本含「构建镜像」与进行中态类名
// 3. 409 chat_active：mock publishApp 返回 reject({response:{status:409,detail… 见注}})，
//    Modal.confirm 被调用且确认后 publishApp(id, true)
// 4. 完成态：lastDone {ok:true,url:'http://10.0.0.2:8210'} → 大号 URL 文本存在、
//    「复制」点击 clipboard.writeText 收到该 URL
// 5. netsh 命令含 slug 与端口数字
```

注：后端 409 detail 是 dict——axios 错误路径为 `e.response.data.detail.code`；测试 mock 同形。

`PublishHistory.test.js`：columns 数；`success/failed` 两行数据渲染状态点；回滚按钮仅非当前版本 publish 行可点；确认文案逐字断言（含「数据库结构与数据保持当前状态」）；确认后 `rollbackApp` 携带 version。

- [ ] **Step 2: RED** → **Step 3: 实现三组件 + AppsView 接线** → **Step 4: 门槛**

Run: `npx vitest run`（全量绿）+ `npm run build` 成功。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/PublishModal.vue frontend/src/components/PublishModal.test.js frontend/src/components/PublishHistory.vue frontend/src/components/PublishHistory.test.js frontend/src/views/AppsView.vue frontend/src/views/AppsView.test.js
git commit -m "feat(deploy): 应用页发布 UI（进度/完成弹层/历史回滚/局域网排查指引）"
```

---

### Task 11: 端到端真实发布冒烟 + 文档与规格勘误

**Files:**
- Modify: `README.md`（发布章节）
- Modify: `docs/superpowers/specs/2026-09-18-zerocode-deploy-suite/04-Prod打包发布.md`（§4.1 status_prod 枚举 + starting；§4.3 联合健康单 URL 落地口径一句）
- Modify: 计划/台账（进度记录）
- Test: 冒烟清单（人工/控制者执行，无自动化新增）

- [ ] **Step 1: 全量门禁**（后端 pytest + 前端 vitest/build，数字记台账）
- [ ] **Step 2: 冒烟清单**（独立 worktree 起平台于临时端口 8110，避开并行会话）：
  1. 建应用 smokepub → 预览起（M1 链路回归）→ 停止。
  2. 点发布 → 7 步全绿 ≤3 分钟 → 完成弹层 URL 可访问（`/api/health` 经 nginx 反代 `db:true`）。
  3. 改代码 + 新增 `sqlchange/V*_02_x.sql` → 二次发布 ≤90s → prod 库增量迁移成功、`demo_notes` 数据保留。
  4. 连续发到 7 版 → `docker images zc-smokepub-*` 仅保留 5 版 tag。
  5. 回滚第 1 版 → 旧版秒级恢复；回滚已清理版 → 400「镜像已清理」。
  6. 制造构建失败（requirements.txt 加 `no-such-pkg-xyz`）→ 旧版全程可访问、历史 failed 含 step=build、UI 错误折叠日志。
  7. 停平台重启 → `publishing` 中断记录标 interrupted；prod running 对账正确；「启动正式环境」可用。
  8. 局域网 URL：`GET /api/system/nics` 有候选；选 default_nic 后 README 顶部块刷新。
  9. 冒烟清理：删应用（用另一会话的删除端点/或手工清行+drop_env+目录），残留自查全 0。
- [ ] **Step 3: README**「发布上线」小节（向导式：发布→复制地址→同事访问三提醒）+ **04 规格勘误**（status_prod 加 starting；体检单 URL 口径）
- [ ] **Step 4: Commit**

```bash
git add README.md docs/superpowers/specs/2026-09-18-zerocode-deploy-suite/04-Prod打包发布.md
git commit -m "docs: M2 发布使用说明与规格勘误"
```

---

## Self-Review 记录（计划完成时已执行）

1. **Spec 覆盖**：04 FR4-01~09 → T7（1/2/3/4/5/6/7 步全链 + 4-07 自动回退 + 4-08 keep5/records DELETE + 4-09 闸门）✓；05 FR5-03/04/05/06 → T5+T10 ✓；05 FR5-02（运行中端口被抢）M1 已有 dev 侧，prod 侧靠 error+对账复位，**分诊：M3 增强实时探测**；08 UI 发布部分 → T10 ✓。缺口（有意）：static/springboot 栈发布（M3）、卸载清理 prod 全量（FR4-08 删库=01 FR1-07 他会话在途）、`/apps/{id}/prod/status` 实时端口抢占提示（分诊 M3）。
2. **占位符**：T7 第二用例给「断言清单+转写指令」非 TBD——形态与 M1 计划同规；T8 「rollback/prod 端点同型」附了公共函数落点与全部校验条件，可执行。
3. **类型一致**：`prod_status` 事件三处（T6/T7/T9 switch）字段 `{status, message?}` 统一；`publish_done` 字段 `{ok,url,version,error}` T7/T9/T10 一致；`ensure_deploy_files(root, meta, creds, version=None)` T4 定义 ↔ T6/T7 调用一致（**creds 里 dev 传 `{}` 是否安全**：render_env 对缺 dev 键写空——发布时 dev creds 缺失会把 .env DEV_* 冲空！**修正：T6/T7 的调用必须先 read_cred dev 再传**；见下条修正）。
4. **执行前修正（本波自审新发现）**：`ensure_deploy_files(root, app, {"dev": {}, "prod": cred}, …)` 的写法会清空既有 DEV_* ——改为读 dev cred：`{"dev": await read_cred(sf, slug, "dev") or {}, "prod": cred}`（prod_start/publish/rollback 三处同步修正；实现者按此执行，派发词加裁定 D-T4）。
5. **他会话接缝**：apps.py/main.py/hub.py/AppsView.vue/api/index.js 五文件与并行 WIP 重叠——worktree 基线为届时 HEAD；冲突在合并波处理；T10 只加不改预览通道（dev 抽屉不动）。

## 执行交接

计划已保存到 `docs/superpowers/plans/2026-09-20-zerocode-deploy-suite-m2.md`（11 任务）。执行方式沿 M1：**子代理驱动（subagent-driven-development）**，且**必须**在独立 worktree（全局约束第 1 条）施工后合回分支。
