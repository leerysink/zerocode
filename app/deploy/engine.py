# app/deploy/engine.py
"""dev 环境生命周期（规格 03）：preflight → 渲染 → 迁移 → compose up → 健康等待 → 状态落库+事件。

所有 docker 交互经 async `docker()` 封装，便于测试替身；`run_sqlchange` 与 `preflight_docker`、
`wait_frontend_health` 一律以**模块级名字**在本模块内调用（裁定 D-1），monkeypatch 才生效。
迁移闸门（D-2）：编排顺序 迁移 → compose up，`sqlrun.run_sqlchange` 自带「sqlchange 目录缺失
fail-closed」守卫，此处不再重复预检。
"""
import asyncio, inspect, logging
from pathlib import Path
import httpx
from sqlalchemy import text
from app.deploy import compose as compose_mod
from app.deploy.errors import DeployError
from app.deploy.provision import read_cred
from app.deploy.settings import deploy_cfg
# 迁移入口按模块级名字引入（D-1）：引擎内以 bare name 调用，测试 monkeypatch engine.run_sqlchange 生效
from app.deploy.sqlrun import run_sqlchange

logger = logging.getLogger(__name__)


async def docker(*args, cwd=None, on_line=None, timeout=600) -> tuple[int, str]:
    """执行一条 docker 命令：stdout+stderr 合流逐行收集（并转给 on_line），返回 (退出码, 末尾 300 行)。"""
    p = await asyncio.create_subprocess_exec(
        "docker", *args, cwd=cwd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    lines: list[str] = []

    async def _pump():
        assert p.stdout
        while raw := await p.stdout.readline():
            line = raw.decode(errors="replace").rstrip()
            lines.append(line)
            if on_line:
                res = on_line(line)                     # 同步回调（hub.publish）与协程回调都兼容
                if inspect.isawaitable(res):
                    await res

    pump = asyncio.create_task(_pump())
    try:
        code = await asyncio.wait_for(p.wait(), timeout)
    except asyncio.TimeoutError:
        p.kill()
        # 被杀子进程的 transport 必须在本 loop 关闭前收尾：先回收退出码，再让泵任务读到 EOF 关掉管道，
        # 否则 Windows 上留一条「unclosed transport」的 unraisable 异常（GC 发生在 loop 已关闭之后）。
        try:
            await asyncio.wait_for(p.wait(), 10)
        except asyncio.TimeoutError:
            pass
        try:
            await asyncio.wait_for(pump, 10)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pump.cancel()
        raise DeployError(f"docker {' '.join(args[:3])}… 超时（{timeout}s）", status=504)
    # 进程退出时管道里可能还有未读完的尾部输出（compose 的最后一行 Started/ERROR 正是断言与用户
    # 引导的来源），立即 cancel 会丢末行——先让泵任务读到 EOF 自然收尾，超时兜底再 cancel。
    try:
        await asyncio.wait_for(pump, 10)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        pump.cancel()
    return code, "\n".join(lines[-300:])


async def preflight_docker() -> None:
    """Docker 引擎可用性前置检查：不可用抛 503 中文引导（规格 03 §5 / D9）。"""
    code, log = await docker("info", "--format", "ok")
    if code != 0:
        raise DeployError(
            "Docker 未启动或不可用：请打开 Docker Desktop（设置页「部署环境」可一键启动）"
            f"；原始错误: {log[-200:]}", status=503)


async def _app_root(sf, app: dict) -> Path:
    """应用工作区根目录（app_project → workspace.root_path）。"""
    async with sf() as s:
        row = (await s.execute(text(
            "SELECT w.root_path FROM app_project a JOIN workspace w ON w.id = a.workspace_id "
            "WHERE a.id = :i"), {"i": app["id"]})).first()
    if not row:
        raise DeployError("应用不存在", status=404)
    return Path(row[0])


def _compose_meta(app: dict) -> dict:
    """compose 渲染入参归一：app_service.get_app 返回扁平列（dev_port/prod_port），
    而渲染器与引擎取端口走 app.json 风格的 `ports` 映射——两种形态都支持（Task 12 直接传 get_app 结果）。
    """
    ports = app.get("ports")
    if not isinstance(ports, dict):
        ports = {}
    meta = dict(app)
    meta["ports"] = {"dev": ports.get("dev", app.get("dev_port")),
                     "prod": ports.get("prod", app.get("prod_port"))}
    return meta


def _dev_port(app: dict):
    ports = app.get("ports")
    if isinstance(ports, dict) and ports.get("dev") is not None:
        return ports["dev"]
    return app.get("dev_port")


async def _set_status(sf, app_id: int, status: str) -> None:
    async with sf() as s:
        await s.execute(text(
            "UPDATE app_project SET status_dev = :st, updated_at = now() WHERE id = :i"),
            {"st": status, "i": app_id})
        await s.commit()


async def _notify(notify, app_id, event) -> None:
    if notify:
        notify(app_id, {"app_id": app_id, **event})


async def wait_frontend_health(port: int, timeout: int) -> bool:
    """前端健康判定（规格 03 §4.3）：GET http://localhost:<port>/ 拿到 200 即就绪。"""
    async with httpx.AsyncClient(timeout=2.0) as c:
        for _ in range(max(int(timeout), 1)):
            try:
                if (await c.get(f"http://127.0.0.1:{port}/")).status_code == 200:
                    return True
            except Exception:
                pass
            await asyncio.sleep(1)
    return False


async def start_dev(sf, app: dict, notify=None) -> dict:
    """启动 dev 预览栈：stopped→starting→running（任一失败 → error，异常原样上抛）。"""
    app_id = app["id"]
    app = _compose_meta(app)
    await _notify(notify, app_id, {"type": "dev_status", "status": "starting"})
    try:
        await _set_status(sf, app_id, "starting")
        await preflight_docker()
        root = await _app_root(sf, app)
        cred = await read_cred(sf, app["slug"], "dev")
        if not cred:
            raise DeployError("dev 数据库凭据缺失，请删除应用后重建（或检查 dbcred 配置）", status=409)
        compose_mod.ensure_deploy_files(root, app, {"dev": cred})
        on_log = None
        if notify:
            async def on_log(line):
                await _notify(notify, app_id, {"type": "dev_log", "line": line})
        mig = await run_sqlchange(root, "dev", cred, on_log=on_log)
        await _notify(notify, app_id, {"type": "migrate_result",
                                       "env": "dev", "ok": mig["ok"], "log": mig["log"]})
        if not mig["ok"]:
            raise DeployError(f"dev 迁移失败（sqlrunner 退出码 {mig['code']}），详情见迁移日志")
        code, log = await docker(
            "compose", "-f", str(root / "deploy" / "dev-compose.yml"), "up", "-d",
            cwd=str(root), on_line=on_log)
        if code != 0:
            raise DeployError(f"dev 栈启动失败:\n{log[-800:]}")
        ok = await wait_frontend_health(_dev_port(app),
                                        int((await deploy_cfg(sf)).get("deploy.health_timeout", 60)))
        if not ok:
            raise DeployError(f"dev 前端在健康等待期内未就绪（端口 {_dev_port(app)}）")
        await _set_status(sf, app_id, "running")
        await _notify(notify, app_id, {"type": "dev_status", "status": "running"})
        return {"status": "running", "url": f"http://localhost:{_dev_port(app)}"}
    except DeployError as e:
        await _set_status(sf, app_id, "error")
        await _notify(notify, app_id, {"type": "dev_status", "status": "error",
                                       "message": str(e)})
        raise
    except Exception as e:
        await _set_status(sf, app_id, "error")
        await _notify(notify, app_id, {"type": "dev_status", "status": "error", "message": str(e)})
        raise DeployError(f"启动预览失败: {e}") from e


async def stop_dev(sf, app: dict, notify=None) -> dict:
    """停止 dev 栈（幂等：栈本就不在时 compose 返回 0，状态同样落 stopped）。"""
    root = await _app_root(sf, app)
    await _notify(notify, app["id"], {"type": "dev_status", "status": "stopping"})
    code, log = await docker("compose", "-f", str(root / "deploy" / "dev-compose.yml"), "down",
                             cwd=str(root))
    if code != 0:
        logger.warning("dev 栈停止命令返回非零（仍按 stopped 收口）: %s\n%s", app.get("slug"), log[-300:])
    await _set_status(sf, app["id"], "stopped")
    await _notify(notify, app["id"], {"type": "dev_status", "status": "stopped"})
    return {"status": "stopped"}


async def restart_dev(sf, app: dict, notify=None) -> dict:
    await stop_dev(sf, app, notify)
    return await start_dev(sf, app, notify)


async def dev_status(sf, app: dict) -> dict:
    root = await _app_root(sf, app)
    code, log = await docker("compose", "-f", str(root / "deploy" / "dev-compose.yml"), "ps",
                             "--format", "json", cwd=str(root))
    row = await get_status_dev(sf, app["id"])
    return {"status_dev": row, "compose_ps": log if code == 0 else "",
            "url": f"http://localhost:{_dev_port(app)}"}


async def get_status_dev(sf, app_id: int) -> str:
    async with sf() as s:
        return (await s.execute(text(
            "SELECT status_dev FROM app_project WHERE id = :i"), {"i": app_id})).scalar() or "stopped"


async def cleanup_orphans(sf, notify=None) -> None:
    """平台启动时：所有应用 dev 栈一律 down（崩溃残留清理），状态复位 stopped（规格 03 §5）。"""
    async with sf() as s:
        rows = (await s.execute(text("SELECT id, slug FROM app_project"))).fetchall()
    for app_id, slug in rows:
        try:
            await docker("compose", "-p", f"zc-{slug}-dev", "down", timeout=120)
        except Exception:
            logger.exception("清理孤儿 dev 栈失败: %s", slug)
        await _set_status(sf, app_id, "stopped")
        await _notify(notify, app_id, {"type": "dev_status", "status": "stopped"})


async def stop_all_dev_on_exit(sf, cfg: dict, notify=None) -> None:
    """平台优雅退出：停掉全部 running 的 dev 栈（prod 不受影响，FR3-04）；状态留原值供下次启动对账。"""
    if not cfg.get("deploy.auto_stop_dev_on_exit", True):
        return
    async with sf() as s:
        rows = (await s.execute(text(
            "SELECT id, slug FROM app_project WHERE status_dev = 'running'"))).fetchall()
    for app_id, slug in rows:
        try:
            await docker("compose", "-p", f"zc-{slug}-dev", "down", timeout=120)
        except Exception:
            logger.exception("退出清理 dev 栈失败: %s", slug)
