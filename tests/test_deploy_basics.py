# tests/test_deploy_basics.py
import socket
import pytest
from app.deploy.errors import DeployError
from app.deploy import slugs, settings, ports

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

async def test_deploy_cfg_defaults(test_engine):
    from app.db import make_session_factory
    sf = make_session_factory(test_engine)
    cfg = await settings.deploy_cfg(sf)
    assert cfg["deploy.port_base"] == 8210
    assert settings.resolve_apps_root(cfg).name == "apps"

async def test_allocate_pair_skips_used(test_engine):
    from app.db import make_session_factory
    from sqlalchemy import text
    sf = make_session_factory(test_engine)
    async with sf() as s:
        wid = (await s.execute(text(
            "INSERT INTO workspace (name, root_path) VALUES ('t', 'D:/__zc_basic__') RETURNING id"))).scalar()
        await s.execute(text(
            "INSERT INTO app_project (workspace_id, name, slug, scale, stack, prod_port, dev_port) "
            "VALUES (:w, 't', 'zcbasic', 'small', 'static', 8210, 8211)"), {"w": wid})
        await s.commit()
        assert await ports.allocate_pair(s, probe=lambda p: True) == (8212, 8213)

async def test_allocate_pair_skips_occupied(test_engine):
    from app.db import make_session_factory
    sf = make_session_factory(test_engine)
    async with sf() as s:
        # 8210/8211 空闲登记但 TCP 探测失败（被其他程序占用）→ 跳到下一对
        assert await ports.allocate_pair(s, probe=lambda p: p not in (8210, 8211)) == (8212, 8213)

async def test_allocate_pair_exhausted(test_engine):
    from app.db import make_session_factory
    from sqlalchemy import text as t2
    sf = make_session_factory(test_engine)
    async with sf() as s:
        await s.execute(t2("UPDATE app_config SET value='8210' WHERE key='deploy.port_base'"))
        await s.execute(t2("UPDATE app_config SET value='8210' WHERE key='deploy.port_max'"))
        await s.commit()
        with pytest.raises(DeployError):
            await ports.allocate_pair(s, probe=lambda p: True)
        await s.execute(t2("UPDATE app_config SET value='8210' WHERE key='deploy.port_base'"))
        await s.execute(t2("UPDATE app_config SET value='8399' WHERE key='deploy.port_max'"))
        await s.commit()

def test_port_free_local_udp_free_port():
    import random
    s = socket.socket(); s.bind(("127.0.0.1", 0)); free = s.getsockname()[1]; s.close()
    assert ports.port_free(free) is True
