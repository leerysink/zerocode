# Agent 预览验证工具（preview tools）规格说明

**日期**：2026-10-29  **来源**：需求访谈（「Agent 执行终端命令」可行性 → 第一步）  **状态**：待实现

**相关文件**：[tools.py](../../app/agent/tools.py)、[loop.py](../../app/agent/loop.py)、[llm.py](../../app/agent/llm.py)、[prompts.py](../../app/agent/prompts.py)、[engine.py](../../app/deploy/engine.py)、[hub.py](../../app/deploy/hub.py)、[apps.py](../../app/api/apps.py)、[dev_logs.py](../../app/api/dev_logs.py)、[app_service.py](../../app/services/app_service.py)、[ws.py](../../app/api/ws.py)、[chat.js](../../frontend/src/stores/chat.js)、[ChatMessages.vue](../../frontend/src/components/ChatMessages.vue)

## 1. 问题陈述

Agent 现在只有 7 个工具（`list_dir` / `read_file` / `write_file` / `edit_file` / `ask_user` /
`todo_write` / `memory_write`）。它能改代码，却**无法回答「改完能不能跑」**：看不到 dev 预览是否启动、
看不到 Vite 编译错误、看不到后端 traceback。结果是它只能凭静态阅读宣称「已完成」，用户仍要手动点
「预览」、翻日志、再把报错贴回来——「改 → 验 → 读错 → 修」这条最高频的回路断在 Agent 手里。

最直接的补法是给 Agent 一个终端，但**平台不能这么做**：

- 平台后端容器持有宿主 `docker.sock`（[ADR-0013](../adr/0013-backend-containerized-same-path-mount.md)），
  等价于持有宿主 root 权限。一条任意 shell 命令就能越过所有应用边界。
- 工作区内容不可信：商店导入的工程、用户自写技能、`@"path"` 内联的文件都可能藏提示注入。
  给 shell 等于把「模型被注入」升级成「宿主命令执行」。
- shell 会绕过现有的三道收口：工作区路径围栏（`resolve_in_workspace`）、写入确认、`file_change` 改动账本。
- 多用户平台上，一个 shell 能读到别人的工作区、平台库超级角色凭据与 `deploy/.env`。
- 就算把 shell 塞进 dev 容器，那也是 root + `zcnet` + dev 库凭据，还能顺手杀掉正在跑的预览服务。

所以真正缺的不是「命令执行」，而是**「把预览跑起来并读到结果」这一件窄事**。平台后端与前端其实已经
把这件事做完了（引擎、部署任务表、DeployHub、日志归并、凭据遮断），只差把它们暴露给 Agent。
本规格只开这三个预览工具（两个只读、一个会启动/重启），不开 shell。

## 2. 方案概述

- **三个工具**：`preview_status`（只读状态与可用性）、`preview_logs`（只读最近日志）、
  `preview_restart`（启动或重启并等待结果）。没有第四个「万能执行」工具。
- **应用身份由平台解析**：从会话的 `workspace_id` 反查 `app_project`，工具 schema 里**没有**
  `app_id` / `slug` 参数，模型无从指定别的应用。
- **复用现有引擎与事件流**：`engine.start_dev` / `restart_dev` / `preview_url` / `container_logs`、
  `app.state.dev_tasks`、`DeployHub`、`app/agent/redact.py` 的遮断规则全部沿用，不新造第二套部署逻辑。
- **共享任务表**：REST 的 `_spawn` 与 Agent 共用 `app.state.dev_tasks`，同一应用同时只允许一个部署任务；
  Agent 不会和用户手点的「启动预览」双跑。
- **策略**：`stopped` / `error` → 启动；`running` → 重启（先确认）；`starting` / `stopping` → 只观察现有任务；
  已有其它部署任务（如发布）→ 拒绝并发，不排队。
- **等待有界**：默认最多等 240 秒，超时返回 `starting` 并提示继续观察；聊天被取消只停止等待，
  预览任务在后台继续跑完。
- **单次 run 最多 3 次启动/重启**，防止模型改一行就重启一次的抖动循环。
- **不做**：任意 shell、prod 发布/回滚、停止预览、数据库操作、跨应用操作、浏览器截图。

## 3. 用户故事（可独立验证）

### 存在性与边界

- **US-01** 作为用户，我在**非应用工作区**（没有 `app_project` 行）的会话里，模型的工具列表中没有三个
  `preview_*` 工具；我让它「起一下预览」，它会说明当前工作区不是应用工程（工具未挂载，模型不会看到
  一个可调用的假工具）；即使历史里遗留了调用，handler 也返回明确错误。
- **US-02** 作为用户，我在应用工作区的会话里，模型能看到三个预览工具，system 尾部出现 `<preview>` 指引。
- **US-03** 作为用户，我在会话进行中删除了应用（另一个标签页操作），Agent 再调预览工具时得到
  「应用已被删除或不可用」的正常结果，不抛 500、不留下后台任务。
- **US-04** 作为用户，我无法通过对话让 Agent 操作**别人**的应用或另一个应用：工具没有应用参数，
  解析只认当前会话所属工作区。

### 状态（preview_status）

- **US-05** 预览 `stopped` 时，返回 `status=stopped`、`healthy=false`，并给一句「预览未运行」的提示。
- **US-06** 预览 `running` 且探针 200 时，返回 `status=running`、`healthy=true`、可点击的 `url`。
- **US-07** 预览 `running` 但探针 403/拒连时，返回 `healthy=false`，`probe` 里带上最后一轮的判定
  （例如 `"/ → 403"` 或 `ConnectError: ...`），**不把「HTTP 可达」说成「功能正确」**。
- **US-08** Docker 没开时，`preview_status` 仍能返回数据库里的状态与 `url`，`probe` 说明连接失败；
  不因为探针失败就整体报错。
- **US-09** 该应用有后台部署任务在跑时，返回 `deploy_task=true`，让模型知道现在不该重复启动。

### 日志（preview_logs）

- **US-10** 返回最近的平台步骤行（预检/渲染/迁移/起栈/健康检查）与容器输出，按时间归并，每行带时间与来源。
- **US-11** `tail` 默认 80、夹在 10–300；总字符超限时从最旧的行开始丢，并标 `truncated=true`。
- **US-12** 容器日志取不到（Docker 没开、栈没起）时返回 `note` 说明原因，平台步骤行照样返回。
- **US-13** 日志里的已知凭据形态（API key、数据库 URL 口令等）被替换为 `<redacted:kind>`；
  容器输出里的 ANSI 转义不出现。
- **US-14** 读日志不改变预览状态、不产生部署任务。

### 重启（preview_restart）

- **US-15** `stopped` / `error` 时执行启动；`running` 时执行重启；两者最终都返回真实终态。
- **US-16** 预览正在 `starting`（用户刚点过启动）时，工具**等待现有任务**并回报结果，不重复起第二个栈。
- **US-17** 等待期间每收到一条平台步骤行就下发 `tool_progress`，前端能看到「迁移中 / 起栈中 / 等待就绪」。
- **US-18** 默认等 240 秒仍未结束时返回 `ok=false`、`status=starting` 和「仍在启动，请继续观察」的提示；
  后台任务不取消。
- **US-19** 我点「停止」取消本轮聊天时，工具在一个轮询周期内返回，预览任务继续在后台跑；本轮以「已取消」收口。
- **US-20** 启动失败（迁移失败 / compose 失败 / 健康超时）时，返回 `ok=false`、`status=error`、错误原文
  与最近日志，模型不必再猜。
- **US-21** 预览正在 `running` 时，重启前弹出确认；我拒绝则**不发生任何启停**，模型收到「用户拒绝重启预览」。
  `stopped` / `error` 的启动不弹确认。
- **US-22** 单次 run 内达到上限后再次启动/重启被拒绝，返回「本次任务已启动/重启预览 N 次」的提示，
  不执行（默认上限 3）。
- **US-23** Docker 预检失败时，返回中文引导（与 REST 的 503 文案同源），不登记后台任务。
- **US-24** Agent 重启预览时，应用页的预览日志面板照常实时收到 `dev_log` / `dev_status`——两条通道共用
  同一个 DeployHub。

## 4. 工具契约

### 4.1 Schema

工具描述会随每次 LLM 请求发送，保持一句话、不做长篇教程。

```json
{"type": "function", "function": {
  "name": "preview_status",
  "description": "查看当前应用 dev 预览的状态与可用性（只读）",
  "parameters": {"type": "object", "properties": {}, "required": []}}}

{"type": "function", "function": {
  "name": "preview_logs",
  "description": "读取当前应用 dev 预览的最近日志（平台步骤行 + 容器输出，只读）",
  "parameters": {"type": "object", "properties": {
    "tail": {"type": "integer", "description": "最近多少行，默认 80，最大 300"}},
    "required": []}}}

{"type": "function", "function": {
  "name": "preview_restart",
  "description": "启动或重启当前应用的 dev 预览，等待就绪后返回最终状态与日志。改了依赖清单/配置、或预览未运行时使用；纯代码改动通常会自动热更新，先看 preview_status/preview_logs",
  "parameters": {"type": "object", "properties": {
    "wait_seconds": {"type": "integer", "description": "最多等待多少秒，默认 240；超时返回 starting，预览仍在后台继续"}},
    "required": []}}}
```

### 4.2 返回形状

成功状态：

```json
{"ok": true, "app": {"slug": "demo", "stack": "vue3-fastapi"},
 "status": "running", "healthy": true, "probe": "",
 "url": "http://localhost:8213", "deploy_task": false, "hint": ""}
```

日志：

```json
{"ok": true, "status": "error", "url": "http://localhost:8213",
 "lines": ["10:00:01 [平台] ▶ 启动预览 demo（dev）", "10:00:03 [backend] ..."],
 "truncated": false, "note": "", "redacted": 0}
```

行格式为 `时间 [来源] 正文`，时间到秒（本地时区，示例省略日期）。

重启成功：

```json
{"ok": true, "status": "running", "healthy": true, "probe": "",
 "url": "http://localhost:8213", "elapsed_s": 34.2,
 "app": {"slug": "demo", "stack": "vue3-fastapi"},
 "restarts_left": 2, "note": ""}
```

重启失败：

```json
{"ok": false, "status": "error", "url": "http://localhost:8213", "elapsed_s": 63.2,
 "error": "dev 迁移失败（sqlrunner 退出码 1），详情见迁移日志",
 "logs": ["[平台] ✗ 迁移失败，dev 栈不启动", "[migrate] error: column ... does not exist"],
 "restarts_left": 2, "hint": ""}
```

等待超时：

```json
{"ok": false, "status": "starting", "elapsed_s": 240.0,
 "error": "", "hint": "预览仍在启动，用 preview_status / preview_logs 继续观察；不要连续重启",
 "restarts_left": 2}
```

聊天被取消（工具尽快返回，任务继续）：

```json
{"ok": true, "cancelled": true, "status": "starting", "elapsed_s": 42.0,
 "hint": "用户已停止本轮；预览仍在后台继续启动"}
```

语义约定：

- `ok` 表示**这次调用本身是否完成**，不等于预览健康。`preview_status` / `preview_logs` 在应用报错时
  仍是 `ok=true` + `status=error`；`preview_restart` 的 `ok` 才表示启动动作成功。
- 非应用工作区、应用被删除、已有别的部署任务、次数用尽等都是**正常工具结果**（`ok=false` + `error`），
  不抛异常——模型需要读到原因并向用户解释。
- handler 不向 loop 抛 `DeployError`；所有引擎异常在工具内被塑造成上面的结果形状。

## 5. 运行时设计

### 5.1 应用解析（唯一身份来源）

新增 `app_service.get_app_by_workspace(session_factory, workspace_id, *, owner_user_id=None)`：

```sql
SELECT id, workspace_id, name, slug, scale, stack, prod_port, dev_port,
       status_dev, status_prod, current_version, origin, created_at, updated_at
FROM app_project
WHERE workspace_id = :w
  AND (:owner IS NULL OR owner_user_id = :owner)
ORDER BY id
LIMIT 2
```

- 查回 0 行 → 不是应用工作区，预览工具不挂载。
- 查回 2 行 → **fail closed**：`app_project.workspace_id` 没有 UNIQUE 约束，历史上可能手工插入重复行；
  这时不猜、也不挂载预览工具，记一条 warning，并在 system prompt 追加一句「当前工作区绑定了多个应用，
  无法使用预览工具」，让模型能向用户说明原因。
- 解析过程本身出错（数据库异常）→ 记 warning，按非应用工作区处理，**不让整轮对话失败**。
- `owner_user_id` 过滤与 `list_apps` 同一条判据：会话已经过 WS 所有权校验，这里是第二道闸；
  存量 NULL 归属的应用在应用列表里同样不可见，不制造新的不一致。
- 解析在每次 run 开始时做一次（`AgentRunner._inner`），结果放进 `ToolContext.app`；
  **每次工具调用都重新 `get_app` 读一次**，拿到最新 `status_dev` / `dev_port` / `preview_url`，
  并识别「运行中应用被删」。快照里的状态永远不可信。

### 5.2 共享任务注册表

现状：`app/api/apps.py` 里的 `_spawn` 与 `_log_task_crash` 只服务 REST；Agent 若另建一套，
同一应用会被起两个栈。抽成 `app/deploy/tasks.py`：

```python
def spawn(tasks: dict[int, asyncio.Task], app_id: int, coro_factory) -> asyncio.Task
def task_running(tasks: dict[int, asyncio.Task], app_id: int) -> bool
def log_task_crash(app_id: int, task: asyncio.Task) -> None
```

- 值仍是裸 `asyncio.Task`（现有测试直接 `await client.app.state.dev_tasks[app_id]`，不改形状）。
- `spawn` 在已有未完成任务时抛 `DeployError(..., status=409)`，文案保持「该应用有部署任务进行中，请稍候」。
- `log_task_crash` 保持现有日志消息「部署后台任务异常退出（app_id=%s）」，`caplog` 用例不改。
- REST 的 `_spawn` 改为委托 `tasks.spawn`，行为与状态码不变；Agent 用同一个 `app.state.dev_tasks`。
- **部署任务独立于聊天 run**：聊天取消只停止等待，不取消任务。

### 5.3 等待循环

```
preflight_docker()                    # 失败 → 直接返回 ok=false，不登记任务
q = hub.subscribe(app_id)             # 先订阅再 spawn，避免漏掉最早的事件
try:
    task = existing_task or spawn(...)    # spawn 的 409 也转成正常结果，不继续等待
    deadline = None if wait_seconds <= 0 else monotonic() + wait_seconds
    while not task.done():
        drain(q) → 只转发 [zerocode] 步骤行（再过一遍 redact_sensitive）
        每 30s 补一条「仍在启动，已等待 Ns」
        if ctx.cancel.is_set():
            return {"ok": true, "status": await get_status_dev(...), "cancelled": true,
                    "hint": "用户已停止本轮；预览仍在后台继续启动"}
        if deadline and monotonic() >= deadline:
            return {"ok": false, "status": "starting", "elapsed_s": ...,
                    "hint": "预览仍在启动，用 preview_status / preview_logs 继续观察；不要连续重启",
                    "logs": 最近 20 行}
        await asyncio.sleep(0.5)
    exc = task.exception()            # 先判 cancelled() 再取 exception()
    ...
finally:
    hub.unsubscribe(app_id, q)
```

- 进度节流：同一时刻最多一条/250ms；只转发 `dev_log` 中以 `[zerocode]` 开头的步骤行与
  `dev_status` 的状态变化，**不转发** pip/npm 的成百上千行容器输出（那属于 `preview_logs`）。
- `dev_status` 事件转成一行自然语言进度（例如「预览状态：starting」），仍放在 `line` 字段里。
- 取消检查每 0.5 秒一次，保证用户点「停止」后工具在一个轮询周期内返回。
- 任务结束（无论成败）后再 drain 一次队列，把最后几条步骤行纳入进度与失败日志。
- `spawn` 抛出的 409（另一条通道刚抢先起了任务）不登记新任务，直接返回「该应用有部署任务进行中」。
- 任务异常：`DeployError` → `error=str(e)`；其它异常 → `error=f"预览任务异常: {e}"`；
  `CancelledError` → 「预览任务已被取消」。三种都附带 `status` 与最近日志。
- 成功后做一次 2 秒探针（复用 `probe_dev`），`healthy` 反映真实可达性；探针失败不把 `ok` 翻成 false。

### 5.4 状态与探针

- `preview_status` **不调用** `engine.dev_status`（那会起一个 `docker compose ps` 子进程）。
  它读数据库状态 + 一次短探针，足够回答「活着吗、能访问吗」，且调用频率可能高于应用页。
- 在 `engine.py` 抽出 `_dev_probe_paths(stack)`（前端 `/` + 大型栈的后端路径），
  `start_dev` 与新函数 `probe_dev(app, timeout=2)` 共用它，避免健康路径出现第二份真相。
- 只有 `status == "running"` 才探针；其余状态 `healthy=false`、`probe=""`，并给 `hint`。
- `url` 取 `app["preview_url"]`（`_with_public_urls` 生成）；`dev_port` 缺失时为空串，
  此时 `hint` 说明「应用没有 dev 端口」。
- `healthy=true` 只代表「前端 `/`（大型栈再加后端健康路径）返回 200」，**不代表新改动已编译通过**；
  编译错误与 traceback 要看 `preview_logs`。

### 5.5 重启策略

| 当前 `status_dev` | 有未完成任务 | 动作 |
|---|---|---|
| `stopped` / `error` / 未识别值 | 否 | `start_dev`（不先 down，与应用页「启动预览」一致） |
| `running` | 否 | `restart_dev`（= stop + start）；默认先确认 |
| `starting` / `stopping` | 是 | 观察现有任务，等待并回报 |
| 任意 | 是，但状态不是 `starting` / `stopping` | 返回 `ok=false`「该应用有部署任务进行中（可能正在发布），请稍后再试」 |

- `stopping` 只是 WS 事件名，数据库不落这个值；表里保留它是为了说明事件语义。
- 工具从不单独调用 `stop_dev`，也没有 `preview_stop`。
- 若确认期间预览从 `running` 变成了 `stopped`，确认通过后按启动执行（不构成中断，无需二次确认）。

### 5.6 确认

- 条件：`status_dev == "running"` **且** `agent.preview_confirm_running` 为真（默认）。
- 复用现有 `confirm_request` 事件与 `ChatHub.confirm` future，**AgentRunner 仍是唯一发送方**；
  事件增加 `summary` 字段：

```json
{"type": "confirm_request", "tool": "preview_restart", "args": {"wait_seconds": 240},
 "summary": "应用「演示应用」的 dev 预览正在运行，重启会中断正在进行的调试。确定重启？"}
```

- 用户拒绝 → 返回 `{ok:false, "error":"用户拒绝重启预览"}`，不 spawn 任务。
- 已有未完成任务时**不发确认**：handler 会直接返回并发提示，不应该先让用户确认一次再告诉他做不了。
- 次数上限在确认**之前**检查：到限直接返回，不打扰用户点确认。
- **竞态兜底**：`_dispatch` 把确认结果作为 `confirmed` 传给 handler；若 `_dispatch` 读状态时不是 `running`
  （没有确认），但 handler 临 spawn 前发现已是 `running`，则返回
  `{ok:false, "error":"预览已在运行，请重试并在确认框中选择确认"}`，不执行未确认的中断。
- `stopped` / `error` 启动、`starting` 观察都不弹确认。

### 5.7 单次 run 次数上限

- `ToolContext.run_state` 是每次 `AgentRunner.run` 新建的 dict，`preview_restarts` 计数只在
  **真正 spawn 任务时** +1；观察现有任务不算。
- 上限读 `agent.preview_max_restarts`（默认 3，`<=0` 不限）；到限返回：
  `{"ok": false, "error": "本次任务已启动/重启预览 N 次，请先检查失败原因再继续", "restarts_left": 0}`
  （N 为当前配置值）。
- 返回值带 `restarts_left`，模型能自己判断还剩几次。
- 跨用户消息重置：计数不落库，下一条用户消息重新开始。

### 5.8 热更新语义（写进提示词，也决定重启策略）

| 栈 | 前端改动 | 后端改动 | 依赖/配置改动 |
|---|---|---|---|
| `vue3-fastapi` | Vite HMR | `uvicorn --reload` 自动重载 | 必须重启（`requirements.txt` 不会自动重装） |
| `vue3-springboot` | Vite HMR | `mvn spring-boot:run`，无 devtools，**必须重启** | 必须重启（`pom.xml` / 环境变量） |
| `vue3-node` / `static` | Vite HMR / 刷新即可 | 无 / 无 | 必须重启 |

Agent 不应「每改一行就重启」：先 `preview_status`，`healthy=true` 时让用户刷新；只有改动不可能热更新、
或状态不是 `running`、或用户明确要求时才重启。

### 5.9 配置项

全部是平台级键（`agent.*` 不在 `USER_PREFIXES` 里），普通用户改动会影响全平台，这是既有取舍。
默认值写在代码里（与 `agent.max_tool_rounds` 同样的读取方式），**不新增数据库迁移**。

| key | 默认 | 夹取 | 说明 |
|---|---|---|---|
| `agent.preview_wait_seconds` | 240 | 0–900 | `preview_restart` 的默认等待上限；`<=0` 表示等到任务结束 |
| `agent.preview_log_lines` | 80 | 10–300 | `preview_logs` 的默认行数 |
| `agent.preview_max_restarts` | 3 | `<=0` 不限 | 单次 run 内最多启动/重启次数 |
| `agent.preview_confirm_running` | true | 布尔 | 中断运行中的预览前是否确认 |

工具参数 `wait_seconds` 与配置值同样夹在 0–900；未提供时用 `agent.preview_wait_seconds`，
`<=0` 表示一直等到任务结束（仍可被「停止」取消等待）。

## 6. 安全边界

- **应用身份不可由模型指定**：schema 无 `app_id` / `slug` / 路径参数；解析只认
  `chat_session.workspace_id → app_project`，再按 owner 过滤。跨应用、跨用户没有入口。
- **动作面只有引擎已有的三个动作**：启动、重启、读状态/日志。没有 shell、没有 `docker exec`、
  没有容器内命令、没有包管理器、没有测试命令。
- **工作区围栏不变**：预览工具不接收路径，不读 `deploy/**`，不读 `app.json`；`resolve_in_workspace` 的规则
  与写确认、改动账本对这些工具不需要开口子。
- **日志出口收口**：平台步骤行与容器输出在返回模型之前逐行跑 `redact_sensitive`，已知凭据形态替换为
  `<redacted:kind>`；单行 500 字、总量 16KB、最多 300 行，避免把整份启动日志灌进上下文。
  残余风险：应用用自定义编码（base64、拼接）打印的口令无法被规则识别——日志只尾读，且只在排障时读。
- **并发**：与 REST 共用 `app.state.dev_tasks`，同一应用至多一个部署任务；重复请求返回 409 语义的正常结果。
- **次数上限**：单次 run 3 次，防止模型在失败循环里反复起栈、反复拉镜像。
- **prod 隔离**：工具只碰 `status_dev` / dev 栈，不发布、不回滚、不启停 prod、不读 prod 日志。
- **提示注入的最坏结果**：在自己的应用上重启一次 dev 预览、读自己的日志、把日志发给模型。
  与「宿主 root shell」不是一个量级；且运行中的重启还需要用户确认。
- **Docker 不可用**：预检失败返回中文引导，不登记任务；状态工具仍能工作（数据库读 + 探针失败说明）。
- **平台权限模型不变**：沿用登录态与 owner 判据，不新增权限点、不改 `SecurityConfig` 式的平台配置。

## 7. 代码落点

| 文件 | 变更 |
|---|---|
| `app/deploy/tasks.py` | 新增：`spawn` / `task_running` / `log_task_crash`，从 `apps.py` 提取 |
| `app/api/apps.py` | `_spawn` 委托 `tasks.spawn`；409 文案与日志不变 |
| `app/deploy/logs.py` | 新增：`collect_dev_logs`（平台环形缓冲 + 容器日志归并）与 `_iso`，从 `dev_logs.py` 提取 |
| `app/api/dev_logs.py` | 改用共享收集器；`MAX_TAIL`、响应形状、`since_seq` 语义不变 |
| `app/deploy/engine.py` | 新增 `_dev_probe_paths` + `probe_dev`；`start_dev` 复用同一路径函数；`container_logs` 增加 `timeout` 参数（默认 60，工具传 15） |
| `app/services/app_service.py` | 新增 `get_app_by_workspace`（owner 过滤、0/多行语义、`preview_url`） |
| `app/agent/preview_tools.py` | 新增：`resolve_session_app`、`run_preview_tool`、三个 handler、等待循环、日志整形、确认摘要、`build_preview_prompt` |
| `app/agent/llm.py` | 新增 `PREVIEW_TOOLS` 与 `PREVIEW_TOOL_NAMES`；基础 `TOOLS` 不变 |
| `app/agent/tools.py` | `ToolContext` 增加 `app` / `cfg` / `cancel` / `deploy_hub` / `deploy_tasks` / `run_state`；`execute_tool` 懒加载分派 `preview_*` |
| `app/agent/loop.py` | 每次 run 解析 app、按需挂载工具列表、构造 ctx、`preview_restart` 确认分支、注入 `<preview>` 指引 |
| `app/agent/prompts.py` | `build_system_prompt` 增加 `preview=None` 参数，追加在 `app_contract` 之后（默认 None，既有调用不变） |
| `app/api/ws.py` | `AgentRunner` 注入 `app.state.deploy_hub` 与 `app.state.dev_tasks` |
| `frontend/src/stores/chat.js` | 处理 `tool_progress`；`confirm_request` 保存可选 `summary`；`tool_result`/终态清理进度 |
| `frontend/src/components/PreviewCard.vue` | 新增预览状态卡（状态徽标、地址、可折叠日志、错误与提示） |
| `frontend/src/components/ChatMessages.vue` | `preview_*` 结果路由到 PreviewCard；确认卡支持 `summary` 与重启文案 |

`preview_tools.py` **不 import** `ToolContext` 或 `ToolError`（避免与 `tools.py` 循环导入）：handler 只读
ctx 的字段，所有错误都作为结果返回；`tools.py` 在分派分支里惰性 import `run_preview_tool`。

## 8. 前端交互

### 8.1 新事件：`tool_progress`

```json
{"type": "tool_progress", "name": "preview_restart", "line": "[zerocode] → 执行 sqlchange 迁移（dev 库 demo_dev）"}
```

- **临时事件，不落库**：刷新页面后消失，最终结论以 `tool_result` 的持久化结果为准。
- `chat.js` 把它存进 `store.toolProgress = { name, line }`；收到同名 `tool_result`、`message_done`、
  `status` 终态或 `error` 时清空。
- 只在消息流**最后一条**、尚未有 `result` 的同名工具卡上显示，避免历史卡片复活进度条。

### 8.2 PreviewCard 状态

| 输入 | 视觉 |
|---|---|
| 只有 `tool_call`（进行中）+ progress | 蓝色「正在重启预览…」+ 最近一条步骤行 |
| `result.ok` 且 `status=running` 且 `healthy=true` | 绿色「预览已就绪」+「打开预览」链接 |
| `result.ok` 且（`status!=running` 或 `healthy=false`） | 橙色「预览未就绪」+ `probe` / `hint` |
| `result.ok=false` 且无 `error`、有 `hint`（等待超时） | 橙色「仍在启动」+ `hint` + 最近日志 |
| `result.cancelled=true` | 灰色「已停止等待」+ `hint` |
| `result.ok=false` 且有 `error` | 红色「预览操作失败」+ 错误原文 + 可折叠日志 |
| `preview_status` / `preview_logs` 的 `ok=true` | 紧凑状态行或日志块（日志默认折叠，点击展开） |

- 地址链接用 `<a target="_blank" rel="noopener">`，只在 `status=running`、`healthy=true` 且 `url` 非空时出现
  （探针说不可达还给一个「打开预览」，等于把故障藏起来让用户自己撞 403）；`preview_logs` 的结果不带
  `healthy` 字段，因此日志卡永不给链接。不在前端拼 `localhost` / 端口。
- 日志块用等宽字体、最大高度 240px、可滚动；显示 `note` 与「已遮断 N 处凭据」（`redacted > 0` 时）。
- `restarts_left <= 1` 时显示「本次任务还可重启 N 次」，给用户一个预警。

### 8.3 确认卡

- `confirm_request` 增加 `summary` 后，`pendingConfirm` 兼容保存：`ev.summary` 存在才加字段，
  不改变无 `summary` 时既有对象形状（避免既有单测的 `toEqual` 失败）。
- `tool === "preview_restart"` 时：标题「重启预览」、正文用 `summary`、按钮「确认重启 / 取消」；
  其它工具保持「写入确认 / 确认写入 / 拒绝」不变。
- 无 `summary` 时仍回落到原来的 args JSON 摘要，写入确认行为零变化。

### 8.4 历史回放

`tool_call` / `tool_result` 本来就落库；刷新后 PreviewCard 从 `tool_result` 渲染最终状态。
历史消息没有 `args`，卡片不依赖 `args` 才能显示——只有进行中的实时卡片才需要 `args`。

### 8.5 应用页

`preview_restart` 用 `hub.publish` 作为 notify，应用页的 `DeployLogPanel` 订阅的是同一个
`/ws/deploy/{app_id}`，因此 Agent 触发的启动在应用页**同样实时可见**，不需要改应用页。

## 9. 系统提示词

仅当应用解析成功（工具已挂载）时注入，位置在 `app_contract` 之后：

```text
<preview>
当前工作区是可运行的应用工程（技术栈：{栈名}）。改完代码要验证，不要仅凭阅读宣称“已完成”。
- 纯前端/后端代码改动通常会自动热更新：先调 preview_status，healthy=true 时让用户刷新页面。
- 改了依赖清单（package.json / requirements.txt / pom.xml）、环境变量或启动配置，或预览不是 running：
  调 preview_restart。
- preview_restart 默认最多等 240 秒；返回 starting 表示仍在后台启动，用 preview_status / preview_logs
  继续观察，不要连续重启。
- healthy 只表示 HTTP 可达，不表示新改动编译通过；前端编译错误、后端 traceback 看 preview_logs。
- 同一任务内不要反复启动/重启（平台有次数上限）；重启会中断用户正在进行的预览调试，
  运行中的重启需要用户确认。
- 不要尝试停止预览、发布、操作数据库或执行任意命令——你没有这些工具。
</preview>
```

按栈追加一句：

- `vue3-fastapi`：`后端 Python 改动由 uvicorn --reload 自动重载；requirements.txt 改动必须重启。`
- `vue3-springboot`：`后端 Java 改动不会热更新，必须 preview_restart；前端改动仍走 Vite 热更新。`
- `vue3-node` / `static`：`无后端/静态页，刷新即可；依赖清单改动才需要重启。`

提示词要明确「不要每改一行就重启」，否则模型会把可热更新的场景也走成重启。

## 10. 测试计划

后端测试必须**串行**执行（共享 `zerocode_test` 库，并发 TRUNCATE 会死锁）。

### 10.1 新文件 `tests/test_preview_tools.py`

- 应用解析：非应用工作区 → 工具不挂载 / 调用返回明确错误；owner 不匹配 → 查不到；重复 `workspace_id`
  → fail closed（不挂载 + 提示词说明 + warning）；正常命中带 `preview_url`。
- `preview_status`：`stopped` 不探针；`running` + 探针 200 / 403 / 拒连三态；Docker 不可用时仍返回状态；
  有未完成任务时 `deploy_task=true`；应用被删后返回「不可用」。
- `preview_logs`：平台行 + 容器行归并顺序；`tail` 夹取；超总量截断；容器不可用带 `note`；
  凭据行被替换为占位符；ANSI 不出现；只读不改状态。
- `preview_restart`：
  - `stopped` 调 `start_dev`、`running` 调 `restart_dev`（monkeypatch 引擎，断言调用与参数）；
  - 已有 `starting` 任务 → 只观察，不新增任务；
  - 已有非 `starting` 任务 → `ok=false`，不 spawn；
  - 超时返回 `starting` 且任务未被取消；
  - `ctx.cancel` 置位后 1 秒内返回，任务继续；
  - 任务抛 `DeployError` → `ok=false` + `error` + `logs`；
  - 第 4 次调用被上限拒绝；`wait_seconds` 夹取；
  - Docker 预检失败 → `ok=false`，`dev_tasks` 不新增。
- 确认：`running` + 默认配置 → 发送 `confirm_request`（含 `summary`），拒绝不执行；
  `agent.preview_confirm_running=false` → 不确认；`stopped` → 不确认；
  未确认时状态变为 `running` 的竞态兜底（返回「请重试并在确认框中选择确认」，不 spawn）。
- 进度：假 hub 推 `dev_log`，断言只转发 `[zerocode]` 行、`dev_status` 转成一行、批量容器行不转发。

### 10.2 增补既有测试

- `tests/test_agent_loop.py`：应用工作区工具列表含三个预览工具、非应用不含；ctx 字段注入；
  `preview_restart` 确认分支走 `self.confirm`；`tool_progress` 事件顺序在 `tool_result` 之前。
- `tests/test_prompts.py`：`<preview>` 只在 app 存在时出现；三种栈的差异化句子；无 app 时零注入。
- `tests/test_apps_dev_api.py` / `tests/test_publish_api.py`：任务注册表抽取后行为不变
  （202、409、崩溃日志、`dev_tasks` 可 await）。
- `tests/test_dev_logs.py`：共享收集器后响应形状、`tail`、`since_seq`、`note` 不变。

### 10.3 前端

- `frontend/src/stores/chat.test.js`：`tool_progress` 写入与清理；无 `summary` 时 `pendingConfirm`
  形状不变；有 `summary` 时保存。
- 新 `frontend/src/components/PreviewCard.test.js`：各状态渲染、地址链接、日志折叠、`redacted` 提示、
  不出现前端拼接的 `localhost`。
- `frontend/src/components/ChatMessages.test.js`：`preview_*` 路由到 PreviewCard；确认卡标题/按钮按工具切换；
  历史 `tool_result` 无 `args` 也能渲染。
- 红线断言：`PreviewCard.vue` 源码不得出现 `localhost` / `://${` / `dev_port` 的地址拼接形态
  （对生成文本做断言前先收窄判定域，避免命中注释）。

### 10.4 手工矩阵

1. FastAPI 应用：改 Vue 文件不重启，`preview_status` healthy，刷新可见；改 Python 触发 `--reload`。
2. 改 `requirements.txt` 后重启，观察等待与最终状态。
3. Spring 应用：改 Java 后状态仍 running 但行为没变；重启并等待冷启动。
4. 预览 stopped：Agent 启动，应用页日志面板同步看到步骤。
5. 预览 running：Agent 要求重启 → 确认卡出现；拒绝不重启，确认后重启。
6. 长启动中取消聊天：1 秒内停止，预览继续跑完，应用页继续有日志。
7. 两个会话同时操作同一应用：只出现一个任务，另一个观察到同一结果。
8. Docker 关闭：启动返回中文引导，状态工具仍可读。
9. 在应用日志里打印一个假口令：`preview_logs` 显示 `<redacted:...>`。
10. 非应用工作区：对话里没有预览工具，模型说明原因。

**完成标志**：上述后端用例串行全绿、`cd frontend && npm run test -- --run` 全绿、
`npm run build` 成功，手工矩阵走通。

## 11. 分阶段落地

- **阶段 A（只读，先上线）**：`preview_status` + `preview_logs` + 应用解析 + 工具挂载 + `<preview>` 提示词 +
  测试。这一阶段已经能回答「预览活着吗、错在哪」，不产生任何写操作，风险最低。
- **阶段 B（启动/重启）**：`tasks.py` 抽取、`preview_restart`、等待循环、取消/超时、次数上限、
  确认分支、`DeployError` 塑形、测试。
- **阶段 C（前端体验）**：`tool_progress`、`PreviewCard`、确认卡文案、历史回放、前端测试。

## 12. 明确不做

- **不做任意 shell / 命令执行 / 包管理 / 构建 / 测试命令**。需要跑测试是下一阶段的白名单任务，
  另立规格；本期不给模型命令面。
- **不做 `preview_stop`**：停止预览不是验证回路的一环；应用页已有该按钮。
- **不做 prod**：不发布、不回滚、不启停 prod、不读 prod 日志/状态。
- **不做数据库操作**：不执行 SQL、不跑 sqlchange、不建/删库。
- **不做跨应用**：没有 `app_id` 参数，也不能切换应用。
- **不做浏览器/截图/Playwright**：见 §13，这是下一步，不混进本期。
- **不做自动修复循环**：失败后由模型决定下一步，工具不自动重试、不自动回滚代码。
- **不做后台常驻任务/长命令**：除现有部署任务外不新增执行器。
- **不读写 `deploy/**` 与 `app.json`**：平台管理面保持禁区。
- **不改平台权限模型**：不新增权限点、不改网关路由、不改登录态。

## 13. 后续（非本期）

- **浏览器验证（第二步）**：用服务端 headless 浏览器打开预览地址，产出「页面健康摘要」（可见文本、
  控制台错误、失败请求、截图）。它比 shell 更贴近前端问题，且不需要给模型命令面。需要单独解决
  Vite Host 校验（探针/浏览器用裸 IP 或 `host.docker.internal`）、Playwright 镜像体积与下载源、
  以及 in-process 与 sidecar 的网络隔离取舍。本期不实现。
- **白名单任务执行（第三步）**：如果将来确实需要跑 `npm test` / `pytest` / `mvn test`，用独立的
  runner 容器（非 root、cap-drop、不接 `zcnet`、资源限额）+ 固定命令白名单实现，仍不暴露任意 shell。
- **prod 验证**：prod 健康与日志另立规格，与 dev 预览的安全边界不同。

## 14. 补充说明与风险

- **任务竞态**：`spawn` 到 `start_dev` 写 `starting` 之间有极短窗口，此时另一个请求会看到「有任务但状态不是
  starting」，按 §5.5 返回 `ok=false`。这比双起一个栈安全，模型下一次 `preview_status` 就能看到 `starting`。
- **`agent.*` 是平台级键**：普通用户在设置页改 `agent.preview_confirm_running` 会影响所有用户。这是既有取舍，
  本期不为它单开用户级配置。
- **工具描述成本**：三个工具的描述随每次请求发送；不要往 description 里塞教程，教程在 system prompt。
- **`tool_progress` 不持久化**：刷新后只看到最终 `tool_result`；这是有意的，避免把几十行进度写进消息表。
- **日志遮断的残余风险**：应用自定义编码打印的口令无法被规则识别；日志只在排障时尾读，且平台侧
  存储/推送另有 `redact_tool_result` 兜底。
- **`container_logs` 的超时**：REST 保持 60 秒；工具调用传 15 秒，避免模型一次日志读取卡住整轮对话。
- **等待上限与健康上限是两回事**：`preview_restart` 的 240 秒是模型愿意等多久；
  `deploy.health_timeout`（大型栈 900 秒）是引擎愿意等多久。工具返回 `starting` 不代表任务失败。
- **`get_app` 的额外 store 查询**：每次工具调用会多一次 `app_instance_link` 查询；预览工具调用频率低，
  不为此改 `get_app` 的签名。
