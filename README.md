# zerocode

类 DSH 的 AI 编程助手：FastAPI + PostgreSQL 后端、Vue 3 前端，单体部署（方案 A）。
Agent 循环具备工具调用、写文件确认、会话持久化与崩溃恢复（watchdog）。

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

5. **配置 LLM**：启动后打开 <http://127.0.0.1:8100> → 配置页，填入 `llm.base_url`、`llm.api_key`、`llm.model`（OpenAI 兼容接口，如 DeepSeek）。

6. **访问**：<http://127.0.0.1:8100>（后端同时静态托管前端；非 `/api`、`/ws` 路径 SPA 回退 index.html）。

## 应用（部署预览）

面向零开发经验用户：像聊天一样把想要的软件（台账、登记、查询页面）讲给 AI，它边写边让你在本机预览。

1. **新建应用**：侧栏「应用」→「新建应用」，三步向导——起名字（应用标识用小写字母开头、仅字母/数字）→ 选规模 → 选技术栈。当前阶段（M1）仅开放「小型 + Vue3 + FastAPI」；大型应用的 Vue3 + Spring Boot 即将上线。
2. **让 AI 长功能**：在聊天页选中该应用作为工作区，直接说需求（如"加一个来料登记页面"）。涉及数据变更时，AI 会写成应用内 `sqlchange/V日期_序号_说明.sql`，启动预览时自动按顺序执行，只补跑没执行过的文件。
3. **启动预览**：应用卡片上有「启动预览 / 停止 / 重启」按钮。状态变「运行中」后，点「预览」在右侧抽屉里直接操作页面，或浏览器访问本机地址 `http://localhost:<端口>`（端口自动分配，卡片上可见）。改了代码或新增 SQL 后，点「重启」即可生效。

前提：Docker Desktop 正在运行，且数据库容器 `zerocode-pg16` 已启动（见上文「启动步骤」第 1 步）。预览用的是应用独立 dev 库，随便试错不影响正式数据；正式上线见下文「发布上线」。

## 发布上线

把预览满意的应用一键发布成局域网可访问的正式版本：应用卡片点「发布」，流水线跑七步（发布前检查 → 镜像构建 → 数据库供给 → 数据库迁移 → 正式栈启动 → 联合体检 → 发布记账），完成后弹出访问地址。

1. **发布**：`vue3-fastapi` 栈的应用卡片上点「发布」。若 AI 正在该应用里改代码，会先弹确认框（可选择等一等或继续）。发布期间旧版本全程可访问；构建/体检失败自动回退上一版本，弹层里能看到失败步骤与构建日志，可一键把错误日志复制给 AI 排查。「发布历史」抽屉里可回滚到任意历史版本（只切程序版本，数据库保持当前状态）。
2. **复制地址**：完成弹层里点大号 URL 即复制，或「复制给同事」直接把引导话术带走。正式端口是偶数（与预览端口分开），多网卡机器可在弹层里选默认网卡决定局域网地址。
3. **同事打不开时三提醒**：① 同网段——两机 IP 前三段需一致；② 防火墙——被挡时按弹层给出的 `netsh` 命令以管理员身份放行端口（可转给 IT）；③ 地址完整性——要带 `http://` 前缀和端口号，别只填 IP。

平台重启后：被中断的发布记账会标为 `interrupted`，正式环境状态按容器实况自动复位，卡片上「启动正式 / 停止正式」随时可切。连续发布只保留最近 5 个版本的镜像，旧镜像自动清理。

## 约定

- 端口：后端 `8100`，前端 Vite dev `8082`，PG 宿主 `5433`；数据库可用环境变量 `ZEROCODE_DATABASE_URL` 覆盖。
- DDL/DML 一律走 `changelog/V<YYYYMMDD>_<序号>_<说明>.sql`，应用启动时自动迁移。
- 测试：`.venv\Scripts\python.exe -m pytest tests -v`（测试库 `zerocode_test`）；前端 `npx vitest run`（在 `frontend/` 下）。
