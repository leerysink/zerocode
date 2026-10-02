# Spec：技能的 AI 生成通道（skill ai generation）

日期：2026-10-03 ｜ 状态：已批准（用户确认设计全部通过，进入实施）
前置共识：ADR-0004（先登录后技能；技能账户级、存用户库、渐进披露）、`docs/specs/2026-09-26-user-skills.md`（技能本体，已交付）
术语：见 `CONTEXT.md`（技能 skill、控制台 console）

## 1. 问题陈述

账户级技能库目前只有**取回远端**这一类安装通道：`POST /api/skills/install` 吃一个 URL，
走 GitHub 仓库扫描或 SKILL.md 直链（`app/skills/installer.py`）。用户想让 Agent 具备一项
自己脑子里的技能时，必须先有一个公开仓库或直链——没有仓库就装不了。

本 spec 补上第二类通道：**用一句话需求描述，让平台侧模型直接写出一份 SKILL.md**，
用户预览、可改、确认后入库。它是既有安装链路的第三个入口（url / github / ai），
不改任何既有语义。

## 2. 方案概述

两段式，草稿态**零副作用**：

- `POST /api/skills/generate`：一次性调模型，返回草稿 `{name, description, content, warnings}`。
  **不碰磁盘、不碰 DB**——用户关掉页面什么都不脏。
- `POST /api/skills/adopt`：收下（可能被用户编辑过的）正文，硬校验 → 落盘 → 落库。

落盘落库这半截不复制、从 `install()` 里抽出来共用（见 §5.1）。前端在技能页加一张
内联卡片，输入态 ↔ 草稿态两态切换。

## 3. 用户故事

### 生成
1. 作为用户，我在技能页「用 AI 生成」卡片里写一句「帮我把会议记录整理成结构化周报」，
   点生成，等十几秒后看到一份完整的 SKILL.md 草稿（frontmatter + 正文）。
2. 作为用户，草稿里我可以直接编辑正文与 frontmatter，编辑后点安装，入库的就是我改完的版本。
3. 作为用户，模型返回的东西不合规范（缺 description、frontmatter 未闭合、整段被
   \`\`\` 围栏包住）时，生成**不失败**：草稿照旧返回并展示在编辑区，卡片顶部标黄列出
   具体问题；我自己改两笔就能救回来，不必再花一次 token 重跑。
4. 作为用户，我在草稿态点安装，若正文仍不合格才被拒（400），报错说明缺哪个字段。
5. 作为用户，生成中关掉页面 / 取消草稿，我的技能库里什么都不会多出来。
6. 作为用户，模型超时或报错时我收到「可重试」的明确提示，不留半成品目录。
7. 作为用户，我没配过任何模型时，报错直接告诉我「请先到设置页配置模型」，而不是一个裸异常。

### 落库与消费
8. 作为用户，AI 生成的技能在列表里来源显示为「AI 生成」，与「直链」「GitHub」并列；
   启用开关、删除、Agent 侧 `<available_skills>` 注入与既有技能完全一致（本通道不加特例）。
9. 作为用户，我的生成型技能行里保留着当初那句描述原文（`generation_prompt`），日后
   能回看这个技能是怎么来的。
10. 作为用户，我连续生成三个中文名技能时，slug 会退化成 `skill`/`skill-2`/`skill-3`
    （现有 slugify 剔非 ASCII 的既定行为）；这一点在草稿预览时就以 warning 告知，
    且提示词已要求模型用英文 kebab-case 命名，正常情况下撞不到。

## 4. 已定决策

| 决策 | 内容 | 理由 |
|---|---|---|
| 输入形态 | 一句话**需求描述**，模型从头创作 | 不是「粘贴现成素材转格式」——两个需求形态混在一个输入框里，中间长度必然判错 |
| 人在环 | 必须预览，可编辑，确认后才入库 | LLM 产物质量不可控，直接入库污染技能库 |
| 产物范围 | 只生成单个 `SKILL.md` | 与现有 `parse_skill_md` 校验、大小守卫完全复用；多文件需结构化输出，解析失败面变大 |
| 模型选择 | 用该账号的默认模型，不让用户挑 | 少一个控件；生成质量与聊天同源，不另立标准 |
| 等待形态 | 同步 HTTP 一次性返回（非流式） | 生成不属于任何会话，而现有 WS hub 按 session 分槽；为一句话产物另开一套流式协议不划算 |
| 校验时机 | 生成只警告（200 + warnings）、安装硬拦（400） | 生成端拒了用户只能重跑（再花 token）；安装端不拦则脏数据入库 |
| 通道架构 | 两段式两新端点 + 抽 `_persist_skill` 共用 | 见 §6 方案取舍 |
| 元数据存储 | 加 `generation_prompt TEXT` 可空列；`source_type='ai'`、`source_url='ai://generated'` 占位 | 不动 `source_url NOT NULL`，既有读面零影响；比把中文描述塞进 source_url 干净 |
| 名称约定 | 提示词要求模型输出英文 kebab-case `name` | 规避 slugify 剔非 ASCII 导致 slug 退化成 `skill`；退化仍发生时以 warning 兜底 |
| Agent 写面 | **不开**。技能目录对 Agent 依旧只读 | 生成是**平台侧**写入，不触碰 ADR-0004 的受控读面裁定；ADR-0004 本文不改 |

## 5. 实现要点

### 5.1 后端：从 install 抽出共用落盘

`app/api/skills.py` 现有 `install()` 的第 67~95 行（读已占 slug → `next_free_slug` →
`write_skill_dir` → INSERT → `IntegrityError`/其它异常时 `rmtree` 清孤儿目录）抽成：

```python
async def _persist_skill(request, uid, payload: dict, *, generation_prompt: str | None = None) -> dict
```

- `install()` 改为「取 payload → `_persist_skill`」，行为与响应形状**完全不变**。
- `adopt` 走同一个函数，多传一个 `generation_prompt`。
- 抽取的理由：那段孤儿清理是 001~003 审查轮盯出来的（Blocker #1），复制一份最容易漏；
  并发撞唯一约束的 409 + `rmtree` 语义必须只存在一处。
- INSERT 语句加 `generation_prompt` 列，`_COLUMNS` 同步加（列表接口自然带出，
  `GET /api/skills` 的响应形状多一个可空字段，前端表格不展示它）。

### 5.2 迁移 SQL（规则要求版本化）

`changelog/V20261003_01_user_skill_ai.sql`：

```sql
ALTER TABLE user_skill ADD COLUMN IF NOT EXISTS generation_prompt TEXT;
```

- 幂等靠 `IF NOT EXISTS`，可在存量库安全重放（对齐 `V20260926_01_user_skill.sql` 的写法）。
- 文件头按 changelog 惯例注释：日期 / 用途 / 影响。
- 无数据回填：老行 `generation_prompt` 恒 NULL，即「非 AI 生成」。

### 5.3 新模块 `app/skills/generator.py`（无 DB、无磁盘）

```python
SKILL_SYSTEM_PROMPT = ...                      # §5.4 的硬约束
PROMPT_MAX = 4000                              # 描述输入上限
GENERATE_TIMEOUT_S = 60

def build_llm(model_cfg: dict):                # 内部 new OpenAICompatLLM，供测试 monkeypatch
async def generate_skill_draft(llm, prompt: str) -> dict   # {name, description, content, warnings}
def _unwrap_fences(raw: str) -> str            # 剥 ``` 围栏与 BOM/首尾空白
```

- LLM 调用沿用 `llm.chat_stream(messages, tools=[])` 聚合 content，
  `parts` 增量拼接 + `done.message.content` 兜底 —— 与 `compaction_service`、
  `/api/config/test-llm` 完全同款，不发明第二种调用姿势。
- 外层 `asyncio.wait_for(..., GENERATE_TIMEOUT_S)`。
- 草稿解析用**现有** `parse_skill_md`，失败不抛、转成 warning（见 §5.5）。

### 5.4 提示词契约（写死为常量）

系统提示词必须包含：
1. 输出**必须是 SKILL.md 文件全文**，第一个字符即 `---`；不要任何解释文字、不要用 \`\`\` 围栏包裹。
2. frontmatter 恰好两个字段：`name`、`description`。
3. `name` 用小写英文 kebab-case（示例 `meeting-minutes`），不要用中文。
4. `description` 要同时写清「做什么」与「什么时候该用它」——Agent 侧只靠 name+description
   做渐进披露判断（`<available_skills>` 只索引不展开）。
5. 正文是给平台 Agent 读的指令，直接给步骤与约束，不写客套。
6. 遵循 Anthropic Agent Skills 开放标准。

用户消息即描述原文（`prompt`），不额外包装。

### 5.5 端点契约与错误分区

```python
class GenerateIn(BaseModel):
    prompt: str

class AdoptIn(BaseModel):
    content: str          # 用户可能已编辑过的 SKILL.md 全文
    prompt: str | None = None   # 描述原文，落 generation_prompt
```

| 端点 | 状态码 | 响应 |
|---|---|---|
| `POST /api/skills/generate` | 200 | `{name, description, content, warnings: string[]}` |
| `POST /api/skills/adopt` | 201 | 技能行（与 install 同形状） |

错误按**责任方**分码（测试断言具体错误码，不只断言「没抛异常」）：

| 情况 | 码 | detail |
|---|---|---|
| prompt 空 / 纯空格 / 超 `PROMPT_MAX` | 400 | 输入问题 |
| 无该账号默认模型且无全局 `llm.*` | 400 | 「未配置模型，请先到设置页配置」 |
| 上游超时 / 模型抛异常 / 返回空内容 | 502 | 含原因，可重试 |
| 草稿 frontmatter 不合格 / name 非 ASCII 会退化 slug | **200 + warnings** | 草稿照返 |
| adopt 正文不合格（走 `parse_skill_md`） | 400 | 缺哪个字段 |
| adopt 正文超 1MB / name 超 200 | 400 | 复用既有列宽与文件守卫 |
| adopt 撞 `(owner, slug)` 唯一约束 | 409 | 同 install，且孤儿目录必须被清 |

warnings 至少覆盖：frontmatter 各类解析失败、缺 name、缺 description、
name 含非 ASCII 字符（slug 可能退化）。

**退化 warning 的判据**：`name.encode("ascii")` 抛 `UnicodeEncodeError` 即告警（全中文与
中英混合两种形态一并覆盖），不去反推 `slugify` 的结果——比对最终 slug 要再拼一次
`next_free_slug`，把磁盘/DB 状态带进纯逻辑校验。

**adopt 的载荷构造（不信任前端传值）**：

```python
name, desc = parse_skill_md(body.content)          # SkillError → 400
payload = {
    "name": name, "description": desc,             # 服务端重新解析，不取 body.name
    "files": {"SKILL.md": body.content.encode("utf-8")},  # 文件名固定大写单文件
    "source_url": "ai://generated", "subpath": None, "source_type": "ai",
}
await _persist_skill(request, uid, payload, generation_prompt=body.prompt)
```

- `name`/`description` **一律从正文重新解析**：前端传来的字段可被篡改，而这两个值直接进
  列表与 `<available_skills>` 注入，脏了就污染 Agent 上下文。
- `content` 走 `write_skill_dir` 的现成 1MB 单文件守卫；`name` 长度走 `_persist_skill` 里新加的
  `library.NAME_MAX`（200，`user_skill.name VARCHAR(200)`）预校验——两条通道共用一道守卫，
  否则超长 name 会在 INSERT 处撞成 asyncpg `StringDataRightTruncation`（非 IntegrityError）而冒成 500。
  `installer._NAME_MAX` 改为引用同一常量，不另立一个 200。

### 5.6 模型解析补一条「按用户」的回落

`app/agent/model_resolver.py` 把现有的「owner 默认行 → 全局 `app_config` `llm.*`」两级
抽成 `resolve_model_for_user(sf, uid, cfg)`，`resolve_model` 反查到 owner 后调用它。
回落逻辑只留一份，不写平行实现；`<uid>` 隔离语义与 `IS NOT DISTINCT FROM` 兜无主遗留行的
写法原样保留（Ticket 007 的关键链）。

### 5.7 前端

- `api/index.js`：`generateSkill(data)`、`adoptSkill(data)`。
- `SkillsView.vue` 新增内联卡片「用 AI 生成」，两态：
  - **输入态**：`a-textarea` + `生成` 按钮。Enter **不**提交（textarea 要能换行）；
    `generating` 在飞短路，照抄现有 `installing` 的防重入。
  - **草稿态**：`a-alert`（warnings，warning 级）→ 摘要行（解析出的 name/description）
    → 可编辑 `a-textarea`（正文全文）→ `安装` / `重新生成` / `取消`。
  - 草稿态点「重新生成」会覆盖编辑区：正文被改过时先 `Modal.confirm`，防手改被吞。
  - 取消：丢弃草稿回输入态，**保留描述文本**。
  - 安装成功：复用 `finishInstall` 的 toast + `load()`，回输入态，保留描述、清草稿。
- 新增 `frontend/src/utils/skillSource.js` 纯函数：`url→直链`、`github→GitHub`、
  `ai→AI 生成`，未知值原样显示，空/null 显示 `-`。「来源」列经它渲染。
- 组件正确性按项目惯例走浏览器冒烟，vitest 只测纯函数。

## 6. 方案取舍（被拒的两条）

- **单一 `/install` 端点 body 三选一**（`{url | content | prompt}`）：少一个端点，但 install
  的语义从「取回远端」漂成「什么都能塞」，url/content 互斥、subpath 只对 url 有效、
  candidates 只对 github 有效，守卫分支线性膨胀。省下的端点数全花在条件判断上。
- **让平台 Agent 生成**（起会话走 `write_file`，确认后搬进技能库）：表达力最强（能多轮改、
  能读参考文件），但 spec §3 故事 11 已把技能目录定成 **Agent 只读**，打通就要开新写面，
  与 ADR-0004 的受控读面裁定正面冲突；且一次生成要建会话、发消息、等工具循环，
  成本远高于一趟一次性调用。**本 spec 不改 ADR-0004。**

## 7. 测试决策

**后端接缝 = HTTP API**，新文件 `tests/test_skills_ai_api.py`，沿用 `tests/test_skills_api.py`
的 `client` + tmp `data_dir` 模式，LLM 用 `monkeypatch.setattr(generator, "OpenAICompatLLM", FakeLLM)`
注入（对齐 `tests/test_config.py` 既有惯例，不新增 `app.state` 字段）。

主流程：generate 返草稿 → adopt 装成功 → 列表出现 `source_type='ai'`、`generation_prompt`
等于描述原文、母本 `SKILL.md` 字节级等于提交正文。

边界（优先于主流程，逐条对到测试名）：

| # | 边界 | 断言 |
|---|---|---|
| 1 | generate 无副作用 | `skills_root(uid)` 目录不存在、`user_skill` 行数不变 |
| 2 | prompt 空串 / 纯空格 / 恰 4000 / 4001 字符 | 400 / 400 / 200 / 400 |
| 3 | 模型输出包 \`\`\`markdown 围栏、带 BOM、前后大量空行 | 剥完仍解析出 name |
| 4 | 模型返空字符串 | 502，零落盘 |
| 5 | 模型返 JSON 文本 / 缺 description / frontmatter 未闭合 | 200 + `warnings` 非空，`content` 原样透传 |
| 6 | 上游抛异常 / 超时（慢 fake） | 502 且 detail 含原因；目录零残留 |
| 7 | 无默认模型行且无全局 `llm.*` | 400「未配置模型」（经 `PUT /api/config` 把自己的 `llm.base_url/model` 置空造该环境，`finally` 还原种子） |
| 8 | 中文 name | warnings 含退化提示；adopt 后 slug 确为 `skill` |
| 9 | 同名连装三次 | `skill` / `skill-2` / `skill-3` 独立共存 |
| 10 | 正文超 1MB、name 恰 200 / 201 字符 | 400 / 201 / 400 |
| 11 | 未登录访问 generate / adopt | 全局 guard 401（不新造鉴权分支） |
| 12 | 跨账号：A 生成 B 看不到 | 列表不含；PATCH/DELETE 404（沿用既有语义） |
| 13 | adopt 撞 `(owner,slug)` 唯一约束 | 409 且孤儿目录被 `rmtree`（专盯 `_persist_skill` 抽取有无漏带清理） |
| 14 | 生成失败后前端状态 | 回输入态、草稿区不残留半截内容（归浏览器冒烟，不强求 vitest） |

**回归护栏**（抽取改动的代价必须还）：`tests/test_skills_api.py`、
`tests/test_skills_installer*.py`、`tests/test_model_resolver.py`、
`tests/test_isolation_config_models.py`、`tests/test_config.py` 全量绿。

**前端 vitest**：`skillSource.js`（`url`/`github`/`ai`/未知值/空串/null）。

**"完成"** = 新增测试全绿 + 现有全量测试全绿 + 浏览器冒烟（生成一个真技能 → 编辑 →
安装 → 列表显示「AI 生成」→ 开来说话确认 Agent 能读到 → 确认工作区文件树无技能文件）。

## 8. 明确不做

- **不做**已装技能的更新 / 一键重生成 —— 与 `2026-09-26-user-skills.md` §7「v1 没有更新按钮，
  想更新就重装一次」保持一致（重装产生 `-2` slug）。
- **不做**多文件生成（`references/`、`assets/`）。
- **不做**配额与限流。生成一次要烧真金白银的 token，且描述原文整段上送给用户自己配置的
  模型端点（可能是内部术语）—— 这是单机/小团队控制台，`/api/skills` 全局 guard 已要求登录。
  风险如实记录，被要求再补。
- **不做**把用户已有技能清单喂给模型（防重名 / 风格一致）：收益不明确，且 prompt 长度不可控。
- **不做**流式打字机输出。
- **不做**「Agent 边读工作区边写技能」的深链路（§6 方案三）。
- **不改** ADR-0004 与技能的 Agent 侧消费链路（`<available_skills>` 注入、受控读面）。

## 9. 补充说明

- `source_url='ai://generated'` 是**占位哨兵**而非 URL：它不过 SSRF 预检、不被任何通道 GET，
  仅用来满足 `NOT NULL` 并在「来源」列被 `skillSource.js` 折叠成「AI 生成」。
  判定生成型技能的依据是 `source_type`，**不是** `source_url`。
- 提示词里「name 用英文 kebab-case」与 §5.5 的退化 warning 是一对**双保险**：前者减少发生，
  后者保证发生时用户知情。不因这个坑去改 `slugify`（会牵动既有三个通道的落盘路径）。
- `generation_prompt` 存的是**描述原文**，不是模型输出；模型输出已经完整落在母本 `SKILL.md` 里。
- 术语已同步：`CONTEXT.md` 的「技能（skill）」条目补上「或由账户的默认模型按文本描述生成」。
