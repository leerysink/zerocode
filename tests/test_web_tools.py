# tests/test_web_tools.py
"""联网读取 web_fetch（ADR-0053）：URL 闸、DNS 闸、HTML 提取、重定向复检与配额。

网络与 DNS 一律替身：这里验 SSRF 防线与结果形状，真站点抓取由手工矩阵负责。
"""
import asyncio
import socket
import types

import httpx

from app.agent import web_tools as wt


class Ctx:
    """工具上下文桩（与 tests/test_browser_session_tools.py 的 Ctx 同形，只留 web 需要的字段）。"""

    def __init__(self, cfg=None, run_state=None):
        self.cfg = cfg or {}
        self.run_state = run_state if run_state is not None else {}
        self.session_id = 7
        self.sent = []

    async def send(self, ev):
        self.sent.append(ev)


# --------------------------------------------------------------------- URL 闸

def test_normalize_accepts_public_http_and_https():
    for url in ("https://example.com/a?b=1", "http://example.com/",
                "https://93.184.216.34/x"):
        got, err = wt.normalize_web_url(url)
        assert err == "" and got == url, (url, err)


def test_normalize_rejects_malformed_scheme_and_credentials():
    for raw in (None, "", "   ", 42):
        got, err = wt.normalize_web_url(raw)
        assert got == "" and err
    for url, needle in (
        ("ftp://example.com/", "http"),
        ("file:///etc/passwd", "http"),
        ("https://user:pw@example.com/", "用户名"),
        ("https://example.com/" + "a" * 2100, "过长"),
        ("https://example.com:999999/", "端口"),
        ("https://exa\nmple.com/", "控制字符"),
        ("http:///nohost", "host"),
    ):
        got, err = wt.normalize_web_url(url)
        assert got == "" and needle in err, (url, err)


def test_normalize_rejects_private_hosts_and_ips():
    for url in ("http://localhost/", "http://app.internal/x", "http://my.local/",
                "http://host.docker.internal:9000/", "http://backend:8000/",
                "http://zerocode-gateway/", "http://127.0.0.1/", "http://10.0.0.8/",
                "http://192.168.1.5/", "http://[::1]/",
                "http://169.254.169.254/latest/meta-data/"):
        got, err = wt.normalize_web_url(url)
        assert got == "" and err, url


# --------------------------------------------------------------------- DNS 闸

class _FakeLoop:
    def __init__(self, infos=None, exc=None):
        self.infos = infos or []
        self.exc = exc

    async def getaddrinfo(self, host, port, type=None):
        if self.exc is not None:
            raise self.exc
        return self.infos


def _pin_asyncio(monkeypatch, loop):
    async def wait_for(aw, timeout):
        return await aw

    monkeypatch.setattr(wt, "asyncio", types.SimpleNamespace(
        wait_for=wait_for, get_running_loop=lambda: loop,
        TimeoutError=asyncio.TimeoutError))


def _addr(ip):
    return (socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))


async def test_check_host_passes_when_all_results_are_public(monkeypatch):
    _pin_asyncio(monkeypatch, _FakeLoop([_addr("93.184.216.34"), _addr("1.1.1.1")]))
    assert await wt._check_host("https://example.com/") == ""


async def test_check_host_blocks_any_private_resolution(monkeypatch):
    _pin_asyncio(monkeypatch, _FakeLoop([_addr("93.184.216.34"), _addr("10.0.0.5")]))
    err = await wt._check_host("https://example.com/")
    assert "10.0.0.5" in err and "example.com" in err


async def test_check_host_passes_on_resolution_failure(monkeypatch):
    _pin_asyncio(monkeypatch, _FakeLoop(exc=socket.gaierror("no dns")))
    assert await wt._check_host("https://no-such-host.invalid/") == ""


async def test_check_host_literal_ip_needs_no_dns():
    assert await wt._check_host("https://93.184.216.34/") == ""
    assert "非公网" in await wt._check_host("http://127.0.0.1/")


# --------------------------------------------------------------------- HTML 提取

def test_extract_html_title_text_links_and_description():
    html = """<html><head><title>订单 列表</title>
    <meta name="description" content="全部订单"></head>
    <body><script>ignore()</script><h1>订单</h1><p>共 <b>3</b> 条</p>
    <a href="/orders/1">详情</a><a href="javascript:void(0)">点我</a>
    <a href="https://other.example/x#frag">外部</a></body></html>"""
    data = wt._extract_html(html, "https://example.com/list")
    assert data["title"] == "订单 列表"
    assert "ignore()" not in data["text"] and "订单" in data["text"]
    urls = [item["url"] for item in data["links"]]
    assert "https://example.com/orders/1" in urls
    assert "https://other.example/x" in urls
    assert all("javascript:" not in u for u in urls)
    assert data["description"] == "全部订单"


def test_content_kind_and_charset():
    assert wt._content_kind("text/html; charset=utf-8", b"") == "html"
    assert wt._content_kind("application/json", b"") == "text"
    assert wt._content_kind("", b"<!doctype html><html>") == "html"
    assert wt._content_kind("image/png", b"\x89PNG") == "binary"
    assert wt._charset_of('text/html; charset="GBK"') == "gb18030"
    assert wt._charset_of("text/html; charset=gb2312") == "gb18030"
    assert wt._charset_of("text/plain") == "utf-8"


# --------------------------------------------------------------------- 配额

def test_fetches_left_reads_cfg_and_run_state():
    ctx = Ctx(cfg={"agent.web_max_fetches_per_run": 3}, run_state={"web_fetches": 2})
    assert wt._fetches_left(ctx) == 1
    ctx.run_state["web_fetches"] = 9
    assert wt._fetches_left(ctx) == 0
    assert wt._fetches_left(Ctx(cfg={"agent.web_max_fetches_per_run": 0})) == -1
    assert wt._fetches_left(Ctx()) == wt.DEFAULT_MAX_FETCHES


# --------------------------------------------------------------------- 抓取流程

class _Resp:
    def __init__(self, status=200, headers=None, body=b"", url="https://example.com/"):
        self.status_code = status
        self.headers = headers or {}
        self._body = body
        self.url = url

    async def aiter_bytes(self):
        yield self._body


class _Stream:
    def __init__(self, resp):
        self._resp = resp

    async def __aenter__(self):
        return self._resp

    async def __aexit__(self, *_a):
        return False


class _Client:
    """httpx.AsyncClient 替身：按脚本逐次返回响应或抛异常，记下每一次请求的地址。"""

    def __init__(self, script):
        self.script = list(script)
        self.requests = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    def stream(self, method, url):
        self.requests.append(url)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return _Stream(item)


def _patch_httpx(monkeypatch, client, *, dns_error=""):
    holder = types.SimpleNamespace(AsyncClient=lambda **_kw: client,
                                   TimeoutException=httpx.TimeoutException,
                                   HTTPError=httpx.HTTPError)
    monkeypatch.setattr(wt, "httpx", holder)

    async def fake_check(_url):
        return dns_error

    monkeypatch.setattr(wt, "_check_host", fake_check)


async def test_run_web_tool_rejects_before_network(monkeypatch):
    called = []

    async def boom(*_a, **_k):
        called.append(1)
        return {}

    monkeypatch.setattr(wt, "_fetch", boom)
    out = await wt.run_web_tool(Ctx(), "web_fetch", {"url": "http://localhost/"})
    assert out["ok"] is False and "内网" in out["error"]
    out = await wt.run_web_tool(Ctx(), "other", {})
    assert out["ok"] is False and "未知" in out["error"]
    ctx = Ctx(cfg={"agent.web_max_fetches_per_run": 5}, run_state={"web_fetches": 5})
    out = await wt.run_web_tool(ctx, "web_fetch", {"url": "https://example.com/"})
    assert out["ok"] is False and out["fetches_left"] == 0 and "上限" in out["error"]
    assert not called


async def test_run_web_tool_returns_dns_gate_reason(monkeypatch):
    client = _Client([])
    _patch_httpx(monkeypatch, client, dns_error="目标站点解析到了非公网地址（10.0.0.5）")
    ctx = Ctx()
    out = await wt.run_web_tool(ctx, "web_fetch", {"url": "https://example.com/"})
    assert out["ok"] is False and "10.0.0.5" in out["error"]
    assert client.requests == [] and "web_fetches" not in ctx.run_state


async def test_fetch_follows_public_redirect_and_charges_once(monkeypatch):
    client = _Client([
        _Resp(302, {"location": "/next"}, url="https://example.com/start"),
        _Resp(200, {"content-type": "text/html; charset=utf-8"},
              body="<html><head><title>完成页</title></head><body>ok</body></html>".encode(),
              url="https://example.com/next"),
    ])
    _patch_httpx(monkeypatch, client)
    ctx = Ctx(cfg={"agent.web_max_fetches_per_run": 10})
    out = await wt.run_web_tool(ctx, "web_fetch", {"url": "https://example.com/start"})
    assert out["ok"] is True and out["title"] == "完成页"
    assert out["status"] == 200 and out["fetches_left"] == 9
    assert out["redirects"] == ["https://example.com/start"]
    assert client.requests == ["https://example.com/start", "https://example.com/next"]
    assert ctx.run_state["web_fetches"] == 1


async def test_fetch_refuses_redirect_into_private_space(monkeypatch):
    client = _Client([
        _Resp(302, {"location": "http://127.0.0.1:8080/admin"},
              url="https://example.com/go"),
    ])
    _patch_httpx(monkeypatch, client)
    ctx = Ctx()
    out = await wt.run_web_tool(ctx, "web_fetch", {"url": "https://example.com/go"})
    assert out["ok"] is False and "非公网" in out["error"]
    assert client.requests == ["https://example.com/go"]
    assert ctx.run_state["web_fetches"] == 1   # 首跳已计费；第二跳在闸上被拒，未再扣


async def test_fetch_reports_binary_without_reading_text(monkeypatch):
    client = _Client([_Resp(200, {"content-type": "application/pdf"},
                            body=b"%PDF-1.7", url="https://example.com/f.pdf")])
    _patch_httpx(monkeypatch, client)
    out = await wt.run_web_tool(Ctx(), "web_fetch", {"url": "https://example.com/f.pdf"})
    assert out["ok"] is True and out["bytes"] == 8 and "未读取正文" in out["note"]


async def test_fetch_timeout_is_a_normal_result(monkeypatch):
    client = _Client([httpx.ReadTimeout("slow")])
    _patch_httpx(monkeypatch, client)
    out = await wt.run_web_tool(Ctx(), "web_fetch", {"url": "https://example.com/"})
    assert out["ok"] is False and "超时" in out["error"]
