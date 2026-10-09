# Spec：工作区记忆（Workspace Memory）

日期：2026-10-08 ｜ 状态：待评审
前置共识：[`2026-10-08-workspace-memory.md`](./2026-10-08-workspace-memory.md)（需求/grill 共识）、[ADR-0012](../adr/0012-workspace-memory-pgvector-embedding.md)（pgvector + 独立 embedding 检索底座）
术语：见 [`CONTEXT.md`](../../CONTEXT.md)（工作区记忆、记忆条目、记忆召回）
参照实现：账户级技能库 [`2026-09-26-user-skills.md`](./2026-09-26-user-skills.md)（同为「注入 system + 受控 CRUD」范式，但作用域不同）

## 1. 问题陈述

平台 Agent 每次对话的上下文只活在单个 `chat_session` 内：`_build_messages` 拼装的 system
只有当轮工作区契约、已启用技能清单、压缩摘要与 todo 快照，历史消息按 session 隔离。换一个
会话，Agent 对这个项目「之前定了什么、踩过什么坑、用户偏好怎么写」全部失忆，用户得从零重述。

本 spec 给 Agent 补一层**跨会话、按 workspace 隔离的语义记忆**：把原子化的经验条目沉淀入库
（向量），每轮按当前任务召回 topK 相关条目注入 system，让用户与应用之间形成的共识随工作区
累积、被 Agent 自动调起。

## 2. 方案概述

一条主链路（**沉淀 → 向量化入库 → 语义召回 → 注入 system**），四个部件 + 一个降级路径：

- **存储**：DB 新表 `workspace_memory`（按 `workspace_id` 隔离），`embedding` 列为 pgvector
  `vector` 类型。CRUD 收敛到新服务 `app/services/memory_service.py`（与 `message_service` 并列）。
- **embedding provider**：复用对话 LLM 之外的独立 embedding 配置（见决策 D-2），
  新 `app/agent/embeddings.py` 提供 `embed(texts) -> list[vector]`，走 OpenAI 兼容 `/embeddings`。
- **召回（消费）**：`memory_service.recall_memories(sf, workspace_id, query, cfg)` 是**最高测试接缝**——
  封装「向量检索 or 关键词降级 + 阈值 + topK + token 封顶」的全部判据，对外只吐条目列表。
  `AgentRunner._build_messages` 调它，把结果注入 `<memories>` 块（与 `<context_summary>` 同注入位）。
- **写入**：新工具 `memory_write`（`llm.py` TOOLS + `tools.py` execute_tool 分派，与 `todo_write`
  同款），支持 `create / update / delete`；create 前查重、写入前**敏感脱敏**。用户侧经前端面板走
  同一 `memory_service` CRUD。
- **降级**：embedding 未配置时 `recall_memories` 内部回退 002 已落地的关键词检索（Python 侧打分：
  拉丁词 + 中文二元滑窗），调用方与注入路径完全无感（单一入口，不写平行实现——ADR-0012 后果条）。
  票面 D-6 原写「优先 `pg_trgm`」，006 实现时改为此案，理由见票面 006「落地偏差」。

**接缝清单（测试锚点，优先复用而非新建）**：
1. `memory_service.{create,update,delete,list,recall_memories}` —— 记忆域的唯一后端接口，绝大多数测试打这里
2. `AgentRunner._build_messages` —— `<memories>` 注入的有无/格式（用 `ScriptedLLM.received` 断言）
3. `execute_tool("memory_write", args)` —— 工具的 create/update/delete/查重/脱敏语义
4. `embeddings.embed` —— 向量化与未配置降级判定
5. `/api/workspaces/{id}/memory` 路由 —— owner 隔离与增删改

理想情况下注入侧只跨接缝 2 一处、检索侧只跨接缝 1 一处。

## 3. 用户故事

### A. 存储与迁移
1. 作为平台，启动时 `run_migrations` 应用 `changelog/V20261008_01_workspace_memory.sql`，
   建 `workspace_memory` 表（含 `vector` 列）并登记 `schema_migration`；重复启动幂等
   （`CREATE ... IF NOT EXISTS`）。
2. 作为平台，迁移文件先执行 `CREATE EXTENSION IF NOT EXISTS vector`；目标库不具备该扩展时
   迁移报错并终止启动（明确失败优于静默无向量），错误信息指向 pgvector 前置条件。
3. 作为平台，`workspace_id` 上建查询索引（`(workspace_id, enabled)`）；`embedding` 上建向量索引
   （HNSW/IVFFlat），当且仅当扩展可用时创建。

### B. embedding 配置
4. 作为账户管理员，我在全局或账户级配置一份 embedding 模型（base_url / api_key / model），
   与对话模型分开、互不影响；未配置时记忆功能仍可写可召回（走降级）。
5. 作为 Agent，写记忆时对 `content` 调用 embedding；召回时对「当前任务文本」调用 embedding。
   embedding 调用失败（网络/超时/非预期结构）不阻断主流程：写入落库但 `embedding` 置空并记一条
   warning，召回该次回退关键词检索。

### C. 写入（memory_write 工具）
6. 作为 Agent，我调用 `memory_write` 且 `op="create"`、`type`∈{preference,decision,fact,pitfall}、
   `content` 非空时，一条归属当前 session 所属 workspace 的记忆入库，`enabled` 默认 true。
7. 作为 Agent，`op="update"` 携记忆 `id` 与新内容时，仅更新本 workspace 内该条；`id` 不存在或
   不属于本 workspace → 返回明确失败（`ToolError`/`{ok:false}`），不静默、不新建。
8. 作为 Agent，`op="delete"` 携 `id` 时软删/硬删本 workspace 内该条（见决策 D-5）；越界 id 同样明确失败。
9. 作为 Agent，`type` 不在四类枚举内、`content` 为空/纯空格 → create 被拒并给出原因。
10. 作为 Agent，create 前系统用向量（或降级关键词）检索相似旧条；命中高相似时返回既有条目摘要，
    提示我改用 `update`/合并而非重复新建（查重为**建议**，不硬拦，见决策 D-4）。

### D. 敏感信息红线
11. 作为安全边界的消费者，任何写入内容中的凭据/密钥/令牌/密码形态（如 `password=`、api key 串、
    Bearer token）在入库前被拦截或**自动脱敏**（替换为占位符），库中永不落敏感明文，
    不进向量、不回注模型上下文（对齐 `deploy/**` 凭据不入上下文红线）。
12. 作为用户，手动在面板录入含敏感值的内容时，同样经过后端同一脱敏关卡（前端不可绕过）。

### E. 召回与注入
13. 作为已配置 embedding 的消费者（Agent），每轮 `_build_messages` 时，system 尾部出现
    `<memories>` 块，含按当前任务语义检索的本 workspace 记忆 topK（每条标 type），
    位置与 `<context_summary>` `<current_todos>` 一致。
14. 作为 Agent，当本 workspace 无启用记忆、或全部命中低于相似度阈值时，**不注入** `<memories>`
    空块（对齐技能空清单不渲染）。
15. 作为 Agent，召回结果受三重上限约束：topK 条数、相似度阈值、token 预算封顶；
    三者均可配（`memory.enabled` / `memory.top_k` / `memory.min_similarity` / `memory.max_tokens`，
    账号级），触顶时按相似度降序截断。

### F. 降级
16. 作为未配置 embedding 的用户，召回自动走关键词降级路，仍能取到 topK；
    对话不因缺向量底座而报错或降级为「不召回」。
17. 作为运维，配好 embedding 后，新写入的记忆带上向量，召回无缝切回语义检索；历史无向量旧条
    在降级检索下仍可命中（不丢）。

### G. 隔离与权限
18. 作为 owner A，我永远无法召回、列出、修改、删除 owner B 所在 workspace 的记忆；
    跨 workspace 的 id 直接访问一律 404/明确失败（沿用现有 owner 归属过滤）。
19. 作为 founder，按现有可见性规则可查看全部（沿用 metrics/store 的 founder 口径，不新立权限模型）。

### H. 前端面板
20. 作为用户，我在工作区/聊天页看到「记忆」面板，列出本 workspace 记忆（type 分组、摘要、
    搜索框、启用开关、增/改/删）；0 条时显示空态引导，不报错。
21. 作为用户，面板的增删改调用后端 `memory_service`，与 Agent 工具写的是同一份数据（无双轨）。

### I. 生命周期
22. 作为维护者，记忆**不自动衰减/过期**；矛盾由新记忆显式 update 覆盖旧条、或人工删除解决。

## 4. 数据模型（示意，实现可微调）

```sql
CREATE TABLE IF NOT EXISTS workspace_memory (
    id           BIGSERIAL PRIMARY KEY,
    workspace_id BIGINT       NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
    type         VARCHAR(20)  NOT NULL,          -- preference|decision|fact|pitfall
    title        VARCHAR(200),                    -- 面板/查重用的短标（可选）
    content      TEXT         NOT NULL,
    embedding    vector,                          -- pgvector；未配置/失败时为 NULL
    enabled      BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_ws_memory_ws_enabled ON workspace_memory (workspace_id, enabled);
-- 向量索引仅在扩展可用时建；维度以所选 embedding 模型为准（见 D-3）
```

> `vector` 需固定维度方可建向量索引：实现时按 embedding 模型维度落定（如 1024/1536），
> 或先用无索引的 `vector` 精确检索起步。此为 `to-tickets` 里「DB 迁移」票需拍板的点。

## 5. 已定决策

- **D-1 作用域**：记忆按 `workspace_id` 隔离，挂 session 之上；同 workspace 多 session 共享。
  （翻案成本高：改账户级要动全部归属与召回 SQL）
- **D-2 embedding 配置落点**（已定 2026-10-08）：**扩展 `model_config` 增加 `kind`（'chat'|'embedding'）**，
  复用其 `owner_user_id` 过滤与默认回落，隔离逻辑不写第二套。曾考虑的备选 `app_config` 全局三件套
  `llm.embed_*` 已否掉（会多一套 owner 判据、且无法复用模型管理页）。维度变更仍走 D-3。
- **D-3 向量维度**：随 embedding 模型固定；维度变更=迁移，标注为破坏性变更。
- **D-4 查重强度**：create 前检索相似条并**提示**，不硬拦（Agent 仍可选择新建），避免误伤近义不同义。
- **D-5 删除语义**：`delete` 采用**软删（enabled=false）优先、面板提供硬删**；工具侧默认软删，可撤销。
- **D-6 降级检索实现**（已定 2026-10-09，006 实现时翻案）：**复用 002 的 Python 侧关键词打分**，
  不引入 `pg_trgm`/`tsvector`。曾定的「优先 Postgres 全文」已否掉：工作区级记忆量为几十条量级，
  扩展依赖 + GIN 索引 + 一份平行的 SQL 检索路换来的收益为零，且中文二元滑窗已在 Python 侧实现。
  外部检索组件依旧不引入。
- **D-7 注入位置**：`<memories>` 与 `<context_summary>` 同由 `build_system_prompt` 之后的
  `_build_messages` 拼接追加，不改 `build_system_prompt` 签名以外的 prompt 生成结构。

## 6. 测试决策（接缝处）

- **主靶心 = `recall_memories`**：用 fake embedding provider（可控返回向量）+ 真实/内存 DB fixture，
  覆盖向量命中、阈值过滤、topK 截断、token 封顶、**未配置降级**、空结果不注入。
- **`_build_messages`**：`ScriptedLLM.received` 断言 `<memories>` 块的**存在性/内容/位置**，
  及无记忆时无空块（复用现成 ScriptedLLM 快照机制，注意其 received 存的是快照）。
- **`execute_tool("memory_write")`**：create 成功、update/delete 目标不存在报错、type 非法报错、
  空 content 报错、**敏感值脱敏后入库文本**（断言具体脱敏结果，而非「没抛异常」）。
- **owner 隔离**：A 会话召回/CRUD 触及 B workspace id 的越权断言（404/ToolError）。
- **迁移**：pgvector 可用则建表+向量索引成功；模拟扩展缺失则迁移明确失败（不静默）。
- 遵循边界优先：0 条 / 1 条 / 恰好满 K / 超 K 截断 / 阈值边界 ±1 / embedding 超时返回空 /
  中文与特殊字符 content / 大整数 id 按字符串不截断。

## 7. 明确不做

- 不做跨 workspace/账户级全局记忆共享（严格工作区级）。
- 不做执行会话全文日志的向量化与检索（只存原子记忆条目）。
- 不做自动衰减/过期淘汰/后台定时提炼（写入只走 Agent 工具 + 用户手动）。
- 不引入独立向量数据库/外部检索服务（用同栈 pgvector + Postgres FTS）。
- 不改对话主链路既有行为（压缩、技能注入、todo、steering 插话等保持原样）。
- 不在本期做记忆的跨语言检索优化、rerank 模型、记忆置信度评分。

## 8. 补充说明

- **部署前置（已完成，2026-10-08）**：平台库容器 `zerocode-pg16` 原为 `postgres:16-alpine`（不含 pgvector），
  现已换为 `pgvector/pgvector:pg16`（Postgres 16.15 / Debian glibc，vector 0.8.7）。因 musl/glibc 不能复用
  PGDATA，数据走 `pg_dumpall --clean --if-exists` 导出后导入**新卷** `zerocode-pgdata-v2`，旧卷 `zerocode-pgdata`
  保留作回滚点。重建时三项不可变（已实测通过）：容器名 `zerocode-pg16` 与 `--network zcnet`（已发布应用
  容器靠该 DNS 别名连库）、原 env 三件套、`5433:5432` 端口映射；pg_hba 与原容器逐行一致
  （local trust / loopback trust / 末尾 `host all all all scram-sha-256`）。因此本 feature 的第一颗票是
  **环境票**，不是代码票；`CREATE EXTENSION vector` 失败必须让启动明确报错而非静默降级。

- **注入 token 成本**：`<memories>` 与摘要/todo/技能清单叠加，需靠 token 封顶与阈值兜住；
  阈值/条数默认值建议起步保守（topK 5~8、阈值经验值待压测），随反馈调。
- **与技能的关系**：技能是「账户级、跨工作区的做法指令」，记忆是「工作区级、本项目的事实沉淀」，
  两者都在 system 注入但语义正交，不合并、不互相替代。
- **可观测**：建议在 WS 事件流里给 `<memories>` 召回结果留一条可展开的 status/trace（复用现有
  tool_result/status 通道），便于用户看清「Agent 这次看了哪几条记忆」——非必需，作为增强票。
- **首版最小闭环建议**：DB迁移+表 → recall_memories（向量+降级）→ `_build_messages` 注入 →
  `memory_write` 工具 → 脱敏 → 前端面板。可按此顺序切示踪弹。
