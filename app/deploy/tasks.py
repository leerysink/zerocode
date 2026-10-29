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
