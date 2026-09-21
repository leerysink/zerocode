# zerocode 设计文档 — 应用 dev/prod 数据库结构等价（双轨真相：结构声明式 + 数据显式）

日期：2026-09-22　范围：应用级 `apps/<slug>/` 的 dev 库与 prod 库迁移等价性　前置：docs/superpowers/specs/2026-09-18-zerocode-deploy-suite（规格 06 分库供给 / 07 迁移闸门）

## 1. 目标与非目标

### 目标
让「AI 在 dev 上迭代出来的库结构」与「发布到 prod 后从零跑出来的库结构」**保证等价**，且这种等价**由构造保证**、而不是靠发布后补救式校验。具体：

1. 消除 dev 库真实结构与迁移文件之间的**漂移（drift）**：AI/人直接对 dev 跑临时 DDL、或回改已应用文件导致的偏差。
2. 消除「增量迁移文件被反复增删改、顺序错乱、改了没重放」这一 AI 迭代期最高频痛点。
3. 消除 prod 从零 fold 与 dev 增量结果不一致的隐患，包括**种子/配置数据**翻倍或缺失。
4. 提供发布前的等价性保障，并保留「把 dev 当前结构差异导出为迁移」的兜底路径。

### 非目标（YAGNI）
- 不改平台自身库（仓库根 `changelog/` + `app/db.py::run_migrations`），本设计只作用于应用级 `apps/<slug>/`。
- 不做多租户 / 跨库分布式迁移；不引入 ORM / Alembic 作为结构真相（后端维持裸 SQL 现状）。
- 不做 prod 数据自动回滚（迁移失败沿用「旧版本继续服务」）；不做跨版本双向自动 down migration。
- 结构 diff 不试图自动推导「需要计算的数据回填」——这类动作显式落数据轨。

## 2. 现状与根因

### 2.1 现有机制
- 每应用一个 `apps/<slug>/sqlchange/`，放线性 `V<日期>_<序号>_<说明>.sql`；一应用两库 `<slug>_dev` / `<slug>_prod`（规格 06）。
- `app/deploy/sqlrunner/run.sh` 用 `sqlchange_history(filename, checksum, success, duration_ms)` 做增量：已应用且校验和一致 → SKIP；已应用但文件被改 → TAMPER 退出 3；执行报错 → FAIL 退出 2。
- dev 迁移在预览时跑（`api/apps.py::_run_sqlchange`）；prod 迁移在「发布」的 migrate 步跑（`deploy/prod.py`，空库从零 fold 全部文件）。

### 2.2 根因
当前设计把「增量迁移文件」当作唯一真相，要求「谁都不许回改已应用文件」。但 AI 在快速迭代中恰恰不断回头编辑结构、拆分/合并迁移、甚至直接对 dev 跑临时 DDL。只要 dev 真实状态 ≠ `fold(sqlchange 文件)`，prod 从零跑同一批文件就会得到不同结果。四类具体表现：dev 被手改漂移、文件被反复增删改、prod 无法验证等价、种子/配置数据差异。

> 结论：加「发布后 diff 校验闸门」只是补救，未消除病因。治本之道是**改变真相的载体**。

## 3. 核心方案：双轨真相

把「结构」与「数据」拆成两条独立、语义不同的轨道，应用目录下形成两类产物：

### 3.1 结构轨 —— `schema/schema.sql`（声明式当前快照）
- 一份**全量、幂等**的 DDL（`CREATE TABLE IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS` / `ADD COLUMN IF NOT EXISTS` 等），只描述「应用结构最终长什么样」，**不含任何数据**。
- AI 要改表结构 = **编辑这一份目标态**，不再手写/回改结构增量文件。
- 同时是 AI 写后端查询时的**权威结构参考**（读它即知表/列/索引，无需再猜或翻历史）。

### 3.2 数据轨 —— `sqlchange/D<日期>_<序号>_xxx.sql`（显式数据变更）
- 语义收窄：只放**无法从结构推导、必须显式声明且需在 prod 执行**的数据动作——初始种子、必要回填、字典刷值。
- **append-only、不可变、幂等、按序执行**：种子一律 `ON CONFLICT ... DO NOTHING`；回填脚本只追加不回改。
- 用 `sqlchange_history` 记账（沿用现有），只跑「上次发布之后新增的」。

> 双轨切分把最高频、最易出错的「手工维护结构增量文件」交给机器推导；人工/AI 只需描述「最终要什么结构」+「不可自动推导的数据动作」。

## 4. 生命周期与数据流

### 4.1 dev：投影，可丢弃重建
- 预览 migrate 对 dev 做两件事：① 幂等 fold `schema/schema.sql`；② 增量跑数据轨脚本。
- 因 `schema.sql` 幂等全量，AI 任意改都不会让 dev 漂移；额外提供**「重置 dev」**：drop dev → 重新 provision → fold 快照 + 全量数据脚本。dev 从此是可丢弃投影。

### 4.2 prod：发布时由「快照 diff」生成结构迁移
- 平台为**每个已发布版本归档一份 schema 快照**（`releases/<version>/schema.sql` 或落库）。
- 发布第 N 版：`结构迁移 = diff(第 N-1 版快照, 当前 schema.sql)`，由轻量 differ 生成前向 DDL（`CREATE/ALTER`）。
- prod migrate = 执行该 diff DDL ＋ 执行发布区间内新增的数据脚本。

### 4.3 等价性（构造性保证，不靠事后校验）
```
dev_after  = fold(当前 schema.sql)
prod_after = fold(历史所有 diff) = fold(当前 schema.sql)   # diff 串联恰把基线推成当前态
```
两边都等于同一个「当前快照」，故**结构等价由构造成立**。事后 `pg_dump schema-only` diff 从「必需闸门」降级为「可选自检」。

## 5. differ 引擎与破坏性变更边界

- **实现取向**：自研轻量 differ（解析幂等 DDL，抽取「表 / 列(类型/约束) / 索引 / 约束」集合，做集合差 → 生成前向 DDL），无 ORM 依赖，够用即可。P1 的 `pg_dump schema-only` diff 能力正好复用为其实现与验证工具。
- **改名 / 类型收窄**：differ 看成 `DROP + ADD`（丢数据）。策略：**破坏性变更（`DROP COLUMN/TABLE`、类型收窄）默认只生成告警、不自动执行，需人工确认**；要保数据则在数据轨补一条回填脚本。
- **幂等兜底**：dev fold 与快照均用 `IF [NOT] EXISTS` 保证可重放；prod diff 为一次性前向 DDL。

## 6. AI 工作流与工具改造

- 改结构 → 编辑 `schema/schema.sql` 目标态；改数据 → 追加 `sqlchange/D*.sql`。
- 写后端查询前先读 `schema/schema.sql` 取权威结构。
- 规范注入：`workspace_system_prompt` 与 `sqlchange/README` 从「一切建表改表都成为版本文件」改写为「**结构改 `schema.sql` 目标态、数据才进 `sqlchange/`**」双轨口径。
- 工具守卫：写文件工具对 `schema.sql` 做静态校验（`DROP` 须确认、语法粗校验）；`prod.scan_sqlchange` 命名闸门改为「数据轨 `D*` + 结构快照存在性」。

## 7. 代码 / 模板影响面

| 位置 | 改动 |
|---|---|
| `templates/vue3-fastapi` | 新增 `schema/schema.sql`；`sqlchange/` 语义改数据轨；改 README / 启动 banner |
| `deploy/compose.py` | migrate 服务挂载拆为 `../schema` + `../sqlchange` 两份 |
| `deploy/sqlrunner/run.sh` | 逻辑分叉：dev = fold `schema.sql`(全量幂等) + 增量数据脚本；prod = 执行平台传入的 diff DDL + 增量数据脚本；两者用 `sqlchange_history` 记账 |
| `deploy/sqlrun.py` | `run_sqlchange` 增 env 分支、多挂 `schema` 目录、prod 侧接收 diff 产物 |
| `deploy/prod.py`（发布 migrate 步） | 先 `diff(prev_release 快照, 当前 schema.sql)` 落结构迁移再执行；发布成功后归档本版快照 |
| 新增 `deploy/schemadiff.py` | 轻量 differ：解析幂等 DDL → 集合差 → 前向 DDL；破坏性变更只告警 |
| `api/apps.py` | 加「重置 dev」端点；`/sqlchange/files` 待办清单口径改为数据轨 |
| 测试 | 改 `test_sqlrunner`；新增 differ 单测 + 等价性集成测试（dev 全量 fold 与 prod 逐步 diff 后 schema 相等） |

## 8. 存量迁移与分期落地

### 8.1 存量 apps（test1 / statistics）一次性搬迁
- 从各自 dev 库 `pg_dump --schema-only` 反向生成当前 `schema/schema.sql` 作为基线快照；
- 现有 `sqlchange` 的**结构文件归档退役**、**数据/种子文件保留为数据轨**；
- 把首个已发布版本登记为「基线快照」，后续发布即走 diff。

### 8.2 分期（每段独立可上线，风险递减）
- **P1 · 止血（≈路线 A 安全网，改动小）**：加「dev 一键重建」+「发布前 schema-only diff 校验闸门」。立刻消除大部分漂移，且不依赖 differ。
- **P2 · 治本（路线 C 核心）**：引入 `schema.sql` 声明式 + `schemadiff.py` 机器生成 prod 结构迁移 + 快照归档。此后等价性由构造保证，P1 校验闸门降级为可选自检。
- **P3 · 打磨**：破坏性变更确认交互、回填脚本脚手架、AI 提示词收尾。

> P1/P2 不冲突：P1 的 diff 能力正好复用为 P2 differ 的实现与验证工具。

## 9. 关键取舍记录

- **结构声明式 vs 增量文件**：选声明式，以「改名/收窄需人工确认」为代价，换取根除漂移与 AI 回改之痛。
- **构造性等价 vs 事后校验**：选构造性等价，校验降级为自检。
- **数据不自动 diff**：数据变更显式落 append-only 数据轨，接受「数据脚本仍需幂等纪律」，但范围被压缩到极小。
- **无 ORM 依赖**：后端保持裸 SQL，differ 自研轻量集合差，避免引入模型层改造。