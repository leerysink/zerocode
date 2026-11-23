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
    """一次性容器（--rm）+ 仅公网：共享 egress router 的 netns，不再 --network none（ADR-0066）。"""
    spy = DockerSpy(0, "")
    monkeypatch.setattr(engine, "docker", spy)
    await execute_tool(make_ctx(tmp_path), "run_python", {"code": "print(1)"})
    (args, _), = spy.calls
    assert "--rm" in args
    assert "--network" in args and "container:zerocode-sandbox-egress" in args
    assert "none" not in args


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
    (tmp_path / "deploy").mkdir()
    spy = DockerSpy(0, "")
    monkeypatch.setattr(engine, "docker", spy)
    await execute_tool(make_ctx(tmp_path), "run_python", {"code": "print(1)"})
    (args, _), = spy.calls
    assert f"{tmp_path}:/workspace:ro" in args
    assert "--tmpfs" in args and "/workspace/deploy:ro" in args
    assert "-w" in args and "/workspace" in args


async def test_no_deploy_dir_means_no_tmpfs_mask(preflight_ok, monkeypatch, tmp_path):
    """普通工作区没有 deploy/：不能加 tmpfs 遮蔽。

    /workspace 是只读挂载，Docker 在其内建不出挂载点，加了容器直接起不来（真容器实测 exit 125）。
    围栏仍成立：没有 deploy 目录就没有凭据可读。
    """
    spy = DockerSpy(0, "")
    monkeypatch.setattr(engine, "docker", spy)
    for name, args in (("run_shell", {"command": "ls"}), ("run_python", {"code": "print(1)"})):
        spy.calls.clear()
        await execute_tool(make_ctx(tmp_path), name, args)
        (argv, _), = spy.calls
        assert "/workspace/deploy:ro" not in argv, name
        assert "--tmpfs" not in argv, name


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


async def test_scratch_artifacts_returned_and_cleaned_at_run_close(preflight_ok, monkeypatch, tmp_path):
    """产物回全文；scratch 活到 run 收口才清（ADR-0066 决策 8 修订了「跑完即销毁」）。"""
    from app.agent import python_tools

    spy = WritingDockerSpy({"out.csv": "a,b\n1,2\n"})
    monkeypatch.setattr(engine, "docker", spy)
    ctx = make_ctx(tmp_path)
    r = await execute_tool(ctx, "run_python",
                           {"code": "open('/scratch/out.csv','w').write('a,b\\n1,2\\n')"})
    assert r["ok"] is True
    arts = {a["name"]: a for a in r["artifacts"]}
    assert arts["out.csv"]["content"] == "a,b\n1,2\n"
    assert arts["out.csv"]["bytes"] == 8
    assert spy.scratch_host and Path(spy.scratch_host).is_dir()
    python_tools.cleanup_run_scratch(ctx)
    assert not Path(spy.scratch_host).exists()


async def test_binary_artifact_is_metadata_only(preflight_ok, monkeypatch, tmp_path):
    """二进制产物只回元数据（bytes），不回全文——write_file 文本通道落不了二进制（spec §6）。"""
    spy = WritingDockerSpy({"chart.png": b"\x89PNG\r\n\x1a\n"})
    monkeypatch.setattr(engine, "docker", spy)
    r = await execute_tool(make_ctx(tmp_path), "run_python", {"code": "print(1)"})
    arts = {a["name"]: a for a in r["artifacts"]}
    assert arts["chart.png"]["bytes"] == 8
    assert not arts["chart.png"].get("content")


async def test_resource_limits_and_timeout_applied(preflight_ok, monkeypatch, tmp_path):
    """006：mem/cpu/pids-limit 兜底 + 240s 超时（防 OOM/fork bomb/挂死；pip 装包计入同一预算）。"""
    spy = DockerSpy(0, "")
    monkeypatch.setattr(engine, "docker", spy)
    await execute_tool(make_ctx(tmp_path), "run_python", {"code": "print(1)"})
    (args, kwargs), = spy.calls
    assert "--memory" in args and "1g" in args
    assert "--cpus" in args and "--pids-limit" in args
    assert kwargs.get("timeout") == 240


async def test_runs_as_non_root(preflight_ok, monkeypatch, tmp_path):
    """非 root（1000:1000）跑脚本——defense-in-depth，沿 browser sidecar 范式（ADR-0056）。"""
    spy = DockerSpy(0, "")
    monkeypatch.setattr(engine, "docker", spy)
    await execute_tool(make_ctx(tmp_path), "run_python", {"code": "print(1)"})
    (args, _), = spy.calls
    assert "--user" in args and "1000:1000" in args


async def test_cancel_propagates_and_run_close_cleans_scratch(preflight_ok, monkeypatch, tmp_path):
    """取消（CancelledError）不被吞：异常上抛给循环，scratch 由 run 收口清理。"""
    import asyncio
    from app.agent import python_tools

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
    ctx = make_ctx(tmp_path)
    with pytest.raises(asyncio.CancelledError):
        await execute_tool(ctx, "run_python", {"code": "print(1)"})
    assert spy.scratch_host
    python_tools.cleanup_run_scratch(ctx)
    assert not Path(spy.scratch_host).exists()


# ---- scratch 生命周期 per-run（ADR-0066 决策 8）：产物要活过产生它的那次执行 ----

async def test_scratch_is_shared_within_a_run(preflight_ok, monkeypatch, tmp_path):
    """同一 run 内第二次执行看得到第一次的产物——import_artifact 是独立的一次工具调用。"""
    from app.agent import python_tools

    ctx = make_ctx(tmp_path)
    spy = WritingDockerSpy({"a.csv": "a,b\n"})
    monkeypatch.setattr(engine, "docker", spy)
    await execute_tool(ctx, "run_python", {"code": "print(1)"})
    first = spy.scratch_host
    spy.files = {"b.txt": "hello"}
    r2 = await execute_tool(ctx, "run_shell", {"command": "echo hello > /scratch/b.txt"})
    assert spy.scratch_host == first
    assert {a["name"] for a in r2["artifacts"]} == {"a.csv", "b.txt"}
    assert Path(first).is_dir()          # run 未收口，产物还在
    python_tools.cleanup_run_scratch(ctx)
    assert not Path(first).exists()


def test_sweep_orphan_scratches_only_takes_stale_ours(tmp_path, monkeypatch):
    """启动清扫只收陈旧的本平台目录：共享 TEMP 下另一个活进程的 scratch 不能被顺手删掉。"""
    import os
    import time
    from app.agent import python_tools

    old = tmp_path / "zc-pysandbox-old"
    old.mkdir()
    fresh = tmp_path / "zc-pysandbox-fresh"
    fresh.mkdir()
    other = tmp_path / "not-ours"
    other.mkdir()
    stale = time.time() - python_tools.ORPHAN_MIN_AGE_SECONDS - 60
    os.utime(old, (stale, stale))
    monkeypatch.setattr(python_tools.tempfile, "gettempdir", lambda: str(tmp_path))
    python_tools.sweep_orphan_scratches()
    assert not old.exists()
    assert fresh.is_dir()
    assert other.is_dir()


async def test_oversized_artifacts_rejected_and_dropped(preflight_ok, monkeypatch, tmp_path):
    """产物超 scratch 配额 → 可读拒绝并丢弃，不把磁盘留着满（US-34）。"""
    from app.agent import python_tools

    monkeypatch.setattr(python_tools, "SCRATCH_BYTES_CAP", 10)
    spy = WritingDockerSpy({"big.bin": b"x" * 64})
    monkeypatch.setattr(engine, "docker", spy)
    ctx = make_ctx(tmp_path)
    r = await execute_tool(ctx, "run_python", {"code": "print(1)"})
    assert r["ok"] is False and "配额" in r["error"]
    assert spy.scratch_host and not Path(spy.scratch_host).exists()


def test_python_prompt_names_boundaries():
    """提示词与工具能力同源：触达面边界（只读/scratch/落盘/无网/无库）都要教会模型。"""
    from app.agent import python_tools
    text = python_tools.build_python_prompt()
    assert "<python_sandbox>" in text and "</python_sandbox>" in text
    for kw in ("/workspace", "/scratch", "write_file", "db_query", "仅公网", "skill_dir", "/skill"):
        assert kw in text, kw


def test_python_prompt_teaches_public_egress_and_pip():
    """网络口径改到仅公网（US-18/19/38）：可 pip 装到 scratch，且旧的离线口径不能留。"""
    from app.agent import python_tools
    text = python_tools.build_python_prompt()
    for kw in ("pip install", "/scratch/pylibs", "私网"):
        assert kw in text, kw
    assert "容器离线" not in text
    assert "无网络出口" not in text


def test_python_prompt_teaches_artifact_import():
    """二进制落盘通道要教会模型（US-21/23），旧的「暂不能落盘」口径必须删掉。"""
    from app.agent import python_tools
    text = python_tools.build_python_prompt()
    assert "import_artifact" in text
    assert "暂不能落盘" not in text


def test_python_prompt_teaches_shell_face():
    """命令面也要教会模型（US-38）：run_shell 存在、经 sh -c、可用命令以镜像内为限、apt 不支持。"""
    from app.agent import python_tools
    text = python_tools.build_python_prompt()
    for kw in ("run_shell", "sh -c", "apt", "镜像"):
        assert kw in text, kw


def test_python_prompt_teaches_office_scope():
    """办公文档口径（US-17/20/22）：预装库名要告诉模型，老格式明确拒并引导另存。"""
    from app.agent import python_tools
    text = python_tools.build_python_prompt()
    for kw in ("openpyxl", "python-docx", "python-pptx", "老 .doc", "另存"):
        assert kw in text, kw
    # 反向断言：预装库到位后「只用纯标准库」这条口径就不再成立，不能留着自相矛盾
    assert "纯标准库" not in text


# ---- 技能脚本模式（ADR-0060）：skill_dir+script+argv，母本只读挂载进同一沙箱 ----

def _skill_tree(tmp_path):
    skills = tmp_path / "users" / "1" / "skills"
    pdf = skills / "pdf"
    (pdf / "scripts").mkdir(parents=True)
    (pdf / "scripts" / "run.py").write_text("print('hi')", encoding="utf-8")
    (pdf / "SKILL.md").write_text("---\nname: pdf\ndescription: d\n---\n读我", encoding="utf-8")
    ws = tmp_path / "ws"
    ws.mkdir()
    return skills, pdf, ws


def _skill_ctx(ws, skills):
    return ToolContext(root=ws, session_id=1, session_factory=None, send=Recorder(),
                       skills_root=skills)


async def test_skill_mode_mounts_readonly_and_runs_master_copy(preflight_ok, monkeypatch, tmp_path):
    skills, pdf, ws = _skill_tree(tmp_path)
    spy = DockerSpy(0, "hi")
    monkeypatch.setattr(engine, "docker", spy)
    r = await execute_tool(_skill_ctx(ws, skills), "run_python",
                           {"skill_dir": str(pdf), "script": "scripts/run.py"})
    assert r["ok"] is True and r["exit_code"] == 0
    assert r["script"] == "scripts/run.py"
    (args, _), = spy.calls
    assert f"{pdf.resolve()}:/skill:ro" in args          # 单技能目录只读挂载
    assert "python" in args and "/skill/scripts/run.py" in args
    assert "-c" not in args                              # 跑母本原件，不是 python -c
    assert "--network" in args and "container:zerocode-sandbox-egress" in args  # 仅公网
    assert f"{ws}:/workspace:ro" in args


async def test_skill_mode_argv_passed_in_order(preflight_ok, monkeypatch, tmp_path):
    skills, pdf, ws = _skill_tree(tmp_path)
    spy = DockerSpy(0, "")
    monkeypatch.setattr(engine, "docker", spy)
    await execute_tool(_skill_ctx(ws, skills), "run_python",
                       {"skill_dir": str(pdf), "script": "scripts/run.py", "argv": ["in.csv", "42"]})
    (args, _), = spy.calls
    i = args.index("/skill/scripts/run.py")
    assert args[i + 1:i + 3] == ("in.csv", "42")


async def test_code_and_skill_mode_mutually_exclusive(preflight_ok, monkeypatch, tmp_path):
    skills, pdf, ws = _skill_tree(tmp_path)
    spy = DockerSpy()
    monkeypatch.setattr(engine, "docker", spy)
    r = await execute_tool(_skill_ctx(ws, skills), "run_python",
                           {"code": "print(1)", "skill_dir": str(pdf), "script": "scripts/run.py"})
    assert r["ok"] is False and "互斥" in r["error"]
    assert spy.calls == []


async def test_skill_mode_requires_both_fields(preflight_ok, monkeypatch, tmp_path):
    skills, pdf, ws = _skill_tree(tmp_path)
    spy = DockerSpy()
    monkeypatch.setattr(engine, "docker", spy)
    for bad in ({"skill_dir": str(pdf)}, {"script": "scripts/run.py"}):
        r = await execute_tool(_skill_ctx(ws, skills), "run_python", bad)
        assert r["ok"] is False and "skill" in r["error"], bad
    assert spy.calls == []


async def test_skill_dir_out_of_root_rejected(preflight_ok, monkeypatch, tmp_path):
    skills, pdf, ws = _skill_tree(tmp_path)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    spy = DockerSpy()
    monkeypatch.setattr(engine, "docker", spy)
    r = await execute_tool(_skill_ctx(ws, skills), "run_python",
                           {"skill_dir": str(outside), "script": "x.py"})
    assert r["ok"] is False and "skill_dir" in r["error"]
    assert spy.calls == []


async def test_skill_dir_must_be_directory_not_root(preflight_ok, monkeypatch, tmp_path):
    skills, pdf, ws = _skill_tree(tmp_path)
    spy = DockerSpy()
    monkeypatch.setattr(engine, "docker", spy)
    ctx = _skill_ctx(ws, skills)
    r1 = await execute_tool(ctx, "run_python",
                            {"skill_dir": str(pdf / "SKILL.md"), "script": "scripts/run.py"})
    assert r1["ok"] is False and "skill_dir" in r1["error"]   # 指向文件
    r2 = await execute_tool(ctx, "run_python",
                            {"skill_dir": str(skills), "script": "pdf/scripts/run.py"})
    assert r2["ok"] is False and "skill_dir" in r2["error"]   # 技能根本身：不挂整个根
    assert spy.calls == []


async def test_script_fence_matrix(preflight_ok, monkeypatch, tmp_path):
    skills, pdf, ws = _skill_tree(tmp_path)
    (pdf / "notpy.txt").write_text("x", encoding="utf-8")
    spy = DockerSpy()
    monkeypatch.setattr(engine, "docker", spy)
    ctx = _skill_ctx(ws, skills)
    cases = [
        {"skill_dir": str(pdf), "script": str(pdf / "scripts" / "run.py")},  # 绝对路径
        {"skill_dir": str(pdf), "script": "../escape.py"},                    # 逃逸出 skill_dir
        {"skill_dir": str(pdf), "script": "notpy.txt"},                       # 非 .py
        {"skill_dir": str(pdf), "script": "scripts/missing.py"},              # 不存在
    ]
    for c in cases:
        r = await execute_tool(ctx, "run_python", c)
        assert r["ok"] is False and "script" in r["error"], c
    assert spy.calls == []


async def test_argv_limits(preflight_ok, monkeypatch, tmp_path):
    skills, pdf, ws = _skill_tree(tmp_path)
    spy = DockerSpy()
    monkeypatch.setattr(engine, "docker", spy)
    ctx = _skill_ctx(ws, skills)
    base = {"skill_dir": str(pdf), "script": "scripts/run.py"}
    bads = [
        {**base, "argv": ["x"] * 33},
        {**base, "argv": ["x" * 2001]},
        {**base, "argv": "notalist"},
        {**base, "argv": [123]},
    ]
    for b in bads:
        r = await execute_tool(ctx, "run_python", b)
        assert r["ok"] is False and "argv" in r["error"], b
    assert spy.calls == []


async def test_no_skills_root_skill_mode_rejected(preflight_ok, monkeypatch, tmp_path):
    skills, pdf, ws = _skill_tree(tmp_path)
    spy = DockerSpy()
    monkeypatch.setattr(engine, "docker", spy)
    ctx = ToolContext(root=ws, session_id=1, session_factory=None, send=Recorder())  # skills_root=None
    r = await execute_tool(ctx, "run_python", {"skill_dir": str(pdf), "script": "scripts/run.py"})
    assert r["ok"] is False and "skill" in r["error"]
    assert spy.calls == []


async def test_code_mode_has_no_skill_mount(preflight_ok, monkeypatch, tmp_path):
    spy = DockerSpy(0, "")
    monkeypatch.setattr(engine, "docker", spy)
    await execute_tool(make_ctx(tmp_path), "run_python", {"code": "print(1)"})
    (args, _), = spy.calls
    assert not any(a.endswith(":/skill:ro") for a in args)
    assert "-c" in args


# ---- 沙箱命令面 run_shell（ADR-0066）：同一套围栏，接任意命令行 ----

async def test_run_shell_executes_command(preflight_ok, monkeypatch, tmp_path):
    """示踪弹：模型给命令行，沙箱经 sh -c 跑完回退出码与输出。"""
    spy = DockerSpy(0, "total 0")
    monkeypatch.setattr(engine, "docker", spy)
    r = await execute_tool(make_ctx(tmp_path), "run_shell", {"command": "ls -la /workspace"})
    assert r["ok"] is True and r["exit_code"] == 0
    assert "total 0" in r["output"]
    (args, _), = spy.calls
    assert args[0] == "run" and "--rm" in args
    assert "sh" in args and "-c" in args and "ls -la /workspace" in args


async def test_run_shell_shares_python_fence(preflight_ok, monkeypatch, tmp_path):
    """围栏与 run_python 同源：只读工作区、deploy 遮蔽、scratch 可写、非 root、断网、一次性。"""
    spy = DockerSpy(0, "")
    monkeypatch.setattr(engine, "docker", spy)
    (tmp_path / "deploy").mkdir()
    await execute_tool(make_ctx(tmp_path), "run_shell", {"command": "ls"})
    (args, _), = spy.calls
    assert "--rm" in args
    assert f"{tmp_path}:/workspace:ro" in args
    assert "--tmpfs" in args and "/workspace/deploy:ro" in args
    assert "--user" in args and "1000:1000" in args
    assert "--network" in args and "container:zerocode-sandbox-egress" in args
    assert "--memory" in args and "--cpus" in args and "--pids-limit" in args
    assert any(a.endswith(":/scratch") for a in args)
    assert "-w" in args and "/workspace" in args
    assert not any(a.endswith(":/skill:ro") for a in args)


async def test_run_shell_passes_pipeline_verbatim(preflight_ok, monkeypatch, tmp_path):
    """任意命令行：管道与重定向原样交给 sh -c，不做命令白名单（ADR-0066 决策 2）。"""
    spy = DockerSpy(0, "")
    monkeypatch.setattr(engine, "docker", spy)
    cmd = "cat a.txt | grep b > /scratch/c.txt"
    r = await execute_tool(make_ctx(tmp_path), "run_shell", {"command": cmd})
    assert r["ok"] is True
    (args, _), = spy.calls
    assert list(args[-3:]) == ["sh", "-c", cmd]


async def test_run_shell_empty_command_rejected_without_container(preflight_ok, monkeypatch, tmp_path):
    """空/非字符串 command 当场拒，不起容器。"""
    spy = DockerSpy(0, "")
    monkeypatch.setattr(engine, "docker", spy)
    for bad in ({}, {"command": ""}, {"command": "   "}, {"command": 123}):
        r = await execute_tool(make_ctx(tmp_path), "run_shell", bad)
        assert r["ok"] is False and "command" in r["error"]
    assert spy.calls == []


async def test_run_shell_nonzero_exit_is_normal_result(preflight_ok, monkeypatch, tmp_path):
    """命令非零退出是正常结果：ok=True，模型据 output 自己修（沿 run_python 语义）。"""
    spy = DockerSpy(2, "ls: cannot access 'nope': No such file or directory")
    monkeypatch.setattr(engine, "docker", spy)
    r = await execute_tool(make_ctx(tmp_path), "run_shell", {"command": "ls nope"})
    assert r["ok"] is True and r["exit_code"] == 2
    assert "No such file" in r["output"]


async def test_run_shell_docker_unavailable_is_error_result(monkeypatch, tmp_path):
    """平台故障才是 ok=False，且不起容器。"""
    from app.deploy.errors import DeployError

    async def down():
        raise DeployError("docker 不可用", status=503)

    monkeypatch.setattr(engine, "preflight_docker", down)
    spy = DockerSpy()
    monkeypatch.setattr(engine, "docker", spy)
    r = await execute_tool(make_ctx(tmp_path), "run_shell", {"command": "ls"})
    assert r["ok"] is False and "docker" in r["error"]
    assert spy.calls == []


async def test_run_shell_timeout_is_error_result(preflight_ok, monkeypatch, tmp_path):
    from app.deploy.errors import DeployError

    async def slow(*a, **k):
        raise DeployError("docker run… 超时（120s）", status=504)

    monkeypatch.setattr(engine, "docker", slow)
    r = await execute_tool(make_ctx(tmp_path), "run_shell", {"command": "sleep 999"})
    assert r["ok"] is False and "超时" in r["error"]


async def test_run_shell_artifacts_returned_and_cleaned_at_run_close(preflight_ok, monkeypatch, tmp_path):
    """命令写 /scratch 的产物回全文，run 收口后清理宿主临时目录。"""
    from app.agent import python_tools

    spy = WritingDockerSpy({"out.txt": "hello\n"})
    monkeypatch.setattr(engine, "docker", spy)
    ctx = make_ctx(tmp_path)
    r = await execute_tool(ctx, "run_shell",
                           {"command": "echo hello > /scratch/out.txt"})
    assert r["ok"] is True
    arts = {a["name"]: a for a in r["artifacts"]}
    assert arts["out.txt"]["content"] == "hello\n"
    assert spy.scratch_host and Path(spy.scratch_host).is_dir()
    python_tools.cleanup_run_scratch(ctx)
    assert not Path(spy.scratch_host).exists()


# ---- 自建沙箱镜像（ADR-0066 决策 4/6）：预装办公库与 CLI，不再用裸官方镜像 ----

async def test_sandbox_uses_platform_built_image(preflight_ok, monkeypatch, tmp_path):
    """两个工具都跑平台自建镜像——裸 python:3.12-slim 里没有 openpyxl，也没有 git/curl。"""
    spy = DockerSpy(0, "")
    monkeypatch.setattr(engine, "docker", spy)
    for name, args in (("run_shell", {"command": "ls"}), ("run_python", {"code": "print(1)"})):
        spy.calls.clear()
        await execute_tool(make_ctx(tmp_path), name, args)
        (argv, _), = spy.calls
        assert "zerocode-sandbox:dev" in argv, name
        assert "python:3.12-slim" not in argv, name


async def test_sandbox_image_env_override(preflight_ok, monkeypatch, tmp_path):
    """交付包烘另一个 tag：环境变量覆盖，不改代码。"""
    monkeypatch.setenv("ZEROCODE_SANDBOX_IMAGE", "zerocode-sandbox:demo")
    spy = DockerSpy(0, "")
    monkeypatch.setattr(engine, "docker", spy)
    await execute_tool(make_ctx(tmp_path), "run_shell", {"command": "ls"})
    (argv, _), = spy.calls
    assert "zerocode-sandbox:demo" in argv
    assert "zerocode-sandbox:dev" not in argv


async def test_missing_sandbox_image_is_readable_error(preflight_ok, monkeypatch, tmp_path):
    """镜像没构建 → 可读引导，不是把 docker 的 125 读成「命令非零退出」（那离根因很远）。"""
    async def no_image(*a, **k):
        return 125, ("Unable to find image 'zerocode-sandbox:dev' locally\n"
                     "docker: Error response from daemon: pull access denied for zerocode-sandbox.")

    monkeypatch.setattr(engine, "docker", no_image)
    for name, args in (("run_shell", {"command": "ls"}), ("run_python", {"code": "print(1)"})):
        r = await execute_tool(make_ctx(tmp_path), name, args)
        assert r["ok"] is False, name
        assert "镜像" in r["error"] and "构建" in r["error"], name


async def test_unrelated_125_is_not_treated_as_missing_image(preflight_ok, monkeypatch, tmp_path):
    """反向断言：125 但原因与镜像无关时，仍是正常的非零退出结果。"""
    async def bad_flag(*a, **k):
        return 125, "docker: invalid reference format."

    monkeypatch.setattr(engine, "docker", bad_flag)
    r = await execute_tool(make_ctx(tmp_path), "run_shell", {"command": "ls"})
    assert r["ok"] is True and r["exit_code"] == 125


async def test_missing_egress_router_is_readable_error(preflight_ok, monkeypatch, tmp_path):
    """router 没起来 → 可读引导，不是报成「命令非零退出」（那会让模型去改命令）。"""
    async def no_router(*a, **k):
        return 125, ("docker: Error response from daemon: container "
                     "zerocode-sandbox-egress is not running.")

    monkeypatch.setattr(engine, "docker", no_router)
    for name, args in (("run_shell", {"command": "ls"}), ("run_python", {"code": "print(1)"})):
        r = await execute_tool(make_ctx(tmp_path), name, args)
        assert r["ok"] is False, name
        assert "出口" in r["error"], name


async def test_pypi_mirror_passed_through_when_configured(preflight_ok, monkeypatch, tmp_path):
    """网络受限服务器：换源位透传进容器，运行期 pip 才装得动（US-19）。"""
    monkeypatch.setenv("ZEROCODE_PYPI_MIRROR", "https://mirror.example/simple")
    spy = DockerSpy(0, "")
    monkeypatch.setattr(engine, "docker", spy)
    await execute_tool(make_ctx(tmp_path), "run_shell", {"command": "pip install six"})
    (argv, _), = spy.calls
    assert "PIP_INDEX_URL=https://mirror.example/simple" in argv


async def test_no_pypi_mirror_env_means_no_index_override(preflight_ok, monkeypatch, tmp_path):
    """反向断言：没配换源就不塞 PIP_INDEX_URL，别把默认源覆盖成空。"""
    monkeypatch.delenv("ZEROCODE_PYPI_MIRROR", raising=False)
    spy = DockerSpy(0, "")
    monkeypatch.setattr(engine, "docker", spy)
    await execute_tool(make_ctx(tmp_path), "run_shell", {"command": "ls"})
    (argv, _), = spy.calls
    assert not any(a.startswith("PIP_INDEX_URL=") for a in argv)


# ---- 产物导入 import_artifact（ADR-0066 决策 7）：二进制经确认落工作区 ----

async def test_import_artifact_lands_binary_in_workspace(preflight_ok, monkeypatch, tmp_path):
    """示踪弹：scratch 里的 docx 落进工作区，字节保真，并明说不可回滚。"""
    from app.agent import python_tools

    payload = b"PK\x03\x04fake-docx-bytes"
    spy = WritingDockerSpy({"报告.docx": payload})
    monkeypatch.setattr(engine, "docker", spy)
    ctx = make_ctx(tmp_path)
    await execute_tool(ctx, "run_python", {"code": "print(1)"})
    r = await execute_tool(ctx, "import_artifact", {"name": "报告.docx", "path": "out/报告.docx"})
    assert r["ok"] is True
    assert r["path"] == "out/报告.docx"
    assert r["bytes"] == len(payload)
    assert (tmp_path / "out" / "报告.docx").read_bytes() == payload
    assert "不可回滚" in r.get("notice", "")
    python_tools.cleanup_run_scratch(ctx)


async def test_import_artifact_rejects_hostile_names_and_targets(preflight_ok, monkeypatch, tmp_path):
    """入参围栏矩阵：它不得成为绕过 write_file 的任意文件读写通道（规格 §5 第 2 条）。"""
    from app.agent import python_tools
    from app.agent.tools import ToolError

    spy = WritingDockerSpy({"ok.docx": b"PK"})
    monkeypatch.setattr(engine, "docker", spy)
    ctx = make_ctx(tmp_path)
    await execute_tool(ctx, "run_python", {"code": "print(1)"})
    # 写保护只在应用工作区生效（盘上有 app.json），普通工作区零影响——与 write_file 同口径。
    (tmp_path / "app.json").write_text("{}", encoding="utf-8")
    # scratch 的父目录里放一个真实存在的文件：越界断言要验「存在但在目录外」，
    # 否则会被「产物不存在」那条出口顺手挡掉，测不到越界围栏本身。
    outside = Path(spy.scratch_host).parent / "zc-outside.txt"
    outside.write_text("secret", encoding="utf-8")
    try:
        for bad in ({"name": str(outside), "path": "x.txt"},
                    {"name": "../zc-outside.txt", "path": "x.txt"},
                    {"name": "missing.docx", "path": "x.txt"},
                    {"name": "ok.docx", "path": "../escape.docx"},
                    {"name": "ok.docx", "path": str(tmp_path / "abs.docx")},
                    {"name": "ok.docx", "path": "deploy/.env"},
                    {"name": "ok.docx", "path": "app.json"},
                    {"path": "x.docx"},
                    {"name": "ok.docx"}):
            with pytest.raises(ToolError):
                await execute_tool(ctx, "import_artifact", bad)
        assert not (tmp_path / "x.txt").exists()
        assert (tmp_path / "app.json").read_text(encoding="utf-8") == "{}"
    finally:
        outside.unlink(missing_ok=True)
        python_tools.cleanup_run_scratch(ctx)


async def test_import_artifact_after_run_close_is_readable(preflight_ok, monkeypatch, tmp_path):
    """跨 run 取不到：回可读引导，不是裸 FileNotFoundError。"""
    from app.agent import python_tools
    from app.agent.tools import ToolError

    spy = WritingDockerSpy({"ok.docx": b"PK"})
    monkeypatch.setattr(engine, "docker", spy)
    ctx = make_ctx(tmp_path)
    await execute_tool(ctx, "run_python", {"code": "print(1)"})
    python_tools.cleanup_run_scratch(ctx)
    with pytest.raises(ToolError, match="清理"):
        await execute_tool(ctx, "import_artifact", {"name": "ok.docx", "path": "x.docx"})