"""Python 沙箱 run_python：隔离一次性容器跑模型给的任意 Python 脚本（ADR-0056）。

红线（ADR-0046 改写）：工具面无通用命令执行能力，只允许平台写死的 preset 与隔离 Python 沙箱。
沙箱靠最小触达面让围栏成立——工作区只读挂载（deploy 遮蔽）、写只进 scratch、无库凭据、
无 docker.sock、非 root、限资源；网络出口待 005（当前 --network none 离线 fail-closed）。
挂载/scratch/资源由 003/004/006 落地，网络过滤由 005 加宽。

结果语义沿 run_check：ok=True 表示沙箱执行了脚本（exit_code 反映脚本结果，脚本异常是正常结果，
模型据 output 里的 traceback 去修）；只有平台侧故障（docker 不可用/超时/取消）才是 ok=False。
output 是 stdout+stderr 合流尾部——engine.docker 接缝所限（stderr=STDOUT），不另起执行客户端。

产物：脚本写 /scratch（绑挂宿主临时目录），跑完扫目录回 artifacts（文本 under cap 回全文，
二进制/超限只回元数据），宿主临时目录随即清理。文本产物由模型经 write_file 落盘（围栏原样成立）；
二进制产物导入不在本票（write_file 是 UTF-8 文本通道，spec §6）。
"""
from __future__ import annotations

import logging
import os
import shutil
import tempfile
import time
from pathlib import Path

from app.agent import sandbox_egress

logger = logging.getLogger(__name__)

SANDBOX_IMAGE_ENV = "ZEROCODE_SANDBOX_IMAGE"
PYPI_MIRROR_ENV = "ZEROCODE_PYPI_MIRROR"
DEFAULT_SANDBOX_IMAGE = "zerocode-sandbox:dev"
OUTPUT_CHARS = 16_000
RUN_TIMEOUT = 240                # pip 装包计入同一预算，120s 装大包会撞超时
ARTIFACT_BYTES_CAP = 64_000      # 单产物超此体积只回元数据
ARTIFACT_CONTENT_CHARS = 16_000  # 单产物全文回注上限
MAX_ARTIFACTS = 50
SCRATCH_KEY = "sandbox_scratch"
ORPHAN_MIN_AGE_SECONDS = 6 * 3600
SCRATCH_BYTES_CAP = 100 * 1024 * 1024


def _scan_artifacts(scratch: str) -> list[dict]:
    """扫 scratch 产物：文本 under cap 回全文，二进制/超限只回元数据（name/bytes）。"""
    arts: list[dict] = []
    root = Path(scratch)
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        entry = {"name": p.relative_to(root).as_posix(), "bytes": p.stat().st_size}
        if entry["bytes"] <= ARTIFACT_BYTES_CAP:
            try:
                text = p.read_text(encoding="utf-8")
            except (UnicodeDecodeError, ValueError):
                text = None                          # 二进制：只回元数据
            if text is not None:
                entry["content"] = text[:ARTIFACT_CONTENT_CHARS]
                if len(text) > ARTIFACT_CONTENT_CHARS:
                    entry["truncated"] = True
        arts.append(entry)
        if len(arts) >= MAX_ARTIFACTS:
            break
    return arts


def _new_scratch() -> str:
    scratch = tempfile.mkdtemp(prefix="zc-pysandbox-")
    os.chmod(scratch, 0o777)   # 容器以非 root（1000）跑，scratch 须任意 uid 可写
    return scratch


def _run_scratch(ctx) -> str:
    """per-run scratch：同一 run 内复用，活到 run 收口（ADR-0066 决策 8）。

    import_artifact 是独立的一次工具调用，产物若当场删就必然取不到。容器仍是一次性——
    「无状态残留」保的是容器与解释器，不是产物目录。
    """
    state = getattr(ctx, "run_state", None)
    existing = (state or {}).get(SCRATCH_KEY)
    if existing and Path(existing).is_dir():
        return existing
    scratch = _new_scratch()
    if state is not None:
        state[SCRATCH_KEY] = scratch
    return scratch


def cleanup_run_scratch(ctx) -> None:
    """run 收口清理（正常/取消/报错三条出口都走这里）；删不掉静默忽略，不拖住对话结束的读数。"""
    state = getattr(ctx, "run_state", None)
    if state is None:
        return
    scratch = state.pop(SCRATCH_KEY, None)
    if scratch:
        shutil.rmtree(scratch, ignore_errors=True)


def _scratch_bytes(scratch: str) -> int:
    total = 0
    for p in Path(scratch).rglob("*"):
        if p.is_file():
            try:
                total += p.stat().st_size
            except OSError:
                continue
    return total


def sweep_orphan_scratches() -> None:
    """进程启动时清扫崩溃/kill -9 留下的产物目录。

    只收**陈旧**的：TEMP 是全机共享的，另一个活着的后端进程可能正持有同名前缀的目录，
    按前缀一律删会顺手删掉别人正在用的 scratch。
    """
    cutoff = time.time() - ORPHAN_MIN_AGE_SECONDS
    root = Path(tempfile.gettempdir())
    for p in root.glob("zc-pysandbox-*"):
        try:
            if p.is_dir() and p.stat().st_mtime < cutoff:
                shutil.rmtree(p, ignore_errors=True)
        except OSError:
            continue


def _sandbox_image() -> str:
    return os.environ.get(SANDBOX_IMAGE_ENV) or DEFAULT_SANDBOX_IMAGE


def _deploy_mask(ctx) -> list[str]:
    """deploy 凭据遮蔽——**只在盘上真有 deploy/ 时加**。

    /workspace 是只读挂载，Docker 在其内创建不出挂载点：没有 deploy 目录时加 tmpfs 会让容器
    直接起不来（真容器实测 exit 125，报 read-only file system）。围栏两种情形都成立：
    没有 deploy 目录就没有凭据可读；有则被空 tmpfs 遮住。
    """
    return ["--tmpfs", "/workspace/deploy:ro"] if (Path(ctx.root) / "deploy").is_dir() else []


def _sandbox_argv(ctx, scratch: str, extra_mount: list[str], cmd: list[str]) -> list[str]:
    """沙箱围栏的唯一出处——抄第二份就会静默分叉，两个工具的围栏必须同源。"""
    return [
        "run", "--rm", "--network", f"container:{sandbox_egress.ROUTER_CONTAINER}",
        "--user", "1000:1000",
        "--memory", "1g", "--cpus", "1", "--pids-limit", "256",
        "-v", f"{ctx.root}:/workspace:ro",
        *_deploy_mask(ctx),
        "-v", f"{scratch}:/scratch",
        *_pypi_env(),
        *extra_mount,
        "-w", "/workspace",
        _sandbox_image(), *cmd,
    ]


def _image_missing(exit_code: int, log: str) -> bool:
    """docker run 自身失败（125）且输出指向镜像取不到。

    与脚本/命令非零退出区分开：把「镜像没构建」报成「命令非零退出」离根因很远，
    模型只会去改命令而不是告诉用户镜像缺失。
    """
    return exit_code == 125 and any(
        m in (log or "") for m in ("Unable to find image", "pull access denied", "No such image"))


def _router_missing(exit_code: int, log: str) -> bool:
    """共享 netns 的 router 不在跑——与镜像缺失同理，要报成平台故障而不是命令非零退出。"""
    return (exit_code == 125 and sandbox_egress.ROUTER_CONTAINER in (log or "")
            and ("not found" in log or "is not running" in log))


def _pypi_env() -> list[str]:
    mirror = os.environ.get(PYPI_MIRROR_ENV)
    return ["-e", f"PIP_INDEX_URL={mirror}"] if mirror else []


async def _run_sandbox(ctx, cmd: list[str], extra_mount: list[str]) -> dict:
    """跑一次沙箱容器并收口成工具结果。

    ok=True 表示沙箱执行了（exit_code 反映脚本/命令结果，非零退出是正常结果）；
    只有平台侧故障（docker 不可用/超时/取消）才是 ok=False。
    """
    from app.agent.db_tools import _safe_error
    from app.deploy import engine

    scratch = _run_scratch(ctx)
    try:
        await engine.preflight_docker()
        t0 = time.monotonic()
        exit_code, log = await engine.docker(
            *_sandbox_argv(ctx, scratch, extra_mount, cmd), timeout=RUN_TIMEOUT)
        if _image_missing(exit_code, log):
            return {"ok": False,
                    "error": f"沙箱镜像 {_sandbox_image()} 未构建：本地按 sandbox/README.md 构建，"
                             "交付包用 docker compose build sandbox 后重试"}
        if _router_missing(exit_code, log):
            return {"ok": False,
                    "error": "沙箱出口 router 未在运行：请确认平台启动日志里的 "
                             "「沙箱出口 router 启动失败」，或重启平台后重试"}
        artifacts = _scan_artifacts(scratch)
        used = _scratch_bytes(scratch)
        if used > SCRATCH_BYTES_CAP:
            # 事后判定而非硬配额：scratch 是 bind mount，Docker 没有可移植的目录级配额
            # （--storage-opt 要 overlay2+xfs pquota）。所以超限只能丢弃产物并如实报原因，
            # 挡不住写入过程中的磁盘占用——真要硬限得换成容器内 tmpfs 再拷出，那是另一票。
            cleanup_run_scratch(ctx)
            return {"ok": False,
                    "error": f"产物超出 scratch 配额（{used // (1024 * 1024)}MB > "
                             f"{SCRATCH_BYTES_CAP // (1024 * 1024)}MB），已丢弃；"
                             "请减少输出量或分批生成"}
    except Exception as e:
        logger.exception("沙箱执行异常")
        return {"ok": False, "error": _safe_error(e)}
    return {"ok": True, "exit_code": exit_code,
            "duration_seconds": round(time.monotonic() - t0, 1),
            "output": (log or "")[-OUTPUT_CHARS:],
            "artifacts": artifacts}


async def run_shell_tool(ctx, args: dict, *, confirmed: bool = False) -> dict:
    """沙箱命令面（ADR-0066）：任意命令行经 sh -c 跑，围栏与 run_python 同源。"""
    args = args if isinstance(args, dict) else {}
    command = args.get("command")
    if not (isinstance(command, str) and command.strip()):
        return {"ok": False, "error": "command 必填：要执行的命令行（经 sh -c，支持管道与重定向）"}
    return await _run_sandbox(ctx, ["sh", "-c", command], [])


async def run_python_tool(ctx, args: dict, *, confirmed: bool = False) -> dict:
    """统一入口（registry 的 handler_ref 指向这里）；平台故障作为正常结果返回。"""
    args = args if isinstance(args, dict) else {}
    code = args.get("code")
    skill_dir = args.get("skill_dir")
    script = args.get("script")
    has_code = isinstance(code, str) and bool(code.strip())
    has_skill = skill_dir is not None or script is not None
    if has_code and has_skill:
        return {"ok": False, "error": "code 与 skill_dir/script 互斥，二选一"}
    extra_mount: list[str] = []
    script_rel = None
    if has_skill:
        if not (isinstance(skill_dir, str) and skill_dir.strip()
                and isinstance(script, str) and script.strip()):
            return {"ok": False, "error": "skill 模式需同时给 skill_dir 与 script"}
        from app.agent.tools import resolve_skill_path
        sd = resolve_skill_path(ctx.skills_root, skill_dir)
        if sd is None or not sd.is_dir():
            return {"ok": False, "error": "skill_dir 必须是当前用户技能根内的目录"}
        sd_r = sd.resolve()
        if sd_r == Path(ctx.skills_root).resolve():
            return {"ok": False, "error": "skill_dir 必须是技能根下的单个技能目录，不能是根本身"}
        sp = Path(script)
        if sp.is_absolute():
            return {"ok": False, "error": "script 必须是技能目录内的相对路径"}
        try:
            full = (sd_r / sp).resolve()
        except (OSError, RuntimeError, ValueError):
            return {"ok": False, "error": "script 路径异常"}
        if sd_r != full and sd_r not in full.parents:
            return {"ok": False, "error": "script 越出技能目录"}
        if full.suffix.lower() != ".py":
            return {"ok": False, "error": "script 必须是 .py 文件"}
        if not full.is_file():
            return {"ok": False, "error": f"script 不存在: {script}"}
        argv = args.get("argv") or []
        if not isinstance(argv, list) or not all(isinstance(a, str) for a in argv):
            return {"ok": False, "error": "argv 必须是字符串数组"}
        if len(argv) > 32 or any(len(a) > 2000 for a in argv):
            return {"ok": False, "error": "argv 超限（≤32 个、单个 ≤2000 字符）"}
        script_rel = full.relative_to(sd_r).as_posix()
        extra_mount = ["-v", f"{sd_r}:/skill:ro"]
        cmd = ["python", f"/skill/{script_rel}", *argv]
    elif has_code:
        cmd = ["python", "-c", code]
    else:
        return {"ok": False,
                "error": "code 必填：要执行的 Python 源码字符串（或给 skill_dir+script 跑技能脚本）"}
    result = await _run_sandbox(ctx, cmd, extra_mount)
    if script_rel is not None and result["ok"]:
        result["script"] = script_rel
    return result


async def import_artifact_tool(ctx, args: dict, *, confirmed: bool = False) -> dict:
    """把本次 run 的 scratch 产物落进工作区（ADR-0066 决策 7）。

    只收 scratch 内的相对名：**收宿主绝对路径或内容直传，它就变成绕过 write_file 的
    任意文件读写通道**。目标路径与写入围栏逐字复用 write_file 那一套。
    """
    from app.agent.tools import ToolError, _record_change, _rel_of, resolve_in_workspace
    from app.deploy.guard import is_protected

    args = args if isinstance(args, dict) else {}
    name = args.get("name")
    rel = args.get("path")
    if not (isinstance(name, str) and name.strip()):
        raise ToolError("name 必填：本次执行产物清单里的相对名（如 报告.docx）")
    scratch = (getattr(ctx, "run_state", None) or {}).get(SCRATCH_KEY)
    if not scratch or not Path(scratch).is_dir():
        raise ToolError("产物已随上次执行结束清理，请重新生成后再导入")
    p = Path(name)
    if p.is_absolute():
        raise ToolError("name 必须是产物清单里的相对名，不接受绝对路径")
    scratch_r = Path(scratch).resolve()
    src = (scratch_r / p).resolve()
    if src != scratch_r and scratch_r not in src.parents:
        raise ToolError("name 越出本次产物目录")
    if not src.is_file():
        raise ToolError(f"产物不存在: {name}（请对照本次执行返回的 artifacts 清单）")

    f = resolve_in_workspace(ctx.root, rel)
    if is_protected(Path(ctx.root), rel):
        raise ToolError("app.json 与 deploy/ 由平台管理，AI 不可修改")
    if f == Path(ctx.root).resolve() or f.is_dir():
        raise ToolError(f"路径必须是文件: {rel}")
    existed = f.exists()
    data = src.read_bytes()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(data)
    path_rel = _rel_of(ctx.root, f)
    # before/after=None 表示「未存原文」：revert_change 要求 before_content IS NOT NULL
    # 且按 UTF-8 文本比对磁盘，二进制过不了那道比对。沿 delete_file 既有口径明说不可回滚，
    # 不是遗漏——想支持字节级回滚要另设备份通道与迁移（ADR-0066）。
    await _record_change(ctx, path_rel, "edit" if existed else "create", None, None)
    return {"ok": True, "path": path_rel, "bytes": len(data),
            "notice": "产物未存原文，不可回滚；如需撤销请用 delete_file"}


def build_python_prompt() -> str:
    """`<python_sandbox>` 使用口径：与 run_python 同生同灭（SCOPE_ALWAYS，恒注入）。"""
    return "\n".join([
        "\n\n<python_sandbox>",
        "run_python 在隔离一次性容器里跑 Python 脚本，用于数据处理/计算/验证，两种模式二选一：",
        "- code：你给的源码字符串（python -c）。",
        "- skill_dir+script（+argv）：跑技能母本里的 .py 脚本——skill_dir 填 <available_skills> 索引的 path，",
        "  script 填技能目录内相对路径（如 scripts/run.py），技能目录只读挂在 /skill，跑的是母本原件；",
        "  脚本可用镜像预装库（见下），也可运行期 pip 装到 /scratch/pylibs；何时跑哪个脚本按 SKILL.md 说明判断。",
        "- 工作区只读挂在 /workspace（deploy/ 凭据不可读），脚本写只进 /scratch；",
        "  文本产物（CSV/JSON/SQL）工具回全文，再用 write_file 落盘工作区；",
        "  二进制产物（docx/xlsx/pptx/图片）用 import_artifact 落盘——name 填 artifacts 清单里的相对名、",
        "  path 填工作区目标路径；它只认本次 run 的产物，且落盘后不可回滚（要撤销用 delete_file）。",
        "- 容器**仅公网**出口、无数据库凭据：私网/本机/宿主端口/容器别名在网络层被拒（raw socket 也不行），",
        "  所以别在脚本里连库或访问平台服务；读写数据库走 db_query/db_write。",
        "- 预装库不够用时可 pip install --target /scratch/pylibs <包>，再 sys.path.insert(0, '/scratch/pylibs')；",
        "  装的包只活本次 run，下次调用要重装，所以优先用预装库、别反复装。",
        "- 改完跑一次即可，不反复跑；脚本异常看 output 里的 traceback 自己修。",
        "镜像已预装：python-docx（docx）、openpyxl（xlsx）、python-pptx（pptx）、pandas、numpy、",
        "requests、python-dateutil，import 即用，不必安装。办公文档只支持 OOXML 三件：",
        "- 读 .docx/.xlsx、生成 .docx/.xlsx/.pptx 都可以；生成的文件写 /scratch。",
        "- 老 .doc/.xls（旧二进制格式）不支持：明确告诉用户另存为 .docx/.xlsx 再来，",
        "  不要硬解析或猜内容。",
        "run_shell 在同一个沙箱里跑终端命令（经 sh -c，管道与重定向可用），围栏与 run_python 逐字相同：",
        "- 适合文件查看/批量操作/调命令行工具；数据处理与计算仍用 run_python。",
        "- 可用命令以沙箱镜像内已装的为限；运行期不支持 apt（系统包由平台预装进镜像）。",
        "  命令不存在就换 Python 做法或告诉用户缺哪个工具，别反复试。",
        "- 命令非零退出是正常结果，看 output 自己修；写只进 /scratch，产物同样经 write_file 落盘。",
        "</python_sandbox>",
    ])