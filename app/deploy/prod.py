# app/deploy/prod.py
"""prod 生命周期与发布（规格 04）：状态机 stopped|starting|publishing|running|unhealthy|error。
docker 交互全部转 engine.docker（单一实现，终审 M-资源面）；事件走 notify（hub.publish）。
prod 起栈**不做迁移**（07：迁移只发生在发布与预览启动两个入口，日常重启不重放）。

D-1 模块级名字纪律（同 engine）：对 engine/compose/provision 的复用一律
`from ... import name` 后以 bare name 调用，测试 monkeypatch prod.<name> 才生效；
`_docker` 是 engine.docker 的薄包装（模块级可替身）。
"""
import asyncio, logging, re as _re, time
from datetime import datetime
from pathlib import Path
import httpx
from sqlalchemy import text
from app.deploy import engine, records
from app.deploy import gateway_routes
from app.deploy.compose import ensure_deploy_files
from app.deploy.errors import DeployError
from app.deploy.engine import (preflight_docker, _app_root, _compose_meta, _notify,
                               LOG_PREFIX)
from app.deploy.provision import read_cred, provision_env
from app.deploy.settings import deploy_cfg
from app.deploy.sqlrun import run_sqlchange
from app.deploy.schemacheck import verify_dev_matches_files, parity_block_message
from app.deploy import schemadiff
from app.deploy.schemadiff import destructive_changes, destructive_message

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
    # 状态落库后旁路刷新网关路由表（running 进表、其余出表）；失败只告警不回滚业务
    await gateway_routes.refresh(sf)


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
    """停止 prod 栈（幂等：栈本就不在时 compose 返回 0，状态同样落 stopped）。

    停之前先 ensure 一遍 deploy/，与 stop_dev 同构：大型栈的 prod-compose 密钥位带 `${VAR:?}`，
    那是 compose 的**文件解析期**闸门——`.env` 被手工清空时连 `down` 都会 exit=1，于是下面这个
    「非零只记 warning」的收口会把一台还在跑、仍占端口的栈报成已停止。ensure 是幂等的（内容一致就
    不写、缺位密钥现场生成、version 缺位保留既有 PROD_VERSION），正常情形这一步零成本；补不上位
    （只读目录等）时按现有文件照 down，不许把「点得动的停止」变成点不动。
    """
    app_id = app["id"]
    t0 = time.monotonic()
    await _step(app_id, notify, f"▶ 停止正式环境 {app.get('slug') or app_id}")
    app = _compose_meta(app)
    root = await _app_root(sf, app)
    cred = await read_cred(sf, app["slug"], "prod") or {}
    cred_dev = await read_cred(sf, app["slug"], "dev") or {}
    try:
        ensure_deploy_files(root, app, {"dev": cred_dev, "prod": cred})
    except Exception as e:
        # 补位是新加的抛点，它不许把「点得动的停止」变成点不动：停栈是栈卡死时唯一的自救通道
        # （prod_restart 也走它），补不上位就拿现有文件照 down。
        logger.warning("停栈前补位 deploy/ 失败，按现有文件尝试 down: %s\n%s", app.get("slug"), e)
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


# ===================== Task 7：发布流水线（publish 七步 / 自动回退 / rollback / keep-5）=====================


def compute_schema_diff(root: Path, prev_version: str | None) -> dict | None:
    """结构轨破坏性 diff 的唯一计算口：上一版归档快照 vs 当前 schema.sql。

    缺基线（prev_version 空 / 无归档 / 无当前快照）→ None（无从比较）。破坏性闸门
    与回填脚手架端点共用，避免路径口径漂移。
    """
    if not prev_version:
        return None
    snap_cur = root / "schema" / "schema.sql"
    snap_prev = root / "releases" / prev_version / "schema.sql"
    if not (snap_cur.is_file() and snap_prev.is_file()):
        return None
    return schemadiff.schema_diff(
        schemadiff.parse_schema(snap_prev.read_text(encoding="utf-8")),
        schemadiff.parse_schema(snap_cur.read_text(encoding="utf-8")))

# 04 §4.1 状态机进度（前端进度条按此顺序点亮；record 收口在记账之后，故最后一步是记账）
STEPLIST = ("preflight", "build", "provision", "migrate", "deploy", "health", "record")
# 迁移文件名规范（07 迁移闸门 + 全局约束「注释不得含分号」）：安全名=词/连字符/点/汉字，不含分号
# 数据轨 D* 与遗留结构轨 V* 均合法（P2 双轨；破坏性变更由 diff 闸门拦，不靠命名）
_SQL_NAME = _re.compile(r"^[VD]\d{8}_\d{2}_[\w\-.\u4e00-\u9fff]+\.sql$")


def next_version() -> str:
    """版本号 `b<本地 YYYYMMDDHHmmss>`：字符串字典序即时间序（04 §4.5）。"""
    return datetime.now().strftime("b%Y%m%d%H%M%S")


def scan_sqlchange(root: Path) -> list[str]:
    """sqlchange 目录里不合命名规范的文件名清单（preflight 闸门的数据源；目录不存在=无违规）。"""
    d = root / "sqlchange"
    if not d.is_dir():
        return []
    return sorted(p.name for p in d.iterdir()
                  if p.suffix == ".sql" and not _SQL_NAME.match(p.name))


async def _step_ev(app_id, notify, rid_step_cb, step: str, state: str) -> None:
    """publish_step 事件（04 §5 逐字 {step,state}）+ 记账游标推进。

    state 只发 running|ok：失败统一由 publish_done 收口（③裁定——事件精简，
    前端不必在两条通道上各自判定失败）。
    """
    await _notify(notify, app_id, {"type": "publish_step", "step": step, "state": state})
    if rid_step_cb:
        await rid_step_cb(step)


async def _ensure_prod_proxy_conf(root: Path) -> bool:
    """存量应用补齐生产反代接线（T3b 之前创建的工程没有 nginx.conf，更没把它 COPY 进镜像）。

    终审 C-1：只补 conf 不够——M1 存量 frontend/Dockerfile 没有 `COPY nginx.conf` 那行，
    构建会**静默成功**并产出 stock nginx 配置（无 /api 反代），体检 api 探针 404 直到超时。
    故 conf 补齐后顺带幂等补 Dockerfile 接线；两者任一发生即返回 True（播报补齐事件）。
    模板源取 `app_service.TEMPLATES`——平台唯一写入方就地补文件，不造 parents 链（控制者裁定）。
    """
    from app.services.app_service import TEMPLATES
    fe = root / "frontend"
    conf = fe / "nginx.conf"
    patched = False
    if fe.is_dir() and not conf.is_file():
        src = TEMPLATES / "vue3-fastapi" / "frontend" / "nginx.conf"
        if not src.is_file():
            raise DeployError(f"生产反代模板缺失：{src}", status=500)
        conf.write_text(src.read_text(encoding="utf-8"), encoding="utf-8", newline="")
        patched = True
    if fe.is_dir() and _wire_dockerfile_proxy(fe / "Dockerfile"):
        patched = True
    return patched


def _wire_dockerfile_proxy(dockerfile: Path) -> bool:
    """M1 存量 frontend/Dockerfile 缺 `COPY nginx.conf`：在 nginx 基座 FROM 行后补一行（幂等）。

    只对**平台模板形状**出手（FROM nginx* 且无该 COPY 指令）；形状不认识就原样放过，
    不虚补也不报错——应用自带的反代接线由应用自己负责（conf 已有测试钉住不覆盖）。
    """
    if not dockerfile.is_file():
        return False
    lines = dockerfile.read_text(encoding="utf-8").splitlines()
    if any("nginx.conf" in ln and ln.strip().upper().startswith("COPY") for ln in lines):
        return False
    for i, ln in enumerate(lines):
        if ln.strip().upper().startswith("FROM NGIN"):
            lines.insert(i + 1, "COPY nginx.conf /etc/nginx/conf.d/default.conf")
            dockerfile.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="")
            return True
    return False


async def publish(sf, app: dict, notify=None, *, force: bool = False,
                  confirm_destructive: bool = False) -> dict:
    """七步发布（04 §4.1）：旧容器全程不动，体检过了才切 current_version（§4.3 失败自动回退）。

    force 是端点层 chat 闸门（FR4-09）的透传位——引擎不查 hub，「发布进行中/有活跃会话」的 409
    由端点判（分层干净）；这里保留形参以免端点侧出现签名漂移。

    **publish_done.version 语义（M-c 钉死，T10 契约以此为准，勿再各自猜）**：本字段只回答
    「库里晋升在服的是哪一版」，不回答「容器里物理跑着哪一版」——
      · 成功 → 新版本（此刻二者一致，晋升已发生）；
      · 失败 → prev_version（失败路径一律不晋升，故回指仍登记在服的旧版；回退成功亦是它）；
      · 首版失败（prev 为空）→ None：从来没有过在服版本，物理上跑着的那个不健康新容器
        由 status=unhealthy 承载、由 records.detail 承载，不借 version 字段虚报「已晋升」。
    即：version 与 current_version 同源同值，前端可无条件用它刷新卡片。
    """
    app_id = app["id"]
    slug = app["slug"]
    await preflight_docker()          # 预检前置于一切状态与事件（Task 11/12：零事件、503 立答）
    app = _compose_meta(app)
    root = await _app_root(sf, app)
    version = next_version()
    from app.services import app_service
    prev_version = (await app_service.get_app(sf, app_id)).get("current_version")
    rid = await records.begin(sf, app_id, "publish", version)
    t0 = time.monotonic()

    async def rst(step):
        await records.set_step(sf, rid, step)

    def done_payload(ok: bool, error: str = "", payload: dict | None = None) -> dict:
        # 失败时 version 回指 prev_version：前端卡片继续显示「在服务的版本」，不虚报新版本
        out = {"type": "publish_done", "ok": ok, "version": version if ok else prev_version,
               "url": f"http://localhost:{_prod_port(app)}", "error": error}
        if payload:
            out["code"] = payload.get("code")            # 本期恒 "schema_destructive"
            out["destructive"] = payload.get("destructive")
        return out

    try:
        await set_status(sf, app_id, "publishing")
        await _notify(notify, app_id, {"type": "prod_status", "status": "publishing"})
        # 1 preflight：命名规范 + 工程件齐备（旧栈原样在跑，此时失败=零影响）
        await _step_ev(app_id, notify, rst, "preflight", "running")
        bad = scan_sqlchange(root)
        if bad:
            raise DeployError(f"sqlchange 文件名不规范：{bad}（应为 V<日期>_<两位序号>_<说明>.sql）")
        if not (root / "app.json").is_file():
            raise DeployError("app.json 缺失，无法发布")
        if await _ensure_prod_proxy_conf(root):
            await _step(app_id, notify, "✓ 已为存量应用补齐 frontend 生产反代（nginx.conf / Dockerfile COPY 接线）")
        # 1.x dev/prod 结构等价校验（P1 止血；并入 preflight，不改步序）：
        #     dev 实际结构必须等于 fold(sqlchange 文件)，否则视为漂移，硬阻断发布。
        _skip_verify = str((await deploy_cfg(sf)).get("deploy.skip_schema_parity", "")).lower() in ("1", "true", "yes")
        if _skip_verify:
            await _step(app_id, notify, "⚠ 已跳过 dev/prod 结构等价校验（deploy.skip_schema_parity）")
        else:
            _vp = await verify_dev_matches_files(sf, root, slug)
            _block = parity_block_message(_vp)
            if _block:
                raise DeployError(_block)
            await _step(app_id, notify, "✓ dev 结构与 sqlchange 文件等价校验通过")
        # 1.y 结构破坏性闸门（P2→P3 可确认）：上一版归档快照 vs 当前 schema.sql，
        #     命中删/改名/收窄即阻断；confirm_destructive（一次性）或持久 skip 才放行。
        _skip_destr = str((await deploy_cfg(sf)).get("deploy.skip_schema_destructive", "")).lower() in ("1", "true", "yes")
        if _skip_destr:
            await _step(app_id, notify, "⚠ 已跳过结构破坏性闸门（deploy.skip_schema_destructive 持久兜底）")
        elif confirm_destructive:
            await _step(app_id, notify, "⚠ 破坏性变更已由人工确认放行（本次一次性，不改持久配置）")
        else:
            d = compute_schema_diff(root, prev_version)
            if d is not None:
                msg = destructive_message(d)
                if msg:
                    raise DeployError(msg, payload={
                        "code": "schema_destructive",
                        "destructive": destructive_changes(d),
                        "diff": d})
                await _step(app_id, notify, "✓ 结构破坏性校验通过（相对上一版无删/改名/收窄）")
        await _step_ev(app_id, notify, rst, "preflight", "ok")
        # 2 build（旧容器不动——04 §4.1；渲染先于 build：prod-compose.yml 的落盘条件已放宽为栈判定）
        await _step_ev(app_id, notify, rst, "build", "running")

        async def blog(line):
            await _notify(notify, app_id, {"type": "build_log", "line": line})

        compose_file = str(root / "deploy" / "prod-compose.yml")
        # D-T4：dev 侧凭据真读再传（渲染器对「没带」的语义是保留既有 DEV_*，谎称没带会错位）；
        # 读空=数据异常（provision 从未跑过或被清），显式 500，不静默传空 dict 让发布继续吃灰。
        cred_dev = await read_cred(sf, slug, "dev")
        if cred_dev is None:
            raise DeployError("dev 凭据异常：应用缺 dev 数据库凭据（未供给或已被清理），"
                              "请先在预览环境启动一次再发布", status=500)
        cred0 = await read_cred(sf, slug, "prod")
        ensure_deploy_files(root, app, {"dev": cred_dev, "prod": cred0 or {}}, version=version)
        code, log = await _docker("compose", "-f", compose_file, "build", cwd=str(root), on_line=blog)
        if code != 0:
            raise DeployError(f"镜像构建失败:\n{log[-1500:]}")
        await _step_ev(app_id, notify, rst, "build", "ok")
        # 3 provision（幂等复用 M1 供给；prod 凭据在这里补齐，随后重渲染把真值写进 .env）
        await _step_ev(app_id, notify, rst, "provision", "running")
        cred = await provision_env(sf, slug, "prod")
        ensure_deploy_files(root, app, {"dev": cred_dev, "prod": cred}, version=version)
        await _step_ev(app_id, notify, rst, "provision", "ok")
        # 4 migrate（prod 库自有 sqlchange_history，增量语义同 dev；不过应用不起）
        await _step_ev(app_id, notify, rst, "migrate", "running")
        mig = await run_sqlchange(root, "prod", cred, on_log=blog)
        if not mig["ok"]:
            raise DeployError(f"prod 迁移失败（退出码 {mig['code']}），旧版本继续服务")
        sql_applied = [ln.split()[1] for ln in mig["log"].splitlines()
                       if ln.startswith("OK ") and len(ln.split()) > 1]
        await _step_ev(app_id, notify, rst, "migrate", "ok")
        # 5 deploy：同 tag 已在 build 产出，up -d 只做切换（migrate 闸门已在 compose 内）
        await _step_ev(app_id, notify, rst, "deploy", "running")
        code, log = await _docker("compose", "-f", compose_file, "up", "-d", cwd=str(root),
                                  on_line=blog)
        if code != 0:
            raise DeployError(f"切换新版本失败:\n{log[-800:]}")
        await _step_ev(app_id, notify, rst, "deploy", "ok")
        # 6 health：失败自动回退旧 tag（04 §4.3 / S-04-2）——程序回退，数据库永不回滚
        await _step_ev(app_id, notify, rst, "health", "running")
        timeout = int((await deploy_cfg(sf)).get("deploy.health_timeout", 240))
        ok, checks = await wait_prod_health(root, _prod_port(app), timeout, app.get("stack", ""))
        if not ok:
            has_prev = bool(prev_version)
            rolled_back = has_prev and await image_exists(
                f"zc-{slug}-frontend:prod-{prev_version}")
            rb_fail = ""
            if rolled_back:
                await _step(app_id, notify, "✗ 新版本体检未通过，自动回退上一版本", level=logging.WARNING)
                # 回退=按旧版本号重渲染（PROD_VERSION 落回旧 tag）后再 up：顺序反了就起错栈
                ensure_deploy_files(root, app, {"dev": cred_dev, "prod": cred},
                                    version=prev_version)
                code2, log2 = await _docker("compose", "-f", compose_file, "up", "-d", cwd=str(root))
                rolled_back = code2 == 0
                if rolled_back:
                    await set_status(sf, app_id, "running")
                    await _notify(notify, app_id, {"type": "prod_status", "status": "running"})
                else:
                    # 04 §6「若回退也失败 → error，展示两个版本的日志入口」：新旧两版都不可服务，
                    # 既不能谎报 running 也不能只标 unhealthy（那是「旧版还在跑」的口径）——标 error，
                    # 两版号并列进文案，日志入口收敛到同一条记账（rid，含各自 up/build 尾行）。
                    rb_fail = (f"回退也失败：新版本 {version} 体检未通过、"
                               f"旧版本 {prev_version} 起栈退出码 {code2}，两版日志入口见记录 {rid}")
                    await set_status(sf, app_id, "error")
                    await _notify(notify, app_id, {"type": "prod_status", "status": "error",
                                                   "message": f"{rb_fail}（体检 {checks}）"})
                    await _step(app_id, notify, rb_fail, level=logging.WARNING)
            else:
                # 无回退目标（首版，或旧版镜像已被 keep-5 清掉）：新容器留着供排查，标 unhealthy
                await set_status(sf, app_id, "unhealthy")
                await _notify(notify, app_id, {"type": "prod_status", "status": "unhealthy"})
            why = ("已回退上一版本" if rolled_back
                   else rb_fail if rb_fail
                   else "上一版本镜像已清理，无回退目标" if has_prev
                   else "首次发布无旧版可回退")
            await records.finish(sf, rid, "failed", step="health",
                                 error=f"体检未通过 {checks}；{why}"
                                       + (f"；回退输出尾：{log2[-400:]}" if rb_fail else ""))
            await _notify(notify, app_id, done_payload(
                False, f"新版本体检未通过（{checks}），"
                       + ("已回退并保持上一版本运行" if rolled_back
                          else f"自动回退失败（{version} / {prev_version}），两版日志入口见记录 {rid}"
                          if rb_fail
                          else f"无回退目标（{why}），标 unhealthy 供排查")))
            return {"status": "running" if rolled_back else "error" if rb_fail else "unhealthy",
                    "version": prev_version, "url": "", "recordId": rid}
        await _step_ev(app_id, notify, rst, "health", "ok")
        # 7 record：体检通过才推进 current_version（版本闸门的另一半含义），随后 keep-5 清镜像
        await _step_ev(app_id, notify, rst, "record", "running")
        async with sf() as s:
            await s.execute(text(
                "UPDATE app_project SET current_version=:v, status_prod='running', "
                "updated_at=now() WHERE id=:i"), {"v": version, "i": app_id})
            await s.commit()
        # P2：发布成功即归档本版结构快照（releases/<version>/schema.sql），作为下次破坏性 diff 基线。
        # 文件存、不碰平台库；归档失败不改判发布成败（下次缺基线时闸门自动跳过）。
        try:
            _snap = root / "schema" / "schema.sql"
            if _snap.is_file():
                _dst = root / "releases" / version
                _dst.mkdir(parents=True, exist_ok=True)
                (_dst / "schema.sql").write_text(_snap.read_text(encoding="utf-8"),
                                                 encoding="utf-8", newline="")
        except Exception as se:  # noqa: BLE001
            await _step(app_id, notify, f"✗ schema 快照归档失败（不影响发布结果）：{se}",
                        level=logging.WARNING)
        # 终审 I-1：晋升+体检通过即不可撤销点——之后的收尾（URL/README/记账/清镜像）各自圈稳，
        # 任何一步抛只降级为 warning，绝不再改判成败（finish 的 success 守卫是记账层兜底）。
        from app.deploy import nics
        try:
            urls = await nics.app_urls(sf, app)
            url = urls["domain"]
        except Exception as ue:  # noqa: BLE001
            url = f"https://{slug}.{nics.GATEWAY_DOMAIN}"
            await _step(app_id, notify, f"✗ 发布 URL 汇总失败（不影响发布结果，排查稍后看 prod/status）：{ue}",
                        level=logging.WARNING)
        try:
            nics.update_readme(root, url, version)
        except Exception as re_:  # noqa: BLE001  README 属应用工作区文件（编码可能被用户改坏），读挂只警
            await _step(app_id, notify, f"✗ README 发布块刷新失败（不影响发布结果）：{re_}",
                        level=logging.WARNING)
        await records.finish(sf, rid, "success", step="record", detail={
            "images": [f"zc-{slug}-frontend:prod-{version}", f"zc-{slug}-backend:prod-{version}"],
            "sqlApplied": sql_applied, "durationMs": int((time.monotonic() - t0) * 1000),
            "health": checks, "url": url})
        try:
            await cleanup_images(sf, app_id, slug, notify=notify)   # keep-N：发布收尾顺手清旧镜像
        except Exception as ce:  # noqa: BLE001  清理本就有下次发布兜底，抛上来不把成功改判
            await _step(app_id, notify, f"✗ 旧镜像清理异常（下次发布兜底重试）：{ce}",
                        level=logging.WARNING)
        await _step_ev(app_id, notify, None, "record", "ok")
        await _notify(notify, app_id, {"type": "prod_status", "status": "running"})
        await _step(app_id, notify, f"✓ 发布完成 {version}，访问 {url}"
                    f"（耗时 {time.monotonic() - t0:.1f}s）")
        out = done_payload(True)
        out["url"] = url
        await _notify(notify, app_id, out)
        return {"status": "running", "version": version, "url": url, "recordId": rid}
    except DeployError as e:
        # 一切失败收口：记账 failed → publishing 残留复位 error → 播报 → publish_done(ok=false)
        await records.finish(sf, rid, "failed", error=str(e)[:2000])
        if await get_status(sf, app_id) == "publishing":
            await set_status(sf, app_id, "error")
            await _notify(notify, app_id, {"type": "prod_status", "status": "error",
                                           "message": str(e)})
        await _step(app_id, notify, f"✗ 发布失败：{str(e).splitlines()[0][:200]}",
                    level=logging.WARNING)
        await _notify(notify, app_id, done_payload(False, str(e)[:500], getattr(e, "payload", None)))
        raise
    except Exception as e:
        await records.finish(sf, rid, "failed", error=f"{type(e).__name__}: {e}")
        await set_status(sf, app_id, "error")
        await _notify(notify, app_id, {"type": "prod_status", "status": "error", "message": str(e)})
        await _notify(notify, app_id, done_payload(False, str(e)[:500]))
        raise DeployError(f"发布失败: {e}") from e


async def rollback(sf, app: dict, version: str, notify=None) -> dict:
    """一键回滚（FR4-04）：只切程序（旧 tag 重渲染 + up -d），**数据库永不回滚**（07/D8）。"""
    app_id = app["id"]
    slug = app["slug"]
    await preflight_docker()
    app = _compose_meta(app)

    async def _reject(message: str, status: int):
        """记账与状态变更之前的闸门失败（400/409 由端点立答）：引擎侧也收口 publish_done，
        避免端点把它放后台执行时留下 202 黑洞（与「不留悬空态」同一条纪律）。"""
        await _notify(notify, app_id, {"type": "publish_done", "ok": False, "version": version,
                                       "url": "", "error": message})
        raise DeployError(message, status=status)

    for role in ("frontend", "backend"):
        if not await image_exists(f"zc-{slug}-{role}:prod-{version}"):
            await _reject(f"该版本镜像已清理（{role}），无法回滚", 400)
    root = await _app_root(sf, app)
    cred = await read_cred(sf, slug, "prod")
    if not cred:
        await _reject("prod 凭据缺失，无法回滚", 409)
    cred_dev = await read_cred(sf, slug, "dev") or {}       # D-T4：dev 侧真读，缺位才交空
    rid = await records.begin(sf, app_id, "rollback", version)
    try:
        await set_status(sf, app_id, "publishing")
        await _notify(notify, app_id, {"type": "prod_status", "status": "publishing"})
        ensure_deploy_files(root, app, {"dev": cred_dev, "prod": cred}, version=version)
        code, log = await _docker(*_prod_compose_args(root), "up", "-d", cwd=str(root))
        if code != 0:
            raise DeployError(f"回滚起栈失败:\n{log[-800:]}")
        timeout = int((await deploy_cfg(sf)).get("deploy.health_timeout", 240))
        ok, checks = await wait_prod_health(root, _prod_port(app), timeout, app.get("stack", ""))
        async with sf() as s:
            await s.execute(text("UPDATE app_project SET current_version=:v, status_prod=:st, "
                                 "updated_at=now() WHERE id=:i"),
                            {"v": version, "st": "running" if ok else "unhealthy", "i": app_id})
            await s.commit()
        await records.finish(sf, rid, "success" if ok else "failed", step="health",
                             error=None if ok else f"回滚后体检未通过 {checks}",
                             detail={"health": checks} if ok else None)
        await _notify(notify, app_id, {"type": "prod_status",
                                       "status": "running" if ok else "unhealthy"})
        from app.deploy import nics
        try:
            urls = await nics.app_urls(sf, app)
            rurl = urls["domain"]
        except Exception as ue:  # noqa: BLE001  I-1 同款收尾窗：记账已收口，URL 汇总挂只降级
            rurl = f"https://{slug}.{nics.GATEWAY_DOMAIN}"
        await _notify(notify, app_id, {"type": "publish_done", "ok": ok, "version": version,
                                       "url": rurl,
                                       "error": "" if ok else f"体检未通过 {checks}"})
        return {"status": "running" if ok else "unhealthy", "version": version,
                "url": rurl, "recordId": rid}
    except DeployError as e:
        await records.finish(sf, rid, "failed", error=str(e)[:2000])
        await set_status(sf, app_id, "error")
        await _notify(notify, app_id, {"type": "prod_status", "status": "error", "message": str(e)})
        await _notify(notify, app_id, {"type": "publish_done", "ok": False, "version": version,
                                       "url": "", "error": str(e)[:500]})
        raise
    except Exception as e:
        await records.finish(sf, rid, "failed", error=f"{type(e).__name__}: {e}")
        await set_status(sf, app_id, "error")
        await _notify(notify, app_id, {"type": "prod_status", "status": "error", "message": str(e)})
        await _notify(notify, app_id, {"type": "publish_done", "ok": False, "version": version,
                                       "url": "", "error": str(e)[:500]})
        raise DeployError(f"回滚失败: {e}") from e


async def cleanup_images(sf, app_id: int, slug: str, notify=None) -> list[str]:
    """keep-N 镜像清理（FR4-05）：rmi 尽力而为，返回**实删** tag（删失败不算数）。"""
    keep = int((await deploy_cfg(sf)).get("deploy.keep_versions", 5))
    removed: list[str] = []
    for v in await records.versions_for_cleanup(sf, app_id, keep):
        for role in ("frontend", "backend"):
            tag = f"zc-{slug}-{role}:prod-{v}"
            code, _ = await _docker("image", "rm", tag, timeout=120)
            if code == 0:
                removed.append(tag)
    if removed:
        await _step(app_id, notify, f"✓ 已清理旧镜像 {len(removed)} 个（保留最近 {keep} 版）")
    return removed
