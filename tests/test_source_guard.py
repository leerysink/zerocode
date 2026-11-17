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


# 终审 F1：`urlparse().hostname` 按定义不含 userinfo，所以 SSRF 判据对「URL 里内嵌的凭证」天生盲。
# 这条链路的后果不是「拿到了私有内容」（端点全部按硬编码 host 重拼、从不带鉴权头，token 一个字节
# 都不生效），而是凭证被原样冻进 manifest.sourceUrl 与 README 出处行 —— manifest 正是发布后
# **公开站点**上的一个文件。所以拒在共用的守卫层，两条通道一起受益。
CREDENTIAL_URLS = [
    "https://oauth2:GLtok-SECRET123@gitee.com/leerys/zerocode",
    "https://user@gitee.com/leerys/zerocode",
    "https://user:pw@gitee.com/leerys/zerocode",
    # 空用户名也算 userinfo：`@` 一出现就改写了权威段，hostname 已经不是看起来那个主机
    "https://@gitee.com/leerys/zerocode",
    "https://gitee.com@198.51.100.7/leerys/zerocode",
]


@pytest.mark.parametrize("url", CREDENTIAL_URLS)
def test_validate_http_url_rejects_credentials_in_url(url):
    with pytest.raises(guard.GuardError) as e:
        guard.validate_http_url(url)
    assert "令牌" in str(e.value)
    # 文案不许回显 URL：400 detail 会进响应体与访问日志，把凭证从快照搬到那里等于没修
    assert "SECRET123" not in str(e.value)
    assert "@" not in str(e.value)


@pytest.mark.parametrize("url", CREDENTIAL_URLS)
def test_installer_translates_credential_rejection(url):
    """通道一致性：技能侧的薄包装必须把同一条拒绝翻成 SkillError（→400），而不是漏成 500。"""
    with pytest.raises(installer.SkillError) as e:
        installer._validate_http_url(url)
    assert "令牌" in str(e.value)
    assert "SECRET123" not in str(e.value)


# 终审 F2：未闭合的 IPv6 字面量让 urlparse 抛**裸 ValueError**。它在调用方自己的 try 之前触发，
# 于是 api 层那句 `except SourceError` 根本看不见 —— 一次 500 + traceback，破掉「失败只以
# 400 类领域错误出去」这条分支级不变量（规格 US-10 / §5.4 承诺的是 400）。
UNPARSABLE_URLS = ["http://[::1/x", "https://[::1", "http://[bad/x/path"]


@pytest.mark.parametrize("url", UNPARSABLE_URLS)
def test_validate_http_url_turns_parse_error_into_guard_error(url):
    with pytest.raises(guard.GuardError) as e:
        guard.validate_http_url(url)
    assert "URL 形态非法" in str(e.value)


@pytest.mark.parametrize("url", UNPARSABLE_URLS)
def test_installer_translates_parse_error(url):
    with pytest.raises(installer.SkillError):
        installer._validate_http_url(url)


def test_public_host_passes():
    guard.validate_http_url("https://gitee.com/leerys/zerocode")


async def test_unparsable_redirect_target_never_escapes_as_value_error():
    """重定向落点由远端 Response 决定，比用户粘的串更不可信，所以它也在 F2 的口径之内。

    实测这一形态今天由 httpx 自己先判掉（`URL(location)` 解析失败 → `RemoteProtocolError`，
    它是 `httpx.HTTPError` 的子类，两条通道的包装层都翻译它）。守卫内部那道
    `urljoin`/`urlparse` 的 ValueError 收口是第二层：将来 httpx 放宽校验时它接管，
    两层加起来的铁律是「裸 ValueError 不许递给调用方」—— 这条用例钉的就是这一句。
    """
    async def handler(request):
        return httpx.Response(302, headers={"location": "//[::1/x"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(httpx.HTTPError):        # 换成 ValueError 逃逸，这里直接红
        await guard.fetch_limited(client, "https://example.com/a", "a",
                                  required=True, max_bytes=10)
    await client.aclose()


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
