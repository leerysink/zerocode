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
    if not isinstance(code, str) or not code.strip():
        return {"ok": False, "error": "code 必填：要执行的 Python 源码字符串"}
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
            "-w", "/workspace",
            SANDBOX_IMAGE, "python", "-c", code,
            timeout=RUN_TIMEOUT)
        artifacts = _scan_artifacts(scratch)
    except Exception as e:
        logger.exception("Python 沙箱执行异常")
        return {"ok": False, "error": _safe_error(e)}
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return {"ok": True, "exit_code": exit_code,
            "duration_seconds": round(time.monotonic() - t0, 1),
            "output": (log or "")[-OUTPUT_CHARS:],
            "artifacts": artifacts}


def build_python_prompt() -> str:
    """`<python_sandbox>` 使用口径：与 run_python 同生同灭（SCOPE_ALWAYS，恒注入）。"""
    return "\n".join([
        "\n\n<python_sandbox>",
        "run_python 在隔离一次性容器里跑你给的 Python 脚本（code 参数），用于数据处理/计算/验证：",
        "- 工作区只读挂在 /workspace（deploy/ 凭据不可读），脚本写只进 /scratch；",
        "  文本产物（CSV/JSON/SQL）工具回全文，再用 write_file 落盘工作区；二进制产物（图片/xlsx）暂不能落盘。",
        "- 容器无网络出口、无数据库凭据：脚本离线跑；要公网数据先 web_fetch/web_download 取到工作区再读，",
        "  读写数据库走 db_query/db_write，不要在脚本里连库。",
        "- 改完跑一次即可，不反复跑；脚本异常看 output 里的 traceback 自己修。",
        "</python_sandbox>",
    ])