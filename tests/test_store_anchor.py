# tests/test_store_anchor.py
"""锚点：读 prod 库 sqlchange_history 的有序成功集 + 指纹 + 前缀 + md5 比对。"""
import hashlib
import pytest
from sqlalchemy import text
from app.store import anchor


async def _seed_history(test_engine, rows):
    async with test_engine.begin() as c:
        await c.execute(text("DROP TABLE IF EXISTS sqlchange_history"))
        await c.execute(text(
            "CREATE TABLE sqlchange_history (filename VARCHAR(200) PRIMARY KEY,"
            " checksum VARCHAR(32), success BOOLEAN NOT NULL, applied_at TIMESTAMPTZ DEFAULT now())"))
        for f, sum_, ok in rows:
            await c.execute(text("INSERT INTO sqlchange_history (filename, checksum, success) "
                                 "VALUES (:f, :s, :o)"), {"f": f, "s": sum_, "o": ok})


async def test_reads_only_success_ordered(test_engine):
    from app.db import make_session_factory  # noqa: F401  (保持与其它用例同样的取用习惯)
    url = test_engine.url.render_as_string(hide_password=False)
    await _seed_history(test_engine, [
        ("V20260920_03_c.sql", "c", True),
        ("V20260920_01_a.sql", "a", True),
        ("V20260920_02_bad.sql", "x", False),
    ])
    got = await anchor.read_history(url)
    assert [e["filename"] for e in got] == ["V20260920_01_a.sql", "V20260920_03_c.sql"]
    assert [e["checksum"] for e in got] == ["a", "c"]


async def test_missing_history_table_is_not_swallowed(test_engine):
    """表不存在必须抛，不能返 []：返 [] 会让上架闸门以为「锚点为空」而给出错误原因。"""
    url = test_engine.url.render_as_string(hide_password=False)
    async with test_engine.begin() as c:
        await c.execute(text("DROP TABLE IF EXISTS sqlchange_history"))
    with pytest.raises(Exception):
        await anchor.read_history(url)


def test_fingerprint_changes_with_checksum_only():
    a = [{"filename": "1.sql", "checksum": "x"}]
    b = [{"filename": "1.sql", "checksum": "y"}]
    assert anchor.fingerprint(a) != anchor.fingerprint(b)
    assert anchor.fingerprint(a) == anchor.fingerprint([{"filename": "1.sql", "checksum": "x"}])


def test_is_prefix():
    assert anchor.is_prefix(["a", "b"], ["a", "b", "c"])
    assert anchor.is_prefix([], ["a"])
    assert not anchor.is_prefix(["a", "c"], ["a", "b", "c"])     # 中间缺文件 = 分叉
    assert not anchor.is_prefix(["b", "a"], ["a", "b", "c"])      # 顺序不同 = 分叉


def test_mismatches_detects_edited_history_file(tmp_path):
    f = tmp_path / "sqlchange"
    f.mkdir()
    (f / "V1_01_a.sql").write_text("SELECT 1;", encoding="utf-8", newline="")
    good = [{"filename": "V1_01_a.sql", "checksum": hashlib.md5(b"SELECT 1;").hexdigest()}]
    disk = {"V1_01_a.sql": anchor.file_md5(f / "V1_01_a.sql")}
    assert anchor.mismatches(good, disk) == []
    (f / "V1_01_a.sql").write_text("SELECT 2;", encoding="utf-8", newline="")
    assert anchor.mismatches(good, {"V1_01_a.sql": anchor.file_md5(f / "V1_01_a.sql")}) == \
        ["V1_01_a.sql"]
    # 文件被删掉也算 mismatch（比 [] 更有信息量）
    assert anchor.mismatches(good, {}) == ["V1_01_a.sql（文件已不在 sqlchange/）"]
