# tests/test_mpruntime.py
"""小程序前置条件（规格 2026-11-09 US-22/US-25）：域名投影、出网 IP 探测与缓存、
TLS 可达性与 readiness 汇总。网络全部用 httpx.MockTransport 注入，不碰真外网。"""
import httpx
import pytest

from app.deploy import mpruntime, nics

pytestmark = pytest.mark.asyncio

APP = {"slug": "zcmini"}
BOUND = {"appid": "wx1234567890abcdef"}


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("ZEROCODE_EGRESS_IP", raising=False)
    mpruntime._cache.update(ip="", at=None)
    yield
    mpruntime._cache.update(ip="", at=None)


def _mock(handler):
    return httpx.MockTransport(handler)


async def test_request_domain_requires_ssl_and_slug(monkeypatch):
    monkeypatch.delenv("ZEROCODE_SSL_ENABLE", raising=False)
    assert mpruntime.request_domain(APP) == f"https://zcmini.{nics.GATEWAY_DOMAIN}"
    assert mpruntime.api_base(APP) == f"https://zcmini.{nics.GATEWAY_DOMAIN}/api"

    assert mpruntime.request_domain({"slug": ""}) == ""
    assert mpruntime.api_base({"slug": ""}) == ""

    monkeypatch.setenv("ZEROCODE_SSL_ENABLE", "false")
    assert mpruntime.request_domain(APP) == ""
    assert mpruntime.api_base(APP) == ""


async def test_probe_tls_counts_any_http_status_as_reachable(monkeypatch):
    """401/403 也证明 TLS 与应用在；只有连不上/证书错才算不可达。"""
    monkeypatch.setattr(mpruntime, "transport", _mock(lambda req: httpx.Response(401)))
    assert await mpruntime.probe_tls("https://zcmini.zerocode.com") is True

    def boom(req):
        raise httpx.ConnectError("certificate verify failed", request=req)

    monkeypatch.setattr(mpruntime, "transport", _mock(boom))
    assert await mpruntime.probe_tls("https://zcmini.zerocode.com") is False


async def test_egress_ip_env_wins_without_probing(monkeypatch):
    def must_not_call(req):  # 走 env 时不应发起任何请求
        raise AssertionError("不应探测")

    monkeypatch.setattr(mpruntime, "transport", _mock(must_not_call))
    monkeypatch.setenv("ZEROCODE_EGRESS_IP", "203.0.113.9")
    assert await mpruntime.egress_ip() == "203.0.113.9"


async def test_egress_ip_probe_is_cached(monkeypatch):
    calls = []

    def handler(req):
        calls.append(req.url)
        return httpx.Response(200, text="198.51.100.7\n")

    monkeypatch.setattr(mpruntime, "transport", _mock(handler))
    assert await mpruntime.egress_ip() == "198.51.100.7"
    assert await mpruntime.egress_ip() == "198.51.100.7"
    assert len(calls) == 1                    # 10 分钟缓存内不再打网络

    assert await mpruntime.egress_ip(force=True) == "198.51.100.7"
    assert len(calls) == 2


async def test_egress_ip_failure_returns_empty_and_caches(monkeypatch):
    calls = []

    def boom(req):
        calls.append(req.url)
        raise httpx.ConnectError("network is unreachable", request=req)

    monkeypatch.setattr(mpruntime, "transport", _mock(boom))
    assert await mpruntime.egress_ip() == ""   # 不抛：探测失败不阻断任何操作
    assert await mpruntime.egress_ip() == ""
    assert len(calls) == 1                     # 失败同样进缓存，避免每拍都卡超时


async def test_readiness_collects_reasons(monkeypatch):
    monkeypatch.setenv("ZEROCODE_EGRESS_IP", "203.0.113.9")
    monkeypatch.delenv("ZEROCODE_SSL_ENABLE", raising=False)

    ok = await mpruntime.readiness(APP, BOUND)
    assert ok["ok"] is True and ok["reasons"] == []
    assert ok["request_domain"] == f"https://zcmini.{nics.GATEWAY_DOMAIN}"
    assert ok["egress_ip"] == "203.0.113.9"

    monkeypatch.setenv("ZEROCODE_SSL_ENABLE", "false")
    off = await mpruntime.readiness(APP, BOUND)
    assert off["ok"] is False and off["reasons"] == ["ssl_off"]
    assert off["request_domain"] == ""

    monkeypatch.delenv("ZEROCODE_SSL_ENABLE", raising=False)
    unbound = await mpruntime.readiness(APP, {})
    assert unbound["reasons"] == ["no_credentials"]

    async def bad(url, timeout=3.0):
        return False

    monkeypatch.setattr(mpruntime, "probe_tls", bad)
    tls = await mpruntime.readiness(APP, BOUND, probe=True)
    assert tls["reasons"] == ["tls_unreachable"]
    assert "域名或证书" in mpruntime.reason_text(tls["reasons"])


async def test_reason_text_covers_every_reason():
    for reason in ("ssl_off", "no_domain", "no_credentials", "tls_unreachable"):
        text = mpruntime.reason_text([reason])
        assert text and text != reason
    assert "HTTPS" in mpruntime.reason_text(["ssl_off"])
    assert mpruntime.reason_text([]) == ""


async def test_guidance_shape():
    guide = mpruntime.guidance(APP, egress_ip="203.0.113.9")
    assert guide["request_domain"] == f"https://zcmini.{nics.GATEWAY_DOMAIN}"
    assert guide["egress_ip"] == "203.0.113.9"
    assert guide["console_url"] == "https://mp.weixin.qq.com/"
    assert guide["steps"] == ["dev_version", "trial_version", "submit_review"]
