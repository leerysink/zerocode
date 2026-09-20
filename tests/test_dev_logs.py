# tests/test_dev_logs.py
"""预览日志链路：DeployHub 环形缓冲 → engine 容器日志解析/归并 → GET /api/apps/{id}/dev/logs。

Docker 侧一律替身（真调由 test_engine.py 的 needs_docker 用例负责）；本文件只验三件事：
① 步骤行进了内存环、能按 seq 增量取、按应用分槽且不越界；② compose logs 的时间戳与服务名前缀
被正确切开并按时间归并；③ 端点**绝不因为「日志拿不到」而 500**——Docker 没开时照样返回平台
步骤行 + 中文 note（那正是应用起不来时唯一看得见的线索）。
"""
import shutil
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import text

from app.api.dev_logs import MAX_TAIL
from app.db import make_session_factory
from app.deploy import engine as eng_mod
from app.deploy import provision, sqlrun
from app.deploy.hub import DeployHub

SLUG = "zclogs"
ROOT = Path("D:/__zc_devlogs__")


async def _noop(*args, **kwargs):
    return None


@pytest.fixture(autouse=True)
def _docks_stubbed(monkeypatch):
    """client 的 lifespan 会跑部署侧启动准备（清孤儿栈 / zcnet / 镜像），本文件一律收成替身。"""
    monkeypatch.setattr(eng_mod, "cleanup_orphans", _noop)
    monkeypatch.setattr(eng_mod, "stop_all_dev_on_exit", _noop)
    monkeypatch.setattr(eng_mod, "preflight_docker", _noop)
    monkeypatch.setattr(sqlrun, "build_if_missing", _noop)
    monkeypatch.setattr(provision, "ensure_zcnet", _noop)


@pytest.fixture
async def sf(test_engine):
    return make_session_factory(test_engine)


@pytest.fixture
async def dev_app(client, sf):
    """测试库里造一个应用（不建工程目录：容器侧被替身接管，平台侧只读 DB 与 hub）。"""
    ROOT.mkdir(parents=True, exist_ok=True)
    async with sf() as s:
        wid = (await s.execute(text("INSERT INTO workspace (name, root_path) VALUES ('logs', :r) "
                                    "RETURNING id"), {"r": str(ROOT)})).scalar()
        aid = (await s.execute(text(
            "INSERT INTO app_project (workspace_id, name, slug, scale, stack, prod_port, dev_port) "
            "VALUES (:w, '日志应用', :sl, 'small', 'vue3-fastapi', 8264, 8265) RETURNING id"),
            {"w": wid, "sl": SLUG})).scalar()
        await s.commit()
    yield aid
    async with sf() as s:
        await s.execute(text("DELETE FROM app_project WHERE id=:i"), {"i": aid})
        await s.execute(text("DELETE FROM workspace WHERE id=:i"), {"i": wid})
        await s.commit()
    shutil.rmtree(ROOT, ignore_errors=True)


def _epoch(iso: str) -> float:
    return datetime.fromisoformat(iso).timestamp()


# ---------- DeployHub 环形缓冲 ----------

def test_hub_buffers_platform_lines():
    hub = DeployHub()
    hub.publish(1, {"app_id": 1, "type": "dev_status", "status": "starting"})   # 非日志事件不入缓冲
    hub.publish(1, {"app_id": 1, "type": "dev_log", "line": "[zerocode] ▶ 启动预览 a（dev）"})
    hub.publish(1, {"app_id": 1, "type": "dev_log", "line": "[zerocode] ✓ 迁移完成"})
    rows = hub.tail_logs(1)
    assert [r["line"] for r in rows] == ["[zerocode] ▶ 启动预览 a（dev）", "[zerocode] ✓ 迁移完成"]
    assert [r["seq"] for r in rows] == [1, 2]
    assert all(r["source"] == "platform" and r["epoch"] > 0 for r in rows)


def test_hub_seq_written_back_for_ws_dedupe():
    """seq 要回写进事件本身：REST 历史与 WS 实时共用一套编号，前端才可能去重。"""
    hub = DeployHub()
    ev = {"app_id": 7, "type": "dev_log", "line": "hello"}
    hub.publish(7, ev)
    assert ev["seq"] == 1
    q = hub.subscribe(7)
    hub.publish(7, {"app_id": 7, "type": "dev_log", "line": "again"})
    assert q.get_nowait()["seq"] == 2


def test_hub_ring_is_bounded_and_per_app():
    hub = DeployHub(log_ring=3)
    for i in range(5):
        hub.publish(1, {"app_id": 1, "type": "dev_log", "line": f"l{i}"})
    hub.publish(2, {"app_id": 2, "type": "dev_log", "line": "别人的应用"})
    assert [r["line"] for r in hub.tail_logs(1)] == ["l2", "l3", "l4"]      # 只留最近 3 条
    assert [r["line"] for r in hub.tail_logs(2)] == ["别人的应用"]            # 分槽隔离，不串读
    assert hub.tail_logs(99) == []                                          # 没日志的应用 = 空，不是异常


def test_hub_tail_since_seq_and_limit():
    hub = DeployHub()
    for i in range(4):
        hub.publish(1, {"app_id": 1, "type": "dev_log", "line": f"l{i}"})
    assert [r["seq"] for r in hub.tail_logs(1, since_seq=2)] == [3, 4]       # 增量取（轮询续读用）
    assert [r["seq"] for r in hub.tail_logs(1, limit=1)] == [4]              # limit 取尾部


# ---------- compose 日志解析与归并 ----------

def test_parse_compose_logs_splits_ts_service_and_text():
    out = ("2026-09-20T14:27:58.123456789Z backend  | 2026-09-20 22:27:58 INFO [zc] 陌如玉后端启动\n"
           "2026-09-20T14:27:59.000123456Z frontend | npm info vite v5 ready in 812 ms\n"
           "\n"
           "Network zc-zclogs-dev  Created\n")
    rows = eng_mod.parse_compose_logs(out, tail=10)
    assert len(rows) == 3                                                    # 空行丢掉
    assert rows[0]["service"] == "backend" and rows[0]["source"] == "container"
    assert rows[0]["line"].endswith("陌如玉后端启动")                          # 消息本体不被前缀污染
    assert rows[1]["epoch"] == pytest.approx(_epoch("2026-09-20T14:27:59.000123+00:00"), abs=1)
    assert rows[2]["line"] == "Network zc-zclogs-dev  Created"               # 无 ` | ` 的行整行进 line
    assert rows[2]["service"] == "" and rows[2]["epoch"] is None
    assert len(eng_mod.parse_compose_logs(out, tail=1)) == 1                 # tail 截尾


def test_parse_compose_logs_keeps_inner_pipe_in_message():
    rows = eng_mod.parse_compose_logs("2026-09-20T14:27:58Z backend | a | b", tail=10)
    assert rows[0]["line"] == "a | b" and rows[0]["service"] == "backend"    # 只按首个分隔符切一次


def test_merge_logs_orders_by_time_and_hides_epoch():
    platform = [{"seq": 1, "ts": "t1", "epoch": 100.0, "source": "platform", "line": "a"},
                {"seq": 2, "ts": "t2", "epoch": 300.0, "source": "platform", "line": "b"}]
    container = [{"ts": "c1", "epoch": 200.0, "source": "container", "service": "backend", "line": "c"},
                 {"ts": "", "epoch": None, "source": "container", "service": "", "line": "no-ts"}]
    merged = eng_mod.merge_logs(platform, container)
    assert [r["line"] for r in merged] == ["a", "c", "b", "no-ts"]           # 时间序 + 无戳行沉底
    assert all("epoch" not in r for r in merged)                              # 内部排序键不外传


async def test_container_logs_degrades_without_docker(monkeypatch, sf, dev_app):
    """Docker 没开 / 命令非零退出 / 栈无输出：一律 ([], 中文提示)，绝不向上抛。"""
    app = {"id": dev_app, "slug": SLUG}

    async def boom(*args, **kwargs):
        raise FileNotFoundError(2, "docker not found")

    monkeypatch.setattr(eng_mod, "docker", boom)
    rows, note = await eng_mod.container_logs(sf, app)
    assert rows == [] and "容器日志暂不可用" in note and "docker" in note.lower()

    async def nonzero(*args, **kwargs):
        return 17, "Error response from daemon: No such container"

    monkeypatch.setattr(eng_mod, "docker", nonzero)
    rows, note = await eng_mod.container_logs(sf, app)
    assert rows == [] and "17" in note

    async def empty(*args, **kwargs):
        return 0, ""

    monkeypatch.setattr(eng_mod, "docker", empty)
    rows, note = await eng_mod.container_logs(sf, app)
    assert rows == [] and "暂无容器输出" in note


async def test_container_logs_asks_compose_for_tailed_timestamps(monkeypatch, sf, dev_app):
    """docker 命令形态是契约：logs --no-color --timestamps --tail N（有时间戳才能与步骤行归并）。"""
    calls = []

    async def fake(*args, cwd=None, on_line=None, timeout=600):
        calls.append({"args": args, "cwd": cwd, "timeout": timeout})
        return 0, "2026-09-20T14:27:58.000000000Z backend | hello"

    monkeypatch.setattr(eng_mod, "docker", fake)
    rows, note = await eng_mod.container_logs(sf, {"id": dev_app, "slug": SLUG}, tail=42)
    args = calls[0]["args"]
    assert args[:2] == ("compose", "-f") and "logs" in args
    assert "--timestamps" in args and "--no-color" in args
    assert args[args.index("--tail") + 1] == "42"
    assert calls[0]["timeout"] == 60                                  # 读日志不许把请求吊死
    assert note == "" and rows[0]["line"] == "hello"


# ---------- REST 端点 ----------

async def test_dev_logs_endpoint_merges_both_sources(client, dev_app, monkeypatch):
    hub = client.app.state.deploy_hub
    hub.publish(dev_app, {"app_id": dev_app, "type": "dev_log", "line": "[zerocode] ✓ 预览已就绪"})

    captured = {}

    async def fake_container(sf, app, tail=200):
        captured["slug"] = app["slug"]
        captured["tail"] = tail
        return [{"source": "container", "service": "backend", "ts": "2026-09-20T14:27:58+00:00",
                 "epoch": _epoch("2026-09-20T14:27:58+00:00"), "line": "Uvicorn running"}], ""

    monkeypatch.setattr(eng_mod, "container_logs", fake_container)
    r = await client.get(f"/api/apps/{dev_app}/dev/logs", params={"tail": 50})
    assert r.status_code == 200, r.text
    body = r.json()
    assert captured == {"slug": SLUG, "tail": 50}                     # tail 透传，应用取的就是这一条
    lines = body["lines"]
    assert [l["source"] for l in lines] == ["container", "platform"]  # 两路来源按时间归并
    assert lines[1]["line"] == "[zerocode] ✓ 预览已就绪" and lines[1]["seq"] >= 1
    assert lines[1]["ts"] and "epoch" not in lines[1]                 # 人看的时间有，内部键不外传
    assert body["status_dev"] == "stopped" and body["url"] == "http://localhost:8265"
    assert body["last_seq"] == lines[1]["seq"]


async def test_dev_logs_endpoint_fail_soft_when_docker_missing(client, dev_app, monkeypatch):
    hub = client.app.state.deploy_hub
    hub.publish(dev_app, {"app_id": dev_app, "type": "dev_log", "line": "[zerocode] ✗ 启动预览失败"})

    async def unavailable(sf, app, tail=200):
        return [], "容器日志暂不可用：Docker 未启动或不可用"

    monkeypatch.setattr(eng_mod, "container_logs", unavailable)
    r = await client.get(f"/api/apps/{dev_app}/dev/logs")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["note"] == "容器日志暂不可用：Docker 未启动或不可用"
    assert [l["line"] for l in body["lines"]] == ["[zerocode] ✗ 启动预览失败"]


async def test_dev_logs_endpoint_clamps_tail_and_filters_since(client, dev_app, monkeypatch):
    hub = client.app.state.deploy_hub
    for i in range(3):
        hub.publish(dev_app, {"app_id": dev_app, "type": "dev_log", "line": f"l{i}"})
    seen = {}

    async def fake_container(sf, app, tail=200):
        seen["tail"] = tail
        return [], ""

    monkeypatch.setattr(eng_mod, "container_logs", fake_container)
    r = await client.get(f"/api/apps/{dev_app}/dev/logs", params={"tail": 999999})
    assert seen["tail"] == MAX_TAIL                                   # 上限夹住，不连锅端
    last = r.json()["last_seq"]
    r2 = await client.get(f"/api/apps/{dev_app}/dev/logs", params={"since_seq": last - 1})
    assert [l["line"] for l in r2.json()["lines"]] == ["l2"]           # 只要 seq 更新的
    await client.get(f"/api/apps/{dev_app}/dev/logs", params={"tail": 0})
    assert seen["tail"] == 1                                          # 下限夹住


async def test_dev_logs_endpoint_404_for_unknown_app(client):
    r = await client.get("/api/apps/999999/dev/logs")
    assert r.status_code == 404
