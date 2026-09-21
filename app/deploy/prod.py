# app/deploy/prod.py
"""prod 生命周期与发布（规格 04）：状态机 stopped|starting|publishing|running|unhealthy|error。
docker 交互全部转 engine.docker（单一实现，终审 M-资源面）；事件走 notify（hub.publish）。
prod 起栈**不做迁移**（07：迁移只发生在发布与预览启动两个入口，日常重启不重放）。

D-1 模块级名字纪律（同 engine）：对 engine/compose/provision 的复用一律
`from ... import name` 后以 bare name 调用，测试 monkeypatch prod.<name> 才生效；
`_docker` 是 engine.docker 的薄包装（模块级可替身）。
"""
import asyncio, logging, time
from pathlib import Path
import httpx
from sqlalchemy import text
from app.deploy import engine, records
from app.deploy.compose import ensure_deploy_files
from app.deploy.errors import DeployError
from app.deploy.engine import (preflight_docker, _app_root, _compose_meta, _notify,
                               LOG_PREFIX)
from app.deploy.provision import read_cred
from app.deploy.settings import deploy_cfg

logger = logging.getLogger(__name__)

# T7/T8 依赖的状态列契约（04 §4.1 五态 + starting：恢复场景需要进行中反馈）
STATUS_COL = "status_prod"
STATUSES = ("stopped", "starting", "publishing", "running", "unhealthy", "error")


def _prod_port(app: dict):
    ports = app.get("ports")
    if isinstance(ports, dict) and ports.get("prod") is not None:
        return ports["prod"]
    return app.get("prod_port")


async def set_status(sf, app_id: int, status: str) -> None:
    async with sf() as s:
        await s.execute(text(
            "UPDATE app_project SET status_prod = :st, updated_at = now() WHERE id = :i"),
            {"st": status, "i": app_id})
        await s.commit()


async def get_status(sf, app_id: int) -> str:
    async with sf() as s:
        return (await s.execute(text(
            "SELECT status_prod FROM app_project WHERE id = :i"), {"i": app_id})).scalar() or "stopped"


async def _step(app_id, notify, message: str, *, since: float | None = None,
                level: int | None = None) -> None:
    """prod 步骤行：与 engine._step 同形态（dev_log 事件，前端日志面板同源），独立 prod 日志源。
    since 给出时附「+秒数」累计耗时（brief 原稿收了该参数却没用——死参收口）。"""
    line = f"{LOG_PREFIX} {message}"
    if since is not None:
        line += f"（+{time.monotonic() - since:.1f}s）"
    logger.log(level or logging.INFO, "%s", line)
    await _notify(notify, app_id, {"type": "dev_log", "line": line})


async def _docker(*args, cwd=None, on_line=None, timeout=600):
    return await engine.docker(*args, cwd=cwd, on_line=on_line, timeout=timeout)


async def image_exists(tag: str) -> bool:
    code, _ = await _docker("image", "inspect", tag, timeout=30)
    return code == 0


async def wait_prod_health(root: Path, port: int, timeout: int,
                           stack: str = "vue3-fastapi") -> tuple[bool, dict]:
    """联合健康（04 §4.3 最简路径）：GET / 200 且（非 static）GET /api/health 200——
    /api 由 T3b 的 nginx 反代进后端，单端口即前后端联合判定。
    root 形参当前探针不用（http 走 127.0.0.1）——保留供 T7 日志取用（控制者裁定，不删）。"""
    checks = {"frontend": False, "api": stack == "static"}
    async with httpx.AsyncClient(timeout=3.0) as c:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                checks["frontend"] = (await c.get(f"http://127.0.0.1:{port}/")).status_code == 200
            except Exception:
                checks["frontend"] = False
            if checks["frontend"] and stack != "static":
                try:
                    checks["api"] = (await c.get(f"http://127.0.0.1:{port}/api/health")).status_code == 200
                except Exception:
                    checks["api"] = False
            if checks["frontend"] and checks["api"]:
                return True, checks
            await asyncio.sleep(1)
    return False, checks


def _prod_compose_args(root: Path):
    return ("compose", "-f", str(root / "deploy" / "prod-compose.yml"))


async def prod_start(sf, app: dict, notify=None) -> dict:
    app_id = app["id"]
    slug = app.get("slug") or f"app#{app_id}"
    await preflight_docker()          # 预检前置于任何状态变更与事件（Task 11/12 契约：零事件、503 立答）
    t0 = time.monotonic()
    await _step(app_id, notify, f"▶ 启动正式环境 {slug}")
    try:
        await set_status(sf, app_id, "starting")
        await _notify(notify, app_id, {"type": "prod_status", "status": "starting"})
        app = _compose_meta(app)
        root = await _app_root(sf, app)
        cred = await read_cred(sf, app["slug"], "prod")
        if not cred:
            raise DeployError("尚未发布过：prod 数据库凭据不存在，请先点「发布」", status=409)
        from app.services import app_service
        cur = (await app_service.get_app(sf, app_id)).get("current_version")
        if not cur:
            raise DeployError("应用还没有发布版本，无法启动正式环境", status=409)
        # D-T4：dev 侧同样真读凭据再传（空 dict 直传会让渲染器把「没带」误当供给缺位的保留语义）
        cred_dev = await read_cred(sf, app["slug"], "dev") or {}
        ensure_deploy_files(root, app, {"dev": cred_dev, "prod": cred}, version=cur)
        await _step(app_id, notify, f"→ docker compose up -d 启动 prod 栈（版本 {cur}）")
        code, log = await _docker(*_prod_compose_args(root), "up", "-d", cwd=str(root))
        if code != 0:
            raise DeployError(f"正式栈启动失败:\n{log[-800:]}")
        timeout = int((await deploy_cfg(sf)).get("deploy.health_timeout", 240))
        ok, checks = await wait_prod_health(root, _prod_port(app), timeout, app.get("stack", ""))
        if not ok:
            raise DeployError(f"正式环境体检未通过（{checks}），容器保持运行供排查")
        await set_status(sf, app_id, "running")
        await _notify(notify, app_id, {"type": "prod_status", "status": "running"})
        await _step(app_id, notify, f"✓ 正式环境已就绪 http://localhost:{_prod_port(app)}", since=t0)
        return {"status": "running"}
    except DeployError as e:
        await set_status(sf, app_id, "error")
        await _notify(notify, app_id, {"type": "prod_status", "status": "error", "message": str(e)})
        await _step(app_id, notify, f"✗ 启动失败：{str(e).splitlines()[0][:200]}", since=t0,
                    level=logging.WARNING)
        raise


async def prod_stop(sf, app: dict, notify=None) -> dict:
    app_id = app["id"]
    t0 = time.monotonic()
    await _step(app_id, notify, f"▶ 停止正式环境 {app.get('slug') or app_id}")
    root = await _app_root(sf, _compose_meta(app))
    code, log = await _docker(*_prod_compose_args(root), "down", cwd=str(root))
    if code != 0:
        logger.warning("prod 栈停止返回非零（仍按 stopped 收口）: %s", log[-300:])
    await set_status(sf, app_id, "stopped")
    await _notify(notify, app_id, {"type": "prod_status", "status": "stopped"})
    await _step(app_id, notify, "✓ 正式环境已停止（数据保留在平台数据库中）", since=t0)
    return {"status": "stopped"}


async def prod_restart(sf, app: dict, notify=None) -> dict:
    try:
        await prod_stop(sf, app, notify)
    except Exception:
        pass
    return await prod_start(sf, app, notify)


async def prod_status(sf, app: dict) -> dict:
    root = await _app_root(sf, _compose_meta(app))
    code, ps = await _docker(*_prod_compose_args(root), "ps", "--format", "json", cwd=str(root))
    from app.deploy import nics
    return {"status_prod": await get_status(sf, app["id"]),
            "compose_ps": ps if code == 0 else "",
            **(await nics.app_urls(sf, _compose_meta(app)))}


async def reconcile(sf, notify=None) -> None:
    """平台启动对账（04 §6）：中断的发布记录标 failed；publishing/starting 残留态按容器实况复位。"""
    n = await records.mark_interrupted(sf)
    if n:
        logger.warning("上次发布中断，已标记 %d 条", n)
    async with sf() as s:
        rows = (await s.execute(text(
            "SELECT id, slug FROM app_project WHERE status_prod IN "
            "('publishing','starting','running','unhealthy','error')"))).fetchall()
    for app_id, slug in rows:
        try:
            code, out = await _docker("compose", "-p", f"zc-{slug}-prod", "ps", "-q", timeout=60)
            # I-1：engine.docker 以 stderr=STDOUT 合流——daemon 失联时 exit 1 且 out 是错误文本。
            # 只看输出会把五态残留误升 running；以退出码为准，非零视为无容器（与异常分支同向）。
            up = code == 0 and bool(out.strip())
        except Exception:
            up = False
        new = "running" if up else "stopped"
        await set_status(sf, app_id, new)
        await _notify(notify, app_id, {"type": "prod_status", "status": new})
