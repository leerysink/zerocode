# 需求：工作区记忆（Workspace Memory）

- 日期：2026-10-08
- 状态：需求已对齐（grill 完成），待 to-spec / to-tickets 细化
- 术语见 [`CONTEXT.md`](../../CONTEXT.md)「工作区记忆 / 记忆条目 / 记忆召回」
- 关键决策见 [`ADR-0012`](../adr/0012-workspace-memory-pgvector-embedding.md)

## 1. 一句话

给平台 Agent 加一层**跨会话记忆**：把「这个工作区历史上发生过什么、定了什么、踩过什么坑」
沉淀成可检索的记忆条目，Agent 在新会话里按当前任务语义自动召回，不用每次从零问起。
参照 AI 编码助手的记忆范式（自动沉淀 + 自动召回），落到 ZeroCode 的应用工厂场景。

## 2. 目标与非目标

### 目标
- Agent 能在同一 workspace 的**不同 chat_session 之间**延续经验，减少重复提问与重复劳动。
- 记忆是**原子、分类、可管理**的条目，而非整段执行日志的堆砌。
- 召回**按语义相关性**取 topK，条目变多后仍精准、可控 token。
- 用户能在 UI 上查看、新增、修改、删除本工作区的记忆。

### 非目标（本期不做）
- 不做跨工作区/账户级全局记忆（作用域严格 = workspace）。
- 不做自动衰减/过期淘汰（靠查重 + Agent/用户手动维护）。
- 不做执行会话全文日志检索（只存原子记忆条目）。
- 不改对话生成主链路的其他行为（压缩、技能注入、todo 等保持原样）。

## 3. 作用域与数据归属

| 维度 | 裁定 |
|------|------|
| 隔离层级 | **按 workspace 隔离**，一个工作区一套记忆 |
| 与 session 关系 | 记忆挂在 session **之上**；同一 workspace 下多个 session 共享 |
| 与技能库关系 | 并列但不同域：技能=账户级「怎么做」，记忆=工作区级「发生过什么」 |
| 可见性 | owner 查看/管理自己工作区的记忆（沿用现有 owner/founder 归属过滤） |

数据模型（示意，最终以 to-spec 为准）：新增表 `workspace_memory`，至少含
`id / workspace_id / type / content / summary / embedding vector / enabled / created_at / updated_at`，
唯一/查询索引参照 `user_skill` 风格（按 `(workspace_id, ...)` 建，非全局唯一）。

## 4. 核心行为

### 4.1 写入（产生记忆）
- **Agent 主动写**：新增工具 `memory_write`（与 `todo_write` 同款、模型判断「值得记」时调用），
  支持 `create / update / delete` 三类操作，入参含 `type`（四分类之一）+ `content`。
- **用户手动**：工作区记忆面板提供增删改入口。
- **查重前置**：写入前先用向量检索找相似旧条，命中则提示/让 Agent 选择「新建 / 更新 / 合并」，
  避免重复膨胀。
- **敏感红线**：凭据/密钥/令牌/密码等**禁止入库**——写工具层面拦截 + 存入前自动脱敏，
  绝不进库、不进向量、不进模型上下文外发。

### 4.2 记忆分类（type）
四类之一：`preference`（偏好）、`decision`（决策）、`fact`（事实）、`pitfall`（经验/踩坑）。

### 4.3 召回（调起记忆）
- 每轮 `_build_messages` 构建 system 时，按**当前任务**语义检索本 workspace 记忆 topK，
  注入 `<memories>` 块（与 `<context_summary>` 同一注入位、同一路径）。
- 检索底座 = **pgvector + 独立 embedding 模型**（见 ADR-0012）；写时算向量、查时算相似度。
- **降级**：embedding 模型未配置时，回退**关键词/全文检索**，功能不阻断；配好向量模型后自动切回。
- **注入上限三重约束**：topK 条数 + 相似度阈值过滤 + token 预算封顶。全部可配，沿用
  `agent.*` 配置风格与 owner 过滤（定稿键名：`memory.enabled` / `memory.top_k` /
  `memory.min_similarity` / `memory.max_tokens`；grill 阶段曾写 `memory.recall_*`，006 落地时
  去掉 `recall_` 前缀，因为 `enabled` 不单属于召回路径），越界/非法值回落默认。

### 4.4 生命周期
- 可去重、可更新、可失效（`enabled` 开关）。
- **不做自动衰减**：矛盾时由新记忆通过「更新旧条」显式覆盖，或用户在面板删除。

## 5. 依赖与前置条件

1. **embedding 模型配置**：账户级或全局新增一份（独立于对话 `model_config`），提供
   embeddings 接口。缺省走降级检索。
2. **pgvector 扩展**：目标 Postgres 需可 `CREATE EXTENSION vector`；不可用则安装失败，属部署前置。
3. **SQL 变更（走版本管理）**：平台库迁移写入顶层 `changelog/V20261008_xx_workspace_memory.sql`，
   含 `CREATE EXTENSION IF NOT EXISTS vector`、`workspace_memory` 建表、索引；受 `run_migrations`
   自动应用、`schema_migration` 登记、`IF NOT EXISTS` 幂等守卫可安全重放。

## 6. UI 入口

- **工作区级记忆面板**：在聊天页/工作区提供「记忆」面板（类比现有右栏文件面板），
  列出本 workspace 记忆条目，支持搜索、按 type 分组、增/改/删。
- 无记忆 / 0 条时显示空态引导，不报错。

## 7. 边界与验收关注点（供 to-spec/tdd 展开）

- 0 条记忆 → 不注入 `<memories>` 空块（对齐技能空清单不渲染）。
- embedding 服务超时 / 返回非预期结构 → 降级关键词检索，不阻断对话。
- 命中相似度阈值以下 → 不注入（宁缺毋滥），阈值 = 边界值 ±1 需覆盖。
- topK 恰好 1 条 / 满 K / 超过 K 截断；token 封顶触顶时的裁剪。
- 敏感值写入：含密码/`api_key`/令牌的 content 被拦截或脱敏的具体表现（断言错误码/脱敏后文本）。
- 跨 workspace 隔离：A 工作区记忆不得出现在 B 工作区召回结果里（越权/旁路直绑兜底）。
- 雪花/大整数 id 全链路按字符串走，不做 `Number()` 截断（沿用项目既有约束）。
- update / delete 目标记忆不存在 → 明确失败语义，不静默。

## 8. 决策速查（本次 grill 共识）

| # | 议题 | 结论 |
|---|------|------|
| 1 | 参照对象 | AI 编码助手式记忆（跨会话记住偏好/决策，自动召回） |
| 2 | 作用域 | 按 workspace/应用隔离（项目级） |
| 3 | 写入机制 | Agent 工具主动写 + 用户手动 |
| 4 | 召回方式 | 语义检索 topK 注入 |
| 5 | 检索引擎 | pgvector + 独立 embedding 模型（真语义） |
| 6 | 记忆粒度 | 原子分类记忆（非执行日志） |
| 7 | 分类 | 偏好 / 决策 / 事实 / 踩坑 |
| 8 | 冲突处理 | 检索查重 + Agent 手动增/改/删（无自动衰减） |
| 9 | 降级策略 | embedding 未配置 → 回退关键词检索 |
| 10 | 管理 UI | 工作区级记忆面板 |
| 11 | 敏感信息 | 禁止入库 + 自动脱敏 |
| 12 | 注入上限 | topK + 相似度阈值 + token 封顶 |

## 9. 下一步

需求已对齐，接构建链下游：`to-spec` 综合为规格说明 → `to-tickets` 拆示踪弹切片
（建议切片：DB迁移+表 / embedding配置+向量写入 / recall注入+降级 / memory_write工具 / 敏感脱敏 /
记忆面板UI）→ `implement`。
