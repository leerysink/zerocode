# tests/test_schemadiff.py
from app.deploy.schemadiff import parse_schema, schema_diff, destructive_changes, destructive_message


def _snap(s):
    return parse_schema(s)

SNAP = """
CREATE TABLE IF NOT EXISTS demo_notes (
    id BIGSERIAL PRIMARY KEY,
    title VARCHAR(200) NOT NULL
);
ALTER TABLE demo_notes ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT now();
CREATE INDEX IF NOT EXISTS idx_demo_title ON demo_notes (title);
-- 注释应被忽略
"""


def test_parse_collects_tables_columns_indexes():
    m = parse_schema(SNAP)
    assert "demo_notes" in m["tables"]
    cols = m["tables"]["demo_notes"]["columns"]
    assert set(cols) == {"id", "title", "created_at"}
    assert m["tables"]["demo_notes"]["indexes"] == {"idx_demo_title"}


def test_parse_normalizes_column_type():
    m = parse_schema("CREATE TABLE IF NOT EXISTS t (a VARCHAR(50) NOT NULL);")
    assert m["tables"]["t"]["columns"]["a"].startswith("varchar(50)")


def test_detects_added_table_and_column():
    prev = _snap("CREATE TABLE IF NOT EXISTS a (id INT);")
    cur = _snap("CREATE TABLE IF NOT EXISTS a (id INT);\n"
                "ALTER TABLE a ADD COLUMN IF NOT EXISTS b TEXT;\n"
                "CREATE TABLE IF NOT EXISTS c (id INT);")
    d = schema_diff(prev, cur)
    assert "c" in d["added_tables"]
    assert "b" in d["added_columns"]["a"]
    assert destructive_changes(d) == []   # 纯新增非破坏


def test_dropped_column_and_type_change_are_destructive():
    prev = _snap("CREATE TABLE IF NOT EXISTS a (id INT, old TEXT, n VARCHAR(20));")
    cur = _snap("CREATE TABLE IF NOT EXISTS a (id INT, n INT);")   # old 删、n 类型收窄
    d = schema_diff(prev, cur)
    msgs = destructive_changes(d)
    assert any("a.old" in m for m in msgs)
    assert any("a.n" in m for m in msgs)
    assert "删除列" in destructive_message(d) or "类型" in destructive_message(d)


def test_rename_surfaces_as_drop_plus_add_destructive():
    prev = _snap("CREATE TABLE IF NOT EXISTS a (fullname TEXT);")
    cur = _snap("CREATE TABLE IF NOT EXISTS a (full_name TEXT);")
    assert destructive_changes(schema_diff(prev, cur))


from app.deploy.schemadiff import backfill_skeleton


def _diff(prev_sql, cur_sql):
    return schema_diff(parse_schema(prev_sql), parse_schema(cur_sql))


def test_backfill_skeleton_empty_when_no_destructive():
    d = _diff("CREATE TABLE IF NOT EXISTS a (id INT);",
              "CREATE TABLE IF NOT EXISTS a (id INT);\n"
              "ALTER TABLE a ADD COLUMN IF NOT EXISTS b TEXT;")   # 纯新增
    assert backfill_skeleton(d, today="20260922") == ""


def test_backfill_skeleton_covers_dropped_and_type_change():
    prev = ("CREATE TABLE IF NOT EXISTS a (id INT, old TEXT, amount VARCHAR(20));\n"
            "CREATE TABLE IF NOT EXISTS gone (id INT);")
    cur = "CREATE TABLE IF NOT EXISTS a (id INT, amount INT);"   # 删 old、gone 整表删、amount 类型变更
    out = backfill_skeleton(_diff(prev, cur), today="20260922")
    assert out
    assert "20260922" in out                       # 头含生成日期
    assert "删除表 gone" in out                     # 删表项
    assert "a.old" in out                          # 删列项
    assert "a.amount" in out                       # 类型变更项
    # 通体注释：每个非空行都以 -- 开头（未补实即发布为 no-op）
    assert all(ln.startswith("--") for ln in out.splitlines() if ln.strip())
    # 占位 UPDATE 被注释（含 UPDATE 关键字且整行是注释）
    assert any("UPDATE" in ln for ln in out.splitlines() if ln.startswith("--"))


def test_backfill_skeleton_rename_candidate_pairing():
    prev = "CREATE TABLE IF NOT EXISTS a (id INT, fullname TEXT);"
    cur = ("CREATE TABLE IF NOT EXISTS a (id INT, full_name TEXT);")   # drop fullname + add full_name
    out = backfill_skeleton(_diff(prev, cur), today="20260922")
    assert "改名" in out and "a.fullname" in out and "a.full_name" in out
