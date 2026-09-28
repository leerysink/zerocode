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
from sqlalchemy.ext.asyncio import create_async_engine

logger = logging.getLogger(__name__)

# 采集节奏：容器 30s（Ticket 004）、磁盘 600s（workspace_size / db_size）。
DISK_INTERVAL = 600
CONTAINER_INTERVAL = 30
# docker stats 行格式：容器名 / CPU% / 内存用量（used / limit）；应用级汇总只取 Name、CPU、内存 used。
_STATS_FMT = "{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}"
# 内存单位 → 字节倍率（docker stats 用 IEC 单位；KB/MB 等 SI 写法一并容错）。
_MEM_UNITS = {"B": 1, "KiB": 1024, "MiB": 1024 ** 2, "GiB": 1024 ** 3, "TiB": 1024 ** 4,
              "KB": 1000, "MB": 1000 ** 2, "GB": 1000 ** 3, "TB": 1000 ** 4}
# 启动后首采延时（秒）：冷启动不必干等整整一个 DISK_INTERVAL，否则前 10 分钟当前值恒 null。
# 默认 30s——远大于任何单个 API 测试的 lifespan 时长，故测试期内不会触发、不污染数据。
DEFAULT_BOOT_DELAY = 30.0
# 历史滚动保留天数默认（单粒度窗口，可经 ZEROCODE_METRICS_RETENTION_DAYS 覆盖）。
DEFAULT_RETENTION_DAYS = 30
# prod 真正有活容器、cpu/memory 才算“当前值”的状态集（写侧入查询、读侧判空共用，免漂移）。
LIVE_PROD_STATUSES = ("running", "unhealthy")


def _boot_delay() -> float:
    """首采延时读环境变量（非法值回退默认），测试可用 monkeypatch 置大值彻底禁掉首采。"""
    try:
        return float(os.environ.get("ZEROCODE_METRICS_BOOT_DELAY", DEFAULT_BOOT_DELAY))
    except (TypeError, ValueError):
        return DEFAULT_BOOT_DELAY


def _retention_days() -> int:
    """历史保留天数（单粒度滚动窗口，可配）：读环境变量，非法/非正回退默认 30 天。"""
    try:
        days = int(os.environ.get("ZEROCODE_METRICS_RETENTION_DAYS", DEFAULT_RETENTION_DAYS))
    except (TypeError, ValueError):
        return DEFAULT_RETENTION_DAYS
    return days if days > 0 else DEFAULT_RETENTION_DAYS


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
    """磁盘循环的一拍：workspace_size（文件系统）+ db_size（PG），同属低频一类；顺带扫一次过期清理。"""
    await sample_workspace_size(app.state.session_factory)
    await sample_db_size(app.state.session_factory)
    await prune_expired(app.state.session_factory, _retention_days())


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


# ===================== 容器 CPU/内存采集（Ticket 004） =====================

def _parse_cpu(s: str):
    """docker CPUPerc 形如 "12.50%" → float；不可用（"--"/空）返 None（不可用≠0%，不当真点落库）。"""
    s = (s or "").strip().rstrip("%").strip()
    try:
        return float(s)
    except ValueError:
        return None


def _parse_mem_bytes(s: str):
    """docker 内存用量串（如 "128.0MiB"）→ 字节；无数字或单位认不出返 None。

    宁可不写点，也不能把认不出的单位当字节×1 静默低估几个数量级（历史上留错比留洞槽糕）。"""
    s = (s or "").strip()
    num = ""
    i = 0
    while i < len(s) and (s[i].isdigit() or s[i] == "."):
        num += s[i]
        i += 1
    unit = s[i:].strip()
    if not num or unit not in _MEM_UNITS:
        if s:
            logger.warning("内存用量解析不了，当不可用处理: %r", s)
        return None
    return int(float(num) * _MEM_UNITS[unit])


def parse_stats(out: str) -> list[dict]:
    """把 docker stats --no-stream 的制表符输出解析成 [{name, cpu_pct, mem_bytes}]。

    MemUsage 是 "used / limit"，只取 used 侧作为该容器实时占用。行不足 3 列跳过。
    """
    rows = []
    for line in (out or "").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        used = parts[2].split("/")[0]
        rows.append({"name": parts[0].strip(), "cpu_pct": _parse_cpu(parts[1]),
                     "mem_bytes": _parse_mem_bytes(used)})
    return rows


async def _docker_stats_raw() -> str:
    """跑一次 `docker stats --no-stream`（全系统运行中容器）。退出码非零视为 daemon 不可用 → 抛。

    只跑这一条命令、不按应用逐个调，把 N 次 docker 压成 1 次；应用归属靠容器名前缀在后头切。
    已退出的 migrate 一次性容器不在 stats 输出内，天然被排除。"""
    from app.deploy import engine  # 延迟导入，避开模块加载期潜在环依赖
    code, out = await engine.docker("stats", "--no-stream", "--format", _STATS_FMT, timeout=60)
    if code != 0:
        raise RuntimeError(f"docker stats 退出码 {code}: {out[-200:]}")
    return out


async def _prod_running_apps(session_factory) -> list[tuple[int, str]]:
    """当前处于 prod 运行/体检态的 (app_id, slug)——只有这些栈有活容器值得采。"""
    async with session_factory() as s:
        rows = (await s.execute(text(
            "SELECT id, slug FROM app_project WHERE status_prod = ANY(:live)"),
            {"live": list(LIVE_PROD_STATUSES)})).mappings().all()
    return [(r["id"], r["slug"]) for r in rows]


async def sample_containers(session_factory) -> int:
    """轮询 docker stats，按应用汇总其 prod 栈常驻容器的 CPU/内存并落 cpu/memory 行。返回写入应用数。

    应用级口径（grill 定）：同栈多容器的 CPU%、内存字节各自求和成一个点。容器名前缀
    `zc-{slug}-prod-` 认归属。本轮无匹配容器（刚 up/正在起）不写点——历史留洞胜 over 写 0 假点。
    docker 不可用吐掉不崩，对齐采集兜底纪律。"""
    apps = await _prod_running_apps(session_factory)
    if not apps:
        return 0
    try:
        raw = await _docker_stats_raw()
    except Exception:
        logger.warning("docker stats 采集失败，本轮容器指标跳过", exc_info=True)
        return 0
    parsed = parse_stats(raw)
    written = 0
    for app_id, slug in apps:
        prefix = f"zc-{slug}-prod-"
        rows = [p for p in parsed if p["name"].startswith(prefix)]
        # 只累可信读数：瞬时取不到（cpu/mem 为 None）的容器跳过；某类本轮全无可信值则不写该 kind。
        cpus = [r["cpu_pct"] for r in rows if r["cpu_pct"] is not None]
        mems = [r["mem_bytes"] for r in rows if r["mem_bytes"] is not None]
        if not cpus and not mems:
            continue
        try:
            if cpus:
                await record_metric(session_factory, app_id, "cpu", sum(cpus))
            if mems:
                await record_metric(session_factory, app_id, "memory", sum(mems))
            written += 1
        except Exception:
            logger.warning("容器指标落库失败 app_id=%s", app_id, exc_info=True)
    return written


async def container_loop(app) -> None:
    """高频容器采集循环（30s）：节奏与 disk_loop 同构，先等首采再周期采。"""
    await asyncio.sleep(_boot_delay())
    while True:
        try:
            await sample_containers(app.state.session_factory)
        except Exception:
            logger.exception("容器采集 tick 失败")
        await asyncio.sleep(CONTAINER_INTERVAL)


# ===================== 应用数据库磁盘采集（Ticket 005） =====================

async def _prod_db_sizes(session_factory, names: list[str]) -> dict[str, int]:
    """连平台 PG 维护库，一次查这批库名的字节大小（pg_database_size）；不存在的库不在结果里。

    应用库角色只能连自己库，故不能逐库连入——从维护库视角用 pg_database_size(datname) 跨库量，
    一条 SQL 批量拿完。names 来自应用 slug 派生（可信），但依旧走参数化 `= ANY`。"""
    from app.deploy import provision  # 延迟导入避开模块加载期环依赖
    if not names:
        return {}
    eng = create_async_engine(provision.admin_url())
    try:
        async with eng.connect() as conn:
            rows = (await conn.execute(text(
                "SELECT datname, pg_database_size(datname) AS size FROM pg_database "
                "WHERE datname = ANY(:names)"), {"names": names})).mappings().all()
    finally:
        await eng.dispose()
    return {r["datname"]: int(r["size"]) for r in rows}


async def sample_db_size(session_factory) -> int:
    """逐应用量其 prod 库字节数并落 kind=db_size 行。返回写入应用数。

    应用 prod 库名固定 `{slug}_prod`（provision 约定）；尚未发布（库不存在）的应用自然不在
    查询结果里 → 跳过不写 0（与 workspace_size 同纪律）。PG 不可达吐掉不崩。"""
    async with session_factory() as s:
        apps = (await s.execute(text("SELECT id, slug FROM app_project"))).mappings().all()
    if not apps:
        return 0
    name_to_apps: dict[str, list[int]] = {}
    for a in apps:
        name_to_apps.setdefault(f"{a['slug']}_prod", []).append(a["id"])
    try:
        sizes = await _prod_db_sizes(session_factory, list(name_to_apps))
    except Exception:
        logger.warning("数据库磁盘采集失败，本轮跳过", exc_info=True)
        return 0
    written = 0
    for name, size in sizes.items():
        for app_id in name_to_apps.get(name, []):
            try:
                await record_metric(session_factory, app_id, "db_size", size)
                written += 1
            except Exception:
                logger.warning("db_size 落库失败 app_id=%s", app_id, exc_info=True)
    return written


# ===================== 过期清理（Ticket 006 / 单粒度滚动窗口） =====================

async def prune_expired(session_factory, days=None) -> int:
    """删除超过保留期的指标点，返回删除行数。days=None 时读配置（默认 30 天）。

    单粒度滚动窗口：不分档降采样，只按天数一条硬线剪尾（grill 定：保留天数可配）。
    (app_id, ts DESC) 与 (ts) 两个索引支撑这句 DELETE；无行可删时廉价。"""
    if days is None:
        days = _retention_days()
    async with session_factory() as s:
        res = await s.execute(text(
            "DELETE FROM app_metrics WHERE ts < now() - make_interval(days => :d)"),
            {"d": int(days)})
        await s.commit()
        return res.rowcount or 0
