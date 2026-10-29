# app/deploy/engine.py
"""dev 环境生命周期（规格 03）：preflight → 渲染 → 迁移 → compose up → 健康等待 → 状态落库+事件。

所有 docker 交互经 async `docker()` 封装，便于测试替身；`run_sqlchange` 与 `preflight_docker`、
两个健康探针（`wait_frontend_health` / `wait_dev_health`）一律以**模块级名字**在本模块内调用
（裁定 D-1），monkeypatch 才生效。
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
# provision_env 按模块级名字引入（同 D-1）：惰性供给在单测里可被 monkeypatch 接管，
# 否则用例会在共享测试实例上真建库建角色（副作用跨用例泄漏）。
from app.deploy.ports import explain_start_failure
from app.deploy.provision import provision_env, read_cred
from app.deploy.settings import deploy_cfg, health_timeout, preview_probe_host, preview_url
# 迁移入口按模块级名字引入（D-1）：引擎内以 bare name 调用，测试 monkeypatch engine.run_sqlchange 生效
from app.deploy.sqlrun import run_sqlchange

logger = logging.getLogger(__name__)

# 起预览时要「前端 + 后端一起 200」才报 running 的栈。vue3-fastapi 刻意不在名单里：它的后端
# （pip install + uvicorn）通常先于前端（npm install + vite）就绪，只探前端今天不成问题；而存量应用
# 里谁都可能改掉 /api/health 那条路由，给它们补一道探针等于把「能用的预览」变成 240s 后 error。
# 大型栈没这个顾虑：后端冷启动要数分钟，而它本身就是本次交付的对象。
JOINT_HEALTH_STACKS = ("vue3-springboot",)


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


def _probe_headers(port: int) -> dict:
    """探针自己带 Host 头：连的是宿主的发布端口，报给应用的却是回环名。

    Vite 5.4.12+/6 起对 Host 头做 DNS-rebinding 校验（`server.allowedHosts`）：裸 IP 与 `localhost`
    无条件放行，其余主机名一律 403「Blocked request」。而容器化部署下探针连的正是
    `host.docker.internal`（`settings.preview_probe_host`），于是**每一个**应用的 dev 预览都会等满
    健康窗口，而浏览器用 IP 访问同一个端口是 200——线上实测过这一次。
    刻意不在应用侧修：存量应用各有一份 AI 生成的 vite.config.js，改模板救不了它们，
    而探针报什么 Host 本来就是平台的事。
    """
    return {"Host": f"localhost:{port}"}


async def _all_200(port: int, paths: tuple, timeout: int, host: str | None = None) -> tuple[bool, str]:
    """轮询到超时为止：给定的一组路径全部 200 才算就绪（任一非 200 就连本轮一起不算过）。

    返回 `(是否就绪, 最后一轮的判定)`，就绪时判定是空串。判定必须带出来：非 200 与拒连在旧实现里
    都被 `except: pass` 吞成同一句「未就绪」，排障时 403 和「端口没起」长得一模一样，只能人进容器
    手搓 curl 才分得开。

    按 deadline 计时而不是「轮数 × 1s」（与 prod.wait_prod_health 同形）：一轮本身要花时间
    （httpx 单请求 2s 超时），按轮数计会把「900s」拖成十几分钟。

    `host` 是探针连应用发布端口用的宿主地址，缺省取 `preview_probe_host()`：应用端口发布在宿主上，
    平台自己跑在容器里时 127.0.0.1 那一侧永远没有监听。
    """
    target = host or preview_probe_host()
    headers = _probe_headers(port)
    last = "未探测"
    async with httpx.AsyncClient(timeout=2.0) as c:
        deadline = time.monotonic() + max(int(timeout), 1)
        while True:
            try:
                results = [(p, (await c.get(f"http://{target}:{port}{p}", headers=headers)).status_code)
                           for p in paths]
                bad = [f"{p} → {code}" for p, code in results if code != 200]
                if not bad:
                    return True, ""
                last = "；".join(bad)
            except Exception as e:
                # 端口没起来时是连接拒绝，与非 200 同判：本轮没过
                last = f"{type(e).__name__}: {e}"
            if time.monotonic() >= deadline:
                return False, last[:200]
            await asyncio.sleep(1)


async def wait_frontend_health(port: int, timeout: int,
                               host: str | None = None) -> tuple[bool, str]:
    """前端健康判定（规格 03 §4.3）：GET `<probe host>:<port>/` 拿到 200 即就绪。"""
    return await _all_200(port, ("/",), timeout, host)


async def wait_dev_health(port: int, timeout: int, backend_path: str,
                          host: str | None = None) -> tuple[bool, str]:
    """dev 联合健康：前端 `/` 与经 dev proxy 打到后端的 `backend_path` 都要 200。

    大型栈后端在容器里 `mvn spring-boot:run`，前端先起、后端后起——只探前端会出现
    「预览已就绪、后端还在编译」的假绿（规格 §5.4）。两个探针都从 dev 端口进：模板的 vite
    `server.proxy` 已把 `/api` 转发到 backend 服务，因此这里不必知道后端端口（它也没对外暴）。
    """
    return await _all_200(port, ("/", backend_path), timeout, host)


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
            cred = await _ensure_dev_cred(sf, app_id, app["slug"], notify, t0)
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
            raise DeployError(f"dev 栈启动失败: {await explain_start_failure(log, docker)}")
        await _step(app_id, notify, "✓ 容器已启动", since=t0)
        cfg = await deploy_cfg(sf)
        stack = app.get("stack") or ""
        timeout = health_timeout(cfg, stack)
        # 健康路径不抄第三份：取 app_service.HEALTH_BY_STACK（也就是写进 app.json 的那个源）；
        # deploy → services 就地取用是 prod.py 已有的惯例，提到模块顶会变成循环导入。
        from app.services.app_service import HEALTH_BY_STACK
        backend_path = (HEALTH_BY_STACK.get(stack) or "") if stack in JOINT_HEALTH_STACKS else ""
        probe = preview_probe_host()
        if backend_path:
            await _step(app_id, notify, f"→ 等待预览就绪（探测 http://{probe}:{port}/ 与后端 {backend_path}，"
                                        f"最长 {timeout}s）；"
                                        f"首次启动可能要数分钟（后端在装依赖与编译），请勿重复点「重启」")
            ok, verdict = await wait_dev_health(port, timeout, backend_path)
            fail = (f"dev 预览在健康等待期内未就绪（探测 http://{probe}:{port}，需前端 / 与后端 "
                    f"{backend_path} 同时 200，后端可能仍在编译，详情看 dev 日志）")
        else:
            await _step(app_id, notify,
                        f"→ 等待前端就绪（探测 http://{probe}:{port}/，最长 {timeout}s）")
            ok, verdict = await wait_frontend_health(port, timeout)
            fail = f"dev 前端在健康等待期内未就绪（探测 http://{probe}:{port}/）"
        if not ok:
            # 带上最后一轮的判定：403（应用答了但不应）与拒连（没人答）是两类完全不同的故障
            raise DeployError(f"{fail}；最后一轮 {verdict}")
        await _set_status(sf, app_id, "running")
        await _step(app_id, notify,
                    f"✓ 预览已就绪 {preview_url(port)}，总耗时 {time.monotonic() - t0:.1f}s")
        await _notify(notify, app_id, {"type": "dev_status", "status": "running"})
        return {"status": "running", "url": preview_url(port)}
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


async def _ensure_dev_cred(sf, app_id: int, slug: str, notify, t0: float) -> dict:
    """dev 凭据惰性供给（ADR-0015）：库里没有 `dbcred.<slug>.dev` 时现场建库建角色并登记。

    为什么需要：凭据原先只在 `app_service.create_app` 里生成一次，没有任何补建路径。把应用元数据行
    种子到另一台机器（build-output 预置应用）时，那一行不存在，旧行为是 409「请删除应用后重建」——
    在演示机上等于预置应用全是死的，而重建会连源码目录一起抹掉。

    三道守卫（缺任一条都比继续跑更便宜）：
    - 供给抛错 → 503：平台库连不上或连接账号无建库权限，属基础设施不可用，与 db_admin 的 503 口径一致；
    - 供给结果缺 db/口令 → 500：对齐 `create_app` 的同款守卫。空口令渲染进 .env 后，大型栈 compose 的
      `${VAR:?}` 会在**文件解析期**拒收，报出来的是 compose 的英文错，比这里早失败难懂得多；
    - 凭据健在时本函数根本不被调用（调用方判空），存量库的口令永不被换掉。
    """
    await _step(app_id, notify, f"→ dev 凭据缺失，现场供给分库 {slug}_dev")
    try:
        cred = await provision_env(sf, slug, "dev")
    except DeployError:
        raise                                     # 已带状态码与中文引导，原样上抛不套壳
    except Exception as e:
        raise DeployError(f"dev 分库供给失败（{slug}_dev）：{e}", status=503) from e
    if not (cred or {}).get("db") or not cred.get("password"):
        raise DeployError(f"dev 分库供给结果不完整（{slug}_dev），"
                          f"请检查平台库连接账号是否具备建库建角色权限", status=500)
    await _step(app_id, notify, f"✓ dev 分库已供给并登记凭据（{cred['db']}）", since=t0)
    return cred


async def stop_dev(sf, app: dict, notify=None) -> dict:
    """停止 dev 栈（幂等：栈本就不在时 compose 返回 0，状态同样落 stopped）。

    停之前先 ensure 一遍 deploy/：大型栈的 dev-compose 里密钥位带 `${VAR:?}`，而那是 compose 的**文件
    解析期**闸门——`.env` 被手工清空时连 `down` 都会 exit=1，于是下面这个「非零只记 warning」的收口
    会把一台还在跑的栈报成已停止（端口不释放，restart 跟着挂）。ensure 是幂等的（内容一致就不写），
    正常情形下这一步零成本；凭据照常从库里供给，缺位时 ensure 的单向推进语义会沿用 .env 既有值。
    `--remove-orphans` 补另一半：down 按文件里的服务名移除，模板改过名（redis → appredis）时旧名容器会留下。
    """
    app_id = app["id"]
    t0 = time.monotonic()
    await _step(app_id, notify, f"▶ 停止预览 {app.get('slug') or f'app#{app_id}'}（dev）")
    root = await _app_root(sf, app)
    await _notify(notify, app_id, {"type": "dev_status", "status": "stopping"})
    cred = await read_cred(sf, app["slug"], "dev") or {}
    try:
        compose_mod.ensure_deploy_files(root, _compose_meta(app), {"dev": cred})
    except Exception as e:
        # 补位是新加的抛点（只读目录、未支持的栈……），它不许把「点得动的停止」变成点不动：
        # 停栈是栈卡死时唯一的自救通道（restart 也走它），补不上位就拿现有文件照 down。
        logger.warning("停栈前补位 deploy/ 失败，按现有文件尝试 down: %s\n%s", app.get("slug"), e)
    code, log = await docker("compose", "-f", str(root / "deploy" / "dev-compose.yml"), "down",
                             "--remove-orphans", cwd=str(root))
    if code != 0:
        logger.warning("dev 栈停止命令返回非零（仍按 stopped 收口）: %s\n%s", app.get("slug"), log[-300:])
    await _set_status(sf, app_id, "stopped")
    await _notify(notify, app_id, {"type": "dev_status", "status": "stopped"})
    await _step(app_id, notify, "✓ 预览已停止", since=t0)
    return {"status": "stopped"}


async def down_app_stacks(slug: str) -> list[str]:
    """按 compose 项目名 down 掉该应用的 dev 与 prod 栈，返回未停净的告警（自身不抛）。

    与 stop_dev 的分工：那条是用户点「停止预览」（要发事件、写状态、先补 deploy/）；这条是删除应用的
    收口——记录马上就没了对谁说状态都没意义，只要把容器和端口放掉。刻意不读 deploy/*.yml：模板里
    密钥位带 `${VAR:?}`，那是 compose 的文件解析期闸门，`.env` 被清空时按文件 down 会 exit=1，
    按项目名 down 不受影响。
    """
    warnings: list[str] = []
    for env in ("dev", "prod"):
        project = f"zc-{slug}-{env}"
        try:
            code, log = await docker("compose", "-p", project, "down", timeout=120)
            if code != 0:
                warnings.append(f"{project} 未停净: {log[-200:]}")
        except Exception as e:
            warnings.append(f"{project} 停止失败: {e}")
    return warnings


async def restart_dev(sf, app: dict, notify=None) -> dict:
    await stop_dev(sf, app, notify)
    return await start_dev(sf, app, notify)


async def dev_status(sf, app: dict) -> dict:
    root = await _app_root(sf, app)
    code, log = await docker("compose", "-f", str(root / "deploy" / "dev-compose.yml"), "ps",
                             "--format", "json", cwd=str(root))
    row = await get_status_dev(sf, app["id"])
    return {"status_dev": row, "compose_ps": log if code == 0 else "",
            "url": preview_url(_dev_port(app))}


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


# compose 项目名的唯一形状 `zc-<slug>-<env>`（slug 段不含连字符，反解无歧义），与 docker 的
# compose 项目标签；标签值就是项目名，所以残留清扫不必认识容器叫什么。
STACK_PROJECT_RE = re.compile(r"^zc-([a-z][a-z0-9]{1,39})-(dev|prod)$")
COMPOSE_PROJECT_LABEL = "com.docker.compose.project"
COMPOSE_PROJECT_FORMAT = '{{.Label "com.docker.compose.project"}}'


async def sweep_deleted_stacks(known_slugs: set[str]) -> None:
    """down 掉库里已无对应应用的平台栈：已删应用留下的残留还在占宿主端口，而端口在库里已经释放了。

    只告警不抛：孤儿扫不到不影响平台起来，宁可漏扫也不要在 docker 失联时误停一切。
    """
    try:
        code, out = await docker("ps", "--filter", f"label={COMPOSE_PROJECT_LABEL}",
                                 "--format", COMPOSE_PROJECT_FORMAT, timeout=60)
    except Exception:
        logger.exception("清扫已删应用残留栈失败（docker ps 不可用），本轮跳过")
        return
    if code != 0:
        logger.warning("清扫已删应用残留栈：docker ps 退出码 %s，本轮跳过: %s", code, out[-200:])
        return
    # `docker ps` 是每容器一行，一个三容器的栈会报三遍同一个项目名——按项目去重后再 down
    for project in dict.fromkeys(line.strip() for line in (out or "").splitlines()):
        m = STACK_PROJECT_RE.fullmatch(project)
        if not m or m.group(1) in known_slugs:
            continue                                    # 非平台命名 / 应用还在，都不碰
        logger.warning("清扫已删应用残留栈: %s", project)
        try:
            await docker("compose", "-p", project, "down", timeout=120)
        except Exception:
            logger.exception("清扫已删应用残留栈失败: %s", project)


async def cleanup_orphans(sf, notify=None) -> None:
    """平台启动时：所有应用 dev 栈一律 down（崩溃残留清理），状态复位 stopped（规格 03 §5）；
    再扫一遍 Docker，把库里已无应用的残留栈一并 down——按库清理管不到已删应用，端口就漏在它们身上。"""
    async with sf() as s:
        rows = (await s.execute(text("SELECT id, slug FROM app_project"))).fetchall()
    for app_id, slug in rows:
        try:
            await docker("compose", "-p", f"zc-{slug}-dev", "down", timeout=120)
        except Exception:
            logger.exception("清理孤儿 dev 栈失败: %s", slug)
        await _set_status(sf, app_id, "stopped")
        await _notify(notify, app_id, {"type": "dev_status", "status": "stopped"})
    await sweep_deleted_stacks({slug for _, slug in rows})


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
