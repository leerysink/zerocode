# tests/test_source_guard.py
"""共享出站守卫：SSRF 判据、URL 预检、逐跳重定向预检与流式限量。

判据清单与 app/skills/installer.py 的既有用例同源——Task 1 把判据下沉到这里后，
同一批敌意输入必须在两边得到同一个结论，否则两条通道会各自漂移。

本文件同步与异步用例混排，靠 pytest.ini 的 asyncio_mode=auto 跑协程（不挂模块级
pytestmark，否则纯同步用例每个多刷一条 PytestWarning，同 test_nics.py /
test_skills_generator.py）。
"""
import httpx
import pytest

from app.skills import installer
from app.source import guard

HOSTILE = [
    ("http://localhost/skill", "内网"),
    ("http://a.localhost/skill", "内网"),
    ("http://127.0.1/skill", "混淆"),
    ("http://2130706433/skill", "混淆"),
    ("http://0x7f000001/skill", "混淆"),
    ("http://10.0.0.5/skill", "内网"),
    ("http://169.254.169.254/skill", "内网"),
    ("ftp://example.com/skill", "协议"),
]


@pytest.mark.parametrize("url", [u for u, _ in HOSTILE])
def test_validate_http_url_rejects(url):
    with pytest.raises(guard.GuardError):
        guard.validate_http_url(url)


@pytest.mark.parametrize("url", [u for u, _ in HOSTILE])
def test_installer_and_guard_agree(url):
    """两条通道同一个判据：installer 的包装必须把敌意 URL 同样拦下。"""
    with pytest.raises(installer.SkillError):
        installer._validate_http_url(url)


def test_url_length_cap():
    with pytest.raises(guard.GuardError) as e:
        guard.validate_http_url("https://example.com/" + "a" * guard.MAX_URL_LEN)
    assert "字符上限" in str(e.value)


def test_public_host_passes():
    guard.validate_http_url("https://gitee.com/leerys/zerocode")


async def test_fetch_limited_streams_and_caps_size():
    seen = []

    async def handler(request):
        seen.append(request)
        return httpx.Response(200, content=b"x" * 100)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    assert await guard.fetch_limited(client, "https://example.com/f", "f",
                                     required=True, max_bytes=100) == b"x" * 100
    with pytest.raises(guard.GuardError) as e:
        await guard.fetch_limited(client, "https://example.com/f", "f",
                                  required=True, max_bytes=99)
    assert e.value.oversize is True
    await client.aclose()


async def test_redirect_rechecks_target_before_connecting():
    calls = []

    async def handler(request):
        calls.append(str(request.url))
        if request.url.host == "example.com":
            return httpx.Response(302, headers={"location": "http://127.0.0.1/evil"})
        return httpx.Response(200, content=b"never")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(guard.GuardError):
        await guard.fetch_limited(client, "https://example.com/a", "a",
                                  required=True, max_bytes=10)
    assert calls == ["https://example.com/a"], "内网落点被连接了：预检没跑在请求之前"
    await client.aclose()


async def test_cross_host_redirect_drops_authorization_per_request():
    seen = []

    async def handler(request):
        seen.append((request.url.host, request.headers.get("authorization", "")))
        if request.url.host == "a.example":
            return httpx.Response(302, headers={"location": "https://b.example/f"})
        return httpx.Response(200, content=b"ok")

    # token 挂在共享 client 上（同 installer._gh_client 的真实形态）：守卫只能按请求覆盖，
    # 若它改成突变 client.headers 来摘凭证，下面那条「共享 client 未被突变」的断言就会变红
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler),
                               headers={"authorization": "Bearer tok"})
    await guard.fetch_limited(client, "https://a.example/f", "f", required=True,
                              max_bytes=10, headers={"authorization": "Bearer tok"})
    assert seen == [("a.example", "Bearer tok"), ("b.example", "")]
    # 不永久突变共享 client：同一次任务里后续同源调用仍带 token
    assert client.headers.get("authorization") == "Bearer tok"
    await client.aclose()


async def test_optional_ref_missing_is_separate_exception():
    async def handler(request):
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(guard.RefMissing):
        await guard.fetch_limited(client, "https://example.com/x", "x",
                                  required=False, max_bytes=10)
    await client.aclose()
