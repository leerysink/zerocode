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


async def _make_app(sf, slug: str, root: Path, *, owner=None, prod_port=8266, dev_port=8267,
                    status_prod="stopped"):
    """造 workspace + app_project（conftest 每测试清库，无需手工回滚）。owner 为 actor id 或 None。"""
    async with sf() as s:
        wid = (await s.execute(
            text("INSERT INTO workspace (name, root_path, owner_user_id) VALUES (:n, :r, :o) "
                 "RETURNING id"), {"n": slug, "r": str(root), "o": owner})).scalar()
        aid = (await s.execute(
            text("INSERT INTO app_project (workspace_id, name, slug, scale, stack, prod_port, "
                 "dev_port, owner_user_id, status_prod) VALUES (:w, :n, :s, 'small', "
                 "'vue3-fastapi', :pp, :dp, :o, :st) RETURNING id"),
            {"w": wid, "n": slug, "s": slug, "pp": prod_port, "dp": dev_port, "o": owner,
             "st": status_prod})).scalar()
        await s.commit()
    return aid


async def _insert_metric(sf, app_id, kind, value, *, age_minutes=0):
    """直插一条指定年龄（分钟前）的指标点，供历史窗口用例造时间轴。"""
    async with sf() as s:
        await s.execute(
            text("INSERT INTO app_metrics (app_id, kind, value, ts) "
                 "VALUES (:a, :k, :v, now() - make_interval(mins => :m))"),
            {"a": app_id, "k": kind, "v": value, "m": age_minutes})
        await s.commit()


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


# ---------- REST 端点：历史窗口 ----------

async def test_metrics_history_filters_by_window(client, tmp_path):
    """窗口只截时轴：1h 只要 5min 前的点，24h 连 90min 前的点一起收（按时间升序）。"""
    sf = client.app.state.session_factory
    uid = (await client.get("/api/auth/me")).json()["id"]
    aid = await _make_app(sf, "histA", tmp_path, owner=uid, prod_port=8280, dev_port=8281)
    await _insert_metric(sf, aid, "workspace_size", 100, age_minutes=5)
    await _insert_metric(sf, aid, "workspace_size", 90, age_minutes=90)
    r = await client.get(f"/api/apps/{aid}/metrics/history", params={"window": "1h"})
    assert r.status_code == 200, r.text
    assert [p["value"] for p in r.json()["workspace_size"]] == [100]
    r2 = await client.get(f"/api/apps/{aid}/metrics/history", params={"window": "24h"})
    assert [p["value"] for p in r2.json()["workspace_size"]] == [90, 100]  # 旧的在前
    assert all("ts" in p for p in r2.json()["workspace_size"])


async def test_metrics_history_empty_when_no_points(client, tmp_path):
    sf = client.app.state.session_factory
    uid = (await client.get("/api/auth/me")).json()["id"]
    aid = await _make_app(sf, "histB", tmp_path, owner=uid, prod_port=8282, dev_port=8283)
    r = await client.get(f"/api/apps/{aid}/metrics/history", params={"window": "7d"})
    assert r.status_code == 200, r.text
    assert r.json()["workspace_size"] == []


async def test_metrics_history_rejects_unknown_window(client, tmp_path):
    sf = client.app.state.session_factory
    uid = (await client.get("/api/auth/me")).json()["id"]
    aid = await _make_app(sf, "histC", tmp_path, owner=uid, prod_port=8284, dev_port=8285)
    r = await client.get(f"/api/apps/{aid}/metrics/history", params={"window": "5m"})
    assert r.status_code == 400


async def test_metrics_history_404_for_other_owner(two_auth_clients, tmp_path):
    alice, bob = two_auth_clients
    sf = alice.app.state.session_factory
    alice_uid = (await alice.get("/api/auth/me")).json()["id"]
    aid = await _make_app(sf, "histD", tmp_path, owner=alice_uid, prod_port=8286, dev_port=8287)
    r = await bob.get(f"/api/apps/{aid}/metrics/history", params={"window": "1h"})
    assert r.status_code == 404


# ---------- 创始人可见性（Ticket 003 / US-06 后半） ----------

async def test_metrics_founder_sees_others_app(two_auth_clients, tmp_path):
    """创始人（首注册者）看别人的应用监控 → 200；非创始人只能看自己。"""
    alice, bob = two_auth_clients   # alice 先注册 = 创始人，bob = 普通用户
    assert (await alice.get("/api/auth/me")).json()["is_founder"] is True
    assert (await bob.get("/api/auth/me")).json()["is_founder"] is False
    sf = alice.app.state.session_factory
    bob_uid = (await bob.get("/api/auth/me")).json()["id"]
    aid = await _make_app(sf, "fndA", tmp_path, owner=bob_uid, prod_port=8290, dev_port=8291)
    # 创始人 alice 访问 bob 的应用：current 与 history 都放行
    r_cur = await alice.get(f"/api/apps/{aid}/metrics/current")
    assert r_cur.status_code == 200, r_cur.text
    r_hist = await alice.get(f"/api/apps/{aid}/metrics/history", params={"window": "1h"})
    assert r_hist.status_code == 200, r_hist.text
    # 普通用户 bob 访问自己的应用：仍 200（归属命中）
    r_own = await bob.get(f"/api/apps/{aid}/metrics/current")
    assert r_own.status_code == 200, r_own.text


# ---------- 采集：容器 CPU/内存汇总（Ticket 004） ----------

def test_parse_stats_extracts_cpu_and_mem_bytes():
    out = (
        "zc-web-prod-backend-1\t12.50%\t128.0MiB / 3.84GiB\n"
        "zc-web-prod-frontend-1\t2.50%\t32.0MiB / 3.84GiB\n"
        "unrelated-ctr\t0.00%\t1.5GiB / 3.84GiB\n"
    )
    by_name = {r["name"]: r for r in metrics.parse_stats(out)}
    assert by_name["zc-web-prod-backend-1"]["cpu_pct"] == 12.5
    assert by_name["zc-web-prod-backend-1"]["mem_bytes"] == 128 * 1024 * 1024
    assert by_name["unrelated-ctr"]["mem_bytes"] == int(1.5 * 1024 ** 3)


def test_parse_stats_tolerates_missing_cpu_placeholder():
    # docker 偶尔把不可用的 CPU 列返成 "--"：当不可用（None），不能写成真实的 0% 假点。
    by_name = {r["name"]: r for r in metrics.parse_stats("c1\t--\t10MiB / 1GiB\n")}
    assert by_name["c1"]["cpu_pct"] is None
    assert by_name["c1"]["mem_bytes"] == 10 * 1024 * 1024


async def test_sample_containers_skips_unavailable_kind(sf, tmp_path, monkeypatch):
    """栈有容器但 cpu 瞬时不可用（"--"）：不写 cpu 假点，内存可信则照写（不可用≠0）。"""
    aid = await _make_app(sf, "part", tmp_path, status_prod="running", prod_port=8312, dev_port=8313)
    async def fake_raw(): return "zc-part-prod-backend-1\t--\t64MiB / 1GiB\n"
    monkeypatch.setattr(metrics, "_docker_stats_raw", fake_raw)
    assert await metrics.sample_containers(sf) == 1
    async with sf() as s:
        kinds = (await s.execute(text(
            "SELECT kind FROM app_metrics WHERE app_id = :i"), {"i": aid})).scalars().all()
    assert set(kinds) == {"memory"}


async def test_sample_containers_records_app_aggregate(sf, tmp_path, monkeypatch):
    """一个应用多个常驻容器 → CPU 百分比与内存字节各自求和，汇总成 cpu/memory 两个点（应用级口径）。"""
    aid = await _make_app(sf, "web", tmp_path, status_prod="running", prod_port=8300, dev_port=8301)
    stats_out = (
        "zc-web-prod-backend-1\t10.00%\t100MiB / 1GiB\n"
        "zc-web-prod-frontend-1\t5.00%\t50MiB / 1GiB\n"
    )
    async def fake_raw(): return stats_out
    monkeypatch.setattr(metrics, "_docker_stats_raw", fake_raw)
    assert await metrics.sample_containers(sf) == 1
    async with sf() as s:
        rows = (await s.execute(text(
            "SELECT kind, value FROM app_metrics WHERE app_id = :i"), {"i": aid})).mappings().all()
    d = {r["kind"]: float(r["value"]) for r in rows}
    assert d["cpu"] == 15.0                    # 10 + 5
    assert d["memory"] == 150 * 1024 * 1024    # 100 + 50 MiB


async def test_sample_containers_skips_stopped_app(sf, tmp_path, monkeypatch):
    """只监控 prod 运行态栈：status_prod=stopped 的应用不入查询，不写点。"""
    aid = await _make_app(sf, "off", tmp_path, status_prod="stopped", prod_port=8302, dev_port=8303)
    async def fake_raw(): return "zc-off-prod-backend-1\t9.00%\t10MiB / 1GiB\n"
    monkeypatch.setattr(metrics, "_docker_stats_raw", fake_raw)
    assert await metrics.sample_containers(sf) == 0
    async with sf() as s:
        cnt = (await s.execute(text(
            "SELECT count(*) FROM app_metrics WHERE app_id = :i"), {"i": aid})).scalar()
    assert cnt == 0


async def test_sample_containers_no_rows_leaves_gap(sf, tmp_path, monkeypatch):
    """运行态但 docker stats 无匹配容器（刚 up 未就绪）：本轮不写点，历史自然留洞（US-10）。"""
    aid = await _make_app(sf, "gap", tmp_path, status_prod="running", prod_port=8304, dev_port=8305)
    async def fake_raw(): return ""
    monkeypatch.setattr(metrics, "_docker_stats_raw", fake_raw)
    assert await metrics.sample_containers(sf) == 0
    async with sf() as s:
        cnt = (await s.execute(text(
            "SELECT count(*) FROM app_metrics WHERE app_id = :i"), {"i": aid})).scalar()
    assert cnt == 0


async def test_sample_containers_docker_error_no_crash(sf, tmp_path, monkeypatch):
    """docker 不可用（stats 抛错）：吐掉不崩，本轮返 0，循环下一拍照常。"""
    await _make_app(sf, "boom", tmp_path, status_prod="running", prod_port=8306, dev_port=8307)
    async def fake_raw(): raise RuntimeError("docker daemon down")
    monkeypatch.setattr(metrics, "_docker_stats_raw", fake_raw)
    assert await metrics.sample_containers(sf) == 0


async def test_metrics_current_includes_cpu_memory(client, tmp_path):
    """current 响应形状扩 cpu/memory 两个 kind（同一接缝，前端无需改接口）；需栈处于运行态。"""
    sf = client.app.state.session_factory
    uid = (await client.get("/api/auth/me")).json()["id"]
    aid = await _make_app(sf, "cm", tmp_path, owner=uid, prod_port=8310, dev_port=8311,
                          status_prod="running")
    await _insert_metric(sf, aid, "cpu", 42.0)
    await _insert_metric(sf, aid, "memory", 1234567)
    body = (await client.get(f"/api/apps/{aid}/metrics/current")).json()
    assert body["cpu"]["value"] == 42.0
    assert body["memory"]["value"] == 1234567


# ---------- 采集：应用数据库磁盘（Ticket 005） ----------

async def test_prod_db_sizes_queries_real_instance(sf):
    """直连平台 PG 实例验证 pg_database_size + = ANY 真能拉字节；不存在的库不返。"""
    async with sf() as s:
        dbname = (await s.execute(text("SELECT current_database()"))).scalar()
    sizes = await metrics._prod_db_sizes(sf, [dbname, "no_such_db_xyz_zz"])
    assert sizes[dbname] > 0
    assert "no_such_db_xyz_zz" not in sizes


async def test_sample_db_size_records_present_skips_missing(sf, tmp_path, monkeypatch):
    """prod 库存在的应用写 db_size；未发布（库不存在）的跳过，不写 0 假点。"""
    a1 = await _make_app(sf, "db1", tmp_path / "a1", prod_port=8320, dev_port=8321)
    a2 = await _make_app(sf, "db2", tmp_path / "a2", prod_port=8322, dev_port=8323)
    async def fake_sizes(session_factory, names):
        assert "db1_prod" in names and "db2_prod" in names
        return {"db1_prod": 999999}   # 只有 db1 的 prod 库存在
    monkeypatch.setattr(metrics, "_prod_db_sizes", fake_sizes)
    assert await metrics.sample_db_size(sf) == 1
    async with sf() as s:
        rows = (await s.execute(text("SELECT app_id, value FROM app_metrics WHERE kind='db_size'"))
                ).mappings().all()
    assert len(rows) == 1 and rows[0]["app_id"] == a1 and float(rows[0]["value"]) == 999999
    _ = a2


async def test_sample_db_size_pg_down_no_crash(sf, tmp_path, monkeypatch):
    """PG 不可达：吐掉不崩，本轮返 0。"""
    await _make_app(sf, "boomdb", tmp_path, prod_port=8324, dev_port=8325)
    async def fake_sizes(session_factory, names):
        raise RuntimeError("pg down")
    monkeypatch.setattr(metrics, "_prod_db_sizes", fake_sizes)
    assert await metrics.sample_db_size(sf) == 0


async def test_metrics_current_includes_db_size(client, tmp_path):
    sf = client.app.state.session_factory
    uid = (await client.get("/api/auth/me")).json()["id"]
    aid = await _make_app(sf, "dbz", tmp_path, owner=uid, prod_port=8330, dev_port=8331)
    await _insert_metric(sf, aid, "db_size", 777)
    body = (await client.get(f"/api/apps/{aid}/metrics/current")).json()
    assert body["db_size"]["value"] == 777


# ---------- 停止态语义 + 过期清理（Ticket 006） ----------

async def test_current_cpu_memory_null_when_stopped(client, tmp_path):
    """停止态：即使库里有旧 cpu/memory 点，current 也归 None（不把几小时前残留当“当前”）。"""
    sf = client.app.state.session_factory
    uid = (await client.get("/api/auth/me")).json()["id"]
    aid = await _make_app(sf, "stopd", tmp_path, owner=uid, prod_port=8340, dev_port=8341,
                          status_prod="stopped")
    await _insert_metric(sf, aid, "cpu", 30.0)
    await _insert_metric(sf, aid, "memory", 111)
    await _insert_metric(sf, aid, "workspace_size", 222)
    await _insert_metric(sf, aid, "db_size", 333)
    body = (await client.get(f"/api/apps/{aid}/metrics/current")).json()
    assert body["cpu"] is None
    assert body["memory"] is None
    assert body["prod_status"] == "stopped"
    # 磁盘/db 与运行态无关：停了照回真实占用
    assert body["workspace_size"]["value"] == 222
    assert body["db_size"]["value"] == 333


async def test_prune_expired_deletes_old_keeps_recent(sf, tmp_path):
    """一条硬线剪尾：超过保留天数的旧点被删，窗口内的保留。"""
    aid = await _make_app(sf, "prune", tmp_path, prod_port=8350, dev_port=8351)
    await _insert_metric(sf, aid, "cpu", 1, age_minutes=60 * 24 * 40)   # 40 天前，超 30 天
    await _insert_metric(sf, aid, "cpu", 2, age_minutes=60 * 5)          # 5 小时前，窗内
    deleted = await metrics.prune_expired(sf, days=30)
    assert deleted == 1
    async with sf() as s:
        vals = (await s.execute(text(
            "SELECT value FROM app_metrics WHERE app_id = :i"), {"i": aid})).scalars().all()
    assert [float(v) for v in vals] == [2]


def test_retention_days_falls_back_on_invalid(monkeypatch):
    monkeypatch.delenv("ZEROCODE_METRICS_RETENTION_DAYS", raising=False)
    assert metrics._retention_days() == metrics.DEFAULT_RETENTION_DAYS
    monkeypatch.setenv("ZEROCODE_METRICS_RETENTION_DAYS", "7")
    assert metrics._retention_days() == 7
    monkeypatch.setenv("ZEROCODE_METRICS_RETENTION_DAYS", "abc")   # 非法回退默认
    assert metrics._retention_days() == metrics.DEFAULT_RETENTION_DAYS
    monkeypatch.setenv("ZEROCODE_METRICS_RETENTION_DAYS", "0")     # 非正回退默认
    assert metrics._retention_days() == metrics.DEFAULT_RETENTION_DAYS
