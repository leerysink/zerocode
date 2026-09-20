# app/deploy/sqlrun.py
"""sqlrunner 镜像构建与一次性执行（规格 07 §4.3）。退出码：0 成功 / 2 迁移失败 / 3 篡改。

镜像 zerocode-sqlrunner:1（postgres:16-alpine + /run.sh）：输入 env TARGET_HOST/TARGET_DB/
TARGET_USER/TARGET_PASSWORD，只读挂载 /sql，跑增量迁移并写 sqlchange_history。
"""
import asyncio
from pathlib import Path
from app.deploy.provision import ensure_zcnet

IMAGE = "zerocode-sqlrunner:1"
SQLRUNNER_DIR = Path(__file__).resolve().parent / "sqlrunner"


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


async def run_sqlchange(app_dir: Path, env: str, cred: dict, on_log=None) -> dict:
    """对 <app_dir>/sqlchange 执行一次性迁移容器；cred 为 provision 返回 {db,user,password}。

    dev/prod 的差异只体现在传入的 cred（同一入口两条发布线复用），故 env 暂不参与容器参数。
    Windows 下 -v 传 D:\\... 形式，Docker Desktop 自行转换挂载路径。

    fail-closed 闸门（审查 I-1）：挂载源目录缺失时 Docker Desktop 会静默补一个空 /sql，run.sh 走
    「空目录=成功」分支退 0 → 绿色空跑击穿 depends_on: migrate 闸门；宿主侧才分得清「缺失 vs 空」，
    故在起任何 docker 命令之前先拒绝（空目录仍合法，按现路径 exit 0）。
    """
    sql_dir = app_dir / "sqlchange"
    if not sql_dir.is_dir():
        return {"ok": False, "code": 2,
                "log": f"sqlchange 目录不存在: {sql_dir}（迁移闸门拒绝空跑）"}
    await ensure_zcnet()
    code, log = await _docker(
        "run", "--rm", "--network", "zcnet",
        "-e", f"TARGET_DB={cred['db']}",
        "-e", f"TARGET_USER={cred['user']}",
        "-e", f"TARGET_PASSWORD={cred['password']}",
        "-v", f"{sql_dir}:/sql:ro",
        IMAGE, on_line=on_log)
    return {"ok": code == 0, "code": code, "log": log}
