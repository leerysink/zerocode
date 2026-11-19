# tests/test_skill_display_api.py
"""技能多文件展示 HTTP 面（ADR-0061 / spec 2026-11-20-skill-multifile-display）。

最高接缝＝HTTP API：文件清单/读文本/读图片的 owner 隔离与围栏、list 聚合、
两段式快照预览（preview==install）。installer 远端用 httpx.MockTransport 注入。
注：本文件启动 app lifespan（client 夹具），须串行、避开 live dev 栈，且不与并行
pytest 争用 zerocode_test（见 spec §5 环境约束）。
"""
import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _skills_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path / "data"))


def _blob(path, size=16):
    return {"path": path, "type": "blob", "size": size}


TREE = [_blob("skills/pdf/SKILL.md"), _blob("skills/pdf/scripts/run.py"),
        _blob("skills/pdf/assets/logo.png"), _blob("skills/pdf/data.bin")]
FILES = {
    "skills/pdf/SKILL.md": b"---\nname: PDF\ndescription: d\n---\n# PDF\n",
    "skills/pdf/scripts/run.py": b"print(1)",
    "skills/pdf/assets/logo.png": b"\x89PNG\r\n\x1a\n",
    "skills/pdf/data.bin": b"\x00\x01",
}


def _gh_transport():
    import httpx

    async def handler(request):
        host, p = request.url.host, request.url.path
        if host == "api.github.com":
            if p == "/repos/owner/repo":
                return httpx.Response(200, json={"default_branch": "main"})
            if p.startswith("/repos/owner/repo/git/trees/"):
                return httpx.Response(200, json={"tree": TREE, "truncated": False})
            return httpx.Response(404)
        if host == "raw.githubusercontent.com":
            fp = "/".join(p.split("/")[4:])   # 剥 /owner/repo/main/
            if fp in FILES:
                return httpx.Response(200, content=FILES[fp])
            return httpx.Response(404)
        return httpx.Response(404)
    return httpx.MockTransport(handler)


async def _install_pdf(client):
    client.app.state.skills_http_transport = _gh_transport()
    r = await client.post("/api/skills/install",
                          json={"url": "https://github.com/owner/repo", "subpath": "skills/pdf"})
    assert r.status_code == 201, r.text
    return r.json()


async def test_list_aggregation_fields(client):
    sk = await _install_pdf(client)
    rows = (await client.get("/api/skills")).json()
    row = next(x for x in rows if x["id"] == sk["id"])
    assert row["file_count"] == 4
    assert row["total_bytes"] == sum(len(b) for b in FILES.values())
    assert row["has_scripts"] is True


async def test_files_tree_kinds(client):
    sk = await _install_pdf(client)
    r = await client.get(f"/api/skills/{sk['id']}/files")
    assert r.status_code == 200
    by = {f["path"]: f for f in r.json()["files"]}
    assert set(by) == {"SKILL.md", "scripts/run.py", "assets/logo.png", "data.bin"}
    assert by["scripts/run.py"]["kind"] == "text"
    assert by["assets/logo.png"]["kind"] == "image"
    assert by["data.bin"]["kind"] == "binary"


async def test_read_text_content_and_fence(client):
    sk = await _install_pdf(client)
    sid = sk["id"]
    r = await client.get(f"/api/skills/{sid}/files/content", params={"path": "scripts/run.py"})
    assert r.status_code == 200 and r.json()["content"] == "print(1)"
    assert (await client.get(f"/api/skills/{sid}/files/content",
                             params={"path": "data.bin"})).status_code == 400   # 非白名单
    assert (await client.get(f"/api/skills/{sid}/files/content",
                             params={"path": "../secret"})).status_code == 400   # 逃逸


async def test_read_image_and_type_fence(client):
    sk = await _install_pdf(client)
    sid = sk["id"]
    r = await client.get(f"/api/skills/{sid}/files/raw", params={"path": "assets/logo.png"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/png")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.content == FILES["skills/pdf/assets/logo.png"]
    assert (await client.get(f"/api/skills/{sid}/files/raw",
                             params={"path": "data.bin"})).status_code == 400   # 非图片


async def test_cross_account_files_404(two_auth_clients):
    alice, bob = two_auth_clients
    sk = await _install_pdf(alice)
    sid = sk["id"]
    assert (await bob.get(f"/api/skills/{sid}/files")).status_code == 404
    assert (await bob.get(f"/api/skills/{sid}/files/content",
                          params={"path": "SKILL.md"})).status_code == 404
    assert (await bob.get(f"/api/skills/{sid}/files/raw",
                          params={"path": "assets/logo.png"})).status_code == 404


async def test_preview_not_landed_then_install_from_snapshot(client):
    client.app.state.skills_http_transport = _gh_transport()
    r = await client.post("/api/skills/preview",
                          json={"url": "https://github.com/owner/repo", "subpath": "skills/pdf"})
    assert r.status_code == 200, r.text
    pv = r.json()
    assert pv["token"] and pv["name"] == "PDF"
    assert {f["path"] for f in pv["files"]} == {"SKILL.md", "scripts/run.py", "assets/logo.png", "data.bin"}
    assert "# PDF" in pv["skill_md"]
    # 预览不落地：列表仍空
    assert (await client.get("/api/skills")).json() == []
    # 确认安装从快照落盘，内容==预览
    ri = await client.post("/api/skills/install", json={"token": pv["token"]})
    assert ri.status_code == 201, ri.text
    sk = ri.json()
    assert (Path(sk["skill_dir"]) / "scripts" / "run.py").read_bytes() == b"print(1)"
    # 快照消费后失效
    assert (await client.post("/api/skills/install", json={"token": pv["token"]})).status_code == 400


async def test_preview_expired_token_rejected(client, tmp_path):
    client.app.state.skills_http_transport = _gh_transport()
    pv = (await client.post("/api/skills/preview",
                            json={"url": "https://github.com/owner/repo", "subpath": "skills/pdf"})).json()
    meta = tmp_path / "data" / "skill-preview" / pv["token"] / "meta.json"
    m = json.loads(meta.read_text(encoding="utf-8"))
    m["expires_at"] = 0
    meta.write_text(json.dumps(m), encoding="utf-8")
    r = await client.post("/api/skills/install", json={"token": pv["token"]})
    assert r.status_code == 400 and "过期" in r.text


async def test_preview_token_cross_owner_rejected(two_auth_clients):
    alice, bob = two_auth_clients
    alice.app.state.skills_http_transport = _gh_transport()
    pv = (await alice.post("/api/skills/preview",
                           json={"url": "https://github.com/owner/repo", "subpath": "skills/pdf"})).json()
    assert (await bob.post("/api/skills/install", json={"token": pv["token"]})).status_code == 400