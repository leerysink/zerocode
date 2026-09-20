# zerocode 部署发布套件（v2）— 03 Dev 预览环境

- 日期：2026-09-18
- 关联：00 总览（D2/D5）、02 技术栈模板、05 端口管理、07 sqlchange

## 1. 需求背景

用户在 AI 编辑代码时最关心"现在长什么样、能不能点"。dev 套是应用的**开发预览运行环境**：源码 bind-mount 进容器 + 各栈热重载，AI 每次写文件，浏览器里的页面自动更新；dev 库独立于 prod，随便改不伤正式数据。

## 2. 用户故事

- S-03-1 AI 说"供应商页面做好了"，王厂长点「预览」，右侧出现可操作的页面，边聊边看。
- S-03-2 AI 改了表格列，预览里的页面 1-2 秒自己变了（HMR），不用任何手动操作。
- S-03-3 AI 新写了建表 SQL，dev 环境重启迁移后新表生效；王厂长往 dev 里乱录一堆数据也不心疼，反正和正式库无关。

## 3. 功能需求

| 编号 | 需求 | 优先级 |
|---|---|---|
| FR3-01 | 「预览」按钮一键启动 dev 栈：渲染 `deploy/dev-compose.yml`（若缺失/过期则重新渲染）→ 起 sqlrunner 跑 dev 库迁移（07）→ 起前端/后端 dev 服务 → 状态机 `stopped→starting→running` | P0 |
| FR3-02 | 预览 UI：聊天页右栏新增「预览」页签，iframe 加载 `http://localhost:<dev_port>`；工具栏：刷新 / 新窗口打开 / 停止；dev 栈未启动时显示引导性空态 | P0 |
| FR3-03 | 热重载：源码 bind-mount，前端 Vite HMR、后端各栈自动重载；依赖变更（package.json/requirements.txt/pom.xml 变化）自动重建重启 dev 栈 | P0 |
| FR3-04 | dev 栈生命周期：手动停止；zerocode 优雅退出时自动停掉全部 dev 栈（可配 `deploy.auto_stop_dev_on_exit=true`）；prod 栈不受影响 | P0 |
| FR3-05 | 预览健康状态：iframe 加载失败/容器异常时，页签内展示中文原因 + 后端日志入口 + 「重启预览」按钮 | P0 |
| FR3-06 | dev 日志查看：前端/后端/迁移三个服务的日志流（WS），保留最近 500 行内存缓冲 | P1 |
| FR3-07 | 会话内 AI 完成代码修改后，回复尾部的固定引导（契约第 7 条），并可附「打开预览」动作按钮 | P1 |

## 4. 详细设计

### 4.1 dev-compose.yml（渲染规则，按栈）

以 `vue3-fastapi`、slug=lailiao、dev_port=8211 为例（其余栈结构一致，服务定义按 02 §4.2 变化）：

```yaml
name: zc-lailiao-dev
networks:
  default: {}                          # 项目内网：服务名互访（frontend→backend）
  zcnet: { external: true }            # 平台外部网络，zerocode-pg16 已接入（06 §4.4）
services:
  migrate:
    image: zerocode-sqlrunner:1
    environment:                       # 显式映射为 sqlrunner 输入契约（07 §4.3）
      TARGET_DB: ${DEV_DB}
      TARGET_USER: ${DEV_USER}
      TARGET_PASSWORD: ${DEV_PASSWORD}
    volumes: [ ../sqlchange:/sql:ro ]
    networks: [zcnet]
    restart: "no"
  backend:
    image: node:20-alpine / python:3.12-slim / maven:3.9-…   # 按栈
    working_dir: /src
    volumes:
      - ../backend:/src
      - backend_deps:/src/…             # 依赖目录匿名卷（node/spring 栈落地；fastapi 栈无此行），避免宿主 node_modules/venv 冲突
    environment:                        # ${DEV_*} 由 compose 自动读同目录 deploy/.env 插值（06）
      DATABASE_URL: postgresql+asyncpg://${DEV_USER}:${DEV_PASSWORD}@zerocode-pg16:5432/${DEV_DB}
      APP_PORT: "8000"
    command: <热重载命令，见 4.2>
    networks: [default, zcnet]
    depends_on: { migrate: { condition: service_completed_successfully } }
  frontend:
    image: node:20-alpine
    working_dir: /src
    volumes: [ ../frontend:/src, fe_node_modules:/src/node_modules ]
    command: sh -c "npm install && npm run dev -- --host 0.0.0.0 --port 5173"
    ports: [ "8211:5173" ]
    depends_on: [ backend ]
volumes: { fe_node_modules: {} }        # backend_deps 随 node/spring 栈按栈追加
```

- **仅前端端口对外**（D5）；容器网络内部互访用服务名（frontend 代理 `/api → http://backend:<容器端口>`，模板已配好）。
- `static` 栈无 backend/migrate 服务，仅 frontend（渲染产物不含 networks 声明，落项目默认网络即可——无 DB 依赖不接 zcnet，Task 10 审查裁定 N-2）。
- 挂载相对路径以 **compose 文件所在目录（`deploy/`）为项目目录**解析：`../backend` 即应用根下 `backend/`（原示例 `../../` 会跳出应用根，系笔误）。
- `.env` 由引擎生成：`DEV_PORT/PROD_PORT/DEV_DB/DEV_USER/DEV_PASSWORD/PROD_DB/…`（06），app 目录内随目录可迁移。

> 修订记录（Task 14B 勘误波，依 Task 10 审查裁定 N-3「实现正确、应改规格」，以 `app/deploy/compose.py` 渲染产物为准）：① 三处挂载深度 `../../`→`../`；② 删除 `migrate.env_file: .env`（compose 自动读取同目录 `.env` 完成 `${DEV_*}` 插值，省略属功能等价），migrate 的 `environment` 三行映射、顶层 `name: zc-<slug>-dev` 与 `zcnet: {external: true}` 落位、backend `DATABASE_URL/APP_PORT` 映射写法、`networks` 服务级指派与 `depends_on migrate` 门控、frontend 命令与顶层 `volumes` 声明，均已与渲染结果逐字段对齐。

### 4.2 各栈热重载命令（dev 容器内）

| 栈 | backend | frontend |
|---|---|---|
| vue3-fastapi | `pip install -r requirements.txt && uvicorn app.main:app --reload --host 0.0.0.0 --port 8000` | `npm install && npm run dev`（Vite HMR） |
| vue3-node | `npm install && node --watch src/server.js` | 同上 |
| vue3-springboot | `mvn -Dspring-boot.run.jvmArguments="-Dserver.port=8000" spring-boot:run`（首次/依赖变更才走完整构建） | 同上 |
| static | — | 同上 |

- 依赖变更检测：引擎对 `package.json`/`requirements.txt`/`pom.xml` 做 hash，变化即 `docker compose up -d --force-recreate backend`（自动重建，无需用户理解）。
- Windows bind-mount 性能提示：单应用目录级别挂载，事件延迟通常 <2s，满足预览体验；不做轮询补偿。

### 4.3 状态机（app_project.status_dev）

```
stopped --启动--> starting --迁移+服务 healthy--> running
starting --任一失败--> error（记录原因；UI 提供日志与重试）
running --手动停止/平台退出--> stopped
running --健康检查连续 3 次失败--> unhealthy --恢复--> running / --人工--> stopped
```

健康判定：前端 `GET http://localhost:<dev_port>/` 200 即可（vite 始终起得来）；backend 容器 `docker inspect` 健康态 + 平台侧对 `/api/health` 的经端口探活仅作展示提示（后端端口不对外，平台通过 `docker compose exec` 探测或直接信任容器运行状态——取实现最简路径：`docker inspect --format {{.State.Health/Running}}` + compose ps）。

### 4.4 预览页签（08 的组成部分，此处定行为）

- iframe src=`http://localhost:<dev_port>`；srcdoc 占位空态：dev 未启动时显示"点「预览」查看最新效果"+ 三个快捷按钮（启动预览/查看日志/发布）。
- iframe 隔离注意：生成的应用 dev 服务器不设置 X-Frame-Options/CSP frame-ancestors（模板保证）；若 AI 生成的代码加了拦截，FR3-05 的失败引导里给出"检测到页面拒绝内嵌，请用新窗口打开"。
- 刷新 = iframe reload；新窗口 = window.open 同 URL。

### 4.5 接口

| 方法与路径 | 说明 |
|---|---|
| `POST /apps/{id}/dev/start` | 启动 dev 栈（幂等：running 直接 200） |
| `POST /apps/{id}/dev/stop` | 停止 dev 栈 |
| `POST /apps/{id}/dev/restart` | 重启（依赖变更/迁移后） |
| `GET /apps/{id}/dev/status` | 状态 + 各容器运行态 + 最近迁移执行结果摘要 |
| `GET /apps/{id}/dev/logs?service=` | 近期日志（回看），实时流走 WS `/ws/deploy/{id}` `dev_log` 事件 |

WS `/ws/deploy/{app_id}` 下行事件（本域）：`dev_status`（状态机变化）、`dev_log`（{service, line}）、`migrate_result`（07）。

## 5. 错误处理

| 场景 | 行为 |
|---|---|
| Docker 不可用 | 启动前置检查失败：中文引导（D9）+ 一键启动 Docker Desktop 按钮，不进入 starting |
| 迁移失败 | sqlrunner 非零退出 → dev 栈不启动 → 预览空态显示失败文件名/SQL 错误 + 日志入口（07） |
| 端口被宿主其他程序占用 | 启动前探测，占用则自动改分下一个可用端口并更新 app.json/app_project（05 分配器） |
| 容器反复崩溃（代码错误） | 状态 error，日志区展示最后 100 行 + 常见原因提示（语法错误/依赖缺失/连接失败） |
| 平台崩溃退出（非优雅） | 残留 dev 容器：下次启动时引擎对孤儿 compose 项目（`zc-<slug>-dev`）执行 down，再按用户操作重启 |

## 6. 验收标准

1. 新建应用 → 点预览 → ≤60s 内 running，iframe 出现模板首页，`/api/health` 探测通过。
2. AI 用 edit_file 改前端组件文案，预览中 ≤3s 自动更新，无需手动刷新。
3. AI 新增 `sqlchange/V…_02_xxx.sql` 后重启预览：新表在 dev 库存在，`sqlchange_history` 有记录。
4. 修改 requirements.txt → dev 栈自动重建重启，服务恢复 running。
5. zerocode 退出再进入：dev 栈已停止，prod 栈（若有）仍 running；孤儿容器被清理。
6. 人为让迁移失败：预览给出失败文件名与中文引导，且 frontend 服务未被启动。
