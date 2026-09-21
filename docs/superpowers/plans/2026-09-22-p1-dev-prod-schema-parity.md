# 应用 dev/prod 结构等价 — P1 止血段 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不改变现有「增量文件为真相」模型的前提下，为应用级库加两道止血能力：① 一键把 dev 库从 `sqlchange` 文件重建（消除 dev 漂移）；② 发布前用 `pg_dump --schema-only` 比对「dev 实际结构」与「fold(sqlchange 文件) 应有的结构」，不一致即硬阻断发布。

**Architecture:** 复用现有 `provision`（供给/删库）、`sqlrun.run_sqlchange`（docker 一次性迁移容器）、`prod.publish_pipeline`（发布状态机）。新增纯函数模块 `app/deploy/schemacheck.py` 负责 pg_dump 文本规范化与差异比对；新增 scratch 库辅助（建/删一个临时验证库并把 sqlchange 全量 fold 进去）；发布流水线**在既有 preflight 步内并入等价校验闸门（不改 `STEPLIST`）**；应用 API 新增 `POST /{app_id}/dev/reset` 后台任务。

**Tech Stack:** Python 3.12 / FastAPI / SQLAlchemy async(asyncpg) / PostgreSQL 16（docker 容器 `zerocode-pg16`，含 `pg_dump`）/ pytest（asyncio，docker 相关用 `skipif`）。

**前置 spec:** `docs/superpowers/specs/2026-09-22-dev-prod-schema-parity-design.md`（本计划只覆盖其 §8.2 的 P1；P2 治本段另立计划）。

---

## 文件结构

- 新建 `app/deploy/schemacheck.py` —— 纯文本规范化 + 差异比对（可无 docker 单测）+ 依赖 docker 的 `verify_dev_matches_files`。
- 新建 `app/deploy/scratch.py` —— 临时验证库的建/删（复用 provision 的 admin_url 与角色约定）。
- 修改 `app/deploy/provision.py` —— 抽出一个「按任意库名建/删 + 返回 cred」的内部 helper 供 scratch 复用（不改现有 `provision_env`/`drop_env` 对外行为）。
- 修改 `app/deploy/prod.py` —— 在 `publish_pipeline` 的 **preflight 步内**（命名检查之后）并入 parity 闸门；**不改 `STEPLIST`**（避免波及 `tests/test_prod_engine.py` 与前端步序硬编码）。
- 修改 `app/api/apps.py` —— 新增 `POST /{app_id}/dev/reset` 端点与 `_reset_dev` 后台任务。
- 新建测试 `tests/test_schemacheck.py`（纯函数，无 docker）、`tests/test_schema_parity_e2e.py`（docker skipif）、`tests/test_dev_reset.py`（docker skipif）。

---

## Task 1: 抽出 scratch 库建/删 helper

**Files:**
- Modify: `app/deploy/provision.py`
- Create: `app/deploy/scratch.py`
- Test: `tests/test_schema_parity_e2e.py`（本任务只加一条 provision 级断言，docker skipif）

- [ ] **Step 1: 在 provision.py 增内部建/删库原语**

在 `app/deploy/provision.py` 末尾新增（复用现成 `admin_url()`；不改 `provision_env`/`drop_env`）：

```python
async def create_db_with_role(sf, name: str) -> dict:
    """建一个以平台超级角色(zerocode)为 owner 的库，返回可连 cred；幂等。
    供发布校验的临时库使用，不动 dev/prod 语义。"""
    url = make_url(app_config.database_url())
    admin_user = url.username or "zerocode"
    admin_pw = url.password or ""
    engine = create_async_engine(admin_url(), isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            has_db = (await conn.execute(text(
                "SELECT 1 FROM pg_database WHERE datname = :n"), {"n": name})).first()
            if not has_db:
                await conn.exec_driver_sql(f'CREATE DATABASE "{name}" OWNER "{admin_user}"')
    finally:
        await engine.dispose()
    return {"db": name, "user": admin_user, "password": admin_pw}


async def drop_db(sf, name: str) -> None:
    """强断连后删库；幂等。"""
    engine = create_async_engine(admin_url(), isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            for _ in range(3):
                r = await conn.execute(text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :n AND pid <> pg_backend_pid()"), {"n": name})
                if r.rowcount == 0:
                    break
            has_db = (await conn.execute(text(
                "SELECT 1 FROM pg_database WHERE datname = :n"), {"n": name})).first()
            if has_db:
                await conn.exec_driver_sql(f'DROP DATABASE "{name}"')
    finally:
        await engine.dispose()
```

- [ ] **Step 2: 写 scratch 库对外薄封装**

创建 `app/deploy/scratch.py`：

```python
# app/deploy/scratch.py
"""发布校验专用临时库：名字固定 <slug>_verify，建/删走 provision 原语。"""
from app.deploy import provision


def verify_db_name(slug: str) -> str:
    return f"{slug}_verify"


async def ensure_scratch(sf, slug: str) -> dict:
    await provision.drop_db(sf, verify_db_name(slug))          # 先清残留，保证从零 fold
    return await provision.create_db_with_role(sf, verify_db_name(slug))


async def cleanup_scratch(sf, slug: str) -> None:
    await provision.drop_db(sf, verify_db_name(slug))
```

- [ ] **Step 3: 写一条 scratch 生命周期测试（docker 无关，纯 PG）**

在 `tests/test_schema_parity_e2e.py` 建文件头与本用例：

```python
# tests/test_schema_parity_e2e.py
import pytest
from app.db import make_engine, make_session_factory
from app.deploy import provision, scratch
from app.deploy.sqlrun import docker_available

pytestmark = pytest.mark.skipif(not docker_available(), reason="docker/pg 不可用")


@pytest.fixture
async def sf():
    """平台库 session_factory（provision/read_cred 都走它）。"""
    return make_session_factory(make_engine())


@pytest.mark.asyncio
async def test_scratch_create_and_drop(sf):
    name = scratch.verify_db_name("zcp1verify")
    cred = await scratch.ensure_scratch(sf, "zcp1verify")
    assert cred["db"] == name
    await scratch.cleanup_scratch(sf, "zcp1verify")
```

- [ ] **Step 4: 运行以验证**

Run: `pytest tests/test_schema_parity_e2e.py::test_scratch_create_and_drop -v`
Expected: PASS（若 pg/docker 不可用则 SKIPPED）

- [ ] **Step 5: 提交**

```bash
git add app/deploy/provision.py app/deploy/scratch.py tests/test_schema_parity_e2e.py
git commit -m "feat(deploy): add scratch verify-db helpers for schema parity gate"
```

---

## Task 2: pg_dump 文本规范化 + 差异比对（纯函数）

**Files:**
- Create: `app/deploy/schemacheck.py`
- Test: `tests/test_schemacheck.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_schemacheck.py`：

```python
# tests/test_schemacheck.py
from app.deploy.schemacheck import normalize_dump, schema_diff

DUMP_A = """
-- comment line
SET foo = bar;
SELECT pg_catalog.set_config('search_path', '', false);
CREATE TABLE public.demo_notes (
    id bigint NOT NULL,
    title character varying(200) NOT NULL
);
ALTER TABLE public.demo_notes OWNER TO demo_dev;
"""

# 同结构、但 owner 与库名不同：规范化后必须等价
DUMP_B = DUMP_A.replace("demo_dev", "demo_verify")


def test_normalize_drops_owner_set_and_comments():
    norm = normalize_dump(DUMP_A)
    assert "OWNER TO" not in norm
    assert "SET foo" not in norm
    assert "set_config" not in norm
    assert "CREATE TABLE public.demo_notes" in norm


def test_identical_schema_no_diff():
    assert schema_diff(normalize_dump(DUMP_A), normalize_dump(DUMP_B)) == []


def test_diff_reports_missing_and_extra_objects():
    extra = DUMP_A + "\nCREATE TABLE public.stray (id int);\n"
    diff = schema_diff(normalize_dump(extra), normalize_dump(DUMP_A))
    assert diff["only_in_dev"] and "stray" in "\n".join(diff["only_in_dev"])
    assert not diff["only_in_files"]
```

- [ ] **Step 2: 运行以验证失败**

Run: `pytest tests/test_schemacheck.py -v`
Expected: FAIL（`ModuleNotFoundError: app.deploy.schemacheck`）

- [ ] **Step 3: 实现规范化与 diff**

创建 `app/deploy/schemacheck.py`：

```python
# app/deploy/schemacheck.py
"""pg_dump --schema-only 文本规范化与结构差异比对。

规范化策略（够用即可，P1 为启发式闸门）：
- 丢弃 `--` 注释、空行、`SET ...`、`SELECT pg_catalog.set_config(...)`；
- 丢弃 `ALTER TABLE ... OWNER TO ...` / `GRANT` / `REVOKE`（角色名跨库不同）；
  （调用方 pg_dump 已带 --no-owner --no-privileges，这里双保险）
- 按 `;` 切成语句块，逐块压缩空白后排序，得到与语句输出顺序无关的稳定集合。
"""
from __future__ import annotations
import re

_DROP_PATTERNS = (
    re.compile(r"^--"), re.compile(r"^SET\b", re.I),
    re.compile(r"^SELECT pg_catalog\.set_config", re.I),
    re.compile(r"^ALTER \w+ .*OWNER TO\b", re.I),
    re.compile(r"^(GRANT|REVOKE)\b", re.I),
    re.compile(r"^COMMENT ON\b", re.I),
)


def _drop_line(line: str) -> bool:
    s = line.strip()
    return not s or any(p.match(s) for p in _DROP_PATTERNS)


def normalize_dump(dump: str) -> str:
    kept = [ln for ln in dump.splitlines() if not _drop_line(ln)]
    blocks = []
    for stmt in "\n".join(kept).split(";"):
        flat = re.sub(r"\s+", " ", stmt).strip()
        if flat:
            blocks.append(flat)
    return "\n".join(sorted(blocks))


def _object_names(blocks: list[str]) -> set[str]:
    """从 CREATE 语句块抽对象名，用于缺失/多余归类。"""
    out = set()
    for b in blocks:
        m = re.match(r"CREATE (?:UNIQUE )?(\w+) (?:IF NOT EXISTS )?([\w.]+)", b)
        if m:
            out.add(m.group(2).split(".")[-1].strip('"'))
    return out


def schema_diff(dev_norm: str, files_norm: str) -> dict:
    """返回 dev 相对 files 的差异：only_in_dev / only_in_files（规范化文本块级）。"""
    a = [b for b in dev_norm.split("\n") if b]
    b = [bb for bb in files_norm.split("\n") if bb]
    sa, sb = set(a), set(b)
    only_dev = sorted(sa - sb)
    only_files = sorted(sb - sa)
    return {"only_in_dev": only_dev, "only_in_files": only_files,
            "objects_only_in_dev": sorted(_object_names(only_dev)),
            "objects_only_in_files": sorted(_object_names(only_files))}


def is_parity(diff: dict) -> bool:
    return not diff["only_in_dev"] and not diff["only_in_files"]
```

- [ ] **Step 4: 运行以验证通过**

Run: `pytest tests/test_schemacheck.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: 提交**

```bash
git add app/deploy/schemacheck.py tests/test_schemacheck.py
git commit -m "feat(deploy): add schema dump normalizer and differ"
```

---

## Task 3: docker 侧 pg_dump 抓取 + verify_dev_matches_files

**Files:**
- Modify: `app/deploy/schemacheck.py`（加 async 依赖 docker 部分）
- Test: `tests/test_schema_parity_e2e.py`

- [ ] **Step 1: 写失败集成测试**

在 `tests/test_schema_parity_e2e.py` 追加（需要真实应用工作区，这里用最小临时 sqlchange 目录）：

```python
import shutil
from pathlib import Path
from app.deploy import sqlrun, schemacheck

ROOT = Path("D:/__zc_p1__")
SLUG = "zcp1verify"
SQL_DIR = ROOT / SLUG / "sqlchange"


def _write(files):
    shutil.rmtree(ROOT, ignore_errors=True)
    SQL_DIR.mkdir(parents=True)
    for name, txt in files.items():
        (SQL_DIR / name).write_text(txt, encoding="utf-8", newline="")


@pytest.mark.asyncio
async def test_verify_scratch_fold_is_green(sf):
    """主流程冒烟：files 从零 fold 进 scratch，scratch↔scratch 恒等 → ok=True。
    （真正的「dev 漂移被捕获」由 Step 4 的用例覆盖。）"""
    _write({"V20260922_01_init.sql": "CREATE TABLE IF NOT EXISTS t1 (id INT);\n"})
    await sqlrun.build_if_missing()
    from app.deploy import provision
    cred = await provision.create_db_with_role(sf, scratch.verify_db_name(SLUG))
    try:
        mig = await sqlrun.run_sqlchange(ROOT / SLUG, "verify", cred)
        assert mig["ok"] is True
        norm = schemacheck.normalize_dump(await schemacheck.dump_schema(cred))
        diff = schemacheck.schema_diff(norm, norm)
        assert schemacheck.is_parity(diff) is True
    finally:
        await provision.drop_db(sf, scratch.verify_db_name(SLUG))
        shutil.rmtree(ROOT, ignore_errors=True)
```

> 说明：Step 1 先钉「scratch fold 主流程可跑通且 files↔scratch 恒等」。`sf` 夹具见 Task 1 Step 3 定义。

- [ ] **Step 2: 运行以验证失败**

Run: `pytest tests/test_schema_parity_e2e.py::test_verify_scratch_fold_is_green -v`
Expected: FAIL（`dump_schema` / `create_db_with_role` 未定义）

- [ ] **Step 3: 实现 docker 抓取与主流程**

在 `app/deploy/schemacheck.py` 末尾追加：

```python
import asyncio
from pathlib import Path
from app.deploy import scratch


async def _docker(*args) -> tuple[int, str]:
    p = await asyncio.create_subprocess_exec(
        "docker", *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    out, _ = await p.communicate()
    return p.returncode, out.decode(errors="replace")


async def dump_schema(cred: dict, container: str = "zerocode-pg16") -> str:
    """容器内 pg_dump（--no-owner --no-privileges 去角色差异）。连接走容器本地 socket。"""
    code, out = await _docker(
        "exec", "-e", f"PGPASSWORD={cred['password']}", container,
        "pg_dump", "--schema-only", "--no-owner", "--no-privileges",
        "-U", cred["user"], "-d", cred["db"])
    if code != 0:
        raise RuntimeError(f"pg_dump 失败({cred['db']}):\n{out[-800:]}")
    return out


async def verify_dev_matches_files(sf, app_dir: Path, slug: str) -> dict:
    """P1 校验闸门：把 sqlchange 全量 fold 进 scratch，比对 dev 实际结构。

    返回 {"ok": bool, "diff": {...}}；scratch 用后即删。
    ok=False 表示 dev 与文件不一致（漂移），发布须阻断。
    """
    from app.deploy.provision import read_cred
    cred_dev = await read_cred(sf, slug, "dev")
    if not cred_dev:
        return {"ok": False, "diff": {}, "error": "dev 凭据缺失，无法校验等价性"}
    from app.deploy import sqlrun
    scratch_cred = await scratch.ensure_scratch(sf, slug)
    try:
        mig = await sqlrun.run_sqlchange(app_dir, "verify", scratch_cred)
        if not mig["ok"]:
            return {"ok": False, "diff": {}, "error": f"scratch fold 失败: {mig['log'][-500:]}"}
        dev_norm = normalize_dump(await dump_schema(cred_dev))
        files_norm = normalize_dump(await dump_schema(scratch_cred))
        diff = schema_diff(dev_norm, files_norm)
        return {"ok": is_parity(diff), "diff": diff}
    finally:
        await scratch.cleanup_scratch(sf, slug)
```

- [ ] **Step 4: 追加「dev 漂移被捕获」用例**

编辑 `tests/test_schema_parity_e2e.py`，把漂移场景写成真实用例（fold 后向 dev 直连建 stray 表，再清掉应重新 OK）：

```python
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import create_async_engine
from app.deploy.provision import read_cred


@pytest.mark.asyncio
async def test_verify_flags_dev_drift(sf):
    _write({"V20260922_01_init.sql": "CREATE TABLE IF NOT EXISTS t1 (id INT);\n"})
    # 先让 dev 与 files 对齐
    cred_dev = await read_cred(sf, SLUG, "dev")
    if not cred_dev:
        from app.deploy import provision
        cred_dev = await provision.provision_env(sf, SLUG, "dev")
    await sqlrun.build_if_missing()
    assert (await sqlrun.run_sqlchange(ROOT / SLUG, "dev", cred_dev))["ok"] is True
    assert (await schemacheck.verify_dev_matches_files(sf, ROOT / SLUG, SLUG))["ok"] is True
    # 制造漂移：直接往 dev 建一张不在文件里的表
    eng = create_async_engine(
        f"postgresql+asyncpg://{cred_dev['user']}:{cred_dev['password']}"
        f"@localhost:5433/{cred_dev['db']}")
    async with eng.begin() as c:
        await c.execute(sa_text("CREATE TABLE IF NOT EXISTS stray (id int)"))
    await eng.dispose()
    res = await schemacheck.verify_dev_matches_files(sf, ROOT / SLUG, SLUG)
    assert res["ok"] is False and "stray" in "\n".join(res["diff"]["only_in_dev"])
    # 清理：删掉 stray 后应重新 OK
    eng = create_async_engine(
        f"postgresql+asyncpg://{cred_dev['user']}:{cred_dev['password']}"
        f"@localhost:5433/{cred_dev['db']}")
    async with eng.begin() as c:
        await c.execute(sa_text("DROP TABLE stray"))
    await eng.dispose()
    assert (await schemacheck.verify_dev_matches_files(sf, ROOT / SLUG, SLUG))["ok"] is True
    shutil.rmtree(ROOT, ignore_errors=True)
    from app.deploy import provision
    await provision.drop_env(sf, SLUG, "dev")
```

- [ ] **Step 5: 运行**

Run: `pytest tests/test_schema_parity_e2e.py -v`
Expected: PASS（docker 不可用则 SKIPPED）

- [ ] **Step 6: 提交**

```bash
git add app/deploy/schemacheck.py tests/test_schema_parity_e2e.py
git commit -m "feat(deploy): implement dev-vs-files schema parity verification via pg_dump"
```

---

## Task 4: 把 parity 闸门并入 preflight 步（不改 STEPLIST）

**Files:**
- Modify: `app/deploy/schemacheck.py`（加纯函数 `parity_block_message`）
- Modify: `app/deploy/prod.py`（preflight 块内调用闸门）
- Test: `tests/test_schema_parity_e2e.py`（纯函数部分追加）

- [ ] **Step 1: 写失败测试（纯函数：不等价时给出阻断文案）**

在 `tests/test_schemacheck.py` 追加：

```python
from app.deploy.schemacheck import parity_block_message


def test_parity_block_message_none_when_ok():
    assert parity_block_message({"ok": True, "diff": {}}) is None


def test_parity_block_message_lists_drift_when_mismatch():
    vp = {"ok": False, "diff": {"objects_only_in_dev": ["stray"],
                                 "objects_only_in_files": ["t2"]}}
    msg = parity_block_message(vp)
    assert msg and "stray" in msg and "t2" in msg
```

- [ ] **Step 2: 运行以验证失败**

Run: `pytest tests/test_schemacheck.py -k parity_block_message -v`
Expected: FAIL（`parity_block_message` 未定义）

- [ ] **Step 3: 实现纯函数**

在 `app/deploy/schemacheck.py` 末尾追加：

```python
def parity_block_message(vp: dict) -> str | None:
    """ok=True → None（放行）；否则返回人读的漂移阻断文案（含缺失/多余对象）。"""
    if vp.get("ok"):
        return None
    if vp.get("error"):
        return f"结构等价校验无法完成：{vp['error']}"
    diff = vp.get("diff", {})
    return ("dev 结构与 sqlchange 文件不一致（疑似 dev 漂移），已阻断发布。"
            f"仅存在于 dev: {diff.get('objects_only_in_dev', [])}；"
            f"仅存在于文件: {diff.get('objects_only_in_files', [])}。"
            "请点「重建 dev」补齐，或修正迁移文件后重试")
```

- [ ] **Step 4: 在 preflight 块内调用闸门**

`app/deploy/prod.py` 的 publish_pipeline 内，`await _step_ev(app_id, notify, rst, "preflight", "ok")` **之前**（即 app.json / nginx 检查之后）插入：

```python
        # 1.x dev/prod 结构等价校验（P1 止血；并入 preflight，不改步序）：
        #     dev 实际结构必须等于 fold(sqlchange 文件)，否则视为漂移，硬阻断发布。
        from app.deploy.settings import deploy_cfg as _cfg
        skip = str((await _cfg(sf)).get("deploy.skip_schema_parity", "")).lower() in ("1", "true", "yes")
        if skip:
            await _step(app_id, notify, "⚠ 已跳过 dev/prod 结构等价校验（deploy.skip_schema_parity）")
        else:
            from app.deploy import schemacheck
            vp = await schemacheck.verify_dev_matches_files(sf, root, slug)
            block = schemacheck.parity_block_message(vp)
            if block:
                raise DeployError(block)
            await _step(app_id, notify, "✓ dev 结构与 sqlchange 文件等价校验通过")
```

> 不改 `STEPLIST` 常量（第 197 行）：校验发生在既有 `preflight` 步内，`tests/test_prod_engine.py` 与前端 `useDeploy.js`/`PublishModal.vue` 的步序断言全部保持有效。

- [ ] **Step 5: 运行以验证通过**

Run: `pytest tests/test_schemacheck.py -k parity_block_message -v; python -m pytest tests/test_prod_engine.py -q`
Expected: 前者 PASS；后者全绿（步序未变）

- [ ] **Step 6: 提交**

```bash
git add app/deploy/schemacheck.py app/deploy/prod.py tests/test_schemacheck.py
git commit -m "feat(deploy): block publish on dev/files schema drift inside preflight step"
```

---

## Task 5: dev 一键重建端点

**Files:**
- Modify: `app/api/apps.py`
- Test: `tests/test_dev_reset.py`

- [ ] **Step 1: 写失败测试（后台任务函数行为，替身 docker/provision）**

创建 `tests/test_dev_reset.py`：

```python
import pytest
from app.api import apps


@pytest.mark.asyncio
async def test_reset_dev_drops_provisions_migrates(monkeypatch):
    calls = []

    async def fake_drop_env(sf, slug, env):
        calls.append(("drop", env))

    async def fake_provision(sf, slug, env):
        calls.append(("provision", env))
        return {"db": f"{slug}_{env}", "user": "u", "password": "p"}

    async def fake_root(sf, app):
        return "ROOT"

    async def fake_run_sqlchange(root, env, cred, on_log=None):
        calls.append(("migrate", env))
        return {"ok": True, "code": 0, "log": "ALL DONE"}

    monkeypatch.setattr(apps.provision, "drop_env", fake_drop_env)
    monkeypatch.setattr(apps.provision, "provision_env", fake_provision)
    monkeypatch.setattr(apps.sqlrun, "run_sqlchange", fake_run_sqlchange)
    monkeypatch.setattr(apps.engine, "_app_root", fake_root)

    events = []
    class Hub:
        def publish(self, aid, ev): events.append(ev)

    await apps._reset_dev(object(), {"id": 1, "slug": "demo"}, Hub())
    assert calls == [("drop", "dev"), ("provision", "dev"), ("migrate", "dev")]
    assert events[-1]["type"] == "dev_reset_result" and events[-1]["ok"] is True
```

- [ ] **Step 2: 运行以验证失败**

Run: `pytest tests/test_dev_reset.py -v`
Expected: FAIL（`_reset_dev` / provision import 不存在）

- [ ] **Step 3: 实现 `_reset_dev` 与端点**

在 `app/api/apps.py` 顶部 import 补：

```python
from app.deploy import provision
```

在 `_run_sqlchange` 之后、`sql_apply` 端点附近新增：

```python
async def _reset_dev(sf, app: dict, hub) -> None:
    """重建 dev 库：drop → 重新 provision → 从 sqlchange 文件全量 fold。
    用于消除 dev 漂移，使其重新等于 fold(文件)。"""
    app_id = app["id"]
    slug = app["slug"]

    def on_log(line: str) -> None:
        hub.publish(app_id, {"app_id": app_id, "type": "dev_log", "line": line})

    try:
        await provision.drop_env(sf, slug, "dev")
        cred = await provision.provision_env(sf, slug, "dev")
        root = await engine._app_root(sf, app)
        res = await sqlrun.run_sqlchange(root, "dev", cred, on_log=on_log)
        ok = res["ok"]
    except Exception as e:
        ok, res = False, {"code": -1, "log": str(e)}
    hub.publish(app_id, {"app_id": app_id, "type": "dev_reset_result", "env": "dev", "ok": ok, **res})


@router.post("/{app_id}/dev/reset", status_code=202)
async def dev_reset(request: Request, app_id: int):
    sf = request.app.state.session_factory
    hub = request.app.state.deploy_hub
    try:
        app = await app_service.get_app(sf, app_id)
        _spawn(request, app_id, lambda: _reset_dev(sf, app, hub))
    except DeployError as e:
        raise _http(e)
    return {"ok": True}
```

- [ ] **Step 4: 运行以验证通过**

Run: `pytest tests/test_dev_reset.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add app/api/apps.py tests/test_dev_reset.py
git commit -m "feat(api): add POST /apps/{id}/dev/reset to rebuild dev from migrations"
```

---

## Task 6: 前端「重建 dev」按钮 + 全量回归

**Files:**
- Modify: `frontend/src`（应用详情/dev 操作区，接 `POST /api/apps/{id}/dev/reset` 并监听 `dev_reset_result` 事件）
- Test: `frontend/tests`（对应组件单测，若已有同类按钮范式）

- [ ] **Step 1: 定位现有 dev 操作按钮组**

Run: `Select-String -Path frontend\src\**\*.vue -Pattern "sqlchange/apply|dev_log|migrate_result"`
Expected: 找到现有「执行迁移」按钮与其事件订阅处，作为新按钮的接入模板。

- [ ] **Step 2: 加「重建 dev」按钮与事件处理**

在找到的组件内，复用「执行迁移」按钮的调用/事件范式，新增一个次级按钮：点击 `POST /api/apps/{id}/dev/reset`（202 受理），订阅 `dev_reset_result` 事件更新按钮态与提示（成功刷新迁移记录列表）。

- [ ] **Step 3: 后端全量回归**

Run: `python -m pytest -q`
Expected: 全绿（docker 相关用例在无可用时 SKIPPED）

- [ ] **Step 4: 前端构建与单测**

Run: `cd frontend; npm run test -- --run; npm run build`
Expected: 通过，dist 重新生成

- [ ] **Step 5: 提交**

```bash
git add frontend/src frontend/tests
git commit -m "feat(frontend): add rebuild-dev action wired to dev_reset_result events"
```

---

## 自检对照（spec §8.2 P1）

- **dev 一键重建** → Task 1（scratch/原语）、Task 5（端点/后台）、Task 6（前端）。
- **发布前 schema-only diff 校验闸门** → Task 2（纯函数规范化/diff）、Task 3（pg_dump 抓取 + verify）、Task 4（并入 preflight 闸门 + 阻断文案，`deploy.skip_schema_parity` 兜底开关，**不改 STEPLIST/前端步序**）。
- **不影响既有 dev/prod 迁移语义**：仅在 preflight 步内加校验、新增 reset，`run_sqlchange`/`provision_env` 对外行为不变（Task 4 提供 `deploy.skip_schema_parity` 兜底开关）。

## 已知边界（P1 明确不做，留给 P2）

- 规范化比对是启发式：对「列顺序不同但集合相同」判为等价（按语句块排序）；对「同对象定义差异」能报出块级差异但不定位到具体列。P2 的声明式 differ 会替换为构造性保证。
- 不处理数据轨幂等校验（属 P2/P3）。
- verify 步会为每次发布多创建一个临时库并 fold，秒级开销；docker/pg 不可用时集成测试跳过、发布时若无法 pg_dump 会抛错（生产环境恒有 docker，可接受）。
