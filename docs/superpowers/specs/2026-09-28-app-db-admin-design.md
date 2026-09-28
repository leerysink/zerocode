# zerocode 设计文档 — 应用 DB 操作台（Navicat 式 dev/prod 库浏览与查询）

日期：2026-09-28　范围：应用级 `<slug>_dev` / `<slug>_prod` 两库的交互式浏览、结构查看、数据编辑、任意 SQL 执行，以及平台侧操作审计　前置：规格 06 分库供给（`app/deploy/provision.py`）、资源监控模块隔离纪律（`app/api/metrics.py`）、dev/prod schema parity 设计（`docs/superpowers/specs/2026-09-22-dev-prod-schema-parity-design.md`）

## 1. 目标与非目标

### 目标
在应用卡片 ☰ 菜单提供一个「DB 操作」入口，点击后以独立路由页面（新标签打开）提供类 Navicat 的能力：

1. **表列表树 + dev/prod 切换**：左侧列出目标库的表/视图，顶部一键切换 dev / prod。
2. **表结构查看**：列名/类型/nullable/默认值/主键/索引/约束。
3. **数据浏览 + 行内编辑**：`a-table` 分页排序展示数据；单元格编辑后回写 UPDATE（写操作二次确认）。
4. **SQL 查询控制台**：手写任意 SQL 执行，结果以表格展示；非 SELECT 同样二次确认。
5. **操作审计日志**：平台侧记录每一次经本操作台发起的 DB 操作（读 + 写全记）。

### 非目标（YAGNI）
- 不做 ER 图、数据导入导出（CSV/SQL dump）、多结果集分页流式游标、跨库 join。
- 不引入重型第三方 Web SQL 客户端、不引入 Monaco；SQL 编辑器用 `a-textarea`（零新依赖）。
- 不改应用自身的 `sqlchange`/`schema` 迁移轨——本功能是「直接连库执行」的旁路工具，与发布迁移闸门并行存在（见 §7 关系说明）。
- 行内编辑不支持无主键表（无法安全生成 WHERE），此类表仅浏览 + 引导用控制台。

## 2. 现状与既有基础设施

- **分库供给**：每应用两库 `<slug>_dev` / `<slug>_prod`，同实例 `zerocode-pg16`（宿主侧 `localhost:5433`）。凭据存平台 `app_config` 表键 `dbcred.{slug}.{env}` = `{db,user,password}`（`app/deploy/provision.py::read_cred`），另有专属角色 `{slug}_{env}` 拥有并仅可连自己的库。
- **平台迁移轨**：`changelog/V<日期>_<序号>_<名>.sql`，由 `app/db.py::run_migrations` 按文件名升序自动应用、`schema_migration` 去重；全部 `IF [NOT] EXISTS` 幂等守卫。
- **模块隔离纪律**：`app/api/metrics.py` 与 `apps_api` 同前缀 `/api/apps` 但独立成路由模块，互不干涉。
- **鉴权**：`request.state.actor`（含 `is_founder`）；归属过滤复用 `app_service.get_app(sf, app_id, owner_user_id=owner)`——非创始人传自己 id、创始人传 None 看全部，命中不到即 404。
- **前端**：Vue3 + ant-design-vue；`AppsView.vue` ☰ 菜单项 class 为 `.zc-menu-*`；路由在 `frontend/src/router/index.js` 静态注册；REST 封装在 `frontend/src/api/index.js`。

## 3. 架构概览

```
AppsView ☰「DB 操作」(a href target=_blank)
        → /apps/:id/db  (slug 由后端 get_app 派生，无需 query 参数)
        → DbAdminView.vue (三栏: 表树 | tabs[数据/结构/查询] | 审计抽屉)
             │ axios /api
             ▼
app/api/db_admin.py (新模块, prefix /api/apps)
   _load_app 归属校验 → 读 dbcred.{slug}.{env} → create_async_engine(目标库)
   GET  /{id}/db/{env}/tables            表/视图列表
   GET  /{id}/db/{env}/tables/{t}/schema 结构
   GET  /{id}/db/{env}/tables/{t}/data   分页数据
   POST /{id}/db/{env}/sql               执行任意 SQL（读写均经此，UI 层已二次确认）
   GET  /{id}/db/audit                   操作历史
             │ 每次执行前后写
             ▼
   平台表 db_operation_log（changelog/V20260928_02_db_operation_log.sql）
```

连接策略：平台进程按请求直连目标库（方案 A）。sqlrunner 一次性容器为批量迁移设计、冷启动数秒，不适合交互式，明确弃用。用完 `dispose()`，不缓存跨请求引擎（凭据可能随 dev reset 变化）。

## 4. 后端设计（`app/api/db_admin.py`）

薄层，逻辑与 apps/metrics 同风格；`DeployError.status` 映射 HTTP。所有端点先过 `_load_app`（非归属/未知 → 404），再做 `env ∈ {dev,prod}` 白名单校验（否则 400）。

### 4.1 连接构建
新增私有 `_target_engine(sf, slug, env)`：`read_cred(sf, slug, env)` 取 `{db,user,password}`，用 `sqlalchemy.engine.make_url(app_config.database_url())` 作底座、`.set(database=db, user=user, password=password)` 派生 URL，`create_async_engine`。凭据缺失 → 409（提示库未供给/已被删除）。

### 4.2 端点

| 端点 | 语义 | 实现要点 |
|---|---|---|
| `GET /{id}/db/{env}/tables` | 表 + 视图列表 | 读 `information_schema.tables` WHERE `table_schema='public'`，参数化 |
| `GET /{id}/db/{env}/tables/{t}/schema` | 列/约束/索引 | 读 `information_schema.columns` + `table_constraints`/`key_column_usage` + `pg_indexes`；`{t}` 作绑定参数 |
| `GET /{id}/db/{env}/tables/{t}/data` | 分页数据 | `?page&size&sort&dir`；`ORDER BY` 列走标识符白名单转义（见 §6），`LIMIT/OFFSET` 绑定；返回 `columns`+`rows`+`total` |
| `POST /{id}/db/{env}/sql` | 执行任意 SQL | body `{sql}`；有结果集回 `columns+rows`（截断上限），无结果回 `affected`；行内编辑由前端生成带主键 WHERE 的 UPDATE/INSERT/DELETE 走此端点 |
| `GET /{id}/db/audit` | 操作历史 | 读 `db_operation_log` WHERE `app_id`，`?env&limit`，倒序 |

`/{t}/data`、`/{t}/schema` 的表名既进路径也进绑定；SQL 一律不做字符串拼接注入值。

### 4.3 语句分类与执行护栏
`op_type` 由 SQL 首个关键字归类：`select/insert/update/delete/ddl/other`。SELECT 与结构/数据读取端点设 `statement_timeout`（默认 15s）与行数上限（如 5000，超出截断并标记 `truncated`），防打爆响应。写/DDL 端不设超时（由用户确认承担）。

## 5. 审计日志（`db_operation_log`）

新迁移文件 `changelog/V20260928_02_db_operation_log.sql`（平台库，幂等 `IF NOT EXISTS`）：

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | BIGSERIAL PK | |
| `app_id` | BIGINT FK→`app_project(id)` ON DELETE CASCADE | |
| `actor_user_id` | BIGINT | 操作人（`actor["id"]`，可空兼容无登录态） |
| `env` | VARCHAR(10) | `dev`/`prod` |
| `op_type` | VARCHAR(20) | `select/insert/update/delete/ddl/other/struct_view` |
| `sql_text` | TEXT | 实际执行 SQL（读端点用固定标识记 `struct_view`） |
| `target_table` | VARCHAR | 主操作对象，可空 |
| `status` | VARCHAR(10) | `success`/`error` |
| `affected` | INTEGER | 影响/返回行数，可空 |
| `error` | TEXT | 失败摘要，可空 |
| `duration_ms` | INTEGER | 耗时 |
| `ts` | TIMESTAMPTZ DEFAULT now() | |

索引：`(app_id, ts DESC)`。**全量记录**（读 + 写均入审计，最简逻辑，可事后按 `op_type` 筛）。

写入点：`POST /sql` 执行前记录开始、执行后写 success；异常 `except` 内先写 error 行再上抛 HTTPException，保证失败留痕。`/{t}/schema`、`/{t}/data`、`/{env}/tables` 三个读端点各补一条 `op_type=struct_view` 审计（不含全文 SQL，`sql_text` 存端点描述）。

## 6. 安全与防护

- **SQL 注入面**：交互执行任意 SQL 是本功能的设计目的，注入防护针对的是**标识符**——表名/排序列/方向不来自自由文本，`{t}` 来自路径但仅用于绑定；`ORDER BY` 列名与 `dir` 用白名单（取自该表实际列 + `asc|desc`），双引号转义。值一律参数绑定。
- **越权**：所有端点经 `_load_app` 归属过滤，别人的应用一律 404（不暴露存在性）。
- **写确认**：dev/prod 同策——UI 层对所有写/DDL 操作 `Modal.confirm` 展示即将执行的完整 SQL，确认后才发请求。后端信任「已过确认闸门」的前端，不再重复拦截（与用户选择的「所有写操作二次确认」一致）。
- **凭据保密**：`dbcred.*` 已被通用配置读取排除（规格 06），本模块仅内部使用凭据建连，绝不回传密码/连接串给前端。

## 7. 与既有 dev/prod schema parity 机制的关系（重要）

`2026-09-22-dev-prod-schema-parity-design.md` 明确将「人/AI 直接对 dev 跑临时 DDL」列为要消除的**漂移（drift）**根因。本操作台恰恰提供这条直连旁路，需诚实说明边界：

- 本功能**不接入** `sqlchange_history`/`schema.sql` 双轨：经此执行的 DDL/DML 不会被记为应用的迁移文件，也不参与发布 diff。
- 后果：用本台对 dev 改结构会引入漂移；prod 直改更会绕过发布闸门。属**运维/排障用的显式逃逸口**，不是常规开发路径。
- 缓解：① 审计全量留痕可追责；② dev 结构漂移可用既有「重建 dev」（`devReset`，从 sqlchange 全量 fold）消除；③ prod 直改风险自担，页面 prod 切换时给出黄条提示「直改 prod 会绕过发布迁移轨，请确认你知道自己在做什么」。

> 若后续希望把操作台里的 DDL 一键回填成 `sqlchange` 文件以纳入真相轨，属独立增强，不在本期。

## 8. 前端设计

### 8.1 入口（`AppsView.vue`）
☰ 菜单「查看日志」下方新增：`<a class="zc-app-menu-item zc-menu-db" :href="dbRouteHref(a.id)" target="_blank"><database-outlined /> DB 操作</a>`；`dbRouteHref = (id) => \`/apps/${id}/db\``。（图标用 `@ant-design/icons-vue` 的 `DatabaseOutlined`。）

### 8.2 路由（`router/index.js`）
`{ path: '/apps/:id/db', name: 'app-db', component: () => import('../views/DbAdminView.vue') }`，受全局登录守卫保护。

### 8.3 API 封装（`api/index.js`）
`dbTables(id,env)`、`dbTableSchema(id,env,table)`、`dbTableData(id,env,table,params)`、`dbSqlExec(id,env,sql)`、`dbAudit(id,params)`。

### 8.4 `DbAdminView.vue` 布局
- 顶栏：应用名 + `a-segmented`（dev/prod）+ prod 警示条 + 刷新。
- 左栏 `a-tree`：`dbTables` 结果，选中表驱动右栏。
- 右栏 `a-tabs`：
  1. **数据**：`a-table`（`pagination` 远程分页、`column.sorter` 触发重取、可编辑单元格）。保存前 `Modal.confirm` 展示生成的 UPDATE/INSERT/DELETE SQL；无主键表禁用编辑。
  2. **结构**：只读 `a-table` 展示列/约束/索引。
  3. **查询**：`a-textarea` 控制台 + 执行按钮，结果 `a-table`；非 SELECT 走二次确认。
- 抽屉：操作历史（`dbAudit`）。

## 9. 错误处理

- 归属/未知应用 → 404；`env` 非法 → 400；凭据缺失 → 409。
- SQL 执行报错 → 捕获，写 error 审计行，回 500（`detail` = PG 原始报错原文，前端 `message.error` 展示）。
- 连接失败（pg 未起）→ 503，提示检查 zerocode-pg16。

## 10. 测试策略

- **后端 pytest**（`tests/`，沿用 docker/pg `skipif` 守卫，连真实 pg）：表列表/结构/分页数据端点形状；`POST /sql` 的 SELECT/INSERT/UPDATE/DELETE/DDL 结果与影响行数；审计 success+error 落库；归属过滤（他人 404）；`env` 白名单 400；标识符转义防注入（恶意表名/排序列）；无主键表数据端仍可读。
- **前端 vitest**：☰ 菜单出现 `.zc-menu-db` 且 href 正确；路由表含 `/apps/:id/db`；DbAdminView dev/prod 切换重新拉表；写操作触发 `Modal.confirm` 且确认后才发请求；无主键表编辑被禁用。

## 11. 改动文件清单

- 新增 `app/api/db_admin.py`
- 改 `app/main.py`（include `db_admin.router`）
- 新增 `changelog/V20260928_02_db_operation_log.sql`
- 改 `frontend/src/views/AppsView.vue`（菜单项）
- 改 `frontend/src/router/index.js`（路由）
- 改 `frontend/src/api/index.js`（5 个封装）
- 新增 `frontend/src/views/DbAdminView.vue`
- 新增对应后端 pytest + 前端 vitest
