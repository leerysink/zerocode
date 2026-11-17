# tests/test_source_hosting.py
"""Gitee 仓库 URL 解析与端点形态（URL 全部对照 2026-11-17 实测 200 的真实端点写）。

本文件全是纯同步解析用例，故不挂模块级 `pytestmark = pytest.mark.asyncio`：
auto 模式下给同步用例挂 asyncio 标记，每个用例多刷一条 PytestWarning（同门文件
tests/test_source_guard.py 已就这一点做过同样裁定）。
"""
import pytest

from app.source import hosting


def test_repo_root_url():
    ref = hosting.parse_repo_url("https://gitee.com/leerys/zerocode")
    assert (ref.host, ref.owner, ref.repo, ref.ref, ref.subpath) == (
        "gitee.com", "leerys", "zerocode", None, None)


def test_dot_git_suffix_stripped():
    assert hosting.parse_repo_url("https://gitee.com/leerys/zerocode.git").repo == "zerocode"


def test_dot_git_only_repo_segment_raises():
    """末段只有 `.git` 时剥完后缀仓库名就成了空串，端点会拼出 `…/repos/o/` 这种
    必然 404 的地址——判成坏形态当场报错，比发一次注定失败的请求诚实。"""
    with pytest.raises(hosting.HostingError):
        hosting.parse_repo_url("https://gitee.com/o/.git")


def test_tree_ref_and_subpath():
    ref = hosting.parse_repo_url("https://gitee.com/leerys/zerocode/tree/master/docs/adr")
    assert (ref.ref, ref.subpath) == ("master", "docs/adr")


def test_www_host_normalized_to_gitee():
    assert hosting.parse_repo_url("https://www.gitee.com/leerys/zerocode").host == "gitee.com"


def test_unsupported_host_returns_none():
    assert hosting.parse_repo_url("https://github.com/owner/repo") is None
    assert hosting.parse_repo_url("https://gitlab.com/owner/repo") is None


def test_skill_md_direct_link_is_not_a_repo():
    assert hosting.parse_repo_url(
        "https://gitee.com/leerys/x/raw/master/SKILL.md") is None
    # 文件页形态（/blob/）同样是指向单个文件，不是仓库
    assert hosting.parse_repo_url(
        "https://gitee.com/leerys/x/blob/master/SKILL.md") is None


def test_skill_md_under_tree_dir_is_still_a_repo():
    """`/tree/<ref>/<path>` 指的是目录，末段叫 SKILL.md 也仍是仓库地址：
    浏览器里复制目录链接容易带上这一层，把它判成「不是仓库」会把真仓库拒在门外。"""
    ref = hosting.parse_repo_url("https://gitee.com/o/r/tree/master/skills/foo/SKILL.md")
    assert (ref.ref, ref.subpath) == ("master", "skills/foo/SKILL.md")


@pytest.mark.parametrize("bad", [
    "https://gitee.com/onlyowner",
    "https://gitee.com/leerys/zerocode/branch/master",
    "https://gitee.com/leerys/zerocode/tree",
])
def test_malformed_repo_url_raises(bad):
    with pytest.raises(hosting.HostingError):
        hosting.parse_repo_url(bad)


def test_tree_ref_only_without_subpath_is_accepted():
    """/tree/<ref> 不带子路径是用户最常粘贴的形态，Task 3 的显式分支用例（`/tree/dev`、
    `/tree/nope`）直接依赖它解析成 ref——把它当坏形态抛错，那条链路第一步就断。"""
    ref = hosting.parse_repo_url("https://gitee.com/leerys/zerocode/tree/dev")
    assert (ref.owner, ref.repo, ref.ref, ref.subpath) == ("leerys", "zerocode", "dev", None)


def test_tree_with_slashed_ref_is_resolvable_by_caller():
    """含斜杠分支名靠调用方回退重试，这里先按「ref=master、subpath=dev/x」给出初判。"""
    ref = hosting.parse_repo_url("https://gitee.com/o/r/tree/master/dev/x")
    assert (ref.ref, ref.subpath) == ("master", "dev/x")


@pytest.mark.parametrize("bad", [
    "https://gitee.com/o/r/tree/..",
    "https://gitee.com/o/r/tree/master/../../otherowner/otherrepo",
    "https://gitee.com/o/r/tree/master/docs/../..",
    "https://gitee.com/o/r/tree/./x",
    "https://gitee.com/o/r/tree/master/a\\b",
    "https://gitee.com/../r",
    "https://gitee.com/o/..",
])
def test_dot_segment_or_backslash_in_url_raises(bad):
    """点段/反斜杠在解析层就拒，不放行到端点拼接：`api_tree_url(ref, "..")` 拼出的
    `…/repos/o/r/git/trees/..?recursive=1` 会被点段归一吃掉 `/git/trees/` 那一截，
    一条用户粘的 URL 就能打到同域任意 API 路径，脏形态还会冻进 ref/subpath。
    静默放行坏形态正是本文件开头那条裁定要避免的事。"""
    with pytest.raises(hosting.HostingError) as e:
        hosting.parse_repo_url(bad)
    assert "形态非法" in str(e.value)


def test_unsafe_path_segment_predicate_is_pure_and_total():
    """空值 = 「没有这一段」，不是「一段坏值」，放行；正常点号文件名不受牵连。"""
    assert hosting.unsafe_path_segment(None) is None
    assert hosting.unsafe_path_segment("") is None
    assert hosting.unsafe_path_segment("master") is None
    assert hosting.unsafe_path_segment("feature/docs") is None
    assert hosting.unsafe_path_segment("v1.2.3") is None
    assert hosting.unsafe_path_segment("docs/.github") is None
    assert hosting.unsafe_path_segment("..") == ".."
    assert hosting.unsafe_path_segment("a/../b") == ".."
    assert hosting.unsafe_path_segment("a//b") == ""
    assert hosting.unsafe_path_segment("a\\b") == "a\\b"


def test_slashed_branch_survives_api_tree_url():
    """含斜杠的分支名原样留在 tree 端点里：Task 3 把 f"{branch}/{scoped}" 直接喂给
    api_tree_url，靠的就是 quote 不转义斜杠；此前没有任何测试钉住这条契约。"""
    ref = hosting.parse_repo_url("https://gitee.com/o/r")
    assert hosting.api_tree_url(ref, "release/1.2") == (
        "https://gitee.com/api/v5/repos/o/r/git/trees/release/1.2?recursive=1")


def test_endpoint_shapes_match_measured_working_urls():
    ref = hosting.parse_repo_url("https://gitee.com/leerys/zerocode")
    assert hosting.api_repo_url(ref) == "https://gitee.com/api/v5/repos/leerys/zerocode"
    assert hosting.api_tree_url(ref, "master") == (
        "https://gitee.com/api/v5/repos/leerys/zerocode/git/trees/master?recursive=1")
    assert hosting.raw_file_url(ref, "master", "docs/adr/0001-x.md") == (
        "https://gitee.com/leerys/zerocode/raw/master/docs/adr/0001-x.md")


def test_raw_path_quotes_reserved_chars():
    ref = hosting.parse_repo_url("https://gitee.com/o/r")
    assert hosting.raw_file_url(ref, "master", "a b/目录/c#1.md") == (
        "https://gitee.com/o/r/raw/master/a%20b/%E7%9B%AE%E5%BD%95/c%231.md")


def test_default_branch_from_repo_json():
    assert hosting.default_branch_of({"default_branch": "master"}) == "master"
    with pytest.raises(hosting.HostingError):
        hosting.default_branch_of({})
