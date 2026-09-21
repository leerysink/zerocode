# tests/test_prod_engine.py
"""Task 6：prod 生命周期（启停/状态/联合体检/镜像存在性/启动对账）。全替身，无真 Docker 依赖。"""
import asyncio
import logging
from pathlib import Path

import pytest
from sqlalchemy import text as t

from app.db import make_session_factory
from app.deploy import prod, sqlrun
from app.deploy.errors import DeployError

pytestmark = pytest.mark.asyncio
needs_docker = pytest.mark.skipif(not sqlrun.docker_available(),
                                  reason="docker 不可用（Docker Desktop 未启动）")

APP = {"id": 1, "slug": "zcdemo", "stack": "vue3-fastapi",
       "ports": {"prod": 8210, "dev": 8211}, "prod_port": 8210, "status_prod": "stopped"}

CV = "b20260920120000"      # current_version：prod 启动以「已发布版本」为闸门（04 §4.2）


class Seams:
    """替身集中营：prod.py 对 engine/compose/provision 的调用一律模块级名字（D-1 纪律）。

    裁定（brief「实现注意」）：set_status 不做替身替换，而是**观察包装（spy）**——
    调用真实现（真写库）的同时记录调用序。事件与调用序完整，且「真写库断言」不恒真。
    docker 替身按 brief 原稿只匹配 ("compose","-f","x")，对真实渲染参数（prod-compose.yml
    全路径）永不命中、up_code 失效——改为 compose+up 判定，up_code 才有观测意义。
    Task 6 微修（I-2）扩展：down_code / preflight_exc 仍是同一替身营的开关参数，
    不新增打桩框架。
    """

    def __init__(self, monkeypatch, up_code=0, down_code=0, preflight_exc=None):
        self.calls, self.events = [], []
        self.up_code = up_code
        self.down_code = down_code

        async def docker(*a, **k):
            self.calls.append(("docker",) + a)
            if a[0] == "compose" and "up" in a:
                return self.up_code, "compose up boom"
            if a[0] == "compose" and "down" in a:
                return self.down_code, "compose down boom"
            return 0, "ok"

        async def preflight():
            self.calls.append(("preflight",))
            if preflight_exc is not None:
                raise preflight_exc

        async def app_root(sf, app):
            return Path("D:/__zc_prod_test__")

        async def health(root, port, timeout, stack="vue3-fastapi"):
            self.calls.append(("health", port, stack))
            return True, {"frontend": True, "api": True}

        real_set_status = prod.set_status

        async def spy_set_status(sf, aid, st):
            self.calls.append(("status", st))
            await real_set_status(sf, aid, st)

        monkeypatch.setattr(prod, "_docker", docker)
        monkeypatch.setattr(prod, "preflight_docker", preflight)
        monkeypatch.setattr(prod, "_app_root", app_root)
        monkeypatch.setattr(prod, "wait_prod_health", health)
        monkeypatch.setattr(prod, "set_status", spy_set_status)

    def notify(self, app_id, ev):
        self.events.append(ev)

    @property
    def statuses(self):
        # prod_status 事件流（WS 契约：T8 前端状态机消费的就是这条序列）
        return [e["status"] for e in self.events if e["type"] == "prod_status"]


def _app(app_id, slug="zcdemo", prod_port=8998, dev_port=8999, stack="vue3-fastapi"):
    """端口相对化（全局约束）：_compose_meta 优先读 ports 字典，两形态一起给。"""
    return {"id": app_id, "slug": slug, "stack": stack, "status_prod": "stopped",
            "ports": {"prod": prod_port, "dev": dev_port},
            "prod_port": prod_port, "dev_port": dev_port}


@pytest.fixture
async def sf(test_engine):
    return make_session_factory(test_engine)


@pytest.fixture
async def seed(sf):
    """真实种子行：workspace + app_project（prod 状态/版本可控），返回 (wid, aid)。

    I-3 清理纪律：插行登记进 fixture，teardown 必删——**断言失败也不泄漏**
    （替代用例尾内联 _drop；dev 引擎在 Task 11 审查中已就同一问题修成此形态）。
    """
    made = []

    async def _seed(slug="zcdemo", prod_port=8998, dev_port=8999, status_prod="stopped",
                    current_version=CV):
        async with sf() as s:
            wid = (await s.execute(t(
                "INSERT INTO workspace (name, root_path) VALUES (:n, :r) RETURNING id"),
                {"n": slug, "r": "D:/__zc_prod_test__"})).scalar()
            aid = (await s.execute(t(
                "INSERT INTO app_project (workspace_id, name, slug, scale, stack, prod_port,"
                " dev_port, status_prod, current_version) VALUES (:w, :n, :sl, 'small',"
                " 'vue3-fastapi', :pp, :dp, :st, :cv) RETURNING id"),
                {"w": wid, "n": slug, "sl": slug, "pp": prod_port, "dp": dev_port,
                 "st": status_prod, "cv": current_version})).scalar()
            await s.commit()
        made.append((wid, aid))
        return wid, aid

    yield _seed
    async with sf() as s:
        for wid, aid in made:
            await s.execute(t("DELETE FROM app_project WHERE id=:i"), {"i": aid})
            await s.execute(t("DELETE FROM workspace WHERE id=:i"), {"i": wid})
        await s.commit()


async def _status_prod(sf, aid):
    """真库回读（set_status 未被替换的落库证据）。"""
    async with sf() as s:
        return (await s.execute(t(
            "SELECT status_prod FROM app_project WHERE id=:i"), {"i": aid})).scalar()


def _cred_and_ensure(monkeypatch, seams):
    """发布件前置：凭据齐（dev/prod 都真读）+ 渲染器替身（渲染本体在 test_compose_render 覆盖）。"""
    async def cred(sf, slug, env):
        return {"db": f"{slug}_{env}", "user": f"{slug}_{env}", "password": "x"}

    def ensure(root, meta, creds, version=None):
        seams.calls.append(("ensure", version, creds))

    monkeypatch.setattr(prod, "read_cred", cred)
    monkeypatch.setattr(prod, "ensure_deploy_files", ensure)


async def test_start_stop_events_and_sequence(monkeypatch, sf, seed):
    wid, aid = await seed()
    app = _app(aid)
    seams = Seams(monkeypatch)
    _cred_and_ensure(monkeypatch, seams)

    res = await prod.prod_start(sf, app, notify=seams.notify)
    assert res["status"] == "running"
    # 状态机全序：starting → running（set_status 调用序，spy 记录 + 真写库）
    seq = [c for c in seams.calls if c[0] == "status"]
    assert seq == [("status", "starting"), ("status", "running")]
    # prod_status 事件与落库同序；步骤行走 dev_log 通道（前端日志面板同源）：首 ▶、尾 ✓
    assert seams.statuses == ["starting", "running"]
    assert seams.events[0]["type"] == "dev_log" and "启动正式环境" in seams.events[0]["line"]
    assert seams.events[-1]["type"] == "dev_log" and "已就绪" in seams.events[-1]["line"]
    # 调用序：预检最前（预检失败=状态零变更的前提）、渲染先于起栈、起栈先于体检
    kinds = [c[0] for c in seams.calls]
    assert kinds.index("preflight") < kinds.index("ensure") < kinds.index("docker") < kinds.index("health")
    up = next(c for c in seams.calls if c[0] == "docker" and "up" in c)
    assert up[1:4] == ("compose", "-f", str(Path("D:/__zc_prod_test__") / "deploy" / "prod-compose.yml"))
    assert ("health", 8998, "vue3-fastapi") in seams.calls      # 体检端口 = prod 端口（相对化）
    ens = next(c for c in seams.calls if c[0] == "ensure")
    assert ens[1] == CV                                         # version=current_version
    assert ens[2]["prod"]["db"] == "zcdemo_prod"
    assert ens[2]["dev"]["db"] == "zcdemo_dev"                  # D-T4：dev 侧传真凭据，不是空 dict
    assert await _status_prod(sf, aid) == "running"             # 真写库（set_status 未被替换）

    await prod.prod_stop(sf, app, notify=seams.notify)
    down = next(c for c in seams.calls if c[0] == "docker" and "down" in c)
    assert down[1:4] == ("compose", "-f", str(Path("D:/__zc_prod_test__") / "deploy" / "prod-compose.yml"))
    assert seams.statuses[-1] == "stopped"
    assert await _status_prod(sf, aid) == "stopped"


async def test_start_requires_prod_cred(monkeypatch, sf):
    async def no_cred(sf, slug, env):
        return None

    async def root(sf, app):
        return Path("D:/none")

    monkeypatch.setattr(prod, "preflight_docker", lambda: asyncio.sleep(0))
    monkeypatch.setattr(prod, "_app_root", root)
    monkeypatch.setattr(prod, "read_cred", no_cred)
    with pytest.raises(DeployError) as ei:
        await prod.prod_start(sf, {**APP, "id": 999}, notify=None)
    assert ei.value.status == 409 and "凭据" in str(ei.value)


async def test_start_preflight_failure_zero_change_zero_event(monkeypatch, sf, seed):
    """I-2①（对位 dev 的 test_start_dev_docker_unavailable；03 §5 prod 同构、Task 11/12 立约点）：
    预检失败先于任何状态变更与事件——status_prod 零变更、零事件（连 dev_log ▶ 都不发）、503 原样上抛。"""
    wid, aid = await seed(status_prod="stopped")
    app = _app(aid)
    seams = Seams(monkeypatch, preflight_exc=DeployError("Docker 引擎不可用", status=503))
    _cred_and_ensure(monkeypatch, seams)
    with pytest.raises(DeployError) as ei:
        await prod.prod_start(sf, app, notify=seams.notify)
    assert ei.value.status == 503
    assert seams.calls == [("preflight",)]                  # 预检之外什么都没发生
    assert seams.events == []                               # 零事件（dev_log ▶ 亦不得发）
    assert await _status_prod(sf, aid) == "stopped"         # 真库零变更


async def test_start_requires_current_version(monkeypatch, sf, seed):
    """I-2②（04 §4.2 版本闸门）：cred 齐但未发布过版本 → 409 引导文案含「发布」，
    starting 之后按通用失败路径收口 error + 带 message 事件，不留悬空。"""
    wid, aid = await seed(current_version=None)
    app = _app(aid)
    seams = Seams(monkeypatch)
    _cred_and_ensure(monkeypatch, seams)
    with pytest.raises(DeployError) as ei:
        await prod.prod_start(sf, app, notify=seams.notify)
    assert ei.value.status == 409 and "发布" in str(ei.value)
    assert [c[1] for c in seams.calls if c[0] == "status"] == ["starting", "error"]
    assert seams.statuses == ["starting", "error"]
    err = [e for e in seams.events if e["type"] == "prod_status" and e["status"] == "error"]
    assert err and "发布" in err[0]["message"]              # error 态事件必带 message（T9/T10 契约）
    assert await _status_prod(sf, aid) == "error"


async def test_stop_down_nonzero_still_stopped(monkeypatch, sf, seed, caplog):
    """I-2③（与 dev stop_dev 逐行同口径）：compose down 非零退出（栈半死/停不干净）
    仍记 warning 后收口 stopped、不抛——停语义必须幂等，否则前端永远卡 running。"""
    wid, aid = await seed(status_prod="running")
    app = _app(aid)
    seams = Seams(monkeypatch, down_code=1)
    with caplog.at_level(logging.WARNING):
        res = await prod.prod_stop(sf, app, notify=seams.notify)
    assert res == {"status": "stopped"}
    assert seams.statuses == ["stopped"]
    assert seams.events[-1]["type"] == "dev_log" and "已停止" in seams.events[-1]["line"]
    assert any("非零" in r.getMessage() for r in caplog.records)   # 失败可见但不改收口
    assert await _status_prod(sf, aid) == "stopped"


async def test_start_up_failure_sets_error(monkeypatch, sf, seed):
    """compose up 非零 → 400 上抛且状态收口 error（starting 之后的失败必须 error+事件，不留悬空）。"""
    wid, aid = await seed()
    app = _app(aid)
    seams = Seams(monkeypatch, up_code=1)
    _cred_and_ensure(monkeypatch, seams)
    with pytest.raises(DeployError) as ei:
        await prod.prod_start(sf, app, notify=seams.notify)
    assert "启动失败" in str(ei.value)
    assert [c[1] for c in seams.calls if c[0] == "status"] == ["starting", "error"]
    assert seams.statuses == ["starting", "error"]
    assert seams.events[-1]["type"] == "dev_log" and "启动失败" in seams.events[-1]["line"]
    assert await _status_prod(sf, aid) == "error"


async def test_reconcile_marks_publishing_and_matches_docker(monkeypatch, sf, seed):
    wid, aid = await seed(slug="zcrec2", prod_port=8996, dev_port=8997,
                          status_prod="publishing", current_version=None)
    calls = []

    async def docker(*a, **k):
        calls.append(a)
        return 0, ""                 # ps -q 输出空 = 无容器

    monkeypatch.setattr(prod, "_docker", docker)

    async def no_int(sf_):
        calls.append(("mark_interrupted",))
        return 0

    monkeypatch.setattr(prod.records, "mark_interrupted", no_int)
    await prod.reconcile(sf, notify=None)
    assert calls[0] == ("mark_interrupted",)                     # 中断发布标记先于容器核对
    assert ("compose", "-p", "zc-zcrec2-prod", "ps", "-q") in calls  # 项目名 = zc-<slug>-prod
    assert await _status_prod(sf, aid) == "stopped"    # publishing 且无容器 → stopped（发布要重发）


async def test_reconcile_keeps_running_when_containers_up(monkeypatch, sf, seed):
    """对账正向：running 且容器在 → 维持 running（04 §6：running 以 docker 实际容器态核对）。"""
    wid, aid = await seed(slug="zcrec3", prod_port=8994, dev_port=8995, status_prod="running")

    async def docker(*a, **k):
        return 0, "3f2a1b9c\n"       # ps -q 有输出 = 容器在跑

    monkeypatch.setattr(prod, "_docker", docker)

    async def no_int(sf_):
        return 0

    monkeypatch.setattr(prod.records, "mark_interrupted", no_int)
    await prod.reconcile(sf, notify=None)
    assert await _status_prod(sf, aid) == "running"


async def test_reconcile_docker_unreachable_resets_stopped(monkeypatch, sf, seed):
    """I-1 回归钉 + I-2④：daemon 失联 = **exit 1 且错误文本非空**（engine.docker 以
    stderr=STDOUT 合流进 out，审查探针 DOCKER_HOST=tcp://127.0.0.1:1 实证）——
    只看输出会把五态残留误升 running；对账以退出码为准，非零视为无容器复位 stopped，
    与异常分支同向（失联时宁可停、不可谎报在跑）。"""
    wid, aid = await seed(slug="zcrec4", prod_port=8992, dev_port=8993, status_prod="running")
    seen = []

    async def docker(*a, **k):
        seen.append(a)
        return 1, "Cannot connect to the Docker daemon at tcp://127.0.0.1:1. Is the docker daemon running?"

    monkeypatch.setattr(prod, "_docker", docker)

    async def no_int(sf_):
        return 0

    monkeypatch.setattr(prod.records, "mark_interrupted", no_int)
    await prod.reconcile(sf, notify=None)
    assert ("compose", "-p", "zc-zcrec4-prod", "ps", "-q") in seen
    assert await _status_prod(sf, aid) == "stopped"              # 而非误升 running


async def test_image_exists(monkeypatch):
    """镜像存在性（回滚/发布前置）：docker image inspect 退出码 0=在、非 0=不在。"""
    calls = []
    codes = iter([0, 1])

    async def docker(*a, **k):
        calls.append(a)
        return next(codes), ""

    monkeypatch.setattr(prod, "_docker", docker)
    assert await prod.image_exists("zc-demo-frontend:prod-b20260920120000") is True
    assert await prod.image_exists("zc-demo-frontend:prod-b20200101000000") is False
    assert calls[0] == ("image", "inspect", "zc-demo-frontend:prod-b20260920120000")


@needs_docker
async def test_image_exists_real_docker_negative():
    """真机最小冒烟（可选补，控制者允许范围内）：不建 tag、零清理负担——
    未发布的镜像名在真 Docker 上必判 False，顺带证明 _docker 薄包装到 engine.docker 的直连可用。"""
    assert await prod.image_exists("zc-no-such-app-frontend:prod-never-built") is False


async def test_wait_prod_health_static_api_always_true():
    """static 栈 api 恒 True（无后端可查）；死端口前端不通 → 整体 False，但 checks 形态完整。"""
    ok, checks = await prod.wait_prod_health(Path("D:/__none__"), 59997, 1, "static")
    assert ok is False
    assert checks == {"frontend": False, "api": True}


async def test_prod_status_reads_db_and_compose_text(monkeypatch, sf, seed):
    wid, aid = await seed(status_prod="running")

    async def app_root(sf, app):
        return Path("D:/__zc_prod_test__")

    seen = []

    async def docker(*a, **k):
        seen.append(a)
        return 0, '{"State":"running"}'

    monkeypatch.setattr(prod, "_app_root", app_root)
    monkeypatch.setattr(prod, "_docker", docker)
    res = await prod.prod_status(sf, _app(aid))
    assert res["status_prod"] == "running"                       # 库里态，不是猜的
    assert res["compose_ps"] == '{"State":"running"}'            # 文本仅经端点回（不进事件）
    assert res["localhost"] == "http://localhost:8998"
    assert seen and seen[0][0] == "compose" and "ps" in seen[0]


async def test_prod_restart_stops_then_starts(monkeypatch, sf, seed):
    wid, aid = await seed(status_prod="running")
    app = _app(aid)
    seams = Seams(monkeypatch)
    _cred_and_ensure(monkeypatch, seams)
    res = await prod.prod_restart(sf, app, notify=seams.notify)
    assert res["status"] == "running"
    verbs = [next(x for x in c[1:] if x in ("up", "down"))
             for c in seams.calls if c[0] == "docker"]
    assert verbs == ["down", "up"]                               # 先停后起
    assert [c[1] for c in seams.calls if c[0] == "status"] == ["stopped", "starting", "running"]
    assert seams.statuses == ["stopped", "starting", "running"]
