# tests/test_apps_dev_api.py
"""Task 12：dev 生命周期 REST + sqlchange 端点 + WS /ws/deploy/{app_id} + lifespan 集成。

落地裁定（控制者 D-1…D-4）：
  * D-2 client 一律取 conftest 的 `client`（`create_app(database_url=…)` → zerocode_test，
    Task 6 实证机制），不新造第二套 client——brief 的局部 fixture 连的是开发库；
  * D-3 平台启动的部署侧准备（cleanup_orphans / ensure_zcnet / build_if_missing）与退出停栈
    全部非致命：Docker 不可用只 log 告警，绝不卡住平台启动（专门的 lifespan 用例钉住）；
  * D-4 引擎调用是**后台任务**：202 先返回，任务句柄登记在 `app.state.dev_tasks`，
    断言形态为「202 → 后台任务确实跑完 → GET status」；重复任务在 `_spawn` 收口 409（不是 500）；
  * Docker 边界（compose ps、启动清理、镜像构建）在本文件用替身——真 Docker 行为由
    tests/test_engine.py / tests/test_sqlrunner.py 覆盖，本文件只验「REST/WS 接线」。
"""
import asyncio
import json
import logging
import shutil
from pathlib import Path

import pytest
from sqlalchemy import text
from starlette.testclient import TestClient

from app.db import make_session_factory
from app.deploy import engine as eng_mod, ports, provision, sqlrun
from app.deploy.errors import DeployError
from app.deploy.hub import DeployHub
from app.main import create_app
from app.services import app_service

pytestmark = pytest.mark.asyncio

SLUG = "zcapi"
ROOT = Path("D:/__zc_task12__")        # 应用工作区根（仓库外，沿用 Task 9/11 约定），fixture 收尾删掉


async def _free_pair(sf) -> tuple[int, int]:
    """当前登记状态下真实可用的一对端口（prod 偶 / dev 奇）。

    端口无关化（本波 Task 1）：插桩行不再写死 8294/8295——共享库里只要存在他方应用行占住那一对，
    插桩自己就撞 app_project_prod_port_key 假红（造干扰实测：Key (prod_port)=(8294) already exists）。
    """
    async with sf() as s:
        return await ports.allocate_pair(s)


async def _app_row(client, app_id: int) -> dict:
    """回读库内登记行：断言用 app["dev_port"]，而不是与插桩同步维护的端口字面量。"""
    return await app_service.get_app(client.app.state.session_factory, app_id)


@pytest.fixture(autouse=True)
def _docks_stubbed(monkeypatch):
    """本文件的每个用例都在 app 起来之前把「Docker 侧启动/退出动作」收口成替身：
    部署 REST/WS 接线与 Docker 真调用是两件事，后者已有专项单测（且 Docker Desktop 可能没开）。

    微修 I-1 后 dev/start、dev/restart 会在受理前**同步**预检 Docker，故 `preflight_docker` 同样
    要收成替身：本文件验接线（新用例①专门把它换成必抛 503 的版本），真预检语义由 test_engine.py 钉。
    """
    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(eng_mod, "cleanup_orphans", _noop)
    monkeypatch.setattr(eng_mod, "stop_all_dev_on_exit", _noop)
    monkeypatch.setattr(eng_mod, "preflight_docker", _noop)
    monkeypatch.setattr(sqlrun, "build_if_missing", _noop)
    monkeypatch.setattr(provision, "ensure_zcnet", _noop)


@pytest.fixture
async def dev_app(client, test_engine):
    """在测试库造一个应用（workspace + app_project），端口对由当前登记状态推导（不写字面量）。

    依赖 `client` 以保证顺序：app 的 lifespan 启动清理先跑完，再插本用例的行
    （否则 startup 的孤儿清理会去 docker compose 一个还不存在的应用）。
    """
    sf = make_session_factory(test_engine)
    ROOT.mkdir(parents=True, exist_ok=True)
    prod_port, dev_port = await _free_pair(sf)
    async with sf() as s:
        wid = (await s.execute(text("INSERT INTO workspace (name, root_path) VALUES ('api', :r) "
                                    "RETURNING id"), {"r": str(ROOT)})).scalar()
        aid = (await s.execute(text(
            "INSERT INTO app_project (workspace_id, name, slug, scale, stack, prod_port, dev_port) "
            "VALUES (:w, 'api', :sl, 'small', 'vue3-fastapi', :pp, :dp) RETURNING id"),
            {"w": wid, "sl": SLUG, "pp": prod_port, "dp": dev_port})).scalar()
        await s.commit()
    yield aid
    async with sf() as s:
        await s.execute(text("DELETE FROM app_project WHERE id=:i"), {"i": aid})
        await s.execute(text("DELETE FROM workspace WHERE id=:i"), {"i": wid})
        await s.execute(text("DELETE FROM app_config WHERE key LIKE :p"), {"p": f"dbcred.{SLUG}.%"})
        await s.commit()
    shutil.rmtree(ROOT, ignore_errors=True)


async def _set_cred(sf, slug: str, env: str = "dev") -> None:
    """登记假凭据（不建库不建角色）：应用库连接链路由 fail-soft 用例专门验「连不上也返回空」。"""
    async with sf() as s:
        await s.execute(text("INSERT INTO app_config (key, value) VALUES (:k, CAST(:v AS JSONB)) "
                             "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, "
                             "updated_at = now()"),
                        {"k": f"dbcred.{slug}.{env}",
                         "v": json.dumps({"db": f"{slug}_{env}", "user": f"{slug}_{env}",
                                          "password": "fake-pw"})})
        await s.commit()


def _drain(q: asyncio.Queue) -> list[dict]:
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


# ---------- dev 生命周期：202 + 后台任务 + 状态 ----------

async def test_dev_start_202_and_status(client, dev_app, monkeypatch):
    """202 立即返回（引擎在后台任务里跑完，D-4），进度事件经 hub 走 WS；status 由引擎给预览地址。

    预览地址里的端口取自库内登记行（本波 Task 1 端口无关化）：插桩端口是推导出来的，
    写死 8295 会在共享库他方行占位时假红，也会让断言退化成「替身返回了它自己写的字面量」。
    """
    app = client.app
    started = asyncio.Event()
    captured = {}
    registered = await _app_row(client, dev_app)        # 本应用登记的端口对

    async def fake_start(sf, a, notify=None):
        captured["notify"] = notify
        captured["slug"] = a["slug"]
        started.set()
        return {"status": "running", "url": f"http://localhost:{a['dev_port']}"}

    async def no_docker(*args, **kwargs):        # dev_status 的 compose ps 不打真 Docker
        return 0, ""

    monkeypatch.setattr(eng_mod, "start_dev", fake_start)
    monkeypatch.setattr(eng_mod, "docker", no_docker)

    r = await client.post(f"/api/apps/{dev_app}/dev/start")
    assert r.status_code == 202 and r.json() == {"ok": True}
    await asyncio.wait_for(started.wait(), 2)     # 响应之后后台任务确实继续跑（不是被丢弃）
    assert captured["slug"] == SLUG               # 引擎收到的正是该应用（app_service.get_app 结果）
    assert captured["notify"] == app.state.deploy_hub.publish   # 进度接到 DeployHub（WS 事件流的源）
    assert dev_app in app.state.dev_tasks         # 长任务句柄登记在案（重复启动防并发的依据）

    r2 = await client.get(f"/api/apps/{dev_app}/dev/status")
    assert r2.status_code == 200
    body = r2.json()
    assert body["url"] == f"http://localhost:{registered['dev_port']}"
    assert registered["prod_port"] % 2 == 0 and registered["dev_port"] == registered["prod_port"] + 1
    assert body["status_dev"] == "stopped"        # 替身不写库：状态是引擎/真链路的职责


async def test_dev_start_and_restart_gate_docker_synchronously(client, dev_app, monkeypatch):
    """微修 I-1：Docker 没开是最高频故障——start/restart 在**受理前**同步预检，用户当场拿到 503 中文引导。

    引擎侧契约（Task 11 / 610a943）是「预检失败零事件、异常原样上抛供 REST 映射 503」，前提是调用方
    还在请求线程里；一旦进了 create_task，映射点就消失了（202 + WS 静默 + 日志一条 never retrieved）。
    所以断言两件事：**同步 503**，且**后台任务压根没登记**（dev_tasks 无该 app_id）。
    stop 不吃这道闸门：daemon 挂着时 compose down 本就无意义，仍 202 尽力而为（平台启动清理兜底）。
    """
    async def down(*args, **kwargs):
        raise DeployError("Docker 未启动或不可用：请打开 Docker Desktop（设置页「部署环境」可一键启动）",
                          status=503)

    async def fake_stop(sf, a, notify=None):
        return {"status": "stopped"}

    monkeypatch.setattr(eng_mod, "preflight_docker", down)
    monkeypatch.setattr(eng_mod, "stop_dev", fake_stop)

    for action in ("start", "restart"):
        r = await client.post(f"/api/apps/{dev_app}/dev/{action}")
        assert r.status_code == 503, action                     # 同步可见，不是 202 + 静默
        assert "Docker Desktop" in r.json()["detail"]           # 中文引导原样进 detail
        assert dev_app not in client.app.state.dev_tasks, action  # 没有后台任务被登记

    assert (await client.post(f"/api/apps/{dev_app}/dev/stop")).status_code == 202
    await client.app.state.dev_tasks[dev_app]                   # stop 照常被受理并跑完


async def test_dev_spawned_task_crash_is_logged(client, dev_app, monkeypatch, caplog):
    """微修 I-1 配套（审查 Minor-3）：`_spawn` 的 done 回调要 retrieve 后台异常并记 exception 日志。

    不 retrieve 的后果不是「日志少了」而是日志脏了：asyncio 只在任务被 GC 时甩一条
    「Task exception was never retrieved」，既没有上下文也拦不住任何东西。
    反向同样要钉：成功与被取消的任务必须**沉默**——无脑记 ERROR 等于把正常链路刷成满屏堆栈，
    而对已取消的 task 调 `exception()` 会再抛一次 CancelledError（回调里抛 = 新噪音）。
    """
    def _errors() -> list[logging.LogRecord]:
        return [r for r in caplog.records if r.levelno >= logging.ERROR]

    async def boom(sf, a, notify=None):
        raise RuntimeError("预览任务内部炸了")

    async def ok(sf, a, notify=None):
        return {"status": "running"}

    async def blocking(sf, a, notify=None):
        await asyncio.Event().wait()                      # 永远跑不完 → 由用例取消

    with caplog.at_level(logging.ERROR):
        monkeypatch.setattr(eng_mod, "start_dev", boom)
        assert (await client.post(f"/api/apps/{dev_app}/dev/start")).status_code == 202
        with pytest.raises(RuntimeError):
            await client.app.state.dev_tasks[dev_app]
        await asyncio.sleep(0)                            # done 回调经 call_soon 排队：让 loop 转一圈
        recs = _errors()
        assert len(recs) == 1 and isinstance(recs[0].exc_info[1], RuntimeError)
        assert "预览任务内部炸了" in caplog.text and str(dev_app) in caplog.text

        caplog.clear()                                    # 成功收口：一条 ERROR 都不该有
        monkeypatch.setattr(eng_mod, "start_dev", ok)
        assert (await client.post(f"/api/apps/{dev_app}/dev/start")).status_code == 202
        await client.app.state.dev_tasks[dev_app]
        await asyncio.sleep(0)
        assert _errors() == []

        caplog.clear()                                    # 取消收口：同样沉默
        monkeypatch.setattr(eng_mod, "start_dev", blocking)
        assert (await client.post(f"/api/apps/{dev_app}/dev/start")).status_code == 202
        task = client.app.state.dev_tasks[dev_app]
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0)
        assert _errors() == []


async def test_dev_start_duplicate_task_is_409(client, dev_app, monkeypatch):
    """同一应用已有部署任务在跑 → 409 中文提示（D-4：不是 500，也不是排队双起）。"""
    gate = asyncio.Event()

    async def blocking_start(sf, a, notify=None):
        await gate.wait()
        return {"status": "running"}

    monkeypatch.setattr(eng_mod, "start_dev", blocking_start)
    assert (await client.post(f"/api/apps/{dev_app}/dev/start")).status_code == 202
    r = await client.post(f"/api/apps/{dev_app}/dev/start")
    assert r.status_code == 409 and "进行中" in r.json()["detail"]

    gate.set()
    await client.app.state.dev_tasks[dev_app]     # 前一个任务收口（不留悬挂任务）
    assert (await client.post(f"/api/apps/{dev_app}/dev/start")).status_code == 202
    await client.app.state.dev_tasks[dev_app]


async def test_dev_stop_and_restart_delegate_to_engine(client, dev_app, monkeypatch):
    """stop/restart 与 start 同一形态：202 受理 + 后台调引擎（引擎自己发 stopping/stopped 事件）。"""
    seen: list[str] = []

    async def fake_stop(sf, a, notify=None):
        seen.append("stop")
        return {"status": "stopped"}

    async def fake_restart(sf, a, notify=None):
        seen.append("restart")
        return {"status": "running"}

    monkeypatch.setattr(eng_mod, "stop_dev", fake_stop)
    monkeypatch.setattr(eng_mod, "restart_dev", fake_restart)

    assert (await client.post(f"/api/apps/{dev_app}/dev/stop")).status_code == 202
    await client.app.state.dev_tasks[dev_app]
    assert (await client.post(f"/api/apps/{dev_app}/dev/restart")).status_code == 202
    await client.app.state.dev_tasks[dev_app]
    assert seen == ["stop", "restart"]


async def test_dev_endpoints_404_for_unknown_app(client):
    """不存在的应用 id：dev 与 sqlchange 全线 404（DeployError.status → HTTP 状态码）。"""
    assert (await client.post("/api/apps/999999/dev/start")).status_code == 404
    assert (await client.post("/api/apps/999999/dev/stop")).status_code == 404
    assert (await client.post("/api/apps/999999/dev/restart")).status_code == 404
    assert (await client.get("/api/apps/999999/dev/status")).status_code == 404
    assert (await client.post("/api/apps/999999/sqlchange/apply")).status_code == 404
    assert (await client.get("/api/apps/999999/sqlchange/files")).status_code == 404


# ---------- sqlchange 端点 ----------

async def test_sqlchange_files_and_records(client, dev_app):
    """无 sqlchange 目录 / 无凭据都返回空列表不报错（前端首屏友好）；env 白名单 400。"""
    r = await client.get(f"/api/apps/{dev_app}/sqlchange/files")
    assert r.status_code == 200 and r.json() == []
    r2 = await client.get(f"/api/apps/{dev_app}/sqlchange/records?env=dev")
    assert r2.status_code == 200 and r2.json() == []
    r3 = await client.get(f"/api/apps/{dev_app}/sqlchange/records?env=staging")
    assert r3.status_code == 400 and "dev/prod" in r3.json()["detail"]


async def test_sqlchange_files_lists_pending(client, dev_app):
    """目录里的 V*.sql 按文件名排序给出，未记账的一律 applied=False（待执行徽章的来源）。"""
    d = ROOT / "sqlchange"
    d.mkdir(parents=True, exist_ok=True)
    (d / "V20260918_02_b.sql").write_text("-- b\nSELECT 1\n", encoding="utf-8")
    (d / "V20260918_01_a.sql").write_text("-- a\nSELECT 1\n", encoding="utf-8")
    (d / "notes.md").write_text("非迁移文件\n", encoding="utf-8")

    got = (await client.get(f"/api/apps/{dev_app}/sqlchange/files")).json()
    assert [f["filename"] for f in got] == ["V20260918_01_a.sql", "V20260918_02_b.sql"]
    assert all(f["applied"] is False and f["success"] is None for f in got)


async def test_sqlchange_records_fail_soft_without_reachable_db(client, dev_app):
    """凭据在、库不可达（应用刚建还没供给 / 角色被手工删掉）→ 空列表而不是 500。"""
    await _set_cred(client.app.state.session_factory, SLUG)
    r = await client.get(f"/api/apps/{dev_app}/sqlchange/records?env=dev")
    assert r.status_code == 200 and r.json() == []
    r2 = await client.get(f"/api/apps/{dev_app}/sqlchange/files")
    assert r2.status_code == 200                      # 同样不因连不上库而炸


async def test_sqlchange_apply_202_and_events(client, dev_app, monkeypatch):
    """apply 受理 202；迁移每行日志与终态以 dev_log/migrate_result 进 DeployHub（WS 的下发源）。"""
    await _set_cred(client.app.state.session_factory, SLUG)
    hub = client.app.state.deploy_hub
    q = hub.subscribe(dev_app)
    seen = {}

    async def fake_run(root, env, cred, on_log=None):
        seen["root"], seen["env"], seen["cred"] = str(root), env, dict(cred)
        on_log("OK V20260918_01_a.sql")               # 真 run_sqlchange 传的就是 hub.publish 形态的同步回调
        return {"ok": True, "code": 0, "log": "ALL DONE"}

    monkeypatch.setattr(sqlrun, "run_sqlchange", fake_run)
    r = await client.post(f"/api/apps/{dev_app}/sqlchange/apply")
    assert r.status_code == 202 and r.json() == {"ok": True}
    await client.app.state.dev_tasks[dev_app]

    assert seen["env"] == "dev" and seen["root"] == str(ROOT)      # 跑在应用根的 dev 库上
    assert seen["cred"]["db"] == f"{SLUG}_dev"
    evs = _drain(q)
    assert [e["type"] for e in evs] == ["dev_log", "migrate_result"]
    assert evs[0]["line"] == "OK V20260918_01_a.sql" and evs[0]["app_id"] == dev_app
    assert evs[1]["env"] == "dev" and evs[1]["ok"] is True and evs[1]["app_id"] == dev_app


async def test_sqlchange_apply_without_cred_emits_failed_result(client, dev_app):
    """dev 凭据缺失：后台不能静默死掉——终态事件必发（否则前端永远停在「迁移中」）。"""
    hub = client.app.state.deploy_hub
    q = hub.subscribe(dev_app)
    assert (await client.post(f"/api/apps/{dev_app}/sqlchange/apply")).status_code == 202
    await client.app.state.dev_tasks[dev_app]
    evs = _drain(q)
    assert len(evs) == 1 and evs[0]["type"] == "migrate_result"
    assert evs[0]["ok"] is False and "凭据" in evs[0]["log"]


async def test_sqlchange_apply_reports_docker_failure(client, dev_app, monkeypatch):
    """迁移入口抛异常（docker 未启动等）同样收口成 migrate_result 失败终态，不冒 500。"""
    await _set_cred(client.app.state.session_factory, SLUG)
    hub = client.app.state.deploy_hub
    q = hub.subscribe(dev_app)

    async def boom(root, env, cred, on_log=None):
        raise DeployError("Docker 未启动或不可用", status=503)

    monkeypatch.setattr(sqlrun, "run_sqlchange", boom)
    assert (await client.post(f"/api/apps/{dev_app}/sqlchange/apply")).status_code == 202
    await client.app.state.dev_tasks[dev_app]
    evs = _drain(q)
    assert len(evs) == 1 and evs[0]["type"] == "migrate_result" and evs[0]["ok"] is False
    assert "Docker" in evs[0]["log"]


# ---------- WS /ws/deploy/{app_id} ----------

async def test_ws_deploy_hello(client, dev_app):
    """连接即回一条 hello（前端据此确认订阅成功、开始拉状态）。"""
    with TestClient(client.app) as tc:
        with tc.websocket_connect(f"/ws/deploy/{dev_app}") as ws:
            msg = ws.receive_json()
            assert msg["type"] == "hello" and msg["app_id"] == dev_app


async def test_ws_deploy_streams_engine_events(client, dev_app, monkeypatch):
    """REST 受理 → 后台任务 → hub → WS：整条事件流接得上（UI 只看这条流，不看轮询）。"""
    async def fake_start(sf, a, notify=None):
        notify(a["id"], {"app_id": a["id"], "type": "dev_status", "status": "running"})
        return {"status": "running"}

    monkeypatch.setattr(eng_mod, "start_dev", fake_start)
    with TestClient(client.app) as tc:
        # guard（Ticket 002）：裸 TestClient 无会话，复用 client fixture 的 owner cookie 同 app 同库
        tc.cookies.set("zc_actor", client.cookies.get("zc_actor"))
        with tc.websocket_connect(f"/ws/deploy/{dev_app}") as ws:
            assert ws.receive_json()["type"] == "hello"
            assert tc.post(f"/api/apps/{dev_app}/dev/start").status_code == 202
            ev = ws.receive_json()
            assert ev["type"] == "dev_status" and ev["status"] == "running"


async def test_ws_deploy_keepalive_ping_keeps_stream_open(client, dev_app, monkeypatch):
    """空闲只发保活 ping，**连接不断**（预览构建期可能几十秒无输出，掉流等于 UI 失明）。"""
    from app.api import deploy_ws as ws_api

    monkeypatch.setattr(ws_api, "_KEEPALIVE", 0.05)
    with TestClient(client.app) as tc:
        with tc.websocket_connect(f"/ws/deploy/{dev_app}") as ws:
            assert ws.receive_json()["type"] == "hello"
            assert [ws.receive_json()["type"] for _ in range(3)] == ["ping", "ping", "ping"]


async def test_ws_deploy_unsubscribes_on_disconnect(client, dev_app):
    """断连必须退订：每次预览页开合都留一个僵尸队列的话，hub 会被越堆越大的槽吃满。"""
    with TestClient(client.app) as tc:
        hub = tc.app.state.deploy_hub
        with tc.websocket_connect(f"/ws/deploy/{dev_app}") as ws:
            assert ws.receive_json()["type"] == "hello"
            assert hub.subs.get(dev_app)
        assert not hub.subs.get(dev_app)


# ---------- lifespan 集成（D-3） ----------

async def test_lifespan_startup_wires_deploy_state_and_never_blocks(test_engine, monkeypatch, caplog):
    """启动：deploy_hub/dev_tasks 就位 + 孤儿清理/zcnet/镜像构建按序尝试，
    Docker 侧任何一步失败只 log 告警（平台照常起来）；退出：按配置停 dev 栈。"""
    order: list = []

    async def fake_cleanup(sf, notify=None):
        order.append("cleanup")
        raise RuntimeError("docker CLI 未安装")       # 最坏情形：整步失败

    async def fake_zcnet():
        order.append("zcnet")

    async def fake_build():
        order.append("build")

    async def fake_exit(sf, cfg, notify=None):
        order.append(("exit", "deploy.health_timeout" in cfg))

    monkeypatch.setattr(eng_mod, "cleanup_orphans", fake_cleanup)
    monkeypatch.setattr(eng_mod, "stop_all_dev_on_exit", fake_exit)
    monkeypatch.setattr(sqlrun, "build_if_missing", fake_build)
    monkeypatch.setattr(provision, "ensure_zcnet", fake_zcnet)

    app = create_app(test_engine.url.render_as_string(hide_password=False))
    with caplog.at_level(logging.WARNING):
        with TestClient(app) as tc:
            tc.post("/api/auth/register", json={"nickname": "lifespan-看门", "password": "pw"})  # guard 会话
            assert isinstance(app.state.deploy_hub, DeployHub)   # WS 与引擎共用的事件总线
            assert app.state.dev_tasks == {}                     # 长任务句柄表
            assert tc.get("/api/apps").status_code == 200        # 启动清理失败没把平台卡死
    assert order == ["cleanup", "zcnet", "build", ("exit", True)]
    assert "部署环境启动清理失败" in caplog.text                  # 但必须留下告警痕迹
