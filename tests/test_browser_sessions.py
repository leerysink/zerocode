"""sidecar session 注册表与端点（规格 §6.1/§6.2）。

浏览器对象用桩：注册表那一段验语义（上限、TTL、幂等 close、未知 id），端点那一段验
Task 4/5 要对接的 HTTP 契约——字段名、状态码、扣不扣步数、附不附最新清单。
Playwright 真能不能点中由 Step 16 的真机走查负责（本次按 R-8 交回主会话）。
"""
import base64
import importlib.util
import re
import sys
import time
import types
import warnings

import pytest

# TestClient 在 **模块导入期** 就会发出这条弃用告警：starlette/testclient.py:53 拿
# `anyio.abc.BlockingPortal` 这个已弃用别名做类型注解（真凶在 anyio 的 _lazyimport 里）。
# 只能在 import 那一刻用 `catch_warnings` 局部压掉，两个原因：
#   1. 消除不了——那是第三方的 import 语句，我们既不 pin starlette 也不新增依赖（全局约束），
#      自己这一侧没有任何代码产生它；
#   2. 配置级过滤压不住——命令行 `-W error` 的过滤器比 pytest.ini 的 `filterwarnings` 后应用、
#      位置更靠前（`warnings.filterwarnings` 是插到队首的），所以 CI 上 `-W error` 会把它变成
#      collection error。放在 import 现场就与命令行为无关了。
# 口径是 message 精确到这一条别名，不是 `ignore::DeprecationWarning`：将来 anyio 或 starlette
# 修掉之后这条匹配不到任何东西，会静静留在原地，而别的弃用告警照常出声。
with warnings.catch_warnings():
    warnings.filterwarnings("ignore",
                            message=r"The anyio\.abc\.BlockingPortal alias is deprecated",
                            category=DeprecationWarning)
    from fastapi.testclient import TestClient

from browser import sessions
from browser.session_core import (INTERACTIVE_JS, INTERACTIVE_JS_SCAN_MAX, REF_RE,
                                  new_ref_tokens)

# app.collect_page_state 采正文用的脚本字面量（R-1：它是唯一采集器）。假 page 靠它区分
# 「取交互节点」和「取可见正文」两种调用。
TEXT_JS = "document.body ? document.body.innerText : ''"


def _load_sidecar_app():
    """取到 browser.app；没装 playwright 的机器临时补一个模块桩（裁定 R-18）。

    装了真 playwright 的机器完全不走桩；没装的机器测试照跑，而 `import browser.app` 不包
    try——import 链一坏就是 collection error 而不是静默跳过，比 importorskip 强。
    桩只活在导入那一瞬，导入完立刻还原 sys.modules：留在里面就成了跨测试文件的隐式全局
    状态，同一个 pytest session 里后来真用 playwright 的代码会拿到假模块。
    """
    if "playwright" not in sys.modules and importlib.util.find_spec("playwright") is None:
        pkg = types.ModuleType("playwright")
        api = types.ModuleType("playwright.async_api")
        api.async_playwright = lambda: None
        pkg.async_api = api
        stubs = {"playwright": pkg, "playwright.async_api": api}
        sys.modules.update(stubs)
        try:
            return _import_app()
        finally:
            for key in stubs:
                sys.modules.pop(key, None)
    return _import_app()


def _import_app():
    with warnings.catch_warnings():
        # 只吞 on_event 那一条：消息宽的 ignore DeprecationWarning 会连 pydantic 的
        # PydanticDeprecatedSince* 一起吞掉，那些是会在升级时变成硬错误的真信号。
        # `(?s)` 是必需的：FastAPI 的这条文案以换行开头，而过滤器用 re.match 锚起点，
        # 少了它这条正则一个字都匹配不上——过滤条件就会退化成「什么都没吞」。
        warnings.filterwarnings("ignore", message=r"(?s).*on_event is deprecated",
                                category=DeprecationWarning)
        return __import__("browser.app", fromlist=["app"])


_sidecar = _load_sidecar_app()
app = _sidecar.app

# validate_target 的默认允许清单里有 127.0.0.1；测试里 `_connect_url` 被打成恒等，
# 所以会话的 origin 就是这条地址。
OPEN_URL = "http://127.0.0.1:8210/"


class FakeMsg:
    """playwright 给的 console 消息形状：msg.type / msg.text。"""

    def __init__(self, level, text):
        self.type = level
        self.text = text


class FakeDialog:
    def __init__(self, type_, message):
        self.type = type_
        self.message = message
        self.dismissed = False
        self.accepted = False

    async def dismiss(self):
        self.dismissed = True

    async def accept(self):
        self.accepted = True


class FakeLocator:
    """Playwright 的 `page.locator(sel)`：这里只用到 `count()`——「这个属性在不在页面上」，不等待。

    替身按「上一次采集打完的句柄集合」回答，等价于真浏览器打完标之后的那份 DOM：`ref_is_present`
    判在场、`click` 才点得动，两处读同一个集合，就不会出现「count 放行而 click 撞墙」这种
    替身自己漂开的形状。
    """

    def __init__(self, page, selector):
        self._page = page
        self._selector = selector

    async def count(self):
        return 1 if self._page.holds_the_ref(self._selector) else 0


class FakePage:
    def __init__(self, url="http://h:8210/", text="首页", nodes=None):
        self.url = url
        self.text = text
        self.nodes = [] if nodes is None else nodes
        # 这一轮真正打在页面上的句柄集合：每次采集整批换发（`evaluate` 里更新），
        # 上一轮那一批随这次重打一起消失——就是旧令牌查不到的物理原因。
        self.stamped: set[str] = {str(n.get("ref")) for n in self.nodes}
        self.clicked: list[str] = []
        self.filled: list[tuple[str, str]] = []
        self.handlers: dict = {}
        self.goto_calls: list[tuple] = []
        self.goto_error: Exception | None = None
        self.click_error: Exception | None = None
        self.evaluate_error: Exception | None = None
        self.screenshot_error: Exception | None = None
        self.screenshot_calls: list[dict] = []
        self.waited: list = []
        # 页面自己「制造」外部事件的几个钩子：真浏览器里它们发生在 goto / click 期间，
        # 替身只能在同一时刻把事件灌回 attach_listeners 注册的处理器。
        self.console_on_goto: list[tuple[str, str]] = []
        # 与 console 同形：真浏览器里 pageerror 给的是一个 Error 对象，收集器只做 str(err)，
        # 替身因此也得传一个异常而不是裸字符串。
        self.pageerror_on_goto: list[str] = []
        self.dialog_on_click: str | None = None
        self.redirect_to: str | None = None
        # 打标脚本每次采集收到的令牌数组（采集器应当在 Python 侧生成、整份传进来）
        self.tokens_seen: list[list[str]] = []

    REF_SELECTOR = re.compile(r'^\[data-zc-ref="([a-z0-9]+)"\]$')

    def holds_the_ref(self, selector: str) -> bool:
        """这个属性选择器指的元素在不在「页面上」（替身用 `stamped` 建模 DOM 的打标状态）。"""
        match = self.REF_SELECTOR.fullmatch(selector or "")
        return bool(match) and match.group(1) in self.stamped

    def locator(self, selector):
        return FakeLocator(self, selector)

    async def evaluate(self, script, *a):
        if self.evaluate_error is not None:
            raise self.evaluate_error
        if script == INTERACTIVE_JS:
            self.tokens_seen.append(list(a[0]) if a else [])
            self.stamped = {str(n.get("ref")) for n in self.nodes}
            return self.nodes
        # 采集器只该问这两件事（R-1 的另一面：`snapshot_state` 是薄封装，不会自己另发明
        # 第三种取法）。真出现第三种就是采集口径漂了，宁可在这里响亮失败。
        assert script == TEXT_JS, f"page.evaluate 收到了预期之外的脚本: {script[:60]}"
        return self.text

    async def click(self, sel, timeout=None):
        self._must_hold(sel)
        self.clicked.append(sel)
        if self.dialog_on_click is not None:
            await self.handlers["dialog"](FakeDialog("confirm", self.dialog_on_click))
        if self.click_error is not None:
            raise self.click_error

    async def fill(self, sel, value, timeout=None):
        self._must_hold(sel)
        self.filled.append((sel, value))

    async def press(self, sel, key, timeout=None):
        self._must_hold(sel)
        self.clicked.append(f"{sel}+{key}")

    async def select_option(self, sel, value):
        self._must_hold(sel)
        self.filled.append((sel, value))

    async def scroll_into_view_if_needed(self, sel):
        self._must_hold(sel)
        self.clicked.append(f"scroll:{sel}")

    def _must_hold(self, sel):
        """页面上没有这个句柄就是「匹配不到元素」，替身硬失败而不是让它点成功。

        放行判据（`ref_is_present`）与动作走的是同一份 DOM 状态：两边各判一次的话，
        「count 说在、click 撞墙」这种替身自己漂开的形状会一路绿到生产。
        """
        if not self.holds_the_ref(sel):
            raise AssertionError(f"替身页面上没有这个句柄：{sel}")

    async def goto(self, url, **kw):
        if self.goto_error is not None:
            raise self.goto_error
        self.goto_calls.append((url, kw))
        self.url = self.redirect_to or url
        for level, text in self.console_on_goto:
            self.handlers["console"](FakeMsg(level, text))
        for message in self.pageerror_on_goto:
            self.handlers["pageerror"](RuntimeError(message))

    async def route(self, pattern, handler):
        self.handlers["route"] = handler

    def on(self, event, handler):
        self.handlers[event] = handler

    async def title(self):
        return "t"

    async def wait_for_timeout(self, ms):
        self.waited.append(("timeout", ms))

    async def wait_for_load_state(self, state, timeout=None):
        self.waited.append((state, timeout))

    async def screenshot(self, **kw):
        self.screenshot_calls.append(kw)
        if self.screenshot_error is not None:
            raise self.screenshot_error
        return b"\x89PNG\r\n\x1a\n"


class FakeContext:
    def __init__(self, page=None):
        self.closed = False
        self.page = FakePage() if page is None else page

    async def new_page(self):
        return self.page

    async def close(self):
        self.closed = True


class FakeBrowser:
    """`_startup` 造出来的那个浏览器的替身：每次 new_context 给一个新 page。

    `page_factory` 让单个测试决定页面行为（节点、正文、goto 失败），而不必替整个文件
    换掉夹具。默认那份页面带着 `NODES` 打好的标——不建模「页面上一个可点元素都没有」，
    否则端点测试里绝大多数「点得动」的期望会凭空落进句柄查不到那一支。
    """

    def __init__(self, page_factory=None):
        self.page_factory = page_factory or (lambda: FakePage(url=OPEN_URL, nodes=NODES))
        self.contexts: list[FakeContext] = []
        self.closed = False

    async def new_context(self, **kw):
        context = FakeContext(self.page_factory())
        self.contexts.append(context)
        return context

    async def close(self):
        self.closed = True


class FakeChromium:
    def __init__(self, browser):
        self._browser = browser

    async def launch(self, **kw):
        return self._browser


class FakePlaywright:
    """app 的 startup 只要 `async_playwright().start() → .chromium.launch()` 这条链。

    打桩点在 `async_playwright` 而不是 `_browser`：startup 会给模块全局 `_browser` 赋值，
    进 TestClient 之前塞进去的假浏览器会被它覆盖掉。
    """

    def __init__(self, browser):
        self.chromium = FakeChromium(browser)

    async def start(self):
        return self

    async def stop(self):
        return None


class FakeCollectors:
    """app.Collectors 的测试替身：`collect_page_state` 只读这三个列表和 dialog。

    console 条目刻意保持 `{level, text}` 两键——`session_core.new_only` 靠「有没有 url」
    区分 console 与失败请求，多一个 url 字段会让旧错误每步都变成新账。
    """

    def __init__(self):
        self.console: list[dict] = []
        self.page_errors: list[str] = []
        self.failed: list[dict] = []
        self.blocked_urls: set[str] = set()
        self.dialog: dict | None = None


@pytest.fixture(autouse=True)
def _clear():
    sessions.SESSIONS.clear()
    yield
    sessions.SESSIONS.clear()


def _put(sid="s1", **kw):
    s = sessions.Session(session_id=sid, context=FakeContext(), page=FakePage(),
                         origin="http://h:8210", collectors=FakeCollectors(), **kw)
    sessions.SESSIONS[sid] = s
    return s


async def test_sweep_closes_idle_but_keeps_active():
    _put("old")
    _put("new")
    sessions.SESSIONS["new"].last_used = 1000.0
    sessions.SESSIONS["old"].last_used = 1000.0 - 300
    closed = await sessions.sweep_expired(now=1000.0, ttl_seconds=120)
    assert closed == ["old"]
    assert "old" not in sessions.SESSIONS
    # brief 原文用 `SESSIONS["old" if False else "new"]` 表达「只剩 new」，那是绕圈写法；
    # 断言的是同一件事：幸存会话的 context 必须一次都没被关过。
    assert sessions.SESSIONS["new"].context.closed is False


async def test_close_is_idempotent_and_unknown_is_ok():
    s = _put("s1")
    assert await sessions.drop("s1") is True
    assert s.context.closed is True
    assert await sessions.drop("s1") is True
    assert await sessions.drop("nope") is True


def test_registry_limits_are_the_ones_the_spec_pinned():
    """上限值集中钉一次（进度账 Task 1 条目把这些默认值全押在 T3 身上）。

    漂了不会有任何报错：expect 从 3000 漂到 30000 只是每次多等一点，session 上限从 2 漂到
    20 才是真出事——而它发生在 `mem_limit: 1g` 的容器里。
    """
    assert sessions.MAX_SESSIONS_DEFAULT == 2
    assert sessions.TTL_SECONDS_DEFAULT == 120
    assert sessions.STEP_TIMEOUT_MS_DEFAULT == 3000
    assert sessions.STEP_TIMEOUT_MS_MAX == 8000
    assert sessions.SETTLE_MS_MAX == 5000
    assert sessions.ACTIONS == ("click", "fill", "press", "select", "scroll_into_view",
                                "navigate")
    assert "screenshot" not in sessions.ACTIONS      # 裁定 R-4：截图不是模型的动作


def test_env_overrides_are_read_at_call_time_and_bad_values_fall_back(monkeypatch):
    """compose 显式烘入的这两个键要真的生效（T6 那条「不接受只靠代码默认值」的依赖侧）。"""
    monkeypatch.setenv("ZEROCODE_BROWSER_MAX_SESSIONS", "5")
    monkeypatch.setenv("ZEROCODE_BROWSER_SESSION_TTL_SECONDS", "30")
    assert sessions.max_sessions() == 5
    assert sessions.session_ttl() == 30

    monkeypatch.setenv("ZEROCODE_BROWSER_MAX_SESSIONS", "  ")   # 空值 = 没设，用默认
    monkeypatch.setenv("ZEROCODE_BROWSER_SESSION_TTL_SECONDS", "abc")
    assert sessions.max_sessions() == sessions.MAX_SESSIONS_DEFAULT
    assert sessions.session_ttl() == sessions.TTL_SECONDS_DEFAULT


def test_origin_url_folds_a_full_url_down_to_its_origin():
    """会话存的必须是 origin：存整条 URL 会让「同源」判定跟着首轮 path 走。"""
    assert sessions.origin_url("http://h:8210/orders?id=4") == "http://h:8210"
    assert sessions.origin_url("https://h/orders") == "https://h"          # 默认端口省略
    assert sessions.origin_url("http://[fdc4::254]:8210/a") == "http://[fdc4::254]:8210"
    assert sessions.origin_url("not-a-url") == ""


def test_navigation_url_only_accepts_a_relative_path_on_the_same_origin():
    assert sessions.navigation_url("http://h:8210", "/orders?id=4") == "http://h:8210/orders?id=4"
    # //host/ 是 protocol-relative（换 host）、`\` 是 Windows 风格的双斜杠别名、
    # 绝对地址与相对片段都能把主文档带出 origin——全部拒绝。
    for bad in ("//evil.com/x", "\\\\evil.com/x", "http://evil.com/", "orders", "", None):
        assert sessions.navigation_url("http://h:8210", bad) is None, bad
    # 控制字符：换行混进 URL 是经典的绕过形状，goto 之前先挡掉
    assert sessions.navigation_url("http://h:8210", "/ok\r\njavascript:alert(1)") is None
    assert sessions.navigation_url("", "/ok") is None


def test_charge_is_the_single_place_steps_are_counted():
    s = _put("s1")
    assert (sessions.charge(s), sessions.charge(s)) == (1, 2)
    assert s.steps == 2


def test_active_summary_shape_and_idle_reading():
    s = _put("s1")
    s.steps = 3
    s.last_used = 1000.0
    assert sessions.active_summary(now=1002.5) == [
        {"session_id": "s1", "steps": 3, "idle_seconds": 2.5}]


# ---------------------------------------------------------------------------
# Step 5：动作分派与「按断言轮询」。后者是本功能存在的理由——固定 sleep 遇到自带
# 开场动画的应用，截图与文本必然停在动画中段。
# ---------------------------------------------------------------------------
from browser import sessions as S  # noqa: E402  brief 原文如此：同模块的第二个别名，读起来短


async def test_resolve_target_rejects_bad_ref():
    _put()
    # 合法令牌后面拼注入字符：形状闸必须在拼进选择器之前挡下
    assert S.resolve_target("rd2k7qx; x") is None
    assert S.resolve_target('rd2k7qx"][x') is None
    assert S.resolve_target("") is None
    # 旧形状（按采集顺序排的序号）**必须**判非法：留着它，模型抄一个上一轮的句柄还能点下去，
    # 「旧句柄必然查不到」这条验收就没了（规格 §6.2 / §15）
    assert S.resolve_target("e1") is None
    assert S.resolve_target("e7") is None
    sel = S.resolve_target("rk2m7xt")
    assert sel == '[data-zc-ref="rk2m7xt"]'


async def test_ref_is_present_answers_from_the_page_and_fails_open():
    """`ref_is_present` 是「旧令牌查不到」唯一的检测点，它自己不许新增失败模式。

    - 页面上没有这个属性 → False，端点据此回「ref 无效或页面已变化」（而不是让模型等满一次
      可点超时、再把同一件事报成「元素不可点击」）；
    - 数本身炸了（页面正在导航、执行上下文被换掉）→ **放行**，交给动作去撞它自己的超时。
      这里若判成 False，一次正常的点击就会被说成一句作废的句柄。
    """
    class _Counting:
        def __init__(self, n):
            self._n = n

        async def count(self):
            return self._n

    class _PageThatCounts:
        def __init__(self, n):
            self._n = n

        def locator(self, selector):
            return _Counting(self._n)

    class _PageThatCannotAnswer:
        def locator(self, selector):
            raise RuntimeError("Execution context was destroyed, most likely because of "
                               "a navigation.")

    s = _put()
    s.page = _PageThatCounts(1)
    assert await S.ref_is_present(s, '[data-zc-ref="rd2k7qx"]') is True
    s.page = _PageThatCounts(0)
    assert await S.ref_is_present(s, '[data-zc-ref="rd2k7qx"]') is False
    s.page = _PageThatCannotAnswer()
    assert await S.ref_is_present(s, '[data-zc-ref="rd2k7qx"]') is True


async def test_perform_action_dispatches_each_kind():
    s = _put()
    # 这一层不管打标，动作只认选择器；替身因此要把这四个句柄当成「页面上在场」的
    s.page.stamped = {"rd2k7qx", "r4xwntk", "rp9zsdw", "r7kqmbt"}
    await S.perform_action(s, "click", '[data-zc-ref="rd2k7qx"]', None, 3000)
    await S.perform_action(s, "fill", '[data-zc-ref="r4xwntk"]', "hello", 3000)
    await S.perform_action(s, "press", '[data-zc-ref="rp9zsdw"]', "Enter", 3000)
    await S.perform_action(s, "scroll_into_view", '[data-zc-ref="r7kqmbt"]', None, 3000)
    assert s.page.clicked == ['[data-zc-ref="rd2k7qx"]', '[data-zc-ref="rp9zsdw"]+Enter',
                              'scroll:[data-zc-ref="r7kqmbt"]']
    assert s.page.filled == [('[data-zc-ref="r4xwntk"]', "hello")]


async def test_perform_action_unknown_is_a_value_error():
    s = _put()
    with pytest.raises(ValueError):
        await S.perform_action(s, "teleport", '[data-zc-ref="rd2k7qx"]', None, 3000)


async def test_expect_poll_passes_when_text_lands_late():
    """开机动画场景的回归锚：断言要轮询到满足为止，不是固定 sleep 一次定生死。"""
    class LatePage(FakePage):
        def __init__(self):
            super().__init__()
            self.ticks = 0

        async def evaluate(self, script, *a):
            if script == INTERACTIVE_JS:
                return []
            self.ticks += 1  # 一次轮询读一次正文
            return "牌桌" if self.ticks >= 3 else "正在配置组件…"

    s = _put()
    s.page = LatePage()
    passed, note, snap = await S.await_expect(s, {"type": "text_contains", "value": "牌桌"},
                                              timeout_ms=3000)
    assert passed is True
    assert s.page.ticks >= 3
    assert "牌桌" in snap["text"]


async def test_expect_poll_times_out_honestly():
    class NeverPage(FakePage):
        async def evaluate(self, script, *a):
            return [] if script == INTERACTIVE_JS else "一直转圈"

    s = _put()
    s.page = NeverPage()
    passed, note, snap = await S.await_expect(s, {"type": "text_contains", "value": "好了"},
                                              timeout_ms=300)
    assert passed is False
    assert "好了" in note


async def test_expect_poll_last_snapshot_wins_on_timeout():
    """超时那一轮必须回「现在到底长啥样」：模型要看的是中间态过后的现状。"""
    class GrowingPage(FakePage):
        def __init__(self):
            super().__init__()
            self.ticks = 0

        async def evaluate(self, script, *a):
            if script == INTERACTIVE_JS:
                return []
            self.ticks += 1
            return f"第 {self.ticks} 帧"

    s = _put()
    s.page = GrowingPage()
    passed, _, snap = await S.await_expect(s, {"type": "text_contains", "value": "不会出现"},
                                           timeout_ms=250, interval_ms=50)
    assert passed is False
    assert s.page.ticks >= 3
    assert f"第 {s.page.ticks} 帧" in snap["text"]


async def test_no_new_console_error_is_not_vacuously_true():
    """`no_new_console_error` 必须真的看得见到控制台错误。

    判定入参给空列表的话这条断言恒过——模型写「点完不许报错」，工具回 passed:true，
    页面其实正在抛 TypeError。恒过和恒假是同一类静默失效（R-13 的另一半）。
    """
    s = _put()
    s.collectors.console.append({"level": "error", "text": "boom"})
    passed, note, _ = await S.await_expect(s, {"type": "no_new_console_error"}, timeout_ms=120)
    assert passed is False
    assert "1" in note

    s.console_seen = {("error", "boom")}       # 上一步已经报过账的错误不该再算新增
    passed, _, _ = await S.await_expect(s, {"type": "no_new_console_error"}, timeout_ms=120)
    assert passed is True


async def test_peeking_console_does_not_bill_the_seen_set():
    """轮询期间的「偷看」不许动增量账：看了就记账，`_step_state` 会把同一批错误当成旧账吞掉。"""
    s = _put()
    s.collectors.console.append({"level": "error", "text": "boom"})
    await S.await_expect(s, {"type": "no_new_console_error"}, timeout_ms=60)
    assert s.console_seen == set()


async def test_snapshot_state_delegates_to_the_shared_collector(monkeypatch):
    """R-1 的闸：`snapshot_state` 是 `collect_page_state` 的薄封装，不是第二套采集。

    自己再拼一遍 title/innerText/INTERACTIVE_JS 的话，同一页的同一眼状态会在快检与走查
    两条路径里给出两种裁剪口径——而漂移只在生产环境看得见。
    """
    import browser.app as app_mod

    seen: list[tuple] = []

    async def spy(page, collectors, *, started, timed_out, interactive_limit=40,
                  include_screenshot=False):
        seen.append((page, collectors, timed_out, interactive_limit, include_screenshot))
        return {"ok": True, "text": "牌桌", "interactive": []}

    monkeypatch.setattr(app_mod, "collect_page_state", spy)
    s = _put()
    out = await S.snapshot_state(s, 12)

    assert out["text"] == "牌桌"
    assert seen == [(s.page, s.collectors, False, 12, False)]


# ---------------------------------------------------------------------------
# Step 9 起：端点契约（规格 §6.1）。这一段才真正是 Task 4/5 要对接的东西，
# 所以字段名、状态码、扣不扣步数、附不附新清单都在这儿钉住，而不是留给真机走查。
# ---------------------------------------------------------------------------

NODES = [
    {"ref": "rd2k7qx", "role": "button", "name": "新建订单"},
    {"ref": "r4xwntk", "role": "textbox", "name": "备注"},
    {"ref": "rp9zsdw", "role": "textbox", "name": "口令", "type": "password"},
]


async def _identity_connect_url(url: str) -> str:
    """把 host→IP 的重写打成恒等：测试机上没有那套容器网络，也不该真去解析 DNS。"""
    return url


@pytest.fixture()
def client(monkeypatch):
    """起一个假 playwright 的 sidecar：TestClient 进上下文才触发 `_startup`。

    打桩点在 `async_playwright` 而不是 `_browser`——`_startup` 会给模块全局 `_browser`
    重新赋值，提前塞进去的假浏览器会被它覆盖掉。
    """
    import browser.app as app_mod

    fake_browser = FakeBrowser()
    monkeypatch.setattr(app_mod, "async_playwright", lambda: FakePlaywright(fake_browser))
    monkeypatch.setattr(app_mod, "_connect_url", _identity_connect_url)
    with TestClient(app) as c:
        c.fake_browser = fake_browser
        yield c


def _open(client, **kw):
    body = {"url": OPEN_URL}
    body.update(kw)
    return client.post("/sessions", json=body)


def _step(client, sid, **kw):
    return client.post(f"/sessions/{sid}/step", json=kw)


def _opened(client, page_factory=None):
    """开到「确实有一个活会话」的那一步，把 session_id 交给调用方。"""
    if page_factory is not None:
        client.fake_browser.page_factory = page_factory
    r = _open(client)
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    return r.json()["session_id"]


def test_open_rejects_host_outside_allowlist(client):
    r = client.post("/sessions", json={"url": "http://example.com/"})
    assert r.status_code == 400
    assert "允许清单" in r.json()["detail"]


def test_open_refuses_when_the_browser_is_not_ready(client, monkeypatch):
    """503 而不是 500：容器刚起来、Chromium 还没 launch 完时后端要能读懂「还没就绪」。"""
    import browser.app as app_mod

    monkeypatch.setattr(app_mod, "_browser", None)
    r = _open(client)
    assert r.status_code == 503
    assert r.json()["detail"] == "浏览器尚未就绪"
    assert sessions.SESSIONS == {}


def test_open_fixes_the_origin_of_the_rewritten_connect_url(client, monkeypatch):
    """会话 origin 跟着**重写后**的地址，不是后端传来的显示地址。

    `_connect_url` 把 `host.docker.internal` 换成容器可达 IP（Vite 的 allowedHosts 只放
    IP 与 localhost），而 `allow_request` 与 `navigate` 都拿这个 origin 做同源判定：存错了
    就等于每一张页面的子资源请求都被自己人判成跨源、全部阻断。
    """
    import browser.app as app_mod

    async def rewrite(url: str) -> str:
        return url.replace("127.0.0.1:8210", "192.168.65.254:8210")

    monkeypatch.setattr(app_mod, "_connect_url", rewrite)
    body = _open(client).json()
    s = sessions.SESSIONS[body["session_id"]]
    assert s.origin == "http://192.168.65.254:8210"
    assert s.page.goto_calls[0][0] == "http://192.168.65.254:8210/"
    assert body["url"] == "http://192.168.65.254:8210/"     # 内部地址，后端展示前要换回 display_url


def test_open_refuses_when_at_capacity(client, monkeypatch):
    """超限是拒绝、不是排队，而且**不动别人正在用的会话**（§6.2 第一条不变式）。"""
    monkeypatch.setenv("ZEROCODE_BROWSER_MAX_SESSIONS", "1")
    sessions.SESSIONS["occupied"] = _put("occupied")
    r = _open(client)
    assert r.status_code == 409
    assert "已满" in r.json()["detail"]
    assert list(sessions.SESSIONS) == ["occupied"]
    assert sessions.SESSIONS["occupied"].context.closed is False
    assert client.fake_browser.contexts == []   # 连 context 都不该为被拒的请求建出来


def test_open_registers_a_session_and_returns_the_state_contract(client):
    def factory():
        page = FakePage(url=OPEN_URL, nodes=NODES)
        page.console_on_goto = [("error", "首屏就炸了")]
        return page

    client.fake_browser.page_factory = factory
    r = _open(client)
    body = r.json()
    assert r.status_code == 200
    # 契约字段一个不许少（Task 4 的 browser_open 按名字取值，改名就是静默 None）
    # page_errors 也在契约里：裁定 R-27 之后后端要读它（open 全量 / step 增量 `page_errors_new`）
    assert {"ok", "session_id", "title", "text", "url", "interactive",
            "console", "page_errors", "failed_requests", "elapsed_ms"} <= set(body)
    assert body["ok"] is True and body["elapsed_ms"] >= 0
    s = sessions.SESSIONS[body["session_id"]]
    assert s.origin == "http://127.0.0.1:8210"      # origin 在创建时固化，不跟着 path 走
    assert s.steps == 0
    # 首屏 console 是整份报的（增量账从这里才开始），失败请求同理
    assert body["console"] == [{"level": "error", "text": "首屏就炸了", "truncated": False}]
    assert body["page_errors"] == []


def test_open_does_not_take_the_inspect_semaphore(client, monkeypatch):
    """会话不许占 `_sem` 的槽位：一个走查能活几分钟，占住全局并发会把 preview_page 饿死。"""
    import browser.app as app_mod

    async def exploding_acquire(*a, **kw):
        raise AssertionError("session 端点不该走 _sem")

    monkeypatch.setattr(app_mod._sem, "acquire", exploding_acquire)
    assert _open(client).status_code == 200


def test_open_with_expect_polls_until_the_text_lands(client):
    """US-06：开场动画 2.1s 的应用，首屏等到文案出现才回，而不是截在动画中段。"""
    class LatePage(FakePage):
        def __init__(self):
            super().__init__(url=OPEN_URL, nodes=NODES)
            self.ticks = 0

        async def evaluate(self, script, *a):
            if script == INTERACTIVE_JS:
                return self.nodes
            self.ticks += 1
            return "订单已创建" if self.ticks >= 3 else "正在配置组件…"

    client.fake_browser.page_factory = LatePage
    body = client.post("/sessions", json={
        "url": OPEN_URL, "expect": {"type": "text_contains", "value": "订单已创建"}}).json()
    assert body["passed"] is True
    assert body["note"] == ""            # 通过时不许带回失败措辞（携带项：那个三元判断）
    assert [i["ref"] for i in body["interactive"]] == ["rd2k7qx", "r4xwntk", "rp9zsdw"]
    assert body["interactive"][2]["sensitive"] is True
    # 轮询到第 3 帧才满足：固定 sleep 一次定生死的话读到的还是动画那两句
    assert sessions.SESSIONS[body["session_id"]].page.ticks >= 3


def test_open_reports_gone_and_leaves_no_session_when_goto_fails(client):
    def factory():
        page = FakePage(url=OPEN_URL)
        page.goto_error = RuntimeError("net::ERR_CONNECTION_REFUSED")
        return page

    client.fake_browser.page_factory = factory
    body = _open(client).json()
    assert body == {"ok": False, "error": "页面打开失败: net::ERR_CONNECTION_REFUSED",
                    "session_gone": True}
    assert sessions.SESSIONS == {}       # 失败不能把 context 留在注册表里等 OOM
    assert client.fake_browser.contexts[0].closed is True


def test_step_on_unknown_session_reports_session_gone(client):
    r = _step(client, "nope", action="click", ref="rd2k7qx")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["session_gone"] is True
    assert body["steps_charged"] is False


def test_delete_unknown_session_is_200_ok(client):
    assert client.delete("/sessions/nope").json() == {"ok": True}


def test_delete_closes_the_context_and_is_idempotent(client):
    sid = _opened(client)
    context = sessions.SESSIONS[sid].context
    assert client.delete(f"/sessions/{sid}").json() == {"ok": True}
    assert context.closed is True
    assert sessions.SESSIONS == {}
    assert client.delete(f"/sessions/{sid}").json() == {"ok": True}   # 重复收口也要回 ok


def test_step_rejects_illegal_ref(client):
    sid = _opened(client, lambda: FakePage(url=OPEN_URL, nodes=NODES))
    r = _step(client, sid, action="click", ref='rd2k7qx"][x')
    body = r.json()
    assert body["ok"] is False
    assert "ref" in body["error"]
    # 当场附带最新交互清单（US-09）：不给的话 Agent 要多花一次往返才知道该指哪儿
    assert [i["ref"] for i in body["interactive"]] == ["rd2k7qx", "r4xwntk", "rp9zsdw"]
    assert body["steps_charged"] is True
    assert sessions.SESSIONS[sid].steps == 1


class ScanningPage(FakePage):
    """替身里「真会重新打标」的那一页：节点按**传进来的令牌**现造，每轮整批换发。

    真脚本就是这个形状（`INTERACTIVE_JS` 取 `tokens[i]` 并 `setAttribute` 同一个值）。
    静态 `nodes=` 的替身标不出「换了一批令牌」这件事，而本次改动要验收的恰恰是它。
    """

    ELEMENTS = (("button", "新建订单"), ("textbox", "备注"), ("textbox", "口令"))

    def __init__(self):
        super().__init__(url=OPEN_URL, nodes=[])

    async def evaluate(self, script, *a):
        if script != INTERACTIVE_JS:
            return await super().evaluate(script, *a)
        if self.evaluate_error is not None:
            raise self.evaluate_error
        tokens = list(a[0]) if a else []
        self.tokens_seen.append(tokens)
        self.nodes = [{"ref": token, "role": role, "name": name,
                       **({"type": "password"} if name == "口令" else {})}
                      for token, (role, name) in zip(tokens, self.ELEMENTS)]
        self.stamped = {str(n["ref"]) for n in self.nodes}
        return self.nodes


def test_a_stale_token_from_the_previous_scan_fails_loudly_and_brings_the_new_list(client):
    """本次改动的验收点（规格 §6.2 / §15「ref 失效」那一行）。

    序号形状下这一格回的是 **`ok:true`**：旧序号在新清单里大概率仍然合法，于是静默点到另一个
    元素（真机实测 `stale_ref_still_exists_in_new_list:true`）。令牌形状下旧令牌在新扫描里查不到，
    必须落到既有的那条「ref 无效或页面已变化」出口：`ok:false` + 那句中文 + 当场带回**新清单**
    （省一次往返，US-09）+ 这一步照样扣额度。自动附图在后端那一层按 R-26 的口径补（`ok:false`
    那一支），`tests/test_browser_session_tools.py` 里钉着。
    """
    client.fake_browser.page_factory = ScanningPage
    opened = _open(client).json()
    sid = opened["session_id"]
    old_refs = [i["ref"] for i in opened["interactive"]]
    assert len(old_refs) == 3 and all(REF_RE.fullmatch(r) for r in old_refs)

    # 先确认「新令牌点得动」：否则下面那条失败可能只是因为替身压根点不动
    fresh = _step(client, sid, action="click", ref=old_refs[0]).json()
    assert fresh["ok"] is True, fresh
    page = sessions.SESSIONS[sid].page
    assert page.clicked[-1] == f'[data-zc-ref="{old_refs[0]}"]'

    # 这一步之后页面又重扫了一次（`_after_action` 采集），令牌整批换发
    new_refs = [i["ref"] for i in fresh["interactive"]]
    assert new_refs and set(new_refs).isdisjoint(old_refs)

    stale = _step(client, sid, action="click", ref=old_refs[1])
    body = stale.json()
    assert stale.status_code == 200, stale.text
    assert body["ok"] is False
    assert body["error"] == "ref 无效或页面已变化，请用最新清单里的 ref"
    # 带回的是**这一轮**的清单，不是旧的那一份
    assert [i["ref"] for i in body["interactive"]] == [n["ref"] for n in page.nodes]
    assert {i["ref"] for i in body["interactive"]}.isdisjoint(old_refs)
    assert body["steps_charged"] is True
    # 没有把「查不到」误报成「点不动」：模型对两者的下一步不一样
    assert "元素不可点击" not in body["error"]
    assert page.clicked[-1] != f'[data-zc-ref="{old_refs[1]}"]'      # 动作根本没被执行


def test_the_token_pool_size_is_the_scan_cap_end_to_end(client):
    """令牌池不够用时不许越界（规格 §6.2）：池与遍历上限同值，触顶照样报截断。

    判据只有一处（`shape_interactive` 看节点数 >= SCAN_MAX）。如果哪天把池调小而不改这里，
    脚本会先被令牌耗尽卡住、节点数却达不到 SCAN_MAX，模型看到少了条目的清单还写着
    `interactive_truncated:false`——这条要红的是**同一个测试**，不是等真机发现。
    """
    from browser.session_core import REF_TOKEN_POOL

    assert REF_TOKEN_POOL == INTERACTIVE_JS_SCAN_MAX
    sid = _opened(client)
    page = sessions.SESSIONS[sid].page
    # 采集器确实把「按遍历上限那么大的一批令牌」交给了打标脚本
    assert len(page.tokens_seen[-1]) == INTERACTIVE_JS_SCAN_MAX
    assert len(set(page.tokens_seen[-1])) == INTERACTIVE_JS_SCAN_MAX


def test_step_returns_incremental_console_only(client):
    """US-04：每步只回「上一步以来新增」。首屏报过的错误不能再在第二步重播一遍。"""
    def factory():
        page = FakePage(url=OPEN_URL, nodes=NODES)
        page.console_on_goto = [("error", "首屏就炸了")]
        return page

    sid = _opened(client, factory)
    s = sessions.SESSIONS[sid]
    body = _step(client, sid, action="click", ref="rd2k7qx").json()
    assert body["ok"] is True and body["steps_charged"] is True
    assert body["console_new"] == []
    assert "console" not in body          # 全量清单已经被增量取代，别两份一起发
    assert s.page.clicked == ['[data-zc-ref="rd2k7qx"]']

    s.collectors.console.append({"level": "error", "text": "这一步才炸的"})
    second = _step(client, sid, action="fill", ref="r4xwntk", value="备注内容").json()
    assert second["console_new"] == [{"level": "error", "text": "这一步才炸的",
                                      "truncated": False}]
    assert second["failed_requests_new"] == []
    assert s.page.filled == [('[data-zc-ref="r4xwntk"]', "备注内容")]
    assert s.steps == 2


def test_step_new_items_respect_the_shaping_budget(client):
    """增量条目也走 shape_result 那把尺：40 条 / 单条 1000 字的上限不许因为「是增量」而漂。"""
    sid = _opened(client, lambda: FakePage(url=OPEN_URL, nodes=NODES))
    s = sessions.SESSIONS[sid]
    s.collectors.console.extend({"level": "error", "text": f"e{i} " + "x" * 1200}
                               for i in range(45))
    # page_errors 的同一条尺（裁定 R-27）：10 条 / 单条 1000 字，预算只在 shape_result 定义一次
    s.collectors.page_errors.extend(f"e{i} " + "x" * 1200 for i in range(12))
    body = _step(client, sid, action="click", ref="rd2k7qx").json()
    assert len(body["console_new"]) == 40
    assert body["truncated"] is True
    assert all(i["truncated"] is True for i in body["console_new"])
    # 1000 字正文 + 「…（已截断）」这 6 字标记，就是 _clip 的形状；多一个字符都是预算漂了
    assert all(len(i["text"]) == 1006 for i in body["console_new"])
    assert len(body["page_errors_new"]) == 10
    assert all(len(e) == 1006 for e in body["page_errors_new"])


def test_page_errors_new_replays_nothing_already_reported(client):
    """裁定 R-27：`page_errors` 是与 console 同款的**增量**账，不是每步重播的全量。

    未捕获异常（`pageerror`）比 console.error 更能说明「我这一步把页面点崩了」，而全量累积
    会让首屏那一次崩溃在之后每一步都以旧账的模样出现——模型分不清是自己点崩的还是页面本来就崩的。
    """
    sid = _opened(client, lambda: FakePage(url=OPEN_URL, nodes=NODES))
    s = sessions.SESSIONS[sid]
    s.collectors.page_errors.append("Uncaught TypeError: 这一步崩的")
    first = _step(client, sid, action="click", ref="rd2k7qx").json()
    assert first["page_errors_new"] == ["Uncaught TypeError: 这一步崩的"]
    # 与 console 同一口径：全量那份已经被增量取代，别两份一起发
    assert "page_errors" not in first

    second = _step(client, sid, action="click", ref="rd2k7qx").json()
    assert second["page_errors_new"] == []      # 上一步报过的不在本步

    s.collectors.page_errors.append("Uncaught ReferenceError: 这一步才崩")
    third = _step(client, sid, action="click", ref="rd2k7qx").json()
    assert third["page_errors_new"] == ["Uncaught ReferenceError: 这一步才崩"]


def test_open_bills_the_first_screen_page_errors(client):
    """首屏的崩溃整份报过一次，账就要从这里开始记：不记的话第二步会把它当「本步新增」重播。"""
    def factory():
        page = FakePage(url=OPEN_URL, nodes=NODES)
        page.pageerror_on_goto = ["Uncaught TypeError: 首屏就崩了"]
        return page

    client.fake_browser.page_factory = factory
    opened = _open(client).json()
    # open 那一面仍是全量 `page_errors`（Task 2 起的既有契约，与 step 的增量键名刻意不同）
    assert opened["page_errors"] == ["Uncaught TypeError: 首屏就崩了"]
    body = _step(client, opened["session_id"], action="click", ref="rd2k7qx").json()
    assert body["page_errors_new"] == []
    assert sessions.SESSIONS[opened["session_id"]].page_errors_seen == {
        ("", "Uncaught TypeError: 首屏就崩了")}


def test_step_with_expect_marks_passed_and_note(client):
    sid = _opened(client, lambda: FakePage(url=OPEN_URL, text="订单已创建", nodes=NODES))
    ok = _step(client, sid, action="click", ref="rd2k7qx",
               expect={"type": "text_contains", "value": "订单已创建"}).json()
    assert ok["passed"] is True and ok["note"] == "" and ok["steps_charged"] is True
    bad = _step(client, sid, action="click", ref="rd2k7qx",
                expect={"type": "text_contains", "value": "不会出现的话"},
                timeout_ms=500).json()
    # 走通了但断言不过 = ok:true + passed:false（动作成功与业务断言是两件事）
    assert bad["ok"] is True and bad["passed"] is False
    assert "不会出现的话" in bad["note"]


def test_step_without_expect_has_no_passed_key(client):
    sid = _opened(client)
    body = _step(client, sid, action="click", ref="rd2k7qx").json()
    assert "passed" not in body and "note" not in body


def test_step_unknown_action_is_not_charged(client):
    sid = _opened(client)
    body = _step(client, sid, action="teleport", ref="rd2k7qx").json()
    assert body == {"ok": False, "error": "未知动作: teleport", "steps_charged": False}
    assert sessions.SESSIONS[sid].steps == 0


def test_step_unclickable_element_charges_and_rescans(client):
    sid = _opened(client, lambda: FakePage(url=OPEN_URL, nodes=NODES))
    sessions.SESSIONS[sid].page.click_error = TimeoutError("element is not visible")
    body = _step(client, sid, action="click", ref="rd2k7qx").json()
    assert body["ok"] is False
    assert "元素不可点击或动作失败" in body["error"]
    assert [i["ref"] for i in body["interactive"]] == ["rd2k7qx", "r4xwntk", "rp9zsdw"]
    assert body["steps_charged"] is True
    assert sessions.SESSIONS[sid].steps == 1


def test_action_failure_reports_the_new_items_of_the_broken_step(client):
    """裁定 R-29：`_action_failure` 复用 `_step_state`，「点不动」那一步也带本步增量信号。

    真机里最常见的一格是「点下去把页面点崩了、动作本身却报失败」：点击触发的脚本抛未捕获异常，
    Playwright 因此等不到稳定态。原来这条响应只有 `error` + 清单，模型拿到一张崩页图却读不到
    一句文字证据，只能靠图猜——而那件证据下一步才浮出，且因为这一步没记账，下一步会把这次的
    崩溃当成「本步新增」，归因错一位。
    """
    sid = _opened(client, lambda: FakePage(url=OPEN_URL, nodes=NODES))
    s = sessions.SESSIONS[sid]
    s.page.click_error = TimeoutError("element is not visible")
    # 崩溃发生在这一「步」里：首屏那批已由 open 记过账，这三条是动作之后才出现的
    s.collectors.page_errors.append("Uncaught TypeError: 被点崩了 at app.js:1:9")
    s.collectors.console.append({"level": "error", "text": "点完就报的错"})
    s.collectors.failed.append({"method": "GET", "url": "http://127.0.0.1:8210/api/orders",
                                "status": 500, "type": "fetch", "blocked": False})
    body = _step(client, sid, action="click", ref="rd2k7qx").json()
    assert body["ok"] is False and "元素不可点击或动作失败" in body["error"]
    # 形状仍是「点不动」：`passed` 这个键一个字都不许出现在这条响应里（与断言失败分家）
    assert "passed" not in body and "note" not in body
    assert body["page_errors_new"] == ["Uncaught TypeError: 被点崩了 at app.js:1:9"]
    assert [i["text"] for i in body["console_new"]] == ["点完就报的错"]
    assert [i["url"] for i in body["failed_requests_new"]] == ["http://127.0.0.1:8210/api/orders"]
    assert body["steps_charged"] is True and s.steps == 1
    # 增量键成族，全量那份不与自己并存（裁定 R-27 的口径）
    assert "page_errors" not in body and "console" not in body and "failed_requests" not in body

    # 记账就在失败这一步做完：下一步不许把这批旧账当「本步新增」重播一遍
    s.page.click_error = None
    second = _step(client, sid, action="click", ref="rd2k7qx").json()
    assert second["ok"] is True
    assert second["page_errors_new"] == [] and second["console_new"] == []
    assert second["failed_requests_new"] == []


def test_dead_page_is_reported_as_gone_and_dropped(client):
    """「这一步做砸了」和「这个会话物理上没了」必须分开：后者要让后端释放额度并重建会话。"""
    sid = _opened(client, lambda: FakePage(url=OPEN_URL, nodes=NODES))
    sessions.SESSIONS[sid].page.click_error = RuntimeError(
        "Target page, context or browser has been closed")
    body = _step(client, sid, action="click", ref="rd2k7qx").json()
    assert body["ok"] is False and body["session_gone"] is True
    assert body["steps_charged"] is False
    # 裁定 R-29 之后失败响应带采集状态，但「会话没了」那条分诊仍在采集之前：页面物理上不存在，
    # 就没有「本步新增」可报，报一个空列表会被读成「这一步没崩」。
    assert "page_errors_new" not in body and "console_new" not in body
    assert sessions.SESSIONS == {}


def test_step_after_action_read_failure_is_readable_not_a_500(client):
    """「动作执行成功、随后读不回页面」是常态失败，必须是 200 + ok:false + 原因。

    真机场景就是本功能要治的那类 SPA：点一下触发重新渲染/主文档导航，`page.evaluate` 抛
    「Execution context was destroyed」——既不是 gone 也不是 timeout，落不进前面任何一条分诊，
    而外层若把它继续往上抛，FastAPI 就回一个没有原因的空壳 500。更糟的是 `charge` 已经在
    采集之前跑过了：sidecar 记了这一步，后端却什么也读不出来，重试语义两难。
    """
    class ReRenderingPage(FakePage):
        """点击之后紧跟的那一次采集抛错，再采一帧就恢复正常（页面导航已经过去）。"""

        def __init__(self):
            super().__init__(url=OPEN_URL, nodes=NODES)
            self.broken_reads_left = 0

        async def click(self, sel, timeout=None):
            await super().click(sel, timeout=timeout)
            self.broken_reads_left = 1

        async def evaluate(self, script, *a):
            if self.broken_reads_left:
                self.broken_reads_left -= 1
                raise RuntimeError("Execution context was destroyed, most likely because of "
                                   "a navigation.")
            return await super().evaluate(script, *a)

    sid = _opened(client, ReRenderingPage)
    r = _step(client, sid, action="click", ref="rd2k7qx")
    assert r.status_code == 200, r.text      # 不是 500：工具失败一律是带原因的正常结果
    body = r.json()
    assert body["ok"] is False
    assert "步骤执行后页面读取失败" in body["error"]
    assert "Execution context was destroyed" in body["error"]    # 原因原文要在，不许糊掉
    assert body["steps_charged"] is True     # 动作确实执行过，账也已经落下
    assert sessions.SESSIONS[sid].steps == 1
    # 尽力重采拿到了清单（US-09 省一次往返）；会话没有被摘掉——读不回一帧不等于会话没了
    assert [i["ref"] for i in body["interactive"]] == ["rd2k7qx", "r4xwntk", "rp9zsdw"]
    assert "session_gone" not in body
    assert sid in sessions.SESSIONS


def test_step_omits_the_ref_list_when_the_retry_also_fails(client):
    """重采也读不回来时只回原因：`interactive` 宁缺毋滥，更不能把它变成 500。"""
    sid = _opened(client, lambda: FakePage(url=OPEN_URL, nodes=NODES))
    sessions.SESSIONS[sid].page.evaluate_error = RuntimeError(
        "Execution context was destroyed, most likely because of a navigation.")
    r = _step(client, sid, action="click", ref="rd2k7qx")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is False and "步骤执行后页面读取失败" in body["error"]
    assert body["steps_charged"] is True
    assert "interactive" not in body          # 没有可信清单就别摆一个空的出来
    assert sid in sessions.SESSIONS


def test_step_dialog_is_reported_once_and_only_for_that_step(client):
    """US-11：原生弹窗按取消处理并显式回报；而且「本步弹过一次」不能变成「每步都弹过」。"""
    def factory():
        page = FakePage(url=OPEN_URL, nodes=NODES)
        page.dialog_on_click = "确认删除全部订单？"
        return page

    sid = _opened(client, factory)
    page = sessions.SESSIONS[sid].page
    first = _step(client, sid, action="click", ref="rd2k7qx").json()
    assert first["dialog"] == {"type": "confirm", "message": "确认删除全部订单？",
                               "handled": "dismissed"}
    assert first["ok"] is True           # 弹窗不改变「动作确实执行了」这件事
    page.dialog_on_click = None
    second = _step(client, sid, action="click", ref="rd2k7qx").json()
    assert second["dialog"] is None


async def test_a_dialog_raised_after_the_step_is_still_reported(client):
    """动作返回之后才弹出来的框（`setTimeout` 里那种）不能被下一步当成没发生过。

    读一次即清（`sessions.take_dialog`）而不是动作前清零，就是为了这一条：清零会把这一份
    直接吞掉，而 US-11 要防的正是「Agent 不知道有个 confirm 被取消了」。归属偏晚一步，
    但至少它出声了。
    """
    sid = _opened(client, lambda: FakePage(url=OPEN_URL, nodes=NODES))
    page = sessions.SESSIONS[sid].page
    assert _step(client, sid, action="click", ref="rd2k7qx").json()["dialog"] is None
    await page.handlers["dialog"](FakeDialog("confirm", "稍后才弹的"))
    body = _step(client, sid, action="click", ref="r4xwntk").json()
    assert body["dialog"] == {"type": "confirm", "message": "稍后才弹的",
                              "handled": "dismissed"}
    assert _step(client, sid, action="click", ref="r4xwntk").json()["dialog"] is None


def test_open_reports_a_dialog_raised_during_load(client):
    """加载期弹的框要算在 open 那一本账上，否则它会被追认到第一步动作上。"""
    class AlertingPage(FakePage):
        async def goto(self, url, **kw):
            await super().goto(url, **kw)
            await self.handlers["dialog"](FakeDialog("alert", "本站已被劫持"))

    sid = _opened(client, lambda: AlertingPage(url=OPEN_URL, nodes=NODES))
    body = _open(client).json()
    assert body["dialog"] == {"type": "alert", "message": "本站已被劫持",
                              "handled": "dismissed"}
    step = _step(client, sid, action="click", ref="rd2k7qx").json()
    assert step["dialog"] is None          # 不会串到下一步


def test_navigate_stays_on_the_session_origin(client):
    sid = _opened(client, lambda: FakePage(url=OPEN_URL, nodes=NODES))
    page = sessions.SESSIONS[sid].page
    body = _step(client, sid, action="navigate", path="/orders?id=4").json()
    assert body["ok"] is True and body["steps_charged"] is True
    assert page.goto_calls[-1][0] == "http://127.0.0.1:8210/orders?id=4"
    assert body["url"] == "http://127.0.0.1:8210/orders?id=4"

    for evil in ("//evil.com/x", "http://evil.com/", "relative/path", ""):
        bad = _step(client, sid, action="navigate", path=evil).json()
        assert bad["ok"] is False and "同源相对路径" in bad["error"], evil
        assert bad["steps_charged"] is True


def test_navigation_timeout_still_returns_the_current_state(client):
    """导航超时不是「这一步废了」：已渲染的内容照样读回来，并显式带上 timed_out。"""
    class SlowPage(FakePage):
        async def goto(self, url, **kw):
            raise TimeoutError("Timeout 500ms exceeded.")

    sid = _opened(client, lambda: SlowPage(url=OPEN_URL, nodes=NODES, text="卡在动画里"))
    body = _step(client, sid, action="navigate", path="/slow", timeout_ms=500).json()
    assert body["ok"] is True and body["timed_out"] is True
    assert body["text"] == "卡在动画里"
    assert body["steps_charged"] is True


def test_get_sessions_is_a_diagnostic_view(client):
    assert client.get("/sessions").json() == {"active": []}
    sid = _opened(client)
    _step(client, sid, action="click", ref="rd2k7qx")
    active = client.get("/sessions").json()["active"]
    assert len(active) == 1
    assert active[0]["session_id"] == sid
    assert active[0]["steps"] == 1
    assert active[0]["idle_seconds"] >= 0


def test_screenshot_endpoint_returns_only_the_shot_and_changes_nothing(client):
    """R-4：截图是后端替模型补的现场，不 touch、不计步、不进 ACTIONS。"""
    sid = _opened(client, lambda: FakePage(url=OPEN_URL, nodes=NODES))
    s = sessions.SESSIONS[sid]
    before = s.last_used
    body = client.post(f"/sessions/{sid}/screenshot").json()
    assert set(body) == {"ok", "screenshot_b64", "screenshot_width", "screenshot_height"}
    assert base64.b64decode(body["screenshot_b64"]) == b"\x89PNG\r\n\x1a\n"
    assert (body["screenshot_width"], body["screenshot_height"]) == (1280, 800)
    assert s.page.screenshot_calls == [{"type": "png", "full_page": False}]
    assert s.steps == 0 and s.last_used == before      # 续命与扣步都不干
    assert "screenshot" not in S.ACTIONS

    assert client.post("/sessions/nope/screenshot").json()["session_gone"] is True


def test_screenshot_failure_is_readable_not_a_500(client):
    sid = _opened(client, lambda: FakePage(url=OPEN_URL, nodes=NODES))
    sessions.SESSIONS[sid].page.screenshot_error = RuntimeError("Cannot take screenshot")
    body = client.post(f"/sessions/{sid}/screenshot").json()
    assert body["ok"] is False and "Cannot take screenshot" in body["error"]
    assert sid in sessions.SESSIONS          # 截图坏了不等于会话没了，别顺手把它摘了


def test_screenshot_on_a_dead_session_is_reported_as_gone(client):
    sid = _opened(client, lambda: FakePage(url=OPEN_URL, nodes=NODES))
    sessions.SESSIONS[sid].page.screenshot_error = RuntimeError(
        "Target page, context or browser has been closed")
    body = client.post(f"/sessions/{sid}/screenshot").json()
    assert body["ok"] is False and body["session_gone"] is True
    assert sessions.SESSIONS == {}


def test_interactive_max_can_shrink_but_never_exceed_the_cap(client):
    """40 条上限是规格钉死的：请求可以把清单调小，不能把它调大。"""
    many = [{"ref": ref, "role": "button", "name": f"按钮{i}"}
            for i, ref in enumerate(new_ref_tokens(45), start=1)]
    sid = _opened(client, lambda: FakePage(url=OPEN_URL, nodes=many))
    assert len(_step(client, sid, action="click", ref="rd2k7qx").json()["interactive"]) == 40
    small = _step(client, sid, action="click", ref="rd2k7qx", interactive_max=5).json()
    assert len(small["interactive"]) == 5
    assert small["interactive_truncated"] is True
    greedy = _step(client, sid, action="click", ref="rd2k7qx", interactive_max=5000).json()
    assert len(greedy["interactive"]) == 40


def test_settle_ms_is_bounded_and_optional(client):
    sid = _opened(client, lambda: FakePage(url=OPEN_URL, nodes=NODES))
    page = sessions.SESSIONS[sid].page
    _step(client, sid, action="click", ref="rd2k7qx", settle_ms=999999)
    assert page.waited.count(("timeout", S.SETTLE_MS_MAX)) == 1   # 裁到 5000，不照单全收


def test_sweeper_task_lives_and_dies_with_the_app(monkeypatch):
    """巡检任务必须跟着 `_startup`/`_shutdown` 生灭。

    建在模块期会绑死第一个事件循环（TestClient 换个循环就抛「bound to a different event
    loop」）；不在 `_shutdown` 里 cancel 则是收口后留一个 pending 任务，日志刷一片。
    """
    import browser.app as app_mod

    monkeypatch.setattr(app_mod, "async_playwright", lambda: FakePlaywright(FakeBrowser()))
    with TestClient(app) as c:
        task = app_mod._sweeper_task
        assert task is not None and not task.done()
        assert c.get("/healthz").json() == {"ok": True}
    assert app_mod._sweeper_task is None
    assert task.cancelled()


async def test_idle_session_is_swept_and_the_next_step_reports_gone(client):
    """TTL 那条不变式在端点上的投影：回收之后 step 必须回 session_gone，而不是别的错。"""
    sid = _opened(client)
    context = sessions.SESSIONS[sid].context
    sessions.SESSIONS[sid].last_used = time.monotonic() - 300
    assert await sessions.sweep_expired() == [sid]
    assert sessions.SESSIONS == {}
    assert context.closed is True                 # 回收是真的把 context 关了，不是只删表
    body = _step(client, sid, action="click", ref="rd2k7qx").json()
    assert body["session_gone"] is True and body["steps_charged"] is False


async def test_open_is_not_swept_away_while_the_page_is_still_loading(client):
    """巡检不许在 open 进行中把会话关掉——一次打开的预算比 TTL 还长。

    导航超时上限 120000ms 再套上 `wait_for` 的 5s 宽限 = 125s > TTL 120s，而 `last_used` 停在
    登记那一刻，巡检每 10s 一跳必然落在中间。命中的症状是回一句「页面打开失败」：方向没错，
    文案失真——真相是 goto 还没结束 context 就被自家巡检关了，排查的人会去查为什么打不开，
    而那个页面根本打得开。
    """
    class LoadingPage(FakePage):
        def __init__(self):
            super().__init__(url=OPEN_URL, nodes=NODES)
            self.swept_during_open: list = []

        async def goto(self, url, **kw):
            # 在打开中间真跑一拍巡检，并把时钟拨过 TTL：等价于真机上 _sweeper 那一跳落在 goto 期间
            self.swept_during_open.append(await sessions.sweep_expired(
                now=time.monotonic() + sessions.session_ttl() + 1))
            await super().goto(url, **kw)

    made: list = []

    def factory():
        page = LoadingPage()
        made.append(page)
        return page

    client.fake_browser.page_factory = factory
    body = client.post("/sessions",
                       json={"url": OPEN_URL, "timeout_ms": _sidecar.TIMEOUT_MS_MAX}).json()
    assert body["ok"] is True, body
    assert made[0].swept_during_open == [[]]      # 巡检跳了一拍，而且没动正在打开的会话
    sid = body["session_id"]
    assert sessions.SESSIONS[sid].context.closed is False
    # 标记必须在 open 结束后清掉，否则这个会话永远不进巡检：
    # 占到进程退出比「早杀一步」糟得多，所以两个方向都要钉住。
    assert sessions.SESSIONS[sid].opening is False
    assert await sessions.sweep_expired(
        now=time.monotonic() + sessions.session_ttl() + 1) == [sid]


# ---------------------------------------------------------------------------
# 最终修复波第 1 批：两处不可回收的槽位泄漏（1a / 1b）与「不消费增量账」的三条出口（F-7）。
# ---------------------------------------------------------------------------


async def test_a_wedged_open_is_swept_once_the_open_budget_is_spent():
    """裁定 R-43（发现项 1a）：`opening` 不是豁免，超过一次 open 的最坏预算后照旧回收。

    改前的说法是「打开路径自己带超时与 `drop`，不会靠巡检兜底」——那句只在正常协程里成立：
    内层协程对 `CancelledError` 不敏感时（真卡死的 Playwright 调用）`open_session` 的 `finally`
    永远跑不到，`opening` 恒 True，这个会话就**再也不进巡检**。后果是整功能级 outage：
    `max_sessions` 默认 2，两次 wedge 之后 sidecar 永久回「浏览器服务已满」直到人工重启容器，
    而 `wedge` 发生在后端绑定 sid 之前，`close_browser_session` 连可 DELETE 的对象都没有。
    """
    s = _put("s1", opening=True)
    budget = sessions.session_ttl() + sessions.OPEN_BUDGET_SECONDS
    # 预算之内：一次 open 的最坏情况本来就比 TTL 长，此刻关掉就是把正在等响应的页面杀了，
    # 报出去的原因还会失真成「页面打开失败」（既有那条 open 中途巡检的测试钉的是同一件事）。
    s.last_used = time.monotonic() - (budget - 30)
    assert await sessions.sweep_expired() == []
    assert "s1" in sessions.SESSIONS
    # 预算之外：这就是 wedge，巡检是唯一的兜底。
    s.last_used = time.monotonic() - (budget + 1)
    assert await sessions.sweep_expired() == ["s1"]
    assert s.context.closed is True
    assert sessions.SESSIONS == {}


def test_the_open_budget_is_the_sidecar_timeout_ceiling_plus_the_wait_grace():
    """`OPEN_BUDGET_SECONDS` 与 app 那两个数必须同值：这里是跨模块的第二个字面量，只能靠测试钉。

    漂开的方向很具体：app 那边把 `wait_for` 的宽限调到 30s，这边还按 125s 判——巡检就会在
    一次合法但慢的 open 中间把 context 关掉，症状正是那条被杀掉的会话报「页面打开失败」。
    """
    assert sessions.OPEN_BUDGET_SECONDS == (_sidecar.TIMEOUT_MS_MAX // 1000
                                            + _sidecar.SIDECAR_WAIT_GRACE_SECONDS)


async def test_open_closes_the_context_when_the_page_cannot_be_created(client, monkeypatch):
    """发现项 1b：`new_page` 抛异常时那个 context 既没进注册表也没被关。

    改前三步（new_context → new_page → attach_listeners）没有 try 包着，后两者失败就留下一个
    「不在 `SESSIONS` 里、因此巡检永远看不见」的活 context。默认上限 2、mem_limit 1g，
    攒两次就把整个服务填满，而且同样没有任何自愈路径（后端连 sid 都没有）。
    """
    import browser.app as app_mod

    made: list = []

    class NoPageContext(FakeContext):
        async def new_page(self):
            raise RuntimeError("Failed to create a new page")

    async def fake_new_context(**_kw):
        context = NoPageContext()
        made.append(context)
        return context

    monkeypatch.setattr(app_mod._browser, "new_context", fake_new_context)
    body = client.post("/sessions", json={"url": OPEN_URL}).json()
    assert body["ok"] is False and body["session_gone"] is True
    assert "Failed to create a new page" in body["error"]
    assert made and made[0].closed is True      # 泄漏的那一步：context 被关了
    assert sessions.SESSIONS == {}              # 也确实没留在注册表里


async def test_a_failing_new_context_is_a_readable_result_not_a_500(client, monkeypatch):
    """三步都在 try 里（含 `new_context` 本身）：Chromium 拒绝建 context 也要回带原因的正常结果。"""
    import browser.app as app_mod

    async def boom(**_kw):
        raise RuntimeError("Context creation failed")

    monkeypatch.setattr(app_mod._browser, "new_context", boom)
    r = client.post("/sessions", json={"url": OPEN_URL})
    assert r.status_code == 200
    assert r.json()["ok"] is False and "Context creation failed" in r.json()["error"]
    assert sessions.SESSIONS == {}


def test_an_unaccounted_step_does_not_leak_its_errors_into_the_next(client):
    """发现项 F-7（裁定 R-45）：未知动作那条出口也要消费增量账。

    那三条不经过 `_step_state` 的出口本来就不回状态，但页面上的错误如果不在这里被记掉，
    就会活到下一步、被当成「本步新增」报出来——模型于是把上一步的错记到下一步头上，
    US-04 那个「这个错是不是我这一步搞出来的」的核心信号正好毁在这儿。
    """
    sid = _opened(client)
    s = sessions.SESSIONS[sid]
    # 上一段异步任务在这一步之后才落地：真机上就是 goto 期间排队的 setTimeout 报错。
    s.collectors.console.append({"level": "error", "text": "动画收尾时报的错"})
    body = _step(client, sid, action="nope").json()
    assert body["ok"] is False and "未知动作" in body["error"]
    assert "console_new" not in body            # 这条出口本来就不回增量形状
    nxt = _step(client, sid, action="click", ref="rd2k7qx").json()
    assert nxt["ok"] is True
    assert nxt["console_new"] == []             # 本步的错不许出现在下一步的 *_new 里


def test_a_rejected_navigation_consumes_the_incremental_ledger(client):
    """同一族的第二条出口（navigate 的 path 形状非法）：它扣了步数却不回状态，账照样要记掉。"""
    sid = _opened(client)
    s = sessions.SESSIONS[sid]
    s.collectors.page_errors.append("setTimeout 里炸的")
    s.collectors.failed.append({"method": "GET", "url": "http://other.example/x", "status": 500,
                                "type": "fetch"})
    body = _step(client, sid, action="navigate", path="http://other.example/x").json()
    assert body["ok"] is False and body["steps_charged"] is True
    assert "page_errors_new" not in body
    nxt = _step(client, sid, action="click", ref="rd2k7qx").json()
    assert nxt["page_errors_new"] == [] and nxt["failed_requests_new"] == []


def test_a_gone_session_has_no_ledger_to_consume(client):
    """第三条出口（会话已经没了）无需消费：收集器与三份 seen 集合都跟着那个 Session 一起没了。

    钉住它是为了「三条都处理过」这件事本身可查——将来若有人给这条分支补一份状态形状，
    这里就是他必须一起改的地方。
    """
    sid = _opened(client)
    assert _step(client, "not-a-session", action="click", ref="rd2k7qx").json()[
        "session_gone"] is True
    body = _step(client, sid, action="click", ref="rd2k7qx").json()
    assert body["ok"] is True and body["console_new"] == []
