"""浏览器 sidecar 纯函数测试：不需要 playwright 或 sidecar 进程。

`app.py` 的监听与采集也在覆盖范围内，但喂的是假 page：那两个函数只读
`route`/`on`/`main_frame` 和 `title`/`evaluate`/`screenshot`/`url`，真浏览器不必出现
（见文件末尾的 `_load_sidecar_app`，它只补一个 playwright 模块桩，不引入依赖）。
"""
import base64
import importlib.util
import socket
import sys
import time
import types
import warnings

from browser.inspect_core import (allow_request, clean_text, is_ip_literal, is_private_host,
                                  parse_allowed_hosts, pick_connect_ip, rewrite_url_host,
                                  shape_result, validate_target)
from browser.session_core import (INTERACTIVE_JS, INTERACTIVE_JS_SCAN_MAX, REF_RE, REF_TOKEN_POOL,
                                  REF_TOKEN_SPACE)


def test_parse_allowed_hosts_defaults_and_trim():
    assert parse_allowed_hosts("") == ("host.docker.internal", "127.0.0.1", "localhost")
    assert parse_allowed_hosts(" Example.COM , 10.0.0.1 ") == ("example.com", "10.0.0.1")


def test_validate_target_accepts_allowlisted_http():
    url, err = validate_target("http://host.docker.internal:8210/orders?x=1")
    assert err == ""
    assert url == "http://host.docker.internal:8210/orders?x=1"


def test_validate_target_rejects_non_http_and_bad_hosts():
    for url in ("file:///etc/passwd", "javascript:alert(1)", "ftp://localhost/a",
                "http://example.com/", "http://user:pass@localhost/",
                "http://localhost:bad/", "", None):
        got, err = validate_target(url)
        assert got is None
        assert err


def test_validate_target_rejects_outside_allowlist_with_custom_hosts():
    got, err = validate_target("http://app.local:3000/", ("localhost",))
    assert got is None
    assert "app.local" in err


def test_allow_request_main_navigation_only_target_origin():
    target = "http://host.docker.internal:8210/orders"
    assert allow_request(target, "http://host.docker.internal:8210/login",
                         is_main_navigation=True)
    assert not allow_request(target, "https://example.com/", is_main_navigation=True)
    assert not allow_request(target, "http://host.docker.internal:8100/api",
                             is_main_navigation=True)


def test_allow_request_subresources_public_allowed_private_blocked():
    target = "http://host.docker.internal:8210/"
    assert allow_request(target, "http://host.docker.internal:8210/assets/a.js",
                         is_main_navigation=False)
    assert allow_request(target, "https://cdn.example.com/lib.js", is_main_navigation=False)
    assert not allow_request(target, "http://127.0.0.1:5433/", is_main_navigation=False)
    assert not allow_request(target, "http://10.1.2.3/internal", is_main_navigation=False)
    assert not allow_request(target, "http://zerocode-backend:8100/api",
                             is_main_navigation=False)
    assert not allow_request(target, "http://host.docker.internal:8100/api",
                             is_main_navigation=False)


def test_is_private_host_matrix():
    for host in ("localhost", "app.internal", "zerocode-pg16", "127.0.0.1", "10.0.0.1",
                 "172.16.0.5", "192.168.1.2", "169.254.1.1", "::1"):
        assert is_private_host(host), host
    for host in ("example.com", "cdn.example.org", "8.8.8.8"):
        assert not is_private_host(host), host


def test_clean_text_strips_ansi_and_control_chars():
    assert clean_text("\x1b[31merror\x1b[0m\r\nline\x00 two") == "error\nline two"


def test_shape_result_caps_and_marks_truncation():
    raw_text = "x" * 9000
    console = [{"level": "error", "text": f"err {i}"} for i in range(60)]
    console.append({"level": "log", "text": "noise"})
    errors = [f"page error {i}" for i in range(15)]
    failed = [{"method": "get", "url": f"http://localhost:8210/api/{i}", "status": 500,
               "type": "fetch"} for i in range(40)]
    failed.append({"method": "GET", "url": "http://localhost:8210/blocked", "status": 0,
                   "type": "script", "blocked": True})
    out = shape_result(title="T", text=raw_text, console=console, page_errors=errors,
                       failed_requests=failed, elapsed_ms=12)
    assert out["ok"] is True
    assert out["text_truncated"] is True
    assert len(out["text"]) < 9000
    assert len(out["console"]) == 40
    assert all(c["level"] in ("error", "warning") for c in out["console"])
    assert len(out["page_errors"]) == 10
    assert len(out["failed_requests"]) == 30
    assert out["truncated"] is True
    assert out["failed_requests"][0]["method"] == "GET"


def test_shape_result_dedupes_failed_requests():
    item = {"method": "GET", "url": "http://localhost:1/a", "status": 404, "type": "fetch"}
    out = shape_result(title="", text="", console=[], page_errors=[],
                       failed_requests=[item, dict(item)], elapsed_ms=1)
    assert len(out["failed_requests"]) == 1
    assert out["truncated"] is False


def test_is_ip_literal_covers_v4_v6_and_names():
    assert is_ip_literal("192.168.65.254") is True
    assert is_ip_literal("[fdc4::254]") is True
    assert is_ip_literal("host.docker.internal") is False
    assert is_ip_literal("") is False


def test_pick_connect_ip_prefers_ipv4_over_return_order():
    info = [
        (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("fdc4:f303:9324::254", 0, 0, 0)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.65.254", 8213)),
    ]
    assert pick_connect_ip(info) == "192.168.65.254"
    assert pick_connect_ip([info[0]]) == "fdc4:f303:9324::254"
    assert pick_connect_ip([]) is None


def test_rewrite_url_host_keeps_port_path_and_query():
    url = "http://host.docker.internal:8213/orders?x=1#frag"
    assert rewrite_url_host(url, "192.168.65.254") == "http://192.168.65.254:8213/orders?x=1#frag"
    assert rewrite_url_host(url, "127.0.0.1") == "http://127.0.0.1:8213/orders?x=1#frag"


def test_rewrite_url_host_wraps_ipv6_in_brackets():
    url = "http://host.docker.internal:8213/"
    assert rewrite_url_host(url, "fdc4::254") == "http://[fdc4::254]:8213/"


def test_allow_request_uses_the_rewritten_origin():
    target = rewrite_url_host("http://host.docker.internal:8213/", "192.168.65.254")
    assert allow_request(target, "http://192.168.65.254:8213/src/main.js",
                         is_main_navigation=False) is True
    assert allow_request(target, "http://host.docker.internal:8213/src/main.js",
                         is_main_navigation=True) is False


def test_shape_result_keeps_preview_page_contract():
    """Task 2 抽共享采集函数时的回归锚：preview_page 依赖这些字段名与上限语义。"""
    out = shape_result(title="订单", text="x" * 9000,
                       console=[{"level": "log", "text": "噪声"}, {"level": "error", "text": "e"}],
                       page_errors=["boom"], failed_requests=[], elapsed_ms=10)
    assert out["ok"] is True
    assert out["text"].endswith("…（已截断）")
    assert out["text_truncated"] is True
    assert out["console"] == [{"level": "error", "text": "e", "truncated": False}]   # log 不收
    assert out["page_errors"] == ["boom"]
    assert out["truncated"] is True


# ---------------------------------------------------------------------------
# app.py：Task 2 把监听与采集从 _inspect_in_context 搬进两个函数，验收口径是「搬完行为不变」。
# 等价证据不能只留在那次一次性对拍脚本里——第二次改动就会把它改歪而没人拦。下面钉的正是
# 脚本当时验过的几条：放行/阻断去重、console 只收 error/warning、非 http 响应被忽略、
# 截图异常落「页面读取失败」、include_screenshot=False 不拍。
# ---------------------------------------------------------------------------

CONNECT_URL = "http://192.168.65.254:8210/orders"      # _connect_url 重写 host 之后的形状
PUBLIC_ASSET = "https://cdn.example.com/lib.js"
PRIVATE_ASSET = "http://127.0.0.1:5433/"               # 侧信道探测容器宿主端口，必须阻断
DATA_ASSET = "data:image/png;base64,AAA"

# shape_result 的 10 个键：preview_page 契约的全部既有字段，一个不许少。
SHAPE_KEYS = {"ok", "title", "text", "text_truncated", "console", "page_errors",
              "failed_requests", "truncated", "timed_out", "elapsed_ms"}
# 本次重构新增的 3 个键：对外可见的响应变宽，钉住它们，免得下次「顺手」再塞第 4 个。
NEW_KEYS = {"interactive", "interactive_truncated", "url"}
SCREENSHOT_KEYS = {"screenshot_b64", "screenshot_width", "screenshot_height"}


def _load_sidecar_app():
    """取到 browser.app 模块；主树 venv 没装 playwright，这里临时补一个最小模块桩。

    playwright 按全局约束只属于 browser/requirements.txt（镜像里装），不该为了跑单测
    进共享 venv。app.py 只在 startup 事件里用 async_playwright，被搬动的两个函数一行
    都不碰它，所以空壳足够；装了真 playwright 的机器走另一分支，不覆盖真模块。
    桩只活在导入那一瞬，导入完立刻还原 sys.modules（审查携带项）：它若常驻，就成了
    跨测试文件的隐式全局状态——同一个 pytest session 里后来真用 playwright 的代码会
    拿到这个假模块。判定用 find_spec 而不是 `import playwright`，是为了只把「没装」当
    补桩的理由；装了但坏掉必须响亮报错，不能被桩悄悄替掉。
    """
    if "playwright" not in sys.modules and importlib.util.find_spec("playwright") is None:
        pkg = types.ModuleType("playwright")
        api = types.ModuleType("playwright.async_api")
        api.async_playwright = lambda: None
        pkg.async_api = api
        stubs = {"playwright": pkg, "playwright.async_api": api}
        sys.modules.update(stubs)
        try:
            return _import_sidecar_app()
        finally:
            for key in stubs:
                sys.modules.pop(key, None)
    return _import_sidecar_app()


def _import_sidecar_app():
    with warnings.catch_warnings():
        # app.py 用 FastAPI 已弃用的 on_event 装饰器（既有选择，本轮不动实现），
        # 不吞掉的话 import 期会冒出一条 DeprecationWarning，读起来像本次改动引入的。
        # 只按消息吞 on_event 那一条（审查携带项）：消息宽的 ignore DeprecationWarning
        # 会连 pydantic 的 PydanticDeprecatedSince* 一起吞掉，那些是真信号。
        # `(?s)` 必需——FastAPI 的文案以换行开头，过滤器用 re.match 锚起点。
        warnings.filterwarnings("ignore", message=r"(?s).*on_event is deprecated",
                                category=DeprecationWarning)
        import browser.app as sidecar_app
    return sidecar_app


_sidecar = _load_sidecar_app()
Collectors = _sidecar.Collectors
attach_listeners = _sidecar.attach_listeners
collect_page_state = _sidecar.collect_page_state
_inspect_in_context = _sidecar._inspect_in_context


class _Req:
    """假 Request：只实现三个 handler 会读到的成员（url/method/resource_type/frame）。"""

    def __init__(self, url, *, method="GET", resource_type="script",
                 navigation=False, frame=None):
        self.url = url
        self.method = method
        self.resource_type = resource_type
        self._navigation = navigation
        self.frame = frame

    def is_navigation_request(self):
        return self._navigation


class _Route:
    def __init__(self, request):
        self.request = request
        self.actions = []

    async def continue_(self):
        self.actions.append("continue")

    async def abort(self):
        self.actions.append("abort")


class _Resp:
    def __init__(self, url, status, *, resource_type="fetch"):
        self.url = url
        self.status = status
        self.request = _Req(url, resource_type=resource_type)


class _Msg:
    """假 console 消息：playwright 给的是 msg.type / msg.text。"""

    def __init__(self, level, text):
        self.type = level
        self.text = text


class _Dialog:
    """假原生弹窗：playwright 给的是 type / message + accept() / dismiss()。"""

    def __init__(self, type_="confirm", message="确认删除？"):
        self.type = type_
        self.message = message
        self.dismissed = False
        self.accepted = False

    async def dismiss(self):
        self.dismissed = True

    async def accept(self):
        self.accepted = True


INTERACTIVE_NODES = [
    {"ref": "rk2m7xt", "role": "button", "name": "提交订单"},
    {"ref": "rwq4jbn", "role": "textbox", "name": ""},          # 无名条目：shape_interactive 丢掉
    {"ref": "rp9zsdw", "role": "textbox", "name": "密码", "type": "password"},
]


class _Page:
    """假 page：够 attach_listeners 注册与回灌事件，也够 collect_page_state 取状态。

    `_screenshot_error` 用来把「不该截图」变成硬失败：include_screenshot=False 那一侧
    只要多调一次 screenshot 就会抛，比数调用次数更难看漏。
    """

    def __init__(self, *, url=CONNECT_URL, text="订单正文", nodes=None,
                 shot=b"png-bytes", screenshot_error=None):
        self.url = url
        self._text = text
        self._nodes = INTERACTIVE_NODES if nodes is None else nodes
        self._shot = shot
        self._screenshot_error = screenshot_error
        self.main_frame = object()
        self.route_patterns = []
        self.handlers = {}
        self.screenshot_calls = []
        self.evaluated = []
        self.evaluate_args = []
        self.goto_calls = []
        self.waited = []

    async def route(self, pattern, handler):
        self.route_patterns.append(pattern)
        self.handlers["route"] = handler

    def on(self, event, handler):
        self.handlers[event] = handler

    async def goto(self, url, **kwargs):
        self.goto_calls.append((url, kwargs))

    async def wait_for_timeout(self, ms):
        self.waited.append(("timeout", ms))

    async def wait_for_load_state(self, state, **kwargs):
        self.waited.append((state, kwargs))

    async def title(self):
        return "订单"

    async def evaluate(self, script, arg=None):
        self.evaluated.append(script)
        self.evaluate_args.append(arg)
        return self._nodes if script == INTERACTIVE_JS else self._text

    async def screenshot(self, **kwargs):
        self.screenshot_calls.append(kwargs)
        if self._screenshot_error is not None:
            raise self._screenshot_error
        return self._shot


class _Context:
    def __init__(self, page):
        self._page = page

    async def new_page(self):
        return self._page


def _collectors_with_events():
    """一份手工填好的收集器：省掉键集合用例里重复的回灌动作。"""
    c = Collectors()
    c.console.append({"level": "error", "text": "炸了"})
    c.page_errors.append("boom")
    c.failed.append({"method": "GET", "url": PRIVATE_ASSET, "status": 0,
                     "type": "xhr", "blocked": True})
    return c


async def test_attach_listeners_registers_route_and_honours_allowlist():
    page = _Page()
    collectors = await attach_listeners(page, CONNECT_URL)

    assert page.route_patterns == ["**/*"]
    # dialog 也在册：原生弹窗必须有人接，否则 confirm 会把整次 goto 卡到超时
    assert set(page.handlers) == {"route", "console", "pageerror", "response", "requestfailed",
                                  "dialog"}
    assert collectors.snapshot() == {"console": [], "page_errors": [], "failed": [],
                                     "dialog": None}
    on_route = page.handlers["route"]

    main_doc = _Route(_Req(CONNECT_URL, navigation=True, frame=page.main_frame))
    await on_route(main_doc)
    inline = _Route(_Req(DATA_ASSET, resource_type="image"))
    await on_route(inline)
    public = _Route(_Req(PUBLIC_ASSET, resource_type="script"))
    await on_route(public)
    assert (main_doc.actions, inline.actions, public.actions) == (["continue"], ["continue"],
                                                                 ["continue"])

    blocked = _Route(_Req(PRIVATE_ASSET, method="GET", resource_type="xhr"))
    await on_route(blocked)
    assert blocked.actions == ["abort"]
    assert collectors.blocked_urls == {PRIVATE_ASSET}
    assert collectors.failed == [{"method": "GET", "url": PRIVATE_ASSET, "status": 0,
                                 "type": "xhr", "blocked": True}]


async def test_attach_listeners_does_not_double_count_a_blocked_request_failure():
    """阻断已经在 on_route 记过一次账；浏览器随后为同一 URL 发 requestfailed，
    再记一次就是同一面墙报两个错——旧实现靠 blocked_urls 去重，搬完必须还在。"""
    page = _Page()
    collectors = await attach_listeners(page, CONNECT_URL)
    await page.handlers["route"](_Route(_Req(PRIVATE_ASSET, resource_type="xhr")))
    page.handlers["requestfailed"](_Req(PRIVATE_ASSET, resource_type="xhr"))
    page.handlers["requestfailed"](_Req(DATA_ASSET))

    assert len(collectors.failed) == 1
    assert collectors.failed[0]["blocked"] is True


async def test_attach_listeners_keeps_console_items_url_free():
    """console 条目形状是 session 路径的判据：多一个 url 字段，new_only 就会把
    旧错误按请求指纹重新算成新账（详见 attach_listeners 的 docstring）。"""
    page = _Page()
    collectors = await attach_listeners(page, CONNECT_URL)
    on_console = page.handlers["console"]
    on_console(_Msg("log", "噪声"))
    on_console(_Msg("error", "炸了"))
    on_console(_Msg("warning", "小心"))
    page.handlers["pageerror"](RuntimeError("未捕获异常"))

    assert [i["level"] for i in collectors.console] == ["error", "warning"]
    assert all(set(i) == {"level", "text"} for i in collectors.console)
    assert collectors.page_errors == ["未捕获异常"]


async def test_attach_listeners_ignores_non_http_responses():
    """dev 页面的 ws://（Vite HMR）状态码不总是 2xx；把它记成失败请求会让每次
    preview_page 都固定带回一条假故障。"""
    page = _Page()
    collectors = await attach_listeners(page, CONNECT_URL)
    page.handlers["response"](_Resp("ws://192.168.65.254:8210/hmr", 500))
    assert collectors.failed == []

    page.handlers["response"](_Resp("http://192.168.65.254:8210/api/orders", 500,
                                    resource_type="fetch"))
    assert collectors.failed == [{"method": "GET", "url": "http://192.168.65.254:8210/api/orders",
                                  "status": 500, "type": "fetch"}]


async def test_attach_listeners_dismisses_dialogs_and_reports_them():
    """原生弹窗一律 dismiss 且把现场记进 `collectors.dialog`（规格 US-11）。

    两头都少不得：不 dismiss 的话 confirm 会把页面挂在那里直到动作超时；dismiss 了不回报
    的话，Agent 点了「删除」、弹窗被取消、它以为删除成功了。这里也钉住 `/inspect` 同样受益
    ——共用一个 attach_listeners 就是这条覆盖能落在两个调用面上的原因。
    """
    page = _Page()
    collectors = await attach_listeners(page, CONNECT_URL)
    dialog = _Dialog("confirm", "确认删除全部订单？")
    await page.handlers["dialog"](dialog)

    assert dialog.dismissed is True
    assert dialog.accepted is False          # accept 等于替 Agent 按下不可撤销的确认
    assert collectors.dialog == {"type": "confirm", "message": "确认删除全部订单？",
                                 "handled": "dismissed"}

    long = _Dialog("alert", "x" * 400)
    await page.handlers["dialog"](long)
    assert len(collectors.dialog["message"]) == 200   # 弹窗文案是页面给的，不能整段进上下文

    state = await collect_page_state(page, collectors, started=time.monotonic(),
                                     timed_out=False)
    assert "dialog" not in state             # 弹窗由 step 端点单独回报，采集器不替它做归属


async def test_collect_page_state_hands_the_labeling_script_a_fresh_token_pool():
    """打标脚本不再自己编号：令牌由采集器生成、整份数组当参数传进去（规格 §6.2）。

    三条各钉一件事，缺一条就是这次改动要修的那个静默错点的另一种形态：
    - 条数 = 遍历上限（池小于 SCAN_MAX 的话脚本会越界拿到 undefined）；
    - 彼此互不相同（同一次扫描里两个元素撞同一个句柄，点哪一个纯看运气）；
    - 两次采集发出去的不是同一批（旧句柄因此在新一页的清单里必然查不到）。
    """
    page = _Page()
    other = _Page()
    await collect_page_state(page, _collectors_with_events(),
                             started=time.monotonic(), timed_out=False)
    await collect_page_state(other, _collectors_with_events(),
                             started=time.monotonic(), timed_out=False)

    def tokens_of(fake):
        index = fake.evaluated.index(INTERACTIVE_JS)
        return fake.evaluate_args[index]

    first, second = tokens_of(page), tokens_of(other)
    assert len(first) == REF_TOKEN_POOL == INTERACTIVE_JS_SCAN_MAX
    assert len(set(first)) == len(first)
    assert all(REF_RE.fullmatch(token) for token in first)
    assert set(first) != set(second)
    assert len(set(first) | set(second)) == 2 * REF_TOKEN_POOL   # 两次整批采集 400 个令牌，零重复
    assert REF_TOKEN_SPACE > 400 * 400        # 空间远大于「一次走查的总令牌数」量级，见规格 §8


async def test_collect_page_state_keeps_the_key_contract():
    """键集合契约：shape_result 的 10 个旧键一个不许少，新增的就是这三个（I-2a）。

    消费侧 app/agent/browser_tools.py 按白名单取字段、不遍历键，所以多一个键就是
    sidecar→backend 这一跳的 JSON 变宽；把它固定在测试里，变宽这件事必须显式做过。
    """
    page = _Page()
    out = await collect_page_state(page, _collectors_with_events(),
                                   started=time.monotonic(), timed_out=False)

    assert set(out) == SHAPE_KEYS | NEW_KEYS
    assert out["ok"] is True
    assert out["title"] == "订单"
    assert out["text"] == "订单正文"
    assert out["console"] == [{"level": "error", "text": "炸了", "truncated": False}]
    assert out["page_errors"] == ["boom"]
    assert out["failed_requests"] == [{"method": "GET", "url": PRIVATE_ASSET, "status": 0,
                                       "type": "xhr", "blocked": True}]
    assert out["timed_out"] is False
    # 无名元素被丢掉，password 输入框标成 sensitive
    assert [i["ref"] for i in out["interactive"]] == ["rk2m7xt", "rp9zsdw"]
    assert out["interactive"][1]["sensitive"] is True
    assert out["interactive_truncated"] is False
    assert out["truncated"] is False
    # url 是重写后的 connect_url（容器可达 IP），展示给用户前还要转回 display_url
    assert out["url"] == CONNECT_URL


async def test_collect_page_state_screenshot_is_opt_in_and_adds_three_keys():
    """include_screenshot=False 时截图一次都不该发生（step 路径的预算）；True 时
    /inspect 的三键与 base64 形状保持重构前一致。"""
    never = _Page(screenshot_error=AssertionError("include_screenshot=False 不该截图"))
    out = await collect_page_state(never, _collectors_with_events(),
                                   started=time.monotonic(), timed_out=False)
    assert set(out) == SHAPE_KEYS | NEW_KEYS
    assert never.screenshot_calls == []

    page = _Page(shot=b"png-bytes")
    included = await collect_page_state(page, _collectors_with_events(),
                                        started=time.monotonic(), timed_out=True,
                                        include_screenshot=True)
    assert set(included) == SHAPE_KEYS | NEW_KEYS | SCREENSHOT_KEYS
    assert page.screenshot_calls == [{"type": "png", "full_page": False}]
    assert included["screenshot_b64"] == base64.b64encode(b"png-bytes").decode("ascii")
    assert (included["screenshot_width"], included["screenshot_height"]) == (1280, 800)
    assert included["timed_out"] is True


async def test_inspect_in_context_returns_the_collected_state_with_screenshot():
    page = _Page()
    started = time.monotonic()
    out = await _inspect_in_context(_Context(page), CONNECT_URL, 0, 10_000, started)

    assert set(out) == SHAPE_KEYS | NEW_KEYS | SCREENSHOT_KEYS
    assert page.goto_calls == [(CONNECT_URL, {"wait_until": "domcontentloaded",
                                              "timeout": 10_000})]
    assert INTERACTIVE_JS in page.evaluated          # 交互打标脚本确实被送进浏览器
    assert ("networkidle", {"timeout": 3000}) in page.waited


async def test_inspect_in_context_screenshot_failure_is_a_readable_error():
    """截图读不出来 = 整次读取失败，落「页面读取失败」而不是抛给 FastAPI 变 500：
    重构前就是这个分支，搬进 collect_page_state 后仍然如此。"""
    page = _Page(screenshot_error=RuntimeError("shot failed"))
    out = await _inspect_in_context(_Context(page), CONNECT_URL, 0, 10_000, time.monotonic())

    assert out["ok"] is False
    assert out["error"] == "页面读取失败: shot failed"
    assert out["timed_out"] is False
    assert set(out) == {"ok", "error", "timed_out", "elapsed_ms"}
    assert page.screenshot_calls == [{"type": "png", "full_page": False}]
