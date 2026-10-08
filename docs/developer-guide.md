# ZeroCode 开发者指南

本文面向平台自身的开发/维护人员，收录项目结构、接口、环境变量、测试与分支约定等施工细节。
面向客户的产品介绍请看仓库根 [README.md](../README.md)。

## 项目结构

```
app/
  main.py              FastAPI 入口：挂路由 + 静态托管 frontend/dist + SPA 回退 + 启动迁移与 watchdog
  config.py            环境变量与默认连接串（唯一取值处）
  db.py                SQLAlchemy / asyncpg 引擎与会话
  agent/               LLM 循环 loop.py、工具 tools.py、提示词 prompts.py、@ 引用 mentions.py、
                       模型解析 model_resolver.py、上下文压缩（见 services/compaction_service.py）
  api/                 路由层：sessions / ws / apps / dev_logs / deploy_ws / files / workspaces / models / config_api
  deploy/              部署套件：compose.py 渲染、engine.py 执行、provision.py 建库、ports.py 端口分配、
                       sqlrun.py 迁移、contract.py 契约、guard.py 校验、hub.py 事件广播与日志环形缓冲、
                       scaffold.py 脚手架、settings.py 配置、slugs.py 标识校验
  services/            业务服务：app_service（建应用含端口分配与失败回滚）/ message / config / compaction
  templates/
    vue3-fastapi/      小型应用技术栈模板（自带 sqlchange 规范 README）
    vue3-springboot/   大型应用技术栈模板（Vue3 + Spring Boot 3，含 RBAC 权限体系）
  store/               应用商店：包上架、申请审批、独立实例供给与升级
frontend/              Vue 3 单页应用（views：ChatView / AppsView / StoreView / SettingsView）
  src/components/      聊天与部署组件：ChatMessages、ToolCallBlock、ThinkingBlock、DiffBlock、
                       AskUserCard、TodoBar/TodoCard、FilePanel、WorkspaceExplorer、
                       DeployLogPanel（日志抽屉）、SideBar、NewSessionModal、MarkdownView …
  src/stores/          Pinia：chat.js、config.js
  src/utils/           纯逻辑层（可单测）：deployLog.js、diff.js、fsNav.js、mentions.js、tree.js、appForm.js …
gateway/               中央 OpenResty 网关：通配域名 HTTPS 终止 + routes.json 动态路由
  lua/                 按 mtime 刷新路由缓存的 lua 脚本
  certs/               自签通配证书（gitignore 排除，不入库）
apps/                  AI 生成的应用工作区（每台机器各自生成，已 gitignore，不入库）
changelog/             平台自身 DDL/DML，V<YYYYMMDD>_<序号>_<说明>.sql，启动时自动迁移
docs/superpowers/
  plans/               施工计划
  specs/               设计规格（部署套件、应用商店、通配网关、SpringBoot 栈等）
tests/                 后端 pytest，与 `app/` 分层对应
```

## 完整启动步骤

1. **启动 PostgreSQL 容器**（首次需 `docker run`，之后 `docker start zerocode-pg16`）：

   ```powershell
   docker run -d --name zerocode-pg16 `
     -v zerocode-pgdata-v2:/var/lib/postgresql/data `
     -e POSTGRES_USER=zerocode -e POSTGRES_PASSWORD=zerocode123 -e POSTGRES_DB=zerocode `
     -p 5433:5432 pgvector/pgvector:pg16
   ```

   > `-v` 不可省：不留命名卷时数据进的是匿名卷，`docker rm` 后旧库直接消失。
   > 本仓库当前真实使用的卷是 `zerocode-pgdata-v2`（pgvector 迁移后的新卷），
   > `zerocode-pgdata` 是迁移前的 alpine 旧卷，仅作回滚点保留。
   >
   > 镜像必须是 `pgvector/pgvector:pg16`（不是 `postgres:16-alpine`）：工作区记忆的语义
   > 检索靠 `CREATE EXTENSION vector`，原生 alpine 镜像不带该扩展。已有的 alpine 容器不能
   > 直接复用数据目录（musl/glibc 不兼容），需 `pg_dumpall` 导出后导入新卷。
   >
   > 无需手动 `--network zcnet`：已发布应用靠 DNS 别名 `zerocode-pg16:5432` 连库，
   > 首次发布时 `app/deploy/provision.py::ensure_zcnet()` 会建网并把本容器接进去（幂等）。

2. **安装后端依赖**：

   ```powershell
   python -m venv .venv
   .venv\Scripts\pip install -r requirements.txt
   ```

3. **安装前端依赖**（新克隆没有 node_modules，构建前必须先装）：

   ```powershell
   npm install --prefix frontend
   ```

4. **构建前端并启动**（均在仓库根）：

   ```powershell
   .\build.bat   # 进入 frontend 执行 npm run build，产物在 frontend/dist
   .\start.bat   # 优先用 .venv\Scripts\python.exe 启动 uvicorn app.main:app --host 127.0.0.1 --port 8100
   ```

5. **开发模式（推荐日常改代码用）**：运行 `.\dev.bat` —— 尝试启动 PG 容器 → 补装依赖 →
   新窗口起后端 `:8100`、当前窗口起 Vite dev `:8082`（代理 `/api`、`/ws`）。改前后端代码即时生效。
   故意不加 `--reload`：部署套件会往 `apps/` 写生成文件，热重载会被自己触发刷屏。

6. **配置 LLM**：打开 <http://127.0.0.1:8100> → 配置页，填入 `llm.base_url`、`llm.api_key`、`llm.model`（OpenAI 兼容接口，如 DeepSeek）。

## 接口一览

| 前缀 / 端点 | 用途 | 说明 |
| --- | --- | --- |
| `/api/config` | 平台配置 | `GET`/`PUT` 读写配置，`GET /pg-status` 探库，`POST /test-llm` 试调模型 |
| `/api/models` | 多模型凭据 | `GET`/`POST` 列表与新增，`PUT`/`DELETE /{model_id}` 改删 |
| `/api/workspaces`、`/api/fs/*` | 工作区与本机文件 | 建/列/删工作区；`/fs/browse`、`/fs/tree`、`/fs/ls`、`/fs/download`、`/fs/open-folder` |
| `/api/sessions` | 会话与消息 | 会话 CRUD、`PATCH /{id}/model` 换模型、`GET /{id}/messages`、`GET /{id}/file-changes` |
| `/api/files/read`、`/api/files/save` | 文件读写 | 供编辑器/预览使用 |
| `/api/apps` | 应用与 dev 预览 | 应用 CRUD、`POST /validate-slug`、`POST /{id}/dev/start|stop|restart`（202 异步）、`GET /{id}/dev/status`、`GET /{id}/dev/logs`、`POST /{id}/sqlchange/apply`、`GET /{id}/sqlchange/records|files` |
| `WS /ws/chat/{session_id}` | 聊天主通道 | 流式回复、工具调用事件、写文件确认、执行中插话 |
| `WS /ws/deploy/{app_id}` | 部署事件流 | 状态变更与 `dev_log` 实时行；只搬运，不解释业务语义 |

## 技术栈明细

- **后端**：Python 3.11+、FastAPI ≥ 0.115、SQLAlchemy ≥ 2.0.30、asyncpg、Pydantic 2、OpenAI SDK（OpenAI 兼容接口）、uvicorn。见 `requirements.txt`。
- **前端**：Vue 3.5 + Ant Design Vue 4.2 + Pinia + Vue Router + Less，构建 Vite 8，单测 Vitest 5（happy-dom）。
- **数据库**：PostgreSQL 16（容器 `zerocode-pg16`，宿主端口 `5433`）。平台表结构走 `changelog/`，应用库由 `app/deploy/provision.py` 按应用供给独立 dev 库。
- **容器**：Docker Compose 承载生成应用的 dev/prod 运行，端口对由 `app/deploy/ports.py` 分配并落库为 UNIQUE 约束兜底。
- **网关**：独立 OpenResty 容器（`gateway/`）终止 TLS，按 `routes.json` 动态路由到各应用 prod 端口，仅 `status_prod=='running'` 的应用进路由。
- **生成应用栈**：小型 `vue3-fastapi`；大型 `vue3-springboot`（Spring Boot 3 + Vue 3，含 RBAC 权限体系、JWT 认证、Redis 登录态缓存，健康检查 `/api/health`）。

## 环境变量

| 变量 | 默认值 | 作用 |
| --- | --- | --- |
| `ZEROCODE_DATABASE_URL` | `postgresql+asyncpg://zerocode:zerocode123@localhost:5433/zerocode` | 平台库连接串 |
| `ZEROCODE_TEST_DATABASE_URL` | `postgresql+asyncpg://zerocode:zerocode123@localhost:5433/zerocode_test` | pytest 测试库；多分支并行施工时建议各用独立库 |
| `ZEROCODE_HOST` | `127.0.0.1` | uvicorn 监听地址 |
| `ZEROCODE_PORT` | `8100` | uvicorn 监听端口 |

取值集中在 `app/config.py`，其余代码不重复读环境变量。

## 测试

```powershell
# 后端（需 zerocode-pg16 容器在跑，测试库 zerocode_test）
.venv\Scripts\python.exe -m pytest tests -v

# 前端
cd frontend; npx vitest run
```

`pytest.ini` 已设 `asyncio_mode = auto`、`testpaths = tests`。

## 分支与远程约定

```
origin  https://gitee.com/leerys/zerocode.git

本地 main                     ──跟踪──▶  origin/master        （远端主干名为 master）
```

- 本地主干分支叫 `main`、远端主干叫 `master`，推送用 refspec：`git push --set-upstream origin main:master` 建好跟踪后，在 `main` 上直接 `git push`。
- M2/M3 等在途里程碑使用 linked worktree（如 `D:\project\zc-m2`），与主工作区共享 `.git`，改动只落对应工作树。

## 约定

- 端口：后端 `8100`，前端 Vite dev `8082`，PG 宿主 `5433`；数据库可用环境变量 `ZEROCODE_DATABASE_URL` 覆盖。
- DDL/DML 一律走 `changelog/V<YYYYMMDD>_<序号>_<说明>.sql`，应用启动时自动迁移。
- 测试：`.venv\Scripts\python.exe -m pytest tests -v`（测试库 `zerocode_test`）；前端 `npx vitest run`（在 `frontend/` 下）。
- `apps/` 与 `frontend/dist/` 是生成物，已在 `.gitignore`，不入库。
- 提交只显式列文件（`git add <路径>`），不用 `git add -A`；提交前确认目标文件 `git diff HEAD` 为空，避免把无关的在途改动混进同一个提交。

## 领域术语

平台网关、应用域名（`{slug}.zerocode.com`）、routes.json、自签通配证书、miss、控制台等术语定义见仓库根 [CONTEXT.md](../CONTEXT.md)。
