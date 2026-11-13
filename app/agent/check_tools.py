"""Agent 预设自检：`run_check` 只在一次性容器里跑平台写死的检查项（ADR-0046）。

模型能给的只有 preset 名，命令/参数/路径全部由这里按 stack 与工程事实决定——它是**不是**命令
模板，新增一项必须改这份代码。执行载体与迁移容器同构：`docker compose run --rm --no-deps -T`，
复用 dev 栈的 node_modules / .m2 具名卷，冷启动之后才快；但**不**依赖 dev 栈在跑。

结果是报告而非闸门：检查未通过返回 `ok=True, passed=False`（模型按输出里的 file:line 去修），
只有平台侧故障（docker 不可用、超时、deploy/ 还没渲染）才是 `ok=False`。
"""
from __future__ import annotations

import json
import logging
import time

logger = logging.getLogger(__name__)

PRESETS = ("frontend_typecheck", "backend_compile")
OUTPUT_CHARS = 8_000
RUN_TIMEOUT = 900        # 冷启动 + npm install / mvn 首次下载都可能上分钟

_TS_CHECK = "npm install --no-audit --no-fund --loglevel=error && npx vue-tsc --noEmit"
# Node 小栈的后端类型检查：与工程自己的 `npm run typecheck` 同一条命令（模板 package.json 里就是
# tsc --noEmit），所以模型自检红了与本地跑红的会是同一批 file:line。
_BE_TS_CHECK = "npm install --no-audit --no-fund --loglevel=error && npx tsc --noEmit"
_FE_BUILD = ("npm install --no-audit --no-fund --loglevel=error && "
             "npx vite build --outDir /tmp/zc-check-dist --emptyOutDir")
_FALLBACK_NOTE = ("工程未装类型检查器（vue-tsc），已降级为 vite build 编译检查"
                  "（产物写容器内 /tmp，不落工作区）")


class _CheckError(Exception):
    """检查项无法执行（不是「检查没通过」）：文案直接回给模型。"""


def _frontend_check(root) -> dict:
    pkg = root / "frontend" / "package.json"
    if not pkg.is_file():
        raise _CheckError("frontend/package.json 不存在，无法做前端检查")
    raw = pkg.read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except ValueError:
        data = {}
    dev = data.get("devDependencies") or {}
    scripts = data.get("scripts") or {}
    has_tsc = "vue-tsc" in dev or "vue-tsc" in scripts.get("build", "")
    if has_tsc:
        return {"service": "frontend", "argv": ["sh", "-c", _TS_CHECK]}
    return {"service": "frontend", "argv": ["sh", "-c", _FE_BUILD], "note": _FALLBACK_NOTE}


def _backend_check(root, stack: str) -> dict:
    if stack in ("vue3-springboot", "vue3-springboot-mobile"):
        if not (root / "backend" / "pom.xml").is_file():
            raise _CheckError("backend/pom.xml 不存在，无法编译 Java 后端")
        return {"service": "backend", "argv": ["mvn", "-B", "-DskipTests", "compile"]}
    if stack == "vue3-node":
        if not (root / "backend" / "tsconfig.json").is_file():
            raise _CheckError("backend/tsconfig.json 不存在，无法执行 TypeScript 类型检查")
        return {"service": "backend", "argv": ["sh", "-c", _BE_TS_CHECK]}
    if stack == "vue3-fastapi":
        if not (root / "backend" / "app").is_dir():
            raise _CheckError("backend/app 不存在，无法编译 Python 后端")
        return {"service": "backend", "argv": ["python", "-m", "compileall", "-q", "app"]}
    raise _CheckError(f"该技术栈没有后端编译预设：{stack}")


def _build(preset: str, root, stack: str) -> dict:
    if preset == "frontend_typecheck":
        return _frontend_check(root)
    return _backend_check(root, stack)


async def run_check_tool(ctx, name: str, args: dict, *, confirmed: bool = False) -> dict:
    """统一入口（registry 的 handler_ref 指向这里）；所有失败都作为正常结果返回。"""
    from app.agent.db_tools import _safe_error
    from app.deploy import engine
    from app.agent.diag_tools import _app_of

    args = args if isinstance(args, dict) else {}
    preset = args.get("preset")
    if preset not in PRESETS:
        return {"ok": False,
                "error": f"preset 只接受 {' / '.join(PRESETS)}，收到 {preset!r}；"
                         "检查项由平台写死，不能传命令或参数"}
    try:
        app = await _app_of(ctx)
        if app is None:
            return {"ok": False, "error": "应用已被删除或不可用"}
        root = await engine._app_root(ctx.session_factory, app)
        stack = app.get("stack") or "vue3-fastapi"
        compose = root / "deploy" / "dev-compose.yml"
        if not compose.is_file():
            return {"ok": False,
                    "error": "deploy/ 还没渲染：请先启动一次预览，让平台生成 dev-compose 与依赖卷"}
        spec = _build(preset, root, stack)
        await engine.preflight_docker()
        t0 = time.monotonic()
        code, log = await engine.docker(
            "compose", "-f", str(compose), "run", "--rm", "--no-deps", "-T",
            spec["service"], *spec["argv"], cwd=str(root), timeout=RUN_TIMEOUT)
    except Exception as e:
        logger.exception("预设自检 %s 执行异常", preset)
        return {"ok": False, "error": _safe_error(e)}

    out = {"ok": True, "preset": preset, "stack": stack, "service": spec["service"],
           "passed": code == 0, "exit_code": code,
           "duration_seconds": round(time.monotonic() - t0, 1),
           "output": (log or "")[-OUTPUT_CHARS:]}
    if spec.get("note"):
        out["note"] = spec["note"]
    return out


def build_check_prompt() -> str:
    """`<app_checks>` 使用口径：与 run_check 同生同灭。"""
    return "\n".join([
        "\n\n<app_checks>",
        "run_check 在一次性容器里跑平台写死的检查项，只能选 preset、不能传命令：",
        "- frontend_typecheck：前端类型/编译检查（工程有 vue-tsc 就用它，否则降级为 vite build）；",
        "- backend_compile：后端编译（Python 走 compileall，TypeScript 走 tsc --noEmit，Java 走 mvn compile）。",
        "改完一段代码后跑一次即可，冷启动几十秒到几分钟；按输出里的 file:line 自己修，"
        "不要反复跑同一个检查。结果只作参考，不是发布闸门。",
        "</app_checks>",
    ])
