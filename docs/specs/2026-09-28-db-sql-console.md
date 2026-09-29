# 查询控制台升级（SQL 编辑器）规格说明

**日期**：2026-09-28  **来源**：grill-with-docs 访谈  **术语**：见 [CONTEXT.md](../../CONTEXT.md)「查询控制台 / 查询页签 / 保存 / 执行段 / 语句拆分 / 逐条串行遇错即停 / 确认词」  **ADR**：[0006 内核选 CodeMirror 6](../adr/0006-sql-editor-codemirror6.md) · [0007 前端拆分 + 批量端点](../adr/0007-sql-batch-split-frontend-serial-stop.md) · [0008 页签走 localStorage](../adr/0008-sql-tabs-localstorage-no-server-store.md)

## 1. 问题陈述

DB 操作台的查询控制台现在是一个 4 行 `a-textarea`（[DbAdminView.vue#L184-L196](file:///d:/project/zerocode/frontend/src/views/DbAdminView.vue)），实际使用中五件事不成立：

- 无语法高亮，一屏 SQL 靠肉眼找关键字；
- 无格式化，从别处粘进来的语句排版全乱；
- 只有一个输入框，写第二个查询要把上一个删掉，切表/切环境还会被 `watch(env)` 直接清空；
- 不保存，刷新页面 SQL 就没了；
- **不支持分号分隔的多条语句**——后端 `execute_sql` 把 SELECT 外层包成 `SELECT * FROM (<sql>) LIMIT 5001`，带分号的多语句必然被这个包裹搞成语法错误。

这不是"体验优化"，是管控台的核心用法（手工排查数据、批量修正、临时建索引）根本跑不起来。

## 2. 方案概述

- **内核**：查询控制台整体抽成独立组件 `SqlConsole.vue`，编辑器用 CodeMirror 6 + `@codemirror/lang-sql`；格式化用 `sql-formatter`（postgresql 方言）；新增前端依赖，后端零新依赖。
- **查询页签**：控制台内做页签栏，可新建/重命名/关闭，每页签一份 SQL 文本与一份结果块列表；页签按 `(appId, env)` 各自成一套，切环境即切工作区。
- **保存**：页签列表与 SQL 文本写 localStorage（只存脚本不存结果），刷新后原样恢复；配额异常降级为"停止自动保存 + 黄条提醒"。
- **多语句**：前端状态机做语句拆分 → 新端点 `POST /api/apps/{id}/db/{env}/sql/batch`，服务端**同一个连接内**逐条串行遇错即停，已成功的不回滚，逐条写审计。
- **执行范围**：有选区只跑选区，无选区跑全段；`Ctrl+Enter` 执行、`Ctrl+Shift+F` 格式化。
- **确认与 prod 保护**：段内含非只读语句时一次确认，弹框列出每条语句的 op 徽章、无 WHERE 的 update/delete 与 DDL 标红；`env=prod` 且含非只读语句时须输入确认词 `prod` 才解禁"执行"。
- **结果**：一条语句一个**结果页签**（二级页签，挂在当前查询页签之下），页签内是第 i/N 条 + op + 耗时 + 表格/影响行数/错误原文；失败页签标红、未执行页签灰显，末尾汇总条常驻。
- **结果页签**：只有 1 条结果时不显示页签栏；执行完成后默认停在第一个失败页签（无失败停第 1 条）；页签本身**不写 localStorage**（Ticket 009）。
- **补全**：表名与列名自动补全，数据源复用现有 `dbTables` / `dbTableSchema`，不新增接口。

## 3. 用户故事

编号可独立验证。

- **US-01** 作为 owner，我在查询控制台输入 `select id,name from widgets where name='a'`，关键字、字符串、数字、注释呈现不同颜色。
- **US-02** 作为 owner，我点「格式化」（或按 `Ctrl+Shift+F`），全段 SQL 按 PostgreSQL 方言重排为多行缩进、关键字大写；**有选区时只重排选中部分**。
- **US-03** 作为 owner，我能点「+」新建第二个查询页签，两个页签的 SQL 文本与结果互不干扰；页签可双击重命名、可关闭。
- **US-04** 作为 owner，我写完三个页签的 SQL 后刷新页面（或关掉浏览器重开），三个页签及其内容、当前激活页签都还在。
- **US-05** 作为 owner，我的执行结果**不会**被恢复——刷新后结果区是空的，这是刻意约定（结果不进 localStorage）。
- **US-06** 作为 owner，我切到 prod 环境时看到的是 prod 那一套页签，dev 写的脚本不会出现在 prod 里；切回 dev 时 dev 的页签原样还在。
- **US-07** 作为 owner，我在一个框里写 `SELECT 1; SELECT 2;` 点执行，得到两个结果页签（各 1 行表格），不再报语法错误。
- **US-08** 作为 owner，我执行 `INSERT ...; INSERT ...; SELECT * FROM t;` 这类混合段，三条按顺序都生效，三个结果页签分别显示"影响 1 行""影响 1 行""1 行表格"。
- **US-09** 作为 owner，中间某条写错（如第 2 条引用不存在的表）时，第 1 条的数据**已落库保留**，第 2 个结果页签显示 PG 错误原文并**被自动选中**，第 3 个页签灰显"未执行"，末尾汇总"共 3 条，成功 1，失败于第 2 条，剩余 1 条未执行"。
- **US-10** 作为 owner，我只选中第 2 和第 3 条语句再点执行，只跑选中的这两条。
- **US-11** 作为 owner，我的 SQL 里含 `'a;b'`、`-- 分号 ; 在注释里`、`/* ; */`、`$$ ... ; ... $$` 时，语句拆分不会在这些位置错切。
- **US-12** 作为 owner，执行段里有任何非只读语句时弹一次确认框，框内逐条列出语句与 op 徽章；纯只读段（全 SELECT）直接执行不弹框。
- **US-13** 作为 owner，我在 prod 环境执行含写/DDL 的段时，确认框的"执行"按钮初始禁用，必须输入 `prod` 才解禁；prod 下纯 SELECT 不需要输入。
- **US-14** 作为 owner，一次批量执行里的每条语句在「操作历史」抽屉中各有一条审计记录（含各自 op、状态、耗时），未执行的语句不产生审计。
- **US-15** 作为 owner，我输入 `select * from ` 时补全出当前环境的表名，输入 `widgets.` 时补全该表列名。
- **US-16** 作为 owner，我的 SELECT 在批量执行里仍受行数上限保护，超限显示"结果过大，仅显示前 N 行"。
- **US-17** 作为 owner，「数据」页签的行内编辑「保存」行为完全不变（仍走单条 `/sql` 端点、仍单次确认）。
- **US-18** 作为非归属用户访问别人的应用 DB，批量端点与现有端点一样返回 404（不区分 403）。
- **US-19** 作为 owner，localStorage 写入超出浏览器配额时，我得到黄条"本地存储已满，本次不再自动保存"，编辑器里的文本不被清空或截断。
- **US-20** 作为 owner，应用尚未供给数据库（凭据缺失）时执行，得到与现有一致的 409 提示，不返回半成品结果。
- **US-21** 作为 owner，一个框里跑 5 条语句时结果区是 5 个结果页签而不是 5 块纵向堆叠，我点第 3 个只看第 3 条；只跑 1 条时看不到页签栏；刷新后结果区仍为空（结果与结果页签的停留位都不进 localStorage）。
- **US-22**（Ticket 010）作为 owner，「数据」页签的表格最右侧有一列固定的「操作」列，每行一个「编辑」按钮；点它弹出一个编辑框（主键列只读展示、其余列可改），改完点保存仍是原来那一次确认 + 单条 `UPDATE`。表格下方的常驻编辑面板不再有，点行本身不再有任何副作用；表无主键时按钮禁用并说明原因。

## 4. 已定决策

| 维度 | 决策 |
|---|---|
| 编辑器内核 | CodeMirror 6（非 Monaco、非 textarea 叠层高亮层）→ ADR 0006 |
| 格式化 | 前端 `sql-formatter`，postgresql 方言，仅手动触发（按钮 + `Ctrl+Shift+F`），不自动格式化 |
| 多页面 | 查询控制台内页签栏（非"每表一页签"、非整页多实例） |
| 保存 | 只写 localStorage，按 `(appId, env)` 分区；不建服务端脚本库 → ADR 0008 |
| 持久化内容 | 页签名称/顺序/激活索引/SQL 文本；**不含结果、不含结果页签的停留位** |
| env 关系 | 页签按 env 隔离，切环境=切整套页签；替换掉原 `watch(env)` 清空语义 |
| 拆分责任方 | 前端自写状态机（不用 CodeMirror 语法树、不用后端解析器）→ ADR 0007 |
| 执行契约 | 新端点 `/sql/batch` 收语句数组；保留单条 `/sql` 端点不动 |
| 执行语义 | 同连接逐条串行、遇错即停、已成功不回滚 |
| 护栏 | 逐条沿用现状：SELECT 加 `statement_timeout=15s` + 外层 `LIMIT 5001`，写/DDL 不加超时；**不设段内语句数/长度上限** |
| 执行范围 | 选区优先，无选区跑全段；`Ctrl+Enter` 执行 |
| 确认粒度 | 一次确认 + 逐条 op 清单 + 高危标红 |
| 确认门槛 | 以"是否只读"为准，沿用现有 `isSelect` 的**严格**语义（`SELECT ... INTO`、`setval/nextval/pg_sleep/call/perform` 等归为非只读、要确认）；op 徽章用 `classify_op`，两者用途不同、允许不同 |
| prod 保护 | env=prod 且段内含非只读语句 → 须输入确认词 `prod` |
| 结果展示 | 一条语句一个结果页签（≥2 条才显栏），失败页签标红 + 未执行页签灰显 + 底部汇总条常驻（Ticket 009） |
| 结果页签落点 | 执行回来后有失败停在第一个失败页签，无失败停第 1 条；重执行后结果变少则激活位钳回 0 |
| 附带范围 | 仅表名/列名补全 |
| 审计 | 逐条写，`skipped` 不写；批量端点不再写"整段一条" |

## 5. 接缝与改动点

**顶层接缝只有一个**：后端 `POST /api/apps/{id}/db/{env}/sql/batch`。编辑器内部（高亮/页签/格式化）不作为跨系统契约，靠纯函数下沉到 `src/utils/` 单独测。

### 5.1 新增前端依赖（`frontend/`，用 npm）

`codemirror`、`@codemirror/lang-sql`、`@codemirror/autocomplete`、`sql-formatter`。沿用现有 huaweicloud registry，不动 `requirements.txt`。

### 5.2 `src/utils/sqlSplit.js`（新，纯函数）

```js
splitSqlStatements(text) -> [{ sql, startLine }]   // 空段/纯空白段/纯注释段丢弃
```

手写状态机（约 40 行，不依赖解析库），必须正确跳过：单引号串（含 `''` 转义）、双引号标识符（含 `""`）、`--` 行注释、`/* */` 块注释（**PG 允许嵌套**，需深度计数）、`$$...$$` 与 `$tag$...$tag$`。未闭合的引号/注释**降级为"整段作为一条返回"**，不抛异常（用户手打一半不该让按钮失灵）。

### 5.3 `src/utils/sqlClassify.js`（新，纯函数）

```js
classifyOp(sql)          // 与后端 classify_op 一一对齐的 JS 镜像，仅用于 op 徽章展示
isReadOnlySelect(sql)    // 从 DbAdminView 现有 isSelect 迁移，语义不变（严格）
riskFlags(sql, op)       // -> ['no_where' | 'ddl']，update/delete 无 \bwhere\b 或 ddl 时给高危标记
planExecution(stmts, env)// -> { needConfirm, requirePhrase, items: [{sql, op, risk, startLine}] }
```

`requirePhrase = env === 'prod' && items.some(i => !isReadOnlySelect(i.sql))`。
权威口径在**服务端** `classify_op`（审计落库用它）；JS 镜像只负责展示。**必须**把后端 `tests/test_db_admin.py` 里 classify 用到的样例作为共享用例清单搬进前端测试，防两处漂移。

### 5.4 `src/utils/sqlTabsStore.js`（新，纯函数）

```js
loadTabs(appId, env) -> { version, active, tabs: [{ id, name, sql }] }
saveTabs(appId, env, state) -> { ok: true } | { ok: false, reason: 'quota' }
```

- key：`zc.dbq.tabs.{appId}.{env}`。
- 无 key / JSON 解析失败 / `version` 不匹配 → 返回默认单页签（`query_1`，空文本），不抛。
- 页签数上限 12，超出拒绝新建并返回提示。
- `QuotaExceededError`（含 happy-dom 下的等价异常）→ `{ok:false, reason:'quota'}`，调用方显示黄条且**后续不再尝试写入本次会话**。

### 5.5 `src/components/SqlConsole.vue` + `src/components/SqlEditor.vue`（新）

- `SqlEditor.vue`：**薄壳**，只负责把 CodeMirror 挂到一个 div、双向同步文本、暴露 `getSelectionText()`。一切可测逻辑都不放这里。理由：happy-dom 无布局引擎，CodeMirror 的光标/滚动/选区测不动；把 CodeMirror 隔离成壳，其余全部走纯函数与注入文本。
- `SqlConsole.vue`：页签栏（+ / 重命名 / ×）、工具栏（执行 / 格式化 / 当前 env）、确认弹框、结果页签栏与当前激活结果块（Ticket 009：≥2 条才显栏、只渲染激活那一条、激活位存在页签的运行态字段 `resultIndex` 上）。props：`appId`、`env`、`tables`（补全与表名数据源）；内部持有 tabs 状态并与 `sqlTabsStore` 同步。
- 关闭**非空**页签时弹一次轻确认（与本项目"破坏性动作二次确认"纪律一致）；关闭最后一个页签后自动补一个空白页签（不允许 0 页签）。
- 补全：表名来自传入的 `tables`；列名按当前 SQL 里出现的表名懒拉 `dbTableSchema`，`Map` 缓存 key=`{env}:{table}`，切 env 清缓存，拉取失败静默（只是没有列补全）。
- 主题：`EditorView.theme` 对齐 antd `colorPrimary: #5b7a9d` 基调，等宽字体沿用现有 monospace。

### 5.6 `DbAdminView.vue`（改）

「查询」tab-pane 内容替换为 `<SqlConsole :app-id="appId" :env="env" :tables="tables" />`；删掉本地 `sqlText`/`sqlResult`/`isSelect`/`runSql`（迁至 utils 与 SqlConsole）；`watch(env)` 不再清 `sqlText`（页签由 SqlConsole 按 env 重载），仍清 `selected/schema/rows`。「数据」页签行内编辑与 `dbSqlExec` 单条调用保持不动。

### 5.7 `src/api/index.js`（改）

```js
export const dbSqlBatch = (id, env, statements) =>
  http.post(`/apps/${id}/db/${env}/sql/batch`, { statements })
```

### 5.8 后端 service（`app/services/db_admin_service.py`）

**关键重构**：把 `execute_sql` 的语句体抽出 `execute_one(conn, sql) -> dict`（含 SELECT 的 `statement_timeout` + `LIMIT` 包裹 + 显式 `conn.commit()`），单条 `execute_sql` 与批量 `execute_batch` 都调它——**两条路径共用同一份护栏代码，避免行为漂移**。

```python
async def execute_batch(request, slug, env, statements: list[str]) -> list[dict]
```

- 一个引擎、一个 `async with eng.connect()`，按序 `execute_one` → `await conn.commit()`；
- 任一条抛异常：该条 `{status:'error', error:str(e)}`，其后所有条 `{status:'skipped'}`，**立即 break 不再执行**；前面的提交已生效，不回滚（符合 ADR 0007）；
- 返回 `[{index, op, sql, status, kind, columns?, rows?, truncated?, affected?, error?, duration_ms}]`。

### 5.9 后端 API（`app/api/db_admin.py`）

```python
class SqlBatchIn(BaseModel):
    statements: list[str]

@router.post("/{app_id}/db/{env}/sql/batch")
```

- 顺序与现有端点一致：`_load_app` 归属过滤 → `validate_env` → 委派 service；
- `statements` 为空数组（或全部空白/纯注释，拆分后为空）→ 400，前端提示"没有可执行的语句"；
- 基础设施类异常沿用 HTTPException：非法 env 400、非归属 404、凭据缺失 409；**建连阶段**失败（`async with eng.connect()` 就抛，库不可达）→ 503，与表列表/数据端点的 503 口径一致；
- **语句级失败返回 200**（已拿到连接、只是某条 SQL 报错），错误在 result 项内——否则拿不到前 i-1 条的结果。这与单条 `/sql` 对所有异常一律 500 的行为**故意不同**，单条端点保持不变以免动旧测试；判定分界：`connect()` 之前/之外的异常走 HTTPException，`connect()` 之内逐条执行的异常走 result 项；
- 审计逐条 `log_operation`（`op_type=classify_op(该条)`、`sql_text=该条`、各自 `status/affected/error/duration_ms`），`skipped` 不写。

### 5.10 SQL 变更

**本次无任何表结构或初始化数据变更**（ADR 0008 明确不建表），`changelog/` 不新增文件。

## 6. 测试决策

**接缝**：① `POST /db/{env}/sql/batch`（pytest，真 PG fixture `db_app`）；② `sqlSplit` / `sqlClassify` / `sqlTabsStore` 纯函数（vitest）；③ `SqlConsole` 挂载行为（用 `SqlEditor` 桩 + `localStorage` 桩）。

**边界优先于主流程**，按项目规范边界用例与正常用例分开放：

- `sqlSplit`：空串、纯空白、只有 `;`、尾分号、`'a;b'`、`''` 转义串、`"we;ird"` 标识符、`-- ;` 行注释、`/* ; */`、**嵌套块注释** `/* a /* b ; */ ; */`、`$$ BEGIN ...; END; $$`、`$body$` 具名 dollar-quote、中文与全角字符、未闭合单引号（降级整段一条）、未闭合 `$$`、200KB 长文本不超时、纯注释段被丢弃、恰好一条时无空尾项。
- `sqlClassify`：`SELECT 1`（只读）vs `SELECT ... INTO`（非只读）vs `SELECT setval(...)`（非只读）vs `CALL x()`（非只读）；`update t set a=1`（no_where 高危）vs `update t set a=1 where id=1`；子查询里含 `where` 的假阴性（保守接受，写注释说明）；`CREATE/DROP/TRUNCATE/COMMENT` → ddl；小写与大小写混排；前导空白与换行；空串不抛。
- `planExecution`：全只读段（`needConfirm=false`）、混合段（true）、prod+只读（`requirePhrase=false`）、prod+写（true）、dev+写（`needConfirm=true, requirePhrase=false`）、空数组。
- `sqlTabsStore`：首次读无 key、JSON 损坏、`version` 不符、12 个上限的第 12/13 个、模拟 quota 抛错、`(appId,env)` 四个组合互不串（1/dev 与 1/prod 与 2/dev 各独立）、空 SQL 页签也持久化、active 越界（存的是 5 但只剩 2 个页签 → 回落 0）。
- 后端 `sql/batch`（pytest）：单条 SELECT、单条写、多条全成功、**第 2 条失败→第 1 条已持久化（独立连接回查 count）+ 第 3 条 skipped**、全失败（第 1 条就错）、空数组 400、全空白/纯注释数组 400、非法 env 400、非归属 404、凭据缺失 409、建连失败 503（不可达库名/故坏凭据）、`SELECT_ROW_CAP=5` 下批量内仍截断且 `truncated=true`、审计条数=实际执行条数且 skipped 无记录、含 `$$` 的单条建函数成功（验证前端整条送去的原文没被服务端二次拆分破坏）、`INSERT ... RETURNING` 归为 rows。
- 前端 `SqlConsole`：挂载恢复 localStorage 页签、切换页签结果区跟着切、env 变化重载另一套页签、关闭非空页签需确认、prod 确认词未输够"执行"禁用、纯读段不弹框、执行调用 `dbSqlBatch` 且参数为拆分后的数组、有选区时用选区文本（通过注入 `SqlEditor` 桩的 `getSelectionText()`）。
- 前端 `SqlConsole` 结果页签（Ticket 009）：两条→两个页签且**只有激活块在 DOM**、点第二个切换内容、单条不渲染页签栏、0 结果与执行中仍走空态、有失败时默认落失败页且页签带 `--error`、重执行后结果变少不越界空白、服务端丢弃纯注释段导致的稀疏 index 仍写「第 3/3 条」、切脚本页签不串结果页签激活位、点结果页签**不触发**任何 localStorage 写、结果页签无关闭按钮。
- 旧测试处理：`DbAdminView.test.js` 里打 `.zc-db-sql-input` 的用例迁移到 `SqlConsole.test.js`，审计抽屉用例保持不动；数据网格行内编辑的用例（Ticket 010）按新入口重写——**只重写"怎么打开编辑"，保存链路的断言（`Modal.confirm` 一次 + `dbSqlExec` 单条 `UPDATE`）原样保留**。

**"完成"的标志**：`npm test`（vitest）全绿 + `pytest` 全绿 + `npm run build` 通过且**重新产出 dist**（前端功能缺失常因 dist 未重建）+ 手工冒烟：进 DB 操作台看到高亮 → 格式化生效 → 开两个页签 → 跑 `SELECT 1; SELECT 2;` 出两个结果页签（点第二个只看第 2 条）→ 中间塞一条错语句看 skipped 与"前面已生效"、自动停在失败页签 → prod 下确认词拦住 → 刷新后页签还在、结果为空。

## 7. 明确不做

- **不做服务端脚本库**（不建表、不跨设备/跨人共享）→ ADR 0008。
- **不做整段事务**（不包 `BEGIN...COMMIT`），不做"遇错继续"。
- **不做执行取消**（长查询只能等 `statement_timeout` 或写完/DDL 自己跑完）。
- **不做报错行号波浪线**（PG 的 `position` 不结构化回传，错误只在结果块里显示原文）。
- **不做结果复制/导出 CSV**。
- **不做自动格式化**（切页签/保存/失焦都不自动重排）。
- **不设段内语句数与单条长度上限**（第 12 问明确选择沿用现有护栏、不加语句数闸门）。
- **不做页签拖拽排序、页签颜色标记、多应用同时打开**。
- **不做结果页签的关闭/重命名/持久化**（Ticket 009）：结果页签只是同一批结果的水平导航，不是可关闭的工作现场，不设数量上限、靠横向滚动。
- **不做列值补全与 SQL Lint**。
- **不改「数据」页签行内编辑的保存语义与审计抽屉**（Ticket 010 只搬入口：底部常驻面板 → 行末固定列 + 弹框；`buildUpdate`、单次确认、单条 `/sql` 端点、成功后重拉全部不变），也不在这一列新增行/删除行。
- **不改网关/发布/迁移轨**：本功能与 `sqlchange/` 目录无关，不改任何发布链路。

## 8. 补充说明

- **`SELECT * FROM (<sql>) LIMIT` 包裹的既有缺陷**：现状对含重复输出列名的 SELECT 会报"column name specified more than once"（注释里已认可为可接受限制）。批量里沿用，不额外修复；如用户遇到，提示改写为带别名的 SELECT。
- **行尾 `--` 注释与包裹式样不兼容（Ticket 002 已修）**：若包裹时内层不换行，`SELECT 1 AS n -- x` 会被拼成 `... (SELECT 1 AS n -- x) AS _zc_cap LIMIT 5001`，右括号与 LIMIT 一起被注释掉 → `syntax error at end of input`。现在 `execute_one` 在内层前后各补一个换行，单条与批量两条路径同时受益（同源共用 `execute_one`）；守卫用例 `test_sql_select_trailing_line_comment_survives_row_cap` 等 4 条。
- **审计量级上升**：一次点击 N 条 = N 条审计记录（ADR 0007 后果）。抽屉现有 `limit=100` 足够，不改；若后续抱怨刷屏再另立 spec。
- **确认门槛与 op 徽章故意用两套判据**：`isReadOnlySelect` 管"要不要弹框"（取严，宁可多问一次），`classifyOp` 管"徽章显示什么 + 审计记什么"（与后端一致）。`SELECT setval(...)` 会出现"徽章 select、但要二次确认"的组合，这是有意的。
- **`riskFlags` 的 `no_where` 判据是正则** `\bwhere\b`，会漏判 `UPDATE t SET a=(SELECT ...)` 之类，属已接受的保守误报/漏报（宁可标红也不给假安全）。
- **CodeMirror 与 antd 弹框的 z-index/焦点冲突**：`Ctrl+Enter` 需在 `EditorView.domEventHandlers` 里拦，不能只挂 window keydown，否则焦点在编辑器外时行为不一致。
- **Windows 冒烟前置**：需 `zerocode-pg16` 容器与应用 dev/prod 库凭据已供给，否则批量端点返回 409（属预期，不是新 bug）。
- **下游**：本 spec 已拆为 10 张示踪弹工单，见 [.scratch/db-sql-console/tickets/](../../.scratch/db-sql-console/tickets)：001 预重构 `execute_one` → 002 示踪弹（两条 SELECT 端到端）→ 003 遇错即停与失败可见 / 004 拆分状态机 / 005 页签与本地保存（三票可并行）→ 006 格式化与选区执行 → 007 确认弹框与 prod 确认词 → 008 补全 + dist 重建 + 手工冒烟 → 009 结果页签一条一页（Blocked by 005）→ 010 数据网格行末编辑列与弹框（独立，只动 `DbAdminView.vue`）。
