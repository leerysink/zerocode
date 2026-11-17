# tests/test_source_ingest.py
"""仓库快照抓取：全树一次拿路径、正文走 raw、预算与白名单、失败语义。

transport 是 httpx.MockTransport，伪造 gitee.com 与 api/v5 两个 host，
真网络一个都不碰（真仓端到端验收在 Task 9，且要用户下令）。

本文件同步与异步用例混排，靠 pytest.ini 的 asyncio_mode=auto 跑协程（不挂模块级
pytestmark，否则纯同步用例每个多刷一条 PytestWarning，同 test_source_guard.py /
test_source_hosting.py）。
"""
import json

import pytest

from app.source import ingest

URL = "https://gitee.com/leerys/zerocode"


def _blob(path, size=100):
    return {"path": path, "mode": "100644", "type": "blob", "sha": "s" + path[:6], "size": size}


def _tree_entry(path):
    return {"path": path, "mode": "40000", "type": "tree", "sha": "t" + path[:6], "size": 0}


def _transport(tree, files, *, default_branch="master", truncated=False,
               trees_status=200, repo_status=200, requests=None, bad_branch=None):
    import httpx

    async def handler(request):
        if requests is not None:
            requests.append(str(request.url))
        host, p = request.url.host, request.url.path
        if host == "gitee.com" and p.startswith("/api/v5/repos/"):
            if p.endswith("/git/trees") or "/git/trees/" in p:
                if trees_status != 200:
                    return httpx.Response(trees_status, json={"message": "rate limited"})
                ref = p.split("/git/trees/")[1].split("?")[0]
                if bad_branch and ref == bad_branch:
                    return httpx.Response(404, json={"message": "Not Found"})
                return httpx.Response(200, json={"tree": tree, "truncated": truncated})
            if repo_status != 200:
                return httpx.Response(repo_status, json={"message": "Forbidden"})
            return httpx.Response(200, json={"default_branch": default_branch})
        if host == "gitee.com" and p.startswith("/leerys/zerocode/raw/"):
            # raw 形态是 /<owner>/<repo>/raw/<ref>/<path>：绝对 path 的首段是空串，
            # 文件路径从下标 5 起（下标 4 是 ref）——真实端点形态见 test_source_hosting.py。
            fp = "/".join(p.split("/")[5:])
            if fp in files:
                return httpx.Response(200, content=files[fp])
            return httpx.Response(404)
        return httpx.Response(404)

    return httpx.MockTransport(handler)


TREE = [_blob("README.md"), _tree_entry("docs"), _blob("docs/user-manual.md"),
        _blob("frontend/package-lock.json"), _blob("docs/images/a.png"),
        _blob("node_modules/evil/index.md"), _blob(".git/config")]
FILES = {"README.md": b"# ZeroCode\n\nbody",
         # 中文字面量不能写进 bytes（`b"# 使用手册"` 是 SyntaxError），用等价的 utf-8 编码
         "docs/user-manual.md": "# 使用手册\n\nbody".encode("utf-8"),
         "frontend/package-lock.json": b"{}",
         "docs/images/a.png": b"\x89PNG",
         "node_modules/evil/index.md": b"# nope",
         ".git/config": b"[core]"}


async def test_happy_path_pulls_tree_then_raw_bodies():
    calls = []
    t = _transport(TREE, FILES, requests=calls)
    out = await ingest.fetch_repo_snapshot(URL, transport=t)
    assert set(out["files"]) == {"README.md", "docs/user-manual.md"}
    m = out["manifest"]
    assert (m["host"], m["repo"], m["ref"]) == ("gitee", "zerocode", "master")
    assert m["counts"]["listed"] == len(TREE)
    assert m["counts"]["snapshotted"] == 2
    assert {f["path"] for f in m["files"]} == {"README.md", "docs/user-manual.md"}
    assert calls[0].endswith("/api/v5/repos/leerys/zerocode")     # 先问默认分支
    assert "/git/trees/master?recursive=1" in calls[1]           # 再一次拿全树
    assert sum(1 for c in calls if "/raw/" in c) == 2            # 正文只下白名单内的


async def test_titles_come_from_first_heading():
    out = await ingest.fetch_repo_snapshot(URL, transport=_transport(TREE, FILES))
    by_path = {f["path"]: f for f in out["manifest"]["files"]}
    assert by_path["README.md"]["title"] == "ZeroCode"
    assert by_path["README.md"]["dir"] == ""
    assert by_path["docs/user-manual.md"]["dir"] == "docs"


async def test_skip_reasons_are_recorded_not_silent():
    out = await ingest.fetch_repo_snapshot(URL, transport=_transport(TREE, FILES))
    reasons = {s["path"]: s["reason"] for s in out["manifest"]["skipped"]}
    assert reasons["frontend/package-lock.json"] == "lockfile"
    assert reasons["docs/images/a.png"] == "not-text"
    assert reasons["node_modules/evil/index.md"] == "ignored-dir"
    assert reasons[".git/config"] == "ignored-dir"


async def test_explicit_ref_skips_default_branch_call():
    calls = []
    await ingest.fetch_repo_snapshot("https://gitee.com/leerys/zerocode/tree/dev",
                                     transport=_transport(TREE, FILES, requests=calls))
    assert not any(c.endswith("/api/v5/repos/leerys/zerocode") for c in calls)


async def test_subpath_narrows_snapshot_scope():
    out = await ingest.fetch_repo_snapshot(URL, subpath="docs",
                                           transport=_transport(TREE, FILES))
    assert set(out["files"]) == {"docs/user-manual.md"}
    assert out["manifest"]["subpath"] == "docs"


async def test_truncated_tree_asks_for_subpath():
    with pytest.raises(ingest.SourceError) as e:
        await ingest.fetch_repo_snapshot(URL, transport=_transport(TREE, FILES, truncated=True))
    assert "truncated" in str(e.value) or "过大" in str(e.value)


async def test_rate_limited_tree_reports_quota():
    with pytest.raises(ingest.SourceError) as e:
        await ingest.fetch_repo_snapshot(URL, transport=_transport(TREE, FILES, trees_status=403))
    assert "配额" in str(e.value)
    assert e.value.status_code == 403


async def test_private_repo_reports_unreadable():
    with pytest.raises(ingest.SourceError) as e:
        await ingest.fetch_repo_snapshot(URL, transport=_transport(TREE, FILES, repo_status=401))
    assert "不可读" in str(e.value)


async def test_unknown_branch_reports_404():
    with pytest.raises(ingest.SourceError):
        await ingest.fetch_repo_snapshot("https://gitee.com/leerys/zerocode/tree/nope",
                                         transport=_transport(TREE, FILES, bad_branch="nope"))


async def test_total_budget_aborts_before_downloading():
    big = [_blob(f"d{i}.md", size=ingest.TOTAL_MAX_BYTES) for i in range(2)]
    with pytest.raises(ingest.SourceError) as e:
        await ingest.fetch_repo_snapshot(URL, transport=_transport(big, {}))
    assert "总大小" in str(e.value)


async def test_oversize_single_file_is_skipped_not_fatal():
    tree = [_blob("big.md", size=ingest.MAX_FILE_BYTES + 1), _blob("ok.md", size=10)]
    files = {"big.md": b"x" * (ingest.MAX_FILE_BYTES + 1), "ok.md": b"# ok"}
    out = await ingest.fetch_repo_snapshot(URL, transport=_transport(tree, files))
    assert set(out["files"]) == {"ok.md"}
    assert out["manifest"]["skipped"][0]["reason"] == "too-large"


async def test_file_count_cap_enforced():
    tree = [_blob(f"f{i}.md") for i in range(ingest.FILE_COUNT_MAX + 1)]
    files = {f"f{i}.md": b"# x" for i in range(ingest.FILE_COUNT_MAX + 1)}
    with pytest.raises(ingest.SourceError) as e:
        await ingest.fetch_repo_snapshot(URL, transport=_transport(tree, files))
    assert "文件数" in str(e.value)


async def test_internal_url_rejected_before_any_request():
    for bad in ("https://127.0.0.1/o/r", "https://localhost/o/r", "http://10.0.0.1/o/r"):
        with pytest.raises(ingest.SourceError):
            await ingest.fetch_repo_snapshot(bad, transport=_transport([], {}))


async def test_unsupported_host_says_which_hosts_work():
    with pytest.raises(ingest.SourceError) as e:
        await ingest.fetch_repo_snapshot("https://github.com/o/r", transport=_transport([], {}))
    assert "gitee.com" in str(e.value)


async def test_manifest_is_json_safe():
    out = await ingest.fetch_repo_snapshot(URL, transport=_transport(TREE, FILES))
    json.dumps(out["manifest"], ensure_ascii=False)
    assert out["manifest"]["schema"] == "zc-source/1"
    assert out["manifest"]["fetchedAt"].startswith("20")


def test_first_heading_ignores_code_fence_and_prefixed_lines():
    assert ingest.first_heading("# T\n\n```text\n# not me\n```") == "T"
    assert ingest.first_heading("nope\n## Sub") == "Sub"
    assert ingest.first_heading("#x") is None
    assert ingest.first_heading("") is None


def test_is_eligible_extension_and_lockfile_and_dir():
    assert ingest.is_eligible("docs/a.md") == (True, None)
    assert ingest.is_eligible("a.yaml") == (True, None)
    assert ingest.is_eligible("a.png")[1] == "not-text"
    assert ingest.is_eligible("package-lock.json")[1] == "lockfile"
    assert ingest.is_eligible(".git/config")[1] == "ignored-dir"
    assert ingest.is_eligible("../escape.md")[1] == "unsafe-path"
    assert ingest.is_eligible("/abs.md")[1] == "unsafe-path"
    assert ingest.is_eligible("a\\b.md")[1] == "unsafe-path"
