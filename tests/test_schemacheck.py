# tests/test_schemacheck.py
from app.deploy.schemacheck import normalize_dump, schema_diff, is_parity, parity_block_message

DUMP_A = """
-- comment line
SET foo = bar;
SELECT pg_catalog.set_config('search_path', '', false);
CREATE TABLE public.demo_notes (
    id bigint NOT NULL,
    title character varying(200) NOT NULL
);
ALTER TABLE public.demo_notes OWNER TO demo_dev;
"""

# 同结构、但 owner 与库名不同：规范化后必须等价
DUMP_B = DUMP_A.replace("demo_dev", "demo_verify")


def test_normalize_drops_owner_set_and_comments():
    norm = normalize_dump(DUMP_A)
    assert "OWNER TO" not in norm
    assert "SET foo" not in norm
    assert "set_config" not in norm
    assert "CREATE TABLE public.demo_notes" in norm


def test_identical_schema_no_diff():
    assert schema_diff(normalize_dump(DUMP_A), normalize_dump(DUMP_B)) == {
        "only_in_dev": [], "only_in_files": [],
        "objects_only_in_dev": [], "objects_only_in_files": []}
    assert is_parity(schema_diff(normalize_dump(DUMP_A), normalize_dump(DUMP_B))) is True


def test_diff_reports_missing_and_extra_objects():
    extra = DUMP_A + "\nCREATE TABLE public.stray (id int);\n"
    diff = schema_diff(normalize_dump(extra), normalize_dump(DUMP_A))
    assert diff["only_in_dev"] and "stray" in "\n".join(diff["only_in_dev"])
    assert not diff["only_in_files"]
    assert is_parity(diff) is False


def test_normalize_strips_pg_dump_restrict_meta():
    # pg_dump 16 的 \restrict/\unrestrict 带随机 token，必须被丢弃，否则同结构会误报漂移
    a = "\\restrict TOKENXYZ\nCREATE TABLE public.t1 (\n    id integer\n);\n\\unrestrict TOKENXYZ\n"
    b = "\\restrict OTHERTOKEN\nCREATE TABLE public.t1 (\n    id integer\n);\n\\unrestrict OTHERTOKEN\n"
    assert is_parity(schema_diff(normalize_dump(a), normalize_dump(b))) is True
    assert "restrict" not in normalize_dump(a)


def test_parity_block_message_none_when_ok():
    assert parity_block_message({"ok": True, "diff": {}}) is None


def test_parity_block_message_lists_drift_when_mismatch():
    vp = {"ok": False, "diff": {"objects_only_in_dev": ["stray"],
                                 "objects_only_in_files": ["t2"]}}
    msg = parity_block_message(vp)
    assert msg and "stray" in msg and "t2" in msg


def test_parity_block_message_surfaces_error():
    msg = parity_block_message({"ok": False, "diff": {}, "error": "dev 凭据缺失"})
    assert msg and "dev 凭据缺失" in msg
