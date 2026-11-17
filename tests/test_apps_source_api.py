# tests/test_apps_source_api.py
"""建应用带仓库地址：先抓快照再建应用，失败 400 且零副作用。

transport 经 app.state.source_http_transport 注入（照 app.state.skills_http_transport 的形状，
见 tests/test_skills_github_api.py:73），真网络一个都不碰。
"""
import shutil

import httpx
import pytest
from sqlalchemy import text

from app import config as app_config
from app.db import make_engine, make_session_factory
from app.deploy import provision
from app.deploy.settings import REPO_ROOT

pytestmark = pytest.mark.asyncio
SLUG = "zcesrcapi"
APP_DIR = REPO_ROOT / "apps" / SLUG
URL = "https://gitee.com/leerys/zerocode"


def _blob(p, size=20):
    return {"path": p, "mode": "100644", "type": "blob", "sha": "s" + p[:5], "size": size}


def _transport(tree, files, *, status=200, requests=None):
    async def handler(request):
        if requests is not None:
            requests.append(str(request.url))
        p = request.url.path
        if request.url.host != "gitee.com":
            return httpx.Response(404)
        if "/git/trees/" in p:
            return httpx.Response(status, json={"tree": tree, "truncated": False}
                                  if status == 200 else {"message": "no"})
        if p == "/api/v5/repos/leerys/zerocode":
            return httpx.Response(status, json={"default_branch": "master"}
                                  if status == 200 else {"message": "no"})
        if p.startswith("/leerys/zerocode/raw/"):
            fp = "/".join(p.split("/")[5:])
            return httpx.Response(200, content=files.get(fp, b"")) if fp in files \
                else httpx.Response(404)
        return httpx.Response(404)
    return httpx.MockTransport(handler)


TREE = [_blob("README.md"), _blob("docs/a.md")]
FILES = {"README.md": b"# ZeroCode\n", "docs/a.md": b"# A\n"}


async def _wipe():
    eng = make_engine(app_config.test_database_url())
    sf = make_session_factory(eng)
    try:
        for env in ("dev", "prod"):
            try:
                await provision.drop_env(sf, SLUG, env)
            except Exception:
                pass
        async with sf() as s:
            await s.execute(text("DELETE FROM app_project WHERE slug=:s"), {"s": SLUG})
            w = (await s.execute(text("SELECT id FROM workspace WHERE root_path LIKE :p"),
                                 {"p": f"%\\{SLUG}"})).scalars().all()
            for wid in w:
                await s.execute(text("DELETE FROM workspace WHERE id=:i"), {"i": wid})
            await s.commit()
        shutil.rmtree(APP_DIR, ignore_errors=True)
    finally:
        await eng.dispose()


@pytest.fixture(autouse=True)
async def _clean(test_engine):
    await _wipe()
    yield
    await _wipe()


def _body(**over):
    b = {"name": "官网摄入", "slug": SLUG, "scale": "small", "stack": "static",
         "source": {"url": URL}}
    b.update(over)
    return b


async def _origin(client):
    async with client.app.state.session_factory() as s:
        return (await s.execute(text("SELECT origin FROM app_project WHERE slug=:s"),
                               {"s": SLUG})).scalar()


async def _app_rows(client):
    return (await client.get("/api/apps")).json()


async def test_create_with_source_snapshots_into_workspace(client):
    client.app.state.source_http_transport = _transport(TREE, FILES)
    r = await client.post("/api/apps", json=_body())
    assert r.status_code == 201, r.text
    assert (APP_DIR / "frontend/public/source/manifest.json").is_file()
    assert (APP_DIR / "frontend/public/source/files/docs/a.md").read_bytes() == b"# A\n"
    # 读库直接用 app.state.session_factory：brief 原文的 make_session_factory(...) 拿到的是
    # sessionmaker 而非 engine，SQLAlchemy 直接 ArgumentError（夹具笔误，断言本身不变）。
    async with client.app.state.session_factory() as s:
        got = (await s.execute(text("SELECT origin FROM app_project WHERE slug=:s"),
                               {"s": SLUG})).scalar()
    assert got == "repo:gitee"


async def test_rate_limited_gitee_is_400_and_no_app_created(client):
    before = (await client.get("/api/apps")).json()
    client.app.state.source_http_transport = _transport(TREE, FILES, status=403)
    r = await client.post("/api/apps", json=_body())
    assert r.status_code == 400
    assert "配额" in r.json()["detail"]
    assert not APP_DIR.exists(), "摄入失败却建了工作区"
    after = (await client.get("/api/apps")).json()
    assert {a["slug"] for a in after} == {a["slug"] for a in before}


async def test_non_gitee_url_says_what_is_supported(client):
    client.app.state.source_http_transport = _transport(TREE, FILES)
    r = await client.post("/api/apps", json=_body(source={"url": "https://example.com/o/r"}))
    assert r.status_code == 400 and "gitee.com" in r.json()["detail"]


async def test_internal_url_rejected(client):
    client.app.state.source_http_transport = _transport(TREE, FILES)
    for bad in ("https://127.0.0.1/o/r", "https://localhost/o/r"):
        r = await client.post("/api/apps", json=_body(source={"url": bad}))
        assert r.status_code == 400, bad


async def test_empty_source_url_is_ignored(client):
    """空串当没填：不许把一个空格变成一次网络请求，也不许报错拦住正常创建。"""
    r = await client.post("/api/apps", json=_body(source={"url": "   "}))
    assert r.status_code == 201, r.text
    assert not (APP_DIR / "frontend" / "public").exists()


async def test_subpath_narrows_the_snapshot(client):
    client.app.state.source_http_transport = _transport(TREE, FILES)
    r = await client.post("/api/apps", json=_body(source={"url": URL, "subpath": "docs"}))
    assert r.status_code == 201, r.text
    import json
    m = json.loads((APP_DIR / "frontend/public/source/manifest.json").read_text(encoding="utf-8"))
    assert m["subpath"] == "docs"
    assert {f["path"] for f in m["files"]} == {"docs/a.md"}


async def test_request_without_source_field_still_creates_normally(client):
    r = await client.post("/api/apps", json={"name": "无快照", "slug": SLUG,
                                             "scale": "small", "stack": "static"})
    assert r.status_code == 201, r.text
    assert not (APP_DIR / "frontend" / "public").exists()


async def test_source_on_unsupported_stack_reports_clearly(client):
    """large 栈带后端：快照照落，但 README 段要说清前端目录在哪——这里钉住不因栈被拦死。"""
    client.app.state.source_http_transport = _transport(TREE, FILES)
    r = await client.post("/api/apps", json=_body(stack="vue3-springboot", scale="large",
                                                  source={"url": URL}))
    assert r.status_code == 201, r.text
    assert (APP_DIR / "frontend/public/source/files/README.md").is_file()


# ---- 接线层的三条裁定（Task 5 审查遗留 + 401/超预算两条错误路径）----


async def test_source_object_without_url_never_reaches_the_seam(client):
    """`source={}` 是 schema 层的事：422 立答，一次请求都不发、一行应用都不落。"""
    reqs = []
    client.app.state.source_http_transport = _transport(TREE, FILES, requests=reqs)
    r = await client.post("/api/apps", json=_body(source={}))
    assert r.status_code == 422, r.text
    assert reqs == []
    assert not APP_DIR.exists()
    assert await _app_rows(client) == []


async def test_blank_source_url_makes_no_request_at_all(client):
    """空串 / 空格 / 制表符都算「没填」：strip 判定要能在变异测试里红——这里数请求条数。"""
    reqs = []
    client.app.state.source_http_transport = _transport(TREE, FILES, requests=reqs)
    for blank in ("", "   ", "\t\n"):
        r = await client.post("/api/apps", json=_body(source={"url": blank, "subpath": "docs"}))
        assert r.status_code == 201, (blank, r.text)
        assert not (APP_DIR / "frontend" / "public").exists()
        await _wipe()
    assert reqs == []


async def test_origin_is_written_only_when_a_snapshot_was_ingested(client):
    """origin 只在真摄入了快照时写：不填 source 与填空格 url 都得留 NULL，响应形状一字不变。"""
    r = await client.post("/api/apps", json={"name": "无快照", "slug": SLUG,
                                             "scale": "small", "stack": "static"})
    assert r.status_code == 201, r.text
    assert "source" not in r.json()
    assert await _origin(client) is None
    await _wipe()
    r = await client.post("/api/apps", json=_body(source={"url": "  "}))
    assert r.status_code == 201, r.text
    assert await _origin(client) is None


async def test_private_repo_401_maps_to_400(client):
    client.app.state.source_http_transport = _transport(TREE, FILES, status=401)
    r = await client.post("/api/apps", json=_body())
    assert r.status_code == 400, r.text
    assert "公开仓库" in r.json()["detail"]
    assert not APP_DIR.exists()


async def test_declared_oversize_tree_is_400_without_downloading(client):
    """按树里声明的 size 预估超预算：400 且一个 raw 都不发（零副作用含「零下载」）。"""
    reqs = []
    client.app.state.source_http_transport = _transport(
        [_blob("big.md", size=9 * 1024 * 1024)], {}, requests=reqs)
    r = await client.post("/api/apps", json=_body())
    assert r.status_code == 400, r.text
    assert "总大小" in r.json()["detail"]
    assert not any("/raw/" in u for u in reqs)
    assert not APP_DIR.exists()
