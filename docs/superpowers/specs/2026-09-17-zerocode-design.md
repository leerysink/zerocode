# zerocode 设计文档 — 类 DSH 的 AI 编程助手（Vue3 + Python）

- 日期：2026-09-17
- 状态：已与用户确认设计方向（OpenAI 兼容接入 / 读写编辑文件 / 双向提问 / 右侧文件面板 / PostgreSQL 存储）
- 项目位置：`D:\project\zerocode`

## 1. 目标与非目标

### 目标
做一个本地单机 Web 应用，界面与交互对齐 DSH（DeepSeek Harness Web GUI）：

1. **工作区（目录）选择**：可浏览/输入本机目录，作为 Agent 的工作区，多工作区管理。
2. **目录内生成代码**：Agent 通过工具对工作区内文件进行读取、新建、改写、编辑。
3. **多轮对话**：会话按工作区分组、可命名、可恢复历史；流式输出。
4. **双向提问**：用户可随时向 AI 提问；Agent 执行中遇到不确定可主动弹出提问卡片，用户回答后继续执行。
5. **配置页**：管理 LLM（base_url / api_key / model）、执行参数（写入确认开关、提问超时）、查看 PG 连接状态。

### 非目标（YAGNI）
- 不执行 shell 命令（npm install、跑测试等不在本期范围）。
- 不做多人协作、登录鉴权（本地单用户）。
- 不做文件上传/附件、@ 文件引用（输入框仅纯文本）。
- 不做工作区之间的代码索引/向量化。

## 2. 总体架构

方案 A：**FastAPI 单体后端 + Vue3 SPA 前端**。

```
浏览器 (Vue3 SPA, :8082 dev)
   │  REST (/api/*)            WebSocket (/ws/chat/{session_id})
   ▼
FastAPI 单体 (:8100)
   ├─ api/        REST 路由（workspaces / sessions / messages / config / fs）
   ├─ agent/      Agent 循环（流式调用 LLM、工具调度、提问挂起/恢复）
   ├─ tools/      list_dir / read_file / write_file / edit_file / ask_user
   ├─ models/     SQLAlchemy 2 模型（asyncpg）
   └─ db/         连接池 + changelog/ SQL 迁移执行器
   ▼
PostgreSQL 16（Docker 专用容器，宿主端口 5433，库 zerocode）
```

- 端口：后端 `8100`，前端 Vite dev `8082`（均可在配置中改）。
- 前端构建产物可由后端静态托管（`/`），开发期走 Vite 代理 `/api` 与 `/ws`。

## 3. 数据模型（PostgreSQL，变更走 changelog/）

遵循全局规则：所有 DDL/DML 记录为 `changelog/V<YYYYMMDD>_<序号>_<说明>.sql`，后端启动时按文件名顺序执行未应用的迁移（表 `schema_migration` 记录已应用文件）。

| 表 | 关键字段 | 说明 |
|---|---|---|
| `workspace` | id, name, root_path(唯一), created_at | 工作区目录 |
| `chat_session` | id, workspace_id(FK), title, status(idle/running/error), created_at, updated_at | 会话，按工作区分组 |
| `message` | id, session_id(FK), role(user/assistant/tool/system), content, tool_name, tool_args(jsonb), tool_result(jsonb), seq, created_at | 对话与工具调用记录；seq 用于排序 |
| `file_change` | id, session_id, message_id(FK), path, action(create/edit), before_content, after_content, diff(unified diff 文本), created_at | 每次写入/编辑留痕，供前端 diff 展示与审计 |
| `app_config` | key(主键), value(jsonb), updated_at | LLM 配置、执行参数等 |
| `schema_migration` | filename(主键), applied_at | 迁移记录 |

初始数据（`app_config` 默认值）：
- `llm.base_url` = `https://api.deepseek.com/v1`（OpenAI 兼容）
- `llm.api_key` = 空（配置页填写，返回前端时脱敏为 `sk-***`）
- `llm.model` = `deepseek-chat`
- `agent.write_confirm` = `false`（开启后 write_file/edit_file 需用户在前端确认 diff）
- `agent.ask_timeout_seconds` = `0`（0 = 不限制等待用户回答）

## 4. Agent 循环与工具协议（核心）

### 4.1 执行链
1. 用户消息经 WS 上行 → 落库（role=user）。
2. 组装消息列表：system prompt（说明工作区根目录、工具用法、当前日期）+ 历史（近 N 条，N 可配，默认 40）+ 本轮。
3. 调用 OpenAI 兼容 `/chat/completions`（`stream=true`，`tools=[...]`）。
4. 流式增量通过 WS 下行（`type=token`）。
5. 若返回 tool_calls：逐个执行本地工具 → 结果落库（role=tool）→ 作为 tool 消息追加，回到第 3 步；最多连续 30 轮工具循环（可配）。
6. 无 tool_calls 且内容结束 → 本轮 assistant 消息落库，会话状态 idle。

### 4.2 工具集
| 工具 | 参数 | 行为 |
|---|---|---|
| `list_dir` | path（相对工作区，可空=根） | 返回目录树两层（名称/类型/大小） |
| `read_file` | path | 返回文本内容（>200KB 截断并提示） |
| `write_file` | path, content | 新建或整体覆盖；记录 file_change（diff） |
| `edit_file` | path, old_string, new_string | 精确唯一匹配替换；0 次或多次匹配即报错回传模型 |
| `ask_user` | question, options[]（可空） | 挂起本轮，WS 推提问卡片 |

### 4.3 双向提问（ask_user）
- Agent 调用 `ask_user` 后：当前轮挂起（内存态 + 会话状态标记），WS 下行 `type=ask_user`（含 question、options、超时）。
- 用户点选项或自由输入 → WS 上行 `answer` → 作为 tool 结果注入，继续 4.1 第 5 步。
- 超时 `agent.ask_timeout_seconds`，默认 0 = 无限等待；用户也可点"取消执行"终止本轮（会话回 idle）。

### 4.4 安全与确认
- 所有工具路径先 resolve 后校验前缀必须位于工作区 root 内，越界一律拒绝（结果回传模型并提示）。
- 二进制/超大文件：read_file 拒绝非文本扩展名与 >200KB 文件。
- `agent.write_confirm=true` 时：write_file/edit_file 先下行 `type=confirm`（含 diff），用户确认/拒绝后继续；拒绝则把"用户拒绝该修改"作为 tool 结果。
- 默认 `write_confirm=false`：直接写入但全部留痕 file_change。

## 5. REST / WS API

### REST（前缀 `/api`）
| 方法与路径 | 说明 |
|---|---|
| GET/POST/DELETE `/workspaces`、GET `/workspaces/{id}/sessions` | 工作区 CRUD 与分组查询 |
| POST `/fs/browse` | 服务端目录浏览（body: path 可空 → 返回盘符/子目录列表，仅目录） |
| POST `/sessions` | 建会话（workspace_id、title 可空=自动"新会话"） |
| GET `/sessions`、GET `/sessions/{id}/messages`、PATCH/DELETE `/sessions/{id}` | 会话列表（含相对时间）/消息/改名/删除 |
| GET/PUT `/config` | 读写 app_config（api_key 脱敏返回；PUT 支持部分键） |
| GET `/config/pg-status` | PG 连接与迁移状态检测 |
| GET `/fs/tree?workspace_id=` | 工作区文件树（右侧面板，忽略 .git/node_modules 等） |
| GET `/files/read?workspace_id=&path=`、PUT `/files/save` | 面板内查看/手动保存文件 |

### WS `/ws/chat/{session_id}`
- 上行：`{"type":"user_message","content":...}`、`{"type":"ask_answer","text":...}`、`{"type":"confirm","approved":bool}`、`{"type":"cancel"}`
- 下行：`token`（流式增量）、`tool_call`（名称+参数）、`tool_result`、`ask_user`、`confirm_request`、`message_done`、`error`、`status`（会话状态变化）
- 断线重连：前端指数退避重连，重连后拉取 `/sessions/{id}/messages` 增量（按 seq）。

## 6. 前端界面（对齐 DSH 截图）

三栏布局 + 配置页，Vue3 + Pinia + ant-design-vue，素雅低饱和灰蓝主题。

- **左栏**：顶部 logo/版本；「新会话」按钮；工作区分组列表（工作区名 + 其下会话，右侧相对时间“1天/26天”，折叠“展开其余 N 个会话”）；底部「设置」入口。
- **中栏**：空态大标题“探索未至之境”；会话态为消息流：用户气泡、assistant Markdown（代码高亮）、工具调用折叠块（名称/参数/结果）、diff 视图、提问卡片（选项按钮+自由输入）；底部输入框（工作区/模式/模型下拉 + 发送按钮）。
- **右栏**：可折叠面板：工作区文件树 + 点击查看文件内容（只读/可编辑保存）；file_change 列表入口。
- **新会话弹窗**：选工作区（下拉已有 + 「浏览目录」调 `/fs/browse` 的树形选择弹窗，或手输路径校验）。
- **配置页**：分组表单：模型接入（base_url/model/api_key + 「测试连接」按钮）、执行参数（write_confirm 开关、ask_timeout、max_tool_rounds、history_window）、环境状态（PG 连接/版本）。保存即生效（agent 每轮开始时读配置）。

## 7. 错误处理

- LLM 调用失败（网络/鉴权/限流）：下行 `error`，消息标 error，前端提供「重试」（重发该 user 消息）。
- WS 断开：前端自动重连 + 增量拉取；后端会话 running 状态带心跳，孤儿 running 超时（>10 分钟无活动）标记 error。
- 工具异常：作为 tool 结果（`{"error":...}`）回传模型自行纠正；路径越界同样处理并明确提示。
- 迁移失败：启动失败并在日志给出具体 SQL 文件名。

## 8. 测试策略（TDD）

- 后端 pytest + pytest-asyncio + httpx AsyncClient：
  - 路径守卫（越界/绝对路径/`..` 穿越）；
  - edit_file（唯一匹配成功/0 次/多次失败）、read_file 截断与二进制拒绝；
  - 迁移执行器（幂等）；config 读写与 key 脱敏；
  - Agent 循环：注入 MockLLM（脚本化返回 token 流与 tool_calls），覆盖：普通问答、write_file 落盘与 file_change、ask_user 挂起→回答→恢复、confirm 拒绝、连续工具轮数上限。
- 前端 vitest：pinia 会话 store（消息合并/流式追加）、diff 解析。
- 收尾：browser-skill 冒烟（建工作区 → 新会话 → 对话生成文件 → 文件树查看 → 配置页改 model）。

## 9. 部署形态

- 开发：`uvicorn app.main:app --port 8100` + `pnpm dev`。
- 日常使用：`build.bat`（前端 build → 后端静态托管）+ `start.bat`（起后端即可），PG 用 docker 容器 `zerocode-pg16`（宿主 5433，库/用户 zerocode）。
- Windows 下 .bat 一律做 LF→CRLF 字节级转换（历史踩坑）。
