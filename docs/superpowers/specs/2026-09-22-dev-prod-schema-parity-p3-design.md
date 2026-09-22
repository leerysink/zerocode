# zerocode 设计文档 — 应用 dev/prod 结构等价 · P3 打磨段（破坏性变更确认交互 + 回填脚手架）

日期：2026-09-22　范围：`apps/<slug>/` 发布流水线中破坏性结构变更的**人工确认交互**与**数据轨回填脚手架**　前置：`docs/superpowers/specs/2026-09-22-dev-prod-schema-parity-design.md`（§5 differ 破坏性边界、§8.2 P3）+ 已交付的 P2 核心机制（`schemadiff.py` 破坏性闸门、快照归档 `releases/<version>/schema.sql`、双轨 fold）

## 1. 目标与非目标

### 目标
把 P2 遗留的「破坏性变更 = 抛一段红字硬阻断 + 人工去翻配置」升级为**可用的确认式工作流**：

1. 发布被破坏性闸门拦截时，前端呈现**结构化清单**（删了哪些表/列、哪些列类型变更），而非一段截断文本。
2. 提供**一次性人工放行**：用户显式确认「这些变更是有意的、自行承担数据风险」后，本次发布放行（并把当前 `schema.sql` 归档为新基线），**不改动持久配置**。
3. 提供**回填脚手架**：从确认弹层一键在 `apps/<slug>/sqlchange/` 生成一份**带注释的数据轨 D 文件骨架**，引导用户/AI 走两步走（先加新列双写、补回填，下版才移除旧列）。

### 非目标（YAGNI）
- **不做发布前的同步预览弹层**：确认在发布流水线内进行（首次发布尝试被拦 → 富化收口事件 → 用户确认 → 带一次性标志重发），与 `chat_active` 的「受理前同步 409」刻意不同。
- **不臆造回填业务逻辑**：脚手架只产出注释 + 幂等占位（`UPDATE ... WHERE ... IS NULL`），具体转换由人/AI 填实（对齐主 spec §19「结构 diff 不自动推导需计算的数据回填」）。
- **不自动改 `schema/schema.sql`**、不自动执行任何 `DROP`（fold 永远 `IF NOT EXISTS`，破坏性只体现在「从声明式真相移除」这一意图上）。
- **不引入持久化的「记住已确认」状态**：放行是一次性、每次都要人显式点。
- 不改平台自身库、不改发布流水线 `STEPLIST`、不新增平台表。

## 2. 现状与 P2 落点（复用基础）

- P2 已在 `prod.publish()` 的 preflight 步内、dev/prod 等价校验之后加入破坏性闸门：`diff(releases/<prev>/schema.sql, schema/schema.sql)`，命中 `destructive_message()` 非空即 `raise DeployError`；发布成功后把当前 `schema.sql` 归档到 `releases/<version>/schema.sql`。
- 闸门以 `schema/schema.sql` 存在 + `prev_version` 存在 + 上一版有归档快照为前置；存量应用无结构轨时自动跳过。
- 兜底开关 `deploy.skip_schema_destructive`（持久配置）已生效。
- 前端 `PublishModal.vue` 已有 `force` 发布链与 `chat_active` 的 409 → `Modal.confirm` → 带 `force` 重发先例；`publish_done{ok:false}` 目前只把 `error` 文本显示为一段 `failText`。

## 3. 方案

### 3.1 后端：闸门升级为「可确认」
- `publish(sf, app, notify=None, *, force=False, confirm_destructive=False)` 新增 `confirm_destructive`。
- 破坏性闸门判定命中后，若 `confirm_destructive` 为真**或**持久 `deploy.skip_schema_destructive` 为真 → 跳过（记录「人工确认放行」步骤日志）；否则抛 `DeployError`。
- 抛出的 `DeployError` 携带结构化负载（在异常对象上挂 `payload: dict`）：
  ```python
  {"code": "schema_destructive",
   "changes": [ ...破坏性明细，来自 destructive_changes()... ],
   "diff": {"dropped_tables": [...], "dropped_columns": {...}, "type_changed": {...}}}
  ```
- 失败收口的 `done_payload(False, error)` 读取 `getattr(exc, "payload", None)`，把 `code` 与 `destructive`（=changes 列表）并入 `publish_done` 事件（**新增可选字段，向后兼容**：现有消费者忽略即可）。
- 确认重发：前端带 `confirmDestructive=true` 再次 `publish` → 闸门放行 → 正常走完并归档本版快照为新基线。

### 3.2 后端：共享 IO 助手（去重）
`prod.compute_schema_diff(root, prev_version) -> dict | None`
- 读 `root/releases/<prev_version>/schema.sql` 与 `root/schema/schema.sql`；任一缺失或 `prev_version` 为空 → 返回 `None`（无基线可比）。
- 否则 `schemadiff.schema_diff(parse_schema(prev), parse_schema(cur))`。
- 破坏性闸门与回填脚手架端点**共用**此函数，避免重复文件读取与路径口径漂移。

### 3.3 后端：回填脚手架
- 纯函数 `schemadiff.backfill_skeleton(diff, *, today) -> str`（无 IO、无 docker，可单测）：
  - 从 `dropped_columns`、`type_changed`、以及「同表内 drop+add 推断的改名候选」生成注释 + 幂等占位；
  - 文件头写明「用途 / 影响表 / 两步走指引 / 本文件为骨架需补实」；
  - 每个受影响列给 `-- TODO 回填 <new>←<old>` + 一条 `UPDATE <t> SET <col>=<expr?> WHERE <col> IS NULL;` 占位（`<expr?>` 留待人填）；
  - 无破坏性 → 返回空串。
- 端点 `POST /apps/{app_id}/sqlchange/scaffold-backfill`（202 之外，同步）：
  1. `compute_schema_diff(root, current_version)`；为 `None` → 400「无上一版结构基线可比对」；
  2. `destructive_changes(diff)` 为空 → 400「未检出破坏性变更，无需回填」；
  3. 否则生成 `backfill_skeleton`，按 `D<today>_<下一个两位序号>_backfill.sql` 写入 `apps/<slug>/sqlchange/`（**只新增、绝不覆盖**：序号取当日 `D<today>_*` 现有最大值 +1）；
  4. 返回 `{filename, content}`。
  - 路径来自 `engine._app_root`；仅 `stack=vue3-fastapi` 且有 `schema/schema.sql` 有意义，其余按前置返回 400。
  - 与 store 打包无冲突：store 锚点取自 `sqlchange_history`（DB），未发布应用的半途 D 文件不进包。

### 3.4 前端：PublishModal 阻断态富化
- `useDeploy` 已透传 `lastDone`；本次让 `publish_done` 携带的 `code` / `destructive` 可被读到。
- `doneFail` 分支若 `lastDone.code === 'schema_destructive'`：
  - 结构化渲染 `destructive` 清单（分组：删除的表 / 删除或改名的列 / 类型变更列）；
  - 动作按钮一：**「我已确认风险，继续发布」** → `publishWith(force, confirmDestructive=true)`（复用现有重试/受理链）；
  - 动作按钮二：**「生成回填骨架」** → 调 `scaffoldBackfill(app.id)`，成功后 `message` 提示「已写入 `sqlchange/<filename>`，请在 AI 会话补实回填逻辑后重新发布」；
  - 普通失败（非 `schema_destructive`）保持现有 `failText` + 重试 + 「把错误日志发给 AI」不变。
- `api/index.js`：`publishApp(id, force, confirmDestructive)` 扩参；新增 `scaffoldBackfill(id)`。

## 4. 数据流（破坏性发布一次完整往返）

```
用户点「发布」 → publish(force=false)
  → preflight：fold 校验 ok → compute_schema_diff(prev, cur) 命中破坏性
  → raise DeployError(payload.code=schema_destructive, changes=[...])
  → 记账 failed/preflight → publish_done{ok:false, code, destructive:[...]}
前端弹层渲染清单 → 用户点「生成回填骨架」
  → POST scaffold-backfill → 写 D<today>_NN_backfill.sql → 返回文件名
  → 提示去 AI 会话补实
（可选）用户点「我已确认，继续发布」
  → publish(force, confirmDestructive=true) → 闸门放行 → 走完 7 步
  → record 成功后归档 releases/<version>/schema.sql（新基线）
```

## 5. 影响面

| 位置 | 改动 |
|---|---|
| `deploy/prod.py` | `publish` 增 `confirm_destructive`；闸门命中改抛带 `payload` 的 `DeployError`；新增 `compute_schema_diff(root, prev_version)`；`done_payload` 并入 `code/destructive` |
| `deploy/schemadiff.py` | 新增纯函数 `backfill_skeleton(diff, *, today) -> str` |
| `api/apps.py` | `PublishIn` 增 `confirmDestructive`；新增 `POST /{app_id}/sqlchange/scaffold-backfill` |
| `frontend/src/components/PublishModal.vue` | `doneFail` 增 `schema_destructive` 富化分支 + 两按钮 |
| `frontend/src/api/index.js` | `publishApp` 扩参 + `scaffoldBackfill` |
| 测试 | `test_schemadiff`（骨架）、`test_prod_engine`（放行/结构化/归档）、apps 端点测试、`PublishModal.test.js`（阻断分支） |

## 6. 关键取舍记录
- **发布中一次性放行 vs 发布前同步预览**：选前者，复用已有 `publish_done` + 重试链，改动面最小、不新增受理前闸门；代价是用户会先看到一次「被拦」的收口（本就是确认语义的一部分）。
- **结构化负载走 publish_done 新字段 vs 新事件类型**：选前者（additive），与 `chat_active` 用结构化 detail 驱动确认弹层的口径一致，不扩事件面。
- **脚手架只给骨架 vs 推导回填**：只给骨架，守 spec §19 边界，避免把猜测的转换逻辑写进会真在生产执行的数据轨。
- **持久配置跳过 vs 一次性确认**：默认硬阻断 + 一次性人工确认；持久 `deploy.skip_schema_destructive` 仅保留为运维兜底，UI 不再诱导长期开启。
