# zerocode 对话交互增强 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 zerocode 补齐对标 DSH 的对话交互能力：任务列表（todo_write）、多问题提问（ask_user 升级）、思考内容查看（reasoning）、@文件指定（智能注入）、上下文压缩（自动+手动）、会话级模型选择（多模型库）。

**Architecture:** 沿用一期「事件流 + 纯函数 applyEvent」架构做增量扩展（方案 A）：新增工具/事件/表/列，WS 通道与 hub 挂起机制复用；消息表用「content 文本 + context_content 旁路列」双份存储实现 @ 展开只注入一次；压缩摘要与水位落 compaction 表；模型按会话解析、逐级回退（会话→默认→全局）。

**Tech Stack:** FastAPI + SQLAlchemy(async) + PostgreSQL(asyncpg) + pytest(asyncio_mode=auto)；Vue3 + Pinia + Ant Design Vue 4 + vitest。

**Spec:** `docs/superpowers/specs/2026-09-17-zerocode-interaction-design.md`（本计划逐条对应 spec §1-§9，spec 与计划一起阅读）

## Global Constraints

- 后端测试命令：`D:\project\zerocode\.venv\Scripts\python.exe -m pytest tests -q`（自动建 PG 测试库 zerocode_test@localhost:5433，需本机 PG 在跑）
- 前端测试命令：workdir `D:\project\zerocode\frontend`，`npx vitest run`；构建 `npm run build`
- vitest 只测纯函数（无 @vue/test-utils 依赖）；组件正确性由 Task 17 browser-skill 冒烟兜底
- 所有注释/提交信息中文；提交格式 `feat|test|fix|docs: 中文描述`；每任务一提交
- SQL 变更只进 `changelog/V*.sql`（迁移器按文件名序执行一次，见 app/db.py）；不往其他文件塞 SQL
- WS 下行新事件：`reasoning_delta {content}` / `todo_sync {todos}` / `compacting` / `compaction_done {summary, up_to_seq}`；上行新增 `ask_submit {answers}` / `compact`；移除 `ask_answer`/`ask_choice`
- 新配置键（app_config 代码内默认兜底，Settings 页不做）：`agent.auto_compact_chars=50000`、`agent.compact_keep_recent=8`、`agent.compact_min_new=6`
- 文件写入保持 LF（write/edit 工具默认）；不生成 .bat
- @ 注入阈值 20KB（`MENTION_MAX_BYTES`）；read_file 上限仍是 200KB

---

### Task 1: 迁移 V20260917_02（新表/新列）+ conftest 清表扩展

**Files:**
- Create: `changelog/V20260917_02_interaction.sql`
- Modify: `tests/conftest.py:26`（TRUNCATE 列表）
- Test: `tests/test_migrations.py`（文件末尾追加）

**Interfaces:**
- Produces: 表 `model_config(id,name,base_url,api_key,model,is_default,created_at)`、`compaction(id,session_id,summary,up_to_seq,created_at)`；列 `chat_session.model_config_id/todo_state`、`message.context_content/reasoning`。后续所有任务依赖。

- [ ] **Step 1: 写迁移 SQL**

```sql
-- V20260917_02_interaction.sql
-- 日期：2026-09-17
-- 用途：对话交互增强——模型库、会话级模型与任务快照、@展开旁路列与思考留痕、压缩摘要表
-- 影响范围：model_config（新）、compaction（新）、chat_session、message

CREATE TABLE model_config (
  id BIGSERIAL PRIMARY KEY,
  name VARCHAR(100) NOT NULL,
  base_url VARCHAR(500) NOT NULL,
  api_key VARCHAR(500) NOT NULL DEFAULT '',
  model VARCHAR(200) NOT NULL,
  is_default BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- 同库唯一默认（部分唯一索引：仅约束 is_default=true 的行）
CREATE UNIQUE INDEX uk_model_default ON model_config (is_default) WHERE is_default;

ALTER TABLE chat_session
  ADD COLUMN model_config_id BIGINT REFERENCES model_config(id) ON DELETE SET NULL,
  ADD COLUMN todo_state JSONB NOT NULL DEFAULT '[]'::jsonb;

ALTER TABLE message
  ADD COLUMN context_content TEXT,
  ADD COLUMN reasoning TEXT;

CREATE TABLE compaction (
  id BIGSERIAL PRIMARY KEY,
  session_id BIGINT NOT NULL REFERENCES chat_session(id) ON DELETE CASCADE,
  summary TEXT NOT NULL,
  up_to_seq BIGINT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_compaction_session ON compaction (session_id, id DESC);

-- 回滚：
-- DROP TABLE compaction;
-- ALTER TABLE message DROP COLUMN reasoning, DROP COLUMN context_content;
-- ALTER TABLE chat_session DROP COLUMN todo_state, DROP COLUMN model_config_id;
-- DROP INDEX uk_model_default;
-- DROP TABLE model_config;
```

注意：迁移器 `split_statements` 会把行内 `--` 之后内容当注释剥掉，因此回滚块必须整体以 `--` 开头（如上），且 SQL 字符串里不得出现 `--`。

- [ ] **Step 2: 写失败测试（追加到 tests/test_migrations.py 末尾）**

```python
# ---------- V20260917_02：交互增强表结构 ----------

async def test_interaction_migration_schema(test_engine):
    from sqlalchemy import text
    async with test_engine.connect() as conn:
        tables = (await conn.execute(text(
            "SELECT tablename FROM pg_tables WHERE schemaname='public'"))).scalars().all()
        cols = (await conn.execute(text(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_name IN ('chat_session', 'message')"))).all()
    assert "model_config" in tables and "compaction" in tables
    s_cols = {c[1] for c in cols if c[0] == "chat_session"}
    assert {"model_config_id", "todo_state"} <= s_cols
    m_cols = {c[1] for c in cols if c[0] == "message"}
    assert {"context_content", "reasoning"} <= m_cols
```

- [ ] **Step 3: 跑测试确认失败**

Run: `D:\project\zerocode\.venv\Scripts\python.exe -m pytest tests/test_migrations.py -q`
Expected: 新用例 FAIL（表/列不存在），既有用例 PASS

- [ ] **Step 4: 创建迁移文件（Step 1 内容）并扩展 conftest 清表**

`tests/conftest.py` 第 26 行 TRUNCATE 改为：

```python
        await conn.execute(text(
            "TRUNCATE message, file_change, compaction, chat_session, workspace, model_config "
            "RESTART IDENTITY CASCADE"))
```

- [ ] **Step 5: 跑测试确认通过**

Run: `D:\project\zerocode\.venv\Scripts\python.exe -m pytest tests/test_migrations.py -q`
Expected: PASS（迁移器对已建过表的开发库幂等——schema_migration 已记录旧文件名，新文件按序执行）

- [ ] **Step 6: Commit**

```bash
git add changelog/V20260917_02_interaction.sql tests/test_migrations.py tests/conftest.py
git commit -m "feat: 交互增强迁移（模型库/压缩表/会话todo与模型列/消息reasoning与context列）"
```

---

### Task 2: 模型库 REST API /api/models

**Files:**
- Create: `app/api/models.py`
- Modify: `app/main.py:56-63`（注册路由）
- Test: `tests/test_models_api.py`（新建）

**Interfaces:**
- Consumes: Task 1 的 `model_config` 表
- Produces: `GET/POST /api/models`、`PUT/DELETE /api/models/{id}`；响应统一掩码 `api_key: "sk-***"|"|"`；PUT 传 `"sk-***"` 保留原密钥；POST/PUT `is_default=true` 清其他默认

- [ ] **Step 1: 写失败测试**

```python
"""Task 2：模型库 CRUD（掩码回显 / 默认唯一 / 掩码哨兵不改密钥）。"""
import pytest

async def _create(client, **kw):
    payload = {"name": "m1", "base_url": "http://x/v1", "api_key": "sk-real", "model": "deepseek-chat", **kw}
    return (await client.post("/api/models", json=payload)).json()

async def test_create_and_list_masks_key(client):
    mid = (await _create(client))["id"]
    data = (await client.get("/api/models")).json()
    assert len(data) == 1 and data[0]["id"] == mid
    assert data[0]["api_key"] == "sk-***"  # 列表永远掩码

async def test_default_unique(client):
    await _create(client, is_default=True)
    m2 = await _create(client, name="m2", is_default=True)
    rows = (await client.get("/api/models")).json()
    defaults = [r for r in rows if r["is_default"]]
    assert len(defaults) == 1 and defaults[0]["id"] == m2["id"]

async def test_put_masked_key_keeps_old(client):
    mid = (await _create(client))["id"]
    await client.put(f"/api/models/{mid}", json={
        "name": "m1b", "base_url": "http://y/v1", "api_key": "sk-***", "model": "m2"})
    async with client.app.state.session_factory() as s:
        from sqlalchemy import text
        key = (await s.execute(text("SELECT api_key FROM model_config WHERE id=:i"), {"i": mid})).scalar()
    assert key == "sk-real"  # 掩码哨兵=不改

async def test_delete(client):
    mid = (await _create(client))["id"]
    assert (await client.delete(f"/api/models/{mid}")).json()["ok"] is True
    assert (await client.get("/api/models")).json() == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:\project\zerocode\.venv\Scripts\python.exe -m pytest tests/test_models_api.py -q`
Expected: FAIL（404，路由不存在）

- [ ] **Step 3: 实现 app/api/models.py**

```python
"""模型库 CRUD：多模型配置（名称/接入点/密钥/模型名/默认标记）。api_key 永远掩码回显；
PUT 传掩码哨兵 "sk-***" 表示保留原密钥；is_default=true 时清除其他默认（部分唯一索引兜底）。"""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import text

router = APIRouter(prefix="/api/models")
MASK = "sk-***"

class ModelIn(BaseModel):
    name: str
    base_url: str
    api_key: str = ""
    model: str
    is_default: bool = False

def _row_out(r) -> dict:
    d = dict(r)
    d["api_key"] = MASK if d["api_key"] else ""
    return d

_SELECT = ("SELECT id, name, base_url, api_key, model, is_default, created_at "
           "FROM model_config")

@router.get("")
async def list_models(request: Request):
    async with request.app.state.session_factory() as s:
        rows = (await s.execute(text(_SELECT + " ORDER BY is_default DESC, id"))).mappings().all()
    return [_row_out(r) for r in rows]

@router.post("")
async def create_model(request: Request, body: ModelIn):
    async with request.app.state.session_factory() as s:
        if body.is_default:
            await s.execute(text("UPDATE model_config SET is_default=FALSE WHERE is_default"))
        row = (await s.execute(text(
            "INSERT INTO model_config (name, base_url, api_key, model, is_default) "
            "VALUES (:n, :b, :k, :m, :d) RETURNING id, name, base_url, api_key, model, is_default, created_at"),
            {"n": body.name, "b": body.base_url, "k": body.api_key, "m": body.model,
             "d": body.is_default})).mappings().first()
        await s.commit()
    return _row_out(row)

@router.put("/{model_id}")
async def update_model(request: Request, model_id: int, body: ModelIn):
    async with request.app.state.session_factory() as s:
        old = (await s.execute(text("SELECT api_key FROM model_config WHERE id=:i"), {"i": model_id})).first()
        if not old:
            raise HTTPException(404, "模型不存在")
        new_key = old[0] if body.api_key == MASK else body.api_key
        if body.is_default:
            await s.execute(text("UPDATE model_config SET is_default=FALSE WHERE is_default AND id <> :i"),
                            {"i": model_id})
        await s.execute(text(
            "UPDATE model_config SET name=:n, base_url=:b, api_key=:k, model=:m, is_default=:d WHERE id=:i"),
            {"n": body.name, "b": body.base_url, "k": new_key, "m": body.model,
             "d": body.is_default, "i": model_id})
        await s.commit()
    return {"ok": True}

@router.delete("/{model_id}")
async def delete_model(request: Request, model_id: int):
    async with request.app.state.session_factory() as s:
        await s.execute(text("DELETE FROM model_config WHERE id=:i"), {"i": model_id})
        await s.commit()
    return {"ok": True}
```

`app/main.py` 路由注册处（第 56 行附近）把 import 与 include 补上：

```python
    from app.api import workspaces, sessions, files, models
    ...
    app.include_router(models.router)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `D:\project\zerocode\.venv\Scripts\python.exe -m pytest tests/test_models_api.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/api/models.py app/main.py tests/test_models_api.py
git commit -m "feat: 模型库REST（CRUD/掩码回显/默认唯一/掩码哨兵不改密钥）"
```

---

### Task 3: 会话详情 / 模型切换 / messages 带 reasoning

**Files:**
- Modify: `app/api/sessions.py`
- Test: `tests/test_sessions.py`（末尾追加）

**Interfaces:**
- Consumes: Task 1 的列
- Produces: `GET /api/sessions/{id}` → `{..., todo_state: list, model_config_id: int|null}`；`PATCH /api/sessions/{id}/model` body `{model_config_id: int|null}`；`GET /api/sessions/{id}/messages` 每条含 `reasoning`。前端 Task 11/15 依赖。

- [ ] **Step 1: 写失败测试（追加）**

```python
# ---------- 交互增强：会话详情 / 模型切换 / messages 带 reasoning ----------

async def test_session_detail(client):
    ws = (await client.post("/api/workspaces", json={"root_path": "D:/tmp"})).json()
    sid = (await client.post("/api/sessions", json={"workspace_id": ws["id"]})).json()["id"]
    d = (await client.get(f"/api/sessions/{sid}")).json()
    assert d["todo_state"] == [] and d["model_config_id"] is None
    assert (await client.get("/api/sessions/999999")).status_code == 404

async def test_patch_session_model(client):
    ws = (await client.post("/api/workspaces", json={"root_path": "D:/tmp"})).json()
    sid = (await client.post("/api/sessions", json={"workspace_id": ws["id"]})).json()["id"]
    mc = (await client.post("/api/models", json={
        "name": "m", "base_url": "http://x/v1", "model": "g"})).json()
    r = await client.patch(f"/api/sessions/{sid}/model", json={"model_config_id": mc["id"]})
    assert r.json()["ok"] is True
    d = (await client.get(f"/api/sessions/{sid}")).json()
    assert d["model_config_id"] == mc["id"]
    # 回退默认：null
    await client.patch(f"/api/sessions/{sid}/model", json={"model_config_id": None})
    assert ((await client.get(f"/api/sessions/{sid}")).json())["model_config_id"] is None
    # 不存在的模型 → 400
    r = await client.patch(f"/api/sessions/{sid}/model", json={"model_config_id": 999})
    assert r.status_code == 400

async def test_messages_include_reasoning(client):
    ws = (await client.post("/api/workspaces", json={"root_path": "D:/tmp"})).json()
    sid = (await client.post("/api/sessions", json={"workspace_id": ws["id"]})).json()["id"]
    from app.services.message_service import add_message
    await add_message(client.app.state.session_factory, sid, "assistant",
                      content="答", reasoning="想了想")
    msgs = (await client.get(f"/api/sessions/{sid}/messages")).json()
    assert msgs[0]["reasoning"] == "想了想"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:\project\zerocode\.venv\Scripts\python.exe -m pytest tests/test_sessions.py -q`
Expected: 新用例 FAIL（404/字段缺失），既有 PASS

- [ ] **Step 3: 实现（app/api/sessions.py）**

```python
class ModelIn(BaseModel):
    model_config_id: int | None = None

@router.get("/{session_id}")
async def session_detail(request: Request, session_id: int):
    async with request.app.state.session_factory() as s:
        row = (await s.execute(text(
            "SELECT id, workspace_id, title, status, todo_state, model_config_id "
            "FROM chat_session WHERE id=:i"), {"i": session_id})).mappings().first()
    if not row:
        raise HTTPException(404, "会话不存在")
    return dict(row)

@router.patch("/{session_id}/model")
async def set_session_model(request: Request, session_id: int, body: ModelIn):
    async with request.app.state.session_factory() as s:
        if body.model_config_id is not None:
            ok = (await s.execute(text("SELECT 1 FROM model_config WHERE id=:i"),
                                  {"i": body.model_config_id})).first()
            if not ok:
                raise HTTPException(400, "模型不存在")
        r = await s.execute(text(
            "UPDATE chat_session SET model_config_id=:m WHERE id=:i"),
            {"m": body.model_config_id, "i": session_id})
        await s.commit()
        if r.rowcount == 0:
            raise HTTPException(404, "会话不存在")
    return {"ok": True}
```

`get_messages` 的 SELECT 列表加 `reasoning`：

```python
            "SELECT id, seq, role, content, reasoning, tool_call_id, tool_name, tool_args, tool_result, created_at "
```

- [ ] **Step 4: 跑测试确认通过（全量）**

Run: `D:\project\zerocode\.venv\Scripts\python.exe -m pytest tests -q`
Expected: PASS（含既有用例无回归）

- [ ] **Step 5: Commit**

```bash
git add app/api/sessions.py tests/test_sessions.py
git commit -m "feat: 会话详情与模型切换API，messages响应带reasoning"
```

---

### Task 4: LLM 层 reasoning 解析 + 循环转发与落库

**Files:**
- Modify: `app/agent/llm.py:43-67`（chat_stream）、`app/agent/loop.py:104-105`（转发）、`app/agent/loop.py:129`（落库）、`app/services/message_service.py`（reasoning 参数）
- Test: `tests/test_llm.py`（追加+改 1 处断言）、`tests/test_agent_loop.py`（追加）

**Interfaces:**
- Produces: `chat_stream` 新事件 `{"type":"reasoning_delta","content":str}`（有才发）；done message 新键 `"reasoning": str|None`；`add_message(..., reasoning=None)`；WS 下行 `reasoning_delta` 原样透传。前端 Task 11/12 依赖。

- [ ] **Step 1: 写失败测试**

`tests/test_llm.py` 追加（复用既有 stub 基建）：

```python
# ---------- reasoning_content 解析（Task 4） ----------

def _rdelta(reasoning=None, content=None):
    return SimpleNamespace(content=content, tool_calls=None, reasoning_content=reasoning)

@pytest.mark.asyncio
async def test_stream_reasoning_delta_and_done(monkeypatch):
    llm, _ = _make_llm(monkeypatch, [
        _chunk(_rdelta(reasoning="先想")),
        _chunk(_rdelta(reasoning="清楚")),
        _chunk(_rdelta(content="答案")),
    ])
    evs = await _collect(llm)
    assert [e["type"] for e in evs] == ["reasoning_delta", "reasoning_delta", "content_delta", "done"]
    assert evs[0]["content"] == "先想"
    assert evs[-1]["message"]["reasoning"] == "先想清楚"

@pytest.mark.asyncio
async def test_stream_no_reasoning_no_event(monkeypatch):
    # 既有 _delta（无 reasoning_content 属性）不产生 reasoning 事件，done.reasoning=None
    llm, _ = _make_llm(monkeypatch, [_chunk(_delta(content="ok"))])
    evs = await _collect(llm)
    assert all(e["type"] != "reasoning_delta" for e in evs)
    assert evs[-1]["message"]["reasoning"] is None
```

同文件 `test_stream_empty_round_done_none` 的最后断言改为（done 消息新增 reasoning 键）：

```python
    assert evs[-1]["message"] == {"role": "assistant", "content": None, "tool_calls": None, "reasoning": None}
```

`tests/test_agent_loop.py` 追加：

```python
@pytest.mark.asyncio
async def test_reasoning_forwarded_and_saved(test_engine, tmp_path):
    turns = [[{"type": "reasoning_delta", "content": "想"}, {"type": "content_delta", "content": "答"},
              {"type": "done", "message": {"role": "assistant", "content": "答", "tool_calls": None,
                                           "reasoning": "想"}}]]
    sid, status, evs, sf = await run_once(test_engine, tmp_path, turns)
    assert any(e["type"] == "reasoning_delta" and e["content"] == "想" for e in evs)
    async with sf() as s:
        r = (await s.execute(sql(
            "SELECT reasoning FROM message WHERE session_id=:i AND role='assistant'"), {"i": sid})).scalar()
    assert r == "想"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:\project\zerocode\.venv\Scripts\python.exe -m pytest tests/test_llm.py tests/test_agent_loop.py -q`
Expected: 新用例 FAIL，其余 PASS

- [ ] **Step 3: 实现**

`app/agent/llm.py` chat_stream 改造（模块级加 `_reasoning_of`）：

```python
def _reasoning_of(delta):
    """兼容取 reasoning_content：SDK 具名属性优先，其次 model_extra（未知字段兜底）。"""
    if delta is None:
        return None
    rc = getattr(delta, "reasoning_content", None)
    if rc is None:
        rc = (getattr(delta, "model_extra", None) or {}).get("reasoning_content")
    return rc
```

chat_stream 内：

```python
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        ...
            rc = _reasoning_of(delta)
            if rc:
                reasoning_parts.append(rc)
                yield {"type": "reasoning_delta", "content": rc}
            if delta and delta.content:
                ...
        yield {"type": "done", "message": {
            "role": "assistant",
            "content": "".join(content_parts) or None,
            "reasoning": "".join(reasoning_parts) or None,
            "tool_calls": [tool_calls[i] for i in sorted(tool_calls)] or None}}
```

`app/services/message_service.py`：`add_message` 签名加 `reasoning=None`，INSERT 列与参数加 `reasoning`：

```python
async def add_message(session_factory, session_id: int, role: str, content=None, reasoning=None,
                      tool_call_id=None, tool_name=None, tool_args=None, tool_result=None) -> dict:
    ...
            "INSERT INTO message (session_id, seq, role, content, reasoning, tool_call_id, tool_name, tool_args, tool_result) "
            "VALUES (:sid, COALESCE((SELECT max(seq)+1 FROM message WHERE session_id=:sid), 1), "
            ":role, :content, :reasoning, :tcid, :tname, CAST(:targs AS JSONB), CAST(:tresult AS JSONB)) "
            "RETURNING id, seq, role, content, reasoning, tool_call_id, tool_name, tool_args, tool_result, created_at"),
            {..., "reasoning": reasoning, ...}
```

`app/agent/loop.py` `_inner` 流式循环加转发；assistant 落库加 reasoning：

```python
                if ev["type"] == "reasoning_delta":
                    await self.send(ev)
                elif ev["type"] == "content_delta":
                    await self.send({"type": "token", "delta": ev["content"]})
                elif ev["type"] == "done":
                    final = ev["message"]
```

```python
            await add_message(self.sf, session_id, "assistant", content=final.get("content"),
                              reasoning=final.get("reasoning"))
```

- [ ] **Step 4: 跑测试确认通过（全量）**

Run: `D:\project\zerocode\.venv\Scripts\python.exe -m pytest tests -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/agent/llm.py app/agent/loop.py app/services/message_service.py tests/test_llm.py tests/test_agent_loop.py
git commit -m "feat: 流式reasoning解析/转发/留痕（端点不支持则无此事件）"
```

---

### Task 5: todo_write 工具（任务列表）

**Files:**
- Modify: `app/agent/llm.py`（TOOLS 注册）、`app/agent/tools.py`（execute_tool 分支）
- Test: `tests/test_agent_loop.py`（追加）

**Interfaces:**
- Consumes: Task 1 `chat_session.todo_state`
- Produces: 工具 `todo_write(todos:[{content,status: pending|in_progress|completed}])` 全量替换；执行后下发 `todo_sync {todos}` 事件并更新 `todo_state`；返回 `{"ok":true,"count":N}`；非法 status → ToolError。前端 Task 14 依赖。

- [ ] **Step 1: 写失败测试（追加到 tests/test_agent_loop.py）**

```python
@pytest.mark.asyncio
async def test_todo_write_sync_and_persist(test_engine, tmp_path):
    todos = [{"content": "实现A", "status": "in_progress"}, {"content": "写测试", "status": "pending"}]
    turns = [done(None, [tool_call("c1", "todo_write", todos=todos)]), done("清单已建")]
    sid, status, evs, sf = await run_once(test_engine, tmp_path, turns)
    assert status == "idle"
    sync = next(e for e in evs if e["type"] == "todo_sync")
    assert sync["todos"] == todos
    async with sf() as s:
        st = (await s.execute(sql(
            "SELECT todo_state FROM chat_session WHERE id=:i"), {"i": sid})).scalar()
    assert st == todos

@pytest.mark.asyncio
async def test_todo_write_invalid_status(test_engine, tmp_path):
    turns = [done(None, [tool_call("c1", "todo_write", todos=[{"content": "x", "status": "bad"}])]),
             done("知道了")]
    sid, status, evs, sf = await run_once(test_engine, tmp_path, turns)
    tr = next(e for e in evs if e["type"] == "tool_result")
    assert tr["result"]["ok"] is False and "非法" in tr["result"]["error"]
```

`tests/test_llm.py` 的 `test_tools_schema` 集合断言加入 todo_write：

```python
    assert names == {"list_dir", "read_file", "write_file", "edit_file", "ask_user", "todo_write"}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:\project\zerocode\.venv\Scripts\python.exe -m pytest tests/test_agent_loop.py tests/test_llm.py -q`
Expected: FAIL

- [ ] **Step 3: 实现**

`app/agent/llm.py` TOOLS 追加：

```python
    {"type": "function", "function": {
        "name": "todo_write",
        "description": "写入/全量替换当前任务清单。多步任务开始前先建清单，每完成一步即更新状态",
        "parameters": {"type": "object", "properties": {
            "todos": {"type": "array", "items": {"type": "object", "properties": {
                "content": {"type": "string", "description": "任务描述"},
                "status": {"type": "string", "enum": ["pending", "in_progress", "completed"],
                           "description": "状态：待办/进行中/已完成"}},
                "required": ["content", "status"]}}}, "required": ["todos"]}}},
```

`app/agent/tools.py` `execute_tool` 末尾 `raise ToolError` 之前加分支：

```python
    if name == "todo_write":
        raw = args.get("todos")
        if not isinstance(raw, list):
            raise ToolError("todos 必须是数组")
        todos = []
        for t in raw:
            st = t.get("status")
            if st not in ("pending", "in_progress", "completed"):
                raise ToolError(f"非法任务状态: {st}")
            todos.append({"content": str(t.get("content", "")), "status": st})
        if ctx.session_factory is not None:
            async with ctx.session_factory() as s:
                await s.execute(text(
                    "UPDATE chat_session SET todo_state = CAST(:j AS JSONB) WHERE id = :i"),
                    {"j": json.dumps(todos, ensure_ascii=False), "i": ctx.session_id})
                await s.commit()
        await ctx.send({"type": "todo_sync", "todos": todos})
        return {"ok": True, "count": len(todos)}
```

- [ ] **Step 4: 跑测试确认通过（全量）**

Run: `D:\project\zerocode\.venv\Scripts\python.exe -m pytest tests -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/agent/llm.py app/agent/tools.py tests/test_agent_loop.py tests/test_llm.py
git commit -m "feat: todo_write工具（全量替换/todo_sync事件/todo_state落库）"
```

---

### Task 6: ask_user 多问题升级（协议改动）

**Files:**
- Modify: `app/agent/llm.py`（ask_user schema）、`app/hub.py`（ask/submit_answers）、`app/agent/loop.py`（_dispatch + _normalize_questions）、`app/api/ws.py`（上行分支）
- Test: `tests/test_agent_loop.py`（追加+改 2 处）、`tests/test_ws.py`（改 3 处上行）

**Interfaces:**
- Produces: 工具参数 `{questions:[{question, options:[{label,description?,recommended?}], multi_select?}]}`；hub.ask(session_id, questions, timeout)；恢复值 `{"answers": [...]}`（每题 `{answer}` 或 `{options:[...]}`）；WS 上行 `ask_submit {answers}`；旧参数 `question/options`(str) 自动包装单题。**移除** hub.answer/choose 与 WS ask_answer/ask_choice。

- [ ] **Step 1: 改既有测试为新协议 + 新增测试**

`tests/test_agent_loop.py`：
- `test_ask_user_resume` 改为：

```python
@pytest.mark.asyncio
async def test_ask_user_resume(test_engine, tmp_path):
    async def ask_ok(session_id, questions, timeout):
        return {"answers": [{"options": ["方案A"]}]}
    turns = [done(None, [tool_call("c1", "ask_user", questions=[
        {"question": "选哪个", "options": [{"label": "方案A", "recommended": True}, {"label": "方案B"}]}])]),
        done("好的按方案A")]
    sid, status, evs, sf = await run_once(test_engine, tmp_path, turns, ask=ask_ok)
    assert status == "idle"
    async with sf() as s:
        row = (await s.execute(sql(
            "SELECT tool_result FROM message WHERE session_id=:i AND role='tool'"), {"i": sid})).scalar()
    assert row["answers"] == [{"options": ["方案A"]}]

@pytest.mark.asyncio
async def test_ask_user_legacy_args_wrapped(test_engine, tmp_path):
    # 旧参数（question/options 为字符串）自动包装为单题，保证模型误用时对话不断
    async def ask_ok(session_id, questions, timeout):
        assert questions == [{"question": "选哪个", "options": [{"label": "A"}]}]
        return {"answers": [{"options": ["A"]}]}
    turns = [done(None, [tool_call("c1", "ask_user", question="选哪个", options=["A"])]), done("ok")]
    sid, status, evs, sf = await run_once(test_engine, tmp_path, turns, ask=ask_ok)
    assert status == "idle"
```

- `test_ask_user_cancelled`、`test_cancel` 的 ask 替身签名改为 `(session_id, questions, timeout)`。
- `test_ws.py`：`test_ws_ask_flow` 与 `test_run_task_marks_running` 中：
  - ScriptedLLM 的 ask_user arguments 改为 `json.dumps({"questions": [{"question": "选哪个", "options": [{"label": "A"}]}]}, ensure_ascii=False)`
  - 断言 `got["questions"][0]["question"] == "选哪个"`
  - 上行 `ws.send_json({"type": "ask_submit", "answers": [{"options": ["A"]}]})`
  - `test_ws_ask_flow` 中 result 断言改为 `ev["result"]["answers"] == [{"options": ["A"]}]`
- 追加多题 WS 流程测试：

```python
def test_ws_multi_question_submit(client):
    client.app.state.llm_factory = lambda cfg: ScriptedLLM([
        done(None, [{"id": "c1", "function": {
            "name": "ask_user", "arguments": json.dumps({"questions": [
                {"question": "用哪种库", "options": [{"label": "X", "recommended": True}, {"label": "Y"}],
                 "multi_select": False},
                {"question": "补充说明？"}]}, ensure_ascii=False)}}]),
        done("按回答继续"),
    ])
    with client.websocket_connect(f"/ws/chat/{client.zc_sid}") as ws:
        ws.send_json({"type": "user_message", "content": "提问"})
        for _ in range(6):
            ev = ws.receive_json()
            if ev["type"] == "ask_user":
                break
        assert len(ev["questions"]) == 2
        ws.send_json({"type": "ask_submit", "answers": [{"options": ["X"]}, {"answer": "无"}]})
        for _ in range(8):
            ev = ws.receive_json()
            if ev["type"] == "message_done":
                break
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:\project\zerocode\.venv\Scripts\python.exe -m pytest tests/test_agent_loop.py tests/test_ws.py -q`
Expected: FAIL（新协议未实现）

- [ ] **Step 3: 实现**

`app/agent/llm.py` ask_user schema 替换为：

```python
    {"type": "function", "function": {
        "name": "ask_user",
        "description": "当需求不明确或有多种实现方案时，向用户提问；可一次提多个问题，每题具体可决策",
        "parameters": {"type": "object", "properties": {
            "questions": {"type": "array", "items": {"type": "object", "properties": {
                "question": {"type": "string"},
                "options": {"type": "array", "items": {"type": "object", "properties": {
                    "label": {"type": "string"},
                    "description": {"type": "string", "description": "选项说明"},
                    "recommended": {"type": "boolean", "description": "推荐标注"}},
                    "required": ["label"]},
                    "description": "可选项（可为空，空则用户自由输入）"},
                "multi_select": {"type": "boolean", "description": "是否多选"}},
                "required": ["question"]}}}, "required": ["questions"]}}},
```

`app/hub.py`：`ask` 替换、`answer`/`choose` 删除、新增 `submit_answers`：

```python
    async def ask(self, session_id, questions, timeout):
        fut = asyncio.get_running_loop().create_future()
        self.ask_futures[session_id] = fut
        try:
            if self.emit:
                await self.emit(session_id, {"type": "ask_user", "questions": questions})
            return await fut
        finally:
            self.ask_futures.pop(session_id, None)

    async def submit_answers(self, session_id, answers):
        self._resolve(self.ask_futures, session_id, {"answers": answers})
```

`app/agent/loop.py` 模块级加归一化；`_dispatch` ask_user 分支替换：

```python
def _normalize_questions(args: dict) -> list[dict]:
    """新协议 questions 优先；兼容旧单题参数（question/options 字符串）。"""
    qs = args.get("questions")
    if isinstance(qs, list) and qs:
        return qs
    q = args.get("question")
    if isinstance(q, str) and q:
        opts = [{"label": o} if isinstance(o, str) else o for o in (args.get("options") or [])]
        return [{"question": q, "options": opts}]
    raise ToolError("缺少 question(s)")
```

```python
        if name == "ask_user":
            timeout = cfg.get("agent.ask_timeout_seconds", 0)
            try:
                questions = _normalize_questions(args)
                ans = await asyncio.wait_for(self.ask(session_id, questions, timeout),
                                             None if timeout in (None, 0) else float(timeout))
            except ToolError as e:
                return {"ok": False, "error": str(e)}
            except asyncio.TimeoutError:
                return {"cancelled": True, "reason": "提问超时"}
            if ans is None:
                return {"cancelled": True}
            return {"answers": ans.get("answers", [])}
```

`app/api/ws.py` 上行分支替换：

```python
            elif t == "ask_submit":
                await hub.submit_answers(session_id, msg.get("answers") or [])
```

（删除 `ask_answer`/`ask_choice` 两个分支。）

- [ ] **Step 4: 跑测试确认通过（全量）**

Run: `D:\project\zerocode\.venv\Scripts\python.exe -m pytest tests -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/agent/llm.py app/hub.py app/agent/loop.py app/api/ws.py tests/test_agent_loop.py tests/test_ws.py
git commit -m "feat: ask_user升级多问题协议（questions/选项说明/推荐/多选），上行ask_submit"
```

---

### Task 7: @文件指定（mentions 展开与一次注入）

**Files:**
- Create: `app/agent/mentions.py`
- Modify: `app/services/message_service.py`（context_content 参数）、`app/agent/loop.py`（_inner 展开落库、_history COALESCE）
- Test: `tests/test_mentions.py`（新建）、`tests/test_agent_loop.py`（追加）

**Interfaces:**
- Consumes: `app.agent.tools.resolve_in_workspace / BINARY_EXTS`
- Produces: `extract_mentions(text) -> list[str]`；`expand_mentions(root, text, max_bytes=20480) -> str|None`（无标记返回 None）；`add_message(..., context_content=None)`；`_history(session_id, window)` 内部改用 `COALESCE(context_content, content)`。Task 8 依赖 `_history` 再演进。

- [ ] **Step 1: 写失败测试**

`tests/test_mentions.py`：

```python
"""Task 7：@文件标记解析与展开（小文件内联 / 大文件·二进制·缺失占位 / 引号路径 / 去重）。"""
from pathlib import Path
from app.agent.mentions import extract_mentions, expand_mentions

def test_extract_basic_and_quoted():
    assert extract_mentions("看下 @a.txt 和 @\"my file.txt\" 再说") == ["a.txt", "my file.txt"]

def test_extract_dedup_and_email_safe():
    # 去重；邮箱 hi@bar.com 不算引用（@ 前是单词字符）
    assert extract_mentions("@a.txt @a.txt hi@bar.com") == ["a.txt"]
    assert extract_mentions("联系 hi@bar.com") == []

def test_expand_small_file_inline(tmp_path):
    (tmp_path / "a.txt").write_text("你好", encoding="utf-8")
    out = expand_mentions(Path(tmp_path), "看 @a.txt")
    assert '<file path="a.txt">\n你好\n</file>' in out
    # 无标记返回 None（不落 context_content）
    assert expand_mentions(Path(tmp_path), "没有引用") is None

def test_expand_big_file_placeholder(tmp_path):
    (tmp_path / "big.txt").write_text("x" * (21 * 1024), encoding="utf-8")
    out = expand_mentions(Path(tmp_path), "@big.txt")
    assert "[大文件 big.txt" in out and "read_file" in out

def test_expand_binary_and_missing(tmp_path):
    (tmp_path / "i.png").write_bytes(b"\x89PNG")
    out = expand_mentions(Path(tmp_path), "@i.png @nope.txt")
    assert "[二进制文件 i.png" in out
    assert "[文件不可用: nope.txt]" in out

def test_expand_out_of_workspace(tmp_path):
    out = expand_mentions(Path(tmp_path), "@/etc/passwd")
    assert "[文件不可用:" in out
```

`tests/test_agent_loop.py` 追加：

```python
@pytest.mark.asyncio
async def test_mention_expanded_into_context(test_engine, tmp_path):
    (tmp_path / "a.txt").write_text("文件内容XYZ", encoding="utf-8")
    llm = ScriptedLLM([[{"type": "content_delta", "content": "好"},
                        {"type": "done", "message": {"role": "assistant", "content": "好", "tool_calls": None}}]])
    sid = await mk_session(test_engine, tmp_path)
    sf = make_session_factory(test_engine)
    rec = Recorder()
    runner = AgentRunner(sf, llm, cfg_provider(), rec, lambda *a, **k: asyncio.sleep(0, None), lambda *a, **k: True)
    await runner.run(sid, "请看 @a.txt", asyncio.Event())
    # 模型收到的是展开后的 <file> 块
    assert '<file path="a.txt">' in llm.received[0][1]["content"]
    # 落库双份：content 原文、context_content 展开
    async with sf() as s:
        row = (await s.execute(sql(
            "SELECT content, context_content FROM message WHERE session_id=:i AND role='user'"),
            {"i": sid})).first()
    assert row[0] == "请看 @a.txt" and "文件内容XYZ" in row[1]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:\project\zerocode\.venv\Scripts\python.exe -m pytest tests/test_mentions.py tests/test_agent_loop.py -q`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现**

`app/agent/mentions.py`：

```python
"""@文件指定：解析消息中的 @路径 标记并展开为注入文本（spec §4.3）。

只注入一次：展开结果写入 message.context_content（旁路列），UI 与历史原文不受影响；
历史组装读 COALESCE(context_content, content)。文件缺失/越界/二进制/超限只注入占位说明，
不阻断发送。
"""
import re
from pathlib import Path
from app.agent.tools import BINARY_EXTS, resolve_in_workspace

MENTION_MAX_BYTES = 20 * 1024
MENTION_RE = re.compile(r'@"([^"\n]+)"|@([^\s@，。；、）】]+)')

def extract_mentions(text: str) -> list[str]:
    """提取唯一路径（保序）；@ 前是单词字符视为邮箱，跳过；尾部标点剔除。"""
    out, seen = [], set()
    for m in MENTION_RE.finditer(text or ""):
        start = m.start()
        if start > 0 and (re.match(r"[\w.]", text[start - 1] or "")):
            continue
        p = (m.group(1) or m.group(2)).strip().rstrip("，。；、）】:,.")
        if p and p not in seen:
            seen.add(p)
            out.append(p)
    return out

def expand_mentions(root: Path, text: str, max_bytes: int = MENTION_MAX_BYTES) -> str | None:
    """把 @标记 展开为注入文本；无任何标记返回 None。"""
    mentions = extract_mentions(text)
    if not mentions:
        return None
    parts = []
    for p in mentions:
        try:
            f = resolve_in_workspace(root, p)
            if not f.is_file():
                parts.append(f"[文件不可用: {p}]")
            elif f.suffix.lower() in BINARY_EXTS:
                parts.append(f"[二进制文件 {p}，无法内联]")
            elif f.stat().st_size > max_bytes:
                parts.append(f"[大文件 {p}（{f.stat().st_size // 1024} KB），请用 read_file 分段读取]")
            else:
                body = f.read_text(encoding="utf-8", errors="replace")
                parts.append(f'<file path="{p}">\n{body}\n</file>')
        except Exception:
            parts.append(f"[文件不可用: {p}]")
    return "\n\n".join(parts)
```

注意：expand 分支顺序——`resolve_in_workspace` 对 `/etc/passwd` 这类绝对路径抛 ToolError → except 落 `[文件不可用]`。

`app/services/message_service.py`：签名与 INSERT 加 `context_content=None`（与 Task 4 的 reasoning 同型）。

`app/agent/loop.py` `_inner` 开头两行替换为：

```python
        expanded = expand_mentions(root, user_text)
        await add_message(self.sf, session_id, "user", content=user_text, context_content=expanded)
```

（import 处加 `from app.agent.mentions import expand_mentions`。）`_history` 的 SELECT 改：

```python
                "SELECT role, COALESCE(context_content, content) AS content, tool_call_id, tool_name, tool_args, tool_result "
                "FROM message WHERE session_id=:i ORDER BY seq DESC LIMIT :w"
```

- [ ] **Step 4: 跑测试确认通过（全量）**

Run: `D:\project\zerocode\.venv\Scripts\python.exe -m pytest tests -q`
Expected: PASS（test_history_trim_keeps_tool_pairs 仍过——COALESCE 对 NULL 旁路列即原文）

- [ ] **Step 5: Commit**

```bash
git add app/agent/mentions.py app/services/message_service.py app/agent/loop.py tests/test_mentions.py tests/test_agent_loop.py
git commit -m "feat: @文件指定（≤20KB内联/大文件占位/越界防护，context_content一次注入）"
```

---

### Task 8: 上下文压缩（自动+手动）

**Files:**
- Create: `app/services/compaction_service.py`
- Modify: `app/agent/loop.py`（_history 加水位与摘要返回、新增 _build_messages、_inner 组装改造）、`app/api/ws.py`（compact 上行）
- Test: `tests/test_compaction.py`（新建）、`tests/test_agent_loop.py`（改 _history 直调 + 追加自动压缩）、`tests/test_ws.py`（追加手动压缩）

**Interfaces:**
- Consumes: Task 1 `compaction` 表；Task 4 的 add_message/llm
- Produces（模块 `app.services.compaction_service`）:
  - `latest(sf, session_id) -> (summary: str|None, watermark: int)`
  - `rows_after(sf, session_id, after_seq) -> list[dict]`（含 id/seq/role/content/tool_result）
  - `estimate_chars(rows) -> int`
  - `compact(sf, llm, session_id, send, keep_recent=8) -> dict|None`（写库 + 下发 `compaction_done`；失败只发 error 返回 None）
- loop：`_history(session_id, window, after_seq=0) -> (msgs, summary)`；`_build_messages(session_id, root, cfg, send)`；WS 上行 `{"type":"compact"}`（空闲执行，期间占 hub.running，先发 `compacting`）。

- [ ] **Step 1: 写失败测试**

`tests/test_compaction.py`：

```python
"""Task 8：压缩服务（水位/估算/摘要落库/失败不阻断）。"""
from sqlalchemy import text as sql
from app.db import make_session_factory
from app.agent.llm import ScriptedLLM
from app.services import compaction_service as comp
from tests.test_agent_loop import mk_session

class _Rec:
    def __init__(self): self.events = []
    async def __call__(self, ev): self.events.append(ev)

async def _seed_messages(sf, sid, n):
    from app.services.message_service import add_message
    for i in range(n):
        await add_message(sf, sid, "user" if i % 2 == 0 else "assistant", content=f"消息{i}内容")

class _Rec:
    def __init__(self): self.events = []
    async def __call__(self, ev): self.events.append(ev)

async def _seed_messages(sf, sid, n):
    from app.services.message_service import add_message
    for i in range(n):
        await add_message(sf, sid, "user" if i % 2 == 0 else "assistant", content=f"消息{i}内容")

async def test_latest_and_rows_after(test_engine, tmp_path):
    sf = make_session_factory(test_engine)
    sid = await mk_session(test_engine, tmp_path)
    assert await comp.latest(sf, sid) == (None, 0)
    await _seed_messages(sf, sid, 3)
    rows = await comp.rows_after(sf, sid, 0)
    assert [r["seq"] for r in rows] == [1, 2, 3]
    assert comp.estimate_chars(rows) > 0

async def test_compact_writes_and_emits(test_engine, tmp_path):
    sf = make_session_factory(test_engine)
    sid = await mk_session(test_engine, tmp_path)
    await _seed_messages(sf, sid, 12)
    rec = _Rec()
    llm = ScriptedLLM([[{"type": "content_delta", "content": "目标：测试"},
                        {"type": "done", "message": {"role": "assistant", "content": "目标：测试", "tool_calls": None}}]])
    res = await comp.compact(sf, llm, sid, rec, keep_recent=8)
    assert res["up_to_seq"] == 4  # 12 条保留 8 条 → 摘到 seq=4
    assert await comp.latest(sf, sid) == ("目标：测试", 4)
    assert any(e["type"] == "compaction_done" and e["summary"] == "目标：测试" for e in rec.events)

async def test_compact_skips_when_few(test_engine, tmp_path):
    sf = make_session_factory(test_engine)
    sid = await mk_session(test_engine, tmp_path)
    await _seed_messages(sf, sid, 5)
    llm = ScriptedLLM([])  # 不应被消费
    assert await comp.compact(sf, llm, sid, _Rec(), keep_recent=8) is None
    assert llm.turns == []

async def test_compact_failure_emits_error_only(test_engine, tmp_path):
    sf = make_session_factory(test_engine)
    sid = await mk_session(test_engine, tmp_path)
    await _seed_messages(sf, sid, 12)

    class Boom:
        async def chat_stream(self, messages, tools):
            raise RuntimeError("llm down")
            yield  # pragma: no cover
    rec = _Rec()
    assert await comp.compact(sf, Boom(), sid, rec, keep_recent=8) is None
    assert any(e["type"] == "error" and "压缩失败" in e["message"] for e in rec.events)
    async with sf() as s:
        n = (await s.execute(sql("SELECT count(*) FROM compaction"), {})).scalar()
    assert n == 0
```

`tests/test_agent_loop.py`：
- `test_history_trim_keeps_tool_pairs` 中两处直调改为三元组协议：

```python
    msgs, _ = await runner._history(sid, 2)
    ...
    full, _ = await runner._history(sid, 3)
```

- 追加自动压缩集成测试（摘要轮 + 正常回复轮；compact 消费 ScriptedLLM 第一轮，故局部变量持有 llm 以便断言 received）：

```python
@pytest.mark.asyncio
async def test_auto_compaction_triggers(test_engine, tmp_path):
    sid = await mk_session(test_engine, tmp_path)
    sf = make_session_factory(test_engine)
    for i in range(12):
        await add_message(sf, sid, "user" if i % 2 == 0 else "assistant", content=f"历史{i}的一些内容")
    llm = ScriptedLLM([
        [{"type": "done", "message": {"role": "assistant", "content": "摘要：历史已压缩", "tool_calls": None}}],
        [delta("收到"), {"type": "done", "message": {"role": "assistant", "content": "收到", "tool_calls": None}}]])
    rec = Recorder()

    async def _p():
        return {"agent.write_confirm": False, "agent.ask_timeout_seconds": 0,
                "agent.max_tool_rounds": 30, "agent.history_window": 40,
                "agent.auto_compact_chars": 10, "agent.compact_min_new": 1,
                "agent.compact_keep_recent": 8}
    runner = AgentRunner(sf, llm, _p, rec,
                         lambda *a, **k: asyncio.sleep(0, None), lambda *a, **k: True)
    status = await runner.run(sid, "继续", asyncio.Event())
    assert status == "idle"
    system_msg = llm.received[1][0]
    assert system_msg["role"] == "system" and "<context_summary>" in system_msg["content"]
    assert "摘要：历史已压缩" in system_msg["content"]
```

`tests/test_ws.py` 追加（开发库同步测试，沿用 `_db_status_dev` 的 asyncio.run 模式）：

```python
def test_ws_manual_compact(client):
    import asyncio as _aio
    from app.db import make_session_factory as _mk_sf
    from app.services.message_service import add_message as _add

    async def _seed():
        eng = make_engine(None)
        sf = _mk_sf(eng)
        try:
            for i in range(12):
                await _add(sf, client.zc_sid, "user" if i % 2 == 0 else "assistant", content=f"消息{i}")
        finally:
            await eng.dispose()
    _aio.run(_seed())
    client.app.state.llm_factory = lambda cfg: ScriptedLLM([
        [{"type": "done", "message": {"role": "assistant", "content": "压缩摘要OK", "tool_calls": None}}]])
    with client.websocket_connect(f"/ws/chat/{client.zc_sid}") as ws:
        ws.send_json({"type": "compact"})
        types = []
        for _ in range(4):
            ev = ws.receive_json()
            types.append(ev["type"])
            if ev["type"] == "compaction_done":
                assert ev["summary"] == "压缩摘要OK"
                break
        assert "compaction_done" in types
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:\project\zerocode\.venv\Scripts\python.exe -m pytest tests/test_compaction.py tests/test_agent_loop.py tests/test_ws.py -q`
Expected: FAIL

- [ ] **Step 3: 实现**

`app/services/compaction_service.py`：

```python
"""上下文压缩（spec §4.3）：水位读取、字符估算、LLM 摘要、落库与事件下发。

- 摘要覆盖 up_to_seq 之前的所有消息；保留最近 keep_recent 条原文；
- 摘要拼装在 loop._build_messages 中注入 system 尾部 <context_summary>；
- 失败只 send error 事件并返回 None，不抛出、不改会话状态、不阻断对话。
"""
import json
from sqlalchemy import text

SUMMARY_PROMPT = (
    "你是会话摘要器。把以下对话与工具结果压缩为继续工作所需的最小上下文，"
    "必须保留：任务目标、关键决策、涉及文件与改动、当前任务清单、遗留问题。"
    "直接输出摘要正文，不要客套。")

async def latest(sf, session_id: int) -> tuple[str | None, int]:
    async with sf() as s:
        row = (await s.execute(text(
            "SELECT summary, up_to_seq FROM compaction WHERE session_id=:i ORDER BY id DESC LIMIT 1"),
            {"i": session_id})).first()
    return (row[0], row[1]) if row else (None, 0)

async def rows_after(sf, session_id: int, after_seq: int) -> list[dict]:
    async with sf() as s:
        rows = (await s.execute(text(
            "SELECT id, seq, role, content, tool_result FROM message "
            "WHERE session_id=:i AND seq > :a ORDER BY seq"),
            {"i": session_id, "a": after_seq})).mappings().all()
    return [dict(r) for r in rows]

def estimate_chars(rows: list[dict]) -> int:
    n = 0
    for r in rows:
        n += len(r.get("content") or "")
        if r.get("tool_result") is not None:
            n += len(json.dumps(r["tool_result"], ensure_ascii=False))
    return n

async def compact(sf, llm, session_id: int, send, keep_recent: int = 8) -> dict | None:
    _, wm = await latest(sf, session_id)
    rows = await rows_after(sf, session_id, wm)
    if len(rows) <= keep_recent:
        return None
    head, tail = rows[:-keep_recent], rows[-keep_recent:]
    lines = []
    for r in head:
        body = r["content"]
        if r["role"] == "tool" and r["tool_result"] is not None:
            body = json.dumps(r["tool_result"], ensure_ascii=False)
        lines.append(f"[{r['seq']}][{r['role']}] {body}")
    digest = "\n".join(lines)[:120_000]
    parts = []
    try:
        async for ev in llm.chat_stream(
                [{"role": "system", "content": SUMMARY_PROMPT},
                 {"role": "user", "content": digest}], []):
            if ev["type"] == "content_delta":
                parts.append(ev["content"])
    except Exception as e:
        await send({"type": "error", "message": f"压缩失败，已保留原文: {e}"})
        return None
    summary = "".join(parts).strip()
    if not summary:
        await send({"type": "error", "message": "压缩失败：模型未返回摘要，已保留原文"})
        return None
    up_to = head[-1]["seq"]
    async with sf() as s:
        await s.execute(text(
            "INSERT INTO compaction (session_id, summary, up_to_seq) VALUES (:i, :s, :u)"),
            {"i": session_id, "s": summary, "u": up_to})
        await s.commit()
    out = {"summary": summary, "up_to_seq": up_to}
    await send({"type": "compaction_done", **out})
    return out
```

`app/agent/loop.py`：

```python
from app.services import compaction_service as _comp
```

`_history` 签名与查询改（返回三元组语义 → 实际返回 `(msgs, summary)`）：

```python
    async def _history(self, session_id: int, window: int, after_seq: int = 0):
        summary, wm = await _comp.latest(self.sf, session_id)
        async with self.sf() as s:
            rows = (await s.execute(text(
                "SELECT role, COALESCE(context_content, content) AS content, tool_call_id, tool_name, tool_args, tool_result "
                "FROM message WHERE session_id=:i AND seq > :w ORDER BY seq DESC LIMIT :win"),
                {"i": session_id, "w": after_seq, "win": window})).mappings().all()
        ...（修剪逻辑不变）...
        return msgs, summary
```

（注意原 `_history` 只查一次库；现在内部先读水位。`after_seq` 参数保留但以内部 wm 为准——简化：删除 `after_seq` 形参，直调处改 `runner._history(sid, 2)` 返回值解包即可。**裁定：_history 签名保持 `(session_id, window)`，返回值变为 `(msgs, summary)`**，测试直调处 `msgs, _ = ...`。）

新增 `_build_messages` 与 `_llm_for`；`_inner` 组装段替换：

```python
    async def _llm_for(self, cfg):
        llm = self.llm(cfg) if callable(self.llm) else self.llm
        if inspect.isawaitable(llm):
            llm = await llm
        return llm

    async def _build_messages(self, session_id: int, root: Path, cfg: dict, send) -> list[dict]:
        """自动压缩（阈值+最小新增双条件，thr=0 关闭）→ 摘要注入 system 尾部 → 水位后窗口消息。"""
        summary, wm = await _comp.latest(self.sf, session_id)
        rows = await _comp.rows_after(self.sf, session_id, wm)
        thr = int(cfg.get("agent.auto_compact_chars", 50000))
        min_new = int(cfg.get("agent.compact_min_new", 6))
        keep = int(cfg.get("agent.compact_keep_recent", 8))
        if thr > 0 and len(rows) >= min_new and len(rows) > keep and _comp.estimate_chars(rows) > thr:
            llm = await self._llm_for(cfg)
            res = await _comp.compact(self.sf, llm, session_id, send, keep)
            if res:
                summary = res["summary"]
        history, _ = await self._history(session_id, int(cfg.get("agent.history_window", 40)))
        system_text = build_system_prompt(str(root), datetime.date.today().isoformat())
        if summary:
            system_text += f"\n\n<context_summary>\n{summary}\n</context_summary>"
        return [{"role": "system", "content": system_text}] + history
```

`_inner` 中原来的 history/system 组装两行删除，改为：

```python
        messages = await self._build_messages(session_id, root, cfg, self.send)
```

轮内 `llm = self.llm(cfg)...` 三行改为 `llm = await self._llm_for(cfg)`。

`app/api/ws.py` 上行加（`cancel` 分支之后）：

```python
            elif t == "compact":
                if session_id in hub.running:
                    await emit(session_id, {"type": "error", "message": "会话正在执行中，无法压缩"})
                    continue

                async def send_local(event: dict):
                    if hub.emit:
                        await hub.emit(session_id, event)

                async def do_compact():
                    hub.running.add(session_id)
                    try:
                        await send_local({"type": "compacting"})
                        cfg = await config_getter()
                        llm = llm_factory(cfg)
                        keep = int(cfg.get("agent.compact_keep_recent", 8))
                        await compaction_service.compact(
                            app.state.session_factory, llm, session_id, send_local, keep)
                    finally:
                        hub.running.discard(session_id)

                asyncio.create_task(do_compact())
```

文件头 import 加 `from app.services import compaction_service`。

- [ ] **Step 4: 跑测试确认通过（全量）**

Run: `D:\project\zerocode\.venv\Scripts\python.exe -m pytest tests -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/compaction_service.py app/agent/loop.py app/api/ws.py tests/test_compaction.py tests/test_agent_loop.py tests/test_ws.py
git commit -m "feat: 上下文压缩（LLM摘要+水位/自动阈值触发/手动compact/失败不阻断）"
```

---

### Task 9: 会话级模型解析（回退链）

**Files:**
- Create: `app/agent/model_resolver.py`
- Modify: `app/api/ws.py:39-42,79`（llm_factory 使用 resolver）
- Test: `tests/test_model_resolver.py`（新建）

**Interfaces:**
- Consumes: Task 1/2 的 model_config 与 chat_session.model_config_id
- Produces: `await resolve_model(sf, session_id, cfg) -> {"base_url","api_key","model","source": "session"|"default"|"global"}`；回退链 会话→默认→全局 `llm.*`。ws.py 消费。

- [ ] **Step 1: 写失败测试**

```python
"""Task 9：会话级模型解析与回退链（session → default → global）。"""
from sqlalchemy import text as sql
from app.db import make_session_factory
from app.agent.model_resolver import resolve_model
from tests.test_agent_loop import mk_session

CFG = {"llm.base_url": "http://g/v1", "llm.api_key": "gk", "llm.model": "g-model"}

async def _mk_model(sf, **kw):
    async with sf() as s:
        mid = (await s.execute(sql(
            "INSERT INTO model_config (name, base_url, api_key, model, is_default) "
            "VALUES (:n, :b, :k, :m, :d) RETURNING id"),
            {"n": kw.get("name", "m"), "b": kw.get("base_url", "http://m/v1"),
             "k": kw.get("api_key", "mk"), "m": kw.get("model", "m-model"),
             "d": kw.get("is_default", False)})).scalar()
        await s.commit()
    return mid

async def _bind(sf, sid, mid):
    async with sf() as s:
        await s.execute(sql("UPDATE chat_session SET model_config_id=:m WHERE id=:i"), {"m": mid, "i": sid})
        await s.commit()

async def test_fallback_global(test_engine, tmp_path):
    sf = make_session_factory(test_engine)
    sid = await mk_session(test_engine, tmp_path)
    r = await resolve_model(sf, sid, CFG)
    assert r == {"base_url": "http://g/v1", "api_key": "gk", "model": "g-model", "source": "global"}

async def test_default_used_when_session_null(test_engine, tmp_path):
    sf = make_session_factory(test_engine)
    sid = await mk_session(test_engine, tmp_path)
    await _mk_model(sf, name="dft", is_default=True)
    r = await resolve_model(sf, sid, CFG)
    assert r["source"] == "default" and r["model"] == "m-model"

async def test_session_model_wins(test_engine, tmp_path):
    sf = make_session_factory(test_engine)
    sid = await mk_session(test_engine, tmp_path)
    await _mk_model(sf, name="dft", is_default=True)
    other = await _mk_model(sf, name="other", model="other-model")
    await _bind(sf, sid, other)
    r = await resolve_model(sf, sid, CFG)
    assert r["source"] == "session" and r["model"] == "other-model"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:\project\zerocode\.venv\Scripts\python.exe -m pytest tests/test_model_resolver.py -q`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现**

`app/agent/model_resolver.py`：

```python
"""会话级模型解析（spec §4.4）：session.model_config_id → model_config 行；
无绑定/行已删 → 默认模型；无默认 → 全局 app_config llm.*（老部署零迁移可用）。"""
from sqlalchemy import text

async def resolve_model(sf, session_id: int, cfg: dict) -> dict:
    async with sf() as s:
        mid = (await s.execute(text(
            "SELECT model_config_id FROM chat_session WHERE id=:i"), {"i": session_id})).scalar()
        if mid is not None:
            row = (await s.execute(text(
                "SELECT base_url, api_key, model FROM model_config WHERE id=:i"), {"i": mid})).first()
            if row:
                return {"base_url": row[0], "api_key": row[1] or "", "model": row[2], "source": "session"}
        row = (await s.execute(text(
            "SELECT base_url, api_key, model FROM model_config WHERE is_default ORDER BY id LIMIT 1"))).first()
    if row:
        return {"base_url": row[0], "api_key": row[1] or "", "model": row[2], "source": "default"}
    return {"base_url": cfg.get("llm.base_url"), "api_key": cfg.get("llm.api_key") or "",
            "model": cfg.get("llm.model"), "source": "global"}
```

`app/api/ws.py`：`llm_factory` 改签名（保留测试钩子优先级），`run_task` 内解析：

```python
    def llm_factory(cfg, model_cfg=None):
        if hasattr(app.state, "llm_factory"):
            return app.state.llm_factory(cfg)
        mc = model_cfg or {}
        return OpenAICompatLLM(
            mc.get("base_url") or cfg.get("llm.base_url"),
            mc.get("api_key") if mc.get("api_key") is not None else cfg.get("llm.api_key"),
            mc.get("model") or cfg.get("llm.model"))
```

```python
                        llm = llm_factory(await config_getter())
```

替换为：

```python
                        from app.agent.model_resolver import resolve_model
                        cfg_now = await config_getter()
                        model_cfg = await resolve_model(app.state.session_factory, session_id, cfg_now)
                        llm = llm_factory(cfg_now, model_cfg)
```

- [ ] **Step 4: 跑测试确认通过（全量）**

Run: `D:\project\zerocode\.venv\Scripts\python.exe -m pytest tests -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/agent/model_resolver.py app/api/ws.py tests/test_model_resolver.py
git commit -m "feat: 会话级模型解析（session→default→global回退链）"
```

---

### Task 10: 系统提示词增补

**Files:**
- Modify: `app/agent/prompts.py`
- Test: `tests/test_prompts.py`（新建）

**Interfaces:**
- Consumes: Task 5/7/8 的工具与机制名
- Produces: `build_system_prompt(root, date)` 文本追加规则 6-8（todo_write 约定、@文件说明、<context_summary> 说明）。

- [ ] **Step 1: 写失败测试**

```python
"""Task 10：系统提示词包含新能力约定。"""
from app.agent.prompts import build_system_prompt

def test_prompt_mentions_new_capabilities():
    p = build_system_prompt("/ws", "2026-09-17")
    assert "todo_write" in p and "in_progress" in p
    assert "<file" in p and "read_file" in p  # @文件内联与分段读取提示
    assert "<context_summary>" in p
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:\project\zerocode\.venv\Scripts\python.exe -m pytest tests/test_prompts.py -q`
Expected: FAIL

- [ ] **Step 3: 实现（prompts.py 追加规则行）**

```python
        "6. 多步任务先用 todo_write 建立任务清单（pending/in_progress/completed），随做随更、全量提交。\n"
        "7. 用户消息中的 @\"path\" 已内联为 <file> 块（大文件仅给出路径提示，用 read_file 分段读取）。\n"
        "8. 上下文可能被压缩：system 尾部 <context_summary> 是更早对话的摘要，据此衔接工作。")
```

- [ ] **Step 4: 跑测试确认通过（全量）**

Run: `D:\project\zerocode\.venv\Scripts\python.exe -m pytest tests -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/agent/prompts.py tests/test_prompts.py
git commit -m "docs: 系统提示词补todo/@文件/上下文摘要约定"
```

---

### Task 11: 前端 API 与 store 扩展（新事件/新 actions）

**Files:**
- Modify: `frontend/src/api/index.js`（尾部追加）、`frontend/src/stores/chat.js`
- Test: `frontend/src/stores/chat.test.js`、`frontend/src/utils/mentions.test.js`、`frontend/src/utils/tree.test.js`（均新建）

**Interfaces:**
- Consumes: Task 2/3 的 REST
- Produces:
  - api：`listModels/createModel/updateModel/deleteModel/getSessionDetail/patchSessionModel`
  - store state：`todoList/streamingThinking/models/currentModelId/compacting`
  - store actions：`submitAsk(answers)` / `requestCompact()` / `setModel(id|null)` / `loadModels()`
  - applyEvent 分支：`reasoning_delta`、`todo_sync`、`compacting`、`compaction_done`；`message_done` 附带 `reasoning` 并复位 `streamingThinking`
- 纯函数工具（Task 15 消费）：`src/utils/mentions.js`（`activeMentionToken(text, caret)`、`applyMention(text, token, path)`）；`src/utils/tree.js`（`flattenTree(nodes)`）

- [ ] **Step 1: 写失败测试**

`frontend/src/stores/chat.test.js`：

```javascript
// applyEvent 纯函数测试：传入普通对象即可（不依赖 pinia/WS）
import { describe, it, expect } from 'vitest'
import { applyEvent } from './chat'

function mk() {
  return { messages: [], streamingContent: '', streamingThinking: '', todoList: [], compacting: false, pendingAsk: null }
}

describe('applyEvent 新事件', () => {
  it('reasoning_delta 累积', () => {
    const s = mk()
    applyEvent(s, { type: 'reasoning_delta', content: '想' })
    applyEvent(s, { type: 'reasoning_delta', content: '清楚' })
    expect(s.streamingThinking).toBe('想清楚')
  })
  it('message_done 附带 reasoning 并复位', () => {
    const s = mk()
    s.streamingThinking = '想'
    applyEvent(s, { type: 'message_done', content: '答' })
    expect(s.messages[0]).toEqual({ role: 'assistant', content: '答', reasoning: '想' })
    expect(s.streamingThinking).toBe('')
  })
  it('todo_sync 覆盖清单', () => {
    const s = mk()
    const todos = [{ content: 'A', status: 'pending' }]
    applyEvent(s, { type: 'todo_sync', todos })
    expect(s.todoList).toEqual(todos)
  })
  it('compacting/compaction_done 状态与提示条', () => {
    const s = mk()
    applyEvent(s, { type: 'compacting' })
    expect(s.compacting).toBe(true)
    applyEvent(s, { type: 'compaction_done', summary: 'x', up_to_seq: 4 })
    expect(s.compacting).toBe(false)
    expect(s.messages[0].role).toBe('notice')
  })
  it('error/status 复位 thinking', () => {
    const s = mk()
    s.streamingThinking = 'x'
    applyEvent(s, { type: 'status', status: 'idle' })
    expect(s.streamingThinking).toBe('')
  })
})
```

`frontend/src/utils/mentions.test.js`：

```javascript
import { describe, it, expect } from 'vitest'
import { activeMentionToken, applyMention } from './mentions'

describe('activeMentionToken', () => {
  it('光标前是 @词 时返回 token', () => {
    expect(activeMentionToken('看 @app', 7)).toEqual({ query: 'app', start: 3, end: 7 })
  })
  it('引号形式', () => {
    expect(activeMentionToken('@"src/a.ts"', 10).query).toBe('src/a.ts')
  })
  it('邮箱不触发（@ 前是单词字符）', () => {
    expect(activeMentionToken('hi@bar', 6)).toBeNull()
  })
  it('无 @ 返回 null', () => {
    expect(activeMentionToken('hello', 5)).toBeNull()
  })
})

describe('applyMention', () => {
  it('替换 @token 为带引号路径+空格', () => {
    const t = activeMentionToken('看 @app', 7)
    expect(applyMention('看 @app', t, 'src/app.js')).toBe('看 @"src/app.js" ')
  })
})
```

`frontend/src/utils/tree.test.js`：

```javascript
import { describe, it, expect } from 'vitest'
import { flattenTree } from './tree'

it('展平文件节点（跳过目录本身，深入子级）', () => {
  const tree = [
    { name: 'src', type: 'dir', path: 'src', children: [
      { name: 'a.js', type: 'file', path: 'src/a.js' },
      { name: 'sub', type: 'dir', path: 'src/sub', children: [
        { name: 'b.ts', type: 'file', path: 'src/sub/b.ts' }] }] },
    { name: 'README.md', type: 'file', path: 'README.md' }]
  expect(flattenTree(tree)).toEqual([
    { path: 'src/a.js', name: 'a.js' },
    { path: 'src/sub/b.ts', name: 'b.ts' },
    { path: 'README.md', name: 'README.md' }])
})
```

- [ ] **Step 2: 跑测试确认失败**

Run（workdir `D:\project\zerocode\frontend`）: `npx vitest run`
Expected: FAIL（utils 模块不存在；applyEvent 无新分支）

- [ ] **Step 3: 实现**

`frontend/src/utils/mentions.js`：

```javascript
// @ 文件引用解析：提取光标前正在输入的 @词；选择后替换为带引号路径（spec §6）
export function activeMentionToken(text, caret) {
  const before = (text || '').slice(0, caret)
  const m = before.match(/@"?([^"\n]*)$/)
  if (!m) return null
  const start = caret - m[0].length
  if (start > 0 && /[\w.]/.test(before[start - 1])) return null // 邮箱等场景不触发
  return { query: m[1], start, end: caret }
}

export function applyMention(text, token, path) {
  return text.slice(0, token.start) + '@"' + path + '" ' + text.slice(token.end)
}
```

`frontend/src/utils/tree.js`：

```javascript
// 文件树 → 文件平铺列表（@补全数据源）
export function flattenTree(nodes, out = []) {
  for (const n of nodes || []) {
    if (n.type === 'file') out.push({ path: n.path, name: n.name })
    if (n.children) flattenTree(n.children, out)
  }
  return out
}
```

`frontend/src/api/index.js` 尾部追加：

```javascript
// ---- 模型库与会话级模型（交互增强） ----
export const listModels = () => http.get('/models')
export const createModel = (data) => http.post('/models', data)
export const updateModel = (id, data) => http.put(`/models/${id}`, data)
export const deleteModel = (id) => http.delete(`/models/${id}`)
export const getSessionDetail = (id) => http.get(`/sessions/${id}`)
export const patchSessionModel = (id, modelConfigId) =>
  http.patch(`/sessions/${id}/model`, { model_config_id: modelConfigId })
```

`frontend/src/stores/chat.js`：
- import 增加 `getSessionDetail, listModels, patchSessionModel`
- state 增：

```javascript
    todoList: [],
    streamingThinking: '',
    models: [],
    currentModelId: null,
    compacting: false,
```

- `applyEvent` 增/改分支：

```javascript
    case 'reasoning_delta':
      store.streamingThinking += ev.content
      break
    case 'todo_sync':
      store.todoList = ev.todos || []
      break
    case 'compacting':
      store.compacting = true
      break
    case 'compaction_done': // 压缩完成：复位 + 消息流提示条
      store.compacting = false
      store.messages.push({ role: 'notice', content: '已压缩上下文：更早对话已替换为摘要，最近几轮保留原文' })
      break
    case 'message_done': // 整段回复固化（附带思考留痕）
      store.messages.push({ role: 'assistant', content: ev.content, reasoning: store.streamingThinking || undefined })
      store.streamingContent = ''
      store.streamingThinking = ''
      break
```

- `error`/`status` 分支各加 `store.streamingThinking = ''`（status 分支另加 `store.compacting = false`）。
- `openSession` 中（竞态守卫后）加：

```javascript
      const detail = (await getSessionDetail(id)).data
      if (this.currentSessionId !== id) return
      this.todoList = detail.todo_state || []
      this.currentModelId = detail.model_config_id ?? null
      this.streamingThinking = ''
      this.compacting = false
```

- actions 增：

```javascript
    submitAsk(answers) {
      if (this._uplink({ type: 'ask_submit', answers })) this.pendingAsk = null
    },
    async requestCompact() {
      this._uplink({ type: 'compact' })
    },
    async loadModels() {
      this.models = (await listModels()).data
    },
    async setModel(id) {
      await patchSessionModel(this.currentSessionId, id)
      this.currentModelId = id
    },
```

- [ ] **Step 4: 跑测试确认通过**

Run（workdir frontend）: `npx vitest run`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/index.js frontend/src/stores/chat.js frontend/src/stores/chat.test.js frontend/src/utils/mentions.js frontend/src/utils/mentions.test.js frontend/src/utils/tree.js frontend/src/utils/tree.test.js
git commit -m "feat: 前端store新事件/todo与模型状态/ask_submit与compact上行/@补纯函数"
```

---

### Task 12: ThinkingBlock + 对话流集成（思考块/提示条）

**Files:**
- Create: `frontend/src/components/ThinkingBlock.vue`
- Modify: `frontend/src/components/ChatMessages.vue`、`frontend/src/views/ChatView.vue`

**Interfaces:**
- Consumes: Task 11 的 `streamingThinking` 与消息 `reasoning`/`notice`
- Produces: `<ThinkingBlock :thinking :streaming>`；ChatMessages assistant 分支渲染思考块；流式区顶部思考气泡；`notice` 渲染为居中灰字分隔。

- [ ] **Step 1: 实现 ThinkingBlock.vue**（无单测，Task 17 冒烟覆盖）

```vue
<script setup>
// 思考内容折叠块：流式形态展开+“思考中…”动画；历史形态默认折叠（点击回看）
import { ref, watch } from 'vue'

const props = defineProps({
  thinking: { type: String, default: '' },
  streaming: { type: Boolean, default: false },
})
const open = ref(props.streaming)
watch(() => props.streaming, (v) => { if (v) open.value = true })
</script>

<template>
  <div class="zc-think">
    <button class="zc-think-head" type="button" @click="open = !open">
      <span v-if="streaming" class="zc-think-live">思考中…</span>
      <span v-else>查看思考</span>
    </button>
    <pre v-show="open" class="zc-think-body">{{ thinking }}</pre>
  </div>
</template>

<style scoped>
.zc-think {
  width: 100%;
  max-width: 640px;
  margin-bottom: 8px;
  border: 1px dashed var(--zc-border);
  border-radius: 8px;
  overflow: hidden;
}
.zc-think-head {
  width: 100%;
  text-align: left;
  border: none;
  background: rgba(91, 122, 157, 0.06);
  color: var(--zc-text-sub);
  font-size: 12px;
  padding: 6px 10px;
  cursor: pointer;
}
.zc-think-live {
  animation: zc-think-blink 1.4s ease-in-out infinite;
}
.zc-think-body {
  margin: 0;
  max-height: 220px;
  overflow: auto;
  padding: 8px 10px;
  font-family: inherit;
  font-size: 12.5px;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--zc-text-sub);
  background: #fff;
}
@keyframes zc-think-blink {
  50% { opacity: 0.45; }
}
</style>
```

- [ ] **Step 2: ChatMessages.vue 集成**

script：import `ThinkingBlock`；props 增：

```javascript
  streamingThinking: { type: String, default: '' },
```

template：assistant 分支替换为（空内容但有思考的历史消息也渲染）：

```html
        <div v-else-if="m.role === 'assistant' && (m.content || m.reasoning)" class="zc-msg is-assistant">
          <div class="zc-assistant">
            <ThinkingBlock v-if="m.reasoning" :thinking="m.reasoning" />
            <MarkdownView v-if="m.content" class="zc-md" :content="m.content" />
          </div>
        </div>
        <div v-else-if="m.role === 'notice'" class="zc-msg is-notice">
          <span class="zc-notice-text">{{ m.content }}</span>
        </div>
```

流式区（`<div v-if="streaming" ...>` 之前）加：

```html
      <!-- 流式思考：reasoning_delta 累积期间展示 -->
      <div v-if="streamingThinking" class="zc-msg is-assistant">
        <ThinkingBlock :thinking="streamingThinking" streaming />
      </div>
```

style 追加：

```css
.zc-assistant { width: 100%; min-width: 0; }
.zc-msg.is-notice { justify-content: center; margin: 4px 0 12px; }
.zc-notice-text {
  font-size: 12px;
  color: var(--zc-text-sub);
  background: rgba(91, 122, 157, 0.06);
  border-radius: 999px;
  padding: 3px 12px;
}
```

- [ ] **Step 3: ChatView.vue 传参**

```html
      <ChatMessages
        :messages="store.messages"
        :streaming="store.streamingContent"
        :streaming-thinking="store.streamingThinking"
        :pending-ask="store.pendingAsk"
        :pending-confirm="store.pendingConfirm"
        :has-session="!!store.currentSessionId"
      />
```

- [ ] **Step 4: 验证**

Run（workdir frontend）: `npx vitest run && npm run build`
Expected: 全部 PASS、构建成功

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ThinkingBlock.vue frontend/src/components/ChatMessages.vue frontend/src/views/ChatView.vue
git commit -m "feat: 思考内容折叠块（流式动画/历史回看）与压缩提示条"
```

---

### Task 13: AskUserCard 多问题版

**Files:**
- Modify: `frontend/src/components/AskUserCard.vue`（整体替换）

**Interfaces:**
- Consumes: Task 6 `pendingAsk = {questions: [...]}`、Task 11 `submitAsk(answers)`
- Produces: 多题渲染（单选 a-radio-group / 多选 a-checkbox-group / 自由输入 a-textarea）、推荐徽标、选项说明次行、一次提交（未答完禁用）、取消执行。

- [ ] **Step 1: 实现（整体替换）**

```vue
<script setup>
// 提问卡（多问题版）：pendingAsk.questions 逐题渲染；options 题单选/多选+推荐徽标+说明，
// 无 options 题自由输入；submitAsk 一次提交全部答案，未答完禁用。上行失败卡片保留可重试。
import { computed, reactive } from 'vue'
import { useChatStore } from '../stores/chat'

const props = defineProps({ ask: { type: Object, required: true } })
const store = useChatStore()

const questions = computed(() => props.ask?.questions || [])
const drafts = reactive({}) // 未答题的索引 → 文本
const picks = reactive({})  // 有 options 题的索引 → 数组（单选长度1）

function selectionOf(i) {
  if (!picks[i]) picks[i] = []
  return picks[i]
}

function answerOf(q, i) {
  const sel = picks[i] || []
  if (sel.length) return q.multi_select ? { options: [...sel] } : { options: [sel[0]] }
  const t = (drafts[i] || '').trim()
  return t ? { answer: t } : null
}

const allAnswered = computed(() =>
  questions.value.every((q, i) => answerOf(q, i) !== null))

function submit() {
  if (!allAnswered.value) return
  store.submitAsk(questions.value.map((q, i) => answerOf(q, i)))
}
</script>

<template>
  <div class="zc-msg is-ask">
    <div class="zc-ask">
      <div v-for="(q, i) in questions" :key="i" class="zc-ask-item">
        <div class="zc-ask-question">{{ q.question }}</div>
        <template v-if="q.options && q.options.length">
          <div v-if="q.multi_select" class="zc-ask-options">
            <a-checkbox-group v-model:value="selectionOf(i)">
              <div v-for="opt in q.options" :key="opt.label" class="zc-opt">
                <a-checkbox :value="opt.label">
                  <span class="zc-opt-label">{{ opt.label }}</span>
                  <a-tag v-if="opt.recommended" color="blue" class="zc-opt-rec">推荐</a-tag>
                </a-checkbox>
                <div v-if="opt.description" class="zc-opt-desc">{{ opt.description }}</div>
              </div>
            </a-checkbox-group>
          </div>
          <a-radio-group v-else v-model:value="picks[i]" class="zc-ask-options">
            <div v-for="opt in q.options" :key="opt.label" class="zc-opt" @click="picks[i] = [opt.label]">
              <a-radio :value="opt.label">
                <span class="zc-opt-label">{{ opt.label }}</span>
                <a-tag v-if="opt.recommended" color="blue" class="zc-opt-rec">推荐</a-tag>
              </a-radio>
              <div v-if="opt.description" class="zc-opt-desc">{{ opt.description }}</div>
            </div>
          </a-radio-group>
        </template>
        <a-textarea
          v-else
          v-model:value="drafts[i]"
          :auto-size="{ minRows: 1, maxRows: 4 }"
          placeholder="或输入自定义回答…"
          class="zc-ask-free"
        />
      </div>
      <div class="zc-ask-foot">
        <a-button type="link" size="small" class="zc-ask-cancel" @click="store.cancel()">取消执行</a-button>
        <a-button type="primary" :disabled="!allAnswered" @click="submit">提交回答</a-button>
      </div>
    </div>
  </div>
</template>
```

样式沿用原 `.zc-ask*` 基础，追加：

```css
.zc-ask-item { margin-bottom: 14px; }
.zc-ask-item:last-of-type { margin-bottom: 0; }
.zc-opt { margin: 2px 0; }
.zc-opt-label { margin-right: 4px; }
.zc-opt-rec { transform: scale(0.85); }
.zc-opt-desc { font-size: 12px; color: var(--zc-text-sub); padding-left: 24px; }
.zc-ask-free { margin-top: 6px; }
.zc-ask-foot { display: flex; justify-content: flex-end; gap: 8px; margin-top: 8px; }
```

注意：单选题用 `picks[i]`（数组）承接 radio 的 string 值会类型不符——`a-radio-group` 的 `v-model:value` 直接绑 `picks[i]` 时把默认值设为 `undefined` 即可，`answerOf` 里 `sel.length` 对字符串为 undefined → 需兼容。**裁定实现**：picks[i] 初始 `undefined`；`answerOf` 改为：

```javascript
function answerOf(q, i) {
  const sel = picks[i]
  if (Array.isArray(sel) ? sel.length : sel) {
    return q.multi_select ? { options: [...sel] } : { options: [sel] }
  }
  const t = (drafts[i] || '').trim()
  return t ? { answer: t } : null
}
```

多选组仍用 `selectionOf(i)`。同时 `allAnswered` 逻辑不变。

- [ ] **Step 2: 验证**

Run（workdir frontend）: `npx vitest run && npm run build`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/AskUserCard.vue
git commit -m "feat: 提问卡多问题版（单选/多选/自由输入/推荐徽标/一次提交）"
```

---

### Task 14: 任务栏 TodoBar + 对话流 TodoCard

**Files:**
- Create: `frontend/src/components/TodoBar.vue`、`frontend/src/components/TodoCard.vue`
- Modify: `frontend/src/views/ChatView.vue`、`frontend/src/components/ChatMessages.vue`（tool 分支）

**Interfaces:**
- Consumes: Task 11 `todoList`；消息流中 `role:'tool', name:'todo_write', args.todos`
- Produces: `<TodoBar :todos>`（输入区上方可折叠）；`<TodoCard :todos>`（tool 消息位渲染）。

- [ ] **Step 1: 实现 TodoBar.vue**

```vue
<script setup>
// 顶部任务栏：todo_sync 驱动的实时清单，可折叠；纯展示（状态由 agent 维护）
import { computed, ref } from 'vue'
import { DownOutlined, RightOutlined } from '@ant-design/icons-vue'

const props = defineProps({ todos: { type: Array, default: () => [] } })
const open = ref(true)
const doneCount = computed(() => props.todos.filter((t) => t.status === 'completed').length)
const STATUS_TEXT = { pending: '待办', in_progress: '进行中', completed: '已完成' }
</script>

<template>
  <div class="zc-todo-bar">
    <button class="zc-todo-head" type="button" @click="open = !open">
      <component :is="open ? DownOutlined : RightOutlined" class="zc-todo-arrow" />
      <span>任务清单</span>
      <span class="zc-todo-count">{{ doneCount }}/{{ todos.length }} 已完成</span>
    </button>
    <ul v-show="open" class="zc-todo-list">
      <li v-for="(t, i) in todos" :key="i" :class="`is-${t.status}`">
        <span class="zc-todo-dot" />
        <span class="zc-todo-text">{{ t.content }}</span>
        <span class="zc-todo-status">{{ STATUS_TEXT[t.status] || t.status }}</span>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.zc-todo-bar {
  flex: 0 0 auto;
  margin: 0 28px;
  border: 1px solid var(--zc-border);
  border-radius: 8px;
  background: #fff;
  overflow: hidden;
}
.zc-todo-head {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 8px;
  border: none;
  background: rgba(91, 122, 157, 0.05);
  padding: 6px 12px;
  font-size: 12.5px;
  color: var(--zc-text);
  cursor: pointer;
}
.zc-todo-arrow { font-size: 10px; color: var(--zc-text-sub); }
.zc-todo-count { color: var(--zc-text-sub); }
.zc-todo-list { list-style: none; margin: 0; padding: 6px 12px 8px; }
.zc-todo-list li { display: flex; align-items: center; gap: 8px; padding: 3px 0; font-size: 13px; }
.zc-todo-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--zc-border); flex: 0 0 auto; }
.is-in_progress .zc-todo-dot { background: var(--zc-primary); }
.is-completed .zc-todo-dot { background: #7aa77a; }
.is-completed .zc-todo-text { color: var(--zc-text-sub); text-decoration: line-through; }
.zc-todo-status { margin-left: auto; font-size: 11px; color: var(--zc-text-sub); }
</style>
```

- [ ] **Step 2: 实现 TodoCard.vue**

```vue
<script setup>
// 对话流内任务卡：todo_write 调用处渲染当次清单快照
defineProps({ todos: { type: Array, default: () => [] } })
const STATUS_TEXT = { pending: '待办', in_progress: '进行中', completed: '已完成' }
</script>

<template>
  <div class="zc-todo-card">
    <div class="zc-todo-card-head">任务清单更新</div>
    <ul>
      <li v-for="(t, i) in todos" :key="i" :class="`is-${t.status}`">
        <span class="zc-dot" /><span>{{ t.content }}</span>
        <span class="zc-st">{{ STATUS_TEXT[t.status] || t.status }}</span>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.zc-todo-card {
  width: 100%;
  max-width: 640px;
  border: 1px solid var(--zc-border);
  border-radius: 8px;
  padding: 10px 12px;
  background: #fff;
}
.zc-todo-card-head { font-size: 12px; color: var(--zc-text-sub); margin-bottom: 6px; }
.zc-todo-card ul { list-style: none; margin: 0; padding: 0; }
.zc-todo-card li { display: flex; align-items: center; gap: 8px; padding: 2px 0; font-size: 13px; }
.zc-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--zc-border); }
.is-in_progress .zc-dot { background: var(--zc-primary); }
.is-completed .zc-dot { background: #7aa77a; }
.is-completed span:nth-child(2) { color: var(--zc-text-sub); text-decoration: line-through; }
.zc-st { margin-left: auto; font-size: 11px; color: var(--zc-text-sub); }
</style>
```

- [ ] **Step 3: 集成**

`ChatView.vue`：import `TodoBar`，插入输入区之前：

```html
      <TodoBar v-if="store.todoList.length" :todos="store.todoList" />
      <ChatInput :running="store.running" @send="store.send" @cancel="store.cancel" />
```

`ChatMessages.vue`：import `TodoCard`；tool 分支替换：

```html
        <div v-else-if="m.role === 'tool'" class="zc-msg is-tool">
          <TodoCard v-if="m.name === 'todo_write' && m.args && m.args.todos" :todos="m.args.todos" />
          <ToolCallBlock v-else :msg="m" />
        </div>
```

- [ ] **Step 4: 验证**

Run（workdir frontend）: `npx vitest run && npm run build`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/TodoBar.vue frontend/src/components/TodoCard.vue frontend/src/views/ChatView.vue frontend/src/components/ChatMessages.vue
git commit -m "feat: 任务清单前端（顶部任务栏+对话流任务卡）"
```

---

### Task 15: ChatInput（@补全 / 模型下拉 / 压缩按钮）

**Files:**
- Modify: `frontend/src/components/ChatInput.vue`

**Interfaces:**
- Consumes: Task 11（store.models/currentModelId/setModel/requestCompact/compacting、utils）、`api.index.js` 的 `tree(workspaceId)`
- Produces: 输入框 @ 弹出文件补全（选中插入 `@"path" `）；上方模型下拉（默认模型 + 模型库）；压缩按钮（running/compacting 禁用）。

- [ ] **Step 1: 实现（关键改动）**

script setup 整体替换为：

```javascript
// 输入区：工作区/模型元信息条 + a-textarea（Enter 发送）+ 操作行（压缩/发送|停止）。
// @ 触发文件补全（/api/fs/tree 展平过滤 top8，Enter/点击选中）；模型下拉按会话记忆。
import { computed, onMounted, ref } from 'vue'
import { FolderOutlined, ScissorOutlined, SendOutlined, StopOutlined } from '@ant-design/icons-vue'
import { useChatStore } from '../stores/chat'
import { tree } from '../api'
import { activeMentionToken, applyMention } from '../utils/mentions'
import { flattenTree } from '../utils/tree'

const props = defineProps({ running: { type: Boolean, default: false } })
const emit = defineEmits(['send', 'cancel'])

const chatStore = useChatStore()
const text = ref('')
const textareaRef = ref(null)

const wsName = computed(
  () => chatStore.workspaces.find((w) => w.id === chatStore.currentWorkspaceId)?.name || ''
)
const currentModelLabel = computed(() => {
  if (chatStore.currentModelId == null) return '默认模型'
  const m = chatStore.models.find((x) => x.id === chatStore.currentModelId)
  return m ? m.name : '默认模型'
})

// ---- @ 文件补全 ----
const files = ref([]) // [{path, name}]
const mention = ref(null) // {query, start, end} | null
const mentionIndex = ref(0)
const mentionItems = computed(() => {
  if (!mention.value) return []
  const q = mention.value.query.toLowerCase()
  return files.value.filter((f) => f.path.toLowerCase().includes(q)).slice(0, 8)
})

async function loadFiles() {
  if (!chatStore.currentWorkspaceId) return
  try {
    const { data } = await tree(chatStore.currentWorkspaceId)
    files.value = flattenTree(data)
  } catch (e) { /* 树加载失败不阻塞输入 */ }
}
onMounted(() => {
  loadFiles()
  if (!chatStore.models.length) chatStore.loadModels()
})

function onInput() {
  const el = textareaRef.value?.resizableTextArea?.textArea
  const caret = el ? el.selectionStart : text.value.length
  mention.value = activeMentionToken(text.value, caret)
  mentionIndex.value = 0
}

function pickMention(item) {
  const t = mention.value
  text.value = applyMention(text.value, t, item.path)
  mention.value = null
}

function onKeydown(e) {
  if (mention.value && mentionItems.value.length) {
    if (e.key === 'ArrowDown') { e.preventDefault(); mentionIndex.value = (mentionIndex.value + 1) % mentionItems.value.length; return }
    if (e.key === 'ArrowUp') { e.preventDefault(); mentionIndex.value = (mentionIndex.value - 1 + mentionItems.value.length) % mentionItems.value.length; return }
    if (e.key === 'Enter' || e.key === 'Tab') { e.preventDefault(); pickMention(mentionItems.value[mentionIndex.value]); return }
    if (e.key === 'Escape') { mention.value = null; return }
  }
  if (e.key !== 'Enter') return
  if (e.shiftKey) return
  if (e.isComposing || e.keyCode === 229) return
  e.preventDefault()
  doSend()
}

function doSend() {
  const content = text.value.trim()
  if (!content || props.running) return
  mention.value = null
  emit('send', content)
  text.value = ''
}

async function onModelChange(id) {
  try {
    await chatStore.setModel(id === 0 ? null : id) // 0 = 默认模型哨兵
  } catch (e) { /* 失败保持原值 */ }
}

function requestCompact() {
  chatStore.requestCompact()
}
```

template 关键段（元信息条与操作行）：

```html
    <div v-if="wsName || chatStore.models.length" class="zc-input-meta">
      <span v-if="wsName" class="zc-meta-item"><folder-outlined /> {{ wsName }}</span>
      <a-select
        v-if="chatStore.models.length"
        :value="chatStore.currentModelId ?? 0"
        size="small"
        class="zc-model-select"
        @change="onModelChange"
      >
        <a-select-option :value="0">默认模型</a-select-option>
        <a-select-option v-for="m in chatStore.models" :key="m.id" :value="m.id">
          {{ m.name }}（{{ m.model }}）
        </a-select-option>
      </a-select>
    </div>
```

textarea 上加 `ref="textareaRef"` 与 `@input="onInput"`；textarea 容器内补全下拉：

```html
    <div class="zc-input-wrap">
      <a-textarea ref="textareaRef" v-model:value="text" :auto-size="{ minRows: 2, maxRows: 6 }"
        placeholder="输入消息，@ 引用文件，Enter 发送，Shift+Enter 换行"
        @keydown="onKeydown" @input="onInput" />
      <div v-if="mention && mentionItems.length" class="zc-mention">
        <div v-for="(f, i) in mentionItems" :key="f.path" class="zc-mention-item"
          :class="{ 'is-active': i === mentionIndex }" @mousedown.prevent="pickMention(f)">
          {{ f.path }}
        </div>
      </div>
    </div>
```

操作行加压缩按钮：

```html
      <a-button size="small" :disabled="running || chatStore.compacting" :loading="chatStore.compacting"
        @click="requestCompact">
        <template #icon><scissor-outlined /></template>
        压缩上下文
      </a-button>
```

样式追加（wrap 定位 + 下拉 + 下拉宽度）：

```css
.zc-input-wrap { position: relative; }
.zc-mention {
  position: absolute; left: 0; right: 0; bottom: calc(100% + 4px);
  background: #fff; border: 1px solid var(--zc-border); border-radius: 8px;
  box-shadow: 0 4px 16px rgba(47, 58, 71, 0.12); overflow: hidden; z-index: 20;
}
.zc-mention-item { padding: 6px 12px; font-size: 12.5px; cursor: pointer; }
.zc-mention-item.is-active { background: rgba(91, 122, 157, 0.1); }
.zc-model-select { min-width: 180px; }
```

（`.zc-input-actions` 里的发送/停止逻辑保留不动；模型元信息条替代原 `模型：{{ model }}` 只读文案。）

- [ ] **Step 2: 验证**

Run（workdir frontend）: `npx vitest run && npm run build`
Expected: PASS（mentions/tree 纯函数已覆盖；组件由冒烟兜底）

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/ChatInput.vue
git commit -m "feat: 输入区@文件补全/会话级模型下拉/压缩上下文按钮"
```

---

### Task 16: 设置页「模型库」页签

**Files:**
- Modify: `frontend/src/views/SettingsView.vue`

**Interfaces:**
- Consumes: Task 2 的 `/api/models` + Task 11 的 `listModels/createModel/updateModel/deleteModel`
- Produces: a-tabs：`模型库`（表格+新增/编辑弹窗+删除确认+设默认）与 `基础配置`（现有三卡+保存栏迁入）。

- [ ] **Step 1: 实现（关键改动）**

script setup 追加：

```javascript
// ---- 模型库管理 ----
import { listModels, createModel, updateModel, deleteModel } from '../api'
import { createVNode } from 'vue'
import { Modal } from 'ant-design-vue'
import { ExclamationCircleOutlined } from '@ant-design/icons-vue'

const activeTab = ref('models')
const models = ref([])
const modelsLoading = ref(false)
const modelModal = ref(false)
const modelSaving = ref(false)
const editingId = ref(null)
const modelForm = ref({ name: '', base_url: '', api_key: '', model: '', is_default: false })

async function loadModels() {
  modelsLoading.value = true
  try {
    models.value = (await listModels()).data
  } finally {
    modelsLoading.value = false
  }
}

function openCreate() {
  editingId.value = null
  modelForm.value = { name: '', base_url: '', api_key: '', model: '', is_default: false }
  modelModal.value = true
}

function openEdit(record) {
  editingId.value = record.id
  modelForm.value = { ...record, api_key: record.api_key } // 掩码回显，留掩码=不改
  modelModal.value = true
}

async function saveModel() {
  modelSaving.value = true
  try {
    if (editingId.value == null) await createModel(modelForm.value)
    else await updateModel(editingId.value, modelForm.value)
    modelModal.value = false
    message.success('已保存')
    await loadModels()
  } catch (e) {
    message.error(e?.response?.data?.detail || '保存失败')
  } finally {
    modelSaving.value = false
  }
}

function removeModel(record) {
  Modal.confirm({
    title: '删除模型',
    icon: createVNode(ExclamationCircleOutlined),
    content: `确认删除「${record.name}」？引用它的会话将回退到默认模型。`,
    async onOk() {
      await deleteModel(record.id)
      message.success('已删除')
      await loadModels()
    },
  })
}

onMounted(loadModels) // 与既有 onMounted 合并时注意保留原加载逻辑
```

（若组件已有 `onMounted`，把 `loadModels()` 调用并入原回调，不要注册两个。）

template：最外层加页签，原三卡与保存栏整体移入 `基础配置` pane；新增 pane：

```html
  <a-tabs v-model:activeKey="activeTab" class="zc-tabs">
    <a-tab-pane key="models" tab="模型库">
      <a-button type="primary" class="zc-model-add" @click="openCreate">新增模型</a-button>
      <a-table :data-source="models" :loading="modelsLoading" row-key="id" :pagination="false" size="small">
        <a-table-column title="名称" data-index="name" />
        <a-table-column title="模型" data-index="model" />
        <a-table-column title="Base URL" data-index="base_url" />
        <a-table-column title="默认" data-index="is_default" width="80px">
          <template #default="{ record }">
            <a-tag v-if="record.is_default" color="blue">默认</a-tag>
          </template>
        </a-table-column>
        <a-table-column title="操作" width="130px">
          <template #default="{ record }">
            <a-button type="link" size="small" @click="openEdit(record)">编辑</a-button>
            <a-button type="link" size="small" danger @click="removeModel(record)">删除</a-button>
          </template>
        </a-table-column>
      </a-table>

      <a-modal v-model:open="modelModal" :title="editingId == null ? '新增模型' : '编辑模型'"
        :confirm-loading="modelSaving" @ok="saveModel">
        <a-form layout="vertical">
          <a-form-item label="名称"><a-input v-model:value="modelForm.name" placeholder="如 DeepSeek 官方" /></a-form-item>
          <a-form-item label="Base URL"><a-input v-model:value="modelForm.base_url" placeholder="https://api.deepseek.com/v1" /></a-form-item>
          <a-form-item label="模型名"><a-input v-model:value="modelForm.model" placeholder="deepseek-chat" /></a-form-item>
          <a-form-item label="API Key" extra="编辑时显示掩码 sk-***，保留表示不修改">
            <a-input-password v-model:value="modelForm.api_key" placeholder="sk-..." />
          </a-form-item>
          <a-form-item label="设为默认"><a-switch v-model:checked="modelForm.is_default" /></a-form-item>
        </a-form>
      </a-modal>
    </a-tab-pane>
    <a-tab-pane key="base" tab="基础配置">
      <!-- 原「模型接入 / 执行参数 / 环境状态」三卡 + 保存栏原样迁入 -->
    </a-tab-pane>
  </a-tabs>
```

- [ ] **Step 2: 验证**

Run（workdir frontend）: `npx vitest run && npm run build`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add frontend/src/views/SettingsView.vue
git commit -m "feat: 设置页模型库页签（CRUD/设默认/掩码回显/删除确认）"
```

---

### Task 17: 全量验证 + 构建 + browser-skill 冒烟 + 报告

**Files:**
- Modify: `docs/superpowers/plans/2026-09-17-zerocode-interaction.md`（勾选核对）
- 报告: 按用户惯例输出执行报告（任务/测试/留痕）

**Interfaces:**
- Consumes: Task 1-16 全部交付物
- Produces: 全量测试证据、生产构建产物、冒烟结论

- [ ] **Step 1: 后端全量测试**

Run: `D:\project\zerocode\.venv\Scripts\python.exe -m pytest tests -q`
Expected: 全 PASS（0 fail）

- [ ] **Step 2: 前端全量测试 + 生产构建**

Run（workdir frontend）: `npx vitest run` 然后 `npm run build`
Expected: 全 PASS；`frontend/dist` 生成

- [ ] **Step 3: 启动联调环境冒烟（browser-skill）**

启动：后台运行 `.venv\Scripts\python.exe -m uvicorn app.main:app --port 8100`（后端已托管 frontend/dist）。冒烟清单：
1. 设置页 → 模型库 → 新增模型（is_default）；
2. 建会话 → 输入 `@README.md 这个项目是做什么的？` → 观察 @ 下拉出现且选中插入；发送后模型上下文含文件内容（回复正确引用）；
3. 让 agent 建任务清单 → 顶部任务栏出现且随 todo_sync 更新；对话流出现任务卡；
4. 用支持 reasoning 的模型（如 deepseek-reasoner 配置为某模型条目）提问 → 思考块流式动画、完成后折叠可回看；
5. 让 agent 调 ask_user 多问题 → 多题卡片、推荐徽标、一次提交恢复执行；
6. 点「压缩上下文」→ 提示条出现；重开会话 → 任务栏/模型选择恢复。

- [ ] **Step 4: 提交收尾 + 报告**

```bash
git add -A
git commit -m "chore: 交互增强收尾（冒烟验证通过）"
```

输出执行报告：任务清单勾选状态、测试计数、冒烟截图结论、遗留问题。

---

## Self-Review 记录（已执行）

1. **Spec 覆盖**：§3 数据模型→T1；§4.1→T5/T6；§4.2→T4；§4.3@展开→T7、压缩→T8、todo→T5；§4.4→T6/T9；§5→T2/T3；§6 前端→T11-T16；§7 错误处理散布于 T4/T7/T8 实现与测试；§8→各任务测试+T17 冒烟；§9 配置→Global Constraints（代码默认值兜底，无需 Settings UI）✓
2. **占位符扫描**：无 TBD/TODO/「类似 Task N」；Task 8 的两版草稿已合并为唯一最终版 ✓
3. **类型一致性**：`_history(session_id, window) -> (msgs, summary)`（T8 裁定签名不变仅返回值变）；`compaction_service.{latest,rows_after,estimate_chars,compact}` 前后一致；`add_message(..., reasoning, context_content)` 两参数分属 T4/T7；`hub.ask(session_id, questions, timeout)` / `submit_answers(session_id, answers)` 一致；前端 `submitAsk(answers)`/`patchSessionModel(id, modelConfigId)` 与后端一致 ✓
