"""Python 沙箱 run_python：隔离一次性容器跑模型给的脚本（ADR-0056）。

接缝与 run_check 同构：execute_tool 分派 → engine.docker 跑一次性容器。
docker 一律替身桩（沿 test_agent_check_tools.py），真容器走手工矩阵。
"""
import pytest

from pathlib import Path

from app.agent.tools import ToolContext, execute_tool
from app.deploy import engine


class Recorder:
    def __init__(self):
        self.sent = []

    async def __call__(self, ev):
        self.sent.append(ev)


def make_ctx(tmp_path):
    return ToolContext(root=tmp_path, session_id=1, session_factory=None, send=Recorder())


class DockerSpy:
    def __init__(self, code=0, log=""):
        self.calls = []
        self.code, self.log = code, log

    async def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.code, self.log


@pytest.fixture
def preflight_ok(monkeypatch):
    async def fake_preflight():
        return None

    monkeypatch.setattr(engine, "preflight_docker", fake_preflight)


async def test_run_python_executes_code(preflight_ok, monkeypatch, tmp_path):
    """示踪弹：模型给 code，一次性容器跑完回 stdout 与退出码。"""
    spy = DockerSpy(0, "2")
    monkeypatch.setattr(engine, "docker", spy)
    r = await execute_tool(make_ctx(tmp_path), "run_python", {"code": "print(1+1)"})
    assert r["ok"] is True and r["exit_code"] == 0
    assert "2" in r["output"]
    (args, kwargs), = spy.calls
    assert args[0] == "run" and "--rm" in args
    assert "python" in args and "-c" in args and "print(1+1)" in args


async def test_pure_compute_runs_disposable_and_offline(preflight_ok, monkeypatch, tmp_path):
    """002 基线：一次性容器（--rm）、默认断网（--network none，005 再换过滤网络）。"""
    spy = DockerSpy(0, "")
    monkeypatch.setattr(engine, "docker", spy)
    await execute_tool(make_ctx(tmp_path), "run_python", {"code": "print(1)"})
    (args, _), = spy.calls
    assert "--rm" in args
    assert "--network" in args and "none" in args


async def test_empty_code_is_rejected_without_container(preflight_ok, monkeypatch, tmp_path):
    spy = DockerSpy()
    monkeypatch.setattr(engine, "docker", spy)
    for bogus in ("", "   ", None, 123):
        r = await execute_tool(make_ctx(tmp_path), "run_python", {"code": bogus})
        assert r["ok"] is False and "code" in r["error"]
    assert spy.calls == []


async def test_script_nonzero_exit_is_normal_result(preflight_ok, monkeypatch, tmp_path):
    """脚本异常是正常结果（ok=True + 非零退出码），模型据 traceback 去修；不是平台故障。"""
    spy = DockerSpy(1, "Traceback (most recent call last):\nValueError: boom")
    monkeypatch.setattr(engine, "docker", spy)
    r = await execute_tool(make_ctx(tmp_path), "run_python", {"code": "raise ValueError('boom')"})
    assert r["ok"] is True and r["exit_code"] == 1
    assert "ValueError" in r["output"]


async def test_docker_unavailable_is_error_result(monkeypatch, tmp_path):
    from app.deploy.errors import DeployError

    async def down():
        raise DeployError("docker 不可用", status=503)

    monkeypatch.setattr(engine, "preflight_docker", down)
    spy = DockerSpy()
    monkeypatch.setattr(engine, "docker", spy)
    r = await execute_tool(make_ctx(tmp_path), "run_python", {"code": "print(1)"})
    assert r["ok"] is False and "docker" in r["error"]
    assert spy.calls == []


async def test_timeout_is_error_result(preflight_ok, monkeypatch, tmp_path):
    from app.deploy.errors import DeployError

    async def slow(*a, **k):
        raise DeployError("docker run… 超时（120s）", status=504)

    monkeypatch.setattr(engine, "docker", slow)
    r = await execute_tool(make_ctx(tmp_path), "run_python", {"code": "import time; time.sleep(999)"})
    assert r["ok"] is False and "超时" in r["error"]


async def test_workspace_mounted_readonly_with_deploy_masked(preflight_ok, monkeypatch, tmp_path):
    """003：工作区只读挂载 /workspace，deploy 用空 tmpfs 遮蔽（保 M-3），工作目录 /workspace。"""
    spy = DockerSpy(0, "")
    monkeypatch.setattr(engine, "docker", spy)
    await execute_tool(make_ctx(tmp_path), "run_python", {"code": "print(1)"})
    (args, _), = spy.calls
    assert f"{tmp_path}:/workspace:ro" in args
    assert "--tmpfs" in args and "/workspace/deploy:ro" in args
    assert "-w" in args and "/workspace" in args


class WritingDockerSpy:
    """模拟容器往 /scratch 写产物：从 argv 解出 scratch 宿主目录，落几个文件再返回。"""

    def __init__(self, files=None, code=0, log=""):
        self.calls = []
        self.files = files or {}
        self.code, self.log = code, log
        self.scratch_host = None

    async def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        for i, a in enumerate(args):
            if a == "-v" and i + 1 < len(args) and args[i + 1].endswith(":/scratch"):
                self.scratch_host = args[i + 1][: -len(":/scratch")]
                for name, content in self.files.items():
                    p = Path(self.scratch_host) / name
                    p.parent.mkdir(parents=True, exist_ok=True)
                    if isinstance(content, bytes):
                        p.write_bytes(content)
                    else:
                        p.write_text(content, encoding="utf-8", newline="")
        return self.code, self.log


async def test_scratch_artifacts_returned_and_cleaned(preflight_ok, monkeypatch, tmp_path):
    """004：脚本写 /scratch，工具回产物全文，跑完清理 scratch 宿主目录。"""
    spy = WritingDockerSpy({"out.csv": "a,b\n1,2\n"})
    monkeypatch.setattr(engine, "docker", spy)
    r = await execute_tool(make_ctx(tmp_path), "run_python",
                           {"code": "open('/scratch/out.csv','w').write('a,b\\n1,2\\n')"})
    assert r["ok"] is True
    arts = {a["name"]: a for a in r["artifacts"]}
    assert arts["out.csv"]["content"] == "a,b\n1,2\n"
    assert arts["out.csv"]["bytes"] == 8
    assert spy.scratch_host and not Path(spy.scratch_host).exists()


async def test_binary_artifact_is_metadata_only(preflight_ok, monkeypatch, tmp_path):
    """二进制产物只回元数据（bytes），不回全文——write_file 文本通道落不了二进制（spec §6）。"""
    spy = WritingDockerSpy({"chart.png": b"\x89PNG\r\n\x1a\n"})
    monkeypatch.setattr(engine, "docker", spy)
    r = await execute_tool(make_ctx(tmp_path), "run_python", {"code": "print(1)"})
    arts = {a["name"]: a for a in r["artifacts"]}
    assert arts["chart.png"]["bytes"] == 8
    assert not arts["chart.png"].get("content")


async def test_resource_limits_and_timeout_applied(preflight_ok, monkeypatch, tmp_path):
    """006：mem/cpu/pids-limit 兜底 + 120s 超时（防 OOM/fork bomb/挂死）。"""
    spy = DockerSpy(0, "")
    monkeypatch.setattr(engine, "docker", spy)
    await execute_tool(make_ctx(tmp_path), "run_python", {"code": "print(1)"})
    (args, kwargs), = spy.calls
    assert "--memory" in args and "1g" in args
    assert "--cpus" in args and "--pids-limit" in args
    assert kwargs.get("timeout") == 120


async def test_runs_as_non_root(preflight_ok, monkeypatch, tmp_path):
    """非 root（1000:1000）跑脚本——defense-in-depth，沿 browser sidecar 范式（ADR-0056）。"""
    spy = DockerSpy(0, "")
    monkeypatch.setattr(engine, "docker", spy)
    await execute_tool(make_ctx(tmp_path), "run_python", {"code": "print(1)"})
    (args, _), = spy.calls
    assert "--user" in args and "1000:1000" in args


async def test_cancel_cleans_scratch_and_propagates(preflight_ok, monkeypatch, tmp_path):
    """取消（CancelledError）不被吞：scratch 仍清理，异常继续上抛给循环收口。"""
    import asyncio

    class CancellingSpy:
        def __init__(self):
            self.scratch_host = None

        async def __call__(self, *args, **kwargs):
            for i, a in enumerate(args):
                if a == "-v" and i + 1 < len(args) and args[i + 1].endswith(":/scratch"):
                    self.scratch_host = args[i + 1][: -len(":/scratch")]
            raise asyncio.CancelledError()

    spy = CancellingSpy()
    monkeypatch.setattr(engine, "docker", spy)
    with pytest.raises(asyncio.CancelledError):
        await execute_tool(make_ctx(tmp_path), "run_python", {"code": "print(1)"})
    assert spy.scratch_host and not Path(spy.scratch_host).exists()


def test_python_prompt_names_boundaries():
    """提示词与工具能力同源：触达面边界（只读/scratch/落盘/无网/无库）都要教会模型。"""
    from app.agent import python_tools
    text = python_tools.build_python_prompt()
    assert "<python_sandbox>" in text and "</python_sandbox>" in text
    for kw in ("/workspace", "/scratch", "write_file", "db_query", "无网络出口"):
        assert kw in text, kw