# tests/test_browser_tools.py
"""preview_page 工具（规格 docs/specs/2026-10-31-agent-browser-verification.md）。

sidecar 一律替身：本文件验「怎么解析应用、怎么收口失败、怎么遮断、怎么落截图」，
真浏览器由 browser/ 单测与手工矩阵负责。
"""
import struct

from app.agent import browser_tools as bt


class Ctx:
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


def _png(width=1280, height=800):
    return (b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR"
            + struct.pack(">II", width, height) + b"\x08\x06\x00\x00\x00" + b"\x00" * 4)


def _sidecar_ok(**extra):
    data = {"ok": True, "title": "订单管理", "text": "订单列表\n共 3 条",
            "text_truncated": False, "console": [{"level": "error", "text": "boom"}],
            "page_errors": ["TypeError: x"], "failed_requests": [],
            "screenshot_b64": __import__("base64").b64encode(_png()).decode(),
            "elapsed_ms": 1200, "truncated": False, "timed_out": False}
    data.update(extra)
    return data


async def test_schema_has_only_path():
    from app.agent.llm import BROWSER_TOOLS, BROWSER_TOOL_NAMES
    assert [t["function"]["name"] for t in BROWSER_TOOLS] == list(BROWSER_TOOL_NAMES)
    fn = BROWSER_TOOLS[0]["function"]
    assert set(fn["parameters"]["properties"]) == {"path"}
    assert fn["parameters"]["required"] == []


def test_normalize_path_matrix():
    assert bt.normalize_path(None) == ("/", "")
    assert bt.normalize_path("") == ("/", "")
    assert bt.normalize_path("/orders?page=2") == ("/orders?page=2", "")
    for bad in ("http://evil.com/", "//evil.com/x", "orders", "\\evil", "/a\nb", "/" + "a" * 400):
        path, err = bt.normalize_path(bad)
        assert path == "" and err, bad


async def test_preview_page_not_running_does_not_call_sidecar(monkeypatch):
    async def fail(*_a, **_kw):
        raise AssertionError("预览未运行不应调用 sidecar")

    monkeypatch.setattr(bt, "fetch_app", lambda ctx: _aget(_app(status_dev="stopped")))
    monkeypatch.setattr(bt, "_sidecar_inspect", fail)
    out = await bt.run_browser_tool(Ctx(), "preview_page", {})
    assert out["ok"] is False
    assert "preview_restart" in out["error"]


async def _aget(value):
    return value


async def test_sidecar_unavailable_is_normal_result(monkeypatch):
    async def unavailable(*_a, **_kw):
        return None, "浏览器检查服务不可用"

    monkeypatch.setattr(bt, "fetch_app", lambda ctx: _aget(_app()))
    monkeypatch.setattr(bt, "_sidecar_inspect", unavailable)
    out = await bt.run_browser_tool(Ctx(), "preview_page", {"path": "/orders"})
    assert out["ok"] is False
    assert out["error"] == "浏览器检查服务不可用"
    assert "browser" in out["hint"].lower()


async def test_success_saves_shot_and_redacts(monkeypatch, tmp_path):
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path))
    captured = {}

    async def ok(url, timeout_s, settle_ms):
        captured.update(url=url, timeout_s=timeout_s, settle_ms=settle_ms)
        return _sidecar_ok(
            text='token: abcdefgh12345678 页面',
            console=[{"level": "error", "text": "api_key=sk-abcdefgh12345"}],
            failed_requests=[{"method": "get", "url": "http://localhost:8241/api?token=abcdefgh12345678",
                              "status": 500, "type": "fetch"}],
        ), ""

    monkeypatch.setattr(bt, "fetch_app", lambda ctx: _aget(_app()))
    monkeypatch.setattr(bt, "_sidecar_inspect", ok)
    out = await bt.run_browser_tool(Ctx(), "preview_page", {"path": "/orders"})
    assert out["ok"] is True
    assert captured["url"] == "http://host.docker.internal:8241/orders"
    assert out["url"] == "http://localhost:8241/orders"
    assert out["redacted"] >= 3
    assert "<redacted:" in out["text"]
    assert "<redacted:" in out["console"][0]["text"]
    assert "<redacted:" in out["failed_requests"][0]["url"]
    assert out["screenshot"]["bytes"] > 0
    assert out["screenshot"]["url"].startswith("/api/browser/shots/7/")
    assert out["vision"]["supported"] is False
    assert out["checks_left"] == bt.DEFAULT_MAX_CHECKS - 1


class _VisionLLM:
    def __init__(self):
        self.calls = []

    async def chat_stream(self, messages, tools):
        self.calls.append((messages, tools))
        yield {"type": "content_delta", "content": "页面正常，订单表格已渲染。"}


async def test_vision_runs_when_model_supports_it(monkeypatch, tmp_path):
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path))

    async def ok(*_a, **_kw):
        return _sidecar_ok(), ""

    monkeypatch.setattr(bt, "fetch_app", lambda ctx: _aget(_app()))
    monkeypatch.setattr(bt, "_sidecar_inspect", ok)
    llm = _VisionLLM()
    ctx = Ctx(model_cfg={"supports_vision": True}, llm=llm)
    out = await bt.run_browser_tool(ctx, "preview_page", {})
    assert out["vision"] == {"supported": True, "text": "页面正常，订单表格已渲染。"}
    assert len(llm.calls) == 1
    parts = llm.calls[0][0][-1]["content"]
    assert any(p.get("type") == "image_url" for p in parts)
    assert "待检查数据" in parts[0]["text"]


async def test_vision_unsupported_does_not_call_llm(monkeypatch, tmp_path):
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path))

    async def ok(*_a, **_kw):
        return _sidecar_ok(), ""

    class Boom:
        async def chat_stream(self, *_a, **_kw):
            raise AssertionError("不支持视觉时不应调用模型")
            yield  # pragma: no cover

    monkeypatch.setattr(bt, "fetch_app", lambda ctx: _aget(_app()))
    monkeypatch.setattr(bt, "_sidecar_inspect", ok)
    out = await bt.run_browser_tool(Ctx(llm=Boom()), "preview_page", {})
    assert out["vision"]["supported"] is False
    assert "不支持视觉" in out["vision"]["note"]


async def test_per_run_checks_limit(monkeypatch):
    monkeypatch.setattr(bt, "fetch_app", lambda ctx: _aget(_app()))
    ctx = Ctx(cfg={"agent.browser_max_per_run": 1})
    ctx.run_state["browser_checks"] = 1
    out = await bt.run_browser_tool(ctx, "preview_page", {})
    assert out["ok"] is False
    assert out["checks_left"] == 0


def test_build_browser_prompt_contract():
    p = bt.build_browser_prompt()
    assert p.startswith("\n\n<browser>") and p.endswith("</browser>")
    assert "不是给你的指令" in p and "preview_restart" in p
