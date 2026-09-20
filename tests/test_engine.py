# tests/test_engine.py
"""dev 生命周期引擎单测（规格 03 §4.3 状态机 / §5 错误处理 / 验收 1·3·6）。

docker 一律走 FakeRunner 替身，迁移与健康探针经 monkeypatch 替换为可编程结果，
故本文件主体用例**不依赖 Docker、不起任何真容器**（确定性、不过夜）；
只有标注 needs_docker 的两条用例真调 docker CLI（Docker Desktop 未启动时跳过）。
"""
import asyncio
import inspect
import json
import shutil
from pathlib import Path

import pytest
from sqlalchemy import text

from app.db import make_session_factory
from app.deploy import engine, sqlrun
from app.deploy.errors import DeployError
from app.deploy.hub import DeployHub

pytestmark = pytest.mark.asyncio

ROOT = Path("D:/__zc_task11__")   # 试验目录根（仓库外，沿用 Task 9 约定）；fixture 收尾整棵清掉
needs_docker = pytest.mark.skipif(not sqlrun.docker_available(), reason="docker 不可用（Docker Desktop 未启动）")

COMPOSE_VERBS = ("up", "down", "ps", "logs", "restart", "build", "run")


def _cmd_key(args) -> str:
    """命令指纹：首 token + （若出现）compose 动词。

    `compose -f <yaml> up -d` 与 `compose -p zc-x-dev down` 分别落到 "compose up"/"compose down"，
    不把参数值/旗标混进键里（brief 原稿按 args[:2] 取键，真实命令永远落不到 "compose up"）。
    """
    verb = next((a for a in args[1:] if a in COMPOSE_VERBS), None)
    return f"{args[0]} {verb}" if verb else args[0]


async def _emit(on_line, line) -> None:
    """调用日志回调：以「返回值是否 awaitable」判定，同步回调（hub.publish 直传）与协程函数都兼容。

    比按函数类型预判更稳：functools.partial / lambda 返回 awaitable 的情况也能正确 await。
    """
    res = on_line(line)
    if inspect.isawaitable(res):
        await res


class FakeRunner:
    """替身 engine.docker：按命令指纹返回脚本化结果，记录调用（args / cwd / timeout）。"""

    def __init__(self, script=None):
        self.calls = []                       # [{args, cwd, timeout}]
        self.script = dict(script or {})      # 命令指纹 -> (code, out) | Exception

    async def __call__(self, *args, cwd=None, on_line=None, timeout=600):
        self.calls.append({"args": args, "cwd": cwd, "timeout": timeout})
        handler = self.script.get(_cmd_key(args), (0, ""))
        if isinstance(handler, Exception):
            raise handler
        code, out = handler
        if out and on_line:
            for line in out.splitlines():
                await _emit(on_line, line)
        return code, out

    def keys(self) -> list:
        return [_cmd_key(c["args"]) for c in self.calls]

    def arg_of(self, key: str, index: int):
        """取第一条命中指纹 key 的命令的第 index 个参数（无则 None）。"""
        for c in self.calls:
            if _cmd_key(c["args"]) == key:
                return c["args"][index]
        return None


def _hub_spy():
    """真 DeployHub 实例 + publish 替身：按 D-1 的同步 notify 签名 (app_id, event) 观察事件。"""
    hub = DeployHub()
    events = []
    hub.publish = lambda app_id, event: events.append(event)
    return hub, events


def _statuses(events) -> list:
    return [e["status"] for e in events if e["type"] == "dev_status"]


def _dev_logs(events) -> list:
    return [e["line"] for e in events if e["type"] == "dev_log"]


def _events_of(events, typ) -> list:
    return [e for e in events if e["type"] == typ]


@pytest.fixture
async def sf(test_engine):
    return make_session_factory(test_engine)


@pytest.fixture
async def make_app(sf):
    """在真表里造 workspace + app_project 行并登记 dbcred.<slug>.dev。

    凭据是写入 app_config 的假密码（不建库不建角色、不碰 docker）——迁移入口在测试里被替身接管，
    引擎其余环节（读凭据、渲染 deploy/、状态落库）全部走真实代码。
    brief 原稿把插行/删行写在用例首尾（断言一失败就泄漏），此处整理成 fixture teardown。
    """
    made = []

    async def _make(slug: str, *, prod_port: int, dev_port: int, root: Path | None = None) -> dict:
        root = root or (ROOT / slug)
        root.mkdir(parents=True, exist_ok=True)
        async with sf() as s:
            wid = (await s.execute(text(
                "INSERT INTO workspace (name, root_path) VALUES (:n, :p) RETURNING id"),
                {"n": slug, "p": str(root)})).scalar()
            app_id = (await s.execute(text(
                "INSERT INTO app_project (workspace_id, name, slug, scale, stack, prod_port, dev_port) "
                "VALUES (:w, :n, :sl, 'small', 'vue3-fastapi', :pp, :dp) RETURNING id"),
                {"w": wid, "n": slug, "sl": slug, "pp": prod_port, "dp": dev_port})).scalar()
            await s.execute(text(
                "INSERT INTO app_config (key, value) VALUES (:k, CAST(:v AS JSONB)) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()"),
                {"k": f"dbcred.{slug}.dev",
                 "v": json.dumps({"db": f"{slug}_dev", "user": f"{slug}_dev", "password": "fake-pw"})})
            await s.commit()
        made.append({"app_id": app_id, "wid": wid, "slug": slug})
        return {"id": app_id, "workspace_id": wid, "slug": slug, "stack": "vue3-fastapi",
                "ports": {"prod": prod_port, "dev": dev_port}}

    yield _make
    async with sf() as s:
        for m in made:
            await s.execute(text("DELETE FROM app_project WHERE id = :i"), {"i": m["app_id"]})
            await s.execute(text("DELETE FROM workspace WHERE id = :i"), {"i": m["wid"]})
            await s.execute(text("DELETE FROM app_config WHERE key LIKE :p"),
                            {"p": f"dbcred.{m['slug']}.%"})
        await s.commit()
    shutil.rmtree(ROOT, ignore_errors=True)


class _Seams:
    """engine 的四个外部门（docker / 迁移 / docker 体检 / 健康探针）替身集合；用例只改关心的那一个。"""

    def __init__(self, monkeypatch):
        self.docker = FakeRunner()
        self.mig_result = {"ok": True, "code": 0, "log": "ALL DONE"}
        self.mig_calls = []
        self.preflight_error = None
        self.preflight_calls = 0
        self.health_ok = True
        self.health_calls = []
        monkeypatch.setattr(engine, "docker", self.docker)
        monkeypatch.setattr(engine, "run_sqlchange", self._run_sqlchange)
        monkeypatch.setattr(engine, "preflight_docker", self._preflight)
        monkeypatch.setattr(engine, "wait_frontend_health", self._health)

    async def _run_sqlchange(self, app_dir, env, cred, on_log=None):
        self.mig_calls.append({"app_dir": app_dir, "env": env, "cred": cred, "on_log": on_log})
        if on_log and self.mig_result.get("log"):
            for line in self.mig_result["log"].splitlines():
                await _emit(on_log, line)
        return dict(self.mig_result)

    async def _preflight(self):
        self.preflight_calls += 1
        if self.preflight_error:
            raise self.preflight_error

    async def _health(self, port, timeout):
        self.health_calls.append((port, timeout))
        return self.health_ok


@pytest.fixture
def seams(monkeypatch):
    return _Seams(monkeypatch)


async def _db_status(sf, app_id) -> str:
    async with sf() as s:
        return (await s.execute(text("SELECT status_dev FROM app_project WHERE id = :i"),
                                {"i": app_id})).scalar()


async def _set_db_status(sf, app_id, status) -> None:
    async with sf() as s:
        await s.execute(text("UPDATE app_project SET status_dev = :st WHERE id = :i"),
                        {"st": status, "i": app_id})
        await s.commit()


# ---------- hub ----------

async def test_deploy_hub_pubsub():
    hub = DeployHub()
    q = hub.subscribe(7)
    hub.publish(7, {"type": "dev_status", "status": "starting"})
    assert await asyncio.wait_for(q.get(), 1) == {"type": "dev_status", "status": "starting"}
    hub.unsubscribe(7, q)
    hub.publish(7, {"type": "x"})          # 已退订：不抛、不收
    assert q.empty()


async def test_hub_slots_are_per_app_and_reclaimed():
    hub = DeployHub()
    a, b = hub.subscribe(1), hub.subscribe(2)
    hub.publish(1, {"type": "dev_status", "status": "starting"})
    assert a.get_nowait()["status"] == "starting"
    assert b.empty()                       # 跨应用不串扰（对齐 ChatHub 分槽裁定）
    hub.unsubscribe(1, a)
    assert 1 not in hub.subs               # 空槽回收
    hub.unsubscribe(1, a)                  # 重复退订幂等，不抛


# ---------- start_dev：状态机 + 事件 ----------

async def test_start_dev_happy_path(sf, make_app, seams):
    app = await make_app("zceng", prod_port=8298, dev_port=8299)
    hub, events = _hub_spy()
    seams.docker.script["compose up"] = (0, "Container zc-zceng-dev-frontend-1 Started")
    res = await engine.start_dev(sf, app, notify=hub.publish)
    assert res["status"] == "running"
    assert res["url"] == "http://localhost:8299"
    assert _statuses(events) == ["starting", "running"]        # stopped→starting→running，无中间 error
    assert await _db_status(sf, app["id"]) == "running"        # 状态落库
    assert all(e["app_id"] == app["id"] for e in events)       # 事件带 app_id（WS 分槽下发）
    # 渲染落盘：deploy/dev-compose.yml 端口与 .env 凭据由引擎写入（Task 10 渲染器的真实接线）
    deploy_dir = ROOT / "zceng" / "deploy"
    yml = (deploy_dir / "dev-compose.yml").read_text(encoding="utf-8")
    assert "8299:5173" in yml and "name: zc-zceng-dev" in yml
    assert "DEV_PASSWORD=fake-pw" in (deploy_dir / ".env").read_text(encoding="utf-8")
    # 健康等待参数取自 deploy.health_timeout 配置（默认 60），端口为 dev 端口
    assert seams.health_calls == [(8299, 60)]


async def test_start_dev_migrate_failure(sf, make_app, seams):
    app = await make_app("zceng2", prod_port=8296, dev_port=8297)
    seams.mig_result = {"ok": False, "code": 2, "log": "FAIL V20260918_03_bad.sql"}
    with pytest.raises(DeployError):
        await engine.start_dev(sf, app)
    assert await _db_status(sf, app["id"]) == "error"


async def test_migrate_gate_blocks_stack_and_emits_result(sf, make_app, seams):
    """迁移失败 → 不起 dev 栈（验收 6），但 migrate_result 事件必发（裁定 D-3）。"""
    app = await make_app("zcgate", prod_port=8294, dev_port=8295)
    seams.mig_result = {"ok": False, "code": 2, "log": "FAIL V20260918_03_bad.sql"}
    hub, events = _hub_spy()
    with pytest.raises(DeployError) as ei:
        await engine.start_dev(sf, app, notify=hub.publish)
    assert "迁移失败" in str(ei.value) and "sqlrunner" in str(ei.value)
    assert "compose up" not in seams.docker.keys()
    assert seams.health_calls == []                        # 未起栈也就没有健康探针
    mig = _events_of(events, "migrate_result")
    assert len(mig) == 1 and mig[0]["ok"] is False and mig[0]["env"] == "dev"
    assert "FAIL V20260918_03_bad.sql" in mig[0]["log"]
    assert _statuses(events) == ["starting", "error"]      # starting→error 落状态机


async def test_dev_log_lines_forwarded_and_migrate_seam(sf, make_app, seams):
    """迁移与启动的每行输出都经 notify 的 dev_log 事件转发；无 notify 时不注入日志回调。"""
    app = await make_app("zclog", prod_port=8292, dev_port=8293)
    hub, events = _hub_spy()
    seams.mig_result = {"ok": True, "code": 0, "log": "OK V20260918_01_init.sql\nSKIP old"}
    seams.docker.script["compose up"] = (0, "Container backend Started\nContainer frontend Started")
    await engine.start_dev(sf, app, notify=hub.publish)
    assert _dev_logs(events) == ["OK V20260918_01_init.sql", "SKIP old",
                                 "Container backend Started", "Container frontend Started"]
    assert _statuses(events) == ["starting", "running"]
    m = seams.mig_calls[0]
    assert m["env"] == "dev" and m["app_dir"] == ROOT / "zclog"    # 迁移跑在应用根的 dev 库上
    assert m["cred"] == {"db": "zclog_dev", "user": "zclog_dev", "password": "fake-pw"}
    assert m["on_log"] is not None
    assert _events_of(events, "migrate_result")[0]["ok"] is True    # 成功同样必发
    # notify 缺席时引擎不注入日志回调（on_log=None），迁移入口仍被正常调用
    events.clear()
    seams.mig_calls.clear()
    await engine.start_dev(sf, app)
    assert seams.mig_calls[0]["on_log"] is None and events == []


async def test_start_dev_requires_cred_before_any_container(sf, make_app, seams):
    """dev 凭据缺失 → 409 中文引导，且在起任何 docker 命令之前收口（fail-closed）。"""
    app = await make_app("zccred", prod_port=8290, dev_port=8291)
    async with sf() as s:
        await s.execute(text("DELETE FROM app_config WHERE key LIKE 'dbcred.zccred.%'"))
        await s.commit()
    with pytest.raises(DeployError) as ei:
        await engine.start_dev(sf, app)
    assert ei.value.status == 409 and "凭据" in str(ei.value)
    assert seams.docker.keys() == []
    assert seams.mig_calls == []
    assert await _db_status(sf, app["id"]) == "error"


async def test_start_dev_docker_unavailable(sf, make_app, seams):
    """Docker 不可用 → 503 中文引导（规格 03 §5），既不渲染也不迁移更不起栈。"""
    app = await make_app("zcdock", prod_port=8288, dev_port=8289)
    seams.preflight_error = DeployError(
        "Docker 未启动或不可用：请打开 Docker Desktop（设置页「部署环境」可一键启动）", status=503)
    with pytest.raises(DeployError) as ei:
        await engine.start_dev(sf, app)
    assert ei.value.status == 503 and "Docker Desktop" in str(ei.value)
    assert seams.mig_calls == [] and seams.docker.keys() == []
    assert not (ROOT / "zcdock" / "deploy").exists()
    assert await _db_status(sf, app["id"]) == "error"


async def test_start_dev_compose_up_failure(sf, make_app, seams):
    """compose up 非零退出 → error 且日志尾部进错误消息，健康探针不被调用。"""
    app = await make_app("zcup", prod_port=8286, dev_port=8287)
    seams.docker.script["compose up"] = (1, "Error response from daemon: port is already allocated")
    with pytest.raises(DeployError) as ei:
        await engine.start_dev(sf, app)
    assert "dev 栈启动失败" in str(ei.value) and "already allocated" in str(ei.value)
    assert seams.health_calls == []
    assert await _db_status(sf, app["id"]) == "error"


async def test_start_dev_unhealthy_sets_error(sf, make_app, seams):
    """栈起了但前端健康探针超时未就绪 → error（状态机 running 之前的最后一道门）。"""
    app = await make_app("zcunhealthy", prod_port=8284, dev_port=8285)
    seams.health_ok = False
    hub, events = _hub_spy()
    with pytest.raises(DeployError) as ei:
        await engine.start_dev(sf, app, notify=hub.publish)
    assert "未就绪" in str(ei.value) and "8285" in str(ei.value)
    assert await _db_status(sf, app["id"]) == "error"
    assert _statuses(events) == ["starting", "error"]


# ---------- stop / restart / status ----------

async def test_stop_dev_downs_stack_and_resets_status(sf, make_app, seams):
    app = await make_app("zcstop", prod_port=8282, dev_port=8283)
    await _set_db_status(sf, app["id"], "running")
    hub, events = _hub_spy()
    res = await engine.stop_dev(sf, app, notify=hub.publish)
    assert res == {"status": "stopped"}
    assert seams.docker.keys() == ["compose down"]
    down = seams.docker.calls[0]
    assert str(ROOT / "zcstop" / "deploy" / "dev-compose.yml") in " ".join(down["args"])
    assert down["cwd"] == str(ROOT / "zcstop")
    assert _statuses(events) == ["stopping", "stopped"]
    assert await _db_status(sf, app["id"]) == "stopped"


async def test_restart_dev_stops_then_starts(sf, make_app, seams):
    app = await make_app("zcrest", prod_port=8280, dev_port=8281)
    await _set_db_status(sf, app["id"], "running")
    hub, events = _hub_spy()
    res = await engine.restart_dev(sf, app, notify=hub.publish)
    assert res["status"] == "running"
    assert seams.docker.keys() == ["compose down", "compose up"]     # 时序：先 down 后 up
    assert _statuses(events) == ["stopping", "stopped", "starting", "running"]
    assert await _db_status(sf, app["id"]) == "running"


async def test_dev_status_reports_db_state_compose_ps_and_url(sf, make_app, seams):
    app = await make_app("zcdst", prod_port=8278, dev_port=8279)
    await _set_db_status(sf, app["id"], "running")
    seams.docker.script["compose ps"] = (0, '{"Name":"zcdst-dev-frontend-1","State":"running"}')
    st = await engine.dev_status(sf, app)
    assert st["status_dev"] == "running"
    assert "zcdst-dev-frontend-1" in st["compose_ps"]
    assert st["url"] == "http://localhost:8279"
    # compose 文件不在（应用从未启动过）：ps 非零退出 → 回空串不抛
    seams.docker.script["compose ps"] = (1, "no configuration file provided")
    st2 = await engine.dev_status(sf, app)
    assert st2["compose_ps"] == "" and st2["status_dev"] == "running"


async def test_engine_accepts_flat_app_row_shape(sf, make_app, seams):
    """Interfaces 声明消费 Task 5 `app_service.get_app`——其返回是扁平列（dev_port/prod_port），
    引擎取值须兼容两种形状（ports 映射 + 扁平列），否则 API 线一接上就 KeyError。"""
    app = await make_app("zcflat", prod_port=8276, dev_port=8277)
    flat = {"id": app["id"], "workspace_id": app["workspace_id"], "slug": "zcflat",
            "stack": "vue3-fastapi", "prod_port": 8276, "dev_port": 8277, "status_dev": "stopped"}
    res = await engine.start_dev(sf, flat)
    assert res["status"] == "running" and res["url"] == "http://localhost:8277"
    st = await engine.dev_status(sf, flat)
    assert st["url"] == "http://localhost:8277"
    assert await _db_status(sf, app["id"]) == "running"
    assert seams.docker.keys() == ["compose up", "compose ps"]     # start → status 两条线都走扁平行


# ---------- 孤儿清理 / 退出停栈（规格 03 §5 / 验收 5） ----------

async def test_cleanup_orphans_downs_every_project_and_resets_status(sf, make_app, seams):
    a = await make_app("zcor1", prod_port=8274, dev_port=8275)
    b = await make_app("zcor2", prod_port=8272, dev_port=8273)
    await _set_db_status(sf, a["id"], "running")
    await _set_db_status(sf, b["id"], "starting")
    hub, events = _hub_spy()
    await engine.cleanup_orphans(sf, notify=hub.publish)
    downs = [c for c in seams.docker.calls if _cmd_key(c["args"]) == "compose down"]
    assert len(downs) == 2
    # 按 compose 项目名清理：平台崩溃残留不依赖 yaml 是否还在原位
    assert {d["args"][2] for d in downs} == {f"zc-{a['slug']}-dev", f"zc-{b['slug']}-dev"}
    assert all(d["timeout"] == 120 for d in downs)
    assert await _db_status(sf, a["id"]) == "stopped" and await _db_status(sf, b["id"]) == "stopped"
    assert _statuses(events) == ["stopped", "stopped"]


async def test_cleanup_orphans_survives_docker_failure(sf, make_app, seams, caplog):
    """单个栈清理失败不得中断整轮清扫（Docker 失联时平台仍要能起来），且异常要留日志。"""
    a = await make_app("zcor3", prod_port=8270, dev_port=8271)
    await _set_db_status(sf, a["id"], "running")
    seams.docker.script["compose down"] = DeployError("docker daemon 失联", status=503)
    with caplog.at_level("WARNING"):
        await engine.cleanup_orphans(sf)          # 不抛
    assert await _db_status(sf, a["id"]) == "stopped"
    assert "zcor3" in caplog.text


async def test_stop_all_dev_on_exit_only_running_stacks(sf, make_app, seams):
    a = await make_app("zcexit1", prod_port=8268, dev_port=8269)
    b = await make_app("zcexit2", prod_port=8266, dev_port=8267)
    await _set_db_status(sf, a["id"], "running")
    await _set_db_status(sf, b["id"], "error")
    await engine.stop_all_dev_on_exit(sf, {})     # 缺键 → 默认自动停（DEFAULTS 为 true）
    assert seams.docker.keys() == ["compose down"]
    assert seams.docker.calls[0]["args"][2] == f"zc-{a['slug']}-dev"
    assert await _db_status(sf, a["id"]) == "running"    # 退出清理不改台账状态（进程即结束）


async def test_stop_all_dev_on_exit_skipped_when_disabled(sf, make_app, seams):
    a = await make_app("zcexit3", prod_port=8264, dev_port=8265)
    await _set_db_status(sf, a["id"], "running")
    await engine.stop_all_dev_on_exit(
        sf, {"deploy.auto_stop_dev_on_exit": False}, notify=None)
    assert seams.docker.calls == []
    assert await _db_status(sf, a["id"]) == "running"    # 关掉开关 → prod 与 dev 栈都不受影响


# ---------- 真实 docker 封装（仅这两条依赖 Docker Desktop） ----------

@needs_docker
async def test_docker_wrapper_captures_output_and_sync_on_line():
    """真子进程路径：退出码、合流输出、同步回调（Task 12 传 hub.publish 形态的 lambda）。"""
    seen = []
    code, out = await engine.docker("version", "--format", "v={{.Server.Version}}",
                                    on_line=seen.append, timeout=60)
    assert code == 0
    assert out.strip().startswith("v=") and seen == [out.strip()]


@needs_docker
async def test_docker_timeout_raises_504_and_preflight_passes():
    await engine.preflight_docker()              # 守护进程在跑 → 不抛
    # sleep 5：客户端被 kill 后容器最多再活 3 秒自灭（--rm 随即回收），不留残留
    with pytest.raises(DeployError) as ei:
        await engine.docker("run", "--rm", "postgres:16-alpine", "sleep", "5", timeout=2)
    assert ei.value.status == 504 and "超时" in str(ei.value)
