# tests/test_metrics.py
"""应用资源监控采集与当前值端点（Ticket 001）。

刻意选 workspace_size 做示踪弹——它只依赖文件系统 + DB，不需要 Docker，CI 也能跑。
锁四件事：① dir_size_bytes 递归全量累加、目录不存在返 0；② sample_workspace_size 按应用写
kind=workspace_size 行、目录不存在的应用跳过（不写 0，避免把「没扫到」伪装成「消耗为零」）；
③ GET /metrics/current 返回该 kind 的最近一行 + prod_status，未采过返 null；④ 未知/他人应用 404。

Docker 侧启动准备一律收成替身（同 test_dev_logs 的纪律），本文件不碰真容器。
"""
from pathlib import Path

import pytest
from sqlalchemy import text

from app.db import make_session_factory
from app.deploy import engine as eng_mod
from app.deploy import metrics, provision, sqlrun


async def _noop(*args, **kwargs):
    return None


@pytest.fixture(autouse=True)
def _docks_stubbed(monkeypatch):
    """client 的 lifespan 会跑部署侧启动准备（清孤儿栈 / zcnet / 镜像），本文件收成替身。"""
    monkeypatch.setattr(eng_mod, "cleanup_orphans", _noop)
    monkeypatch.setattr(eng_mod, "stop_all_dev_on_exit", _noop)
    monkeypatch.setattr(eng_mod, "preflight_docker", _noop)
    monkeypatch.setattr(sqlrun, "build_if_missing", _noop)
    monkeypatch.setattr(provision, "ensure_zcnet", _noop)


@pytest.fixture
async def sf(test_engine):
    return make_session_factory(test_engine)


async def _make_app(sf, slug: str, root: Path, *, owner=None, prod_port=8266, dev_port=8267):
    """造 workspace + app_project（conftest 每测试清库，无需手工回滚）。owner 为 actor id 或 None。"""
    async with sf() as s:
        wid = (await s.execute(
            text("INSERT INTO workspace (name, root_path, owner_user_id) VALUES (:n, :r, :o) "
                 "RETURNING id"), {"n": slug, "r": str(root), "o": owner})).scalar()
        aid = (await s.execute(
            text("INSERT INTO app_project (workspace_id, name, slug, scale, stack, prod_port, "
                 "dev_port, owner_user_id) VALUES (:w, :n, :s, 'small', 'vue3-fastapi', :pp, :dp, :o) "
                 "RETURNING id"),
            {"w": wid, "n": slug, "s": slug, "pp": prod_port, "dp": dev_port, "o": owner})).scalar()
        await s.commit()
    return aid


# ---------- 纯函数：目录字节累加 ----------

def test_dir_size_bytes_sums_recursively(tmp_path):
    (tmp_path / "a.txt").write_bytes(b"x" * 100)
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "b.txt").write_bytes(b"y" * 50)
    assert metrics.dir_size_bytes(tmp_path) == 150


def test_dir_size_bytes_missing_dir_returns_zero(tmp_path):
    assert metrics.dir_size_bytes(tmp_path / "nonexistent") == 0


# ---------- 采集：按应用写 workspace_size 行 ----------

async def test_sample_workspace_size_records_bytes(sf, tmp_path):
    (tmp_path / "f.bin").write_bytes(b"z" * 2048)
    aid = await _make_app(sf, "metricA", tmp_path)
    written = await metrics.sample_workspace_size(sf)
    assert written == 1
    async with sf() as s:
        row = (await s.execute(
            text("SELECT kind, value FROM app_metrics WHERE app_id = :i"), {"i": aid}
        )).mappings().first()
    assert row["kind"] == "workspace_size"
    assert float(row["value"]) == 2048


async def test_sample_skips_missing_dir_no_zero_row(sf, tmp_path):
    """目录不存在：跳过而非写 0——0 会被前端画成「消耗为零」的假点。"""
    aid = await _make_app(sf, "metricB", tmp_path / "gone", prod_port=8268, dev_port=8269)
    written = await metrics.sample_workspace_size(sf)
    assert written == 0
    async with sf() as s:
        cnt = (await s.execute(
            text("SELECT count(*) FROM app_metrics WHERE app_id = :i"), {"i": aid}
        )).scalar()
    assert cnt == 0


# ---------- REST 端点：当前值 ----------

async def test_metrics_current_returns_latest(client, tmp_path):
    sf = client.app.state.session_factory
    uid = (await client.get("/api/auth/me")).json()["id"]
    (tmp_path / "f").write_bytes(b"a" * 1024)
    aid = await _make_app(sf, "metricC", tmp_path, owner=uid, prod_port=8270, dev_port=8271)
    await metrics.sample_workspace_size(sf)
    r = await client.get(f"/api/apps/{aid}/metrics/current")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["workspace_size"]["value"] == 1024
    assert body["workspace_size"]["ts"]
    assert body["prod_status"] == "stopped"


async def test_metrics_current_null_when_never_sampled(client, tmp_path):
    sf = client.app.state.session_factory
    uid = (await client.get("/api/auth/me")).json()["id"]
    aid = await _make_app(sf, "metricD", tmp_path, owner=uid, prod_port=8272, dev_port=8273)
    r = await client.get(f"/api/apps/{aid}/metrics/current")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["workspace_size"] is None          # 采过之前是 null，不是 0
    assert body["prod_status"] == "stopped"


async def test_metrics_current_404_for_unknown_app(client):
    r = await client.get("/api/apps/999999/metrics/current")
    assert r.status_code == 404


async def test_metrics_current_404_for_other_owner(two_auth_clients, tmp_path):
    """IDOR 护栏（US-06 前半）：别人的应用 → 404（与不存在同口径，不暴露存在性）；本人 → 200。

    Ticket 003 将在此端点接入创始人 bypass（把 owner_user_id 按 is_founder 置 None），
    最易回归掉的就是这条归属过滤——本用例锁死它。"""
    alice, bob = two_auth_clients
    sf = alice.app.state.session_factory
    alice_uid = (await alice.get("/api/auth/me")).json()["id"]
    aid = await _make_app(sf, "metricE", tmp_path, owner=alice_uid, prod_port=8274, dev_port=8275)
    # bob 访问 alice 的应用：归属过滤命中不到行 → 404
    r_bob = await bob.get(f"/api/apps/{aid}/metrics/current")
    assert r_bob.status_code == 404
    # alice 访问自己的应用：放行 → 200（未采样则 workspace_size 为 null，但状态照回）
    r_alice = await alice.get(f"/api/apps/{aid}/metrics/current")
    assert r_alice.status_code == 200, r_alice.text
    assert r_alice.json()["prod_status"] == "stopped"
