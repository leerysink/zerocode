"""Week 3 预设自检：run_check 只能选平台写死的 preset（ADR-0046）。

三条不变式钉在这里：
- 模型给不出命令/参数/路径——未知 preset 直接拒，且**一个容器都不起**；
- 命令由平台按 stack/工程事实选（vue-tsc 有就用，没有就降级为 vite build 并如实说明）；
- 结果只报告：检查未通过是 `ok=True, passed=False`，只有平台侧故障才是 `ok=False`。
"""
import pytest

from app.agent.tools import ToolContext, execute_tool
from app.deploy import engine

APP = {"id": 7, "slug": "demo", "stack": "vue3-fastapi", "current_version": "b1"}


class Recorder:
    def __init__(self):
        self.sent = []

    async def __call__(self, ev):
        self.sent.append(ev)


def make_ctx(tmp_path):
    return ToolContext(root=tmp_path, session_id=1, session_factory=None, send=Recorder())


@pytest.fixture
def wired(monkeypatch, tmp_path):
    async def fake_fetch_app(ctx):
        return dict(APP)

    async def fake_root(sf, app):
        return tmp_path

    async def fake_preflight():
        return None

    monkeypatch.setattr("app.agent.preview_tools.fetch_app", fake_fetch_app)
    monkeypatch.setattr(engine, "_app_root", fake_root)
    monkeypatch.setattr(engine, "preflight_docker", fake_preflight)
    (tmp_path / "deploy").mkdir()
    (tmp_path / "deploy" / "dev-compose.yml").write_text("services: {}", encoding="utf-8")
    return tmp_path


class DockerSpy:
    def __init__(self, code=0, log="ok"):
        self.calls = []
        self.code, self.log = code, log

    async def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.code, self.log


def _frontend(root, scripts=None, dev_deps=None):
    import json
    (root / "frontend").mkdir(exist_ok=True)
    (root / "frontend" / "package.json").write_text(json.dumps({
        "scripts": scripts or {"dev": "vite", "build": "vite build"},
        "devDependencies": dev_deps or {},
    }), encoding="utf-8")


def _backend(root, kind="py"):
    if kind == "py":
        (root / "backend" / "app").mkdir(parents=True, exist_ok=True)
    else:
        (root / "backend").mkdir(exist_ok=True)
        (root / "backend" / "pom.xml").write_text("<project/>", encoding="utf-8")


def _run_args(spy):
    (args, kwargs), = spy.calls
    return list(args), kwargs


@pytest.mark.asyncio
async def test_frontend_typecheck_uses_vue_tsc_when_present(wired, monkeypatch):
    _frontend(wired, dev_deps={"vue-tsc": "^2.0.0"})
    spy = DockerSpy(0, "no errors")
    monkeypatch.setattr(engine, "docker", spy)
    r = await execute_tool(make_ctx(wired), "run_check", {"preset": "frontend_typecheck"})
    assert r["ok"] is True and r["passed"] is True and r["exit_code"] == 0
    args, kwargs = _run_args(spy)
    assert tuple(args[:4]) == ("compose", "-f", str(wired / "deploy" / "dev-compose.yml"), "run")
    assert "--rm" in args and "--no-deps" in args and "frontend" in args
    joined = " ".join(args)
    assert "vue-tsc --noEmit" in joined and "vite build" not in joined
    assert kwargs["cwd"] == str(wired)


@pytest.mark.asyncio
async def test_frontend_typecheck_falls_back_to_build_and_says_so(wired, monkeypatch):
    _frontend(wired)
    spy = DockerSpy(1, "src/App.vue:3:5 error TS2304: Cannot find name 'foo'")
    monkeypatch.setattr(engine, "docker", spy)
    r = await execute_tool(make_ctx(wired), "run_check", {"preset": "frontend_typecheck"})
    args, _ = _run_args(spy)
    assert "vite build" in " ".join(args) and "--outDir /tmp/" in " ".join(args)
    assert r["ok"] is True and r["passed"] is False and r["exit_code"] == 1
    assert "TS2304" in r["output"] and "降级" in r["note"]


@pytest.mark.asyncio
async def test_backend_compile_fastapi_uses_compileall(wired, monkeypatch):
    _backend(wired)
    spy = DockerSpy(0, "")
    monkeypatch.setattr(engine, "docker", spy)
    r = await execute_tool(make_ctx(wired), "run_check", {"preset": "backend_compile"})
    args, _ = _run_args(spy)
    assert "backend" in args and "compileall" in " ".join(args)
    assert r["passed"] is True


@pytest.mark.asyncio
async def test_backend_compile_springboot_uses_mvn_compile(wired, monkeypatch):
    async def sb_app(ctx):
        return {**APP, "stack": "vue3-springboot-mobile"}

    monkeypatch.setattr("app.agent.preview_tools.fetch_app", sb_app)
    _backend(wired, "maven")
    spy = DockerSpy(1, "[ERROR] cannot find symbol")
    monkeypatch.setattr(engine, "docker", spy)
    r = await execute_tool(make_ctx(wired), "run_check", {"preset": "backend_compile"})
    joined = " ".join(_run_args(spy)[0])
    assert "mvn" in joined and "compile" in joined and "-DskipTests" in joined
    assert r["passed"] is False and "cannot find symbol" in r["output"]


@pytest.mark.asyncio
async def test_unknown_preset_is_rejected_without_running_anything(wired, monkeypatch):
    spy = DockerSpy()
    monkeypatch.setattr(engine, "docker", spy)
    for bogus in ("rm -rf /", "frontend_typecheck --fix", "", None):
        r = await execute_tool(make_ctx(wired), "run_check", {"preset": bogus})
        assert r["ok"] is False and "preset" in r["error"]
    assert spy.calls == []


@pytest.mark.asyncio
async def test_missing_compose_file_tells_user_to_start_preview(wired, monkeypatch):
    (wired / "deploy" / "dev-compose.yml").unlink()
    spy = DockerSpy()
    monkeypatch.setattr(engine, "docker", spy)
    r = await execute_tool(make_ctx(wired), "run_check", {"preset": "backend_compile"})
    assert r["ok"] is False and "预览" in r["error"]
    assert spy.calls == []


@pytest.mark.asyncio
async def test_docker_unavailable_is_an_error_result(wired, monkeypatch):
    from app.deploy.errors import DeployError

    async def down():
        raise DeployError("docker 不可用", status=503)

    _backend(wired)
    monkeypatch.setattr(engine, "preflight_docker", down)
    spy = DockerSpy()
    monkeypatch.setattr(engine, "docker", spy)
    r = await execute_tool(make_ctx(wired), "run_check", {"preset": "backend_compile"})
    assert r["ok"] is False and "docker" in r["error"]
    assert spy.calls == []


@pytest.mark.asyncio
async def test_timeout_is_reported_as_error_not_as_failed_check(wired, monkeypatch):
    from app.deploy.errors import DeployError

    async def slow(*args, **kwargs):
        raise DeployError("docker compose run… 超时（900s）", status=504)

    _backend(wired)
    monkeypatch.setattr(engine, "docker", slow)
    r = await execute_tool(make_ctx(wired), "run_check", {"preset": "backend_compile"})
    assert r["ok"] is False and "超时" in r["error"]
