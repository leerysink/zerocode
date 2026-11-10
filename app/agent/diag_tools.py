"""Agent 只读诊断工具：release_preflight / db_schema / sqlchange_status。

三者都只在应用工作区挂载（scope=app），且全部**只读**：不写盘、不跑构建、不起容器、不留痕。
判据不出第二份——preflight 直接渲染 `prod.preflight_checks(read_only=True)` 的结果，
schema 与迁移状态复用 `db_admin_service` / `sqlrun` 的同一套读法（与 REST 同源）。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def run_diag_tool(ctx, name: str, args: dict, *, confirmed: bool = False) -> dict:
    """统一入口（registry 的 handler_ref 指向这里）。

    任何失败都作为**正常结果**返回：loop 的兜底 except 会把它糊成「工具执行异常」，
    模型就读不到可行动的原因了。
    """
    args = args if isinstance(args, dict) else {}
    try:
        if name == "release_preflight":
            return await _preflight(ctx)
        if name == "db_schema":
            return await _db_schema(ctx, args)
        if name == "sqlchange_status":
            return await _sqlchange_status(ctx)
        return {"ok": False, "error": f"未知诊断工具: {name}"}
    except Exception as e:
        logger.exception("诊断工具 %s 执行异常", name)
        from app.agent.db_tools import _safe_error
        return {"ok": False, "error": _safe_error(e)}


async def _app_of(ctx):
    from app.agent.preview_tools import fetch_app
    return await fetch_app(ctx)


async def _preflight(ctx) -> dict:
    from app.deploy import engine, prod
    app = await _app_of(ctx)
    if app is None:
        return {"ok": False, "error": "应用已被删除或不可用"}
    root = await engine._app_root(ctx.session_factory, app)
    blocking: list[dict] = []
    fixable: list[dict] = []
    notes: list[str] = []
    # read_only=True：修复项只回答「补不补」，绝不碰工作区（修复仍走普通写工具的确认链）。
    async for item in prod.preflight_checks(ctx.session_factory, root, app,
                                            app.get("current_version"), read_only=True):
        if item["kind"] == "block":
            blocking.append({"code": item["code"], "message": item["message"]})
        elif item["kind"] == "fix":
            fixable.append({"code": item["code"], "message": item["report"]})
        else:
            notes.append(item["message"])
    return {"ok": True, "ready": not blocking, "blocking": blocking,
            "self_fixable": fixable, "notes": notes,
            "note": "发布前体检（只读）：blocking 必须先修；self_fixable 发布时会由平台自动补齐"}


async def _db_schema(ctx, args: dict) -> dict:
    from app.agent.db_tools import _connect
    from app.services import db_admin_service
    app = await _app_of(ctx)
    if app is None:
        return {"ok": False, "error": "应用已被删除或不可用"}
    env = str(args.get("env") or "dev")
    if env not in ("dev", "prod"):
        return {"ok": False, "error": "env 只接受 dev 或 prod"}
    table = str(args.get("table") or "").strip()
    async with _connect(ctx, app["slug"], env) as conn:
        if table:
            detail = await db_admin_service.schema_of(conn, table)
            return {"ok": True, "env": env, "table": table, **detail}
        tables = await db_admin_service.tables_of(conn)
    return {"ok": True, "env": env, "tables": tables, "count": len(tables)}


async def _sqlchange_status(ctx) -> dict:
    from app.deploy import engine, sqlrun
    from app.deploy.provision import read_cred
    from app.store.anchor import app_db_url
    app = await _app_of(ctx)
    if app is None:
        return {"ok": False, "error": "应用已被删除或不可用"}
    root = await engine._app_root(ctx.session_factory, app)
    d = root / "sqlchange"
    envs: dict[str, dict] = {}
    for env in sqlrun.SEGMENT_ENVS:
        files = sqlrun.segment_files(d, env) if d.is_dir() else []
        cred = await read_cred(ctx.session_factory, app["slug"], env)
        applied: dict[str, dict] = {}
        if cred:
            applied = {r["filename"]: r for r in await sqlrun.history_rows(
                app_db_url(cred), "SELECT filename, success FROM sqlchange_history")}
        envs[env] = {
            "files": len(files),
            "pending": [f for f in files if f not in applied],
            "failed": [f for f in files if applied.get(f, {}).get("success") is False],
            # 库没供给时 pending 等于「全部文件」并不代表真没跑过，如实标出来避免误读
            "credential": bool(cred),
        }
    return {"ok": True, "envs": envs,
            "note": "pending=未记账的文件；failed=记账过但 success=false（可重试）；"
                    "credential=false 表示该环境库尚未供给，状态不可知"}


def build_diag_prompt() -> str:
    """`<app_diagnostics>` 使用口径：与三个工具同生同灭（非应用工作区不注入）。"""
    return "\n".join([
        "\n\n<app_diagnostics>",
        "当前应用带有发布与结构诊断工具（全部只读，不改文件、不跑构建）：",
        "- release_preflight：发布前体检。先看 blocking（不修必失败），再看 self_fixable"
        "（发布时平台会自动补齐的工程接线，不必自己动手改）。用户问「能不能发布」先跑它，不要靠猜。",
        "- db_schema：看库结构。先不带 table 拿表清单，再带 table 看列/主键/索引；"
        "写 SQL 前先用它确认字段名与可空性，比让 db_query 撞报错快。",
        "- sqlchange_status：看数据轨各环境还差哪些迁移文件（pending）与执行失败项（failed）。"
        "改完 schema.sql 后，用 pending 自查有没有漏写 D/V 文件。",
        "</app_diagnostics>",
    ])
