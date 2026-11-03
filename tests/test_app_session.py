"""验证会话的登录客户端（ADR-0038）：只验信封解析与失败语义，不连真应用。"""
import httpx
import pytest

from app.agent import app_session as s


def test_parse_login_token_envelope_matrix():
    assert s.parse_login_token({"code": 200, "data": {"token": " abc "}}) == "abc"
    for bad in (None, [], {"data": None}, {"data": {"token": ""}},
                {"data": {"token": 123}}, {"token": "top-level"}):
        assert s.parse_login_token(bad) == ""


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_login_success_returns_token():
    def handler(request):
        assert request.url.path == "/api/auth/login"
        assert request.url.port == 8241
        return httpx.Response(200, json={"code": 200, "data": {"token": "jwt-1"}})

    async with _client(handler) as client:
        token, note = await s.login_app_session(8241, client=client)
    assert (token, note) == ("jwt-1", "")


@pytest.mark.parametrize("status,expected", [
    (404, s.NO_LOGIN_NOTE),
    (405, s.NO_LOGIN_NOTE),
])
async def test_missing_or_disallowed_login_endpoint_is_normal(status, expected):
    async with _client(lambda _r: httpx.Response(status)) as client:
        token, note = await s.login_app_session(8241, client=client)
    assert token == "" and note == expected


async def test_login_http_error_and_bad_envelope_are_reported():
    async with _client(lambda _r: httpx.Response(500)) as client:
        assert await s.login_app_session(8241, client=client) == ("", "登录接口返回 HTTP 500")
    async with _client(lambda _r: httpx.Response(200, text="not json")) as client:
        assert await s.login_app_session(8241, client=client) == ("", "登录接口返回的不是 JSON")
    async with _client(lambda _r: httpx.Response(200, json={"code": 200, "data": {}})) as client:
        assert await s.login_app_session(8241, client=client) == ("", "登录接口没有返回 token")


async def test_login_timeout_and_connection_error_are_reported():
    def timeout(_r):
        raise httpx.ConnectTimeout("boom")

    def refused(_r):
        raise httpx.ConnectError("refused")

    async with _client(timeout) as client:
        assert await s.login_app_session(8241, client=client) == ("", "登录请求超时")
    async with _client(refused) as client:
        assert await s.login_app_session(8241, client=client) == ("", "无法连接应用的登录接口")
