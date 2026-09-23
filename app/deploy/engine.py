# app/deploy/engine.py
"""dev 环境生命周期（规格 03）：preflight → 渲染 → 迁移 → compose up → 健康等待 → 状态落库+事件。

所有 docker 交互经 async `docker()` 封装，便于测试替身；`run_sqlchange` 与 `preflight_docker`、
`wait_frontend_health` 一律以**模块级名字**在本模块内调用（裁定 D-1），monkeypatch 才生效。
迁移闸门（D-2）：编排顺序 迁移 → compose up，`sqlrun.run_sqlchange` 自带「sqlchange 目录缺失
fail-closed」守卫，此处不再重复预检。
"""
import asyncio, inspect, logging, re, time
from datetime import datetime, timezone
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
    """Docker 引擎可用性前置检查：不可用抛 503 中文引导（规格 03 §5 / D9）。

    D-1（Task 11 审查遗留）：docker CLI 根本不在 PATH 时，`create_subprocess_exec` 抛的是
    FileNotFoundError（不是「子进程退出码非零」），这里同样收口成 503——REST 层才能给出
    可执行的中文引导，而不是冒 500。
    """
    try:
        code, log = await docker("info", "--format", "ok")
    except FileNotFoundError as e:
        raise DeployError(
            "Docker CLI 未安装：请安装并启动 Docker Desktop（设置页「部署环境」可一键启动）",
            status=503) from e
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


LOG_PREFIX = "[zerocode]"


async def _step(app_id, notify, message: str, *, since: float | None = None,
                level: int = logging.INFO) -> None:
    """预览启动/停止流程的步骤日志。

    双通道：① 平台日志（backend 控制台，start.bat/dev.bat 窗口看得见）；② `dev_log` 事件，
    与容器输出走同一条预览日志流，前端按行渲染即可——用户点「启动预览」后不再只对着
    starting 转圈，而是能看到「预检/渲染/迁移/起栈/健康检查」走到哪一步、各花了多久。
    `since` 给出时附「+秒数」的累计耗时（时间相关，测试只断言前缀与关键字）。
    """
    line = f"{LOG_PREFIX} {message}"
    if since is not None:
        line += f"（+{time.monotonic() - since:.1f}s）"
    logger.log(level, "%s", line)
    await _notify(notify, app_id, {"type": "dev_log", "line": line})


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
    """启动 dev 预览栈：stopped→starting→running（进入 starting 之后任一失败 → error，异常原样上抛）。

    Docker 预检**前置于任何状态变更与事件**（规格 03 §5 / 裁定 task-11-report §8.1）：不可用时不发
    starting 事件、status_dev 保持 stopped、503 中文引导原样上抛（由 REST 层映射状态码）。
    因此 `preflight_docker()` 刻意放在 try **之外**——它抛的 DeployError 不会被下面把状态改写成
    error 的 except 捕获（预检失败 = 状态零变更）。
    """
    app_id = app["id"]
    slug = app.get("slug") or f"app#{app_id}"
    app = _compose_meta(app)
    await preflight_docker()
    t0 = time.monotonic()
    await _step(app_id, notify, f"▶ 启动预览 {slug}（dev）")
    await _step(app_id, notify, "✓ Docker 预检通过", since=t0)
    try:
        await _notify(notify, app_id, {"type": "dev_status", "status": "starting"})
        await _set_status(sf, app_id, "starting")
        root = await _app_root(sf, app)
        cred = await read_cred(sf, app["slug"], "dev")
        if not cred:
            raise DeployError("dev 数据库凭据缺失，请删除应用后重建（或检查 dbcred 配置）", status=409)
        compose_mod.ensure_deploy_files(root, app, {"dev": cred})
        port = _dev_port(app)
        await _step(app_id, notify, f"✓ 已渲染 deploy/（dev-compose.yml + .env），dev 端口 {port}",
                    since=t0)
        on_log = None
        if notify:
            async def on_log(line):
                await _notify(notify, app_id, {"type": "dev_log", "line": line})
        await _step(app_id, notify, f"→ 执行 sqlchange 迁移（dev 库 {cred.get('db', '?')}）")
        mig = await run_sqlchange(root, "dev", cred, on_log=on_log)
        if not mig["ok"]:
            await _step(app_id, notify, "✗ 迁移失败，dev 栈不启动", since=t0, level=logging.WARNING)
        else:
            await _step(app_id, notify, "✓ 迁移完成", since=t0)
        await _notify(notify, app_id, {"type": "migrate_result",
                                       "env": "dev", "ok": mig["ok"], "log": mig["log"]})
        if not mig["ok"]:
            raise DeployError(f"dev 迁移失败（sqlrunner 退出码 {mig['code']}），详情见迁移日志")
        await _step(app_id, notify, "→ docker compose up -d 启动 dev 栈")
        code, log = await docker(
            "compose", "-f", str(root / "deploy" / "dev-compose.yml"), "up", "-d",
            cwd=str(root), on_line=on_log)
        if code != 0:
            raise DeployError(f"dev 栈启动失败:\n{log[-800:]}")
        await _step(app_id, notify, "✓ 容器已启动", since=t0)
        timeout = int((await deploy_cfg(sf)).get("deploy.health_timeout", 60))
        await _step(app_id, notify, f"→ 等待前端就绪 http://localhost:{port}（最长 {timeout}s）")
        ok = await wait_frontend_health(port, timeout)
        if not ok:
            raise DeployError(f"dev 前端在健康等待期内未就绪（端口 {port}）")
        await _set_status(sf, app_id, "running")
        await _step(app_id, notify,
                    f"✓ 预览已就绪 http://localhost:{port}，总耗时 {time.monotonic() - t0:.1f}s")
        await _notify(notify, app_id, {"type": "dev_status", "status": "running"})
        return {"status": "running", "url": f"http://localhost:{port}"}
    except DeployError as e:
        await _step(app_id, notify,
                    f"✗ 启动预览失败：{str(e).splitlines()[0][:200]}"
                    f"（已耗时 {time.monotonic() - t0:.1f}s）", level=logging.WARNING)
        await _set_status(sf, app_id, "error")
        await _notify(notify, app_id, {"type": "dev_status", "status": "error",
                                       "message": str(e)})
        raise
    except Exception as e:
        await _step(app_id, notify, f"✗ 启动预览失败：{e}（已耗时 {time.monotonic() - t0:.1f}s）",
                    level=logging.WARNING)
        await _set_status(sf, app_id, "error")
        await _notify(notify, app_id, {"type": "dev_status", "status": "error", "message": str(e)})
        raise DeployError(f"启动预览失败: {e}") from e


async def stop_dev(sf, app: dict, notify=None) -> dict:
    """停止 dev 栈（幂等：栈本就不在时 compose 返回 0，状态同样落 stopped）。

    停之前先 ensure 一遍 deploy/：大型栈的 dev-compose 里密钥位带 `${VAR:?}`，而那是 compose 的**文件
    解析期**闸门——`.env` 被手工清空时连 `down` 都会 exit=1，于是下面这个「非零只记 warning」的收口
    会把一台还在跑的栈报成已停止（端口不释放，restart 跟着挂）。ensure 是幂等的（内容一致就不写），
    正常情形下这一步零成本；凭据照常从库里供给，缺位时 ensure 的单向推进语义会沿用 .env 既有值。
    """
    app_id = app["id"]
    t0 = time.monotonic()
    await _step(app_id, notify, f"▶ 停止预览 {app.get('slug') or f'app#{app_id}'}（dev）")
    root = await _app_root(sf, app)
    await _notify(notify, app_id, {"type": "dev_status", "status": "stopping"})
    cred = await read_cred(sf, app["slug"], "dev") or {}
    compose_mod.ensure_deploy_files(root, _compose_meta(app), {"dev": cred})
    code, log = await docker("compose", "-f", str(root / "deploy" / "dev-compose.yml"), "down",
                             cwd=str(root))
    if code != 0:
        logger.warning("dev 栈停止命令返回非零（仍按 stopped 收口）: %s\n%s", app.get("slug"), log[-300:])
    await _set_status(sf, app_id, "stopped")
    await _notify(notify, app_id, {"type": "dev_status", "status": "stopped"})
    await _step(app_id, notify, "✓ 预览已停止", since=t0)
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


# ---------- 预览日志（「查看日志」面板的容器侧来源） ----------

_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d+))?(Z|[+-]\d{2}:\d{2})?")
# 容器内 npm/vite/pip 自己发的 ANSI 控制序列：`compose --no-color` 只管 compose 自己的前缀着色，
# 管不到子进程；面板是 <pre>，转义字节会原样显示成 `[33m` 之类乱码，所以入口就剥掉。
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07")


def _split_ts(s: str) -> tuple[float | None, str, str]:
    """剥掉开头的 ISO 时间戳，返回 (epoch, ISO, 剩余文本)；不是时间戳开头则 (None, "", 原文)。

    fromisoformat 吃不上下纳秒（>6 位小数），先截到微秒；解析不出来当作没有时间戳——
    归并时这类行沉到末尾，宁可时间位置不精确，也不把用户的日志丢掉。
    """
    t = s.strip()
    m = _TS_RE.match(t)
    if not m:
        return None, "", t
    frac = (m.group(2) or "")[:6]
    stamp = m.group(1) + (f".{frac}" if frac else "") + (m.group(3) or "Z")
    try:
        dt = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None, "", t
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp(), dt.isoformat(), t[m.end():].lstrip()


def _clean_service(name: str) -> str:
    """compose 的前缀是 `backend-1   `（副本序号 + 对齐空格）：显示成 `backend` 就够。"""
    return re.sub(r"-\d+$", "", name.strip())


def parse_compose_logs(out: str, tail: int = 200) -> list[dict]:
    """容器输出转成行记录 → {source: container, service, ts, line}。

    compose 各版本前缀形态不一致，实测三种都要认（面板里既不能重复一串纳秒，也不能把
    服务名丢进正文）：
      A `backend-1   | 2026-09-20T16:56:16.131035319Z 文本`  ← 本机 compose 实测（时间戳在竖线**后**）
      B `2026-09-20T14:27:58.123456789Z backend  | 文本`     ← 时间戳在竖线前
      C `2026-...Z 文本` / 纯文本                             ← 无竖线（compose 自己的进度行等）
    分隔符只切一次：消息正文里再出现 ` | ` 不会被继续切开。
    """
    rows: list[dict] = []
    for raw in out.splitlines():
        raw = _ANSI_RE.sub("", raw)                        # 子进程自己的转义序列，面板不认
        if not raw.strip():
            continue
        left, sep, right = raw.partition(" | ")
        if sep:
            l_epoch, l_iso, l_rest = _split_ts(left)
            if l_epoch is not None:                       # 形态 B
                service, epoch, iso, body = _clean_service(l_rest), l_epoch, l_iso, right.strip()
            else:                                         # 形态 A
                service, epoch, iso, body = _clean_service(left), None, "", right.strip()
        else:
            service = ""
            epoch, iso, body = _split_ts(raw)
        if epoch is None:                                 # 时间戳可能还挂在正文头部（形态 A）
            epoch, iso, body = _split_ts(body)
        rows.append({"source": "container", "service": service, "ts": iso, "epoch": epoch,
                     "line": body.rstrip()})
    return rows[-max(int(tail), 1):]


def merge_logs(*groups: list[dict]) -> list[dict]:
    """多来源日志按时间归并：同刻保持组内原序（stable），无时间戳的行沉底；`epoch` 只用于排序不外传。"""
    flat = [r for g in groups for r in g]
    flat.sort(key=lambda r: (r.get("epoch") is None, r.get("epoch") or 0.0))
    return [{k: v for k, v in r.items() if k != "epoch"} for r in flat]


async def container_logs(sf, app: dict, tail: int = 200) -> tuple[list[dict], str]:
    """dev 栈的容器输出（含应用自己打的启动 banner）。

    看日志是**排障入口**，它自己不许 500：Docker 没开 / 命令失败 / 非零退出一律降级成
    `([], 中文提示)`，让平台侧的步骤行照样返回——那正是「应用起不来时唯一看得见的东西」。
    """
    try:
        root = await _app_root(sf, app)
        code, out = await docker("compose", "-f", str(root / "deploy" / "dev-compose.yml"),
                                 "logs", "--no-color", "--timestamps", "--tail", str(int(tail)),
                                 cwd=str(root), timeout=60)
    except Exception as e:        # DeployError（含 504 超时）与 FileNotFoundError（CLI 没装）都要收口
        logger.warning("读取 dev 容器日志失败: %s", e)
        return [], f"容器日志暂不可用：{str(e).splitlines()[0][:200]}"
    if code != 0:
        return [], f"容器日志暂不可用（docker compose logs 退出码 {code}）"
    rows = parse_compose_logs(out, tail)
    if not rows:
        return [], "dev 栈暂无容器输出（预览未运行，或应用还没打印日志）"
    return rows, ""


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
