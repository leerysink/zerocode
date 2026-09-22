# tests/test_gateway_routes.py
"""网关 routes.json 导出（通配域名 HTTPS 网关，工单 003/004）。

接缝：app/deploy/gateway_routes.export_routes(session_factory, out_path)。
断言以「给定库内应用状态 → 导出文件内容」为靶心，底层实现可动而测试不动。
"""
import json
import os
from pathlib import Path

import pytest
from sqlalchemy import text

from app.db import make_session_factory
from app.deploy import gateway_routes, prod
from app.services import app_service


async def _mk_app(s, slug: str, prod_port: int, status_prod: str) -> int:
    """插一个 workspace + app_project 行并设 prod 状态，返回 app id。"""
    wid = (await s.execute(text(
        "INSERT INTO workspace (name, root_path) VALUES (:n, :r) RETURNING id"),
        {"n": slug, "r": f"/tmp/{slug}"})).scalar()
    aid = (await s.execute(text(
        "INSERT INTO app_project (workspace_id, name, slug, scale, stack, prod_port, dev_port) "
        "VALUES (:w, :n, :sl, 'small', 'vue3-fastapi', :pp, :dp) RETURNING id"),
        {"w": wid, "n": slug, "sl": slug, "pp": prod_port, "dp": prod_port + 1})).scalar()
    await s.execute(text("UPDATE app_project SET status_prod = :st WHERE id = :i"),
                    {"st": status_prod, "i": aid})
    await s.commit()
    return aid


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


async def test_only_running_apps_are_exported(test_engine, tmp_path):
    """只有 status_prod=='running' 的应用进 routes.json；stopped/未建都不进。"""
    sf = make_session_factory(test_engine)
    async with sf() as s:
        await _mk_app(s, "alpha", 8301, "running")
        await _mk_app(s, "beta", 8303, "stopped")
        await _mk_app(s, "gamma", 8305, "error")
    out = tmp_path / "routes.json"

    result = await gateway_routes.export_routes(sf, out)

    assert result == {"alpha": 8301}
    assert _read(out) == {"alpha": 8301}


async def test_export_is_idempotent_no_rewrite(test_engine, tmp_path):
    """内容未变时第二次导出不重写文件（mtime 保持）——避免无谓的网关刷新。"""
    sf = make_session_factory(test_engine)
    async with sf() as s:
        await _mk_app(s, "stable", 8311, "running")
    out = tmp_path / "routes.json"

    await gateway_routes.export_routes(sf, out)
    mtime1 = os.stat(out).st_mtime_ns
    await gateway_routes.export_routes(sf, out)
    assert os.stat(out).st_mtime_ns == mtime1, "内容没变却重写了文件"


async def test_malformed_slug_skipped(test_engine, tmp_path, caplog):
    """通配证书只匹配一级域名：含点号等的畸形 slug 不进表，且留下告警。"""
    sf = make_session_factory(test_engine)
    async with sf() as s:
        await _mk_app(s, "okapp", 8321, "running")
        await _mk_app(s, "bad.dot", 8323, "running")   # 非法 slug（含点）
    out = tmp_path / "routes.json"

    result = await gateway_routes.export_routes(sf, out)

    assert result == {"okapp": 8321}
    assert "bad.dot" not in _read(out)


async def test_refresh_swallows_export_failure(test_engine, monkeypatch):
    """refresh 是业务旁路：export 抛错只告警，绝不上抛打断主流程（prod 起停/删除不受影响）。"""
    async def _boom(*a, **k):
        raise RuntimeError("disk on fire")
    monkeypatch.setattr(gateway_routes, "export_routes", _boom)

    # 不应抛异常
    await gateway_routes.refresh(make_session_factory(test_engine))


# ---- 工单 004：接线进 prod 状态机与删除流程 ----

async def test_set_status_running_then_stopped_drives_file(test_engine, gateway_routes_tmp):
    """经 prod.set_status 置 running 后 routes.json 含该 slug；置 stopped 后自动消失。

    断言落在 env 指定的会话临时文件上（conftest autouse 隔离，不污染仓库 gateway/routes/）。
    """
    sf = make_session_factory(test_engine)
    async with sf() as s:
        aid = await _mk_app(s, "wired", 8401, "stopped")
    out = gateway_routes_tmp

    await prod.set_status(sf, aid, "running")
    assert _read(out) == {"wired": 8401}

    await prod.set_status(sf, aid, "stopped")
    assert _read(out) == {}


async def test_delete_app_removes_entry(test_engine, gateway_routes_tmp):
    """running 应用经 delete_app 删除后，routes.json 不再含其 slug。"""
    sf = make_session_factory(test_engine)
    async with sf() as s:
        aid = await _mk_app(s, "doomed", 8403, "stopped")
    await prod.set_status(sf, aid, "running")
    assert "doomed" in _read(gateway_routes_tmp)

    await app_service.delete_app(sf, aid)
    assert _read(gateway_routes_tmp) == {}


async def test_set_status_survives_export_failure(test_engine, monkeypatch):
    """导出炸了（如路径不可写）不能把 prod.set_status 带崩：状态仍落库，异常被旁路吞掉。"""
    async def _boom(*a, **k):
        raise RuntimeError("readonly fs")
    monkeypatch.setattr(gateway_routes, "export_routes", _boom)
    sf = make_session_factory(test_engine)
    async with sf() as s:
        aid = await _mk_app(s, "resilient", 8405, "stopped")

    await prod.set_status(sf, aid, "running")   # 不抛

    assert await prod.get_status(sf, aid) == "running"
