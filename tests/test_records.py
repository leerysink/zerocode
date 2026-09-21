# tests/test_records.py
"""deploy_record 记账服务（规格 04 §4.5）：begin/set_step/finish/list_for/对账/keep-N 清单。

用 test_engine（conftest，走 ZEROCODE_TEST_DATABASE_URL 专用测试库）：
mark_interrupted 扫全表，跑在共享 dev 库会误伤并行会话在途记录。
"""
import pytest
from sqlalchemy import text
from app.db import make_session_factory
from app.deploy import records

pytestmark = pytest.mark.asyncio

SLUG = "zcrec"


async def _mk_app(sf):
    async with sf() as s:
        wid = (await s.execute(text(
            "INSERT INTO workspace (name, root_path) VALUES ('r', 'D:/__zc_rec__') RETURNING id"))).scalar()
        aid = (await s.execute(text(
            "INSERT INTO app_project (workspace_id, name, slug, scale, stack, prod_port, dev_port) "
            "VALUES (:w,'r','zcrec','small','vue3-fastapi', 8990, 8991) RETURNING id"), {"w": wid})).scalar()
        await s.commit()
    return aid


@pytest.fixture(autouse=True)
async def _clean(test_engine):
    sf = make_session_factory(test_engine)
    async with sf() as s:
        await s.execute(text("DELETE FROM deploy_record WHERE app_id IN "
                             "(SELECT id FROM app_project WHERE slug=:s)"), {"s": SLUG})
        await s.execute(text("DELETE FROM app_project WHERE slug=:s"), {"s": SLUG})
        await s.execute(text("DELETE FROM workspace WHERE root_path='D:/__zc_rec__'"))
        await s.commit()
    yield
    async with sf() as s:
        await s.execute(text("DELETE FROM deploy_record WHERE app_id IN "
                             "(SELECT id FROM app_project WHERE slug=:s)"), {"s": SLUG})
        await s.execute(text("DELETE FROM app_project WHERE slug=:s"), {"s": SLUG})
        await s.execute(text("DELETE FROM workspace WHERE root_path='D:/__zc_rec__'"))
        await s.commit()


async def test_lifecycle_begin_step_finish(test_engine):
    sf = make_session_factory(test_engine)
    aid = await _mk_app(sf)
    rid = await records.begin(sf, aid, "publish", "b20260920120000")
    await records.set_step(sf, rid, "build")
    await records.finish(sf, rid, "success", step="record",
                         detail={"images": ["zc-zcrec-frontend:prod-b20260920120000"],
                                 "sqlApplied": ["V20260920_01_init.sql"], "durationMs": 123})
    rows = await records.list_for(sf, aid)
    assert len(rows) == 1 and rows[0]["status"] == "success"
    assert rows[0]["detail"]["durationMs"] == 123
    assert rows[0]["finished_at"] is not None and "step" in rows[0]


async def test_failed_finish_keeps_error_and_step(test_engine):
    sf = make_session_factory(test_engine)
    aid = await _mk_app(sf)
    rid = await records.begin(sf, aid, "publish", "b20260920120100")
    await records.set_step(sf, rid, "migrate")
    await records.finish(sf, rid, "failed", error="sqlrunner 退出码 2")
    r = (await records.list_for(sf, aid))[0]
    assert r["status"] == "failed" and r["step"] == "migrate" and "退出码" in r["error"]


async def test_mark_interrupted(test_engine):
    sf = make_session_factory(test_engine)
    aid = await _mk_app(sf)
    rid = await records.begin(sf, aid, "publish", "b20260920120200")
    n = await records.mark_interrupted(sf)
    assert n >= 1
    assert (await records.list_for(sf, aid))[0]["step"] == "interrupted"
    assert await records.mark_interrupted(sf) == 0      # 幂等：无 running 可标


async def test_versions_for_cleanup(test_engine):
    sf = make_session_factory(test_engine)
    aid = await _mk_app(sf)
    for v in ["b20260920120000", "b20260920130000", "b20260920140000"]:
        rid = await records.begin(sf, aid, "publish", v)
        await records.finish(sf, rid, "success")
    await records.begin(sf, aid, "rollback", "b20260920120000")   # rollback 记录不参与版本清单
    out = await records.versions_for_cleanup(sf, aid, keep=2)
    assert out == ["b20260920120000"]                             # 三个 publish 版本只留最新 2


async def test_cleanup_failed_publish_does_not_consume_keep_quota(test_engine):
    """I-1 裁定：keep 名额只给「成功过」的版本；纯 failed 的版本无条件入清理清单（镜像无用）。"""
    sf = make_session_factory(test_engine)
    aid = await _mk_app(sf)
    for v in ["b20260920150000", "b20260920150100", "b20260920150200"]:
        rid = await records.begin(sf, aid, "publish", v)
        await records.finish(sf, rid, "success")
    rid = await records.begin(sf, aid, "publish", "b20260920150300")
    await records.finish(sf, rid, "failed", error="build 阶段失败")
    out = await records.versions_for_cleanup(sf, aid, keep=3)
    assert out == ["b20260920150300"]        # 失败版不占名额且必清，三个成功版全留


async def test_cleanup_same_version_failed_then_success_counts_as_good(test_engine):
    """同版本双记录取并：先 failed 后重发 success → 该版本归好版本并占 keep 名额；
    另放一个纯 failed 版本，钉住「失败版必清」不因按 status 过滤而被漏掉。"""
    sf = make_session_factory(test_engine)
    aid = await _mk_app(sf)
    rid = await records.begin(sf, aid, "publish", "b20260920160000")
    await records.finish(sf, rid, "success")
    rid = await records.begin(sf, aid, "publish", "b20260920160100")   # 同版本首发失败
    await records.finish(sf, rid, "failed", error="migrate 超时")
    rid = await records.begin(sf, aid, "publish", "b20260920160100")   # 重发成功
    await records.finish(sf, rid, "success")
    rid = await records.begin(sf, aid, "publish", "b20260920160200")
    await records.finish(sf, rid, "failed", error="build 阶段失败")
    out = await records.versions_for_cleanup(sf, aid, keep=1)
    assert "b20260920160100" not in out      # 最新的好版本必须留在服务里
    assert out == ["b20260920160200", "b20260920160000"]


async def test_finish_detail_two_merges_keep_keys(test_engine):
    """控制者裁定钉（字面序列）：begin → finish 不传 detail → finish 再传 detail，读回键完整非 NULL。"""
    sf = make_session_factory(test_engine)
    aid = await _mk_app(sf)
    rid = await records.begin(sf, aid, "publish", "b20260920120300")
    await records.finish(sf, rid, "success")
    await records.finish(sf, rid, "success",
                         detail={"images": ["zc-zcrec-frontend:prod-b20260920120300"],
                                 "sqlApplied": ["V20260920_01_init.sql"], "durationMs": 45})
    d = (await records.list_for(sf, aid))[0]["detail"]
    assert d is not None, "detail 被 NULL 吞噬"
    assert d == {"images": ["zc-zcrec-frontend:prod-b20260920120300"],
                 "sqlApplied": ["V20260920_01_init.sql"], "durationMs": 45}


async def test_finish_detail_merge_survives_null_row(test_engine):
    """COALESCE 修正的牙齿：detail 为 NULL 的行（历史行/外部写入）首并不得被 `NULL || x` 吞成 NULL。"""
    sf = make_session_factory(test_engine)
    aid = await _mk_app(sf)
    rid = await records.begin(sf, aid, "publish", "b20260920120400")
    async with sf() as s:                       # 强制 detail=NULL：模拟裁定前的历史行 / 外部写入
        await s.execute(text("UPDATE deploy_record SET detail = NULL WHERE id = :i"), {"i": rid})
        await s.commit()
    await records.finish(sf, rid, "success")    # 空 detail 收口：不得把行改写成别的东西
    await records.finish(sf, rid, "success",
                         detail={"images": ["img:1"], "sqlApplied": ["V20260920_02_x.sql"]})
    async with sf() as s:                       # 确认库里确实落了对象，而非读取端兜底
        raw = (await s.execute(text(
            "SELECT jsonb_typeof(detail) FROM deploy_record WHERE id = :i"), {"i": rid})).scalar()
    assert raw == "object"
    d = (await records.list_for(sf, aid))[0]["detail"]
    assert d is not None and d["images"] == ["img:1"] and d["sqlApplied"] == ["V20260920_02_x.sql"]


async def test_finish_without_detail_keeps_accumulated_detail(test_engine):
    """收口时不传 detail：已累积键不得被覆盖/清空（`CASE WHEN :dt='{}' THEN detail`）。"""
    sf = make_session_factory(test_engine)
    aid = await _mk_app(sf)
    rid = await records.begin(sf, aid, "publish", "b20260920120500")
    await records.finish(sf, rid, "success", detail={"images": ["img:1"], "durationMs": 7})
    await records.finish(sf, rid, "success", step="record")
    d = (await records.list_for(sf, aid))[0]["detail"]
    assert d == {"images": ["img:1"], "durationMs": 7}
