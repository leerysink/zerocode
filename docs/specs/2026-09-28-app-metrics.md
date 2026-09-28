# 应用资源监控（app metrics）规格说明

**日期**：2026-09-28  **来源**：grill-with-docs 访谈  **术语**：见 [CONTEXT.md](../../CONTEXT.md)「应用资源监控（app metrics）」  **ADR**：[0005 应用资源监控用后端轮询 docker stats](../adr/0005-app-metrics-docker-stats-polling.md)

## 1. 问题陈述

平台目前只在应用卡片上呈现"运行态标签"（stopped / running / publishing 等），用户无法回答"这个应用吃掉多少机器资源、这个月有没有异常的磁盘增长"。生产环境长期看更严重：prod 栈发布后就无人值守常驻，内存泄漏 / 磁盘堆积只能在出问题后才知道。

需要给每个应用增加一个资源消耗视图：**当前实时** CPU/内存/磁盘 + **历史曲线**，用于日常巡检和异常回溯。

## 2. 方案概述

- **口径**：容器运行时（CPU/内存） + 应用专属磁盘（数据库占用 + 工作区占用），仅 prod 栈。
- **采集**：平台后端 lifespan 起两个 asyncio 定时循环（模仿现有 `_watchdog`），一个每 30s 采容器、一个每 10min 采磁盘；结果落入平台 Postgres 新表 `app_metrics`。
- **窗口**：单粒度滚动窗口保留 N 天，`ZEROCODE_METRICS_RETENTION_DAYS` 控制（默认 30），后端定时淘汰过期行。
- **展示**：AppsView ☰ 菜单加"资源监控"入口，弹层里三条曲线（CPU / 内存 / 磁盘）+ 顶部实时数字。应用级汇总（多容器合并）。
- **停止态**：实时区显示"未运行"空态，历史曲线保留停机前的点、停机区间自然留洞；磁盘仍出点。
- **权限**：owner 看自己 + 创始人看全部（复用现有 `actor.is_founder`）。

## 3. 用户故事

编号可独立验证。

- **US-01** 作为 owner，我打开一个已发布且 prod 在跑的应用监控页，能看到当前 CPU%、内存 MB、磁盘 MB 三个实时数字。
- **US-02** 作为 owner，我能看到过去 1 小时 / 24 小时 / 7 天 / 30 天四个窗口的曲线，切换窗口只换 X 轴范围、不重取历史数据之外的东西。
- **US-03** 作为 owner，我看到的历史曲线是**应用级汇总**（backend + frontend 容器 CPU 相加、内存相加），看不到分容器明细。
- **US-04** 作为 owner，当 prod 栈停止时，实时区显示"未运行"空态；历史曲线保留停机前的最后一段，停机区间不留 0 值点（图上表现为线断掉）。
- **US-05** 作为 owner，磁盘指标（数据库大小 + 工作区大小）在我停机后仍然每 10min 采一次并画在图上，能观察到源码堆积或数据留存。
- **US-06** 作为创始人，我在监控页 URL 上访问任意 `app_id`，无论归属都能查看；非创始人访问别人的应用返回 404（与现有 `get_app` 语义一致，不区分 403）。
- **US-07** 作为平台运维，修改 `ZEROCODE_METRICS_RETENTION_DAYS=7` 并重启后，7 天前的 `app_metrics` 行在下一次清理 tick 内被删除。
- **US-08** 作为 owner，Docker Desktop 未启动或 prod 栈容器不可达时，采集 tick 静默失败（记 warning）、不影响其他 tick 和平台正常运行；CPU/内存指标缺失但磁盘指标照采。
- **US-09** 作为 owner，应用被删除时，其 `app_metrics` 行级联清除，不留孤儿。
- **US-10** 作为 owner，`migrate` 一次性服务（compose 里 `restart: "no"`）不计入 CPU/内存汇总（只在运行窗口内短暂存在）。

## 4. 已定决策

- 监控口径：容器运行时 + 应用专属磁盘（数据库 + 工作区）。
- 监控范围：**仅 prod 栈**（dev 不采）。
- 采集方式：**后端定时轮询 `docker stats --no-stream`**（约 30s），磁盘独立低频（约 10min）。
- 保留策略：**单粒度滚动窗口 + 可配置保留天数**（`ZEROCODE_METRICS_RETENTION_DAYS`，默认 30）。
- 展示粒度：**应用级汇总**，多容器加和。
- 磁盘范围：**工作区全量 du**（含 node_modules 等），不排除。
- 权限：**owner + 创始人**（创始人 bypass owner 过滤）。
- 停止态：实时区空态 + 历史留洞 + 磁盘照采。

被明确否决：引入 Prometheus/cAdvisor/Grafana、流式 stats 长连接、分段降采样、按容器拆分展示、把停机区间填 0。

## 5. 数据模型与接缝

### 5.1 表（changelog/V20260928_01_app_metrics.sql）

按项目规范落 `changelog/` 目录，幂等 DDL：

```sql
CREATE TABLE IF NOT EXISTS app_metrics (
    id            BIGSERIAL PRIMARY KEY,
    app_id        BIGINT NOT NULL REFERENCES app_project(id) ON DELETE CASCADE,
    ts            TIMESTAMPTZ NOT NULL DEFAULT now(),
    kind          TEXT NOT NULL,   -- 'cpu' | 'memory' | 'db_size' | 'workspace_size'
    value         NUMERIC NOT NULL,-- cpu: % / memory & size: bytes
    CHECK (kind IN ('cpu','memory','db_size','workspace_size'))
);
CREATE INDEX IF NOT EXISTS ix_app_metrics_app_ts ON app_metrics (app_id, ts DESC);
CREATE INDEX IF NOT EXISTS ix_app_metrics_ts     ON app_metrics (ts);   -- 供过期清理扫
```

单表 + `kind` 维度，四种指标共用一条查询通路，读取时无需 JOIN 多表。级联删除覆盖 US-09。

### 5.2 采集器接缝

新模块 `app/deploy/metrics.py`，两个协程：

- `sample_containers(session_factory, interval_sec=30)`：对 `status_prod IN ('running','unhealthy')` 的应用，`docker compose -f deploy/prod-compose.yml ps --format json` 拿容器 ID → `docker stats --no-stream --format json <cid1> <cid2> ...` → CPU/内存加和 → INSERT 两行 (kind='cpu','kind='memory')。异常吞掉记 warning。
- `sample_disk(session_factory, interval_sec=600)`：对**所有** `app_project` 行（不管 prod 是否在跑）取工作区目录 `apps/{slug}/` 的 du bytes + `SELECT pg_database_size(current_database())` 与 schema 归属拆分（简单起见先用 `SELECT pg_size_pretty` 或按表前缀估算；具体口径留给 implement）。INSERT 两行 (kind='db_size','kind='workspace_size')。
- `prune_expired(session_factory, days)`：`DELETE FROM app_metrics WHERE ts < now() - INTERVAL 'N days'`。在同一 tick 尾部跑一次即可，无需独立循环。

**lifespan 接入**：[app/main.py](file:///d:/project/zerocode/app/main.py) 现有 `_watchdog` 旁边加 `_metrics_loops(app)` 起三个 `asyncio.create_task`（容器、磁盘、可选把 prune 挂磁盘循环尾部），yield 后统一 cancel。全部包 try/except 保证 Docker 不可用不卡启动（对齐 `_deploy_startup` 兜底纪律）。

### 5.3 REST 接缝

新增端点，挂在现有 `app_router`（[app/api/apps.py](file:///d:/project/zerocode/app/api/apps.py)）：

- `GET /api/apps/{app_id}/metrics/current` → 返回 `{cpu, memory, db_size, workspace_size, prod_status}`；每项含 `value` + `ts`；prod 停机时 `cpu`/`memory` 为 `null`，磁盘仍有值。
- `GET /api/apps/{app_id}/metrics/history?window=1h|24h|7d|30d` → 返回 `{kind: [{ts, value}, ...]}` 四个序列。

两个端点都通过 `request.state.actor` 判权限：

```python
actor = request.state.actor
owner = None if actor.get("is_founder") else actor["id"]
await app_service.get_app(sf, app_id, owner_user_id=owner)  # 复用 404 语义
```

（`get_app` 现有实现按 `owner_user_id` 过滤；传 `None` 即绕过。）

### 5.4 配置接缝

- `ZEROCODE_METRICS_RETENTION_DAYS`：环境变量 → 默认 30；读点放在 metrics 模块内，测试用 monkeypatch。
- 采集间隔先常量硬编码（30 / 600 秒）；如需可调再走环境变量，非本期范围。

### 5.5 前端接缝

- [AppsView.vue](file:///d:/project/zerocode/frontend/src/views/AppsView.vue) ☰ 菜单加一项 `资源监控`（放在"查看日志"之前，与"发布历史"平级），点击打开新组件 `MetricsDrawer.vue`。
- `MetricsDrawer` 打开时 `GET /metrics/current` 拿实时数字、`GET /metrics/history?window=...` 拿曲线；每 15s 拉一次 current 刷新顶部数字（不轮询 history）；窗口切换按钮 1h/24h/7d/30d，一次只一个窗。
- 图表库沿用前端已有的（若尚无则用 ECharts 或轻量 SVG，具体由 implement 决定；spec 不锁库）。

## 6. 测试决策

**顶层接缝**：REST 端点 + 采集 tick 函数。

- 后端单测（pytest）：
  - 采集器：用 fake `docker` subprocess（`monkeypatch` 或注入 runner），验证 CPU/内存加和正确、`migrate` 一次性服务被剔除、异常吞掉不抛。
  - 磁盘采集：临时目录 + 已知大小文件，验证 du 口径包含 node_modules 等；数据库大小 mock SQL。
  - 权限：`GET /metrics/*` 三种身份（owner 本人、owner 他人、创始人）—— 200/404/200。
  - 停止态：`status_prod='stopped'` 时 current 端点 `cpu/memory` 返回 null，历史不新增容器点但磁盘点继续。
  - 过期清理：插入 `ts = now() - interval '31 days'` 的行，跑一次 prune 后消失；改 `ZEROCODE_METRICS_RETENTION_DAYS=7` 后窗口变化。
- 前端单测（vitest）：MetricsDrawer 空态渲染、窗口切换调用参数正确、☰ 菜单项存在。
- **不测**：真实 docker 环境下的采集（Docker Desktop 手测一次即可，CI 里不依赖）；具体图表渲染细节。

**"完成"的标志**：pytest 全绿 + vitest 全绿 + 手工在本地 Docker Desktop 环境跑一个应用、进监控页看到点、停栈看到空态且历史留洞。

## 7. 明确不做

- **不监控 dev 栈**（dev 预览资源消耗不入库、不展示）。
- **不分容器展示**，也不提供下钻视图（后续如需，另立 spec）。
- **不做告警 / 阈值通知**（本期只"看数据"，不"响应数据"）。
- **不做跨应用聚合视图**（平台管理员总览页、全局资源占用图）。
- **不做 CPU 峰值精度改进**（放弃流式长连接方案，接受采样平均）。
- **不动共享 PG 实例的资源归属**（不试图拆分 zerocode-pg16 的 CPU/内存到单应用，只拆磁盘）。
- **不做工作区磁盘白名单排除**（node_modules / dist 等一并计入，低频消化扫描成本）。
- **不做历史降采样分层存储**（单粒度滚动窗口，超过保留期即淘汰）。

## 8. 补充说明

- **changelog 与迁移纪律**：新增表要按项目规范落 `changelog/V{YYYYMMDD}_{NN}_{slug}.sql`（现有最新 V20260926_01），由 `app/db.py` 的 `run_migrations` 在启动时自动 apply。DDL 必须幂等（`IF NOT EXISTS`），可安全重放。
- **compose 结构事实**：prod 栈一般含 `migrate`（一次性）、`backend`（常驻）、`frontend`（常驻），见 [apps/{slug}/deploy/prod-compose.yml](file:///d:/project/zerocode/apps/test1/deploy/prod-compose.yml)。采集时通过 `docker compose ps --format json` 拿到当前实际跑的容器 ID，不硬编码服务名。
- **数据库大小估算策略**（留给 implement 决策）：应用与平台共用 `zerocode-pg16`，每个应用一个独立 database（`PROD_DB` 见 `.env`），用 `SELECT pg_database_size(current_database())` 在应用容器内不可行——由平台后端用 admin_url 连过去，按应用的 `PROD_DB` 名逐个查。若实现更复杂可先只做工作区磁盘，数据库列留 `null`，下一版补。
- **workspace 目录位置**：从 `app_project.workspace_id` 关联 `workspace.root_path` 得到；实际 du 目标 = `{root_path}/{slug}/`。du 用 `docker run --rm -v ... alpine du -sb` 或原生 Python `os.walk`（Windows 上后者更简单）。
- **CPU 单位约定**：`docker stats` 输出 `xx.xx%`（相对一个核心，可 >100%），存 `NUMERIC` 原样保留百分比、不做归一化；前端按百分比展示。
- **内存 / 磁盘单位**：bytes 存 `NUMERIC`，前端格式化为 MB / GB。
- **下游**：本 spec 已就绪进入 [to-tickets](../) 拆垂直切片；预计可切成"表迁移 → 采集器 → REST → 前端 → 清理与权限"约 5 张票。
