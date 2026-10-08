"""工作区记忆服务（ticket 002；spec 故事 1、3、6(service create)、13、14、22 的服务侧起步）。

主靶心 = `recall_memories`：本版关键词起步（真语义留 005、上限与降级收口留 006），
断言集中在「按 workspace 隔离 + enabled 过滤 + 关键词命中数排序 + topK 截断」。
"""
import pytest
from sqlalchemy import text
from app.db import make_session_factory
from app.deploy.errors import DeployError
from app.services import memory_service as mem


async def mk_ws(test_engine, root: str) -> int:
    """建一个工作区并返回 id。name 列是 VARCHAR(100)，不能塞长长的临时路径。"""
    sf = make_session_factory(test_engine)
    async with sf() as s:
        ws = (await s.execute(text(
            "INSERT INTO workspace (name, root_path) VALUES (:n, :p) RETURNING id"),
            {"n": "ws", "p": root})).scalar()
        await s.commit()
    return ws


async def mk_session_in(test_engine, ws: int) -> int:
    sf = make_session_factory(test_engine)
    async with sf() as s:
        sid = (await s.execute(text(
            "INSERT INTO chat_session (workspace_id) VALUES (:w) RETURNING id"), {"w": ws})).scalar()
        await s.commit()
    return sid


# ---------------- 正常路径 ----------------

async def test_create_returns_row_and_list_enabled(test_engine, tmp_path):
    ws = await mk_ws(test_engine, str(tmp_path / "a"))
    sf = make_session_factory(test_engine)
    row = await mem.create(sf, ws, "preference", "导出统一走 pandas 分块", title="导出规范")
    assert row["type"] == "preference"
    assert row["content"] == "导出统一走 pandas 分块"
    assert row["title"] == "导出规范"
    assert row["enabled"] is True
    # 大整数 id 一律按字符串比较，禁 Number() 截断（全局红线）
    assert str(row["workspace_id"]) == str(ws)
    rows = await mem.list_enabled(sf, ws)
    assert [r["content"] for r in rows] == ["导出统一走 pandas 分块"]
    assert str(rows[0]["id"]) == str(row["id"])


async def test_recall_matches_latin_keyword(test_engine, tmp_path):
    ws = await mk_ws(test_engine, str(tmp_path / "a"))
    sf = make_session_factory(test_engine)
    await mem.create(sf, ws, "fact", "订单导出统一走 pandas 分块，避免 OOM")
    await mem.create(sf, ws, "fact", "登录页样式沿用 antd 主题变量")
    hit = await mem.recall_memories(sf, ws, "pandas 导出")
    assert len(hit) == 1
    assert "pandas" in hit[0]["content"]


async def test_recall_matches_chinese_query_without_spaces(test_engine, tmp_path):
    """中文任务文本没有空格分词，靠二元切分也能命中（否则中文工作区等于没有记忆）。"""
    ws = await mk_ws(test_engine, str(tmp_path / "a"))
    sf = make_session_factory(test_engine)
    await mem.create(sf, ws, "preference", "接口字段一律返回驼峰命名")
    hit = await mem.recall_memories(sf, ws, "驼峰字段")
    assert len(hit) == 1
    assert "驼峰" in hit[0]["content"]


async def test_recall_orders_by_hit_count_desc(test_engine, tmp_path):
    ws = await mk_ws(test_engine, str(tmp_path / "a"))
    sf = make_session_factory(test_engine)
    await mem.create(sf, ws, "fact", "pandas 只写一条弱相关")
    await mem.create(sf, ws, "decision", "pandas 分块导出，先定分块大小再上线")
    hit = await mem.recall_memories(sf, ws, "pandas 分块 导出")
    assert [h["type"] for h in hit] == ["decision", "fact"]


async def test_recall_scoped_to_workspace(test_engine, tmp_path):
    ws_a = await mk_ws(test_engine, str(tmp_path / "a"))
    ws_b = await mk_ws(test_engine, str(tmp_path / "b"))
    sf = make_session_factory(test_engine)
    await mem.create(sf, ws_a, "fact", "pandas 分块导出")
    assert await mem.recall_memories(sf, ws_b, "pandas") == []
    assert await mem.list_enabled(sf, ws_b) == []


async def test_recall_via_session_workspace_lookup(test_engine, tmp_path):
    """同 workspace 的两个 session 反查到同一 workspace_id，召回同一条（故事：跨 session 共享）。"""
    ws = await mk_ws(test_engine, str(tmp_path / "a"))
    sf = make_session_factory(test_engine)
    sid1 = await mk_session_in(test_engine, ws)
    sid2 = await mk_session_in(test_engine, ws)
    await mem.create(sf, ws, "pitfall", "pandas 读 xlsx 必须先装 openpyxl")
    w1 = await mem.workspace_id_of_session(sf, sid1)
    w2 = await mem.workspace_id_of_session(sf, sid2)
    assert str(w1) == str(w2) == str(ws)
    for w in (w1, w2):
        hit = await mem.recall_memories(sf, w, "openpyxl")
        assert len(hit) == 1
        assert hit[0]["type"] == "pitfall"


# ---------------- 边界与异常分支 ----------------

async def test_create_rejects_unknown_type(test_engine, tmp_path):
    ws = await mk_ws(test_engine, str(tmp_path / "a"))
    sf = make_session_factory(test_engine)
    with pytest.raises(DeployError) as ei:
        await mem.create(sf, ws, "hint", "内容")
    assert "hint" in str(ei.value)
    assert "preference" in str(ei.value)
    assert await mem.list_enabled(sf, ws) == []  # 报错即未落库


async def test_create_rejects_empty_content(test_engine, tmp_path):
    ws = await mk_ws(test_engine, str(tmp_path / "a"))
    sf = make_session_factory(test_engine)
    with pytest.raises(DeployError):
        await mem.create(sf, ws, "fact", "")
    assert await mem.list_enabled(sf, ws) == []


async def test_create_rejects_whitespace_only_content(test_engine, tmp_path):
    ws = await mk_ws(test_engine, str(tmp_path / "a"))
    sf = make_session_factory(test_engine)
    with pytest.raises(DeployError):
        await mem.create(sf, ws, "fact", "   \n\t  ")
    assert await mem.list_enabled(sf, ws) == []


async def test_create_rejects_none_content(test_engine, tmp_path):
    ws = await mk_ws(test_engine, str(tmp_path / "a"))
    sf = make_session_factory(test_engine)
    with pytest.raises(DeployError):
        await mem.create(sf, ws, "fact", None)


async def test_create_rejects_overlong_title(test_engine, tmp_path):
    """title 列 VARCHAR(200)：越界要给业务错误，不能透传 Postgres 的 DataError。"""
    ws = await mk_ws(test_engine, str(tmp_path / "a"))
    sf = make_session_factory(test_engine)
    with pytest.raises(DeployError):
        await mem.create(sf, ws, "fact", "内容", title="标" * 201)
    assert await mem.list_enabled(sf, ws) == []


async def test_create_rejects_missing_workspace(test_engine, tmp_path):
    sf = make_session_factory(test_engine)
    with pytest.raises(DeployError) as ei:
        await mem.create(sf, 999999999, "fact", "内容")
    assert ei.value.status == 404
    async with sf() as s:
        n = (await s.execute(text("SELECT count(*) FROM workspace_memory"))).scalar()
    assert n == 0


async def test_create_accepts_title_at_exact_limit(test_engine, tmp_path):
    """刚好等于阈值（200 字）必须通过——与 201 的拒绝用例成对。"""
    ws = await mk_ws(test_engine, str(tmp_path / "a"))
    sf = make_session_factory(test_engine)
    row = await mem.create(sf, ws, "fact", "内容", title="标" * 200)
    assert len(row["title"]) == 200


async def test_recall_returns_empty_for_blank_query(test_engine, tmp_path):
    ws = await mk_ws(test_engine, str(tmp_path / "a"))
    sf = make_session_factory(test_engine)
    await mem.create(sf, ws, "fact", "pandas 分块导出")
    assert await mem.recall_memories(sf, ws, "") == []
    assert await mem.recall_memories(sf, ws, "   ") == []
    assert await mem.recall_memories(sf, ws, None) == []


async def test_recall_returns_empty_for_punctuation_only_query(test_engine, tmp_path):
    """只有标点/全角符号的查询切不出词，退化为不注入（对齐故事 14 空块不注）。"""
    ws = await mk_ws(test_engine, str(tmp_path / "a"))
    sf = make_session_factory(test_engine)
    await mem.create(sf, ws, "fact", "pandas 分块导出")
    assert await mem.recall_memories(sf, ws, "！！！***///") == []


async def test_recall_skips_disabled_memory(test_engine, tmp_path):
    ws = await mk_ws(test_engine, str(tmp_path / "a"))
    sf = make_session_factory(test_engine)
    await mem.create(sf, ws, "fact", "pandas 分块导出")
    off = await mem.create(sf, ws, "fact", "pandas 已停用的旧结论", enabled=False)
    hit = await mem.recall_memories(sf, ws, "pandas")
    assert len(hit) == 1
    assert str(hit[0]["id"]) != str(off["id"])
    assert str(off["id"]) not in [str(r["id"]) for r in await mem.list_enabled(sf, ws)]


async def test_recall_truncates_at_exact_top_k(test_engine, tmp_path):
    ws = await mk_ws(test_engine, str(tmp_path / "a"))
    sf = make_session_factory(test_engine)
    for i in range(3):
        await mem.create(sf, ws, "fact", f"pandas 第{i}条")
    assert len(await mem.recall_memories(sf, ws, "pandas", top_k=2)) == 2  # 恰好截断
    assert len(await mem.recall_memories(sf, ws, "pandas", top_k=3)) == 3  # 刚好等于命中数
    assert len(await mem.recall_memories(sf, ws, "pandas", top_k=4)) == 3  # 超过命中数不多返回


async def test_recall_non_positive_top_k_means_unlimited(test_engine, tmp_path):
    """沿用本项目「<=0 即不限制」的参数约定（见 agent.max_tool_rounds）。"""
    ws = await mk_ws(test_engine, str(tmp_path / "a"))
    sf = make_session_factory(test_engine)
    for i in range(3):
        await mem.create(sf, ws, "fact", f"pandas 第{i}条")
    assert len(await mem.recall_memories(sf, ws, "pandas", top_k=0)) == 3
    assert len(await mem.recall_memories(sf, ws, "pandas", top_k=-1)) == 3


async def test_recall_ties_prefer_newer_memory(test_engine, tmp_path):
    """同分时取哪几条必须是确定的（id 降序 = 新优先），否则 topK 截断会退化成扫描顺序的随机结果。"""
    ws = await mk_ws(test_engine, str(tmp_path / "a"))
    sf = make_session_factory(test_engine)
    ids = [str((await mem.create(sf, ws, "fact", f"pandas 第{i}条做法"))["id"]) for i in range(5)]
    hit = await mem.recall_memories(sf, ws, "pandas", top_k=2)
    # 五条同分（都只命中 pandas）→ 取最新两条，且按 id 降序给出
    assert [str(h["id"]) for h in hit] == sorted(ids, key=int, reverse=True)[:2]


async def test_content_with_special_chars_roundtrip(test_engine, tmp_path):
    """引号/换行/全角/连字符斜杠原样入库原样取出（注入格式不能用分隔符污染内容）。"""
    ws = await mk_ws(test_engine, str(tmp_path / "a"))
    sf = make_session_factory(test_engine)
    tricky = '他说“要用 double quotes”，然后\n换行；路径 docs/a-b.md 与 <memories> 标签'
    row = await mem.create(sf, ws, "fact", tricky)
    assert row["content"] == tricky
    listed = await mem.list_enabled(sf, ws)
    assert listed[0]["content"] == tricky


async def test_recall_uses_stored_id_as_string_safe(test_engine, tmp_path):
    """id 走 BIGSERIAL：返回类型必须是 int，且字符串化后与库内文本一致（不被截断）。"""
    ws = await mk_ws(test_engine, str(tmp_path / "a"))
    sf = make_session_factory(test_engine)
    row = await mem.create(sf, ws, "fact", "pandas 分块导出")
    assert isinstance(row["id"], int)
    async with sf() as s:
        raw = (await s.execute(text("SELECT id::text FROM workspace_memory WHERE id=:i"),
                               {"i": row["id"]})).scalar()
    assert raw == str(row["id"])


async def test_workspace_id_of_session_unknown_session(test_engine):
    sf = make_session_factory(test_engine)
    assert await mem.workspace_id_of_session(sf, 999999999) is None


async def test_memories_cascade_when_workspace_deleted(test_engine, tmp_path):
    """workspace_memory 挂 ON DELETE CASCADE：删工作区不留孤儿记忆（召回与面板都不该看到）。"""
    ws = await mk_ws(test_engine, str(tmp_path / "a"))
    sid = await mk_session_in(test_engine, ws)
    sf = make_session_factory(test_engine)
    await mem.create(sf, ws, "fact", "pandas 分块导出")
    async with sf() as s:
        await s.execute(text("DELETE FROM chat_session WHERE id=:i"), {"i": sid})
        await s.execute(text("DELETE FROM workspace WHERE id=:w"), {"w": ws})
        await s.commit()
    assert await mem.list_enabled(sf, ws) == []
    assert await mem.recall_memories(sf, ws, "pandas") == []
    assert await mem.workspace_id_of_session(sf, sid) is None
