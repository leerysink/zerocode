# tests/test_preview_tools.py
"""Agent 预览工具（规格 docs/specs/2026-10-29-agent-preview-tools.md）。

Docker 侧一律替身：本文件验「工具怎么解析应用、怎么整形结果、怎么等待与收口」，
真起栈由 tests/test_engine.py 与手工矩阵负责。
"""
import asyncio
import time

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
# ---------- ctx 替身：preview_tools 只读字段，不 import ToolContext ----------

class Ctx:
    def __init__(self, app=None, *, sf=None, cfg=None, hub=None, tasks=None, cancel=None):
        from app.deploy.hub import DeployHub
        self.app = app
        self.session_factory = sf
        self.cfg = cfg or {}
        self.deploy_hub = hub if hub is not None else DeployHub()   # 真 hub：订阅/环形缓冲都走生产代码
        self.deploy_tasks = tasks if tasks is not None else {}
        self.cancel = cancel or asyncio.Event()
        self.run_state = {}
        self.session_id = 1
        self.sent = []

    async def send(self, ev):
        self.sent.append(ev)


# ---------- resolve_session_app（§5.1 fail closed） ----------

async def test_resolve_session_app_hit_and_miss(sf, tmp_path):
    from app.agent import preview_tools as pt
    wid = await _mk_workspace(sf, str(tmp_path / "r1"))
    aid = await _mk_app(sf, wid, "pvres")
    app, note = await pt.resolve_session_app(sf, wid)
    assert app["id"] == aid and note == ""
    plain = await _mk_workspace(sf, str(tmp_path / "r2"))
    assert await pt.resolve_session_app(sf, plain) == (None, "")
    assert await pt.resolve_session_app(sf, None) == (None, "")


async def test_resolve_session_app_duplicate_fails_closed_with_note(sf, tmp_path):
    """重复绑定：不挂载工具，但要给模型一句能转述给用户的原因（不能静默）。"""
    from app.agent import preview_tools as pt
    wid = await _mk_workspace(sf, str(tmp_path / "r3"))
    await _mk_app(sf, wid, "pvdupA", dev_port=8247, prod_port=8246)
    await _mk_app(sf, wid, "pvdupB", dev_port=8249, prod_port=8248)
    app, note = await pt.resolve_session_app(sf, wid)
    assert app is None and "多个应用" in note


async def test_resolve_session_app_survives_db_error(sf, monkeypatch):
    """解析出错按非应用工作区处理：记 warning，绝不让整轮对话失败。"""
    from app.agent import preview_tools as pt

    async def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(pt.app_service, "get_app_by_workspace", boom)
    assert await pt.resolve_session_app(sf, 1) == (None, "")


async def test_resolve_session_app_splits_404_from_409(sf, monkeypatch):
    """404（探测到行之后应用被删）必须与 409（绑定了多个应用）分岔。

    `get_app_by_workspace` 是「只查 id、再委托 get_app 取行」，两查之间应用可能被另一个标签页删掉；
    把这条也说成「绑定了多个应用」，用户删掉应用后会看到一句莫名其妙的解释。
    """
    from app.agent import preview_tools as pt

    async def gone(*a, **k):
        raise DeployError("应用不存在", status=404)

    monkeypatch.setattr(pt.app_service, "get_app_by_workspace", gone)
    assert await pt.resolve_session_app(sf, 1) == (None, "")


async def test_fetch_app_returns_none_after_app_deleted(sf, tmp_path):
    """运行中应用被删（另一个标签页）：工具拿到 None，据此回「已被删除」而不是 500。"""
    from app.agent import preview_tools as pt
    wid = await _mk_workspace(sf, str(tmp_path / "r4"))
    aid = await _mk_app(sf, wid, "pvgone")
    ctx = Ctx({"id": aid}, sf=sf)
    assert (await pt.fetch_app(ctx))["slug"] == "pvgone"
    async with sf() as s:
        await s.execute(text("DELETE FROM app_project WHERE id=:i"), {"i": aid})
        await s.commit()
    assert await pt.fetch_app(ctx) is None
    assert await pt.fetch_app(Ctx(None, sf=sf)) is None


# ---------- 等待/行数夹取（§5.9） ----------

async def test_wait_seconds_clamped_and_config_driven():
    from app.agent import preview_tools as pt
    assert pt.wait_seconds_of({}, {}) == 240
    assert pt.wait_seconds_of({"agent.preview_wait_seconds": 30}, {}) == 30
    assert pt.wait_seconds_of({}, {"wait_seconds": 5000}) == 900
    assert pt.wait_seconds_of({}, {"wait_seconds": -5}) == 0
    assert pt.wait_seconds_of({}, {"wait_seconds": "abc"}) == 240
    assert pt.wait_seconds_of({"agent.preview_wait_seconds": 0}, {}) == 0     # <=0 = 等到任务结束


async def test_tail_clamped_between_10_and_300():
    from app.agent import preview_tools as pt
    assert pt.tail_of({}, {}) == 80
    assert pt.tail_of({"agent.preview_log_lines": 120}, {}) == 120
    assert pt.tail_of({}, {"tail": 1}) == 10
    assert pt.tail_of({}, {"tail": 9999}) == 300
    assert pt.tail_of({}, {"tail": None}) == 80


# ---------- 日志整形（§6 日志出口收口） ----------

def _row(line, *, source="container", service="backend", ts="2026-10-29T10:00:01+08:00"):
    return {"source": source, "service": service, "ts": ts, "line": line}


async def test_format_log_lines_labels_time_and_source():
    from app.agent import preview_tools as pt
    lines, truncated, redacted = pt.format_log_lines([
        _row("Uvicorn running", ts="2026-10-29T10:00:01+08:00"),
        _row("✓ 迁移完成", source="platform", service="平台"),
    ])
    assert lines[0].startswith("10:00:01 [backend] Uvicorn running")
    assert lines[1].endswith("[平台] ✓ 迁移完成")
    assert truncated is False and redacted == 0


async def test_format_log_lines_redacts_credentials_and_strips_ansi():
    from app.agent import preview_tools as pt
    lines, _, redacted = pt.format_log_lines([
        _row("\x1b[33mDATABASE_URL=postgresql://u:supersecret@db:5432/app\x1b[39m"),
    ])
    assert "\x1b" not in lines[0]
    assert "supersecret" not in lines[0] and "<redacted:" in lines[0]
    assert redacted >= 1


async def test_format_log_lines_caps_each_line_and_total():
    from app.agent import preview_tools as pt
    lines, truncated, _ = pt.format_log_lines([_row("x" * 900)])
    assert len(lines[0]) < 600 and "已截断" in lines[0]
    many = [_row(f"line-{i} " + "y" * 200) for i in range(400)]
    lines, truncated, _ = pt.format_log_lines(many, limit_lines=300, limit_chars=4000)
    assert len(lines) <= 300 and truncated is True
    assert sum(len(l) for l in lines) <= 4000
    assert lines[-1].startswith("10:00:01 [backend] line-399")    # 丢的是最旧的行


async def test_format_log_lines_skipped_blank_body_is_not_truncation():
    """空正文行被跳过不算截断：容器打空行时 compose 给的是 `backend-1 |`，
    parse_compose_logs 会无条件 append，把它算成截断会让 truncated 恒真、沦为噪声。"""
    from app.agent import preview_tools as pt
    lines, truncated, _ = pt.format_log_lines([_row(""), _row("真实内容")])
    assert lines[-1].endswith("真实内容") and truncated is False


async def test_format_log_lines_marks_truncated_when_rows_exceed_limit():
    from app.agent import preview_tools as pt
    rows = [_row(f"l{i}") for i in range(5)]
    lines, truncated, _ = pt.format_log_lines(rows, limit_lines=3)
    assert len(lines) == 3 and truncated is True


# ---------- preview_status（§5.4） ----------

async def test_status_stopped_does_not_probe(sf, tmp_path, monkeypatch):
    from app.agent import preview_tools as pt
    from app.deploy import engine as eng
    wid = await _mk_workspace(sf, str(tmp_path / "s1"))
    aid = await _mk_app(sf, wid, "pvst")
    hits = []

    async def fake_probe(app, timeout=2):
        hits.append(app["id"])
        return True, ""

    monkeypatch.setattr(eng, "probe_dev", fake_probe)
    r = await pt.run_preview_tool(Ctx({"id": aid}, sf=sf), "preview_status", {})
    assert r["ok"] is True and r["status"] == "stopped"
    assert r["healthy"] is False and r["probe"] == "" and "未运行" in r["hint"]
    assert r["url"].endswith(":8241") and r["app"] == {"slug": "pvst", "stack": "vue3-fastapi"}
    assert r["deploy_task"] is False and hits == []          # 非 running 不探针


async def test_status_running_healthy_and_unhealthy(sf, tmp_path, monkeypatch):
    from app.agent import preview_tools as pt
    from app.deploy import engine as eng
    wid = await _mk_workspace(sf, str(tmp_path / "s2"))
    aid = await _mk_app(sf, wid, "pvrun", dev_port=8251, prod_port=8250)
    async with sf() as s:
        await s.execute(text("UPDATE app_project SET status_dev='running' WHERE id=:i"), {"i": aid})
        await s.commit()

    async def ok_probe(app, timeout=2):
        return True, ""

    monkeypatch.setattr(eng, "probe_dev", ok_probe)
    r = await pt.run_preview_tool(Ctx({"id": aid}, sf=sf), "preview_status", {})
    assert r["healthy"] is True and r["probe"] == "" and r["hint"] == ""

    async def bad_probe(app, timeout=2):
        return False, "/ → 403"

    monkeypatch.setattr(eng, "probe_dev", bad_probe)
    r2 = await pt.run_preview_tool(Ctx({"id": aid}, sf=sf), "preview_status", {})
    assert r2["ok"] is True and r2["healthy"] is False        # 应用报错仍是 ok=true（§4.2 语义）
    assert r2["probe"] == "/ → 403" and "preview_logs" in r2["hint"]


async def test_status_reports_pending_deploy_task(sf, tmp_path, monkeypatch):
    from app.agent import preview_tools as pt
    from app.deploy import engine as eng
    wid = await _mk_workspace(sf, str(tmp_path / "s3"))
    aid = await _mk_app(sf, wid, "pvtask", dev_port=8253, prod_port=8252)
    monkeypatch.setattr(eng, "probe_dev", lambda app, timeout=2: asyncio.sleep(0, (True, "")))
    gate = asyncio.Event()
    task = asyncio.create_task(gate.wait())
    ctx = Ctx({"id": aid}, sf=sf, tasks={aid: task})
    r = await pt.run_preview_tool(ctx, "preview_status", {})
    assert r["deploy_task"] is True and "正在启动" in r["hint"]
    gate.set()
    await task


async def test_status_after_app_deleted_is_a_normal_result(sf, tmp_path):
    from app.agent import preview_tools as pt
    wid = await _mk_workspace(sf, str(tmp_path / "s4"))
    aid = await _mk_app(sf, wid, "pvdel", dev_port=8255, prod_port=8254)
    async with sf() as s:
        await s.execute(text("DELETE FROM app_project WHERE id=:i"), {"i": aid})
        await s.commit()
    r = await pt.run_preview_tool(Ctx({"id": aid}, sf=sf), "preview_status", {})
    assert r["ok"] is False and "已被删除" in r["error"]


# ---------- preview_logs（§4.2 / US-10…US-14） ----------

async def test_logs_merges_platform_and_container_lines(sf, tmp_path, monkeypatch):
    from app.agent import preview_tools as pt
    from app.deploy.hub import DeployHub
    wid = await _mk_workspace(sf, str(tmp_path / "l1"))
    aid = await _mk_app(sf, wid, "pvlog", dev_port=8257, prod_port=8256)
    hub = DeployHub()
    hub.publish(aid, {"app_id": aid, "type": "dev_log", "line": "[zerocode] ✓ 预览已就绪"})
    captured = {}

    async def fake_collect(session_factory, app, h, *, tail=200, since_seq=0, container_timeout=60):
        captured.update({"tail": tail, "timeout": container_timeout})
        return {"lines": [{"source": "container", "service": "backend",
                           "ts": "2026-10-29T10:00:02+08:00", "line": "Uvicorn running"},
                          {"source": "platform", "service": "平台",
                           "ts": "2026-10-29T10:00:03+08:00", "line": "[zerocode] ✓ 预览已就绪"}],
                "note": "", "status_dev": "running", "url": "http://x", "last_seq": 1}

    monkeypatch.setattr(pt, "collect_dev_logs", fake_collect)
    ctx = Ctx({"id": aid}, sf=sf, hub=hub, cfg={"agent.preview_log_lines": 42})
    r = await pt.run_preview_tool(ctx, "preview_logs", {})
    assert captured == {"tail": 42, "timeout": 15}         # 工具侧 15s，不吊死整轮对话
    assert r["ok"] is True and r["status"] == "stopped"     # 状态取库里的，不取收集器回声
    assert r["lines"][0].endswith("[backend] Uvicorn running")
    assert r["truncated"] is False and r["note"] == "" and r["redacted"] == 0


async def test_logs_clamps_tail_and_keeps_note(sf, tmp_path, monkeypatch):
    from app.agent import preview_tools as pt
    wid = await _mk_workspace(sf, str(tmp_path / "l2"))
    aid = await _mk_app(sf, wid, "pvlog2", dev_port=8259, prod_port=8258)
    seen = {}

    async def fake_collect(session_factory, app, h, *, tail=200, since_seq=0, container_timeout=60):
        seen["tail"] = tail
        return {"lines": [], "note": "容器日志暂不可用：Docker 未启动", "status_dev": "error",
                "url": "", "last_seq": 0}

    monkeypatch.setattr(pt, "collect_dev_logs", fake_collect)
    r = await pt.run_preview_tool(Ctx({"id": aid}, sf=sf), "preview_logs", {"tail": 99999})
    assert seen["tail"] == 300 and r["note"] == "容器日志暂不可用：Docker 未启动"
    assert r["lines"] == [] and r["ok"] is True


async def test_logs_are_read_only(sf, tmp_path, monkeypatch):
    """US-14：读日志不改状态、不产生部署任务。"""
    from app.agent import preview_tools as pt
    wid = await _mk_workspace(sf, str(tmp_path / "l3"))
    aid = await _mk_app(sf, wid, "pvlog3", dev_port=8261, prod_port=8260)

    async def fake_collect(session_factory, app, h, *, tail=200, since_seq=0, container_timeout=60):
        return {"lines": [], "note": "", "status_dev": "stopped", "url": "", "last_seq": 0}

    monkeypatch.setattr(pt, "collect_dev_logs", fake_collect)
    ctx = Ctx({"id": aid}, sf=sf)
    await pt.run_preview_tool(ctx, "preview_logs", {})
    async with sf() as s:
        st = (await s.execute(text("SELECT status_dev FROM app_project WHERE id=:i"),
                              {"i": aid})).scalar()
    assert st == "stopped" and ctx.deploy_tasks == {}


async def test_logs_note_is_redacted_before_reaching_the_model(sf, tmp_path, monkeypatch):
    """note 是容器/子进程异常原文的透传口：凭据不能顺着它进模型上下文。"""
    from app.agent import preview_tools as pt
    wid = await _mk_workspace(sf, str(tmp_path / "n1"))
    aid = await _mk_app(sf, wid, "pvnote", dev_port=8265, prod_port=8264)

    async def fake_collect(session_factory, app, h, *, tail=200, since_seq=0, container_timeout=60):
        return {"lines": [], "note": "容器日志暂不可用：postgresql://u:supersecret@db:5432/app",
                "status_dev": "error", "url": "", "last_seq": 0}

    monkeypatch.setattr(pt, "collect_dev_logs", fake_collect)
    r = await pt.run_preview_tool(Ctx({"id": aid}, sf=sf), "preview_logs", {})
    assert "supersecret" not in r["note"] and "<redacted:" in r["note"]


async def test_unknown_preview_tool_is_a_normal_result(sf):
    from app.agent import preview_tools as pt
    r = await pt.run_preview_tool(Ctx(None, sf=sf), "preview_nope", {})
    assert r == {"ok": False, "error": "未知预览工具: preview_nope"}


async def test_unexpected_exception_text_is_redacted_and_capped(sf, monkeypatch):
    """宽 except 的兜底文案同样要过闸：第三方异常把凭据拼进 message 时不能原样送给模型。"""
    from app.agent import preview_tools as pt

    async def boom(ctx, args):
        raise RuntimeError("连接失败 postgresql://u:supersecret@db:5432/app " + "x" * 400)

    monkeypatch.setattr(pt, "_status", boom)
    r = await pt.run_preview_tool(Ctx({"id": 1}, sf=sf), "preview_status", {})
    assert r["ok"] is False and "supersecret" not in r["error"]
    assert "<redacted:" in r["error"] and len(r["error"]) <= 200


# ---------- preview_restart：策略表（§5.5） ----------

@pytest.fixture
async def no_docker(monkeypatch):
    """Docker 预检与探针一律替身：本段验接线，真起栈由 test_engine.py 与手工矩阵负责。"""
    from app.deploy import engine as eng

    async def ok():
        return None

    async def probe(app, timeout=2):
        return True, ""

    monkeypatch.setattr(eng, "preflight_docker", ok)
    monkeypatch.setattr(eng, "probe_dev", probe)


@pytest.fixture
async def stopped_app(sf, tmp_path):
    return await _mk_app(sf, await _mk_workspace(sf, str(tmp_path / "rs")), "pvstart")


@pytest.fixture
async def running_app(sf, tmp_path):
    aid = await _mk_app(sf, await _mk_workspace(sf, str(tmp_path / "rr")), "pvrestart",
                        dev_port=8263, prod_port=8262)
    async with sf() as s:
        await s.execute(text("UPDATE app_project SET status_dev='running' WHERE id=:i"), {"i": aid})
        await s.commit()
    return aid


async def _engine_action_catcher(monkeypatch, action_name: str):
    """把 engine.<action> 换成「立刻完成」的替身，返回它收到的调用记录。

    替身像真引擎一样把终态写进 `status_dev`：工具的 `status` 读的是库，不是替身的返回值——
    不写库的替身会让断言变成「替身自说自话」。
    """
    from app.deploy import engine as eng
    seen = []

    async def fake(sf_, app, notify=None):
        seen.append({"slug": app["slug"], "notify": notify})
        await eng._set_status(sf_, app["id"], "running")
        return {"status": "running"}

    monkeypatch.setattr(eng, action_name, fake)
    return seen


async def test_stopped_preview_calls_start_dev_without_confirm(stopped_app, sf, monkeypatch, no_docker):
    from app.agent import preview_tools as pt
    started = await _engine_action_catcher(monkeypatch, "start_dev")
    restart = await _engine_action_catcher(monkeypatch, "restart_dev")
    ctx = Ctx({"id": stopped_app}, sf=sf, cfg={"agent.preview_confirm_running": True})
    r = await pt.run_preview_tool(ctx, "preview_restart", {"wait_seconds": 5})
    assert r["ok"] is True and r["status"] == "running" and r["healthy"] is True
    assert len(started) == 1 and restart == []               # stopped → 启动，不先 down（与应用页一致）
    assert ctx.run_state["preview_restarts"] == 1 and r["restarts_left"] == 2


async def test_running_preview_refuses_unconfirmed_restart(running_app, sf, monkeypatch, no_docker):
    """竞态兜底（§5.6）：确认期间预览变成 running，也不执行未确认的中断。"""
    from app.agent import preview_tools as pt
    started = await _engine_action_catcher(monkeypatch, "restart_dev")
    ctx = Ctx({"id": running_app}, sf=sf, cfg={"agent.preview_confirm_running": True})
    r = await pt.run_preview_tool(ctx, "preview_restart", {})
    assert r["ok"] is False and "请重试并在确认框中选择确认" in r["error"]
    assert started == [] and ctx.deploy_tasks == {}


async def test_running_preview_restarts_when_user_confirmed(running_app, sf, monkeypatch, no_docker):
    from app.agent import preview_tools as pt
    restarted = await _engine_action_catcher(monkeypatch, "restart_dev")
    ctx = Ctx({"id": running_app}, sf=sf, cfg={"agent.preview_confirm_running": True})
    r = await pt.run_preview_tool(ctx, "preview_restart", {}, confirmed=True)
    assert r["ok"] is True and len(restarted) == 1
    assert restarted[0]["notify"] == ctx.deploy_hub.publish  # 进度接到同一个 DeployHub（US-24）


async def test_confirm_switch_off_skips_the_confirm_gate(running_app, sf, monkeypatch, no_docker):
    from app.agent import preview_tools as pt
    restarted = await _engine_action_catcher(monkeypatch, "restart_dev")
    ctx = Ctx({"id": running_app}, sf=sf, cfg={"agent.preview_confirm_running": False})
    r = await pt.run_preview_tool(ctx, "preview_restart", {})
    assert r["ok"] is True and len(restarted) == 1


async def test_existing_starting_task_is_observed_not_respawned(stopped_app, sf, monkeypatch, no_docker):
    """US-16：用户刚点过启动，工具等同一个任务，绝不双起一个栈。"""
    from app.agent import preview_tools as pt
    from app.deploy import engine as eng
    started = await _engine_action_catcher(monkeypatch, "start_dev")
    async with sf() as s:
        await s.execute(text("UPDATE app_project SET status_dev='starting' WHERE id=:i"),
                        {"i": stopped_app})
        await s.commit()
    gate = asyncio.Event()

    async def user_task():
        await gate.wait()
        return {"status": "running"}

    task = asyncio.create_task(user_task())
    ctx = Ctx({"id": stopped_app}, sf=sf, tasks={stopped_app: task})
    r = await pt.run_preview_tool(ctx, "preview_restart", {"wait_seconds": 1})
    assert started == [] and r["ok"] is False and r["status"] == "starting"
    assert ctx.run_state.get("preview_restarts") is None     # 观察现有任务不计次数
    gate.set()
    await task


async def test_restart_cap_still_observes_an_existing_task(stopped_app, sf, monkeypatch, no_docker):
    """§5.5 / §5.7：额度用尽时照样观察现有的 starting 任务。

    上限管的是「模型又起了一次栈」，而观察一次都没起栈——把闸排在并发判定之前（修复前），
    这里会回「已启动/重启预览 1 次」而不是等同一个任务，模型只剩 preview_status 一条路，
    容易改判成「再试一次」。
    """
    from app.agent import preview_tools as pt
    from app.deploy import engine as eng
    started = await _engine_action_catcher(monkeypatch, "start_dev")
    preflight = []

    async def counting_preflight():
        preflight.append(1)

    monkeypatch.setattr(eng, "preflight_docker", counting_preflight)
    async with sf() as s:
        await s.execute(text("UPDATE app_project SET status_dev='starting' WHERE id=:i"),
                        {"i": stopped_app})
        await s.commit()

    async def user_task():
        await eng._set_status(sf, stopped_app, "running")
        return {"status": "running"}

    task = asyncio.create_task(user_task())
    ctx = Ctx({"id": stopped_app}, sf=sf, cfg={"agent.preview_max_restarts": 1},
              tasks={stopped_app: task})
    ctx.run_state["preview_restarts"] = 1          # 额度已用满（left == 0）
    r = await pt.run_preview_tool(ctx, "preview_restart", {"wait_seconds": 5})
    assert r["ok"] is True and r["status"] == "running"    # 返回的是等待结果，不是上限文案
    assert "已启动/重启预览" not in r.get("error", "")
    assert started == [] and preflight == []               # 不 spawn、不预检：不碰别人的栈
    assert ctx.deploy_tasks[stopped_app] is task          # 等的就是那一个任务
    assert ctx.run_state["preview_restarts"] == 1          # 观察不计次
    assert r["restarts_left"] == 0                         # 额度如实报 0，只是不挡观察
    assert ctx.deploy_hub.subs == {}                       # 观察这条出口也退了订
    await task


async def test_other_deploy_task_is_refused_without_spawning(stopped_app, sf, monkeypatch, no_docker):
    """库里状态不是 starting/stopping 却有任务在跑（多半是发布）：拒绝并发，不排队、不双跑。"""
    from app.agent import preview_tools as pt
    started = await _engine_action_catcher(monkeypatch, "start_dev")
    gate = asyncio.Event()
    task = asyncio.create_task(gate.wait())
    ctx = Ctx({"id": stopped_app}, sf=sf, tasks={stopped_app: task})
    r = await pt.run_preview_tool(ctx, "preview_restart", {})
    assert r["ok"] is False and "部署任务进行中" in r["error"] and started == []
    assert ctx.deploy_tasks[stopped_app] is task             # 没有覆盖别人的任务
    gate.set()
    await task


async def test_wait_timeout_returns_starting_and_leaves_task_running(stopped_app, sf, monkeypatch):
    """US-18：等待有界，超时是正常结果——后台任务继续跑完，模型改用 preview_status 观察。"""
    from app.agent import preview_tools as pt
    from app.deploy import engine as eng
    gate = asyncio.Event()

    async def slow(sf_, app, notify=None):
        await eng._set_status(sf_, app["id"], "starting")     # 真引擎一进 start_dev 就落 starting
        await gate.wait()

    monkeypatch.setattr(eng, "preflight_docker", lambda: asyncio.sleep(0))
    monkeypatch.setattr(eng, "start_dev", slow)
    ctx = Ctx({"id": stopped_app}, sf=sf)
    r = await pt.run_preview_tool(ctx, "preview_restart", {"wait_seconds": 1})
    assert r["ok"] is False and r["status"] == "starting"
    assert "不要连续重启" in r["hint"] and "preview_status" in r["hint"]
    assert ctx.deploy_tasks[stopped_app] and not ctx.deploy_tasks[stopped_app].done()
    gate.set()
    await ctx.deploy_tasks[stopped_app]


async def test_chat_cancel_stops_waiting_only(stopped_app, sf, monkeypatch):
    """US-19：点「停止」后工具在一个轮询周期内返回，部署任务不被取消。"""
    from app.agent import preview_tools as pt
    from app.deploy import engine as eng
    gate = asyncio.Event()

    async def slow(sf_, app, notify=None):
        await eng._set_status(sf_, app["id"], "starting")
        await gate.wait()

    monkeypatch.setattr(eng, "preflight_docker", lambda: asyncio.sleep(0))
    monkeypatch.setattr(eng, "start_dev", slow)
    cancel = asyncio.Event()
    ctx = Ctx({"id": stopped_app}, sf=sf, cancel=cancel)
    task = asyncio.create_task(pt.run_preview_tool(ctx, "preview_restart", {"wait_seconds": 60}))
    await asyncio.sleep(0.8)
    cancel.set()
    r = await asyncio.wait_for(task, 2)
    assert r["ok"] is True and r["cancelled"] is True and "用户已停止本轮" in r["hint"]
    assert r["status"] == "starting"
    assert not ctx.deploy_tasks[stopped_app].done()          # 预览仍在后台继续启动
    gate.set()
    await ctx.deploy_tasks[stopped_app]


async def test_task_failure_is_shaped_with_status_and_logs(stopped_app, sf, monkeypatch):
    """US-20：DeployError 原文带出 + 状态 + 最近日志，模型不必再猜。

    状态由「引擎」自己写库（真 start_dev 就是这么收口的），工具侧每次终态都重新读一遍应用行，
    所以断言的是「读到什么回什么」，不是替身硬写的字面量。
    """
    from app.agent import preview_tools as pt
    from app.deploy import engine as eng
    from app.deploy.errors import DeployError

    async def preflight_ok():
        return None

    async def boom(sf_, app, notify=None):
        notify(app["id"], {"app_id": app["id"], "type": "dev_log",
                           "line": "[zerocode] ✗ 迁移失败，dev 栈不启动"})
        await eng._set_status(sf_, app["id"], "error")
        raise DeployError("dev 迁移失败（sqlrunner 退出码 1），详情见迁移日志")

    monkeypatch.setattr(eng, "preflight_docker", preflight_ok)
    monkeypatch.setattr(eng, "start_dev", boom)
    ctx = Ctx({"id": stopped_app}, sf=sf)
    r = await pt.run_preview_tool(ctx, "preview_restart", {"wait_seconds": 5})
    assert r["ok"] is False and r["status"] == "error"
    assert "sqlrunner 退出码 1" in r["error"]
    assert any("迁移失败" in line for line in r["logs"])


async def test_non_deploy_error_gets_a_prefix(stopped_app, sf, monkeypatch):
    from app.agent import preview_tools as pt
    from app.deploy import engine as eng

    async def preflight_ok():
        return None

    async def crash(sf_, app, notify=None):
        raise ValueError("unexpected")

    monkeypatch.setattr(eng, "preflight_docker", preflight_ok)
    monkeypatch.setattr(eng, "start_dev", crash)
    r = await pt.run_preview_tool(Ctx({"id": stopped_app}, sf=sf), "preview_restart",
                                  {"wait_seconds": 5})
    assert r["ok"] is False and r["error"] == "预览任务异常: unexpected"


async def test_confirm_window_status_change_falls_back_to_start(running_app, sf, monkeypatch, no_docker):
    """§5.5：确认期间预览从 running 变成 stopped → 确认通过后按启动执行，不再问第二次。"""
    from app.agent import preview_tools as pt
    from app.deploy import engine as eng
    started = await _engine_action_catcher(monkeypatch, "start_dev")
    restarted = await _engine_action_catcher(monkeypatch, "restart_dev")
    async with sf() as s:
        await s.execute(text("UPDATE app_project SET status_dev='stopped' WHERE id=:i"),
                        {"i": running_app})
        await s.commit()
    ctx = Ctx({"id": running_app}, sf=sf, cfg={"agent.preview_confirm_running": True})
    r = await pt.run_preview_tool(ctx, "preview_restart", {"wait_seconds": 5}, confirmed=True)
    assert r["ok"] is True and len(started) == 1 and restarted == []


async def test_restart_cap_refuses_without_asking_the_user(stopped_app, sf, monkeypatch):
    """US-22：到限直接返回并带上次数；观察现有任务不占用额度。"""
    from app.agent import preview_tools as pt
    from app.deploy import engine as eng
    seen = []

    async def preflight_ok():
        return None

    async def quick(sf_, app, notify=None):
        seen.append(1)
        return {"status": "running"}

    monkeypatch.setattr(eng, "preflight_docker", preflight_ok)
    monkeypatch.setattr(eng, "start_dev", quick)
    ctx = Ctx({"id": stopped_app}, sf=sf, cfg={"agent.preview_max_restarts": 2})
    ctx.run_state["preview_restarts"] = 2
    r = await pt.run_preview_tool(ctx, "preview_restart", {})
    assert r["ok"] is False and "已启动/重启预览 2 次" in r["error"] and r["restarts_left"] == 0
    assert seen == []


async def test_restart_cap_zero_means_unlimited(stopped_app, sf, monkeypatch, no_docker):
    from app.agent import preview_tools as pt
    from app.deploy import engine as eng

    async def quick(sf_, app, notify=None):
        await eng._set_status(sf_, app["id"], "running")
        return {"status": "running"}

    monkeypatch.setattr(eng, "start_dev", quick)
    ctx = Ctx({"id": stopped_app}, sf=sf, cfg={"agent.preview_max_restarts": 0})
    ctx.run_state["preview_restarts"] = 9
    r = await pt.run_preview_tool(ctx, "preview_restart", {"wait_seconds": 5})
    assert r["ok"] is True and r["restarts_left"] == -1      # -1 = 不限


async def test_docker_preflight_failure_registers_no_task(stopped_app, sf, monkeypatch):
    """US-23：预检失败给出中文引导（与 REST 的 503 同源），且不登记后台任务。"""
    from app.agent import preview_tools as pt
    from app.deploy import engine as eng
    from app.deploy.errors import DeployError

    async def down():
        raise DeployError("Docker 未启动或不可用：请打开 Docker Desktop（设置页「部署环境」可一键启动）",
                          status=503)

    monkeypatch.setattr(eng, "preflight_docker", down)
    ctx = Ctx({"id": stopped_app}, sf=sf)
    r = await pt.run_preview_tool(ctx, "preview_restart", {})
    assert r["ok"] is False and "Docker" in r["error"]
    assert ctx.deploy_tasks == {}


async def test_progress_forwards_only_step_lines_and_status_changes(
        stopped_app, sf, monkeypatch, no_docker):
    """US-17：pip/npm 的成百上千行容器输出不进聊天进度（那属于 preview_logs）。"""
    from app.agent import preview_tools as pt
    from app.deploy import engine as eng

    async def with_steps(sf_, app, notify=None):
        notify(app["id"], {"app_id": app["id"], "type": "dev_log",
                           "line": "[zerocode] → 执行 sqlchange 迁移（dev 库 pv_dev）"})
        notify(app["id"], {"app_id": app["id"], "type": "dev_log", "line": "Collecting fastapi==0.115"})
        notify(app["id"], {"app_id": app["id"], "type": "dev_status", "status": "starting"})
        await eng._set_status(sf_, app["id"], "running")
        notify(app["id"], {"app_id": app["id"], "type": "dev_log", "line": "[zerocode] ✓ 预览已就绪"})
        return {"status": "running"}

    monkeypatch.setattr(eng, "start_dev", with_steps)
    ctx = Ctx({"id": stopped_app}, sf=sf)
    r = await pt.run_preview_tool(ctx, "preview_restart", {"wait_seconds": 5})
    lines = [ev["line"] for ev in ctx.sent if ev["type"] == "tool_progress"]
    assert all(ev["name"] == "preview_restart" for ev in ctx.sent)
    assert any("执行 sqlchange 迁移" in l for l in lines)
    assert any("预览状态：starting" in l for l in lines)
    assert not any("Collecting" in l for l in lines)          # 容器输出不转发
    assert r["ok"] is True
    assert any("预览已就绪" in l for l in r["logs"])          # 全量步骤行仍进结果的 logs


async def test_successful_restart_carries_probe_and_url(stopped_app, sf, monkeypatch):
    from app.agent import preview_tools as pt
    from app.deploy import engine as eng

    async def preflight_ok():
        return None

    async def unreachable(app, timeout=2):
        return False, "ConnectError: [Errno 111] Connection refused"

    async def quick(sf_, app, notify=None):
        await eng._set_status(sf_, app["id"], "running")
        return {"status": "running"}

    monkeypatch.setattr(eng, "preflight_docker", preflight_ok)
    monkeypatch.setattr(eng, "start_dev", quick)
    monkeypatch.setattr(eng, "probe_dev", unreachable)
    r = await pt.run_preview_tool(Ctx({"id": stopped_app}, sf=sf), "preview_restart",
                                  {"wait_seconds": 5})
    assert r["ok"] is True                                    # 探针失败不把动作翻成失败
    assert r["healthy"] is False and "ConnectError" in r["probe"]
    assert r["status"] == "running" and r["url"].endswith(":8241")
    assert r["app"]["slug"] == "pvstart"


# ---------- 并发不变量：退订无泄漏 / 进度节流合并 ----------

async def test_restart_unsubscribes_the_hub_on_every_exit_path(
        stopped_app, running_app, sf, monkeypatch, no_docker):
    """`_restart` 的每一条出口都退订，不在 `DeployHub.subs` 里留孤儿队列。

    `Ctx.deploy_hub` 用的是**真** DeployHub，`unsubscribe` 会把空 list 从 `subs` 里 pop 掉，
    所以 `subs == {}` 就是「一个队列都没漏」的判据——摘掉 finally 里的 unsubscribe 本用例即红。
    覆盖出口：成功、等待超时、聊天取消、Docker 预检失败、spawn 抛 409、观察现有任务、
    并发拒绝、确认竞态兜底、上限拒绝（后三道在订阅之前就 return，一并钉住「闸不许提前订阅」）。
    """
    from app.agent import preview_tools as pt
    from app.deploy import engine as eng
    from app.deploy import tasks as deploy_tasks
    from app.deploy.errors import DeployError

    def leak_free(label, ctx):
        assert ctx.deploy_hub.subs == {}, f"{label}：DeployHub 订阅泄漏"

    def use(action):
        """启动/重启两个动作一起换替身：本用例只验出口路径，不管走的是哪一个（也不留真引擎）。"""
        monkeypatch.setattr(eng, "start_dev", action)
        monkeypatch.setattr(eng, "restart_dev", action)

    def slow(status_written):
        """替身引擎：落一个状态然后挂住，等用例自己放行。"""
        gate = asyncio.Event()

        async def action(sf_, app, notify=None):
            await eng._set_status(sf_, app["id"], status_written)
            await gate.wait()

        return action, gate

    async def success(sf_, app, notify=None):
        notify(app["id"], {"app_id": app["id"], "type": "dev_log",
                           "line": "[zerocode] ✓ 预览已就绪"})
        await eng._set_status(sf_, app["id"], "running")
        return {"status": "running"}

    async def set_status(app_id, value):
        """把 `status_dev` 摆回这一条出口的起点：九条路径共用同一个应用行，不摆回去会互相串味。"""
        async with sf() as s:
            await s.execute(text("UPDATE app_project SET status_dev=:st WHERE id=:i"),
                            {"st": value, "i": app_id})
            await s.commit()

    use(success)
    await set_status(stopped_app, "stopped")
    ctx = Ctx({"id": stopped_app}, sf=sf)
    r = await pt.run_preview_tool(ctx, "preview_restart", {"wait_seconds": 5})
    assert r["ok"] is True
    leak_free("成功", ctx)

    action, gate = slow("starting")
    use(action)
    await set_status(stopped_app, "stopped")
    ctx = Ctx({"id": stopped_app}, sf=sf)
    r = await pt.run_preview_tool(ctx, "preview_restart", {"wait_seconds": 1})
    assert r["ok"] is False and r["status"] == "starting"
    leak_free("等待超时", ctx)
    gate.set()
    await ctx.deploy_tasks[stopped_app]

    action, gate = slow("starting")
    use(action)
    await set_status(stopped_app, "stopped")
    cancel = asyncio.Event()
    cancel.set()                                        # 一进 _wait 就走取消出口
    ctx = Ctx({"id": stopped_app}, sf=sf, cancel=cancel)
    r = await pt.run_preview_tool(ctx, "preview_restart", {"wait_seconds": 60})
    assert r["ok"] is True and r["cancelled"] is True
    leak_free("聊天取消", ctx)
    gate.set()
    await ctx.deploy_tasks[stopped_app]

    async def down():
        raise DeployError("Docker 未启动或不可用", status=503)

    await set_status(stopped_app, "stopped")
    monkeypatch.setattr(eng, "preflight_docker", down)
    ctx = Ctx({"id": stopped_app}, sf=sf)
    r = await pt.run_preview_tool(ctx, "preview_restart", {"wait_seconds": 5})
    assert r["ok"] is False and "Docker" in r["error"]
    leak_free("Docker 预检失败", ctx)
    assert ctx.deploy_tasks == {}

    ctx = Ctx({"id": stopped_app}, sf=sf)
    rivals = []

    async def preflight_that_races():
        # 真 409：预检与登记之间另一条通道（REST）抢先起了同一个应用的任务
        rivals.append(deploy_tasks.spawn(ctx.deploy_tasks, stopped_app, lambda: asyncio.sleep(30)))

    monkeypatch.setattr(eng, "preflight_docker", preflight_that_races)
    r = await pt.run_preview_tool(ctx, "preview_restart", {"wait_seconds": 5})
    assert r["ok"] is False and "部署任务进行中" in r["error"] and len(rivals) == 1
    leak_free("spawn 抛 409", ctx)
    rivals[0].cancel()
    await asyncio.gather(*rivals, return_exceptions=True)
    monkeypatch.setattr(eng, "preflight_docker", lambda: asyncio.sleep(0))

    await set_status(stopped_app, "starting")
    gate = asyncio.Event()
    task = asyncio.create_task(gate.wait())             # 别人起的任务：工具只观察
    ctx = Ctx({"id": stopped_app}, sf=sf, tasks={stopped_app: task})
    r = await pt.run_preview_tool(ctx, "preview_restart", {"wait_seconds": 1})
    assert r["ok"] is False and r["status"] == "starting" and ctx.deploy_tasks[stopped_app] is task
    leak_free("观察现有任务", ctx)
    gate.set()
    await task

    gate = asyncio.Event()
    task = asyncio.create_task(gate.wait())
    ctx = Ctx({"id": running_app}, sf=sf, tasks={running_app: task})
    r = await pt.run_preview_tool(ctx, "preview_restart", {"wait_seconds": 5})
    assert r["ok"] is False and "部署任务进行中" in r["error"]
    leak_free("并发拒绝", ctx)
    gate.set()
    await task

    ctx = Ctx({"id": running_app}, sf=sf, cfg={"agent.preview_confirm_running": True})
    r = await pt.run_preview_tool(ctx, "preview_restart", {"wait_seconds": 5})
    assert r["ok"] is False and "请重试并在确认框中选择确认" in r["error"]
    leak_free("确认竞态兜底", ctx)

    await set_status(stopped_app, "stopped")
    ctx = Ctx({"id": stopped_app}, sf=sf, cfg={"agent.preview_max_restarts": 1})
    ctx.run_state["preview_restarts"] = 1
    r = await pt.run_preview_tool(ctx, "preview_restart", {"wait_seconds": 5})
    assert r["ok"] is False and "已启动/重启预览 1 次" in r["error"]
    leak_free("上限拒绝", ctx)
    assert ctx.deploy_tasks == {}                         # 上限闸也不许起任务


async def test_drain_progress_merges_within_the_window_and_flushes_only_at_the_end():
    """等待期间的节流合并语义（活体证据进仓库）。

    钉三件事：一次排空里多条步骤行**只投最新一条**、`seen` 仍累积全量（供超时/失败结果取用）、
    窗口内再次排空**不重复投递**；再加 `flush=True` 的全量投递与它的条数上限。
    把 250ms 节流改成「每行即投」，本用例即红。
    """
    from app.agent import preview_tools as pt

    def step(i):
        return {"type": "dev_log", "line": f"{pt.STEP_PREFIX} → 第 {i} 步"}

    def line_of(i):
        return f"{pt.STEP_PREFIX} → 第 {i} 步"

    def q_of(events):
        q = asyncio.Queue()
        for ev in events:
            q.put_nowait(ev)
        return q

    ctx = Ctx()
    q = q_of([step(1), {"type": "dev_log", "line": "Collecting fastapi==0.115"},
              step(2), {"type": "dev_status", "status": "starting"}])
    seen: list[str] = []
    last_emit, seen = await pt._drain_progress(ctx, q, 0.0, seen)
    assert [ev["line"] for ev in ctx.sent] == [f"{pt.STEP_PREFIX} 预览状态：starting"]
    assert seen == [line_of(1), line_of(2), f"{pt.STEP_PREFIX} 预览状态：starting"]

    q.put_nowait(step(3))                                # 同一次等待的下一轮：窗口还没过
    kept_emit, seen = await pt._drain_progress(ctx, q, last_emit, seen)
    assert kept_emit == last_emit and len(ctx.sent) == 1  # 窗口内一条都不投、节流时刻不推进
    assert seen[-1] == line_of(3)                        # 但 seen 照收

    await asyncio.sleep(pt.PROGRESS_INTERVAL)            # 窗口过后恢复投递
    q.put_nowait(step(4))
    kept_emit, seen = await pt._drain_progress(ctx, q, kept_emit, seen)
    assert len(ctx.sent) == 2 and ctx.sent[-1]["line"] == line_of(4)

    ctx2 = Ctx()                                         # 收尾：窗口内也全量投递
    _, seen_flush = await pt._drain_progress(ctx2, q_of([step(1), step(2), step(3)]),
                                             time.monotonic(), [], flush=True)
    assert [ev["line"] for ev in ctx2.sent] == [line_of(1), line_of(2), line_of(3)]
    assert seen_flush == [line_of(1), line_of(2), line_of(3)]

    ctx3 = Ctx()                                         # 收尾同样限长：不一次性灌满聊天
    many = q_of([step(i) for i in range(pt.RESTART_LOG_LINES + 5)])
    await pt._drain_progress(ctx3, many, 0.0, [], flush=True)
    assert len(ctx3.sent) == pt.RESTART_LOG_LINES
    assert ctx3.sent[0]["line"] == line_of(5)             # 丢的是最旧的
    assert ctx3.sent[-1]["line"] == line_of(pt.RESTART_LOG_LINES + 4)


# ---------- <preview> 指引（§9）：工具挂载时才注入，按栈补一句热更新语义 ----------

async def test_preview_prompt_carries_the_verify_dont_restart_rules():
    from app.agent import preview_tools as pt
    p = pt.build_preview_prompt("vue3-fastapi")
    assert p.startswith("\n\n<preview>") and p.endswith("</preview>")
    assert "preview_status" in p and "preview_logs" in p and "preview_restart" in p
    assert "healthy 只表示 HTTP 可达" in p          # 不许把「能访问」说成「功能正确」
    assert "不要连续重启" in p
    assert "你没有这些工具" in p                     # 明确动作面边界
    assert "3 次" not in p                          # 次数上限是可配的，提示词不写死数字


async def test_preview_prompt_stack_specific_lines():
    from app.agent import preview_tools as pt
    assert "uvicorn --reload" in pt.build_preview_prompt("vue3-fastapi")
    spring = pt.build_preview_prompt("vue3-springboot")
    assert "必须 preview_restart" in spring and "uvicorn" not in spring
    assert "刷新即可" in pt.build_preview_prompt("static")
    assert "刷新即可" in pt.build_preview_prompt("vue3-node")
    assert "</preview>" in pt.build_preview_prompt("unknown-stack")   # 未知栈仍有基线指引
