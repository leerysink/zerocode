# zerocode

类 DSH 的 AI 编程助手：FastAPI + PostgreSQL 后端、Vue 3 前端，单体部署（方案 A）。
Agent 循环具备工具调用、写文件确认、会话持久化与崩溃恢复（watchdog）。
面向零开发经验的用户：像聊天一样把想要的软件讲给 AI，它边写边让你在本机预览。

仓库：<https://gitee.com/leerys/zerocode.git>（远端主干 `master`）

## 能力概览

| 能力 | 状态 | 落在哪 |
| --- | --- | --- |
| 编程助手内核：会话/消息持久化、工具调用、写文件确认、`@` 引用工作区、上下文压缩、执行中插话（steering）、watchdog 崩溃恢复 | 已交付 | `app/agent/`、`app/api/ws.py` |
| 多模型配置：OpenAI 兼容接口多套凭据、按会话切换模型 | 已交付 | `app/api/models.py`、配置页 |
| 本机文件浏览与工作区管理（树/列表/下载/在资源管理器打开） | 已交付 | `app/api/files.py`、`app/api/workspaces.py` |
| 应用工作区：三步向导建应用、重命名/删除、dev/prod 端口对自动分配 | 已交付（M1） | `app/api/apps.py`、`app/deploy/ports.py` |
| Dev 预览：Docker Compose 起停/重启、应用独立 dev 库、右侧抽屉内嵌预览 | 已交付（M1） | `app/deploy/compose.py`、`app/deploy/engine.py` |
| 页面内查看日志：先拉历史再订阅实时（黑底等宽抽屉） | 已交付（M1） | `app/api/dev_logs.py`、`app/deploy/hub.py`、`frontend/src/components/DeployLogPanel.vue` |
| sqlchange 数据变更：按序执行、只补跑未执行过的文件 | 已交付（M1） | `app/deploy/sqlrun.py`、`app/api/apps.py` |
| Prod 打包发布：发布流水线、失败自动回退、保留 5 份产物 | 在途（M2） | 分支 `feature/zerocode-deploy-m2`，规格 `docs/superpowers/specs/2026-09-18-zerocode-deploy-suite/04-Prod打包发布.md` |
| 大型应用技术栈 Vue3 + Spring Boot | 未开始 | 当前仅有 `app/templates/vue3-fastapi/` 一套模板 |

## 启动步骤

1. **启动 PostgreSQL 容器**（首次需 `docker run`，之后 `docker start zerocode-pg16`）：

   ```powershell
   docker run -d --name zerocode-pg16 `
     -e POSTGRES_USER=zerocode -e POSTGRES_PASSWORD=zerocode123 -e POSTGRES_DB=zerocode `
     -p 5433:5432 postgres:16-alpine
   ```

2. **安装后端依赖**：

   ```powershell
   python -m venv .venv
   .venv\Scripts\pip install -r requirements.txt
   ```

3. **安装前端依赖**（新克隆没有 node_modules，构建前必须先装）：

   ```powershell
   npm install --prefix frontend
   ```

4. **构建前端并启动**（双击或命令行运行，均在仓库根）：

   ```powershell
   .\build.bat   # 进入 frontend 执行 npm run build，产物在 frontend/dist
   .\start.bat   # 优先用 .venv\Scripts\python.exe 启动 uvicorn app.main:app --host 127.0.0.1 --port 8100（无 .venv 时回退 PATH 中的 python）
   ```

   手动等价命令：`npm run build --prefix frontend` + `.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8100`（与 start.bat 一致优先 venv；若无 .venv 则用 `python -m uvicorn ...`，需全局已装 uvicorn）。

5. **开发模式（可选，推荐日常改代码用）**：双击或命令行运行 `.\dev.bat` —— 依次做四件事：尝试启动 PG 容器 `zerocode-pg16`（失败只告警并打印手动命令）→ 缺 `.venv` 则创建并按 `requirements.txt` 补装 → 缺前端依赖则 `npm install` → 新窗口起后端 `:8100`、当前窗口起 Vite dev `:8082`（按 `frontend/vite.config.js` 代理 `/api`、`/ws`）。浏览器访问 <http://127.0.0.1:8082>，改前后端代码即时生效，无需先跑 `build.bat`。故意不加 `--reload`：部署套件会往 `apps/` 写生成文件，热重载会被自己触发刷屏。

6. **配置 LLM**：启动后打开 <http://127.0.0.1:8100> → 配置页，填入 `llm.base_url`、`llm.api_key`、`llm.model`（OpenAI 兼容接口，如 DeepSeek）。

7. **访问**：<http://127.0.0.1:8100>（后端同时静态托管前端；非 `/api`、`/ws` 路径 SPA 回退 index.html）。

## 应用（部署预览）

面向零开发经验用户：像聊天一样把想要的软件（台账、登记、查询页面）讲给 AI，它边写边让你在本机预览。

1. **新建应用**：侧栏「应用」→「新建应用」，三步向导——起名字（应用标识用小写字母开头、仅字母/数字）→ 选规模 → 选技术栈。当前阶段（M1）仅开放「小型 + Vue3 + FastAPI」；大型应用的 Vue3 + Spring Boot 尚未实现。
2. **让 AI 长功能**：在聊天页选中该应用作为工作区，直接说需求（如"加一个来料登记页面"）。涉及数据变更时，AI 会写成应用内 `sqlchange/V日期_序号_说明.sql`，启动预览时自动按顺序执行，只补跑没执行过的文件。
3. **启动预览**：应用卡片上有「启动预览 / 停止 / 重启」按钮。状态变「运行中」后，点「预览」在右侧抽屉里直接操作页面，或浏览器访问本机地址 `http://localhost:<端口>`（端口自动分配，卡片上可见）。改了代码或新增 SQL 后，点「重启」即可生效。
4. **看日志排错**：卡片上的「查看日志」打开抽屉，先拉最近的历史输出，再经 `/ws/deploy/{app_id}` 订阅实时行——启动失败、迁移报错、容器起不来都能在页面上直接看到，不必回终端。

前提：Docker Desktop 正在运行，且数据库容器 `zerocode-pg16` 已启动（见上文「启动步骤」第 1 步）。预览用的是应用独立 dev 库，随便试错不影响正式数据；正式上线见下文「发布上线」。

## 发布上线

把预览满意的应用一键发布成局域网可访问的正式版本：应用卡片点「发布」，流水线跑七步（发布前检查 → 镜像构建 → 数据库供给 → 数据库迁移 → 正式栈启动 → 联合体检 → 发布记账），完成后弹出访问地址。

1. **发布**：`vue3-fastapi` 栈的应用卡片上点「发布」。若 AI 正在该应用里改代码，会先弹确认框（可选择等一等或继续）。发布期间旧版本全程可访问；构建/体检失败自动回退上一版本，弹层里能看到失败步骤与构建日志，可一键把错误日志复制给 AI 排查。「发布历史」抽屉里可回滚到任意历史版本（只切程序版本，数据库保持当前状态）。
2. **复制地址**：完成弹层里点大号 URL 即复制，或「复制给同事」直接把引导话术带走。正式端口是偶数（与预览端口分开），多网卡机器可在弹层里选默认网卡决定局域网地址。
3. **同事打不开时三提醒**：① 同网段——两机 IP 前三段需一致；② 防火墙——被挡时按弹层给出的 `netsh` 命令以管理员身份放行端口（可转给 IT）；③ 地址完整性——要带 `http://` 前缀和端口号，别只填 IP。

平台重启后：被中断的发布记账会标为 `interrupted`，正式环境状态按容器实况自动复位，卡片上「启动正式 / 停止正式」随时可切。连续发布只保留最近 5 个版本的镜像，旧镜像自动清理。

## 应用商店

把发布好的应用上架，同事在商店里申请，你审批后平台自动为他建一份独立实例（独立代码目录 + 独立数据库 + 独立地址）。

1. **上架**：应用卡片「发布历史」旁的「发布到商店」，填名称与简介。平台取的是**正式库真实执行过的数据变更序列**，本地预览里试过又没发布的变更不会进包。
2. **申请与审批**：对方在侧栏「商店」首次使用留个名字→ 点「申请使用」→ 你在「待我审批」点「通过并安装」，安装进度在日志里看。
3. **数据各自一份**：新实例从空库起步，按上架版本的结构自动建表；你看不见对方的数据，对方也拿不到你的。
4. **升级**：使用者在自己卡片上点「升级」，平台先在真数据的克隆库上预演一遍再动真库（M3-b）。
5. **纪律**：上架后的应用，数据变更文件只能追加；改列类型这类破坏性变更要分两步（先加新列双写，下个版本再删旧列），否则订阅实例无法安全跟随升级。

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
    vue3-fastapi/      生成应用的技术栈模板（自带 sqlchange 规范 README）
frontend/              Vue 3 单页应用（views：ChatView / AppsView / SettingsView）
  src/components/      聊天与部署组件：ChatMessages、ToolCallBlock、ThinkingBlock、DiffBlock、
                       AskUserCard、TodoBar/TodoCard、FilePanel、WorkspaceExplorer、
                       DeployLogPanel（日志抽屉）、SideBar、NewSessionModal、MarkdownView …
  src/stores/          Pinia：chat.js、config.js
  src/utils/           纯逻辑层（可单测）：deployLog.js、diff.js、fsNav.js、mentions.js、tree.js、appForm.js …
apps/                  AI 生成的应用工作区（每台机器各自生成，已 gitignore，不入库）
changelog/             平台自身 DDL/DML，V<YYYYMMDD>_<序号>_<说明>.sql，启动时自动迁移
docs/superpowers/
  plans/               施工计划（含部署套件 M1、M2 任务清单）
  specs/               设计规格（部署套件八篇：应用模型 / 模板规范 / Dev 预览 / Prod 发布 /
                       端口管理 / 数据库供给 / sqlchange / 平台 UI）
tests/                 后端 pytest，30 个测试模块，与 `app/` 分层对应
```

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

## 技术栈

- **后端**：Python 3.11+、FastAPI ≥ 0.115、SQLAlchemy ≥ 2.0.30、asyncpg、Pydantic 2、OpenAI SDK（OpenAI 兼容接口）、uvicorn。见 `requirements.txt`。
- **前端**：Vue 3.5 + Ant Design Vue 4.2 + Pinia + Vue Router + Less，构建 Vite 8，单测 Vitest 5（happy-dom）。
- **数据库**：PostgreSQL 16（容器 `zerocode-pg16`，宿主端口 `5433`）。平台表结构走 `changelog/`，应用库由 `app/deploy/provision.py` 按应用供给独立 dev 库。
- **容器**：Docker Compose 承载生成应用的 dev/prod 运行，端口对由 `app/deploy/ports.py` 分配并落库为 UNIQUE 约束兜底。

## 环境变量

| 变量 | 默认值 | 作用 |
| --- | --- | --- |
| `ZEROCODE_DATABASE_URL` | `postgresql+asyncpg://zerocode:zerocode123@localhost:5433/zerocode` | 平台库连接串 |
| `ZEROCODE_TEST_DATABASE_URL` | `postgresql+asyncpg://zerocode:zerocode123@localhost:5433/zerocode_test` | pytest 测试库；多分支并行施工时建议各用独立库（M2 惯例：`zerocode_test_m2`） |
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
feature/zerocode-deploy-m1    ◀─工作区─  D:\project\zerocode  （M1：dev 预览 + 日志）
feature/zerocode-deploy-m2    ◀─工作树─  D:\project\zc-m2     （M2：Prod 打包发布）
```

- 本地主干分支叫 `main`、远端主干叫 `master`，推送用 refspec：首次已用 `git push --set-upstream origin main:master` 建好跟踪，之后在 `main` 上直接 `git push` 即可。
- `D:\project\zc-m2` 是同一仓库的 linked worktree，与主工作区共享 `.git`，remote 与分支跟踪配置两边同源；M2 施工不进主工作区，改动只落 `zc-m2`。

## 约定

- 端口：后端 `8100`，前端 Vite dev `8082`，PG 宿主 `5433`；数据库可用环境变量 `ZEROCODE_DATABASE_URL` 覆盖。
- DDL/DML 一律走 `changelog/V<YYYYMMDD>_<序号>_<说明>.sql`，应用启动时自动迁移。
- 测试：`.venv\Scripts\python.exe -m pytest tests -v`（测试库 `zerocode_test`）；前端 `npx vitest run`（在 `frontend/` 下）。
- `apps/` 与 `frontend/dist/` 是生成物，已在 `.gitignore`，不入库。
- 提交只显式列文件（`git add <路径>`），不用 `git add -A`；提交前确认目标文件 `git diff HEAD` 为空，避免把无关的在途改动混进同一个提交。
