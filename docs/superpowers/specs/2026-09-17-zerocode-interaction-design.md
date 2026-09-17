# zerocode 设计文档 — 对话交互增强：任务列表 / 多问题提问 / 思考查看 / @文件指定 / 上下文压缩 / 模型选择

日期：2026-09-17　分支：feature/zerocode　前置：docs/superpowers/specs/2026-09-17-zerocode-design.md（一期）

## 1. 目标与非目标

### 目标
对话体验对标 DSH：
1. **任务列表**：Agent 可调用 `todo_write` 维护任务清单；前端顶部常驻任务栏 + 对话流内任务卡，状态实时同步、重开会话可恢复。
2. **提问增强**：`ask_user` 升级为多问题（每题带选项说明、推荐标注、多选开关），一次挂起、一次提交。
3. **思考查看**：流式转发 `reasoning_content`，前端折叠块展示（思考中动画 / 完成后回看），历史消息留痕可回看。
4. **文件创建等**：一期已有 `write_file`/`edit_file`（diff、写确认），本设计只做联动，不重复建设。
5. **文件指定**：输入框 `@` 引用工作区文件（补全下拉）；小文件（≤20KB）内容直接注入本轮上下文，大文件注入路径提示。
6. **上下文压缩**：字符量阈值自动触发 + 输入区手动按钮；LLM 摘要替换旧历史、保留最近若干条原文；摘要与水位落库。
7. **模型选择**：多模型库（设置页 CRUD），会话级切换（输入框下拉），按会话记忆。

### 非目标（YAGNI）
- 不做消息结构化 content-blocks 重构（方案 B 已否决）；消息表仍为「content 文本 + 旁路列」。
- 不做多用户/权限；不做文件拖拽上传；不做压缩摘要的人工编辑。
- 不保留旧 `ask_answer`/`ask_choice` 上行兼容（前后端同发布）。
- 不支持会话中途换模型重跑历史（切换只影响后续轮次）。

## 2. 总体架构

沿用一期「事件流 + 纯函数 applyEvent」架构，全部增量扩展：

- **工具层** `app/agent/tools.py` / `llm.py`：TOOLS 增 `todo_write`；`ask_user` 参数升级；`chat_stream` 解析 reasoning。
- **循环层** `app/agent/loop.py`：@展开、压缩触发与执行、todo 落库、reasoning 转发与留痕。
- **通道层** `app/hub.py` / `app/api/ws.py`：ask 挂起 payload 升级；上行 `ask_submit`/`compact`；下行 `reasoning_delta`/`todo_sync`/`compaction_done`。
- **API 层**：`/api/models` CRUD；`PATCH /api/sessions/{id}`（换模型）；会话详情带 `todo_state`。
- **前端** `frontend/src`：store 新事件分支；ThinkingBlock / TodoBar / TodoCard / AskUserCard 多题版 / ChatInput（@补全+模型下拉+压缩按钮）/ SettingsView 模型管理页签。

## 3. 数据模型（changelog/V20260917_02_interaction.sql，含回滚）

```sql
-- 模型库
CREATE TABLE model_config (
  id BIGSERIAL PRIMARY KEY,
  name VARCHAR(100) NOT NULL,          -- 显示名
  base_url VARCHAR(500) NOT NULL,
  api_key VARCHAR(500) NOT NULL DEFAULT '',
  model VARCHAR(200) NOT NULL,         -- 模型名（如 deepseek-chat）
  is_default BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- 同库唯一默认（部分唯一索引：仅约束 is_default=true 的行）
CREATE UNIQUE INDEX uk_model_default ON model_config (is_default) WHERE is_default;

-- 会话：模型 + 任务快照
ALTER TABLE chat_session
  ADD COLUMN model_config_id BIGINT REFERENCES model_config(id) ON DELETE SET NULL,
  ADD COLUMN todo_state JSONB NOT NULL DEFAULT '[]'::jsonb;

-- 消息：发给模型的展开文本 + 思考留痕
ALTER TABLE message
  ADD COLUMN context_content TEXT,
  ADD COLUMN reasoning TEXT;

-- 压缩摘要与水位
CREATE TABLE compaction (
  id BIGSERIAL PRIMARY KEY,
  session_id BIGINT NOT NULL REFERENCES chat_session(id) ON DELETE CASCADE,
  summary TEXT NOT NULL,
  up_to_seq BIGINT NOT NULL,           -- 已被摘要覆盖的最大 message.seq
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_compaction_session ON compaction (session_id, id DESC);

-- 回滚：DROP TABLE compaction; ALTER TABLE message DROP COLUMN reasoning, DROP COLUMN context_content;
--        ALTER TABLE chat_session DROP COLUMN todo_state, DROP COLUMN model_config_id;
--        DROP INDEX uk_model_default; DROP TABLE model_config;
```

语义约定：
- `message.context_content`：仅 user 消息在含 `@标记` 时写入；NULL=无展开，历史组装用 `COALESCE(context_content, content)`。
- `chat_session.todo_state`：每次 `todo_write` 全量覆盖；重开会话恢复任务栏。
- `compaction`：只取每会话最新一条生效；`up_to_seq` 之前的原文不参与组装。

## 4. Agent 工具协议（核心增量）

### 4.1 TOOLS 变更
- 新增 `todo_write`：
  ```json
  {"name":"todo_write","parameters":{"properties":{"todos":{"items":{"properties":{
    "content":{"type":"string"},"status":{"enum":["pending","in_progress","completed"]}},
    "required":["content","status"],"type":"object"},"type":"array"}},"required":["todos"]}}
  ```
  语义：全量替换（同 DSH）。执行：更新 `chat_session.todo_state` → 下发 `todo_sync` 事件 → 返回 `{"ok":true,"count":N}`。非法 status → ToolError。
- `ask_user` 参数升级：
  ```json
  {"name":"ask_user","parameters":{"properties":{"questions":{"items":{"properties":{
    "question":{"type":"string"},
    "options":{"items":{"properties":{"label":{"type":"string"},"description":{"type":"string"},
                                      "recommended":{"type":"boolean"}},"required":["label"]},"type":"array"},
    "multi_select":{"type":"boolean"}},"required":["question"],"type":"object"}},
   "required":["questions"]}}
  ```
  兼容约定：若模型误传旧参数 `question`/`options`（string），服务端包装为单题（稳健性，不写进提示词）。

### 4.2 LLM 层 reasoning（llm.py）
- `chat_stream` 逐 chunk 读取 `delta.reasoning_content`：累积并 `yield {"type":"reasoning_delta","content":...}`（有才发，端点不支持则整段无此事件）。
- `done` 事件 message 增加 `"reasoning": <累积文本 or None>`。
- `ScriptedLLM` 不变（测试直接注入 reasoning_delta 事件）。

### 4.3 循环层（loop.py）
- **reasoning 转发**：`reasoning_delta` → `send` 原样转发；assistant 落库时 `add_message(..., reasoning=final.get("reasoning"))`（`add_message` 签名扩展）。
- **@展开（发送时一次注入）**：
  - 解析 user_text 中 `@路径` 标记（正则 `@"[^"]+"|@\S+`，支持含空格的引号形式）；
  - 每个唯一路径 `resolve_in_workspace`：越界/不存在 → 注入 `[文件不可用: path]`；二进制/超 20KB → 注入 `[大文件 path（N KB），请用 read_file 分段读取]`；其余注入 `<file path="...">\n内容\n</file>`；
  - 落库 `content=原文`，`context_content=展开文本`；`_history` 组装与压缩统计均读 `COALESCE`。
- **压缩**：
  - 组装后估算上下文字符量（system + 各消息 COALESCE 文本 + tool_result JSON 长度）；> `agent.auto_compact_chars`（默认 50000，0=关闭自动）且自上次压缩水位（seq > 最新 `up_to_seq`，无水位则从头）新增消息 ≥ `agent.compact_min_new` → 触发；
  - 摘要调用复用当前会话 LLM（非流式收集），提示词要求产出：目标 / 关键决策 / 文件改动 / 待办 / 遗留问题；
  - 保留最近 `agent.compact_keep_recent`（默认 8）条消息原文不参与摘要；摘要与 `up_to_seq` 写入 `compaction` 表；下发 `compaction_done {summary}`；
  - 摘要在下次组装时以 `<context_summary>…</context_summary>` 拼接于 system 尾部；
  - 手动压缩：WS 上行 `{"type":"compact"}`；仅空闲可执行——执行期间将 session 加入 `hub.running`（复用 busy 语义挡住并发 user_message），结束移除；执行中下发 `compacting` 提示事件；失败仅发 error 不改状态；
- **todo 落库**：见 4.1；`cancel` 时不回滚 todo_state（保留现状快照）。

### 4.4 hub / WS（hub.py / ws.py）
- `hub.ask(session_id, questions, timeout)`：payload 变 `{"type":"ask_user","questions":[...]}`；解析结果对象改为 `{"answers":[{...}]}`（每题 `{answer}` 或 `{options:[...]}`，多选题为数组）。
- 上行新增：`ask_submit {"answers":[...]}`（整体一次提交）、`compact`；移除 `ask_answer`/`ask_choice` 处理分支。
- 下行新增事件：`reasoning_delta {content}`、`todo_sync {todos:[...]}`、`compaction_done {summary}`、`compacting`（可选提示）。
- `llm_factory` 改为按 run 查询 `chat_session.model_config_id` → `model_config` 行构造 `OpenAICompatLLM`；无行/被删 → 回退全局 `llm.base_url/api_key/model`（老部署零迁移可用）。

## 5. REST API

| 方法/路径 | 说明 |
|---|---|
| `GET /api/models` | 列表（api_key 掩码 `sk-***`） |
| `POST /api/models` | 新增；`is_default=true` 时清旧默认 |
| `PUT /api/models/{id}` | 更新；api_key 传掩码=不改原值 |
| `DELETE /api/models/{id}` | 删除（会话引用置 NULL，ON DELETE SET NULL） |
| `PATCH /api/sessions/{id}` | `{model_config_id}`；null=回退默认 |
| `GET /api/sessions/{id}` | 会话详情带 `todo_state` 与 `model_config_id`（模型下拉回显用） |
| `GET /api/sessions/{id}/messages` | 响应消息带 `reasoning` |

## 6. 前端界面

- **store（chat.js）**：state 增 `todoList/streamingThinking/models/currentModelId/compacting`；`applyEvent` 增分支：`reasoning_delta`（累积 `streamingThinking`）、`todo_sync`（覆盖 `todoList`）、`compaction_done`（消息流插提示条）、`compacting`；`message_done` 复位 `streamingThinking` 并把已聚合思考挂到该条 assistant 消息；`openSession` 从会话详情恢复 `todoList`；actions 增 `submitAsk(answers)`、`requestCompact()`、`setModel(id)`（REST PATCH）。
- **ThinkingBlock.vue**：流式（思考中…动画，自动展开）与历史（默认折叠「查看思考」）两种形态；对话流中 assistant 消息先渲染思考块再渲染正文。
- **TodoBar.vue**：输入区上方可折叠条，展示当前清单（状态图标/勾选完成），实时跟随 `todo_sync`。
- **TodoCard.vue**：对话流内 `todo_write` 的 tool 消息位置渲染当次清单快照。
- **AskUserCard.vue**：多题渲染；单选/多选（a-checkgroup）；推荐项徽标；`description` 次行说明；底部一次提交（未答完禁用）。
- **ChatInput.vue**：`@` 触发补全下拉（`/api/fs/tree` 展平 + 前端过滤，选中插入 `@"path" `）；上方模型下拉（当前会话模型，切换即 PATCH）；压缩按钮（运行中禁用）。
- **SettingsView.vue**：新增「模型管理」页签：表格（名称/模型/base_url/默认标记）+ 新增/编辑弹窗（api_key 不回显，留空=不改）+ 删除确认。
- 时间格式化、素雅低饱和风格、克制尺寸沿用一期约定。

## 7. 错误处理

| 场景 | 行为 |
|---|---|
| 端点无 reasoning_content | 无 reasoning_delta 事件，前端无感 |
| @ 文件越界/不存在/二进制/超限 | 注入占位说明文本，不阻断发送 |
| 自动压缩 LLM 调用失败 | 发 error 事件，继续用未压缩原文组装，不置会话 error 态 |
| 运行中点手动压缩 | 前端禁用；后端再校验 `hub.running` |
| 所选模型被删除 | run 时回退默认模型（无默认→回退全局 llm.*） |
| todo_write 非法 status | ToolError 返回模型，消息流不受影响 |
| ask 超时/取消 | 沿用一期：返回 cancelled，卡片前端清除 |

## 8. 测试策略（TDD，ScriptedLLM 注入）

- **后端 pytest**：
  - todo：合法写入落库+todo_sync；非法 status 报错；
  - ask_user：多题挂起→ask_submit 恢复；超时/取消；旧参数包装兼容；
  - reasoning：ScriptedLLM 注入 reasoning_delta → send 转发 + 落库；
  - @展开：小文件注入/大文件占位/越界占位/引号路径/去重；
  - 压缩：阈值触发、水位跳过、keep_recent 保留、摘要进 system、手动入口、busy 拒绝、失败不阻断；
  - 模型：factory 按会话解析/回退链；models API CRUD+掩码+默认唯一；PATCH session；
  - 迁移：V20260917_02 可重复执行于 init 之后（迁移器顺序）。
- **前端 vitest**：applyEvent 新事件分支；AskUserCard 多题提交；ChatInput @ 补全与插入；TodoBar 渲染；模型下拉 PATCH。
- **browser-skill 冒烟**：建模型→选模型→@文件提问→观察思考块/任务栏/工具块→手动压缩→重开会话验证恢复。

## 9. 配置项（app_config，改值即时生效）

| key | 默认 | 说明 |
|---|---|---|
| `agent.auto_compact_chars` | 50000 | 自动压缩阈值，0=关闭 |
| `agent.compact_keep_recent` | 8 | 压缩时保留原文条数 |
| `agent.compact_min_new` | 6 | 距上次压缩最少新增消息数 |
