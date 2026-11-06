# tests/test_spa_static.py
"""SPA 静态托管的缓存语义（2026-11-06 线上演示机实证）。

症状：前端重新部署后，已经开过页面的用户在几个小时里继续看旧界面——侧栏折叠功能
已在服务器上（ChatView 分块逐字节与本地一致），页面却拿不到。

根因不在部署层，在响应头：index.html 只带 ETag/Last-Modified、不带 Cache-Control，
浏览器就按启发式有效期（RFC 7234 §4.2.2，经验值约 (now - last-modified) * 10%）自行
决定「还能直接用」，连条件请求都不发。而 index.html 是整条链路里唯一不带内容哈希的
文件——它一被缓存，它引用的那批旧哈希分块就跟着旧界面一起在本地复活。

所以契约是：入口 HTML 每次向服务器校验（no-cache，靠 ETag 走 304，代价很小），
带哈希的分块不被强行降级成每次都回源。
"""
import httpx
import pytest

from app.main import create_app

HTML_PATHS = ("/", "/index.html", "/chat")  # / 走目录索引、/chat 走 SPA 回退


@pytest.fixture
def spa_dist(tmp_path, monkeypatch):
    """造一份最小 dist：入口 HTML + 一个带内容哈希的分块，不碰仓库真实产物。"""
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>zerocode</title>", encoding="utf-8")
    (dist / "assets" / "index-yWBiOPGN.js").write_text("console.log('chunk')", encoding="utf-8")
    monkeypatch.setenv("ZEROCODE_FRONTEND_DIST", str(dist))
    return dist


@pytest.fixture
async def spa_client(spa_dist):
    """不经 lifespan 的裸 ASGI client：静态与 SPA 回退不碰数据库，用真 test_engine 反而绕远。"""
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        yield c


@pytest.mark.parametrize("path", HTML_PATHS)
async def test_入口html必须每次回源校验(spa_client, path):
    """入口 HTML 一律 no-cache——缺了它，浏览器会启发式缓存整个旧前端图。"""
    res = await spa_client.get(path)
    assert res.status_code == 200, f"{path} 应命中 SPA 入口：{res.text[:120]}"
    assert "no-cache" in res.headers.get("cache-control", ""), \
        f"{path} 缺 Cache-Control: no-cache，浏览器会按启发式缓存 index.html（实际 {res.headers.get('cache-control')!r}）"


@pytest.mark.parametrize("path", HTML_PATHS)
async def test_回源校验靠etag走304(spa_client, path):
    """no-cache 的代价必须很小：拿首次响应的 ETag 再请求，应命中 304 而非重传正文。"""
    first = await spa_client.get(path)
    etag = first.headers.get("etag")
    assert etag, f"{path} 应带 ETag，否则 no-cache 等于每次全量重传"
    again = await spa_client.get(path, headers={"If-None-Match": etag})
    assert again.status_code == 304
    assert not again.content


async def test_带哈希的分块不被强行降级(spa_client):
    """哈希文件名本身就能安全长缓存；本修复只管入口，别顺手改资产缓存策略。"""
    res = await spa_client.get("/assets/index-yWBiOPGN.js")
    assert res.status_code == 200
    assert "no-cache" not in res.headers.get("cache-control", "")


async def test_api路径的404语义不被spa回退吞掉(spa_client):
    """入口 HTML 放宽缓存的同时，/api 未命中仍须是 JSON 404（既有口径，防回归）。

    取 /api/auth/* 这条：guard 只拦非 auth 的 /api，用它能直达路由 404 → SPA 回退分支。"""
    res = await spa_client.get("/api/auth/不存在")
    assert res.status_code == 404
    assert res.headers.get("content-type", "").startswith("application/json")
