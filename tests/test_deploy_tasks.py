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
