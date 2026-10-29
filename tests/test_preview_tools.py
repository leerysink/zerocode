# tests/test_preview_tools.py
"""Agent 预览工具（规格 docs/specs/2026-10-29-agent-preview-tools.md）。

Docker 侧一律替身：本文件验「工具怎么解析应用、怎么整形结果、怎么等待与收口」，
真起栈由 tests/test_engine.py 与手工矩阵负责。
"""
import asyncio

import pytest
from sqlalchemy import text

from app.db import make_session_factory
from app.deploy.errors import DeployError
from app.services import app_service

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def sf(test_engine):
    return make_session_factory(test_engine)


async def _mk_actor(sf, uid: int) -> int:
    """按指定 id 落一个账号：app_project.owner_user_id 的 FK 指向 actor(id)，
    owner 用例要先有这一个账号才插得进应用行。返回真实 id（撞车时复用既有行）。"""
    async with sf() as s:
        aid = (await s.execute(text(
            "INSERT INTO actor (id, nickname, token) VALUES (:i, :n, :t) "
            "ON CONFLICT (id) DO UPDATE SET nickname = EXCLUDED.nickname RETURNING id"),
            {"i": uid, "n": f"pv-{uid}", "t": f"tok-pv-{uid}"})).scalar()
        await s.commit()
    return int(aid)


async def _mk_workspace(sf, root: str, uid: int | None = None) -> int:
    async with sf() as s:
        wid = (await s.execute(text(
            "INSERT INTO workspace (name, root_path, owner_user_id) VALUES ('pv', :p, :o) RETURNING id"),
            {"p": root, "o": uid})).scalar()
        await s.commit()
    return wid


async def _mk_app(sf, wid: int, slug: str, *, stack="vue3-fastapi", uid: int | None = None,
                  dev_port: int | None = 8241, prod_port: int | None = 8240) -> int:
    async with sf() as s:
        aid = (await s.execute(text(
            "INSERT INTO app_project (workspace_id, name, slug, scale, stack, prod_port, dev_port, "
            "owner_user_id) VALUES (:w, '预览应用', :sl, 'small', :st, :pp, :dp, :o) RETURNING id"),
            {"w": wid, "sl": slug, "st": stack, "pp": prod_port, "dp": dev_port, "o": uid})).scalar()
        await s.commit()
    return aid


# ---------- 应用解析（唯一身份来源，§5.1） ----------

async def test_get_app_by_workspace_hits_single_row(sf, tmp_path):
    wid = await _mk_workspace(sf, str(tmp_path / "a"))
    aid = await _mk_app(sf, wid, "pvone")
    app = await app_service.get_app_by_workspace(sf, wid)
    assert app["id"] == aid and app["slug"] == "pvone"
    assert app["preview_url"].endswith(":8241")       # 与 get_app 同形状，地址只来自后端
    assert app["status_dev"] == "stopped"
    assert set(app) == set(await app_service.get_app(sf, aid))   # 可互换使用是规格的硬要求


async def test_get_app_by_workspace_returns_none_without_app(sf, tmp_path):
    wid = await _mk_workspace(sf, str(tmp_path / "b"))
    assert await app_service.get_app_by_workspace(sf, wid) is None


async def test_get_app_by_workspace_filters_by_owner(sf, tmp_path):
    """owner 过滤与 list_apps 同一条判据：别人的应用等于不存在（会话已过 WS 校验，这是第二道闸）。"""
    wid = await _mk_workspace(sf, str(tmp_path / "c"))
    await _mk_actor(sf, 4242)
    await _mk_app(sf, wid, "pvother", uid=4242)
    assert await app_service.get_app_by_workspace(sf, wid, owner_user_id=9999) is None
    assert (await app_service.get_app_by_workspace(sf, wid, owner_user_id=4242))["slug"] == "pvother"


async def test_get_app_by_workspace_fails_closed_on_duplicate(sf, tmp_path):
    """workspace_id 没有 UNIQUE 约束：查到两行时不猜哪一行，fail closed 让调用方不挂载工具。"""
    wid = await _mk_workspace(sf, str(tmp_path / "d"))
    await _mk_app(sf, wid, "pvdup1", dev_port=8243, prod_port=8242)
    await _mk_app(sf, wid, "pvdup2", dev_port=8245, prod_port=8244)
    with pytest.raises(DeployError) as ei:
        await app_service.get_app_by_workspace(sf, wid)
    assert ei.value.status == 409 and "多个应用" in str(ei.value)


# ---------- 工具 schema（§4.1）：三个工具，且没有应用身份参数 ----------

async def test_preview_tool_schemas_have_no_app_identity_params():
    """红线：schema 里出现 app_id / slug / path 就等于把跨应用入口交给模型。"""
    from app.agent.llm import PREVIEW_TOOLS, PREVIEW_TOOL_NAMES
    assert [t["function"]["name"] for t in PREVIEW_TOOLS] == list(PREVIEW_TOOL_NAMES)
    assert PREVIEW_TOOL_NAMES == ("preview_status", "preview_logs", "preview_restart")
    for t in PREVIEW_TOOLS:
        fn = t["function"]
        assert t["type"] == "function" and fn["description"]
        props = fn["parameters"]["properties"]
        assert not ({"app_id", "slug", "path", "workspace_id"} & set(props))
        assert fn["parameters"]["required"] == []


async def test_preview_tool_params_are_only_tail_and_wait_seconds():
    from app.agent.llm import PREVIEW_TOOLS
    by_name = {t["function"]["name"]: t["function"]["parameters"]["properties"] for t in PREVIEW_TOOLS}
    assert by_name["preview_status"] == {}
    assert set(by_name["preview_logs"]) == {"tail"}
    assert set(by_name["preview_restart"]) == {"wait_seconds"}


async def test_base_tools_unchanged_by_preview_feature():
    from app.agent.llm import TOOLS
    assert [t["function"]["name"] for t in TOOLS] == [
        "list_dir", "read_file", "write_file", "edit_file", "ask_user", "todo_write", "memory_write"]
