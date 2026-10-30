# Agent 数据验证工具（db_query / db_write）规格说明

**日期**：2026-10-30  **来源**：grill-with-docs 访谈  **术语**：见 [CONTEXT.md](../../CONTEXT.md)「测试库（test db）」「Agent 数据库工具（db_query / db_write）」「断言（expect）」「重建 dev 库」「查询控制台」  **ADR**：[0027 读写边界](../adr/0027-agent-db-tools-dev-write-prod-readonly.md) · [0028 判定双轨](../adr/0028-agent-data-verification-dual-track.md)

## 1. 问题陈述

平台 Agent 目前的验证能力全部在**进程层面**：预览三工具（`preview_status` / `preview_logs` / `preview_restart`）只能回答"跑起来了吗、日志有没有错"，回答不了"数据是对的吗"。生成的 CRUD 应用里，种子数据是否正确、一次业务操作后库里状态对不对、用户直接问"帮我看下订单表里昨天的数据"——Agent 全部没有入口，只能猜或让用户自己去 DB 操作台查。

人侧底座已经存在且经过实战（查询控制台、`db_admin_service.execute_one/execute_batch`、逐条审计、超时与行数护栏），本功能把它以**零命令面、服务端定身份**的方式开放给 Agent，并补上 Agent 特有的机器断言判定。

## 2. 方案概述

- **两个工具、零命令面**：`db_query`（只读查询）+ `db_write`（dev 写通道），与预览工具同风格——应用身份由服务端 `resolve_session_app` / `fetch_app` 解析，schema 里没有 app 参数，模型指不到别的应用。
- **读写边界钉死**（ADR-0027）：`db_query` 可选 `env=dev|prod`，服务端语句级判定只放行读语句；`db_write` 只打 dev 库，schema 里**没有 env 参数**，prod 保护不依赖模型自觉。
- **判定双轨**（ADR-0028）：不带 `expect` 时回原始表格供模型目测（探索式查数）；带 `expect` 时工具在返回前完成机器比对，回 `ok` + 差异摘要，结论可复现。
- **断言三档**：`expect = {type: "count" | "scalar" | "rows", value}`。count 由服务端把语句包成 `SELECT count(*) FROM (...)` 判定，不受行数截断影响；scalar 要求单行单列；rows 两侧按行排序后逐格比对，不符回前 10 行差异。
- **写必确认**：`db_write` 执行前由 `AgentRunner._dispatch` 发 `confirm_request`，摘要逐条列 op 徽章、无 WHERE 的 update/delete 与表级 DROP 标红；用户拒绝则一条不执行。不受 `agent.write_confirm` 开关影响。
- **底座复用**：执行走 `db_admin_service.execute_one / execute_batch`（同一连接逐条串行遇错即停、`statement_timeout=15s`、外层 LIMIT 库侧截断），审计走 `log_operation`，detail 记 `source="agent"` 与会话 id。不新增执行轨。
- **预算另设小口径**：给模型的行预算走配置 `agent.db_rows_limit`（默认 200，夹在 10..1000），字符总预算按字符数收口（与预览日志 `LOG_TOTAL_CHARS` 同数量级、同"按字符不按字节"口径）。不套人用的 5001 行口径。
- **提示词**：应用工作区随工具挂载一段使用说明（`build_db_prompt`，与 `build_preview_prompt` 并列），告知模型：要给验证结论时用 expect 机器断言、目测只用于探索；被截断的结果不得做行数判断。

## 3. 用户故事

编号可独立验证。

### db_query（只读）

- **US-01** 作为 Agent，我对应用工作区调用 `db_query {sql: "select id,name from widgets"}`，得到列名、行数据、实际行数与 truncated 标志——默认打在 dev 库。
- **US-02** 作为 Agent，我查询 `env=prod` 的 SELECT 正常返回；prod 对我而言只是另一个只读环境。
- **US-03** 作为 Agent，我在 `db_query` 里提交 `UPDATE ...`，工具不执行，回 `{"ok": false, "error": ...}` 并指引我用 `db_write`。
- **US-04** 作为 Agent，我的结果超过 `agent.db_rows_limit`（默认 200）行时，返回前 N 行 + `truncated: true` + 一句"请加 WHERE 或改用聚合/count 断言"。
- **US-05** 作为 Agent，我的结果字符总量超预算时按行边界再截，返回里注明因字符预算截断——模型上下文不被一次查询吃光。
- **US-06** 作为 Agent，我写出语法错误、不存在的表、凭据缺失（未供给）等任何失败时，PG 错误原文作为**正常结果**返回（不抛异常），我能读原因并向用户解释；语句超时沿用内核 15s。
- **US-07** 作为 Agent，我带 `expect={type:"count", value:5}` 查询，服务端把语句包成 count 聚合后比对，回 `ok:true/false` + 实际数——结果即使超过行预算，count 判定依然准确（不受截断影响）。
- **US-08** 作为 Agent，我带 `expect={type:"scalar", value:"已支付"}` 查询，结果必须恰好单行单列，否则回"不满足标量断言前提"的说明而不是误判不符。
- **US-09** 作为 Agent，我带 `expect={type:"rows", value:[...]}` 查询，两侧按行规范化排序后逐格比对；不符时回前 10 行差异摘要（行号、列名、期望值、实际值）。
- **US-10** 作为 Agent，我提交的 rows 期望超过 200 行时，工具拒绝对比并提示改用 count/scalar 或收窄查询。
- **US-11** 作为 Agent，我不带 `expect` 时只拿表格，工具不给任何"对不对"的结论——目测判断是我的事，机器判定要显式走断言。

### db_write（dev 写通道）

- **US-12** 作为用户，Agent 要往我的 dev 库写入时，聊天里先出现确认卡：逐条列语句的 op 徽章，无 WHERE 的 update/delete 与表级 DROP 标红；我拒绝则**一条都不执行**，Agent 收到"用户已拒绝"的正常结果。
- **US-13** 作为 Agent，我确认后 `db_write {statements: [...]}` 在同一连接内逐条串行遇错即停：已成功的不回滚，逐条回 `{op, status, affected | 错误原文}`，失败之后的标"未执行"。
- **US-14** 作为平台，`db_write` 的工具定义里不存在 env 参数，且语句含 DATABASE 级/实例级操作（`DROP/CREATE DATABASE`、`ALTER SYSTEM`）时工具直接拒绝——写 prod 与写别人的库在 schema 层就不可能，不依赖提示词自觉。
- **US-15** 作为用户，`db_write` 的确认**不受** `agent.write_confirm` 开关影响：文件写入可 git 回滚，数据库写入不可回滚，写库永远点头。
- **US-16** 作为 owner，批量里每条语句在「操作历史」抽屉各有一条审计记录（op、状态、耗时），detail 记 `source="agent"` 与会话 id；未执行的语句不产生审计——与查询控制台批量执行的审计口径同形。

### 挂载与身份

- **US-17** 作为平台，应用工作区才挂载这两个工具与提示词；非应用工作区静默不挂载，普通对话看不到多余工具面。
- **US-18** 作为平台，工作区绑定了多个应用时 fail closed：不挂载，并给模型一句可转述的原因（复用 `resolve_session_app` 的 note 口径，与预览工具不重复实现）。
- **US-19** 作为 Agent，run 开始后应用被另一个标签页删除：每次工具调用都重读应用行（`fetch_app` 口径），回正常错误结果，绝不让整轮对话失败。
- **US-20** 作为非归属用户，我的会话永远碰不到别人的应用——应用身份按 `(workspace_id, owner_user_id)` 服务端解析，schema 无 app 参数，越权在参数层不存在。

## 4. 已定决策

| 维度 | 决策 |
|---|---|
| 挂载层 | 平台 Agent 工具（非 DB 操作台功能、非发布流水线阶段、非生成应用自带能力） |
| 「测试库」口径 | 承载测试数据的 dev 库；**不是**测试用例套件，**不是**平台内核 `zerocode_test` |
| 读写边界 | dev 可写（每次确认）+ prod 只读；`db_write` schema 无 env → ADR-0027 |
| 灌数路径 | Agent 直接写 SQL；不开放「重建 dev 库」（DROP 全库会连带清掉用户手工数据）→ ADR-0027 |
| 判定时机 | 按需、模型自决；不做"生成后自动验证闭环"→ ADR-0027 |
| 判定主体 | 目测 + 机器断言双轨；断言判定在工具内完成 → ADR-0028 |
| 工具切分 | 两工具（`db_query` 可带 expect / `db_write`）；"验证准确性"是用法不是第三个工具 → ADR-0028 |
| 断言形态 | count / scalar / rows 三档；count 走服务端包 count 聚合 |
| 执行底座 | 复用 `execute_one / execute_batch`，护栏（超时/行数上限/提交时机）只此一份 |
| 写确认 | `AgentRunner._dispatch` 仍是 `confirm_request` 唯一发出方；db_write 恒确认，与 `agent.write_confirm` 解耦 |
| 行预算 | `agent.db_rows_limit` 默认 200、夹 10..1000；字符预算按字符数（同预览日志口径） |

## 5. 测试决策

- **最高接缝 = 工具 handler 层**：`run_db_tool(ctx, name, args, confirmed=...)`，桩掉 `db_admin_service` 的函数，按 `tests/test_preview_tools.py` 的既有模式断言——schema 形状、fail-closed 分支、确认分支、截断标志、断言结果结构全在这层测。
- **断言引擎 = 纯函数**：`compare_expect(actual, expect)`（排序、归一化、scalar 前提校验、rows 差异摘要）表驱动单测，不碰数据库。
- **确认与挂载分支在 loop 层**：`tests/test_agent_loop.py` 模式补 `_dispatch` 的 db_write 确认路径与"非应用工作区不挂载"两条。
- **执行内核不重测**：串行遇错即停、超时、库侧截断由 `db_admin_service` 既有真实库测试覆盖，本规格只验证 Agent 路径**接到了**这些底座（审计有 `source="agent"`）。
- **完成定义**：pytest 全绿（注意本仓库必须串行跑，共享 `zerocode_test` 库）+ 聊天页手工矩阵：查 dev/prod、三档断言各一、写入确认与拒绝各一、非应用工作区不挂载。

## 6. 明确不做

- **不跑测试套件**：pytest/vitest 等自动化测试执行不在本功能域（「测试库」已按数据库口径收口）。
- **不碰平台内核库**：`zerocode_test` 与用户应用库无关，Agent 不可达。
- **不做生成后自动验证**：不烧每轮的 token 与时间，模型按对话意图自决。
- **不开放「重建 dev 库」/ DATABASE 级操作**：DROP 全库级动作绝不给 Agent。
- **不做 prod 写**：连确认形态都不提供。
- **不做服务端断言模板库**：查询控制台的页签 localStorage 是人侧功能，Agent 侧不存期望值。
- **不做前端式语句拆分**：模型直接给 `statements` 数组，服务端不承担分号状态机。
- **不新增依赖**：不引新的 SQL 解析库；op 判定与语句拆分复用内核既有实现。

## 7. 补充说明

- **命名**：规范名就是 `db_query` / `db_write`（见 CONTEXT.md）；对外叙述沿用用户原话"三个能力"，落地形态是两个工具，文档里不要把 verify 说成独立工具。
- **提示词要点**：给模型的使用说明要包含——要下"数据对不对"的结论时优先 expect 机器断言；被截断的结果不得用于行数判断；写库前想清楚语句，用户会看到逐条摘要。
- **配置键**：`agent.db_rows_limit`（200）、`agent.db_rows_assert_max`（200，rows 断言期望行数上限）；其余阈值（字符预算、确认开关形态）作为常量收口在模块内，同 `preview_tools` 的常量风格。
- **与预览工具的模块关系**：新工具独立成 `app/agent/db_tools.py`，`tools.py` 分派分支里惰性 import（避免反向依赖成环，同 `preview_tools` 的处理）；`DB_TOOLS` schema 与 `PREVIEW_TOOLS` 并列定义在 `app/agent/llm.py`。
