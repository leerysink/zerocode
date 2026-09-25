# tests/test_skills_api.py
"""Ticket 001 技能直链安装示踪弹：install→list 闭环、frontmatter 校验、slug 后缀、
双账号隔离、GitHub 占位。最高接缝＝HTTP API（installer 远端用 httpx.MockTransport 注入）。
"""
import pytest
from pathlib import Path

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _skills_data_dir(tmp_path, monkeypatch):
    """技能母本落 tmp，绝不写仓库 data/。data_dir() 每次现读 env，故作用域内生效。"""
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path / "data"))


SKILL_MD = (b"---\n"
            b"name: PDF Processing\n"
            b"description: Handle PDF files end to end\n"
            b"---\n"
            b"# PDF\nSee [guide](references/guide.md).\n")


def _transport():
    import httpx

    def handler(request):
        if request.url.path.upper().endswith("SKILL.MD"):
            return httpx.Response(200, content=SKILL_MD)
        return httpx.Response(404)
    return httpx.MockTransport(handler)


async def test_install_then_list_and_disk(client):
    client.app.state.skills_http_transport = _transport()
    r = await client.post("/api/skills/install",
                          json={"url": "https://raw.invalid/o/r/master/pdf/SKILL.md"})
    assert r.status_code == 201, r.text
    sk = r.json()
    assert sk["name"] == "PDF Processing"
    assert sk["slug"] == "pdf-processing"
    assert sk["enabled"] is True
    assert sk["source_type"] == "url"
    # 列表可见
    rows = (await client.get("/api/skills")).json()
    assert [x["id"] for x in rows] == [sk["id"]]
    # 磁盘母本内容与来源逐字节一致
    assert (Path(sk["skill_dir"]) / "SKILL.md").read_bytes() == SKILL_MD


async def test_missing_description_rejected_with_fieldname(client):
    import httpx

    bad = b"---\nname: OnlyName\n---\nbody"

    async def handler(request):
        return httpx.Response(200, content=bad)
    client.app.state.skills_http_transport = httpx.MockTransport(handler)
    r = await client.post("/api/skills/install", json={"url": "https://x.invalid/a/SKILL.md"})
    assert r.status_code == 400
    assert "description" in r.text
    # 无半成品：列表仍空
    assert (await client.get("/api/skills")).json() == []


async def test_same_name_reinstall_gets_slug_suffix(client):
    client.app.state.skills_http_transport = _transport()
    r1 = await client.post("/api/skills/install", json={"url": "https://x.invalid/a/SKILL.md"})
    r2 = await client.post("/api/skills/install", json={"url": "https://x.invalid/a/SKILL.md"})
    s1, s2 = r1.json(), r2.json()
    assert s1["slug"] == "pdf-processing"
    assert s2["slug"] == "pdf-processing-2"
    assert s1["id"] != s2["id"]
    # 两个母本目录并存且不同
    assert s1["skill_dir"] != s2["skill_dir"]
    assert Path(s2["skill_dir"], "SKILL.md").is_file()


async def test_github_repo_url_scans_via_github_channel(client):
    """002 后 GitHub 仓库 URL 不再占位报错：扫到多命中返候选（详表见 github 测试文件）。"""
    import httpx

    async def handler(request):
        if request.url.path == "/repos/owner/repo":
            return httpx.Response(200, json={"default_branch": "main"})
        tree = [{"path": "skills/a/SKILL.md", "type": "blob", "size": 10},
                {"path": "skills/b/SKILL.md", "type": "blob", "size": 10}]
        return httpx.Response(200, json={"tree": tree, "truncated": False})
    client.app.state.skills_http_transport = httpx.MockTransport(handler)
    r = await client.post("/api/skills/install", json={"url": "https://github.com/owner/repo"})
    assert r.status_code == 200
    assert [c["path"] for c in r.json()["candidates"]] == ["skills/a", "skills/b"]


async def test_direct_url_must_point_to_skill_md(client):
    client.app.state.skills_http_transport = _transport()
    r = await client.post("/api/skills/install", json={"url": "https://x.invalid/a/readme.md"})
    assert r.status_code == 400


async def test_two_accounts_list_isolated(two_auth_clients):
    alice, bob = two_auth_clients
    alice.app.state.skills_http_transport = _transport()
    r = await alice.post("/api/skills/install", json={"url": "https://x.invalid/a/SKILL.md"})
    assert r.status_code == 201
    assert [x["id"] for x in (await alice.get("/api/skills")).json()] == [r.json()["id"]]
    # bob 完全看不到 alice 的技能
    assert (await bob.get("/api/skills")).json() == []


async def test_requires_login(anon_client):
    assert (await anon_client.get("/api/skills")).status_code == 401


# ---- 审查修复回归：SSRF/长度/网络异常/限长/不安全引用 ----

async def test_intranet_or_nonhttp_url_rejected(client):
    import httpx

    async def handler(request):
        return httpx.Response(200, content=SKILL_MD)
    client.app.state.skills_http_transport = httpx.MockTransport(handler)
    for bad in ("http://127.0.0.1/a/SKILL.md",
                "http://169.254.169.254/latest/meta/SKILL.md",
                "http://10.0.0.8/x/SKILL.md",
                "http://localhost:8080/a/SKILL.md",
                "file:///tmp/a/SKILL.md"):
        r = await client.post("/api/skills/install", json={"url": bad})
        assert r.status_code == 400, bad
    assert (await client.get("/api/skills")).json() == []


async def test_overlong_url_rejected(client):
    r = await client.post("/api/skills/install",
                          json={"url": "https://x.invalid/" + "a" * 1200 + "/SKILL.md"})
    assert r.status_code == 400


async def test_overlong_name_rejected(client):
    import httpx

    bad = ("---\nname: " + "N" * 300 + "\ndescription: d\n---\n").encode()

    async def handler(request):
        return httpx.Response(200, content=bad)
    client.app.state.skills_http_transport = httpx.MockTransport(handler)
    r = await client.post("/api/skills/install", json={"url": "https://x.invalid/a/SKILL.md"})
    assert r.status_code == 400
    assert (await client.get("/api/skills")).json() == []


async def test_oversize_file_rejected(client):
    import httpx

    huge = SKILL_MD + b"x" * (1024 * 1024 + 10)

    async def handler(request):
        return httpx.Response(200, content=huge)
    client.app.state.skills_http_transport = httpx.MockTransport(handler)
    r = await client.post("/api/skills/install", json={"url": "https://x.invalid/a/SKILL.md"})
    assert r.status_code == 400
    assert "大小上限" in r.text
    assert (await client.get("/api/skills")).json() == []


async def test_network_error_maps_to_400(client):
    import httpx

    async def handler(request):
        raise httpx.ConnectError("boom", request=request)
    client.app.state.skills_http_transport = httpx.MockTransport(handler)
    r = await client.post("/api/skills/install", json={"url": "https://x.invalid/a/SKILL.md"})
    assert r.status_code == 400  # 不能是 500


async def test_unsafe_relative_refs_skipped(client):
    import httpx

    md = (b"---\nname: RefSkill\ndescription: d\n---\n"
          b"[ok](references/guide.md) [evil](../evil.md) [up](./b.md)\n")

    async def handler(request):
        if request.url.path.upper().endswith("SKILL.MD"):
            return httpx.Response(200, content=md)
        return httpx.Response(200, content=b"ref body")
    client.app.state.skills_http_transport = httpx.MockTransport(handler)
    r = await client.post("/api/skills/install", json={"url": "https://x.invalid/a/SKILL.md"})
    assert r.status_code == 201, r.text  # 非法引用不拖死整次安装
    skill_dir = Path(r.json()["skill_dir"])
    assert (skill_dir / "references" / "guide.md").is_file()
    assert (skill_dir / "b.md").is_file()
    assert not (skill_dir.parent / "evil.md").exists()


async def test_redirect_to_internal_host_rejected(client):
    """审查 I-1：公网 URL 借 302 跳内网必须被拦，且内网请求根本不发出（002 逐跳预检）。"""
    import httpx

    seen = []

    async def handler(request):
        seen.append(request.url.host)
        if request.url.host == "outer.invalid":
            return httpx.Response(302, headers={"location": "http://127.0.0.1:5433/x/SKILL.md"})
        return httpx.Response(200, content=SKILL_MD)
    client.app.state.skills_http_transport = httpx.MockTransport(handler)
    r = await client.post("/api/skills/install", json={"url": "https://outer.invalid/a/SKILL.md"})
    assert r.status_code == 400
    assert "内网" in r.text
    assert "127.0.0.1" not in seen  # 落点预检在请求前，不是事后拦读
    assert (await client.get("/api/skills")).json() == []


async def test_public_redirect_chain_followed(client):
    """公网→公网的合法重定向仍能装成（逐跳手动跟不再误伤）。"""
    import httpx

    async def handler(request):
        if request.url.host == "short.invalid":
            return httpx.Response(301, headers={"location": "https://long.invalid/a/SKILL.md"})
        return httpx.Response(200, content=SKILL_MD)
    client.app.state.skills_http_transport = httpx.MockTransport(handler)
    r = await client.post("/api/skills/install", json={"url": "https://short.invalid/a/SKILL.md"})
    assert r.status_code == 201, r.text


async def test_obfuscated_ip_host_rejected(client):
    """审查 M-1：inet_aton 缩写/十六进制形态的回环地址不得当域名放行。"""
    import httpx

    async def handler(request):
        return httpx.Response(200, content=SKILL_MD)
    client.app.state.skills_http_transport = httpx.MockTransport(handler)
    for bad in ("http://2130706433/a/SKILL.md",
                "http://127.1/a/SKILL.md",
                "http://0x7f000001/a/SKILL.md",
                "http://127.1./a/SKILL.md",
                "http://169.254.169.254./x/SKILL.md"):
        r = await client.post("/api/skills/install", json={"url": bad})
        assert r.status_code == 400, bad
    assert (await client.get("/api/skills")).json() == []


async def test_oversize_optional_ref_skipped_not_fatal(client):
    """审查 M-2：可选引用文件超限降级为跳过，本体照装。"""
    import httpx

    md = (b"---\nname: BigRef\ndescription: d\n---\n"
          b"[ok](references/guide.md) [big](big.md)\n")

    async def handler(request):
        if request.url.path.upper().endswith("SKILL.MD"):
            return httpx.Response(200, content=md)
        if request.url.path.endswith("big.md"):
            return httpx.Response(200, content=b"x" * (1024 * 1024 + 1))
        return httpx.Response(200, content=b"small ref")
    client.app.state.skills_http_transport = httpx.MockTransport(handler)
    r = await client.post("/api/skills/install", json={"url": "https://x.invalid/a/SKILL.md"})
    assert r.status_code == 201, r.text
    skill_dir = Path(r.json()["skill_dir"])
    assert (skill_dir / "SKILL.md").is_file()
    assert (skill_dir / "references" / "guide.md").is_file()
    assert not (skill_dir / "big.md").exists()


async def test_db_failure_cleans_up_skill_dir(client, monkeypatch, tmp_path):
    """审查 Blocker #1 兜底半边：DB 写入非 IntegrityError 异常不得留永久孤儿母本。"""
    from sqlalchemy.exc import DBAPIError

    import app.api.skills as skills_api
    client.app.state.skills_http_transport = _transport()
    # 把 RETURNING 列换成不存在的列，让 INSERT 在 DB 端炸出非 IntegrityError 异常
    monkeypatch.setattr(skills_api, "_COLUMNS", "id, no_such_column")
    with pytest.raises(DBAPIError):
        await client.post("/api/skills/install", json={"url": "https://x.invalid/a/SKILL.md"})
    # 母本目录已被清理：users/*/skills 下不留任何子目录/文件
    assert list((tmp_path / "data").glob("users/*/skills/*")) == []
