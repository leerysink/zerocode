# tests/test_skills_github_api.py
"""Ticket 002 GitHub 仓库安装：trees 扫描、monorepo 候选、subpath 定位、
大小/文件数守卫、ref 解析、token 注入、blob 直链改写。接缝同 001（HTTP API +
MockTransport 伪造 api.github.com / raw.githubusercontent.com）。
"""
import pytest
from pathlib import Path

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _skills_data_dir(tmp_path, monkeypatch):
    """技能母本落 tmp，绝不写仓库 data/。"""
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path / "data"))


SKILL_A = b"---\nname: Skill Alpha\ndescription: alpha skill\n---\n# A\nsee [x](references/x.md)\n"
SKILL_B = b"---\nname: Skill Beta\ndescription: beta skill\n---\n# B\n"


def _blob(path, size=16):
    return {"path": path, "type": "blob", "size": size}


def _gh_transport(tree, files, *, truncated=False, trees_status=200, requests=None,
                  repo="owner/repo", ref="main", bad_refs=()):
    """伪造 GitHub：/repos/o/r→默认分支，/git/trees/<ref>→清单，raw host→文件内容。

    bad_refs：这些 ref 的 trees 请求返 404（模拟含斜杠分支名解析歧义，审查 M-1）；
    ref 同步供 raw 路径剥段用（含斜杠 ref 多一段）。
    """
    import httpx

    async def handler(request):
        if requests is not None:
            requests.append(request)
        host, p = request.url.host, request.url.path
        if host == "api.github.com":
            if p == f"/repos/{repo}":
                return httpx.Response(200, json={"default_branch": "main"})
            if p.startswith(f"/repos/{repo}/git/trees/"):
                if trees_status != 200:
                    return httpx.Response(trees_status, json={"message": "rate limited"})
                tree_ref = p.rsplit("/", 1)[-1].split("?", 1)[0]
                if tree_ref in bad_refs:
                    return httpx.Response(404, json={"message": "Not Found"})
                return httpx.Response(200, json={"tree": tree, "truncated": truncated})
            return httpx.Response(404)
        if host == "raw.githubusercontent.com":
            fp = "/".join(p.split("/")[3 + ref.count("/") + 1:])  # 剥 /owner/repo/<ref含斜杠…>/
            if fp in files:
                return httpx.Response(200, content=files[fp])
            return httpx.Response(404)
        return httpx.Response(404)
    return httpx.MockTransport(handler)


MONO_TREE = [
    _blob("README.md"),
    _blob("skills/a/SKILL.md"), _blob("skills/a/references/x.md"),
    _blob("skills/b/SKILL.md"),
]
MONO_FILES = {
    "README.md": b"# repo",
    "skills/a/SKILL.md": SKILL_A,
    "skills/a/references/x.md": b"ref x",
    "skills/b/SKILL.md": SKILL_B,
}


async def test_monorepo_multiple_hits_return_candidates(client):
    client.app.state.skills_http_transport = _gh_transport(MONO_TREE, MONO_FILES)
    r = await client.post("/api/skills/install", json={"url": "https://github.com/owner/repo"})
    # 不指定 subpath 时多命中→候选清单，不落库不落盘
    assert r.status_code == 200, r.text
    assert [c["path"] for c in r.json()["candidates"]] == ["skills/a", "skills/b"]
    assert (await client.get("/api/skills")).json() == []


async def test_monorepo_candidate_resubmit_with_subpath(client):
    client.app.state.skills_http_transport = _gh_transport(MONO_TREE, MONO_FILES)
    r = await client.post("/api/skills/install", json={
        "url": "https://github.com/owner/repo", "subpath": "skills/a"})
    assert r.status_code == 201, r.text
    sk = r.json()
    assert sk["name"] == "Skill Alpha"
    assert sk["source_type"] == "github"
    assert sk["subpath"] == "skills/a"
    assert sk["slug"] == "skill-alpha"
    # 目录整包：同目录附属进母本，目录外的 README.md 不进
    assert (Path(sk["skill_dir"]) / "SKILL.md").read_bytes() == SKILL_A
    assert (Path(sk["skill_dir"]) / "references" / "x.md").is_file()
    assert not (Path(sk["skill_dir"]) / "README.md").exists()


async def test_root_skill_repo_installs_whole_tree(client):
    tree = [_blob("SKILL.md"), _blob("docs/guide.md")]
    files = {"SKILL.md": SKILL_B, "docs/guide.md": b"guide"}
    client.app.state.skills_http_transport = _gh_transport(tree, files)
    r = await client.post("/api/skills/install", json={"url": "https://github.com/owner/repo"})
    assert r.status_code == 201, r.text
    sk = r.json()
    assert sk["subpath"] is None
    assert (Path(sk["skill_dir"]) / "docs" / "guide.md").is_file()


async def test_no_skill_md_rejected_with_reason(client):
    tree = [_blob("README.md"), _blob("docs/guide.md")]
    client.app.state.skills_http_transport = _gh_transport(tree, {"README.md": b"x"})
    r = await client.post("/api/skills/install", json={"url": "https://github.com/owner/repo"})
    assert r.status_code == 400
    assert "SKILL.md" in r.text
    assert (await client.get("/api/skills")).json() == []


async def test_file_count_guard(client):
    tree = [_blob("SKILL.md")] + [_blob(f"d/f{i}.md") for i in range(600)]
    client.app.state.skills_http_transport = _gh_transport(tree, {"SKILL.md": SKILL_B})
    r = await client.post("/api/skills/install", json={"url": "https://github.com/owner/repo"})
    assert r.status_code == 400
    assert "文件数" in r.text


async def test_total_size_guard_uses_tree_metadata(client):
    # 元数据合计 12MB > 10MB：应在下载前就被拒（raw 一个文件都不给）
    tree = [_blob("SKILL.md", size=6 * 1024 * 1024),
            _blob("big.bin", size=6 * 1024 * 1024)]
    client.app.state.skills_http_transport = _gh_transport(tree, {})
    r = await client.post("/api/skills/install", json={"url": "https://github.com/owner/repo"})
    assert r.status_code == 400
    assert "总大小" in r.text


async def test_truncated_tree_rejected(client):
    client.app.state.skills_http_transport = _gh_transport([_blob("SKILL.md")], {},
                                                           truncated=True)
    r = await client.post("/api/skills/install", json={"url": "https://github.com/owner/repo"})
    assert r.status_code == 400
    assert "truncated" in r.text or "subpath" in r.text


async def test_api_failure_reports_status(client):
    client.app.state.skills_http_transport = _gh_transport([], {}, trees_status=403)
    r = await client.post("/api/skills/install", json={"url": "https://github.com/owner/repo"})
    assert r.status_code == 400
    assert "403" in r.text


async def test_token_env_adds_authorization(client, monkeypatch):
    monkeypatch.setenv("ZEROCODE_GITHUB_TOKEN", "tok-abc")
    reqs = []
    client.app.state.skills_http_transport = _gh_transport(
        [_blob("SKILL.md")], {"SKILL.md": SKILL_B}, requests=reqs)
    r = await client.post("/api/skills/install", json={"url": "https://github.com/owner/repo"})
    assert r.status_code == 201, r.text
    gh = [q for q in reqs if q.url.host == "api.github.com"]
    assert gh and all(q.headers.get("authorization") == "Bearer tok-abc" for q in gh)


async def test_tree_url_ref_used_for_fetch(client):
    reqs = []
    client.app.state.skills_http_transport = _gh_transport(
        [_blob("SKILL.md")], {"SKILL.md": SKILL_B}, requests=reqs)
    r = await client.post("/api/skills/install",
                          json={"url": "https://github.com/owner/repo/tree/dev"})
    assert r.status_code == 201, r.text
    # ref=dev 走 trees/dev 与 raw/.../dev/...，且不再查默认分支
    hosts_paths = [(q.url.host, q.url.path) for q in reqs]
    assert ("api.github.com", "/repos/owner/repo/git/trees/dev") in hosts_paths
    assert all(p != "/repos/owner/repo" for _, p in hosts_paths)
    assert ("raw.githubusercontent.com", "/owner/repo/dev/SKILL.md") in hosts_paths


async def test_bad_github_urls_rejected(client):
    client.app.state.skills_http_transport = _gh_transport([], {})
    r = await client.post("/api/skills/install", json={"url": "https://github.com/owner"})
    assert r.status_code == 400  # 缺 repo
    r = await client.post("/api/skills/install",
                          json={"url": "https://github.com/owner/repo/blob/main/README.md"})
    assert r.status_code == 400  # blob 非 SKILL.md 形态不被当仓库扫


async def test_blob_skill_md_url_rawized(client):
    """github.com/.../blob/.../SKILL.md 直链改写 raw 拉取，走 url 通道（001 复审 #9）。"""
    tree = [_blob("SKILL.md")]
    client.app.state.skills_http_transport = _gh_transport(
        tree, {"skills/a/SKILL.md": SKILL_A, "skills/a/references/x.md": b"ref x"})
    r = await client.post("/api/skills/install",
                          json={"url": "https://github.com/owner/repo/blob/main/skills/a/SKILL.md"})
    assert r.status_code == 201, r.text
    sk = r.json()
    assert sk["source_type"] == "url"
    assert sk["source_url"].startswith("https://raw.githubusercontent.com/")
    assert (Path(sk["skill_dir"]) / "references" / "x.md").is_file()


async def test_subpath_on_direct_url_rejected(client):
    r = await client.post("/api/skills/install",
                          json={"url": "https://x.invalid/a/SKILL.md", "subpath": "skills/a"})
    assert r.status_code == 400
    assert "GitHub" in r.text


# ---- 002 首轮审查回归 ----


ROOT_CAND_TREE = [_blob("SKILL.md"), _blob("logo.png"), _blob("matrix.md"),
                  _blob("skills/a/SKILL.md"), _blob("skills/b/SKILL.md")]
ROOT_CAND_FILES = {"SKILL.md": SKILL_B, "logo.png": b"png", "matrix.md": b"m",
                   "skills/a/SKILL.md": SKILL_A, "skills/b/SKILL.md": SKILL_B}


async def test_root_candidate_resubmit_with_empty_subpath(client):
    """审查 B-1：根候选 path="" 二次提交必须装仓库根，不能再折叠成全仓扫描死循环。"""
    client.app.state.skills_http_transport = _gh_transport(ROOT_CAND_TREE, ROOT_CAND_FILES)
    r = await client.post("/api/skills/install", json={"url": "https://github.com/owner/repo"})
    assert r.status_code == 200
    assert [c["path"] for c in r.json()["candidates"]] == ["", "skills/a", "skills/b"]
    r = await client.post("/api/skills/install",
                          json={"url": "https://github.com/owner/repo", "subpath": ""})
    assert r.status_code == 201, r.text
    sk = r.json()
    assert sk["name"] == "Skill Beta"
    assert sk["subpath"] is None
    assert (Path(sk["skill_dir"]) / "SKILL.md").read_bytes() == SKILL_B
    assert (Path(sk["skill_dir"]) / "logo.png").is_file()
    # 根技能装根目录直系文件（与未指定 subpath 单命中装全仓语义一致，子目录文件也在目录树内）
    assert (Path(sk["skill_dir"]) / "matrix.md").is_file()


async def test_lowercase_skill_md_variant_installs(client):
    """审查 I-1：命中判定大小写不敏感，回取同规则——skill.md 变体不 KeyError→500。"""
    tree = [_blob("skill.md")]
    files = {"skill.md": SKILL_A}
    client.app.state.skills_http_transport = _gh_transport(tree, files)
    r = await client.post("/api/skills/install", json={"url": "https://github.com/owner/repo"})
    assert r.status_code == 201, r.text
    assert r.json()["name"] == "Skill Alpha"


async def test_non_json_tree_response_rejected_400(client):
    """审查 I-1：远端返非 JSON（登录页/HTML）要收敛 400，不 JSONDecodeError→500。"""
    import httpx

    async def handler(request):
        return httpx.Response(200, content=b"<html>not json</html>")
    client.app.state.skills_http_transport = httpx.MockTransport(handler)
    r = await client.post("/api/skills/install", json={"url": "https://github.com/owner/repo"})
    assert r.status_code == 400
    assert "JSON" in r.text


async def test_missing_metadata_size_running_guard(client):
    """审查 I-2：元数据 size 缺失时预检放行，下载中按实际字节跨文件累加即拒。"""
    # 13 个 900KB 文件均在 1MB 单文件限内，合计约 11.7MB，下载途中 running 越过 10MB 总额即拒
    tree = [{"path": "SKILL.md", "type": "blob"}] + [
        {"path": f"d/p{i}.bin", "type": "blob"} for i in range(13)]
    files = {"SKILL.md": SKILL_B,
             **{f"d/p{i}.bin": b"z" * (900 * 1024) for i in range(13)}}
    client.app.state.skills_http_transport = _gh_transport(tree, files)
    r = await client.post("/api/skills/install", json={"url": "https://github.com/owner/repo"})
    assert r.status_code == 400
    assert "总大小" in r.text


async def test_slash_branch_ref_fallback_retry(client):
    """审查 M-1：/tree/feature/x 被切成 ref=feature+subpath=x，404 时并入 ref 重试一次。"""
    tree = [_blob("skills/a/SKILL.md"), _blob("skills/a/references/x.md")]
    files = {"skills/a/SKILL.md": SKILL_A, "skills/a/references/x.md": b"ref x"}
    reqs = []
    client.app.state.skills_http_transport = _gh_transport(
        tree, files, repo="jane/dotfiles", ref="feature/x", bad_refs=("feature",),
        requests=reqs)
    r = await client.post("/api/skills/install",
                          json={"url": "https://github.com/jane/dotfiles/tree/feature/x"})
    assert r.status_code == 201, r.text
    assert r.json()["name"] == "Skill Alpha"
    paths = [q.url.path for q in reqs if q.url.host == "api.github.com"]
    assert "/repos/jane/dotfiles/git/trees/feature" in paths  # 首次尝试确实发生过
    assert "/repos/jane/dotfiles/git/trees/feature/x" in paths
