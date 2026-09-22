# 破坏性变更确认交互 + 回填脚手架（P3）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 P2 的「破坏性结构变更 = 一段红字硬阻断」升级为可用的**确认式工作流**——发布被拦时前端呈现结构化破坏清单，并提供「一次性人工确认放行」与「生成数据轨回填骨架」两条出路。

**Architecture:** 复用 P2 已有的 `schemadiff` 破坏性检测 + 发布流水线 + `chat_active` 的「结构化 detail → 前端确认 → 带标志重发」先例。后端把破坏性闸门升级为「可确认」（`DeployError` 携带结构化 `payload`，经 `publish_done` 事件的 `code`/`destructive` 字段透传，additive 向后兼容）；回填脚手架是一个纯函数产骨架文本 + 一个同步端点把它落进 `apps/<slug>/sqlchange/`（版本化 `D*.sql`，只新增不覆盖）。

**Tech Stack:** Python 3.12 / FastAPI / SQLAlchemy async / PostgreSQL 16 / pytest（`.venv\Scripts\python.exe -m pytest`）；Vue3 + ant-design-vue + vitest（`npm run test -- --run`，Windows PowerShell 用 `;` 分隔、无 `tail`）。

**设计来源：** `docs/superpowers/specs/2026-09-22-dev-prod-schema-parity-p3-design.md`（已复审通过）

---

## 文件结构（改动地图）

| 文件 | 责任 | 动作 |
|---|---|---|
| `app/deploy/errors.py` | `DeployError` 增可选 `payload` 结构化负载 | Modify |
| `app/deploy/schemadiff.py` | 新增纯函数 `backfill_skeleton(diff, *, today)` | Modify |
| `app/deploy/prod.py` | 新增 `compute_schema_diff`；`publish` 增 `confirm_destructive`；闸门可确认；`done_payload` 并入 `code`/`destructive` | Modify |
| `app/api/apps.py` | `PublishIn` 增 `confirmDestructive` 并透传；新增 `POST /{app_id}/sqlchange/scaffold-backfill` | Modify |
| `tests/test_schemadiff.py` | `backfill_skeleton` 单测 | Modify |
| `tests/test_prod_engine.py` | 放行 / 结构化负载 / `compute_schema_diff` 单测 | Modify |
| `tests/test_publish_api.py` | `fake_publish` 签名补 `confirm_destructive`；确认透传 + scaffold 端点测试 | Modify |
| `frontend/src/api/index.js` | `publishApp` 扩参 + 新增 `scaffoldBackfill` | Modify |
| `frontend/src/components/PublishModal.vue` | `doneFail` 增 `schema_destructive` 富化分支 + 两按钮 | Modify |
| `frontend/src/components/PublishModal.test.js` | 阻断富化分支单测 | Modify |

**关键契约（贯穿全计划，勿各自猜）：**
- `publish_done` 失败事件新增两个**可选**字段：`code`（本期恒为 `"schema_destructive"`，非破坏性失败为 `undefined`）与 `destructive`（`string[]`，人读破坏清单）。既有消费者忽略即可（additive）。
- 后端 `publish(..., *, force=False, confirm_destructive=False)` 两个标志**解耦**：`force` 跳 dev/prod 等价校验（既有），`confirm_destructive` 跳破坏性闸门（本期）。端点请求体字段名为 `confirmDestructive`（camelCase），引擎形参为 `confirm_destructive`（snake_case）。
- 回填骨架文件内容**通体为 `--` 注释**（含被注释掉的 `UPDATE` 占位），保证「未补实即发布」时是幂等 no-op、不会把迁移步打挂；用户/AI 填实后取消注释生效。

---

## Task 1: `backfill_skeleton` 纯函数（schemadiff.py）

**Files:**
- Modify: `app/deploy/schemadiff.py`（文件末尾追加函数）
- Test: `tests/test_schemadiff.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_schemadiff.py` 末尾追加：

```python
from app.deploy.schemadiff import backfill_skeleton


def _diff(prev_sql, cur_sql):
    return schema_diff(parse_schema(prev_sql), parse_schema(cur_sql))


def test_backfill_skeleton_empty_when_no_destructive():
    d = _diff("CREATE TABLE IF NOT EXISTS a (id INT);",
              "CREATE TABLE IF NOT EXISTS a (id INT);\n"
              "ALTER TABLE a ADD COLUMN IF NOT EXISTS b TEXT;")   # 纯新增
    assert backfill_skeleton(d, today="20260922") == ""


def test_backfill_skeleton_covers_dropped_and_type_change():
    prev = ("CREATE TABLE IF NOT EXISTS a (id INT, old TEXT, amount VARCHAR(20));\n"
            "CREATE TABLE IF NOT EXISTS gone (id INT);")
    cur = "CREATE TABLE IF NOT EXISTS a (id INT, amount INT);"   # 删 old、gone 整表删、amount 类型变更
    out = backfill_skeleton(_diff(prev, cur), today="20260922")
    assert out
    assert "20260922" in out                       # 头含生成日期
    assert "删除表 gone" in out                     # 删表项
    assert "a.old" in out                          # 删列项
    assert "a.amount" in out                       # 类型变更项
    # 通体注释：每个非空行都以 -- 开头（未补实即发布为 no-op）
    assert all(ln.startswith("--") for ln in out.splitlines() if ln.strip())
    # 占位 UPDATE 被注释（含 UPDATE 关键字且整行是注释）
    assert any("UPDATE" in ln for ln in out.splitlines() if ln.startswith("--"))


def test_backfill_skeleton_rename_candidate_pairing():
    prev = "CREATE TABLE IF NOT EXISTS a (id INT, fullname TEXT);"
    cur = ("CREATE TABLE IF NOT EXISTS a (id INT, full_name TEXT);")   # drop fullname + add full_name
    out = backfill_skeleton(_diff(prev, cur), today="20260922")
    assert "改名" in out and "a.fullname" in out and "a.full_name" in out
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/test_schemadiff.py -k backfill -v`
Expected: FAIL（`ImportError: cannot import name 'backfill_skeleton'`）

- [ ] **Step 3: 实现 `backfill_skeleton`**

在 `app/deploy/schemadiff.py` 末尾追加：

```python
def backfill_skeleton(diff: dict, *, today: str) -> str:
    """由破坏性 diff 生成数据轨回填骨架文件内容（D<today>_NN_backfill.sql 的正文）。

    只产出注释与被注释掉的幂等 UPDATE 占位——不臆造业务转换（对齐主 spec §19）。
    通体 `--` 注释：未补实即发布也仅是 no-op、不会打断迁移；填实后取消注释生效。
    无破坏性变更 → 返回空串（调用端据此拒绝生成）。
    """
    if not destructive_changes(diff):
        return ""
    L = [
        f"-- 数据轨回填骨架 · 生成于 {today}",
        "-- 用途：承接被破坏性闸门拦下的结构变更（删表/删列/改名/类型变更），在数据轨做一次幂等回填。",
        "-- 这是骨架：以下 UPDATE 均为注释占位，请用真实转换逻辑替换 <...> 后取消注释再发布。",
        "-- 纪律：schema.sql 永不 DROP，物理旧列仍在库中；回填完成后再于下个版本移除旧列声明。",
        "",
    ]
    for t in diff["dropped_tables"]:
        L.append(f"-- 删除表 {t}：先确认无下游依赖并归档数据，下个版本再从 schema.sql 移除")
    if diff["dropped_tables"]:
        L.append("")
    for t in sorted(diff["dropped_columns"]):
        olds = diff["dropped_columns"][t]
        news = list(diff["added_columns"].get(t, []))
        it = iter(news)
        for c in olds:
            rn = next(it, None)
            if rn:
                L.append(f"-- 疑似改名 {t}.{c} → {t}.{rn}（如非改名请忽略本段）")
                L.append(f"-- UPDATE {t} SET {rn} = <从 {c} 派生的表达式> WHERE {rn} IS NULL;")
            else:
                L.append(f"-- 删除列 {t}.{c}：先归档/迁移其数据，下个版本再从 schema.sql 移除")
                L.append(f"-- UPDATE {t} SET <目标列> = <从 {c} 派生的表达式> WHERE <目标列> IS NULL;")
        L.append("")
    for t in sorted(diff["type_changed"]):
        for c in sorted(diff["type_changed"][t]):
            a, b = diff["type_changed"][t][c]
            L.append(f"-- 列 {t}.{c} 类型变更 {a} → {b}：确认既有值可安全转换，必要时先加新列双写")
            L.append(f"-- UPDATE {t} SET {c} = <按 {b} 归一化的表达式> WHERE <守卫条件>;")
        L.append("")
    return "\n".join(L).rstrip() + "\n"
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv\Scripts\python.exe -m pytest tests/test_schemadiff.py -v`
Expected: PASS（含既有 6 条 + 新增 3 条）

- [ ] **Step 5: 提交**

```bash
git add app/deploy/schemadiff.py tests/test_schemadiff.py
git commit -m "feat(p3): add backfill_skeleton pure function for data-track scaffolding"
```

---

## Task 2: 破坏性闸门升级为「可确认」（errors.py + prod.py）

**Files:**
- Modify: `app/deploy/errors.py:2-7`（`DeployError` 增 `payload`）
- Modify: `app/deploy/prod.py`（`publish` 签名、闸门段 line 327-341、`done_payload` line 299-302、失败收口 line 491、新增 `compute_schema_diff`）
- Test: `tests/test_prod_engine.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_prod_engine.py` 末尾追加（沿用既有 `PREV_VER`、`pub_root`、`PubSeams`、`_pub_app`、`sf`、`seed`、`_db_pair`）：

```python
def _seed_destructive(pub_root):
    """当前 schema.sql 删了上一版有 title 列 → 破坏性。返回 (prev_snap, cur_snap)。"""
    (pub_root / "schema").mkdir()
    (pub_root / "schema" / "schema.sql").write_text(
        "CREATE TABLE IF NOT EXISTS demo_notes (id BIGSERIAL PRIMARY KEY);\n",
        encoding="utf-8", newline="")
    prev_dir = pub_root / "releases" / PREV_VER
    prev_dir.mkdir(parents=True)
    (prev_dir / "schema.sql").write_text(
        "CREATE TABLE IF NOT EXISTS demo_notes (id BIGSERIAL PRIMARY KEY);\n"
        "ALTER TABLE demo_notes ADD COLUMN IF NOT EXISTS title VARCHAR(200) NOT NULL;\n",
        encoding="utf-8", newline="")


async def test_compute_schema_diff_helper(tmp_path):
    # 缺基线 → None
    assert prod.compute_schema_diff(tmp_path, None) is None
    assert prod.compute_schema_diff(tmp_path, PREV_VER) is None   # 无文件
    # 有 prev + cur，删列 → 破坏性 diff
    _seed_destructive(tmp_path)
    d = prod.compute_schema_diff(tmp_path, PREV_VER)
    assert d is not None and "title" in d["dropped_columns"].get("demo_notes", [])


async def test_publish_destructive_carries_structured_payload(monkeypatch, sf, seed, pub_root):
    """被拦时 publish_done 事件带 code/destructive 结构化负载（不再只有红字 blob）。"""
    wid, aid = await seed(slug="zcpub", prod_port=8982, dev_port=8983,
                          status_prod="running", current_version=PREV_VER)
    _seed_destructive(pub_root)
    seams = PubSeams(monkeypatch, pub_root)
    with pytest.raises(DeployError):
        await prod.publish(sf, _pub_app(aid), notify=seams.notify, force=True)
    done = seams.dones[-1]
    assert done["ok"] is False
    assert done["code"] == "schema_destructive"
    assert isinstance(done["destructive"], list) and done["destructive"]
    assert any("title" in x for x in done["destructive"])


async def test_publish_confirm_destructive_bypasses_gate(monkeypatch, sf, seed, pub_root):
    """确认放行：confirm_destructive=True → 闸门跳过 → 走完并归档本版快照为新基线。"""
    wid, aid = await seed(slug="zcpub", prod_port=8982, dev_port=8983,
                          status_prod="running", current_version=PREV_VER)
    _seed_destructive(pub_root)
    seams = PubSeams(monkeypatch, pub_root)
    res = await prod.publish(sf, _pub_app(aid), notify=seams.notify,
                             force=True, confirm_destructive=True)
    assert res["status"] == "running"
    assert seams.step_seq.count(("preflight", "ok")) == 1        # preflight 通过
    archived = pub_root / "releases" / res["version"] / "schema.sql"
    assert archived.is_file()
    assert any("人工确认放行" in ln for ln in seams.logs)


async def test_publish_nondestructive_has_no_code_field(monkeypatch, sf, seed, pub_root):
    """既有全绿用例：无 schema/ 破坏性时 done 不带 code（向后兼容）。"""
    wid, aid = await seed(slug="zcpub", prod_port=8982, dev_port=8983,
                          status_prod="running", current_version=PREV_VER)
    seams = PubSeams(monkeypatch, pub_root)
    await prod.publish(sf, _pub_app(aid), notify=seams.notify, force=True)
    assert seams.dones[-1]["ok"] is True
    assert "code" not in seams.dones[-1]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/test_prod_engine.py -k "compute_schema_diff or confirm_destructive or structured_payload or no_code_field" -v`
Expected: FAIL（`compute_schema_diff` 不存在 / `confirm_destructive` 未知 kwarg / `done` 无 `code`）

- [ ] **Step 3a: `DeployError` 增 `payload`**

`app/deploy/errors.py` 全量替换为：

```python
# app/deploy/errors.py
class DeployError(Exception):
    """部署域业务错误；status 用于 REST 映射；payload 为结构化负载（如破坏性清单），
    供失败收口事件透传给前端做分支渲染（不影响 str(e) 的人类可读文案）。"""
    def __init__(self, message: str, status: int = 400, payload: dict | None = None):
        super().__init__(message)
        self.status = status
        self.payload = payload
```

- [ ] **Step 3b: `prod.py` 顶部 import + `compute_schema_diff`**

`app/deploy/prod.py` line 23 之后补一行模块级 import（schemadiff 不依赖 prod，无循环）：

```python
from app.deploy.schemadiff import destructive_changes, destructive_message
from app.deploy import schemadiff
```

在 `# ===== Task 7：发布流水线` 注释块之前（line 195 上方）新增函数：

```python
def compute_schema_diff(root: Path, prev_version: str | None) -> dict | None:
    """结构轨破坏性 diff 的唯一计算口：上一版归档快照 vs 当前 schema.sql。

    缺基线（prev_version 空 / 无归档 / 无当前快照）→ None（无从比较）。破坏性闸门
    与回填脚手架端点共用，避免路径口径漂移。
    """
    if not prev_version:
        return None
    snap_cur = root / "schema" / "schema.sql"
    snap_prev = root / "releases" / prev_version / "schema.sql"
    if not (snap_cur.is_file() and snap_prev.is_file()):
        return None
    return schemadiff.schema_diff(
        schemadiff.parse_schema(snap_prev.read_text(encoding="utf-8")),
        schemadiff.parse_schema(snap_cur.read_text(encoding="utf-8")))
```

- [ ] **Step 3c: `publish` 签名 + `done_payload` 并入结构化负载**

`publish` 定义（line 271）改：

```python
async def publish(sf, app: dict, notify=None, *, force: bool = False,
                  confirm_destructive: bool = False) -> dict:
```

`done_payload`（line 299-302）改：

```python
    def done_payload(ok: bool, error: str = "", payload: dict | None = None) -> dict:
        # 失败时 version 回指 prev_version：前端卡片继续显示「在服务的版本」，不虚报新版本
        out = {"type": "publish_done", "ok": ok, "version": version if ok else prev_version,
               "url": f"http://localhost:{_prod_port(app)}", "error": error}
        if payload:
            out["code"] = payload.get("code")            # 本期恒 "schema_destructive"
            out["destructive"] = payload.get("destructive")
        return out
```

- [ ] **Step 3d: 破坏性闸门段改写为「可确认」**

把现有 line 327-341 的闸门块整段替换（旧块以 `from app.deploy import schemadiff` 内联 import 起、以 `✓ 结构破坏性校验通过` 止）：

```python
        # 1.y 结构破坏性闸门（P2→P3 可确认）：上一版归档快照 vs 当前 schema.sql，
        #     命中删/改名/收窄即阻断；confirm_destructive（一次性）或持久 skip 才放行。
        _skip_destr = str((await deploy_cfg(sf)).get("deploy.skip_schema_destructive", "")).lower() in ("1", "true", "yes")
        if _skip_destr:
            await _step(app_id, notify, "⚠ 已跳过结构破坏性闸门（deploy.skip_schema_destructive 持久兜底）")
        elif confirm_destructive:
            await _step(app_id, notify, "⚠ 破坏性变更已由人工确认放行（本次一次性，不改持久配置）")
        else:
            d = compute_schema_diff(root, prev_version)
            if d is not None:
                msg = destructive_message(d)
                if msg:
                    raise DeployError(msg, payload={
                        "code": "schema_destructive",
                        "destructive": destructive_changes(d),
                        "diff": d})
                await _step(app_id, notify, "✓ 结构破坏性校验通过（相对上一版无删/改名/收窄）")
```

- [ ] **Step 3e: 失败收口把 payload 传进 done 事件**

`except DeployError as e:` 块内（line 491）把：

```python
        await _notify(notify, app_id, done_payload(False, str(e)[:500]))
```

改为：

```python
        await _notify(notify, app_id, done_payload(False, str(e)[:500], getattr(e, "payload", None)))
```

- [ ] **Step 4: 跑测试确认通过（含 P2 既有两条不回归）**

Run: `.venv\Scripts\python.exe -m pytest tests/test_prod_engine.py -k "destructive or confirm_destructive or compute_schema_diff or no_code_field or archives_schema" -v`
Expected: PASS（P3 新增 4 条 + P2 既有 `blocks_on_destructive_schema_drift`、`archives_schema_snapshot_on_success` 仍绿）

- [ ] **Step 5: 提交**

```bash
git add app/deploy/errors.py app/deploy/prod.py tests/test_prod_engine.py
git commit -m "feat(p3): make destructive gate confirmable with structured payload"
```

---

## Task 3: API 透传 `confirmDestructive`（apps.py + 测试替身）

**Files:**
- Modify: `app/api/apps.py:48-49`（`PublishIn`）、`app/api/apps.py:405-406`（`_spawn` 调用）
- Test: `tests/test_publish_api.py:85,119,153`（`fake_publish` 签名）+ 新增断言

- [ ] **Step 1: 先修既有替身签名 + 写失败测试**

`tests/test_publish_api.py` 三处 `async def fake_publish(sf, app, notify=None, *, force=False):` 全部改为（replace_all）：

```python
    async def fake_publish(sf, app, notify=None, *, force=False, confirm_destructive=False):
```

在 `test_publish_chat_gate_and_spawn_shape` 之后新增：

```python
async def test_publish_forwards_confirm_destructive(client, monkeypatch, no_preflight):
    """端点把 confirmDestructive 透传给引擎（与 force 解耦）：body→kwarg 双向可观测。"""
    aid = await _mk(client)
    seen = []

    async def cap(sf, app, notify=None, *, force=False, confirm_destructive=False):
        seen.append((force, confirm_destructive))
        return {"status": "running"}

    monkeypatch.setattr(prod_mod, "publish", cap)
    r = await client.post(f"/api/apps/{aid}/publish",
                          json={"force": False, "confirmDestructive": True})
    assert r.status_code == 202, r.text
    await client.app.state.dev_tasks[aid]
    assert seen == [(False, True)]        # force 未开、破坏性确认开
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/test_publish_api.py::test_publish_forwards_confirm_destructive -v`
Expected: FAIL（`fake_publish() got an unexpected keyword argument 'confirm_destructive'` 或断言 `seen` 为空/False）

> 注：Step 1 已修签名，此处失败点应是端点**没传** `confirm_destructive` → `seen == [(False, False)]`。

- [ ] **Step 3: `PublishIn` 增字段 + 端点透传**

`app/api/apps.py:48-49`：

```python
class PublishIn(BaseModel):
    force: bool = False
    confirmDestructive: bool = False
```

`app/api/apps.py:405-406` 的 `_spawn(...)` 调用改为：

```python
        _spawn(request, app_id, lambda: prod.publish(sf, app, notify=hub.publish,
                                                     force=body.force,
                                                     confirm_destructive=body.confirmDestructive))
```

- [ ] **Step 4: 跑测试确认通过 + 既有发布 API 不回归**

Run: `.venv\Scripts\python.exe -m pytest tests/test_publish_api.py -v`
Expected: PASS（含新 `test_publish_forwards_confirm_destructive`）

- [ ] **Step 5: 提交**

```bash
git add app/api/apps.py tests/test_publish_api.py
git commit -m "feat(p3): forward confirmDestructive from publish endpoint to engine"
```

---

## Task 4: 回填脚手架端点 `POST /{app_id}/sqlchange/scaffold-backfill`

**Files:**
- Modify: `app/api/apps.py`（`sql_files` 端点之后、发布段注释 line 370 之前插入新端点）
- Test: `tests/test_publish_api.py`（末尾追加）

- [ ] **Step 1: 写失败测试**

在 `tests/test_publish_api.py` 末尾追加（`client`、`_mk`、`APP_DIR`、`SLUG`、`prod_mod` 均已在文件内）：

```python
async def test_scaffold_backfill_writes_D_file(client, monkeypatch):
    """有破坏性基线 → 同步写 sqlchange/D<today>_01_backfill.sql，返回 {filename, content}。"""
    import datetime as _dt
    from app.deploy import schemadiff
    aid = await _mk(client)
    root = APP_DIR
    (root / "schema").mkdir(parents=True, exist_ok=True)
    (root / "schema" / "schema.sql").write_text(
        "CREATE TABLE IF NOT EXISTS demo_notes (id BIGSERIAL PRIMARY KEY);\n", encoding="utf-8")
    prev = "b20260920000000"
    (root / "releases" / prev).mkdir(parents=True, exist_ok=True)
    (root / "releases" / prev / "schema.sql").write_text(
        "CREATE TABLE IF NOT EXISTS demo_notes (id BIGSERIAL PRIMARY KEY);\n"
        "ALTER TABLE demo_notes ADD COLUMN IF NOT EXISTS title VARCHAR(200);\n", encoding="utf-8")
    async with client.app.state.session_factory() as s:
        from sqlalchemy import text as _t
        await s.execute(_t("UPDATE app_project SET current_version=:v WHERE id=:i"),
                        {"v": prev, "i": aid})
        await s.commit()

    r = await client.post(f"/api/apps/{aid}/sqlchange/scaffold-backfill")
    assert r.status_code == 200, r.text
    body = r.json()
    today = _dt.datetime.now().strftime("%Y%m%d")
    assert body["filename"] == f"D{today}_01_backfill.sql"
    assert "title" in body["content"]
    on_disk = (root / "sqlchange" / body["filename"])
    assert on_disk.is_file() and on_disk.read_text(encoding="utf-8") == body["content"]

    # 再点一次 → 序号递增到 02（只新增不覆盖）
    r2 = await client.post(f"/api/apps/{aid}/sqlchange/scaffold-backfill")
    assert r2.json()["filename"] == f"D{today}_02_backfill.sql"


async def test_scaffold_backfill_400_without_baseline(client):
    """无 current_version / 无基线 → 400，不落任何文件。"""
    aid = await _mk(client)
    r = await client.post(f"/api/apps/{aid}/sqlchange/scaffold-backfill")
    assert r.status_code == 400
    assert not (APP_DIR / "sqlchange").exists() or not list(
        (APP_DIR / "sqlchange").glob("*_backfill.sql"))


async def test_scaffold_backfill_400_when_no_destructive(client):
    """有基线但非破坏（等价）→ 400「未检出破坏性变更」。"""
    aid = await _mk(client)
    root = APP_DIR
    (root / "schema").mkdir(parents=True, exist_ok=True)
    same = "CREATE TABLE IF NOT EXISTS t (id INT);\n"
    (root / "schema" / "schema.sql").write_text(same, encoding="utf-8")
    prev = "b20260920000000"
    (root / "releases" / prev).mkdir(parents=True, exist_ok=True)
    (root / "releases" / prev / "schema.sql").write_text(same, encoding="utf-8")
    async with client.app.state.session_factory() as s:
        from sqlalchemy import text as _t
        await s.execute(_t("UPDATE app_project SET current_version=:v WHERE id=:i"),
                        {"v": prev, "i": aid})
        await s.commit()
    r = await client.post(f"/api/apps/{aid}/sqlchange/scaffold-backfill")
    assert r.status_code == 400 and "破坏性" in r.json()["detail"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/test_publish_api.py -k scaffold_backfill -v`
Expected: FAIL（404：路由不存在）

- [ ] **Step 3: 实现端点**

`app/api/apps.py` 在 `sql_files` 函数（line 368 结束）之后、`# ---------- 发布与 prod` 注释（line 370）之前插入：

```python
@router.post("/{app_id}/sqlchange/scaffold-backfill")
async def scaffold_backfill(request: Request, app_id: int):
    """破坏性结构变更的数据轨回填骨架生成（同步）：在 sqlchange/ 新增 D<日期>_<序号>_backfill.sql。

    与 store 打包无冲突：store 上架锚点取自 sqlchange_history（DB），未发布应用的半途 D 文件不进包；
    文件名 D<8位日期>_<2位序号>_backfill.sql 匹配 prod._SQL_NAME（[VD] 轨均合法），preflight 命名闸门放行。
    只新增绝不覆盖：序号取当日 D<today>_* 现有最大 +1。
    """
    from datetime import datetime
    from app.deploy import schemadiff
    sf = request.app.state.session_factory
    try:
        app = await app_service.get_app(sf, app_id)
        root = await engine._app_root(sf, app)
    except DeployError as e:
        raise _http(e)
    d = prod.compute_schema_diff(root, app.get("current_version"))
    if d is None:
        raise HTTPException(400, "无上一版结构基线可比对（未发布或缺 schema 快照），无法生成回填骨架")
    if not schemadiff.destructive_changes(d):
        raise HTTPException(400, "未检出破坏性变更，无需回填")
    content = schemadiff.backfill_skeleton(d, today=datetime.now().strftime("%Y%m%d"))
    scdir = root / "sqlchange"
    scdir.mkdir(exist_ok=True)
    today = datetime.now().strftime("%Y%m%d")
    prefix = f"D{today}_"
    seqs = []
    for p in scdir.glob(prefix + "*.sql"):
        tail = p.name[len(prefix):len(prefix) + 2]
        if tail.isdigit():
            seqs.append(int(tail))
    nn = f"{(max(seqs) + 1) if seqs else 1:02d}"
    filename = f"{prefix}{nn}_backfill.sql"
    (scdir / filename).write_text(content, encoding="utf-8", newline="")
    return {"filename": filename, "content": content}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv\Scripts\python.exe -m pytest tests/test_publish_api.py -k scaffold_backfill -v`
Expected: PASS（三条）

- [ ] **Step 5: 提交**

```bash
git add app/api/apps.py tests/test_publish_api.py
git commit -m "feat(p3): add scaffold-backfill endpoint writing versioned D file"
```

---

## Task 5: 前端 API 扩参（api/index.js）

**Files:**
- Modify: `frontend/src/api/index.js:73`（`publishApp`）+ 新增 `scaffoldBackfill`
- Test: 由 Task 6 的 `PublishModal.test.js` 覆盖（api 模块被整体 mock，无需独立测试）

- [ ] **Step 1: 改 `publishApp` 并新增 `scaffoldBackfill`**

`frontend/src/api/index.js:73` 改为：

```js
export const publishApp = (id, force = false, confirmDestructive = false) =>
  http.post(`/apps/${id}/publish`, { force, confirmDestructive })
export const scaffoldBackfill = (id) => http.post(`/apps/${id}/sqlchange/scaffold-backfill`)
```

- [ ] **Step 2: 语法自检**

Run（在 `frontend` 目录）: `npm run build`
Expected: 构建成功（无 import/export 报错）

- [ ] **Step 3: 提交**

```bash
git add frontend/src/api/index.js
git commit -m "feat(p3): publishApp confirmDestructive arg + scaffoldBackfill api"
```

---

## Task 6: PublishModal 阻断态富化（结构化清单 + 两按钮）

**Files:**
- Modify: `frontend/src/components/PublishModal.vue`（imports、computed、`publishWith`、`genSkeleton`、`doneFail` 模板块）
- Test: `frontend/src/components/PublishModal.test.js`

- [ ] **Step 1: 写失败测试**

在 `frontend/src/components/PublishModal.test.js` 的 `mocks.api` 对象（line 12）内补一个 `scaffoldBackfill: vi.fn()`：

```js
  api: { appUrls: vi.fn(), publishApp: vi.fn(), putConfig: vi.fn(), rollbackApp: vi.fn(),
    scaffoldBackfill: vi.fn() },
```

在 `describe('PublishModal 发布链', ...)` 之后新增一个 describe 块：

```js
describe('PublishModal 破坏性确认交互', () => {
  function destructive() {
    const ctx = mountModal()
    ctx.dep.lastDone.value = { type: 'publish_done', ok: false, error: '破坏性 blob...',
      code: 'schema_destructive',
      destructive: ['删除表 gone', '删除列 demo_notes.title（改名亦属此，需两步走）'] }
    return ctx
  }

  it('code=schema_destructive → 渲染结构化清单 + 两按钮，不再是红字 blob', async () => {
    const { wrapper } = destructive()
    await flushPromises()
    const list = wrapper.find('.zc-pub-destr')
    expect(list.exists()).toBe(true)
    expect(list.text()).toContain('demo_notes.title')
    const html = wrapper.text()
    expect(html).toContain('我已确认风险，继续发布')
    expect(html).toContain('生成回填骨架')
  })

  it('「继续发布」→ publishApp(id, true, true)（force + confirmDestructive）', async () => {
    mocks.api.publishApp.mockResolvedValue({ data: { ok: true } })
    const { wrapper } = destructive()
    await flushPromises()
    const btn = wrapper.findAll('.zc-pub-destr .stub-btn')
      .find((b) => b.text().includes('我已确认风险'))
    await btn.trigger('click')
    await flushPromises()
    expect(mocks.api.publishApp).toHaveBeenLastCalledWith(7, true, true)
  })

  it('「生成回填骨架」→ scaffoldBackfill(id) 成功提示落盘文件名', async () => {
    mocks.api.scaffoldBackfill.mockResolvedValue({ data: { filename: 'D20260922_01_backfill.sql' } })
    const { wrapper } = destructive()
    await flushPromises()
    const btn = wrapper.findAll('.zc-pub-destr .stub-btn')
      .find((b) => b.text().includes('生成回填骨架'))
    await btn.trigger('click')
    await flushPromises()
    expect(mocks.api.scaffoldBackfill).toHaveBeenCalledWith(7)
    expect(mocks.antd.message.success).toHaveBeenCalledWith(
      expect.stringContaining('D20260922_01_backfill.sql'))
  })

  it('普通失败（无 code）仍走原 failText + 重试，不显破坏清单', async () => {
    const { wrapper, dep } = mountModal()
    dep.lastDone.value = { type: 'publish_done', ok: false, error: '镜像构建失败' }
    await flushPromises()
    expect(wrapper.find('.zc-pub-destr').exists()).toBe(false)
    expect(wrapper.find('.zc-pub-fail').text()).toContain('镜像构建失败')
  })
})
```

- [ ] **Step 2: 跑测试确认失败**

Run（在 `frontend` 目录）: `npm run test -- --run src/components/PublishModal.test.js`
Expected: FAIL（`.zc-pub-destr` 不存在 / 按钮缺失）

- [ ] **Step 3a: `PublishModal.vue` imports + computed + 函数**

`frontend/src/components/PublishModal.vue:11` 的 import 补 `scaffoldBackfill`：

```js
import { appUrls, publishApp, putConfig, rollbackApp, scaffoldBackfill } from '../api'
```

`failText`（line 38-39）之后新增 computed：

```js
const isDestructive = computed(() => done.value?.code === 'schema_destructive')
const destructiveList = computed(() => (Array.isArray(done.value?.destructive)
  ? done.value.destructive : []))
```

`publishWith`（line 110）签名与调用改（新增第二形参并透传）：

```js
async function publishWith(force, confirmDestructive = false) {
  dep.reset()
  accepting.value = true
  try {
    await publishApp(props.app.id, force, confirmDestructive)
    message.info('发布已受理，进度见下方步骤')
  } catch (e) {
    const detail = e?.response?.data?.detail
    if (e?.response?.status === 409 && detail && detail.code === 'chat_active') {
      Modal.confirm({
        title: 'AI 正在修改代码', content: detail.message,
        okText: '继续发布', cancelText: '等一等',
        onOk: () => publishWith(true, confirmDestructive),
      })
    } else {
      message.error(typeof detail === 'string' ? detail : '发布请求未被受理')
    }
  } finally { accepting.value = false }
}
```

`publishWith` 之后新增 `genSkeleton` 与 `confirmPublish`：

```js
async function confirmPublish() { await publishWith(true, true) }
async function genSkeleton() {
  try {
    const { data } = await scaffoldBackfill(props.app.id)
    message.success(`已写入 sqlchange/${data.filename}，请在 AI 会话补实回填逻辑后重新发布`)
  } catch (e) {
    message.error(e?.response?.data?.detail || '生成回填骨架失败')
  }
}
```

- [ ] **Step 3b: 模板 `doneFail` 块增破坏性分支**

`frontend/src/components/PublishModal.vue` 现有 `<template v-if="doneFail">`（line 201-209）整段替换：

```html
    <template v-if="doneFail && isDestructive">
      <p class="zc-pub-fail">检测到破坏性结构变更，已阻断发布。请确认下列变更系有意为之：</p>
      <ul class="zc-pub-destr">
        <li v-for="(item, i) in destructiveList" :key="i">{{ item }}</li>
        <li v-if="!destructiveList.length">（无明细，见上方说明）</li>
      </ul>
      <a-space>
        <a-button size="middle" @click="genSkeleton">生成回填骨架</a-button>
        <a-button size="middle" type="primary" :loading="running" @click="confirmPublish">
          我已确认风险，继续发布
        </a-button>
      </a-space>
    </template>

    <template v-else-if="doneFail">
      <p class="zc-pub-fail">{{ failText }}</p>
      <a-space>
        <a-button size="middle" :loading="running" @click="start">
          {{ mode === 'rollback' ? '重试回滚' : '重试发布' }}
        </a-button>
        <a-button size="middle" @click="copyError">把错误日志发给 AI</a-button>
      </a-space>
    </template>
```

- [ ] **Step 3c: 样式补一条**

`<style scoped>` 内 `.zc-pub-fail`（line 238-239）之后新增：

```css
.zc-pub-destr { margin: 6px 0 10px; padding-left: 20px; font-size: 12px; color: #b96a6a; }
.zc-pub-destr li { margin: 2px 0; }
```

- [ ] **Step 4: 跑测试确认通过**

Run（在 `frontend` 目录）: `npm run test -- --run src/components/PublishModal.test.js`
Expected: PASS（原有用例 + 新增 4 条）

- [ ] **Step 5: 前端全量 + 构建**

Run（在 `frontend` 目录）: `npm run test -- --run`
Expected: 全绿（较改动前 +4 条用例）
Run: `npm run build`
Expected: `✓ built`

- [ ] **Step 6: 提交**

```bash
git add frontend/src/components/PublishModal.vue frontend/src/components/PublishModal.test.js
git commit -m "feat(p3): PublishModal destructive confirmation + backfill scaffolder actions"
```

---

## 收尾：全量回归

- [ ] **后端全量**

Run: `.venv\Scripts\python.exe -m pytest -q`
Expected: 全绿（P3 新增用例并入既有；docker e2e 视环境可能 skip）

- [ ] **前端全量 + 构建**（若 Task 6 Step 5 未跑则此处跑）

Run（在 `frontend` 目录）: `npm run test -- --run` ； `npm run build`
Expected: 全绿；`✓ built`

- [ ] **确认无占位/漂移**

自查：`code`/`destructive` 字段名、`confirm_destructive`↔`confirmDestructive` 映射、`backfill_skeleton` 通体注释、脚手架文件名匹配 `prod._SQL_NAME`，四处贯穿一致。

---

## Self-Review（对照 spec）

- **spec §3.1 闸门可确认 + 结构化负载** → Task 2（payload/confirm_destructive/done_payload）。✓
- **spec §3.2 compute_schema_diff 共享助手** → Task 2 Step 3b，Task 4 端点复用。✓
- **spec §3.3 backfill_skeleton 纯函数 + 同步端点** → Task 1 + Task 4。✓
- **spec §3.4 前端富化两按钮** → Task 5（api）+ Task 6（组件）。✓
- **spec §4 数据流**：首次发布被拦→done 带 code/destructive→点生成骨架→POST scaffold→写 D 文件；确认→publish(force, confirmDestructive=true)→归档新基线。Task 2/4/6 覆盖。✓
- **类型一致性**：`compute_schema_diff(root, prev_version)`（Task 2 定义、Task 4 以 `current_version` 作实参调用，语义即「上一在服版」）；`backfill_skeleton(diff, *, today)`（Task 1 定义、Task 4 调用）；`publish(..., confirm_destructive=)`（Task 2 定义、Task 3 透传）。✓
- **常驻规则（SQL 版本管理）**：回填产物落 `sqlchange/` 且版本化命名、只新增不覆盖（Task 4 序号递增）。✓
- **无占位符**：每步含真实代码/命令/期望。`<expr>` 类尖括号是**生成骨架内**给终端用户的填空标记，非计划占位。✓
