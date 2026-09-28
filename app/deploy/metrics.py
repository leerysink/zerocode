# app/deploy/metrics.py
"""应用资源监控采集（Ticket 001 起，规格 docs/specs/2026-09-28-app-metrics.md）。

口径：一个应用消耗的资源 = prod 栈容器运行时（CPU/内存，Ticket 004）+ 应用专属磁盘
（工作区 du + 数据库 size，本期 Ticket 001/005）。仅 prod 栈，应用级汇总，单粒度滚动窗口。

本模块只提供**采集函数 + 循环协程**，不碰 HTTP：读端点在 app/api/metrics.py。
所有 tick 一律「尽力而为、异常吞掉记 warning」——Docker/PG/文件系统任一不可用都只意味着
某一类指标当次缺失，绝不能带崩平台（对齐 deploy 启动准备的兜底纪律）。
"""
import asyncio
import logging
import os
from pathlib import Path

from sqlalchemy import text

logger = logging.getLogger(__name__)

# 采集节奏：容器 30s（Ticket 004 接入）、磁盘 600s（本期 workspace_size）。
DISK_INTERVAL = 600
# 启动后首采延时（秒）：冷启动不必干等整整一个 DISK_INTERVAL，否则前 10 分钟当前值恒 null。
# 默认 30s——远大于任何单个 API 测试的 lifespan 时长，故测试期内不会触发、不污染数据。
DEFAULT_BOOT_DELAY = 30.0


def _boot_delay() -> float:
    """首采延时读环境变量（非法值回退默认），测试可用 monkeypatch 置大值彻底禁掉首采。"""
    try:
        return float(os.environ.get("ZEROCODE_METRICS_BOOT_DELAY", DEFAULT_BOOT_DELAY))
    except (TypeError, ValueError):
        return DEFAULT_BOOT_DELAY


def dir_size_bytes(path) -> int:
    """目录下所有普通文件字节数递归累加（工作区**全量**，含 node_modules/dist 等，低频消化成本）。

    - 目录不存在 → 返 0（调用方据此区分「空目录」与「不存在」，见 sample_workspace_size 跳过语义）。
    - 悬空符号链接 / 无权限项：跳过而非中断，一次坏条目不能让整盘扫描崩掉。
    """
    root = Path(path)
    if not root.is_dir():
        return 0
    total = 0
    for dirpath, _dirs, files in os.walk(root, onerror=lambda e: None):
        for name in files:
            fp = os.path.join(dirpath, name)
            try:
                if not os.path.islink(fp):
                    total += os.path.getsize(fp)
            except OSError:
                continue
    return total


async def _app_roots(session_factory) -> list[tuple[int, str]]:
    """全部应用的 (app_id, workspace.root_path)。root_path 建应用时即写成工程目录绝对路径。"""
    async with session_factory() as s:
        rows = (await s.execute(text(
            "SELECT ap.id, w.root_path FROM app_project ap "
            "JOIN workspace w ON w.id = ap.workspace_id"))).mappings().all()
    return [(r["id"], r["root_path"]) for r in rows]


async def record_metric(session_factory, app_id: int, kind: str, value) -> None:
    """落一个指标点（ts 取库端 now()）。CPU 传百分比、内存/磁盘传字节。"""
    async with session_factory() as s:
        await s.execute(text(
            "INSERT INTO app_metrics (app_id, kind, value) VALUES (:a, :k, :v)"),
            {"a": app_id, "k": kind, "v": value})
        await s.commit()


async def sample_workspace_size(session_factory) -> int:
    """逐应用采工作区磁盘字节数并落 kind=workspace_size 行。返回写入行数。

    目录不存在的应用**跳过不写**：写 0 会被历史曲线画成「消耗为零」的假点，而实际是「没扫到」。
    单应用异常吞掉记 warning，不影响其余应用（一次扫盘失败不该连累整轮采集）。
    """
    written = 0
    for app_id, root_path in await _app_roots(session_factory):
        if not root_path or not Path(root_path).is_dir():
            continue
        try:
            # 全量 du 可能遍历数万文件（node_modules），同步 os.walk 会独占事件循环卡住所有请求，
            # 故丢进线程池跑；单次 await 不阻塞 loop。
            size = await asyncio.to_thread(dir_size_bytes, root_path)
            await record_metric(session_factory, app_id, "workspace_size", size)
            written += 1
        except Exception:
            logger.warning("工作区磁盘采集失败 app_id=%s", app_id, exc_info=True)
    return written


async def _disk_tick(app) -> None:
    """磁盘循环的一拍：本期只有 workspace_size，Ticket 005 在此追加 db_size。"""
    await sample_workspace_size(app.state.session_factory)


async def disk_loop(app) -> None:
    """低频磁盘采集循环（模仿 main._watchdog：先睡后采，故启动即采会卡 Docker 侧就绪前失败）。

    启动后先等一个较短首采延时（默认 30s）采第一个点，之后按 DISK_INTERVAL 周期采；
    单拍失败只记日志、循环不死——下一拍照常。优雅退出由 lifespan cancel 收口。
    """
    await asyncio.sleep(_boot_delay())
    while True:
        try:
            await _disk_tick(app)
        except Exception:
            logger.exception("磁盘采集 tick 失败")
        await asyncio.sleep(DISK_INTERVAL)
