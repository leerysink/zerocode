# tests/test_deploy_basics.py
import re
import socket
from pathlib import Path

import pytest
from sqlalchemy import text
from app.deploy.errors import DeployError
from app.deploy import slugs, settings, ports


def _always_free(port: int) -> bool:
    """探测替身：端口可用性只由 app_project 的登记状态决定，真实 TCP 占用不参与期望推导。"""
    return True


async def _port_base(s) -> int:
    return int((await s.execute(text(
        "SELECT value FROM app_config WHERE key='deploy.port_base'"))).scalar() or 8210)


async def _registered_ports(s) -> set[int]:
    """当前 app_project 已登记的全部端口——共享库里可能含并行会话/冒烟残留的他方应用行。"""
    return {p for pair in (await s.execute(text(
        "SELECT prod_port, dev_port FROM app_project"))).fetchall() for p in pair}


async def _reserve_pair(s, wid: int, slug: str, prod: int, dev: int) -> None:
    """在调用方的事务里登记一对端口（用例结束统一 rollback，不留残留行）。"""
    await s.execute(text(
        "INSERT INTO app_project (workspace_id, name, slug, scale, stack, prod_port, dev_port) "
        "VALUES (:w, :n, :sl, 'static', 'static', :p, :d)"),
        {"w": wid, "n": slug, "sl": slug, "p": prod, "d": dev})


async def _assert_earliest_available(s, prod: int, occupied: set[int] = frozenset()) -> None:
    """钉「取最早可用对」：段首到 prod 之前的每一对，必须已被登记或被探测占用。

    这是相对化断言的核心——期望值来自当前登记状态，而不是「空库下第 N 对等于某个字面量」。
    后者在共享环境里只要有他方应用行就必然假红（M1 执行/冒烟三次踩坑，本波 Task 1 硬化）。
    """
    base = await _port_base(s)
    used = await _registered_ports(s)
    for q in range(base if base % 2 == 0 else base + 1, prod, 2):
        assert q in used or q + 1 in used or q in occupied or q + 1 in occupied, \
            f"端口对 {q}/{q + 1} 更早且可用，却被 allocate_pair 跳过"


def test_slug_rules():
    assert slugs.SLUG_RE.fullmatch("lailiao2")
    assert not slugs.SLUG_RE.fullmatch("2abc")      # 数字开头
    assert not slugs.SLUG_RE.fullmatch("a" * 41)    # 超长
    assert not slugs.SLUG_RE.fullmatch("Abc")       # 大写
    with pytest.raises(DeployError):
        slugs.validate_slug("坏slug")

def test_suggest_slug_dedup():
    assert slugs.suggest_slug("app", set()) == "app"
    assert slugs.suggest_slug("app", {"app"}) == "app2"
    assert slugs.suggest_slug("app", {"app", "app2"}) == "app3"

def test_normalize_slug():
    assert slugs.normalize_slug("MyApp-2") == "myapp2"    # 大写转小写、符号剥离
    assert slugs.normalize_slug("a" * 50) == "a" * 40     # 超长截 40
    assert slugs.normalize_slug("宠物店") == ""            # 纯中文无法提取 → 空串

async def test_deploy_cfg_defaults(test_engine):
    from app.db import make_session_factory
    sf = make_session_factory(test_engine)
    cfg = await settings.deploy_cfg(sf)
    assert cfg["deploy.port_base"] == 8210
    assert cfg["deploy.health_timeout"] == 240      # 默认 240s：首次冷启动需在容器内安装依赖
    assert settings.resolve_apps_root(cfg).name == "apps"


def test_build_timeout_by_stack():
    """大型栈的首次 prod build 要在构建期下载整套 Maven 依赖（spike 实测 ≈350s）+ npm ci + vue-tsc，
    600s 默认上限会把成功构建误杀成「镜像构建失败」——而且这刀切在半成品上，比体检超时更难查。"""
    from app.deploy.settings import DEFAULTS, build_timeout
    assert DEFAULTS["deploy.build_timeout"] == 600
    assert DEFAULTS["deploy.build_timeout_large"] == 1800
    cfg = {"deploy.build_timeout": 600, "deploy.build_timeout_large": 1800}
    assert build_timeout(cfg, "vue3-springboot") == 1800
    assert build_timeout(cfg, "vue3-fastapi") == 600
    assert build_timeout(cfg, "") == 600                       # 栈缺失（M1 老行）走默认
    assert build_timeout({}, "vue3-springboot") == 600         # 配置读空退回默认，不炸发布
    assert build_timeout({"deploy.build_timeout": 900}, "vue3-springboot") == 900   # 只设旧键时大型栈跟着走那个值（与 health_timeout 同构）

async def test_allocate_pair_skips_used(test_engine):
    """已登记的端口对必须被跳过，且取到的是紧随其后的最早可用对。

    硬化（本波 Task 1）：原写法「插 8210/8211 → 期望 (8212, 8213)」隐含「共享库为空」假设，
    并行会话/冒烟残留的 app_project 行会让他假红（插桩撞 UNIQUE 或期望对错位）。
    现在先按当前登记状态取真实可用对并占住两对，再从登记状态推导期望。
    """
    from app.db import make_session_factory
    sf = make_session_factory(test_engine)
    async with sf() as s:
        wid = (await s.execute(text(
            "INSERT INTO workspace (name, root_path) VALUES ('guard', 'D:/__zc_guard_ports__') "
            "RETURNING id"))).scalar()
        reserved = []
        for slug in ("zcg1", "zcg2"):
            p, d = await ports.allocate_pair(s, probe=_always_free)   # 当前真实可用对（含他方行在内）
            await _reserve_pair(s, wid, slug, p, d)
            reserved.append((p, d))
        prod, dev = await ports.allocate_pair(s, probe=_always_free)
        assert prod % 2 == 0 and dev == prod + 1                      # prod=偶 / dev=奇 成对不变式
        assert not {prod, dev} & await _registered_ports(s)           # 已登记的一律跳过
        assert prod > reserved[-1][1]                                 # 排在刚登记的两对之后
        await _assert_earliest_available(s, prod)                     # 且不放过更早的可用对
        await s.rollback()                                             # 守卫行不落库（双保险：conftest 也 TRUNCATE）

async def test_allocate_pair_skips_occupied(test_engine):
    """空闲登记但 TCP 探测不通（被别的程序占用）的端口对也要跳过。

    硬化：占用哪一对、跳过之后得到哪一对，都由当前登记状态推导——不再钉死「8210/8211 占用 →
    期望 8212/8213」（他方行占住段首时该期望必然假红）。
    """
    from app.db import make_session_factory
    sf = make_session_factory(test_engine)
    async with sf() as s:
        first_prod, first_dev = await ports.allocate_pair(s, probe=_always_free)
        occupied = {first_prod, first_dev}                            # 唯一被探测拒掉的那一（对）
        prod, dev = await ports.allocate_pair(s, probe=lambda p: p not in occupied)
        assert (prod, dev) != (first_prod, first_dev)
        assert prod % 2 == 0 and dev == prod + 1
        assert not {prod, dev} & await _registered_ports(s)
        await _assert_earliest_available(s, prod, occupied=occupied)

async def test_allocate_pair_exhausted(test_engine):
    from app.db import make_session_factory
    from sqlalchemy import text as t2
    sf = make_session_factory(test_engine)
    async with sf() as s:
        await s.execute(t2("UPDATE app_config SET value='8210' WHERE key='deploy.port_base'"))
        await s.execute(t2("UPDATE app_config SET value='8212' WHERE key='deploy.port_max'"))
        await s.commit()
        # 合法小区间（8210~8212 仅一对）+ 全部端口探测占用 → 409「端口段已用尽」
        # 复位进 finally（本波追加项）：中途断言抛错也不把 port_max=8212 残留在共享库
        try:
            with pytest.raises(DeployError) as excinfo:
                await ports.allocate_pair(s, probe=lambda p: False)
            assert excinfo.value.status == 409
            assert "已用尽" in str(excinfo.value)
        finally:
            await s.execute(t2("UPDATE app_config SET value='8210' WHERE key='deploy.port_base'"))
            await s.execute(t2("UPDATE app_config SET value='8399' WHERE key='deploy.port_max'"))
            await s.commit()

def test_port_free_local_tcp_free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); free = s.getsockname()[1]; s.close()
    assert ports.port_free(free) is True


async def test_allocate_pair_probes_the_configured_probe_host(test_engine, monkeypatch):
    """默认探针连的是 preview_probe_host()，不是写死的 127.0.0.1。

    平台容器化部署时 127.0.0.1 是容器自己的 loopback，宿主上已发布的应用端口一律「探起来空闲」，
    分配于是只看库内登记 —— 库里已无行、容器还活着的那些端口就这样被重复分出去。
    """
    from app.db import make_session_factory
    seen: list[tuple[int, str]] = []

    def record(port, host="127.0.0.1", timeout=0.2):
        seen.append((port, host))
        return True

    monkeypatch.setattr(ports, "port_free", record)
    monkeypatch.setenv("ZEROCODE_PREVIEW_PROBE_HOST", "172.17.0.1")
    async with make_session_factory(test_engine)() as s:
        await ports.allocate_pair(s)
    assert seen, "allocate_pair 没调用任何端口探测"
    assert {host for _, host in seen} == {"172.17.0.1"}


# 线上 daemon 原文（演示服 2026-10-28，dev 栈起不来时 compose 吐出的那一整行）
_BIND_CONFLICT_LOG = (
    "Error response from daemon: driver failed programming external connectivity on "
    "endpoint zc-finance-dev-frontend-1 (8e8cd85384b731ca9546cce8b493b68efbfc6eb612c5703e"
    "373305b731bd4699): Bind for 0.0.0.0:8213 failed: port is already allocated")


def test_parse_bind_conflict_reads_the_host_port_that_failed_to_bind():
    assert ports.parse_bind_conflict(_BIND_CONFLICT_LOG) == 8213
    assert ports.parse_bind_conflict("Bind for [::]:8214 failed: port is already allocated") == 8214
    assert ports.parse_bind_conflict("Error response from daemon: No such image: x") is None
    assert ports.parse_bind_conflict("") is None


async def test_port_holder_names_the_container_publishing_the_port():
    """占用者点名走 `docker ps --filter publish=<port>`：查得到给名字，查不到给空串。"""
    calls = []

    async def run(*args, **kwargs):
        calls.append(args)
        return 0, "zc-test1-dev-frontend-1\n"

    assert await ports.port_holder(8213, run) == "zc-test1-dev-frontend-1"
    assert "publish=8213" in calls[0], f"查询没按端口过滤，点名会张冠李戴: {calls[0]}"


async def test_port_holder_returns_empty_when_nothing_publishes_the_port():
    async def free(*args, **kwargs):
        return 0, ""

    async def broken(*args, **kwargs):
        return 1, "Cannot connect to the Docker daemon"

    assert await ports.port_holder(8213, free) == ""
    assert await ports.port_holder(8213, broken) == ""


def test_frontend_publish_gate_matches_backend_whitelist():
    """发布闸门不漂移：真源是后端 PUBLISHABLE_STACKS，前端那份只是镜像。

    M2 时期后端已放开 vue3-springboot、前端仍写死 fastapi，大型应用的「发布」因此置灰了一个
    迭代，而且漂移是静默的——禁用按钮不带原因，用户只会当成后端坏了。商店入口同一道闸门。

    镜像住在 utils/publishableStacks.js：应用列表与聊天页操作条是同一个闸门的两个入口，
    两边都必须 import 这一份——谁自己写死一份，谁就在下一次放栈时静默漂移。
    """
    from app.deploy.compose import PUBLISHABLE_STACKS
    fe = Path(__file__).resolve().parents[1] / "frontend" / "src"
    mirror = fe / "utils" / "publishableStacks.js"
    src = mirror.read_text(encoding="utf-8")

    decl = re.search(r"export const PUBLISHABLE_STACKS\s*=\s*\[([^\]]*)\]", src)
    assert decl, f"{mirror.name} 里找不到 PUBLISHABLE_STACKS 常量——闸门又退回写死在各处了"
    front = frozenset(re.findall(r"['\"]([^'\"]+)['\"]", decl.group(1)))
    assert front == frozenset(PUBLISHABLE_STACKS), \
        f"前端栈白名单 {sorted(front)} 与后端 {sorted(PUBLISHABLE_STACKS)} 不一致"

    gate = re.search(r"export const canPublish = \(app\) =>(.*)", src)
    assert gate, "找不到 canPublish 的判定式，闸门形态变了，请同步本测试"
    assert "PUBLISHABLE_STACKS" in gate.group(1), \
        "canPublish 没走统一栈常量，会在后端放开新栈时再次漂移"

    for rel in ("views/AppsView.vue", "components/ChatAppActions.vue"):
        assert "utils/publishableStacks" in (fe / rel).read_text(encoding="utf-8"), \
            f"{rel} 没读共享名单，闸门分叉了"

    apps_view = (fe / "views" / "AppsView.vue").read_text(encoding="utf-8")
    store_gate = re.search(r"const canReleaseToStore = \(a\) =>(.*)", apps_view)
    assert store_gate, "找不到 canReleaseToStore 的判定式，商店闸门形态变了，请同步本测试"
    assert "canPublish(a)" in store_gate.group(1), "商店入口没走同一道栈闸门"
