# tests/test_browser_session_tools.py
"""三个交互工具（规格 docs/specs/2026-11-02-agent-browser-interaction.md）。

sidecar 一律替身：这里验绑定、额度、遮断、收口，真浏览器由 Task 3 的走查与手工矩阵负责。
"""
import base64
import json
import struct
import types

import httpx
import pytest

from app.agent import browser_session_tools as bst


class Ctx:
    """工具上下文测试桩。

    裁定 R-3：**在本文件内联一份**（与 tests/test_browser_tools.py 同形），不跨测试模块 import——
    跨文件 import 会随文件重排而断，而这是测试桩不是生产逻辑，复制一份的维护成本更低。
    """

    def __init__(self, app=None, *, cfg=None, model_cfg=None, llm=None):
        self.app = app
        self.cfg = cfg or {}
        self.model_cfg = model_cfg
        self.llm = llm
        self.session_id = 7
        self.session_factory = None
        self.run_state = {}
        self.sent = []

    async def send(self, ev):
        self.sent.append(ev)


def _app(**extra):
    base = {"id": 1, "slug": "demo", "stack": "vue3-fastapi", "status_dev": "running",
            "dev_port": 8241, "preview_url": "http://localhost:8241"}
    base.update(extra)
    return base


async def _aget(value):
    return value


def _interactive(n=3):
    return [{"ref": f"e{i + 1}", "role": "button", "name": f"按钮{i + 1}",
             "disabled": False, "sensitive": False} for i in range(n)]


def _session_ok(**extra):
    """Task 3 的 `POST /sessions` 200 体形状（session_id 在里面，模型那一侧永远看不到它）。"""
    data = {"ok": True, "session_id": "sid-123", "title": "订单管理",
            "text": "订单列表\n共 3 条", "text_truncated": False,
            "url": "http://host.docker.internal:8241/",
            "interactive": _interactive(), "interactive_truncated": False,
            "console": [{"level": "error", "text": "boom", "truncated": False}],
            "page_errors": [], "failed_requests": [],
            "truncated": False, "timed_out": False, "elapsed_ms": 1200}
    data.update(extra)
    return data


class _Resp:
    def __init__(self, status_code, payload, *, bad_json=False):
        self.status_code = status_code
        self._payload = payload
        self._bad_json = bad_json

    def json(self):
        if self._bad_json:
            raise ValueError("not json")
        return self._payload


class _Client:
    """httpx.AsyncClient 替身：记下调用面，按脚本返回响应或抛异常。"""

    def __init__(self, resp=None, error=None):
        self.resp = resp
        self.error = error
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    async def post(self, url, json=None):
        self.calls.append(("POST", url, json))
        if self.error is not None:
            raise self.error
        return self.resp

    async def delete(self, url):
        self.calls.append(("DELETE", url))
        if self.error is not None:
            raise self.error
        return self.resp


def _fake_httpx(resp=None, error=None):
    """只替 `AsyncClient`；异常类沿用真的 httpx——`_sidecar_*` 是按类型分支捕获的。"""
    client = _Client(resp=resp, error=error)
    holder = types.SimpleNamespace(AsyncClient=lambda **_kw: client,
                                   TimeoutException=httpx.TimeoutException,
                                   HTTPError=httpx.HTTPError)
    return client, holder


# --------------------------------------------------------------------------- 契约面

async def test_session_schema_exposes_no_session_id():
    """session_id 不进模型参数面是 ADR-0037 的硬约束：模型能指到别人的 session 就全完了。"""
    from app.agent.llm import BROWSER_SESSION_TOOLS
    props = {t["function"]["name"]: set(t["function"]["parameters"]["properties"])
             for t in BROWSER_SESSION_TOOLS}
    assert props["browser_open"] == {"path"}
    assert props["browser_close"] == set()
    assert "session_id" not in props["browser_step"]
    assert props["browser_step"] == {"action", "target", "value", "expect", "screenshot"}


async def test_session_tool_names_match_the_schema():
    from app.agent.llm import BROWSER_SESSION_TOOLS, BROWSER_SESSION_TOOL_NAMES
    assert [t["function"]["name"] for t in BROWSER_SESSION_TOOLS] == list(BROWSER_SESSION_TOOL_NAMES)


async def test_the_ordinal_list_is_the_same_object_as_the_dispatch_list():
    """工具名单只有一个来源（复查者判 Important）：位次名单不许是抄出来的第二份字面量。

    现状的三份名单：分派闸用 `tools.py` 的 `BROWSER_SESSION_TOOL_NAMES`（定义在 `llm.py`，与 schema
    同源），handler 里另有三处 `if name == "browser_open" / "browser_step" / "browser_close"`，而
    位次计数器读的是 `browser_session_tools.SESSION_TOOL_NAMES` 这**第二个字面量**。三份今天内容
    相同纯属抄写一致，此前没有任何测试钉它们相等。将来加第四个交互工具时，作者必然改 schema、
    `BROWSER_SESSION_TOOL_NAMES` 与 handler 分支，而漏改这条旧常量**不会红任何测试**——漏的方向
    正好是「少遮」（新工具改写了清单却不进口径，口令明文落库），是危险方向上的一份漂移点。

    断 `is` 而不是 `==`：内容相同的那份独立字面量正是本条要防的形状，`==` 对它恰好是瞎的。
    """
    from app.agent.llm import BROWSER_SESSION_TOOLS, BROWSER_SESSION_TOOL_NAMES
    assert bst.SESSION_TOOL_NAMES is BROWSER_SESSION_TOOL_NAMES
    assert list(bst.SESSION_TOOL_NAMES) == [t["function"]["name"] for t in BROWSER_SESSION_TOOLS]


# --------------------------------------------------------------------------- browser_open

async def test_open_requires_running_preview(monkeypatch):
    async def fake_app(ctx):
        return {"id": 1, "slug": "d", "stack": "vue3-fastapi", "status_dev": "stopped",
                "dev_port": 8241, "preview_url": "http://localhost:8241"}

    async def boom(*_a, **_k):
        raise AssertionError("预览未运行不该打到 sidecar")

    monkeypatch.setattr(bst, "fetch_app", fake_app)
    monkeypatch.setattr(bst, "_sidecar_open", boom)
    out = await bst.run_browser_session_tool(Ctx(), "browser_open", {})
    assert out["ok"] is False
    assert "preview_restart" in out["hint"]


async def test_open_starting_preview_does_not_call_sidecar(monkeypatch):
    """US-15：预览没起来就绝不自己起栈，也不把「starting」说成「未运行」。"""
    async def fake_app(ctx):
        return _app(status_dev="starting")

    async def boom(*_a, **_k):
        raise AssertionError("预览未运行不该打到 sidecar")

    monkeypatch.setattr(bst, "fetch_app", fake_app)
    monkeypatch.setattr(bst, "_sidecar_open", boom)
    out = await bst.run_browser_session_tool(Ctx(), "browser_open", {})
    assert out["ok"] is False and out["status"] == "starting"
    assert "稍后再试" in out["hint"]


async def test_open_gone_app_is_a_normal_result(monkeypatch):
    monkeypatch.setattr(bst, "fetch_app", lambda ctx: _aget(None))
    out = await bst.run_browser_session_tool(Ctx(), "browser_open", {})
    assert out["ok"] is False and "不可用" in out["error"]


async def test_open_bad_path_is_rejected_before_the_sidecar(monkeypatch):
    async def boom(*_a, **_k):
        raise AssertionError("非法 path 不该打到 sidecar")

    monkeypatch.setattr(bst, "fetch_app", lambda ctx: _aget(_app()))
    monkeypatch.setattr(bst, "_sidecar_open", boom)
    out = await bst.run_browser_session_tool(Ctx(), "browser_open", {"path": "orders"})
    assert out["ok"] is False and "同源" in out["error"]


async def test_open_binds_session_to_run_state_only(monkeypatch):
    """绑定只存在 run_state：返回值里既没有 session_id，也没有它的前缀。"""
    ctx = Ctx()
    monkeypatch.setattr(bst, "fetch_app", lambda c: _aget(_app()))
    monkeypatch.setattr(bst, "_sidecar_open", lambda url, **kw: _aget((_session_ok(), "")))
    out = await bst.run_browser_session_tool(ctx, "browser_open", {})
    assert ctx.run_state["browser_session"] == "sid-123"
    assert ctx.run_state["browser_steps_used"] == 0
    assert ctx.run_state["browser_refs"] == _interactive()
    assert ctx.run_state["browser_checks"] == 1        # open 计入 preview_page 那份额度
    assert out["ok"] is True and out["session"] == "opened"
    assert out["steps_left"] == bst.DEFAULT_MAX_STEPS
    assert "sid-123" not in json.dumps(out, ensure_ascii=False)
    assert "session_id" not in json.dumps(out, ensure_ascii=False)


async def test_open_uses_display_url_and_never_the_sidecar_one(monkeypatch):
    """裁定 R-16：sidecar 的 url 是 `_connect_url` 重写后的容器可达地址（host.docker.internal →
    192.168.65.254 一类）。它进模型上下文、进 run_state["browser_log"]、进工具返回值都是拓扑泄漏，
    三处一律用后端自己算的 display_url。
    """
    ctx = Ctx()
    internal = "http://192.168.65.254:8241/orders"
    monkeypatch.setattr(bst, "fetch_app", lambda c: _aget(_app()))

    async def fake_open(url, **_kw):
        return _session_ok(url=internal), ""

    monkeypatch.setattr(bst, "_sidecar_open", fake_open)
    out = await bst.run_browser_session_tool(ctx, "browser_open", {"path": "/orders"})
    assert out["url"] == "http://localhost:8241/orders"
    assert ctx.run_state["browser_log"][0]["url"] == "http://localhost:8241/orders"
    assert internal not in json.dumps(out, ensure_ascii=False)
    assert internal not in json.dumps(ctx.run_state["browser_log"], ensure_ascii=False)


async def test_open_targets_the_dev_port_through_the_sidecar_host(monkeypatch):
    seen = {}

    async def fake_open(url, **kw):
        seen["url"] = url
        seen["timeout_s"] = kw["timeout_s"]
        return _session_ok(), ""

    monkeypatch.setattr(bst, "fetch_app", lambda c: _aget(_app()))
    monkeypatch.setattr(bst, "browser_target_host", lambda: "host.docker.internal")
    monkeypatch.setattr(bst, "_sidecar_open", fake_open)
    await bst.run_browser_session_tool(Ctx(), "browser_open", {"path": "/orders?page=2"})
    assert seen["url"] == "http://host.docker.internal:8241/orders?page=2"
    assert seen["timeout_s"] == 45


async def test_open_quota_exhausted_does_not_call_sidecar(monkeypatch):
    ctx = Ctx()
    ctx.run_state["browser_checks"] = 6

    async def boom(*_a, **_k):
        raise AssertionError("额度用尽不该打到 sidecar")

    monkeypatch.setattr(bst, "fetch_app", lambda c: _aget(_app()))
    monkeypatch.setattr(bst, "_sidecar_open", boom)
    out = await bst.run_browser_session_tool(ctx, "browser_open", {})
    assert out["ok"] is False
    assert "上限" in out["error"]
    assert "browser_close" in out["error"]


async def test_open_quota_zero_means_unlimited(monkeypatch):
    """`agent.browser_max_per_run <= 0` 不限（与超时参数同口径）：已用 99 次也照样能开。"""
    ctx = Ctx(cfg={"agent.browser_max_per_run": 0})
    ctx.run_state["browser_checks"] = 99
    monkeypatch.setattr(bst, "fetch_app", lambda c: _aget(_app()))
    monkeypatch.setattr(bst, "_sidecar_open", lambda url, **kw: _aget((_session_ok(), "")))
    out = await bst.run_browser_session_tool(ctx, "browser_open", {})
    assert out["ok"] is True
    assert out["checks_left"] == -1


async def test_open_business_failure_keeps_the_reason(monkeypatch):
    """200 体里的 ok:false 是业务失败：原因、session_gone 与「先确认预览」都要留着。"""
    async def fake_open(url, **_kw):
        return {"ok": False, "error": "页面打开失败: net::ERR_CONNECTION_REFUSED",
                "session_gone": True}, ""

    monkeypatch.setattr(bst, "fetch_app", lambda c: _aget(_app()))
    monkeypatch.setattr(bst, "_sidecar_open", fake_open)
    out = await bst.run_browser_session_tool(Ctx(), "browser_open", {"path": "/orders"})
    assert out["ok"] is False and out["session_gone"] is True
    assert "ERR_CONNECTION_REFUSED" in out["error"]
    assert "preview_status" in out["hint"]


async def test_open_does_not_bind_state_when_sidecar_failed(monkeypatch):
    """失败不记账：额度与 session 都不该被一次没开成的 open 消耗或写脏。"""
    ctx = Ctx()

    async def fake_open(url, **_kw):
        return None, "浏览器服务已满"

    monkeypatch.setattr(bst, "fetch_app", lambda c: _aget(_app()))
    monkeypatch.setattr(bst, "_sidecar_open", fake_open)
    out = await bst.run_browser_session_tool(ctx, "browser_open", {})
    assert out["ok"] is False and "已满" in out["error"]
    assert ctx.run_state.get("browser_session") is None
    assert "browser_checks" not in ctx.run_state


# ------------------------------------------- 重复 open / 步数账 / 收口口径（I-1、I-2、R-24、R-25）

async def test_reopen_auto_closes_the_previous_session(monkeypatch):
    """规格 §7「一个 run 一个」：重复 open 必须先 DELETE 前一个会话。

    直接覆写绑定的话，前一个 context 永远不会被关——sidecar 的
    `ZEROCODE_BROWSER_MAX_SESSIONS` 默认 2，同一 run 内第三次 open 就撞 409，还得等 120s TTL
    才恢复（ADR-0037 后果第 2 条的僵尸 context，正是本功能要防的症状）。
    """
    ctx = Ctx()
    deleted = []
    monkeypatch.setattr(bst, "fetch_app", lambda c: _aget(_app()))
    monkeypatch.setattr(bst, "_sidecar_delete", lambda sid: _aget(deleted.append(sid) or True))
    monkeypatch.setattr(bst, "_sidecar_open", lambda url, **kw: _aget((_session_ok(), "")))
    first = await bst.run_browser_session_tool(ctx, "browser_open", {})
    assert first["ok"] is True and deleted == []          # 首次 open 不该有多余的 DELETE

    async def second(url, **_kw):
        return _session_ok(session_id="sid-456"), ""

    monkeypatch.setattr(bst, "_sidecar_open", second)
    out = await bst.run_browser_session_tool(ctx, "browser_open", {})
    assert deleted == ["sid-123"]                         # 前一个真的被 DELETE 了
    assert ctx.run_state["browser_session"] == "sid-456"  # 一个 run 只留一个活会话
    assert out["ok"] is True and out["auto_closed_previous"] is True
    assert "自动关闭" in out["hint"]                      # 不许静默换掉（步数账与 log 都是那个 run 的）
    blob = json.dumps(out, ensure_ascii=False)
    assert "sid-123" not in blob and "sid-456" not in blob
    assert "session_id" not in blob


async def test_quota_exhausted_keeps_the_old_session_alive(monkeypatch):
    """裁定 R-24：额度判定排在自动关旧**之前**——一次被拒绝的调用不许留下副作用。

    同 `app/deploy/engine.py::start_dev` 的「预检失败 = 状态零变更」。反过来（先关再拒）模型看到的是
    「我没做成任何事，但我手上的会话没了」：下一步只能重开，而重开又要再花一次额度，形成它自己
    解释不了的死循环。旧会话因此必须仍然活着、仍绑在 run 上，可继续 step 也可 browser_close。
    """
    ctx = Ctx()
    ctx.run_state["browser_checks"] = 6
    ctx.run_state["browser_session"] = "sid-old"
    ctx.run_state["browser_log"] = [{"seq": 0, "action": "open", "target": "/", "passed": None}]
    calls = []

    async def boom(*_a, **_k):
        raise AssertionError("额度用尽不该打到 sidecar")

    monkeypatch.setattr(bst, "fetch_app", lambda c: _aget(_app()))
    monkeypatch.setattr(bst, "_sidecar_delete", lambda sid: _aget(calls.append(sid) or True))
    monkeypatch.setattr(bst, "_sidecar_open", boom)
    out = await bst.run_browser_session_tool(ctx, "browser_open", {})
    assert calls == []                                    # 被拒绝的 open 一个 DELETE 都不发
    assert ctx.run_state["browser_session"] == "sid-old"  # 旧会话原样留着
    assert ctx.run_state["browser_log"] == [{"seq": 0, "action": "open", "target": "/",
                                             "passed": None}]
    assert ctx.run_state.get("browser_sessions_replaced") in (None, 0)
    assert out["ok"] is False and "上限" in out["error"]
    assert out.get("auto_closed_previous") is not True    # 这句话在该分支下不成立
    assert "browser_close" in out["error"]                # 文案指向一个确实还在的会话
    assert "sid-old" not in json.dumps(out, ensure_ascii=False)


async def test_rejected_open_never_closes_the_old_session(monkeypatch):
    """同一条不变式的第二个拒绝出口：path 不合法也一样不能先把旧会话关掉再报错。"""
    ctx = Ctx()
    ctx.run_state["browser_session"] = "sid-old"
    calls = []
    monkeypatch.setattr(bst, "fetch_app", lambda c: _aget(_app()))
    monkeypatch.setattr(bst, "_sidecar_delete", lambda sid: _aget(calls.append(sid) or True))
    out = await bst.run_browser_session_tool(ctx, "browser_open", {"path": "orders"})
    assert out["ok"] is False and "同源" in out["error"]
    assert calls == []
    assert ctx.run_state["browser_session"] == "sid-old"


async def test_steps_left_is_per_run_not_per_session(monkeypatch):
    """步数账 per-run（规格 §10「单 run 步数上限」、US-13 的「本 run 步数已用完」）。

    open 里的归零会把它变成 per-session：Task 5 照这份口径 charge，于是
    「open→12 步→close→open→12 步」在一个 run 里合法，模型上下文的成本上限被悄悄翻倍。
    """
    ctx = Ctx()
    deleted = []
    monkeypatch.setattr(bst, "fetch_app", lambda c: _aget(_app()))
    monkeypatch.setattr(bst, "_sidecar_delete", lambda sid: _aget(deleted.append(sid) or True))
    monkeypatch.setattr(bst, "_sidecar_open", lambda url, **kw: _aget((_session_ok(), "")))
    first = await bst.run_browser_session_tool(ctx, "browser_open", {})
    assert first["steps_left"] == 12                      # 缺省仍要初始化成 0 已用
    ctx.run_state["browser_steps_used"] = 12              # 模拟 Task 5 在本 run 里记满 12 步
    await bst.run_browser_session_tool(ctx, "browser_close", {})
    out = await bst.run_browser_session_tool(ctx, "browser_open", {})
    assert out["ok"] is True
    assert ctx.run_state["browser_steps_used"] == 12      # 再 open 不许回满
    assert out["steps_left"] == 0


async def test_close_report_says_how_many_sessions_it_covers(monkeypatch):
    """裁定 R-25：重复 open 依旧重置 `browser_log`（一个 run 一个会话，跨会话合并会让
    `steps_total` 与 per-run 步数账对不上），但收口报告必须**承认**自己只覆盖最后一个会话。
    """
    monkeypatch.setattr(bst, "fetch_app", lambda c: _aget(_app()))
    monkeypatch.setattr(bst, "_sidecar_open", lambda url, **kw: _aget((_session_ok(), "")))
    deleted: list[str] = []
    monkeypatch.setattr(bst, "_sidecar_delete", lambda sid: _aget(deleted.append(sid) or True))

    clean = Ctx()                                        # 单会话的 run：计数为 0，hint 不多话
    await bst.run_browser_session_tool(clean, "browser_open", {})
    out = await bst.run_browser_session_tool(clean, "browser_close", {})
    assert out["sessions_replaced"] == 0
    assert "共开过" not in out["hint"]

    ctx = Ctx()                                          # 同一 run 里连开三个会话
    for _ in range(3):
        got = await bst.run_browser_session_tool(ctx, "browser_open", {})
        assert got["ok"] is True
    assert ctx.run_state["browser_sessions_replaced"] == 2

    out = await bst.run_browser_session_tool(ctx, "browser_close", {})
    assert out["sessions_replaced"] == 2
    assert out["steps_total"] == 1                        # report 仍只是最后一个会话，不合并历史
    assert "本 run 共开过 3 个浏览器会话" in out["hint"]
    assert "只覆盖最后一个" in out["hint"]
    assert any(s.get("replaced_sessions") == 2 for s in out["report"])   # 事实也写进 report 本身
    blob = json.dumps(out, ensure_ascii=False)
    assert "sid-" not in blob and "session_id" not in blob               # 计数不带 session_id


async def test_failed_reopen_says_the_old_session_is_already_gone(monkeypatch):
    """遗留 #3：额度尚在、但新 open 打到 sidecar 失败时，旧会话已经被 DELETE 了。

    这条出口不回报「旧的已关」，模型会以为手上那个会话还活着，下一步白跑一次 step；
    而替换计数记在一笔没建成的替换上，收口报告的数字就虚高。
    """
    ctx = Ctx()
    ctx.run_state["browser_session"] = "sid-old"
    deleted = []
    monkeypatch.setattr(bst, "fetch_app", lambda c: _aget(_app()))
    monkeypatch.setattr(bst, "_sidecar_delete", lambda sid: _aget(deleted.append(sid) or True))
    monkeypatch.setattr(bst, "_sidecar_open", lambda url, **kw: _aget((None, "浏览器服务已满")))
    out = await bst.run_browser_session_tool(ctx, "browser_open", {})
    assert out["ok"] is False and deleted == ["sid-old"]
    assert ctx.run_state["browser_session"] is None            # 绑定确实跟着 DELETE 解掉了
    assert "browser_close" not in out["hint"]
    assert "上一个浏览器会话已关闭" in out["hint"]               # 旧会话不再可用，要说出口
    assert "现在没有可用会话" in out["hint"]
    assert ctx.run_state.get("browser_sessions_replaced") in (None, 0)   # 计数不虚高
    assert ctx.run_state.get("browser_opens") in (None, 0)

    # 业务失败（导航失败）那条出口同样要交代，否则症状一模一样。
    ctx2 = Ctx()
    ctx2.run_state["browser_session"] = "sid-old"
    monkeypatch.setattr(bst, "_sidecar_open", lambda url, **kw: _aget(
        ({"ok": False, "error": "页面打开失败: net::ERR_CONNECTION_REFUSED",
          "session_gone": True}, "")))
    got = await bst.run_browser_session_tool(ctx2, "browser_open", {"path": "/orders"})
    assert got["ok"] is False and got["session_gone"] is True
    assert "上一个浏览器会话已关闭" in got["hint"]
    assert ctx2.run_state.get("browser_sessions_replaced") in (None, 0)


async def test_close_counts_every_session_this_run_actually_opened(monkeypatch):
    """遗留 #1：`replaced + 1` 数的是「没被 close 打断的替换链」，不是本 run 开过的会话数。

    open→close→open→open→close 这条合法路径上，`replaced` 只有 1，写「共开过 2 个」是给模型
    的一句假话——收口报告是模型唯一一次向用户交代全过程的地方，它必须数得准。
    """
    monkeypatch.setattr(bst, "fetch_app", lambda c: _aget(_app()))
    monkeypatch.setattr(bst, "_sidecar_open", lambda url, **kw: _aget((_session_ok(), "")))
    monkeypatch.setattr(bst, "_sidecar_delete", lambda sid: _aget(True))
    ctx = Ctx()
    await bst.run_browser_session_tool(ctx, "browser_open", {})        # 第 1 个
    await bst.run_browser_session_tool(ctx, "browser_close", {})        # 正常收口，不算替换
    await bst.run_browser_session_tool(ctx, "browser_open", {})         # 第 2 个
    await bst.run_browser_session_tool(ctx, "browser_open", {})         # 第 3 个（替换掉第 2 个）
    assert ctx.run_state["browser_sessions_replaced"] == 1
    out = await bst.run_browser_session_tool(ctx, "browser_close", {})
    assert out["sessions_replaced"] == 1
    assert "本 run 共开过 3 个浏览器会话" in out["hint"]
    assert ctx.run_state["browser_opens"] == 3


# ------------------------------------------------------------------ interactive_max 接线（R-23）

async def test_interactive_max_config_reaches_the_sidecar(monkeypatch):
    """规格 §10 的 `agent.browser_interactive_max`（默认 40）不是装饰：改了要传到 sidecar。"""
    seen = {}

    async def fake_open(url, **kw):
        seen["interactive_max"] = kw.get("interactive_max")
        return _session_ok(), ""

    monkeypatch.setattr(bst, "fetch_app", lambda c: _aget(_app()))
    monkeypatch.setattr(bst, "_sidecar_open", fake_open)
    await bst.run_browser_session_tool(Ctx(cfg={"agent.browser_interactive_max": 12}),
                                       "browser_open", {})
    assert seen["interactive_max"] == 12

    seen.clear()
    await bst.run_browser_session_tool(Ctx(), "browser_open", {})      # 缺省回落到规格默认
    assert seen["interactive_max"] == bst.DEFAULT_INTERACTIVE_MAX == 40


async def test_sidecar_open_payload_carries_interactive_max(monkeypatch):
    """钉在真正的线协议上：载荷里少了这个字段，配置就成了无人读的死键（OpenRequest 支持它）。"""
    client, holder = _fake_httpx(resp=_Resp(200, _session_ok()))
    monkeypatch.setattr(bst, "httpx", holder)
    await bst._sidecar_open("http://h:1/", timeout_s=45, expect=None, interactive_max=12)
    assert client.calls[0][2]["interactive_max"] == 12


# --------------------------------------------------------------------------- 双形状契约

@pytest.mark.parametrize("status,expected", [
    (400, "目标地址不在允许清单内"),
    (409, "浏览器服务已满：活跃会话数已达上限"),
    (503, "浏览器尚未就绪"),
])
async def test_rejections_read_the_detail_string(monkeypatch, status, expected):
    """入口拒绝走 HTTPException：体是 `{"detail": <str>}`，400/409/503 三种同一个形状。"""
    _c, holder = _fake_httpx(resp=_Resp(status, {"detail": expected}))
    monkeypatch.setattr(bst, "httpx", holder)
    data, err = await bst._sidecar_open("http://h:1/", timeout_s=45, expect=None)
    assert data is None
    assert err == expected


async def test_reject_detail_list_is_not_repr_dumped(monkeypatch):
    """body 畸形时 FastAPI 自动回 422，`detail` 是 **list** 不是 str：`str(...)` 会得到一句
    Python repr 给模型看（Task 3 把这条写进了 browser/app.py 的注释）。"""
    _c, holder = _fake_httpx(resp=_Resp(422, {"detail": [
        {"loc": ["body", "url"], "msg": "Field required", "type": "missing"},
        {"loc": ["body", "timeout_ms"], "msg": "Input should be a valid integer",
         "type": "int_type"}]}))
    monkeypatch.setattr(bst, "httpx", holder)
    data, err = await bst._sidecar_open("http://h:1/", timeout_s=45, expect=None)
    assert data is None
    assert "url" in err and "Field required" in err
    assert "loc" not in err and "{" not in err and "missing" not in err
    assert not err.startswith("[")


async def test_unparsable_body_is_http_scoped(monkeypatch):
    _c, holder = _fake_httpx(resp=_Resp(500, None, bad_json=True))
    monkeypatch.setattr(bst, "httpx", holder)
    data, err = await bst._sidecar_open("http://h:1/", timeout_s=45, expect=None)
    assert data is None and "HTTP 500" in err


async def test_open_timeout_is_actionable(monkeypatch):
    _c, holder = _fake_httpx(error=httpx.ReadTimeout("slow"))
    monkeypatch.setattr(bst, "httpx", holder)
    data, err = await bst._sidecar_open("http://h:1/", timeout_s=45, expect=None)
    assert data is None and "超时" in err


async def test_open_transport_error_is_actionable(monkeypatch):
    _c, holder = _fake_httpx(error=httpx.ConnectError("connection refused"))
    monkeypatch.setattr(bst, "httpx", holder)
    data, err = await bst._sidecar_open("http://h:1/", timeout_s=45, expect=None)
    assert data is None and err == "浏览器服务不可用"


async def test_business_failure_stays_in_the_200_body(monkeypatch):
    _c, holder = _fake_httpx(resp=_Resp(200, {"ok": False, "error": "页面打开超时",
                                              "session_gone": True}))
    monkeypatch.setattr(bst, "httpx", holder)
    data, err = await bst._sidecar_open("http://h:1/", timeout_s=45, expect=None)
    assert err == "" and data["ok"] is False and data["session_gone"] is True


# --------------------------------------------------------------------------- 遮断与裁剪

async def test_page_text_and_ref_names_are_redacted(monkeypatch):
    """页面内容进模型上下文前的唯一闸口：漏一项等于开了第二条路。"""
    async def fake_open(url, **_kw):
        return _session_ok(
            text="订单列表 password=hunter2 共 3 条",
            title="api_key=sk-abcdefgh12345 管理页",
            interactive=[{"ref": "e1", "role": "textbox",
                          "name": "token=abc123def456ghi 输入框", "sensitive": True}],
            console=[{"level": "error", "text": "保存失败 password=hunter2"}],
            failed_requests=[{"method": "POST", "url": "http://h/api?pwd=topsecret999",
                              "status": 500, "type": "fetch", "blocked": False}]), ""

    monkeypatch.setattr(bst, "fetch_app", lambda c: _aget(_app()))
    monkeypatch.setattr(bst, "_sidecar_open", fake_open)
    out = await bst.run_browser_session_tool(Ctx(), "browser_open", {})
    blob = json.dumps(out, ensure_ascii=False)
    for secret in ("hunter2", "sk-abcdefgh12345", "abc123def456ghi", "topsecret999"):
        assert secret not in blob
    assert out["redacted"] >= 4


async def test_dialog_message_is_redacted(monkeypatch):
    """原生弹窗的文案也是页面原话：整块形状要给模型（US-11 靠它判断「删除没发生」），但正文过遮断。"""
    async def fake_open(url, **_kw):
        return _session_ok(dialog={"type": "confirm",
                                    "message": "确定删除 password=hunter2x 吗？",
                                    "handled": "dismissed"}), ""

    monkeypatch.setattr(bst, "fetch_app", lambda c: _aget(_app()))
    monkeypatch.setattr(bst, "_sidecar_open", fake_open)
    out = await bst.run_browser_session_tool(Ctx(), "browser_open", {})
    assert out["dialog"]["type"] == "confirm" and out["dialog"]["handled"] == "dismissed"
    assert "hunter2x" not in json.dumps(out["dialog"], ensure_ascii=False)


# ---------------------------------------------------------------- 内部 host 清洗（R-22 / I-3）

def test_scrub_known_host_only_rewrites_the_matching_port():
    out = bst.scrub_known_host(
        "a http://192.168.65.254:8241/orders 与 http://192.168.65.254:9999/x，"
        "还有 http://host.docker.internal:8241/y",
        target_netloc="host.docker.internal:8241", display_netloc="localhost:8241")
    assert "localhost:8241/orders" in out and "localhost:8241/y" in out
    assert "192.168.65.254:8241" not in out
    assert "192.168.65.254:9999" in out          # 别的端口不是本应用的 dev 端口，不动它
    assert bst.scrub_known_host(None, target_netloc="h:1", display_netloc="d:1") == ""


async def test_business_error_does_not_leak_the_container_ip(monkeypatch):
    """sidecar 的 error 原话内嵌 `_connect_url` 重写后的地址（`browser/app.py` 里那句
    `f"页面打开失败: {_short(e)}"`：Playwright 的导航异常自带被重写后的 URL）。
    `redact_sensitive` 只认凭据形态、没有 host/IP 规则——这是 R-16 之外的第二个拓扑出口，
    而导航失败恰是走查里最常见的失败。
    """
    async def fake_open(url, **_kw):
        return {"ok": False, "session_gone": True,
                "error": ("Page.goto: net::ERR_CONNECTION_REFUSED at "
                          "http://192.168.65.254:8241/orders")}, ""

    monkeypatch.setattr(bst, "fetch_app", lambda c: _aget(_app()))
    monkeypatch.setattr(bst, "browser_target_host", lambda: "host.docker.internal")
    monkeypatch.setattr(bst, "_sidecar_open", fake_open)
    out = await bst.run_browser_session_tool(Ctx(), "browser_open", {"path": "/orders"})
    blob = json.dumps(out, ensure_ascii=False)
    assert "192.168.65.254" not in blob
    assert "localhost:8241" in out["error"]            # 换回用户看得见的那个地址
    assert "ERR_CONNECTION_REFUSED" in out["error"]    # 清洗只动地址，原因本身不能被洗掉


async def test_rejection_reason_swaps_the_sidecar_host_back(monkeypatch):
    """传输/拒绝分支同样是 sidecar 自由文本：`validate_target` 的 detail 会回显我们发出去的 host。"""
    async def fake_open(url, **_kw):
        return None, "host 不在允许清单: host.docker.internal:8241"

    monkeypatch.setattr(bst, "fetch_app", lambda c: _aget(_app()))
    monkeypatch.setattr(bst, "browser_target_host", lambda: "host.docker.internal")
    monkeypatch.setattr(bst, "_sidecar_open", fake_open)
    out = await bst.run_browser_session_tool(Ctx(), "browser_open", {"path": "/orders"})
    assert out["ok"] is False
    assert "host.docker.internal" not in json.dumps(out, ensure_ascii=False)
    assert "localhost:8241" in out["error"]


async def test_assertion_note_is_scrubbed_like_the_error(monkeypatch):
    """note 与 error 同源（都是 sidecar 写的自由文本），走同一把闸，不然只堵了一半。"""
    async def fake_open(url, **_kw):
        return _session_ok(passed=False,
                           note="URL 不匹配：实际停在 http://192.168.65.254:8241/orders"), ""

    monkeypatch.setattr(bst, "fetch_app", lambda c: _aget(_app()))
    monkeypatch.setattr(bst, "browser_target_host", lambda: "host.docker.internal")
    monkeypatch.setattr(bst, "_sidecar_open", fake_open)
    out = await bst.run_browser_session_tool(Ctx(), "browser_open", {"path": "/orders"})
    assert out["passed"] is False
    assert "192.168.65.254" not in json.dumps(out, ensure_ascii=False)
    assert "localhost:8241/orders" in out["note"]


async def test_scrub_runs_before_the_clip_so_a_long_error_cannot_leak_half_a_host(monkeypatch):
    """裁剪会把端口号从中间切断（留下 `...65.254:8`），清洗若排在 `_redact` 之后就换不掉这半截。
    把内部地址正好摆在 300 字裁剪边界上，才能钉住「先换 host、再裁剪」这个顺序。
    """
    # 24 == len("http://192.168.65.254:")：边界因此落在完整 IP 之后、端口号中间。
    long_error = "x" * (bst.ERROR_MAX_CHARS - 24) + "http://192.168.65.254:8241/orders"

    async def fake_open(url, **_kw):
        return {"ok": False, "error": long_error}, ""

    monkeypatch.setattr(bst, "fetch_app", lambda c: _aget(_app()))
    monkeypatch.setattr(bst, "browser_target_host", lambda: "host.docker.internal")
    monkeypatch.setattr(bst, "_sidecar_open", fake_open)
    out = await bst.run_browser_session_tool(Ctx(), "browser_open", {})
    assert "192.168.65.254" not in out["error"]
    assert "localhost:8241" in out["error"]


async def test_interactive_list_capped_at_40(monkeypatch):
    async def fake_open(url, **_kw):
        return _session_ok(interactive=_interactive(60), interactive_truncated=True), ""

    monkeypatch.setattr(bst, "fetch_app", lambda c: _aget(_app()))
    monkeypatch.setattr(bst, "_sidecar_open", fake_open)
    out = await bst.run_browser_session_tool(Ctx(), "browser_open", {})
    assert bst.DEFAULT_INTERACTIVE_MAX == 40
    assert len(out["interactive"]) == 40
    assert out["interactive_truncated"] is True


async def test_refs_are_stored_unredacted_for_the_sensitive_check(monkeypatch):
    """sensitive 遮断的判定源是 run_state["browser_refs"]（Task 5 的 fill 要用它查元素）。
    那里存的是 sidecar 的原始清单，不是给模型看的裁剪版——遮断版少了 type/label 就判不出来。
    """
    ctx = Ctx()
    refs = [{"ref": "e1", "role": "textbox", "name": "密码", "sensitive": True}]

    async def fake_open(url, **_kw):
        return _session_ok(interactive=refs), ""

    monkeypatch.setattr(bst, "fetch_app", lambda c: _aget(_app()))
    monkeypatch.setattr(bst, "_sidecar_open", fake_open)
    out = await bst.run_browser_session_tool(ctx, "browser_open", {})
    assert ctx.run_state["browser_refs"] == refs
    assert out["interactive"][0]["name"] == "密码"


async def test_passed_assertion_does_not_carry_a_failure_note(monkeypatch):
    """`session_core.judge_expect` 通过时也返回一句失败措辞（Task 1 记为携带项）：
    少了「note 只在 passed 为假时给」这一句三元判断，模型会收到 passed:true + 一句不匹配说明。"""
    monkeypatch.setattr(bst, "fetch_app", lambda c: _aget(_app()))
    note = "页面文本未包含「订单已创建」"

    async def fake_pass(url, **_kw):
        return _session_ok(passed=True, note=note), ""

    monkeypatch.setattr(bst, "_sidecar_open", fake_pass)
    out = await bst.run_browser_session_tool(Ctx(), "browser_open", {})
    assert out["passed"] is True and out["note"] == ""

    async def fake_fail(url, **_kw):
        return _session_ok(passed=False, note=note), ""

    monkeypatch.setattr(bst, "_sidecar_open", fake_fail)
    out = await bst.run_browser_session_tool(Ctx(), "browser_open", {})
    assert out["passed"] is False and out["note"] == note

    async def fake_none(url, **_kw):
        return _session_ok(), ""

    monkeypatch.setattr(bst, "_sidecar_open", fake_none)
    out = await bst.run_browser_session_tool(Ctx(), "browser_open", {})
    assert "passed" not in out and "note" not in out       # 没给 expect 时字段缺席，模型自判


def test_steps_left_semantics():
    """`<=0 不限` 要回 -1（模型据此知道没有额度约束），用尽回 0，正常回余额。"""
    assert bst._steps_left(Ctx(cfg={"agent.browser_max_steps_per_run": 0})) == -1
    assert bst._steps_left(Ctx(cfg={"agent.browser_max_steps_per_run": -1})) == -1
    ctx = Ctx(cfg={"agent.browser_max_steps_per_run": 12})
    ctx.run_state["browser_steps_used"] = 5
    assert bst._steps_left(ctx) == 7
    ctx.run_state["browser_steps_used"] = 99
    assert bst._steps_left(ctx) == 0


def test_limits_are_the_ones_the_spec_pinned():
    """上限值不许漂：漂了会在配置里静默生效，很难从症状回溯到代码。"""
    assert bst.DEFAULT_MAX_STEPS == 12
    assert bst.DEFAULT_INTERACTIVE_MAX == 40
    assert bst.DEFAULT_STEP_TIMEOUT_MS == 3000
    assert bst.TEXT_MAX_CHARS == 8000
    assert bst.PAGE_ERRORS_MAX == 10           # 裁定 R-27 暴露给模型的那份增量账
    assert (bst.LIST_ITEM_MAX, bst.LIST_ITEM_CHARS) == (40, 1000)
    assert bst.INTERACTIVE_CFG_KEY == "agent.browser_interactive_max"     # 规格 §10 的键名逐字
    assert (bst.INTERACTIVE_MAX_MIN, bst.INTERACTIVE_MAX_MAX) == (1, 200)


# --------------------------------------------------------------------------- browser_step

def _step_data(**extra):
    """Task 3 的 `POST /sessions/{sid}/step` 200 体形状（`_step_state` 的产物）。

    console / failed_requests / page_errors 在 step 响应里都已经被 sidecar 换成 `*_new` 三键
    （全量那份留在同一份快照里会被 `_step_state` 摘掉），这份替身保留那个差异：把两边写成
    同形会放过「重播旧错误」这个 bug（US-04，page_errors 那一半是裁定 R-27 补的）。
    """
    data = {"ok": True, "title": "订单管理", "text": "订单列表\n共 3 条",
            "text_truncated": False, "url": "http://192.168.65.254:8241/orders",
            "interactive": _interactive(), "interactive_truncated": False,
            "console_new": [], "failed_requests_new": [], "page_errors_new": [],
            "truncated": False, "timed_out": False, "dialog": None,
            "steps_charged": True, "elapsed_ms": 900}
    data.update(extra)
    return data


def _step_env(monkeypatch, replies):
    """装好 `fetch_app` + `_sidecar_step` 替身；`replies` 是被调用时返回的 200 体。"""
    seen = []

    async def fake_step(sid, payload):
        seen.append((sid, payload))
        return replies, ""

    monkeypatch.setattr(bst, "fetch_app", lambda c: _aget(_app()))
    monkeypatch.setattr(bst, "_sidecar_step", fake_step)
    return seen


async def test_fill_on_sensitive_ref_redacts_value_before_storing(monkeypatch):
    """模型填的口令会进聊天历史。指向 sensitive 元素的 value 必须在落库前遮掉。"""
    ctx = Ctx()
    ctx.run_state["browser_session"] = "abc"
    ctx.run_state["browser_refs"] = [{"ref": "e2", "role": "textbox", "name": "密码",
                                      "sensitive": True}]
    monkeypatch.setattr(bst, "fetch_app", lambda c: _aget(_app()))
    monkeypatch.setattr(bst, "_sidecar_step",
                        lambda sid, payload: _aget((_step_data(passed=True, interactive=[]), "")))
    out = await bst.run_browser_session_tool(
        ctx, "browser_step", {"action": "fill", "target": "e2", "value": "hunter2"})
    assert out["ok"] is True
    stored = ctx.run_state["browser_log"][-1]
    assert "hunter2" not in str(stored)
    assert stored["value_redacted"] is True


async def test_fill_on_normal_ref_keeps_value_visible_in_log(monkeypatch):
    ctx = Ctx()
    ctx.run_state["browser_session"] = "abc"
    ctx.run_state["browser_refs"] = [{"ref": "e3", "role": "textbox", "name": "备注",
                                     "sensitive": False}]
    monkeypatch.setattr(bst, "fetch_app", lambda c: _aget(_app()))
    monkeypatch.setattr(bst, "_sidecar_step",
                        lambda sid, payload: _aget((_step_data(interactive=[]), "")))
    await bst.run_browser_session_tool(
        ctx, "browser_step", {"action": "fill", "target": "e3", "value": "第一单"})
    assert ctx.run_state["browser_log"][-1]["value"] == "第一单"


async def test_sensitive_value_still_reaches_the_sidecar_verbatim(monkeypatch):
    """遮断只针对落库那份：sidecar 拿到的必须是原值，否则 US-08 的登录根本填不进去。"""
    ctx = Ctx()
    ctx.run_state["browser_session"] = "abc"
    ctx.run_state["browser_refs"] = [{"ref": "e2", "role": "textbox", "name": "密码",
                                      "sensitive": True}]
    seen = _step_env(monkeypatch, _step_data())
    await bst.run_browser_session_tool(
        ctx, "browser_step", {"action": "fill", "target": "e2", "value": "hunter2"})
    assert seen[0][1]["value"] == "hunter2"
    assert seen[0][0] == "abc"                       # 会话由后端绑定，模型参数面里没有它


def _tool_call(name, **args):
    """OpenAI 协议形状的一条 tool_call（参数侧遮断的靶子）。"""
    return {"id": "c1", "type": "function",
            "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}


async def test_sensitive_step_args_masks_only_the_sensitive_target():
    """参数侧遮断（裁定 R-33，US-19）：通用规则表按凭据**形态**匹配，`hunter2` 不成形态。

    loop 在落库/推送前调这一刀，钉四种情形：sensitive 目标遮、非 sensitive 目标照旧明文
    （一刀切会把「这一步填了什么备注」一起毁掉）、别的工具不碰、已过一遍不重复计数。
    「非 sensitive 照旧明文」那一条单独用**一条调用**去钉：同轮第 2 条起另有位次从严规则
    （裁定 R-35），混在一份多调用列表里就分不清遮是遮在哪条规则上了。
    """
    refs = [{"ref": "e2", "role": "textbox", "name": "密码", "sensitive": True},
            {"ref": "e3", "role": "textbox", "name": "备注", "sensitive": False}]
    calls = [_tool_call("browser_step", action="fill", target="e2", value="hunter2"),
             _tool_call("browser_step", action="click", target="e2"),
             _tool_call("fs_write", path="a.txt", content="hunter2")]
    out, hits = bst.redact_sensitive_step_args(calls, refs)
    assert hits == 1
    masked = json.loads(out[0]["function"]["arguments"])
    assert masked["value"] == "<redacted:sensitive_input>"
    # 只改 value 这一个值：动作、ref、协议结构（id/type/name）一个字不动
    assert masked["action"] == "fill" and masked["target"] == "e2"
    assert out[0]["id"] == "c1" and out[0]["function"]["name"] == "browser_step"
    assert out[1] is calls[1] and out[2] is calls[2]     # 没 value 与别的工具：原条目原样返回
    again, hits2 = bst.redact_sensitive_step_args(out, refs)
    assert (hits2, json.loads(again[0]["function"]["arguments"])["value"]) == (
        0, "<redacted:sensitive_input>")                # 幂等：不套娃也不重复计数
    # 已知非 sensitive 的目标在位次 1 照旧明文（否则正常走查全被遮成占位符）
    plain, plain_hits = bst.redact_sensitive_step_args(
        [_tool_call("browser_step", action="fill", target="e3", value="第一单")], refs)
    assert plain_hits == 0 and json.loads(plain[0]["function"]["arguments"])["value"] == "第一单"


async def test_the_two_value_masks_share_one_sensitive_verdict(monkeypatch):
    """「一处遮一处不遮就是漏」的钉法（裁定 R-33）：结果侧与参数侧必须过同一个判定。

    把判定本身换成探针，钉三件事：两处都调用它（不是各自内联一份 `any(...)`）、对同一个 ref
    给出同一个结论、非 sensitive 的那一半两处都照旧明文（一刀切全遮也算不一致）。
    """
    refs = [{"ref": "e2", "role": "textbox", "name": "密码", "sensitive": True},
            {"ref": "e3", "role": "textbox", "name": "备注", "sensitive": False}]
    checked = []
    real = bst.is_sensitive_ref

    def spy(refs_, ref):
        verdict = real(refs_, ref)
        checked.append((ref, verdict))
        return verdict

    monkeypatch.setattr(bst, "is_sensitive_ref", spy)
    monkeypatch.setattr(bst, "fetch_app", lambda c: _aget(_app()))
    monkeypatch.setattr(bst, "_sidecar_step",
                        lambda sid, payload: _aget((_step_data(interactive=[]), "")))
    for target, secret in (("e2", "hunter2"), ("e3", "第一单")):
        ctx = Ctx()
        ctx.run_state["browser_session"] = "abc"
        ctx.run_state["browser_refs"] = refs
        args = {"action": "fill", "target": target, "value": secret}
        await bst.run_browser_session_tool(ctx, "browser_step", args)
        out, hits = bst.redact_sensitive_step_args([_tool_call("browser_step", **args)], refs)
        logged = ctx.run_state["browser_log"][-1]
        # 两本账互为镜像：log 侧的 value_redacted == 参数侧遮没遮 == 判定本身
        assert bool(hits) is logged["value_redacted"] is checked[-1][1] is checked[-2][1]
        assert checked[-1] == checked[-2]          # 同一个 ref 打到同一个判定源（动作前那份清单）
        if target == "e2":
            assert "hunter2" not in str(logged)
            assert "<redacted:sensitive_input>" in out[0]["function"]["arguments"]
        else:
            assert logged["value"] == "第一单"
            assert json.loads(out[0]["function"]["arguments"])["value"] == "第一单"
    assert checked == [("e2", True), ("e2", True), ("e3", False), ("e3", False)]


async def test_an_unknown_handle_is_masked_like_a_sensitive_one():
    """未知即遮（Critical C-1）：target 是句柄形态、却不在清单里时，参数侧按 sensitive 处理。

    `is_sensitive_ref` 只有「在清单里且 sensitive」一个 True 分支，对不在清单里的 ref 返回
    False——它没有「未知」这第三态。而 loop 落库那行写在派发**之前**，只能用本轮开始时那份清单，
    于是「同一条消息里先 click 展开带口令框的表单、紧接着 fill 那个新出现的 ref」这两步，
    第二步的 value 会明文进 `message.tool_args` 与 WS `tool_call` 事件，而结果侧读到的已经是
    第一步返回的新清单、照常遮断——正是 R-33 禁止的那种分家。模型不必猜中 ref：登录页
    e1=用户名、e2=口令这类编号本来就可预测。
    只认句柄形态：navigate 的 target 是 path，不是 ref，不进这条规则。
    每一种都单独用**只含一条调用**的列表去钉（位次 1）：位次 2 起另有位次从严规则（裁定 R-35），
    两条规则叠在同一条上就分不出各自的红。
    """
    refs = [{"ref": "e2", "role": "textbox", "name": "用户名", "sensitive": False}]
    out, hits = bst.redact_sensitive_step_args(
        [_tool_call("browser_step", action="fill", target="e9", value="hunter2")], refs)
    assert hits == 1
    assert json.loads(out[0]["function"]["arguments"])["value"] == "<redacted:sensitive_input>"
    # path 形态（navigate）不进这条规则：不是句柄，无从判定也谈不上风险
    kept, kept_hits = bst.redact_sensitive_step_args(
        [_tool_call("browser_step", action="navigate", target="/login", value="hunter2")], refs)
    assert kept_hits == 0 and "hunter2" in kept[0]["function"]["arguments"]
    # 已知非 sensitive 的 ref 在位次 1 照旧放行——一刀切会把备注/地址的可读性一起毁掉
    listed, listed_hits = bst.redact_sensitive_step_args(
        [_tool_call("browser_step", action="fill", target="e2", value="alice")], refs)
    assert listed_hits == 0 and json.loads(listed[0]["function"]["arguments"])["value"] == "alice"
    # 清单为空（本 run 还没 open 过 / open 前就被裁掉）是「未知」的极端形状，同样遮
    lonely, lonely_hits = bst.redact_sensitive_step_args(
        [_tool_call("browser_step", action="fill", target="e9", value="hunter2")], [])
    assert lonely_hits == 1 and "hunter2" not in lonely[0]["function"]["arguments"]


async def test_every_step_after_the_first_is_masked_even_on_a_listed_plain_ref():
    """位次从严（裁定 R-35，C-1 残留）：同一条消息里第 2 个及以后的 `browser_step` 一律按 sensitive 遮。

    「未知即遮」只覆盖「轮初清单里查无此 ref」，可真实形状是**索引被复用**的那一半：ref 每次
    重渲染都从头重编（规格 §6.2），`click` 展开出来的口令框编号通常正落在旧清单已有的索引上。
    轮初 `e2=搜索框`（非 sensitive）→ 同轮第一步 click 之后 `e2=口令`（sensitive）→
    紧接着 `fill e2 value=hunter2`。「存在即信任」在这种形状下仍然放行，因为参数侧只能看到
    轮初那一份，而那份说的已经不是当步的事实。

    轮初清单只对**位次 1** 那条权威：本条消息在这一步之前没有别的**浏览器调用**改写过页面
    （改写者含 `_open`，裁定 R-36；args 畸形/为空却照样被派发的 open 也算，裁定 R-38）。
    第 2 条起面对的是「已被前几条改写、但落库行写不出来」的清单——与其猜它长什么样，不如从严。
    代价只是同轮多调用时非口令的 fill 也显示成占位符（每次走查 0–2 条的量级），
    sidecar 拿的仍是原值，功能不断。
    """
    refs = [{"ref": "e2", "role": "textbox", "name": "搜索框", "sensitive": False},
            {"ref": "e3", "role": "textbox", "name": "备注", "sensitive": False}]
    calls = [_tool_call("browser_step", action="fill", target="e2", value="订单号"),
             _tool_call("browser_step", action="click", target="e3"),
             _tool_call("browser_step", action="fill", target="e3", value="第一单"),
             _tool_call("browser_step", action="fill", target="e2", value="hunter2")]
    out, hits = bst.redact_sensitive_step_args(calls, refs)
    assert hits == 2
    # 位次 1：轮初清单权威，已知非 sensitive 就放行（否则正常走查全被遮成占位符）
    assert out[0] is calls[0]
    # 位次也数「不带 value 的步」：click 本身不遮（没 value 可遮），但它占掉一个位次，
    # 排在它后面的 fill 不能因为它而退回「第一条」
    assert out[1] is calls[1]
    assert json.loads(out[2]["function"]["arguments"])["value"] == "<redacted:sensitive_input>"
    assert json.loads(out[3]["function"]["arguments"])["value"] == "<redacted:sensitive_input>"
    # 位次 1 之外不看清单：连 navigate 带 value 也一并遮——结果侧 `_step` 遮 value 时同样不问动作
    # （它对任何带 value 的步按 target 判 sensitive），两侧口径保持一致
    tail, tail_hits = bst.redact_sensitive_step_args(
        [_tool_call("browser_step", action="click", target="e3"),
         _tool_call("browser_step", action="navigate", target="/login", value="hunter2")], refs)
    assert tail_hits == 1
    assert json.loads(tail[1]["function"]["arguments"])["value"] == "<redacted:sensitive_input>"
    # 遮的只有 value：navigate 的 target 是 path 不是句柄，读历史的人仍要能看出这一步去了哪
    # （防止后来人「顺手」把 target 一起遮掉）
    assert json.loads(tail[1]["function"]["arguments"])["target"] == "/login"
    # 位次是按**传进来的那份列表**数的（loop 传的是整条 assistant 消息的全部调用）：
    # 同一条 fill 单独喂进来就是位次 1、照旧放行——这条规则整个挂在「同一份列表里有前序调用」上
    alone, alone_hits = bst.redact_sensitive_step_args(calls[2:3], refs)
    assert alone_hits == 0
    assert json.loads(alone[0]["function"]["arguments"])["value"] == "第一单"


async def test_open_and_close_hold_an_ordinal_but_other_tools_do_not():
    """位次口径（裁定 R-36）数的是「本条消息在这一步之前有没有别的**浏览器调用**改写过清单」。

    R-35 的计数器只数 `browser_step`，可改写/作废清单的调用不止它：
    - `_open` 同样往 `run_state["browser_refs"]` 写新页清单（`browser_session_tools.py` 的
      `ctx.run_state["browser_refs"] = list(data.get("interactive"))`），而轮初那份在派发前就一次读死。
      于是 `[browser_open "/login", browser_step fill e2]` 里落库看到旧页（`e2=搜索框`，放行）、
      执行读到新页（`e2=口令`，遮断）——形状 ④。
    - `_close` 之后的 step 必然被拒，值进不了表单也进不了 `browser_log`，但落库那行写在派发之前，
      原值照样明文进 `message.tool_args`。同族，一并计入。

    非浏览器调用不占位次：`fs_write` 之类改不动页面清单，把它算进去会把「一条写文件 + 一条正常
    step」这种普通消息一起遮成占位符，那是 R-34/R-35 都明确拒绝的一刀切。
    open/close 自身不带 `value`，进计数器只占位次、条目原样返回（`hits == 0` 保原对象那条身份契约）。
    """
    refs = [{"ref": "e2", "role": "textbox", "name": "搜索框", "sensitive": False},
            {"ref": "e3", "role": "textbox", "name": "备注", "sensitive": False}]
    fill = _tool_call("browser_step", action="fill", target="e2", value="hunter2")
    open_call = _tool_call("browser_open", path="/login")
    close_call = _tool_call("browser_close")
    write_call = _tool_call("fs_write", path="a.txt", content="notes")

    # open 占位次：紧随其后的 fill 不看清单
    out, hits = bst.redact_sensitive_step_args([open_call, fill], refs)
    assert hits == 1
    assert json.loads(out[1]["function"]["arguments"])["value"] == "<redacted:sensitive_input>"
    assert out[0] is open_call                     # open 的 args 一字未改，连条目对象都是原引用
    # close 占位次
    out2, hits2 = bst.redact_sensitive_step_args([close_call, fill], refs)
    assert hits2 == 1
    assert json.loads(out2[1]["function"]["arguments"])["value"] == "<redacted:sensitive_input>"
    assert out2[0] is close_call
    # 别的浏览器调用也占位次：open 之后已经两步，第三条 fill 更该从严
    out3, hits3 = bst.redact_sensitive_step_args([close_call, open_call, fill], refs)
    assert hits3 == 1 and out3[0] is close_call and out3[1] is open_call
    # 非浏览器调用不占位次：单条 step 仍是位次 1、轮初清单照旧权威（反向钉）
    kept, kept_hits = bst.redact_sensitive_step_args([write_call, fill], refs)
    assert kept_hits == 0 and kept[0] is write_call and kept[1] is fill


def _raw_call(id_, name, raw_args):
    """`arguments` 是**原样字符串**的一条 tool_call：复现「无参数调用」与流式截断的真实形状。

    `llm.py` 把 slot 的 arguments 初始化成 `""`、只在 delta 带 args 时才往后追加，所以模型对一个
    schema 里 `"required": []` 的 `browser_open`（或一条被截断的流）产出的就是空串/半截 JSON。
    """
    return {"id": id_, "type": "function",
            "function": {"name": name, "arguments": raw_args}}


# 形状 ⑤ 的共同前提（三轮复查之后仍然成立的那句「一处遮一处不遮就是漏」）：
# `redact.py` 的 `rewrite_tool_calls_args` 对「名字命中名单、但 arguments 为空 / 非字符串 /
# JSON 不可解析」的条目直接 continue——不调 `rewrite`、不占位次；而 `loop.py` 派发时把同一份
# 畸形 args 换成 `{}` **照样执行**（`json.loads(...) or "{}"`，`JSONDecodeError` → `{}`）。
# `browser_open` 的 schema 是 `"required": []`（path 缺省合法），`_open(ctx, {})` 在
# `normalize_path(None)` 拿到 `("/", "")`，于是它真的开了一页并把 `run_state["browser_refs"]`
# 覆写成新页清单——上一轮的「畸形条目不占位次，因为它确实没改写过页面」这条论证就是错在这里。
# 下面两支各自独立钉一种畸形形状（分成两条测试，是为了让「只挡畸形 JSON、不挡空串」这种修法红）。
async def test_a_malformed_json_open_before_a_step_still_holds_an_ordinal():
    """位次按**名字**数（裁定 R-38，形状 ⑤ 之一）：`arguments` 是不可解析 JSON 的 open 也占位次。

    `[browser_open arguments='{"/path"', browser_step fill e2 value=hunter2]`：旧口径下那条 open
    进不了 `rewrite`、不占位次 → fill 被数成位次 1 → 判「轮初清单里已知非 sensitive」→ 原值进
    `message.tool_args` 与 WS `tool_call`；而派发端 `_open({})` 真开了 `/`、`_step` 读到新页的
    `e2=口令` → 结果侧遮断，值还真被填进口令框。参数侧那份清单就此不再权威，位次必须算上它。
    """
    refs = [{"ref": "e2", "role": "textbox", "name": "搜索框", "sensitive": False},
            {"ref": "e3", "role": "textbox", "name": "备注", "sensitive": False}]
    fill = _tool_call("browser_step", action="fill", target="e2", value="hunter2")
    broken_open = _raw_call("c1", "browser_open", '{"/path"')

    out, hits = bst.redact_sensitive_step_args([broken_open, fill], refs)
    assert hits == 1
    assert json.loads(out[1]["function"]["arguments"])["value"] == "<redacted:sensitive_input>"
    # 遮的仍只有 fill 的 value；畸形那条 open 原样保留（`redact.py` 的既有语义，本轮不碰它一个字节）
    assert json.loads(out[1]["function"]["arguments"])["target"] == "e2"
    assert out[0] is broken_open and out[0]["function"]["arguments"] == '{"/path"'
    # 名字没命中的畸形条目不占位次：`fs_write` 改不动清单，派发它也一样（反向钉）
    broken_write = _raw_call("c2", "fs_write", "")
    kept, kept_hits = bst.redact_sensitive_step_args([broken_write, fill], refs)
    assert kept_hits == 0 and kept[0] is broken_write and kept[1] is fill


async def test_an_open_with_empty_arguments_before_a_step_still_holds_an_ordinal():
    """位次按**名字**数（裁定 R-38，形状 ⑤ 之二）：`arguments == ""` 的 open 也占位次。

    这一支比畸形 JSON **更日常**：空串不是畸形，是 `llm.py` 对「无参数的 `browser_open`」的正常产物
    （slot 初始化成 `""`，delta 不带 args 就一直是 `""`），而 `browser_open` 的 `"required": []` 让它
    是个完全合法的调用——既有 `test_open_binds_session_to_run_state_only` 就是用 `{}` 打开一页的。
    `redact.py:if not raw_args` 把它跳过（不占位次），`loop.py` 的 `arguments or "{}"` 却把它换成 `{}`
    真执行：于是「一条默认的 open + 一条 fill」这条天天会走的路径上，fill 被数成位次 1、按已经作废的
    轮初清单放行，明文进库与 WS。必须独立钉住，不能靠上面那条畸形 JSON 的测试兜。
    """
    refs = [{"ref": "e2", "role": "textbox", "name": "搜索框", "sensitive": False},
            {"ref": "e3", "role": "textbox", "name": "备注", "sensitive": False}]
    fill = _tool_call("browser_step", action="fill", target="e2", value="hunter2")
    empty_open = _raw_call("c1", "browser_open", "")

    out, hits = bst.redact_sensitive_step_args([empty_open, fill], refs)
    assert hits == 1
    assert json.loads(out[1]["function"]["arguments"])["value"] == "<redacted:sensitive_input>"
    assert json.loads(out[1]["function"]["arguments"])["action"] == "fill"
    # 空串条目原样保留：`redact.py` 不替它补 `{}`，本轮也不改它（身份与结构都不动）
    assert out[0] is empty_open and out[0]["function"]["arguments"] == ""
    # 位次 1 的豁免仍然成立：fill 前面没有任何浏览器调用时照旧按轮初清单放行（反向钉）
    alone, alone_hits = bst.redact_sensitive_step_args([fill], refs)
    assert alone_hits == 0 and alone[0] is fill


async def test_a_numeric_pin_is_masked_on_both_sides(monkeypatch):
    """非字符串 value 不许绕过参数侧（I-1）：数值型 PIN 正是这个形状，单步轮也漏。

    结果侧 `value = str(args.get("value") or "")` 主动兜住了非字符串，参数侧改前是
    `if not isinstance(value, str)` 就 return——同一个 sensitive ref、同一个 123456，一边遮
    一边原样落库。修法是与结果侧对齐口径：先 `str(value or "")` 再判空再遮，
    占位符检查排在转换之后。
    """
    refs = [{"ref": "e2", "role": "textbox", "name": "密码", "sensitive": True}]
    ctx = Ctx()
    ctx.run_state["browser_session"] = "abc"
    ctx.run_state["browser_refs"] = refs
    monkeypatch.setattr(bst, "fetch_app", lambda c: _aget(_app()))
    monkeypatch.setattr(bst, "_sidecar_step",
                        lambda sid, payload: _aget((_step_data(interactive=[]), "")))
    await bst.run_browser_session_tool(
        ctx, "browser_step", {"action": "fill", "target": "e2", "value": 123456})
    logged = ctx.run_state["browser_log"][-1]
    assert logged["value_redacted"] is True and "123456" not in str(logged)
    out, hits = bst.redact_sensitive_step_args(
        [_tool_call("browser_step", action="fill", target="e2", value=123456)], refs)
    assert hits == 1
    assert json.loads(out[0]["function"]["arguments"])["value"] == "<redacted:sensitive_input>"
    # 判空口径也与结果侧一致：0 在两侧都算「没填」，不计数也不遮（结果侧连 value 键都不写）
    zero = _tool_call("browser_step", action="fill", target="e2", value=0)
    kept, zero_hits = bst.redact_sensitive_step_args([zero], refs)
    assert zero_hits == 0 and kept[0] is zero
    # 转换后才查标记：已经是占位符的那一份不重复计数、不套娃
    again, again_hits = bst.redact_sensitive_step_args(out, refs)
    assert again_hits == 0


async def test_step_echo_target_name_comes_from_the_pre_action_refs(monkeypatch):
    """US-22「目标元素名」的取法（裁定 R-32）：只认**动作之前**那份清单。

    动作之后的 `interactive` 是新快照，而 ref 每次重渲染全部作废（规格 §6.2）——拿新快照反查
    `e7` 会把「这一步点的是谁」标成另一个元素，画出一个假目标比不画更糟。
    这条测试就是给下一个想改成读新清单的人准备的。
    """
    ctx = Ctx()
    ctx.run_state["browser_session"] = "abc"
    ctx.run_state["browser_refs"] = [{"ref": "e7", "role": "button", "name": "结算",
                                      "sensitive": False}]
    fresh = [{"ref": "e7", "role": "button", "name": "退出登录", "sensitive": False}]
    _step_env(monkeypatch, _step_data(interactive=fresh))
    out = await bst.run_browser_session_tool(
        ctx, "browser_step", {"action": "click", "target": "e7"})
    assert out["target_name"] == "结算"
    assert out["target"] == "e7"
    assert [i["name"] for i in out["interactive"]] == ["退出登录"]     # 新快照仍照常附回，只是不拿来标名


async def test_step_echo_target_name_and_failure_shape(monkeypatch):
    """点不动那一步也要有名字（卡片两种形状读同一个字段），且名字过既有遮断闸。"""
    ctx = Ctx()
    ctx.run_state["browser_session"] = "abc"
    ctx.run_state["browser_refs"] = [{"ref": "e7", "role": "textbox",
                                      "name": "密码 token=abc123def456ghi 输入框",
                                      "sensitive": True}]

    async def blocked(sid, payload):
        return {"ok": False, "error": "元素不可点击或动作失败: timeout",
                "interactive": [], "steps_charged": True}, ""

    monkeypatch.setattr(bst, "fetch_app", lambda c: _aget(_app()))
    monkeypatch.setattr(bst, "_sidecar_step", blocked)
    _no_shot(monkeypatch)
    out = await bst.run_browser_session_tool(
        ctx, "browser_step", {"action": "click", "target": "e7"})
    assert out["ok"] is False
    assert "abc123def456ghi" not in out["target_name"]      # 页面派生文本，同清单一个口径
    assert "输入框" in out["target_name"]


async def test_step_echo_omits_target_name_when_it_cannot_be_known(monkeypatch):
    """查不到就不编一个名字：ref 不在动作前的清单里、navigate 的 target 是 path 都不带这字段。"""
    ctx = Ctx()
    ctx.run_state["browser_session"] = "abc"
    ctx.run_state["browser_refs"] = [{"ref": "e1", "role": "button", "name": "出牌",
                                      "sensitive": False}]
    _step_env(monkeypatch, _step_data())
    out = await bst.run_browser_session_tool(
        ctx, "browser_step", {"action": "click", "target": "e9"})
    assert "target_name" not in out
    nav = await bst.run_browser_session_tool(
        ctx, "browser_step", {"action": "navigate", "target": "/orders"})
    assert "target_name" not in nav and nav["target"] == "/orders"


async def test_close_report_carries_the_target_name(monkeypatch):
    """M-6：收口报告的行不能只留一个作废的句柄。

    按规格 §6.2，每次重渲染后旧 ref 全部作废——读报告的人手上没有任何一份清单能对回 `e7`
    是谁，那一个句柄实际是噪声。`_step` 早就算好了 `target_name`（裁定 R-32），这次把它写进
    log，`_close` 再照 `error` / `note` 的同一个 `_redact` 形状带进 report 项（报告是会被原样
    贴进消息历史的那一份，纵深防御）。成功与点不动两支都记账，所以两支都带名字。
    """
    async def fake_step(sid, payload):
        if payload["ref"] == "e7":
            return {"ok": False, "error": "元素不可点击或动作失败: timeout",
                    "interactive": [], "steps_charged": True}, ""
        return _step_data(), ""

    monkeypatch.setattr(bst, "fetch_app", lambda c: _aget(_app()))
    monkeypatch.setattr(bst, "browser_target_host", lambda: "host.docker.internal")
    monkeypatch.setattr(bst, "_sidecar_open", lambda url, **kw: _aget((_session_ok(), "")))
    monkeypatch.setattr(bst, "_sidecar_delete", lambda sid: _aget(True))
    monkeypatch.setattr(bst, "_sidecar_step", fake_step)
    _no_shot(monkeypatch)

    # 成功两支：第一步点 e1（动作前清单里叫「按钮1」），第二步点 e9——清单里没有的 ref 不编名字
    ctx = Ctx()
    await bst.run_browser_session_tool(ctx, "browser_open", {"path": "/orders"})
    await bst.run_browser_session_tool(ctx, "browser_step", {"action": "click", "target": "e1"})
    await bst.run_browser_session_tool(ctx, "browser_step", {"action": "click", "target": "e9"})
    report = (await bst.run_browser_session_tool(ctx, "browser_close", {}))["report"]
    assert report[0]["action"] == "open" and "target_name" not in report[0]
    assert report[1]["target_name"] == "按钮1"
    assert "target_name" not in report[2]            # 查不到就没有这个键（同 echo 的规矩）
    assert [s.get("target_name") for s in ctx.run_state["browser_log"]] == [None, "按钮1", None]

    # 点不动那一支同样带名字，且名字已过遮断闸（页面派生文本，与清单同一个口径）
    ctx2 = Ctx()
    ctx2.run_state["browser_session"] = "abc"
    ctx2.run_state["browser_refs"] = [{"ref": "e7", "role": "button",
                                       "name": "提交 token=abc123def456ghi", "sensitive": False}]
    await bst.run_browser_session_tool(
        ctx2, "browser_step", {"action": "click", "target": "e7"})
    row = (await bst.run_browser_session_tool(ctx2, "browser_close", {}))["report"][0]
    assert row["ok"] is False and row["target_name"]
    assert "abc123def456ghi" not in row["target_name"] and "提交" in row["target_name"]


async def test_action_failure_is_ok_false_and_expect_failure_is_passed_false(monkeypatch):
    ctx = Ctx()
    ctx.run_state["browser_session"] = "abc"
    ctx.run_state["browser_refs"] = [{"ref": "e1", "role": "button", "name": "出牌"}]
    monkeypatch.setattr(bst, "fetch_app", lambda c: _aget(_app()))

    async def blocked(sid, payload):
        return {"ok": False, "error": "元素不可点击或动作失败: timeout",
                "interactive": [{"ref": "e1", "role": "button", "name": "出牌"}],
                "steps_charged": True}, ""

    monkeypatch.setattr(bst, "_sidecar_step", blocked)
    _no_shot(monkeypatch)
    out = await bst.run_browser_session_tool(
        ctx, "browser_step", {"action": "click", "target": "e1"})
    assert out["ok"] is False
    assert out["interactive"]                        # 失败当场带回新清单，省一次往返

    # 两种形状不许混：断言不过仍是 ok:true，模型对它们的下一步动作完全不同。
    ctx2 = Ctx()
    ctx2.run_state["browser_session"] = "abc"
    _step_env(monkeypatch, _step_data(passed=False, note="页面文本未包含「订单已创建」"))
    _no_shot(monkeypatch)
    got = await bst.run_browser_session_tool(
        ctx2, "browser_step", {"action": "click", "target": "e1",
                               "expect": {"type": "text_contains", "value": "订单已创建"}})
    assert got["ok"] is True and got["passed"] is False
    assert got["note"] == "页面文本未包含「订单已创建」"


async def test_step_without_session_does_not_call_sidecar(monkeypatch):
    async def boom(*_a, **_k):
        raise AssertionError("没开会话不该打 sidecar")
    monkeypatch.setattr(bst, "_sidecar_step", boom)
    out = await bst.run_browser_session_tool(
        Ctx(), "browser_step", {"action": "click", "target": "e1"})
    assert out["ok"] is False and "browser_open" in out["error"]


async def test_steps_exhausted_rejects_before_the_sidecar(monkeypatch):
    """US-13：额度用尽要说清楚「先收口」，而且不发起一次不执行的调用、不动任何账。"""
    ctx = Ctx(cfg={"agent.browser_max_steps_per_run": 2})
    ctx.run_state["browser_session"] = "abc"
    ctx.run_state["browser_steps_used"] = 2
    ctx.run_state["browser_refs"] = [{"ref": "e1", "role": "button", "name": "出牌"}]

    async def boom(*_a, **_k):
        raise AssertionError("步数用尽不该打到 sidecar")

    monkeypatch.setattr(bst, "fetch_app", lambda c: _aget(_app()))
    monkeypatch.setattr(bst, "_sidecar_step", boom)
    out = await bst.run_browser_session_tool(ctx, "browser_step",
                                             {"action": "click", "target": "e1"})
    assert out["ok"] is False and out["steps_left"] == 0
    assert "browser_close" in out["error"]
    assert ctx.run_state["browser_session"] == "abc"      # 会话还在，只是不能再走
    assert ctx.run_state["browser_steps_used"] == 2


async def test_bad_navigate_path_and_missing_target_reject_without_sidecar(monkeypatch):
    """US-17：navigate 只接受同源相对 path；其余动作少了 target 就是没意图，别替它猜。"""
    async def boom(*_a, **_k):
        raise AssertionError("参数不合法不该打到 sidecar")

    ctx = Ctx()
    ctx.run_state["browser_session"] = "abc"
    monkeypatch.setattr(bst, "fetch_app", lambda c: _aget(_app()))
    monkeypatch.setattr(bst, "_sidecar_step", boom)

    out = await bst.run_browser_session_tool(
        ctx, "browser_step", {"action": "navigate", "target": "https://evil.example/x"})
    assert out["ok"] is False and "同源" in out["error"]
    assert out["steps_charged"] is False
    out = await bst.run_browser_session_tool(ctx, "browser_step", {"action": "click", "target": ""})
    assert out["ok"] is False and "target" in out["error"]
    assert ctx.run_state.get("browser_steps_used") in (None, 0)


async def test_every_step_rejection_exit_says_steps_charged(monkeypatch):
    """每条拒绝出口都显式回 `steps_charged: false`：模型与前端按字段读，缺字段就是第三种状态。

    「没开会话」与「步数用尽」这两条最早出口原先没有这个字段，而 path 非法、缺 target、
    传输失败三条都有——同一个「什么都没做成」的类别里出现两种写法，读的人只能猜。
    """
    monkeypatch.setattr(bst, "fetch_app", lambda c: _aget(_app()))

    async def boom(*_a, **_k):
        raise AssertionError("被拒绝的调用不该打到 sidecar（R-24 零副作用）")

    monkeypatch.setattr(bst, "_sidecar_step", boom)
    exhausted = Ctx(cfg={"agent.browser_max_steps_per_run": 1})
    exhausted.run_state["browser_session"] = "abc"
    exhausted.run_state["browser_steps_used"] = 1
    has_session = Ctx()
    has_session.run_state["browser_session"] = "abc"
    outs = [await bst.run_browser_session_tool(Ctx(), "browser_step",
                                               {"action": "click", "target": "e1"}),
            await bst.run_browser_session_tool(exhausted, "browser_step",
                                               {"action": "click", "target": "e1"}),
            await bst.run_browser_session_tool(has_session, "browser_step",
                                               {"action": "navigate", "target": "//evil.example"}),
            await bst.run_browser_session_tool(has_session, "browser_step",
                                               {"action": "click", "target": ""})]
    for out in outs:
        assert out["ok"] is False
        assert "steps_charged" in out, out
        assert out["steps_charged"] is False, out
    assert exhausted.run_state["browser_steps_used"] == 1      # 拒绝仍不留副作用
    assert "browser_log" not in exhausted.run_state


async def test_session_gone_charges_nothing_and_unbinds(monkeypatch):
    """US-12 / US-14：会话没了不扣步数，且要解绑——留着绑定会让每一步都重打那个死 id。"""
    ctx = Ctx()
    ctx.run_state["browser_session"] = "abc"
    ctx.run_state["browser_refs"] = [{"ref": "e1", "role": "button", "name": "出牌"}]

    async def no_shot(sid):
        raise AssertionError("页面物理上已经没了，§5.3 那一行不带 screenshot，不该去补图")

    monkeypatch.setattr(bst, "_sidecar_screenshot", no_shot)
    _step_env(monkeypatch, {"ok": False, "error": "浏览器会话已结束，请重新 browser_open",
                            "session_gone": True, "steps_charged": False})
    out = await bst.run_browser_session_tool(ctx, "browser_step",
                                             {"action": "click", "target": "e1"})
    assert out["ok"] is False and out["session_gone"] is True
    assert out["steps_charged"] is False
    assert "browser_open" in out["error"]
    assert ctx.run_state["browser_session"] is None
    assert ctx.run_state.get("browser_steps_used") in (None, 0)


async def test_step_charges_exactly_what_the_sidecar_charged(monkeypatch):
    """步数账与 `steps_charged` 必须一起涨或一起不涨（规格 §5.2）。

    只信 sidecar 的读数、自己不加，`steps_left` 会长期虚高，US-13 的上限形同不存在；
    反过来自加而 sidecar 没扣，会话没了的那一步会被白记一笔。
    """
    ctx = Ctx()
    ctx.run_state["browser_session"] = "abc"
    ctx.run_state["browser_refs"] = [{"ref": "e1", "role": "button", "name": "出牌"}]

    async def reply(sid, payload):
        return _step_data(steps_charged=payload["action"] != "click"), ""

    monkeypatch.setattr(bst, "fetch_app", lambda c: _aget(_app()))
    monkeypatch.setattr(bst, "_sidecar_step", reply)
    out = await bst.run_browser_session_tool(ctx, "browser_step",
                                             {"action": "click", "target": "e1"})
    assert out["steps_charged"] is False
    assert ctx.run_state.get("browser_steps_used") in (None, 0)
    out = await bst.run_browser_session_tool(ctx, "browser_step",
                                             {"action": "fill", "target": "e1", "value": "x"})
    assert out["steps_charged"] is True
    assert ctx.run_state["browser_steps_used"] == 1
    assert out["steps_left"] == bst.DEFAULT_MAX_STEPS - 1


async def test_action_failure_charges_and_refreshes_the_ref_list(monkeypatch):
    """US-09：ref 失效当场附新清单，且清单要成为下一步的判定源（旧 ref 物理上已作废）。"""
    ctx = Ctx()
    ctx.run_state["browser_session"] = "abc"
    ctx.run_state["browser_refs"] = [{"ref": "e9", "role": "button", "name": "旧按钮",
                                      "sensitive": False}]
    fresh = [{"ref": "e1", "role": "textbox", "name": "密码 token=abc123def456ghi",
              "sensitive": True}]

    async def gone(sid, payload):
        return {"ok": False, "error": "页面已变化，请用最新清单里的 ref",
                "interactive": fresh, "interactive_truncated": False,
                "steps_charged": True}, ""

    monkeypatch.setattr(bst, "fetch_app", lambda c: _aget(_app()))
    monkeypatch.setattr(bst, "_sidecar_step", gone)
    _no_shot(monkeypatch)
    out = await bst.run_browser_session_tool(ctx, "browser_step",
                                             {"action": "click", "target": "e9"})
    assert out["ok"] is False
    assert [i["ref"] for i in out["interactive"]] == ["e1"]
    assert out["steps_charged"] is True
    assert ctx.run_state["browser_steps_used"] == 1
    assert ctx.run_state["browser_refs"] == fresh       # 存原始清单（sensitive 判定源）
    # 失败分支带回的清单也是页面派生文本，同 `_shape_state` 一个口径过遮断，不许开第二条路。
    assert "abc123def456ghi" not in json.dumps(out, ensure_ascii=False)
    assert out["redacted"] >= 1


async def test_action_failure_error_is_scrubbed_and_reports_the_dialog(monkeypatch):
    """R-22 与 US-11：动作失败的原因文本是 sidecar 原话（内嵌容器 IP），弹窗也来自页面。

    失败分支若不走 `_sidecar_text` 这把闸，拓扑泄漏就只在「点不动」这条最常见的路径上复活；
    丢掉 dialog 的后果是模型以为删除生效了。
    """
    ctx = Ctx()
    ctx.run_state["browser_session"] = "abc"
    ctx.run_state["browser_refs"] = [{"ref": "e1", "role": "button", "name": "删除"}]
    monkeypatch.setattr(bst, "browser_target_host", lambda: "host.docker.internal")

    async def failed(sid, payload):
        return {"ok": False,
                "error": "导航失败: net::ERR_BLOCKED at http://192.168.65.254:8241/x",
                "dialog": {"type": "confirm", "message": "确定删除 password=hunter2 吗",
                           "handled": "dismissed"},
                "steps_charged": True}, ""

    monkeypatch.setattr(bst, "fetch_app", lambda c: _aget(_app()))
    monkeypatch.setattr(bst, "_sidecar_step", failed)
    _no_shot(monkeypatch)
    out = await bst.run_browser_session_tool(
        ctx, "browser_step", {"action": "navigate", "target": "/x"})
    blob = json.dumps(out, ensure_ascii=False)
    assert "192.168.65.254" not in blob
    assert "hunter2" not in blob
    assert "localhost:8241" in out["error"]
    assert "ERR_BLOCKED" in out["error"]
    assert out["dialog"]["handled"] == "dismissed"


async def test_action_failure_carries_the_incremental_signals_of_the_broken_step(monkeypatch):
    """裁定 R-29：`ok:false` 也带 console_new / page_errors_new / failed_requests_new。

    「点下去把页面点崩了、动作本身却报失败」是真机里最常见的一格。R-26 之后这条路径必然附一张
    崩页图，但只有图没有字——模型只能猜「这个崩是我点的还是页面本来就崩的」。sidecar 现在把
    `_step_state` 也接给了失败响应，后端这一头必须把它过同一道清洗闸再送进模型上下文。
    形状仍是 `ok:false` 且没有 `passed`：加信号不许把「点不动」写成「断言未通过」。
    """
    ctx = Ctx()
    ctx.run_state["browser_session"] = "abc"
    ctx.run_state["browser_refs"] = [{"ref": "e1", "role": "button", "name": "出牌"}]
    monkeypatch.setattr(bst, "fetch_app", lambda c: _aget(_app()))

    async def broken(sid, payload):
        return {"ok": False, "error": "元素不可点击或动作失败: timeout",
                "interactive": _interactive(),
                "console_new": [{"level": "error",
                                 "text": "Uncaught 崩了 at http://192.168.65.254:8241/a.js"}],
                "page_errors_new": ["Uncaught TypeError: 点崩了 at "
                                    "http://192.168.65.254:8241/app.js:1:9",
                                    "Uncaught Error: token=abc123def456ghi 在栈里"],
                "failed_requests_new": [{"method": "GET", "status": 500,
                                         "url": "http://192.168.65.254:8241/api/orders"}],
                "steps_charged": True}, ""

    monkeypatch.setattr(bst, "_sidecar_step", broken)
    _no_shot(monkeypatch)
    out = await bst.run_browser_session_tool(
        ctx, "browser_step", {"action": "click", "target": "e1"})
    assert out["ok"] is False and "passed" not in out        # 两形不混：失败步没有断言结论
    assert out["page_errors_new"], "动作失败必须把「这一步点崩了」的文字信号带给模型"
    assert "localhost:8241" in out["page_errors_new"][0] and "点崩了" in out["page_errors_new"][0]
    assert [i["text"] for i in out["console_new"]] == [
        "Uncaught 崩了 at http://localhost:8241/a.js"]
    assert out["failed_requests_new"][0]["url"] == "http://localhost:8241/api/orders"
    assert out["failed_requests_new"][0]["status"] == 500    # 非文本字段原样保留
    blob = json.dumps(out, ensure_ascii=False)
    assert "192.168.65.254" not in blob and "abc123def456ghi" not in blob
    assert out["redacted"] >= 1
    assert out["steps_charged"] is True and bool(out["interactive"])


async def test_action_failure_without_collected_state_claims_nothing(monkeypatch):
    """缺 `*_new` 键的失败（sidecar「动作后读不回页面」那条出口）就不报增量：空列表会被读成「没崩」。"""
    ctx = Ctx()
    ctx.run_state["browser_session"] = "abc"
    ctx.run_state["browser_refs"] = [{"ref": "e1", "role": "button", "name": "出牌"}]
    monkeypatch.setattr(bst, "fetch_app", lambda c: _aget(_app()))

    async def unreadable(sid, payload):
        return {"ok": False,
                "error": "步骤执行后页面读取失败: Execution context was destroyed",
                "steps_charged": True}, ""

    monkeypatch.setattr(bst, "_sidecar_step", unreadable)
    _no_shot(monkeypatch)
    out = await bst.run_browser_session_tool(
        ctx, "browser_step", {"action": "click", "target": "e1"})
    assert out["ok"] is False and out["steps_charged"] is True
    for key in ("console_new", "page_errors_new", "failed_requests_new"):
        assert key not in out


async def test_sidecar_reported_urls_are_scrubbed_while_page_text_is_not(monkeypatch):
    """裁定 R-28 的边界：凡 sidecar 报告的 URL 一律清洗，凡页面自身的内容一律不清洗。

    清洗是 `target_netloc → display_netloc` 的等价改写（同一个资源、同一条 path），证据一个字没少；
    而 `title`/`text` 是被检查的对象本身，改写它就是篡改走查对象。原来那句「页面数据不清洗」把
    console 与失败请求也算进去了——`browser/app.py` 明写 sidecar 回的是重写后的 connect_url，
    所以那两处带的正是内部拓扑。
    """
    ctx = Ctx()
    ctx.run_state["browser_session"] = "abc"
    ctx.run_state["browser_refs"] = [{"ref": "e1", "role": "button", "name": "出牌"}]
    internal = "http://192.168.65.254:8241"
    _step_env(monkeypatch, _step_data(
        title=f"订单管理 {internal}", text=f"正文里也有 {internal}/keep 这句原话",
        console_new=[{"level": "error", "text": f"脚本报错 {internal}/app.js:1:1",
                      "truncated": False}],
        failed_requests_new=[{"method": "GET", "url": f"{internal}/api/orders", "status": 500,
                              "type": "fetch", "blocked": False}]))
    _no_shot(monkeypatch)
    out = await bst.run_browser_session_tool(ctx, "browser_step",
                                             {"action": "click", "target": "e1"})
    # 洗：sidecar 报出的两处 URL
    assert out["console_new"][0]["text"] == "脚本报错 http://localhost:8241/app.js:1:1"
    assert out["failed_requests_new"][0]["url"] == "http://localhost:8241/api/orders"
    # 不洗：页面正文原样（被检查的对象不许被改写）
    assert out["title"] == f"订单管理 {internal}"
    assert out["text"] == f"正文里也有 {internal}/keep 这句原话"


async def test_step_never_leaks_the_bound_session_or_the_internal_url(monkeypatch):
    """ADR-0037 + R-16 的合并锚：返回值与 browser_log 里既没有 session_id 也没有容器 IP。

    run_state 里的绑定本身不算泄漏面（它不进消息历史），所以只断言模型看得见的那两份。
    """
    ctx = Ctx()
    ctx.run_state["browser_session"] = "sid-123"
    ctx.run_state["browser_refs"] = [{"ref": "e1", "role": "button", "name": "出牌"}]
    internal = "http://192.168.65.254:8241/orders?page=2"
    _step_env(monkeypatch, _step_data(url=internal))
    out = await bst.run_browser_session_tool(ctx, "browser_step",
                                             {"action": "click", "target": "e1"})
    assert out["url"] == "http://localhost:8241/orders?page=2"
    assert ctx.run_state["browser_log"][-1]["url"] == "http://localhost:8241/orders?page=2"
    for blob in (json.dumps(out, ensure_ascii=False),
                 json.dumps(ctx.run_state["browser_log"], ensure_ascii=False)):
        assert "sid-123" not in blob
        assert internal not in blob
        assert "session_id" not in blob


async def test_failed_action_is_written_into_the_browser_log(monkeypatch):
    """发现项 I-1：点不动的步也进 `browser_log`——章程里的「步数记账」要求账与报告一致。

    形状与断言失败刻意不同：`ok:false` + `passed:null` + 一句已清洗的原因 + 位置（display_url）。
    只涨 `browser_steps_used` 而不写日志的话，「12 次点不动 + 1 次成功」的收口报告会说
    steps_total:1、steps_failed:0、hint「所有已声明的断言均通过」——最贵的失败信号被洗掉了。
    """
    ctx = Ctx()
    ctx.run_state["browser_session"] = "abc"
    ctx.run_state["browser_log"] = [{"seq": 0, "action": "open", "target": "/", "passed": None,
                                     "url": "http://localhost:8241/"}]
    ctx.run_state["browser_refs"] = [{"ref": "e1", "role": "button", "name": "出牌"}]
    monkeypatch.setattr(bst, "fetch_app", lambda c: _aget(_app()))

    async def blocked(sid, payload):
        return {"ok": False,
                "error": "元素不可点击或动作失败: http://192.168.65.254:8241/x 超时",
                "url": "http://192.168.65.254:8241/orders", "interactive": _interactive(),
                "steps_charged": True}, ""

    monkeypatch.setattr(bst, "_sidecar_step", blocked)
    _no_shot(monkeypatch)
    out = await bst.run_browser_session_tool(
        ctx, "browser_step", {"action": "click", "target": "e1"})
    assert out["ok"] is False
    entry = ctx.run_state["browser_log"][-1]
    assert entry["ok"] is False and entry["passed"] is None   # 不与断言失败混成一个形状
    assert entry["seq"] == 1 and entry["action"] == "click" and entry["target"] == "e1"
    assert entry["url"] == "http://localhost:8241/orders"     # 失败在哪一页，读报告的人要的
    assert "超时" in entry["error"]
    blob = json.dumps(ctx.run_state["browser_log"], ensure_ascii=False)
    assert "192.168.65.254" not in blob                       # 原因在写日志之前就已清洗


async def test_failed_action_without_a_page_url_keeps_the_last_known_one(monkeypatch):
    """sidecar 没带地址的失败（path 非法那条出口）不许在日志里编一个「当前页」出来。"""
    ctx = Ctx()
    ctx.run_state["browser_session"] = "abc"
    ctx.run_state["browser_log"] = [{"seq": 0, "action": "open", "target": "/orders",
                                     "passed": None, "url": "http://localhost:8241/orders"}]
    ctx.run_state["browser_refs"] = [{"ref": "e1", "role": "button", "name": "出牌"}]
    monkeypatch.setattr(bst, "fetch_app", lambda c: _aget(_app()))
    monkeypatch.setattr(bst, "_sidecar_step", lambda sid, payload: _aget(
        ({"ok": False, "error": "path 必须是同源相对路径（以 / 开头）", "steps_charged": True}, "")))
    _no_shot(monkeypatch)
    await bst.run_browser_session_tool(ctx, "browser_step",
                                       {"action": "navigate", "target": "/x"})
    assert len(ctx.run_state["browser_log"]) == 2        # 这一步确实进了账
    assert ctx.run_state["browser_log"][-1]["url"] == "http://localhost:8241/orders"


async def test_uncharged_step_does_not_enter_the_report(monkeypatch):
    """没扣费的失败步（sidecar 的未知动作那条）不进报告：报告里的步恒等于「花掉额度的步」。"""
    ctx = Ctx()
    ctx.run_state["browser_session"] = "abc"
    ctx.run_state["browser_log"] = [{"seq": 0, "action": "open", "target": "/", "passed": None,
                                     "url": "http://localhost:8241/"}]
    ctx.run_state["browser_refs"] = [{"ref": "e1", "role": "button", "name": "出牌"}]
    monkeypatch.setattr(bst, "fetch_app", lambda c: _aget(_app()))
    monkeypatch.setattr(bst, "_sidecar_step", lambda sid, payload: _aget(
        ({"ok": False, "error": "未知动作: teleport", "steps_charged": False}, "")))
    _no_shot(monkeypatch)
    out = await bst.run_browser_session_tool(ctx, "browser_step",
                                             {"action": "teleport", "target": "e1"})
    assert out["ok"] is False and out["steps_charged"] is False
    assert len(ctx.run_state["browser_log"]) == 1
    assert ctx.run_state.get("browser_steps_used", 0) == 0


async def test_action_is_trimmed_before_the_sidecar(monkeypatch):
    """`"click "` 这种尾空格会被 sidecar 判成未知动作：那条出口不记账、不回清单，
    模型只剩一句它解释不了的重试指引。后端在发载荷之前就该把动作名 trim 掉。"""
    ctx = Ctx()
    ctx.run_state["browser_session"] = "abc"
    ctx.run_state["browser_refs"] = [{"ref": "e1", "role": "button", "name": "出牌"}]
    seen = _step_env(monkeypatch, _step_data())
    out = await bst.run_browser_session_tool(ctx, "browser_step",
                                             {"action": " click ", "target": "e1"})
    assert seen[0][1]["action"] == "click"
    assert out["action"] == "click"


async def test_step_transport_rejection_scrubs_the_internal_host(monkeypatch):
    """拒绝文案也是 sidecar 自由文本（`validate_target` 会回显我们发出去的 host）：
    step 这一面同样要过 `_sidecar_text`，否则拓扑泄漏只在「服务不可用」这条路径上复活。
    """
    ctx = Ctx()
    ctx.run_state["browser_session"] = "abc"
    ctx.run_state["browser_refs"] = [{"ref": "e1", "role": "button", "name": "出牌"}]
    monkeypatch.setattr(bst, "browser_target_host", lambda: "host.docker.internal")

    async def refused(sid, payload):
        return None, "目标地址不在允许清单内: 192.168.65.254:8241"

    monkeypatch.setattr(bst, "fetch_app", lambda c: _aget(_app()))
    monkeypatch.setattr(bst, "_sidecar_step", refused)
    out = await bst.run_browser_session_tool(ctx, "browser_step",
                                             {"action": "click", "target": "e1"})
    assert out["ok"] is False
    assert "192.168.65.254" not in json.dumps(out, ensure_ascii=False)
    assert "localhost:8241" in out["error"]


async def test_step_transport_rejection_reads_both_detail_shapes(monkeypatch):
    """双形状契约在 step 这一面同样成立：409 的 detail 是 str、422 的是 list。"""
    ctx = Ctx()
    ctx.run_state["browser_session"] = "abc"
    ctx.run_state["browser_refs"] = [{"ref": "e1", "role": "button", "name": "出牌"}]
    monkeypatch.setattr(bst, "fetch_app", lambda c: _aget(_app()))

    client, holder = _fake_httpx(resp=_Resp(409, {"detail": "浏览器服务已满"}))
    monkeypatch.setattr(bst, "httpx", holder)
    out = await bst.run_browser_session_tool(ctx, "browser_step",
                                             {"action": "click", "target": "e1"})
    assert out["ok"] is False and out["error"] == "浏览器服务已满"
    assert out["steps_charged"] is False
    assert client.calls[0][1].endswith("/sessions/abc/step")

    client, holder = _fake_httpx(resp=_Resp(422, {"detail": [
        {"loc": ["body", "expect"], "msg": "Input should be a valid dictionary",
         "type": "dict_type"}]}))
    monkeypatch.setattr(bst, "httpx", holder)
    out = await bst.run_browser_session_tool(ctx, "browser_step",
                                             {"action": "click", "target": "e1"})
    assert "expect" in out["error"] and "dict_type" not in out["error"]
    assert ctx.run_state.get("browser_steps_used") in (None, 0)


def test_step_payload_pins_the_expect_timeout_bounds():
    """上限值不许漂：夹在 500..8000 是规格 §10 钉的，超界配置要落到边界而不是原样发出。"""
    assert (bst.STEP_TIMEOUT_MS_MIN, bst.STEP_TIMEOUT_MS_MAX) == (500, 8000)
    assert bst.DEFAULT_STEP_TIMEOUT_MS == 3000
    assert bst.EXPECT_TIMEOUT_CFG_KEY == "agent.browser_expect_timeout_ms"


async def test_step_payload_carries_the_configured_budgets(monkeypatch):
    ctx = Ctx(cfg={"agent.browser_expect_timeout_ms": 99999,
                   "agent.browser_interactive_max": 12})
    ctx.run_state["browser_session"] = "abc"
    ctx.run_state["browser_refs"] = [{"ref": "e1", "role": "button", "name": "出牌"}]
    seen = _step_env(monkeypatch, _step_data())
    await bst.run_browser_session_tool(ctx, "browser_step",
                                       {"action": "click", "target": "e1",
                                        "expect": {"type": "url_matches", "value": "/orders*"}})
    assert seen[0][1]["timeout_ms"] == bst.STEP_TIMEOUT_MS_MAX          # 夹在上限，不漂
    assert seen[0][1]["interactive_max"] == 12
    assert seen[0][1]["expect"] == {"type": "url_matches", "value": "/orders*"}

    plain = Ctx(cfg={"agent.browser_expect_timeout_ms": 1})
    plain.run_state["browser_session"] = "abc"
    seen2 = _step_env(monkeypatch, _step_data())
    await bst.run_browser_session_tool(plain, "browser_step",
                                       {"action": "click", "target": "e1"})
    assert seen2[0][1]["timeout_ms"] == bst.STEP_TIMEOUT_MS_MIN          # 也夹在下限
    assert seen2[0][1]["expect"] is None                                 # 没给就不判定
    assert seen2[0][1]["ref"] == "e1" and seen2[0][1]["path"] == ""


async def test_navigate_step_sends_path_and_keeps_origin_local(monkeypatch):
    """US-17：navigate 走 `path` 字段（sidecar 拼回会话 origin），`ref` 留空免得被当元素动作。"""
    ctx = Ctx()
    ctx.run_state["browser_session"] = "abc"
    seen = _step_env(monkeypatch, _step_data(url="http://192.168.65.254:8241/orders/7"))
    await bst.run_browser_session_tool(ctx, "browser_step",
                                       {"action": "navigate", "target": "/orders/7"})
    assert seen[0][1]["path"] == "/orders/7"
    assert seen[0][1]["ref"] == ""
    assert ctx.run_state["browser_log"][-1]["target"] == "/orders/7"


def _png(width: int = 1280, height: int = 800) -> bytes:
    """够骗过 `png_size` 的最小 PNG 头（与 tests/test_browser_shots.py 同形）。

    宽高默认取 1280×800，也就是 sidecar 的视口（`browser/app.py::VIEWPORT_WIDTH`）：
    落盘服务报出的宽高读的是 IHDR 而不是 sidecar 自报的字段，两者对不上就是前端坐标换算的坑。
    """
    return (b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR"
            + struct.pack(">II", width, height) + b"\x00" * 14)


def _shot_b64() -> str:
    return base64.b64encode(_png()).decode("ascii")


def _shot_env(monkeypatch, *, replies=None, shot=None):
    """`_sidecar_step` + `_sidecar_screenshot` 两个替身；截图面被叫了几次要数得出来。"""
    shots_seen = []

    async def fake_shot(sid):
        shots_seen.append(sid)
        return shot if shot is not None else ({"ok": True, "screenshot_b64": _shot_b64(),
                                               "screenshot_width": 1280,
                                               "screenshot_height": 800}, "")

    monkeypatch.setattr(bst, "fetch_app", lambda c: _aget(_app()))
    monkeypatch.setattr(bst, "_sidecar_step",
                        lambda sid, payload: _aget((replies if replies is not None
                                                    else _step_data(), "")))
    monkeypatch.setattr(bst, "_sidecar_screenshot", fake_shot)
    return shots_seen


def _no_shot(monkeypatch):
    """把补图那条面钉成「不真去取图」。

    裁定 R-26 之后动作失败也会自动补图，不验截图的失败测试若不替它，就会打真 httpx、
    并把一张 PNG 落进真的 data 目录——测试要的只是那一步的形状与账。
    """
    seen: list[str] = []

    async def skip(sid):
        seen.append(sid)
        return None, "本测试不验截图"

    monkeypatch.setattr(bst, "_sidecar_screenshot", skip)
    return seen


async def test_failed_assertion_attaches_a_scene_shot(monkeypatch, tmp_path):
    """US-05：断言不过的那一刻要看现场，落盘沿用 preview_page 那套服务（US-23）。"""
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path))
    ctx = Ctx()
    ctx.run_state["browser_session"] = "abc"
    ctx.run_state["browser_refs"] = [{"ref": "e1", "role": "button", "name": "出牌"}]
    seen = _shot_env(monkeypatch, replies=_step_data(passed=False, note="页面文本未包含「已创建」"))
    out = await bst.run_browser_session_tool(
        ctx, "browser_step", {"action": "click", "target": "e1",
                              "expect": {"type": "text_contains", "value": "已创建"}})
    assert out["ok"] is True and out["passed"] is False
    assert out["screenshot"]["url"].startswith("/api/browser/shots/7/")
    assert out["screenshot"]["width"] == 1280 and out["screenshot"]["bytes"] == len(_png())
    assert (tmp_path / "browser_shots" / "7" / out["screenshot"]["url"].rsplit("/", 1)[1]).is_file()
    assert seen == ["abc"]


async def test_passing_assertion_attaches_no_shot(monkeypatch):
    """一张 1280×800 的图不便宜：通过时不附图是规格 §4 的决策，不是遗漏。"""
    ctx = Ctx()
    ctx.run_state["browser_session"] = "abc"
    ctx.run_state["browser_refs"] = [{"ref": "e1", "role": "button", "name": "出牌"}]
    seen = _shot_env(monkeypatch, replies=_step_data(passed=True, note="页面文本未包含「已创建」"))
    out = await bst.run_browser_session_tool(
        ctx, "browser_step", {"action": "click", "target": "e1",
                              "expect": {"type": "text_contains", "value": "已创建"}})
    assert out["passed"] is True and "screenshot" not in out
    assert seen == []


async def test_explicit_screenshot_attaches_even_without_an_assertion(monkeypatch, tmp_path):
    """`screenshot: true` 是模型的显式请求：没给 expect、动作也成功也要给图。"""
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path))
    ctx = Ctx()
    ctx.run_state["browser_session"] = "abc"
    ctx.run_state["browser_refs"] = [{"ref": "e1", "role": "button", "name": "出牌"}]
    seen = _shot_env(monkeypatch, replies=_step_data())
    out = await bst.run_browser_session_tool(
        ctx, "browser_step", {"action": "click", "target": "e1", "screenshot": True})
    assert "passed" not in out and out["screenshot"]["height"] == 800
    assert seen == ["abc"]


async def test_action_failure_attaches_a_scene_shot(monkeypatch, tmp_path):
    """裁定 R-26：`ok:false`（点不到 / 被遮挡 / 动作执行失败）同样自动附一张现场图。

    规格 §5.3 的表格里「元素不可点击，可能被遮挡」那一行本来就带 `screenshot`——被遮挡这件事
    只有看图才说得清，新清单说不出「是谁盖住了它」。与上面那条断言失败的测试分开钉：
    两个形状各管各的，一条测试不该同时管两件事。
    """
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path))
    ctx = Ctx()
    ctx.run_state["browser_session"] = "abc"
    ctx.run_state["browser_refs"] = [{"ref": "e1", "role": "button", "name": "出牌"}]
    seen = _shot_env(monkeypatch, replies={"ok": False, "error": "元素不可点击，可能被遮挡",
                                           "interactive": _interactive(), "steps_charged": True})
    out = await bst.run_browser_session_tool(
        ctx, "browser_step", {"action": "click", "target": "e1"})
    assert out["ok"] is False
    assert out["screenshot"]["url"].startswith("/api/browser/shots/7/")
    assert out["screenshot"]["bytes"] == len(_png())
    assert seen == ["abc"]
    # 补图仍然不是形状的一部分：`ok:false` 该有的账与清单都还在
    assert out["steps_charged"] is True and out["interactive"]


async def test_a_plain_successful_step_attaches_no_shot(monkeypatch):
    """「其余情况不附」仍在（token 成本）：没给 expect、也没要 screenshot 的普通一步不带图。"""
    ctx = Ctx()
    ctx.run_state["browser_session"] = "abc"
    ctx.run_state["browser_refs"] = [{"ref": "e1", "role": "button", "name": "出牌"}]
    seen = _shot_env(monkeypatch)
    out = await bst.run_browser_session_tool(
        ctx, "browser_step", {"action": "click", "target": "e1"})
    assert out["ok"] is True and "passed" not in out
    assert "screenshot" not in out
    assert seen == []


async def test_screenshot_asked_on_action_failure_still_attaches(monkeypatch, tmp_path):
    """`screenshot: true` 显式要求时仍附——这一条在 R-26 之后与自动补图重叠，仍单独钉住。"""
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path))
    ctx = Ctx()
    ctx.run_state["browser_session"] = "abc"
    ctx.run_state["browser_refs"] = [{"ref": "e1", "role": "button", "name": "出牌"}]
    seen = _shot_env(monkeypatch, replies={"ok": False, "error": "元素不可点击，可能被遮挡",
                                          "steps_charged": True})
    got = await bst.run_browser_session_tool(
        ctx, "browser_step", {"action": "click", "target": "e1", "screenshot": True})
    assert got["ok"] is False and got["screenshot"]["bytes"] == len(_png())
    assert seen == ["abc"]


async def test_capturing_the_shot_costs_no_step(monkeypatch, tmp_path):
    """补图走 screenshot 端点：它既不 touch 也不计步，两步账（1 次动作 + 1 次补图）必须是 1。"""
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path))
    ctx = Ctx()
    ctx.run_state["browser_session"] = "abc"
    ctx.run_state["browser_refs"] = [{"ref": "e1", "role": "button", "name": "出牌"}]
    ctx.run_state["browser_steps_used"] = 4
    _shot_env(monkeypatch, replies=_step_data(passed=False, note="不匹配"))
    out = await bst.run_browser_session_tool(
        ctx, "browser_step", {"action": "click", "target": "e1",
                              "expect": {"type": "text_contains", "value": "已创建"}})
    assert out["screenshot"] and out["steps_charged"] is True
    assert ctx.run_state["browser_steps_used"] == 5
    assert out["steps_left"] == bst.DEFAULT_MAX_STEPS - 5


async def test_missing_shot_says_so_instead_of_failing_the_step(monkeypatch):
    """补图失败不该把已经拿到手的断言结论一起带走（模型要的是 passed 与现场说明）。"""
    ctx = Ctx()
    ctx.run_state["browser_session"] = "abc"
    ctx.run_state["browser_refs"] = [{"ref": "e1", "role": "button", "name": "出牌"}]
    _shot_env(monkeypatch, replies=_step_data(passed=False, note="不匹配"),
              shot=(None, "浏览器服务不可用"))
    out = await bst.run_browser_session_tool(
        ctx, "browser_step", {"action": "click", "target": "e1",
                              "expect": {"type": "text_contains", "value": "已创建"}})
    assert out["ok"] is True and out["passed"] is False
    assert out["screenshot"] is None
    assert "截图" in out["screenshot_note"]


async def test_a_broken_image_does_not_become_a_step_failure(monkeypatch, tmp_path):
    """落盘炸了（不是 PNG / 超预算）只说「这张没图」，不把断言结论降级成工具失败。"""
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path))
    ctx = Ctx()
    ctx.run_state["browser_session"] = "abc"
    ctx.run_state["browser_refs"] = [{"ref": "e1", "role": "button", "name": "出牌"}]
    _shot_env(monkeypatch, replies=_step_data(passed=False, note="不匹配"),
              shot=({"ok": True, "screenshot_b64": "###not base64###"}, ""))
    out = await bst.run_browser_session_tool(
        ctx, "browser_step", {"action": "click", "target": "e1",
                              "expect": {"type": "text_contains", "value": "已创建"}})
    assert out["ok"] is True and out["passed"] is False and out["screenshot"] is None


async def test_interactive_max_shrinks_the_list_for_both_tools(monkeypatch):
    """遗留 #2：配置只发给 sidecar、后端仍按常量 40 切片的话，调小不省 token、调大被静默挡回。

    清单条数是直接决定进模型上下文 token 量的那把尺，配置必须真的生效在这一刀上。
    """
    ctx = Ctx(cfg={"agent.browser_interactive_max": 12})
    monkeypatch.setattr(bst, "fetch_app", lambda c: _aget(_app()))
    monkeypatch.setattr(bst, "_sidecar_open",
                        lambda url, **kw: _aget((_session_ok(interactive=_interactive(60)), "")))
    out = await bst.run_browser_session_tool(ctx, "browser_open", {})
    assert len(out["interactive"]) == 12

    ctx2 = Ctx(cfg={"agent.browser_interactive_max": 12})
    ctx2.run_state["browser_session"] = "abc"
    _step_env(monkeypatch, _step_data(interactive=_interactive(60), interactive_truncated=True))
    got = await bst.run_browser_session_tool(ctx2, "browser_step",
                                             {"action": "click", "target": "e1"})
    assert len(got["interactive"]) == 12
    assert got["interactive_truncated"] is True


async def test_empty_console_new_does_not_replay_the_opening_errors(monkeypatch):
    """遗留 #4：`data.get("console_new") or data.get("console")` 把「本步新增为空」与
    「字段不存在」混为一谈。实现 `_step` 后两份都可能带 `console`，届时每步都会重播首屏的
    旧错误，US-04 那个「这个错是不是我这一步搞出来的」的核心信号就没了。
    """
    ctx = Ctx()
    ctx.run_state["browser_session"] = "abc"
    ctx.run_state["browser_refs"] = [{"ref": "e1", "role": "button", "name": "出牌"}]
    _step_env(monkeypatch, _step_data(console_new=[],
                                      console=[{"level": "error", "text": "首屏就有的旧错误"}]))
    out = await bst.run_browser_session_tool(ctx, "browser_step",
                                             {"action": "click", "target": "e1"})
    assert out["console_new"] == []
    assert "首屏就有的旧错误" not in json.dumps(out, ensure_ascii=False)


async def test_open_without_console_new_reports_its_full_console(monkeypatch):
    """存在性判定的另一头：`POST /sessions` 只回全量 `console`，那一面仍要当基线报出去。"""
    monkeypatch.setattr(bst, "fetch_app", lambda c: _aget(_app()))
    monkeypatch.setattr(bst, "_sidecar_open", lambda url, **kw: _aget(
        (_session_ok(console=[{"level": "error", "text": "首屏报错"}]), "")))
    out = await bst.run_browser_session_tool(Ctx(), "browser_open", {})
    assert [i["text"] for i in out["console_new"]] == ["首屏报错"]


async def test_page_errors_new_reaches_the_tool_result_through_the_gate(monkeypatch):
    """裁定 R-27：`page_errors_new` 必须出现在工具返回值里，且过同一道清洗闸。

    它是「我这一步把页面点崩了」这个最重要的失败信号（未捕获异常），此前对模型完全不可见；
    异常原文内嵌 `_connect_url` 重写后的容器 IP 与栈里的凭据，所以 host 清洗、遮断、裁剪
    三样一样不能少——少一样就是给模型上下文开了一条旁路。
    """
    ctx = Ctx()
    ctx.run_state["browser_session"] = "abc"
    ctx.run_state["browser_refs"] = [{"ref": "e1", "role": "button", "name": "出牌"}]
    monkeypatch.setattr(bst, "browser_target_host", lambda: "host.docker.internal")
    _step_env(monkeypatch, _step_data(page_errors_new=[
        "Uncaught TypeError: 点崩了 at http://192.168.65.254:8241/app.js:1:9",
        "Uncaught Error: token=abc123def456ghi 泄漏在栈里"]))
    out = await bst.run_browser_session_tool(ctx, "browser_step",
                                             {"action": "click", "target": "e1"})
    assert len(out["page_errors_new"]) == 2
    blob = json.dumps(out, ensure_ascii=False)
    assert "192.168.65.254" not in blob and "abc123def456ghi" not in blob
    assert "localhost:8241" in out["page_errors_new"][0]
    assert out["redacted"] >= 1
    assert "点崩了" in out["page_errors_new"][0]        # 清洗之后原文仍读得懂


async def test_page_errors_new_is_capped_at_ten_items(monkeypatch):
    """上限值不许漂：page_errors 10 条（与 sidecar 的 shape_result 同一条尺）。"""
    ctx = Ctx()
    ctx.run_state["browser_session"] = "abc"
    ctx.run_state["browser_refs"] = [{"ref": "e1", "role": "button", "name": "出牌"}]
    _step_env(monkeypatch, _step_data(page_errors_new=[f"boom {i}" for i in range(30)]))
    out = await bst.run_browser_session_tool(ctx, "browser_step",
                                             {"action": "click", "target": "e1"})
    assert out["page_errors_new"] == [f"boom {i}" for i in range(10)]


async def test_step_does_not_replay_page_errors_from_the_full_snapshot(monkeypatch):
    """与 console 同一头：step 带空 `page_errors_new` 时不许退回全量 `page_errors` 重播首屏崩溃。"""
    ctx = Ctx()
    ctx.run_state["browser_session"] = "abc"
    ctx.run_state["browser_refs"] = [{"ref": "e1", "role": "button", "name": "出牌"}]
    _step_env(monkeypatch, _step_data(page_errors_new=[],
                                      page_errors=["Uncaught TypeError: 首屏就崩了"]))
    out = await bst.run_browser_session_tool(ctx, "browser_step",
                                             {"action": "click", "target": "e1"})
    assert out["page_errors_new"] == []
    assert "首屏就崩了" not in json.dumps(out, ensure_ascii=False)


async def test_open_reports_its_full_page_errors_under_the_new_key(monkeypatch):
    """两条路径字段名不同是 sidecar 那一侧的事；后端给模型的仍是同一族键，open 读全量当基线。"""
    monkeypatch.setattr(bst, "fetch_app", lambda c: _aget(_app()))
    monkeypatch.setattr(bst, "_sidecar_open", lambda url, **kw: _aget(
        (_session_ok(page_errors=["Uncaught TypeError: 首屏报错"]), "")))
    out = await bst.run_browser_session_tool(Ctx(), "browser_open", {})
    assert out["page_errors_new"] == ["Uncaught TypeError: 首屏报错"]


async def test_step_result_describes_the_step_it_just_ran(monkeypatch):
    """规格 §5.2：step 的返回值自带 `action` / `target`（/ 给了就带 `expect`）。

    步骤卡（Task 7）与收口报告都要按步读，只给一份页面状态的话「这一步是对哪个 ref 做的」
    就得回头翻上一条消息记录——那是可以省掉的一次歧义。
    """
    ctx = Ctx()
    ctx.run_state["browser_session"] = "abc"
    ctx.run_state["browser_refs"] = [{"ref": "e7", "role": "button", "name": "提交订单"}]
    _step_env(monkeypatch, _step_data(passed=False, note="不匹配"))
    out = await bst.run_browser_session_tool(
        ctx, "browser_step", {"action": "click", "target": "e7",
                              "expect": {"type": "text_contains", "value": "订单已创建"}})
    assert out["action"] == "click" and out["target"] == "e7"
    assert out["expect"] == {"type": "text_contains", "value": "订单已创建"}

    plain = Ctx()
    plain.run_state["browser_session"] = "abc"
    plain.run_state["browser_refs"] = [{"ref": "e7", "role": "button", "name": "提交订单"}]
    _step_env(monkeypatch, _step_data())
    got = await bst.run_browser_session_tool(plain, "browser_step",
                                             {"action": "click", "target": "e7"})
    assert got["action"] == "click" and got["target"] == "e7"
    assert "expect" not in got                        # 没给断言就不假装判过

    # 失败的那一步也要自述：步骤卡对成功与失败用同一份字段渲染，否则失败卡只能显示一句原因。
    failed = Ctx()
    failed.run_state["browser_session"] = "abc"
    failed.run_state["browser_refs"] = [{"ref": "e7", "role": "button", "name": "提交订单"}]
    monkeypatch.setattr(bst, "fetch_app", lambda c: _aget(_app()))
    monkeypatch.setattr(bst, "_sidecar_step", lambda sid, payload: _aget(
        ({"ok": False, "error": "元素不可点击或动作失败: timeout", "steps_charged": True}, "")))
    _no_shot(monkeypatch)
    bad = await bst.run_browser_session_tool(failed, "browser_step",
                                             {"action": "click", "target": "e7"})
    assert bad["ok"] is False and bad["action"] == "click" and bad["target"] == "e7"


async def test_open_then_steps_report_continuous_seq_in_the_close_report(monkeypatch):
    """open→step→step→close 的账要连得上：`seq` 连续、`steps_used` 与扣费一致、报告说得出步数。"""
    deleted = []
    monkeypatch.setattr(bst, "fetch_app", lambda c: _aget(_app()))
    monkeypatch.setattr(bst, "_sidecar_open", lambda url, **kw: _aget((_session_ok(), "")))
    monkeypatch.setattr(bst, "_sidecar_delete", lambda sid: _aget(deleted.append(sid) or True))
    ctx = Ctx()
    await bst.run_browser_session_tool(ctx, "browser_open", {"path": "/orders"})
    _step_env(monkeypatch, _step_data())
    await bst.run_browser_session_tool(ctx, "browser_step", {"action": "click", "target": "e1"})
    _step_env(monkeypatch, _step_data(passed=False, note="页面文本未包含「已创建」"))
    monkeypatch.setattr(bst, "_sidecar_screenshot", lambda sid: _aget((None, "服务不可用")))
    await bst.run_browser_session_tool(
        ctx, "browser_step", {"action": "click", "target": "e2",
                              "expect": {"type": "text_contains", "value": "已创建"}})
    out = await bst.run_browser_session_tool(ctx, "browser_close", {})
    assert [s["seq"] for s in out["report"]] == [0, 1, 2]
    assert out["steps_used"] == 2 == ctx.run_state["browser_steps_used"]
    assert out["steps_failed"] == 1
    assert "2" in out["hint"]                          # 说得出失败在第几步
    assert deleted == ["sid-123"]


async def test_close_report_counts_failed_actions_without_calling_them_assertions(monkeypatch):
    """发现项 I-1 的收口那一头：点不动进步数账、也进报告，但不进 `steps_failed`。

    `steps_failed` 只数 `passed is False`（断言未通过），report 项另带 `ok` 供 Task 7 分色——
    两个数各管各的账，读报告的人因此能区分「12 次点不动」与「12 次断言不过」，
    而这两件事在修复前的报告里都只体现为「这一步没进报告」。
    """
    replies = [{"ok": False, "error": "元素不可点击或动作失败: timeout",
                "url": "http://192.168.65.254:8241/orders", "interactive": _interactive(),
                "steps_charged": True},
               {"ok": False, "error": "ref 无效或页面已变化，请用最新清单里的 ref",
                "url": "http://192.168.65.254:8241/orders", "interactive": _interactive(),
                "steps_charged": True},
               _step_data(passed=False, note="页面文本未包含「已创建」")]

    async def fake_step(sid, payload):
        return replies.pop(0), ""

    monkeypatch.setattr(bst, "fetch_app", lambda c: _aget(_app()))
    monkeypatch.setattr(bst, "_sidecar_open", lambda url, **kw: _aget((_session_ok(), "")))
    monkeypatch.setattr(bst, "_sidecar_delete", lambda sid: _aget(True))
    monkeypatch.setattr(bst, "_sidecar_step", fake_step)
    monkeypatch.setattr(bst, "_sidecar_screenshot", lambda sid: _aget((None, "本测试不验截图")))
    ctx = Ctx()
    await bst.run_browser_session_tool(ctx, "browser_open", {"path": "/orders"})
    for args in ({"action": "click", "target": "e1"}, {"action": "click", "target": "e9"},
                 {"action": "click", "target": "e1",
                  "expect": {"type": "text_contains", "value": "已创建"}}):
        await bst.run_browser_session_tool(ctx, "browser_step", args)
    out = await bst.run_browser_session_tool(ctx, "browser_close", {})
    assert out["steps_used"] == 3 == ctx.run_state["browser_steps_used"]
    assert out["steps_total"] == 4                      # open 那条 + 三步全部走过
    assert out["steps_failed"] == 1                     # 只有断言未通过那一步
    assert [s["seq"] for s in out["report"]] == [0, 1, 2, 3]
    assert [s["ok"] for s in out["report"]] == [True, False, False, True]
    assert [s["passed"] for s in out["report"]] == [None, None, None, False]
    assert [s["target"] for s in out["report"]] == ["/orders", "e1", "e9", "e1"]
    assert "timeout" in out["report"][1]["error"]       # 失败原因到了读报告的人手上
    assert "ref" in out["report"][2]["error"]
    assert "error" not in out["report"][0] and "error" not in out["report"][3]
    assert "均通过" not in out["hint"] and "3" in out["hint"]
    assert "另有 2 步动作未成功" in out["hint"]          # 两本账在汇总行里都露头
    assert "192.168.65.254" not in json.dumps(out, ensure_ascii=False)


async def test_close_report_carries_the_assertion_note(monkeypatch):
    """裁定 R-31 第 2 条：断言未通过那一步的 `note` 也要进 log 与 report。

    改前只有动作失败带 `error`，断言失败在 report 里什么也没说——步骤卡因此「点不动那步有红字
    原因、断言没过那步一片空白」。note 与 error 同源（sidecar 自由文本），写日志前已过同一把
    清洗闸，报告里再过一遍 `_redact` 是纵深防御。
    """
    replies = [_step_data(passed=True, note="页面文本未包含「已创建」"),
               _step_data(passed=False,
                          note="URL 不匹配：实际停在 http://192.168.65.254:8241/orders")]

    async def fake_step(sid, payload):
        return replies.pop(0), ""

    monkeypatch.setattr(bst, "fetch_app", lambda c: _aget(_app()))
    monkeypatch.setattr(bst, "browser_target_host", lambda: "host.docker.internal")
    monkeypatch.setattr(bst, "_sidecar_open", lambda url, **kw: _aget((_session_ok(), "")))
    monkeypatch.setattr(bst, "_sidecar_delete", lambda sid: _aget(True))
    monkeypatch.setattr(bst, "_sidecar_step", fake_step)
    monkeypatch.setattr(bst, "_sidecar_screenshot", lambda sid: _aget((None, "本测试不验截图")))
    ctx = Ctx()
    await bst.run_browser_session_tool(ctx, "browser_open", {"path": "/orders"})
    expect = {"type": "text_contains", "value": "已创建"}
    logged = []
    for _ in range(2):
        out = await bst.run_browser_session_tool(
            ctx, "browser_step", {"action": "click", "target": "e1", "expect": expect})
        # 写进账的那一句，就是模型这一步看到的那一句（两处各写一遍迟早分家）
        logged.append(ctx.run_state["browser_log"][-1].get("note"))
    assert logged[0] is None and logged[1] == out["note"]
    close = await bst.run_browser_session_tool(ctx, "browser_close", {})
    report = close["report"]
    assert "note" not in report[1]                        # 断言通过的那步不假装有失败说明
    assert report[2]["passed"] is False
    assert "URL 不匹配" in report[2]["note"]
    assert "localhost:8241/orders" in report[2]["note"]    # 换回用户看得见的那个地址
    assert "192.168.65.254" not in json.dumps(close, ensure_ascii=False)


# --------------------------------------------------------------------------- browser_close

async def test_close_reports_failed_steps(monkeypatch):
    ctx = Ctx()
    ctx.run_state["browser_session"] = "abc"
    ctx.run_state["browser_log"] = [
        {"seq": 1, "action": "click", "target": "e1", "passed": True},
        {"seq": 2, "action": "click", "target": "e5", "passed": False}]
    called = []
    monkeypatch.setattr(bst, "_sidecar_delete", lambda sid: _aget(called.append(sid) or True))
    out = await bst.run_browser_session_tool(ctx, "browser_close", {})
    assert out["steps_total"] == 2
    assert out["steps_failed"] == 1
    assert "2" in out["hint"]
    assert called == ["abc"]
    assert ctx.run_state["browser_session"] is None


async def test_close_reports_all_assertions_passed(monkeypatch):
    ctx = Ctx()
    ctx.run_state["browser_session"] = "abc"
    ctx.run_state["browser_log"] = [
        {"seq": 0, "action": "open", "target": "/", "passed": None},
        {"seq": 1, "action": "click", "target": "e1", "ok": True, "passed": True}]
    monkeypatch.setattr(bst, "_sidecar_delete", lambda sid: _aget(True))
    out = await bst.run_browser_session_tool(ctx, "browser_close", {})
    assert out["ok"] is True and out["closed"] is True and out["steps_failed"] == 0
    assert "均通过" in out["hint"]


async def test_close_hint_does_not_call_an_unasserted_run_clean(monkeypatch):
    """裁定 R-31 第 1 条（空真那一半）：从未声明 expect 的 run 不能说「所有已声明的断言均通过」。

    「未声明任何断言」与「声明的断言均通过」是两件事：前者是本步没做判定（模型自判），
    后者是走了查且全绿。字面上都为真，但模型对 `browser_close` 的默认读法只看汇总行、
    不逐条翻 report，把前者讲成后者就是凭空造出一条「已验证通过」的假结论。
    """
    ctx = Ctx()
    ctx.run_state["browser_session"] = "abc"
    ctx.run_state["browser_log"] = [
        {"seq": 0, "action": "open", "target": "/", "passed": None},
        {"seq": 1, "action": "click", "target": "e1", "ok": True, "passed": None}]
    monkeypatch.setattr(bst, "_sidecar_delete", lambda sid: _aget(True))
    out = await bst.run_browser_session_tool(ctx, "browser_close", {})
    assert out["steps_failed"] == 0 and out["steps_action_failed"] == 0
    assert "均通过" not in out["hint"]
    assert "未声明任何断言" in out["hint"]


async def test_close_hint_counts_action_failures_even_without_any_expect(monkeypatch):
    """裁定 R-31 第 1 条（该组合此前无测试）：动作失败拉满、一条 expect 都没声明。

    这是最贵的一种失真——`steps_failed` 恒 0、断言层确实「全绿」，而 12 次点不动只在 report 的
    `ok:false` 项里逐条躺着。hint 必须另报 `steps_action_failed`，否则汇总行讲成一路干净。
    """
    async def fake_step(sid, payload):
        return ({"ok": False, "error": "元素不可点击或动作失败: timeout",
                 "url": "http://192.168.65.254:8241/orders", "interactive": _interactive(),
                 "steps_charged": True}), ""

    monkeypatch.setattr(bst, "fetch_app", lambda c: _aget(_app()))
    monkeypatch.setattr(bst, "_sidecar_open", lambda url, **kw: _aget((_session_ok(), "")))
    monkeypatch.setattr(bst, "_sidecar_delete", lambda sid: _aget(True))
    monkeypatch.setattr(bst, "_sidecar_step", fake_step)
    _no_shot(monkeypatch)
    ctx = Ctx()
    await bst.run_browser_session_tool(ctx, "browser_open", {"path": "/orders"})
    for _ in range(bst.DEFAULT_MAX_STEPS):     # 一直点到步数额度用尽
        await bst.run_browser_session_tool(ctx, "browser_step", {"action": "click", "target": "e1"})
    out = await bst.run_browser_session_tool(ctx, "browser_close", {})
    assert out["steps_action_failed"] == bst.DEFAULT_MAX_STEPS
    assert out["steps_failed"] == 0                       # 两本账不串（发现项 I-1 的口径）
    assert out["steps_total"] == bst.DEFAULT_MAX_STEPS + 1
    assert "均通过" not in out["hint"]
    assert f"另有 {bst.DEFAULT_MAX_STEPS} 步动作未成功" in out["hint"]
    assert "未声明任何断言" in out["hint"]                 # 两个缺口都要说出口


async def test_close_redacts_report_targets(monkeypatch):
    """收口报告里的 target 也是自由文本：模型可能把凭据当目标名写进日志。"""
    ctx = Ctx()
    ctx.run_state["browser_session"] = "abc"
    ctx.run_state["browser_log"] = [{"seq": 1, "action": "fill", "target": "pwd=hunter2x",
                                     "passed": True}]
    monkeypatch.setattr(bst, "_sidecar_delete", lambda sid: _aget(True))
    out = await bst.run_browser_session_tool(ctx, "browser_close", {})
    assert "hunter2x" not in json.dumps(out, ensure_ascii=False)


async def test_close_without_session_does_not_call_sidecar(monkeypatch):
    called = []
    monkeypatch.setattr(bst, "_sidecar_delete", lambda sid: _aget(called.append(sid) or True))
    out = await bst.run_browser_session_tool(Ctx(), "browser_close", {})
    assert out["ok"] is True and out["closed"] is False and out["steps_total"] == 0
    assert called == []


async def test_close_session_failure_is_swallowed(monkeypatch):
    """run 收口不该拖住对话结束（US-21）：DELETE 抛异常也只记日志，TTL 会兜底。"""
    ctx = Ctx()
    ctx.run_state["browser_session"] = "abc"
    ctx.run_state["browser_log"] = []

    async def die(sid):
        raise RuntimeError("sidecar down")

    monkeypatch.setattr(bst, "_sidecar_delete", die)
    await bst.close_browser_session(ctx)      # 不抛
    assert ctx.run_state["browser_session"] is None


async def test_close_browser_session_without_session_is_a_noop(monkeypatch):
    called = []
    monkeypatch.setattr(bst, "_sidecar_delete", lambda sid: _aget(called.append(sid) or True))
    await bst.close_browser_session(Ctx())
    await bst.close_browser_session(None)     # run 在 ctx 之前就炸了也不该有第二步异常
    assert called == []


async def test_sidecar_delete_swallows_transport_errors(monkeypatch):
    _c, holder = _fake_httpx(error=httpx.ConnectError("refused"))
    monkeypatch.setattr(bst, "httpx", holder)
    assert await bst._sidecar_delete("sid-1") is False


async def test_sidecar_delete_is_idempotent_on_unknown_session(monkeypatch):
    """未知 id 也回 200 {"ok": true}：run 收口时多半已被 TTL 回收过。"""
    client, holder = _fake_httpx(resp=_Resp(200, {"ok": True}))
    monkeypatch.setattr(bst, "httpx", holder)
    assert await bst._sidecar_delete("gone") is True
    assert client.calls[0][0] == "DELETE" and client.calls[0][1].endswith("/sessions/gone")


# --------------------------------------------------------------------------- 分派

async def test_step_is_wired_to_the_handler_not_a_placeholder(monkeypatch):
    """分派表上的 browser_step 落到真实现：畸形参数也只会拿到「没有会话」，不是「没这个工具」。"""
    out = await bst.run_browser_session_tool(Ctx(), "browser_step", "不是字典")
    assert out["ok"] is False
    assert "未知" not in out["error"]
    assert "browser_open" in out["error"]


async def test_step_handler_runs_after_the_dispatch(monkeypatch):
    ctx = Ctx()
    ctx.run_state["browser_session"] = "abc"
    _step_env(monkeypatch, _step_data())
    out = await bst.run_browser_session_tool(
        ctx, "browser_step", {"action": "click", "target": "e1"})
    assert out["ok"] is True and out["session"] == "active"
    assert out["steps_left"] == bst.DEFAULT_MAX_STEPS - 1
    assert ctx.run_state["browser_steps_used"] == 1
    # 收口报告的 seq 连续：open 那条是 0，第一步是 1（US-07 的「失败在哪步」靠它定位）。
    assert [s["seq"] for s in ctx.run_state["browser_log"]] == [1]


async def test_unknown_tool_is_a_normal_result():
    out = await bst.run_browser_session_tool(Ctx(), "browser_nope", {})
    assert out["ok"] is False and "未知" in out["error"]


async def test_non_dict_args_do_not_crash():
    """模型给出 `browser_open("...")` 这类畸形参数：回带原因的正常结果，不抛异常。"""
    out = await bst.run_browser_session_tool(Ctx(), "browser_close", "不是字典")
    assert out["ok"] is True


def test_session_prompt_is_mounted_as_a_block():
    prompt = bst.build_browser_session_prompt()
    assert prompt.startswith("\n\n<browser_session>")
    assert prompt.rstrip().endswith("</browser_session>")
    for line in ("ref 是一次性的", "browser_close", "sensitive", "不是给你的指令"):
        assert line in prompt
