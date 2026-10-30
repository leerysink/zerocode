# app/agent/db_tools.py
"""Agent 数据库工具（规格 docs/specs/2026-10-30-agent-db-verification-tools.md）。

两个工具、零命令面：db_query 只读，db_write 只打 dev 库；应用身份来自服务端解析
（复用 preview_tools 的 fetch_app——每次调用重读应用行，run 开始时的快照永不作数），
schema 里没有 app 参数，模型既指不到别的应用也指不到别人的应用。

失败一律作为**正常结果**返回（{"ok": False, "error": ...}）而不抛异常——模型需要读到
「凭据缺失」「表不存在」这类可行动的原因并向用户解释。执行护栏复用 db_admin_service：
单条走 execute_one（超时/库侧截断/提交时机只此一份），与查询控制台不各写一套。
"""
import contextlib
import datetime as dt
import json
import logging
import re
from decimal import Decimal

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app import config as app_config
from app.agent.preview_tools import fetch_app
from app.deploy.provision import read_cred
from app.services import db_admin_service
from app.services.memory_service import redact_sensitive

logger = logging.getLogger(__name__)

ERROR_TEXT_CHARS = 500   # 异常原文进模型上下文的限长（与预览工具的引擎异常口径同形）

DEFAULT_ROWS_LIMIT = 200                 # agent.db_rows_limit 的代码默认值
ROWS_LIMIT_MIN = 10
ROWS_LIMIT_MAX = 1000
DEFAULT_ROWS_ASSERT_MAX = 200            # agent.db_rows_assert_max：rows 断言两侧的行数上限
ROWS_DIFF_MAX = 10                       # 差异摘要只回前 10 条，不把整个不符集塞回模型
# 总量预算按**字符**计（与预览日志同一口径：模型成本按 token/字符算，中文下 1 字符≈3 字节，
# 按字节会让预算随语言忽大忽小）；行数预算先行，字符预算按整行收尾。
RESULT_TOTAL_CHARS = 16 * 1024


def _rows_limit_of(cfg: dict | None) -> int:
    """行预算：读 `agent.db_rows_limit`，字符串/畸形值回落默认，一律夹在 10..1000。"""
    raw = (cfg or {}).get("agent.db_rows_limit", DEFAULT_ROWS_LIMIT)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = DEFAULT_ROWS_LIMIT
    return max(ROWS_LIMIT_MIN, min(value, ROWS_LIMIT_MAX))


def _engine_url_for(cred: dict) -> str:
    """以平台引擎 URL 为底座换库/账号——与 db_admin_service.target_engine_url 同一口径，
    但 Agent 侧没有 request.app.state，从 config 直取底座。"""
    return (make_url(app_config.database_url())
            .set(database=cred["db"], username=cred["user"], password=cred["password"])
            .render_as_string(hide_password=False))


@contextlib.asynccontextmanager
async def _connect(ctx, slug: str, env: str):
    """按应用凭据开一条连接（用完即 dispose）。凭据缺失抛 DeployError，由入口收口成正常结果。"""
    from app.deploy.errors import DeployError
    cred = await read_cred(ctx.session_factory, slug, env)
    if not cred:
        raise DeployError(f"库 {slug}_{env} 凭据缺失（未供给或已删除）", status=409)
    eng = create_async_engine(_engine_url_for(cred))
    try:
        async with eng.connect() as conn:
            yield conn
    finally:
        await eng.dispose()


def _safe_error(raw) -> str:
    """异常原文进模型前过一道遮凭据 + 限长：建连失败的报错可能带 DSN，
    与 preview_tools._safe_text 同一红线（当轮回注模型的文本 loop 不再净化）。"""
    text_, _ = redact_sensitive(str(raw or ""))
    if len(text_) > ERROR_TEXT_CHARS:
        text_ = text_[:ERROR_TEXT_CHARS] + "…（已截断）"
    return text_


def _jsonable(v):
    """行值过 WS JSON 出口前的归一：datetime/date 用 isoformat，其余非原生标量转字符串。"""
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    if isinstance(v, (dt.datetime, dt.date, dt.time)):
        return v.isoformat()
    return str(v)


def _shape_rows(outcome: dict, rows_limit: int) -> dict:
    rows = [{k: _jsonable(v) for k, v in row.items()} for row in outcome["rows"]]
    kept = rows[:rows_limit]
    truncated_by = "rows" if len(rows) > len(kept) else None
    out, total = [], 0
    for row in kept:
        size = len(json.dumps(row, ensure_ascii=False))
        if out and total + size > RESULT_TOTAL_CHARS:
            # 至少保留第一行：一行超限的大字段也要让模型看到现场
            truncated_by = "chars"
            break
        out.append(row)
        total += size
    db_truncated = bool(outcome["truncated"])
    result = {"ok": True, "kind": outcome["kind"], "columns": outcome["columns"],
              "rows": out, "row_count": len(out),
              "truncated": db_truncated or truncated_by is not None}
    if truncated_by == "rows":
        result["truncated_by"] = "rows"
        result["note"] = "行数超过返回上限，仅回前 %d 行；请加 WHERE 收窄，或改用聚合/count 断言拿全量结论" % len(out)
    elif truncated_by == "chars":
        result["truncated_by"] = "chars"
        result["note"] = "结果字符总量超预算，已按整行截断；请减少返回列或收窄查询范围"
    elif db_truncated:
        result["note"] = "库侧已达内核行数硬上限（5000），仍有更多行未取回"
    return result


def _outcome_scalar(outcome: dict):
    """取结果第一行第一列（聚合类查询的形状）；空结果回 None。"""
    rows = outcome.get("rows") or []
    if not rows:
        return None
    return next(iter(rows[0].values()), None)


def _norm(v):
    """断言比对前的值归一：数值一律 float（1 ≡ "1" ≡ 1.0），文本去首尾空白，
    datetime/Decimal 先走 _jsonable 的字符串形态再归一——库回的类型与模型写的期望
    形状永远对不齐，比对必须在归一化空间里做。"""
    if v is None or isinstance(v, bool):
        return v
    if isinstance(v, (int, float, Decimal)):
        return float(v)
    if isinstance(v, (dt.datetime, dt.date, dt.time)):
        v = v.isoformat()
    text_ = str(v).strip()
    try:
        return float(text_)
    except ValueError:
        return text_


def _norm_row(row: dict) -> dict:
    return {k: _norm(v) for k, v in row.items()}


def _row_key(pair):
    return json.dumps(pair[0], sort_keys=True, ensure_ascii=False)


def compare_expect(etype: str, outcome: dict, expected):
    """期望值 vs 实际结果的机器判定——纯函数、不碰库，判定结论可复现（ADR-0028）。

    count 数的是服务端包出来的聚合值，与返回行预算无关；scalar 要求恰好单行单列，
    前提不满足回 error 而不是误导性的 passed；rows 两侧规范化排序后逐格比对，
    不符回前 ROWS_DIFF_MAX 条差异摘要（行号/列名/期望/实际，原始值便于转述）。
    """
    if etype == "count":
        actual = _outcome_scalar(outcome)
        actual_n = int(actual or 0)
        return {"passed": actual_n == int(expected), "actual": actual_n,
                "expected": int(expected)}
    rows = outcome.get("rows") or []
    if etype == "scalar":
        if len(rows) != 1 or len(rows[0]) != 1:
            return {"error": "scalar 断言要求查询结果恰好单行单列（请只 SELECT 一个聚合/标量列）"}
        actual = _norm(next(iter(rows[0].values())))
        return {"passed": actual == _norm(expected), "actual": actual,
                "expected": _norm(expected)}
    if etype == "rows":
        a_pairs = sorted(((_norm_row(r), r) for r in rows), key=_row_key)
        b_pairs = sorted(((_norm_row(r), r) for r in expected), key=_row_key)
        if len(a_pairs) != len(b_pairs):
            return {"passed": False, "diff": [{"row": None, "column": "(row_count)",
                                               "expected": len(b_pairs), "actual": len(a_pairs)}]}
        diffs = []
        for i, ((an, ao), (bn, bo)) in enumerate(zip(a_pairs, b_pairs)):
            if an == bn:
                continue
            for col in sorted(set(an) | set(bn)):
                if an.get(col) != bn.get(col):
                    diffs.append({"row": i, "column": col,
                                  "expected": bo.get(col), "actual": ao.get(col)})
            if len(diffs) >= ROWS_DIFF_MAX:
                break
        return {"passed": not diffs, "diff": diffs[:ROWS_DIFF_MAX]}
    raise ValueError(f"暂不支持的断言类型: {etype}")


async def _query(ctx, args: dict) -> dict:
    if not getattr(ctx, "app", None):
        # 非应用工作区本该看不到这个工具；直连调用时守住「不碰任何库」
        return {"ok": False, "error": "当前工作区不是应用工程，数据库工具不可用"}
    app = await fetch_app(ctx)
    if app is None:
        return {"ok": False, "error": "应用已被删除或不可用"}
    sql = str(args.get("sql") or "").strip()
    if not sql:
        return {"ok": False, "error": "db_query 需要一条 SQL"}
    env = str(args.get("env") or "dev")
    if env not in ("dev", "prod"):
        # 畸形 env 在连库前拒掉：绝不「猜一个环境」把查询打下去
        return {"ok": False, "error": "env 只接受 dev 或 prod"}
    # 只放行 select：内核 execute_one 只对 select 做库侧截断与超时；WITH/EXPLAIN 等
    # 其它读形态一律拒入并说明改写方式——闸门宁可窄，不给「看起来只读其实没护栏」的路径。
    if db_admin_service.classify_op(sql) != "select":
        return {"ok": False, "error": "db_query 只支持单条 SELECT 只读查询（首关键词需为 SELECT，"
                                      "WITH/VALUES 请改写；写语句请改用 db_write）"}
    expect = args.get("expect")
    if expect is not None:
        return await _query_assert(ctx, app, env, sql, expect)
    async with _connect(ctx, app["slug"], env) as conn:
        outcome = await db_admin_service.execute_one(conn, sql)
    return _shape_rows(outcome, _rows_limit_of(getattr(ctx, "cfg", None)))


def _assert_max_of(cfg: dict | None) -> int:
    raw = (cfg or {}).get("agent.db_rows_assert_max", DEFAULT_ROWS_ASSERT_MAX)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = DEFAULT_ROWS_ASSERT_MAX
    return max(1, min(value, ROWS_LIMIT_MAX))


def _validate_expect(cfg, expect) -> tuple[str, dict] | tuple[None, dict]:
    """断言入参在连库前收口：形状不对回一句可行动的 error。"""
    if not isinstance(expect, dict):
        return None, {"ok": False, "error": "expect 需是 {type, value} 对象"}
    etype = expect.get("type")
    if etype not in ("count", "scalar", "rows"):
        return None, {"ok": False, "error": "expect.type 只支持 count / scalar / rows"}
    if expect.get("value") is None:
        return None, {"ok": False, "error": f"expect.{etype} 必须给出 value（期望值）"}
    if etype == "count":
        try:
            int(expect["value"])
        except (TypeError, ValueError):
            return None, {"ok": False, "error": "count 断言的 value 需是整数"}
    if etype == "rows":
        value = expect["value"]
        if not isinstance(value, list) or not all(isinstance(r, dict) for r in value):
            return None, {"ok": False, "error": "rows 断言的 value 需是行对象数组"}
        limit = _assert_max_of(cfg)
        if len(value) > limit:
            return None, {"ok": False,
                          "error": f"期望行数 {len(value)} 超过断言上限 {limit}；"
                                   "请收窄查询，或改用 count/scalar 断言"}
    return etype, {"ok": True}


async def _query_assert(ctx, app: dict, env: str, sql: str, expect) -> dict:
    etype, gate = _validate_expect(getattr(ctx, "cfg", None), expect)
    if gate["ok"] is False:
        return gate
    if etype == "count":
        # 包裹口径同内核 execute_one：内层前后补换行，防 `--` 收尾注释吃掉右括号
        inner = sql.strip().rstrip(";")
        wrapped = f"SELECT count(*) FROM (\n{inner}\n) AS _zc_cnt"
        async with _connect(ctx, app["slug"], env) as conn:
            outcome = await db_admin_service.execute_one(conn, wrapped)
        verdict = compare_expect("count", outcome, expect["value"])
        return {"ok": True, "asserted": True, "expect_type": "count", **verdict}
    # scalar/rows：断言比对必须在**完整**结果集上做——截断集上的全行比对是假判定，
    # 所以实际行数超断言上限时宁可拒绝，让模型收窄查询（行预算只管目测路径）。
    async with _connect(ctx, app["slug"], env) as conn:
        outcome = await db_admin_service.execute_one(conn, sql)
    if etype == "rows":
        limit = _assert_max_of(getattr(ctx, "cfg", None))
        actual_n = len(outcome.get("rows") or [])
        if actual_n > limit:
            return {"ok": False, "error": f"结果行数 {actual_n} 超过断言上限 {limit}；"
                                          "请加 WHERE 收窄，或改用 count 断言"}
    verdict = compare_expect(etype, outcome, expect["value"])
    if "error" in verdict:
        return {"ok": False, "error": verdict["error"]}
    return {"ok": True, "asserted": True, "expect_type": etype, **verdict}


# ---------- db_write：dev 写通道（ADR-0027） ----------

# DATABASE/实例级操作绝不给 Agent：整库级破坏不可回滚，连让用户点确认的机会都不该有。
# 在剥掉注释的文本上做子串匹配——字符串字面量里的 'drop database' 会被误拒，
# 误拒可改写，误放不可回收，方向刻意偏保守。
_DB_LEVEL_RE = re.compile(r"\b(drop|create)\s+database\b|\balter\s+system\b|\bshutdown\b", re.I)


def _strip_comments(sql: str) -> str:
    return db_admin_service._BLOCK_COMMENT_RE.sub(
        " ", db_admin_service._LINE_COMMENT_RE.sub(" ", sql))


def _db_level_forbidden(sql: str) -> bool:
    return bool(_DB_LEVEL_RE.search(_strip_comments(sql)))


_DANGER_DDL_RE = re.compile(r"\b(drop|truncate)\b", re.I)


def db_confirm_summary(statements) -> str:
    """确认卡的人话摘要：逐条 op 徽章；无 WHERE 的 update/delete 与 DROP/TRUNCATE 标【危险】。

    给用户看的是「将发生什么」，不给模型看参数 JSON——与 preview 的 confirm_summary 同一风格。"""
    lines = ["将对应用的 dev 数据库执行写入（不可回滚）："]
    shown = 0
    for i, s in enumerate(statements or []):
        if not isinstance(s, str) or not db_admin_service.has_executable_content(s):
            continue
        if shown >= 12:
            lines.append("…（其余语句略）")
            break
        op = db_admin_service.classify_op(s)
        danger = ((op in ("update", "delete") and not re.search(r"\bwhere\b", s, re.I))
                  or (op == "ddl" and bool(_DANGER_DDL_RE.search(s))))
        one_line = " ".join(s.split())
        if len(one_line) > 120:
            one_line = one_line[:120] + "…"
        lines.append(f"{i + 1}. [{op}]{'【危险】' if danger else ''} {one_line}")
        shown += 1
    if shown == 0:
        # 确认卡不能是空的：语句全不可执行时让用户在点确认前就看见问题
        lines.append("（没有可执行的语句）")
    return "\n".join(lines)


def _shape_write(results: list[dict]) -> dict:
    """逐条结果整形给模型：skipped 说人话（未执行），RETURNING 不回显行（走 db_query 复核）。"""
    items, ok_n, err_at = [], 0, None
    for it in results:
        item = {"index": it["index"], "op": it["op"], "status": it["status"]}
        if it["status"] == "ok":
            ok_n += 1
            if it.get("kind") == "affected":
                item["affected"] = it["affected"]
            else:
                item["returned_rows"] = len(it.get("rows") or [])
                item["note"] = "RETURNING 结果未回显，需要核对数据请用 db_query"
        elif it["status"] == "error":
            item["error"] = it["error"]
            if err_at is None:
                err_at = it["index"] + 1
        else:
            item["status"] = "skipped"
            item["note"] = "未执行（前面有语句失败）"
        items.append(item)
    skipped_n = sum(1 for it in items if it["status"] == "skipped")
    if err_at is not None:
        summary = f"共 {len(items)} 条可执行语句：成功 {ok_n}，失败于第 {err_at} 条，{skipped_n} 条未执行"
    else:
        summary = f"共 {len(items)} 条可执行语句全部成功"
    return {"ok": True, "results": items, "summary": summary}


async def _write(ctx, args: dict, *, confirmed: bool) -> dict:
    if not getattr(ctx, "app", None):
        return {"ok": False, "error": "当前工作区不是应用工程，数据库工具不可用"}
    app = await fetch_app(ctx)
    if app is None:
        return {"ok": False, "error": "应用已被删除或不可用"}
    statements = args.get("statements")
    if (not isinstance(statements, list) or not statements
            or not all(isinstance(s, str) for s in statements)):
        return {"ok": False, "error": "db_write 需要 statements：SQL 字符串数组"}
    if not any(db_admin_service.has_executable_content(s) for s in statements):
        return {"ok": False, "error": "没有可执行的语句"}
    for s in statements:
        if _db_level_forbidden(s):
            return {"ok": False,
                    "error": "DATABASE 级/实例级操作不对 Agent 开放（DROP/CREATE DATABASE、ALTER SYSTEM 等）"}
    if not confirmed:
        # 正常路径由 loop._dispatch 先走确认卡；这里是直连调用的兜底闸
        return {"ok": False, "error": "数据库写入需要用户确认，本次未经确认流程"}
    async with _connect(ctx, app["slug"], "dev") as conn:
        results = await db_admin_service.run_batch_on(conn, statements)
    # 审计逐条落、skipped 不留痕；actor 记应用 owner（Agent 只能以 owner 身份到达这里），
    # source=agent + session_id 与人工操作台区分（US-16）。
    await db_admin_service.audit_batch(
        ctx.session_factory, app_id=app["id"], actor_user_id=app.get("owner_user_id"),
        env="dev", results=results, source="agent", session_id=ctx.session_id)
    return _shape_write(results)


async def run_db_tool(ctx, name: str, args: dict, *, confirmed: bool = False) -> dict:
    """数据库工具统一入口（tools.execute_tool 分派分支惰性 import 本函数）。

    任何失败都作为正常结果返回：loop 的兜底 except 会把它糊成「工具执行异常」，
    模型就读不到可行动的原因了。
    """
    args = args if isinstance(args, dict) else {}
    try:
        if name == "db_query":
            return await _query(ctx, args)
        if name == "db_write":
            return await _write(ctx, args, confirmed=confirmed)
        return {"ok": False, "error": f"未知数据库工具: {name}"}
    except Exception as e:
        logger.exception("数据库工具 %s 执行异常", name)
        return {"ok": False, "error": _safe_error(e)}


def build_db_prompt() -> str:
    """`<database>` 使用口径：只在应用工作区（工具已挂载）时注入，与 <preview> 并列。

    工具描述随每次请求发送、刻意一句话；「怎么用来下结论」的教程写在这里。核心是三件事：
    结论靠机器断言不靠目测、截断结果不数行、写库会被用户逐条过目（ADR-0028）。"""
    lines = [
        "\n\n<database>",
        "当前应用带有数据库：db_query 只读查询（dev/prod 均可），db_write 只写 dev 库且每次需用户确认。",
        "- 要下「数据对不对」的结论时，优先用 db_query 的 expect 机器断言（count/scalar/rows 三档），"
        "工具会回 passed 与差异摘要；不要靠目测大结果集下结论。",
        "- 结果里 truncated=true 表示被行/字符预算截断：不得据此判断总行数，"
        "请加 WHERE 收窄，或用 count 断言拿全量结论。",
        "- 验证数据的套路：db_write 灌入测试数据 → 让用户或代码操作应用 → db_query+expect 断言库内状态。",
        "- db_write 前想清楚每条语句：用户会在确认卡里逐条过目，无 WHERE 的 UPDATE/DELETE 与 DROP/TRUNCATE 会标危险。",
        "- prod 库永远只读；「重建 dev 库」等整库级操作不在你的工具面里，需要时请用户到操作台执行。",
        "</database>",
    ]
    return "\n".join(lines)
