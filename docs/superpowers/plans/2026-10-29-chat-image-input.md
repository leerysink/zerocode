# 聊天图片输入与识别 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让聊天输入框支持 Ctrl+V / 拖拽 / 选择图片，经浏览器压缩后上传，并在支持视觉的模型请求中以 `image_url` content part 送模。

**Architecture:** 前端取图压缩后立即上传，WS 只传 `attachment_ids` 与 `client_msg_id`；附件独立落表、先上传后绑定，文件存平台数据区而非用户工作区；模型请求时读文件转 base64，只内联最近 2 条带图 user 消息；能力位由 `model_config.supports_vision` 与全局 `llm.supports_vision` 显式声明。

**Tech Stack:** FastAPI + SQLAlchemy async + asyncpg + PostgreSQL；Vue 3 + Pinia + Ant Design Vue 4.2.6 + Vite + Vitest/happy-dom。

**Spec:** `docs/specs/2026-10-29-chat-image-input.md`

## Global Constraints

- 后端 pytest **必须串行**：共享 `zerocode_test` 库，并发 TRUNCATE 会死锁；不要加 `-n` / xdist。
- 前端测试用 `cd frontend && npm run test -- --run`；构建用 `cd frontend && npm run build`。
- 不新增 Python 依赖（尤其不引入 `python-multipart`、Pillow）；不新增前端依赖。
- 迁移文件只新增 `changelog/V20261029_01_chat_image_attachment.sql`，不改历史迁移。
- 图片类型仅 PNG / JPEG / WebP / GIF，**拒绝 SVG**；单张 ≤5MB、单条 ≤4 张、单条合计 ≤10MB；客户端原文件 ≤20MB、压缩目标 1.5MB / 最长边 2048px。
- 附件存 `data_dir()/users/{uid}/chat_uploads/{session_id}/`，**绝不写入用户工作区**。
- `model_config.supports_vision` 与 `llm.supports_vision` 默认均为 false；必须显式开启。
- 无附件时保持既有纯文本链路：请求形态、事件、落库都不变。
- 文档不写行号锚点；提交信息用中文 conventional style，不带 AI 协作者署名。
- Windows 下使用 Git Bash；`pytest` 从仓库根执行，前端命令先 `cd frontend`。

---

## 文件地图

**后端新增**

- `changelog/V20261029_01_chat_image_attachment.sql`
- `app/services/attachment_service.py`
- `app/api/attachments.py`
- `tests/test_attachments_api.py`
- `tests/test_attachment_service.py`
- `tests/test_attachment_cleanup.py`
- `tests/test_agent_image_history.py`
- `tests/test_session_title.py`

**后端修改**

- `app/api/models.py`、`app/agent/model_resolver.py`
- `app/services/message_service.py`、`app/services/compaction_service.py`、`app/services/session_title_service.py`
- `app/api/sessions.py`、`app/api/ws.py`、`app/agent/loop.py`、`app/agent/prompts.py`、`app/hub.py`、`app/main.py`
- `tests/conftest.py`、`tests/test_migrations.py`、`tests/test_models_api.py`、`tests/test_model_resolver.py`、`tests/test_sessions.py`、`tests/test_compaction.py`、`tests/test_ws.py`

**前端新增**

- `frontend/src/utils/imageAttachments.js` + `.test.js`
- `frontend/src/composables/useImageAttachments.js` + `.test.js`
- `frontend/src/components/ChatInput.test.js`
- `frontend/src/components/ChatMessages.test.js`

**前端修改**

- `frontend/src/api/index.js`
- `frontend/src/stores/chat.js`、`frontend/src/stores/config.js`
- `frontend/src/components/ChatInput.vue`、`frontend/src/components/ChatMessages.vue`
- `frontend/src/views/SettingsView.vue`
- `frontend/src/utils/modelForm.js`
- `frontend/src/stores/chat.test.js`、`frontend/src/stores/config.test.js`、`frontend/src/utils/modelForm.test.js`

---

### Task 1: 迁移、测试库清理与迁移测试

**Files:**
- Create: `changelog/V20261029_01_chat_image_attachment.sql`
- Modify: `tests/conftest.py`
- Test: `tests/test_migrations.py`

**Interfaces:**
- Produces: `model_config.supports_vision BOOLEAN NOT NULL DEFAULT FALSE`
- Produces: `message_attachment` 表与三个索引
- Produces: `app_config` 平台行 `llm.supports_vision = false`

- [ ] **Step 1: 先写失败测试**

在 `tests/test_migrations.py` 末尾追加：

```python
# ---------- V20261029_01：聊天图片附件 ----------

async def test_chat_image_migration_schema(test_engine):
    async with test_engine.connect() as conn:
        cols = (await conn.execute(text(
            "SELECT column_name, data_type, is_nullable, column_default "
            "FROM information_schema.columns WHERE table_name='model_config'"))).all()
        t = (await conn.execute(text("SELECT to_regclass('message_attachment')"))).scalar()
        a_cols = (await conn.execute(text(
            "SELECT column_name, is_nullable FROM information_schema.columns "
            "WHERE table_name='message_attachment'"))).all()
        idx = (await conn.execute(text(
            "SELECT indexname, indexdef FROM pg_indexes "
            "WHERE tablename='message_attachment'"))).all()
    vision = {c[0]: c for c in cols}["supports_vision"]
    assert vision[1] == "boolean" and vision[2] == "NO"
    assert "false" in (vision[3] or "").lower()
    assert t is not None
    by_name = {c[0]: c[1] for c in a_cols}
    assert by_name["message_id"] == "YES"
    assert by_name["session_id"] == "NO"
    names = {r[0]: r[1] for r in idx}
    assert "idx_message_attachment_message" in names
    assert "idx_message_attachment_session" in names
    assert "idx_message_attachment_orphan" in names
    assert "WHERE (message_id IS NULL)" in names["idx_message_attachment_orphan"]


async def test_chat_image_migration_config_seed(test_engine):
    async with test_engine.connect() as conn:
        value = (await conn.execute(text(
            "SELECT value FROM app_config WHERE key='llm.supports_vision' "
            "AND owner_user_id IS NULL"))).scalar()
    assert value is False


async def test_chat_image_migration_replayable(test_engine):
    from app.db import split_statements
    from pathlib import Path
    stmts = split_statements(
        Path("changelog/V20261029_01_chat_image_attachment.sql").read_text(encoding="utf-8"))
    assert len(stmts) == 6  # 加列 + 建表 + 三索引 + 种子
    async with test_engine.begin() as conn:
        for st in stmts:
            await conn.execute(text(st))


async def test_chat_image_migration_reappliesThroughRunMigrations(test_engine):
    from app.db import run_migrations
    target = "V20261029_01_chat_image_attachment.sql"
    async with test_engine.begin() as conn:
        await conn.execute(text("DELETE FROM schema_migration WHERE filename=:f"), {"f": target})
    applied = await run_migrations(test_engine)
    assert applied == [target]
```

修改 `tests/conftest.py` 的 TRUNCATE 名单，把 `message_attachment` 显式列在 `message` 前：

```python
"TRUNCATE message_attachment, message, file_change, compaction, chat_session, workspace, "
"app_project, model_config, deploy_record, app_instance_link, release_application, app_release, actor "
"RESTART IDENTITY CASCADE"
```

在同一段 `INSERT INTO app_config ... VALUES` 的种子列表里追加：

```python
"('llm.supports_vision', 'false'), "
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_migrations.py -q`
Expected: FAIL，`changelog/V20261029_01_chat_image_attachment.sql` 不存在 / 列不存在。

- [ ] **Step 3: 写迁移**

创建 `changelog/V20261029_01_chat_image_attachment.sql`：

```sql
-- 1) 模型能力位：默认 false，显式 opt-in
ALTER TABLE model_config
  ADD COLUMN IF NOT EXISTS supports_vision BOOLEAN NOT NULL DEFAULT FALSE;

-- 2) 附件表：先上传（message_id NULL），发送时绑定；删会话/删消息都级联
CREATE TABLE IF NOT EXISTS message_attachment (
  id BIGSERIAL PRIMARY KEY,
  session_id BIGINT NOT NULL REFERENCES chat_session(id) ON DELETE CASCADE,
  message_id BIGINT REFERENCES message(id) ON DELETE CASCADE,
  filename VARCHAR(255) NOT NULL,
  mime VARCHAR(100) NOT NULL,
  size_bytes INTEGER NOT NULL,
  storage_path TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_message_attachment_message
  ON message_attachment (message_id);

CREATE INDEX IF NOT EXISTS idx_message_attachment_session
  ON message_attachment (session_id, message_id);

CREATE INDEX IF NOT EXISTS idx_message_attachment_orphan
  ON message_attachment (created_at) WHERE message_id IS NULL;

-- 3) 全局模型能力默认值（owner NULL = 平台默认，账号可覆盖）
INSERT INTO app_config (key, value) VALUES ('llm.supports_vision', 'false')
ON CONFLICT DO NOTHING;
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_migrations.py -q`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add changelog/V20261029_01_chat_image_attachment.sql tests/conftest.py tests/test_migrations.py
git commit -m "feat(chat): 附件表与模型视觉能力位迁移"
```

---

### Task 2: 模型能力位与 resolver 字段

**Files:**
- Modify: `app/api/models.py`
- Modify: `app/agent/model_resolver.py`
- Test: `tests/test_models_api.py`
- Test: `tests/test_model_resolver.py`

**Interfaces:**
- Produces: `GET/POST/PUT /api/models` 的模型对象含 `supports_vision: bool`
- Produces: `resolve_model*` 返回 `{id, name, base_url, api_key, model, supports_vision, source}`

- [ ] **Step 1: 写失败测试**

在 `tests/test_models_api.py` 末尾追加：

```python
# ---- supports_vision（聊天图片输入）----

async def test_supports_vision_create_defaults_false(client):
    assert (await _create(client))["supports_vision"] is False


async def test_supports_vision_create_and_list(client):
    mid = (await _create(client, supports_vision=True))["id"]
    rows = (await client.get("/api/models")).json()
    assert rows[0]["id"] == mid and rows[0]["supports_vision"] is True


async def test_supports_vision_put_null_keeps_value(client):
    mid = (await _create(client, supports_vision=True))["id"]
    body = {"name": "m1", "base_url": "http://x/v1", "api_key": "sk-***", "model": "deepseek-chat"}
    await client.put(f"/api/models/{mid}", json=body)
    assert (await client.get("/api/models")).json()[0]["supports_vision"] is True


async def test_supports_vision_put_false_turns_off(client):
    mid = (await _create(client, supports_vision=True))["id"]
    body = {"name": "m1", "base_url": "http://x/v1", "api_key": "sk-***",
            "model": "deepseek-chat", "supports_vision": False}
    await client.put(f"/api/models/{mid}", json=body)
    assert (await client.get("/api/models")).json()[0]["supports_vision"] is False


async def test_embedding_forces_supports_vision_false(client):
    assert (await _create(client, kind="embedding", supports_vision=True))["supports_vision"] is False
    mid = (await _create(client, supports_vision=True))["id"]
    body = {"name": "m1", "base_url": "http://x/v1", "api_key": "sk-***",
            "model": "deepseek-chat", "kind": "embedding", "supports_vision": True}
    await client.put(f"/api/models/{mid}", json=body)
    assert (await client.get("/api/models")).json()[0]["supports_vision"] is False
```

更新 `tests/test_model_resolver.py` 的严格等值断言，并新增能力位用例。`CFG` 改为：

```python
CFG = {"llm.base_url": "http://g/v1", "llm.api_key": "gk", "llm.model": "g-model",
       "llm.supports_vision": False}
```

`_mk_model` 增加 `supports_vision` 参数：

```python
async def _mk_model(sf, **kw):
    async with sf() as s:
        mid = (await s.execute(sql(
            "INSERT INTO model_config (name, base_url, api_key, model, kind, is_default, "
            "owner_user_id, supports_vision) "
            "VALUES (:n, :b, :k, :m, :kd, :d, :o, :v) RETURNING id"),
            {"n": kw.get("name", "m"), "b": kw.get("base_url", "http://m/v1"),
             "k": kw.get("api_key", "mk"), "m": kw.get("model", "m-model"),
             "kd": kw.get("kind", "chat"),
             "d": kw.get("is_default", False), "o": kw.get("owner_user_id"),
             "v": kw.get("supports_vision", False)})).scalar()
        await s.commit()
    return mid
```

三处严格等值改为新形状（id/name/supports_vision 参与）：

```python
assert r == {"id": None, "name": "g-model", "base_url": "http://g/v1", "api_key": "gk",
             "model": "g-model", "supports_vision": False, "source": "global"}

assert r == {"id": mid_holder, "name": "mine", "base_url": "http://m/v1",
             "api_key": "mk", "model": "mine-model", "supports_vision": False,
             "source": "default"}

assert r == {"id": None, "name": None, "base_url": None, "api_key": "",
             "model": None, "supports_vision": False, "source": "global"}
```

（第一处与第三处无需 mid；中间一处把 `_mk_model` 返回值存成 `mid_holder`。）

追加能力位用例：

```python
async def test_session_row_vision_wins_over_global(test_engine, tmp_path):
    sf = make_session_factory(test_engine)
    sid = await mk_session(test_engine, tmp_path)
    mid = await _mk_model(sf, name="vl", supports_vision=True)
    await _bind(sf, sid, mid)
    r = await resolve_model(sf, sid, {**CFG, "llm.supports_vision": True})
    assert r["source"] == "session" and r["supports_vision"] is True
    assert r["id"] == mid and r["name"] == "vl"


async def test_global_vision_used_without_rows(test_engine, tmp_path):
    sf = make_session_factory(test_engine)
    sid = await mk_session(test_engine, tmp_path)
    r = await resolve_model(sf, sid, {**CFG, "llm.supports_vision": True})
    assert r["source"] == "global" and r["supports_vision"] is True


async def test_default_row_vision_overrides_global_false(test_engine, tmp_path):
    sf = make_session_factory(test_engine)
    sid = await mk_session(test_engine, tmp_path)
    mid = await _mk_model(sf, name="dft", is_default=True, supports_vision=False)
    r = await resolve_model(sf, sid, {**CFG, "llm.supports_vision": True})
    assert r["source"] == "default" and r["id"] == mid
    assert r["supports_vision"] is False
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_models_api.py tests/test_model_resolver.py -q`
Expected: FAIL，`supports_vision` 缺失 / 字典形状不符。

- [ ] **Step 3: 实现**

`app/api/models.py`：

```python
class ModelIn(BaseModel):
    name: str
    base_url: str
    api_key: str = ""
    model: str
    is_default: bool = False
    kind: str | None = None
    # 缺省 None = PUT 不改；新建时按 False 处理。embedding 无论传什么都强制 False。
    supports_vision: bool | None = None
```

`_SELECT` 增加列：

```python
_SELECT = ("SELECT id, name, base_url, api_key, model, kind, is_default, "
           "supports_vision, created_at FROM model_config")
```

`create_model`：

```python
vision = False if kind == "embedding" else bool(body.supports_vision)
...
"INSERT INTO model_config (name, base_url, api_key, model, is_default, owner_user_id, "
"kind, supports_vision) VALUES (:n, :b, :k, :m, :d, :o, :kind, :v) RETURNING "
"id, name, base_url, api_key, model, kind, is_default, supports_vision, created_at"
...
"v": vision
```

`update_model`：

```python
old = (await s.execute(text(
    "SELECT api_key, kind, supports_vision FROM model_config "
    "WHERE id=:i AND owner_user_id=:u"), {"i": model_id, "u": uid})).first()
...
if kind == "embedding":
    vision = False
elif body.supports_vision is None:
    vision = bool(old[2])
else:
    vision = bool(body.supports_vision)
...
"UPDATE model_config SET name=:n, base_url=:b, api_key=:k, model=:m, "
"is_default=:d, kind=:kind, supports_vision=:v WHERE id=:i AND owner_user_id=:u"
```

`app/agent/model_resolver.py` 的 `resolve_model_for_user`：

```python
row = (await s.execute(text(
    "SELECT id, name, base_url, api_key, model, supports_vision FROM model_config "
    "WHERE is_default AND kind = 'chat' AND owner_user_id IS NOT DISTINCT FROM :o "
    "ORDER BY id LIMIT 1"), {"o": uid})).first()
if row:
    return {"id": row[0], "name": row[1], "base_url": row[2],
            "api_key": row[3] or "", "model": row[4],
            "supports_vision": bool(row[5]), "source": "default"}
return {"id": None, "name": cfg.get("llm.model"),
        "base_url": cfg.get("llm.base_url"), "api_key": cfg.get("llm.api_key") or "",
        "model": cfg.get("llm.model"),
        "supports_vision": bool(cfg.get("llm.supports_vision", False)),
        "source": "global"}
```

`resolve_model` 的会话行同理增加 `id, name, supports_vision`：

```python
row = (await s.execute(text(
    "SELECT id, name, base_url, api_key, model, supports_vision FROM model_config "
    "WHERE id=:i AND kind = 'chat' AND owner_user_id IS NOT DISTINCT FROM :o"),
    {"i": mid, "o": uid})).first()
if row:
    return {"id": row[0], "name": row[1], "base_url": row[2],
            "api_key": row[3] or "", "model": row[4],
            "supports_vision": bool(row[5]), "source": "session"}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_models_api.py tests/test_model_resolver.py -q`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add app/api/models.py app/agent/model_resolver.py tests/test_models_api.py tests/test_model_resolver.py
git commit -m "feat(models): 增加视觉能力位并随解析结果返回"
```

---

### Task 3: attachment_service 核心模块

**Files:**
- Create: `app/services/attachment_service.py`
- Test: `tests/test_attachment_service.py`

**Interfaces:**
- Produces: `AttachmentError(message, status_code=400)`
- Produces: `sniff_image_mime(head)`, `safe_filename(name, mime)`, `session_upload_dir(uid, session_id)`, `resolve_storage_path(storage_path)`, `format_attachment(row)`
- Produces: `create_attachment(sf, uid, session_id, filename, data, mime) -> dict`
- Produces: `staged_bytes(sf, session_id) -> int`
- Produces: `load_bindable(sf, session_id, ids) -> list[dict]`
- Produces: `for_ids(sf, session_id, ids) -> dict[int, dict]`
- Produces: `for_messages(sf, session_id, message_ids) -> dict[int, list[dict]]`
- Produces: `for_llm(sf, message_ids) -> dict[int, list[dict]]`（每项含 `data_url`，文件缺失时为 None）
- Produces: `bind_to_message(s, session_id, message_id, ids) -> list[dict]`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_attachment_service.py`：

```python
import pytest
from pathlib import Path
from sqlalchemy import text as sql
from app.db import make_session_factory
from app.services import attachment_service as att
from tests.test_agent_loop import mk_session

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
WEBP = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 64
GIF = b"GIF89a" + b"\x00" * 64


def test_sniff_image_mime_magic_bytes():
    assert att.sniff_image_mime(PNG) == "image/png"
    assert att.sniff_image_mime(JPEG) == "image/jpeg"
    assert att.sniff_image_mime(WEBP) == "image/webp"
    assert att.sniff_image_mime(GIF) == "image/gif"
    assert att.sniff_image_mime(b"<svg xmlns='http://www.w3.org/2000/svg'></svg>") is None
    assert att.sniff_image_mime(b"not an image") is None


def test_safe_filename_strips_path_and_controls():
    assert att.safe_filename("../x.png", "image/png") == "x.png"
    assert att.safe_filename("..\\x.png", "image/png") == "x.png"
    assert att.safe_filename("a\x00b\x1fc.png", "image/png") == "abc.png"
    assert att.safe_filename("", "image/png") == "image.png"
    assert att.safe_filename("..", "image/jpeg") == "image.jpg"
    assert len(att.safe_filename("x" * 300 + ".png", "image/png")) == 120


@pytest.mark.parametrize("raw,mime,ext", [
    (PNG, "image/png", ".png"), (JPEG, "image/jpeg", ".jpg"),
    (WEBP, "image/webp", ".webp"), (GIF, "image/gif", ".gif"),
])
async def test_create_attachment_writes_file_and_unbound_row(
        test_engine, tmp_path, monkeypatch, raw, mime, ext):
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path))
    sf = make_session_factory(test_engine)
    sid = await mk_session(test_engine, tmp_path)
    row = await att.create_attachment(sf, 7, sid, "截图 1" + ext, raw, mime)
    assert row["filename"] == "截图 1" + ext
    assert row["mime"] == mime and row["size_bytes"] == len(raw)
    assert row["url"] == f"/api/attachments/{row['id']}"
    files = list((tmp_path / "users" / "7" / "chat_uploads" / str(sid)).iterdir())
    assert len(files) == 1 and files[0].read_bytes() == raw
    async with sf() as s:
        bound = (await s.execute(sql(
            "SELECT message_id FROM message_attachment WHERE id=:i"), {"i": row["id"]})).scalar()
    assert bound is None


async def test_staged_bytes_and_bindable(test_engine, tmp_path, monkeypatch):
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path))
    sf = make_session_factory(test_engine)
    sid = await mk_session(test_engine, tmp_path)
    a = await att.create_attachment(sf, 7, sid, "a.png", PNG, "image/png")
    b = await att.create_attachment(sf, 7, sid, "b.png", PNG, "image/png")
    assert await att.staged_bytes(sf, sid) == len(PNG) * 2
    rows = await att.load_bindable(sf, sid, [a["id"], b["id"]])
    assert [r["id"] for r in rows] == [a["id"], b["id"]]
    assert (await att.for_ids(sf, sid, [a["id"]]))[a["id"]]["filename"] == "a.png"

    other_sid = await mk_session(test_engine, tmp_path / "other")
    with pytest.raises(att.AttachmentError):
        await att.load_bindable(sf, other_sid, [a["id"]])


async def test_load_bindable_rejects_bound_missing_and_oversize(
        test_engine, tmp_path, monkeypatch):
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(att, "MAX_MESSAGE_BYTES", 10)
    sf = make_session_factory(test_engine)
    sid = await mk_session(test_engine, tmp_path)
    a = await att.create_attachment(sf, 7, sid, "a.png", PNG, "image/png")
    b = await att.create_attachment(sf, 7, sid, "b.png", PNG, "image/png")

    async with sf() as s:
        mid = (await s.execute(sql(
            "INSERT INTO message (session_id, seq, role, content) "
            "VALUES (:s, 1, 'user', 'x') RETURNING id"), {"s": sid})).scalar()
        await s.commit()
        bound = await att.bind_to_message(s, sid, mid, [a["id"]])
        await s.commit()
    assert [r["id"] for r in bound] == [a["id"]]
    with pytest.raises(att.AttachmentError):
        await att.load_bindable(sf, sid, [a["id"]])

    # 文件缺失（按 storage_path 精确定位 b 的文件，别误删 a）
    async with sf() as s:
        b_path = (await s.execute(sql(
            "SELECT storage_path FROM message_attachment WHERE id=:i"),
            {"i": b["id"]})).scalar()
    att.resolve_storage_path(b_path).unlink()
    with pytest.raises(att.AttachmentError):
        await att.load_bindable(sf, sid, [b["id"]])

    # 合计超限（MAX_MESSAGE_BYTES 被压到 10，任一真实 PNG 都超）
    c = await att.create_attachment(sf, 7, sid, "c.png", PNG, "image/png")
    with pytest.raises(att.AttachmentError):
        await att.load_bindable(sf, sid, [c["id"]])


async def test_load_bindable_rejects_fifth_image(test_engine, tmp_path, monkeypatch):
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path))
    sf = make_session_factory(test_engine)
    sid = await mk_session(test_engine, tmp_path)
    ids = []
    for i in range(5):
        row = await att.create_attachment(sf, 7, sid, f"{i}.png", PNG, "image/png")
        ids.append(row["id"])
    with pytest.raises(att.AttachmentError) as e:
        await att.load_bindable(sf, sid, ids)
    assert e.value.status_code == 400


async def test_for_messages_and_for_llm(test_engine, tmp_path, monkeypatch):
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path))
    sf = make_session_factory(test_engine)
    sid = await mk_session(test_engine, tmp_path)
    a = await att.create_attachment(sf, 7, sid, "a.png", PNG, "image/png")
    b = await att.create_attachment(sf, 7, sid, "b.png", JPEG, "image/jpeg")
    async with sf() as s:
        mid = (await s.execute(sql(
            "INSERT INTO message (session_id, seq, role, content) "
            "VALUES (:s, 1, 'user', '看图') RETURNING id"), {"s": sid})).scalar()
        await s.commit()
        await att.bind_to_message(s, sid, mid, [a["id"], b["id"]])
        await s.commit()
    grouped = await att.for_messages(sf, sid, [mid])
    assert [x["id"] for x in grouped[mid]] == [a["id"], b["id"]]
    llm = await att.for_llm(sf, [mid])
    assert llm[mid][0]["data_url"].startswith("data:image/png;base64,")
    assert llm[mid][1]["data_url"].startswith("data:image/jpeg;base64,")

    # 文件缺失仍返回条目，data_url=None（供 loop 降级成占位）
    next((tmp_path / "users" / "7" / "chat_uploads" / str(sid)).glob("*.jpg")).unlink()
    llm2 = await att.for_llm(sf, [mid])
    assert llm2[mid][1]["data_url"] is None


async def test_resolve_storage_path_blocks_traversal(tmp_path, monkeypatch):
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path))
    assert att.resolve_storage_path("../etc/passwd") is None
    assert att.resolve_storage_path("users/1/a.png") == (tmp_path / "users/1/a.png").resolve()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_attachment_service.py -q`
Expected: FAIL，模块不存在。

- [ ] **Step 3: 实现模块**

创建 `app/services/attachment_service.py`：

```python
"""聊天图片附件：落盘、元数据、与消息绑定。

文件存在平台数据区而不是用户工作区——避免污染文件树、应用打包与 source_hash。
"""
import base64
import logging
import re
import shutil
import uuid
from pathlib import Path

from sqlalchemy import bindparam, text as _sql

from app.config import data_dir

logger = logging.getLogger(__name__)

ALLOWED_IMAGE_MIMES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}
MAX_ATTACHMENTS_PER_MESSAGE = 4
MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024
MAX_MESSAGE_BYTES = 10 * 1024 * 1024
MAX_INLINE_BYTES = 10 * 1024 * 1024
ORPHAN_TTL_HOURS = 24
IMAGE_CHAR_EQUIVALENT = 4000


class AttachmentError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def sniff_image_mime(head: bytes) -> str | None:
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    return None


def safe_filename(name: str | None, mime: str) -> str:
    base = re.split(r"[\\/]", (name or "").strip())[-1]
    base = re.sub(r"[\x00-\x1f\x7f]", "", base).strip()
    if base in ("", ".", ".."):
        base = "image" + ALLOWED_IMAGE_MIMES.get(mime, "")
    return base[:120]


def session_upload_dir(uid: int, session_id: int) -> Path:
    return Path(data_dir()) / "users" / str(uid) / "chat_uploads" / str(session_id)


def resolve_storage_path(storage_path: str) -> Path | None:
    root = Path(data_dir()).resolve()
    p = (root / storage_path).resolve()
    try:
        p.relative_to(root)
    except ValueError:
        return None
    return p


def format_attachment(row) -> dict:
    return {
        "id": row["id"],
        "filename": row["filename"],
        "mime": row["mime"],
        "size_bytes": row["size_bytes"],
        "url": f"/api/attachments/{row['id']}",
    }


async def create_attachment(sf, uid: int, session_id: int, filename: str,
                            data: bytes, mime: str) -> dict:
    ext = ALLOWED_IMAGE_MIMES.get(mime)
    if ext is None:
        raise AttachmentError("不支持的图片类型")
    if len(data) > MAX_ATTACHMENT_BYTES:
        raise AttachmentError("单张图片不能超过 5MB", 413)
    folder = session_upload_dir(uid, session_id)
    folder.mkdir(parents=True, exist_ok=True)
    disk_name = uuid.uuid4().hex + ext
    path = folder / disk_name
    path.write_bytes(data)
    rel = f"users/{uid}/chat_uploads/{session_id}/{disk_name}"
    try:
        async with sf() as s:
            row = (await s.execute(_sql(
                "INSERT INTO message_attachment "
                "(session_id, filename, mime, size_bytes, storage_path) "
                "VALUES (:sid, :f, :m, :n, :p) "
                "RETURNING id, filename, mime, size_bytes, storage_path"),
                {"sid": session_id, "f": filename, "m": mime,
                 "n": len(data), "p": rel})).mappings().first()
            await s.commit()
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return format_attachment(row)


async def staged_bytes(sf, session_id: int) -> int:
    async with sf() as s:
        return (await s.execute(_sql(
            "SELECT COALESCE(SUM(size_bytes), 0) FROM message_attachment "
            "WHERE session_id=:s AND message_id IS NULL"),
            {"s": session_id})).scalar() or 0


def _in_ids(ids: list[int]):
    return bindparam("ids", expanding=True)


async def load_bindable(sf, session_id: int, ids: list[int]) -> list[dict]:
    ids = list(dict.fromkeys(int(i) for i in (ids or [])))
    if not ids:
        return []
    if len(ids) > MAX_ATTACHMENTS_PER_MESSAGE:
        raise AttachmentError(f"单条消息最多 {MAX_ATTACHMENTS_PER_MESSAGE} 张图片")
    async with sf() as s:
        rows = (await s.execute(_sql(
            "SELECT id, filename, mime, size_bytes, storage_path FROM message_attachment "
            "WHERE session_id=:sid AND message_id IS NULL AND id IN :ids")
            .bindparams(_in_ids(ids)), {"sid": session_id, "ids": ids})).mappings().all()
    by_id = {r["id"]: dict(r) for r in rows}
    out, total = [], 0
    for i in ids:
        row = by_id.get(i)
        if row is None:
            raise AttachmentError("附件不存在或已被使用")
        if row["mime"] not in ALLOWED_IMAGE_MIMES:
            raise AttachmentError("不支持的图片类型")
        if row["size_bytes"] > MAX_ATTACHMENT_BYTES:
            raise AttachmentError("单张图片不能超过 5MB", 413)
        path = resolve_storage_path(row["storage_path"])
        if path is None or not path.is_file():
            raise AttachmentError("附件文件缺失，请重新上传")
        total += row["size_bytes"]
        out.append(row)
    if total > MAX_MESSAGE_BYTES:
        raise AttachmentError("单条消息图片合计不能超过 10MB", 413)
    return out


async def for_ids(sf, session_id: int, ids: list[int]) -> dict[int, dict]:
    ids = list(dict.fromkeys(int(i) for i in (ids or [])))
    if not ids:
        return {}
    async with sf() as s:
        rows = (await s.execute(_sql(
            "SELECT id, filename, mime, size_bytes FROM message_attachment "
            "WHERE session_id=:sid AND id IN :ids").bindparams(_in_ids(ids)),
            {"sid": session_id, "ids": ids})).mappings().all()
    return {r["id"]: format_attachment(r) for r in rows}


async def for_messages(sf, session_id: int, message_ids: list[int]) -> dict[int, list[dict]]:
    ids = [int(m) for m in message_ids if m is not None]
    if not ids:
        return {}
    async with sf() as s:
        rows = (await s.execute(_sql(
            "SELECT id, message_id, filename, mime, size_bytes FROM message_attachment "
            "WHERE session_id=:sid AND message_id IN :mids").bindparams(bindparam("mids", expanding=True)),
            {"sid": session_id, "mids": ids})).mappings().all()
    out: dict[int, list[dict]] = {}
    for r in rows:
        out.setdefault(r["message_id"], []).append(format_attachment(r))
    return out


async def for_llm(sf, message_ids: list[int]) -> dict[int, list[dict]]:
    ids = [int(m) for m in message_ids if m is not None]
    if not ids:
        return {}
    async with sf() as s:
        rows = (await s.execute(_sql(
            "SELECT id, message_id, filename, mime, size_bytes, storage_path "
            "FROM message_attachment WHERE message_id IN :mids")
            .bindparams(bindparam("mids", expanding=True)), {"mids": ids})).mappings().all()
    out: dict[int, list[dict]] = {}
    for r in rows:
        path = resolve_storage_path(r["storage_path"])
        data_url = None
        if path is not None and path.is_file():
            data = path.read_bytes()
            data_url = f"data:{r['mime']};base64,{base64.b64encode(data).decode()}"
        item = format_attachment(r)
        item["data_url"] = data_url
        out.setdefault(r["message_id"], []).append(item)
    return out


async def bind_to_message(s, session_id: int, message_id: int, ids: list[int]) -> list[dict]:
    ids = list(dict.fromkeys(int(i) for i in (ids or [])))
    if not ids:
        return []
    rows = (await s.execute(_sql(
        "UPDATE message_attachment SET message_id=:m "
        "WHERE id IN :ids AND session_id=:sid AND message_id IS NULL "
        "RETURNING id, filename, mime, size_bytes").bindparams(_in_ids(ids)),
        {"m": message_id, "ids": ids, "sid": session_id})).mappings().all()
    if len(rows) != len(ids):
        raise ValueError("附件不可用或已被使用")
    by_id = {r["id"]: format_attachment(r) for r in rows}
    return [by_id[i] for i in ids]


def remove_session_dir(uid: int, session_id: int) -> None:
    try:
        shutil.rmtree(session_upload_dir(uid, session_id))
    except FileNotFoundError:
        pass
    except OSError:
        logger.exception("删除会话附件目录失败 uid=%s session=%s", uid, session_id)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_attachment_service.py -q`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add app/services/attachment_service.py tests/test_attachment_service.py
git commit -m "feat(chat): 附件落盘与绑定服务"
```

---

### Task 4: 上传与读取 API

**Files:**
- Create: `app/api/attachments.py`
- Modify: `app/main.py`
- Test: `tests/test_attachments_api.py`

**Interfaces:**
- Produces: `POST /api/sessions/{session_id}/attachments?filename=...` → `201 {id, filename, mime, size_bytes, url}`
- Produces: `GET /api/attachments/{attachment_id}` → 图片字节

- [ ] **Step 1: 写失败测试**

创建 `tests/test_attachments_api.py`：

```python
import os
from pathlib import Path

from sqlalchemy import text as sql

from app.services import attachment_service as att

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
SVG = b"<svg xmlns='http://www.w3.org/2000/svg'></svg>"


async def _mk_session(client, tmp_path):
    ws = (await client.post("/api/workspaces", json={"root_path": str(tmp_path)})).json()
    return (await client.post("/api/sessions", json={"workspace_id": ws["id"]})).json()


async def _upload(client, sid, data=PNG, filename="a.png", mime="image/png"):
    return await client.post(
        f"/api/sessions/{sid}/attachments",
        params={"filename": filename}, content=data,
        headers={"Content-Type": mime})


async def test_upload_and_read(client, tmp_path, monkeypatch):
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path / "data"))
    sess = await _mk_session(client, tmp_path)
    r = await _upload(client, sess["id"], filename="截图 1.png")
    assert r.status_code == 201, r.text
    att_row = r.json()
    assert att_row["filename"] == "截图 1.png"
    assert att_row["mime"] == "image/png"
    assert att_row["size_bytes"] == len(PNG)
    assert att_row["url"] == f"/api/attachments/{att_row['id']}"

    got = await client.get(att_row["url"])
    assert got.status_code == 200 and got.content == PNG
    assert got.headers["content-type"] == "image/png"
    assert got.headers["x-content-type-options"] == "nosniff"
    assert "private" in got.headers["cache-control"]
    assert "immutable" in got.headers["cache-control"]
    assert "filename*=UTF-8''" in got.headers["content-disposition"]


async def test_upload_magic_wins_over_declared_mime(client, tmp_path, monkeypatch):
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path / "data"))
    sess = await _mk_session(client, tmp_path)
    r = await _upload(client, sess["id"], data=PNG, mime="image/jpeg")
    assert r.status_code == 201 and r.json()["mime"] == "image/png"


async def test_upload_rejects_svg_text_and_empty(client, tmp_path, monkeypatch):
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path / "data"))
    sess = await _mk_session(client, tmp_path)
    assert (await _upload(client, sess["id"], data=SVG, filename="x.svg", mime="image/svg+xml")).status_code == 400
    assert (await _upload(client, sess["id"], data=b"hello", filename="x.txt", mime="text/plain")).status_code == 400
    assert (await _upload(client, sess["id"], data=b"", filename="x.png")).status_code == 400


async def test_upload_rejects_non_image_content_type(client, tmp_path, monkeypatch):
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path / "data"))
    sess = await _mk_session(client, tmp_path)
    r = await _upload(client, sess["id"], data=PNG, mime="application/octet-stream")
    assert r.status_code == 400


async def test_upload_size_limits(client, tmp_path, monkeypatch):
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(att, "MAX_ATTACHMENT_BYTES", 10)
    monkeypatch.setattr(att, "MAX_MESSAGE_BYTES", 200)
    sess = await _mk_session(client, tmp_path)
    # 单文件超限（92 字节 > 10）
    assert (await _upload(client, sess["id"], data=PNG + b"x" * 20)).status_code == 413

    monkeypatch.setattr(att, "MAX_ATTACHMENT_BYTES", 1000)
    body = PNG + b"x" * 52  # 124 字节，单文件合法
    assert (await _upload(client, sess["id"], data=body)).status_code == 201
    # 累计 248 字节 > MAX_MESSAGE_BYTES=200
    assert (await _upload(client, sess["id"], data=body)).status_code == 413


async def test_filename_sanitized(client, tmp_path, monkeypatch):
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path / "data"))
    sess = await _mk_session(client, tmp_path)
    r = await _upload(client, sess["id"], filename="../evil.png")
    assert r.status_code == 201 and r.json()["filename"] == "evil.png"
    r = await _upload(client, sess["id"], filename="")
    assert r.json()["filename"] == "image.png"
    r = await _upload(client, sess["id"], filename="x" * 300 + ".png")
    assert len(r.json()["filename"]) == 120


async def test_isolation_and_404(two_auth_clients, tmp_path, monkeypatch):
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path / "data"))
    alice, bob = two_auth_clients
    sess = await _mk_session(alice, tmp_path)
    att_row = (await _upload(alice, sess["id"])).json()

    assert (await _upload(bob, sess["id"])).status_code == 404
    assert (await bob.get(att_row["url"])).status_code == 404
    assert (await alice.get("/api/attachments/999999")).status_code == 404


async def test_missing_file_returns_404(client, tmp_path, monkeypatch):
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path / "data"))
    sess = await _mk_session(client, tmp_path)
    att_row = (await _upload(client, sess["id"])).json()
    for p in Path(tmp_path / "data").rglob("*.png"):
        p.unlink()
    assert (await client.get(att_row["url"])).status_code == 404
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_attachments_api.py -q`
Expected: FAIL，404（路由不存在）。

- [ ] **Step 3: 实现 API 与注册**

创建 `app/api/attachments.py`：

```python
"""聊天附件上传/读取。

上传走原始字节（不引入 python-multipart）；类型以魔数为准，不信任客户端声明。
"""
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy import text

from app.services import attachment_service as att

router = APIRouter()


async def _owns_session(s, session_id: int, uid: int) -> bool:
    return (await s.execute(text(
        "SELECT 1 FROM chat_session cs JOIN workspace w ON w.id = cs.workspace_id "
        "WHERE cs.id=:i AND w.owner_user_id=:u"), {"i": session_id, "u": uid})).first() is not None


@router.post("/api/sessions/{session_id}/attachments", status_code=201)
async def upload_attachment(request: Request, session_id: int, filename: str = ""):
    uid = request.state.actor["id"]
    ctype = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    if not ctype.startswith("image/"):
        raise HTTPException(400, "仅支持图片文件")
    sf = request.app.state.session_factory
    async with sf() as s:
        if not await _owns_session(s, session_id, uid):
            raise HTTPException(404, "会话不存在")
        staged = await att.staged_bytes(sf, session_id)
    if staged >= att.MAX_MESSAGE_BYTES:
        raise HTTPException(413, "单条消息图片合计不能超过 10MB")

    chunks, total = [], 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > att.MAX_ATTACHMENT_BYTES:
            raise HTTPException(413, "单张图片不能超过 5MB")
        chunks.append(chunk)
    data = b"".join(chunks)
    if not data:
        raise HTTPException(400, "图片内容为空")
    mime = att.sniff_image_mime(data[:32])
    if mime is None:
        raise HTTPException(400, "不支持的文件类型（仅 PNG/JPEG/WebP/GIF）")
    if staged + len(data) > att.MAX_MESSAGE_BYTES:
        raise HTTPException(413, "单条消息图片合计不能超过 10MB")

    try:
        return await att.create_attachment(
            sf, uid, session_id, att.safe_filename(filename, mime), data, mime)
    except att.AttachmentError as e:
        raise HTTPException(e.status_code, str(e))
    except OSError as e:
        raise HTTPException(500, f"数据区不可写：{e}")


@router.get("/api/attachments/{attachment_id}")
async def get_attachment(request: Request, attachment_id: int):
    uid = request.state.actor["id"]
    sf = request.app.state.session_factory
    async with sf() as s:
        row = (await s.execute(text(
            "SELECT a.storage_path, a.mime, a.filename FROM message_attachment a "
            "JOIN chat_session cs ON cs.id = a.session_id "
            "JOIN workspace w ON w.id = cs.workspace_id "
            "WHERE a.id=:i AND w.owner_user_id=:u"),
            {"i": attachment_id, "u": uid})).mappings().first()
    if not row:
        raise HTTPException(404, "附件不存在")
    path = att.resolve_storage_path(row["storage_path"])
    if path is None or not path.is_file():
        raise HTTPException(404, "附件不存在")
    disposition = f"inline; filename*=UTF-8''{quote(row['filename'])}"
    return FileResponse(
        path, media_type=row["mime"],
        headers={
            "Content-Disposition": disposition,
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, max-age=31536000, immutable",
        })
```

在 `app/main.py` 的模型库 router 之后注册：

```python
from app.api import attachments as attachments_api
app.include_router(attachments_api.router)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_attachments_api.py -q`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add app/api/attachments.py app/main.py tests/test_attachments_api.py
git commit -m "feat(chat): 附件上传与鉴权读取接口"
```

---

### Task 5: 孤儿清理循环与删会话目录

**Files:**
- Modify: `app/services/attachment_service.py`
- Modify: `app/main.py`
- Test: `tests/test_attachment_cleanup.py`

**Interfaces:**
- Produces: `cleanup_orphans(sf, ttl_hours=24) -> int`
- Produces: `cleanup_loop(sf, interval_seconds=21600)`
- Consumes: `remove_session_dir(uid, session_id)`（Task 3）

- [ ] **Step 1: 写失败测试**

创建 `tests/test_attachment_cleanup.py`：

```python
import os
import time
from pathlib import Path

import pytest
from sqlalchemy import text as sql

from app.db import make_session_factory
from app.services import attachment_service as att
from tests.test_agent_loop import mk_session

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


async def _make_orphan(sf, sid, tmp_path, age_hours):
    row = await att.create_attachment(sf, 7, sid, "o.png", PNG, "image/png")
    async with sf() as s:
        await s.execute(sql(
            "UPDATE message_attachment SET created_at = now() - make_interval(hours => :h) "
            "WHERE id=:i"), {"h": age_hours, "i": row["id"]})
        await s.commit()
    return row


async def test_cleanup_removes_old_orphans_only(test_engine, tmp_path, monkeypatch):
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path))
    sf = make_session_factory(test_engine)
    sid = await mk_session(test_engine, tmp_path)
    old = await _make_orphan(sf, sid, tmp_path, 25)
    young = await _make_orphan(sf, sid, tmp_path, 1)
    kept = await att.create_attachment(sf, 7, sid, "k.png", PNG, "image/png")
    async with sf() as s:
        mid = (await s.execute(sql(
            "INSERT INTO message (session_id, seq, role, content) "
            "VALUES (:s, 1, 'user', 'x') RETURNING id"), {"s": sid})).scalar()
        await s.commit()
        await att.bind_to_message(s, sid, mid, [kept["id"]])
        await s.commit()

    removed = await att.cleanup_orphans(sf, ttl_hours=24)
    assert removed == 1
    async with sf() as s:
        ids = set((await s.execute(sql("SELECT id FROM message_attachment"))).scalars().all())
    assert ids == {young["id"], kept["id"]}
    assert len(list((tmp_path / "users" / "7" / "chat_uploads" / str(sid)).iterdir())) == 2


async def test_cleanup_removes_dead_session_dir_and_unreferenced_file(
        test_engine, tmp_path, monkeypatch):
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path))
    sf = make_session_factory(test_engine)
    dead = tmp_path / "users" / "7" / "chat_uploads" / "999999"
    dead.mkdir(parents=True)
    (dead / "x.png").write_bytes(PNG)
    old = time.time() - 25 * 3600
    os.utime(dead, (old, old))

    live_sid = await mk_session(test_engine, tmp_path)
    live = tmp_path / "users" / "7" / "chat_uploads" / str(live_sid)
    live.mkdir(parents=True)
    stray = live / "stray.png"
    stray.write_bytes(PNG)
    os.utime(stray, (old, old))

    await att.cleanup_orphans(sf, ttl_hours=24)
    assert not dead.exists()
    assert not stray.exists()


async def test_cleanup_never_raises(test_engine, tmp_path, monkeypatch):
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path))
    sf = make_session_factory(test_engine)
    sid = await mk_session(test_engine, tmp_path)
    await _make_orphan(sf, sid, tmp_path, 25)
    monkeypatch.setattr(att.shutil, "rmtree", lambda *a, **k: (_ for _ in ()).throw(OSError("x")))
    assert isinstance(await att.cleanup_orphans(sf, ttl_hours=24), int)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_attachment_cleanup.py -q`
Expected: FAIL，`cleanup_orphans` 不存在。

- [ ] **Step 3: 实现清理**

在 `app/services/attachment_service.py` 追加：

```python
import asyncio
import time


async def cleanup_orphans(sf, ttl_hours: int = ORPHAN_TTL_HOURS) -> int:
    removed = 0
    try:
        async with sf() as s:
            rows = (await s.execute(_sql(
                "DELETE FROM message_attachment WHERE message_id IS NULL "
                "AND created_at < now() - make_interval(hours => :h) "
                "RETURNING storage_path"), {"h": int(ttl_hours)})).mappings().all()
            await s.commit()
        for r in rows:
            path = resolve_storage_path(r["storage_path"])
            if path is not None:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    logger.exception("删除孤儿附件文件失败 %s", path)
            removed += 1
    except Exception:
        logger.exception("清理未绑定附件失败")

    try:
        root = Path(data_dir()).resolve() / "users"
        if not root.is_dir():
            return removed
        async with sf() as s:
            live_sessions = set((await s.execute(_sql("SELECT id FROM chat_session"))).scalars().all())
            referenced = set((await s.execute(_sql(
                "SELECT storage_path FROM message_attachment"))).scalars().all())
        cutoff = time.time() - int(ttl_hours) * 3600
        for user_dir in root.iterdir():
            uploads = user_dir / "chat_uploads"
            if not uploads.is_dir():
                continue
            for sess_dir in list(uploads.iterdir()):
                if not sess_dir.is_dir():
                    continue
                try:
                    sid = int(sess_dir.name)
                except ValueError:
                    sid = None
                if sid is not None and sid not in live_sessions and sess_dir.stat().st_mtime < cutoff:
                    try:
                        shutil.rmtree(sess_dir)
                        continue
                    except OSError:
                        logger.exception("删除已删会话的附件目录失败 %s", sess_dir)
                for f in sess_dir.iterdir():
                    if not f.is_file():
                        continue
                    rel = f.resolve().relative_to(root).as_posix()
                    rel = f"users/{rel}"
                    if rel in referenced:
                        continue
                    if f.stat().st_mtime >= cutoff:
                        continue
                    try:
                        f.unlink()
                    except OSError:
                        logger.exception("删除无主附件文件失败 %s", f)
    except Exception:
        logger.exception("扫描附件目录失败")
    return removed


async def cleanup_loop(sf, interval_seconds: int = 6 * 3600):
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            await cleanup_orphans(sf)
        except Exception:
            logger.exception("附件清理循环失败")
```

`app/main.py` lifespan 里，与 metrics 循环并列启动：

```python
from app.services import attachment_service
attachment_cleanup = asyncio.create_task(
    attachment_service.cleanup_loop(app.state.session_factory))
```

在 `yield` 之后、`engine.dispose()` 之前取消并等待：

```python
attachment_cleanup.cancel()
with suppress(asyncio.CancelledError):
    await attachment_cleanup
```

`delete_session`（`app/api/sessions.py`）在 commit 之后追加：

```python
import asyncio
...
await s.commit()
await asyncio.to_thread(attachment_service.remove_session_dir, uid, session_id)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_attachment_cleanup.py tests/test_attachments_api.py -q`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add app/services/attachment_service.py app/api/sessions.py app/main.py tests/test_attachment_cleanup.py
git commit -m "feat(chat): 未绑定附件 24 小时清理与删会话目录兜底"
```

---

### Task 6: 消息绑定、会话详情与消息附件回显

**Files:**
- Modify: `app/services/message_service.py`
- Modify: `app/api/sessions.py`
- Test: `tests/test_sessions.py`

**Interfaces:**
- Consumes: `bind_to_message`、`for_messages`、`for_ids`、`remove_session_dir`（Task 3）
- Consumes: `resolve_model` 新字段（Task 2）
- Produces: `add_message(..., attachment_ids=None)` 返回 `attachments`
- Produces: `GET /api/sessions/{id}` 的 `effective_model`
- Produces: `GET /api/sessions/{id}/messages` 每条消息的 `attachments`
- Produces: `PATCH /api/sessions/{id}/model` 的 `effective_model`

- [ ] **Step 1: 写失败测试**

在 `tests/test_sessions.py` 末尾追加：

```python
# ---------- 图片附件（消息绑定 / 会话详情 / 回显） ----------

async def _mk_ws_session(client, tmp_path):
    ws = (await client.post("/api/workspaces", json={"root_path": str(tmp_path)})).json()
    return ws, (await client.post("/api/sessions", json={"workspace_id": ws["id"]})).json()


async def test_session_detail_effective_model_sources(client, tmp_path):
    ws, sess = await _mk_ws_session(client, tmp_path)
    sid = sess["id"]
    d = (await client.get(f"/api/sessions/{sid}")).json()
    assert d["effective_model"] == {
        "id": None, "name": "deepseek-chat", "model": "deepseek-chat",
        "supports_vision": False, "source": "global"}

    dft = (await client.post("/api/models", json={
        "name": "dft", "base_url": "http://x/v1", "api_key": "k",
        "model": "dft-m", "is_default": True, "supports_vision": True})).json()
    d = (await client.get(f"/api/sessions/{sid}")).json()
    assert d["effective_model"] == {
        "id": dft["id"], "name": "dft", "model": "dft-m",
        "supports_vision": True, "source": "default"}

    bound = (await client.post("/api/models", json={
        "name": "bound", "base_url": "http://x/v1", "api_key": "k",
        "model": "bound-m", "supports_vision": False})).json()
    r = await client.patch(f"/api/sessions/{sid}/model", json={"model_config_id": bound["id"]})
    assert r.status_code == 200
    assert r.json()["effective_model"] == {
        "id": bound["id"], "name": "bound", "model": "bound-m",
        "supports_vision": False, "source": "session"}


async def test_messages_include_attachments(client, tmp_path, monkeypatch):
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path / "data"))
    from app.services.message_service import add_message
    ws, sess = await _mk_ws_session(client, tmp_path)
    sid = sess["id"]
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    up = await client.post(f"/api/sessions/{sid}/attachments",
                           params={"filename": "a.png"}, content=png,
                           headers={"Content-Type": "image/png"})
    att_row = up.json()
    sf = client.app.state.session_factory
    await add_message(sf, sid, "user", content="看图", attachment_ids=[att_row["id"]])
    msgs = (await client.get(f"/api/sessions/{sid}/messages")).json()
    assert msgs[0]["content"] == "看图"
    assert msgs[0]["attachments"] == [att_row]

    other = (await client.post("/api/sessions", json={"workspace_id": ws["id"]})).json()
    assert (await client.get(f"/api/sessions/{other['id']}/messages")).json() == []


async def test_add_message_rejects_unbindable_attachment(client, tmp_path, monkeypatch):
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path / "data"))
    import pytest
    from app.services.message_service import add_message
    ws, sess = await _mk_ws_session(client, tmp_path)
    sid = sess["id"]
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    up = await client.post(f"/api/sessions/{sid}/attachments",
                           params={"filename": "a.png"}, content=png,
                           headers={"Content-Type": "image/png"})
    att_id = up.json()["id"]
    sf = client.app.state.session_factory
    with pytest.raises(ValueError):
        await add_message(sf, sid, "user", content="x", attachment_ids=[att_id])
    # 消息也回滚了：绑定失败不产生半条消息
    assert (await client.get(f"/api/sessions/{sid}/messages")).json() == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_sessions.py -q`
Expected: FAIL，`effective_model` / `attachments` 缺失。

- [ ] **Step 3: 实现**

`app/services/message_service.py`：

```python
from sqlalchemy import text


async def add_message(session_factory, session_id: int, role: str, content=None,
                      context_content=None, reasoning=None, tool_call_id=None,
                      tool_name=None, tool_args=None, tool_result=None,
                      attachment_ids: list[int] | None = None) -> dict:
    import json
    from app.services import attachment_service
    async with session_factory() as s:
        row = (await s.execute(text(
            "INSERT INTO message (session_id, seq, role, content, context_content, reasoning, "
            "tool_call_id, tool_name, tool_args, tool_result) "
            "VALUES (:sid, COALESCE((SELECT max(seq)+1 FROM message WHERE session_id=:sid), 1), "
            ":role, :content, :context_content, :reasoning, :tcid, :tname, "
            "CAST(:targs AS JSONB), CAST(:tresult AS JSONB)) "
            "RETURNING id, seq, role, content, context_content, reasoning, tool_call_id, "
            "tool_name, tool_args, tool_result, created_at"),
            {"sid": session_id, "role": role, "content": content,
             "context_content": context_content, "reasoning": reasoning,
             "tcid": tool_call_id, "tname": tool_name,
             "targs": json.dumps(tool_args) if tool_args is not None else None,
             "tresult": json.dumps(tool_result) if tool_result is not None else None,
             })).mappings().first()
        attachments = []
        if attachment_ids:
            try:
                attachments = await attachment_service.bind_to_message(
                    s, session_id, row["id"], attachment_ids)
            except ValueError:
                await s.rollback()
                raise
        await s.execute(text("UPDATE chat_session SET updated_at = now() WHERE id = :i"),
                        {"i": session_id})
        await s.commit()
    out = dict(row)
    out["attachments"] = attachments
    return out
```

`app/api/sessions.py`：

```python
import asyncio
from app.agent.model_resolver import resolve_model
from app.services import attachment_service, config_service


async def _effective_model_of(app, session_id: int, cfg: dict) -> dict:
    m = await resolve_model(app.state.session_factory, session_id, cfg)
    return {k: m.get(k) for k in ("id", "name", "model", "supports_vision", "source")}


async def _pending_steering_of(app, session_id: int, raw: list) -> list[dict]:
    ids = [i for it in raw if isinstance(it, dict) for i in (it.get("attachment_ids") or [])]
    atts = await attachment_service.for_ids(app.state.session_factory, session_id, ids)
    out = []
    for it in raw:
        if isinstance(it, str):
            out.append({"content": it, "client_msg_id": None, "attachments": []})
            continue
        out.append({
            "content": it.get("content") or "",
            "client_msg_id": it.get("client_msg_id"),
            "attachments": [atts[i] for i in (it.get("attachment_ids") or []) if i in atts],
        })
    return out
```

`session_detail`：

```python
async with request.app.state.session_factory() as s:
    await _require_session(s, session_id, uid)
    row = (await s.execute(text(
        "SELECT id, workspace_id, title, status, todo_state, model_config_id "
        "FROM chat_session WHERE id=:i"), {"i": session_id})).mappings().first()
    cfg = await config_service.get_all(s, owner_user_id=uid)
if not row:
    raise HTTPException(404, "会话不存在")
out = dict(row)
out["effective_model"] = await _effective_model_of(request.app, session_id, cfg)
hub = getattr(request.app.state, "hub", None)
out["pending_steering"] = await _pending_steering_of(
    request.app, session_id, list(hub.pending.get(session_id, [])) if hub else [])
return out
```

`set_session_model` 的返回改为：

```python
async with request.app.state.session_factory() as s:
    cfg = await config_service.get_all(s, owner_user_id=uid)
return {"ok": True,
        "effective_model": await _effective_model_of(request.app, session_id, cfg)}
```

`get_messages`：

```python
async with request.app.state.session_factory() as s:
    await _require_session(s, session_id, uid)
    rows = (await s.execute(text(
        "SELECT id, seq, role, content, reasoning, tool_call_id, tool_name, "
        "tool_args, tool_result, created_at "
        "FROM message WHERE session_id=:i ORDER BY seq"), {"i": session_id})).mappings().all()
by_msg = await attachment_service.for_messages(
    request.app.state.session_factory, session_id, [r["id"] for r in rows])
return [{**dict(r), "attachments": by_msg.get(r["id"], [])} for r in rows]
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_sessions.py tests/test_attachment_service.py -q`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add app/services/message_service.py app/api/sessions.py tests/test_sessions.py
git commit -m "feat(chat): 消息落库绑定附件并回显会话有效模型"
```

---

### Task 7: 多模态历史、自动标题与系统提示

**Files:**
- Modify: `app/agent/loop.py`
- Modify: `app/services/session_title_service.py`
- Modify: `app/agent/prompts.py`
- Test: `tests/test_agent_image_history.py`
- Test: `tests/test_session_title.py`

**Interfaces:**
- Consumes: `add_message(..., attachment_ids)`、`for_messages`、`for_llm`（Task 3/6）
- Produces: `AgentRunner.run(..., attachment_ids=None)`
- Produces: `ensure_session_title(..., attachment_names=None)`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_session_title.py`：

```python
from app.db import make_session_factory
from app.services.session_title_service import DEFAULT_TITLE, ensure_session_title
from tests.test_agent_loop import mk_session


async def test_pure_image_first_message_titles_from_filename(test_engine, tmp_path):
    sf = make_session_factory(test_engine)
    sid = await mk_session(test_engine, tmp_path)
    assert await ensure_session_title(sf, sid, "", ["截图 2026.png"]) == "图片：截图 2026.png"


async def test_text_still_wins_over_attachment(test_engine, tmp_path):
    sf = make_session_factory(test_engine)
    sid = await mk_session(test_engine, tmp_path)
    assert await ensure_session_title(sf, sid, "看看这个", ["a.png"]) == "看看这个"


async def test_manual_title_never_overwritten(test_engine, tmp_path):
    sf = make_session_factory(test_engine)
    sid = await mk_session(test_engine, tmp_path)
    assert await ensure_session_title(sf, sid, "旧标题", ["a.png"]) == "旧标题"
    assert await ensure_session_title(sf, sid, "", ["b.png"]) is None
```

创建 `tests/test_agent_image_history.py`：

```python
import asyncio
import pytest
from sqlalchemy import text as sql

from app.agent.llm import ScriptedLLM
from app.agent.loop import AgentRunner
from app.db import make_session_factory
from app.services import attachment_service as att
from app.services.message_service import add_message
from tests.test_agent_loop import Recorder, cfg_provider, done, mk_session

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def _cfg():
    async def _p():
        return {"agent.write_confirm": False, "agent.ask_timeout_seconds": 0,
                "agent.max_tool_rounds": 30, "agent.history_window": 40}
    return _p()


async def _run(sf, sid, text_, ids=None):
    llm = ScriptedLLM([done("看到了")])
    rec = Recorder()
    runner = AgentRunner(sf, llm, _cfg(), rec,
                         lambda *a, **k: asyncio.sleep(0, None),
                         lambda *a, **k: True)
    await runner.run(sid, text_, asyncio.Event(), attachment_ids=ids)
    return llm.received[-1]


def _last_user(messages):
    return next(m for m in reversed(messages) if m["role"] == "user")


async def test_current_message_with_image_uses_parts(test_engine, tmp_path, monkeypatch):
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path))
    sf = make_session_factory(test_engine)
    sid = await mk_session(test_engine, tmp_path)
    a = await att.create_attachment(sf, 7, sid, "a.png", PNG, "image/png")
    msgs = await _run(sf, sid, "这是什么", [a["id"]])
    user = _last_user(msgs)
    assert isinstance(user["content"], list)
    assert user["content"][0] == {"type": "text", "text": "这是什么"}
    assert user["content"][1]["type"] == "image_url"
    assert user["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")


async def test_pure_image_message_has_image_placeholder_text(test_engine, tmp_path, monkeypatch):
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path))
    sf = make_session_factory(test_engine)
    sid = await mk_session(test_engine, tmp_path)
    a = await att.create_attachment(sf, 7, sid, "a.png", PNG, "image/png")
    msgs = await _run(sf, sid, "", [a["id"]])
    user = _last_user(msgs)
    assert user["content"][0] == {"type": "text", "text": "[图片]"}


async def test_only_last_two_user_messages_with_images_inline(
        test_engine, tmp_path, monkeypatch):
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path))
    sf = make_session_factory(test_engine)
    sid = await mk_session(test_engine, tmp_path)
    a = await att.create_attachment(sf, 7, sid, "old.png", PNG, "image/png")
    b = await att.create_attachment(sf, 7, sid, "mid.png", PNG, "image/png")
    c = await att.create_attachment(sf, 7, sid, "new.png", PNG, "image/png")
    await add_message(sf, sid, "user", content="第一", attachment_ids=[a["id"]])
    await add_message(sf, sid, "assistant", content="好")
    await add_message(sf, sid, "user", content="第二", attachment_ids=[b["id"]])
    await add_message(sf, sid, "assistant", content="好")
    await add_message(sf, sid, "user", content="第三", attachment_ids=[c["id"]])
    await add_message(sf, sid, "assistant", content="好")
    msgs = await _run(sf, sid, "第四")
    user_msgs = [m for m in msgs if m["role"] == "user"]
    assert isinstance(user_msgs[0]["content"], str)
    assert "较早的图片已省略" in user_msgs[0]["content"]
    # 第二、第三条带图消息内联
    assert isinstance(user_msgs[1]["content"], list)
    assert isinstance(user_msgs[2]["content"], list)


async def test_inline_budget_drops_oldest_history_keeps_current(
        test_engine, tmp_path, monkeypatch):
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(att, "MAX_INLINE_BYTES", 70)
    sf = make_session_factory(test_engine)
    sid = await mk_session(test_engine, tmp_path)
    big = PNG + b"x" * 50  # 每张 66 字节
    a = await att.create_attachment(sf, 7, sid, "a.png", big, "image/png")
    b = await att.create_attachment(sf, 7, sid, "b.png", big, "image/png")
    c = await att.create_attachment(sf, 7, sid, "c.png", big, "image/png")
    await add_message(sf, sid, "user", content="一", attachment_ids=[a["id"]])
    await add_message(sf, sid, "assistant", content="好")
    await add_message(sf, sid, "user", content="二", attachment_ids=[b["id"]])
    await add_message(sf, sid, "assistant", content="好")
    msgs = await _run(sf, sid, "三", [c["id"]])
    user_msgs = [m for m in msgs if m["role"] == "user"]
    assert isinstance(user_msgs[-1]["content"], list)  # 当前消息保留图片
    assert isinstance(user_msgs[-2]["content"], str)
    assert "较早的图片已省略" in user_msgs[-2]["content"]


async def test_missing_file_degrades_to_placeholder(test_engine, tmp_path, monkeypatch):
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path))
    sf = make_session_factory(test_engine)
    sid = await mk_session(test_engine, tmp_path)
    a = await att.create_attachment(sf, 7, sid, "a.png", PNG, "image/png")
    next((tmp_path / "users" / "7" / "chat_uploads" / str(sid)).glob("*.png")).unlink()
    msgs = await _run(sf, sid, "看图", [a["id"]])
    user = _last_user(msgs)
    assert user["content"][0]["text"].endswith("[图片缺失：a.png]")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_agent_image_history.py tests/test_session_title.py -q`
Expected: FAIL，`run()` 不接受 `attachment_ids` / 历史仍是字符串。

- [ ] **Step 3: 实现**

`session_title_service.py`：

```python
async def ensure_session_title(session_factory, session_id: int, user_text: str,
                               attachment_names: list[str] | None = None) -> str | None:
    title = derive_title(user_text)
    if not title and attachment_names:
        title = derive_title("图片：" + attachment_names[0])
    if not title:
        return None
    ...
```

`prompts.py` 规则串末尾追加：

```python
"10. 用户消息可能直接附带图片（content part 中的 image_url 就是原图）。"
"需要看图时直接依据消息里的图片回答，不要尝试用 read_file 去工作区找同名文件；"
"图片附件不占用工作区路径。")
```

`loop.py` 的 `run` / `_inner`：

```python
async def run(self, session_id: int, user_text: str, cancel: asyncio.Event,
              attachment_ids: list[int] | None = None) -> str:
    ...
    await self._inner(session_id, user_text, cancel, attachment_ids)
```

```python
async def _inner(self, session_id: int, user_text: str, cancel: asyncio.Event,
                 attachment_ids: list[int] | None = None):
    ...
    expanded = expand_mentions(root, user_text)
    row = await add_message(self.sf, session_id, "user", content=user_text,
                            context_content=expanded, attachment_ids=attachment_ids)
    titled = await ensure_session_title(
        self.sf, session_id, user_text,
        [a["filename"] for a in row.get("attachments") or []])
```

`_history` 整体替换为：

```python
async def _history(self, session_id: int, window: int) -> tuple[list[dict], str | None]:
    from app.services import attachment_service as _att
    summary, wm = await _comp.latest(self.sf, session_id)
    async with self.sf() as s:
        rows = (await s.execute(text(
            "SELECT id, role, COALESCE(context_content, content) AS content, "
            "tool_call_id, tool_name, tool_args, tool_result "
            "FROM message WHERE session_id=:i AND seq > :w ORDER BY seq DESC LIMIT :win"),
            {"i": session_id, "w": wm, "win": window})).mappings().all()
    rows = list(reversed(rows))
    user_ids = [r["id"] for r in rows if r["role"] == "user"]
    meta = await _att.for_messages(self.sf, session_id, user_ids)
    with_atts = [mid for mid in user_ids if meta.get(mid)]
    inline = set(with_atts[-2:])
    if inline:
        current_id = user_ids[-1] if user_ids else None
        total = sum(a["size_bytes"] for mid in inline for a in meta.get(mid, []))
        while total > _att.MAX_INLINE_BYTES:
            droppable = [mid for mid in with_atts if mid in inline and mid != current_id]
            if not droppable:
                break
            old = droppable[0]
            inline.discard(old)
            total -= sum(a["size_bytes"] for a in meta.get(old, []))
    files = await _att.for_llm(self.sf, sorted(inline)) if inline else {}
    msgs = []
    for r in rows:
        if r["role"] == "user":
            msgs.append({"role": "user", "content": self._user_content(
                r["content"], meta.get(r["id"], []), r["id"] in inline,
                files.get(r["id"], []))})
        elif r["role"] == "assistant":
            m = {"role": "assistant", "content": r["content"]}
            if r["tool_args"] and "tool_calls" in r["tool_args"]:
                m["tool_calls"] = r["tool_args"]["tool_calls"]
            msgs.append(m)
        elif r["role"] == "tool":
            msgs.append({"role": "tool", "tool_call_id": r["tool_call_id"],
                         "content": json.dumps(r["tool_result"], ensure_ascii=False)})
    while msgs:
        head = msgs[0]
        if head["role"] == "tool":
            if not any(m["role"] == "assistant" and any(
                    tc.get("id") == head["tool_call_id"] for tc in (m.get("tool_calls") or []))
                    for m in msgs):
                msgs.pop(0)
                continue
        elif head["role"] == "assistant" and head.get("tool_calls"):
            kept_tools = {m["tool_call_id"] for m in msgs if m["role"] == "tool"}
            if not all(tc.get("id") in kept_tools for tc in head["tool_calls"]):
                msgs.pop(0)
                continue
        break
    return msgs, summary

@staticmethod
def _user_content(text_val, meta_atts, inline: bool, file_atts) -> str | list:
    text_val = text_val or ""
    if not meta_atts:
        return text_val
    if not inline:
        names = [a["filename"] for a in meta_atts]
        note = f"[图片 ×{len(names)}：{', '.join(names)}（较早的图片已省略）]"
        return f"{text_val}\n\n{note}" if text_val else note
    by_id = {a["id"]: a for a in file_atts}
    usable = [by_id[a["id"]] for a in meta_atts if by_id.get(a["id"], {}).get("data_url")]
    missing = [a["filename"] for a in meta_atts if not by_id.get(a["id"], {}).get("data_url")]
    head = text_val if text_val.strip() else ("[图片]" if usable else "")
    if missing:
        head = (head + "\n" if head else "") + "[图片缺失：" + "，".join(missing) + "]"
    parts = [{"type": "text", "text": head or "[图片]"}]
    parts.extend({"type": "image_url", "image_url": {"url": a["data_url"]}} for a in usable)
    return parts
```

`_build_messages` 的记忆召回加纯图片短路：

```python
memories = ([] if not (user_text or "").strip()
            else await self._memories_of(session_id, user_text, cfg))
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_agent_image_history.py tests/test_session_title.py tests/test_agent_loop.py -q`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add app/agent/loop.py app/services/session_title_service.py app/agent/prompts.py tests/test_agent_image_history.py tests/test_session_title.py
git commit -m "feat(agent): 历史消息多模态渲染与纯图片标题"
```

---

### Task 8: 压缩图片计数与摘要占位

**Files:**
- Modify: `app/services/compaction_service.py`
- Test: `tests/test_compaction.py`

**Interfaces:**
- Produces: `rows_after` 每行含 `attachment_count` 与 `attachment_names`
- Produces: `estimate_chars` 按 `4000 × 图片数` 计

- [ ] **Step 1: 写失败测试**

在 `tests/test_compaction.py` 追加：

```python
async def test_estimate_counts_images_as_4000_chars(test_engine, tmp_path, monkeypatch):
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path))
    from app.services import attachment_service as att
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path))
    sf = make_session_factory(test_engine)
    sid = await mk_session(test_engine, tmp_path)
    a = await att.create_attachment(sf, 7, sid, "a.png", b"\x89PNG\r\n\x1a\n" + b"\0" * 8, "image/png")
    b = await att.create_attachment(sf, 7, sid, "b.png", b"\x89PNG\r\n\x1a\n" + b"\0" * 8, "image/png")
    from app.services.message_service import add_message
    await add_message(sf, sid, "user", content="看图", attachment_ids=[a["id"], b["id"]])
    rows = await comp.rows_after(sf, sid, 0)
    assert rows[0]["attachment_count"] == 2
    assert "a.png" in rows[0]["attachment_names"] and "b.png" in rows[0]["attachment_names"]
    assert comp.estimate_chars(rows) == len("看图") + 8000


async def test_compact_digest_contains_image_note_but_not_bytes(test_engine, tmp_path, monkeypatch):
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path))
    from app.services import attachment_service as att
    sf = make_session_factory(test_engine)
    sid = await mk_session(test_engine, tmp_path)
    a = await att.create_attachment(sf, 7, sid, "a.png", b"\x89PNG\r\n\x1a\n" + b"\0" * 8, "image/png")
    b = await att.create_attachment(sf, 7, sid, "b.png", b"\x89PNG\r\n\x1a\n" + b"\0" * 8, "image/png")
    from app.services.message_service import add_message
    await add_message(sf, sid, "user", content="看图", attachment_ids=[a["id"], b["id"]])
    for i in range(11):
        await add_message(sf, sid, "assistant", content=f"答{i}")
    llm = ScriptedLLM([[{"type": "content_delta", "content": "摘要"},
                        {"type": "done", "message": {"role": "assistant", "content": "摘要", "tool_calls": None}}]])
    rec = _Rec()
    assert await comp.compact(sf, llm, sid, rec, keep_recent=8)
    digest = llm.received[0][1]["content"]
    assert "[图片 ×2：a.png, b.png]" in digest
    assert "base64" not in digest
    assert "image_url" not in digest
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_compaction.py -q`
Expected: FAIL，行里没有 `attachment_count`。

- [ ] **Step 3: 实现**

`rows_after`：

```python
async def rows_after(sf, session_id: int, after_seq: int) -> list[dict]:
    async with sf() as s:
        rows = (await s.execute(text(
            "SELECT m.id, m.seq, m.role, COALESCE(m.context_content, m.content) AS content, "
            "m.tool_result, COALESCE(a.cnt, 0) AS attachment_count, "
            "COALESCE(a.names, '') AS attachment_names "
            "FROM message m LEFT JOIN ("
            "  SELECT message_id, count(*) AS cnt, string_agg(filename, ', ' ORDER BY id) AS names "
            "  FROM message_attachment WHERE message_id IS NOT NULL GROUP BY message_id"
            ") a ON a.message_id = m.id "
            "WHERE m.session_id=:i AND m.seq > :a ORDER BY m.seq"),
            {"i": session_id, "a": after_seq})).mappings().all()
    return [dict(r) for r in rows]
```

`estimate_chars`：

```python
from app.services.attachment_service import IMAGE_CHAR_EQUIVALENT
...
n += IMAGE_CHAR_EQUIVALENT * int(r.get("attachment_count") or 0)
```

`compact` 的 digest 行：

```python
body = r["content"]
if r["role"] == "tool" and r["tool_result"] is not None:
    body = json.dumps(r["tool_result"], ensure_ascii=False)
if r.get("attachment_count"):
    body = (body or "") + f"\n[图片 ×{r['attachment_count']}：{r['attachment_names']}]"
lines.append(f"[{r['seq']}][{r['role']}] {body}")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_compaction.py -q`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add app/services/compaction_service.py tests/test_compaction.py
git commit -m "feat(chat): 压缩按图片数量计字并在摘要保留占位"
```

---

### Task 9: WS 协议、插话对象队列与能力门

**Files:**
- Modify: `app/hub.py`
- Modify: `app/api/ws.py`
- Modify: `app/agent/loop.py`
- Modify: `app/api/sessions.py`（若 Task 6 已实现 `_pending_steering_of` 则无需再改）
- Test: `tests/test_ws.py`

**Interfaces:**
- Consumes: `load_bindable`、`resolve_model` 能力位、`add_message` 附件绑定、`_user_content`
- Produces: `hub.pending: dict[int, list[dict]]`，项为 `{content, attachment_ids, client_msg_id}`
- Produces: `busy` 带 `client_msg_id` 与 `running`
- Produces: `steering_done` 带 `client_msg_id`

- [ ] **Step 1: 先改测试再改实现**

`tests/test_ws.py` 已有用例按对象协议更新：

```python
def test_hub_queue_cap_and_drain():
    hub = ChatHub()
    a = {"content": "第一句", "attachment_ids": [], "client_msg_id": None}
    b = {"content": "第二句", "attachment_ids": [], "client_msg_id": None}
    c = {"content": "第三句", "attachment_ids": [], "client_msg_id": None}
    assert hub.queue(1, a, 2) is True
    assert hub.queue(1, b, 2) is True
    assert hub.queue(1, c, 2) is False
    assert asyncio.run(hub.pop_pending(1)) == [a, b]
    assert asyncio.run(hub.pop_pending(1)) == []
```

`test_session_detail_exposes_queued_steering`：

```python
hub.queue(sid, {"content": "还在排队的一句", "attachment_ids": [],
                "client_msg_id": "c1"}, 20)
hub.queue(sid, {"content": "排第二句", "attachment_ids": [],
                "client_msg_id": "c2"}, 20)
detail = client.get(f"/api/sessions/{sid}").json()
assert detail["pending_steering"] == [
    {"content": "还在排队的一句", "client_msg_id": "c1", "attachments": []},
    {"content": "排第二句", "client_msg_id": "c2", "attachments": []},
]
```

`test_ws_busy_is_non_terminal` 里的占位队列：

```python
hub.pending[sid] = [
    {"content": f"占位{i}", "attachment_ids": [], "client_msg_id": None}
    for i in range(20)]
```

`_SteerAfterStream` 的 `after`：

```python
after=lambda: hub.queue(
    client.zc_sid,
    {"content": steer, "attachment_ids": [], "client_msg_id": None}, 20))
```

在 `tests/test_ws.py` 末尾追加：

```python
# ---------- 聊天图片输入（WS 协议） ----------

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def _vision_model(client, vision=True):
    r = client.post("/api/models", json={
        "name": "vl", "base_url": "http://x/v1", "api_key": "k",
        "model": "vl-m", "is_default": True, "supports_vision": vision})
    assert r.status_code == 200, r.text
    return r.json()


def _upload(client, sid, name="a.png"):
    r = client.post(f"/api/sessions/{sid}/attachments", params={"filename": name},
                    content=PNG, headers={"Content-Type": "image/png"})
    assert r.status_code == 201, r.text
    return r.json()


def test_ws_pure_image_message_starts_run(client, monkeypatch, tmp_path):
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path / "data"))
    _vision_model(client)
    att_row = _upload(client, client.zc_sid)
    client.app.state.llm_factory = lambda cfg: ScriptedLLM([done("看到了")])
    with client.websocket_connect(f"/ws/chat/{client.zc_sid}") as ws:
        ws.send_json({"type": "user_message", "content": "",
                      "attachment_ids": [att_row["id"]], "client_msg_id": "c-img"})
        for _ in range(8):
            if ws.receive_json()["type"] == "status":
                break
    rows = client.get(f"/api/sessions/{client.zc_sid}/messages").json()
    assert [r["role"] for r in rows] == ["user", "assistant"]
    assert rows[0]["content"] == ""
    assert rows[0]["attachments"][0]["id"] == att_row["id"]


def test_ws_image_rejected_when_model_lacks_vision(client, monkeypatch, tmp_path):
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path / "data"))
    att_row = _upload(client, client.zc_sid)
    with client.websocket_connect(f"/ws/chat/{client.zc_sid}") as ws:
        ws.send_json({"type": "user_message", "content": "",
                      "attachment_ids": [att_row["id"]], "client_msg_id": "c-no"})
        ev = _reject_event(ws)
    assert ev["type"] == "busy" and ev["client_msg_id"] == "c-no"
    assert ev["running"] is False
    assert client.get(f"/api/sessions/{client.zc_sid}/messages").json() == []


def test_ws_image_steering_binds_and_echoes_client_msg_id(client, monkeypatch, tmp_path):
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path / "data"))
    _vision_model(client)
    att_row = _upload(client, client.zc_sid)
    client.app.state.llm_factory = lambda cfg: ScriptedLLM([
        done(None, [_ask_tool_call()]), done("收到图片")])
    with client.websocket_connect(f"/ws/chat/{client.zc_sid}") as ws:
        ws.send_json({"type": "user_message", "content": "先挂着"})
        for _ in range(6):
            if ws.receive_json()["type"] == "ask_user":
                break
        ws.send_json({"type": "user_message", "content": "",
                      "attachment_ids": [att_row["id"]], "client_msg_id": "c-steer"})
        ws.send_json({"type": "ask_submit", "answers": [{"answer": "ok"}]})
        evs = []
        for _ in range(12):
            ev = ws.receive_json()
            evs.append(ev)
            if ev["type"] == "status":
                break
    done_ev = [e for e in evs if e["type"] == "steering_done"]
    assert done_ev and done_ev[0]["client_msg_id"] == "c-steer"
    rows = client.get(f"/api/sessions/{client.zc_sid}/messages").json()
    users = [r for r in rows if r["role"] == "user"]
    assert len(users) == 2
    assert users[1]["content"] == ""
    assert users[1]["attachments"][0]["id"] == att_row["id"]


def test_ws_image_steering_rejected_when_run_model_lacks_vision(client, monkeypatch, tmp_path):
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path / "data"))
    att_row = _upload(client, client.zc_sid)
    client.app.state.llm_factory = lambda cfg: ScriptedLLM([done(None, [_ask_tool_call()])])
    with client.websocket_connect(f"/ws/chat/{client.zc_sid}") as ws:
        ws.send_json({"type": "user_message", "content": "先挂着"})
        for _ in range(6):
            if ws.receive_json()["type"] == "ask_user":
                break
        ws.send_json({"type": "user_message", "content": "",
                      "attachment_ids": [att_row["id"]], "client_msg_id": "c-no"})
        ev = _reject_event(ws)
        assert ev["type"] == "busy" and ev["client_msg_id"] == "c-no"
        assert ev["running"] is True
        ws.send_json({"type": "cancel"})
        for _ in range(5):
            if ws.receive_json()["type"] == "status":
                break
    assert not client.app.state.hub.pending.get(client.zc_sid)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_ws.py -q`
Expected: FAIL，对象队列与附件协议尚未实现。

- [ ] **Step 3: 实现 hub**

`app/hub.py`：

```python
self.pending: dict[int, list[dict]] = {}  # 执行中插话：{content, attachment_ids, client_msg_id}
self.run_vision: dict[int, bool] = {}     # 本 run 实际模型是否支持视觉（插话能力门）

def queue(self, session_id, item, max_pending) -> bool:
    q = self.pending.setdefault(session_id, [])
    if len(q) >= int(max_pending):
        return False
    q.append(item)
    return True

async def pop_pending(self, session_id) -> list[dict]:
    return self.pending.pop(session_id, None) or []
```

- [ ] **Step 4: 实现 ws**

`app/api/ws.py` 顶部：

```python
from app.agent.model_resolver import resolve_model
from app.services import attachment_service
```

在 `emit` 定义之后加：

```python
async def reject(message: str, client_msg_id=None, running: bool = False):
    await emit(session_id, {"type": "busy", "message": message,
                            "client_msg_id": client_msg_id, "running": running})
```

`user_message` 分支整体替换为：

```python
if t == "user_message":
    cid = msg.get("client_msg_id")
    text_ = (msg.get("content") or "").strip()
    raw_ids = msg.get("attachment_ids") or []
    if not isinstance(raw_ids, list):
        await reject("附件参数非法", cid, running=session_id in hub.running)
        continue
    try:
        attachment_ids = list(dict.fromkeys(int(i) for i in raw_ids))
    except (TypeError, ValueError):
        await reject("附件参数非法", cid, running=session_id in hub.running)
        continue
    if not text_ and not attachment_ids:
        continue
    if len(attachment_ids) > attachment_service.MAX_ATTACHMENTS_PER_MESSAGE:
        await reject(f"单条消息最多 {attachment_service.MAX_ATTACHMENTS_PER_MESSAGE} 张图片",
                     cid, running=session_id in hub.running)
        continue

    if session_id in hub.running:
        if session_id in hub.compacting:
            await reject("上下文压缩中，请稍候", cid, running=True)
            continue
        if attachment_ids:
            if hub.run_vision.get(session_id) is not True:
                await reject("当前模型不支持图片输入，请切换支持视觉的模型", cid, running=True)
                continue
            try:
                await attachment_service.load_bindable(
                    app.state.session_factory, session_id, attachment_ids)
            except attachment_service.AttachmentError as e:
                await reject(str(e), cid, running=True)
                continue
        cfg_now = await config_getter()
        cap = int(cfg_now.get("agent.steering_queue_max", 20) or 20)
        item = {"content": text_, "attachment_ids": attachment_ids,
                "client_msg_id": cid}
        if not hub.queue(session_id, item, cap):
            await reject(f"插话已排满（上限 {cap} 条），请等本轮跑完或点「停止」",
                         cid, running=True)
        continue

    cfg_now = None
    model_cfg = None
    if attachment_ids:
        try:
            await attachment_service.load_bindable(
                app.state.session_factory, session_id, attachment_ids)
        except attachment_service.AttachmentError as e:
            await reject(str(e), cid, running=False)
            continue
        cfg_now = await config_getter()
        model_cfg = await resolve_model(app.state.session_factory, session_id, cfg_now)
        if not model_cfg.get("supports_vision"):
            await reject("当前模型不支持图片输入，请切换支持视觉的模型", cid, running=False)
            continue

    async def send(event: dict):
        await hub.emit_event(session_id, event)

    async def run_task(user_text_: str, attachment_ids_: list[int],
                       cfg_now_, model_cfg_):
        cancel = asyncio.Event()
        hub.cancel_events[session_id] = cancel
        try:
            async with app.state.session_factory() as s:
                await s.execute(_sql(
                    "UPDATE chat_session SET status='running', updated_at=now() WHERE id=:i"),
                    {"i": session_id})
                await s.commit()
            if cfg_now_ is None:
                cfg_now_ = await config_getter()
            if model_cfg_ is None:
                model_cfg_ = await resolve_model(
                    app.state.session_factory, session_id, cfg_now_)
            hub.run_vision[session_id] = bool(model_cfg_.get("supports_vision"))
            hub.running.add(session_id)
            llm = llm_factory(cfg_now_, model_cfg_)
            runner = AgentRunner(app.state.session_factory, llm,
                                 config_getter, send, hub.ask, hub.confirm,
                                 hub.pop_pending)
            await runner.run(session_id, user_text_, cancel,
                             attachment_ids=attachment_ids_)
        finally:
            hub.run_vision.pop(session_id, None)
            hub.running.discard(session_id)
            hub.cancel_events.pop(session_id, None)

    asyncio.create_task(run_task(text_, attachment_ids, cfg_now, model_cfg))
```

注意：`hub.running.add` 从原来进入 task 就加改到能力解析之后、真正开跑之前；`status` 更新仍在最前，保持 watchdog 语义。

- [ ] **Step 5: 实现 loop 插话**

`app/agent/loop.py::_drain_steering` 整体替换：

```python
async def _drain_steering(self, session_id: int, root: Path, messages: list[dict]) -> int:
    from app.services import attachment_service as _att
    if not self.pop_pending:
        return 0
    injected = 0
    for item in await self.pop_pending(session_id):
        if isinstance(item, str):
            item = {"content": item, "attachment_ids": [], "client_msg_id": None}
        text_ = (item.get("content") or "").strip()
        ids = item.get("attachment_ids") or []
        if not text_ and not ids:
            continue
        expanded = expand_mentions(root, text_) if text_ else None
        try:
            row = await add_message(self.sf, session_id, "user", content=text_,
                                    context_content=expanded, attachment_ids=ids)
        except ValueError as e:
            await self.send({"type": "error", "message": f"插话未能送达：{e}"})
            continue
        files = await _att.for_llm(self.sf, [row["id"]]) if ids else {}
        content = self._user_content(text_, row.get("attachments") or [], True,
                                     files.get(row["id"], []))
        messages.append({"role": "user", "content": content})
        await self.send({"type": "steering_done", "content": text_,
                         "client_msg_id": item.get("client_msg_id")})
        injected += 1
    return injected
```

- [ ] **Step 6: 跑测试确认通过**

Run: `python -m pytest tests/test_ws.py -q`
Expected: PASS。

- [ ] **Step 7: 提交**

```bash
git add app/hub.py app/api/ws.py app/agent/loop.py app/api/sessions.py tests/test_ws.py
git commit -m "feat(chat): WS 附件协议、插话对象队列与视觉能力门"
```

---

### Task 10: 历史气泡渲染图片

**Files:**
- Modify: `frontend/src/components/ChatMessages.vue`
- Test: `frontend/src/components/ChatMessages.test.js`

**Interfaces:**
- Consumes: messages 每条的 `attachments: [{id, filename, mime, size_bytes, url}]`

- [ ] **Step 1: 写失败测试**

创建 `frontend/src/components/ChatMessages.test.js`：

```js
// @vitest-environment happy-dom
import { describe, it, expect, vi } from 'vitest'
import { h } from 'vue'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('../api', () => ({
  getMessages: vi.fn(), getSessionDetail: vi.fn(), listModels: vi.fn(),
  listWorkspaces: vi.fn(), patchSessionModel: vi.fn(), wsUrl: vi.fn(() => 'ws://t'),
}))

import ChatMessages from './ChatMessages.vue'

const ImageStub = {
  name: 'a-image',
  props: ['src', 'alt', 'width'],
  render() {
    return h('img', { class: 'stub-image', src: this.src, alt: this.alt })
  },
}
const GroupStub = {
  name: 'a-image-preview-group',
  render() {
    return h('div', { class: 'stub-image-group' }, this.$slots.default ? this.$slots.default() : [])
  },
}
const ButtonStub = {
  name: 'a-button',
  render() {
    return h('button', this.$slots.default ? this.$slots.default() : [])
  },
}

function mountMessages(messages) {
  setActivePinia(createPinia())
  return mount(ChatMessages, {
    props: { messages, hasSession: true },
    global: {
      stubs: {
        'a-image': ImageStub, 'a-image-preview-group': GroupStub, 'a-button': ButtonStub,
        MarkdownView: true, ToolCallBlock: true, AskUserCard: true,
        ThinkingBlock: true, TodoCard: true,
      },
    },
  })
}

it('user 气泡在文字之前渲染附件图片', () => {
  const w = mountMessages([{
    role: 'user', content: '这是什么？',
    attachments: [
      { id: 1, filename: 'a.png', url: '/api/attachments/1' },
      { id: 2, filename: 'b.png', url: '/api/attachments/2' },
    ],
  }])
  const imgs = w.findAll('.zc-bubble-images img')
  expect(imgs.length).toBe(2)
  expect(imgs[0].attributes('src')).toBe('/api/attachments/1')
  expect(w.find('.zc-bubble-text').text()).toBe('这是什么？')
  // 图片容器在文字之前
  expect(w.find('.zc-bubble').html().indexOf('zc-bubble-images'))
    .toBeLessThan(w.find('.zc-bubble').html().indexOf('zc-bubble-text'))
})

it('纯图片消息不渲染空白文字行', () => {
  const w = mountMessages([{
    role: 'user', content: '',
    attachments: [{ id: 1, filename: 'a.png', url: '/api/attachments/1' }],
  }])
  expect(w.find('.zc-bubble-images').exists()).toBe(true)
  expect(w.find('.zc-bubble-text').exists()).toBe(false)
})

it('排队角标与图片共存', () => {
  const w = mountMessages([{
    role: 'user', content: '', queued: true,
    attachments: [{ id: 1, filename: 'a.png', url: '/api/attachments/1' }],
  }])
  expect(w.find('.zc-queued').exists()).toBe(true)
  expect(w.find('.zc-bubble-images').exists()).toBe(true)
})
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npm run test -- --run src/components/ChatMessages.test.js`
Expected: FAIL，未渲染图片。

- [ ] **Step 3: 实现模板与样式**

`frontend/src/components/ChatMessages.vue` 的 user 分支改为：

```html
<div v-if="m.role === 'user'" class="zc-msg is-user">
  <div class="zc-bubble">
    <div v-if="m.attachments && m.attachments.length" class="zc-bubble-images">
      <a-image-preview-group>
        <a-image v-for="a in m.attachments" :key="a.id" :src="a.url" :alt="a.filename" :width="160" />
      </a-image-preview-group>
    </div>
    <span v-if="m.content" class="zc-bubble-text">{{ m.content }}</span>
    <span v-if="m.queued" class="zc-queued">排队中</span>
  </div>
</div>
```

样式追加：

```css
.zc-bubble-images {
  display: grid;
  grid-template-columns: repeat(2, 160px);
  gap: 6px;
  margin-bottom: 6px;
}
.zc-bubble-images :deep(.ant-image) {
  width: 160px;
  height: 160px;
  overflow: hidden;
  border-radius: 8px;
}
.zc-bubble-images :deep(img) {
  width: 160px;
  height: 160px;
  object-fit: cover;
  display: block;
}
.zc-bubble-text {
  display: block;
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd frontend && npm run test -- --run src/components/ChatMessages.test.js`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add frontend/src/components/ChatMessages.vue frontend/src/components/ChatMessages.test.js
git commit -m "feat(chat): 历史 user 气泡渲染图片网格"
```

---

### Task 11: 前端图片工具函数

**Files:**
- Create: `frontend/src/utils/imageAttachments.js`
- Test: `frontend/src/utils/imageAttachments.test.js`

**Interfaces:**
- Produces: `MAX_ATTACHMENTS`、`MAX_SOURCE_BYTES`、`MAX_ATTACHMENT_BYTES`、`MAX_MESSAGE_BYTES`、`MAX_IMAGE_EDGE`、`TARGET_IMAGE_BYTES`、`ACCEPTED_IMAGE_MIMES`
- Produces: `validateImageFile`、`compressImage`、`validateCompressed`、`newClientMsgId`、`formatBytes`

- [ ] **Step 1: 写失败测试**

创建 `frontend/src/utils/imageAttachments.test.js`：

```js
import { describe, it, expect } from 'vitest'
import {
  ACCEPTED_IMAGE_MIMES, MAX_ATTACHMENTS, MAX_ATTACHMENT_BYTES, MAX_MESSAGE_BYTES,
  MAX_SOURCE_BYTES, formatBytes, newClientMsgId, validateCompressed, validateImageFile,
} from './imageAttachments'

function file(name, type, size) {
  return { name, type, size }
}

describe('validateImageFile', () => {
  it('接受四种图片', () => {
    for (const mime of ACCEPTED_IMAGE_MIMES) {
      expect(validateImageFile(file('x', mime, 10), 0)).toBeNull()
    }
  })

  it('超过 4 张拒绝', () => {
    expect(validateImageFile(file('x', 'image/png', 10), MAX_ATTACHMENTS))
      .toContain('最多')
  })

  it('SVG 与其他类型拒绝', () => {
    expect(validateImageFile(file('x.svg', 'image/svg+xml', 10), 0)).toContain('不支持')
    expect(validateImageFile(file('x.pdf', 'application/pdf', 10), 0)).toContain('不支持')
  })

  it('原始文件超过 20MB 拒绝', () => {
    expect(validateImageFile(file('x', 'image/png', MAX_SOURCE_BYTES + 1), 0))
      .toContain('20MB')
  })
})

describe('validateCompressed', () => {
  it('压缩后单张超过 5MB 拒绝', () => {
    expect(validateCompressed({ size: MAX_ATTACHMENT_BYTES + 1 }, []))
      .toContain('5MB')
  })

  it('本批合计超过 10MB 拒绝', () => {
    expect(validateCompressed({ size: 2 }, [{ size_bytes: MAX_MESSAGE_BYTES }]))
      .toContain('10MB')
  })

  it('合计未超限通过', () => {
    expect(validateCompressed({ size: 2 }, [{ size_bytes: 10 }])).toBeNull()
  })
})

describe('formatBytes', () => {
  it('按量级给 B / KB / MB', () => {
    expect(formatBytes(0)).toBe('0 B')
    expect(formatBytes(1024)).toBe('1.0 KB')
    expect(formatBytes(1024 * 1024)).toBe('1.0 MB')
  })
})

describe('newClientMsgId', () => {
  it('两次生成不重复', () => {
    expect(newClientMsgId()).not.toBe(newClientMsgId())
  })
})
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npm run test -- --run src/utils/imageAttachments.test.js`
Expected: FAIL，模块不存在。

- [ ] **Step 3: 实现**

创建 `frontend/src/utils/imageAttachments.js`：

```js
// 聊天图片附件：类型/体积校验、浏览器端压缩与客户端消息 ID。
// 纯决策函数与 canvas 压缩分开，happy-dom 只测前者。
export const MAX_ATTACHMENTS = 4
export const MAX_SOURCE_BYTES = 20 * 1024 * 1024
export const MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024
export const MAX_MESSAGE_BYTES = 10 * 1024 * 1024
export const MAX_IMAGE_EDGE = 2048
export const TARGET_IMAGE_BYTES = 1.5 * 1024 * 1024
export const ACCEPTED_IMAGE_MIMES = ['image/png', 'image/jpeg', 'image/webp', 'image/gif']

export function validateImageFile(file, currentCount) {
  if (currentCount + 1 > MAX_ATTACHMENTS) return `最多 ${MAX_ATTACHMENTS} 张图片`
  if (!ACCEPTED_IMAGE_MIMES.includes(file.type)) {
    return '不支持的文件类型，仅支持 PNG / JPEG / WebP / GIF'
  }
  if (file.size > MAX_SOURCE_BYTES) return '单张图片不能超过 20MB'
  return null
}

export function validateCompressed(blob, items) {
  if (blob.size > MAX_ATTACHMENT_BYTES) return '压缩后图片仍超过 5MB，请换一张'
  const total = items.reduce((n, it) => n + (it.size_bytes || 0), 0) + blob.size
  if (total > MAX_MESSAGE_BYTES) return '单条消息图片合计不能超过 10MB'
  return null
}

export function formatBytes(n) {
  const v = Number(n) || 0
  if (v < 1024) return `${v} B`
  if (v < 1024 * 1024) return `${(v / 1024).toFixed(1)} KB`
  return `${(v / (1024 * 1024)).toFixed(1)} MB`
}

export function newClientMsgId() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID()
  return `c-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`
}

async function decodeImage(file) {
  if (typeof createImageBitmap === 'function') {
    try {
      return await createImageBitmap(file)
    } catch {
      // Safari 旧版/动图解码失败：回退到 Image + object URL
    }
  }
  return await new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file)
    const img = new Image()
    img.onload = () => { URL.revokeObjectURL(url); resolve(img) }
    img.onerror = () => { URL.revokeObjectURL(url); reject(new Error('图片解码失败')) }
    img.src = url
  })
}

function toBlob(canvas, quality) {
  return new Promise((resolve, reject) => {
    canvas.toBlob(
      (blob) => (blob ? resolve(blob) : reject(new Error('图片压缩失败'))),
      'image/jpeg', quality)
  })
}

export async function compressImage(file) {
  const src = await decodeImage(file)
  const w = src.width || src.naturalWidth
  const h = src.height || src.naturalHeight
  if (!w || !h) throw new Error('图片解码失败')
  const scale = Math.min(1, MAX_IMAGE_EDGE / Math.max(w, h))
  const canvas = document.createElement('canvas')
  canvas.width = Math.max(1, Math.round(w * scale))
  canvas.height = Math.max(1, Math.round(h * scale))
  const ctx = canvas.getContext('2d')
  if (!ctx) throw new Error('浏览器不支持图片压缩')
  // JPEG 无透明通道：先铺白，避免透明区域变黑
  ctx.fillStyle = '#fff'
  ctx.fillRect(0, 0, canvas.width, canvas.height)
  ctx.drawImage(src, 0, 0, canvas.width, canvas.height)
  if (src.close) src.close()
  let blob = null
  for (const quality of [0.85, 0.7, 0.5]) {
    blob = await toBlob(canvas, quality)
    if (blob.size <= TARGET_IMAGE_BYTES) break
  }
  return blob
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd frontend && npm run test -- --run src/utils/imageAttachments.test.js`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add frontend/src/utils/imageAttachments.js frontend/src/utils/imageAttachments.test.js
git commit -m "feat(chat): 前端图片校验与压缩工具"
```

---

### Task 12: 上传 API 封装与 useImageAttachments

**Files:**
- Modify: `frontend/src/api/index.js`
- Create: `frontend/src/composables/useImageAttachments.js`
- Test: `frontend/src/composables/useImageAttachments.test.js`

**Interfaces:**
- Produces: `uploadAttachment(sessionId, blob, filename, signal)`
- Produces: `fetchAttachmentBlob(url)`
- Produces: `useImageAttachments(sessionIdGetter)`：`items`、`count`、`uploading`、`hasFailed`、`error`、`addFiles`、`remove`、`retry`、`clear`、`readyItems`、`mergeRejected`

- [ ] **Step 1: 写失败测试**

创建 `frontend/src/composables/useImageAttachments.test.js`：

```js
// @vitest-environment happy-dom
import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('../api', () => ({ uploadAttachment: vi.fn() }))
vi.mock('../utils/imageAttachments', async (importOriginal) => ({
  ...(await importOriginal()),
  compressImage: vi.fn(),
}))

import { uploadAttachment } from '../api'
import { compressImage } from '../utils/imageAttachments'
import { useImageAttachments } from './useImageAttachments'

function file(name = 'a.png', type = 'image/png', size = 10) {
  return new File([new Uint8Array(size)], name, { type })
}

beforeEach(() => {
  vi.clearAllMocks()
  globalThis.URL.createObjectURL = vi.fn(() => 'blob:preview')
  globalThis.URL.revokeObjectURL = vi.fn()
  compressImage.mockResolvedValue(new Blob(['x'], { type: 'image/jpeg' }))
  uploadAttachment.mockResolvedValue({
    data: { id: 1, filename: 'a.png', mime: 'image/png', size_bytes: 10, url: '/api/attachments/1' },
  })
})

it('addFiles 压缩并上传，readyItems 只含服务端字段', async () => {
  const c = useImageAttachments(() => 7)
  await c.addFiles([file()])
  expect(compressImage).toHaveBeenCalledTimes(1)
  expect(uploadAttachment).toHaveBeenCalledWith(7, expect.any(Blob), 'a.png', expect.anything())
  expect(c.items.value[0].status).toBe('ready')
  expect(c.readyItems()).toEqual([
    { id: 1, filename: 'a.png', mime: 'image/png', size_bytes: 10, url: '/api/attachments/1' },
  ])
})

it('超过 4 张不新增，非图片拒绝', async () => {
  const c = useImageAttachments(() => 7)
  await c.addFiles(Array.from({ length: 5 }, (_, i) => file(`a${i}.png`)))
  expect(c.items.value.length).toBe(4)
  expect(c.error.value).toContain('最多')
  await c.addFiles([file('x.pdf', 'application/pdf')])
  expect(c.items.value.length).toBe(4)
  expect(c.error.value).toContain('不支持')
})

it('上传失败可单张重试', async () => {
  uploadAttachment.mockRejectedValueOnce({ response: { data: { detail: '上传失败' } } })
  const c = useImageAttachments(() => 7)
  await c.addFiles([file()])
  expect(c.items.value[0].status).toBe('failed')
  expect(c.hasFailed.value).toBe(true)
  await c.retry(c.items.value[0].key)
  expect(c.items.value[0].status).toBe('ready')
})

it('会话切换后晚到的上传结果不写回', async () => {
  let resolve
  uploadAttachment.mockReturnValue(new Promise((r) => { resolve = r }))
  let sid = 7
  const c = useImageAttachments(() => sid)
  const pending = c.addFiles([file()])
  await vi.waitFor(() => expect(uploadAttachment).toHaveBeenCalled())
  sid = 8
  c.clear()
  resolve({ data: { id: 1, filename: 'a.png', mime: 'image/png', size_bytes: 10, url: '/api/attachments/1' } })
  await pending
  expect(c.items.value).toEqual([])
})

it('mergeRejected 直接标 ready 且按 id 去重', () => {
  const c = useImageAttachments(() => 7)
  const row = { id: 1, filename: 'a.png', mime: 'image/png', size_bytes: 10, url: '/api/attachments/1' }
  c.mergeRejected([row, row])
  expect(c.items.value.length).toBe(1)
  expect(c.readyItems()).toEqual([row])
  expect(uploadAttachment).not.toHaveBeenCalled()
})
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npm run test -- --run src/composables/useImageAttachments.test.js`
Expected: FAIL，模块不存在。

- [ ] **Step 3: 实现 API 与 composable**

`frontend/src/api/index.js` 追加：

```js
// ---- 聊天图片附件 ----
export const uploadAttachment = (sessionId, blob, filename, signal) =>
  http.post(`/sessions/${sessionId}/attachments`, blob, {
    params: { filename },
    headers: { 'Content-Type': blob.type || 'image/jpeg' }, // 客户端压缩产物恒为 JPEG
    signal,
  })
export const fetchAttachmentBlob = (url) =>
  http.get(url, { responseType: 'blob', baseURL: '' }) // url 已是 /api/... 全路径
```

创建 `frontend/src/composables/useImageAttachments.js`：

```js
// 附件队列：校验 → 压缩 → 立即上传；会话切换中止在途请求并清空。
import { computed, ref } from 'vue'
import { uploadAttachment } from '../api'
import {
  compressImage, validateCompressed, validateImageFile,
} from '../utils/imageAttachments'

let seq = 0

export function useImageAttachments(sessionIdGetter) {
  const items = ref([])
  const error = ref('')
  const count = computed(() => items.value.length)
  const uploading = computed(() =>
    items.value.some((i) => i.status === 'compressing' || i.status === 'uploading'))
  const hasFailed = computed(() => items.value.some((i) => i.status === 'failed'))

  function readyItems() {
    return items.value
      .filter((i) => i.status === 'ready')
      .map(({ id, filename, mime, size_bytes, url }) => ({ id, filename, mime, size_bytes, url }))
  }

  function drop(item) {
    if (item.controller) item.controller.abort()
    if (item.previewUrl?.startsWith('blob:')) URL.revokeObjectURL(item.previewUrl)
    items.value = items.value.filter((i) => i !== item)
  }

  async function uploadOne(item, sid) {
    item.status = 'compressing'
    const blob = await compressImage(item.file)
    const err = validateCompressed(blob, items.value.filter((i) => i.status === 'ready'))
    if (err) {
      item.status = 'failed'
      item.error = err
      error.value = err
      return
    }
    item.status = 'uploading'
    item.controller = new AbortController()
    const { data } = await uploadAttachment(sid, blob, item.filename, item.controller.signal)
    if (sessionIdGetter() !== sid || !items.value.includes(item)) return
    item.id = data.id
    item.mime = data.mime
    item.size_bytes = data.size_bytes
    item.url = data.url
    item.status = 'ready'
    item.error = ''
  }

  async function addFiles(files) {
    const sid = sessionIdGetter()
    if (!sid) { error.value = '请先选择会话'; return }
    error.value = ''
    for (const file of Array.from(files || [])) {
      const err = validateImageFile(file, items.value.length)
      if (err) { error.value = err; continue }
      const item = {
        key: `a-${++seq}`,
        file,
        filename: file.name || '图片',
        previewUrl: URL.createObjectURL(file),
        status: 'compressing',
        error: '',
        controller: null,
      }
      items.value.push(item)
      try {
        await uploadOne(item, sid)
      } catch (e) {
        if (e?.name === 'CanceledError' || e?.code === 'ERR_CANCELED') {
          drop(item)
          continue
        }
        item.status = 'failed'
        item.error = e?.response?.data?.detail || '上传失败'
        error.value = item.error
      }
    }
  }

  async function retry(key) {
    const item = items.value.find((i) => i.key === key)
    if (!item) return
    error.value = ''
    if (item.controller) item.controller = null
    try {
      await uploadOne(item, sessionIdGetter())
    } catch (e) {
      item.status = 'failed'
      item.error = e?.response?.data?.detail || '上传失败'
      error.value = item.error
    }
  }

  function remove(key) {
    const item = items.value.find((i) => i.key === key)
    if (item) drop(item)
  }

  function clear() {
    for (const item of items.value) {
      if (item.controller) item.controller.abort()
      if (item.previewUrl?.startsWith('blob:')) URL.revokeObjectURL(item.previewUrl)
    }
    items.value = []
    error.value = ''
  }

  function mergeRejected(list) {
    for (const row of list || []) {
      if (items.value.some((i) => i.id === row.id)) continue
      items.value.push({
        key: `a-${++seq}`,
        file: null,
        filename: row.filename,
        previewUrl: row.url,
        status: 'ready',
        error: '',
        controller: null,
        id: row.id,
        mime: row.mime,
        size_bytes: row.size_bytes,
        url: row.url,
      })
    }
  }

  return {
    items, error, count, uploading, hasFailed,
    addFiles, remove, retry, clear, readyItems, mergeRejected,
  }
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd frontend && npm run test -- --run src/composables/useImageAttachments.test.js`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add frontend/src/api/index.js frontend/src/composables/useImageAttachments.js frontend/src/composables/useImageAttachments.test.js
git commit -m "feat(chat): 附件上传队列组合式函数"
```

---

### Task 13: chat store 发送、回滚、重试与能力位

**Files:**
- Modify: `frontend/src/stores/chat.js`
- Test: `frontend/src/stores/chat.test.js`

**Interfaces:**
- Consumes: `uploadAttachment`、`fetchAttachmentBlob`、`newClientMsgId`
- Produces: `send({text, attachments})`、`effectiveModel`、`visionSupported`、`rejectedAttachments`、`pendingOutbox`、`retrying`

- [ ] **Step 1: 写失败测试**

更新 `frontend/src/stores/chat.test.js`。模块 mock 增加 API：

```js
vi.mock('../api', async (importOriginal) => ({
  ...(await importOriginal()),
  wsUrl: (p) => 'ws://t' + p,
  getMessages: vi.fn(),
  getSessionDetail: vi.fn(),
  patchSessionModel: vi.fn(),
  uploadAttachment: vi.fn(),
  fetchAttachmentBlob: vi.fn(),
}))
```

`mk()` 补齐新状态：

```js
function mk() {
  return {
    messages: [], streamingContent: '', streamingThinking: '', todoList: [],
    compacting: false, pendingAsk: null, currentSessionId: 1,
    pendingEcho: '', pendingEchoId: '', pendingOutbox: {}, rejectedDraft: '',
    rejectedAttachments: [], running: false, visionSupported: false,
  }
}
```

追加用例：

```js
describe('chat store 图片发送（票 001/003/006）', () => {
  const att = { id: 1, filename: 'a.png', mime: 'image/png', size_bytes: 10, url: '/api/attachments/1' }

  function wsStore() {
    setActivePinia(createPinia())
    const s = useChatStore()
    s.currentSessionId = 1
    s._ws = { readyState: 1, send: vi.fn() }
    return s
  }

  it('纯图片消息允许发送，上行带 attachment_ids 与 client_msg_id', () => {
    const s = wsStore()
    s.visionSupported = true
    assert.equal(s.send({ text: '', attachments: [att] }), true)
    assert.equal(s.messages[0].content, '')
    assert.deepEqual(s.messages[0].attachments, [att])
    const payload = JSON.parse(s._ws.send.mock.calls[0][0])
    assert.equal(payload.type, 'user_message')
    assert.deepEqual(payload.attachment_ids, [1])
    assert.ok(payload.client_msg_id)
  })

  it('非视觉模型带附件本地拦截，不产生气泡与 outbox', () => {
    const s = wsStore()
    s.visionSupported = false
    assert.equal(s.send({ text: '看图', attachments: [att] }), false)
    assert.equal(s.messages.length, 0)
    assert.match(s.hint, /不支持图片/)
    assert.deepEqual(Object.keys(s.pendingOutbox), [])
  })

  it('busy 带 client_msg_id 时回滚对应气泡并恢复文字与附件', () => {
    const s = wsStore()
    s.visionSupported = true
    s.send({ text: '看图', attachments: [att] })
    const cid = s.messages[0].client_msg_id
    applyEvent(s, { type: 'busy', message: '不行', client_msg_id: cid, running: false })
    assert.equal(s.messages.length, 0)
    assert.equal(s.rejectedDraft, '看图')
    assert.deepEqual(s.rejectedAttachments, [att])
    assert.equal(s.running, false)
  })

  it('切走会话后 busy 只清 outbox，不把旧草稿回填到新会话', () => {
    const s = wsStore()
    s.visionSupported = true
    s.send({ text: '看图', attachments: [att] })
    const cid = s.messages[0].client_msg_id
    s.currentSessionId = 2
    applyEvent(s, { type: 'busy', message: '不行', client_msg_id: cid, running: false })
    assert.equal(s.rejectedDraft, '')
    assert.deepEqual(s.rejectedAttachments, [])
    assert.deepEqual(s.pendingOutbox, {})
  })

  it('steering_done 按 client_msg_id 只摘对应气泡的角标', () => {
    const s = wsStore()
    s.running = true
    s.visionSupported = true
    s.send({ text: '一', attachments: [att] })
    s.send({ text: '二', attachments: [att] })
    const first = s.messages[0].client_msg_id
    applyEvent(s, { type: 'steering_done', content: '一', client_msg_id: first })
    assert.equal(s.messages[0].queued, false)
    assert.equal(s.messages[1].queued, true)
  })

  it('cancel 把排队气泡的文字与附件一起退回输入框', () => {
    const s = wsStore()
    s.running = true
    s.visionSupported = true
    s.send({ text: '一', attachments: [att] })
    s.send({ text: '二', attachments: [att] })
    s.cancel()
    assert.equal(s.rejectedDraft, '一\n\n二')
    assert.equal(s.rejectedAttachments.length, 2)
    assert.equal(s.messages.filter((m) => m.role === 'user').length, 0)
  })

  it('retry 重新下载并上传图片，再用新附件 ID 发送', async () => {
    const s = wsStore()
    s.visionSupported = true
    const old = { ...att, id: 9, url: '/api/attachments/9' }
    s.messages.push({ role: 'user', content: '看图', attachments: [old] })
    const { fetchAttachmentBlob, uploadAttachment } = await import('../api')
    fetchAttachmentBlob.mockResolvedValue({ data: new Blob(['x'], { type: 'image/png' }) })
    uploadAttachment.mockResolvedValue({
      data: { id: 10, filename: 'a.png', mime: 'image/png', size_bytes: 10, url: '/api/attachments/10' },
    })
    const spy = vi.spyOn(s, 'send').mockReturnValue(true)
    await s.retry()
    expect(fetchAttachmentBlob).toHaveBeenCalledWith('/api/attachments/9')
    expect(uploadAttachment).toHaveBeenCalled()
    expect(spy).toHaveBeenCalledWith({
      text: '看图',
      attachments: [{ id: 10, filename: 'a.png', mime: 'image/png', size_bytes: 10, url: '/api/attachments/10' }],
    })
  })

  it('openSession 写入 effective_model 并还原对象形态的 pending_steering', async () => {
    const { getMessages, getSessionDetail } = await import('../api')
    getMessages.mockResolvedValue({ data: [] })
    getSessionDetail.mockResolvedValue({ data: {
      todo_state: [], model_config_id: null,
      effective_model: { id: null, name: 'deepseek-chat', model: 'deepseek-chat', supports_vision: false, source: 'global' },
      pending_steering: [{ content: '看图', client_msg_id: 'c1', attachments: [att] }],
    } })
    setActivePinia(createPinia())
    const s = useChatStore()
    s.connect = vi.fn()
    await s.openSession(5, 2)
    expect(s.effectiveModel.model).toBe('deepseek-chat')
    expect(s.visionSupported).toBe(false)
    expect(s.messages[0]).toMatchObject({ role: 'user', content: '看图', queued: true, attachments: [att] })
    expect(s.messages[0].client_msg_id).toBe('c1')
  })
})
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npm run test -- --run src/stores/chat.test.js`
Expected: FAIL，`send` 仍只接受字符串。

- [ ] **Step 3: 实现 store**

`frontend/src/stores/chat.js` 顶部 import 增加：

```js
import {
  getMessages, getSessionDetail, listModels, listWorkspaces, patchSessionModel,
  fetchAttachmentBlob, uploadAttachment, wsUrl,
} from '../api'
import { newClientMsgId } from '../utils/imageAttachments'
```

state 增加：

```js
effectiveModel: null,
visionSupported: false,
rejectedAttachments: [],
pendingOutbox: {},
retrying: false,
pendingEchoId: '',
```

`send` 替换为对象签名：

```js
send(payload) {
  const { text = '', attachments = [] } =
    typeof payload === 'string' ? { text: payload } : (payload || {})
  const content = (text || '').trim()
  if (!content && !attachments.length) return false
  if (this.compacting) {
    this.rejectedDraft = content
    this.hint = COMPACT_HINT
    return false
  }
  if (attachments.length && !this.visionSupported) {
    this.hint = '当前模型不支持图片输入，请切换支持视觉的模型'
    return false
  }
  const clientMsgId = newClientMsgId()
  const steering = this.running
  const bubble = { role: 'user', content, attachments, client_msg_id: clientMsgId }
  if (steering) bubble.queued = true
  this.messages.push(bubble)
  this.pendingOutbox[clientMsgId] = {
    sessionId: this.currentSessionId, text: content, attachments,
  }
  this.pendingEcho = content
  this.pendingEchoId = clientMsgId
  this.streamingContent = ''
  this.streamingThinking = ''
  this.hint = ''
  this.rejectedDraft = ''
  this.running = true
  if (!this._uplink({
    type: 'user_message', content,
    attachment_ids: attachments.map((a) => a.id),
    client_msg_id: clientMsgId,
  })) {
    this.messages.pop()
    delete this.pendingOutbox[clientMsgId]
    this.pendingEcho = ''
    this.pendingEchoId = ''
    this.running = false
    this.rejectedDraft = content
    this.hint = OFFLINE_HINT
    return false
  }
  return true
}
```

`busy` 分支替换：

```js
case 'busy': {
  const cid = ev.client_msg_id
  let idx = -1
  if (cid) {
    idx = store.messages.findIndex(
      (m) => m.role === 'user' && m.client_msg_id === cid)
  }
  if (idx < 0 && store.pendingEcho) {
    for (let i = store.messages.length - 1; i >= 0; i--) {
      const m = store.messages[i]
      if (m.role === 'user' && m.content === store.pendingEcho) { idx = i; break }
    }
  }
  if (idx >= 0) {
    const [bubble] = store.messages.splice(idx, 1)
    const key = bubble.client_msg_id || cid
    const out = key ? store.pendingOutbox[key] : null
    if (out) {
      if (out.sessionId === store.currentSessionId) {
        store.rejectedDraft = out.text || ''
        store.rejectedAttachments = out.attachments || []
      }
      delete store.pendingOutbox[key]
    } else {
      store.rejectedDraft = bubble.content || ''
      store.rejectedAttachments = bubble.attachments || []
    }
  }
  if (typeof ev.running === 'boolean') store.running = ev.running
  store.pendingEcho = ''
  store.pendingEchoId = ''
  store.hint = ev.message || BUSY_HINT
  break
}
```

`steering_done`：

```js
case 'steering_done': {
  let idx = -1
  if (ev.client_msg_id) {
    idx = store.messages.findIndex(
      (m) => m.role === 'user' && m.client_msg_id === ev.client_msg_id)
  }
  if (idx < 0) {
    for (let i = store.messages.length - 1; i >= 0; i--) {
      const m = store.messages[i]
      if (m.role === 'user' && m.queued && m.content === ev.content) { idx = i; break }
    }
  }
  if (idx >= 0) store.messages[idx].queued = false
  if (store.pendingEchoId === ev.client_msg_id ||
      (!ev.client_msg_id && store.pendingEcho === ev.content)) {
    store.pendingEcho = ''
    store.pendingEchoId = ''
  }
  break
}
```

`error` 与终态 `status` 清 outbox：

```js
case 'error':
  ...
  store.pendingOutbox = {}
  store.pendingEchoId = ''
  break
case 'status':
  if (ev.status === 'running') { ... }
  ...
  store.pendingOutbox = {}
  store.pendingEchoId = ''
  break
```

`openSession` 增加：

```js
this.effectiveModel = detail?.effective_model || null
this.visionSupported = !!detail?.effective_model?.supports_vision
this.pendingOutbox = {}
this.rejectedAttachments = []
this.pendingEchoId = ''
...
for (const c of detail?.pending_steering || []) {
  if (typeof c === 'string') {
    this.messages.push({ role: 'user', content: c, queued: true })
    continue
  }
  this.messages.push({
    role: 'user', content: c.content || '', attachments: c.attachments || [],
    client_msg_id: c.client_msg_id || undefined, queued: true,
  })
}
```

`setModel`：

```js
async setModel(id) {
  const sid = this.currentSessionId
  const { data } = await patchSessionModel(sid, id)
  if (this.currentSessionId !== sid) return
  this.currentModelId = id
  if (data?.effective_model) {
    this.effectiveModel = data.effective_model
    this.visionSupported = !!data.effective_model.supports_vision
  }
}
```

`cancel`：

```js
cancel() {
  this._uplink({ type: 'cancel' })
  this.pendingAsk = null
  this.pendingConfirm = null
  const stranded = this.messages.filter((m) => m.role === 'user' && m.queued)
  if (stranded.length) {
    this.messages = this.messages.filter((m) => !(m.role === 'user' && m.queued))
    const texts = stranded.map((m) => m.content).filter(Boolean)
    this.rejectedDraft = texts.join('\n\n')
    this.rejectedAttachments = stranded.flatMap((m) => m.attachments || [])
    this.hint = `本轮已停止：${stranded.length} 条插话未送达，文本与图片已退回输入框`
  }
  this.pendingOutbox = {}
  this.pendingEcho = ''
  this.pendingEchoId = ''
  this.running = false
  this.streamingThinking = ''
}
```

`retry` 改为 async：

```js
async retry() {
  if (this.retrying) return
  let last = null
  for (let i = this.messages.length - 1; i >= 0; i--) {
    if (this.messages[i].role === 'user') { last = this.messages[i]; break }
  }
  if (!last) return
  const text = last.content || ''
  const atts = last.attachments || []
  if (!atts.length) { this.send(text); return }
  const sid = this.currentSessionId
  this.retrying = true
  try {
    const fresh = []
    for (const a of atts) {
      try {
        const { data: blob } = await fetchAttachmentBlob(a.url)
        const { data } = await uploadAttachment(sid, blob, a.filename)
        fresh.push({
          id: data.id, filename: data.filename, mime: data.mime,
          size_bytes: data.size_bytes, url: data.url,
        })
      } catch {
        if (this.currentSessionId !== sid) return
        this.rejectedDraft = text
        this.rejectedAttachments = atts
        this.hint = '图片重新上传失败，请重新粘贴后再发'
        return
      }
      if (this.currentSessionId !== sid) return
    }
    if (!this.send({ text, attachments: fresh })) {
      this.rejectedDraft = text
      this.rejectedAttachments = fresh
    }
  } finally {
    this.retrying = false
  }
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd frontend && npm run test -- --run src/stores/chat.test.js`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add frontend/src/stores/chat.js frontend/src/stores/chat.test.js
git commit -m "feat(chat): store 支持附件发送、回滚与重试"
```

---

### Task 14: ChatInput 三条入口与附件条

**Files:**
- Modify: `frontend/src/components/ChatInput.vue`
- Test: `frontend/src/components/ChatInput.test.js`

**Interfaces:**
- Consumes: `useImageAttachments`、`MAX_ATTACHMENTS`、`formatBytes`、`chatStore.rejectedAttachments`
- Produces: paste / drop / 文件选择三入口；附件条；能力警示；发送闸门

- [ ] **Step 1: 写失败测试**

创建 `frontend/src/components/ChatInput.test.js`：

```js
// @vitest-environment happy-dom
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { h, nextTick, ref, computed } from 'vue'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('../api', () => ({
  tree: vi.fn().mockResolvedValue({ data: [] }),
  listModels: vi.fn().mockResolvedValue({ data: [] }),
}))

const attState = vi.hoisted(() => ({
  items: ref([]),
  error: ref(''),
  addFiles: vi.fn(),
  remove: vi.fn(),
  retry: vi.fn(),
  clear: vi.fn(),
  mergeRejected: vi.fn(),
}))
vi.mock('../composables/useImageAttachments', () => ({
  useImageAttachments: () => ({
    items: attState.items,
    error: attState.error,
    count: computed(() => attState.items.value.length),
    uploading: computed(() => attState.items.value.some((i) => i.status === 'uploading')),
    hasFailed: computed(() => attState.items.value.some((i) => i.status === 'failed')),
    addFiles: attState.addFiles,
    remove: attState.remove,
    retry: attState.retry,
    clear: attState.clear,
    readyItems: () => attState.items.value.filter((i) => i.status === 'ready'),
    mergeRejected: attState.mergeRejected,
  }),
}))

import ChatInput from './ChatInput.vue'
import { useChatStore } from '../stores/chat'

const ButtonStub = {
  name: 'a-button',
  props: ['disabled', 'loading'],
  render() {
    return h('button', {
      class: ['stub-btn', { 'is-disabled': !!this.disabled }],
      disabled: !!this.disabled,
      onClick: (e) => this.$emit('click', e),
    }, this.$slots.default ? this.$slots.default() : [])
  },
}
const TextareaStub = {
  name: 'a-textarea',
  props: ['value'],
  emits: ['update:value'],
  render() {
    return h('textarea', {
      value: this.value,
      onInput: (e) => this.$emit('update:value', e.target.value),
    })
  },
}

function mountInput() {
  setActivePinia(createPinia())
  return mount(ChatInput, {
    props: { running: false },
    global: {
      stubs: {
        'a-button': ButtonStub, 'a-textarea': TextareaStub,
        'a-select': true, 'a-select-option': true, 'a-spin': true,
        ChatAppActions: true, 'chat-app-actions': true,
      },
    },
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  attState.items.value = []
  attState.error.value = ''
})

it('粘贴图片调用 addFiles', async () => {
  const w = mountInput()
  const f = new File(['x'], 'a.png', { type: 'image/png' })
  await w.find('.zc-input').trigger('paste', {
    clipboardData: { files: [f], types: ['Files'] },
  })
  expect(attState.addFiles).toHaveBeenCalledWith([f])
})

it('纯文字粘贴不拦截图片入口', async () => {
  const w = mountInput()
  await w.find('.zc-input').trigger('paste', {
    clipboardData: { files: [], types: ['text/plain'] },
  })
  expect(attState.addFiles).not.toHaveBeenCalled()
})

it('上传中或非视觉模型带附件时发送按钮禁用', async () => {
  const w = mountInput()
  attState.items.value = [{ key: 'a', status: 'uploading' }]
  await nextTick()
  expect(w.find('.zc-send-btn').attributes('disabled')).toBeDefined()

  attState.items.value = [{ key: 'b', status: 'ready', id: 1, url: '/api/attachments/1' }]
  await nextTick()
  expect(w.find('.zc-send-btn').attributes('disabled')).toBeDefined()
  expect(w.text()).toContain('当前模型不支持图片输入')
})

it('发送成功清空文字与附件，失败保留', async () => {
  const w = mountInput()
  const store = useChatStore()
  store.visionSupported = true
  attState.items.value = [{ key: 'b', status: 'ready', id: 1, url: '/api/attachments/1' }]
  await w.find('textarea').setValue('看图')
  const send = vi.spyOn(store, 'send').mockReturnValue(true)
  await w.find('.zc-send-btn').trigger('click')
  expect(send).toHaveBeenCalledWith({
    text: '看图',
    attachments: [{ key: 'b', status: 'ready', id: 1, url: '/api/attachments/1' }],
  })
  expect(w.find('textarea').element.value).toBe('')
  expect(attState.clear).toHaveBeenCalled()

  await w.find('textarea').setValue('再看')
  send.mockReturnValue(false)
  await w.find('.zc-send-btn').trigger('click')
  expect(w.find('textarea').element.value).toBe('再看')
})

it('切换会话清空附件、保留文字', async () => {
  const w = mountInput()
  await w.find('textarea').setValue('保留我')
  useChatStore().currentSessionId = 9
  await nextTick()
  expect(attState.clear).toHaveBeenCalled()
  expect(w.find('textarea').element.value).toBe('保留我')
})
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npm run test -- --run src/components/ChatInput.test.js`
Expected: FAIL，组件没有 `zc-send-btn` / 附件条 / paste 入口。

- [ ] **Step 3: 实现组件**

`frontend/src/components/ChatInput.vue` script 顶部 import 增加：

```js
import { PaperClipOutlined } from '@ant-design/icons-vue'
import { useImageAttachments } from '../composables/useImageAttachments'
import { MAX_ATTACHMENTS, formatBytes } from '../utils/imageAttachments'
```

在 `const textareaRef = ref(null)` 之后加：

```js
const fileInputRef = ref(null)
const dragOver = ref(false)
const {
  items: attachmentItems, error: attachmentError, count: attachmentCount,
  uploading: attachmentsUploading, hasFailed: attachmentsFailed,
  addFiles, remove: removeAttachment, retry: retryAttachment,
  clear: clearAttachments, readyItems, mergeRejected,
} = useImageAttachments(() => chatStore.currentSessionId)

const visionBlocked = computed(
  () => attachmentCount.value > 0 && !chatStore.visionSupported)
const canSend = computed(() => {
  const ready = readyItems().length
  return (!!text.value.trim() || ready > 0)
    && !attachmentsUploading.value
    && !attachmentsFailed.value
    && !chatStore.compacting
    && (ready === 0 || chatStore.visionSupported)
})
const hintText = computed(() => {
  if (chatStore.hint) return chatStore.hint
  if (attachmentError.value) return attachmentError.value
  if (attachmentsUploading.value) return '图片上传中…'
  return 'Enter 发送 / Shift+Enter 换行'
})

function onPaste(e) {
  const files = Array.from(e.clipboardData?.files || [])
  if (!files.length) return
  const hasText = Array.from(e.clipboardData?.types || []).includes('text/plain')
  if (!hasText) e.preventDefault()
  addFiles(files)
}
function onDrop(e) {
  dragOver.value = false
  addFiles(Array.from(e.dataTransfer?.files || []))
}
function openFilePicker() {
  fileInputRef.value?.click()
}
function onFileChange(e) {
  addFiles(Array.from(e.target.files || []))
  e.target.value = ''
}
```

watch 增加：

```js
watch(
  () => chatStore.rejectedAttachments,
  (list) => {
    if (!list || !list.length) return
    mergeRejected(list)
    chatStore.rejectedAttachments = []
  }
)
watch(
  () => chatStore.currentSessionId,
  (id, old) => { if (id !== old) clearAttachments() }
)
```

`doSend`：

```js
function doSend() {
  if (!canSend.value) return
  const content = text.value.trim()
  mention.value = null
  if (chatStore.send({ text: content, attachments: readyItems() })) {
    text.value = ''
    clearAttachments()
  }
}
```

模板根节点：

```html
<div
  class="zc-input"
  :class="{ 'is-dragover': dragOver }"
  @paste="onPaste"
  @dragover.prevent="dragOver = true"
  @dragleave="dragOver = false"
  @drop.prevent="onDrop"
>
```

在 `.zc-input-wrap` 之前插入附件条：

```html
<div v-if="attachmentCount" class="zc-attachments">
  <div v-if="visionBlocked" class="zc-attach-warn">
    当前模型不支持图片输入，请切换支持视觉的模型
  </div>
  <div class="zc-attach-list">
    <div v-for="it in attachmentItems" :key="it.key" class="zc-attach-item" :class="`is-${it.status}`">
      <img class="zc-attach-thumb" :src="it.previewUrl" :alt="it.filename" />
      <a-spin v-if="it.status === 'compressing' || it.status === 'uploading'" size="small" class="zc-attach-spin" />
      <div v-else-if="it.status === 'failed'" class="zc-attach-fail">
        <a-button type="link" size="small" @click="retryAttachment(it.key)">重试</a-button>
        <a-button type="link" size="small" danger @click="removeAttachment(it.key)">移除</a-button>
      </div>
      <a-button v-else type="text" size="small" class="zc-attach-remove" @click="removeAttachment(it.key)">×</a-button>
      <span v-if="it.size_bytes" class="zc-attach-size">{{ formatBytes(it.size_bytes) }}</span>
    </div>
  </div>
  <span class="zc-attach-count">{{ attachmentCount }}/{{ MAX_ATTACHMENTS }}</span>
</div>
```

操作行提示改为 `{{ hintText }}`；按钮组在 `<chat-app-actions />` 之前加：

```html
<a-button
  size="small"
  class="zc-attach-btn"
  :disabled="attachmentCount >= MAX_ATTACHMENTS"
  @click="openFilePicker"
>
  <template #icon><paper-clip-outlined /></template>
</a-button>
<input
  ref="fileInputRef"
  type="file"
  accept="image/png,image/jpeg,image/webp,image/gif"
  multiple
  class="zc-file-input"
  @change="onFileChange"
/>
```

发送按钮改为：

```html
<a-button
  class="zc-send-btn"
  type="primary"
  :disabled="!canSend"
  @click="doSend"
>
```

模型下拉选项加能力后缀：

```html
<a-select-option v-for="m in chatStore.models" :key="m.id" :value="m.id">
  {{ m.name }}（{{ m.model }}）{{ m.supports_vision ? ' · 图片' : '' }}
</a-select-option>
```

样式追加：

```css
.zc-file-input { display: none; }
.zc-input.is-dragover { outline: 2px dashed var(--zc-primary); outline-offset: -4px; }
.zc-attachments { margin-bottom: 8px; }
.zc-attach-warn {
  margin-bottom: 6px; font-size: 12px; color: #a17c5b;
}
.zc-attach-list { display: flex; flex-wrap: wrap; gap: 8px; }
.zc-attach-item {
  position: relative; width: 56px; height: 56px; border-radius: 6px;
  overflow: hidden; background: #f4f6f9;
}
.zc-attach-thumb { width: 56px; height: 56px; object-fit: cover; display: block; }
.zc-attach-spin { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; background: rgba(255,255,255,.6); }
.zc-attach-remove { position: absolute; top: 0; right: 0; min-width: 18px; height: 18px; padding: 0; line-height: 18px; }
.zc-attach-fail {
  position: absolute; inset: 0; display: flex; flex-direction: column;
  align-items: center; justify-content: center; background: rgba(253,238,238,.92);
}
.zc-attach-size {
  position: absolute; left: 2px; bottom: 2px; font-size: 10px;
  color: #fff; background: rgba(0,0,0,.45); border-radius: 3px; padding: 0 3px;
}
.zc-attach-count { font-size: 12px; color: var(--zc-text-sub); margin-left: 4px; }
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd frontend && npm run test -- --run src/components/ChatInput.test.js`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add frontend/src/components/ChatInput.vue frontend/src/components/ChatInput.test.js
git commit -m "feat(chat): 输入框粘贴/拖拽/选择图片与附件条"
```

---

### Task 15: 设置页、配置 store 与模型表单

**Files:**
- Modify: `frontend/src/utils/modelForm.js`
- Modify: `frontend/src/stores/config.js`
- Modify: `frontend/src/views/SettingsView.vue`
- Test: `frontend/src/utils/modelForm.test.js`
- Test: `frontend/src/stores/config.test.js`

**Interfaces:**
- Produces: 模型表单 `supports_vision`；基础配置 `supportsVision`
- Produces: 模型表格「能力」列与「图片」标签；两个开关

- [ ] **Step 1: 写失败测试**

`frontend/src/utils/modelForm.test.js` 更新 `emptyModelForm` 期望：

```js
expect(emptyModelForm()).toEqual({
  name: '', base_url: '', api_key: '', model: '',
  kind: 'chat', is_default: false, supports_vision: false,
})
```

`buildModelPayload` 用例追加：

```js
it('supports_vision 原样提交，embedding 强制 false', () => {
  expect(buildModelPayload({ ...form, supports_vision: true }, false).supports_vision).toBe(true)
  expect(buildModelPayload({ ...form, kind: 'embedding', supports_vision: true }, false)
    .supports_vision).toBe(false)
})
```

`frontend/src/stores/config.test.js` 追加：

```js
it('loadConfig / save 读写 llm.supports_vision', async () => {
  getConfig.mockResolvedValue({ data: { 'llm.supports_vision': true } })
  const store = useConfigStore()
  await store.loadConfig()
  expect(store.form.supportsVision).toBe(true)

  await store.save()
  expect(putConfig).toHaveBeenCalledWith(
    expect.objectContaining({ 'llm.supports_vision': true }))
})
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npm run test -- --run src/utils/modelForm.test.js src/stores/config.test.js`
Expected: FAIL，字段缺失。

- [ ] **Step 3: 实现**

`modelForm.js`：

```js
export function emptyModelForm() {
  return {
    name: '', base_url: '', api_key: '', model: '',
    kind: DEFAULT_MODEL_KIND, is_default: false, supports_vision: false,
  }
}
...
supports_vision: form.kind === 'embedding' ? false : !!form.supports_vision,
```

`config.js`：

```js
supportsVision: 'llm.supports_vision',
...
supportsVision: false,
```

`SettingsView.vue`：

- `import { createVNode, onMounted, ref, watch } from 'vue'`；
- `modelColumns` 在「类型」后加 `{ title: '能力', dataIndex: 'supports_vision', width: 90 }`；
- `openEdit` 增加 `supports_vision: !!record.supports_vision`；
- bodyCell 增加：

```html
<template v-else-if="column.dataIndex === 'supports_vision'">
  <a-tag v-if="record.kind === 'chat' && record.supports_vision" color="cyan">图片</a-tag>
  <span v-else>—</span>
</template>
```

- 模型弹框在「类型」之后加：

```html
<a-form-item label="支持图片输入" extra="开启后该模型可用于聊天图片识别">
  <a-switch
    v-model:checked="modelForm.supports_vision"
    :disabled="modelForm.kind === 'embedding'"
  />
</a-form-item>
```

- script 加 watch：

```js
watch(
  () => modelForm.value.kind,
  (k) => { if (k === 'embedding') modelForm.value.supports_vision = false }
)
```

- 基础配置「模型接入」卡片在 API Key 之后加：

```html
<a-form-item
  label="支持图片输入"
  extra="仅在未配置默认对话模型时生效；有默认模型时以模型库设置为准"
>
  <a-switch v-model:checked="store.form.supportsVision" />
</a-form-item>
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd frontend && npm run test -- --run src/utils/modelForm.test.js src/stores/config.test.js`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add frontend/src/utils/modelForm.js frontend/src/stores/config.js frontend/src/views/SettingsView.vue frontend/src/utils/modelForm.test.js frontend/src/stores/config.test.js
git commit -m "feat(settings): 模型视觉能力开关与全局配置"
```

---

### Task 16: 文档、全量回归与人工验收

**Files:**
- Modify: `CONTEXT.md`
- Modify: `docs/specs/2026-10-29-chat-image-input.md`（状态行）
- Verify: 全仓测试与构建

**Interfaces:**
- Consumes: 前 15 个任务全部完成

- [ ] **Step 1: 更新术语与规格状态**

在 `CONTEXT.md` 末尾追加：

```markdown
## 聊天附件（chat attachment）
- 用户在聊天输入框粘贴、拖入或选择图片后，浏览器压缩并立即上传的图片对象。
- 存平台数据区 `data/users/{uid}/chat_uploads/{session_id}/`，不进工作区；先上传后绑定消息。
- 只接受 PNG / JPEG / WebP / GIF；单张 ≤5MB，单条 ≤4 张且合计 ≤10MB；未发送的 24 小时后清理。

## 视觉模型（vision model）
- `model_config.supports_vision = true` 的对话模型；没有默认模型时用全局 `llm.supports_vision`。
- 只有视觉模型能发送带图消息；能力位默认 false，必须在设置里显式打开。
```

把 `docs/specs/2026-10-29-chat-image-input.md` 的状态行改为：

```markdown
**日期**：2026-10-29  **来源**：需求访谈  **状态**：已实现
```

- [ ] **Step 2: 后端全量串行测试**

Run: `python -m pytest -q`
Expected: 全部 PASS。**不要加 `-n` 或 xdist。**

- [ ] **Step 3: 前端全量测试与构建**

Run: `cd frontend && npm run test -- --run`
Expected: 全部 PASS。

Run: `cd frontend && npm run build`
Expected: 构建成功，无 TypeScript/Vite 报错。

- [ ] **Step 4: 人工验收（按 spec §11）**

启动后端与前端（项目既有命令；前端 `cd frontend && npm run dev`，后端按项目 README/启动脚本）。然后逐条走：

1. 设置页 → 模型库：新建一个对话模型，打开「支持图片输入」并设为默认；列表出现「图片」标签。
2. 聊天页粘贴一张截图：出现缩略图与上传进度；输入「这是什么」发送；模型回答图片内容。
3. 纯图片发送：不输入文字，只发图；气泡只有图片；首条消息侧栏标题为「图片：<文件名>」。
4. 切到非视觉模型：附件条出现警示，发送按钮禁用；去掉附件后可发文字；切回视觉模型后同一批附件可发。
5. 刷新页面：历史气泡显示图片缩略图，点击可预览；模型请求里只内联最近 2 条带图 user 消息（用后端日志或 ScriptedLLM 验证）。
6. 执行中粘贴图片发送：气泡带图与「排队中」；工具边界后角标消失；刷新后排队气泡仍在。
7. 停掉 WS 后发送：气泡回滚、文字与附件回到输入框；带图消息报错后点「重试」能重新上传并重发。
8. 另一个账号访问附件/向他人会话上传：404；上传 SVG/6MB 文件：4xx。
9. 把某条未绑定附件的 `created_at` 改到 25 小时前，重启后端或等待清理循环：行与文件消失；已绑定附件不受影响。

- [ ] **Step 5: 提交**

```bash
git add CONTEXT.md docs/specs/2026-10-29-chat-image-input.md
git commit -m "docs(chat): 补充聊天附件与视觉模型术语"
```

---

## 任务依赖顺序

```
Task 1
  └─ Task 2
       └─ Task 3
            ├─ Task 4
            ├─ Task 5
            └─ Task 6
                 ├─ Task 7
                 │    └─ Task 9
                 ├─ Task 8
                 ├─ Task 10
                 └─ Task 13 (还需 11/12)
Task 11 → Task 12 → Task 13 → Task 14
Task 2 → Task 15
全部 → Task 16
```

按任务编号顺序执行即可满足依赖；Task 9 之前不要运行 `tests/test_ws.py` 的图片用例（协议尚未接通）。

## 自检记录

- 规格 §5/§6/§7/§8 的每个接口与文件都有对应任务。
- 所有测试步骤给出了完整测试代码；实现步骤给出了可直接落盘的代码。
- 类型/字段名在任务间保持一致：`attachment_ids`、`client_msg_id`、`attachments`、`supports_vision`、`effective_model`、`run_vision`。
- 明确保留纯文本链路：无附件时 `send` 仍发 `content` 字符串，`_history` 仍返回字符串。
