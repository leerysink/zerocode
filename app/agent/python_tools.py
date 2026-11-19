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

logger = logging.getLogger(__name__)

SANDBOX_IMAGE = "python:3.12-slim"
OUTPUT_CHARS = 16_000
RUN_TIMEOUT = 120
ARTIFACT_BYTES_CAP = 64_000      # 单产物超此体积只回元数据
ARTIFACT_CONTENT_CHARS = 16_000  # 单产物全文回注上限
MAX_ARTIFACTS = 50


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


async def run_python_tool(ctx, args: dict, *, confirmed: bool = False) -> dict:
    """统一入口（registry 的 handler_ref 指向这里）；平台故障作为正常结果返回。"""
    from app.agent.db_tools import _safe_error
    from app.deploy import engine

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
    scratch = tempfile.mkdtemp(prefix="zc-pysandbox-")
    os.chmod(scratch, 0o777)   # 容器以非 root（1000）跑，scratch 须任意 uid 可写
    try:
        await engine.preflight_docker()
        t0 = time.monotonic()
        exit_code, log = await engine.docker(
            "run", "--rm", "--network", "none",
            "--user", "1000:1000",
            "--memory", "1g", "--cpus", "1", "--pids-limit", "256",
            "-v", f"{ctx.root}:/workspace:ro",
            "--tmpfs", "/workspace/deploy:ro",
            "-v", f"{scratch}:/scratch",
            *extra_mount,
            "-w", "/workspace",
            SANDBOX_IMAGE, *cmd,
            timeout=RUN_TIMEOUT)
        artifacts = _scan_artifacts(scratch)
    except Exception as e:
        logger.exception("Python 沙箱执行异常")
        return {"ok": False, "error": _safe_error(e)}
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    result = {"ok": True, "exit_code": exit_code,
              "duration_seconds": round(time.monotonic() - t0, 1),
              "output": (log or "")[-OUTPUT_CHARS:],
              "artifacts": artifacts}
    if script_rel is not None:
        result["script"] = script_rel
    return result


def build_python_prompt() -> str:
    """`<python_sandbox>` 使用口径：与 run_python 同生同灭（SCOPE_ALWAYS，恒注入）。"""
    return "\n".join([
        "\n\n<python_sandbox>",
        "run_python 在隔离一次性容器里跑 Python 脚本，用于数据处理/计算/验证，两种模式二选一：",
        "- code：你给的源码字符串（python -c）。",
        "- skill_dir+script（+argv）：跑技能母本里的 .py 脚本——skill_dir 填 <available_skills> 索引的 path，",
        "  script 填技能目录内相对路径（如 scripts/run.py），技能目录只读挂在 /skill，跑的是母本原件；",
        "  脚本只用纯标准库（容器离线，第三方库装不了），何时跑哪个脚本按 SKILL.md 说明判断。",
        "- 工作区只读挂在 /workspace（deploy/ 凭据不可读），脚本写只进 /scratch；",
        "  文本产物（CSV/JSON/SQL）工具回全文，再用 write_file 落盘工作区；二进制产物（图片/xlsx）暂不能落盘。",
        "- 容器无网络出口、无数据库凭据：脚本离线跑；要公网数据先 web_fetch/web_download 取到工作区再读，",
        "  读写数据库走 db_query/db_write，不要在脚本里连库。",
        "- 改完跑一次即可，不反复跑；脚本异常看 output 里的 traceback 自己修。",
        "</python_sandbox>",
    ])