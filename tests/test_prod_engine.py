# tests/test_prod_engine.py
"""Task 6：prod 生命周期（启停/状态/联合体检/镜像存在性/启动对账）+
Task 7：发布流水线（publish 七步状态机 / 体检失败自动回退 / rollback / keep-5 清理 / 迁移命名扫描）。
全替身，无真 Docker 依赖。"""
import asyncio
import json
import logging
import re
from pathlib import Path

import pytest
from sqlalchemy import text as t

from app.db import make_session_factory
from app.deploy import compose, nics, prod, records, sqlrun
from app.deploy.errors import DeployError
from app.hub import ChatHub

# asyncio_mode=auto（pytest.ini）下模块级 pytest.mark.asyncio 是冗余标记，且会让 brief 的
# 两个同步用例（scan_sqlchange / hub 快照）吃到 PytestWarning——输出必须干净，故不声明。
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
    _cred_and_ensure(monkeypatch, seams)   # 停栈现在会先补位（read_cred+ensure），别让它们真读库/真写盘
    with caplog.at_level(logging.WARNING):
        res = await prod.prod_stop(sf, app, notify=seams.notify)
    assert res == {"status": "stopped"}
    assert seams.statuses == ["stopped"]
    assert seams.events[-1]["type"] == "dev_log" and "已停止" in seams.events[-1]["line"]
    assert any("非零" in r.getMessage() for r in caplog.records)   # 失败可见但不改收口
    assert await _status_prod(sf, aid) == "stopped"


async def test_stop_ensures_deploy_files_before_down(monkeypatch, sf, seed):
    """C1 评审返修（与 stop_dev 的 ensure-before-down 对称）：大型栈的 prod-compose 密钥位带
    `${VAR:?}`，是 compose 的**解析期**闸门——.env 被手工清空时连 `down` 都会 exit=1，于是
    「非零只记 warning」的收口会把一台还在跑、仍占端口的栈误报 stopped。停之前先补一次幂等
    ensure（缺位密钥现场生成、PROD_VERSION 保留），正常情形零成本、补不上位也不阻断停止。"""
    wid, aid = await seed(status_prod="running")
    app = _app(aid)
    seams = Seams(monkeypatch)
    _cred_and_ensure(monkeypatch, seams)
    await prod.prod_stop(sf, app, notify=seams.notify)
    down_at = next(i for i, c in enumerate(seams.calls) if c[0] == "docker" and "down" in c)
    ens_at = next(i for i, c in enumerate(seams.calls) if c[0] == "ensure")
    assert ens_at < down_at                                     # 补位先于 down
    ens = seams.calls[ens_at]
    assert ens[1] is None                                       # 停栈不推进版本（PROD_VERSION 保留）
    assert ens[2]["prod"]["db"] == "zcdemo_prod"                # 凭据真读真传（单向推进不冲空）


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
    assert res["domain"] == f"https://zcdemo.{nics.GATEWAY_DOMAIN}"   # prod 地址统一为网关域名
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


# ===================== Task 7：发布流水线（publish / rollback / keep-5 清理）=====================

PREV_VER = "b20260920000000"       # 「已有 v1 在线」场景里的旧版本
_PW = "pw-never-log"               # 口令哨兵：任何事件/日志出现它即违反全局约束
PUB_STEPS = ("preflight", "build", "provision", "migrate", "deploy", "health", "record")


def _pub_app(aid, slug="zcpub", prod_port=8982, dev_port=8983):
    """publish/rollback 入参形态（app_service.get_app 的扁平列 + ports 双形态一起给）。"""
    return {"id": aid, "slug": slug, "stack": "vue3-fastapi", "scale": "small",
            "ports": {"prod": prod_port, "dev": dev_port},
            "prod_port": prod_port, "dev_port": dev_port}


def _env_value(root: Path, key: str) -> str:
    """读 deploy/.env 的键值。

    PROD_VERSION 是「此刻起的是哪一版」唯一的真观测点：compose 的镜像 tag 由
    `prod-${PROD_VERSION}` 插值而来，起栈命令行里看不到版本——只替渲染器就永远钉不住
    「自动回退把 PROD_VERSION 换回旧版」这条契约（04 §4.3）。
    """
    ef = root / "deploy" / ".env"
    if not ef.is_file():
        return ""
    for line in ef.read_text(encoding="utf-8").splitlines():
        if line.startswith(key + "="):
            return line.split("=", 1)[1]
    return ""


@pytest.fixture
def pub_root(tmp_path):
    """真实文件面的发布试验根：publish 的 preflight 要看 app.json/sqlchange、要补 nginx.conf。

    frontend/ 故意**不带** nginx.conf——顺带钉住存量补齐分支与 app_service.TEMPLATES 单一事实源。
    """
    (tmp_path / "sqlchange").mkdir()
    (tmp_path / "frontend").mkdir()
    (tmp_path / "app.json").write_text(
        json.dumps({"schema": "zc-app/1", "slug": "zcpub", "stack": "vue3-fastapi"}), encoding="utf-8")
    (tmp_path / "README.md").write_text("# zcpub\n", encoding="utf-8")
    return tmp_path


class PubSeams(Seams):
    """发布替身营：在 T6 Seams 之上补齐 T7 的八个新接缝（D-1 纪律：一律打 prod.<模块级名字>）。

    真库不替——records 记账、set_status（继承 spy）、app_project 版本列 UPDATE、nics URL 读取
    全走真实现，事件序与落库互证；替身只覆盖外部世界（docker / 供给 / 迁移 / 体检 / 镜像）。

    **渲染器不替身，改为 spy + 真跑 compose.ensure_deploy_files**（残留脚手架的修正点）：
    prod-compose.yml 落盘条件的放宽（T4 波裁定）与「build/up 前文件必已在盘上」只有让真渲染器
    落真文件才是可观测的——纯 stub 会让这两条契约在替身营里恒真。副作用：.env 的 PROD_VERSION
    真落盘，docker 替身顺手回读进调用记录（`PROD_VERSION=<v>` 尾项），起栈版本成为硬断言对象。

    与 T6 同款裁定：`set_status` 用观察包装而非 stub，「真写库断言」不恒真。
    """

    def __init__(self, monkeypatch, root, *, build_code=0, up_codes=(0,), health_results=(True,),
                 mig_ok=True, dev_cred=True, prod_cred=True, images=True, rm_code=0, parity_ok=True):
        super().__init__(monkeypatch)
        self.root = Path(root)
        self.up_codes, self.health_results = tuple(up_codes), tuple(health_results)
        self.build_code, self.mig_ok, self.images = build_code, mig_ok, images
        self.rm_code = rm_code
        self.parity_ok = parity_ok
        self.n_up = self.n_health = 0
        calls = self.calls

        async def docker(*a, cwd=None, on_line=None, timeout=600):
            extra = ()
            if "build" in a or "up" in a:
                # 构建/起栈前 prod-compose.yml 必须已在盘上（真渲染器落的那份）；顺带回读
                # .env 的 PROD_VERSION 作尾项——「起的是哪一版」唯一可断言的观测点。
                assert (self.root / "deploy" / "prod-compose.yml").is_file(), \
                    f"compose {a[-1]} 前 prod-compose.yml 未落盘"
                extra = (f"PROD_VERSION={_env_value(self.root, 'PROD_VERSION')}",)
            calls.append(("docker",) + tuple(a) + extra)
            if "build" in a:
                if on_line:
                    await on_line("#8 [frontend] COPY nginx.conf /etc/nginx/conf.d/default.conf")
                return self.build_code, "compose build 输出尾部"
            if "up" in a:
                code = self.up_codes[min(self.n_up, len(self.up_codes) - 1)]
                self.n_up += 1
                return code, "compose up 输出尾部"
            if "rm" in a:
                return self.rm_code, "docker rmi 输出尾部"
            return 0, "docker ok"

        async def cred(sf_, slug, env):
            if env == "dev" and not dev_cred:
                return None
            if env == "prod" and not prod_cred:
                return None
            name = f"{slug}_{env}"
            return {"db": name, "user": name, "password": _PW}

        async def prov(sf_, slug, env):
            calls.append(("provision", env))
            name = f"{slug}_{env}"
            return {"db": name, "user": name, "password": _PW}

        async def mig(root_, env, cr, on_log=None):
            calls.append(("migrate", env, str(root_)))
            if on_log:
                await on_log("OK V20260920_01_init.sql")
            return ({"ok": True, "code": 0, "log": "ALL DONE\nOK V20260920_01_init.sql"}
                    if self.mig_ok else
                    {"ok": False, "code": 2, "log": "FAIL V20260920_02_bad.sql: syntax error"})

        async def health(root_, port, timeout, stack=""):
            ok = self.health_results[min(self.n_health, len(self.health_results) - 1)]
            self.n_health += 1
            calls.append(("health", port, stack, timeout))
            return ok, {"frontend": ok, "api": ok}

        def ensure(root_, meta, creds, version=None):
            calls.append(("ensure", version, {k: dict(v or {}) for k, v in creds.items()}))
            compose.ensure_deploy_files(Path(root_), meta, creds, version=version)   # 真渲染

        async def image_exists(tag):
            calls.append(("image_exists", tag))
            return self.images

        async def app_root(sf_, app_):
            return self.root

        async def verify(sf_, root_, slug_):
            # P1 结构等价校验：默认放行（ok=True）；parity_ok=False 时模拟 dev 漂移阻断
            calls.append(("verify", slug_))
            if self.parity_ok:
                return {"ok": True, "diff": {}}
            return {"ok": False, "diff": {"objects_only_in_dev": ["stray"],
                                          "objects_only_in_files": []}}

        # preflight_docker 与 set_status 由基座 Seams 负责（基座已记录 ("preflight",) 与 ("status", st)）
        for name, fn in (("_docker", docker),
                         ("read_cred", cred), ("provision_env", prov), ("run_sqlchange", mig),
                         ("wait_prod_health", health), ("ensure_deploy_files", ensure),
                         ("image_exists", image_exists), ("_app_root", app_root),
                         ("verify_dev_matches_files", verify)):
            monkeypatch.setattr(prod, name, fn)

    @property
    def verbs(self) -> list:
        """调用流的可读指纹（顺序断言用）：docker 收成 `compose build`/`compose up`/`rm`，其余取首项。"""
        out = []
        for c in self.calls:
            if c[0] != "docker":
                out.append(c[0])
                continue
            verb = next((x for x in c[1:] if x in ("build", "up", "down", "rm", "ps")), c[1])
            out.append(f"{c[1]} {verb}" if c[1] == "compose" else verb)
        return out

    @property
    def steps_ok(self) -> list:
        """publish_step 的 ok 序列（04 §4.1 状态机进度——前端进度条的数据源）。"""
        return [e["step"] for e in self.events if e["type"] == "publish_step" and e["state"] == "ok"]

    @property
    def step_seq(self) -> list:
        """publish_step 全序（含 running）：③裁定失败不发 failed 态，故成对出现即为健康。"""
        return [(e["step"], e["state"]) for e in self.events if e["type"] == "publish_step"]

    def ups(self) -> list:
        """全部 compose up 调用（自动回退要看第二次 up 带的是哪个版本、哪条 compose 文件）。"""
        return [c for c in self.calls if c[0] == "docker" and "up" in c[1:]]

    def ensures(self) -> list:
        return [c for c in self.calls if c[0] == "ensure"]

    def rms(self) -> list:
        """docker image rm 的 tag 清单（keep-5 清理的实删面）。"""
        return [c[3] for c in self.calls if c[0] == "docker" and "rm" in c[1:]]

    @property
    def dones(self) -> list:
        """publish_done 事件序列（04 §5：无论成败，最后一次必须是它——不留 202 黑洞）。"""
        return [e for e in self.events if e["type"] == "publish_done"]

    @property
    def logs(self) -> list:
        return [e["line"] for e in self.events if e["type"] == "dev_log"]

    @property
    def bare_events(self) -> list:
        """全事件 JSON 串（口令零外泄断言用：任何事件的任何字段都不得带上 _PW）。"""
        return [json.dumps(e, ensure_ascii=False) for e in self.events]


async def _db_pair(sf, aid):
    """真库回读 (current_version, status_prod)：版本落库与状态机收口的独立证据。"""
    async with sf() as s:
        return (await s.execute(t(
            "SELECT current_version, status_prod FROM app_project WHERE id=:i"), {"i": aid})).first()


async def _record(sf, aid, kind, version):
    """真库回读某条记账（按 kind+version 定位，避开 started_at 同毫秒的排序不定）。"""
    async with sf() as s:
        row = (await s.execute(t(
            "SELECT id, status, step, error, detail FROM deploy_record "
            "WHERE app_id=:i AND kind=:k AND version=:v ORDER BY id DESC LIMIT 1"),
            {"i": aid, "k": kind, "v": version})).first()
    return row


async def _record_count(sf, aid):
    async with sf() as s:
        return (await s.execute(t("SELECT count(*) FROM deploy_record WHERE app_id=:i"),
                                {"i": aid})).scalar()


async def _seed_publish_history(sf, aid, versions, step="record"):
    """造「已发布过 N 版」的记账历史（keep-5 清理与 prev_version 的数据源，走真 records）。"""
    for v in versions:
        rid = await records.begin(sf, aid, "publish", v)
        await records.finish(sf, rid, "success", step=step)


async def test_publish_success_sequence(monkeypatch, sf, seed, pub_root):
    """七步全序 + 事件/落库互证（04 §4.1/§5）：进度、日志、版本推进、README、存量补件、keep-5。"""
    olds = [f"b2026091900000{i}" for i in range(1, 6)]        # 已发 5 版 → 第 6 版触发 keep-5 清理
    wid, aid = await seed(slug="zcpub", prod_port=8982, dev_port=8983,
                          status_prod="running", current_version=olds[-1])
    await _seed_publish_history(sf, aid, olds)
    seams = PubSeams(monkeypatch, pub_root)

    res = await prod.publish(sf, _pub_app(aid), notify=seams.notify, force=True)

    assert res["status"] == "running" and res["url"] == f"https://zcpub.{nics.GATEWAY_DOMAIN}"
    assert re.fullmatch(r"b\d{14}", res["version"])            # b<本地 YYYYMMDDHHmmss>
    # ① 步骤事件：每步 running→ok 成对全序（③裁定：失败不发 state=failed，统一走 publish_done）
    assert seams.step_seq == [p for s in PUB_STEPS for p in ((s, "running"), (s, "ok"))]
    assert seams.steps_ok == list(PUB_STEPS)
    # ② 收口事件：全流最后一条必是 publish_done(ok)——不留 202 黑洞；prod_status 同步收口
    assert seams.events[-1]["type"] == "publish_done" and seams.events[-1]["ok"] is True
    assert seams.dones == [seams.events[-1]]
    assert seams.dones[-1]["version"] == res["version"] and seams.dones[-1]["error"] == ""
    assert seams.statuses == ["publishing", "running"]
    # ③ 构建/迁移尾随行经 build_log 上行（前端日志面板与 WS 同源）
    blog = [e["line"] for e in seams.events if e["type"] == "build_log"]
    assert any("nginx.conf" in ln for ln in blog) and "OK V20260920_01_init.sql" in blog
    # ④ 口令零外泄（全局约束：任何 WS 事件都不得带凭据）
    assert all(_PW not in s for s in seams.bare_events)
    # ⑤ 调用序：预检最前 → build（旧容器不动）→ provision → migrate → up → health
    v = seams.verbs
    assert (v.index("preflight") < v.index("compose build") < v.index("provision")
            < v.index("migrate") < v.index("compose up") < v.index("health"))
    up = seams.ups()[0]
    assert up[1:4] == ("compose", "-f", str(pub_root / "deploy" / "prod-compose.yml"))
    assert up[-1] == f"PROD_VERSION={res['version']}"          # 起栈读的 .env 就是新版本
    hc = next(c for c in seams.calls if c[0] == "health")
    assert hc[1] == 8982 and hc[2] == "vue3-fastapi"           # 体检端口=prod 端口（相对化）
    # ⑥ 渲染器两次收到发布版本号，dev 侧凭据真读（D-T4：空 dict 直传会谎称「没带」）
    ens = seams.ensures()
    assert [c[1] for c in ens] == [res["version"], res["version"]]
    assert ens[0][2]["dev"]["db"] == "zcpub_dev" and ens[0][2]["prod"]["db"] == "zcpub_prod"
    assert _env_value(pub_root, "PROD_VERSION") == res["version"]
    # ⑦ 真库：版本推进 + 状态收口 + 记账（recordId 与行 id 互证，detail 落全）
    assert await _db_pair(sf, aid) == (res["version"], "running")
    row = await _record(sf, aid, "publish", res["version"])
    assert row and row[0] == res["recordId"] and row[1] == "success" and row[2] == "record"
    assert row[4]["sqlApplied"] == ["V20260920_01_init.sql"]
    assert row[4]["images"] == [f"zc-zcpub-frontend:prod-{res['version']}",
                                f"zc-zcpub-backend:prod-{res['version']}"]
    assert row[4]["durationMs"] >= 0 and row[4]["url"] == f"https://zcpub.{nics.GATEWAY_DOMAIN}"
    assert row[4]["health"] == {"frontend": True, "api": True}   # M-b：04 §4.5 detail 形态含 health
    # ⑧ keep-5：只挤掉最老一版（前后端各一个 tag），近 5 版镜像不碰
    assert sorted(seams.rms()) == sorted([f"zc-zcpub-frontend:prod-{olds[0]}",
                                          f"zc-zcpub-backend:prod-{olds[0]}"])
    # ⑨ 存量应用补齐生产反代配置（单一事实源 app_service.TEMPLATES，不造 parents 链）
    from app.services import app_service
    conf = pub_root / "frontend" / "nginx.conf"
    assert conf.is_file()
    assert conf.read_text(encoding="utf-8") == (
        app_service.TEMPLATES / "vue3-fastapi" / "frontend" / "nginx.conf"
        ).read_text(encoding="utf-8")
    assert any("补齐" in ln and "nginx.conf" in ln for ln in seams.logs)
    # ⑩ README 发布块刷新（T5 接缝的实际接线点）
    readme = (pub_root / "README.md").read_text(encoding="utf-8")
    assert res["url"] in readme and res["version"] in readme


async def test_publish_first_time_without_prod_cred_renders_prod_compose(monkeypatch, sf, seed, pub_root):
    """T4 波欠账落点：首次发布（prod 凭据尚未供给）build 前也必须已有 prod-compose.yml。

    渲染内容只依赖 meta，凭据走 .env 的 ${} 插值，故落盘条件放宽为 stack=vue3-fastapi 即渲染；
    替身营里渲染器真跑，缺文件时 docker 替身直接断言炸（旧门会在 compose build 前抛 AssertionError）。
    """
    wid, aid = await seed(slug="zcpub", prod_port=8982, dev_port=8983, current_version=None)
    seams = PubSeams(monkeypatch, pub_root, prod_cred=False)

    res = await prod.publish(sf, _pub_app(aid), notify=seams.notify, force=True)

    assert res["status"] == "running"
    ens = seams.ensures()
    assert ens[0][2]["prod"] == {}                      # build 时 prod 凭据确实还没供给（数据现状）
    assert ens[1][2]["prod"]["db"] == "zcpub_prod"      # provision 步补齐后重渲染
    assert (pub_root / "deploy" / "prod-compose.yml").is_file()
    env = (pub_root / "deploy" / ".env").read_text(encoding="utf-8")
    assert "PROD_DB=zcpub_prod" in env                  # .env 补真值，compose 插值才拿得到
    assert seams.steps_ok == list(PUB_STEPS)


async def test_publish_keeps_existing_nginx_conf(monkeypatch, sf, seed, pub_root):
    """存量补齐只在缺文件时补：已有 frontend/nginx.conf（应用自带反代规则）不得被平台覆盖。"""
    wid, aid = await seed(slug="zcpub", prod_port=8982, dev_port=8983)
    mine = "server { listen 80; # 应用自己的反代规则\n}\n"
    (pub_root / "frontend" / "nginx.conf").write_text(mine, encoding="utf-8")
    seams = PubSeams(monkeypatch, pub_root)

    await prod.publish(sf, _pub_app(aid), notify=seams.notify, force=True)

    assert (pub_root / "frontend" / "nginx.conf").read_text(encoding="utf-8") == mine
    assert not any("补齐" in ln for ln in seams.logs)


async def test_publish_blocks_on_bad_sqlchange_name(monkeypatch, sf, seed, pub_root):
    """命名规范闸门在 preflight（全局约束/07）：坏名一条都不起栈，且失败必收口 publish_done。"""
    wid, aid = await seed(slug="zcpub", prod_port=8982, dev_port=8983,
                          status_prod="running", current_version=PREV_VER)
    (pub_root / "sqlchange" / "notes.sql").write_text("SELECT 1", encoding="utf-8")
    seams = PubSeams(monkeypatch, pub_root)

    with pytest.raises(DeployError) as ei:
        await prod.publish(sf, _pub_app(aid), notify=seams.notify, force=True)
    assert "sqlchange 文件名不规范" in str(ei.value) and "notes.sql" in str(ei.value)
    assert seams.step_seq == [("preflight", "running")]          # 没走到 ok，更没进 build
    assert "compose build" not in seams.verbs and not seams.ups()
    assert seams.dones[-1]["ok"] is False and "notes.sql" in seams.dones[-1]["error"]
    assert seams.statuses == ["publishing", "error"]
    err = [e for e in seams.events if e["type"] == "prod_status" and e["status"] == "error"]
    assert err and "sqlchange" in err[0]["message"]              # error 态事件带 message（T9/T10）
    assert await _db_pair(sf, aid) == (PREV_VER, "error")        # 旧版本记录不动，仅态转 error
    async with sf() as s:                                        # 记账有且只有一条：failed/preflight
        kind, st, step = (await s.execute(t(
            "SELECT kind, status, step FROM deploy_record WHERE app_id=:i"), {"i": aid})).first()
    assert (kind, st, step) == ("publish", "failed", "preflight")


async def test_publish_blocks_on_dev_drift(monkeypatch, sf, seed, pub_root):
    """P1 结构等价闸门（并入 preflight）：dev 与文件漂移 → 不起栈、不收口 ok、阻断发布。"""
    wid, aid = await seed(slug="zcpub", prod_port=8982, dev_port=8983,
                          status_prod="running", current_version=PREV_VER)
    seams = PubSeams(monkeypatch, pub_root, parity_ok=False)

    with pytest.raises(DeployError) as ei:
        await prod.publish(sf, _pub_app(aid), notify=seams.notify, force=True)
    assert "不一致" in str(ei.value) and "stray" in str(ei.value)
    assert seams.step_seq == [("preflight", "running")]          # 校验在 preflight 内、ok 之前
    assert "compose build" not in seams.verbs and not seams.ups()
    assert seams.dones[-1]["ok"] is False and "漂移" in seams.dones[-1]["error"]
    assert seams.statuses == ["publishing", "error"]


async def test_publish_can_skip_schema_parity_check(monkeypatch, sf, seed, pub_root):
    """兑底开关：置 deploy.skip_schema_parity=true 时即使漂移也放行（不写库，monkeypatch 配置）。"""
    async def _cfg(_s):
        return {"deploy.skip_schema_parity": "true"}
    monkeypatch.setattr(prod, "deploy_cfg", _cfg)
    wid, aid = await seed(slug="zcpub", prod_port=8982, dev_port=8983)
    seams = PubSeams(monkeypatch, pub_root, parity_ok=False)      # 即便模拟漂移…

    res = await prod.publish(sf, _pub_app(aid), notify=seams.notify, force=True)   # …仍应发布成功
    assert res["status"] == "running" and seams.steps_ok == list(PUB_STEPS)
    assert not any(c[0] == "verify" for c in seams.calls)          # 开关生效：根本没调校验


async def test_publish_blocks_on_destructive_schema_drift(monkeypatch, sf, seed, pub_root):
    """P2 结构破坏性闸门（并入 preflight）：上一版归档快照有列 title、当前 schema.sql 删了它
    → 抛 DeployError、不起栈、不晋升 current_version（破坏性不自动执行，需人工两步走）。"""
    wid, aid = await seed(slug="zcpub", prod_port=8982, dev_port=8983,
                          status_prod="running", current_version=PREV_VER)
    (pub_root / "schema").mkdir()
    (pub_root / "schema" / "schema.sql").write_text(
        "CREATE TABLE IF NOT EXISTS demo_notes (id BIGSERIAL PRIMARY KEY);\n",
        encoding="utf-8", newline="")
    prev_dir = pub_root / "releases" / PREV_VER
    prev_dir.mkdir(parents=True)
    (prev_dir / "schema.sql").write_text(
        "CREATE TABLE IF NOT EXISTS demo_notes (id BIGSERIAL PRIMARY KEY);\n"
        "ALTER TABLE demo_notes ADD COLUMN IF NOT EXISTS title VARCHAR(200) NOT NULL;\n",
        encoding="utf-8", newline="")
    seams = PubSeams(monkeypatch, pub_root)

    with pytest.raises(DeployError) as ei:
        await prod.publish(sf, _pub_app(aid), notify=seams.notify, force=True)
    assert "破坏性" in str(ei.value) and "title" in str(ei.value)
    assert seams.step_seq == [("preflight", "running")]          # 阻断在 preflight 内、ok 之前
    assert "compose build" not in seams.verbs and not seams.ups()
    assert seams.dones[-1]["ok"] is False
    assert seams.statuses == ["publishing", "error"]
    assert await _db_pair(sf, aid) == (PREV_VER, "error")         # 旧版本记录不动，仅态转 error


async def test_publish_archives_schema_snapshot_on_success(monkeypatch, sf, seed, pub_root):
    """P2 快照归档：非破坏发布成功 → 本版 releases/<version>/schema.sql 落盘（下次 diff 基线）。"""
    wid, aid = await seed(slug="zcpub", prod_port=8982, dev_port=8983,
                          status_prod="running", current_version=PREV_VER)
    snap = ("CREATE TABLE IF NOT EXISTS demo_notes (id BIGSERIAL PRIMARY KEY);\n"
            "ALTER TABLE demo_notes ADD COLUMN IF NOT EXISTS title VARCHAR(200) NOT NULL;\n")
    (pub_root / "schema").mkdir()
    (pub_root / "schema" / "schema.sql").write_text(snap, encoding="utf-8", newline="")
    prev_dir = pub_root / "releases" / PREV_VER
    prev_dir.mkdir(parents=True)
    (prev_dir / "schema.sql").write_text(snap, encoding="utf-8", newline="")   # 同结构=非破坏，闸门放行
    seams = PubSeams(monkeypatch, pub_root)

    res = await prod.publish(sf, _pub_app(aid), notify=seams.notify, force=True)

    assert res["status"] == "running" and seams.steps_ok == list(PUB_STEPS)
    assert any("结构破坏性校验通过" in ln for ln in seams.logs)   # 闸门真跑了（非静默跳过）
    archived = pub_root / "releases" / res["version"] / "schema.sql"
    assert archived.is_file() and archived.read_text(encoding="utf-8") == snap


# ---- P3：破坏性闸门可确认 + 结构化负载 ----


def _seed_destructive(pub_root):
    """当前 schema.sql 删了上一版有 title 列 → 破坏性。"""
    (pub_root / "schema").mkdir()
    (pub_root / "schema" / "schema.sql").write_text(
        "CREATE TABLE IF NOT EXISTS demo_notes (id BIGSERIAL PRIMARY KEY);\n",
        encoding="utf-8", newline="")
    prev_dir = pub_root / "releases" / PREV_VER
    prev_dir.mkdir(parents=True)
    (prev_dir / "schema.sql").write_text(
        "CREATE TABLE IF NOT EXISTS demo_notes (id BIGSERIAL PRIMARY KEY);\n"
        "ALTER TABLE demo_notes ADD COLUMN IF NOT EXISTS title VARCHAR(200) NOT NULL;\n",
        encoding="utf-8", newline="")


async def test_compute_schema_diff_helper(tmp_path):
    # 缺基线 → None
    assert prod.compute_schema_diff(tmp_path, None) is None
    assert prod.compute_schema_diff(tmp_path, PREV_VER) is None   # 无文件
    # 有 prev + cur，删列 → 破坏性 diff
    _seed_destructive(tmp_path)
    d = prod.compute_schema_diff(tmp_path, PREV_VER)
    assert d is not None and "title" in d["dropped_columns"].get("demo_notes", [])


async def test_publish_destructive_carries_structured_payload(monkeypatch, sf, seed, pub_root):
    """被拦时 publish_done 事件带 code/destructive 结构化负载（不再只有红字 blob）。"""
    wid, aid = await seed(slug="zcpub", prod_port=8982, dev_port=8983,
                          status_prod="running", current_version=PREV_VER)
    _seed_destructive(pub_root)
    seams = PubSeams(monkeypatch, pub_root)
    with pytest.raises(DeployError):
        await prod.publish(sf, _pub_app(aid), notify=seams.notify, force=True)
    done = seams.dones[-1]
    assert done["ok"] is False
    assert done["code"] == "schema_destructive"
    assert isinstance(done["destructive"], list) and done["destructive"]
    assert any("title" in x for x in done["destructive"])


async def test_publish_confirm_destructive_bypasses_gate(monkeypatch, sf, seed, pub_root):
    """确认放行：confirm_destructive=True → 闸门跳过 → 走完并归档本版快照为新基线。"""
    wid, aid = await seed(slug="zcpub", prod_port=8982, dev_port=8983,
                          status_prod="running", current_version=PREV_VER)
    _seed_destructive(pub_root)
    seams = PubSeams(monkeypatch, pub_root)
    res = await prod.publish(sf, _pub_app(aid), notify=seams.notify,
                             force=True, confirm_destructive=True)
    assert res["status"] == "running"
    assert seams.step_seq.count(("preflight", "ok")) == 1        # preflight 通过
    archived = pub_root / "releases" / res["version"] / "schema.sql"
    assert archived.is_file()
    assert any("人工确认放行" in ln for ln in seams.logs)


async def test_publish_nondestructive_has_no_code_field(monkeypatch, sf, seed, pub_root):
    """既有全绿用例：无 schema/ 破坏性时 done 不带 code（向后兼容）。"""
    wid, aid = await seed(slug="zcpub", prod_port=8982, dev_port=8983,
                          status_prod="running", current_version=PREV_VER)
    seams = PubSeams(monkeypatch, pub_root)
    await prod.publish(sf, _pub_app(aid), notify=seams.notify, force=True)
    assert seams.dones[-1]["ok"] is True
    assert "code" not in seams.dones[-1]


async def test_publish_requires_dev_cred(monkeypatch, sf, seed, pub_root):
    """D-T4：build 前 dev 凭据读空 = 数据异常，500 显式抛（不静默传空 dict 让渲染器保留旧键）。"""
    wid, aid = await seed(slug="zcpub", prod_port=8982, dev_port=8983)
    seams = PubSeams(monkeypatch, pub_root, dev_cred=False)

    with pytest.raises(DeployError) as ei:
        await prod.publish(sf, _pub_app(aid), notify=seams.notify, force=True)
    assert ei.value.status == 500 and "dev 凭据" in str(ei.value)
    assert "compose build" not in seams.verbs and not seams.ensures()
    assert seams.dones[-1]["ok"] is False and "dev 凭据" in seams.dones[-1]["error"]
    assert seams.statuses == ["publishing", "error"]


async def test_publish_build_failure_keeps_old_stack(monkeypatch, sf, seed, pub_root):
    """build 非零退出不切栈（04 §4.1 旧容器继续服务）：无 up、版本零推进、事件收口 error。"""
    wid, aid = await seed(slug="zcpub", prod_port=8982, dev_port=8983,
                          status_prod="running", current_version=PREV_VER)
    seams = PubSeams(monkeypatch, pub_root, build_code=1)

    with pytest.raises(DeployError) as ei:
        await prod.publish(sf, _pub_app(aid), notify=seams.notify, force=True)
    assert "镜像构建失败" in str(ei.value)
    assert not seams.ups()
    assert "provision" not in seams.verbs and "migrate" not in seams.verbs
    assert seams.steps_ok == ["preflight"]
    assert seams.dones[-1]["ok"] is False and seams.dones[-1]["version"] == PREV_VER
    assert seams.statuses == ["publishing", "error"]
    assert await _db_pair(sf, aid) == (PREV_VER, "error")
    newv = seams.ensures()[0][1]
    row = await _record(sf, aid, "publish", newv)
    assert (row[1], row[2]) == ("failed", "build")


async def test_publish_migrate_failure_keeps_old_stack(monkeypatch, sf, seed, pub_root):
    """迁移不过应用不起（07）：migrate 失败 → 无 up，旧版继续服务，记账 failed/step=migrate。"""
    wid, aid = await seed(slug="zcpub", prod_port=8982, dev_port=8983,
                          status_prod="running", current_version=PREV_VER)
    seams = PubSeams(monkeypatch, pub_root, mig_ok=False)

    with pytest.raises(DeployError) as ei:
        await prod.publish(sf, _pub_app(aid), notify=seams.notify, force=True)
    assert "prod 迁移失败" in str(ei.value) and "旧版本继续服务" in str(ei.value)
    assert not seams.ups() and "health" not in seams.verbs
    assert seams.steps_ok == ["preflight", "build", "provision"]
    assert seams.dones[-1]["ok"] is False
    row = await _record(sf, aid, "publish", seams.ensures()[0][1])
    assert (row[1], row[2]) == ("failed", "migrate")
    assert await _db_pair(sf, aid) == (PREV_VER, "error")


async def test_publish_deploy_failure_skips_health(monkeypatch, sf, seed, pub_root):
    """compose up 非零（切换新版本失败）：不再体检，error + publish_done 收口。"""
    wid, aid = await seed(slug="zcpub", prod_port=8982, dev_port=8983,
                          status_prod="running", current_version=PREV_VER)
    seams = PubSeams(monkeypatch, pub_root, up_codes=(1,))

    with pytest.raises(DeployError) as ei:
        await prod.publish(sf, _pub_app(aid), notify=seams.notify, force=True)
    assert "切换新版本失败" in str(ei.value)
    assert len(seams.ups()) == 1 and "health" not in [c[0] for c in seams.calls]
    assert seams.steps_ok == ["preflight", "build", "provision", "migrate"]
    assert seams.dones[-1]["ok"] is False
    assert seams.statuses == ["publishing", "error"]
    row = await _record(sf, aid, "publish", seams.ensures()[0][1])
    assert (row[1], row[2]) == ("failed", "deploy")


async def test_publish_health_fail_autorollback(monkeypatch, sf, seed, pub_root):
    """已有 v1 在线 → 发 v2 体检挂 → 自动 up 旧 tag，旧版继续服务（04 §4.3/S-04-2）。"""
    wid, aid = await seed(slug="zcpub", prod_port=8982, dev_port=8983,
                          status_prod="running", current_version=PREV_VER)
    seams = PubSeams(monkeypatch, pub_root, health_results=(False,))

    res = await prod.publish(sf, _pub_app(aid), notify=seams.notify, force=True)

    newv = seams.ensures()[0][1]                       # 本次要发的新版本（体检挂在它身上）
    assert newv != PREV_VER
    assert res["status"] == "running" and res["version"] == PREV_VER   # 对外仍是旧版在服务
    # 自动回退：按旧版本号重渲染（.env PROD_VERSION 换回旧版）后再 up —— 第二次 up 带旧 PROD_VERSION
    assert [c[1] for c in seams.ensures()] == [newv, newv, PREV_VER]
    ups = seams.ups()
    assert len(ups) == 2
    assert ups[0][-1] == f"PROD_VERSION={newv}" and ups[1][-1] == f"PROD_VERSION={PREV_VER}"
    assert ups[1][1:4] == ("compose", "-f", str(pub_root / "deploy" / "prod-compose.yml"))
    # 旧版镜像在才是回退前提（按旧 tag 探测 frontend）
    assert ("image_exists", f"zc-zcpub-frontend:prod-{PREV_VER}") in seams.calls
    # 事件收口：体检未通过 + 已回退，最后一条 publish_done(ok=false)；状态机回 running
    assert seams.dones[-1]["ok"] is False and "已回退" in seams.dones[-1]["error"]
    # version 语义（M-c）：publish docstring 钉死「失败回指仍登记在服的 prev_version」——
    # 回退成功即旧版在服，本例正是该约定的「失败→prev」主路径
    assert seams.dones[-1]["version"] == PREV_VER and seams.statuses == ["publishing", "running"]
    assert seams.steps_ok == ["preflight", "build", "provision", "migrate", "deploy"]
    assert any("自动回退" in ln for ln in seams.logs)          # 播报回退（日志面板可见）
    assert all(_PW not in s for s in seams.bare_events)
    # 真库：版本列仍是旧版（新版本从未成为 current），状态回到 running，记账 failed/step=health
    assert await _db_pair(sf, aid) == (PREV_VER, "running")
    row = await _record(sf, aid, "publish", newv)
    assert (row[1], row[2]) == ("failed", "health") and "已回退上一版本" in row[3]


async def test_publish_health_fail_rollback_up_also_fails(monkeypatch, sf, seed, pub_root):
    """I-1（04 §6 行4「若回退也失败 → error，展示两个版本的日志入口」）：回退的第二次 up 也
    非零退出时**不得虚报已回退**——标 error、事件带 message、记账并列新旧两版信息。"""
    wid, aid = await seed(slug="zcpub", prod_port=8982, dev_port=8983,
                          status_prod="running", current_version=PREV_VER)
    seams = PubSeams(monkeypatch, pub_root, health_results=(False,), up_codes=(0, 1))

    res = await prod.publish(sf, _pub_app(aid), notify=seams.notify, force=True)

    newv = seams.ensures()[0][1]
    assert res["status"] == "error" and res["version"] == PREV_VER
    # 回退动作真发生过（按旧版重渲染 + 第二次 up 带旧 PROD_VERSION），只是没起来
    assert [c[1] for c in seams.ensures()] == [newv, newv, PREV_VER]
    ups = seams.ups()
    assert len(ups) == 2 and ups[1][-1] == f"PROD_VERSION={PREV_VER}"
    # 状态机收口 error（不是 running、也不是 unhealthy）；error 态事件必带 message
    assert seams.statuses == ["publishing", "error"]
    err = [e for e in seams.events if e["type"] == "prod_status" and e["status"] == "error"]
    assert err and "回退也失败" in err[0]["message"]
    assert seams.dones[-1]["ok"] is False and "回退失败" in seams.dones[-1]["error"]
    assert seams.dones[-1]["version"] == PREV_VER     # 约定见 publish docstring：失败回指在服版
    assert seams.steps_ok == ["preflight", "build", "provision", "migrate", "deploy"]
    assert await _db_pair(sf, aid) == (PREV_VER, "error")   # 版本零推进（新版本从未晋升）
    # 记账：failed/step=health，error 并列两版信息（新旧日志入口同指这条记录）
    row = await _record(sf, aid, "publish", newv)
    assert (row[1], row[2]) == ("failed", "health") and row[0] == res["recordId"]
    assert "回退也失败" in row[3] and newv in row[3] and PREV_VER in row[3]
    assert all(_PW not in s for s in seams.bare_events)


async def test_publish_health_fail_first_version_marks_unhealthy(monkeypatch, sf, seed, pub_root):
    """首版体检挂且无回退目标：标 unhealthy 供排查，不假装 running，同样收口 publish_done。"""
    wid, aid = await seed(slug="zcpub", prod_port=8982, dev_port=8983, current_version=None)
    seams = PubSeams(monkeypatch, pub_root, health_results=(False,))

    res = await prod.publish(sf, _pub_app(aid), notify=seams.notify, force=True)

    assert res["status"] == "unhealthy" and res["version"] is None
    assert len(seams.ups()) == 1                        # 没有第二次 up（无旧版可回退）
    # version 语义（M-c，见 publish docstring 约定）：首版从来没有过「仍登记在服的版本」，
    # 失败一律回指 prev_version（此即 None）——物理上跑着的不健康新容器由 status=unhealthy
    # 承载，不借 version 虚报晋升（审查报告 B.3 同一口径）。
    assert seams.dones[-1]["ok"] is False and seams.dones[-1]["version"] is None
    assert seams.statuses == ["publishing", "unhealthy"]
    assert await _db_pair(sf, aid) == (None, "unhealthy")   # 版本零推进（首版没成）
    row = await _record(sf, aid, "publish", seams.ensures()[0][1])
    assert (row[1], row[2]) == ("failed", "health") and "无旧版可回退" in row[3]


async def test_publish_health_fail_rollback_image_cleaned_says_so(monkeypatch, sf, seed, pub_root):
    """有旧版但旧版镜像已被 keep-5 清掉：不回退（标 unhealthy），文案要说清是「镜像没了」不是「首版」。"""
    wid, aid = await seed(slug="zcpub", prod_port=8982, dev_port=8983,
                          status_prod="running", current_version=PREV_VER)
    seams = PubSeams(monkeypatch, pub_root, health_results=(False,), images=False)

    res = await prod.publish(sf, _pub_app(aid), notify=seams.notify, force=True)

    assert res["status"] == "unhealthy" and len(seams.ups()) == 1   # 没有第二次 up（回退不可行）
    assert seams.statuses == ["publishing", "unhealthy"]
    assert seams.dones[-1]["ok"] is False and "镜像已清理" in seams.dones[-1]["error"]
    row = await _record(sf, aid, "publish", seams.ensures()[0][1])
    assert (row[1], row[2]) == ("failed", "health") and "镜像已清理" in row[3]
    assert "首次发布" not in row[3]                                  # 不误导成「从来没发过版」
    assert await _db_pair(sf, aid) == (PREV_VER, "unhealthy")        # 版本仍是旧版（没被新版本冲掉）


async def test_rollback_missing_image(monkeypatch, sf, seed, pub_root):
    """回滚目标镜像已被 keep-5 清掉 → 400「镜像已清理」，且在记账与状态变更之前（闸门先立）。"""
    wid, aid = await seed(slug="zcpub", prod_port=8982, dev_port=8983,
                          status_prod="running", current_version=PREV_VER)
    seams = PubSeams(monkeypatch, pub_root, images=False)

    with pytest.raises(DeployError) as ei:
        await prod.rollback(sf, _pub_app(aid), "b20200101000000", notify=seams.notify)
    assert ei.value.status == 400 and "镜像已清理" in str(ei.value)
    # 短路 + 报出缺的是哪个角色（frontend 就没，backend 不必再探——错误文案要能指认）
    tags = [c[1] for c in seams.calls if c[0] == "image_exists"]
    assert tags == ["zc-zcpub-frontend:prod-b20200101000000"]
    assert "frontend" in str(ei.value)
    assert not seams.ups() and seams.ensures() == []        # 前置闸门：什么都没起、没渲染
    assert seams.statuses == []                             # 状态机零变更（无 publishing 闪现）
    assert await _record_count(sf, aid) == 0                # 记账零行（闸门先于 begin）
    assert await _db_pair(sf, aid) == (PREV_VER, "running")
    assert seams.dones[-1]["ok"] is False                   # 后台路径失败也收口，不留 202 黑洞


async def test_rollback_requires_prod_cred(monkeypatch, sf, seed, pub_root):
    """镜像在但 prod 凭据缺失（供给被清）→ 409，同样是记账与状态变更之前的闸门。"""
    wid, aid = await seed(slug="zcpub", prod_port=8982, dev_port=8983,
                          status_prod="running", current_version=PREV_VER)
    seams = PubSeams(monkeypatch, pub_root, prod_cred=False)

    with pytest.raises(DeployError) as ei:
        await prod.rollback(sf, _pub_app(aid), PREV_VER, notify=seams.notify)
    assert ei.value.status == 409 and "凭据" in str(ei.value)
    assert not seams.ups() and seams.ensures() == []
    assert await _record_count(sf, aid) == 0
    assert await _db_pair(sf, aid) == (PREV_VER, "running")
    assert seams.dones[-1]["ok"] is False


async def test_rollback_switches_version_without_migrating(monkeypatch, sf, seed, pub_root):
    """回滚 = 只换程序（07/00 D8：数据库永不回滚）：按目标版本重渲染 + up，绝不重放迁移。"""
    NEW = "b20260921000000"
    wid, aid = await seed(slug="zcpub", prod_port=8982, dev_port=8983,
                          status_prod="running", current_version=NEW)
    seams = PubSeams(monkeypatch, pub_root)

    res = await prod.rollback(sf, _pub_app(aid), PREV_VER, notify=seams.notify)

    assert res["status"] == "running" and res["version"] == PREV_VER
    assert "migrate" not in seams.verbs and "provision" not in seams.verbs   # 不迁移、不再供给
    assert seams.ensures()[0][1] == PREV_VER
    up = seams.ups()[0]
    assert up[-1] == f"PROD_VERSION={PREV_VER}"
    assert up[1:4] == ("compose", "-f", str(pub_root / "deploy" / "prod-compose.yml"))
    assert seams.step_seq == []                     # 回滚无七步进度（它是单步切换）
    assert seams.statuses == ["publishing", "running"]
    assert seams.dones[-1]["ok"] is True and seams.dones[-1]["version"] == PREV_VER
    assert await _db_pair(sf, aid) == (PREV_VER, "running")
    row = await _record(sf, aid, "rollback", PREV_VER)
    assert (row[1], row[2]) == ("success", "health")
    assert row[4]["health"] == {"frontend": True, "api": True}   # M-b：回滚成功 detail 也带 health
    assert all(_PW not in s for s in seams.bare_events)


async def test_rollback_unexpected_error_also_emits_prod_status(monkeypatch, sf, seed, pub_root):
    """M-a（收口对称）：rollback 体内的**意外异常**（非 DeployError）也要发 prod_status
    {status:error, message}——修前只发 publish_done，状态列已 error 而事件流少一条，
    前端按事件流刷新的话会卡在 publishing。publish 侧两分支本就都发，此处补齐。"""
    wid, aid = await seed(slug="zcpub", prod_port=8982, dev_port=8983,
                          status_prod="running", current_version=PREV_VER)
    seams = PubSeams(monkeypatch, pub_root)

    def boom(root_, meta, creds, version=None):
        raise RuntimeError("渲染器炸了")

    monkeypatch.setattr(prod, "ensure_deploy_files", boom)   # try 体内、版本 UPDATE 之前
    with pytest.raises(DeployError) as ei:
        await prod.rollback(sf, _pub_app(aid), PREV_VER, notify=seams.notify)
    assert "回滚失败" in str(ei.value) and "渲染器炸了" in str(ei.value)
    assert seams.statuses == ["publishing", "error"]          # 补发的那一条
    err = [e for e in seams.events if e["type"] == "prod_status" and e["status"] == "error"]
    assert err and "渲染器炸了" in err[0]["message"]           # error 态事件必带 message（T9/T10）
    assert seams.dones[-1]["ok"] is False                     # 收口仍是最后一条
    assert await _db_pair(sf, aid) == (PREV_VER, "error")      # 意外异常不晋升版本
    assert all(_PW not in s for s in seams.bare_events)


async def test_cleanup_images_keeps_recent_five(monkeypatch, sf, seed):
    """keep-5：只交最老的版本去 rmi（前后端各一），返回清单=实删 tag（04 FR4-05）。"""
    wid, aid = await seed(slug="zcpub", prod_port=8982, dev_port=8983)
    vs = [f"b2026091900000{i}" for i in range(1, 8)]        # 7 版成功记录，keep=5 → 删最老 2 版
    await _seed_publish_history(sf, aid, vs)
    seams = PubSeams(monkeypatch, Path("D:/__unused__"))    # 清理不碰文件面，root 不参与

    removed = await prod.cleanup_images(sf, aid, "zcpub", notify=seams.notify)

    assert sorted(removed) == sorted([f"zc-zcpub-frontend:prod-{vs[0]}",
                                      f"zc-zcpub-backend:prod-{vs[0]}",
                                      f"zc-zcpub-frontend:prod-{vs[1]}",
                                      f"zc-zcpub-backend:prod-{vs[1]}"])
    assert sorted(seams.rms()) == sorted(removed)           # 返回的正是真发出去删掉的 tag
    assert not [tag for tag in removed for i in range(2, 7) if vs[i] in tag]
    assert any("已清理旧镜像 4 个" in ln and "保留最近 5 版" in ln for ln in seams.logs)


async def test_cleanup_images_reports_only_actually_removed(monkeypatch, sf, seed):
    """rmi 尽力而为：删失败（镜像被引用/已不存在）不写进返回清单，也不播报「已清理」。"""
    wid, aid = await seed(slug="zcpub", prod_port=8982, dev_port=8983)
    vs = [f"b2026091900000{i}" for i in range(1, 8)]
    await _seed_publish_history(sf, aid, vs)
    seams = PubSeams(monkeypatch, Path("D:/__unused__"), rm_code=1)

    removed = await prod.cleanup_images(sf, aid, "zcpub", notify=seams.notify)

    assert removed == [] and len(seams.rms()) == 4          # 发过删除请求，但不算实删
    assert not any("已清理旧镜像" in ln for ln in seams.logs)


def test_scan_sqlchange_flags_bad_names(tmp_path):
    """迁移文件命名规范扫描（preflight 用）：只认 V<8 位日期>_<2 位序号>_<安全名>.sql。"""
    d = tmp_path / "sqlchange"
    d.mkdir()
    (d / "V20260920_01_ok.sql").write_text("SELECT 1", encoding="utf-8")
    (d / "notes.sql").write_text("x", encoding="utf-8")
    (d / "V2026_02_short.sql").write_text("x", encoding="utf-8")
    bad = prod.scan_sqlchange(tmp_path)
    assert sorted(bad) == ["V2026_02_short.sql", "notes.sql"]
    (d / "V20260920_02_中文说明.sql").write_text("x", encoding="utf-8")      # 中文名合法
    (d / "V20260920_03_a;b.sql").write_text("x", encoding="utf-8")           # 含分号非法
    assert prod.scan_sqlchange(tmp_path) == ["V20260920_03_a;b.sql", "V2026_02_short.sql",
                                             "notes.sql"]
    assert prod.scan_sqlchange(tmp_path / "nowhere") == []                   # 目录不存在=无违规


def test_hub_active_session_ids_snapshot():
    """FR4-09 数据源冒烟：emit 登记表键集的快照（改返回值不得回写内部状态）。"""
    hub = ChatHub()
    assert hub.active_session_ids() == []

    async def emit(session_id, event):
        return None

    hub.set_emit(7, emit)
    hub.set_emit(9, emit)
    assert sorted(hub.active_session_ids()) == [7, 9]
    snap = hub.active_session_ids()
    snap.append(999)
    assert sorted(hub.active_session_ids()) == [7, 9]
    hub.drop_emit(7, emit)
    assert hub.active_session_ids() == [9]


# ---- 终审 C-1 / I-1 回归 ----


async def test_publish_wires_legacy_dockerfile_and_idempotent(monkeypatch, sf, seed, pub_root, tmp_path):
    """C-1：M1 存量 Dockerfile 没有 COPY nginx.conf → 构建静默成功产出 stock nginx 配置，
    /api 探针 404 直到体检超时——preflight 必须把它接上，且幂等/自定义形状放过。"""
    legacy = ("FROM node:20-alpine AS build\nWORKDIR /src\nRUN npm run build\n\n"
              "FROM nginx:1.27-alpine\nCOPY --from=build /src/dist /usr/share/nginx/html\n")
    wid, aid = await seed(slug="zcpub", prod_port=8982, dev_port=8983)
    fe = pub_root / "frontend"
    (fe / "Dockerfile").write_text(legacy, encoding="utf-8")
    seams = PubSeams(monkeypatch, pub_root)

    await prod.publish(sf, _pub_app(aid), notify=seams.notify, force=True)

    wired = (fe / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY nginx.conf /etc/nginx/conf.d/default.conf" in wired
    assert wired.index("COPY nginx.conf") < wired.index("COPY --from=build")   # conf 先拷，构建上下文才带得上
    assert (fe / "nginx.conf").is_file()
    assert any("补齐" in ln and "Dockerfile" in ln for ln in seams.logs)

    # 幂等：已接线再跑一轮不重复插行
    assert prod._wire_dockerfile_proxy(fe / "Dockerfile") is False
    assert wired == (fe / "Dockerfile").read_text(encoding="utf-8")
    # 不认识的平台外形状（非 nginx 基座）原样放过
    custom = tmp_path / "custom" / "frontend"
    custom.mkdir(parents=True)
    (custom / "Dockerfile").write_text("FROM caddy:2\n", encoding="utf-8")
    before = (custom / "Dockerfile").read_text(encoding="utf-8")
    assert prod._wire_dockerfile_proxy(custom / "Dockerfile") is False
    assert (custom / "Dockerfile").read_text(encoding="utf-8") == before


async def test_publish_survives_readme_and_cleanup_tail_failures(monkeypatch, sf, seed, pub_root):
    """I-1：晋升+体检通过即不可撤销点——README 刷挂 / keep-N 清理抛都只降级 warning，
    记账必 success、事件流只有一条 done(ok:true)，修前整条成功发布会被改判 failed 并污染 keep 名额。"""
    wid, aid = await seed(slug="zcpub", prod_port=8982, dev_port=8983)
    (pub_root / "README.md").write_bytes(b"# zcpub \xff\xfe broken-encoding\n")   # 非 UTF-8（用户/AI 可改坏）
    seams = PubSeams(monkeypatch, pub_root)

    async def boom(sf_, app_id_, slug_, notify=None):
        raise RuntimeError("docker daemon 挂了")

    monkeypatch.setattr(prod, "cleanup_images", boom)
    res = await prod.publish(sf, _pub_app(aid), notify=seams.notify, force=True)

    assert res["status"] == "running"
    assert await _db_pair(sf, aid) == (res["version"], "running")
    row = await _record(sf, aid, "publish", res["version"])
    assert row[1] == "success" and row[2] == "record"
    assert len(seams.dones) == 1 and seams.dones[0]["ok"] is True
    assert seams.statuses == ["publishing", "running"]
    assert any("README" in ln for ln in seams.logs) and any("清理异常" in ln for ln in seams.logs)
    # 成功行落定后，任何迟到者都不能把它改成 failed（keep-N 名额的数据地基）
    await records.finish(sf, row[0], "failed", error="迟到的改判")
    again = await _record(sf, aid, "publish", res["version"])
    assert again[1] == "success"
