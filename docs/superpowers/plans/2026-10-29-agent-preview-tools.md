# Agent 预览验证工具 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 Agent 三个窄预览工具（`preview_status` / `preview_logs` / `preview_restart`），让它能自己回答「改完能不能跑」，而不开放任意命令执行。

**Architecture:** 应用身份由服务端从 `chat_session.workspace_id → app_project` 解析（工具 schema 无 app 参数）；启动/重启复用 `engine.start_dev` / `restart_dev` 与 `app.state.dev_tasks` 单一任务表；进度经 `DeployHub` 订阅后以 `tool_progress` 下发；日志经共享收集器归并、遮断与限长后才进模型上下文；运行中的重启走既有 `confirm_request` 通道，单次 run 有次数上限。

**Tech Stack:** FastAPI + SQLAlchemy async + asyncpg + PostgreSQL；httpx 探针；Vue 3 + Pinia + Ant Design Vue 4.2.6 + Vitest/happy-dom。

**Spec:** `docs/specs/2026-10-29-agent-preview-tools.md`

## Global Constraints

- 后端 pytest **必须串行**：共享 `zerocode_test` 库，并发 TRUNCATE 会死锁；不要加 `-n` / xdist。
- 前端测试 `cd frontend && npm run test -- --run`；构建 `cd frontend && npm run build`。
- **不新增数据库迁移**：四个 `agent.preview_*` 键默认值写在代码里，与 `agent.max_tool_rounds` 同一读取方式。
- **不新增 Python / 前端依赖**。
- 工具 schema 里**永远不出现** `app_id` / `slug` / 路径参数；应用身份只来自服务端解析。
- 预览工具**不读不写** `deploy/**` 与 `app.json`，不调用 `resolve_in_workspace`，不接收任何路径入参。
- 只做 dev：不发布、不回滚、不启停 prod、不读 prod 日志。没有 `preview_stop`，工具永不单独调用 `engine.stop_dev`。
- `app.state.dev_tasks` 的值仍是**裸 `asyncio.Task`**（`tests/test_apps_dev_api.py` 直接 `await dev_tasks[app_id]`）。
- 409 文案逐字保持「该应用有部署任务进行中，请稍候」；崩溃日志逐字保持「部署后台任务异常退出（app_id=%s）」。
- 日志出口：单行 500 字、总量 16KB、最多 300 行，逐行过 `redact_sensitive`，ANSI 转义不出现。
- `agent.*` 是平台级键（`config_service.USER_PREFIXES` 只有 `llm.` / `memory.`），本期不为它单开用户级配置。
- 前端**不拼预览地址**：只用后端回的 `preview_url`；`PreviewCard.vue` 源码不得出现 `localhost` / `://${` / `dev_port` 的地址拼接形态。
- 文档不写行号锚点；提交信息用中文 conventional style，不带 AI 协作者署名。
- Windows 下用 Git Bash；`pytest` 从仓库根执行，前端命令先 `cd frontend`。

---

## 文件地图

**后端新增**

- `app/deploy/tasks.py` — 部署任务注册表（`spawn` / `task_running` / `log_task_crash`）
- `app/deploy/logs.py` — dev 日志收集（`MAX_TAIL` / `iso` / `collect_dev_logs`）
- `app/agent/preview_tools.py` — 三个预览工具的应用解析、handler、等待循环、日志整形、提示词块
- `tests/test_deploy_tasks.py`
- `tests/test_preview_tools.py`
- `frontend/src/components/PreviewCard.vue` + `PreviewCard.test.js`

**后端修改**

- `app/api/apps.py`、`app/api/dev_logs.py`、`app/api/ws.py`
- `app/deploy/engine.py`、`app/services/app_service.py`
- `app/agent/llm.py`、`app/agent/tools.py`、`app/agent/loop.py`、`app/agent/prompts.py`
- `tests/test_dev_logs.py`、`tests/test_prompts.py`、`tests/test_agent_loop.py`

**前端修改**

- `frontend/src/stores/chat.js` + `chat.test.js`
- `frontend/src/components/ChatMessages.vue` + `ChatMessages.test.js`

---

### Task 1: 抽取部署任务注册表 `app/deploy/tasks.py`

REST 与 Agent 必须共用同一张任务表，否则同一应用会被起两个栈。本任务只做**纯抽取**：行为、状态码、日志文案一字不变。

**Files:**
- Create: `app/deploy/tasks.py`
- Modify: `app/api/apps.py`
- Test: `tests/test_deploy_tasks.py`

**Interfaces:**
- Produces: `spawn(tasks: dict[int, asyncio.Task], app_id: int, coro_factory) -> asyncio.Task`（已有未完成任务时抛 `DeployError(..., status=409)`）
- Produces: `task_running(tasks: dict[int, asyncio.Task], app_id: int) -> bool`
- Produces: `log_task_crash(app_id: int, task: asyncio.Task) -> None`
- Consumes: `app.deploy.errors.DeployError`

- [ ] **Step 1: 先写失败测试**

新建 `tests/test_deploy_tasks.py`：

```python
# tests/test_deploy_tasks.py
"""部署任务注册表（app/deploy/tasks.py）：REST 与 Agent 共用同一张表的三条契约。

刻意不碰 FastAPI：这里验的是「同应用单任务 + done 回调留痕」这两个纯行为，
接线层（apps.py / preview_tools.py）各自的用例在自己的文件里。
"""
import asyncio
import logging

import pytest

from app.deploy import tasks as deploy_tasks
from app.deploy.errors import DeployError

pytestmark = pytest.mark.asyncio


async def test_spawn_registers_raw_task_and_runs_it():
    tasks: dict[int, asyncio.Task] = {}
    ran = asyncio.Event()

    async def job():
        ran.set()

    task = deploy_tasks.spawn(tasks, 7, job)
    assert tasks[7] is task                    # 值就是裸 Task（测试与 REST 直接 await 它）
    await asyncio.wait_for(ran.wait(), 1)
    await task


async def test_spawn_rejects_second_task_for_same_app():
    tasks = {}
    gate = asyncio.Event()

    async def job():
        await gate.wait()

    first = deploy_tasks.spawn(tasks, 7, job)
    with pytest.raises(DeployError) as ei:
        deploy_tasks.spawn(tasks, 7, job)
    assert ei.value.status == 409
    assert str(ei.value) == "该应用有部署任务进行中，请稍候"
    gate.set()
    await first


async def test_spawn_replaces_finished_task():
    tasks = {}

    async def job():
        return None

    await deploy_tasks.spawn(tasks, 7, job)
    assert not deploy_tasks.task_running(tasks, 7)
    second = deploy_tasks.spawn(tasks, 7, job)   # 上一个已完成 → 可再起
    await second


async def test_task_running_is_false_for_unknown_app():
    assert deploy_tasks.task_running({}, 99) is False


async def test_log_task_crash_records_exception(caplog):
    async def boom():
        raise RuntimeError("炸了")

    task = asyncio.create_task(boom())
    with pytest.raises(RuntimeError):
        await task
    with caplog.at_level(logging.ERROR, logger="app.deploy.tasks"):
        deploy_tasks.log_task_crash(7, task)
    assert "部署后台任务异常退出（app_id=7）" in caplog.text


async def test_log_task_crash_ignores_cancelled_and_clean_tasks():
    async def slow():
        await asyncio.sleep(5)

    cancelled = asyncio.create_task(slow())
    cancelled.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled
    clean = asyncio.create_task(asyncio.sleep(0))
    await clean
    deploy_tasks.log_task_crash(1, cancelled)     # 不抛、不记
    deploy_tasks.log_task_crash(2, clean)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_deploy_tasks.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'app.deploy.tasks'`

- [ ] **Step 3: 写实现**

新建 `app/deploy/tasks.py`：

```python
# app/deploy/tasks.py
"""部署任务注册表：`app.state.dev_tasks`（app_id → 裸 asyncio.Task）的唯一操作面。

REST（apps.py）与 Agent 预览工具（agent/preview_tools.py）共用同一张表：同一应用同时只允许
一个部署任务，否则「用户手点启动」与「Agent 重启预览」会各起一个栈，互踩端口与 compose 项目名。
值是裸 `asyncio.Task` 而不是包装对象——既有测试与 REST 都直接 `await dev_tasks[app_id]`。
"""
import asyncio
import logging
from functools import partial

from app.deploy.errors import DeployError

logger = logging.getLogger(__name__)

BUSY_MESSAGE = "该应用有部署任务进行中，请稍候"


def task_running(tasks: dict[int, asyncio.Task], app_id: int) -> bool:
    """该应用是否有未完成的部署任务（不在表里 / 已完成都算没有）。"""
    task = (tasks or {}).get(app_id)
    return bool(task) and not task.done()


def log_task_crash(app_id: int, task: asyncio.Task) -> None:
    """done 回调：只记真抛了异常的任务，取消与正常返回（exception() is None）一律不动。

    先 `task.cancelled()` 再取 `exception()`：对已取消的任务调 `exception()` 会抛 CancelledError，
    在回调里再抛一次只会给日志添乱。
    """
    if task.cancelled():
        return
    exc = task.exception()
    if exc is None:
        return
    logger.exception("部署后台任务异常退出（app_id=%s）", app_id, exc_info=exc)


def spawn(tasks: dict[int, asyncio.Task], app_id: int, coro_factory) -> asyncio.Task:
    """登记并启动一个部署任务；已有未完成任务时抛 409（不排队、不双跑）。

    202 之后任务异常没有 HTTP 出口，done 回调负责 retrieve + 记堆栈：既消掉 asyncio 的
    「Task exception was never retrieved」噪音，也给「引擎事件之后才失败」的路子留下一条可读现场。
    """
    if task_running(tasks, app_id):
        raise DeployError(BUSY_MESSAGE, status=409)
    task = asyncio.create_task(coro_factory())
    task.add_done_callback(partial(log_task_crash, app_id))
    tasks[app_id] = task
    return task
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_deploy_tasks.py -v`
Expected: PASS（6 passed）

- [ ] **Step 5: `apps.py` 改为委托**

`app/api/apps.py` 顶部导入区加一行（与既有 `from app.deploy import ...` 并列）：

```python
from app.deploy import tasks as deploy_tasks
```

删掉 `_log_task_crash` 整个函数（含 docstring），把 `_spawn` 整体替换为：

```python
def _spawn(request: Request, app_id: int, coro_factory) -> None:
    """长任务句柄表：同一应用同时只允许一个部署任务（重复受理 → 409，而不是排队双跑）。

    实现与 Agent 预览工具共用 `app.deploy.tasks`（同应用单栈的判据只有一份）；
    本函数只负责把 `request.app.state.dev_tasks` 递过去，行为与状态码不变。
    """
    deploy_tasks.spawn(request.app.state.dev_tasks, app_id, coro_factory)
```

把 `_reject_if_task_running` 的函数体换成：

```python
def _reject_if_task_running(request: Request, app_id: int) -> None:
    """与 `_spawn` 同一条「同应用同时只允许一个部署任务」的契约，用在删除入口上。

    删记录时后台起栈/迁移任务还在跑，会留下一只没人认领的容器（行没了，状态回执无处可写），
    所以直接 409 让用户等一等，而不是在这里发明一套取消协议。
    """
    if deploy_tasks.task_running(request.app.state.dev_tasks, app_id):
        raise DeployError(deploy_tasks.BUSY_MESSAGE, status=409)
```

若 `functools.partial` 与 `asyncio` 在 `apps.py` 里已无其它用处，一并删掉对应 import（`asyncio` 仍被类型注解 `asyncio.Task | None` 用到时保留；`grep -n "asyncio\.\|partial(" app/api/apps.py` 确认后再删）。

- [ ] **Step 6: 跑既有测试确认零回归**

Run: `pytest tests/test_apps_dev_api.py tests/test_publish_api.py -v`
Expected: PASS —— 202 / 409 / 崩溃日志 / `dev_tasks` 可 await 四条契约都不变

- [ ] **Step 7: 提交**

```bash
git add app/deploy/tasks.py app/api/apps.py tests/test_deploy_tasks.py
git commit -m "refactor(deploy): 抽取部署任务注册表供 REST 与 Agent 共用"
```

---

### Task 2: 抽取 dev 日志收集器 `app/deploy/logs.py`

预览日志工具与应用页日志面板必须读同一份归并结果，否则两路会漂移。同样是**纯抽取**：响应形状、`MAX_TAIL`、`since_seq` 语义不变。

**Files:**
- Create: `app/deploy/logs.py`
- Modify: `app/api/dev_logs.py`
- Test: `tests/test_dev_logs.py`（追加用例）

**Interfaces:**
- Produces: `MAX_TAIL = 1000`
- Produces: `iso(epoch) -> str`（本地时区、秒精度，解析不了回空串）
- Produces: `collect_dev_logs(session_factory, app: dict, hub, *, tail: int = 200, since_seq: int = 0) -> dict`，返回 `{"lines": list[dict], "note": str, "status_dev": str | None, "url": str, "last_seq": int}`
- Consumes: `app.deploy.engine.container_logs` / `engine.merge_logs`、`hub.tail_logs`

- [ ] **Step 1: 先写失败测试**

在 `tests/test_dev_logs.py` 末尾追加（文件已有 `sf` / `dev_app` / `_epoch` / `monkeypatch` 替身约定）：

```python
# ---------- 共享收集器（app/deploy/logs.py）：REST 与 Agent 工具读同一份归并结果 ----------

async def test_collect_dev_logs_returns_endpoint_shape(sf, dev_app, monkeypatch):
    from app.deploy import logs as logs_mod
    from app.deploy.hub import DeployHub
    from app.services import app_service

    hub = DeployHub()
    hub.publish(dev_app, {"app_id": dev_app, "type": "dev_log", "line": "[zerocode] ✓ 预览已就绪"})

    async def fake_container(session_factory, app, tail=200, timeout=60):
        assert tail == 5
        return [{"source": "container", "service": "backend", "ts": "2026-09-20T14:27:58+00:00",
                 "epoch": _epoch("2026-09-20T14:27:58+00:00"), "line": "Uvicorn running"}], ""

    monkeypatch.setattr(logs_mod.engine, "container_logs", fake_container)
    # 传真实应用行（不是手搓 dict）：status_dev / preview_url 是收集器从行上读的，
    # 两个出口读的是同一份后端投影——手搓字典会让这两条断言无值可读
    body = await logs_mod.collect_dev_logs(sf, await app_service.get_app(sf, dev_app), hub, tail=5)
    assert [l["source"] for l in body["lines"]] == ["container", "platform"]
    assert body["note"] == "" and body["status_dev"] == "stopped"
    assert body["url"] == f"http://localhost:{body['url'].rsplit(':', 1)[1]}"
    assert body["last_seq"] == body["lines"][1]["seq"]


async def test_collect_dev_logs_since_seq_and_empty_ring(sf, dev_app, monkeypatch):
    from app.deploy import logs as logs_mod
    from app.deploy.hub import DeployHub

    hub = DeployHub()
    for i in range(3):
        hub.publish(dev_app, {"app_id": dev_app, "type": "dev_log", "line": f"l{i}"})

    async def no_container(session_factory, app, tail=200, timeout=60):
        return [], ""

    monkeypatch.setattr(logs_mod.engine, "container_logs", no_container)
    body = await logs_mod.collect_dev_logs(sf, {"id": dev_app, "slug": SLUG}, hub, since_seq=1)
    assert [l["line"] for l in body["lines"]] == ["l1", "l2"]
    empty = await logs_mod.collect_dev_logs(sf, {"id": dev_app, "slug": SLUG}, DeployHub())
    assert empty["lines"] == [] and empty["last_seq"] == 0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_dev_logs.py -k collect_dev_logs -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'app.deploy.logs'`

- [ ] **Step 3: 写实现**

新建 `app/deploy/logs.py`：

```python
# app/deploy/logs.py
"""dev 预览日志的两路归并：平台步骤行（DeployHub 环形缓冲）+ 容器输出（compose logs）。

应用页「查看日志」面板（api/dev_logs.py）与 Agent 的 `preview_logs` 工具读的是**同一份**结果：
两路各自维护一份归并逻辑早晚漂移，而「Agent 看到的日志」与「用户看到的日志」不一致会让排障
变成两个人各说各话。本模块只做收集与归并，不做限长/遮断/整形——那是各出口的口径
（面板要 1000 行原文，工具要 300 行遮断后的短行）。
"""
from datetime import datetime, timezone

from app.deploy import engine

MAX_TAIL = 1000        # 日志面板要的是「最近发生了什么」，不是全量转储


def iso(epoch) -> str:
    """环形缓冲存的是 epoch 秒；给人看的时间转成本地时区 + 秒精度，解析不了就留空。"""
    try:
        return datetime.fromtimestamp(float(epoch), timezone.utc).astimezone().isoformat(
            timespec="seconds")
    except (TypeError, ValueError, OSError):
        return ""


async def collect_dev_logs(session_factory, app: dict, hub, *,
                           tail: int = 200, since_seq: int = 0) -> dict:
    """取某应用最近的 dev 日志（平台步骤行 + 容器输出，按时间归并）。

    `tail` 由调用方自行夹取（面板 1..1000，Agent 工具 10..300）；`since_seq` 只要平台侧
    seq 大于它的行。容器侧拿不到（Docker 没开 / 栈没起）**不报错**：note 讲明原因，
    步骤行照样返回——那正是应用起不来时唯一看得见的线索。
    """
    n = max(int(tail), 1)
    since = max(int(since_seq), 0)
    platform = [{"seq": r["seq"], "ts": iso(r["epoch"]), "epoch": r["epoch"],
                 "source": "platform", "service": "平台", "line": r["line"]}
                for r in hub.tail_logs(app["id"], limit=n, since_seq=since)]
    container, note = await engine.container_logs(session_factory, app, n)
    lines = engine.merge_logs(platform, container)
    return {"lines": lines, "note": note, "status_dev": app.get("status_dev"),
            "url": app.get("preview_url") or "",
            "last_seq": platform[-1]["seq"] if platform else since}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_dev_logs.py -k collect_dev_logs -v`
Expected: PASS（2 passed）

- [ ] **Step 5: `dev_logs.py` 改为委托**

把 `app/api/dev_logs.py` 整体替换为：

```python
# app/api/dev_logs.py
"""预览日志 REST：`GET /api/apps/{app_id}/dev/logs`。

一次请求拼两路来源（平台步骤行 + dev 栈容器输出），归并逻辑在 `app/deploy/logs.py`——
Agent 的 `preview_logs` 工具读的是同一个收集器，两路口径不会漂移。
实时增量继续走 `/ws/deploy/{app_id}` 的 `dev_log` 事件（带 `seq`，前端据此与 REST 历史去重）。

刻意单独成模块而不是塞进 app/api/apps.py：前缀同为 /api/apps，路由互不干涉，
而 apps.py 同一时间有另一路改动在改（避免把别人的半成品卷进本次提交）。
"""
from fastapi import APIRouter, HTTPException, Request

from app.deploy.errors import DeployError
from app.deploy.logs import MAX_TAIL, collect_dev_logs
from app.services import app_service

router = APIRouter(prefix="/api/apps")

__all__ = ["router", "MAX_TAIL"]     # MAX_TAIL 是既有测试的导入锚（from app.api.dev_logs import MAX_TAIL）


@router.get("/{app_id}/dev/logs")
async def dev_logs(request: Request, app_id: int, tail: int = 200, since_seq: int = 0):
    """预览日志（平台步骤行 + 容器输出，按时间归并）。

    `tail` 夹在 [1, 1000]：手改 URL 传个 999999 不该把平台内存里的历史连锅端走。
    `since_seq` 只要平台侧 seq 大于它的行——不开 WS 时前端可用它做增量轮询。
    """
    sf = request.app.state.session_factory
    hub = request.app.state.deploy_hub
    try:
        app = await app_service.get_app(sf, app_id, owner_user_id=request.state.actor["id"])
    except DeployError as e:
        raise HTTPException(e.status, str(e))
    n = min(max(int(tail), 1), MAX_TAIL)
    return await collect_dev_logs(sf, app, hub, tail=n, since_seq=max(int(since_seq), 0))
```

- [ ] **Step 6: 跑既有测试确认零回归**

Run: `pytest tests/test_dev_logs.py -v`
Expected: PASS —— 端点响应形状、`tail` 夹取、`since_seq`、`note`、404 全部不变

- [ ] **Step 7: 提交**

```bash
git add app/deploy/logs.py app/api/dev_logs.py tests/test_dev_logs.py
git commit -m "refactor(deploy): 抽取 dev 日志收集器供面板与 Agent 工具共用"
```

---

### Task 3: 引擎侧探针与日志收口（`strip_ansi` / `_dev_probe_paths` / `probe_dev` / `container_logs(timeout=)`）

`preview_status` 不能调 `engine.dev_status`（那会起一个 `docker compose ps` 子进程），它要的是一次短探针。健康路径只能有一份真相，所以把「探哪些路径」抽成 `_dev_probe_paths`，`start_dev` 与新的 `probe_dev` 共用。

**Files:**
- Modify: `app/deploy/engine.py`
- Test: `tests/test_engine.py`（追加用例）

**Interfaces:**
- Produces: `strip_ansi(text: str) -> str`
- Produces: `_dev_probe_paths(stack: str) -> tuple[str, ...]`（`vue3-springboot` → `("/", "/api/health")`，其余 → `("/",)`）
- Produces: `probe_dev(app: dict, timeout: int = 2) -> tuple[bool, str]`
- Produces: `container_logs(sf, app: dict, tail: int = 200, timeout: int = 60) -> tuple[list[dict], str]`
- Consumes: 既有 `_all_200(port, paths, timeout, host=None)`、`_dev_port(app)`、`JOINT_HEALTH_STACKS`、`app_service.HEALTH_BY_STACK`

- [ ] **Step 1: 先写失败测试**

在 `tests/test_engine.py` 末尾追加（文件已有 `_client_returning` / `seams` / `make_app` / `sf` 约定）：

```python
# ---------- 预览工具侧的引擎收口（规格 2026-10-29-agent-preview-tools §5.4） ----------

def test_dev_probe_paths_is_the_single_truth_for_health_routes():
    """健康路径只有一份真相：start_dev 与 probe_dev 都从这里取，不许各写一遍。"""
    assert engine._dev_probe_paths("vue3-springboot") == ("/", "/api/health")
    assert engine._dev_probe_paths("vue3-fastapi") == ("/",)     # 小栈刻意不探后端（见 JOINT_HEALTH_STACKS 注释）
    assert engine._dev_probe_paths("static") == ("/",)
    assert engine._dev_probe_paths("") == ("/",)


async def test_probe_dev_reports_reachable_and_verdict(monkeypatch):
    FakeClient, seen = _client_returning(lambda url: 200)
    monkeypatch.setattr(engine.httpx, "AsyncClient", FakeClient)
    ok, verdict = await engine.probe_dev({"id": 1, "stack": "vue3-fastapi", "dev_port": 8231})
    assert ok is True and verdict == ""
    assert seen == ["http://127.0.0.1:8231/"]


async def test_probe_dev_verdict_carries_non_200(monkeypatch):
    FakeClient, _ = _client_returning(lambda url: 403)
    monkeypatch.setattr(engine.httpx, "AsyncClient", FakeClient)
    ok, verdict = await engine.probe_dev({"id": 1, "stack": "vue3-fastapi", "dev_port": 8231}, timeout=1)
    assert ok is False and "403" in verdict


async def test_probe_dev_probes_both_paths_for_large_stack(monkeypatch):
    FakeClient, seen = _client_returning(lambda url: 200)
    monkeypatch.setattr(engine.httpx, "AsyncClient", FakeClient)
    ok, _ = await engine.probe_dev({"id": 1, "stack": "vue3-springboot", "dev_port": 8233})
    assert ok is True
    assert any(u.endswith("8233/") for u in seen) and any(u.endswith("/api/health") for u in seen)


async def test_probe_dev_without_dev_port_does_not_hit_network(monkeypatch):
    """没有 dev 端口的应用不该发出任何 HTTP 请求（也不该抛），只回一句能转述给用户的说明。"""
    instantiated = []

    class Boom:
        def __init__(self, **kw):
            instantiated.append(kw)

    monkeypatch.setattr(engine.httpx, "AsyncClient", Boom)
    ok, verdict = await engine.probe_dev({"id": 1, "stack": "vue3-fastapi", "dev_port": None})
    assert ok is False and verdict == "应用没有 dev 端口"
    assert instantiated == []


def test_strip_ansi_removes_subprocess_escape_sequences():
    assert engine.strip_ansi("\x1b[33mVITE ready\x1b[39m") == "VITE ready"
    assert engine.strip_ansi("plain") == "plain"


async def test_container_logs_passes_timeout_through(monkeypatch, sf, make_app):
    """工具侧传 15s：读日志不许把整轮对话吊死；REST 仍是 60s 默认。"""
    app = await make_app("zcto", prod_port=8234, dev_port=8235)
    runner = FakeRunner({"compose logs": (0, "2026-09-20T14:27:58Z backend | hi")})
    monkeypatch.setattr(engine, "docker", runner)
    rows, note = await engine.container_logs(sf, app, 10, timeout=15)
    assert runner.calls[0]["timeout"] == 15 and note == "" and rows[0]["line"] == "hi"
    await engine.container_logs(sf, app, 10)
    assert runner.calls[1]["timeout"] == 60
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_engine.py -k "probe_dev or dev_probe_paths or strip_ansi or passes_timeout" -v`
Expected: FAIL —— `AttributeError: module 'app.deploy.engine' has no attribute 'probe_dev'`（其余同理）

- [ ] **Step 3: 写实现**

`app/deploy/engine.py` 三处改动：

① 在 `_ANSI_RE` 定义之后加公开助手，并让 `parse_compose_logs` 用它：

```python
def strip_ansi(text: str) -> str:
    """剥掉子进程自己发的 ANSI 转义序列（`--no-color` 只管 compose 自己的前缀着色）。

    容器输出与平台步骤行都可能带（sqlrunner 的输出经 on_log 原样进环形缓冲），
    面板是 `<pre>`、模型上下文是纯文本，留着就是 `[33m` 这类乱码。
    """
    return _ANSI_RE.sub("", text)
```

`parse_compose_logs` 循环内那行 `raw = _ANSI_RE.sub("", raw)` 改为 `raw = strip_ansi(raw)`（注释保留）。

② 在 `wait_dev_health` 之后加探针路径的唯一真相与单轮探针：

```python
def _dev_probe_paths(stack: str) -> tuple[str, ...]:
    """dev 健康探针要打的路径：前端 `/` 必有，联合健康栈再加后端健康路径。

    唯一真相：`start_dev` 的就绪等待与 `probe_dev` 的单轮探针都从这里取，
    健康路径不许出现第二份（漂移的后果是「启动说好了、状态说没好」）。
    """
    if stack in JOINT_HEALTH_STACKS:
        # deploy → services 就地取用是 prod.py 已有的惯例，提到模块顶会变成循环导入
        from app.services.app_service import HEALTH_BY_STACK
        backend = HEALTH_BY_STACK.get(stack) or ""
        if backend:
            return ("/", backend)
    return ("/",)


async def probe_dev(app: dict, timeout: int = 2) -> tuple[bool, str]:
    """单轮预览探针：只回答「HTTP 可达吗」，不轮询到就绪、不写状态、不发事件。

    与 `wait_*_health` 的分工：那两条是启动流程里等到就绪为止（最长 240/900s），
    这条是给 `preview_status` 用的廉价体检——它可能被高频调用，不能起子进程也不能长等。
    返回 `(是否可达, 最后一轮判定)`，判定串形态与 `_all_200` 一致（`"/ → 403"` / `ConnectError: …`）。
    """
    port = _dev_port(app)
    if not port:
        return False, "应用没有 dev 端口"
    return await _all_200(port, _dev_probe_paths(app.get("stack") or ""), max(int(timeout), 1))
```

③ `start_dev` 里算 `backend_path` 的那三行改为共用 `_dev_probe_paths`：

```python
        cfg = await deploy_cfg(sf)
        stack = app.get("stack") or ""
        timeout = health_timeout(cfg, stack)
        # 健康路径不抄第三份：与 probe_dev 共用 _dev_probe_paths（其内部取 HEALTH_BY_STACK）
        paths = _dev_probe_paths(stack)
        backend_path = paths[1] if len(paths) > 1 else ""
        probe = preview_probe_host()
```

④ `container_logs` 增加 `timeout` 形参并透传：

```python
async def container_logs(sf, app: dict, tail: int = 200, timeout: int = 60) -> tuple[list[dict], str]:
```

函数体内 `docker(...)` 调用的 `timeout=60` 改为 `timeout=timeout`。docstring 补一句：

```
    `timeout` 默认 60s（REST 面板）；Agent 的 preview_logs 传 15s，避免一次日志读取卡住整轮对话。
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_engine.py -v`
Expected: PASS —— 新用例全绿，且既有 `wait_dev_health` / `start_dev` / `container_logs` 用例零回归

- [ ] **Step 5: 跑日志链路测试确认零回归**

Run: `pytest tests/test_dev_logs.py tests/test_deploy_tasks.py -v`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add app/deploy/engine.py tests/test_engine.py
git commit -m "feat(deploy): 抽出 dev 探针路径与单轮 probe_dev，容器日志超时可配"
```

---

### Task 4: 应用解析 `app_service.get_app_by_workspace`

工具 schema 里没有 app 参数，应用身份只能由服务端从会话工作区反查。这是整个特性的安全地基：查不到就不挂载工具，查到多行就 fail closed。

**Files:**
- Modify: `app/services/app_service.py`
- Test: `tests/test_preview_tools.py`（新建）

**Interfaces:**
- Produces: `get_app_by_workspace(session_factory, workspace_id: int, *, owner_user_id: int | None = None) -> dict | None`
  - 0 行 → `None`
  - 1 行 → 与 `get_app` 同形状（含 `preview_url` / `prod_url` / `store`）
  - 多行 → 抛 `DeployError("当前工作区绑定了多个应用，无法确定预览目标", status=409)`
  - 行在两查之间被删 → `get_app` 的 `DeployError(status=404)` 原样上抛（调用方必须与 409 分岔）
- Consumes: 同模块既有的 `get_app`（委托取整行；列清单与 store 子查询只此一份）

- [ ] **Step 1: 先写失败测试**

新建 `tests/test_preview_tools.py`：

```python
# tests/test_preview_tools.py
"""Agent 预览工具（规格 docs/specs/2026-10-29-agent-preview-tools.md）。

Docker 侧一律替身：本文件验「工具怎么解析应用、怎么整形结果、怎么等待与收口」，
真起栈由 tests/test_engine.py 与手工矩阵负责。
"""
import asyncio

import pytest
from sqlalchemy import text

from app.db import make_session_factory
from app.deploy.errors import DeployError
from app.services import app_service

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def sf(test_engine):
    return make_session_factory(test_engine)


async def _mk_workspace(sf, root: str, uid: int | None = None) -> int:
    async with sf() as s:
        wid = (await s.execute(text(
            "INSERT INTO workspace (name, root_path, owner_user_id) VALUES ('pv', :p, :o) RETURNING id"),
            {"p": root, "o": uid})).scalar()
        await s.commit()
    return wid


async def _mk_app(sf, wid: int, slug: str, *, stack="vue3-fastapi", uid: int | None = None,
                  dev_port: int | None = 8241, prod_port: int | None = 8240) -> int:
    async with sf() as s:
        aid = (await s.execute(text(
            "INSERT INTO app_project (workspace_id, name, slug, scale, stack, prod_port, dev_port, "
            "owner_user_id) VALUES (:w, '预览应用', :sl, 'small', :st, :pp, :dp, :o) RETURNING id"),
            {"w": wid, "sl": slug, "st": stack, "pp": prod_port, "dp": dev_port, "o": uid})).scalar()
        await s.commit()
    return aid


# ---------- 应用解析（唯一身份来源，§5.1） ----------

async def test_get_app_by_workspace_hits_single_row(sf, tmp_path):
    wid = await _mk_workspace(sf, str(tmp_path / "a"))
    aid = await _mk_app(sf, wid, "pvone")
    app = await app_service.get_app_by_workspace(sf, wid)
    assert app["id"] == aid and app["slug"] == "pvone"
    assert app["preview_url"].endswith(":8241")       # 与 get_app 同形状，地址只来自后端
    assert app["status_dev"] == "stopped"


async def test_get_app_by_workspace_returns_none_without_app(sf, tmp_path):
    wid = await _mk_workspace(sf, str(tmp_path / "b"))
    assert await app_service.get_app_by_workspace(sf, wid) is None


async def test_get_app_by_workspace_filters_by_owner(sf, tmp_path):
    """owner 过滤与 list_apps 同一条判据：别人的应用等于不存在（会话已过 WS 校验，这是第二道闸）。"""
    wid = await _mk_workspace(sf, str(tmp_path / "c"))
    await _mk_app(sf, wid, "pvother", uid=4242)
    assert await app_service.get_app_by_workspace(sf, wid, owner_user_id=9999) is None
    assert (await app_service.get_app_by_workspace(sf, wid, owner_user_id=4242))["slug"] == "pvother"


async def test_get_app_by_workspace_fails_closed_on_duplicate(sf, tmp_path):
    """workspace_id 没有 UNIQUE 约束：查到两行时不猜哪一行，fail closed 让调用方不挂载工具。"""
    wid = await _mk_workspace(sf, str(tmp_path / "d"))
    await _mk_app(sf, wid, "pvdup1", dev_port=8243, prod_port=8242)
    await _mk_app(sf, wid, "pvdup2", dev_port=8245, prod_port=8244)
    with pytest.raises(DeployError) as ei:
        await app_service.get_app_by_workspace(sf, wid)
    assert ei.value.status == 409 and "多个应用" in str(ei.value)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_preview_tools.py -v`
Expected: FAIL —— `AttributeError: module 'app.services.app_service' has no attribute 'get_app_by_workspace'`

- [ ] **Step 3: 写实现**

在 `app/services/app_service.py` 的 `get_app` 之后插入（**只查 id、再委托 `get_app` 取整行**：
两处各自维护一份列清单与 store 子查询早晚漂移，而规格要求两者可互换使用，漂移的后果是下游拿到
静默的 None。刻意不抽公共助手去改 `get_app` 本体——它是 REST 详情/改名/删除/dev 端点/商店
多路共用的热点，§14 已裁定不为预览工具改它）：

```python
async def get_app_by_workspace(session_factory, workspace_id: int, *,
                               owner_user_id: int | None = None) -> dict | None:
    """按工作区反查唯一应用行（Agent 预览工具的身份来源，规格 2026-10-29 §5.1）。

    与 `get_app` 同形状——因为它就是**委托** `get_app` 取的行：两处各自维护一份列清单与
    store 子查询早晚漂移，而漂移的后果是下游拿到静默的 None（规格要求两者可互换使用）。

    - 0 行 → None：不是应用工作区，预览工具不挂载；
    - 多行 → 抛 409：`app_project.workspace_id` 没有 UNIQUE 约束，历史上可能手工插入重复行，
      这时不猜、也不挂载工具（fail closed），由调用方记 warning 并向模型说明原因；
    - 行在两查之间被删 → `get_app` 的 404 原样上抛：调用方要把它与 409 的「多个应用」
      分开处理（前者是「应用不可用」，后者是「无法确定预览目标」）；
    - `owner_user_id` 过滤与 `list_apps` 同一条判据：会话已过 WS 所有权校验，这里是第二道闸；
      存量 NULL 归属的应用在应用列表里同样不可见，不制造新的不一致。
    """
    async with session_factory() as s:
        sql = "SELECT id FROM app_project WHERE workspace_id = :w"
        params: dict = {"w": workspace_id}
        if owner_user_id is not None:
            sql += " AND owner_user_id = :u"
            params["u"] = owner_user_id
        ids = [r[0] for r in (await s.execute(text(sql + " ORDER BY id LIMIT 2"), params)).all()]
    if not ids:
        return None
    if len(ids) > 1:
        raise DeployError("当前工作区绑定了多个应用，无法确定预览目标", status=409)
    return await get_app(session_factory, ids[0])
```

（委托时刻意**不传** `owner_user_id`：归属过滤已经在第一条查询里做过，再传一次只是多一处
可能不一致的判据。）

`test_get_app_by_workspace_hits_single_row` 里补一条断言，把「可互换」钉成可证伪的：

```python
    assert set(app) == set(await app_service.get_app(sf, aid))   # 可互换使用是规格的硬要求
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_preview_tools.py -v`
Expected: PASS（4 passed）

- [ ] **Step 5: 跑应用服务既有测试确认零回归**

Run: `pytest tests/test_app_service.py tests/test_apps_api.py tests/test_apps_dev_api.py -v`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add app/services/app_service.py tests/test_preview_tools.py
git commit -m "feat(apps): 按工作区反查唯一应用行供预览工具解析身份"
```

---

### Task 5: 工具 schema 与系统提示词的 `preview` 注入口

工具描述随每次请求发送，保持一句话；教程放 system prompt 的 `<preview>` 块，且只在应用解析成功时注入。

**Files:**
- Modify: `app/agent/llm.py`
- Modify: `app/agent/prompts.py`
- Test: `tests/test_preview_tools.py`（追加）、`tests/test_prompts.py`（追加）

**Interfaces:**
- Produces: `PREVIEW_TOOLS: list[dict]`（三条 OpenAI function schema）
- Produces: `PREVIEW_TOOL_NAMES: tuple[str, str, str] = ("preview_status", "preview_logs", "preview_restart")`
- Produces: `build_system_prompt(root, date, app_contract=None, skills=None, preview=None) -> str`
- Consumes: 既有 `TOOLS`（一字不改）

- [ ] **Step 1: 先写失败测试（schema 形状）**

在 `tests/test_preview_tools.py` 末尾追加：

```python
# ---------- 工具 schema（§4.1）：三个工具，且没有应用身份参数 ----------

def test_preview_tool_schemas_have_no_app_identity_params():
    """红线：schema 里出现 app_id / slug / path 就等于把跨应用入口交给模型。"""
    from app.agent.llm import PREVIEW_TOOLS, PREVIEW_TOOL_NAMES
    assert [t["function"]["name"] for t in PREVIEW_TOOLS] == list(PREVIEW_TOOL_NAMES)
    assert PREVIEW_TOOL_NAMES == ("preview_status", "preview_logs", "preview_restart")
    for t in PREVIEW_TOOLS:
        fn = t["function"]
        assert t["type"] == "function" and fn["description"]
        props = fn["parameters"]["properties"]
        assert not ({"app_id", "slug", "path", "workspace_id"} & set(props))
        assert fn["parameters"]["required"] == []


def test_preview_tool_params_are_only_tail_and_wait_seconds():
    from app.agent.llm import PREVIEW_TOOLS
    by_name = {t["function"]["name"]: t["function"]["parameters"]["properties"] for t in PREVIEW_TOOLS}
    assert by_name["preview_status"] == {}
    assert set(by_name["preview_logs"]) == {"tail"}
    assert set(by_name["preview_restart"]) == {"wait_seconds"}


def test_base_tools_unchanged_by_preview_feature():
    from app.agent.llm import TOOLS
    assert [t["function"]["name"] for t in TOOLS] == [
        "list_dir", "read_file", "write_file", "edit_file", "ask_user", "todo_write", "memory_write"]
```

- [ ] **Step 2: 先写失败测试（提示词注入口）**

在 `tests/test_prompts.py` 末尾追加：

```python
# ---- 预览工具指引 <preview>（规格 2026-10-29-agent-preview-tools §9）----

def test_preview_block_appended_after_app_contract():
    p = build_system_prompt("/ws", "2026-10-29", app_contract="\n\n【应用契约】slug=aaa\n",
                            preview="\n\n<preview>\n改完要验证\n</preview>")
    assert "<preview>" in p and "改完要验证" in p
    assert p.index("【应用契约】") < p.index("<preview>")     # 契约在前，预览指引在后


def test_no_preview_block_by_default():
    assert "<preview>" not in build_system_prompt("/ws", "2026-10-29")
    assert "<preview>" not in build_system_prompt("/ws", "2026-10-29", app_contract="【应用契约】")
```

- [ ] **Step 3: 跑测试确认失败**

Run: `pytest tests/test_preview_tools.py -k schema tests/test_prompts.py -k preview -v`
Expected: FAIL —— `ImportError: cannot import name 'PREVIEW_TOOLS'`；`TypeError: build_system_prompt() got an unexpected keyword argument 'preview'`

- [ ] **Step 4: 写实现（schema）**

在 `app/agent/llm.py` 的 `TOOLS = [...]` 之后、`_reasoning_of` 之前插入：

```python
# 预览工具（规格 docs/specs/2026-10-29-agent-preview-tools.md）：只在会话工作区解析出应用时挂载。
# 刻意没有 app_id / slug / path 参数——应用身份由服务端从 chat_session.workspace_id 反查，
# 模型无从指向别的应用或别人的应用。描述保持一句话：这三条随每次请求发送，教程在 system prompt。
PREVIEW_TOOLS = [
    {"type": "function", "function": {
        "name": "preview_status",
        "description": "查看当前应用 dev 预览的状态与可用性（只读）",
        "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {
        "name": "preview_logs",
        "description": "读取当前应用 dev 预览的最近日志（平台步骤行 + 容器输出，只读）",
        "parameters": {"type": "object", "properties": {
            "tail": {"type": "integer", "description": "最近多少行，默认 80，最大 300"}},
            "required": []}}},
    {"type": "function", "function": {
        "name": "preview_restart",
        "description": ("启动或重启当前应用的 dev 预览，等待就绪后返回最终状态与日志。"
                        "改了依赖清单/配置、或预览未运行时使用；纯代码改动通常会自动热更新，"
                        "先看 preview_status/preview_logs"),
        "parameters": {"type": "object", "properties": {
            "wait_seconds": {"type": "integer",
                             "description": "最多等待多少秒，默认 240；超时返回 starting，预览仍在后台继续"}},
            "required": []}}},
]

PREVIEW_TOOL_NAMES = ("preview_status", "preview_logs", "preview_restart")
```

- [ ] **Step 5: 写实现（提示词参数）**

`app/agent/prompts.py` 的签名与收尾改为：

```python
def build_system_prompt(root: str, date: str, app_contract: str | None = None,
                       skills: list[dict] | None = None, preview: str | None = None) -> str:
```

函数末尾（`if app_contract:` 之后）追加：

```python
    if preview:
        text += preview
    return text
```

- [ ] **Step 6: 跑测试确认通过**

Run: `pytest tests/test_preview_tools.py tests/test_prompts.py -v`
Expected: PASS

- [ ] **Step 7: 提交**

```bash
git add app/agent/llm.py app/agent/prompts.py tests/test_preview_tools.py tests/test_prompts.py
git commit -m "feat(agent): 新增三个预览工具 schema 与 preview 提示词注入口"
```

---

### Task 6: `preview_tools.py` 只读内核（应用解析 + 日志整形 + `preview_status` / `preview_logs`）

阶段 A 的全部内容：不产生任何写操作，已经能回答「预览活着吗、错在哪」。`preview_restart` 分支在 Task 7 接入。

**Files:**
- Create: `app/agent/preview_tools.py`
- Modify: `app/deploy/logs.py`（`collect_dev_logs` 增加 `container_timeout`）
- Test: `tests/test_preview_tools.py`（追加）

**Interfaces:**
- Consumes: `app_service.get_app_by_workspace` / `get_app`、`engine.probe_dev` / `strip_ansi` / `LOG_PREFIX`、`logs.collect_dev_logs`、`memory_service.redact_sensitive`
- Produces: `resolve_session_app(session_factory, workspace_id, *, owner_user_id=None) -> tuple[dict | None, str]`
- Produces: `fetch_app(ctx) -> dict | None`
- Produces: `pending_task(ctx, app_id) -> asyncio.Task | None`
- Produces: `wait_seconds_of(cfg, args) -> int`、`tail_of(cfg, args) -> int`
- Produces: `format_log_lines(rows, *, limit_lines=300, limit_chars=16384, limit_line_chars=500) -> tuple[list[str], bool, int]`
- Produces: `run_preview_tool(ctx, name, args, *, confirmed=False) -> dict`
- Produces: 常量 `STEP_PREFIX` / `DEFAULT_WAIT_SECONDS` / `WAIT_SECONDS_MAX` / `DEFAULT_LOG_LINES` / `LOG_LINES_MIN` / `LOG_LINES_MAX` / `DEFAULT_MAX_RESTARTS` / `TOOL_LOG_TIMEOUT` / `RESTART_LOG_LINES` / `PROGRESS_INTERVAL` / `HEARTBEAT_INTERVAL` / `POLL_INTERVAL`
- Produces: `ctx` 只读契约（本模块**不 import** `ToolContext`）：`ctx.app`、`ctx.cfg`、`ctx.session_factory`、`ctx.deploy_hub`、`ctx.deploy_tasks`、`ctx.cancel`、`ctx.run_state`、`ctx.session_id`、`ctx.send`

- [ ] **Step 1: 先写失败测试（ctx 替身与应用解析）**

在 `tests/test_preview_tools.py` 末尾追加：

```python
# ---------- ctx 替身：preview_tools 只读字段，不 import ToolContext ----------

class Ctx:
    def __init__(self, app=None, *, sf=None, cfg=None, hub=None, tasks=None, cancel=None):
        from app.deploy.hub import DeployHub
        self.app = app
        self.session_factory = sf
        self.cfg = cfg or {}
        self.deploy_hub = hub if hub is not None else DeployHub()   # 真 hub：订阅/环形缓冲都走生产代码
        self.deploy_tasks = tasks if tasks is not None else {}
        self.cancel = cancel or asyncio.Event()
        self.run_state = {}
        self.session_id = 1
        self.sent = []

    async def send(self, ev):
        self.sent.append(ev)


# ---------- resolve_session_app（§5.1 fail closed） ----------

async def test_resolve_session_app_hit_and_miss(sf, tmp_path):
    from app.agent import preview_tools as pt
    wid = await _mk_workspace(sf, str(tmp_path / "r1"))
    aid = await _mk_app(sf, wid, "pvres")
    app, note = await pt.resolve_session_app(sf, wid)
    assert app["id"] == aid and note == ""
    plain = await _mk_workspace(sf, str(tmp_path / "r2"))
    assert await pt.resolve_session_app(sf, plain) == (None, "")
    assert await pt.resolve_session_app(sf, None) == (None, "")


async def test_resolve_session_app_duplicate_fails_closed_with_note(sf, tmp_path):
    """重复绑定：不挂载工具，但要给模型一句能转述给用户的原因（不能静默）。"""
    from app.agent import preview_tools as pt
    wid = await _mk_workspace(sf, str(tmp_path / "r3"))
    await _mk_app(sf, wid, "pvdupA", dev_port=8247, prod_port=8246)
    await _mk_app(sf, wid, "pvdupB", dev_port=8249, prod_port=8248)
    app, note = await pt.resolve_session_app(sf, wid)
    assert app is None and "多个应用" in note


async def test_resolve_session_app_survives_db_error(sf, monkeypatch):
    """解析出错按非应用工作区处理：记 warning，绝不让整轮对话失败。"""
    from app.agent import preview_tools as pt

    async def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(pt.app_service, "get_app_by_workspace", boom)
    assert await pt.resolve_session_app(sf, 1) == (None, "")


async def test_resolve_session_app_splits_404_from_409(sf, monkeypatch):
    """404（探测到行之后应用被删）必须与 409（绑定了多个应用）分岔。

    `get_app_by_workspace` 是「只查 id、再委托 get_app 取行」，两查之间应用可能被另一个标签页删掉；
    把这条也说成「绑定了多个应用」，用户删掉应用后会看到一句莫名其妙的解释。
    """
    from app.agent import preview_tools as pt

    async def gone(*a, **k):
        raise DeployError("应用不存在", status=404)

    monkeypatch.setattr(pt.app_service, "get_app_by_workspace", gone)
    assert await pt.resolve_session_app(sf, 1) == (None, "")


async def test_fetch_app_returns_none_after_app_deleted(sf, tmp_path):
    """运行中应用被删（另一个标签页）：工具拿到 None，据此回「已被删除」而不是 500。"""
    from app.agent import preview_tools as pt
    wid = await _mk_workspace(sf, str(tmp_path / "r4"))
    aid = await _mk_app(sf, wid, "pvgone")
    ctx = Ctx({"id": aid}, sf=sf)
    assert (await pt.fetch_app(ctx))["slug"] == "pvgone"
    async with sf() as s:
        await s.execute(text("DELETE FROM app_project WHERE id=:i"), {"i": aid})
        await s.commit()
    assert await pt.fetch_app(ctx) is None
    assert await pt.fetch_app(Ctx(None, sf=sf)) is None


# ---------- 等待/行数夹取（§5.9） ----------

def test_wait_seconds_clamped_and_config_driven():
    from app.agent import preview_tools as pt
    assert pt.wait_seconds_of({}, {}) == 240
    assert pt.wait_seconds_of({"agent.preview_wait_seconds": 30}, {}) == 30
    assert pt.wait_seconds_of({}, {"wait_seconds": 5000}) == 900
    assert pt.wait_seconds_of({}, {"wait_seconds": -5}) == 0
    assert pt.wait_seconds_of({}, {"wait_seconds": "abc"}) == 240
    assert pt.wait_seconds_of({"agent.preview_wait_seconds": 0}, {}) == 0     # <=0 = 等到任务结束


def test_tail_clamped_between_10_and_300():
    from app.agent import preview_tools as pt
    assert pt.tail_of({}, {}) == 80
    assert pt.tail_of({"agent.preview_log_lines": 120}, {}) == 120
    assert pt.tail_of({}, {"tail": 1}) == 10
    assert pt.tail_of({}, {"tail": 9999}) == 300
    assert pt.tail_of({}, {"tail": None}) == 80


# ---------- 日志整形（§6 日志出口收口） ----------

def _row(line, *, source="container", service="backend", ts="2026-10-29T10:00:01+08:00"):
    return {"source": source, "service": service, "ts": ts, "line": line}


def test_format_log_lines_labels_time_and_source():
    from app.agent import preview_tools as pt
    lines, truncated, redacted = pt.format_log_lines([
        _row("Uvicorn running", ts="2026-10-29T10:00:01+08:00"),
        _row("✓ 迁移完成", source="platform", service="平台"),
    ])
    assert lines[0].startswith("10:00:01 [backend] Uvicorn running")
    assert lines[1].endswith("[平台] ✓ 迁移完成")
    assert truncated is False and redacted == 0


def test_format_log_lines_redacts_credentials_and_strips_ansi():
    from app.agent import preview_tools as pt
    lines, _, redacted = pt.format_log_lines([
        _row("\x1b[33mDATABASE_URL=postgresql://u:supersecret@db:5432/app\x1b[39m"),
    ])
    assert "\x1b" not in lines[0]
    assert "supersecret" not in lines[0] and "<redacted:" in lines[0]
    assert redacted >= 1


def test_format_log_lines_caps_each_line_and_total():
    from app.agent import preview_tools as pt
    lines, truncated, _ = pt.format_log_lines([_row("x" * 900)])
    assert len(lines[0]) < 600 and "已截断" in lines[0]
    many = [_row(f"line-{i} " + "y" * 200) for i in range(400)]
    lines, truncated, _ = pt.format_log_lines(many, limit_lines=300, limit_chars=4000)
    assert len(lines) <= 300 and truncated is True
    assert sum(len(l) for l in lines) <= 4000
    assert lines[-1].startswith("10:00:01 [backend] line-399")    # 丢的是最旧的行
```

- [ ] **Step 2: 先写失败测试（两个只读 handler）**

继续追加：

```python
# ---------- preview_status（§5.4） ----------

async def test_status_stopped_does_not_probe(sf, tmp_path, monkeypatch):
    from app.agent import preview_tools as pt
    from app.deploy import engine as eng
    wid = await _mk_workspace(sf, str(tmp_path / "s1"))
    aid = await _mk_app(sf, wid, "pvst")
    hits = []

    async def fake_probe(app, timeout=2):
        hits.append(app["id"])
        return True, ""

    monkeypatch.setattr(eng, "probe_dev", fake_probe)
    r = await pt.run_preview_tool(Ctx({"id": aid}, sf=sf), "preview_status", {})
    assert r["ok"] is True and r["status"] == "stopped"
    assert r["healthy"] is False and r["probe"] == "" and "未运行" in r["hint"]
    assert r["url"].endswith(":8241") and r["app"] == {"slug": "pvst", "stack": "vue3-fastapi"}
    assert r["deploy_task"] is False and hits == []          # 非 running 不探针


async def test_status_running_healthy_and_unhealthy(sf, tmp_path, monkeypatch):
    from app.agent import preview_tools as pt
    from app.deploy import engine as eng
    wid = await _mk_workspace(sf, str(tmp_path / "s2"))
    aid = await _mk_app(sf, wid, "pvrun", dev_port=8251, prod_port=8250)
    async with sf() as s:
        await s.execute(text("UPDATE app_project SET status_dev='running' WHERE id=:i"), {"i": aid})
        await s.commit()

    async def ok_probe(app, timeout=2):
        return True, ""

    monkeypatch.setattr(eng, "probe_dev", ok_probe)
    r = await pt.run_preview_tool(Ctx({"id": aid}, sf=sf), "preview_status", {})
    assert r["healthy"] is True and r["probe"] == "" and r["hint"] == ""

    async def bad_probe(app, timeout=2):
        return False, "/ → 403"

    monkeypatch.setattr(eng, "probe_dev", bad_probe)
    r2 = await pt.run_preview_tool(Ctx({"id": aid}, sf=sf), "preview_status", {})
    assert r2["ok"] is True and r2["healthy"] is False        # 应用报错仍是 ok=true（§4.2 语义）
    assert r2["probe"] == "/ → 403" and "preview_logs" in r2["hint"]


async def test_status_reports_pending_deploy_task(sf, tmp_path, monkeypatch):
    from app.agent import preview_tools as pt
    from app.deploy import engine as eng
    wid = await _mk_workspace(sf, str(tmp_path / "s3"))
    aid = await _mk_app(sf, wid, "pvtask", dev_port=8253, prod_port=8252)
    monkeypatch.setattr(eng, "probe_dev", lambda app, timeout=2: asyncio.sleep(0, (True, "")))
    gate = asyncio.Event()
    task = asyncio.create_task(gate.wait())
    ctx = Ctx({"id": aid}, sf=sf, tasks={aid: task})
    r = await pt.run_preview_tool(ctx, "preview_status", {})
    assert r["deploy_task"] is True and "正在启动" in r["hint"]
    gate.set()
    await task


async def test_status_after_app_deleted_is_a_normal_result(sf, tmp_path):
    from app.agent import preview_tools as pt
    wid = await _mk_workspace(sf, str(tmp_path / "s4"))
    aid = await _mk_app(sf, wid, "pvdel", dev_port=8255, prod_port=8254)
    async with sf() as s:
        await s.execute(text("DELETE FROM app_project WHERE id=:i"), {"i": aid})
        await s.commit()
    r = await pt.run_preview_tool(Ctx({"id": aid}, sf=sf), "preview_status", {})
    assert r["ok"] is False and "已被删除" in r["error"]


# ---------- preview_logs（§4.2 / US-10…US-14） ----------

async def test_logs_merges_platform_and_container_lines(sf, tmp_path, monkeypatch):
    from app.agent import preview_tools as pt
    from app.deploy.hub import DeployHub
    wid = await _mk_workspace(sf, str(tmp_path / "l1"))
    aid = await _mk_app(sf, wid, "pvlog", dev_port=8257, prod_port=8256)
    hub = DeployHub()
    hub.publish(aid, {"app_id": aid, "type": "dev_log", "line": "[zerocode] ✓ 预览已就绪"})
    captured = {}

    async def fake_collect(session_factory, app, h, *, tail=200, since_seq=0, container_timeout=60):
        captured.update({"tail": tail, "timeout": container_timeout})
        return {"lines": [{"source": "container", "service": "backend",
                           "ts": "2026-10-29T10:00:02+08:00", "line": "Uvicorn running"},
                          {"source": "platform", "service": "平台",
                           "ts": "2026-10-29T10:00:03+08:00", "line": "[zerocode] ✓ 预览已就绪"}],
                "note": "", "status_dev": "running", "url": "http://x", "last_seq": 1}

    monkeypatch.setattr(pt, "collect_dev_logs", fake_collect)
    ctx = Ctx({"id": aid}, sf=sf, hub=hub, cfg={"agent.preview_log_lines": 42})
    r = await pt.run_preview_tool(ctx, "preview_logs", {})
    assert captured == {"tail": 42, "timeout": 15}         # 工具侧 15s，不吊死整轮对话
    assert r["ok"] is True and r["status"] == "stopped"     # 状态取库里的，不取收集器回声
    assert r["lines"][0].endswith("[backend] Uvicorn running")
    assert r["truncated"] is False and r["note"] == "" and r["redacted"] == 0


async def test_logs_clamps_tail_and_keeps_note(sf, tmp_path, monkeypatch):
    from app.agent import preview_tools as pt
    wid = await _mk_workspace(sf, str(tmp_path / "l2"))
    aid = await _mk_app(sf, wid, "pvlog2", dev_port=8259, prod_port=8258)
    seen = {}

    async def fake_collect(session_factory, app, h, *, tail=200, since_seq=0, container_timeout=60):
        seen["tail"] = tail
        return {"lines": [], "note": "容器日志暂不可用：Docker 未启动", "status_dev": "error",
                "url": "", "last_seq": 0}

    monkeypatch.setattr(pt, "collect_dev_logs", fake_collect)
    r = await pt.run_preview_tool(Ctx({"id": aid}, sf=sf), "preview_logs", {"tail": 99999})
    assert seen["tail"] == 300 and r["note"] == "容器日志暂不可用：Docker 未启动"
    assert r["lines"] == [] and r["ok"] is True


async def test_logs_are_read_only(sf, tmp_path, monkeypatch):
    """US-14：读日志不改状态、不产生部署任务。"""
    from app.agent import preview_tools as pt
    wid = await _mk_workspace(sf, str(tmp_path / "l3"))
    aid = await _mk_app(sf, wid, "pvlog3", dev_port=8261, prod_port=8260)

    async def fake_collect(session_factory, app, h, *, tail=200, since_seq=0, container_timeout=60):
        return {"lines": [], "note": "", "status_dev": "stopped", "url": "", "last_seq": 0}

    monkeypatch.setattr(pt, "collect_dev_logs", fake_collect)
    ctx = Ctx({"id": aid}, sf=sf)
    await pt.run_preview_tool(ctx, "preview_logs", {})
    async with sf() as s:
        st = (await s.execute(text("SELECT status_dev FROM app_project WHERE id=:i"),
                              {"i": aid})).scalar()
    assert st == "stopped" and ctx.deploy_tasks == {}


async def test_unknown_preview_tool_is_a_normal_result(sf):
    from app.agent import preview_tools as pt
    r = await pt.run_preview_tool(Ctx(None, sf=sf), "preview_nope", {})
    assert r == {"ok": False, "error": "未知预览工具: preview_nope"}
```

- [ ] **Step 3: 跑测试确认失败**

Run: `pytest tests/test_preview_tools.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'app.agent.preview_tools'`

- [ ] **Step 4: 写实现**

新建 `app/agent/preview_tools.py`：

```python
# app/agent/preview_tools.py
"""Agent 的预览验证工具（规格 docs/specs/2026-10-29-agent-preview-tools.md）。

三个工具、零命令面：preview_status / preview_logs 只读，preview_restart 只调引擎既有的
start_dev / restart_dev。应用身份来自服务端解析（ctx.app），schema 里没有 app 参数，
模型既指不到别的应用也指不到别人的应用。

刻意不 import app.agent.tools（ToolContext / ToolError）：tools.py 在分派分支里惰性 import
本模块，反向依赖会成环。handler 只读 ctx 的字段，所有失败都作为**正常结果**返回
（{"ok": False, "error": ...}）而不抛异常——模型需要读到原因并向用户解释。
"""
import asyncio
import logging
import re
import time
from datetime import datetime

from app.deploy import engine
from app.deploy.errors import DeployError
from app.deploy.logs import collect_dev_logs
from app.services import app_service
from app.services.memory_service import redact_sensitive

logger = logging.getLogger(__name__)

STEP_PREFIX = engine.LOG_PREFIX        # "[zerocode]"：只有平台步骤行进聊天进度流
DEFAULT_WAIT_SECONDS = 240             # agent.preview_wait_seconds 的代码默认值
WAIT_SECONDS_MAX = 900
DEFAULT_LOG_LINES = 80                 # agent.preview_log_lines
LOG_LINES_MIN = 10
LOG_LINES_MAX = 300
DEFAULT_MAX_RESTARTS = 3               # agent.preview_max_restarts（<=0 不限）
TOOL_LOG_TIMEOUT = 15                  # 工具读容器日志的超时（REST 面板仍是 60）
RESTART_LOG_LINES = 20                 # 失败/超时结果里附带的最近日志行数
LOG_LINE_CHARS = 500                   # 单行上限：一条 traceback 不该吃掉整个上下文窗口
LOG_TOTAL_BYTES = 16 * 1024            # 总量上限
PROGRESS_INTERVAL = 0.25               # 进度节流：同一时刻最多一条/250ms
HEARTBEAT_INTERVAL = 30.0              # 长等待期间每 30s 补一句「仍在启动」
POLL_INTERVAL = 0.5                    # 等待循环周期，也是取消检查的粒度

_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[T ](\d{2}:\d{2}:\d{2})")


async def resolve_session_app(session_factory, workspace_id, *,
                              owner_user_id=None) -> tuple[dict | None, str]:
    """每次 run 开始时解析一次应用身份。返回 `(app, note)`。

    - app 为 dict：应用工作区，预览工具挂载；
    - app 为 None 且 note 为空：非应用工作区，静默不挂载（普通工作区不该看到一句解释）；
    - app 为 None 且 note 非空：fail closed（工作区绑定了多个应用），note 是给模型转述给用户的一句话。

    解析过程本身出错（数据库异常）按非应用工作区处理并记 warning——绝不让整轮对话失败。
    """
    if workspace_id is None:
        return None, ""
    try:
        app = await app_service.get_app_by_workspace(
            session_factory, workspace_id, owner_user_id=owner_user_id)
    except DeployError as e:
        # 409 = 工作区绑定了多个应用（fail closed，但要给模型一句能转述给用户的原因）；
        # 404 = 探测到行之后、委托回读之前应用被删（另一个标签页），那是「应用不可用」，
        # 不是「绑定了多个应用」——把前者说成后者等于对用户撒谎
        if e.status == 409:
            logger.warning("预览工具未挂载：工作区 %s 绑定了多个应用", workspace_id)
            return None, "当前工作区绑定了多个应用，无法使用预览工具"
        logger.warning("预览工具未挂载：工作区 %s 的应用解析被拒（%s）", workspace_id, e)
        return None, ""
    except Exception:
        logger.warning("预览工具未挂载：工作区 %s 的应用解析失败", workspace_id, exc_info=True)
        return None, ""
    return app, ""


async def fetch_app(ctx) -> dict | None:
    """每次工具调用都重新读一遍应用行：run 开始时那份快照里的状态永远不可信。

    应用被删（另一个标签页操作）→ None，handler 据此回「应用已被删除或不可用」而不是 500。
    """
    app = getattr(ctx, "app", None)
    if not app:
        return None
    try:
        return await app_service.get_app(ctx.session_factory, app["id"])
    except DeployError:
        return None


def pending_task(ctx, app_id: int):
    """该应用在共享任务表里的未完成任务（与 REST 同一张 `app.state.dev_tasks`）。"""
    task = (getattr(ctx, "deploy_tasks", None) or {}).get(app_id)
    return task if task is not None and not task.done() else None


def _clamp_int(raw, default: int, lo: int, hi: int) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = default
    return max(lo, min(value, hi))


def wait_seconds_of(cfg: dict | None, args: dict) -> int:
    """preview_restart 的等待上限：参数优先，缺省读 `agent.preview_wait_seconds`，一律夹在 0..900。

    `<=0` 表示等到任务结束（仍可被「停止」取消等待）。畸形值回落默认，不把异常抛给模型。
    """
    cfg_value = _clamp_int((cfg or {}).get("agent.preview_wait_seconds", DEFAULT_WAIT_SECONDS),
                           DEFAULT_WAIT_SECONDS, 0, WAIT_SECONDS_MAX)
    if args.get("wait_seconds") is None:
        return cfg_value
    return _clamp_int(args.get("wait_seconds"), cfg_value, 0, WAIT_SECONDS_MAX)


def tail_of(cfg: dict | None, args: dict) -> int:
    """preview_logs 的行数：参数优先，缺省读 `agent.preview_log_lines`，夹在 10..300。"""
    cfg_value = _clamp_int((cfg or {}).get("agent.preview_log_lines", DEFAULT_LOG_LINES),
                           DEFAULT_LOG_LINES, LOG_LINES_MIN, LOG_LINES_MAX)
    if args.get("tail") is None:
        return cfg_value
    return _clamp_int(args.get("tail"), cfg_value, LOG_LINES_MIN, LOG_LINES_MAX)


def _clock(ts) -> str:
    """归并行的时间 → 本地 `HH:MM:SS`（模型只需要时刻，日期在结果里另有出处）。"""
    raw = str(ts or "")
    try:
        return datetime.fromisoformat(raw).astimezone().strftime("%H:%M:%S")
    except ValueError:
        m = _TS_RE.match(raw)
        return m.group(1) if m else "--:--:--"


def _source_of(row: dict) -> str:
    if row.get("source") == "platform":
        return "平台"
    return row.get("service") or "容器"


def format_log_lines(rows, *, limit_lines: int = LOG_LINES_MAX, limit_chars: int = LOG_TOTAL_BYTES,
                     limit_line_chars: int = LOG_LINE_CHARS) -> tuple[list[str], bool, int]:
    """归并行 → 模型可读的短行 `时间 [来源] 正文`；返回 `(lines, truncated, redacted)`。

    出口收口（§6）：逐行剥 ANSI、过 `redact_sensitive`、单行限长；总量超限时**从最旧的行开始丢**
    （模型要的是「最近发生了什么」），丢过就标 truncated=True，让它知道还有更早的没给。
    """
    out: list[str] = []
    redacted = 0
    kept = rows[-max(int(limit_lines), 1):]
    for row in kept:
        body = engine.strip_ansi(str(row.get("line") or "")).rstrip()
        if not body:
            continue
        body, hits = redact_sensitive(body)
        redacted += hits
        if len(body) > limit_line_chars:
            body = body[:limit_line_chars] + "…（已截断）"
        out.append(f"{_clock(row.get('ts'))} [{_source_of(row)}] {body}")
    truncated = len(rows) > len(kept) or len(kept) > len(out)
    total = sum(len(line) for line in out)
    while out and total > limit_chars:
        total -= len(out.pop(0))
        truncated = True
    return out, truncated, redacted


def _status_hint(status: str, deploy_task: bool) -> str:
    if deploy_task or status == "starting":
        return "预览正在启动，稍后再用 preview_status 查看，不要重复启动"
    if status == "error":
        return "上次启动失败：用 preview_logs 看错误原文，再决定是否 preview_restart"
    return "预览未运行：需要验证改动时用 preview_restart 启动"


async def _status(ctx, args: dict) -> dict:
    """只读状态：数据库状态 + 一次 2 秒探针。刻意不调 engine.dev_status（那会起 compose ps 子进程）。"""
    app = await fetch_app(ctx)
    if app is None:
        return {"ok": False, "error": "应用已被删除或不可用，预览工具无法使用"}
    status = app.get("status_dev") or "stopped"
    url = app.get("preview_url") or ""
    base = {"ok": True, "app": {"slug": app.get("slug"), "stack": app.get("stack")},
            "status": status, "url": url,
            "deploy_task": pending_task(ctx, app["id"]) is not None}
    if not url:
        return {**base, "healthy": False, "probe": "", "hint": "应用没有 dev 端口，无法给出预览地址"}
    if status != "running":
        return {**base, "healthy": False, "probe": "",
                "hint": _status_hint(status, base["deploy_task"])}
    healthy, probe = await engine.probe_dev(app, timeout=2)
    hint = "" if healthy else ("端口探测未通过：healthy 只代表 HTTP 可达，"
                               "编译错误与 traceback 要看 preview_logs")
    return {**base, "healthy": healthy, "probe": probe, "hint": hint}


async def _logs(ctx, args: dict) -> dict:
    """只读日志：与应用页面板同一个收集器，但限长 + 遮断后才进模型上下文。"""
    app = await fetch_app(ctx)
    if app is None:
        return {"ok": False, "error": "应用已被删除或不可用，预览工具无法使用"}
    collected = await collect_dev_logs(ctx.session_factory, app, ctx.deploy_hub,
                                       tail=tail_of(ctx.cfg, args),
                                       container_timeout=TOOL_LOG_TIMEOUT)
    lines, truncated, redacted = format_log_lines(collected["lines"])
    return {"ok": True, "status": app.get("status_dev") or "stopped",
            "url": app.get("preview_url") or "", "lines": lines, "truncated": truncated,
            "note": collected.get("note") or "", "redacted": redacted}


async def run_preview_tool(ctx, name: str, args: dict, *, confirmed: bool = False) -> dict:
    """三个预览工具的统一入口（`tools.execute_tool` 在分派分支里惰性 import 本函数）。

    任何失败都作为正常结果返回，不抛异常：loop 的兜底 except 会把它糊成「工具执行异常」，
    模型就读不到「Docker 没开」「已有部署任务」这类可行动的原因了。
    """
    args = args if isinstance(args, dict) else {}
    try:
        if name == "preview_status":
            return await _status(ctx, args)
        if name == "preview_logs":
            return await _logs(ctx, args)
        return {"ok": False, "error": f"未知预览工具: {name}"}
    except Exception as e:
        logger.exception("预览工具 %s 执行异常", name)
        return {"ok": False, "error": f"预览工具执行异常: {e}"}
```

- [ ] **Step 5: 给 `collect_dev_logs` 加 `container_timeout`**

`app/deploy/logs.py` 的签名与容器调用改为：

```python
async def collect_dev_logs(session_factory, app: dict, hub, *,
                           tail: int = 200, since_seq: int = 0,
                           container_timeout: int = 60) -> dict:
```

```python
    container, note = await engine.container_logs(session_factory, app, n, timeout=container_timeout)
```

docstring 补一句：

```
    `container_timeout` 默认 60s（REST 面板）；Agent 工具传 15s，避免一次日志读取卡住整轮对话。
```

- [ ] **Step 6: 跑测试确认通过**

Run: `pytest tests/test_preview_tools.py tests/test_dev_logs.py -v`
Expected: PASS —— 新用例全绿，日志端点零回归

- [ ] **Step 7: 提交**

```bash
git add app/agent/preview_tools.py app/deploy/logs.py tests/test_preview_tools.py
git commit -m "feat(agent): 预览工具只读内核（状态探针与日志整形）"
```

---

### Task 7: `preview_restart` 的等待循环与结果塑形

阶段 B 的核心。一条链：策略判定 → Docker 预检 → 订阅 → 登记任务 → 有界等待 → 终态塑形。任何一步的失败都是**正常结果**（模型读得到原因），且聊天取消只停止等待、不取消部署任务。

**Files:**
- Modify: `app/agent/preview_tools.py`
- Test: `tests/test_preview_tools.py`（追加）

**Interfaces:**
- Consumes: `engine.start_dev` / `restart_dev` / `preflight_docker` / `probe_dev`（一律按模块属性在调用时取，monkeypatch 才拦得住）、`deploy_tasks.spawn`、`ctx.deploy_hub.subscribe/unsubscribe/publish`、`ctx.cancel`、`ctx.run_state`
- Produces: `_restart(ctx, args, *, confirmed=False) -> dict`
- Produces: `_cfg_bool(raw, default) -> bool`、`confirm_required(ctx) -> bool`、`_max_restarts(cfg) -> int`、`_restarts_used(ctx) -> int`、`_restarts_left(ctx) -> int`
- Produces: `build_preview_prompt(stack: str, name: str = "") -> str`
- Produces: `run_preview_tool` 的 `preview_restart` 分支

- [ ] **Step 1: 先写失败测试（策略表与并发拒绝）**

在 `tests/test_preview_tools.py` 末尾追加：

```python
# ---------- preview_restart：策略表（§5.5） ----------

@pytest.fixture
async def no_docker(monkeypatch):
    """Docker 预检与探针一律替身：本段验接线，真起栈由 test_engine.py 与手工矩阵负责。"""
    from app.deploy import engine as eng

    async def ok():
        return None

    async def probe(app, timeout=2):
        return True, ""

    monkeypatch.setattr(eng, "preflight_docker", ok)
    monkeypatch.setattr(eng, "probe_dev", probe)


@pytest.fixture
async def stopped_app(sf, tmp_path):
    return await _mk_app(sf, await _mk_workspace(sf, str(tmp_path / "rs")), "pvstart")


@pytest.fixture
async def running_app(sf, tmp_path):
    aid = await _mk_app(sf, await _mk_workspace(sf, str(tmp_path / "rr")), "pvrestart",
                        dev_port=8263, prod_port=8262)
    async with sf() as s:
        await s.execute(text("UPDATE app_project SET status_dev='running' WHERE id=:i"), {"i": aid})
        await s.commit()
    return aid


async def _engine_action_catcher(monkeypatch, action_name: str):
    """把 engine.<action> 换成「立刻完成」的替身，返回它收到的调用记录。

    替身像真引擎一样把终态写进 `status_dev`：工具的 `status` 读的是库，不是替身的返回值——
    不写库的替身会让断言变成「替身自说自话」。
    """
    from app.deploy import engine as eng
    seen = []

    async def fake(sf_, app, notify=None):
        seen.append({"slug": app["slug"], "notify": notify})
        await eng._set_status(sf_, app["id"], "running")
        return {"status": "running"}

    monkeypatch.setattr(eng, action_name, fake)
    return seen


async def test_stopped_preview_calls_start_dev_without_confirm(stopped_app, sf, monkeypatch, no_docker):
    from app.agent import preview_tools as pt
    started = await _engine_action_catcher(monkeypatch, "start_dev")
    restart = await _engine_action_catcher(monkeypatch, "restart_dev")
    ctx = Ctx({"id": stopped_app}, sf=sf, cfg={"agent.preview_confirm_running": True})
    r = await pt.run_preview_tool(ctx, "preview_restart", {"wait_seconds": 5})
    assert r["ok"] is True and r["status"] == "running" and r["healthy"] is True
    assert len(started) == 1 and restart == []               # stopped → 启动，不先 down（与应用页一致）
    assert ctx.run_state["preview_restarts"] == 1 and r["restarts_left"] == 2


async def test_running_preview_refuses_unconfirmed_restart(running_app, sf, monkeypatch, no_docker):
    """竞态兜底（§5.6）：确认期间预览变成 running，也不执行未确认的中断。"""
    from app.agent import preview_tools as pt
    started = await _engine_action_catcher(monkeypatch, "restart_dev")
    ctx = Ctx({"id": running_app}, sf=sf, cfg={"agent.preview_confirm_running": True})
    r = await pt.run_preview_tool(ctx, "preview_restart", {})
    assert r["ok"] is False and "请重试并在确认框中选择确认" in r["error"]
    assert started == [] and ctx.deploy_tasks == {}


async def test_running_preview_restarts_when_user_confirmed(running_app, sf, monkeypatch, no_docker):
    from app.agent import preview_tools as pt
    restarted = await _engine_action_catcher(monkeypatch, "restart_dev")
    ctx = Ctx({"id": running_app}, sf=sf, cfg={"agent.preview_confirm_running": True})
    r = await pt.run_preview_tool(ctx, "preview_restart", {}, confirmed=True)
    assert r["ok"] is True and len(restarted) == 1
    assert restarted[0]["notify"] == ctx.deploy_hub.publish  # 进度接到同一个 DeployHub（US-24）


async def test_confirm_switch_off_skips_the_confirm_gate(running_app, sf, monkeypatch, no_docker):
    from app.agent import preview_tools as pt
    restarted = await _engine_action_catcher(monkeypatch, "restart_dev")
    ctx = Ctx({"id": running_app}, sf=sf, cfg={"agent.preview_confirm_running": False})
    r = await pt.run_preview_tool(ctx, "preview_restart", {})
    assert r["ok"] is True and len(restarted) == 1


async def test_existing_starting_task_is_observed_not_respawned(stopped_app, sf, monkeypatch, no_docker):
    """US-16：用户刚点过启动，工具等同一个任务，绝不双起一个栈。"""
    from app.agent import preview_tools as pt
    from app.deploy import engine as eng
    started = await _engine_action_catcher(monkeypatch, "start_dev")
    async with sf() as s:
        await s.execute(text("UPDATE app_project SET status_dev='starting' WHERE id=:i"),
                        {"i": stopped_app})
        await s.commit()
    gate = asyncio.Event()

    async def user_task():
        await gate.wait()
        return {"status": "running"}

    task = asyncio.create_task(user_task())
    ctx = Ctx({"id": stopped_app}, sf=sf, tasks={stopped_app: task})
    r = await pt.run_preview_tool(ctx, "preview_restart", {"wait_seconds": 1})
    assert started == [] and r["ok"] is False and r["status"] == "starting"
    assert ctx.run_state.get("preview_restarts") is None     # 观察现有任务不计次数
    gate.set()
    await task


async def test_other_deploy_task_is_refused_without_spawning(stopped_app, sf, monkeypatch, no_docker):
    """库里状态不是 starting/stopping 却有任务在跑（多半是发布）：拒绝并发，不排队、不双跑。"""
    from app.agent import preview_tools as pt
    started = await _engine_action_catcher(monkeypatch, "start_dev")
    gate = asyncio.Event()
    task = asyncio.create_task(gate.wait())
    ctx = Ctx({"id": stopped_app}, sf=sf, tasks={stopped_app: task})
    r = await pt.run_preview_tool(ctx, "preview_restart", {})
    assert r["ok"] is False and "部署任务进行中" in r["error"] and started == []
    assert ctx.deploy_tasks[stopped_app] is task             # 没有覆盖别人的任务
    gate.set()
    await task
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_preview_tools.py -k "restart or preview" -v`
Expected: FAIL —— `未知预览工具: preview_restart`（分支未接入），其余因 `Ctx` 未带 hub 而报错

- [ ] **Step 3: 继续写失败测试（等待、取消、超时、失败、上限、进度）**

追加：

```python
async def test_wait_timeout_returns_starting_and_leaves_task_running(stopped_app, sf, monkeypatch):
    """US-18：等待有界，超时是正常结果——后台任务继续跑完，模型改用 preview_status 观察。"""
    from app.agent import preview_tools as pt
    from app.deploy import engine as eng
    gate = asyncio.Event()

    async def slow(sf_, app, notify=None):
        await eng._set_status(sf_, app["id"], "starting")     # 真引擎一进 start_dev 就落 starting
        await gate.wait()

    monkeypatch.setattr(eng, "preflight_docker", lambda: asyncio.sleep(0))
    monkeypatch.setattr(eng, "start_dev", slow)
    ctx = Ctx({"id": stopped_app}, sf=sf)
    r = await pt.run_preview_tool(ctx, "preview_restart", {"wait_seconds": 1})
    assert r["ok"] is False and r["status"] == "starting"
    assert "不要连续重启" in r["hint"] and "preview_status" in r["hint"]
    assert ctx.deploy_tasks[stopped_app] and not ctx.deploy_tasks[stopped_app].done()
    gate.set()
    await ctx.deploy_tasks[stopped_app]


async def test_chat_cancel_stops_waiting_only(stopped_app, sf, monkeypatch):
    """US-19：点「停止」后工具在一个轮询周期内返回，部署任务不被取消。"""
    from app.agent import preview_tools as pt
    from app.deploy import engine as eng
    gate = asyncio.Event()

    async def slow(sf_, app, notify=None):
        await eng._set_status(sf_, app["id"], "starting")
        await gate.wait()

    monkeypatch.setattr(eng, "preflight_docker", lambda: asyncio.sleep(0))
    monkeypatch.setattr(eng, "start_dev", slow)
    cancel = asyncio.Event()
    ctx = Ctx({"id": stopped_app}, sf=sf, cancel=cancel)
    task = asyncio.create_task(pt.run_preview_tool(ctx, "preview_restart", {"wait_seconds": 60}))
    await asyncio.sleep(0.8)
    cancel.set()
    r = await asyncio.wait_for(task, 2)
    assert r["ok"] is True and r["cancelled"] is True and "用户已停止本轮" in r["hint"]
    assert r["status"] == "starting"
    assert not ctx.deploy_tasks[stopped_app].done()          # 预览仍在后台继续启动
    gate.set()
    await ctx.deploy_tasks[stopped_app]


async def test_task_failure_is_shaped_with_status_and_logs(stopped_app, sf, monkeypatch):
    """US-20：DeployError 原文带出 + 状态 + 最近日志，模型不必再猜。

    状态由「引擎」自己写库（真 start_dev 就是这么收口的），工具侧每次终态都重新读一遍应用行，
    所以断言的是「读到什么回什么」，不是替身硬写的字面量。
    """
    from app.agent import preview_tools as pt
    from app.deploy import engine as eng
    from app.deploy.errors import DeployError

    async def preflight_ok():
        return None

    async def boom(sf_, app, notify=None):
        notify(app["id"], {"app_id": app["id"], "type": "dev_log",
                           "line": "[zerocode] ✗ 迁移失败，dev 栈不启动"})
        await eng._set_status(sf_, app["id"], "error")
        raise DeployError("dev 迁移失败（sqlrunner 退出码 1），详情见迁移日志")

    monkeypatch.setattr(eng, "preflight_docker", preflight_ok)
    monkeypatch.setattr(eng, "start_dev", boom)
    ctx = Ctx({"id": stopped_app}, sf=sf)
    r = await pt.run_preview_tool(ctx, "preview_restart", {"wait_seconds": 5})
    assert r["ok"] is False and r["status"] == "error"
    assert "sqlrunner 退出码 1" in r["error"]
    assert any("迁移失败" in line for line in r["logs"])


async def test_non_deploy_error_gets_a_prefix(stopped_app, sf, monkeypatch):
    from app.agent import preview_tools as pt
    from app.deploy import engine as eng

    async def preflight_ok():
        return None

    async def crash(sf_, app, notify=None):
        raise ValueError("unexpected")

    monkeypatch.setattr(eng, "preflight_docker", preflight_ok)
    monkeypatch.setattr(eng, "start_dev", crash)
    r = await pt.run_preview_tool(Ctx({"id": stopped_app}, sf=sf), "preview_restart",
                                  {"wait_seconds": 5})
    assert r["ok"] is False and r["error"] == "预览任务异常: unexpected"


async def test_confirm_window_status_change_falls_back_to_start(running_app, sf, monkeypatch, no_docker):
    """§5.5：确认期间预览从 running 变成 stopped → 确认通过后按启动执行，不再问第二次。"""
    from app.agent import preview_tools as pt
    from app.deploy import engine as eng
    started = await _engine_action_catcher(monkeypatch, "start_dev")
    restarted = await _engine_action_catcher(monkeypatch, "restart_dev")
    async with sf() as s:
        await s.execute(text("UPDATE app_project SET status_dev='stopped' WHERE id=:i"),
                        {"i": running_app})
        await s.commit()
    ctx = Ctx({"id": running_app}, sf=sf, cfg={"agent.preview_confirm_running": True})
    r = await pt.run_preview_tool(ctx, "preview_restart", {"wait_seconds": 5}, confirmed=True)
    assert r["ok"] is True and len(started) == 1 and restarted == []


async def test_restart_cap_refuses_without_asking_the_user(stopped_app, sf, monkeypatch):
    """US-22：到限直接返回并带上次数；观察现有任务不占用额度。"""
    from app.agent import preview_tools as pt
    from app.deploy import engine as eng
    seen = []

    async def preflight_ok():
        return None

    async def quick(sf_, app, notify=None):
        seen.append(1)
        return {"status": "running"}

    monkeypatch.setattr(eng, "preflight_docker", preflight_ok)
    monkeypatch.setattr(eng, "start_dev", quick)
    ctx = Ctx({"id": stopped_app}, sf=sf, cfg={"agent.preview_max_restarts": 2})
    ctx.run_state["preview_restarts"] = 2
    r = await pt.run_preview_tool(ctx, "preview_restart", {})
    assert r["ok"] is False and "已启动/重启预览 2 次" in r["error"] and r["restarts_left"] == 0
    assert seen == []


async def test_restart_cap_zero_means_unlimited(stopped_app, sf, monkeypatch, no_docker):
    from app.agent import preview_tools as pt
    from app.deploy import engine as eng

    async def quick(sf_, app, notify=None):
        await eng._set_status(sf_, app["id"], "running")
        return {"status": "running"}

    monkeypatch.setattr(eng, "start_dev", quick)
    ctx = Ctx({"id": stopped_app}, sf=sf, cfg={"agent.preview_max_restarts": 0})
    ctx.run_state["preview_restarts"] = 9
    r = await pt.run_preview_tool(ctx, "preview_restart", {"wait_seconds": 5})
    assert r["ok"] is True and r["restarts_left"] == -1      # -1 = 不限


async def test_docker_preflight_failure_registers_no_task(stopped_app, sf, monkeypatch):
    """US-23：预检失败给出中文引导（与 REST 的 503 同源），且不登记后台任务。"""
    from app.agent import preview_tools as pt
    from app.deploy import engine as eng
    from app.deploy.errors import DeployError

    async def down():
        raise DeployError("Docker 未启动或不可用：请打开 Docker Desktop（设置页「部署环境」可一键启动）",
                          status=503)

    monkeypatch.setattr(eng, "preflight_docker", down)
    ctx = Ctx({"id": stopped_app}, sf=sf)
    r = await pt.run_preview_tool(ctx, "preview_restart", {})
    assert r["ok"] is False and "Docker" in r["error"]
    assert ctx.deploy_tasks == {}


async def test_progress_forwards_only_step_lines_and_status_changes(
        stopped_app, sf, monkeypatch, no_docker):
    """US-17：pip/npm 的成百上千行容器输出不进聊天进度（那属于 preview_logs）。"""
    from app.agent import preview_tools as pt
    from app.deploy import engine as eng

    async def with_steps(sf_, app, notify=None):
        notify(app["id"], {"app_id": app["id"], "type": "dev_log",
                           "line": "[zerocode] → 执行 sqlchange 迁移（dev 库 pv_dev）"})
        notify(app["id"], {"app_id": app["id"], "type": "dev_log", "line": "Collecting fastapi==0.115"})
        notify(app["id"], {"app_id": app["id"], "type": "dev_status", "status": "starting"})
        await eng._set_status(sf_, app["id"], "running")
        notify(app["id"], {"app_id": app["id"], "type": "dev_log", "line": "[zerocode] ✓ 预览已就绪"})
        return {"status": "running"}

    monkeypatch.setattr(eng, "start_dev", with_steps)
    ctx = Ctx({"id": stopped_app}, sf=sf)
    r = await pt.run_preview_tool(ctx, "preview_restart", {"wait_seconds": 5})
    lines = [ev["line"] for ev in ctx.sent if ev["type"] == "tool_progress"]
    assert all(ev["name"] == "preview_restart" for ev in ctx.sent)
    assert any("执行 sqlchange 迁移" in l for l in lines)
    assert any("预览状态：starting" in l for l in lines)
    assert not any("Collecting" in l for l in lines)          # 容器输出不转发
    assert r["ok"] is True
    assert any("预览已就绪" in l for l in r["logs"])          # 全量步骤行仍进结果的 logs


async def test_successful_restart_carries_probe_and_url(stopped_app, sf, monkeypatch):
    from app.agent import preview_tools as pt
    from app.deploy import engine as eng

    async def preflight_ok():
        return None

    async def quick(sf_, app, notify=None):
        return {"status": "running"}

    async def unreachable(app, timeout=2):
        return False, "ConnectError: [Errno 111] Connection refused"

    async def quick(sf_, app, notify=None):
        await eng._set_status(sf_, app["id"], "running")
        return {"status": "running"}

    monkeypatch.setattr(eng, "preflight_docker", preflight_ok)
    monkeypatch.setattr(eng, "start_dev", quick)
    monkeypatch.setattr(eng, "probe_dev", unreachable)
    r = await pt.run_preview_tool(Ctx({"id": stopped_app}, sf=sf), "preview_restart",
                                  {"wait_seconds": 5})
    assert r["ok"] is True                                    # 探针失败不把动作翻成失败
    assert r["healthy"] is False and "ConnectError" in r["probe"]
    assert r["status"] == "running" and r["url"].endswith(":8241")
    assert r["app"]["slug"] == "pvstart"
```

- [ ] **Step 4: 跑测试确认失败**

Run: `pytest tests/test_preview_tools.py -k "restart or preview or progress or docker or task or confirm or stopped or running or successful" -v`
Expected: FAIL —— 全部落在 `未知预览工具: preview_restart`

- [ ] **Step 5: 写实现（配置位与次数）**

`app/agent/preview_tools.py` 顶部 import 区补一行（与既有 `from app.deploy import engine` 并列）：

```python
from app.deploy import tasks as deploy_tasks
```

常量区补 `DEFAULT_CONFIRM_RUNNING = True`（`agent.preview_confirm_running` 的代码默认值），
并在 `tail_of` 之后插入：

```python
_TRUE_FORMS = ("1", "true", "yes", "y")
_FALSE_FORMS = ("0", "false", "no", "n")


def _cfg_bool(raw, default: bool) -> bool:
    """配置位取值：JSON 布尔直接用；字符串按常见真/假词解析；认不出来回落默认。

    `app_config.value` 是 JSONB，正常存的是布尔；但 `agent.write_confirm` 一类的历史读法
    把配置当字符串比过（`cfg.get(...) in ("true","1",True)`），这里收口成一个明确的判据。
    """
    if isinstance(raw, bool):
        return raw
    text_ = "" if raw is None else str(raw).strip().lower()
    if text_ in _TRUE_FORMS:
        return True
    if text_ in _FALSE_FORMS:
        return False
    return default


def confirm_required(ctx) -> bool:
    """中断运行中的预览前是否要用户确认（`agent.preview_confirm_running`，默认要）。"""
    return _cfg_bool((getattr(ctx, "cfg", None) or {}).get("agent.preview_confirm_running"),
                     DEFAULT_CONFIRM_RUNNING)


def _max_restarts(cfg: dict | None) -> int:
    """单次 run 的启动/重启上限；`<=0` 表示不限（与超时参数 `<=0` 不限制的约定一致）。"""
    try:
        return int((cfg or {}).get("agent.preview_max_restarts", DEFAULT_MAX_RESTARTS))
    except (TypeError, ValueError):
        return DEFAULT_MAX_RESTARTS


def _restarts_used(ctx) -> int:
    try:
        return int((getattr(ctx, "run_state", None) or {}).get("preview_restarts", 0))
    except (TypeError, ValueError):
        return 0


def _restarts_left(ctx) -> int:
    """本次 run 还剩几次；不限时返回 -1（模型据此知道「没有额度限制」）。"""
    limit = _max_restarts(getattr(ctx, "cfg", None))
    if limit <= 0:
        return -1
    return max(limit - _restarts_used(ctx), 0)
```

- [ ] **Step 6: 写实现（进度与等待循环）**

在 `_status_hint` 之前插入：

```python
async def _emit(ctx, line: str) -> None:
    await ctx.send({"type": "tool_progress", "name": "preview_restart", "line": line})


async def _drain_progress(ctx, q, last_emit: float, seen: list[str]) -> tuple[float, list[str]]:
    """把订阅队列里的事件塑成聊天进度：只转发平台步骤行与状态变化，并再过一遍遮断。

    **不转发** pip/npm 的成百上千行容器输出（那属于 `preview_logs`），也不照单全收：
    250ms 内的多条只投递最新一条，进度是给中人看的读数，不是日志流。`seen` 始终累积全量，
    供超时/失败结果直接取用（模型不用再调一次 preview_logs 就能看到现场）。
    """
    if q is None:
        return last_emit, seen
    fresh: list[str] = []
    while not q.empty():
        ev = q.get_nowait()
        if ev.get("type") == "dev_log":
            line = str(ev.get("line") or "")
            if not line.startswith(STEP_PREFIX):
                continue
        elif ev.get("type") == "dev_status":
            line = f"{STEP_PREFIX} 预览状态：{ev.get('status')}"
        else:
            continue
        line, _ = redact_sensitive(engine.strip_ansi(line))
        if not seen or seen[-1] != line:
            seen.append(line)
            fresh.append(line)
    now = time.monotonic()
    if fresh and now - last_emit >= PROGRESS_INTERVAL:
        await _emit(ctx, fresh[-1])
        return now, seen
    return last_emit, seen


async def _wait(ctx, app: dict, task, wait_seconds: int, q) -> dict:
    """有界等待一个部署任务，把它的过程与终态塑成工具结果。

    只等不催：**永不取消部署任务**——聊天取消与等待超时都只停止等待，因为栈的起停在引擎侧是
    独立事务，中途掐掉只会留下一半的容器和一半的状态。也不在这里 stop_dev：工具的动作面
    只有「观察」「启动」「重启」三种。取消检查每 0.5s 一次，保证用户点「停止」后一个轮询周期内返回。
    """
    started = time.monotonic()
    deadline = None if wait_seconds <= 0 else started + wait_seconds
    last_emit, last_beat, seen = 0.0, started, []
    while not task.done():
        last_emit, seen = await _drain_progress(ctx, q, last_emit, seen)
        now = time.monotonic()
        if now - last_beat >= HEARTBEAT_INTERVAL:
            last_beat = now
            await _emit(ctx, f"{STEP_PREFIX} 预览仍在启动，已等待 {now - started:.0f}s")
        if getattr(ctx, "cancel", None) is not None and ctx.cancel.is_set():
            fresh = await fetch_app(ctx) or app
            return {"ok": True, "cancelled": True, "status": fresh.get("status_dev") or "starting",
                    "url": fresh.get("preview_url") or "", "elapsed_s": round(now - started, 1),
                    "hint": "用户已停止本轮；预览仍在后台继续启动", "restarts_left": _restarts_left(ctx)}
        if deadline is not None and now >= deadline:
            fresh = await fetch_app(ctx) or app
            return {"ok": False, "status": fresh.get("status_dev") or "starting",
                    "url": fresh.get("preview_url") or "", "elapsed_s": round(now - started, 1),
                    "error": "", "logs": seen[-RESTART_LOG_LINES:],
                    "hint": "预览仍在启动，用 preview_status / preview_logs 继续观察；不要连续重启",
                    "restarts_left": _restarts_left(ctx)}
        await asyncio.sleep(POLL_INTERVAL)
    _, seen = await _drain_progress(ctx, q, last_emit, seen)   # 收尾再排一次，别丢末行
    elapsed = round(time.monotonic() - started, 1)
    fresh = await fetch_app(ctx) or app
    status = fresh.get("status_dev") or "stopped"
    url = fresh.get("preview_url") or ""
    left = _restarts_left(ctx)
    if task.cancelled():
        return {"ok": False, "status": status, "url": url, "elapsed_s": elapsed,
                "error": "预览任务已被取消", "logs": seen[-RESTART_LOG_LINES:], "restarts_left": left}
    exc = task.exception()
    if exc is not None:
        error = str(exc) if isinstance(exc, DeployError) else f"预览任务异常: {exc}"
        return {"ok": False, "status": status, "url": url, "elapsed_s": elapsed,
                "error": error, "logs": seen[-RESTART_LOG_LINES:], "restarts_left": left,
                "hint": "先看 logs 定位失败原因，再决定是否重试"}
    healthy, probe = (False, "应用没有 dev 端口") if not url else await engine.probe_dev(fresh, timeout=2)
    return {"ok": True, "status": status, "healthy": healthy, "probe": probe, "url": url,
            "elapsed_s": elapsed, "app": {"slug": fresh.get("slug"), "stack": fresh.get("stack")},
            "restarts_left": left, "note": "", "logs": seen[-RESTART_LOG_LINES:]}
```

- [ ] **Step 7: 写实现（策略判定与 `_restart`）**

在 `_logs` 之后、`run_preview_tool` 之前插入：

```python
async def _restart(ctx, args: dict, *, confirmed: bool = False) -> dict:
    """启动或重启 dev 预览（§5.5 策略表）：动作面只有引擎既有的 `start_dev` / `restart_dev`。

    顺序是有意的，每一道都在打扰用户之前：次数上限 → 并发判定 → 确认兜底 → Docker 预检 → 登记任务。
    `confirmed` 由 `AgentRunner._dispatch` 传入（它才是 `confirm_request` 的唯一发出方）；
    这里只做兜底：需要确认却没拿到确认时，宁可让模型重试一次，也不执行未确认的中断。
    """
    app = await fetch_app(ctx)
    if app is None:
        return {"ok": False, "error": "应用已被删除或不可用，预览工具无法使用"}
    app_id = app["id"]
    status = app.get("status_dev") or "stopped"
    url = app.get("preview_url") or ""
    left = _restarts_left(ctx)
    if left == 0:
        return {"ok": False, "restarts_left": 0,
                "error": f"本次任务已启动/重启预览 {_max_restarts(ctx.cfg)} 次，请先检查失败原因再继续"}
    task = pending_task(ctx, app_id)
    if task is not None:
        # starting/stopping 期间有任务：观察它，不另起一个栈
        if status not in ("starting", "stopping"):
            return {"ok": False, "status": status, "url": url, "restarts_left": left,
                    "error": "该应用有部署任务进行中（可能正在发布），请稍后再试"}
    elif status == "running" and confirm_required(ctx) and not confirmed:
        return {"ok": False, "status": status, "url": url, "restarts_left": left,
                "error": "预览已在运行，请重试并在确认框中选择确认"}
    hub = getattr(ctx, "deploy_hub", None)
    q = hub.subscribe(app_id) if hub is not None else None    # 先订阅再 spawn，避免漏掉最早的事件
    try:
        if task is None:
            try:
                await engine.preflight_docker()               # 失败不登记任务（与 REST 的 503 同文案）
            except DeployError as e:
                return {"ok": False, "status": status, "url": url, "restarts_left": left,
                        "error": str(e), "hint": "未登记后台任务，Docker 就绪后重试"}
            action = engine.restart_dev if status == "running" else engine.start_dev
            notify = hub.publish if hub is not None else None
            try:
                task = deploy_tasks.spawn(ctx.deploy_tasks, app_id,
                                          lambda: action(ctx.session_factory, app, notify=notify))
            except DeployError as e:                          # 另一条通道刚抢先起了任务：不排队、不双跑
                return {"ok": False, "status": status, "url": url, "restarts_left": left,
                        "error": str(e)}
            ctx.run_state["preview_restarts"] = _restarts_used(ctx) + 1   # 只有真起任务才计次
        return await _wait(ctx, app, task, wait_seconds_of(ctx.cfg, args), q)
    finally:
        if hub is not None and q is not None:
            hub.unsubscribe(app_id, q)
```

`run_preview_tool` 里 `return {"ok": False, "error": f"未知预览工具: {name}"}` 之前插入：

```python
        if name == "preview_restart":
            return await _restart(ctx, args, confirmed=confirmed)
```

- [ ] **Step 8: 跑测试确认通过**

Run: `pytest tests/test_preview_tools.py -v`
Expected: PASS —— Task 4/6 的用例仍绿，本任务新增用例全绿

- [ ] **Step 9: 先写失败测试（`<preview>` 指引），再写实现**

在 `tests/test_preview_tools.py` 末尾追加：

```python
# ---------- <preview> 指引（§9）：工具挂载时才注入，按栈补一句热更新语义 ----------

def test_preview_prompt_carries_the_verify_dont_restart_rules():
    from app.agent import preview_tools as pt
    p = pt.build_preview_prompt("vue3-fastapi")
    assert p.startswith("\n\n<preview>") and p.endswith("</preview>")
    assert "preview_status" in p and "preview_logs" in p and "preview_restart" in p
    assert "healthy 只表示 HTTP 可达" in p          # 不许把「能访问」说成「功能正确」
    assert "不要连续重启" in p
    assert "你没有这些工具" in p                     # 明确动作面边界
    assert "3 次" not in p                          # 次数上限是可配的，提示词不写死数字


def test_preview_prompt_stack_specific_lines():
    from app.agent import preview_tools as pt
    assert "uvicorn --reload" in pt.build_preview_prompt("vue3-fastapi")
    spring = pt.build_preview_prompt("vue3-springboot")
    assert "必须 preview_restart" in spring and "uvicorn" not in spring
    assert "刷新即可" in pt.build_preview_prompt("static")
    assert "刷新即可" in pt.build_preview_prompt("vue3-node")
    assert "</preview>" in pt.build_preview_prompt("unknown-stack")   # 未知栈仍有基线指引
```

Run: `pytest tests/test_preview_tools.py -k preview_prompt -v`
Expected: FAIL —— `AttributeError: … has no attribute 'build_preview_prompt'`

在 `app/agent/preview_tools.py` 末尾追加实现：

```python
_STACK_HINTS = {
    "vue3-fastapi": "后端 Python 改动由 uvicorn --reload 自动重载；requirements.txt 改动必须重启。",
    "vue3-springboot": "后端 Java 改动不会热更新，必须 preview_restart；前端改动仍走 Vite 热更新。",
    "vue3-node": "无后端，前端刷新即可；依赖清单改动才需要重启。",
    "static": "静态页，刷新即可；依赖清单改动才需要重启。",
}


def build_preview_prompt(stack: str) -> str:
    """`<preview>` 指引：只在应用解析成功（工具已挂载）时注入，位置在应用契约之后。

    工具描述随每次请求发送、刻意保持一句话，教程写在这里。核心是「不要每改一行就重启」——
    可热更新的场景走成重启，代价是几十秒到几分钟的等待和一次用户确认；
    以及「healthy 只代表 HTTP 可达」，否则模型会把探针 200 当成改动已编译通过。
    次数上限是配置项，提示词只说「有上限」，不写死数字（写死就会与配置漂移）。
    """
    lines = [
        "\n\n<preview>",
        f"当前工作区是可运行的应用工程（技术栈：{stack or '未知'}）。"
        "改完代码要验证，不要仅凭阅读宣称“已完成”。",
        "- 纯前端/后端代码改动通常会自动热更新：先调 preview_status，healthy=true 时让用户刷新页面。",
        "- 改了依赖清单（package.json / requirements.txt / pom.xml）、环境变量或启动配置，"
        "或预览不是 running：调 preview_restart。",
        f"- preview_restart 默认最多等 {DEFAULT_WAIT_SECONDS} 秒；返回 starting 表示仍在后台启动，"
        "用 preview_status / preview_logs 继续观察，不要连续重启。",
        "- healthy 只表示 HTTP 可达，不表示新改动编译通过；"
        "前端编译错误、后端 traceback 看 preview_logs。",
        "- 同一任务内不要反复启动/重启（平台有次数上限）；重启会中断用户正在进行的预览调试，"
        "运行中的重启需要用户确认。",
        "- 不要尝试停止预览、发布、操作数据库或执行任意命令——你没有这些工具。",
    ]
    hint = _STACK_HINTS.get(stack or "")
    if hint:
        lines.append(f"- {hint}")
    lines.append("</preview>")
    return "\n".join(lines)
```

Run: `pytest tests/test_preview_tools.py -v`
Expected: PASS

- [ ] **Step 10: 提交**

```bash
git add app/agent/preview_tools.py tests/test_preview_tools.py
git commit -m "feat(agent): preview_restart 有界等待、进度转发与结果塑形"
```

---

### Task 8: `ToolContext` 扩展与 `execute_tool` 分派

预览工具需要本轮的配置、取消信号、DeployHub 与共享任务表。字段全部带默认值，既有构造点（loop、各测试）不受影响。

**Files:**
- Modify: `app/agent/tools.py`
- Test: `tests/test_preview_tools.py`（追加）

**Interfaces:**
- Consumes: `llm.PREVIEW_TOOL_NAMES`、`preview_tools.run_preview_tool`
- Produces: `ToolContext` 新增字段 `app: dict | None = None`、`cfg: dict = field(default_factory=dict)`、`cancel: asyncio.Event | None = None`、`deploy_hub: Any = None`、`deploy_tasks: dict = field(default_factory=dict)`、`run_state: dict = field(default_factory=dict)`
- Produces: `execute_tool(ctx, name, args, *, confirmed: bool = False) -> dict`

- [ ] **Step 1: 先写失败测试**

在 `tests/test_preview_tools.py` 末尾追加：

```python
# ---------- execute_tool 分派与 ToolContext 契约 ----------

async def test_execute_tool_routes_the_three_preview_names(monkeypatch, tmp_path):
    """分派是惰性的：preview_tools 不 import tools（会成环），tools 在分支里 import 它。"""
    from app.agent import preview_tools as pt
    from app.agent.tools import ToolContext, execute_tool
    calls = []

    async def fake(ctx, name, args, *, confirmed=False):
        calls.append((name, args, confirmed))
        return {"ok": True, "name": name}

    monkeypatch.setattr(pt, "run_preview_tool", fake)
    ctx = ToolContext(root=tmp_path, session_id=1, session_factory=None,
                      send=lambda ev: asyncio.sleep(0))
    for name in ("preview_status", "preview_logs", "preview_restart"):
        assert await execute_tool(ctx, name, {"x": 1}, confirmed=True) == {"ok": True, "name": name}
    assert calls == [("preview_status", {"x": 1}, True), ("preview_logs", {"x": 1}, True),
                     ("preview_restart", {"x": 1}, True)]


async def test_preview_context_fields_default_safely_and_are_not_shared(tmp_path):
    from app.agent.tools import ToolContext
    ctx = ToolContext(root=tmp_path, session_id=1, session_factory=None, send=None)
    assert ctx.app is None and ctx.cfg == {} and ctx.cancel is None
    assert ctx.deploy_hub is None and ctx.deploy_tasks == {} and ctx.run_state == {}
    other = ToolContext(root=tmp_path, session_id=2, session_factory=None, send=None)
    other.run_state["preview_restarts"] = 1
    other.deploy_tasks[9] = "x"
    assert ctx.run_state == {} and ctx.deploy_tasks == {}    # dataclass 可变默认值的经典坑


async def test_no_command_execution_tool_exists(tmp_path):
    """红线：动作面只有三个预览工具，没有任何 shell/命令入口（规格 §12）。"""
    from app.agent.llm import PREVIEW_TOOL_NAMES, TOOLS
    from app.agent.tools import ToolContext, ToolError, execute_tool
    names = {t["function"]["name"] for t in TOOLS} | set(PREVIEW_TOOL_NAMES)
    assert names == {"list_dir", "read_file", "write_file", "edit_file", "ask_user",
                     "todo_write", "memory_write",
                     "preview_status", "preview_logs", "preview_restart"}
    ctx = ToolContext(root=tmp_path, session_id=1, session_factory=None, send=None)
    for forbidden in ("run_shell", "exec", "terminal", "run_command", "preview_stop"):
        with pytest.raises(ToolError):
            await execute_tool(ctx, forbidden, {})
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_preview_tools.py -k "execute_tool or context_fields or command_execution" -v`
Expected: FAIL —— `TypeError: ToolContext.__init__() got an unexpected keyword argument 'app'` / `execute_tool() got an unexpected keyword argument 'confirmed'`

- [ ] **Step 3: 写实现**

`app/agent/tools.py`：首行 import 补 `asyncio`：

```python
import asyncio, dataclasses, difflib, json, os
```

`ToolContext` 替换为：

```python
@dataclasses.dataclass
class ToolContext:
    root: Path
    session_id: int
    session_factory: Any
    send: Callable[[dict], Awaitable[None]]
    skills_root: Path | None = None  # 该用户技能母本根（受控读面）；None = 无技能读面，存量调用不受影响
    # ---- 预览工具（规格 docs/specs/2026-10-29-agent-preview-tools.md §5）----
    # 全部带默认值：非应用工作区与既有构造点（loop 之外的测试）一律不受影响。
    app: dict | None = None          # run 开始时解析出的应用行；None = 预览工具不挂载
    cfg: dict = dataclasses.field(default_factory=dict)          # 本轮配置快照（agent.preview_* 阈值）
    cancel: asyncio.Event | None = None                          # 本轮取消信号：等待循环据此尽快返回
    deploy_hub: Any = None           # DeployHub：订阅 dev_log/dev_status 转成聊天进度
    deploy_tasks: dict = dataclasses.field(default_factory=dict)  # app.state.dev_tasks（与 REST 共用同一张表）
    run_state: dict = dataclasses.field(default_factory=dict)     # 单次 run 的计数（preview_restarts），不落库
```

`execute_tool` 的签名与开头替换为：

```python
async def execute_tool(ctx: ToolContext, name: str, args: dict, *, confirmed: bool = False) -> dict:
    if name in PREVIEW_TOOL_NAMES:
        # 惰性 import：preview_tools 只读 ctx 的字段、刻意不 import 本模块（否则成环）；
        # 预览工具的失败一律是**正常结果**（{"ok": False, "error": ...}），不抛 ToolError——
        # 模型需要读到「Docker 没开」「已有部署任务」这类可行动的原因并向用户解释。
        from app.agent.preview_tools import run_preview_tool
        return await run_preview_tool(ctx, name, args, confirmed=confirmed)
    if name == "list_dir":
```

模块顶部（`from sqlalchemy import text` 之后）加：

```python
from app.agent.llm import PREVIEW_TOOL_NAMES
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_preview_tools.py -v`
Expected: PASS

- [ ] **Step 5: 跑既有工具测试确认零回归**

Run: `pytest tests/test_fs_tools.py tests/test_memory_tool.py tests/test_guard.py tests/test_redact_write_guard.py -v`
Expected: PASS —— 文件工具、记忆工具、deploy 禁区守卫行为不变

- [ ] **Step 6: 提交**

```bash
git add app/agent/tools.py tests/test_preview_tools.py
git commit -m "feat(agent): ToolContext 承载预览上下文，execute_tool 分派 preview_*"
```

---

### Task 9: loop / ws 接线：应用解析、工具挂载、`<preview>` 注入、重启确认

到这里三个工具才真正对模型可见。挂载条件、提示词、确认闸门都在这一个任务里，因为它们共享同一次「run 开始时的应用解析」。

**Files:**
- Modify: `app/agent/loop.py`
- Modify: `app/agent/preview_tools.py`（`need_confirm` / `confirm_summary`）
- Modify: `app/agent/llm.py`（`ScriptedLLM` 记录收到的 tools）
- Modify: `app/api/ws.py`
- Test: `tests/test_agent_loop.py`（追加）

**Interfaces:**
- Consumes: `preview_tools.resolve_session_app` / `build_preview_prompt` / `need_confirm` / `confirm_summary` / `fetch_app`、`llm.PREVIEW_TOOLS`
- Produces: `AgentRunner.__init__(..., pop_pending=None, deploy_hub=None, dev_tasks=None)`
- Produces: `AgentRunner._workspace_of(session_id) -> tuple[int | None, int | None]`、`AgentRunner._resolve_app(session_id) -> tuple[dict | None, str]`
- Produces: `build_system_prompt(..., preview=...)`（Task 5 已加参数，这里开始真正传值）
- Produces: `need_confirm(ctx, app) -> bool`、`confirm_summary(app) -> str`
- Produces: `ScriptedLLM.received_tools: list[list[dict]]`

- [ ] **Step 1: 让测试替身记录收到的 tools**

`app/agent/llm.py` 的 `ScriptedLLM.__init__` 末尾加一行，`chat_stream` 里 `self.received.append(...)` 之后加一行：

```python
        self.received_tools: list[list[dict]] = []   # 每轮实际送模的工具清单（挂载条件的断言靶）
```

```python
        self.received_tools.append(list(tools))
```

docstring 补一句：`received_tools 与 received 同轮次对齐，用于断言「这一轮模型看得见哪些工具」。`

- [ ] **Step 2: 先写失败测试（挂载条件与提示词）**

在 `tests/test_agent_loop.py` 末尾追加：

```python
# ---- 预览工具接线（规格 docs/specs/2026-10-29-agent-preview-tools.md §5.1 / §5.6 / §8.1）----

from app.deploy.hub import DeployHub


async def mk_app_session(test_engine, tmp_path, *, stack="vue3-fastapi", status="stopped",
                         slug="pvloop", dev_port=8271, prod_port=8270):
    """workspace + app_project + chat_session 三行一起建，返回 (sid, app_id, sf)。

    owner 一律留 NULL：与 test_agent_loop.py 既有会话同形（WS 所有权校验不在本文件的靶心上），
    `resolve_session_app` 传 owner=None 时不加过滤，正好命中。
    """
    sf = make_session_factory(test_engine)
    async with sf() as s:
        wid = (await s.execute(sql(
            "INSERT INTO workspace (name, root_path) VALUES ('pv', :p) RETURNING id"),
            {"p": str(tmp_path)})).scalar()
        aid = (await s.execute(sql(
            "INSERT INTO app_project (workspace_id, name, slug, scale, stack, prod_port, dev_port) "
            "VALUES (:w, '预览应用', :sl, 'small', :st, :pp, :dp) RETURNING id"),
            {"w": wid, "sl": slug, "st": stack, "pp": prod_port, "dp": dev_port})).scalar()
        sid = (await s.execute(sql(
            "INSERT INTO chat_session (workspace_id) VALUES (:w) RETURNING id"),
            {"w": wid})).scalar()
        if status != "stopped":
            await s.execute(sql("UPDATE app_project SET status_dev=:st WHERE id=:i"),
                            {"st": status, "i": aid})
        await s.commit()
    return sid, aid, sf


def preview_cfg(**over):
    async def _p():
        base = {"agent.write_confirm": False, "agent.ask_timeout_seconds": 0,
                "agent.max_tool_rounds": 10, "agent.history_window": 40}
        base.update(over)
        return base
    return _p


def mk_runner(sf, llm, rec, *, confirm=None, hub=None, tasks=None, cfg=None):
    """带预览接线的 runner：hub/tasks 缺省 None（= 不接线），cfg 缺省平台默认那几键。"""
    return AgentRunner(sf, llm, cfg or preview_cfg(), rec,
                       lambda *a, **k: asyncio.sleep(0, None),
                       confirm or (lambda *a, **k: True),
                       deploy_hub=hub, dev_tasks=tasks)


def tool_names(llm, round_index=0):
    return [t["function"]["name"] for t in llm.received_tools[round_index]]


@pytest.mark.asyncio
async def test_preview_tools_mounted_only_for_app_workspace(test_engine, tmp_path):
    sid, aid, sf = await mk_app_session(test_engine, tmp_path)
    llm = ScriptedLLM([[delta("好"), *done("好了")]])
    await mk_runner(sf, llm, Recorder()).run(sid, "起个预览", asyncio.Event())
    assert tool_names(llm)[-3:] == ["preview_status", "preview_logs", "preview_restart"]
    assert "<preview>" in llm.received[0][0]["content"]
    assert "vue3-fastapi" in llm.received[0][0]["content"]


@pytest.mark.asyncio
async def test_no_preview_tools_without_app_row(test_engine, tmp_path):
    """US-01：非应用工作区里模型根本看不到这三个工具（不是置灰，是不存在）。"""
    sid = await mk_session(test_engine, tmp_path)
    sf = make_session_factory(test_engine)
    llm = ScriptedLLM([[delta("好"), *done("好了")]])
    await mk_runner(sf, llm, Recorder()).run(sid, "起个预览", asyncio.Event())
    names = tool_names(llm)
    assert not any(n.startswith("preview_") for n in names)
    assert "<preview>" not in llm.received[0][0]["content"]


@pytest.mark.asyncio
async def test_duplicate_workspace_binding_fails_closed_with_note(test_engine, tmp_path):
    """US-01/§5.1：一个工作区绑了两个应用 → 不挂载工具，但 system 里给模型一句能转述的原因。"""
    sid, aid, sf = await mk_app_session(test_engine, tmp_path)
    async with sf() as s:
        wid = (await s.execute(sql("SELECT workspace_id FROM app_project WHERE id=:i"),
                               {"i": aid})).scalar()
        await s.execute(sql(
            "INSERT INTO app_project (workspace_id, name, slug, scale, stack, prod_port, dev_port) "
            "VALUES (:w, '重复应用', 'pvloop2', 'small', 'vue3-fastapi', 8273, 8272)"), {"w": wid})
        await s.commit()
    llm = ScriptedLLM([[delta("好"), *done("好了")]])
    await mk_runner(sf, llm, Recorder()).run(sid, "起个预览", asyncio.Event())
    assert not any(n.startswith("preview_") for n in tool_names(llm))
    assert "绑定了多个应用" in llm.received[0][0]["content"]


@pytest.mark.asyncio
async def test_preview_prompt_follows_the_stack(test_engine, tmp_path):
    sid, aid, sf = await mk_app_session(test_engine, tmp_path, stack="vue3-springboot",
                                        slug="pvloopj", dev_port=8275, prod_port=8274)
    llm = ScriptedLLM([[delta("好"), *done("好了")]])
    await mk_runner(sf, llm, Recorder()).run(sid, "改个后端", asyncio.Event())
    c = llm.received[0][0]["content"]
    assert "必须 preview_restart" in c and "uvicorn" not in c     # 按栈差异化，不串味
    assert c.rstrip().endswith("</preview>")                      # 块在 system 尾部（契约/技能之后）
```

（`<preview>` 与 `【应用契约】` 的先后顺序由 `tests/test_prompts.py::test_preview_block_appended_after_app_contract` 钉住，这里不重复断言。）

- [ ] **Step 3: 跑测试确认失败**

Run: `pytest tests/test_agent_loop.py -k preview -v`
Expected: FAIL —— `AgentRunner.__init__() got an unexpected keyword argument 'deploy_hub'`

- [ ] **Step 4: 写实现（loop 的解析、挂载与注入）**

`app/agent/loop.py` 顶部 import 区加：

```python
import logging
from app.agent.preview_tools import (build_preview_prompt, confirm_summary, fetch_app,
                                     need_confirm, resolve_session_app)
```

模块级（`def _normalize_questions` 之前）加：

```python
logger = logging.getLogger(__name__)
```

`AgentRunner.__init__` 签名与末尾改为：

```python
    def __init__(self, session_factory, llm, config_provider, send, ask, confirm, pop_pending=None,
                 deploy_hub=None, dev_tasks=None):
```

```python
        # 预览工具（规格 2026-10-29 §5.2）：DeployHub 与 REST 共用的部署任务表。
        # 缺省 None = 不接线（既有调用方与单测保持原签名可用），此时工具仍可只读工作。
        self.deploy_hub = deploy_hub
        self.dev_tasks = dev_tasks if dev_tasks is not None else {}
```

在 `_skills_root_of` 之后加两个方法：

```python
    async def _workspace_of(self, session_id: int) -> tuple[int | None, int | None]:
        """会话所属工作区与其归属账号：预览工具的身份解析只认这两个值（模型给不出第三个）。"""
        async with self.sf() as s:
            row = (await s.execute(text(
                "SELECT cs.workspace_id, w.owner_user_id FROM chat_session cs "
                "JOIN workspace w ON w.id = cs.workspace_id WHERE cs.id = :i"),
                {"i": session_id})).first()
        return (row[0], row[1]) if row else (None, None)

    async def _resolve_app(self, session_id: int) -> tuple[dict | None, str]:
        """run 开始时解析一次应用身份，返回 `(app, note)`。

        解析不到不是错误（普通工作区就没有应用）；解析出错也只记 warning——预览是增量能力，
        不该让整轮对话失败。note 非空时是一句要注入 system 的说明（工作区绑定了多个应用：
        fail closed，但要让模型能向用户解释为什么用不了）。
        """
        wid, uid = await self._workspace_of(session_id)
        app, note = await resolve_session_app(self.sf, wid, owner_user_id=uid)
        if note:
            logger.warning("会话 %s 的预览工具未挂载：%s", session_id, note)
        return app, note
```

`_build_messages` 的签名与 system 构建改为：

```python
    async def _build_messages(self, session_id: int, root: Path, cfg: dict, send,
                              user_text: str = "", preview: str | None = None) -> list[dict]:
```

```python
        system_text = build_system_prompt(str(root), datetime.date.today().isoformat(),
                                          app_contract=build_app_contract(root), skills=skills,
                                          preview=preview)
```

`_inner` 里 `root = await self._root_of(session_id)` 之后加：

```python
        # 预览工具（规格 2026-10-29 §5.1）：应用身份只在服务端解析，工具列表随之挂载。
        # 非应用工作区一个 preview_* 都不给模型看——不给它一个「调了才发现不能用」的假工具。
        app, preview_note = await self._resolve_app(session_id)
        preview_prompt = preview_note or (build_preview_prompt(app["stack"]) if app else None)
        tools = _TOOLS + PREVIEW_TOOLS if app else _TOOLS
```

`messages = await self._build_messages(...)` 改为传 `preview=preview_prompt`；`ctx = ToolContext(...)` 改为：

```python
        ctx = ToolContext(root=root, session_id=session_id, session_factory=self.sf, send=self.send,
                          skills_root=await self._skills_root_of(session_id),
                          app=app, cfg=cfg, cancel=cancel, deploy_hub=self.deploy_hub,
                          deploy_tasks=self.dev_tasks, run_state={})
```

`async for ev in llm.chat_stream(messages, _TOOLS):` 改为 `async for ev in llm.chat_stream(messages, tools):`

文件末尾的 import 改为：

```python
from app.agent.llm import PREVIEW_TOOLS, TOOLS as _TOOLS
```

- [ ] **Step 5: 跑测试确认通过**

Run: `pytest tests/test_agent_loop.py -v`
Expected: PASS —— 新增 4 条绿，既有用例零回归

- [ ] **Step 6: 先写失败测试（确认分支、进度顺序、ctx 注入）**

继续在 `tests/test_agent_loop.py` 追加：

```python
@pytest.fixture
def pv_seams(monkeypatch):
    """预览重启链路的 Docker 侧替身：预检放行、探针 200、起栈像真引擎那样把终态写进库。"""
    from app.deploy import engine as eng

    async def preflight_ok():
        return None

    async def probe_ok(app, timeout=2):
        return True, ""

    async def start_ok(sf_, app, notify=None):
        await eng._set_status(sf_, app["id"], "running")
        if notify:
            notify(app["id"], {"app_id": app["id"], "type": "dev_log",
                               "line": "[zerocode] ✓ 预览已就绪"})
        return {"status": "running"}

    monkeypatch.setattr(eng, "preflight_docker", preflight_ok)
    monkeypatch.setattr(eng, "probe_dev", probe_ok)
    monkeypatch.setattr(eng, "start_dev", start_ok)
    monkeypatch.setattr(eng, "restart_dev", start_ok)
    return eng


@pytest.mark.asyncio
async def test_running_restart_asks_and_rejection_changes_nothing(test_engine, tmp_path, pv_seams):
    """US-21：running 时先确认；拒绝则一次启停都不发生，模型收到「用户拒绝重启预览」。"""
    sid, aid, sf = await mk_app_session(test_engine, tmp_path, status="running", slug="pvconf",
                                        dev_port=8277, prod_port=8276)
    rec, tasks = Recorder(), {}

    async def reject(session_id, payload):
        assert payload["tool"] == "preview_restart" and "预览应用" in payload["summary"]
        return False

    turns = [done(None, [tool_call("c1", "preview_restart")]), done("好，那不重启")]
    await mk_runner(sf, ScriptedLLM(turns), rec, confirm=reject, hub=DeployHub(),
                    tasks=tasks).run(sid, "重启一下", asyncio.Event())
    tr = next(e for e in rec.events if e["type"] == "tool_result")
    assert tr["result"]["ok"] is False and "用户拒绝重启预览" in tr["result"]["error"]
    cr = next(e for e in rec.events if e["type"] == "confirm_request")
    assert cr["tool"] == "preview_restart" and "正在运行" in cr["summary"]
    assert tasks == {}                                     # 拒绝后没有任务被登记


@pytest.mark.asyncio
async def test_confirmed_restart_uses_the_shared_task_table(test_engine, tmp_path, pv_seams):
    """US-24：Agent 起的栈登记在 `app.state.dev_tasks` 里——与用户手点「启动预览」同一张表。"""
    sid, aid, sf = await mk_app_session(test_engine, tmp_path, status="running", slug="pvok",
                                        dev_port=8279, prod_port=8278)
    rec, tasks = Recorder(), {}
    turns = [done(None, [tool_call("c1", "preview_restart", wait_seconds=5)]), done("已重启")]
    await mk_runner(sf, ScriptedLLM(turns), rec, hub=DeployHub(), tasks=tasks).run(
        sid, "重启", asyncio.Event())
    tr = next(e for e in rec.events if e["type"] == "tool_result")
    assert tr["result"]["ok"] is True and tr["result"]["status"] == "running"
    assert tr["result"]["restarts_left"] == 2
    assert aid in tasks and tasks[aid].done()


@pytest.mark.asyncio
async def test_confirm_switch_off_never_opens_a_dialog(test_engine, tmp_path, pv_seams):
    sid, aid, sf = await mk_app_session(test_engine, tmp_path, status="running", slug="pvnc",
                                        dev_port=8281, prod_port=8280)
    rec, asked = Recorder(), []

    async def never(session_id, payload):
        asked.append(payload)
        return True

    turns = [done(None, [tool_call("c1", "preview_restart", wait_seconds=5)]), done("好")]
    await mk_runner(sf, ScriptedLLM(turns), rec, confirm=never, hub=DeployHub(), tasks={},
                    cfg=preview_cfg(**{"agent.preview_confirm_running": False})).run(
        sid, "重启", asyncio.Event())
    tr = next(e for e in rec.events if e["type"] == "tool_result")
    assert tr["result"]["ok"] is True and asked == []
    assert not any(e["type"] == "confirm_request" for e in rec.events)


@pytest.mark.asyncio
async def test_exhausted_cap_never_opens_a_dialog(test_engine, tmp_path, pv_seams):
    """§5.6/§5.7：上限是**单次 run** 的——同一轮里连着两次重启，第二次既不弹确认也不起任务。

    先问一次确认、再告诉他「次数用尽」是纯打扰，所以 `need_confirm` 里也带了次数判据。
    """
    sid, aid, sf = await mk_app_session(test_engine, tmp_path, status="running", slug="pvcap",
                                        dev_port=8289, prod_port=8288)
    rec, asked = Recorder(), []

    async def always(session_id, payload):
        asked.append(payload)
        return True

    turns = [done(None, [tool_call("c1", "preview_restart", wait_seconds=5)]),
             done(None, [tool_call("c2", "preview_restart", wait_seconds=5)]),
             done("好")]
    await mk_runner(sf, ScriptedLLM(turns), rec, confirm=always, hub=DeployHub(), tasks={},
                    cfg=preview_cfg(**{"agent.preview_max_restarts": 1})).run(
        sid, "重启", asyncio.Event())
    results = [e["result"] for e in rec.events if e["type"] == "tool_result"]
    assert results[0]["ok"] is True and results[0]["restarts_left"] == 0
    assert results[1]["ok"] is False and "已启动/重启预览 1 次" in results[1]["error"]
    assert len(asked) == 1
    assert len([e for e in rec.events if e["type"] == "confirm_request"]) == 1


@pytest.mark.asyncio
async def test_stopped_start_never_opens_a_dialog(test_engine, tmp_path, pv_seams):
    """US-21 的另一半：冷启动不中断任何人，不该弹确认。"""
    sid, aid, sf = await mk_app_session(test_engine, tmp_path, slug="pvcold",
                                        dev_port=8283, prod_port=8282)
    rec = Recorder()
    turns = [done(None, [tool_call("c1", "preview_restart", wait_seconds=5)]), done("好了")]
    await mk_runner(sf, ScriptedLLM(turns), rec, hub=DeployHub(), tasks={}).run(
        sid, "起预览", asyncio.Event())
    assert not any(e["type"] == "confirm_request" for e in rec.events)
    tr = next(e for e in rec.events if e["type"] == "tool_result")
    assert tr["result"]["ok"] is True


@pytest.mark.asyncio
async def test_tool_progress_arrives_before_tool_result(test_engine, tmp_path, pv_seams):
    """US-17/§8.1：进度是临时事件，但必须早于同一工具的结果到达，前端才画得出「正在重启」。"""
    sid, aid, sf = await mk_app_session(test_engine, tmp_path, slug="pvprog",
                                        dev_port=8285, prod_port=8284)
    rec = Recorder()
    turns = [done(None, [tool_call("c1", "preview_restart", wait_seconds=5)]), done("好了")]
    await mk_runner(sf, ScriptedLLM(turns), rec, hub=DeployHub(), tasks={}).run(
        sid, "起预览", asyncio.Event())
    types = [e["type"] for e in rec.events]
    assert "tool_progress" in types
    assert types.index("tool_progress") < types.index("tool_result")
    prog = next(e for e in rec.events if e["type"] == "tool_progress")
    assert prog["name"] == "preview_restart" and prog["line"].startswith("[zerocode]")


@pytest.mark.asyncio
async def test_tool_context_carries_preview_wiring(test_engine, tmp_path, pv_seams, monkeypatch):
    """ctx 注入是接线正确性的唯一靶心：应用行、配置、取消信号、hub、任务表都要到位。"""
    from app.agent import preview_tools as pt
    sid, aid, sf = await mk_app_session(test_engine, tmp_path, slug="pvctx",
                                        dev_port=8287, prod_port=8286)
    captured, hub, tasks = {}, DeployHub(), {}

    async def fake(ctx, name, args, *, confirmed=False):
        captured.update({"app_id": ctx.app["id"], "cfg": dict(ctx.cfg), "cancel": ctx.cancel,
                         "hub": ctx.deploy_hub, "tasks": ctx.deploy_tasks,
                         "run_state": ctx.run_state, "confirmed": confirmed, "name": name})
        return {"ok": True}

    monkeypatch.setattr(pt, "run_preview_tool", fake)
    cancel = asyncio.Event()
    turns = [done(None, [tool_call("c1", "preview_status")]), done("好")]
    runner = mk_runner(sf, ScriptedLLM(turns), Recorder(), hub=hub, tasks=tasks,
                       cfg=preview_cfg(**{"agent.preview_wait_seconds": 30}))
    await runner.run(sid, "看看状态", cancel)
    assert captured["app_id"] == aid and captured["name"] == "preview_status"
    assert captured["hub"] is hub and captured["tasks"] is tasks and captured["cancel"] is cancel
    assert captured["cfg"]["agent.preview_wait_seconds"] == 30
    assert captured["run_state"] == {} and captured["confirmed"] is False
```

- [ ] **Step 7: 跑测试确认失败**

Run: `pytest tests/test_agent_loop.py -k "restart_asks or confirmed_restart or confirm_switch or exhausted_cap or stopped_start or progress_arrives or context_carries" -v`
Expected: FAIL —— 没有 `confirm_request`、没有 `tool_progress`（确认分支与接线尚未落地）

- [ ] **Step 8: 写实现（确认判据与摘要）**

`app/agent/preview_tools.py` 在 `confirm_required` 之后插入：

```python
def confirm_summary(app: dict) -> str:
    """确认卡的人话摘要：说清「哪个应用、正在运行、重启会中断什么」，不给模型看参数 JSON。"""
    name = app.get("name") or app.get("slug") or f"应用 #{app.get('id')}"
    return f"应用「{name}」的 dev 预览正在运行，重启会中断正在进行的调试。确定重启？"


def need_confirm(ctx, app: dict | None) -> bool:
    """这次 preview_restart 要不要先问用户（§5.6）。

    四个条件缺一不可，其中两个纯粹是「别白问一次」：已有部署任务时 handler 会直接回并发提示，
    次数用尽时会直接回上限提示——先让用户点一次确认、再告诉他做不了，是纯打扰。
    """
    if not app or (app.get("status_dev") or "stopped") != "running":
        return False
    if not confirm_required(ctx):
        return False
    if pending_task(ctx, app["id"]) is not None:
        return False
    return _restarts_left(ctx) != 0
```

- [ ] **Step 9: 写实现（loop 的确认分支）**

`app/agent/loop.py` 的 `_dispatch` 里，写入确认那段与 `try:` 之间改为：

```python
        confirmed = False
        if name in ("write_file", "edit_file") and cfg.get("agent.write_confirm"):
            await self.send({"type": "confirm_request", "tool": name, "args": args})
            ok = await self.confirm(session_id, {"tool": name, "args": args})
            if not ok:
                return {"ok": False, "error": "用户拒绝该修改"}
        elif name == "preview_restart":
            # 中断运行中的预览要用户点头（§5.6）。AgentRunner 仍是 confirm_request 的唯一发出方；
            # 事件多带一个 summary，前端据此把「写入确认」换成「重启预览」的人话文案。
            # 判据读的是**此刻**的库状态：确认期间状态可能已经变了，handler 侧还有一道兜底。
            target = await fetch_app(ctx)
            if need_confirm(ctx, target):
                summary = confirm_summary(target)
                await self.send({"type": "confirm_request", "tool": name, "args": args,
                                 "summary": summary})
                approved = await self.confirm(session_id, {"tool": name, "args": args,
                                                           "summary": summary})
                if not approved:
                    return {"ok": False, "error": "用户拒绝重启预览"}
                confirmed = True
        try:
            return await execute_tool(ctx, name, args, confirmed=confirmed)
```

- [ ] **Step 10: 写实现（ws 接线）**

`app/api/ws.py` 里构造 runner 的那处改为：

```python
                        runner = AgentRunner(app.state.session_factory, llm,
                                             config_getter, send, hub.ask, hub.confirm,
                                             hub.pop_pending,
                                             deploy_hub=app.state.deploy_hub,
                                             dev_tasks=app.state.dev_tasks)
```

模块 docstring 的下行事件清单里补 `tool_progress`（协议文档与实现同步）：

```
下行：token / tool_call / tool_result / tool_progress / ask_user / confirm_request /
message_done / error / status / file_change / compacting / compaction_done / busy /
steering_done（由 runner 与 hub 经 emit 下发）。tool_progress 是预览工具的**临时**进度，
不落库、刷新即消失；error 与 status 是本轮终态；busy 是执行中被拒的非终态提示
（不得据此复位前端执行态，否则「停止」按钮消失 → 发不出也停不掉的死循环）。
```

`config_getter` 的返回处补四个预览默认键（`agent.*` 是平台级键，读不到就用代码默认值；
刻意不写迁移、不写 `app_config` 种子行）：

```python
    async def config_getter():
        # 用户视图（Ticket 007）：会话经 workspace 取 owner，llm.* 读自己的/回落全局；
        # agent.* 等平台级键所有人共享，owner 为 NULL 时等价纯全局视图
        async with app.state.session_factory() as s:
            uid = (await s.execute(_sql(
                "SELECT w.owner_user_id FROM chat_session cs "
                "JOIN workspace w ON w.id = cs.workspace_id WHERE cs.id=:i"),
                {"i": session_id})).scalar()
            cfg = await config_service.get_all(s, owner_user_id=uid)
        # 预览工具的平台级默认值（规格 2026-10-29 §5.9）：与 agent.max_tool_rounds 同一读法——
        # 默认值写在代码里，库里存了就以库为准。preview_confirm_running 默认 True 走**字面量**，
        # 不能用 `cfg.get(key, default)`：库里的 JSON false 会被 `or` 吃掉，等于关不掉确认。
        cfg.setdefault("agent.preview_wait_seconds", 240)
        cfg.setdefault("agent.preview_log_lines", 80)
        cfg.setdefault("agent.preview_max_restarts", 3)
        cfg.setdefault("agent.preview_confirm_running", True)
        return cfg
```

- [ ] **Step 11: 跑测试确认通过**

Run: `pytest tests/test_agent_loop.py tests/test_preview_tools.py tests/test_prompts.py -v`
Expected: PASS

- [ ] **Step 12: 跑 WS 与工具链路回归**

Run: `pytest tests/test_ws.py tests/test_fs_tools.py tests/test_memory_tool.py tests/test_dev_logs.py tests/test_apps_dev_api.py -v`
Expected: PASS —— 既有 WS 事件协议、写入确认、文件工具、日志端点、部署 REST 全部不变

- [ ] **Step 13: 提交**

```bash
git add app/agent/loop.py app/agent/preview_tools.py app/agent/llm.py app/api/ws.py tests/test_agent_loop.py
git commit -m "feat(agent): 按工作区挂载预览工具，重启运行中预览先确认"
```

---

### Task 10: 前端 store：`tool_progress` 与确认卡的 `summary`

进度是**临时事件**（不落库、刷新即消失），所以它只能活在 store 里，不能进 `messages`。确认卡的 `summary` 是可选字段：没有它时 `pendingConfirm` 的对象形状必须一字不变，否则既有 `toEqual` 断言会红。

**Files:**
- Modify: `frontend/src/stores/chat.js`
- Test: `frontend/src/stores/chat.test.js`（追加）

**Interfaces:**
- Consumes: 后端事件 `{"type":"tool_progress","name","line"}`、`{"type":"confirm_request","tool","args","summary"?}`
- Produces: `store.toolProgress: { name, line } | null`
- Produces: `store.pendingConfirm: { tool, args, summary? }`

- [ ] **Step 1: 先写失败测试**

`frontend/src/stores/chat.test.js` 的 `mk()` 里补一个字段：

```js
    toolProgress: null, pendingConfirm: null,
```

文件末尾追加：

```js
// 预览工具（规格 2026-10-29-agent-preview-tools §8）：进度是临时事件，只活在 store 里
describe('tool_progress 与确认卡 summary', () => {
  it('tool_progress 写入 store.toolProgress，不进消息流', () => {
    const s = mk()
    applyEvent(s, { type: 'tool_progress', name: 'preview_restart', line: '[zerocode] → 迁移中' })
    expect(s.toolProgress).toEqual({ name: 'preview_restart', line: '[zerocode] → 迁移中' })
    expect(s.messages.length).toBe(0)          // 临时事件不落消息流（刷新即消失）
  })

  it('同名 tool_result 到达时清空进度', () => {
    const s = mk()
    applyEvent(s, { type: 'tool_call', name: 'preview_restart', args: {} })
    applyEvent(s, { type: 'tool_progress', name: 'preview_restart', line: 'x' })
    applyEvent(s, { type: 'tool_result', name: 'preview_restart', result: { ok: true } })
    expect(s.toolProgress).toBe(null)
    expect(s.messages.length).toBe(2)
  })

  it('别的工具的结果不误清当前进度', () => {
    const s = mk()
    applyEvent(s, { type: 'tool_progress', name: 'preview_restart', line: 'x' })
    applyEvent(s, { type: 'tool_result', name: 'read_file', result: { ok: true } })
    expect(s.toolProgress.line).toBe('x')
  })

  it('message_done / status 终态 / error 都清空进度', () => {
    for (const ev of [{ type: 'message_done', content: '好了' },
                      { type: 'status', status: 'idle' },
                      { type: 'error', message: '炸了' }]) {
      const s = mk()
      applyEvent(s, { type: 'tool_progress', name: 'preview_restart', line: 'x' })
      applyEvent(s, ev)
      expect(s.toolProgress).toBe(null)
    }
  })

  it('status(running) 是重连快照，不清进度也不复位执行态', () => {
    const s = mk()
    applyEvent(s, { type: 'tool_progress', name: 'preview_restart', line: 'x' })
    applyEvent(s, { type: 'status', status: 'running' })
    expect(s.toolProgress.line).toBe('x')
  })

  it('confirm_request 无 summary 时对象形状一字不变', () => {
    const s = mk()
    applyEvent(s, { type: 'confirm_request', tool: 'write_file', args: { path: 'a.txt' } })
    expect(s.pendingConfirm).toEqual({ tool: 'write_file', args: { path: 'a.txt' } })
  })

  it('confirm_request 带 summary 时保存', () => {
    const s = mk()
    applyEvent(s, {
      type: 'confirm_request', tool: 'preview_restart', args: { wait_seconds: 240 },
      summary: '应用「演示应用」的 dev 预览正在运行，重启会中断正在进行的调试。确定重启？',
    })
    expect(s.pendingConfirm.summary).toContain('正在运行')
    expect(s.pendingConfirm.tool).toBe('preview_restart')
  })

  it('cancel 顺手清掉进度', () => {
    setActivePinia(createPinia())
    const s = useChatStore()
    s._ws = { readyState: 1, send: vi.fn() }
    applyEvent(s, { type: 'tool_progress', name: 'preview_restart', line: 'x' })
    s.cancel()
    expect(s.toolProgress).toBe(null)
  })
})
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npm run test -- --run src/stores/chat.test.js`
Expected: FAIL —— `toolProgress` 始终为 `null`（`applyEvent` 还没有这个 case）

- [ ] **Step 3: 写实现**

`frontend/src/stores/chat.js`：

① 顶部事件清单注释补一行（保持文档与协议同步）：

```js
// / tool_progress(预览工具的临时进度，不落库不进气泡，收到结果/终态即清)
```

② `applyEvent` 的 `case 'tool_result'` 改为：

```js
    case 'tool_result':
      store.messages.push({ role: 'tool', name: ev.name, result: ev.result })
      // 进度只属于「正在跑的那个工具」：同名结果到达即收口，别的工具的结果不误清
      if (!store.toolProgress || store.toolProgress.name === ev.name) store.toolProgress = null
      break
    case 'tool_progress': // 预览工具的临时进度（不落库，刷新即消失；最终结论看 tool_result）
      store.toolProgress = { name: ev.name, line: ev.line }
      break
```

③ `case 'confirm_request'` 改为：

```js
    case 'confirm_request':
      // summary 是可选的人话摘要（预览重启用）：没有它时对象形状必须一字不变，
      // 既有写入确认的断言与渲染都不受影响
      store.pendingConfirm = ev.summary
        ? { tool: ev.tool, args: ev.args, summary: ev.summary }
        : { tool: ev.tool, args: ev.args }
      break
```

④ `case 'message_done'` 的复位段里加 `store.toolProgress = null`；`case 'error'` 与 `case 'status'`（非 running 分支）的复位段里各加一行 `store.toolProgress = null`。

⑤ state 里 `pendingConfirm: null,` 之后加：

```js
    toolProgress: null, // 预览工具的临时进度 {name, line}：不落库、不进消息流，终态即清
```

⑥ `openSession` 的复位段（`this.pendingConfirm = null` 附近）加 `this.toolProgress = null`；
`cancel()` 里 `this.pendingConfirm = null` 之后加 `this.toolProgress = null`。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd frontend && npm run test -- --run src/stores/chat.test.js`
Expected: PASS —— 新用例全绿，既有 `pendingConfirm` / busy / steering 用例零回归

- [ ] **Step 5: 提交**

```bash
git add frontend/src/stores/chat.js frontend/src/stores/chat.test.js
git commit -m "feat(chat): store 承接预览进度事件与确认卡摘要"
```

---

### Task 11: `PreviewCard.vue` 与消息流路由、确认卡文案

三个预览工具共用一张卡：状态徽标 + 地址链接 + 可折叠日志 + 错误/提示。地址只用后端回的 `url`，前端一个字符都不拼。

**Files:**
- Create: `frontend/src/components/PreviewCard.vue`
- Create: `frontend/src/components/PreviewCard.test.js`
- Modify: `frontend/src/components/ChatMessages.vue`
- Test: `frontend/src/components/ChatMessages.test.js`（追加）

**Interfaces:**
- Consumes: `msg`（实时 `{name, args}` / `{name, result}`，历史 `{tool_name, tool_result}`）、`progress`（`{name, line} | null`）、`store.toolProgress`、`store.pendingConfirm.summary`
- Produces: `PreviewCard` props `{ msg: Object, progress: Object | null }`
- Produces: CSS 类 `.zc-preview`、`.zc-preview-head`、`.zc-preview-detail`、`.zc-preview-link`、`.zc-preview-logs`、`.zc-preview-note`，以及状态修饰类 `.is-ok` / `.is-warn` / `.is-error` / `.is-muted` / `.is-info`

- [ ] **Step 1: 先写失败测试（PreviewCard）**

新建 `frontend/src/components/PreviewCard.test.js`：

```js
// @vitest-environment happy-dom
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { h } from 'vue'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import PreviewCard from './PreviewCard.vue'

const ButtonStub = {
  name: 'a-button',
  props: ['type', 'size'],
  render() {
    return h('button', { class: 'stub-button', onClick: () => this.$emit('click') },
      this.$slots.default ? this.$slots.default() : [])
  },
}

function mountCard(msg, progress = null) {
  setActivePinia(createPinia())
  return mount(PreviewCard, {
    props: { msg, progress },
    global: { stubs: { 'a-button': ButtonStub } },
  })
}

describe('PreviewCard 状态（规格 §8.2）', () => {
  it('进行中：蓝色读数 + 最近一条步骤行', () => {
    const w = mountCard({ role: 'tool', name: 'preview_restart', args: { wait_seconds: 240 } },
      { name: 'preview_restart', line: '[zerocode] → 执行 sqlchange 迁移' })
    expect(w.find('.zc-preview').classes()).toContain('is-info')
    expect(w.text()).toContain('正在重启预览')
    expect(w.text()).toContain('执行 sqlchange 迁移')
  })

  it('就绪：绿色 + 打开预览链接（href 逐字来自后端）', () => {
    const w = mountCard({
      role: 'tool', name: 'preview_restart',
      result: { ok: true, status: 'running', healthy: true, probe: '',
                url: 'http://10.0.0.5:8213', elapsed_s: 34.2, restarts_left: 2 },
    })
    expect(w.find('.zc-preview').classes()).toContain('is-ok')
    expect(w.text()).toContain('预览已就绪')
    const link = w.find('.zc-preview-link')
    expect(link.attributes('href')).toBe('http://10.0.0.5:8213')
    expect(link.attributes('target')).toBe('_blank')
    expect(link.attributes('rel')).toBe('noopener')
  })

  it('未就绪：橙色 + probe 判定，不给链接', () => {
    const w = mountCard({
      role: 'tool', name: 'preview_status',
      result: { ok: true, status: 'running', healthy: false, probe: '/ → 403',
                url: 'http://10.0.0.5:8213', hint: '端口探测未通过' },
    })
    expect(w.find('.zc-preview').classes()).toContain('is-warn')
    expect(w.text()).toContain('预览未就绪')
    expect(w.text()).toContain('/ → 403')
    expect(w.find('.zc-preview-link').exists()).toBe(false)   // healthy=false 不给「打开」
  })

  it('等待超时：橙色「仍在启动」+ hint + 最近日志', () => {
    const w = mountCard({
      role: 'tool', name: 'preview_restart',
      result: { ok: false, status: 'starting', error: '', elapsed_s: 240,
                hint: '预览仍在启动，用 preview_status / preview_logs 继续观察；不要连续重启',
                logs: ['10:00:01 [平台] [zerocode] → docker compose up -d 启动 dev 栈'],
                restarts_left: 2 },
    })
    expect(w.find('.zc-preview').classes()).toContain('is-warn')
    expect(w.text()).toContain('仍在启动')
    expect(w.text()).toContain('不要连续重启')
    expect(w.find('.zc-preview-logs').exists()).toBe(false)   // 日志默认折叠
    expect(w.find('.zc-preview-toggle').text()).toContain('查看日志')
  })

  it('取消等待：灰色 + hint', () => {
    const w = mountCard({
      role: 'tool', name: 'preview_restart',
      result: { ok: true, cancelled: true, status: 'starting', elapsed_s: 42,
                hint: '用户已停止本轮；预览仍在后台继续启动' },
    })
    expect(w.find('.zc-preview').classes()).toContain('is-muted')
    expect(w.text()).toContain('已停止等待')
    expect(w.text()).toContain('后台继续启动')
  })

  it('失败：红色 + 错误原文 + 可折叠日志', async () => {
    const w = mountCard({
      role: 'tool', name: 'preview_restart',
      result: { ok: false, status: 'error', error: 'dev 迁移失败（sqlrunner 退出码 1）',
                logs: ['10:00:03 [平台] [zerocode] ✗ 迁移失败，dev 栈不启动'], restarts_left: 2 },
    })
    expect(w.find('.zc-preview').classes()).toContain('is-error')
    expect(w.text()).toContain('预览操作失败')
    expect(w.text()).toContain('sqlrunner 退出码 1')
    expect(w.find('.zc-preview-logs').exists()).toBe(false)
    await w.find('.zc-preview-toggle').trigger('click')
    expect(w.find('.zc-preview-logs').text()).toContain('迁移失败')
  })

  it('日志工具：紧凑日志块 + 遮断计数 + note', async () => {
    const w = mountCard({
      role: 'tool', name: 'preview_logs',
      result: { ok: true, status: 'error', url: '', truncated: true, redacted: 2,
                note: '容器日志暂不可用：Docker 未启动',
                lines: ['10:00:01 [平台] [zerocode] ✗ 启动预览失败',
                        '10:00:02 [backend] <redacted:db_password>'] },
    })
    expect(w.text()).toContain('预览日志')
    expect(w.text()).toContain('已遮断 2 处凭据')
    expect(w.text()).toContain('Docker 未启动')
    await w.find('.zc-preview-toggle').trigger('click')
    expect(w.findAll('.zc-preview-logs li').length).toBe(2)
  })

  it('剩余次数 <=1 时给预警', () => {
    const w = mountCard({
      role: 'tool', name: 'preview_restart',
      result: { ok: true, status: 'running', healthy: true, url: 'http://h:1', restarts_left: 1 },
    })
    expect(w.text()).toContain('本次任务还可重启 1 次')
  })

  it('历史消息（只有 tool_name / tool_result，没有 args）也能渲染', () => {
    const w = mountCard({
      role: 'tool', tool_name: 'preview_status',
      tool_result: { ok: true, status: 'stopped', healthy: false, probe: '', url: '', hint: '预览未运行' },
    })
    expect(w.text()).toContain('预览未就绪')
    expect(w.text()).toContain('预览未运行')
  })
})

describe('PreviewCard 红线', () => {
  it('源码不出现预览地址的拼接形态（地址只能来自后端字段）', () => {
    const src = readFileSync(new URL('./PreviewCard.vue', import.meta.url), 'utf8')
    expect(src).not.toMatch(/localhost/)
    expect(src).not.toMatch(/:\/\//)
    expect(src).not.toMatch(/dev_port/)
  })
})
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npm run test -- --run src/components/PreviewCard.test.js`
Expected: FAIL —— `Failed to resolve import "./PreviewCard.vue"`

- [ ] **Step 3: 写实现**

新建 `frontend/src/components/PreviewCard.vue`：

```vue
<script setup>
// 预览工具结果卡：preview_status / preview_logs / preview_restart 共用一张卡。
// 一切读数都来自后端结果字段（status / healthy / probe / hint / error / url / note），
// 前端不拼预览地址——远程部署下拼出来的地址指向的是访问者自己的机器，端口再对也没用。
// 进度（progress）是临时事件，只挂在「还没有结果」的那张卡上，刷新后由结果重画终态。
import { computed, ref } from 'vue'

const props = defineProps({
  msg: { type: Object, required: true },
  progress: { type: Object, default: null },
})

const RUNNING_TITLE = {
  preview_status: '正在读取预览状态…',
  preview_logs: '正在读取预览日志…',
  preview_restart: '正在重启预览…',
}

const name = computed(() => props.msg.name ?? props.msg.tool_name ?? '')
const result = computed(() => props.msg.result ?? props.msg.tool_result ?? null)

const tone = computed(() => {
  const r = result.value
  if (!r) return 'info'
  if (r.cancelled) return 'muted'
  if (r.ok === false) return r.error ? 'error' : 'warn'
  if (name.value === 'preview_logs') return 'plain'
  return r.status === 'running' && r.healthy ? 'ok' : 'warn'
})

const title = computed(() => {
  const r = result.value
  if (!r) return RUNNING_TITLE[name.value] || '正在检查预览…'
  if (r.cancelled) return '已停止等待'
  if (r.ok === false) return r.error ? '预览操作失败' : '仍在启动'
  if (name.value === 'preview_logs') return `预览日志（${(r.lines || []).length} 行）`
  const ready = r.status === 'running' && r.healthy
  if (name.value === 'preview_status') return ready ? '预览运行中' : '预览未就绪'
  return ready ? '预览已就绪' : '预览未就绪'
})

const detail = computed(() => {
  const r = result.value
  if (!r) return [props.progress?.line || '等待平台步骤…']
  const parts = []
  if (r.error) parts.push(r.error)
  if (r.hint) parts.push(r.hint)
  if (r.probe) parts.push(`探测：${r.probe}`)
  return parts
})

const logs = computed(() => {
  const r = result.value
  return (r && (r.lines || r.logs)) || []
})

// 只有「真在跑且后端给了地址」才给链接：healthy=false 时给一个打得开的链接，
// 等于把「HTTP 不可达」藏起来让用户自己撞 403。
const url = computed(() => {
  const r = result.value
  if (!r || r.ok === false || r.cancelled) return ''
  return r.status === 'running' && r.url ? r.url : ''
})

const note = computed(() => (result.value && result.value.note) || '')
const redacted = computed(() => (result.value && result.value.redacted) || 0)
const truncated = computed(() => !!(result.value && result.value.truncated))
const leftHint = computed(() => {
  const left = result.value && result.value.restarts_left
  return typeof left === 'number' && left >= 0 && left <= 1 ? `本次任务还可重启 ${left} 次` : ''
})
const open = ref(false)
</script>

<template>
  <div class="zc-preview" :class="`is-${tone}`">
    <div class="zc-preview-head">
      <span class="zc-preview-title">{{ title }}</span>
      <span v-if="name" class="zc-preview-tool">{{ name }}</span>
      <a v-if="url" class="zc-preview-link" :href="url" target="_blank" rel="noopener">打开预览</a>
    </div>
    <div v-for="(line, i) in detail" :key="`d${i}`" class="zc-preview-detail">{{ line }}</div>
    <div v-if="leftHint" class="zc-preview-note">{{ leftHint }}</div>
    <div v-if="redacted" class="zc-preview-note">已遮断 {{ redacted }} 处凭据</div>
    <div v-if="truncated" class="zc-preview-note">日志过长，已丢掉最早的行</div>
    <div v-if="note" class="zc-preview-note">{{ note }}</div>
    <button v-if="logs.length" class="zc-preview-toggle" type="button" @click="open = !open">
      {{ open ? '收起日志' : `查看日志（${logs.length}）` }}
    </button>
    <ul v-if="open && logs.length" class="zc-preview-logs">
      <li v-for="(line, i) in logs" :key="i">{{ line }}</li>
    </ul>
  </div>
</template>

<style scoped>
.zc-preview {
  width: 100%;
  max-width: 640px;
  border: 1px solid var(--zc-border);
  border-left: 3px solid var(--zc-border);
  border-radius: 8px;
  padding: 8px 12px;
  background: #fbfcfd;
}
.zc-preview.is-ok {
  border-left-color: #52a06b;
  background: #f6fbf7;
}
.zc-preview.is-warn {
  border-left-color: #d99a3c;
  background: #fdfaf3;
}
.zc-preview.is-error {
  border-left-color: #c4636a;
  background: #fdeeee;
}
.zc-preview.is-info {
  border-left-color: var(--zc-primary);
  background: #f4f8fd;
}
.zc-preview.is-muted {
  border-left-color: #b6bfca;
}
.zc-preview-head {
  display: flex;
  align-items: center;
  gap: 8px;
}
.zc-preview-title {
  font-size: 13px;
  font-weight: 500;
  color: var(--zc-text);
}
.zc-preview-tool {
  font-size: 11px;
  color: var(--zc-text-sub);
  background: rgba(91, 122, 157, 0.08);
  border-radius: 4px;
  padding: 1px 6px;
}
.zc-preview-link {
  margin-left: auto;
  font-size: 12px;
  color: var(--zc-primary);
}
.zc-preview-detail {
  margin-top: 6px;
  font-size: 12.5px;
  line-height: 1.7;
  color: var(--zc-text);
  white-space: pre-wrap;
  word-break: break-word;
}
.zc-preview-note {
  margin-top: 4px;
  font-size: 12px;
  color: var(--zc-text-sub);
}
.zc-preview-toggle {
  margin-top: 6px;
  padding: 0;
  border: 0;
  background: none;
  font-size: 12px;
  color: var(--zc-primary);
  cursor: pointer;
}
.zc-preview-logs {
  margin: 6px 0 0;
  padding: 8px 10px;
  max-height: 240px;
  overflow: auto;
  list-style: none;
  background: #f6f8fa;
  border: 1px solid var(--zc-border);
  border-radius: 6px;
  font-family: ui-monospace, SFMono-Regular, Consolas, 'Courier New', monospace;
  font-size: 12px;
  line-height: 1.7;
  color: var(--zc-text);
}
.zc-preview-logs li {
  white-space: pre-wrap;
  word-break: break-all;
}
</style>
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd frontend && npm run test -- --run src/components/PreviewCard.test.js`
Expected: PASS（10 passed，含红线断言）

- [ ] **Step 5: 先写失败测试（消息流路由与确认卡文案）**

在 `frontend/src/components/ChatMessages.test.js` 末尾追加：

```js
// ---- 预览工具结果卡与重启确认卡（规格 2026-10-29-agent-preview-tools §8）----

describe('预览工具在消息流里的渲染', () => {
  it('preview_* 结果走 PreviewCard，不再落回通用折叠块', () => {
    const w = mountMessages([
      { role: 'tool', name: 'preview_restart',
        result: { ok: true, status: 'running', healthy: true, url: 'http://10.0.0.5:8213' } },
    ])
    expect(w.find('.zc-preview').exists()).toBe(true)
    expect(w.text()).toContain('预览已就绪')
    expect(w.find('.zc-preview-link').attributes('href')).toBe('http://10.0.0.5:8213')
  })

  it('非预览工具仍走 ToolCallBlock', () => {
    const w = mountMessages([{ role: 'tool', name: 'read_file', result: { ok: true } }])
    expect(w.find('.zc-preview').exists()).toBe(false)
  })

  it('进行中的预览卡拿得到进度，且有结果后不再拿', () => {
    setActivePinia(createPinia())
    const store = useChatStore()
    store.toolProgress = { name: 'preview_restart', line: '[zerocode] → 起栈中' }
    const w = mount(ChatMessages, {
      props: {
        messages: [{ role: 'tool', name: 'preview_restart', args: { wait_seconds: 240 } }],
        hasSession: true,
      },
      global: {
        stubs: {
          'a-image': ImageStub, 'a-image-preview-group': GroupStub, 'a-button': ButtonStub,
          MarkdownView: true, ToolCallBlock: true, AskUserCard: true,
          ThinkingBlock: true, TodoCard: true,
        },
      },
    })
    expect(w.text()).toContain('起栈中')
  })

  it('历史消息（tool_name / tool_result，无 args）也能画出终态', () => {
    const w = mountMessages([
      { role: 'tool', tool_name: 'preview_logs',
        tool_result: { ok: true, status: 'error', lines: ['10:00:01 [平台] ✗ 迁移失败'],
                       truncated: false, note: '', redacted: 0 } },
    ])
    expect(w.find('.zc-preview').exists()).toBe(true)
    expect(w.text()).toContain('预览日志（1 行）')
  })

  it('重启确认卡：标题与按钮换成重启口径，正文用 summary', () => {
    const w = mount(ChatMessages, {
      props: {
        messages: [], hasSession: true,
        pendingConfirm: {
          tool: 'preview_restart', args: { wait_seconds: 240 },
          summary: '应用「演示应用」的 dev 预览正在运行，重启会中断正在进行的调试。确定重启？',
        },
      },
      global: {
        stubs: {
          'a-image': ImageStub, 'a-image-preview-group': GroupStub, 'a-button': ButtonStub,
          MarkdownView: true, ToolCallBlock: true, AskUserCard: true,
          ThinkingBlock: true, TodoCard: true, PreviewCard: true,
        },
      },
    })
    expect(w.text()).toContain('重启预览')
    expect(w.text()).toContain('正在运行')
    expect(w.text()).toContain('确认重启')
    expect(w.find('.zc-confirm-args').exists()).toBe(false)   // 有 summary 就不甩 args JSON
  })

  it('写入确认卡文案一字不变（无 summary 时回落 args 摘要）', () => {
    const w = mount(ChatMessages, {
      props: {
        messages: [], hasSession: true,
        pendingConfirm: { tool: 'write_file', args: { path: 'a.txt', content: 'hi' } },
      },
      global: {
        stubs: {
          'a-image': ImageStub, 'a-image-preview-group': GroupStub, 'a-button': ButtonStub,
          MarkdownView: true, ToolCallBlock: true, AskUserCard: true,
          ThinkingBlock: true, TodoCard: true, PreviewCard: true,
        },
      },
    })
    expect(w.text()).toContain('写入确认')
    expect(w.text()).toContain('确认写入')
    expect(w.find('.zc-confirm-args').text()).toContain('a.txt')
  })
})
```

文件顶部的 import 补 `useChatStore`（既有 `vi.mock('../api', ...)` 保持不变）：

```js
import ChatMessages from './ChatMessages.vue'
import { useChatStore } from '../stores/chat'
```

- [ ] **Step 6: 跑测试确认失败**

Run: `cd frontend && npm run test -- --run src/components/ChatMessages.test.js`
Expected: FAIL —— 找不到 `.zc-preview`；确认卡仍写死「写入确认」

- [ ] **Step 7: 写实现**

`frontend/src/components/ChatMessages.vue`：

① import 区加一行（与 `TodoCard` 并列）：

```js
import PreviewCard from './PreviewCard.vue'
```

② `confirmSummary` computed 之后加：

```js
// 预览工具（规格 2026-10-29-agent-preview-tools §8）：三个工具共用 PreviewCard。
// 名单与后端 llm.PREVIEW_TOOL_NAMES 同名同序；这里刻意不 import 后端常量（前端拿不到），
// 增删预览工具时两处一起改——名字对不上的后果是卡片落回通用折叠块，不会静默出错。
const PREVIEW_TOOL_NAMES = ['preview_status', 'preview_logs', 'preview_restart']

function toolNameOf(m) {
  return m.name ?? m.tool_name ?? ''
}

function isPreviewTool(m) {
  return PREVIEW_TOOL_NAMES.includes(toolNameOf(m))
}

// 进度只给「消息流最后一条、还没有结果」的那张卡：历史卡片复活进度条会让人以为又在重启
function liveProgress(m, i) {
  if (m.result || m.tool_result) return null
  if (i !== props.messages.length - 1) return null
  const p = store.toolProgress
  return p && p.name === toolNameOf(m) ? p : null
}

// 确认卡按工具换口径：写入确认（args 摘要）／重启预览（后端给的人话 summary）
const isRestartConfirm = computed(() => props.pendingConfirm?.tool === 'preview_restart')
const confirmTitle = computed(() => (isRestartConfirm.value ? '重启预览' : '写入确认'))
const confirmOkText = computed(() => (isRestartConfirm.value ? '确认重启' : '确认写入'))
const confirmNoText = computed(() => (isRestartConfirm.value ? '取消' : '拒绝'))
const confirmSummaryText = computed(() => props.pendingConfirm?.summary || '')
```

③ 消息流里 tool 分支替换为：

```html
        <!-- tool：todo_write 渲染任务卡；preview_* 渲染预览卡；其余落回通用折叠块。
             实时消息字段为 name/args/result，历史消息为 tool_name/tool_result，两者都认。 -->
        <div v-else-if="m.role === 'tool'" class="zc-msg is-tool">
          <TodoCard v-if="m.name === 'todo_write' && m.args && m.args.todos" :todos="m.args.todos" />
          <PreviewCard v-else-if="isPreviewTool(m)" :msg="m" :progress="liveProgress(m, i)" />
          <ToolCallBlock v-else :msg="m" />
        </div>
```

④ 确认卡模板块替换为：

```html
      <!-- 确认卡：写入确认（args 摘要）／重启预览（人话 summary），pendingConfirm 时插入消息流 -->
      <div v-if="pendingConfirm" class="zc-msg is-confirm">
        <div class="zc-confirm">
          <div class="zc-confirm-head">
            {{ confirmTitle }}<span class="zc-confirm-tool">{{ pendingConfirm.tool }}</span>
          </div>
          <p v-if="confirmSummaryText" class="zc-confirm-summary">{{ confirmSummaryText }}</p>
          <pre v-else class="zc-confirm-args">{{ confirmSummary }}</pre>
          <div class="zc-confirm-actions">
            <a-button type="primary" @click="store.confirm(true)">{{ confirmOkText }}</a-button>
            <a-button @click="store.confirm(false)">{{ confirmNoText }}</a-button>
          </div>
        </div>
      </div>
```

⑤ 样式区（`.zc-confirm-args` 之后）加：

```css
.zc-confirm-summary {
  margin: 10px 0;
  font-size: 13px;
  line-height: 1.7;
  color: var(--zc-text);
  white-space: pre-wrap;
  word-break: break-word;
}
```

- [ ] **Step 8: 跑前端全量测试与构建**

Run: `cd frontend && npm run test -- --run`
Expected: PASS —— 新增 PreviewCard / ChatMessages 用例全绿，既有 store 与组件用例零回归

Run: `cd frontend && npm run build`
Expected: 构建成功（`vite build` 无报错）

- [ ] **Step 9: 提交**

```bash
git add frontend/src/components/PreviewCard.vue frontend/src/components/PreviewCard.test.js \
        frontend/src/components/ChatMessages.vue frontend/src/components/ChatMessages.test.js
git commit -m "feat(chat): 预览结果卡与重启确认文案"
```

---

### Task 12: 收尾验证（全量测试、构建、手工矩阵）

自动化只证明代码正确，不证明功能正确。启停时序、两条通道同步、Docker 关闭时的引导这些必须真人走一遍。

**Files:**
- Modify: `docs/specs/2026-10-29-agent-preview-tools.md`（状态行）

- [ ] **Step 1: 后端全量测试（必须串行）**

Run: `pytest -q`
Expected: 全部通过。**不要加 `-n` / xdist**：共享 `zerocode_test` 库，并发 TRUNCATE 会死锁并污染结果。
若出现非断言类 `error`（`TimeoutError` in lifespan setup），按既有经验先单独复跑该文件定性，再判断是否回归。

- [ ] **Step 2: 前端全量测试与构建**

Run: `cd frontend && npm run test -- --run`
Expected: 全部通过

Run: `cd frontend && npm run build`
Expected: `vite build` 成功

- [ ] **Step 3: 起平台，走手工矩阵**

启动：仓库根 `.\dev.bat`（开发模式）或 `.\start.bat`（后端）+ `cd frontend && npm run dev`。
逐项确认（对应规格 §10.4）：

- [ ] FastAPI 应用：只改 Vue 文件不重启 → `preview_status` healthy → 浏览器刷新可见改动；改 Python 触发 `uvicorn --reload`，无需重启
- [ ] 改 `requirements.txt` 后让 Agent 重启 → 观察 `tool_progress` 步骤行与最终 `预览已就绪`
- [ ] Spring 应用：改 Java 后状态仍 running 但行为没变 → Agent 重启并等冷启动（数分钟）
- [ ] 预览 stopped：让 Agent 启动 → **同时打开应用页的预览日志面板**，确认步骤实时同步（同一个 DeployHub）
- [ ] 预览 running：让 Agent 重启 → 确认卡标题是「重启预览」、正文是人话摘要；点「取消」后预览不中断；点「确认重启」后真的重启
- [ ] 长启动中点聊天的「停止」→ 1 秒内工具返回「已停止等待」，应用页日志继续滚动到就绪
- [ ] 两个会话同时操作同一应用 → 只出现一个部署任务，另一个观察到同一结果（不双起栈）
- [ ] 关掉 Docker Desktop → 启动返回中文引导且没有登记后台任务；`preview_status` 仍读得到状态
- [ ] 在应用日志里打印一个假口令（如 `DATABASE_URL=postgresql://u:pw@db/app`）→ `preview_logs` 显示 `<redacted:...>`，卡片提示「已遮断 N 处凭据」
- [ ] 非应用工作区（普通会话）→ 让 Agent「起个预览」，它说明当前工作区不是应用工程，且不出现假工具调用
- [ ] 刷新聊天页 → 历史里的预览卡从 `tool_result` 重画终态，进度条不复活

- [ ] **Step 4: 把规格状态行改为已实现**

`docs/specs/2026-10-29-agent-preview-tools.md` 头部的 `**状态**：待实现` 改为 `**状态**：已实现`。

- [ ] **Step 5: 提交**

```bash
git add docs/specs/2026-10-29-agent-preview-tools.md
git commit -m "docs(specs): 预览工具规格状态更新为已实现"
```

---

## 规格覆盖对照

| 规格条目 | 落地任务 |
|---|---|
| §4.1 三个工具 schema、无 app 参数 | Task 5、Task 8（红线用例） |
| §4.2 六种返回形状 | Task 6（status/logs）、Task 7（restart 成功/失败/超时/取消） |
| §5.1 应用解析、0/多行/异常语义 | Task 4、Task 6、Task 9 |
| §5.2 共享任务注册表 | Task 1、Task 7、Task 9 |
| §5.3 等待循环、进度节流、取消、超时 | Task 7 |
| §5.4 状态与探针、不调 `dev_status` | Task 3、Task 6 |
| §5.5 重启策略表 | Task 7 |
| §5.6 确认（含竞态兜底、不白问一次） | Task 7（兜底）、Task 9（发确认）、Task 11（文案） |
| §5.7 单次 run 次数上限 | Task 7、Task 9（`run_state`） |
| §5.8 热更新语义 | Task 7（`build_preview_prompt`） |
| §5.9 四个配置项与夹取 | Task 6（夹取）、Task 9（ws 默认值） |
| §6 安全边界（遮断/限长/无命令面/prod 隔离） | Task 3、Task 6、Task 8（红线用例） |
| §8.1 `tool_progress` 临时事件 | Task 7（发送）、Task 10（store）、Task 11（渲染） |
| §8.2 PreviewCard 状态表 | Task 11 |
| §8.3 确认卡 summary | Task 9（发送）、Task 10（保存）、Task 11（文案） |
| §8.4 历史回放 | Task 11（`tool_name`/`tool_result` 用例） |
| §8.5 应用页同步 | Task 7（notify 用 `hub.publish`）+ Task 12 手工矩阵 |
| §9 系统提示词 | Task 5（注入口）、Task 7（内容）、Task 9（挂载条件） |
| §10.1–10.3 测试计划 | Task 1–11 各自的红绿循环 |
| §10.4 手工矩阵 | Task 12 |
