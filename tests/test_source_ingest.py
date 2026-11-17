# tests/test_source_ingest.py
"""仓库快照抓取：全树一次拿路径、正文走 raw、预算与白名单、失败语义。

transport 是 httpx.MockTransport，伪造 gitee.com 与 api/v5 两个 host，
真网络一个都不碰（真仓端到端验收在 Task 9，且要用户下令）。

本文件同步与异步用例混排，靠 pytest.ini 的 asyncio_mode=auto 跑协程（不挂模块级
pytestmark，否则纯同步用例每个多刷一条 PytestWarning，同 test_source_guard.py /
test_source_hosting.py）。
"""
import json
from urllib.parse import unquote

import pytest

from app.source import ingest

URL = "https://gitee.com/leerys/zerocode"


def _blob(path, size=100):
    return {"path": path, "mode": "100644", "type": "blob", "sha": "s" + path[:6], "size": size}


def _tree_entry(path):
    return {"path": path, "mode": "40000", "type": "tree", "sha": "t" + path[:6], "size": 0}


def _transport(tree, files, *, default_branch="master", truncated=False,
               trees_status=200, repo_status=200, requests=None, bad_branch=None,
               raw_status=None, raw_ref=None):
    """夹具参数：

    - raw_status：按文件路径指定 raw 端点的返回码，演「树里列着但正文取不到」的降级。
    - raw_ref：本次仓库「真正存在」的那个 ref。含斜杠的分支（feature/docs）没法靠固定下标
      从路径里切出文件，所以给它时改成整段前缀匹配，打到别的 ref 一律 404 —— 这样夹具既贴真端点，
      又能证明确实是用合并后的 ref 去取正文。
    - bad_branch：这些 ref 的树调用回 404（可以给一个，也可以给一批，用来演两次都落空）。
    """
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
                missing = {bad_branch} if isinstance(bad_branch, str) else set(bad_branch or ())
                if ref in missing:
                    return httpx.Response(404, json={"message": "Not Found"})
                return httpx.Response(200, json={"tree": tree, "truncated": truncated})
            if repo_status != 200:
                return httpx.Response(repo_status, json={"message": "Forbidden"})
            return httpx.Response(200, json={"default_branch": default_branch})
        if host == "gitee.com" and p.startswith("/leerys/zerocode/raw/"):
            # raw 形态是 /<owner>/<repo>/raw/<ref>/<path>：绝对 path 的首段是空串，ref 之后
            # 才是文件（真实端点形态见 test_source_hosting.py）。默认按「ref 只有一段」切；
            # 给了 raw_ref 就整段前缀匹配，好让含斜杠的分支也能被夹具认下来。
            rest = p[len("/leerys/zerocode/raw/"):]
            if raw_ref is not None:
                if not rest.startswith(raw_ref + "/"):
                    return httpx.Response(404)
                fp = rest[len(raw_ref) + 1:]
            else:
                fp = "/".join(rest.split("/")[1:])
            # 真实 raw 端点收到的是百分号编码后的路径（`hosting.raw_file_url` 里 `quote(path)`），
            # 夹具按解码后的仓库原路径查表 —— 否则 `docs/c#1.md` 这种名字永远匹配不上，
            # 「树里的 path ↔ 拿到的正文」这条真实对应就演不出来。
            fp = unquote(fp)
            if raw_status and fp in raw_status:
                return httpx.Response(raw_status[fp], json={"message": "gone"})
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


async def test_explicit_empty_subpath_overrides_url_carried_scope():
    """显式 "" = 就要仓库根，必须盖掉 URL 带进来的 /tree/master/docs。"""
    out = await ingest.fetch_repo_snapshot(f"{URL}/tree/master/docs", subpath="",
                                           transport=_transport(TREE, FILES))
    assert out["manifest"]["subpath"] is None
    assert set(out["files"]) == {"README.md", "docs/user-manual.md"}


async def test_absent_subpath_falls_back_to_url_carried_scope():
    """不传 subpath 时才用 URL 里的目录——与上一条只差 `subpath=""`，产物必须不同。"""
    out = await ingest.fetch_repo_snapshot(f"{URL}/tree/master/docs",
                                           transport=_transport(TREE, FILES))
    assert out["manifest"]["subpath"] == "docs"
    assert set(out["files"]) == {"docs/user-manual.md"}


async def test_whitespace_only_subpath_is_repo_root_not_a_literal_directory():
    """只空白的 subpath 是「用户把框擦干净了」，不是「找一个名叫三个空格的目录」。
    原样当目录用会静默零命中，201 建出一个没有正文的站点。
    必须用**带目录的 URL** 才分得开「仓库根 / 沿用 URL 目录 / 全域」三种读法：
    干净 URL 上三者逐字节同产物，那条用例等于没钉（工单初版就犯了这个错）。"""
    out = await ingest.fetch_repo_snapshot(f"{URL}/tree/master/docs", subpath="   ",
                                           transport=_transport(TREE, FILES))
    assert out["manifest"]["subpath"] is None
    assert set(out["files"]) == {"README.md", "docs/user-manual.md"}


async def test_unscoped_repo_with_no_text_at_all_still_succeeds_with_zero_docs():
    """硬约束的另一半：只有「显式范围零命中」才报错，全域零正文不许变成 400。
    纯二进制仓库是真实存在的东西，把它判成失败比判成空站更糟。"""
    tree = [_blob("img/a.png"), _blob("frontend/package-lock.json")]
    out = await ingest.fetch_repo_snapshot(URL, transport=_transport(tree, {}))
    assert out["manifest"]["counts"]["snapshotted"] == 0
    assert out["manifest"]["subpath"] is None
    assert out["files"] == {}


async def test_subpath_that_matches_nothing_is_an_error_not_an_empty_site():
    """拼错的目录名（doc / 拼写少一个字）必须当场报错：静默 201 建一个空站点，
    用户要等到打开页面才发现摄入「成功」了个寂寞。"""
    with pytest.raises(ingest.SourceError) as e:
        await ingest.fetch_repo_snapshot(URL, subpath="doc",
                                         transport=_transport(TREE, FILES))
    assert "没有可摄入的文本" in str(e.value)


async def test_scoped_dir_whose_only_text_is_oversize_aborts_with_the_size_hint():
    """范围内只有超限正文时也走同一条零命中报错：行为对（建一个空站没意义），
    但这条用例钉的是「别把它误判成 not-found 后静默降级」。"""
    tree = [_blob("docs/big.md", size=ingest.MAX_FILE_BYTES + 1)]
    with pytest.raises(ingest.SourceError) as e:
        await ingest.fetch_repo_snapshot(URL, subpath="docs",
                                         transport=_transport(tree, {}))
    assert "没有可摄入的文本" in str(e.value)


async def test_tree_path_with_interior_dot_segment_is_rejected_not_written_raw():
    """manifest 里存 `docs/../oops.md`、盘上落 `oops.md` 是自相矛盾的契约：
    站点照着 manifest 去 fetch 就撞 404。git 树里造不出这种路径，出现即拒，别归一后双存。"""
    tree = [_blob("docs/../oops.md"), _blob("ok.md")]
    out = await ingest.fetch_repo_snapshot(URL, transport=_transport(tree, FILES))
    assert "docs/../oops.md" not in out["files"]
    assert {s["reason"] for s in out["manifest"]["skipped"] if
            s["path"] == "docs/../oops.md"} == {"unsafe-path"}


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


async def test_slashed_branch_retry_fires_on_tree_404_only():
    """含斜杠分支名（feature/docs）在 URL 里天然歧义：先把 feature 当分支，404 后把子路径并进
    ref 再试一次。整条链路此前只有临时探针跑过，提交用例一条都没钉住 —— 请求序列、合并后的 ref、
    以及「子路径已并进 ref、不再当 subpath 二次过滤」这三件事都在这里落钉。"""
    calls = []
    out = await ingest.fetch_repo_snapshot(
        "https://gitee.com/leerys/zerocode/tree/feature/docs",
        transport=_transport(TREE, FILES, bad_branch="feature", requests=calls,
                             raw_ref="feature/docs"))
    assert calls == [
        "https://gitee.com/api/v5/repos/leerys/zerocode/git/trees/feature?recursive=1",
        "https://gitee.com/api/v5/repos/leerys/zerocode/git/trees/feature/docs?recursive=1",
        "https://gitee.com/leerys/zerocode/raw/feature/docs/README.md",
        "https://gitee.com/leerys/zerocode/raw/feature/docs/docs/user-manual.md",
    ]
    m = out["manifest"]
    assert m["ref"] == "feature/docs"
    assert m["subpath"] is None
    assert set(out["files"]) == {"README.md", "docs/user-manual.md"}
    assert m["counts"]["snapshotted"] == 2


async def test_slashed_branch_retry_does_not_fire_on_tree_403():
    """重试的信号只有 404。403 是配额/拒绝，再补一次树调用等于把 60 次/小时的匿名配额
    花在注定失败的第二次请求上 —— 这条回归今天能通过，所以必须有用例挡住。"""
    calls = []
    with pytest.raises(ingest.SourceError) as e:
        await ingest.fetch_repo_snapshot(
            "https://gitee.com/leerys/zerocode/tree/feature/docs",
            transport=_transport(TREE, FILES, trees_status=403, requests=calls))
    assert "配额" in str(e.value)
    assert e.value.status_code == 403
    assert calls == ["https://gitee.com/api/v5/repos/leerys/zerocode/git/trees/feature?recursive=1"]


async def test_tree_404_message_names_the_effective_scope():
    """404 文案要回答「哪一次请求落空了」：插的是生效后的 scoped 而不是 subpath 参数，
    否则 URL 带进来的 /tree/<ref>/<subpath> 会整段从用户看得见的文本里消失，
    而已经并进 ref 的子路径又会被重复拼一遍（`master/docs/docs`）。"""
    with pytest.raises(ingest.SourceError) as e:
        await ingest.fetch_repo_snapshot(
            URL, subpath="docs",
            transport=_transport(TREE, FILES, bad_branch={"master", "master/docs"}))
    assert "master/docs" in str(e.value)
    assert "docs/docs" not in str(e.value)
    with pytest.raises(ingest.SourceError) as e2:
        await ingest.fetch_repo_snapshot(
            "https://gitee.com/leerys/zerocode/tree/nope/docs",
            transport=_transport(TREE, FILES, bad_branch={"nope", "nope/docs"}))
    assert "nope/docs" in str(e2.value)
    assert "docs/docs" not in str(e2.value)


async def test_unsafe_ref_or_subpath_shape_is_rejected_before_any_request():
    """点段不是「路径怪一点」而是改写端点：`/git/trees/..` 经点段归一后吃掉 `/git/trees/`
    这一截，空段与反斜杠同理，于是一条用户粘的 URL 能打到同域任意 API 路径，而脏形态还会被
    冻进 manifest["ref"]/["subpath"]（Task 4 把它写进 README 出处行）。拼第一个 URL 之前就拒。"""
    calls = []
    t = _transport(TREE, FILES, requests=calls)
    for bad_url in ("https://gitee.com/leerys/zerocode/tree/..",
                    "https://gitee.com/leerys/zerocode/tree/master/../../otherowner/otherrepo",
                    "https://gitee.com/leerys/zerocode/tree/master/docs/../..",
                    "https://gitee.com/leerys/zerocode/tree/master/a\\b"):
        with pytest.raises(ingest.SourceError) as e:
            await ingest.fetch_repo_snapshot(bad_url, transport=t)
        assert "形态非法" in str(e.value)
    for bad_sub in ("../../evilowner/evilrepo", "docs/..", "../docs", "a//b", "a\\b", "."):
        with pytest.raises(ingest.SourceError) as e:
            await ingest.fetch_repo_snapshot(URL, subpath=bad_sub, transport=t)
        assert "形态非法" in str(e.value)
    assert calls == []


async def test_remote_default_branch_with_dot_segment_is_rejected_before_tree_call():
    """闸不能只盯 URL 那一半：default_branch 是远端 JSON 给的，但它同样直接拼进 /git/trees/，
    所以拿到它之后、拼树端点之前还要判一次（此前只有临时脚本演过，今天落钉）。"""
    calls = []
    with pytest.raises(ingest.SourceError) as e:
        await ingest.fetch_repo_snapshot(URL, transport=_transport(
            TREE, FILES, default_branch="../evilowner/evilrepo", requests=calls))
    assert "形态非法" in str(e.value)
    assert calls == ["https://gitee.com/api/v5/repos/leerys/zerocode"]


async def test_subpath_from_url_is_length_bounded_too():
    """_SUBPATH_MAX 原来只管 subpath 参数，URL 里带的 /tree/<ref>/<subpath> 走的是同一份
    拼接却不受限 —— 两个来源现在都受同一道上限约束。"""
    long_url = "https://gitee.com/leerys/zerocode/tree/master/" + "/".join(
        ["seg" + str(i) for i in range(90)])
    assert len(long_url) < ingest.guard.MAX_URL_LEN
    calls = []
    with pytest.raises(ingest.SourceError) as e:
        await ingest.fetch_repo_snapshot(long_url, transport=_transport(TREE, FILES, requests=calls))
    assert "上限" in str(e.value)
    with pytest.raises(ingest.SourceError) as e2:
        await ingest.fetch_repo_snapshot(URL, subpath="d" * (ingest._SUBPATH_MAX + 1),
                                         transport=_transport(TREE, FILES, requests=calls))
    assert "上限" in str(e2.value)
    assert calls == []


async def test_total_budget_aborts_before_downloading():
    big = [_blob(f"d{i}.md", size=ingest.TOTAL_MAX_BYTES) for i in range(2)]
    calls = []
    with pytest.raises(ingest.SourceError) as e:
        await ingest.fetch_repo_snapshot(URL, transport=_transport(big, {}, requests=calls))
    assert "总大小" in str(e.value)
    assert "预估" in str(e.value)      # 与「下载中实际字节」那道预算区分开，报障才分得清阶段
    # 「执行前预估」的全部意义就是零副作用：一个 raw 请求都不该发出去
    assert not any("/raw/" in c for c in calls)


async def test_oversize_single_file_is_skipped_not_fatal():
    tree = [_blob("big.md", size=ingest.MAX_FILE_BYTES + 1), _blob("ok.md", size=10)]
    files = {"big.md": b"x" * (ingest.MAX_FILE_BYTES + 1), "ok.md": b"# ok"}
    out = await ingest.fetch_repo_snapshot(URL, transport=_transport(tree, files))
    assert set(out["files"]) == {"ok.md"}
    assert out["manifest"]["skipped"][0]["reason"] == "too-large"


async def test_declared_size_lying_is_skipped_by_second_budget():
    """远端元数据会撒谎：树里声明 size=10 混过了声明预估，正文实际 200KB+ 必须由下载中的
    第二道字节预算兜住，并按单文件降级跳过，而不是把整次摄入打死。
    上一条用例走的是声明预筛（根本没发 raw 请求），碰不到这道预算，所以单独钉一条。"""
    tree = [_blob("liar.md", size=10), _blob("ok.md", size=10)]
    files = {"liar.md": b"x" * (ingest.MAX_FILE_BYTES + 1), "ok.md": b"# ok"}
    calls = []
    out = await ingest.fetch_repo_snapshot(
        URL, transport=_transport(tree, files, requests=calls))
    reasons = {s["path"]: s["reason"] for s in out["manifest"]["skipped"]}
    assert reasons == {"liar.md": "too-large"}
    assert set(out["files"]) == {"ok.md"}
    # 两个候选都真的下过：撒谎的那条是被实际字节剔掉的，不是被声明预筛提前剔掉的
    assert sum(1 for c in calls if "/raw/" in c) == 2


async def test_running_actual_bytes_trip_the_second_total_budget():
    """第二道总预算（下载中的实际字节累计）单独钉一条：树里声明 size 撒谎成很小，
    预估放行，累计到实际字节越过 TOTAL_MAX_BYTES 时中止 —— 文案要带上阶段，
    支持侧看文案就得知道撞的是预估还是实际。"""
    n = ingest.TOTAL_MAX_BYTES // ingest.MAX_FILE_BYTES + 2
    tree = [_blob(f"d{i}.md", size=10) for i in range(n)]
    files = {f"d{i}.md": b"x" * ingest.MAX_FILE_BYTES for i in range(n)}
    calls = []
    with pytest.raises(ingest.SourceError) as e:
        await ingest.fetch_repo_snapshot(URL, transport=_transport(tree, files, requests=calls))
    assert "总大小" in str(e.value) and "实际字节" in str(e.value)
    # 中途止住：请求数远小于候选数，说明是累计到上限就收工，不是下完再判
    assert sum(1 for c in calls if "/raw/" in c) < n


async def test_missing_raw_body_degrades_to_skipped_not_abort():
    """树里列着、raw 取不到（改名/子模块/5xx）：404 → not-found、5xx → server-error，
    兄弟文件照常进快照。这两个 reason 在提交套件里此前完全不可达。"""
    tree = [_blob("a.md"), _blob("b.md"), _blob("c.md")]
    # b.md 不在 files 里（夹具回 404），c.md 由夹具回 503
    calls = []
    out = await ingest.fetch_repo_snapshot(
        URL, transport=_transport(tree, {"a.md": b"# A"}, raw_status={"c.md": 503},
                                  requests=calls))
    reasons = {s["path"]: s["reason"] for s in out["manifest"]["skipped"]}
    assert reasons == {"b.md": "not-found", "c.md": "server-error"}
    assert set(out["files"]) == {"a.md"}
    assert out["manifest"]["counts"] == {"listed": 3, "snapshotted": 1, "skipped": 2}
    assert sum(1 for c in calls if "/raw/" in c) == 3


async def test_file_count_cap_enforced():
    tree = [_blob(f"f{i}.md") for i in range(ingest.FILE_COUNT_MAX + 1)]
    files = {f"f{i}.md": b"# x" for i in range(ingest.FILE_COUNT_MAX + 1)}
    with pytest.raises(ingest.SourceError) as e:
        await ingest.fetch_repo_snapshot(URL, transport=_transport(tree, files))
    assert "文件数" in str(e.value)


async def test_internal_url_rejected_before_any_request():
    calls = []
    for bad in ("https://127.0.0.1/o/r", "https://localhost/o/r", "http://10.0.0.1/o/r"):
        with pytest.raises(ingest.SourceError):
            await ingest.fetch_repo_snapshot(bad, transport=_transport([], {}, requests=calls))
    # 「拒绝在任何请求之前」是这条用例的全部价值：一次请求都不许发出去
    assert calls == []


async def test_credential_in_url_is_rejected_before_any_request():
    """终审 F1：这条链路的泄漏面是**公开站点**，不是「抓到了私有内容」。

    `urlparse().hostname` 按定义不含 userinfo，SSRF 判据对它是盲的；而端点全部按硬编码 host
    重拼、从不带鉴权头，所以那个 token 一个请求都不生效 —— 它唯一的作用是原样冻进
    `manifest.sourceUrl`（再由 materialize 抄进 README 出处行），而 `manifest.json` 正是发布后
    `/source/manifest.json` 上那个公开可读的文件。判在共用的守卫层，这里钉摄入侧的三个后果：
    零请求、不返回任何载荷、错误文本自己也不带凭证（它会进 400 detail 与访问日志）。
    """
    calls = []
    secret_url = "https://oauth2:GLtok-SECRET123@gitee.com/leerys/zerocode"
    with pytest.raises(ingest.SourceError) as e:
        await ingest.fetch_repo_snapshot(secret_url, transport=_transport(TREE, FILES,
                                                                         requests=calls))
    assert calls == []
    assert "令牌" in str(e.value)
    assert "SECRET123" not in str(e.value)


async def test_unparsable_url_is_a_source_error_not_a_bare_value_error():
    """终审 F2：未闭合的 IPv6 让 `urlparse` 抛裸 ValueError，而它排在 `ingest` 那个大 try 之前 ——
    漏出去就是一次 500，`app/api/apps.py` 的 `except SourceError` 根本看不见它。
    """
    calls = []
    for bad in ("http://[::1/x", "https://[::1"):
        with pytest.raises(ingest.SourceError) as e:
            await ingest.fetch_repo_snapshot(bad, transport=_transport(TREE, FILES, requests=calls))
        assert "URL 形态非法" in str(e.value)
    assert calls == []


async def test_manifest_files_carry_a_percent_encoded_fetch_url():
    """终审 F4：`path` 是给人看的原串，`url` 才是页面拿去 fetch 的目标。

    上游 raw 端点早就按 `quote(path)` 编码（`test_raw_path_quotes_reserved_chars` 钉着），
    写盘侧落的是 normpath 归一名，唯独 manifest 存原串 —— 站点照抄 `path` 拼 fetch 时，
    `#` 会掉进 URL 片段（静默 404）、`?` 变成查询串、`%XX` 会被再解一次码打到**另一篇**正文。
    """
    tree = [_blob("docs/c#1.md"), _blob("docs/a%20b.md"), _blob("docs/q?x.md"),
            _blob("docs/a b.md")]
    files = {e["path"]: b"# x" for e in tree}
    out = await ingest.fetch_repo_snapshot(URL, transport=_transport(tree, files))
    by_path = {f["path"]: f["url"] for f in out["manifest"]["files"]}
    assert by_path == {
        "docs/c#1.md": "/source/files/docs/c%231.md",
        "docs/a%20b.md": "/source/files/docs/a%2520b.md",   # 字面的 %20 必须再编码一次
        "docs/q?x.md": "/source/files/docs/q%3Fx.md",
        "docs/a b.md": "/source/files/docs/a%20b.md",
    }
    # path 一个字节都不动：编码只发生在 url 上，展示字段不许被顺手改掉
    assert {f["path"] for f in out["manifest"]["files"]} == {e["path"] for e in tree}


async def test_wall_clock_budget_stops_the_download_and_names_the_limit(monkeypatch):
    """终审 F5：条数与字节预算封不住**时间**。

    `_client` 的 `timeout=20.0` 是每段读超时：一个每 19 秒吐一个字节的慢滴响应能把它无限续期，
    而创建请求是同步等的（向导没有 axios 超时）。这里用可替换的单调时钟接缝演「每下一篇正文过
    30 秒墙钟」、预算 45 秒：第一篇与第二篇照收（闸判在每次下载之前，不推翻已完成的工作），
    过期之后**一个 raw 都不许多发**，并且以 SourceError 收口（400，不是 500）。
    真 sleep 会把这条变成 flaky 计时用例，所以走 `_monotonic` 这层接缝。
    """
    import httpx

    tree = [_blob(f"d{i}.md") for i in range(5)]
    files = {f"d{i}.md": b"# x" for i in range(5)}
    calls: list[str] = []
    clock = {"t": 1000.0}
    monkeypatch.setattr(ingest, "_monotonic", lambda: clock["t"])
    monkeypatch.setattr(ingest, "WALL_CLOCK_BUDGET_SECONDS", 45.0)

    async def handler(request):
        calls.append(str(request.url))
        p = request.url.path
        if "/git/trees/" in p:
            return httpx.Response(200, json={"tree": tree, "truncated": False})
        if p.startswith("/leerys/zerocode/raw/"):
            clock["t"] += 30                      # 慢滴：这篇正文花掉 30 秒
            fp = "/".join(p[len("/leerys/zerocode/raw/"):].split("/")[1:])
            return httpx.Response(200, content=files[fp])
        return httpx.Response(200, json={"default_branch": "master"})

    with pytest.raises(ingest.SourceError) as e:
        await ingest.fetch_repo_snapshot(URL, transport=httpx.MockTransport(handler))
    raws = [c for c in calls if "/raw/" in c]
    assert len(raws) == 2, f"预算 45 秒、每篇 30 秒：只该下到第二篇，实际 {raws}"
    assert len(raws) < len(tree)                  # 绝不是「下完再判」
    assert "时间预算" in str(e.value)
    assert "subpath" in str(e.value)              # 文案要给出下一步（收窄范围）


async def test_unsupported_host_says_which_hosts_work():
    with pytest.raises(ingest.SourceError) as e:
        await ingest.fetch_repo_snapshot("https://github.com/o/r", transport=_transport([], {}))
    assert "gitee.com" in str(e.value)


async def test_manifest_is_json_safe():
    out = await ingest.fetch_repo_snapshot(URL, transport=_transport(TREE, FILES))
    json.dumps(out["manifest"], ensure_ascii=False)
    assert out["manifest"]["schema"] == "zc-source/1"
    assert out["manifest"]["fetchedAt"].startswith("20")
    # 能成功返回的快照一定没被截断（截断在树那步就抛错了）。字段是契约占位，
    # Task 4/5/9 别从它读信息，也别指望它哪天变成 True。
    assert out["manifest"]["truncated"] is False


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
    # 内部点段判的是原串的段（normpath 会把 `..` 吃掉，只在归一结果上找就等于永不触发）
    assert ingest.is_eligible("docs/../oops.md")[1] == "unsafe-path"
    assert ingest.is_eligible("./a.md")[1] == "unsafe-path"
