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
from app.deploy import compose as compose_mod
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

    async def _make(slug: str, *, prod_port: int, dev_port: int, root: Path | None = None,
                    stack: str = "vue3-fastapi", scale: str = "small") -> dict:
        root = root or (ROOT / slug)
        root.mkdir(parents=True, exist_ok=True)
        async with sf() as s:
            wid = (await s.execute(text(
                "INSERT INTO workspace (name, root_path) VALUES (:n, :p) RETURNING id"),
                {"n": slug, "p": str(root)})).scalar()
            app_id = (await s.execute(text(
                "INSERT INTO app_project (workspace_id, name, slug, scale, stack, prod_port, dev_port) "
                "VALUES (:w, :n, :sl, :sc, :st, :pp, :dp) RETURNING id"),
                {"w": wid, "n": slug, "sl": slug, "sc": scale, "st": stack,
                 "pp": prod_port, "dp": dev_port})).scalar()
            await s.execute(text(
                "INSERT INTO app_config (key, value) VALUES (:k, CAST(:v AS JSONB)) "
                "ON CONFLICT (owner_user_id, key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()"),
                {"k": f"dbcred.{slug}.dev",
                 "v": json.dumps({"db": f"{slug}_dev", "user": f"{slug}_dev", "password": "fake-pw"})})
            await s.commit()
        made.append({"app_id": app_id, "wid": wid, "slug": slug})
        return {"id": app_id, "workspace_id": wid, "slug": slug, "stack": stack,
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
    """engine 的外部门（docker / 迁移 / docker 体检 / 健康探针）替身集合；用例只改关心的那一个。"""

    def __init__(self, monkeypatch):
        self.docker = FakeRunner()
        self.mig_result = {"ok": True, "code": 0, "log": "ALL DONE"}
        self.mig_calls = []
        self.preflight_error = None
        self.preflight_calls = 0
        self.health_ok = True
        self.health_verdict = ""      # 探针替身带回的「最后一轮看到什么」，失败用例用它注入
        self.health_calls = []
        self.joint_calls = []        # B3 的 dev 联合探针（前端 + 后端），与只探前端的那条分开记
        # dev 凭据惰性供给（ADR-0015）：真 provision_env 会连实例建库建角色，单测里一律替身接管
        self.provision_result = {"db": "stub_dev", "user": "stub_dev", "password": "stub-pw"}
        self.provision_calls = []
        self.provision_error = None
        monkeypatch.setattr(engine, "docker", self.docker)
        monkeypatch.setattr(engine, "run_sqlchange", self._run_sqlchange)
        monkeypatch.setattr(engine, "preflight_docker", self._preflight)
        monkeypatch.setattr(engine, "wait_frontend_health", self._health)
        monkeypatch.setattr(engine, "wait_dev_health", self._dev_health)
        monkeypatch.setattr(engine, "provision_env", self._provision_env)

    async def _provision_env(self, sf, slug, env):
        self.provision_calls.append({"slug": slug, "env": env})
        if self.provision_error:
            raise self.provision_error
        return dict(self.provision_result)

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
        return self.health_ok, self.health_verdict

    async def _dev_health(self, port, timeout, backend_path):
        self.joint_calls.append((port, timeout, backend_path))
        return self.health_ok, self.health_verdict


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
    # 健康等待参数取自 deploy.health_timeout 配置（默认 240），端口为 dev 端口
    assert seams.health_calls == [(8299, 240)]


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
    """迁移与启动的每行输出都经 notify 的 dev_log 事件转发；无 notify 时不注入日志回调。

    引擎自己的步骤日志（`[zerocode] …`）与转发的原始输出同流，断言时先剔掉步骤行，
    保证「每行都转发、顺序不变」这条契约仍然单独可验。
    """
    app = await make_app("zclog", prod_port=8292, dev_port=8293)
    hub, events = _hub_spy()
    seams.mig_result = {"ok": True, "code": 0, "log": "OK V20260918_01_init.sql\nSKIP old"}
    seams.docker.script["compose up"] = (0, "Container backend Started\nContainer frontend Started")
    await engine.start_dev(sf, app, notify=hub.publish)
    logs = _dev_logs(events)
    forwarded = [l for l in logs if not l.startswith(engine.LOG_PREFIX)]
    assert forwarded == ["OK V20260918_01_init.sql", "SKIP old",
                         "Container backend Started", "Container frontend Started"]
    assert logs[0] == f"{engine.LOG_PREFIX} ▶ 启动预览 zclog（dev）"   # 步骤行首尾夹住转发行
    assert logs[-1].startswith(f"{engine.LOG_PREFIX} ✓ 预览已就绪")
    assert _statuses(events) == ["starting", "running"]
    m = seams.mig_calls[0]
    assert m["env"] == "dev" and m["app_dir"] == ROOT / "zclog"    # 迁移跑在应用根的 dev 库上
    assert m["cred"] == {"db": "zclog_dev", "user": "zclog_dev", "password": "fake-pw"}
    assert m["on_log"] is not None
    assert _events_of(events, "migrate_result")[0]["ok"] is True    # 成功同样必发
    # notify 缺席时引擎不注入日志回调（on_log=None），迁移入口仍被正常调用；步骤事件同样为零
    events.clear()
    seams.mig_calls.clear()
    await engine.start_dev(sf, app)
    assert seams.mig_calls[0]["on_log"] is None and events == []


async def test_start_dev_step_logs_cover_every_phase(sf, make_app, seams):
    """预览启动逐步留痕：预检 → 渲染 → 迁移 → 起栈 → 健康检查，收口一行总耗时。"""
    app = await make_app("zcstep", prod_port=8280, dev_port=8281)
    hub, events = _hub_spy()
    await engine.start_dev(sf, app, notify=hub.publish)
    steps = [l for l in _dev_logs(events) if l.startswith(engine.LOG_PREFIX)]
    joined = "\n".join(steps)
    for key in ("▶ 启动预览 zcstep", "✓ Docker 预检通过", "已渲染 deploy/", "执行 sqlchange 迁移",
                "✓ 迁移完成", "docker compose up -d", "✓ 容器已启动", "等待前端就绪", "✓ 预览已就绪"):
        assert key in joined, key
    assert "端口 8281" in joined                                  # 用户最关心的两件事：端口、URL
    assert "http://localhost:8281" in steps[-1]
    assert "总耗时" in steps[-1]                                   # 慢在哪一步，一行看出来
    # 顺序即编排顺序：迁移在起栈之前（闸门 D-2），健康检查在起栈之后
    assert (joined.index("✓ 迁移完成") < joined.index("docker compose up -d")
            < joined.index("等待前端就绪") < joined.index("✓ 预览已就绪"))


async def test_start_dev_failure_leaves_error_step(sf, make_app, seams):
    """失败也要有一行 ✗：写明原因与已耗时，且仍在 dev_status(error) 之前落到日志流。"""
    app = await make_app("zcstepfail", prod_port=8278, dev_port=8279)
    seams.health_ok = False
    hub, events = _hub_spy()
    with pytest.raises(DeployError):
        await engine.start_dev(sf, app, notify=hub.publish)
    fail = [l for l in _dev_logs(events) if "✗" in l]
    assert len(fail) == 1 and "未就绪" in fail[0] and "已耗时" in fail[0]
    assert _statuses(events) == ["starting", "error"]


async def test_migrate_failure_logs_step_before_error_event(sf, make_app, seams):
    """迁移闸门失败：先打「✗ 迁移失败，dev 栈不启动」，再起栈动作一概不发生。"""
    app = await make_app("zcstepmig", prod_port=8276, dev_port=8277)
    seams.mig_result = {"ok": False, "code": 2, "log": "FAIL V20260918_03_bad.sql"}
    hub, events = _hub_spy()
    with pytest.raises(DeployError):
        await engine.start_dev(sf, app, notify=hub.publish)
    joined = "\n".join(_dev_logs(events))
    assert "✗ 迁移失败，dev 栈不启动" in joined and "docker compose up -d" not in joined
    assert "compose up" not in seams.docker.keys()


async def test_stop_dev_step_logs(sf, make_app, seams):
    """停止预览同样有首尾两行，便于和启动行对账。"""
    app = await make_app("zcstepstop", prod_port=8274, dev_port=8275)
    hub, events = _hub_spy()
    await engine.stop_dev(sf, app, notify=hub.publish)
    steps = [l for l in _dev_logs(events) if l.startswith(engine.LOG_PREFIX)]
    assert steps[0] == f"{engine.LOG_PREFIX} ▶ 停止预览 zcstepstop（dev）"
    assert steps[-1].startswith(f"{engine.LOG_PREFIX} ✓ 预览已停止")



async def _drop_dev_cred(sf, slug: str) -> None:
    """抹掉 dbcred.<slug>.dev 行，造出「元数据在、凭据不在」的预置应用态（ADR-0015）。"""
    async with sf() as s:
        await s.execute(text("DELETE FROM app_config WHERE key LIKE :p"), {"p": f"dbcred.{slug}.%"})
        await s.commit()


async def test_start_dev_lazily_provisions_missing_dev_cred(sf, make_app, seams):
    """dev 凭据缺失 → 现场供给分库，而不是 409 判死（预置应用搬到新机器的唯一活路）。

    补建出来的凭据必须真的流下去：渲染 deploy/.env 与 sqlchange 迁移都用它，不能拿着 None 继续跑。
    """
    app = await make_app("zclazy", prod_port=8290, dev_port=8291)
    await _drop_dev_cred(sf, "zclazy")
    seams.provision_result = {"db": "zclazy_dev", "user": "zclazy_dev", "password": "gen-pw-1"}
    hub, events = _hub_spy()
    seams.docker.script["compose up"] = (0, "Container frontend Started")

    res = await engine.start_dev(sf, app, notify=hub.publish)

    assert seams.provision_calls == [{"slug": "zclazy", "env": "dev"}]
    assert res["status"] == "running" and res["url"] == "http://localhost:8291"
    assert seams.mig_calls[0]["cred"]["password"] == "gen-pw-1"      # 补建凭据真被迁移用上
    env = (ROOT / "zclazy" / "deploy" / ".env").read_text(encoding="utf-8")
    assert "DEV_PASSWORD=gen-pw-1" in env                            # 也真被渲染进 .env
    assert await _db_status(sf, app["id"]) == "running"
    joined = "\n".join(_dev_logs(events))
    assert "现场供给分库" in joined and "zclazy_dev" in joined        # 演示时观众能看见发生了什么


async def test_start_dev_lazy_provision_failure_blocks_stack(sf, make_app, seams):
    """边界：供给本身失败（平台库连不上/账号无建库权限）→ 503 可读引导，且不起任何容器、不跑迁移。"""
    app = await make_app("zclazy2", prod_port=8288, dev_port=8289)
    await _drop_dev_cred(sf, "zclazy2")
    seams.provision_error = RuntimeError("connection refused")

    with pytest.raises(DeployError) as ei:
        await engine.start_dev(sf, app)

    assert ei.value.status == 503
    assert "供给失败" in str(ei.value) and "connection refused" in str(ei.value)
    assert seams.docker.keys() == [] and seams.mig_calls == []
    assert await _db_status(sf, app["id"]) == "error"


async def test_start_dev_incomplete_provisioned_cred_blocks_stack(sf, make_app, seams):
    """边界：供给回来的凭据缺口令 → 宁可 500 报错，也不把空口令渲染进 .env
    （大型栈 compose 的 `${VAR:?}` 会在解析期拒收，错误面比这条守卫难懂得多）。"""
    app = await make_app("zclazy3", prod_port=8286, dev_port=8287)
    await _drop_dev_cred(sf, "zclazy3")
    seams.provision_result = {"db": "zclazy3_dev", "user": "zclazy3_dev", "password": ""}

    with pytest.raises(DeployError) as ei:
        await engine.start_dev(sf, app)

    assert ei.value.status == 500 and "不完整" in str(ei.value)
    assert seams.docker.keys() == [] and seams.mig_calls == []
    assert not (ROOT / "zclazy3" / "deploy" / ".env").exists()      # 半成品 .env 一个字节都不落
    assert await _db_status(sf, app["id"]) == "error"


async def test_start_dev_with_existing_cred_never_reprovisions(sf, make_app, seams):
    """边界：凭据健在（本地常规路径）→ 一次 provision 都不许发，否则等于给存量库换口令。"""
    app = await make_app("zchas", prod_port=8284, dev_port=8285)
    seams.docker.script["compose up"] = (0, "Container frontend Started")

    await engine.start_dev(sf, app)

    assert seams.provision_calls == []
    assert seams.mig_calls[0]["cred"]["password"] == "fake-pw"        # 用的是库里既有凭据
    assert await _db_status(sf, app["id"]) == "running"


async def test_start_dev_docker_unavailable(sf, make_app, seams):
    """Docker 不可用 → 503 中文引导，且**不进入 starting**（规格 03 §5 / 裁定 §8.1）：
    预检前置 → 不发任何事件、status_dev 保持 stopped（不落 error），异常原样上抛供 REST 映射 503。"""
    app = await make_app("zcdock", prod_port=8288, dev_port=8289)
    seams.preflight_error = DeployError(
        "Docker 未启动或不可用：请打开 Docker Desktop（设置页「部署环境」可一键启动）", status=503)
    hub, events = _hub_spy()
    with pytest.raises(DeployError) as ei:
        await engine.start_dev(sf, app, notify=hub.publish)
    assert ei.value.status == 503 and "Docker Desktop" in str(ei.value)
    assert events == [] and _statuses(events) == []       # 零事件：starting 也不发
    assert seams.mig_calls == [] and seams.docker.keys() == []
    assert not (ROOT / "zcdock" / "deploy").exists()
    assert await _db_status(sf, app["id"]) == "stopped"    # 状态零变更（不落 error）


async def test_start_dev_compose_up_failure(sf, make_app, seams):
    """compose up 非零退出 → error 且日志尾部进错误消息，健康探针不被调用。"""
    app = await make_app("zcup", prod_port=8286, dev_port=8287)
    seams.docker.script["compose up"] = (1, "Error response from daemon: port is already allocated")
    with pytest.raises(DeployError) as ei:
        await engine.start_dev(sf, app)
    assert "dev 栈启动失败" in str(ei.value) and "already allocated" in str(ei.value)
    assert seams.health_calls == []
    assert await _db_status(sf, app["id"]) == "error"


async def test_start_dev_names_the_container_holding_the_port(sf, make_app, seams):
    """绑端口冲突要点名占用者并给出可执行命令：甩 daemon 原文，用户只会反复重试一个永远好不了的错。

    端口在库里没登记（原属主已被删除）但容器还活着时，这是唯一的溯源路径。
    """
    app = await make_app("zcbind", prod_port=8284, dev_port=8285)
    seams.docker.script["compose up"] = (
        1, "Error response from daemon: driver failed programming external connectivity on "
           "endpoint zcbind-dev-frontend-1: Bind for 0.0.0.0:8285 failed: port is already allocated")
    seams.docker.script["ps"] = (0, "zc-gone-dev-frontend-1\n")
    with pytest.raises(DeployError) as ei:
        await engine.start_dev(sf, app)
    msg = str(ei.value)
    assert "8285" in msg and "zc-gone-dev-frontend-1" in msg
    assert "docker rm -f zc-gone-dev-frontend-1" in msg
    assert "already allocated" in msg                       # daemon 原文仍保留，不丢诊断线索


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

async def test_down_app_stacks_releases_both_projects_without_reading_deploy_files(seams):
    """删除路径的收口：dev 与 prod 各 down 一次，按项目名走（不读 deploy/，`.env` 缺位也停得动），
    未停净只回告警不抛——把应用永久钉在列表里，用户就真没法自救了。"""
    assert await engine.down_app_stacks("zcdel") == []
    assert [c["args"] for c in seams.docker.calls] == [
        ("compose", "-p", "zc-zcdel-dev", "down"),
        ("compose", "-p", "zc-zcdel-prod", "down")]

    seams.docker.script["compose down"] = (1, "permission denied")
    warnings = await engine.down_app_stacks("zcdel")
    assert len(warnings) == 2 and all("permission denied" in w for w in warnings)


async def test_stop_dev_downs_stack_and_resets_status(sf, make_app, seams):
    app = await make_app("zcstop", prod_port=8282, dev_port=8283)
    await _set_db_status(sf, app["id"], "running")
    hub, events = _hub_spy()
    res = await engine.stop_dev(sf, app, notify=hub.publish)
    assert res == {"status": "stopped"}
    assert seams.docker.keys() == ["compose down"]
    down = seams.docker.calls[0]
    assert str(ROOT / "zcstop" / "deploy" / "dev-compose.yml") in " ".join(down["args"])
    # 补 `--remove-orphans`：down 按**文件里的服务名**移除，一旦本系列自己把 redis 改成 appredis，
    # 旧名容器就成孤儿（端口不占、状态照样落 stopped）；项目名由 `name: zc-<slug>-dev` 锁定，不会误伤 prod
    assert "--remove-orphans" in down["args"]
    assert down["cwd"] == str(ROOT / "zcstop")
    assert _statuses(events) == ["stopping", "stopped"]
    assert await _db_status(sf, app["id"]) == "stopped"


async def test_stop_dev_backfills_unbackfilled_secrets_before_down(sf, make_app, seams):
    """停栈不得被自己渲染物的解析期闸门挡住（B1c 复审 I-1）。

    `${VAR:?}` 是 compose 的**文件解析期**行为，实测 .env 密钥位为空时 `down`/`ps`/`logs` 全部 exit=1
    （不只 `up`）；而 stop_dev 把非零降级成一条 warning 后照样落 stopped——结果是容器一个没停、
    宿主 dev 端口仍被占用、UI 却显示「已停止」（restart 更糟：紧接的 up 会因旧端口占用而失败）。
    所以停栈前先把 deploy/ 走一遍 ensure（幂等，只补未回填的密钥位），让 down 永远拿得到可解析的文件。
    """
    app = await make_app("zcstopsec", prod_port=8290, dev_port=8291,
                         stack="vue3-springboot", scale="large")
    meta = engine._compose_meta(app)
    cred = await engine.read_cred(sf, "zcstopsec", "dev")          # 夹具登记的那份假凭据
    deploy = ROOT / "zcstopsec" / "deploy"
    deploy.mkdir(parents=True, exist_ok=True)
    (deploy / "dev-compose.yml").write_text(compose_mod.render_dev_compose(meta),
                                            encoding="utf-8", newline="")
    # 模拟「有人手工清过 .env」：render_env 产出的就是空密钥位（真值靠 ensure 回填）
    (deploy / ".env").write_text(compose_mod.render_env(meta, {"dev": cred}),
                                  encoding="utf-8", newline="")
    assert compose_mod._env_get((deploy / ".env").read_text(encoding="utf-8"), "JWT_SECRET") == ""
    await _set_db_status(sf, app["id"], "running")

    assert await engine.stop_dev(sf, app) == {"status": "stopped"}

    assert seams.docker.keys() == ["compose down"]                 # 补位不是重跑 up，只多一次文件读写
    after = (deploy / ".env").read_text(encoding="utf-8")
    assert compose_mod._env_get(after, "JWT_SECRET"), "停栈前必须把密钥位补齐"
    assert compose_mod._env_get(after, "REDIS_PASSWORD")
    assert compose_mod._env_get(after, "DEV_PASSWORD") == cred["password"], "补位不得冲空既有凭据"


async def test_stop_dev_leaves_a_healthy_env_untouched(sf, make_app, seams):
    """上条的对面：正常 .env 不能被停栈改一个字节（ensure 的 `_write_if_changed` 在此兼作 noop）。"""
    app = await make_app("zcstopkeep", prod_port=8292, dev_port=8293,
                         stack="vue3-springboot", scale="large")
    root = ROOT / "zcstopkeep"
    compose_mod.ensure_deploy_files(root, engine._compose_meta(app),
                                    {"dev": await engine.read_cred(sf, "zcstopkeep", "dev")})
    env_file = root / "deploy" / ".env"
    yml_file = root / "deploy" / "dev-compose.yml"
    before = (env_file.read_bytes(), yml_file.read_bytes())
    await engine.stop_dev(sf, app)
    assert (env_file.read_bytes(), yml_file.read_bytes()) == before
    assert seams.docker.keys() == ["compose down"]


async def test_stop_dev_still_downs_when_the_backfill_fails(sf, make_app, seams, monkeypatch):
    """补位是新加的抛点，它不许把「点得动的停止」变成点不动（复审 N-A）。

    停栈是平台刻意不设 docker 闸门的逃生通道（栈卡死时的唯一自救手段，restart 也走它），
    而 ensure 理论上会碰磁盘（目录只读 / 权限）与模板（未支持的栈）。补不上位就退回修前行为：
    拿现有文件照 down，真 down 不了仍走「非零只记 warning」的老收口。
    """
    app = await make_app("zcstopboom", prod_port=8294, dev_port=8295,
                         stack="vue3-springboot", scale="large")

    def boom(*a, **k):                       # 真函数是同步的（写成 async 会造出永不 await 的协程，本测试变空转）
        raise OSError("deploy/ 写不进去")

    monkeypatch.setattr(compose_mod, "ensure_deploy_files", boom)
    assert await engine.stop_dev(sf, app) == {"status": "stopped"}
    assert seams.docker.keys() == ["compose down"]
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


async def test_cleanup_orphans_sweeps_stacks_whose_app_is_gone(sf, make_app, seams):
    """库里已无 app_project 行的 zc-<slug>-{dev,prod} 是已删应用留下的残留，端口还占着 → 一并 down。

    删除路径已收口（apps.remove → down_app_stacks），这一扫是为了收拾收口之前留下的现场：
    线上演示服 8213 就是这么被一个不存在的应用占住的。现存应用的 prod 栈不在清扫范围——
    平台重启不该把正式环境停掉（FR3-04）；非平台命名的项目一律不碰。
    """
    a = await make_app("zcor4", prod_port=8262, dev_port=8263)
    seams.docker.script["ps"] = (0, "\n".join([
        "zc-zcor4-prod", "zc-gone-dev", "zc-gone-prod", "notaplatformstack"]))
    await engine.cleanup_orphans(sf)
    projects = [c["args"][2] for c in seams.docker.calls if _cmd_key(c["args"]) == "compose down"]
    assert projects == ["zc-zcor4-dev", "zc-gone-dev", "zc-gone-prod"]


async def test_sweep_deleted_stacks_downs_each_orphan_project_once(sf, make_app, seams):
    """`docker ps` 是每容器一行：一个残留栈有三个容器，不能 down 三遍。"""
    seams.docker.script["ps"] = (0, "zc-gone-dev\nzc-gone-dev\nzc-gone-dev\n")
    await engine.sweep_deleted_stacks(set())
    assert [c["args"][2] for c in seams.docker.calls if _cmd_key(c["args"]) == "compose down"] \
        == ["zc-gone-dev"]


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


async def test_preflight_docker_cli_missing_raises_503(monkeypatch):
    """D-1（Task 11 审查遗留）：docker CLI 完全不在 PATH 时 asyncio 抛的是 FileNotFoundError，
    不是「子进程非零退出」；预检必须同样收口成 503 中文引导，否则 REST 层冒 500（Task 12 端点的入口）。"""
    async def raise_fnf(*args, **kwargs):
        raise FileNotFoundError(2, "The system cannot find the file specified.")

    monkeypatch.setattr(engine, "docker", raise_fnf)
    with pytest.raises(DeployError) as ei:
        await engine.preflight_docker()
    assert ei.value.status == 503 and "Docker CLI 未安装" in str(ei.value)


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


# ---------- Task B3：dev 联合健康探针 + 按栈超时 ----------

def _client_returning(status_for):
    """httpx.AsyncClient 替身：按 url 给固定状态码，并把访问过的 url 记下来（不真发网络请求）。

    请求头另存 `FakeClient.heads`（不进返回值元组，免得 12 处既有用例的 `FakeClient, seen = ...` 一起改形）。
    """
    seen = []
    heads = []

    class FakeClient:
        def __init__(self, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None):
            seen.append(url)
            heads.append(headers)

            class R:
                status_code = status_for(url)
            return R()

    FakeClient.heads = heads
    return FakeClient, seen


async def test_wait_dev_health_fails_when_backend_not_ready(monkeypatch):
    """大型栈：前端 200 但后端未就绪时必须等到超时，杜绝「预览已就绪、后端还在编译」的假绿。"""
    FakeClient, seen = _client_returning(lambda url: 502 if url.endswith("/api/health") else 200)
    monkeypatch.setattr(engine.httpx, "AsyncClient", FakeClient)
    ok, _ = await engine.wait_dev_health(8211, 2, "/api/health")
    assert not ok
    assert any(u.endswith("/api/health") for u in seen)          # 后端探针确实打过
    assert any(u.endswith("8211/") for u in seen)                # 前端也打过（不是只探后端）


async def test_wait_dev_health_passes_when_both_200(monkeypatch):
    FakeClient, seen = _client_returning(lambda url: 200)
    monkeypatch.setattr(engine.httpx, "AsyncClient", FakeClient)
    ok, _ = await engine.wait_dev_health(8211, 2, "/api/health")
    assert ok
    assert len(seen) == 2                                        # 一轮内两个探针都过 → 立即返回，不白等


async def test_wait_frontend_health_still_only_probes_root(monkeypatch):
    """只探前端的那条没被顺手改成联合判定（存量小栈的行为一字不动，B3 只动大型栈）。"""
    FakeClient, seen = _client_returning(lambda url: 200)
    monkeypatch.setattr(engine.httpx, "AsyncClient", FakeClient)
    ok, _ = await engine.wait_frontend_health(8211, 2)
    assert ok
    assert seen == ["http://127.0.0.1:8211/"]


# ---------- 探针地址与预览展示地址可配（平台自身容器化部署） ----------

async def test_probe_host_env_moves_the_request_target(monkeypatch):
    """平台跑在容器里时，应用发布的预览端口在**宿主**上：探针必须连 ZEROCODE_PREVIEW_PROBE_HOST。

    连 127.0.0.1 的旧行为在容器化部署下永远连不到（那一侧没有任何监听），240s 全轮连接拒绝，
    把其实已经就绪的栈报成「健康等待期内未就绪」。
    """
    FakeClient, seen = _client_returning(lambda url: 200)
    monkeypatch.setattr(engine.httpx, "AsyncClient", FakeClient)
    monkeypatch.setenv("ZEROCODE_PREVIEW_PROBE_HOST", "host.docker.internal")
    ok, _ = await engine.wait_frontend_health(8213, 2)
    assert ok
    assert seen == ["http://host.docker.internal:8213/"]


async def test_probe_reports_a_loopback_host_header(monkeypatch):
    """连接目标可以是 `host.docker.internal`，但 Host 头必须是 Vite 无条件放行的回环名。

    Vite 5.4.12+/6 起对 Host 头做 DNS-rebinding 校验：只放行裸 IP 与 localhost，其余主机名要
    `server.allowedHosts` 列过，否则 403「Blocked request」。探针报的是容器内解析出来的主机名，
    于是**每个**应用的 dev 预览都等满健康窗口，而浏览器用 IP 访问同一个端口是 200（线上实测）。
    改应用配置救不了存量应用（各有一份 AI 生成的 vite.config.js），改探针是一处修。
    """
    FakeClient, seen = _client_returning(lambda url: 200)
    monkeypatch.setattr(engine.httpx, "AsyncClient", FakeClient)
    monkeypatch.setenv("ZEROCODE_PREVIEW_PROBE_HOST", "host.docker.internal")
    ok, _ = await engine.wait_frontend_health(8213, 2)
    assert ok
    assert seen == ["http://host.docker.internal:8213/"]      # 连的还是宿主探针地址
    assert FakeClient.heads == [{"Host": "localhost:8213"}]   # 报的却是回环名


async def test_probe_verdict_carries_last_non_200_status(monkeypatch):
    """等满窗口时必须把最后一轮的状态码带出来：403 与拒连在旧日志里长得一模一样。"""
    FakeClient, _ = _client_returning(lambda url: 403)
    monkeypatch.setattr(engine.httpx, "AsyncClient", FakeClient)
    ok, verdict = await engine.wait_frontend_health(8211, 1)
    assert not ok
    assert "403" in verdict and "/" in verdict


async def test_probe_verdict_carries_connection_error(monkeypatch):
    """连不上（DNS 解不出 / 端口无监听）也要有名字，不能只剩一句「未就绪」。"""
    seen = []

    class BoomClient:
        def __init__(self, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None):
            seen.append(url)
            raise engine.httpx.ConnectError("[Errno 111] Connection refused")

    monkeypatch.setattr(engine.httpx, "AsyncClient", BoomClient)
    ok, verdict = await engine.wait_frontend_health(8211, 1)
    assert not ok
    assert "ConnectError" in verdict and "refused" in verdict


async def test_probe_verdict_empty_when_ready(monkeypatch):
    """就绪路径不留诊断串（成功日志里不该出现「最后一轮」字样）。"""
    FakeClient, _ = _client_returning(lambda url: 200)
    monkeypatch.setattr(engine.httpx, "AsyncClient", FakeClient)
    ok, verdict = await engine.wait_dev_health(8211, 1, "/api/health")
    assert ok and verdict == ""


async def test_start_dev_failure_message_carries_probe_verdict(sf, make_app, seams):
    """探针的最后一轮要拼进「启动预览失败」那句，用户在日志面板里直接看到 403/拒连是哪一个。"""
    seams.health_ok = False
    seams.health_verdict = "/ → 403"
    app = await make_app("zcverd1", prod_port=8296, dev_port=8297)
    with pytest.raises(DeployError) as ei:
        await engine.start_dev(sf, app)
    assert "未就绪" in str(ei.value) and "/ → 403" in str(ei.value)


async def test_preview_url_without_config_keeps_localhost(monkeypatch):
    """没配基址时展示地址仍是 http://localhost:<port>（存量单机部署的行为一字不动）。"""
    from app.deploy.settings import preview_url
    monkeypatch.delenv("ZEROCODE_PREVIEW_HOST", raising=False)
    assert preview_url(8213) == "http://localhost:8213"


async def test_preview_url_uses_base_host_env(monkeypatch):
    from app.deploy.settings import preview_url
    monkeypatch.setenv("ZEROCODE_PREVIEW_HOST", "10.0.1.25")
    assert preview_url(8213) == "http://10.0.1.25:8213"


async def test_start_dev_separates_probe_and_preview_addresses(sf, make_app, seams, monkeypatch):
    """等待那行报的是探针真正连的地址，收口行与返回值给的是用户打得开的预览地址。"""
    monkeypatch.setenv("ZEROCODE_PREVIEW_PROBE_HOST", "host.docker.internal")
    monkeypatch.setenv("ZEROCODE_PREVIEW_HOST", "10.0.1.25")
    app = await make_app("zcdual", prod_port=8258, dev_port=8259)
    hub, events = _hub_spy()
    res = await engine.start_dev(sf, app, notify=hub.publish)
    steps = [l for l in _dev_logs(events) if l.startswith(engine.LOG_PREFIX)]
    assert res["url"] == "http://10.0.1.25:8259"
    assert "http://10.0.1.25:8259" in steps[-1]
    assert any("http://host.docker.internal:8259" in l for l in steps)


async def test_dev_status_url_follows_preview_base_host(sf, make_app, seams, monkeypatch):
    """预览抽屉与「复制地址」读的是 dev_status.url，它必须跟着基址走，否则远程部署仍拿到 localhost。"""
    monkeypatch.setenv("ZEROCODE_PREVIEW_HOST", "10.0.1.25")
    app = await make_app("zcdsthost", prod_port=8254, dev_port=8255)
    st = await engine.dev_status(sf, app)
    assert st["url"] == "http://10.0.1.25:8255"


async def test_health_timeout_by_stack():
    """冷启动实测 410s（下载 Maven 依赖），默认 240s 会把成功误判成启动失败。

    本文件模块顶有 `pytestmark = asyncio`，所以这个纯逻辑用例也写成 async（写成 def 会被当协程取）。
    """
    from app.deploy.settings import DEFAULTS, health_timeout
    assert DEFAULTS["deploy.health_timeout_large"] == 900
    cfg = {"deploy.health_timeout": 240, "deploy.health_timeout_large": 900}
    assert health_timeout(cfg, "vue3-springboot") == 900
    assert health_timeout(cfg, "vue3-fastapi") == 240
    assert health_timeout(cfg, "static") == 240


async def test_health_timeout_large_falls_back_when_key_absent():
    """库里只设了旧键时，大型栈跟着走那个值（管理员改一个键就能调两条路径，不必知道还有第二个键）。"""
    from app.deploy.settings import health_timeout
    assert health_timeout({"deploy.health_timeout": 300}, "vue3-springboot") == 300
    assert health_timeout({}, "vue3-springboot") == 240                # 两键都没 → 走与 DEFAULTS 同形的默认


async def test_start_dev_large_stack_waits_joint_health_with_long_timeout(sf, make_app, seams):
    """大型栈起预览：走联合探针 + 900s，而不是只等前端 200。"""
    app = await make_app("zcb3large", prod_port=8256, dev_port=8257,
                         stack="vue3-springboot", scale="large")
    hub, events = _hub_spy()
    res = await engine.start_dev(sf, app, notify=hub.publish)
    assert res["status"] == "running"
    assert seams.joint_calls == [(8257, 900, "/api/health")]
    assert seams.health_calls == []            # 只探前端那条不得再走（那就是本 Task 要修的假绿）
    assert "勿重复点" in "\n".join(_dev_logs(events))   # 首次启动要下依赖，不提醒就会被当成卡死去点重启


async def test_start_dev_small_stack_stays_frontend_only_with_default_timeout(sf, make_app, seams):
    """反向钉：小栈不加码（存量应用里有人删过 /api/health，加上去就是把能用的预览变成 240s 后 error）。"""
    app = await make_app("zcb3small", prod_port=8254, dev_port=8255)
    await engine.start_dev(sf, app)
    assert seams.health_calls == [(8255, 240)]
    assert seams.joint_calls == []


async def test_start_dev_large_stack_failure_names_both_probes(sf, make_app, seams):
    """未就绪的中文引导要能看出「前端与后端都要 200」（只说「前端未就绪」会把人引向错方向）。"""
    app = await make_app("zcb3fail", prod_port=8252, dev_port=8253,
                         stack="vue3-springboot", scale="large")
    seams.health_ok = False
    with pytest.raises(DeployError) as ei:
        await engine.start_dev(sf, app)
    msg = str(ei.value)
    assert "未就绪" in msg and "/api/health" in msg


# ---------- 预览工具侧的引擎收口（规格 2026-10-29-agent-preview-tools §5.4） ----------

async def test_dev_probe_paths_is_the_single_truth_for_health_routes():
    """健康路径只有一份真相：start_dev 与 probe_dev 都从这里取，不许各写一遍。

    本文件模块顶有 `pytestmark = asyncio`，纯逻辑用例也写成 async（写成 def 会多一条 PytestWarning）。
    """
    assert engine._dev_probe_paths("vue3-springboot") == ("/", "/api/health")
    assert engine._dev_probe_paths("vue3-fastapi") == ("/",)     # 小栈刻意不探后端（见 JOINT_HEALTH_STACKS 注释）
    assert engine._dev_probe_paths("static") == ("/",)
    assert engine._dev_probe_paths("") == ("/",)


async def test_probe_dev_reports_reachable_and_verdict(monkeypatch):
    FakeClient, seen = _client_returning(lambda url: 200)
    monkeypatch.setattr(engine.httpx, "AsyncClient", FakeClient)
    ok, verdict = await engine.probe_dev({"id": 1, "stack": "vue3-fastapi", "dev_port": 8231})
    assert ok is True and verdict == ""
    assert seen == ["http://127.0.0.1:8231/"]


async def test_probe_dev_verdict_carries_non_200(monkeypatch):
    FakeClient, _ = _client_returning(lambda url: 403)
    monkeypatch.setattr(engine.httpx, "AsyncClient", FakeClient)
    ok, verdict = await engine.probe_dev({"id": 1, "stack": "vue3-fastapi", "dev_port": 8231}, timeout=1)
    assert ok is False and "403" in verdict


async def test_probe_dev_probes_both_paths_for_large_stack(monkeypatch):
    FakeClient, seen = _client_returning(lambda url: 200)
    monkeypatch.setattr(engine.httpx, "AsyncClient", FakeClient)
    ok, _ = await engine.probe_dev({"id": 1, "stack": "vue3-springboot", "dev_port": 8233})
    assert ok is True
    assert any(u.endswith("8233/") for u in seen) and any(u.endswith("/api/health") for u in seen)


async def test_probe_dev_without_dev_port_does_not_hit_network(monkeypatch):
    """没有 dev 端口的应用不该发出任何 HTTP 请求（也不该抛），只回一句能转述给用户的说明。"""
    instantiated = []

    class Boom:
        def __init__(self, **kw):
            instantiated.append(kw)

    monkeypatch.setattr(engine.httpx, "AsyncClient", Boom)
    ok, verdict = await engine.probe_dev({"id": 1, "stack": "vue3-fastapi", "dev_port": None})
    assert ok is False and verdict == "应用没有 dev 端口"
    assert instantiated == []


async def test_strip_ansi_removes_subprocess_escape_sequences():
    """公开函数：Task 6 的日志整形要对平台步骤行也剥一遍（容器输出那条已由 parse_compose_logs 处理）。"""
    assert engine.strip_ansi("\x1b[33mVITE ready\x1b[39m") == "VITE ready"
    assert engine.strip_ansi("plain") == "plain"


async def test_container_logs_passes_timeout_through(monkeypatch, sf, make_app):
    """工具侧传 15s：读日志不许把整轮对话吊死；REST 仍是 60s 默认。"""
    app = await make_app("zcto", prod_port=8234, dev_port=8235)
    runner = FakeRunner({"compose logs": (0, "2026-09-20T14:27:58Z backend | hi")})
    monkeypatch.setattr(engine, "docker", runner)
    rows, note = await engine.container_logs(sf, app, 10, timeout=15)
    assert runner.calls[0]["timeout"] == 15 and note == "" and rows[0]["line"] == "hi"
    await engine.container_logs(sf, app, 10)
    assert runner.calls[1]["timeout"] == 60
