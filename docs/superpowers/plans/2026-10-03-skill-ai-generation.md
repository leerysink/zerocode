# 技能 AI 生成通道 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在既有「直链 / GitHub 仓库」两条技能安装通道之外，补上第三条——用一句话需求描述让账户默认模型写出一份 SKILL.md，用户预览可改、确认后入库。

**Architecture:** 两段式两端点，草稿态零副作用：`POST /api/skills/generate` 一次性调模型返草稿（不碰磁盘不碰 DB），`POST /api/skills/adopt` 收下（可能被编辑过的）正文硬校验后落盘落库。落盘落库那半截从现有 `install()` 抽成 `_persist_skill()` 两通道共用（孤儿清理与 409 语义只存在一处）。模型解析补一条「按用户」的回落 `resolve_model_for_user()`，与既有会话级 `resolve_model()` 共用同一份回落逻辑。前端在技能页加一张内联卡片，输入态 ↔ 草稿态两态切换。

**Tech Stack:** Python FastAPI、SQLAlchemy async（asyncpg）、PostgreSQL 16、pytest（asgi-lifespan + httpx ASGITransport）；Vue3、ant-design-vue 4、vitest（happy-dom）。

**Spec:** `docs/specs/2026-10-03-skill-ai-generation.md`（已批准）。术语见 `CONTEXT.md`。

## Global Constraints

- 前端**零新增依赖**：只用已装的 ant-design-vue / @ant-design/icons-vue；草稿编辑区用 `a-textarea`，不引 Monaco/CodeMirror。
- 平台库表结构变更一律落 `changelog/V<日期>_<序号>_<名>.sql`，`IF NOT EXISTS` 幂等守卫，由 `app/db.py::run_migrations` 自动应用（项目规则 `question.md`）。
- 越权一律 404（不区分 403）；`/api/skills/*` 非白名单，全局 guard 已要求会话——**不新造鉴权分支**。
- 判定「生成型技能」一律看 `source_type='ai'`，**不是** `source_url`；`source_url='ai://generated'` 只是满足 `NOT NULL` 的占位哨兵，永不发起请求、不过 SSRF 预检。
- 技能目录对 Agent **依旧只读**：本通道是平台侧写入，不改 ADR-0004、不改 `<available_skills>` 注入链路。
- 校验时机分区：生成端只警告（200 + `warnings`），安装端硬拦（400）。
- 错误码按责任方分：输入类 400 / 未配模型 400 / 上游超时·异常·空返 502 / 草稿不规范 200+warnings / adopt 正文不合格 400 / 撞 `(owner,slug)` 唯一约束 409。
- `name`/`description` 在 adopt 端**一律从正文服务端重新解析**，不信任前端传值（这两个字段直接进列表与 Agent 上下文）。
- LLM 调用姿势只有一种：`llm.chat_stream(messages, tools=[])` 聚合 `content_delta` + `done.message.content` 兜底，与 `app/api/config_api.py::test_llm` 同款，不发明第二种。
- 边界测试优先于主流程（项目规则 `boundary-testing.md`）：每个分支落地当轮补齐边界用例，用例名说明边界条件。
- 远端写操作（`git push`）由用户决定时机；计划内 commit 仅本地。

---

## 文件结构

- Create `changelog/V20261003_01_user_skill_ai.sql` — `user_skill` 增可空列 `generation_prompt`
- Modify `app/skills/library.py` — 新增常量 `NAME_MAX = 200`（两通道共用的落库前守卫）
- Modify `app/skills/installer.py` — `_NAME_MAX` 改为引用 `library.NAME_MAX`，去掉平行的 200
- Modify `app/agent/model_resolver.py` — 抽出 `resolve_model_for_user(s, uid, cfg)`
- Create `app/skills/generator.py` — 提示词常量 + `build_llm` + `unwrap_fences` + `generate_skill_draft`（无 DB、无磁盘）
- Modify `app/api/skills.py` — 抽 `_persist_skill()`；新增 `POST /generate`、`POST /adopt`；`_COLUMNS` 加 `generation_prompt`
- Create `frontend/src/utils/skillSource.js` — `source_type` → 中文短标签纯函数
- Modify `frontend/src/api/index.js` — `generateSkill` / `adoptSkill` 两个封装
- Modify `frontend/src/views/SkillsView.vue` — 「用 AI 生成」内联卡片（两态）+「来源」列走标签函数
- Test `tests/test_skills_generator.py` — generator 纯逻辑单元测试（围栏剥离 / warnings / 超时）
- Test `tests/test_skills_ai_api.py` — 两端点的 HTTP 面（主流程 + 13 条边界）
- Test `frontend/src/utils/skillSource.test.js` — 标签纯函数

**不需要改** `app/main.py`：`skills_api.router` 已注册，两个新端点在同一路由模块内。

---

## Task 1: 迁移 SQL + `NAME_MAX` 单一真源

**Files:**
- Create: `changelog/V20261003_01_user_skill_ai.sql`
- Create: `tests/test_skills_ai_api.py`
- Modify: `app/skills/library.py:19`（常量区末尾追加）
- Modify: `app/skills/installer.py:23-24,30`

**Interfaces:**
- Consumes: 无（地基任务）
- Produces:
  - `user_skill.generation_prompt`（`TEXT`，可空，老行恒 NULL）
  - `app.skills.library.NAME_MAX: int = 200`
  - `tests/test_skills_ai_api.py` 的 `_skills_data_dir` autouse fixture（把技能母本落 tmp）

- [ ] **Step 1: 写迁移文件**

创建 `changelog/V20261003_01_user_skill_ai.sql`：

```sql
-- changelog/V20261003_01_user_skill_ai.sql
-- 日期：2026-10-03  用途：技能 AI 生成通道——user_skill 增列 generation_prompt
--                    （spec docs/specs/2026-10-03-skill-ai-generation.md §5.2）
-- 影响：user_skill 增一个可空列；老行恒 NULL 即「非 AI 生成」，无数据回填。
-- 幂等：IF NOT EXISTS 守卫，可在存量库安全重放（对齐 V20260926_01_user_skill.sql 写法）。
-- source_type 增第三个取值 'ai'（列是 VARCHAR(20)，取值变化不需要 DDL）；
-- source_url 对生成型技能填占位哨兵 'ai://generated'，仅满足 NOT NULL，永不发起请求。

ALTER TABLE user_skill ADD COLUMN IF NOT EXISTS generation_prompt TEXT;
```

- [ ] **Step 2: 写第一个失败测试（列存在性 + 可空）**

创建 `tests/test_skills_ai_api.py`：

```python
# tests/test_skills_ai_api.py
"""技能 AI 生成通道的 HTTP 面（spec docs/specs/2026-10-03-skill-ai-generation.md §7）。

接缝＝HTTP API：generate 零副作用、adopt 硬校验落盘落库、错误分区码表、13 条边界。
LLM 用 monkeypatch 换掉 app.skills.generator.OpenAICompatLLM（对齐 tests/test_config.py
既有惯例，不新增 app.state 字段）。
"""
import pytest
from pathlib import Path

from sqlalchemy import text

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _skills_data_dir(tmp_path, monkeypatch):
    """技能母本落 tmp，绝不写仓库 data/。data_dir() 每次现读 env，故作用域内生效。"""
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path / "data"))


async def test_migration_adds_nullable_generation_prompt_column(client):
    """列必须是 text 且可空：老行不回填，NOT NULL 会让存量库迁移直接失败。"""
    async with client.app.state.session_factory() as s:
        row = (await s.execute(text(
            "SELECT data_type, is_nullable FROM information_schema.columns "
            "WHERE table_name='user_skill' AND column_name='generation_prompt'"))).first()
    assert row is not None, "user_skill.generation_prompt 列不存在（迁移未应用）"
    assert row[0] == "text"
    assert row[1] == "YES"
```

- [ ] **Step 3: 跑测试确认失败**

Run: `python -m pytest tests/test_skills_ai_api.py::test_migration_adds_nullable_generation_prompt_column -v`
Expected: FAIL — `AssertionError: user_skill.generation_prompt 列不存在（迁移未应用）`

- [ ] **Step 4: 确认迁移被自动应用后测试转绿**

`run_migrations` 在 `test_engine` fixture 里对整个 `changelog/` 目录扫描应用，新增文件无需登记。

Run: `python -m pytest tests/test_skills_ai_api.py -v`
Expected: PASS（1 passed）

若仍 FAIL 且报列不存在：检查文件名是否匹配 `V<8位日期>_<2位序号>_<主题>.sql`（`app/db.py::run_migrations` 按此排序），序号 `01` 是 2026-10-03 当天第一份。

- [ ] **Step 5: 给 library 加 `NAME_MAX`，installer 改引它**

`app/skills/library.py` 常量区（`SLUG_MAX_LEN` 那行之后）追加一行：

```python
SLUG_MAX_LEN = 80             # slug 上限，给 next_free_slug 的 -N 后缀留空间（列宽 VARCHAR(200)）
NAME_MAX = 200          # user_skill.name VARCHAR(200)：落库前的唯一真源，两通道共用（AI 生成 spec §5.5）
```

`app/skills/installer.py` 的 import 改为把 `NAME_MAX` 一并带进来：

```python
from app.skills.library import (TOTAL_MAX_BYTES, FILE_COUNT_MAX, MAX_FILE_BYTES, NAME_MAX,
                                SkillError, check_size_budget, parse_skill_md)
```

同文件第 30 行的 `_NAME_MAX = 200   # user_skill.name VARCHAR(200)` 整行替换为：

```python
_NAME_MAX = NAME_MAX   # 单一真源：library.NAME_MAX（AI 生成通道在落库前也用它守卫）
```

`installer.py` 里两处 `if len(name) > _NAME_MAX:` 的用法与文案**一字不改**（`f"技能 name 超过 {_NAME_MAX} 字符上限"`）。

- [ ] **Step 6: 写 installer 常量收敛的边界测试**

在 `tests/test_skills_ai_api.py` 末尾追加：

```python
async def test_name_max_is_single_source_of_truth():
    """installer 不得再自己写一个 200：两通道共用 library.NAME_MAX，否则改列宽要改两处。"""
    from app.skills import installer
    from app.skills.library import NAME_MAX
    assert NAME_MAX == 200
    assert installer._NAME_MAX is NAME_MAX
```

- [ ] **Step 7: 跑 installer 回归护栏**

Run: `python -m pytest tests/test_skills_ai_api.py tests/test_skills_installer.py tests/test_skills_installer_github.py tests/test_skills_api.py -q`
Expected: PASS（全绿；`_NAME_MAX` 改引常量后超长 name 的既有 400 用例仍过）

若报 `ModuleNotFoundError: tests/test_skills_installer_github.py`：用 `Glob tests/test_skills_installer*.py` 取实际文件名再跑。

- [ ] **Step 8: Commit**

先把中文提交信息写进临时文件（PowerShell 控制台是 GBK，`git commit -m "中文"` 会乱码）：

`.qoder-tmp/_commit_skill_ai_t1.txt`：

```
feat(skills): user_skill 增列 generation_prompt + NAME_MAX 收敛单一真源

- changelog/V20261003_01_user_skill_ai.sql：可空 TEXT 列，IF NOT EXISTS 幂等
- library.NAME_MAX=200 成为落库前 name 长度守卫的唯一真源，installer._NAME_MAX 改引它
- tests/test_skills_ai_api.py：列可空性 + 常量收敛
```

Run:
```bash
git add changelog/V20261003_01_user_skill_ai.sql app/skills/library.py app/skills/installer.py tests/test_skills_ai_api.py
git commit -F .qoder-tmp/_commit_skill_ai_t1.txt
```

---

## Task 2: 模型解析抽出「按用户」的回落

**Files:**
- Modify: `app/agent/model_resolver.py`（整文件重写，32 行）
- Test: `tests/test_model_resolver.py`（既有回归护栏，不改）

**Interfaces:**
- Consumes: 无
- Produces:
  - `async resolve_model_for_user(s, uid: int | None, cfg: dict) -> dict`
    返回 `{"base_url": str|None, "api_key": str, "model": str|None, "source": "default"|"global"}`
    —— `s` 是**已打开的** AsyncSession（不是 session_factory），由调用方持有事务边界
  - `async resolve_model(sf, session_id: int, cfg: dict) -> dict`（签名与返回形状**完全不变**）

- [ ] **Step 1: 先跑既有回归护栏，确认基线绿**

Run: `python -m pytest tests/test_model_resolver.py tests/test_isolation_config_models.py -q`
Expected: PASS（重构前必须有绿基线，否则分不清是自己改坏还是本来就坏）

- [ ] **Step 2: 重写 `app/agent/model_resolver.py`**

整文件替换为：

```python
"""会话级模型解析（spec §4.4）：session.model_config_id → model_config 行；
无绑定/行已删 → 默认模型；无默认 → 全局 app_config llm.*（老部署零迁移可用）。

隔离（Ticket 007，行为关键链）：会话经 workspace 取 owner，绑定行与默认行都按
owner 过滤——别人的档案即使被旁路直绑也读不到（落回自己的默认）；
IS NOT DISTINCT FROM 兼容无主遗留行（老库未承接前 uid 为 NULL）。

resolve_model_for_user 是「按账号」那两级回落的唯一实现：会话链路经 resolve_model 调它，
技能 AI 生成链路（app/api/skills.py）在已知 uid 时直接调它——回落逻辑不写平行实现。"""
from sqlalchemy import text


async def resolve_model_for_user(s, uid, cfg: dict) -> dict:
    """owner 默认模型行 → 全局 app_config llm.*。s 必须是已打开的会话。"""
    row = (await s.execute(text(
        "SELECT base_url, api_key, model FROM model_config "
        "WHERE is_default AND owner_user_id IS NOT DISTINCT FROM :o "
        "ORDER BY id LIMIT 1"), {"o": uid})).first()
    if row:
        return {"base_url": row[0], "api_key": row[1] or "", "model": row[2], "source": "default"}
    return {"base_url": cfg.get("llm.base_url"), "api_key": cfg.get("llm.api_key") or "",
            "model": cfg.get("llm.model"), "source": "global"}


async def resolve_model(sf, session_id: int, cfg: dict) -> dict:
    async with sf() as s:
        uid = (await s.execute(text(
            "SELECT w.owner_user_id FROM chat_session cs "
            "JOIN workspace w ON w.id = cs.workspace_id WHERE cs.id=:i"),
            {"i": session_id})).scalar()
        mid = (await s.execute(text(
            "SELECT model_config_id FROM chat_session WHERE id=:i"), {"i": session_id})).scalar()
        if mid is not None:
            row = (await s.execute(text(
                "SELECT base_url, api_key, model FROM model_config "
                "WHERE id=:i AND owner_user_id IS NOT DISTINCT FROM :o"),
                {"i": mid, "o": uid})).first()
            if row:
                return {"base_url": row[0], "api_key": row[1] or "", "model": row[2], "source": "session"}
        return await resolve_model_for_user(s, uid, cfg)
```

- [ ] **Step 3: 跑回归护栏确认重构零行为变化**

Run: `python -m pytest tests/test_model_resolver.py tests/test_isolation_config_models.py -q`
Expected: PASS（与 Step 1 同样的绿）

- [ ] **Step 4: Commit**

`.qoder-tmp/_commit_skill_ai_t2.txt`：

```
refactor(agent): 抽出 resolve_model_for_user 供技能 AI 生成复用

「owner 默认行 → 全局 llm.*」两级回落此前只在 resolve_model 内部；AI 生成链路
已知 uid、没有 session，直接调会话级函数要凭空造一个 session_id。抽出后回落逻辑
只有一份，resolve_model 的签名与返回形状不变（含 source=session/default/global）。
```

Run:
```bash
git add app/agent/model_resolver.py
git commit -F .qoder-tmp/_commit_skill_ai_t2.txt
```

---

## Task 3: 从 `install()` 抽出 `_persist_skill()`

**Files:**
- Modify: `app/api/skills.py:22-23`（`_COLUMNS`）、`45-95`（`install()` 尾部抽出）
- Test: `tests/test_skills_api.py`（既有回归护栏，不改）

**Interfaces:**
- Consumes: `library.NAME_MAX`（Task 1）
- Produces:
  - `async _persist_skill(request: Request, uid, payload: dict, *, generation_prompt: str | None = None) -> dict`
    —— `payload` 形状：`{"name": str, "description": str, "files": dict[str, bytes], "source_url": str, "subpath": str|None, "source_type": str}`；返回技能行 dict（含 `generation_prompt`）
  - `_COLUMNS` 新增 `generation_prompt`（`GET /api/skills` 与 `PATCH /api/skills/{id}` 的响应形状各多一个可空字段）

- [ ] **Step 1: 跑既有回归护栏取绿基线**

Run: `python -m pytest tests/test_skills_api.py -q`
Expected: PASS

- [ ] **Step 2: `_COLUMNS` 加列**

`app/api/skills.py` 第 22~23 行替换为：

```python
_COLUMNS = ("id, slug, name, description, source_type, source_url, "
            "subpath, skill_dir, generation_prompt, enabled, created_at, updated_at")
```

- [ ] **Step 3: import 补 `NAME_MAX` 与 `library`**

`app/api/skills.py` 第 15~17 行替换为：

```python
from app.skills import generator, installer
from app.skills.library import (NAME_MAX, SkillError, delete_skill_dir, next_free_slug,
                                parse_skill_md, slugify, write_skill_dir)
```

（`generator` 在 Task 4 才创建——本步先只加 `NAME_MAX`、`parse_skill_md`，`generator` 的 import 留到 Task 5 一起加，避免本步跑不了测试。本步实际写成：）

```python
from app.skills import installer
from app.skills.library import (NAME_MAX, SkillError, delete_skill_dir, next_free_slug,
                                parse_skill_md, slugify, write_skill_dir)
```

- [ ] **Step 4: 抽出 `_persist_skill`，`install()` 尾部改调它**

把 `app/api/skills.py` 中从 `    # 逐用户解析空闲 slug（读与写分两个会话，避免磁盘 IO 期间挂着连接）`
（原第 67 行）到 `    return dict(row)`（原第 95 行）整段替换为一行：

```python
    return await _persist_skill(request, uid, payload)
```

然后在 `install()` 函数**之前**（`class PatchIn` 之后、`@router.get("")` 之前）插入：

```python
async def _persist_skill(request: Request, uid, payload: dict, *,
                         generation_prompt: str | None = None) -> dict:
    """母本落盘 + 技能行落库：install（远端取回）与 adopt（AI 草稿）两通道共用。

    抽出来的理由：孤儿目录清理是 001~003 审查轮盯出来的（Blocker #1），并发撞
    (owner, slug) 唯一约束的 409 + rmtree 语义必须只存在一处，复制一份最容易漏。

    name 超 DDL 列宽在这里拦成 400：否则 asyncpg 抛 StringDataRightTruncation
    （不是 IntegrityError），会走 except Exception 清完目录再原样上抛，冒成 500。
    """
    if len(payload["name"]) > NAME_MAX:
        raise HTTPException(400, f"技能 name 超过 {NAME_MAX} 字符上限")

    # 逐用户解析空闲 slug（读与写分两个会话，避免磁盘 IO 期间挂着连接）
    async with request.app.state.session_factory() as s:
        taken = {r[0] for r in (await s.execute(text(
            "SELECT slug FROM user_skill WHERE owner_user_id=:u"), {"u": uid})).all()}
    slug = next_free_slug(taken, slugify(payload["name"]))
    try:
        dest = write_skill_dir(uid, slug, payload["files"])
    except SkillError as e:
        raise HTTPException(400, str(e))

    try:
        async with request.app.state.session_factory() as s:
            row = (await s.execute(text(
                "INSERT INTO user_skill (owner_user_id, slug, name, description, "
                "source_type, source_url, subpath, skill_dir, generation_prompt) "
                "VALUES (:o,:sl,:n,:d,:st,:su,:sp,:sd,:gp) "
                f"RETURNING {_COLUMNS}"),
                {"o": uid, "sl": slug, "n": payload["name"], "d": payload["description"],
                 "st": payload["source_type"], "su": payload["source_url"],
                 "sp": payload["subpath"], "sd": str(dest),
                 "gp": generation_prompt})).mappings().first()
            await s.commit()
    except IntegrityError:
        shutil.rmtree(dest, ignore_errors=True)  # 撞唯一约束（并发竞态）→ 清半成品目录
        raise HTTPException(409, "技能 slug 冲突，请重试")
    except Exception:
        # 长度已在 installer 按 DDL 预校验，其余 DB 异常也不能留永久孤儿母本（审查 Blocker #1）
        shutil.rmtree(dest, ignore_errors=True)
        raise
    return dict(row)
```

- [ ] **Step 5: 跑回归护栏确认 install 行为零变化**

Run: `python -m pytest tests/test_skills_api.py tests/test_skills_installer.py -q`
Expected: PASS（含 409 冲突清孤儿、slug 后缀、磁盘母本字节一致等既有用例）

- [ ] **Step 6: 补「抽取没漏掉清理」的边界测试**

`tests/test_skills_ai_api.py` 末尾追加（这条测的是 install 通道，但守的是 `_persist_skill` 抽取本身）：

```python
async def test_persist_skill_rejects_overlong_name_as_400(client, monkeypatch):
    """name 超列宽必须是 400，不能冒成 500：asyncpg 的 StringDataRightTruncation
    不是 IntegrityError，会走 except Exception 清完目录再原样上抛。"""
    from app.api import skills as sapi

    # 必须是 async：install() 里是 `payload = await installer.fetch_skill_from_url(...)`，
    # 换成同步 lambda 会 await 一个 dict → TypeError 冒成 500，测试红得不是地方。
    async def fake_fetch(url, transport=None):
        return _payload_with_name("x" * 201)

    monkeypatch.setattr(sapi.installer, "fetch_skill_from_url", fake_fetch)
    r = await client.post("/api/skills/install", json={"url": "https://x.invalid/a/SKILL.md"})
    assert r.status_code == 400, r.text
    assert "200" in r.text
    # 零落盘：连技能根目录都不该建出来
    me = (await client.get("/api/auth/me")).json()
    from app.skills.library import skills_root
    assert not skills_root(me["id"]).exists()


def _payload_with_name(name: str) -> dict:
    return {"name": name, "description": "d", "files": {"SKILL.md": b"---\n---\n"},
            "source_url": "https://x.invalid/a/SKILL.md", "subpath": None, "source_type": "url"}
```

Run: `python -m pytest tests/test_skills_ai_api.py -q`
Expected: PASS

- [ ] **Step 7: Commit**

`.qoder-tmp/_commit_skill_ai_t3.txt`：

```
refactor(skills): 抽出 _persist_skill 供 AI 生成通道复用

install() 的落盘落库段（读已占 slug → next_free_slug → write_skill_dir → INSERT →
IntegrityError/其它异常时 rmtree 清孤儿）原样搬进 _persist_skill，响应形状不变。
新增 name 超列宽的前置 400 守卫：两通道共用一道，避免超长 name 在 INSERT 处
撞 asyncpg StringDataRightTruncation 冒成 500。
```

Run:
```bash
git add app/api/skills.py tests/test_skills_ai_api.py
git commit -F .qoder-tmp/_commit_skill_ai_t3.txt
```

---

## Task 4: `app/skills/generator.py` 纯逻辑

**Files:**
- Create: `app/skills/generator.py`
- Create: `tests/test_skills_generator.py`
- Modify: `docs/specs/2026-10-03-skill-ai-generation.md:105`（`_unwrap_fences` → `unwrap_fences`）

**Interfaces:**
- Consumes: `library.parse_skill_md`、`library.SkillError`、`agent.llm.OpenAICompatLLM`
- Produces:
  - `PROMPT_MAX: int = 4000`
  - `GENERATE_TIMEOUT_S: int = 60`
  - `SKILL_SYSTEM_PROMPT: str`
  - `build_llm(model_cfg: dict) -> OpenAICompatLLM`（读 `base_url`/`api_key`/`model` 三键）
  - `unwrap_fences(raw: str) -> str`
  - `async generate_skill_draft(llm, prompt: str, *, timeout: float = GENERATE_TIMEOUT_S) -> dict`
    返回 `{"name": str, "description": str, "content": str, "warnings": list[str]}`；
    上游异常/超时**原样上抛**（端点映射 502），草稿不规范**不抛**（转 warnings）

- [ ] **Step 1: 写失败测试（围栏剥离）**

创建 `tests/test_skills_generator.py`：

```python
# tests/test_skills_generator.py
"""generator 纯逻辑单元测试：围栏剥离、warnings 转换、超时上抛。

不走 HTTP、不碰 DB/磁盘——端点层的错误分区在 tests/test_skills_ai_api.py。
"""
import asyncio

import pytest

from app.skills.generator import (GENERATE_TIMEOUT_S, PROMPT_MAX, SKILL_SYSTEM_PROMPT,
                                  generate_skill_draft, unwrap_fences)

pytestmark = pytest.mark.asyncio


class FakeLLM:
    """一次性吐出固定 content 的假 LLM（形状对齐 OpenAICompatLLM.chat_stream）。"""

    def __init__(self, content: str):
        self._content = content
        self.received: list[list[dict]] = []

    async def chat_stream(self, messages, tools):
        self.received.append([dict(m) for m in messages])
        if self._content:
            yield {"type": "content_delta", "content": self._content}
        yield {"type": "done", "message": {"role": "assistant",
                                           "content": self._content or None,
                                           "tool_calls": None}}


GOOD_MD = ("---\n"
           "name: meeting-minutes\n"
           "description: 把会议记录整理成结构化周报；当用户提到会议纪要或周报时使用。\n"
           "---\n"
           "# 会议纪要\n按议题分组输出。\n")


def test_unwrap_fences_strips_markdown_fence():
    raw = "```markdown\n" + GOOD_MD + "```"
    assert unwrap_fences(raw) == GOOD_MD.strip()


def test_unwrap_fences_strips_bom_and_blank_lines():
    raw = "\ufeff\n\n\n" + GOOD_MD + "\n\n   \n"
    assert unwrap_fences(raw) == GOOD_MD.strip()


def test_unwrap_fences_keeps_inner_code_block():
    body = GOOD_MD.strip() + "\n\n```bash\necho hi\n```"
    assert unwrap_fences("```\n" + body + "\n```") == body


def test_unwrap_fences_on_clean_text_is_noop():
    assert unwrap_fences(GOOD_MD) == GOOD_MD.strip()


def test_unwrap_fences_handles_empty_and_none():
    assert unwrap_fences("") == ""
    assert unwrap_fences(None) == ""
    assert unwrap_fences("   \n ") == ""
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_skills_generator.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.skills.generator'`

- [ ] **Step 3: 写 `app/skills/generator.py`**

```python
"""技能 AI 生成：一句话描述 → SKILL.md 草稿（spec 2026-10-03-skill-ai-generation §5.3~§5.4）。

只做「描述原文 → 草稿文本」这一层纯逻辑：不碰 DB、不碰磁盘、不留任何状态。
草稿不合格**不抛错**，转成 warnings 交回前端——生成端拒了用户只能再花一次 token 重跑，
而绝大多数不规范（缺 description、整段被围栏包住）用户自己改两笔就能救回来。
上游异常与超时原样上抛，由端点映射 502（可重试语义）。
"""
import asyncio
import re

from app.agent.llm import OpenAICompatLLM
from app.skills.library import SkillError, parse_skill_md

PROMPT_MAX = 4000        # 描述输入上限（spec §5.5：超出 400）
GENERATE_TIMEOUT_S = 60  # 单次生成硬超时；到点抛 asyncio.TimeoutError

# 提示词契约（spec §5.4 六条硬约束，写死为常量，不做配置项）
SKILL_SYSTEM_PROMPT = """你是 Agent Skill 的作者。用户会给你一句话需求描述，你要产出一份 SKILL.md 文件全文。

硬约束：
1. 输出必须是 SKILL.md 文件全文，第一个字符即 `---`；不要任何解释文字、不要用 ``` 围栏包裹。
2. YAML frontmatter 恰好两个字段：`name`、`description`。
3. `name` 用小写英文 kebab-case（示例：`meeting-minutes`），不要用中文。
4. `description` 必须同时写清「这个技能做什么」与「什么时候该用它」——平台 Agent 只靠 name 和
   description 判断要不要展开这个技能，写含糊等于这个技能永远不会被用上。
5. 正文是给平台 Agent 读的指令：直接给步骤、约束与判定条件，不写客套话。
6. 遵循 Anthropic Agent Skills 开放标准。"""

# 最外层一对完整围栏：贪婪匹配保证命中**最后**那个收尾 ```，正文内部的代码块不受影响
_FENCE = re.compile(r"^```[^\n]*\n(.*)\n```\s*$", re.DOTALL)


def unwrap_fences(raw: str) -> str:
    """剥掉模型爱加的 ``` 围栏与 BOM/首尾空白。

    adopt 端点也用它：用户可能把整段带围栏的草稿粘回来，不剥就会把围栏写进母本、
    并让 parse_skill_md 报「缺少 YAML frontmatter」。
    """
    s = (raw or "").lstrip("\ufeff").strip()
    m = _FENCE.match(s)
    return m.group(1).strip() if m else s


def build_llm(model_cfg: dict):
    """按解析出的模型档案建客户端。测试 monkeypatch 本模块的 OpenAICompatLLM。"""
    return OpenAICompatLLM(model_cfg["base_url"], model_cfg["api_key"], model_cfg["model"])


async def _collect(llm, prompt: str) -> str:
    """一次性聚合流式输出：content_delta 拼接 + done.message.content 兜底。

    与 app/api/config_api.py::test_llm、compaction_service 同款姿势，不发明第二种。
    """
    parts: list[str] = []
    final: str | None = None
    async for ev in llm.chat_stream(
            [{"role": "system", "content": SKILL_SYSTEM_PROMPT},
             {"role": "user", "content": prompt}], tools=[]):
        if ev.get("type") == "content_delta":
            parts.append(ev.get("content") or "")
        elif ev.get("type") == "done":
            final = (ev.get("message") or {}).get("content")
    return "".join(parts) or (final or "")


async def generate_skill_draft(llm, prompt: str, *,
                               timeout: float = GENERATE_TIMEOUT_S) -> dict:
    """产出草稿 {name, description, content, warnings}。

    timeout 带默认值只为让边界用例传 0.05 秒配慢 fake，不必真等 60 秒。
    """
    raw = await asyncio.wait_for(_collect(llm, prompt), timeout=timeout)
    content = unwrap_fences(raw)
    warnings: list[str] = []
    name = ""
    description = ""
    try:
        name, description = parse_skill_md(content)
    except SkillError as e:
        # 生成端只警告不拒（spec §5.5）：草稿原样透传，前端标黄列出，用户改完再 adopt
        warnings.append(str(e))
    if name:
        try:
            name.encode("ascii")
        except UnicodeEncodeError:
            # 判据是「非 ASCII」而非「slug 退化成了什么」：比对最终 slug 要再拼一次
            # next_free_slug，把磁盘/DB 状态带进纯逻辑校验（spec §5.5）
            warnings.append("name 含非 ASCII 字符，安装后目录名可能退化成 skill")
    return {"name": name, "description": description,
            "content": content, "warnings": warnings}
```

- [ ] **Step 4: 跑测试确认围栏组转绿**

Run: `python -m pytest tests/test_skills_generator.py -q`
Expected: PASS（5 passed）

- [ ] **Step 5: 补 warnings / 超时 / 提示词契约的边界测试**

`tests/test_skills_generator.py` 末尾追加：

```python
async def test_generate_returns_clean_draft_without_warnings():
    d = await generate_skill_draft(FakeLLM(GOOD_MD), "帮我整理会议纪要")
    assert d["name"] == "meeting-minutes"
    assert "周报" in d["description"]
    assert d["content"] == GOOD_MD.strip()
    assert d["warnings"] == []


async def test_generate_sends_system_prompt_and_raw_description():
    """提示词契约：system 用常量、user 是描述原文不额外包装（spec §5.4）。"""
    llm = FakeLLM(GOOD_MD)
    await generate_skill_draft(llm, "  帮我把会议记录整理成周报  ")
    msgs = llm.received[0]
    assert [m["role"] for m in msgs] == ["system", "user"]
    assert msgs[0]["content"] == SKILL_SYSTEM_PROMPT
    assert msgs[1]["content"] == "  帮我把会议记录整理成周报  "


async def test_generate_warns_when_frontmatter_missing():
    """模型返 JSON 文本：200 + warning，content 原样透传（spec §7 边界5）。"""
    raw = '{"name": "x", "description": "y"}'
    d = await generate_skill_draft(FakeLLM(raw), "p")
    assert d["warnings"] and "frontmatter" in d["warnings"][0]
    assert d["content"] == raw
    assert d["name"] == "" and d["description"] == ""


async def test_generate_warns_when_description_missing():
    d = await generate_skill_draft(FakeLLM("---\nname: only-name\n---\nbody"), "p")
    assert d["warnings"] == ["frontmatter 缺少必填字段 description"]
    assert d["name"] == ""   # parse_skill_md 抛错时不返回半截结果


async def test_generate_warns_when_frontmatter_unclosed():
    d = await generate_skill_draft(FakeLLM("---\nname: x\ndescription: y\n# 正文"), "p")
    assert d["warnings"] == ["SKILL.md frontmatter 未闭合（缺少结束的 ---）"]


async def test_generate_warns_on_chinese_name_but_keeps_it():
    """中文 name 不拦，只告警（spec §7 边界8）；name 仍解析出来供前端摘要显示。"""
    raw = "---\nname: 会议纪要\ndescription: 整理会议纪要\n---\n正文"
    d = await generate_skill_draft(FakeLLM(raw), "p")
    assert d["name"] == "会议纪要"
    assert any("非 ASCII" in w for w in d["warnings"])


async def test_generate_warns_on_mixed_ascii_cjk_name():
    """中英混合同样命中退化告警：判据是 encode('ascii') 而非「全中文」。"""
    raw = "---\nname: meeting-纪要\ndescription: d\n---\n正文"
    d = await generate_skill_draft(FakeLLM(raw), "p")
    assert d["name"] == "meeting-纪要"
    assert any("非 ASCII" in w for w in d["warnings"])


async def test_generate_on_empty_model_output_yields_warning_not_crash():
    """空返回在纯逻辑层是一条 warning；判 502 是端点的责任（spec §7 边界4）。"""
    d = await generate_skill_draft(FakeLLM(""), "p")
    assert d["content"] == ""
    assert d["warnings"] == ["SKILL.md 缺少 YAML frontmatter（应以 --- 开头）"]


async def test_generate_uses_done_message_content_when_no_delta():
    """某些端点只在 done 里给全文、不发 delta：兜底路径必须生效。"""

    class DoneOnlyLLM:
        async def chat_stream(self, messages, tools):
            yield {"type": "done", "message": {"role": "assistant",
                                               "content": GOOD_MD, "tool_calls": None}}

    d = await generate_skill_draft(DoneOnlyLLM(), "p")
    assert d["name"] == "meeting-minutes"
    assert d["warnings"] == []


async def test_generate_propagates_upstream_error():
    """上游异常原样上抛，不在纯逻辑层吞成 warning（端点据此映射 502）。"""

    class BoomLLM:
        async def chat_stream(self, messages, tools):
            raise RuntimeError("connection reset")
            yield  # pragma: no cover

    with pytest.raises(RuntimeError, match="connection reset"):
        await generate_skill_draft(BoomLLM(), "p")


async def test_generate_timeout_propagates():
    """慢上游到点抛 asyncio.TimeoutError；timeout 参数可注入，用例不真等 60 秒。"""

    class SlowLLM:
        async def chat_stream(self, messages, tools):
            await asyncio.sleep(1)
            yield {"type": "content_delta", "content": GOOD_MD}

    with pytest.raises(asyncio.TimeoutError):
        await generate_skill_draft(SlowLLM(), "p", timeout=0.05)


def test_constants_are_pinned():
    """常量是对外契约的一部分（端点错误文案直接引用），改动必须是有意的。"""
    assert PROMPT_MAX == 4000
    assert GENERATE_TIMEOUT_S == 60
    assert SKILL_SYSTEM_PROMPT.startswith("你是 Agent Skill 的作者")
    for needle in ("kebab-case", "name", "description", "Anthropic Agent Skills", "```"):
        assert needle in SKILL_SYSTEM_PROMPT
```

- [ ] **Step 6: 跑全量单测**

Run: `python -m pytest tests/test_skills_generator.py -v`
Expected: PASS（17 passed：围栏组 5 + 追加组 12）

若 `test_generate_warns_when_description_missing` 报 warnings 内容不符：以 `library.parse_skill_md` 的**实际文案**为准（本计划里的期望值取自当前实现第 61 行），不要反过来改 library 的文案——既有 installer 测试断言过它。

- [ ] **Step 7: 同步 spec 里的函数名**

`docs/specs/2026-10-03-skill-ai-generation.md` 第 105 行：

```
def _unwrap_fences(raw: str) -> str            # 剥 ``` 围栏与 BOM/首尾空白
```

替换为：

```
def unwrap_fences(raw: str) -> str             # 剥 ``` 围栏与 BOM/首尾空白；adopt 端也复用
```

（提升为 public 的理由：adopt 要复用它兜住用户粘贴的围栏，私有名跨模块引用是坏味道。）

- [ ] **Step 8: Commit**

`.qoder-tmp/_commit_skill_ai_t4.txt`：

```
feat(skills): generator 纯逻辑——描述原文到 SKILL.md 草稿

- SKILL_SYSTEM_PROMPT 写死 spec §5.4 六条硬约束（含 name 用英文 kebab-case）
- unwrap_fences 剥最外层围栏/BOM/首尾空白，adopt 端复用（spec 同步改为 public）
- generate_skill_draft：不规范转 warnings 不抛，上游异常与超时原样上抛交端点映射 502
- timeout 可注入，边界用例不必真等 60 秒
```

Run:
```bash
git add app/skills/generator.py tests/test_skills_generator.py docs/specs/2026-10-03-skill-ai-generation.md
git commit -F .qoder-tmp/_commit_skill_ai_t4.txt
```

---

## Task 5: `POST /api/skills/generate`

**Files:**
- Modify: `app/api/skills.py`（import 区 + `install()` 之后新增端点）
- Test: `tests/test_skills_ai_api.py`

**Interfaces:**
- Consumes: `generator.build_llm` / `generator.generate_skill_draft` / `generator.PROMPT_MAX` / `generator.GENERATE_TIMEOUT_S`（Task 4）、`resolve_model_for_user`（Task 2）、`config_service.get_all`
- Produces:
  - `class GenerateIn(BaseModel): prompt: str`
  - `POST /api/skills/generate` → 200 `{name, description, content, warnings}`

- [ ] **Step 1: 写失败测试（主流程 + 零副作用）**

`tests/test_skills_ai_api.py` 顶部 import 区补：

```python
import asyncio

import pytest
from pathlib import Path

from sqlalchemy import text

pytestmark = pytest.mark.asyncio
```

文件内加公共夹具与假 LLM（放在 `_skills_data_dir` 之后、第一个测试之前）：

```python
DRAFT_MD = ("---\n"
            "name: meeting-minutes\n"
            "description: 把会议记录整理成结构化周报；当用户提到会议纪要或周报时使用。\n"
            "---\n"
            "# 会议纪要\n按议题分组输出。\n")


class FakeLLM:
    """换掉 generator.OpenAICompatLLM：构造签名与真类一致，输出可脚本化。"""

    script: list[str] = [DRAFT_MD]     # 类属性，用例按需覆写
    fail_with: Exception | None = None
    delay: float = 0.0
    built_with: list[dict] = []

    def __init__(self, base_url, api_key, model):
        type(self).built_with.append({"base_url": base_url, "api_key": api_key, "model": model})

    async def chat_stream(self, messages, tools):
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail_with is not None:
            raise self.fail_with
        content = type(self).script[0]
        if content:
            yield {"type": "content_delta", "content": content}
        yield {"type": "done", "message": {"role": "assistant",
                                           "content": content or None, "tool_calls": None}}


@pytest.fixture
def fake_llm(monkeypatch):
    """注入假 LLM 并复位类级脚本状态（用例之间不串味）。"""
    from app.skills import generator
    FakeLLM.script = [DRAFT_MD]
    FakeLLM.fail_with = None
    FakeLLM.delay = 0.0
    FakeLLM.built_with = []
    monkeypatch.setattr(generator, "OpenAICompatLLM", FakeLLM)
    return FakeLLM


async def _uid(client) -> int:
    return (await client.get("/api/auth/me")).json()["id"]


async def _skill_rows(client) -> list[dict]:
    async with client.app.state.session_factory() as s:
        return [dict(r) for r in (await s.execute(text(
            "SELECT id, slug, name, source_type, generation_prompt "
            "FROM user_skill ORDER BY id"))).mappings().all()]


async def test_generate_returns_draft_with_zero_side_effect(client, fake_llm):
    """主流程 + 边界1：草稿返回，且磁盘与 DB 一个字节都不多。"""
    from app.skills.library import skills_root
    before = await _skill_rows(client)
    r = await client.post("/api/skills/generate", json={"prompt": "帮我把会议记录整理成周报"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["name"] == "meeting-minutes"
    assert "周报" in d["description"]
    assert d["content"] == DRAFT_MD.strip()
    assert d["warnings"] == []
    assert await _skill_rows(client) == before
    assert not skills_root(await _uid(client)).exists()


async def test_generate_uses_account_default_model(client, fake_llm):
    """模型来自 resolve_model_for_user 的全局回落（无 model_config 默认行时）。"""
    r = await client.post("/api/skills/generate", json={"prompt": "p"})
    assert r.status_code == 200, r.text
    assert fake_llm.built_with == [{"base_url": "https://api.deepseek.com/v1",
                                    "api_key": "", "model": "deepseek-chat"}]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_skills_ai_api.py -q`
Expected: FAIL — 两条新用例 404（`{"detail":"Not Found"}`）

- [ ] **Step 3: 实现端点**

`app/api/skills.py` import 区改成（把 Task 3 Step 3 的版本补上 `generator` 与解析依赖）：

```python
import asyncio
import logging
import shutil

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.agent.model_resolver import resolve_model_for_user
from app.services import config_service
from app.skills import generator, installer
from app.skills.library import (NAME_MAX, SkillError, delete_skill_dir, next_free_slug,
                                parse_skill_md, slugify, write_skill_dir)
```

`class PatchIn` 之后加：

```python
class GenerateIn(BaseModel):
    prompt: str
```

在 `install()` 之后、`patch_skill()` 之前插入：

```python
@router.post("/generate")
async def generate(request: Request, body: GenerateIn):
    """用一句话描述让账户默认模型写一份 SKILL.md 草稿（spec §2）。

    零副作用：不碰磁盘、不碰 DB——用户在草稿态关掉页面，技能库里什么都不会多出来。
    错误按责任方分码：输入/未配模型 400，上游超时·异常·空返 502（可重试）。
    """
    uid = request.state.actor["id"]
    prompt = (body.prompt or "").strip()
    if not prompt:
        raise HTTPException(400, "请描述你想要的技能")
    if len(prompt) > generator.PROMPT_MAX:
        raise HTTPException(400, f"描述超过 {generator.PROMPT_MAX} 字符上限")

    async with request.app.state.session_factory() as s:
        cfg = await config_service.get_all(s, owner_user_id=uid)
        model_cfg = await resolve_model_for_user(s, uid, cfg)
    if not model_cfg.get("base_url") or not model_cfg.get("model"):
        # 全局种子恒存在，这里空只可能是用户自己把 llm.* 清了（spec §7 边界7）
        raise HTTPException(400, "未配置模型，请先到设置页配置")

    try:
        # timeout 在这里显式传：默认参数在函数定义时就绑定好了，改
        # generator.GENERATE_TIMEOUT_S 不重跑调用点不会生效（测试调小超时就依赖这点）。
        draft = await generator.generate_skill_draft(
            generator.build_llm(model_cfg), prompt, timeout=generator.GENERATE_TIMEOUT_S)
    except asyncio.TimeoutError as e:
        raise HTTPException(502, f"生成超时（{generator.GENERATE_TIMEOUT_S} 秒），请重试") from e
    except Exception as e:
        logger.warning("技能 AI 生成失败 uid=%s: %s", uid, e)
        raise HTTPException(502, f"模型调用失败: {type(e).__name__}") from e
    if not draft["content"].strip():
        raise HTTPException(502, "模型返回了空内容，请重试")
    return draft
```

- [ ] **Step 4: 跑测试确认转绿**

Run: `python -m pytest tests/test_skills_ai_api.py -q`
Expected: PASS

- [ ] **Step 5: 补输入类与上游故障的边界测试**

`tests/test_skills_ai_api.py` 末尾追加：

```python
@pytest.mark.parametrize("bad", ["", "   ", "\n\t ", None])
async def test_generate_rejects_blank_prompt_as_400(client, fake_llm, bad):
    """边界2：空串/纯空格/换行制表/字段缺失一律 400，且一次模型都不调。"""
    payload = {} if bad is None else {"prompt": bad}
    r = await client.post("/api/skills/generate", json=payload)
    assert r.status_code in (400, 422), r.text   # 缺字段由 pydantic 判 422
    assert fake_llm.built_with == []


async def test_generate_prompt_at_4000_chars_is_accepted(client, fake_llm):
    """边界2：恰好等于阈值放行（阈值本身不是错误）。"""
    from app.skills.generator import PROMPT_MAX
    r = await client.post("/api/skills/generate", json={"prompt": "a" * PROMPT_MAX})
    assert r.status_code == 200, r.text


async def test_generate_prompt_at_4001_chars_is_rejected(client, fake_llm):
    """边界2：阈值 +1 拒绝，且不调模型。"""
    from app.skills.generator import PROMPT_MAX
    r = await client.post("/api/skills/generate", json={"prompt": "a" * (PROMPT_MAX + 1)})
    assert r.status_code == 400, r.text
    assert str(PROMPT_MAX) in r.text
    assert fake_llm.built_with == []


async def test_generate_unwraps_fenced_model_output(client, fake_llm):
    """边界3：模型整段包 ```markdown 围栏 + BOM + 大量空行，剥完仍解析出 name。"""
    fake_llm.script = ["\ufeff\n\n```markdown\n" + DRAFT_MD + "```\n\n\n"]
    r = await client.post("/api/skills/generate", json={"prompt": "p"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["name"] == "meeting-minutes"
    assert d["warnings"] == []
    assert "```" not in d["content"]


async def test_generate_empty_model_output_is_502(client, fake_llm):
    """边界4：空返回是上游问题（502 可重试），不是草稿不规范（200+warnings）。"""
    fake_llm.script = [""]
    r = await client.post("/api/skills/generate", json={"prompt": "p"})
    assert r.status_code == 502, r.text
    assert "空内容" in r.text
    from app.skills.library import skills_root
    assert not skills_root(await _uid(client)).exists()


async def test_generate_whitespace_only_model_output_is_502(client, fake_llm):
    """边界4 变体：只有空白的返回同样判空。"""
    fake_llm.script = ["   \n\n  "]
    assert (await client.post("/api/skills/generate",
                              json={"prompt": "p"})).status_code == 502


async def test_generate_malformed_draft_returns_200_with_warnings(client, fake_llm):
    """边界5：草稿不规范**不失败**——200 + warnings，content 原样透传。"""
    fake_llm.script = ['{"name": "x"}']
    r = await client.post("/api/skills/generate", json={"prompt": "p"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["warnings"] and "frontmatter" in d["warnings"][0]
    assert d["content"] == '{"name": "x"}'


async def test_generate_upstream_exception_is_502_with_reason(client, fake_llm):
    """边界6：上游抛异常 → 502，detail 含异常类型（不泄露内部栈），零落盘。"""
    fake_llm.fail_with = RuntimeError("connection reset by peer")
    r = await client.post("/api/skills/generate", json={"prompt": "p"})
    assert r.status_code == 502, r.text
    assert "RuntimeError" in r.text
    from app.skills.library import skills_root
    assert not skills_root(await _uid(client)).exists()


async def test_generate_upstream_timeout_is_502(client, fake_llm, monkeypatch):
    """边界6：超时走专属文案；把 GENERATE_TIMEOUT_S 调小，用例不真等 60 秒。"""
    from app.skills import generator
    monkeypatch.setattr(generator, "GENERATE_TIMEOUT_S", 0.05)
    fake_llm.delay = 2.0
    r = await client.post("/api/skills/generate", json={"prompt": "p"})
    assert r.status_code == 502, r.text
    assert "超时" in r.text


async def test_generate_without_any_model_is_400(client, fake_llm):
    """边界7：全局种子里的 llm.* 被自己清空 → 400 且文案指向设置页，不是裸 500。"""
    saved = (await client.get("/api/config")).json()
    try:
        await client.put("/api/config", json={"llm.base_url": "", "llm.model": ""})
        r = await client.post("/api/skills/generate", json={"prompt": "p"})
        assert r.status_code == 400, r.text
        assert "未配置模型" in r.text
        assert fake_llm.built_with == []
    finally:
        await client.put("/api/config", json={
            "llm.base_url": saved.get("llm.base_url") or "",
            "llm.api_key": saved.get("llm.api_key") or "",
            "llm.model": saved.get("llm.model") or ""})


async def test_generate_requires_login(anon_client):
    """边界11：全局 guard 兜住，端点不新造鉴权分支。"""
    assert (await anon_client.post("/api/skills/generate",
                                   json={"prompt": "p"})).status_code == 401
```

- [ ] **Step 6: 跑全量并检查**

Run: `python -m pytest tests/test_skills_ai_api.py tests/test_skills_generator.py -v`
Expected: PASS（全部绿）

Run: `python -m pytest tests/test_config.py tests/test_model_resolver.py -q`
Expected: PASS（新 import 没碰坏配置面）

- [ ] **Step 7: Commit**

`.qoder-tmp/_commit_skill_ai_t5.txt`：

```
feat(skills): POST /api/skills/generate 生成 SKILL.md 草稿

零副作用端点：调账户默认模型（resolve_model_for_user）产草稿，不碰磁盘不碰 DB。
错误按责任方分码——输入类与未配模型 400，上游超时/异常/空返 502（可重试），
草稿不规范 200+warnings（生成端拒了用户只能再花一次 token 重跑）。
覆盖 11 条边界：空/恰 4000/4001 字符、围栏+BOM、空返、上游异常与超时、
清空 llm.* 后的未配模型、未登录 401。
```

Run:
```bash
git add app/api/skills.py tests/test_skills_ai_api.py
git commit -F .qoder-tmp/_commit_skill_ai_t5.txt
```

---

## Task 6: `POST /api/skills/adopt`

**Files:**
- Modify: `app/api/skills.py`（`class AdoptIn` + `adopt()` 端点 + `AI_SOURCE_URL` 常量）
- Test: `tests/test_skills_ai_api.py`

**Interfaces:**
- Consumes: `generator.unwrap_fences`（Task 4）、`library.parse_skill_md` / `library.SkillError`、`_persist_skill`（Task 3）
- Produces:
  - `AI_SOURCE_URL: str = "ai://generated"`（模块级常量，测试用 `sapi.AI_SOURCE_URL` 引用）
  - `class AdoptIn(BaseModel): content: str; prompt: str | None = None`
  - `POST /api/skills/adopt` → 201 技能行（与 install 同形状）

- [ ] **Step 1: 写失败测试（主流程 + 哨兵 + 不信任前端）**

`tests/test_skills_ai_api.py` 末尾追加：

```python
# ---- Task 6: adopt 端点 ----

async def test_adopt_main_flow_creates_skill_with_ai_source(client, fake_llm):
    """主流程：generate → adopt → 列表可见 source_type='ai'、generation_prompt=描述原文。
    母本 SKILL.md 字节级等于提交正文（不是 generate 返回的 content 的副本，是 adopt 正文）。"""
    from app.skills.library import skills_root
    gen = await client.post("/api/skills/generate", json={"prompt": "帮我整理会议纪要"})
    assert gen.status_code == 200
    draft_content = gen.json()["content"]
    r = await client.post("/api/skills/adopt", json={
        "content": draft_content, "prompt": "帮我整理会议纪要"})
    assert r.status_code == 201, r.text
    sk = r.json()
    assert sk["name"] == "meeting-minutes"
    assert sk["source_type"] == "ai"
    assert sk["source_url"] == "ai://generated"
    assert sk["subpath"] is None
    assert sk["generation_prompt"] == "帮我整理会议纪要"
    # 母本字节级等于提交正文
    md = Path(sk["skill_dir"]) / "SKILL.md"
    assert md.read_bytes() == draft_content.strip().encode("utf-8")
    # 列表可见
    rows = await _skill_rows(client)
    assert len(rows) == 1
    assert rows[0]["source_type"] == "ai"
    assert rows[0]["generation_prompt"] == "帮我整理会议纪要"


async def test_adopt_round_trip_with_prompt_preserved(client, fake_llm):
    """generate 返回体 + 前端原样回传 prompt：round-trip 不丢字段。"""
    gen = await client.post("/api/skills/generate", json={"prompt": "周报整理"})
    r = await client.post("/api/skills/adopt", json={
        "content": gen.json()["content"], "prompt": "周报整理"})
    assert r.status_code == 201
    assert r.json()["generation_prompt"] == "周报整理"


async def test_adopt_ignores_client_supplied_name_and_source(client, fake_llm):
    """不信任前端：body 塞 name/description/source_type，行值仍从正文重新解析。
    防止前端被篡改后可往 Agent 上下文注入脏 name。"""
    gen = await client.post("/api/skills/generate", json={"prompt": "p"})
    r = await client.post("/api/skills/adopt", json={
        "content": gen.json()["content"],
        "prompt": "p",
        "name": "INJECTED-NAME",
        "description": "INJECTED-DESC",
        "source_type": "url"})
    assert r.status_code == 201, r.text
    sk = r.json()
    assert sk["name"] == "meeting-minutes"   # 从正文解析，不是 INJECTED-NAME
    assert sk["source_type"] == "ai"         # 硬编码，不取前端值


async def test_adopt_strips_fences_from_pasted_content(client, fake_llm):
    """用户可能把整段带围栏的草稿粘回来：unwrap_fences 剥完再解析。"""
    fenced = "```markdown\n" + DRAFT_MD + "\n```"
    r = await client.post("/api/skills/adopt", json={"content": fenced, "prompt": "p"})
    assert r.status_code == 201, r.text
    assert r.json()["name"] == "meeting-minutes"
    # 母本不含围栏
    sk = r.json()
    md = Path(sk["skill_dir"]) / "SKILL.md"
    assert b"```" not in md.read_bytes()


@pytest.mark.parametrize("blank", ["", "   ", "\n\t", "```\n\n```"])
async def test_adopt_rejects_blank_content_as_400(client, fake_llm, blank):
    """边界：空正文/纯空白/剥完围栏后为空 → 400。"""
    r = await client.post("/api/skills/adopt", json={"content": blank, "prompt": "p"})
    assert r.status_code == 400, r.text


async def test_adopt_missing_frontmatter_is_400(client, fake_llm):
    """正文缺 frontmatter → 400 + 精确 detail（不是 500）。"""
    r = await client.post("/api/skills/adopt", json={
        "content": "# Just a heading\nSome body text", "prompt": "p"})
    assert r.status_code == 400, r.text
    assert "frontmatter" in r.text


async def test_adopt_unclosed_frontmatter_is_400(client, fake_llm):
    r = await client.post("/api/skills/adopt", json={
        "content": "---\nname: x\ndescription: y\n# 正文", "prompt": "p"})
    assert r.status_code == 400, r.text
    assert "未闭合" in r.text


@pytest.mark.parametrize("missing", ["name", "description"])
async def test_adopt_missing_required_field_is_400(client, fake_llm, missing):
    if missing == "name":
        content = "---\ndescription: only desc\n---\nbody"
    else:
        content = "---\nname: only-name\n---\nbody"
    r = await client.post("/api/skills/adopt", json={"content": content, "prompt": "p"})
    assert r.status_code == 400, r.text
    assert missing in r.text


async def test_adopt_chinese_name_warns_and_uses_skill_slug(client, fake_llm):
    """边界8：中文 name → adopt 后 slug 恰为 'skill'（slugify 退化）。"""
    content = "---\nname: 会议纪要\ndescription: 整理会议纪要\n---\n正文"
    r = await client.post("/api/skills/adopt", json={"content": content, "prompt": "p"})
    assert r.status_code == 201, r.text
    assert r.json()["slug"] == "skill"


async def test_adopt_same_name_three_times_gets_unique_slugs(client, fake_llm):
    """边界9：同名连装三次 → skill / skill-2 / skill-3 独立共存。"""
    content = "---\nname: skill\ndescription: d\n---\nbody"
    for _ in range(3):
        r = await client.post("/api/skills/adopt", json={"content": content, "prompt": "p"})
        assert r.status_code == 201, r.text
    rows = await _skill_rows(client)
    assert sorted(r["slug"] for r in rows) == ["skill", "skill-2", "skill-3"]
    # Windows 下 read_text() 必须显式 encoding="utf-8"（默认 gbk 会抛）
    from app.skills.library import skills_root
    uid = await _uid(client)
    for slug in ["skill", "skill-2", "skill-3"]:
        md = skills_root(uid) / slug / "SKILL.md"
        assert "name: skill" in md.read_text(encoding="utf-8")


async def test_adopt_oversize_content_is_400(client, fake_llm):
    """边界10：正文超 1MB → 400，且目录零残留。"""
    header = "---\nname: big\ndescription: big skill\n---\n"
    body = "x" * (1024 * 1024 + 1)
    r = await client.post("/api/skills/adopt", json={"content": header + body, "prompt": "p"})
    assert r.status_code == 400, r.text
    assert "1MB" in r.text
    from app.skills.library import skills_root
    uid = await _uid(client)
    root = skills_root(uid)
    assert (not root.exists()) or list(root.glob("*")) == []


async def test_adopt_name_at_exactly_200_chars_is_accepted(client, fake_llm):
    """边界10：name 恰 200 字符 → 201。"""
    content = f"---\nname: {'a' * 200}\ndescription: d\n---\nbody"
    r = await client.post("/api/skills/adopt", json={"content": content, "prompt": "p"})
    assert r.status_code == 201, r.text


async def test_adopt_name_over_200_chars_is_400(client, fake_llm):
    """边界10：name 超 200 → 400，detail 含 '200'，且目录零残留。"""
    content = f"---\nname: {'a' * 201}\ndescription: d\n---\nbody"
    r = await client.post("/api/skills/adopt", json={"content": content, "prompt": "p"})
    assert r.status_code == 400, r.text
    assert "200" in r.text
    from app.skills.library import skills_root
    uid = await _uid(client)
    root = skills_root(uid)
    assert (not root.exists()) or list(root.glob("*")) == []


async def test_adopt_requires_login(anon_client):
    """边界11：全局 guard 兜住。"""
    assert (await anon_client.post("/api/skills/adopt",
                                   json={"content": "x", "prompt": "p"})).status_code == 401


async def test_adopt_cross_account_isolation(two_auth_clients):
    """边界12：A 装的技能 B 看不到，PATCH/DELETE 404。"""
    alice, bob = two_auth_clients
    content = "---\nname: secret-skill\ndescription: d\n---\nbody"
    r = await alice.post("/api/skills/adopt", json={"content": content, "prompt": "p"})
    assert r.status_code == 201
    skill_id = r.json()["id"]
    # B 列表为空
    bob_list = (await bob.get("/api/skills")).json()
    assert bob_list == []
    # B PATCH/DELETE 404
    assert (await bob.patch(f"/api/skills/{skill_id}",
                            json={"enabled": False})).status_code == 404
    assert (await bob.delete(f"/api/skills/{skill_id}")).status_code == 404


async def test_adopt_slug_collision_is_409_with_orphan_cleanup(client, fake_llm, monkeypatch):
    """边界13：撞 (owner, slug) 唯一约束 → 409 且孤儿目录被清。
    monkeypatch next_free_slug 返回固定值（禁用 -N 后缀），
    预插一行幽灵行（skill_dir='' 绕开目录已存在分支）让 INSERT 撞唯一约束。"""
    from app.api import skills as sapi
    from app.skills.library import skills_root
    uid = await _uid(client)
    # 禁用 slug 后缀分配
    monkeypatch.setattr(sapi, "next_free_slug", lambda taken, base: base)
    # 预插幽灵行：skill_dir='' 让 write_skill_dir 的 dest.exists() 为 False
    async with client.app.state.session_factory() as s:
        await s.execute(text(
            "INSERT INTO user_skill (owner_user_id, slug, name, description, "
            "source_type, source_url, subpath, skill_dir) "
            "VALUES (:o, 'meeting-minutes', 'old', 'old', 'url', 'https://x', NULL, '')"),
            {"o": uid})
        await s.commit()
    content = DRAFT_MD  # name=meeting-minutes → slug=meeting-minutes → 撞
    r = await client.post("/api/skills/adopt", json={"content": content, "prompt": "p"})
    assert r.status_code == 409, r.text
    # 孤儿目录必须被清
    root = skills_root(uid)
    assert (not root.exists()) or list(root.glob("*")) == []


async def test_adopt_prompt_stored_raw_and_blank_stored_as_null(client, fake_llm):
    """prompt 存原文；纯空白存 NULL（不是空串）。"""
    gen = await client.post("/api/skills/generate", json={"prompt": "p"})
    content = gen.json()["content"]
    # 原文存储
    r1 = await client.post("/api/skills/adopt", json={"content": content, "prompt": "  周报  "})
    assert r1.status_code == 201
    assert r1.json()["generation_prompt"] == "  周报  "
    # 纯空白 → NULL
    r2 = await client.post("/api/skills/adopt", json={"content": content, "prompt": "   "})
    assert r2.status_code == 201
    assert r2.json()["generation_prompt"] is None
    # 缺字段 → NULL
    r3 = await client.post("/api/skills/adopt", json={"content": content})
    assert r3.status_code == 201
    assert r3.json()["generation_prompt"] is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_skills_ai_api.py -q`
Expected: FAIL — 新用例 404（`{"detail":"Not Found"}`）

- [ ] **Step 3: 实现 adopt 端点**

`app/api/skills.py` 在 `class GenerateIn` 之后加：

```python
class AdoptIn(BaseModel):
    content: str
    prompt: str | None = None


AI_SOURCE_URL = "ai://generated"   # 占位哨兵（spec §9）：满足 NOT NULL，不过 SSRF 预检
```

在 `generate()` 之后、`patch_skill()` 之前插入：

```python
@router.post("/adopt", status_code=201)
async def adopt(request: Request, body: AdoptIn):
    """把（可能被编辑过的）草稿正文落盘落库（spec §5.5）。

    name/description 一律从正文服务端重新解析——不信任前端传值（这两个字段
    直接进列表与 Agent 上下文）。content 走 unwrap_fences 兜住用户粘贴的围栏。
    """
    uid = request.state.actor["id"]
    content = generator.unwrap_fences(body.content)
    if not content.strip():
        raise HTTPException(400, "草稿内容为空")
    try:
        name, description = parse_skill_md(content)
    except SkillError as e:
        raise HTTPException(400, str(e)) from e
    payload = {"name": name, "description": description,
               "files": {"SKILL.md": content.encode("utf-8")},
               "source_url": AI_SOURCE_URL, "subpath": None, "source_type": "ai"}
    return await _persist_skill(request, uid, payload,
                                generation_prompt=(body.prompt or "").strip() or None)
```

- [ ] **Step 4: 跑测试确认转绿**

Run: `python -m pytest tests/test_skills_ai_api.py -v`
Expected: PASS（全部绿）

- [ ] **Step 5: 跑回归护栏**

Run: `python -m pytest tests/test_skills_api.py tests/test_skills_generator.py -q`
Expected: PASS（抽取 `_persist_skill` 后 install 通道不退化）

- [ ] **Step 6: Commit**

`.qoder-tmp/_commit_skill_ai_t6.txt`：

```
feat(skills): POST /api/skills/adopt 把 AI 草稿落盘落库

- name/description 一律从正文服务端重新解析（不信任前端传值）
- content 走 unwrap_fences 兜住用户粘贴的围栏
- source_url='ai://generated' 占位哨兵 + source_type='ai'
- 复用 _persist_skill（与 install 共用孤儿清理与 409 语义）
覆盖 18 条测试：主流程/round-trip/不信任前端/围栏剥离/空正文/
缺 frontmatter/中文 name/同名连装/超 1MB/超 200 字符/401/跨账号隔离/409 孤儿清理。
```

Run:
```bash
git add app/api/skills.py tests/test_skills_ai_api.py
git commit -F .qoder-tmp/_commit_skill_ai_t6.txt
```

---

## Task 7: `skillSource.js` + API 封装

**Files:**
- Create: `frontend/src/utils/skillSource.js`
- Create: `frontend/src/utils/skillSource.test.js`
- Modify: `frontend/src/api/index.js:128`（追加两行）

**Interfaces:**
- Consumes: 无
- Produces: `skillSourceLabel(sourceType: string) => string`

- [ ] **Step 1: 写失败测试**

创建 `frontend/src/utils/skillSource.test.js`：

```js
// @vitest-environment happy-dom
import { describe, expect, it } from 'vitest'
import { skillSourceLabel } from './skillSource'

describe('skillSourceLabel', () => {
  it('url → 直链', () => {
    expect(skillSourceLabel('url')).toBe('直链')
  })

  it('github → GitHub', () => {
    expect(skillSourceLabel('github')).toBe('GitHub')
  })

  it('ai → AI 生成', () => {
    expect(skillSourceLabel('ai')).toBe('AI 生成')
  })

  it('未知值原样显示', () => {
    expect(skillSourceLabel('future-type')).toBe('future-type')
  })

  it('空串显示 -', () => {
    expect(skillSourceLabel('')).toBe('-')
  })

  it('null/undefined 显示 -', () => {
    expect(skillSourceLabel(null)).toBe('-')
    expect(skillSourceLabel(undefined)).toBe('-')
  })

  it('前后空格不影响匹配', () => {
    expect(skillSourceLabel('  ai  ')).toBe('AI 生成')
  })

  it('原型链属性不命中（防 constructor/toString 误匹配）', () => {
    expect(skillSourceLabel('constructor')).toBe('constructor')
    expect(skillSourceLabel('toString')).toBe('toString')
  })
})
```

- [ ] **Step 2: 跑测试确认失败**

Run: `npx vitest run frontend/src/utils/skillSource.test.js`
Expected: FAIL — `Cannot find module './skillSource'`

- [ ] **Step 3: 写 `skillSource.js`**

```js
// source_type → 中文短标签（spec §5.7）：url→直链、github→GitHub、ai→AI 生成。
// 未知值原样显示（未来新增类型不必改前端）；空/null 显示 '-'。
// Object.create(null) 防 constructor/toString 命中原型链属性。
const LABELS = Object.assign(Object.create(null), {
  url: '直链',
  github: 'GitHub',
  ai: 'AI 生成',
})

export function skillSourceLabel(sourceType) {
  const key = typeof sourceType === 'string' ? sourceType.trim() : ''
  if (!key) return '-'
  return LABELS[key] ?? key
}
```

- [ ] **Step 4: 跑测试确认转绿**

Run: `npx vitest run frontend/src/utils/skillSource.test.js`
Expected: PASS（8 passed）

- [ ] **Step 5: API 封装**

`frontend/src/api/index.js` 第 128 行后追加：

```js
export const generateSkill = (data) => http.post('/skills/generate', data) // {prompt}
export const adoptSkill = (data) => http.post('/skills/adopt', data) // {content, prompt?}
```

- [ ] **Step 6: Commit**

```bash
git add frontend/src/utils/skillSource.js frontend/src/utils/skillSource.test.js frontend/src/api/index.js
git commit -m "feat(skills): skillSource 标签纯函数 + generateSkill/adoptSkill API 封装"
```

---

## Task 8: `SkillsView.vue` 两态卡片 + 组件测试

**Files:**
- Modify: `frontend/src/views/SkillsView.vue`
- Create: `frontend/src/views/SkillsView.test.js`
- Modify: `docs/specs/2026-10-03-skill-ai-generation.md:201,239,245`（一致性订正）

**Interfaces:**
- Consumes: `generateSkill` / `adoptSkill`（Task 7）、`skillSourceLabel`（Task 7）
- Produces: 两态卡片（输入态 ↔ 草稿态）+ 「来源」列走标签函数

- [ ] **Step 1: 修改 SkillsView.vue**

`frontend/src/views/SkillsView.vue` 做以下改动：

**import 区**（第 5~9 行）改为：

```js
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { message, Modal } from 'ant-design-vue'
import { ArrowLeftOutlined, DeleteOutlined, RobotOutlined } from '@ant-design/icons-vue'
import { adoptSkill, deleteSkill, generateSkill, installSkill, listSkills, patchSkill } from '../api'
import { skillSourceLabel } from '../utils/skillSource'
```

**columns 定义**（第 28 行）加 key：

```js
  { title: '来源', dataIndex: 'source_type', key: 'source', width: 90 },
```

**新增状态**（`const pendingPath = ref(null)` 之后）：

```js
// ---- AI 生成通道（spec §5.7）----
const prompt = ref('')
const draft = ref(null)          // generate 返回体 {name, description, content, warnings}
const draftContent = ref('')     // 用户可编辑的正文副本
const generating = ref(false)
const adopting = ref(false)
const draftDirty = computed(() => !!draft.value && draftContent.value !== draft.value.content)

function resetDraft() {
  draft.value = null
  draftContent.value = ''
}
```

**新增方法**（`finishInstall` 之后）：

```js
async function doGenerate() {
  if (generating.value) return
  const p = prompt.value.trim()
  if (!p) {
    message.warning('请描述你想要的技能')
    return
  }
  generating.value = true
  try {
    const { data } = await generateSkill({ prompt: p })
    draft.value = data
    draftContent.value = data.content
  } catch (e) {
    resetDraft()
    message.error(e?.response?.data?.detail || '生成失败，请重试')
  } finally {
    generating.value = false
  }
}

function regen() {
  if (draftDirty.value) {
    Modal.confirm({
      title: '重新生成会覆盖当前草稿',
      okText: '重新生成',
      cancelText: '继续编辑',
      onOk: doGenerate,
    })
  } else {
    doGenerate()
  }
}

async function doAdopt() {
  if (adopting.value) return
  adopting.value = true
  try {
    const { data } = await adoptSkill({ content: draftContent.value, prompt: prompt.value })
    resetDraft()
    finishInstall(data)
  } catch (e) {
    if (e?.response?.status === 409) {
      message.error('slug 冲突，请重试（可修改 name 中的非 ASCII 字符）')
    } else {
      message.error(e?.response?.data?.detail || '安装失败')
    }
  } finally {
    adopting.value = false
  }
}

function cancelDraft() {
  resetDraft()
  // 保留 prompt 文本，方便用户微调后重新生成
}
```

**`finishInstall` 函数**（第 92~98 行）不清 `prompt`（设计如此——保留描述文本）。

**模板**：在「从链接安装」卡片（`</a-card>` 第 168 行）之后、候选清单卡片之前，插入 AI 生成卡片：

```html
    <a-card class="zc-card zc-ai-card" title="用 AI 生成">
      <template v-if="!draft">
        <a-textarea
          v-model:value="prompt"
          class="zc-ai-prompt"
          placeholder="描述你想要的技能，例如：帮我把会议记录整理成结构化周报"
          :auto-size="{ minRows: 2, maxRows: 4 }" />
        <div class="zc-ai-actions">
          <a-button type="primary" :disabled="generating" :loading="generating"
                    @click="doGenerate()">
            <robot-outlined /> 生成
          </a-button>
        </div>
      </template>
      <template v-else>
        <a-alert v-if="draft.warnings && draft.warnings.length" type="warning" show-icon>
          <template #message>
            <ul style="margin:0;padding-left:16px">
              <li v-for="(w, i) in draft.warnings" :key="i">{{ w }}</li>
            </ul>
          </template>
        </a-alert>
        <p class="zc-ai-summary">
          <strong>{{ draft.name }}</strong> — {{ draft.description }}
        </p>
        <a-textarea
          v-model:value="draftContent"
          class="zc-ai-draft"
          :auto-size="{ minRows: 6, maxRows: 20 }" />
        <div class="zc-ai-actions">
          <a-button type="primary" :disabled="adopting" :loading="adopting"
                    @click="doAdopt()">安装</a-button>
          <a-button :disabled="generating" :loading="generating"
                    @click="regen()">重新生成</a-button>
          <a-button @click="cancelDraft()">取消</a-button>
        </div>
      </template>
    </a-card>
```

**「来源」列渲染**：在 `<template #bodyCell>` 区（第 188 行之后）加：

```html
        <template v-if="column.key === 'source'">
          {{ skillSourceLabel(record.source_type) }}
        </template>
```

**样式**（`</style>` 之前）追加：

```css
.zc-ai-prompt,
.zc-ai-draft {
  width: 100%;
}
.zc-ai-actions {
  display: flex;
  gap: 8px;
  margin-top: 8px;
}
.zc-ai-summary {
  margin: 8px 0 4px;
  font-size: 13px;
}
```

- [ ] **Step 2: 写组件测试**

创建 `frontend/src/views/SkillsView.test.js`：

```js
// @vitest-environment happy-dom
// SkillsView 两态卡片：输入态 → 草稿态 → 安装成功回输入态。
// 桩件方案对齐 AppsView.test.js（vi.mock antd，不挂真 Antd）。
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { h } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'

const mocks = vi.hoisted(() => ({
  api: {
    listSkills: vi.fn(() => Promise.resolve({ data: [] })),
    installSkill: vi.fn(),
    patchSkill: vi.fn(),
    deleteSkill: vi.fn(),
    generateSkill: vi.fn(),
    adoptSkill: vi.fn(),
  },
  antd: { message: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
    Modal: { confirm: vi.fn() } },
}))
vi.mock('../api', () => mocks.api)
vi.mock('ant-design-vue', () => mocks.antd)

import SkillsView from './SkillsView.vue'

const slot = (name) => ({
  name, props: ['disabled', 'title', 'type', 'size', 'loading'],
  render() {
    const attrs = { class: `stub-${name}` }
    if (this.disabled) attrs.disabled = ''
    return h(name === 'a-button' ? 'button' : 'div', attrs, this.$slots.default?.())
  },
})
const STUBS = {
  'a-card': { name: 'a-card', render() {
    return h('div', { class: 'stub-card' }, [
      this.$slots.title?.(), this.$slots.extra?.(), this.$slots.default?.()]) } },
  'a-button': slot('a-button'),
  'a-input': slot('a-input'),
  'a-textarea': { name: 'a-textarea', props: ['value'],
    render() { return h('textarea', { class: 'stub-textarea', value: this.value }) } },
  'a-alert': { name: 'a-alert', props: ['type'],
    render() { return h('div', { class: 'stub-alert' }, [this.$slots.message?.()]) } },
  'a-table': {
    name: 'a-table', props: ['columns', 'dataSource', 'loading', 'rowKey'],
    render() {
      const bodySlot = this.$slots.bodyCell
      const rows = (this.dataSource || []).map((rec) =>
        h('div', { class: 'stub-table-row' },
          (this.columns || []).map((col) =>
            h('span', { class: 'stub-table-cell' },
              bodySlot
                ? bodySlot({ column: col, record: rec })
                : String(rec[col.dataIndex] ?? '')))))
      return h('div', { class: 'stub-table' }, rows)
    },
  },
  'a-switch': slot('a-switch'),
  'a-empty': slot('a-empty'),
  RobotOutlined: { render() { return h('i', { class: 'stub-robot-icon' }) } },
  ArrowLeftOutlined: { render() { return h('i') } },
  DeleteOutlined: { render() { return h('i') } },
}

async function mountView() {
  const wrapper = mount(SkillsView, { global: { stubs: STUBS } })
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  vi.clearAllMocks()
  mocks.api.listSkills.mockResolvedValue({ data: [] })
})

describe('SkillsView AI 生成卡片', () => {
  it('初始显示输入态：textarea + 生成按钮', async () => {
    const wrapper = await mountView()
    const cards = wrapper.findAll('.stub-card')
    const aiCard = cards.find((c) => c.text().includes('用 AI 生成'))
    expect(aiCard.exists()).toBe(true)
    expect(aiCard.find('.stub-textarea').exists()).toBe(true)
    expect(aiCard.findAll('.stub-a-button').some((b) => b.text().includes('生成'))).toBe(true)
  })

  it('空描述点生成 → message.warning，不发请求', async () => {
    const wrapper = await mountView()
    const btns = wrapper.findAll('.stub-card')
      .find((c) => c.text().includes('用 AI 生成'))
      .findAll('.stub-a-button')
    const genBtn = btns.find((b) => b.text().includes('生成'))
    await genBtn.trigger('click')
    expect(mocks.antd.message.warning).toHaveBeenCalled()
    expect(mocks.api.generateSkill).not.toHaveBeenCalled()
  })

  it('生成成功 → 切到草稿态：显示 warnings + 摘要 + 编辑区 + 安装/重新生成/取消', async () => {
    mocks.api.generateSkill.mockResolvedValue({
      data: { name: 'meeting-minutes', description: '整理周报',
              content: '---\nname: meeting-minutes\ndescription: 整理周报\n---\nbody',
              warnings: ['name 含非 ASCII'] } })
    const wrapper = await mountView()
    // 输入描述
    const textarea = wrapper.find('.stub-textarea')
    await textarea.setValue('帮我整理会议纪要')
    // 点生成
    const aiCard = wrapper.findAll('.stub-card').find((c) => c.text().includes('用 AI 生成'))
    const genBtn = aiCard.findAll('.stub-a-button').find((b) => b.text().includes('生成'))
    await genBtn.trigger('click')
    await flushPromises()
    // 草稿态
    expect(aiCard.text()).toContain('meeting-minutes')
    expect(aiCard.text()).toContain('安装')
    expect(aiCard.text()).toContain('重新生成')
    expect(aiCard.text()).toContain('取消')
    expect(aiCard.find('.stub-alert').exists()).toBe(true)
  })

  it('在飞双击只发一次请求（generating 防重入）', async () => {
    let resolveGen
    mocks.api.generateSkill.mockReturnValue(new Promise((r) => { resolveGen = r }))
    const wrapper = await mountView()
    const textarea = wrapper.find('.stub-textarea')
    await textarea.setValue('描述')
    const aiCard = wrapper.findAll('.stub-card').find((c) => c.text().includes('用 AI 生成'))
    const genBtn = aiCard.findAll('.stub-a-button').find((b) => b.text().includes('生成'))
    // 同步连点两次
    await genBtn.trigger('click')
    await genBtn.trigger('click')
    expect(mocks.api.generateSkill).toHaveBeenCalledTimes(1)
    // 收尾
    resolveGen({ data: { name: 'x', description: 'd', content: '---\nname: x\ndescription: d\n---\nb', warnings: [] } })
    await flushPromises()
  })

  it('安装成功 → 回输入态 + toast + load()', async () => {
    mocks.api.generateSkill.mockResolvedValue({
      data: { name: 'x', description: 'd',
              content: '---\nname: x\ndescription: d\n---\nbody', warnings: [] } })
    mocks.api.adoptSkill.mockResolvedValue({
      data: { id: 1, name: 'x', slug: 'x', source_type: 'ai' } })
    const wrapper = await mountView()
    await wrapper.find('.stub-textarea').setValue('描述')
    const aiCard = wrapper.findAll('.stub-card').find((c) => c.text().includes('用 AI 生成'))
    await aiCard.findAll('.stub-a-button').find((b) => b.text().includes('生成')).trigger('click')
    await flushPromises()
    // 点安装
    await aiCard.findAll('.stub-a-button').find((b) => b.text().includes('安装')).trigger('click')
    await flushPromises()
    expect(mocks.antd.message.success).toHaveBeenCalled()
    expect(mocks.api.adoptSkill).toHaveBeenCalledTimes(1)
    // 回输入态
    expect(wrapper.find('.zc-ai-draft').exists()).toBe(false)
  })

  it('取消 → 回输入态，保留描述文本', async () => {
    mocks.api.generateSkill.mockResolvedValue({
      data: { name: 'x', description: 'd',
              content: '---\nname: x\ndescription: d\n---\nbody', warnings: [] } })
    const wrapper = await mountView()
    await wrapper.find('.stub-textarea').setValue('我的描述')
    const aiCard = wrapper.findAll('.stub-card').find((c) => c.text().includes('用 AI 生成'))
    await aiCard.findAll('.stub-a-button').find((b) => b.text().includes('生成')).trigger('click')
    await flushPromises()
    await aiCard.findAll('.stub-a-button').find((b) => b.text().includes('取消')).trigger('click')
    await flushPromises()
    // 回输入态
    expect(wrapper.find('.zc-ai-draft').exists()).toBe(false)
    // 描述保留
    expect(wrapper.find('.stub-textarea').element.value).toContain('我的描述')
  })

  it('生成失败 → 回输入态 + message.error', async () => {
    mocks.api.generateSkill.mockRejectedValue({ response: { data: { detail: '模型调用失败' } } })
    const wrapper = await mountView()
    await wrapper.find('.stub-textarea').setValue('描述')
    const aiCard = wrapper.findAll('.stub-card').find((c) => c.text().includes('用 AI 生成'))
    await aiCard.findAll('.stub-a-button').find((b) => b.text().includes('生成')).trigger('click')
    await flushPromises()
    expect(mocks.antd.message.error).toHaveBeenCalled()
    // 回输入态
    expect(wrapper.find('.zc-ai-draft').exists()).toBe(false)
  })

  it('「来源」列渲染：ai → AI 生成', async () => {
    mocks.api.listSkills.mockResolvedValue({
      data: [{ id: 1, name: 'x', description: 'd', source_type: 'ai', enabled: true }] })
    const wrapper = await mountView()
    expect(wrapper.text()).toContain('AI 生成')
  })

  it('草稿被改过时点重新生成 → Modal.confirm', async () => {
    mocks.api.generateSkill.mockResolvedValue({
      data: { name: 'x', description: 'd',
              content: '---\nname: x\ndescription: d\n---\nbody', warnings: [] } })
    const wrapper = await mountView()
    await wrapper.find('.stub-textarea').setValue('描述')
    const aiCard = wrapper.findAll('.stub-card').find((c) => c.text().includes('用 AI 生成'))
    await aiCard.findAll('.stub-a-button').find((b) => b.text().includes('生成')).trigger('click')
    await flushPromises()
    // 模拟编辑草稿
    const draftTextarea = wrapper.find('.zc-ai-draft')
    if (draftTextarea.exists()) {
      await draftTextarea.setValue('modified content')
    }
    await aiCard.findAll('.stub-a-button').find((b) => b.text().includes('重新生成')).trigger('click')
    expect(mocks.antd.Modal.confirm).toHaveBeenCalledTimes(1)
  })

  it('generate 返回 400 → message.error 显示 detail', async () => {
    mocks.api.generateSkill.mockRejectedValue({
      response: { data: { detail: '未配置模型，请先到设置页配置' } } })
    const wrapper = await mountView()
    await wrapper.find('.stub-textarea').setValue('描述')
    const aiCard = wrapper.findAll('.stub-card').find((c) => c.text().includes('用 AI 生成'))
    await aiCard.findAll('.stub-a-button').find((b) => b.text().includes('生成')).trigger('click')
    await flushPromises()
    expect(mocks.antd.message.error).toHaveBeenCalledWith('未配置模型，请先到设置页配置')
  })
})
```

- [ ] **Step 3: 跑 vitest 确认转绿**

Run: `npx vitest run frontend/src/views/SkillsView.test.js`
Expected: PASS（10 passed）

- [ ] **Step 4: 订正 spec 三处一致性**

`docs/specs/2026-10-03-skill-ai-generation.md` 第 201 行：

```
- 组件正确性按项目惯例走浏览器冒烟，vitest 只测纯函数。
```

替换为：

```
- 纯函数（`skillSource.js`）vitest 覆盖；组件两态切换走 `SkillsView.test.js` 桩件测试（对齐 `AppsView.test.js` 惯例），浏览器冒烟做最终验收。
```

第 239 行：

```
| 14 | 生成失败后前端状态 | 回输入态、草稿区不残留半截内容（归浏览器冒烟，不强求 vitest） |
```

替换为：

```
| 14 | 生成失败后前端状态 | 回输入态、草稿区不残留半截内容（`SkillsView.test.js` 覆盖） |
```

第 245 行：

```
**前端 vitest**：`skillSource.js`（`url`/`github`/`ai`/未知值/空串/null）。
```

替换为：

```
**前端 vitest**：`skillSource.js`（`url`/`github`/`ai`/未知值/空串/null）+ `SkillsView.test.js`（两态切换 / 防重入 / 失败回退 / 来源列渲染）。
```

- [ ] **Step 5: Commit**

```bash
git add frontend/src/views/SkillsView.vue frontend/src/views/SkillsView.test.js docs/specs/2026-10-03-skill-ai-generation.md
git commit -m "feat(skills): AI 生成两态卡片 + 组件测试 + spec 一致性订正"
```

---

## Task 9: 全量门禁

**Files:** 无新增

- [ ] **Step 1: 全量 pytest**

Run: `python -m pytest tests/ -q`
Expected: PASS（全绿，含既有回归护栏）

- [ ] **Step 2: 全量 vitest**

Run: `npx vitest run`
Expected: PASS（含 `skillSource.test.js` + `SkillsView.test.js`）

- [ ] **Step 3: 前端 build**

Run: `npm run build`
Expected: 无报错

- [ ] **Step 4: 浏览器冒烟六步**

1. 打开技能页 → 看到「从链接安装」+ 「用 AI 生成」两张卡片
2. 输入描述 → 点生成 → 草稿态出现（warnings 标黄、摘要行、编辑区可改）
3. 编辑正文 → 点安装 → toast「已安装技能：xxx」→ 列表出现「AI 生成」来源
4. 再输入描述 → 生成 → 改正文 → 点重新生成 → 弹确认框 → 确认 → 重新生成
5. 点取消 → 回输入态 → 描述文本保留
6. 打开 Agent 对话 → 确认新生成的技能出现在可用清单中

- [ ] **Step 5: 边界覆盖声明**

按 `boundary-testing.md` 收尾自检：

**已覆盖**（后端 14 条边界 → 测试名逐条对应）：
1. generate 无副作用 → `test_generate_returns_draft_with_zero_side_effect`
2. prompt 空/恰 4000/4001 → `test_generate_rejects_blank_prompt_as_400` / `_at_4000_` / `_at_4001_`
3. 围栏+BOM → `test_generate_unwraps_fenced_model_output` + `test_adopt_strips_fences_from_pasted_content`
4. 空返回 → `test_generate_empty_model_output_is_502`
5. 不规范草稿 → `test_generate_malformed_draft_returns_200_with_warnings` + `_missing_frontmatter_` + `_unclosed_` + `_missing_required_field_`
6. 上游异常/超时 → `test_generate_upstream_exception_is_502` + `_timeout_`
7. 无模型 → `test_generate_without_any_model_is_400`
8. 中文 name → `test_adopt_chinese_name_warns_and_uses_skill_slug`
9. 同名连装 → `test_adopt_same_name_three_times_gets_unique_slugs`
10. 超 1MB / 超 200 字符 → `test_adopt_oversize_content_is_400` + `_name_at_exactly_200_` + `_name_over_200_`
11. 未登录 → `test_generate_requires_login` + `test_adopt_requires_login`
12. 跨账号隔离 → `test_adopt_cross_account_isolation`
13. 唯一约束冲突 → `test_adopt_slug_collision_is_409_with_orphan_cleanup`
14. 前端失败回退 → `SkillsView.test.js` 的「生成失败 → 回输入态」

**未覆盖**：
- 浏览器冒烟六步（手工验证，不写自动化）。
- 前端 `draftDirty` 的 `Modal.confirm` 取消分支（桩件下 `onOk` 不触发，仅验证 confirm 被调用）。

- [ ] **Step 6: 最终提交**

```bash
git add -A
git commit -m "chore(skills): AI 生成通道全量门禁通过"
```

---

## 自审清单

- [ ] **Spec §7 十四条逐条对到测试名**：见 Step 5 覆盖声明。
- [ ] **占位符扫描**：全文无 TBD / TODO / "implement later" / "add appropriate error handling"。
- [ ] **跨 Task 签名一致性**：
  - `_persist_skill(request, uid, payload, *, generation_prompt)` — Task 3 定义、Task 5/6 调用，签名一致。
  - `generator.unwrap_fences` — Task 4 定义、Task 5/6 调用、spec 第 105 行已同步。
  - `generator.build_llm(model_cfg)` — Task 4 定义、Task 5 调用。
  - `AI_SOURCE_URL = "ai://generated"` — Task 6 定义、测试用 `sapi.AI_SOURCE_URL` 引用。
  - `skillSourceLabel(sourceType)` — Task 7 定义、Task 8 调用。
  - `generateSkill(data)` / `adoptSkill(data)` — Task 7 定义、Task 8 调用。
- [ ] **文件结构补全**：Part 1 文件结构缺 `Create frontend/src/views/SkillsView.test.js`，本 Task 8 已补。

