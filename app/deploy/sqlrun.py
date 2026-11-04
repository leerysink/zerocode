# app/deploy/sqlrun.py
"""sqlrunner 镜像构建与一次性执行（规格 07 §4.3）。退出码：0 成功 / 2 迁移失败 / 3 篡改。

镜像 zerocode-sqlrunner:2（postgres:16-alpine + /run.sh）：输入 env TARGET_HOST/TARGET_DB/
TARGET_USER/TARGET_PASSWORD/TARGET_ENV，只读挂载 /sql，跑「公共段 + TARGET_ENV 段」增量迁移并写
sqlchange_history（记账用相对 sqlchange/ 的路径，ADR-0040）。
tag 从 :1 bump 到 :2 是刻意的：build_if_missing 只在镜像缺失时构建，沿用 :1 会让宿主上那份陈旧
镜像带着「不认识 TARGET_ENV」的旧 run.sh 继续被复用，环境段从此静默不跑。
"""
import asyncio
from pathlib import Path
from app.deploy.provision import ensure_zcnet

IMAGE = "zerocode-sqlrunner:2"
SQLRUNNER_DIR = Path(__file__).resolve().parent / "sqlrunner"
# 数据轨的环境段（ADR-0040）：run.sh 用它决定「公共段 + 本环境段」，野值一律拒。
SEGMENT_ENVS = ("dev", "prod")


def docker_available() -> bool:
    """docker 引擎是否可用——仅供 pytest.mark.skipif 的收集期判定（收集期无运行中 loop）。

    _check 不依赖外层 loop，asyncio.run 起独立事件循环即可；若在已有 loop 内调用，
    asyncio.run 抛 RuntimeError，被下面的 except 兜住返回 False（守卫语义本就是「不可用则跳过」）。
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass

    async def _check():
        p = await asyncio.create_subprocess_exec(
            "docker", "info", stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        return await p.wait() == 0

    try:
        return asyncio.run(_check())
    except Exception:
        return False


async def _docker(*args, cwd=None, on_line=None, timeout=600) -> tuple[int, str]:
    """执行一条 docker 命令（stdout+stderr 合流逐行收集），返回 (退出码, 末尾 200 行日志)。"""
    p = await asyncio.create_subprocess_exec(
        "docker", *args, cwd=cwd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    lines: list[str] = []

    async def _pump():
        assert p.stdout
        while raw := await p.stdout.readline():
            line = raw.decode(errors="replace").rstrip()
            lines.append(line)
            if on_line:
                await on_line(line) if asyncio.iscoroutinefunction(on_line) else on_line(line)

    pump = asyncio.create_task(_pump())
    code = await asyncio.wait_for(p.wait(), timeout)
    # 进程退出时管道里可能还有未读完的尾部输出：立即 cancel 会丢掉末行（OK/FAIL/TAMPER 正是断言目标），
    # 故先等泵任务自然读到 EOF 收尾（超时兜底，避免异常情况下悬挂）。
    try:
        await asyncio.wait_for(pump, 10)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        pump.cancel()
    return code, "\n".join(lines[-200:])


async def build_if_missing() -> None:
    """镜像不存在则从 app/deploy/sqlrunner/ 构建（构建失败抛 RuntimeError 带日志）。"""
    code, _ = await _docker("image", "inspect", IMAGE)
    if code == 0:
        return
    code, log = await _docker("build", "-t", IMAGE, ".", cwd=str(SQLRUNNER_DIR))
    if code != 0:
        raise RuntimeError(f"sqlrunner 镜像构建失败:\n{log}")


def segment_files(sqlchange_dir: Path, env: str) -> list[str]:
    """某环境一次执行会看到的 .sql **相对 sqlchange/ 的路径**（公共段在前、本环境段在后）。

    与 run.sh 的 `ls /sql/*.sql /sql/$TARGET_ENV/*.sql | sort` 同口径：顶层文件名首字母是 V/D（大写），
    排序天然早于小写的段目录名，所以「先公共后环境」在全局 sort 下也成立。
    环境段目录缺失是合法的（存量应用不回填，ADR-0040），此时只剩公共段。
    """
    if env not in SEGMENT_ENVS:
        raise ValueError(f"segment_files 的 env 只接受 {'/'.join(SEGMENT_ENVS)}，收到 {env!r}")
    out = [p.name for p in sorted(sqlchange_dir.glob("*.sql"))]
    seg = sqlchange_dir / env
    if seg.is_dir():
        out += [f"{env}/{p.name}" for p in sorted(seg.glob("*.sql"))]
    return out


async def run_sqlchange(app_dir: Path, env: str, cred: dict, on_log=None) -> dict:
    """对 <app_dir>/sqlchange 执行一次性迁移容器；cred 为 provision 返回 {db,user,password}。

    env 决定数据轨跑哪一段（ADR-0040）：容器执行「公共段（顶层）+ `<env>/` 段」，另一段对本库不可见。
    它同时是连库凭据的选择器（`<slug>_dev` / `<slug>_prod`），两者必须一致——传错 env 会把 dev 段
    写进线上库，或让线上种子静默失踪，故非 dev/prod 的取值直接 raise（不起容器）。
    Windows 下 -v 传 D:\\... 形式，Docker Desktop 自行转换挂载路径。

    fail-closed 闸门（审查 I-1）：挂载源目录缺失时 Docker Desktop 会静默补一个空 /sql，run.sh 走
    「空目录=成功」分支退 0 → 绿色空跑击穿 depends_on: migrate 闸门；宿主侧才分得清「缺失 vs 空」，
    故在起任何 docker 命令之前先拒绝（空目录仍合法，按现路径 exit 0）。
    """
    if env not in SEGMENT_ENVS:
        raise ValueError(f"run_sqlchange 的 env 只接受 {'/'.join(SEGMENT_ENVS)}，收到 {env!r}")
    sql_dir = app_dir / "sqlchange"
    if not sql_dir.is_dir():
        return {"ok": False, "code": 2,
                "log": f"sqlchange 目录不存在: {sql_dir}（迁移闸门拒绝空跑）"}
    # P2 结构轨：存在 schema 目录才挂 /schema（缺时不挂，避免 Docker Desktop 静默造空 /schema；
    # run.sh 的 [ -f /schema/schema.sql ] 双保险，缺快照时 fold 段自动跳过）。
    schema_dir = app_dir / "schema"
    mounts = ["-v", f"{sql_dir}:/sql:ro"]
    if schema_dir.is_dir():
        mounts = ["-v", f"{schema_dir}:/schema:ro", *mounts]
    await ensure_zcnet()
    # M-1（终审 F-01）：镜像只在平台启动构建一次，Docker 晚启动即永久缺失——main.py 注释承诺
    # 「首次预览时会重试」，这里兑现：网络自愈旁补镜像自愈（build_if_missing 幂等，inspect 命中即返回）。
    await build_if_missing()
    code, log = await _docker(
        "run", "--rm", "--network", "zcnet",
        "-e", f"TARGET_DB={cred['db']}",
        "-e", f"TARGET_USER={cred['user']}",
        "-e", f"TARGET_PASSWORD={cred['password']}",
        "-e", f"TARGET_ENV={env}",
        *mounts,
        IMAGE, on_line=on_log)
    return {"ok": code == 0, "code": code, "log": log}
