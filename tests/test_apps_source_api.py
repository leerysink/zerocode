# tests/test_apps_source_api.py
"""建应用带仓库地址：先抓快照再建应用，失败 400 且零副作用。

transport 经 app.state.source_http_transport 注入（照 app.state.skills_http_transport 的形状，
见 tests/test_skills_github_api.py:73），真网络一个都不碰。
"""
import json
import shutil
from urllib.parse import unquote

import httpx
import pytest
from sqlalchemy import text

from app import config as app_config
from app.db import make_engine, make_session_factory
from app.deploy import provision
from app.deploy.settings import REPO_ROOT
from app.source import ingest, materialize

pytestmark = pytest.mark.asyncio
SLUG = "zcesrcapi"
APP_DIR = REPO_ROOT / "apps" / SLUG
URL = "https://gitee.com/leerys/zerocode"
# 快照的读路径钉在 materialize 的写路径常量上（同 tests/test_source_seam.py:76）：
# 这里再硬编一遍 "frontend/public/source/..."，目录改名时测试会假绿或假红，而读不出真实契约。
SNAPSHOT_DIR = APP_DIR / "/".join(materialize.SOURCE_ROOT_PARTS)
MANIFEST_PATH = APP_DIR / materialize.MANIFEST_REL


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
            # raw 端点收到的是百分号编码后的路径（`hosting.raw_file_url` 里的 `quote(path)`），
            # 夹具按解码后的仓库原路径查表，才演得出 `docs/c#1.md` 这类名字的真实往返。
            fp = unquote(fp)
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


def _manifest():
    """读落盘 manifest：路径走 materialize.MANIFEST_REL，与写侧同源。"""
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _assert_no_snapshot(reason: str) -> None:
    """「没摄入」要断言的是快照本身，不是 `frontend/public` 存不存在——
    static 模板今天恰好没有 public/，等哪天模板加了一个 public/，
    旧断言就会替「其实落了快照」这件事作假证。"""
    assert not SNAPSHOT_DIR.exists(), reason
    assert not MANIFEST_PATH.exists(), reason


async def test_create_with_source_snapshots_into_workspace(client):
    client.app.state.source_http_transport = _transport(TREE, FILES)
    r = await client.post("/api/apps", json=_body())
    assert r.status_code == 201, r.text
    assert MANIFEST_PATH.is_file()
    assert (APP_DIR / f"{materialize.FILES_REL_PREFIX}docs/a.md").read_bytes() == b"# A\n"
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
    _assert_no_snapshot("空 url 却落了快照")


async def test_subpath_narrows_the_snapshot(client):
    client.app.state.source_http_transport = _transport(TREE, FILES)
    r = await client.post("/api/apps", json=_body(source={"url": URL, "subpath": "docs"}))
    assert r.status_code == 201, r.text
    m = _manifest()
    assert m["subpath"] == "docs"
    assert {f["path"] for f in m["files"]} == {"docs/a.md"}


async def test_unmatched_subpath_is_400_and_creates_no_app(client):
    """端点级闸：抓取侧的「该目录下没有可摄入的文本」不许在 HTTP 层被吞成 201。
    拼错的目录名与「擦干净的输入框」是两件事——后者已由 ingest 归一成仓库根，前者必须报错。"""
    client.app.state.source_http_transport = _transport(TREE, FILES)
    r = await client.post("/api/apps", json=_body(source={"url": URL, "subpath": "doc"}))
    assert r.status_code == 400
    assert "没有可摄入的文本" in r.json()["detail"]
    assert not APP_DIR.exists()


async def test_explicit_empty_subpath_means_repo_root_not_scan_all(client):
    """三态不许在 HTTP 层被折叠（Task 3 在 ingest.py 里把这条写成契约）：
    `subpath=""` = 就要仓库根，缺省 = 全域扫描，URL 自带 /tree/<ref>/<subpath> = 钻那个目录。
    仓库根形态与全域恰好同产物，所以这里断言的是「参数原样传下去、没被 `or None` 折成缺省」。

    两条请求都发：第一条是 brief 的原始形态（URL 不带目录，此时 "" 与缺省**确实**同产物，
    它只守住「显式空串不能把快照弄窄」）；第二条才照出 `or None`——URL 带着 /tree/master/docs
    而请求显式 subpath=""，折叠一次就把「就要仓库根」悄悄换成「钻那个目录」，Task 7 向导
    「留空即整仓 + 粘目录 URL」正是这条路径。语义本身由 tests/test_source_ingest.py 的
    test_explicit_empty_subpath_overrides_url_carried_scope /
    test_absent_subpath_falls_back_to_url_carried_scope 钉在 ingest 层，这里只钉 HTTP 层不折叠。
    """
    client.app.state.source_http_transport = _transport(TREE, FILES)
    r = await client.post("/api/apps", json=_body(source={"url": URL, "subpath": ""}))
    assert r.status_code == 201, r.text
    m = _manifest()
    assert m["subpath"] is None                     # 仓库根归一成 None（快照不是一条子目录）
    assert {f["path"] for f in m["files"]} == {"README.md", "docs/a.md"}

    await _wipe()
    r = await client.post("/api/apps",
                          json=_body(source={"url": f"{URL}/tree/master/docs", "subpath": ""}))
    assert r.status_code == 201, r.text
    m = _manifest()
    # 显式 "" 优先于 URL 带进来的目录：折叠成 None 的话这里会是 "docs" + 只剩 docs/a.md
    assert m["subpath"] is None, "subpath=\"\" 被 HTTP 层折成了缺省，URL 里的目录反过来赢了"
    assert {f["path"] for f in m["files"]} == {"README.md", "docs/a.md"}


async def test_request_without_source_field_still_creates_normally(client):
    r = await client.post("/api/apps", json={"name": "无快照", "slug": SLUG,
                                             "scale": "small", "stack": "static"})
    assert r.status_code == 201, r.text
    _assert_no_snapshot("没带 source 却落了快照")


async def test_source_on_unsupported_stack_reports_clearly(client):
    """large 栈带后端：快照照落，但 README 段要说清前端目录在哪——这里钉住不因栈被拦死。"""
    client.app.state.source_http_transport = _transport(TREE, FILES)
    r = await client.post("/api/apps", json=_body(stack="vue3-springboot", scale="large",
                                                  source={"url": URL}))
    assert r.status_code == 201, r.text
    assert (APP_DIR / f"{materialize.FILES_REL_PREFIX}README.md").is_file()


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
        _assert_no_snapshot(f"url={blank!r} 却落了快照")
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


# ---- 终审整枝期的三条跨任务结论（F1 凭证泄漏 / F2 坏 URL 成 500 / F4 manifest 的 fetch 目标）----


async def test_url_with_credentials_is_400_and_stores_nothing(client):
    """终审 F1（安全）：粘在 URL 里的 token 曾经一路通过 SSRF 判据，最后冻进 `manifest.sourceUrl`
    与 README 出处行 —— 而 `manifest.json` 是发布后 `/source/manifest.json` 上公开可读的文件，
    这正是本特性的全部意义。

    这条用例断言的不是「状态码是 400」，而是**存储形态**：拒在建应用之前，所以工作区、快照目录、
    manifest、README 与 app 行统统不存在，凭证没有一个字节落盘；400 detail 里也不出现它
    （detail 会进响应体与访问日志，把凭证从快照搬到那里等于没修）。
    """
    reqs = []
    client.app.state.source_http_transport = _transport(TREE, FILES, requests=reqs)
    r = await client.post("/api/apps", json=_body(
        source={"url": "https://oauth2:GLtok-SECRET123@gitee.com/leerys/zerocode"}))
    assert r.status_code == 400, r.text
    assert "SECRET123" not in r.text
    assert "令牌" in r.json()["detail"]
    assert reqs == []
    assert not APP_DIR.exists(), "带凭证的 URL 却留下了工作区"
    _assert_no_snapshot("带凭证的 URL 居然落了快照")
    assert await _app_rows(client) == []


async def test_unparsable_url_is_400_not_500(client):
    """终审 F2：未闭合的 IPv6（`http://[::1/x`）在守卫之前抛的是**裸 ValueError**，
    曾经直接穿过这里那句 `except SourceError` → 一次 500 + traceback，而规格 US-10/§5.4
    承诺的是「失败只以 400 类领域错误出去」。现在映射是全函数。
    """
    reqs = []
    client.app.state.source_http_transport = _transport(TREE, FILES, requests=reqs)
    for bad in ("http://[::1/x", "https://[::1"):
        r = await client.post("/api/apps", json=_body(source={"url": bad}))
        assert r.status_code == 400, (bad, r.text)
        assert "URL 形态非法" in r.json()["detail"]
    assert reqs == []
    assert not APP_DIR.exists()
    assert await _app_rows(client) == []


async def test_wall_clock_budget_exhausted_is_400_and_creates_no_app(client, monkeypatch):
    """终审 F5：一次摄入有一条**聚合墙钟**预算（httpx 的 20 秒是每段读超时，封不住慢滴）。
    端点级要钉的是不变量 (e)：超线发生在建应用之前 —— 没有应用行、没有目录、没有任何请求。
    """
    reqs = []
    client.app.state.source_http_transport = _transport(TREE, FILES, requests=reqs)
    monkeypatch.setattr(ingest, "WALL_CLOCK_BUDGET_SECONDS", 0.0)
    r = await client.post("/api/apps", json=_body())
    assert r.status_code == 400, r.text
    assert "时间预算" in r.json()["detail"]
    assert reqs == []
    assert not APP_DIR.exists()
    assert await _app_rows(client) == []


async def test_manifest_url_is_the_fetch_target_and_matches_the_name_on_disk(client):
    """终审 F4 端点级：`files[].url` 必须是页面真能 fetch 到的地址。

    这里能给的证明是「url 解码后正好落在写侧的快照目录里，且那个文件真实存在」——
    站点侧 `fetch(entry.url)` 打的就是它（Vite 把 `public/` 原样直出，规格 §2.2/§5.3）。
    只断言 `path` 会替「带 `#` 的那篇永远 404」作假证：原串拼进 fetch 打不到任何文件。
    """
    tree = [_blob("docs/c#1.md"), _blob("docs/a b.md"), _blob("docs/a%20b.md")]
    bodies = {e["path"]: b"# x" for e in tree}
    client.app.state.source_http_transport = _transport(tree, bodies)
    r = await client.post("/api/apps", json=_body())
    assert r.status_code == 201, r.text
    m = _manifest()
    assert {f["path"] for f in m["files"]} == set(bodies), "展示字段必须仍是仓库原路径"
    assert {f["url"] for f in m["files"]} == {
        "/source/files/docs/c%231.md",
        "/source/files/docs/a%20b.md",
        "/source/files/docs/a%2520b.md",    # 字面 %20 再编码一次，不然会被解回 'a b.md'
    }
    files_dir = APP_DIR / materialize.FILES_REL_PREFIX
    for entry in m["files"]:
        assert entry["url"].startswith(materialize.FILES_URL_PREFIX)
        landing = files_dir / unquote(entry["url"][len(materialize.FILES_URL_PREFIX):])
        assert landing.is_file(), f"manifest 给的 url 在盘上取不到：{entry}"
