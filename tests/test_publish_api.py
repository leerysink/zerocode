# tests/test_publish_api.py
"""Task 8：发布/回滚/prod 生命周期 REST + chat 闸门 + lifespan 对账接线（规格 04 §5）。

与 dev 线（test_apps_dev_api.py）同一条契约：202 受理 + 后台执行 + `_spawn` 单任务，
进度经 DeployHub → /ws/deploy/{app_id}；publish/rollback 前置**同步** preflight_docker
（Task 11/12 契约：预检异常零事件、503 立答）。本文件全替身，不碰真 Docker。

三处现状核实（brief 自留项）：
  ① chat_session 列 = id/workspace_id/title/status/…（V20260917_01_init.sql），brief 的
     `INSERT INTO chat_session (workspace_id, title)` 原样可用；
  ② hub 属性名 = `app.state.hub`（ChatHub，app/main.py），`active_session_ids()` 存在——
     事件总线另有 `app.state.deploy_hub`（DeployHub），两者不可混用；
  ③ records 端点响应形状 = list[dict]（records.list_for），`?limit=` 为查询参数。

对 brief 测试的一处按实况加固：`_mk` 走 POST /api/apps 真建工程目录并真供给 dev 库，
brief 的 `_wipe` 只删行 → 同 slug 第二次 `_mk` 会被「目录已存在且非空」钉成 409；
故 `_wipe` 补 `provision.drop_env` + `shutil.rmtree`（与 test_apps_api.py 同款），
并改为 autouse 前置/后置各清一次（断言失败也不给下一个测试留残骸）。
"""
import asyncio, logging, shutil
import pytest
from sqlalchemy import text
from starlette.testclient import TestClient
from app.db import make_session_factory
from app.deploy import engine as eng_mod, prod as prod_mod, provision, records as deploy_records, sqlrun
from app.deploy.errors import DeployError
from app.deploy.settings import REPO_ROOT
from app.main import create_app

pytestmark = pytest.mark.asyncio

SLUG = "zcpubapi"
APP_DIR = REPO_ROOT / "apps" / SLUG
VERSION = "b20260920123456"


async def _mk(client) -> int:
    r = await client.post("/api/apps", json={"name": "发布口", "slug": SLUG,
                                             "scale": "small", "stack": "vue3-fastapi"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _wipe(client) -> None:
    """清掉本用例的全部产物：deploy_record/app_project/workspace 行 + dev 库/角色/凭据 + 工程目录。"""
    sf = client.app.state.session_factory
    async with sf() as s:
        ids = (await s.execute(text("SELECT id FROM app_project WHERE slug=:s"),
                               {"s": SLUG})).scalars().all()
        if ids:
            await s.execute(text("DELETE FROM deploy_record WHERE app_id = ANY(:ids)"), {"ids": ids})
            await s.execute(text("DELETE FROM app_project WHERE slug=:s"), {"s": SLUG})
        await s.execute(text("DELETE FROM workspace WHERE root_path LIKE :p"), {"p": f"%{SLUG}"})
        await s.commit()
    try:
        await provision.drop_env(sf, SLUG, "dev")     # 幂等：库/角色/dbcred 键一并收掉
    except Exception:
        pass
    shutil.rmtree(APP_DIR, ignore_errors=True)


@pytest.fixture(autouse=True)
async def _clean(client):
    await _wipe(client)
    yield
    await _wipe(client)


@pytest.fixture
async def no_preflight(monkeypatch):
    """发布/回滚/prod 启动类端点都带同步预检——本文件不碰真 Docker，一律收成通过。"""
    async def ok():
        return None
    monkeypatch.setattr(prod_mod, "preflight_docker", ok)
    return ok


# ---------- 发布闸门（04 §5 / FR4-09）+ publishing 并发闸门（T7 审查交接）----------

async def test_publish_chat_gate_and_spawn_shape(client, monkeypatch, no_preflight):
    """chat 活跃且非 force → 409 + dict detail；force 显式确认 → 202 受理且后台确实起跑。"""
    aid = await _mk(client)
    called = []

    async def fake_publish(sf, app, notify=None, *, force=False, confirm_destructive=False):
        called.append(force)
        return {"status": "running", "version": VERSION, "url": "http://x", "recordId": 1}

    monkeypatch.setattr(prod_mod, "publish", fake_publish)
    # chat 活跃：该应用的工程工作区下有一条会话，且它出现在 hub 的活跃登记表里
    async with client.app.state.session_factory() as s:
        wsid = (await s.execute(text("SELECT workspace_id FROM app_project WHERE id=:i"),
                                {"i": aid})).scalar()
        sid = (await s.execute(text("INSERT INTO chat_session (workspace_id, title) "
                                    "VALUES (:w,'t') RETURNING id"), {"w": wsid})).scalar()
        await s.commit()
    monkeypatch.setattr(client.app.state.hub, "active_session_ids", lambda: [sid])

    r = await client.post(f"/api/apps/{aid}/publish", json={"force": False})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "chat_active"
    assert r.json()["detail"]["message"]            # 文案走 detail.message（前端 {detail} 通道可用）
    assert not called
    assert aid not in client.app.state.dev_tasks    # 被闸门挡下时不留后台任务句柄

    r = await client.post(f"/api/apps/{aid}/publish", json={"force": True})
    assert r.status_code == 202 and r.json() == {"ok": True}
    await client.app.state.dev_tasks[aid]           # join 后台任务（替身，秒回）
    assert called == [True]
    # 记录接口形状（brief ③）
    rr = await client.get(f"/api/apps/{aid}/records")
    assert rr.status_code == 200 and isinstance(rr.json(), list)


async def test_publish_forwards_confirm_destructive(client, monkeypatch, no_preflight):
    """端点把 confirmDestructive 透传给引擎（与 force 解耦）：body→kwarg 双向可观测。"""
    aid = await _mk(client)
    seen = []

    async def cap(sf, app, notify=None, *, force=False, confirm_destructive=False):
        seen.append((force, confirm_destructive))
        return {"status": "running"}

    monkeypatch.setattr(prod_mod, "publish", cap)
    r = await client.post(f"/api/apps/{aid}/publish",
                          json={"force": False, "confirmDestructive": True})
    assert r.status_code == 202, r.text
    await client.app.state.dev_tasks[aid]
    assert seen == [(False, True)]        # force 未开、破坏性确认开


async def test_publish_chat_gate_only_own_workspace(client, monkeypatch, no_preflight):
    """闸门只看**本应用工作区**的活跃会话：别的应用在改代码不该挡住这次发布。"""
    aid = await _mk(client)
    called = []

    async def fake_publish(sf, app, notify=None, *, force=False, confirm_destructive=False):
        called.append(app["slug"])
        return {"status": "running"}

    monkeypatch.setattr(prod_mod, "publish", fake_publish)
    async with client.app.state.session_factory() as s:
        other = (await s.execute(text("INSERT INTO workspace (name, root_path) "
                                      "VALUES ('他人工程', :r) RETURNING id"),
                                {"r": str(REPO_ROOT / "apps" / "otherzcpub")})).scalar()
        sid = (await s.execute(text("INSERT INTO chat_session (workspace_id, title) "
                                    "VALUES (:w,'t') RETURNING id"), {"w": other})).scalar()
        await s.commit()
    monkeypatch.setattr(client.app.state.hub, "active_session_ids", lambda: [sid])
    try:
        r = await client.post(f"/api/apps/{aid}/publish", json={"force": False})
        assert r.status_code == 202, r.text
        await client.app.state.dev_tasks[aid]
        assert called == [SLUG]
    finally:
        async with client.app.state.session_factory() as s:
            await s.execute(text("DELETE FROM chat_session WHERE id=:i"), {"i": sid})
            await s.execute(text("DELETE FROM workspace WHERE id=:i"), {"i": other})
            await s.commit()


async def test_publish_409_when_status_publishing(client, monkeypatch, no_preflight):
    """publishing 状态下禁止并发第二次发布（04 §4.1 / T7 审查交接）：端点层 409「发布进行中」。

    `_spawn` 的「该应用有部署任务进行中」只挡本进程在跑的任务；publishing 可能是上一进程
    遗留（崩溃后 status 还没复位），所以这道按状态判的显式闸门是双保险。
    """
    aid = await _mk(client)
    called = []

    async def fake_publish(sf, app, notify=None, *, force=False, confirm_destructive=False):
        called.append(force)
        return {"status": "running"}

    monkeypatch.setattr(prod_mod, "publish", fake_publish)
    async with client.app.state.session_factory() as s:
        await s.execute(text("UPDATE app_project SET status_prod='publishing' WHERE id=:i"),
                        {"i": aid})
        await s.commit()

    for body in ({"force": False}, {"force": True}):
        r = await client.post(f"/api/apps/{aid}/publish", json=body)
        assert r.status_code == 409, body
        assert "发布进行中" in r.json()["detail"], body
        assert aid not in client.app.state.dev_tasks, body
    assert not called


# ---------- 回滚（04 §4.4）：版本号格式校验 → 202 ----------

async def test_rollback_bad_version_400(client):
    aid = await _mk(client)
    for bad in ("latest", "b2026", "b2026092012345", "b202609201234567", "x20260920123456", ""):
        r = await client.post(f"/api/apps/{aid}/rollback", json={"version": bad})
        assert r.status_code == 400, bad
        assert aid not in client.app.state.dev_tasks, bad     # 脏输入不打扰 Docker，也不留任务


async def test_rollback_valid_version_accepts_and_passes_through(client, monkeypatch, no_preflight):
    aid = await _mk(client)
    seen = {}

    async def fake_rollback(sf, app, version=None, notify=None):
        seen["version"], seen["slug"] = version, app["slug"]
        return {"status": "running", "version": version}

    monkeypatch.setattr(prod_mod, "rollback", fake_rollback)
    r = await client.post(f"/api/apps/{aid}/rollback", json={"version": VERSION})
    assert r.status_code == 202 and r.json() == {"ok": True}
    await client.app.state.dev_tasks[aid]
    assert seen == {"version": VERSION, "slug": SLUG}


# ---------- prod 生命周期（04 §5）：start/restart 同步预检，stop 不吃闸门 ----------

async def test_prod_start_and_restart_gate_docker_synchronously(client, monkeypatch):
    """与 dev 同契约（Task 11/12）：Docker 没开时**受理前**同步 503，零事件、不留后台任务。"""
    aid = await _mk(client)

    async def down():
        raise DeployError("Docker 未启动或不可用：请打开 Docker Desktop（设置页「部署环境」可一键启动）",
                          status=503)

    async def fake_start(sf, app, notify=None):
        return {"status": "running"}

    monkeypatch.setattr(prod_mod, "preflight_docker", down)
    monkeypatch.setattr(prod_mod, "prod_start", fake_start)
    for action in ("start", "restart"):
        r = await client.post(f"/api/apps/{aid}/prod/{action}")
        assert r.status_code == 503, action
        assert "Docker Desktop" in r.json()["detail"], action
        assert aid not in client.app.state.dev_tasks, action


async def test_prod_lifecycle_accepts_202_and_stop_skips_gate(client, monkeypatch):
    """stop 不需要同步预检（daemon 挂着时 compose down 本就无意义）；start/restart 正常受理 202。"""
    aid = await _mk(client)
    seen: list[str] = []

    async def down():
        raise DeployError("Docker 未启动或不可用", status=503)

    async def fake_start(sf, app, notify=None):
        seen.append("start")
        return {"status": "running"}

    async def fake_stop(sf, app, notify=None):
        seen.append("stop")
        return {"status": "stopped"}

    async def fake_restart(sf, app, notify=None):
        seen.append("restart")
        return {"status": "running"}

    monkeypatch.setattr(prod_mod, "preflight_docker", down)     # 闸门是坏的：只有 stop 过得去
    monkeypatch.setattr(prod_mod, "prod_stop", fake_stop)
    r = await client.post(f"/api/apps/{aid}/prod/stop")
    assert r.status_code == 202, r.text
    await client.app.state.dev_tasks[aid]

    async def ok():
        return None
    monkeypatch.setattr(prod_mod, "preflight_docker", ok)
    monkeypatch.setattr(prod_mod, "prod_start", fake_start)
    monkeypatch.setattr(prod_mod, "prod_restart", fake_restart)
    for action in ("start", "restart"):
        assert (await client.post(f"/api/apps/{aid}/prod/{action}")).status_code == 202, action
        await client.app.state.dev_tasks[aid]
    assert seen == ["stop", "start", "restart"]


async def test_prod_status_shape(client, monkeypatch):
    """GET /prod/status 直返引擎结果（含 urls 块）：端点不裁字段、不加包装。"""
    aid = await _mk(client)

    async def fake_status(sf, app):
        return {"status_prod": "stopped", "compose_ps": "", "domain": "https://demo.zerocode.com"}

    monkeypatch.setattr(prod_mod, "prod_status", fake_status)
    r = await client.get(f"/api/apps/{aid}/prod/status")
    assert r.status_code == 200 and r.json()["status_prod"] == "stopped"
    assert "domain" in r.json()


# ---------- 发布历史（04 §5 / FR4-08）：GET 列表 + DELETE 删行并尽力删镜像 ----------

async def test_records_list_and_limit(client):
    aid = await _mk(client)
    sf = client.app.state.session_factory
    for v in (VERSION, "b20260920123457"):
        rid = await deploy_records.begin(sf, aid, "publish", v)
        await deploy_records.finish(sf, rid, "success")
    full = await client.get(f"/api/apps/{aid}/records")
    assert full.status_code == 200 and len(full.json()) == 2
    assert {"version", "kind", "status"} <= set(full.json()[0])
    one = await client.get(f"/api/apps/{aid}/records?limit=1")
    assert one.status_code == 200 and len(one.json()) == 1


async def test_record_delete_removes_row_and_rmi_best_effort(client, monkeypatch):
    """FR4-08：删历史行 + 尽力 rmi 该版本两镜像；镜像删不掉也不回滚删除；rid 不属该应用 → 404。"""
    aid = await _mk(client)
    sf = client.app.state.session_factory
    rid = await deploy_records.begin(sf, aid, "publish", VERSION)
    await deploy_records.finish(sf, rid, "success")
    calls: list[tuple] = []

    async def fake_docker(*args, cwd=None, on_line=None, timeout=600):
        calls.append(args)
        return 0, ""

    monkeypatch.setattr(prod_mod, "_docker", fake_docker)
    assert (await client.delete(f"/api/apps/{aid}/records/999999")).status_code == 404
    assert calls == []                                        # 不存在/不属该应用：不碰 Docker

    r = await client.delete(f"/api/apps/{aid}/records/{rid}")
    assert r.status_code == 200 and r.json() == {"ok": True}, r.text
    assert (await deploy_records.list_for(sf, aid)) == []
    assert calls == [("image", "rm", f"zc-{SLUG}-frontend:prod-{VERSION}"),
                     ("image", "rm", f"zc-{SLUG}-backend:prod-{VERSION}")]

    # 镜像 rmi 抛异常（Docker 未就绪）也只记 warning，删除结果照收
    rid2 = await deploy_records.begin(sf, aid, "publish", "b20260920123458")
    await deploy_records.finish(sf, rid2, "success")

    async def boom(*args, cwd=None, on_line=None, timeout=600):
        raise RuntimeError("docker daemon 失联")

    monkeypatch.setattr(prod_mod, "_docker", boom)
    assert (await client.delete(f"/api/apps/{aid}/records/{rid2}")).status_code == 200
    assert (await deploy_records.list_for(sf, aid)) == []


async def test_publish_prod_endpoints_404_for_unknown_app(client):
    """不存在的应用 id：publish/rollback/prod 全线 404（DeployError.status → HTTP 状态码）。"""
    assert (await client.post("/api/apps/999999/publish", json={"force": True})).status_code == 404
    assert (await client.post("/api/apps/999999/rollback", json={"version": VERSION})).status_code == 404
    for action in ("start", "stop", "restart"):
        assert (await client.post(f"/api/apps/999999/prod/{action}")).status_code == 404
    assert (await client.get("/api/apps/999999/prod/status")).status_code == 404
    assert (await client.get("/api/apps/999999/records")).status_code == 404
    assert (await client.delete("/api/apps/999999/records/1")).status_code == 404


# ---------- lifespan 对账接线（04 §6）----------

async def test_lifespan_reconciles_prod_on_startup(test_engine, monkeypatch):
    """启动尾部挂 `prod.reconcile`（notify 接 DeployHub），失败只 log 不卡启动。"""
    async def _noop(*args, **kwargs):
        return None

    seen = {}

    async def fake_reconcile(sf, notify=None):
        seen["sf"] = sf
        seen["notify"] = notify

    monkeypatch.setattr(eng_mod, "cleanup_orphans", _noop)
    monkeypatch.setattr(eng_mod, "stop_all_dev_on_exit", _noop)
    monkeypatch.setattr(sqlrun, "build_if_missing", _noop)
    monkeypatch.setattr(provision, "ensure_zcnet", _noop)
    monkeypatch.setattr(prod_mod, "reconcile", fake_reconcile)

    app = create_app(test_engine.url.render_as_string(hide_password=False))
    with TestClient(app) as tc:
        assert tc.get("/api/apps").status_code == 200
    assert seen["notify"] == app.state.deploy_hub.publish
    assert seen["sf"] is app.state.session_factory


async def test_lifespan_prod_reconcile_failure_is_not_fatal(test_engine, monkeypatch, caplog):
    """Docker 未就绪时 reconcile 抛异常：平台照常起来（与 cleanup_orphans 同一条非致命纪律）。"""
    async def _noop(*args, **kwargs):
        return None

    async def boom(sf, notify=None):
        raise RuntimeError("docker CLI 未安装")

    monkeypatch.setattr(eng_mod, "cleanup_orphans", _noop)
    monkeypatch.setattr(eng_mod, "stop_all_dev_on_exit", _noop)
    monkeypatch.setattr(sqlrun, "build_if_missing", _noop)
    monkeypatch.setattr(provision, "ensure_zcnet", _noop)
    monkeypatch.setattr(prod_mod, "reconcile", boom)

    app = create_app(test_engine.url.render_as_string(hide_password=False))
    with caplog.at_level(logging.WARNING):
        with TestClient(app) as tc:
            assert tc.get("/api/apps").status_code == 200
    assert "prod 启动对账失败" in caplog.text


# ---- 终审 I-2：publishing 状态二次闸扩到 rollback / prod start / prod restart ----

async def test_publishing_blocks_rollback_and_prod_start(client, monkeypatch, no_preflight):
    """I-2：`_spawn` 只挡本进程——另一进程在发布（或遗留 publishing）时，rollback 与
    prod 启动类动作一律 409 立答，不打扰 Docker、不留后台任务；stop 是逃生通道不挡。"""
    aid = await _mk(client)
    called: list[str] = []

    async def fake_rollback(sf, app, version=None, notify=None):
        called.append("rollback")

    async def fake_start(sf, app, notify=None):
        called.append("start")

    async def fake_stop(sf, app, notify=None):
        called.append("stop")
        return {"status": "stopped"}

    monkeypatch.setattr(prod_mod, "rollback", fake_rollback)
    monkeypatch.setattr(prod_mod, "prod_start", fake_start)
    monkeypatch.setattr(prod_mod, "prod_stop", fake_stop)
    async with client.app.state.session_factory() as s:
        await s.execute(text("UPDATE app_project SET status_prod='publishing' WHERE id=:i"),
                        {"i": aid})
        await s.commit()

    r = await client.post(f"/api/apps/{aid}/rollback", json={"version": VERSION})
    assert r.status_code == 409 and "进行中" in r.json()["detail"]
    for action in ("start", "restart"):
        r = await client.post(f"/api/apps/{aid}/prod/{action}")
        assert r.status_code == 409, action
        assert "进行中" in r.json()["detail"], action
    assert aid not in client.app.state.dev_tasks
    assert not called

    r = await client.post(f"/api/apps/{aid}/prod/stop")      # 停栈不被 publishing 挡住
    assert r.status_code == 202, r.text
    await client.app.state.dev_tasks[aid]
    assert called == ["stop"]


async def test_rollback_and_prod_gates_pass_when_not_publishing(client, monkeypatch, no_preflight):
    """非 publishing 态三道闸全部放行（回归护栏：闸门不把正常路径一起锁死）。"""
    aid = await _mk(client)
    done: list[str] = []

    async def fake_rollback(sf, app, version=None, notify=None):
        done.append("rollback")

    async def fake_start(sf, app, notify=None):
        done.append("start")

    monkeypatch.setattr(prod_mod, "rollback", fake_rollback)
    monkeypatch.setattr(prod_mod, "prod_start", fake_start)
    r = await client.post(f"/api/apps/{aid}/rollback", json={"version": VERSION})
    assert r.status_code == 202, r.text
    await client.app.state.dev_tasks[aid]
    r = await client.post(f"/api/apps/{aid}/prod/start")
    assert r.status_code == 202, r.text
    await client.app.state.dev_tasks[aid]
    assert done == ["rollback", "start"]


# ---------- P3 回填脚手架端点 ----------

async def test_scaffold_backfill_writes_D_file(client):
    """有破坏性基线 → 同步写 sqlchange/D<today>_NN_backfill.sql，返回 {filename, content}；再点序号递增不覆盖。

    不钉死 01：模板自带 D<today>_01_seed.sql 种子，脚手架按当日已有最大序号+1避重——验证不clobber 种子、且递增。
    """
    import datetime as _dt
    import re
    aid = await _mk(client)
    root = APP_DIR
    (root / "schema").mkdir(parents=True, exist_ok=True)
    (root / "schema" / "schema.sql").write_text(
        "CREATE TABLE IF NOT EXISTS demo_notes (id BIGSERIAL PRIMARY KEY);\n", encoding="utf-8")
    prev = "b20260920000000"
    (root / "releases" / prev).mkdir(parents=True, exist_ok=True)
    (root / "releases" / prev / "schema.sql").write_text(
        "CREATE TABLE IF NOT EXISTS demo_notes (id BIGSERIAL PRIMARY KEY);\n"
        "ALTER TABLE demo_notes ADD COLUMN IF NOT EXISTS title VARCHAR(200);\n", encoding="utf-8")
    async with client.app.state.session_factory() as s:
        from sqlalchemy import text as _t
        await s.execute(_t("UPDATE app_project SET current_version=:v WHERE id=:i"),
                        {"v": prev, "i": aid})
        await s.commit()

    today = _dt.datetime.now().strftime("%Y%m%d")
    pat = re.compile(rf"^D{today}_(\d{{2}})_backfill\.sql$")
    r = await client.post(f"/api/apps/{aid}/sqlchange/scaffold-backfill")
    assert r.status_code == 200, r.text
    body = r.json()
    m1 = pat.match(body["filename"])
    assert m1, body["filename"]
    assert "title" in body["content"]
    on_disk = (root / "sqlchange" / body["filename"])
    assert on_disk.is_file() and on_disk.read_text(encoding="utf-8") == body["content"]
    # 不 clobber 模板种子（脚手架文件与 seed 共存于 sqlchange/）
    assert list((root / "sqlchange").glob("D*_seed.sql"))

    r2 = await client.post(f"/api/apps/{aid}/sqlchange/scaffold-backfill")
    m2 = pat.match(r2.json()["filename"])
    assert m2 and int(m2.group(1)) == int(m1.group(1)) + 1


async def test_scaffold_backfill_400_without_baseline(client):
    """无 current_version / 无基线 → 400，不落任何文件。"""
    aid = await _mk(client)
    r = await client.post(f"/api/apps/{aid}/sqlchange/scaffold-backfill")
    assert r.status_code == 400
    sc = APP_DIR / "sqlchange"
    assert not sc.exists() or not list(sc.glob("*_backfill.sql"))


async def test_scaffold_backfill_400_when_no_destructive(client):
    """有基线但非破坏（等价）→ 400「未检出破坏性变更」。"""
    aid = await _mk(client)
    root = APP_DIR
    (root / "schema").mkdir(parents=True, exist_ok=True)
    same = "CREATE TABLE IF NOT EXISTS t (id INT);\n"
    (root / "schema" / "schema.sql").write_text(same, encoding="utf-8")
    prev = "b20260920000000"
    (root / "releases" / prev).mkdir(parents=True, exist_ok=True)
    (root / "releases" / prev / "schema.sql").write_text(same, encoding="utf-8")
    async with client.app.state.session_factory() as s:
        from sqlalchemy import text as _t
        await s.execute(_t("UPDATE app_project SET current_version=:v WHERE id=:i"),
                        {"v": prev, "i": aid})
        await s.commit()
    r = await client.post(f"/api/apps/{aid}/sqlchange/scaffold-backfill")
    assert r.status_code == 400 and "破坏性" in r.json()["detail"]
