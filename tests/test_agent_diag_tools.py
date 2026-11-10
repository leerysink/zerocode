"""Week 2 只读诊断：release_preflight / db_schema / sqlchange_status。

三条不变式在这里钉住：
- preflight 只读——体检过后工作区一个字节都没变；
- preflight 的判据与 publish 同源（这里直接驱动 prod.preflight_checks）；
- schema / 迁移状态复用既有读法（db_admin_service / sqlrun），不另起一套 SQL。
"""
import contextlib

import pytest

from app.agent.tools import ToolContext, execute_tool
from app.deploy import engine, prod, sqlrun
from app.services import db_admin_service

APP = {"id": 7, "slug": "demo", "stack": "vue3-fastapi", "current_version": "b1"}


class Recorder:
    def __init__(self):
        self.sent = []

    async def __call__(self, ev):
        self.sent.append(ev)


def make_ctx(tmp_path):
    return ToolContext(root=tmp_path, session_id=1, session_factory=None, send=Recorder())


@pytest.fixture
def wired(monkeypatch, tmp_path):
    """把应用身份与落盘位置钉到 tmp_path；DB 面全部替身（本文件只验工具语义）。"""
    async def fake_fetch_app(ctx):
        return dict(APP)

    async def fake_root(sf, app):
        return tmp_path

    async def fake_cfg(sf):
        return {}

    async def no_drift(sf, root_, slug):
        return {"ok": True}   # parity_block_message 只认 ok=True

    monkeypatch.setattr("app.agent.preview_tools.fetch_app", fake_fetch_app)
    monkeypatch.setattr(engine, "_app_root", fake_root)
    monkeypatch.setattr(prod, "deploy_cfg", fake_cfg)
    monkeypatch.setattr(prod, "verify_dev_matches_files", no_drift)
    return tmp_path


def _clean_app(root):
    (root / "app.json").write_text("{}", encoding="utf-8")
    (root / "sqlchange").mkdir(exist_ok=True)


# ---------- release_preflight ----------

@pytest.mark.asyncio
async def test_preflight_separates_blocking_from_self_fixable(wired):
    root = wired
    _clean_app(root)
    (root / "sqlchange" / "bad-name.sql").write_text("select 1;", encoding="utf-8")
    (root / "frontend").mkdir()
    (root / "frontend" / "package.json").write_text(
        '{"scripts": {"build": "vue-tsc -b && vite build"}}', encoding="utf-8")
    r = await execute_tool(make_ctx(root), "release_preflight", {})
    assert r["ok"] is True and r["ready"] is False
    assert [b["code"] for b in r["blocking"]] == ["sqlchange_name"]
    assert {f["code"] for f in r["self_fixable"]} == {"prod_proxy", "build_script"}
    assert "发布时平台会自动补齐" in r["self_fixable"][0]["message"]


@pytest.mark.asyncio
async def test_preflight_never_touches_the_workspace(wired):
    """只读体检的核心不变式：修复项只报告，不落盘。"""
    root = wired
    _clean_app(root)
    (root / "frontend").mkdir()
    (root / "frontend" / "package.json").write_text(
        '{"scripts": {"build": "vue-tsc -b && vite build"}}', encoding="utf-8")
    await execute_tool(make_ctx(root), "release_preflight", {})
    assert not (root / "frontend" / "nginx.conf").exists()
    assert "vue-tsc" in (root / "frontend" / "package.json").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_preflight_reports_parity_drift_as_blocking(wired, monkeypatch):
    root = wired
    _clean_app(root)

    async def drift(sf, root_, slug):
        return {"drift": True}

    monkeypatch.setattr(prod, "verify_dev_matches_files", drift)
    monkeypatch.setattr(prod, "parity_block_message", lambda vp: "dev 结构与文件不等价：少一张表")
    r = await execute_tool(make_ctx(root), "release_preflight", {})
    assert [b["code"] for b in r["blocking"]] == ["schema_parity"]
    assert "少一张表" in r["blocking"][0]["message"]


@pytest.mark.asyncio
async def test_preflight_reports_destructive_change_with_code(wired, monkeypatch):
    root = wired
    _clean_app(root)

    monkeypatch.setattr(prod, "compute_schema_diff", lambda root_, prev: {"tables": []})
    monkeypatch.setattr(prod, "destructive_message", lambda d: "将删除列 title")
    monkeypatch.setattr(prod, "destructive_changes", lambda d: [{"kind": "drop_column"}])
    r = await execute_tool(make_ctx(root), "release_preflight", {})
    assert r["blocking"][0]["code"] == "schema_destructive"
    assert "title" in r["blocking"][0]["message"]


@pytest.mark.asyncio
async def test_preflight_skip_flags_become_notes(wired, monkeypatch):
    root = wired
    _clean_app(root)

    async def all_skipped(sf):
        return {"deploy.skip_schema_parity": "true", "deploy.skip_schema_destructive": "1"}

    monkeypatch.setattr(prod, "deploy_cfg", all_skipped)
    r = await execute_tool(make_ctx(root), "release_preflight", {})
    assert r["ready"] is True and r["blocking"] == []
    assert any("已跳过" in n for n in r["notes"])


# ---------- db_schema ----------

@pytest.fixture
def fake_db(monkeypatch):
    @contextlib.asynccontextmanager
    async def fake_connect(ctx, slug, env):
        yield {"slug": slug, "env": env}

    async def fake_tables(conn):
        return [{"name": "orders", "type": "BASE TABLE"}]

    async def fake_schema(conn, table):
        return {"columns": [{"name": "id", "type": "bigint", "nullable": False, "default": None}],
                "primary_key": ["id"], "indexes": []}

    calls = []
    monkeypatch.setattr("app.agent.db_tools._connect", fake_connect)
    monkeypatch.setattr(db_admin_service, "tables_of", fake_tables)
    monkeypatch.setattr(db_admin_service, "schema_of", fake_schema)
    return calls


@pytest.mark.asyncio
async def test_db_schema_lists_tables_then_one_table(wired, fake_db):
    root = wired
    r = await execute_tool(make_ctx(root), "db_schema", {})
    assert r["ok"] is True and r["env"] == "dev"
    assert r["tables"] == [{"name": "orders", "type": "BASE TABLE"}]
    d = await execute_tool(make_ctx(root), "db_schema", {"env": "prod", "table": "orders"})
    assert d["ok"] is True and d["table"] == "orders"
    assert d["columns"][0]["name"] == "id" and d["primary_key"] == ["id"]


@pytest.mark.asyncio
async def test_db_schema_rejects_unknown_env(wired, fake_db):
    r = await execute_tool(make_ctx(wired), "db_schema", {"env": "qa"})
    assert r["ok"] is False and "env" in r["error"]


# ---------- sqlchange_status ----------

@pytest.mark.asyncio
async def test_sqlchange_status_tracks_both_envs(wired, monkeypatch):
    root = wired
    (root / "sqlchange").mkdir()
    (root / "sqlchange" / "V20260101_01_a.sql").write_text("--", encoding="utf-8")
    (root / "sqlchange" / "dev").mkdir()
    (root / "sqlchange" / "dev" / "D20260102_01_seed.sql").write_text("--", encoding="utf-8")

    async def fake_cred(sf, slug, env):
        return {"db": f"{slug}_{env}", "user": "u", "password": "p"}

    async def fake_history(url, sql):
        if "_dev" in url:
            return [{"filename": "V20260101_01_a.sql", "success": True},
                    {"filename": "dev/D20260102_01_seed.sql", "success": False}]
        return []

    monkeypatch.setattr("app.deploy.provision.read_cred", fake_cred)
    monkeypatch.setattr(sqlrun, "history_rows", fake_history)
    r = await execute_tool(make_ctx(root), "sqlchange_status", {})
    dev, prod_env = r["envs"]["dev"], r["envs"]["prod"]
    assert (dev["files"], dev["pending"], dev["failed"]) == (2, [], ["dev/D20260102_01_seed.sql"])
    assert dev["credential"] is True
    assert prod_env["files"] == 1
    assert prod_env["pending"] == ["V20260101_01_a.sql"]   # 公共段对 prod 也是待执行
    assert prod_env["credential"] is True


@pytest.mark.asyncio
async def test_sqlchange_status_marks_missing_credential(wired, monkeypatch):
    root = wired
    (root / "sqlchange").mkdir()
    (root / "sqlchange" / "V20260101_01_a.sql").write_text("--", encoding="utf-8")

    async def only_dev(sf, slug, env):
        return {"db": "d", "user": "u", "password": "p"} if env == "dev" else None

    async def fake_history(url, sql):
        return []

    monkeypatch.setattr("app.deploy.provision.read_cred", only_dev)
    monkeypatch.setattr(sqlrun, "history_rows", fake_history)
    r = await execute_tool(make_ctx(root), "sqlchange_status", {})
    assert r["envs"]["prod"]["credential"] is False
    assert "不可知" in r["note"]
