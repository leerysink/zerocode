# tests/test_deploy_basics.py
import socket
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
