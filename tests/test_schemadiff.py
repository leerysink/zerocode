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
