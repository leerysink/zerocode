# 子 run（agent 子任务委派）规格

- 日期：2026-11-18
- 关联：ADR-0052（同会话只读子 run）、ADR-0029（实时≡历史）、ADR-0030（压缩水位）、
  ADR-0032（澄清门禁不受影响）、`CONTEXT.md`「子 run / 任务书 / 委派卡」
- 术语：**主 run**（发起委派的那次执行）、**子 run**（派生分支，口语「子 agent」）、
  **任务书**（`brief`，子 run 唯一的任务输入）、**委派卡**（消息流里渲染子 run 的卡片）

## 1. 问题陈述

平台 Agent 目前是「单会话严格单 run、工具逐条串行」：一次任务里的调研、检查、验证全部堆在一个
循环里排队，工具结果全灌进主上下文，撑到要压缩。用户要的是：主任务能把**互不依赖**的活派给
多个子 agent 并行去干，主上下文只收合并回执，不被脏活淹没。全库没有任何派生执行分支的雏形，
`docs/specs/2026-09-26-user-skills.md` §7 曾把「subagent 化运行」列为不做——本票以
ADR-0052 取代那一条。

## 2. 方案概述

一个 `delegate` 工具收一批任务书，内部并发跑 N 个**同会话只读子 run**，全部落定后合并为一个
`tool_result` 回主 run；子 run 的过程逐事件实时下发并落库，前端渲染成消息流内的「委派卡」。

### 2.1 工具与白名单

- 主 run 工具面新增 `delegate`（**唯一**新工具）：
  - schema：`tasks: [{title, brief}]`，两项均必填；`title` ≤40 字的人类可读短名，
    `brief` 是完整任务书（目标/范围/验收，建议 ≤500 字，软约束只写进描述）。
  - `scope=always`（非应用工作区也能用，子 run 自动缩到 fs 读类）、`confirm=none`。
  - 它**不走** `execute_tool` 的 handler 分派：与 `ask_user` / `clarify_done` 同列，
    在 `loop._dispatch`（`app/agent/loop.py:756`）里由循环特殊处理——子 run 需要 runner 侧的
    技能/记忆/决策索引装配。注册表仍持有 spec，`result_policy` 由 executor 显式套用
    （`apply_budget`，唯一出口不变）。
- 子 run 白名单（`registry.SUBRUN_TOOL_NAMES`，按 `SPECS` 既有顺序过滤挂载）：
  `list_dir / read_file / search / preview_status / db_query / preview_page / release_preflight / db_schema`。
  - 应用级四项仍按「解析出唯一应用」才挂（复用 `build_tools` 的 app 判据），
    非应用工作区只剩 fs 三件套。
  - **禁**：写盘五件（write/edit/delete/move/revert）、`ask_user`、`clarify_done`、`todo_write`、
    `memory_write`、`db_write`、`preview_restart` / `preview_logs`、`browser_*` 走查、以及
    `delegate` 自身（禁递归）。
  - 新增 `registry.build_subrun_tools(app: bool)` 产出子 run 挂载清单；子 run 内工具调用
    直接走 `execute_tool`（白名单内没有一个带确认策略，无需确认链），结果预算同一出口。
- `tests/test_agent_registry.py` 的 `BASE_NAMES` 追加 `delegate`；
  「循环自处理、无 handler」名单里补 `delegate`（前端没有工具名单常量，无需同步）。

### 2.2 子 run 执行

落点：新模块 `app/agent/subrun.py::run_batch(runner, ctx, cfg, args)`，由 `_dispatch` 在
`delegate` 分支调用，返回合并回执（dict）。

- **校验先于派发**：`tasks` 为空、或条数 > `subrun.max_tasks`（夹取 1..8，默认 5）→ 返回
  `{"ok": false, "error": "一次最多委派 N 个子任务，请拆成多批"}`——**不静默截断**；
  单条 `title/brief` 空白 → 同款逐条报错。不建任何子 run。
- **并发**：每个任务一个 `asyncio.create_task`，共享主 run 的 `ctx.cancel`；`asyncio.gather`
  等全部落定。子 run 的异常一律在各自任务内收敛成 `status=error` 回执，绝不给整批抛错。
- **上下文**：`[system, user(brief)]` 两条。system 由与主 run 同款的装配组成：
  `build_system_prompt(root, 日期, app_contract, skills, preview)`——`clarify=None`；
  preview 段只拼 diag/check/db/preview 四块（浏览器工具不在白名单，不拼 browser 段）；
  再追 `<memories>`（以 `brief` 为查询调 `memory_service.recall_memories`，cfg 透传）与
  决策索引（`clarify.render_decisions(collect_decisions(root))`）。
  **不注入** `<context_summary>`、`<current_todos>`，**不含**主会话聊天历史。
- **循环**：轮数上限 `subrun.max_rounds`（夹取 1..50，默认 10；刻意不沿用「<=0 不限」约定——
  子 run 的意义之一就是成本封顶）。每轮 LLM 流经 `_wait_or_cancel(..., ctx.cancel)` 与停止竞速；
  模型实例经 `runner._llm_for(cfg)` 取得（可调用工厂时每子 run 独立实例，测试注入依赖这条）。
- **每轮行为**（与主循环同形，但更薄）：
  - 有工具调用：assistant 行落库（`content` + `tool_args`，脱敏同主 run 三出口口径）→
    有文字则下发 `subrun_text` → 工具**逐条串行**执行（子 run 内部不并发工具，事件顺序简单）→
    工具行落库 → `subrun_tool_call` / `subrun_tool_result` 下发（`tool_progress` 经包装器转成
    `subrun_progress`）→ 入列。
  - 无工具调用：assistant 终轮行落库 → `subrun_text` → `subrun_done(completed)`，
    该任务回执 = 这段文字。
  - 空回复：沿用主循环的补催口径（共用 `_EMPTY_REPLY_NUDGE` / `_EMPTY_REPLY_RETRIES`
    常量，超过即 `error` 回执）。
- **失败**：LLM 异常 / 轮数用尽 / 补催后仍空 → `subrun_done(error, 原因)` 与
  `status=error` 回执；已完成的部分行原样保留，不伪造总结行、不自动重试。
- **子 run 的 ToolContext**：复用主 run 的 root / session_factory / app / skills_root / cfg /
  cancel / model_cfg；`send` 是包了一层的发件器（给事件补 `index` 归因）；
  `run_state` 每子 run 独立空 dict；`deploy_hub` 传 None、`deploy_tasks` 传空 dict
  （白名单里没有部署类工具，挂空值只为 ToolContext 形态完整）。
- **合并回执**：按 `index` 排序：
  `{"ok": true, "subruns": [{"index", "title", "status": "completed|error", "result": "<最终文字>", "error": "<错误摘要>", "rounds_used", "tool_calls"}]}`
  经 `apply_budget(spec.result_policy)` 截断（`DELEGATE_POLICY = ResultPolicy(max_chars=48_000,
  max_items=8, max_str_chars=12_000)`），截断标记为既有的 `_budget`。

### 2.3 落库与归属

- 新 changelog `changelog/V20261118_01_subrun_attribution.sql`：
  `ALTER TABLE message ADD COLUMN IF NOT EXISTS parent_tool_call_id VARCHAR(100)`、
  `ADD COLUMN IF NOT EXISTS subrun_index INT`（均 NULL；主 run 行两列恒空）。
  不建新表、不加索引（`ux_message_session_seq` 已覆盖读取形态）。
- **`add_message` 加 per-session 写锁**（`app/services/message_service.py`）：`seq` 由
  `max(seq)+1` 现算，多个子 run 并发写同一会话会撞唯一索引；用模块级
  `dict[session_id, asyncio.Lock]` 包住 INSERT+commit。前提是后端单进程——与 hub 内存态
  同一假定，多进程部署本就不成立。
- **排除规则（两处，必须同时改）**：`loop._history`（`app/agent/loop.py:255`）的窗口查询与
  `compaction_service.rows_after`（`app/services/compaction_service.py:32`）都加
  `AND parent_tool_call_id IS NULL`——子 run 消息不进主 run 模型历史、不计入压缩水位与
  自动压缩的字符估算。
- `sessions.py::get_messages` 原样返回全部行（含两列归属字段），压缩提示条按 `seq` 插入的
  既有逻辑不变。

### 2.4 事件协议与补发

- 外层不变：`delegate` 调用先走既有 `tool_call`（args 含 tasks），批量结束后走既有
  `tool_result`（合并回执）。**现有事件类型零改动**。
- 新增子 run 事件（不带 call_id：工具串行执行保证同一会话同一时刻至多一次在跑的委派，
  前端按「同名、还没结果的 `delegate` 调用行」关联，与 `findOpenToolCall` 既有模式一致）：

  | 事件 | 载荷 | 与落库同构的对应行 |
  |---|---|---|
  | `subrun_start` | `index, title` | （无行；快照可覆盖） |
  | `subrun_text` | `index, content` | 同文本的 assistant 行 |
  | `subrun_tool_call` | `index, name, args` | 成对 tool 行的 args 侧 |
  | `subrun_tool_result` | `index, name, result` | 成对 tool 行的 result 侧 |
  | `subrun_progress` | `index, name, line` | 不落库（与 `tool_progress` 同约定） |
  | `subrun_done` | `index, status, error?` | 落定状态 |

  发送顺序固定为「先落行、后发事件」（与主循环一致）：任何 live 画出来的东西，
  刷新后都能从行上重建。
- **hub 补发**：`ChatHub` 增 `subruns`（session → 当前在跑那次委派的
  `{tasks: [{title}], state: {index: {status, error?}}}`），由 hub 在下发事件时
  按流经的 `delegate` `tool_call` 与 `subrun_start/done` 维护（去重与「同一批」判定
  都只有 hub 这个位置同时看得到），`run_task` 的 finally 里随 `running/cancel_events`
  一并清除。WS 连入时在 `replay_pending` 同点补发一条 `subrun_snapshot`（只承载
  **当前在跑**的那次委派；已完成的委派由历史行重建）。这是「待答的 ask/confirm 在历史里
  没有行、只能补发」的同一机制。

### 2.5 前端委派卡

- `stores/chat.js::applyEvent`（`frontend/src/stores/chat.js:71`）新增 `subrun_*` 分支：
  找到打开中的 `delegate` 记录，按 `index` 归并 `{title, status, error, steps[]}`；
  `subrun_snapshot` 在找不到打开记录时**合成**一条 `delegate` 调用行（与 `confirm_request`
  合成行的既有做法同款），存在则只合并状态。
- `stores/messageModel.js::normalizeMessages`：两遍扫描——先按 `parent_tool_call_id`
  归集子 run 行（assistant 行的 `tool_args.tool_calls` 与 tool 行按 `tool_call_id` 配对成
  `steps: [{name, args, result}]`），再挂到按 `callId` 配对的 `delegate` 记录上；
  归一化输出的 `delegate` 记录与实时 reducer 产出**逐字段同形**（ADR-0029 的验收靶心）。
- 新组件 `DelegateCard.vue`：头部「委派 N 个子任务」+ 每行一个子 run（`title` + 状态徽标：
  运行中转圈 / 已完成 / 失败 / 已中断），可展开看该子 run 的步骤（工具名 + 参数摘要 + 结果摘要，
  折叠样式沿用 `ToolCallBlock` 的视觉语言）。默认收起；运行中徽标实时更新，
  卡片本身不折叠。`ChatMessages.vue:241` 分派处加一支
  `DelegateCard v-else-if="item.m.name === 'delegate'"`，放在通用 `ToolCallBlock` 之前。
- 旁观连接（watcher）与 owner 看同一张卡；卡片没有可交互动作，无需权限分支。

### 2.6 配置与提示词

- 账户级键（`config_service.USER_PREFIXES` 追加 `"subrun."`，与 `memory./clarify.` 同族——
  限制的是「我自己烧的钱」）：
  - `subrun.max_tasks` 默认 5，夹取 1..8；
  - `subrun.max_rounds` 默认 10，夹取 1..50。
  - 默认值写在代码（executor 读取处）与前端表单默认值两处，不新增 changelog 种行、
    不新增数据库迁移（与 `agent.preview_*` 同一先例）。
- 前端：`stores/config.js` 的 `FIELD_TO_KEY`/表单默认值追加两键；`SettingsView.vue` 在
  「执行与预览」卡加两个数字输入（带 extra 说明）。
- 提示词（`app/agent/prompts.py` 基础提示词教程段）新增「委派」小节：何时用（≥2 个互不依赖的
  调研/检查/验证）、任务书三要素、禁止用于串行依赖与需要确认/写盘的活、回执会合并、
  子 agent 看不到本会话聊天历史（背景必须写进任务书）。`delegate` 的 schema 描述一句话
  呼应同一口径。

### 2.7 停止、失败与边界

- **停止级联**：`cancel` 置位后，executor 的 `finally` 取消全部子 run 任务并以
  `gather(return_exceptions=True)` 吞异常收尾（不向主循环抛错），主循环按既有取消路径给
  `delegate` 落 `{"cancelled": true}` 结果行（`loop.py` 取消分支）。取消时**不**发
  `subrun_done`：卡片上未收口的子 run 由「委派结果为 cancelled + 缺 done」两端共同推出
  「已中断」，live 与历史同规则。
- 等待委派期间的用户插话：按现有工具边界语义，在 delegate 返回后的边界注入，不打断子 run。
- 会话状态角标、单 run 预占、5 秒轮询全部不变（会话内仍只有一个主 run，子 run 是它内部调度）。
- 澄清门禁不受影响：子 run 无提问、无写盘，门禁只在写工具确认前判定，与委派无交集。

## 3. 用户故事

- **US-01 挂载**：主 run 的工具清单含 `delegate`；子 run 的工具清单不含 `delegate` 及任何写/确认/
  提问工具（禁递归由工具面而非提示词保证）。
- **US-02 并发**：一次 `delegate` 带 2 个任务时，两个子 run 的 LLM 流与工具调用交错推进，
  总时长显著小于串行之和；每个子 run 有独立上下文（互不可见）。
- **US-03 回执**：批量结束后主 run 收到一条 `tool_result`，`subruns[]` 按 index 排序，
  每条含 title/status/result/rounds_used/tool_calls。
- **US-04 失败隔离**：任一子 run 失败（LLM 异常/轮数用尽）不影响其余子 run 完成；
  合并回执里该条 `status=error` 且带错误摘要。
- **US-05 上限**：条数超过 `subrun.max_tasks` 时 `delegate` 返回 `ok:false` 与分批提示，
  不创建任何子 run、不静默截断。
- **US-06 上下文**：子 run 的 system 含技能/记忆/决策索引与 preview 段（应用工作区），
  不含主会话聊天历史、`<context_summary>`、`<current_todos>`。
- **US-07 只读**：子 run 调 `write_file` 一类名字落到「未知工具」错误（schema 里根本没有）；
  工作区无任何写入副作用，`file_change` 零新增。
- **US-08 非应用工作区**：未解析出唯一应用时，子 run 工具只剩 `list_dir/read_file/search`，
  `delegate` 自身仍可用。
- **US-09 实时呈现**：批量执行中，前端委派卡实时显示每个子 run 的 title、状态与步骤，
  无需刷新。
- **US-10 中途刷新**：批量执行中刷新页面，`subrun_snapshot` 补发使委派卡与状态原样恢复，
  已落库的步骤逐条可见，后续事件继续推进。
- **US-11 历史重建**：批量完成后再刷新，委派卡由历史行重建，内容/步骤/状态与实时所见逐字段一致
  （ADR-0029 同构验收）。
- **US-12 主历史排除**：子 run 消息不进主 run 的模型历史窗口，不计入自动压缩的字符估算，
  压缩摘要不覆盖它们。
- **US-13 停止**：委派执行中点停止，全部子 run 与主 run 一同收口；委派结果行为 cancelled，
  未收口的子 run 显示已中断。
- **US-14 轮数预算**：子 run 达到 `subrun.max_rounds` 即以 error 回执收口，其余子 run 不受影响。
- **US-15 并发落库**：多个子 run 并发写同一会话，`seq` 连续且唯一，无唯一索引冲突。
- **US-16 配置**：`subrun.max_tasks` / `subrun.max_rounds` 在设置页可改、按账号生效、
  越界夹取；库里无该键时用代码默认值。
- **US-17 脱敏**：子 run 的 tool_args / tool_result 落库与下发共用主 run 同一套凭据遮断口径。
- **US-18 会话体系不变**：委派不新建会话行、不进会话列表；侧栏角标、5 秒轮询、单 run 预占行为
  与今天一致。

## 4. 已定决策

| # | 决策 | 结论 |
|---|------|------|
| 1 | 载体 | 同会话子 run（不建子会话、事件进同一 WS、消息带归属列） |
| 2 | 工具面 | 只读白名单八件套；写/确认/提问/走查/todo/memory 一律不给 |
| 3 | 收口 | 单 `delegate` 批量派发、内部并发、全部落定合并一次 `tool_result`；主循环串行工具语义不动 |
| 4 | 规模 | 单批默认 5 可配；无全局信号量；禁递归（树深恒 1） |
| 5 | 失败 | 隔离，逐个回执，不自动重试；超上限直接报错 |
| 6 | 上下文 | 任务书 + 同款系统骨架，不含主会话历史 |
| 7 | 模型与预算 | 继承主 run 模型；`subrun.max_rounds` 默认 10 可配 |
| 8 | 呈现 | 消息流内委派卡（嵌套子 run 行，可展开步骤） |
| 9 | 过程留存 | 逐事件实时 + 落库（带归属），同构 ADR-0029；中途刷新靠 hub 快照补发 |
| 10 | 停止 | 级联竞速取消；委派结果行 cancelled；未收口子 run 显示已中断 |

## 5. 测试决策

- **最高接缝 = `AgentRunner` + `ScriptedLLM` + 测试 engine**（`tests/test_agent_loop.py:44`
  的 `mk_session/run_once` 模式复用）。新增 `tests/test_agent_subruns.py`：
  - 并发与交错：两个子 run 的脚本化流交错返回，断言事件合并回执、按 index 排序、
    落库行归属正确；
  - 失败隔离、轮数用尽、空回复补催；
  - 上限校验（超 max_tasks 零副作用）；
  - 子 run 工具清单断言（`build_subrun_tools` 顺序；无 delegate/写类；非 app 只剩三件套）；
  - 历史排除：`_build_messages` 的 messages 不含子 run 文本，`rows_after` 不计其字符；
  - 脱敏两侧同结论（values 与主 run 用例同口径）。
  - 子 run 的 LLM 用**可调用工厂**注入（`_llm_for` 每子 run 独立实例），`ScriptedLLM`
    状态性不会串台。
- **`add_message` 并发**：`asyncio.gather` 并发写同会话 N 条，断言 seq 连续唯一、无
  IntegrityError（接缝在服务层，不需要真 WS）。
- **WS 接缝**：`subrun_*` 事件顺序（先行后事件）与 `subrun_snapshot` 连入补发，
  沿用 `tests/test_ws.py` 的 TestClient 模式。
- **注册表钉住**：`tests/test_agent_registry.py` 的 `BASE_NAMES` 追加 `delegate`、
  「无 handler 的循环自处理名单」同步。
- **前端 vitest**：`chat.test.js` 新增 `subrun_*` / `subrun_snapshot` reducer 用例；
  `messageModel` 分组与配对用例；`DelegateCard` 渲染四态（运行/完成/失败/已中断）与展开。
  实时 reducer 产出与 `normalizeMessages(历史行)` 产出逐字段相等的同构断言。
- **"完成"定义**：新增测试全绿 + 全量 pytest 串行全绿（含 `test_ws`）+ 前端
  `vitest --maxWorkers=4` 全绿 + 浏览器冒烟：真实会话发一句「并行调研两个方向」，
  观察委派卡实时推进、刷新不丢、回执合并。

## 6. 明确不做

- 子会话载体、跨会话子 agent、会话列表展示子任务。
- 子 run 的写盘/写库/重启预览（确认卡回流）、`ask_user`、`todo_write`、`memory_write`。
- 递归委派 / 嵌套子 run、子 run 之间的通信或共享黑板。
- 即发即忘、事后收结果、断点续跑、进程重启后的子 run 恢复（随主 run 生灭）。
- 子 run 降档模型、按任务指定模型。
- `browser_*` 走查式子 run、对外部网页的调研（工具面仍不接任意 URL）。
- 成本统计面板、委派历史归档/分页 UI、全局并发闸。
- 旧 `docs/specs/2026-09-26-user-skills.md` 的其它结论（技能安装/受控读面等）——只有 §7
  「subagent 化运行」一条被取代。

## 7. 补充说明

- **单进程假定**：hub 内存态、`subrun_snapshot`、`add_message` 的 seq 锁都以后端单进程为前提；
  现部署即单进程，多进程化是另一票。
- **消息量**：一次委派最坏 5×10 轮 ≈ 百余行；`get_messages` 按会话全量返回、会话删除级联清理，
  不为此加分页。
- **`preview_page` 并发**：sidecar 的 `/inspect` 是一次性调用，无交互会话容量限制；
  5 路并发快检沿用现状，实测压垮再限流另立票（不在本票预埋闸门）。
- **任务书预算**：`brief` 只做非空校验，长度是提示词软约束（≤500 字）——硬上限会让模型
  为了绕开它把任务拆碎。
- **观察者**：被共享者的旁观连接走同一条事件与历史通道，能看到委派卡全貌。
