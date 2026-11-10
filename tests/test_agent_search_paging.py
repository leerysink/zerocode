"""Week 1 文件面提效：read_file 行切片、list_dir 分页、search 检索。

口径见 CONTEXT.md「分段读 / 目录分页 / 检索工具」：切片按行、分页永不静默截断、
检索与 read_file 共用同一张忽略表与 deploy/ 凭据围栏。
"""
import dataclasses

import pytest

from app.agent.tools import (MAX_READ_BYTES, ToolContext, ToolError, execute_tool)


class Recorder:
    def __init__(self):
        self.sent = []

    async def __call__(self, ev):
        self.sent.append(ev)


def make_ctx(tmp_path):
    return ToolContext(root=tmp_path, session_id=1, session_factory=None, send=Recorder())


def write_lines(path, n, prefix="L"):
    path.write_text("".join(f"{prefix}{i}\n" for i in range(1, n + 1)), encoding="utf-8")


# ---------- read_file：行号切片 ----------

@pytest.mark.asyncio
async def test_read_file_slices_by_lines(tmp_path):
    ctx = make_ctx(tmp_path)
    write_lines(tmp_path / "a.txt", 10)
    r = await execute_tool(ctx, "read_file", {"path": "a.txt", "offset": 3, "limit": 2})
    assert r["content"] == "L3\nL4\n"
    assert (r["total_lines"], r["start_line"], r["end_line"]) == (10, 3, 4)
    assert r["remaining_lines"] == 6
    assert r["truncated_by_bytes"] is False


@pytest.mark.asyncio
async def test_read_file_defaults_to_whole_file(tmp_path):
    ctx = make_ctx(tmp_path)
    write_lines(tmp_path / "a.txt", 4)
    r = await execute_tool(ctx, "read_file", {"path": "a.txt"})
    assert r["content"] == "L1\nL2\nL3\nL4\n"
    assert (r["start_line"], r["end_line"], r["remaining_lines"]) == (1, 4, 0)


@pytest.mark.asyncio
async def test_read_file_offset_past_eof_is_an_error(tmp_path):
    ctx = make_ctx(tmp_path)
    write_lines(tmp_path / "a.txt", 3)
    with pytest.raises(ToolError) as e:
        await execute_tool(ctx, "read_file", {"path": "a.txt", "offset": 9})
    assert "行" in str(e.value)


@pytest.mark.asyncio
async def test_read_file_big_file_becomes_resumable_not_an_error(tmp_path):
    """超过 200KB 不再报错：返回前一段并告知从哪一行续读（旧文案让模型去用 edit_file，是死路）。"""
    ctx = make_ctx(tmp_path)
    (tmp_path / "big.txt").write_text("x" * 99 + "\n" + ("y" * 99 + "\n") * 3000, encoding="utf-8")
    r = await execute_tool(ctx, "read_file", {"path": "big.txt"})
    assert r["truncated_by_bytes"] is True
    assert 0 < r["end_line"] < r["total_lines"]
    assert r["remaining_lines"] == r["total_lines"] - r["end_line"]
    assert len(r["content"].encode("utf-8")) <= MAX_READ_BYTES
    assert "offset" in r["notice"]


@pytest.mark.asyncio
async def test_read_file_single_huge_line_is_cut_with_notice(tmp_path):
    ctx = make_ctx(tmp_path)
    (tmp_path / "one.txt").write_text("y" * (MAX_READ_BYTES + 100), encoding="utf-8")
    r = await execute_tool(ctx, "read_file", {"path": "one.txt"})
    assert r["truncated_by_bytes"] is True
    assert (r["total_lines"], r["start_line"], r["end_line"], r["remaining_lines"]) == (1, 1, 1, 0)
    assert "截断" in r["notice"]


# ---------- list_dir：entry 级分页 ----------

@pytest.mark.asyncio
async def test_list_dir_pages_with_total_and_next_offset(tmp_path):
    ctx = make_ctx(tmp_path)
    for i in range(5):
        (tmp_path / f"f{i}.txt").write_text("x", encoding="utf-8")
    r = await execute_tool(ctx, "list_dir", {"path": "", "limit": 2})
    assert [e["name"] for e in r["entries"]] == ["f0.txt", "f1.txt"]
    assert (r["total_entries"], r["next_offset"]) == (5, 2)
    tail = await execute_tool(ctx, "list_dir", {"path": "", "offset": 4, "limit": 2})
    assert [e["name"] for e in tail["entries"]] == ["f4.txt"]
    assert tail["next_offset"] is None


@pytest.mark.asyncio
async def test_list_dir_limit_is_capped(tmp_path):
    ctx = make_ctx(tmp_path)
    for i in range(501):
        (tmp_path / f"f{i:03d}.txt").write_text("x", encoding="utf-8")
    r = await execute_tool(ctx, "list_dir", {"path": "", "limit": 10_000})
    assert len(r["entries"]) == 500
    assert r["total_entries"] == 501 and r["next_offset"] == 500


# ---------- search：内容 / 文件名两种模式 ----------

@pytest.mark.asyncio
async def test_search_content_reports_path_line_and_text(tmp_path):
    ctx = make_ctx(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x\ny\nneedle here\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("nothing\n", encoding="utf-8")
    r = await execute_tool(ctx, "search", {"mode": "content", "pattern": "needle"})
    assert r["matches"] == [{"path": "src/a.py", "line": 3, "text": "needle here"}]
    assert (r["mode"], r["count"]) == ("content", 1)


@pytest.mark.asyncio
async def test_search_content_is_case_insensitive_literal(tmp_path):
    ctx = make_ctx(tmp_path)
    (tmp_path / "a.py").write_text("Needle\n", encoding="utf-8")
    r = await execute_tool(ctx, "search", {"mode": "content", "pattern": "NEEDLE"})
    assert r["count"] == 1


@pytest.mark.asyncio
async def test_search_excludes_deploy_and_counts_skips(tmp_path):
    """检索不得成为 read_file 凭据读禁之外的第二条明文出口。"""
    ctx = make_ctx(tmp_path)
    (tmp_path / "a.py").write_text("needle\n", encoding="utf-8")
    (tmp_path / "deploy").mkdir()
    (tmp_path / "deploy" / ".env").write_text("SECRET=needle\n", encoding="utf-8")
    r = await execute_tool(ctx, "search", {"mode": "content", "pattern": "needle"})
    assert [m["path"] for m in r["matches"]] == ["a.py"]
    assert r["skipped_dirs"] >= 1
    assert "跳过" in r["note"]


@pytest.mark.asyncio
async def test_search_skips_binary_and_ignored_dirs(tmp_path):
    ctx = make_ctx(tmp_path)
    (tmp_path / "b.bin").write_bytes(b"needle\x00")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "c.js").write_text("needle\n", encoding="utf-8")
    r = await execute_tool(ctx, "search", {"mode": "content", "pattern": "needle"})
    assert r["matches"] == []
    assert r["skipped_files"] >= 1 and r["skipped_dirs"] >= 1


@pytest.mark.asyncio
async def test_search_files_mode_globs_and_ignores_dirs(tmp_path):
    ctx = make_ctx(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "A.vue").write_text("x", encoding="utf-8")
    (tmp_path / "src" / "b.py").write_text("x", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "x.vue").write_text("x", encoding="utf-8")
    r = await execute_tool(ctx, "search", {"mode": "files", "pattern": "*.vue"})
    assert r["matches"] == ["src/A.vue"]
    assert r["count"] == 1


@pytest.mark.asyncio
async def test_search_match_cap_is_visible(tmp_path):
    ctx = make_ctx(tmp_path)
    for i in range(205):
        (tmp_path / f"f{i:03d}.py").write_text("needle\n", encoding="utf-8")
    r = await execute_tool(ctx, "search", {"mode": "content", "pattern": "needle"})
    assert r["count"] == 200
    assert r["truncated"] is True
    assert "收窄" in r["note"]


@pytest.mark.asyncio
async def test_search_rejects_bad_arguments(tmp_path):
    ctx = make_ctx(tmp_path)
    with pytest.raises(ToolError) as e1:
        await execute_tool(ctx, "search", {"mode": "regex", "pattern": "x"})
    assert "mode" in str(e1.value)
    with pytest.raises(ToolError) as e2:
        await execute_tool(ctx, "search", {"mode": "content", "pattern": "  "})
    assert "pattern" in str(e2.value)
