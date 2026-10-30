# tests/test_db_tools.py
"""Agent 数据库工具（规格 docs/specs/2026-10-30-agent-db-verification-tools.md）。

库侧一律替身：本文件验「工具怎么解析应用身份、怎么整形结果、怎么收口失败」，
真连库的护栏（超时/截断/串行遇错即停）由 tests/test_db_admin.py 既有用例覆盖。
"""
import asyncio
import contextlib
import datetime

import pytest

from app.agent import db_tools

pytestmark = pytest.mark.asyncio

APP = {"id": 1, "slug": "demoapp", "name": "演示应用", "owner_user_id": 7}


class Ctx:
    """最小 ToolContext 替身（同 test_preview_tools 的桩风格）。"""

    def __init__(self, app=None, *, sf=None, cfg=None):
        self.app = app
        self.session_factory = sf
        self.skills_root = None
        self.send = lambda ev: asyncio.sleep(0)
        self.cfg = cfg or {}
        self.cancel = None
        self.deploy_hub = None
        self.deploy_tasks = {}
        self.run_state = {}
        self.session_id = 1


class FakeResult:
    def __init__(self, columns=None, rows=None, rowcount=0, returns_rows=True):
        self._columns = columns or []
        self._rows = rows or []
        self.returns_rows = returns_rows
        self.rowcount = rowcount

    def keys(self):
        return list(self._columns)

    def fetchall(self):
        return list(self._rows)


class FakeConn:
    """记录送进来的 SQL，按队列吐出 FakeResult。"""

    def __init__(self, *results):
        self.results = list(results)
        self.calls = []
        self.committed = 0

    async def exec_driver_sql(self, sql, params=None):
        self.calls.append(sql)
        # execute_one 会先发 SET statement_timeout——护栏设置不吃队列结果
        if sql.strip().upper().startswith("SET "):
            return FakeResult(returns_rows=False, rowcount=0)
        if self.results:
            return self.results.pop(0)
        return FakeResult(returns_rows=False, rowcount=0)

    async def commit(self):
        self.committed += 1


@contextlib.asynccontextmanager
async def _conn_cm(conn):
    yield conn


class FakeSF:
    """平台库会话工厂替身：收下 log_operation 的 INSERT 参数供断言。"""

    def __init__(self):
        self.audits = []

    def __call__(self):
        return _FakeSession(self.audits)


class _FakeSession:
    def __init__(self, sink):
        self._sink = sink

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, stmt, params=None):
        self._sink.append(params or {})

    async def commit(self):
        pass


@pytest.fixture
def wired(monkeypatch):
    """桩掉「每次调用重读应用行」与「连库」两个外部依赖，返回可配置的 FakeConn。"""
    conn = FakeConn()
    conn.env_calls = []

    async def fake_fetch(ctx):
        return APP

    def fake_connect(ctx, slug, env):
        conn.env_calls.append((slug, env))
        return _conn_cm(conn)

    monkeypatch.setattr(db_tools, "fetch_app", fake_fetch)
    monkeypatch.setattr(db_tools, "_connect", fake_connect)
    return conn


# ---------- db_query：dev 只读查询 ----------

async def test_db_query_returns_columns_and_rows_for_select(wired):
    """US-01：Agent 对当前应用 dev 库跑一条 SELECT，拿到列名、行数据与行数。"""
    wired.results.append(FakeResult(["id", "name"], [(1, "widget"), (2, "gadget")]))
    r = await db_tools.run_db_tool(Ctx(app=APP), "db_query",
                                   {"sql": "select id, name from widgets"})
    assert r["ok"] is True
    assert r["kind"] == "rows"
    assert r["columns"] == ["id", "name"]
    assert r["rows"] == [{"id": 1, "name": "widget"}, {"id": 2, "name": "gadget"}]
    assert r["row_count"] == 2


async def test_db_query_refuses_write_statement_without_executing(wired):
    """US-03（提前落地）：db_query 里写语句被语句级判定拒绝，一条都不送进库。"""
    r = await db_tools.run_db_tool(Ctx(app=APP), "db_query",
                                   {"sql": "update widgets set name = 'x'"})
    assert r["ok"] is False
    assert "db_write" in r["error"]
    assert not [c for c in wired.calls if not c.strip().upper().startswith("SET ")]


async def test_db_query_requires_sql(wired):
    """空参数是模型的常见失误：回一句可行动的提示，不碰库。"""
    r = await db_tools.run_db_tool(Ctx(app=APP), "db_query", {})
    assert r["ok"] is False and r["error"]
    assert wired.calls == []


async def test_db_query_reports_execution_failure_as_normal_result(wired, monkeypatch):
    """US-06：语法错/表不存在这类库侧失败，原文作为正常结果回给模型，不抛异常。"""
    async def boom(conn, sql):
        raise RuntimeError('relation "nope" does not exist')
    monkeypatch.setattr(db_tools.db_admin_service, "execute_one", boom)
    r = await db_tools.run_db_tool(Ctx(app=APP), "db_query", {"sql": "select * from nope"})
    assert r["ok"] is False
    assert 'relation "nope" does not exist' in r["error"]


async def test_db_query_fails_soft_when_app_row_gone(wired, monkeypatch):
    """US-19：run 中途应用被删——每次调用重读的应用行读不到时回业务文案，不打挂整轮对话。"""
    async def gone(ctx):
        return None
    monkeypatch.setattr(db_tools, "fetch_app", gone)
    r = await db_tools.run_db_tool(Ctx(app=APP), "db_query", {"sql": "select 1"})
    assert r["ok"] is False and "已被删除" in r["error"]


async def test_db_query_needs_app_workspace(monkeypatch):
    """US-20 的 handler 侧兜底：ctx.app 为空（非应用工作区被直连调用）不碰任何库。"""
    called = []

    async def spy(ctx):
        called.append(1)
        return APP
    monkeypatch.setattr(db_tools, "fetch_app", spy)
    r = await db_tools.run_db_tool(Ctx(app=None), "db_query", {"sql": "select 1"})
    assert r["ok"] is False
    assert called == []


async def test_db_query_normalizes_non_json_values(wired):
    """结果要过 WS JSON 出口：datetime/Decimal 这类值统一转字符串，模型与前端都拿得住。"""
    import datetime as dt
    from decimal import Decimal
    wired.results.append(FakeResult(["ts", "price"], [(dt.datetime(2026, 10, 30, 9, 0), Decimal("3.5"))]))
    r = await db_tools.run_db_tool(Ctx(app=APP), "db_query", {"sql": "select ts, price from widgets"})
    assert r["rows"] == [{"ts": "2026-10-30T09:00:00", "price": "3.5"}]


# ---------- 集成接线 ----------

async def test_execute_tool_routes_db_query_to_run_db_tool(monkeypatch):
    """tools.execute_tool 的分派分支把 db_* 转给本模块（惰性 import，不成环）。"""
    from app.agent import tools
    seen = {}

    async def fake_run(ctx, name, args, *, confirmed=False):
        seen.update(name=name, confirmed=confirmed)
        return {"ok": True, "routed": name}

    monkeypatch.setattr(db_tools, "run_db_tool", fake_run)
    r = await tools.execute_tool(Ctx(app=APP), "db_query", {"sql": "select 1"})
    assert r == {"ok": True, "routed": "db_query"}
    assert seen["name"] == "db_query" and seen["confirmed"] is False


# ---------- env 闸门（工单 002） ----------

async def test_db_query_defaults_to_dev(wired):
    """缺省不猜：env 不给就是 dev——模型最常见的用法不需要额外心智。"""
    wired.results.append(FakeResult(["n"], [(1,)]))
    r = await db_tools.run_db_tool(Ctx(app=APP), "db_query", {"sql": "select count(*) as n from t"})
    assert r["ok"] is True
    assert wired.env_calls == [("demoapp", "dev")]


async def test_db_query_targets_prod_readonly_when_asked(wired):
    """US-02：prod 只是另一个只读环境，env 参数决定连哪套凭据；语句闸门与 dev 同一把锁。"""
    wired.results.append(FakeResult(["n"], [(7,)]))
    r = await db_tools.run_db_tool(Ctx(app=APP), "db_query",
                                   {"sql": "select count(*) as n from t", "env": "prod"})
    assert r["ok"] is True
    assert wired.env_calls == [("demoapp", "prod")]


async def test_db_query_rejects_unknown_env_without_connecting(wired):
    """env 只认 dev|prod：畸形值在连库之前就拒，绝不猜一个环境打过去。"""
    r = await db_tools.run_db_tool(Ctx(app=APP), "db_query",
                                   {"sql": "select 1", "env": "pre"})
    assert r["ok"] is False
    assert wired.env_calls == []


def test_db_query_schema_exposes_env_enum():
    """US-02/US-20 的 schema 层证据：只有 sql/env/expect——没有 app、没有命令面，
    应用身份只能由服务端解析。"""
    from app.agent.llm import DB_TOOLS
    q = [t for t in DB_TOOLS if t["function"]["name"] == "db_query"][0]
    params = q["function"]["parameters"]
    assert set(params["properties"]) == {"sql", "env", "expect"}
    assert params["properties"]["env"]["enum"] == ["dev", "prod"]
    assert params["required"] == ["sql"]


# ---------- 模型上下文预算（工单 003） ----------

async def test_db_query_caps_rows_at_default_limit(wired):
    """US-04：超过默认行预算（200）只回前 200 行，标 truncated 并给一句可行动的引导。"""
    wired.results.append(FakeResult(["i"], [(n,) for n in range(300)]))
    r = await db_tools.run_db_tool(Ctx(app=APP), "db_query", {"sql": "select i from big"})
    assert r["row_count"] == 200 and len(r["rows"]) == 200
    assert r["truncated"] is True
    assert r["truncated_by"] == "rows"
    assert "db_write" not in r["note"] and r["note"]     # 提示语存在且与写无关


@pytest.mark.parametrize("raw,expected", [
    ("50", 50), (99999, 1000), (3, 10), ("abc", 200), (None, 200),
])
async def test_rows_limit_reads_config_and_clamps(wired, raw, expected):
    """行预算走配置且一律夹住：字符串/越界/畸形值都不把异常抛给模型。"""
    wired.results.append(FakeResult(["i"], [(n,) for n in range(1500)]))
    cfg = {"agent.db_rows_limit": raw} if raw is not None else {}
    r = await db_tools.run_db_tool(Ctx(app=APP, cfg=cfg), "db_query", {"sql": "select i from big"})
    assert r["row_count"] == expected


async def test_db_query_char_budget_cuts_whole_rows(wired):
    """US-05：行没超但字符总量超预算——按整行边界从尾部丢，至少保留第一行，另标 truncated_by=chars。"""
    fat = "x" * 3000
    wired.results.append(FakeResult(["big"], [(fat,)] * 10))
    r = await db_tools.run_db_tool(Ctx(app=APP), "db_query", {"sql": "select big from t"})
    assert 0 < r["row_count"] < 10
    assert r["truncated"] is True and r["truncated_by"] == "chars"


async def test_db_query_keeps_small_result_unmarked(wired):
    """预算没碰到就不制造噪声：不带 truncated/truncated_by/note。"""
    wired.results.append(FakeResult(["i"], [(1,)]))
    r = await db_tools.run_db_tool(Ctx(app=APP), "db_query", {"sql": "select i from t"})
    assert r["truncated"] is False
    assert "truncated_by" not in r and "note" not in r


# ---------- 机器断言（工单 004/005） ----------

def test_compare_expect_count_table_driven():
    """判定引擎是纯函数：count 档按整数比较，形状独立于执行路径。"""
    assert db_tools.compare_expect("count", {"rows": [{"c": 5}]}, 5)["passed"] is True
    assert db_tools.compare_expect("count", {"rows": [{"c": 4}]}, 5)["passed"] is False
    # actual 是库回的单行单列聚合值——引擎自己取数，调用方不必先拆形状
    assert db_tools.compare_expect("count", {"rows": []}, 0)["passed"] is True


async def test_count_assertion_wraps_sql_at_db_side(wired):
    """US-07：count 断言由服务端包 `SELECT count(*) FROM (...)` 判定——结果即便少于/多于
    行预算，判定数的是全量命中，不数返回行。"""
    wired.results.append(FakeResult(["count"], [(3,)]))
    r = await db_tools.run_db_tool(
        Ctx(app=APP), "db_query",
        {"sql": "select id from widgets where state = 'ok'",
         "expect": {"type": "count", "value": 3}})
    assert r["ok"] is True and r["asserted"] is True and r["passed"] is True
    assert r["actual"] == 3 and r["expected"] == 3
    sent = [c for c in wired.calls if not c.strip().upper().startswith("SET ")]
    assert any("count(*)" in c.lower() and "widgets" in c for c in sent)


async def test_count_assertion_mismatch_reports_actual(wired):
    """断言不符是**正常结果**不是错误：passed=False + 实际值，模型据此向用户解释差异。"""
    wired.results.append(FakeResult(["count"], [(7,)]))
    r = await db_tools.run_db_tool(
        Ctx(app=APP), "db_query",
        {"sql": "select id from widgets", "expect": {"type": "count", "value": 3}})
    assert r["ok"] is True and r["passed"] is False
    assert r["actual"] == 7 and r["expected"] == 3


async def test_expect_without_value_is_refused_before_hitting_db(wired):
    """畸形 expect 在连库前拒：判定没有「期望值」就没有意义。"""
    r = await db_tools.run_db_tool(
        Ctx(app=APP), "db_query",
        {"sql": "select id from widgets", "expect": {"type": "count"}})
    assert r["ok"] is False and "expect" in r["error"]
    assert wired.env_calls == []


async def test_no_expect_returns_plain_result(wired):
    """US-11：不带 expect 的结果里没有任何判定字段——目测归模型，机器判定归断言。"""
    wired.results.append(FakeResult(["i"], [(1,)]))
    r = await db_tools.run_db_tool(Ctx(app=APP), "db_query", {"sql": "select i from t"})
    assert "asserted" not in r and "passed" not in r


# ---------- 断言 scalar / rows（工单 005） ----------

def test_compare_expect_scalar_is_type_lenient():
    """scalar 比较做数值归一：1 与 "1"、2.0 与 "2" 等价；文本去首尾空白后比较。"""
    one = {"rows": [{"total": 1}]}
    assert db_tools.compare_expect("scalar", one, "1")["passed"] is True
    assert db_tools.compare_expect("scalar", one, 1.0)["passed"] is True
    assert db_tools.compare_expect("scalar", one, 2)["passed"] is False
    paid = {"rows": [{"state": " 已支付 "}]}
    assert db_tools.compare_expect("scalar", paid, "已支付")["passed"] is True


def test_compare_expect_scalar_rejects_non_singleton():
    """US-08：结果不是恰好单行单列 → 报前提不满足，而不是给出一个误导性的 passed。"""
    for bad in ({"rows": []}, {"rows": [{"a": 1}, {"a": 2}]}, {"rows": [{"a": 1, "b": 2}]}):
        v = db_tools.compare_expect("scalar", bad, 1)
        assert "error" in v, bad


def test_compare_expect_rows_is_order_insensitive():
    """rows 档：两侧规范化排序后全行比对——查询没写 ORDER BY 也不误报。"""
    actual = {"rows": [{"id": 2, "name": "b"}, {"id": 1, "name": "a"}]}
    expected = [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}]
    assert db_tools.compare_expect("rows", actual, expected)["passed"] is True


def test_compare_expect_rows_diff_summary():
    """不符时回差异摘要：行号 + 列名 + 期望/实际，模型能直接转述给用户。"""
    actual = {"rows": [{"id": 1, "name": "a"}, {"id": 2, "name": "WRONG"}]}
    expected = [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}]
    v = db_tools.compare_expect("rows", actual, expected)
    assert v["passed"] is False
    assert v["diff"] == [{"row": 1, "column": "name", "expected": "b", "actual": "WRONG"}]


def test_compare_expect_rows_length_mismatch():
    """行数不同直接判不符，diff 里给一条行数差异说明。"""
    actual = {"rows": [{"id": 1}]}
    v = db_tools.compare_expect("rows", actual, [{"id": 1}, {"id": 2}])
    assert v["passed"] is False
    assert any(d.get("column") == "(row_count)" for d in v["diff"])


async def test_scalar_assertion_end_to_end(wired):
    """US-08：单行单列查询 + scalar 断言 → passed；值归一化后比对。"""
    wired.results.append(FakeResult(["total"], [("42",)]))
    r = await db_tools.run_db_tool(
        Ctx(app=APP), "db_query",
        {"sql": "select count(*) as total from t", "expect": {"type": "scalar", "value": 42}})
    assert r["ok"] is True and r["passed"] is True and r["actual"] == 42.0


async def test_rows_assertion_reports_diff(wired):
    """US-09：rows 不符回差异摘要——ok 仍是 True（工具没坏），passed 是 False（数据不对）。"""
    wired.results.append(FakeResult(["id", "name"], [(1, "a"), (2, "WRONG")]))
    r = await db_tools.run_db_tool(
        Ctx(app=APP), "db_query",
        {"sql": "select id, name from t",
         "expect": {"type": "rows", "value": [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}]}})
    assert r["ok"] is True and r["passed"] is False
    assert r["diff"] == [{"row": 1, "column": "name", "expected": "b", "actual": "WRONG"}]


async def test_rows_assertion_normalizes_db_value_types(wired):
    """库里回 datetime/Decimal，期望写字符串/数字——归一化后照样判等。"""
    import datetime as dt
    from decimal import Decimal
    wired.results.append(FakeResult(["ts", "price"], [(dt.datetime(2026, 10, 30, 9, 0), Decimal("3.5"))]))
    r = await db_tools.run_db_tool(
        Ctx(app=APP), "db_query",
        {"sql": "select ts, price from t",
         "expect": {"type": "rows", "value": [{"ts": "2026-10-30T09:00:00", "price": 3.5}]}})
    assert r["ok"] is True and r["passed"] is True


async def test_rows_expect_over_limit_refused_before_db(wired):
    """US-10：期望行数超断言上限（默认 200）在连库前拒，并指路 count/scalar。"""
    huge = [{"id": n} for n in range(201)]
    r = await db_tools.run_db_tool(
        Ctx(app=APP), "db_query",
        {"sql": "select id from t", "expect": {"type": "rows", "value": huge}})
    assert r["ok"] is False
    assert "count" in r["error"] and wired.env_calls == []


async def test_rows_assertion_refuses_when_actual_over_limit(wired):
    """实际行数超断言上限也拒比：截断集上的"全行比对"是假判定，宁可让模型收窄查询。"""
    wired.results.append(FakeResult(["i"], [(n,) for n in range(250)]))
    r = await db_tools.run_db_tool(
        Ctx(app=APP, cfg={"agent.db_rows_assert_max": 200}), "db_query",
        {"sql": "select i from t", "expect": {"type": "rows", "value": [{"i": 0}]}})
    assert r["ok"] is False and "断言" in r["error"]


# ---------- db_write（工单 006） ----------

WRITE_APP = APP   # 写路径与查询路径同一应用行（含 owner_user_id 供审计）


async def test_db_write_requires_confirmation(wired):
    """US-12 的 handler 侧兜底：confirmed 未经确认流为 False 时一条不执行。"""
    r = await db_tools.run_db_tool(Ctx(app=WRITE_APP), "db_write",
                                   {"statements": ["insert into t values (1)"]})
    assert r["ok"] is False and "确认" in r["error"]
    assert wired.env_calls == []


async def test_db_write_executes_serially_and_reports_each(wired):
    """US-13：批准后同一连接逐条执行，逐条回 index/op/status/affected。"""
    r = await db_tools.run_db_tool(
        Ctx(app=WRITE_APP, sf=FakeSF()), "db_write",
        {"statements": ["insert into t values (1)", "update t set a = 2 where id = 1"]},
        confirmed=True)
    assert r["ok"] is True
    assert [it["status"] for it in r["results"]] == ["ok", "ok"]
    assert [it["op"] for it in r["results"]] == ["insert", "update"]
    assert all(it["affected"] >= 0 for it in r["results"])
    assert wired.env_calls == [("demoapp", "dev")]


async def test_db_write_stops_at_error_and_marks_rest_skipped(wired):
    """US-13：遇错即停——失败条回错误原文，其后标 skipped，已成功的不回滚。"""
    async def raiser(sql, params=None):
        if "boom" in sql:
            raise RuntimeError('column "boom" does not exist')
        return FakeResult(returns_rows=False, rowcount=1)
    wired.exec_driver_sql = raiser
    r = await db_tools.run_db_tool(
        Ctx(app=WRITE_APP, sf=FakeSF()), "db_write",
        {"statements": ["insert into t values (1)", "insert into t values (boom)",
                        "insert into t values (3)"]},
        confirmed=True)
    assert r["ok"] is True      # 工具没坏，是数据不对
    statuses = [it["status"] for it in r["results"]]
    assert statuses == ["ok", "error", "skipped"]
    assert 'column "boom" does not exist' in r["results"][1]["error"]
    assert "summary" in r and r["summary"]


async def test_db_write_rejects_database_level_statements_before_confirm(wired):
    """US-14：DATABASE/实例级操作在确认之前就直拒——连让用户点确认的机会都不给。"""
    for stmt in ("DROP DATABASE demoapp_dev", "create database evil",
                 "ALTER SYSTEM SET foo = 1", "-- x\ndrop database demoapp_dev"):
        r = await db_tools.run_db_tool(Ctx(app=WRITE_APP), "db_write",
                                       {"statements": [stmt]}, confirmed=True)
        assert r["ok"] is False, stmt
    assert wired.env_calls == []


async def test_db_write_validates_statements_shape(wired):
    """statements 必须是非空字符串数组：畸形入参不碰库。"""
    for bad in ({}, {"statements": []}, {"statements": "insert into t values (1)"},
                {"statements": [1, 2]}, {"statements": ["   ", "-- only comment"]}):
        r = await db_tools.run_db_tool(Ctx(app=WRITE_APP), "db_write", bad, confirmed=True)
        assert r["ok"] is False, bad
    assert wired.env_calls == []


async def test_db_write_audits_each_executed_statement(wired):
    """US-16：每条已执行语句各写一条审计（真走 audit_batch→log_operation 的 INSERT 参数），
    source=agent、带会话 id、actor 是应用 owner；skipped 的不留痕。"""
    sf = FakeSF()

    async def raiser(sql, params=None):
        if "boom" in sql:
            raise RuntimeError('column "boom" does not exist')
        return FakeResult(returns_rows=False, rowcount=1)
    wired.exec_driver_sql = raiser
    r = await db_tools.run_db_tool(
        Ctx(app=WRITE_APP, sf=sf), "db_write",
        {"statements": ["insert into t values (1)", "insert into t values (boom)",
                        "insert into t values (3)"]},
        confirmed=True)
    assert r["ok"] is True
    assert [a["st"] for a in sf.audits] == ["success", "error"]
    assert all(a["src"] == "agent" and a["sid"] == 1 for a in sf.audits)
    assert all(a["u"] == 7 and a["e"] == "dev" for a in sf.audits)


def test_db_confirm_summary_flags_dangerous_statements():
    """确认卡摘要：逐条 op 徽章；无 WHERE 的 update/delete 与 DROP/TRUNCATE 标危险。"""
    summary = db_tools.db_confirm_summary([
        "update t set a = 1",                      # 无 WHERE → 危险
        "delete from t where id = 3",              # 有 WHERE → 普通
        "drop table t",                            # DDL 删表 → 危险
        "insert into t values (1)"])
    assert "[update]" in summary and "[insert]" in summary
    lines = summary.splitlines()
    assert "危险" in lines[1]          # 无 WHERE 的 update
    assert "危险" not in lines[2]      # 带 WHERE 的 delete
    assert "危险" in lines[3]          # drop table


def test_db_write_schema_has_no_env_or_app():
    """US-14 的 schema 层证据：db_write 只有 statements——写 prod 在参数面上不存在。"""
    from app.agent.llm import DB_TOOLS, DB_TOOL_NAMES
    assert DB_TOOL_NAMES == ("db_query", "db_write")
    w = [t for t in DB_TOOLS if t["function"]["name"] == "db_write"][0]
    params = w["function"]["parameters"]
    assert set(params["properties"]) == {"statements"}
    assert params["required"] == ["statements"]



# ---------- 系统提示词（工单 007） ----------

def test_build_db_prompt_states_core_rules():
    """提示词是使用口径的唯一载体：断言优先、截断不可目测计数、写库有确认、prod 只读。"""
    p = db_tools.build_db_prompt()
    assert p.startswith("\n\n<database>") and p.endswith("</database>")
    for kw in ("db_query", "db_write", "断言", "截断", "确认", "prod"):
        assert kw in p, kw


# ---------- 审查修复的回归钉（code-review 2026-10-30） ----------

async def test_exception_text_is_redacted_before_reaching_model(monkeypatch):
    """建连/执行异常原文进模型前必须遮凭据：报错可能带 DSN，与预览工具同一红线。"""
    async def fake_fetch(ctx):
        return APP
    monkeypatch.setattr(db_tools, "fetch_app", fake_fetch)

    @contextlib.asynccontextmanager
    async def _raise_cm(ctx, slug, env):
        raise RuntimeError(
            "connect failed: postgresql+asyncpg://demo:p@ssw0rd@localhost:5433/demoapp_dev")
        yield  # pragma: no cover

    monkeypatch.setattr(db_tools, "_connect", _raise_cm)
    r = await db_tools.run_db_tool(Ctx(app=APP), "db_query", {"sql": "select 1"})
    assert r["ok"] is False
    assert "p@ssw0rd" not in r["error"]


def test_confirm_summary_warns_when_nothing_executable():
    """确认卡不能是空的：全不可执行时用户在点确认前就看见问题。"""
    s = db_tools.db_confirm_summary(["   ", "-- just a comment"])
    assert "没有可执行的语句" in s
