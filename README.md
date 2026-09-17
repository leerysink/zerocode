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

## 约定

- 端口：后端 `8100`，前端 Vite dev `8082`，PG 宿主 `5433`；数据库可用环境变量 `ZEROCODE_DATABASE_URL` 覆盖。
- DDL/DML 一律走 `changelog/V<YYYYMMDD>_<序号>_<说明>.sql`，应用启动时自动迁移。
- 测试：`.venv\Scripts\python.exe -m pytest tests -v`（测试库 `zerocode_test`）；前端 `npx vitest run`（在 `frontend/` 下）。
