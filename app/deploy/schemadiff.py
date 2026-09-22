# app/deploy/schemadiff.py
"""结构快照解析与破坏性检测（P2 分析型闸门；纯文本，无 docker）。

schema.sql 约定为「纯幂等可重放 DDL」的规范子集：
  CREATE TABLE IF NOT EXISTS t (col type [constraints], ...);
  ALTER TABLE t ADD COLUMN IF NOT EXISTS col type ...;
  CREATE [UNIQUE] INDEX IF NOT EXISTS i ON t (...);
其余（约束独立行、注释）忽略。破坏性变更不体现在 schema.sql（它永不 DROP），
由 diff(上一版快照, 当前快照) 检出「上一版有、当前无」的对象 = 删/改名/收窄。
"""
from __future__ import annotations
import re

_CREATE_TABLE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([\w.\"]+)\s*\((.*?)\)\s*;",
    re.I | re.S)
_ADD_COLUMN = re.compile(
    r"ALTER\s+TABLE\s+([\w.\"]+)\s+ADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?"
    r"([\w\"]+)\s+(.+?)\s*;", re.I | re.S)
_CREATE_INDEX = re.compile(
    r"CREATE\s+(?:UNIQUE\s+)?INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?([\w.\"]+)", re.I)
# 表级约束行（非列定义）：这些关键字开头的逗号分段不算列
_TABLE_LEVEL = re.compile(
    r"^\s*(PRIMARY\s+KEY|UNIQUE|FOREIGN\s+KEY|CHECK|CONSTRAINT|EXCLUDE)\b", re.I)


def _norm_type(raw: str) -> str:
    """归一化列类型到「破坏性等价」判定的粗粒度串：小写、折叠空白、剥默认值/NOT NULL。"""
    s = re.split(r"\bDEFAULT\b|\bNOT\s+NULL\b|\bNULL\b|\bPRIMARY\b", raw, flags=re.I)[0]
    return re.sub(r"\s+", " ", s).strip().lower().rstrip(",")


def _strip_quotes(name: str) -> str:
    return name.split(".")[-1].strip('"').strip()


def _split_cols(body: str) -> list[str]:
    """按逗号切列定义，尊重括号嵌套（如 NUMERIC(10,2)）。"""
    parts, depth, cur = [], 0, ""
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(cur); cur = ""
        else:
            cur += ch
    if cur.strip():
        parts.append(cur)
    return parts


def parse_schema(sql: str) -> dict:
    tables: dict[str, dict] = {}
    # 去注释行，避免干扰分号切分
    sql = "\n".join(ln for ln in sql.splitlines() if not ln.strip().startswith("--"))
    for stmt in sql.split(";"):
        if not stmt.strip():
            continue
        body_stmt = stmt + ";"
        m = _CREATE_TABLE.search(body_stmt)
        if m:
            t = _strip_quotes(m.group(1))
            cols = tables.setdefault(t, {"columns": {}, "indexes": set()})["columns"]
            for piece in _split_cols(m.group(2)):
                if not piece.strip() or _TABLE_LEVEL.match(piece):
                    continue
                cname, _, ctype = piece.strip().partition(" ")
                cols[_strip_quotes(cname)] = _norm_type(ctype)
            continue
        m = _ADD_COLUMN.search(body_stmt)
        if m:
            t = _strip_quotes(m.group(1))
            cols = tables.setdefault(t, {"columns": {}, "indexes": set()})["columns"]
            cols[_strip_quotes(m.group(2))] = _norm_type(m.group(3))
            continue
        m = _CREATE_INDEX.search(body_stmt)
        if m:
            iname = _strip_quotes(m.group(1))
            # 索引归属表：ON <table>
            onm = re.search(r"\bON\s+([\w.\"]+)", body_stmt, re.I)
            if onm:
                t = _strip_quotes(onm.group(1))
                tables.setdefault(t, {"columns": {}, "indexes": set()})["indexes"].add(iname)
    return {"tables": tables}


def schema_diff(prev: dict, curr: dict) -> dict:
    """prev/curr 均为 parse_schema 结果。返回新增/删除/类型变更三归类。"""
    pt, ct = prev["tables"], curr["tables"]
    added_tables = sorted(set(ct) - set(pt))
    dropped_tables = sorted(set(pt) - set(ct))
    added_columns: dict[str, list[str]] = {}
    dropped_columns: dict[str, list[str]] = {}
    type_changed: dict[str, dict[str, tuple[str, str]]] = {}
    for t in sorted(set(pt) & set(ct)):
        pc, cc = pt[t]["columns"], ct[t]["columns"]
        ac = sorted(set(cc) - set(pc)); dc = sorted(set(pc) - set(cc))
        if ac:
            added_columns[t] = ac
        if dc:
            dropped_columns[t] = dc
        tc = {k: (pc[k], cc[k]) for k in set(pc) & set(cc) if pc[k] != cc[k]}
        if tc:
            type_changed[t] = tc
    return {"added_tables": added_tables, "dropped_tables": dropped_tables,
            "added_columns": added_columns, "dropped_columns": dropped_columns,
            "type_changed": type_changed}


def destructive_changes(diff: dict) -> list[str]:
    """破坏性项：删表 / 删列（改名会落在此） / 类型变更。返回人读串列表（空=安全）。"""
    out = [f"删除表 {t}" for t in diff["dropped_tables"]]
    for t, cols in diff["dropped_columns"].items():
        out += [f"删除列 {t}.{c}（改名亦属此，需两步走）" for c in cols]
    for t, cm in diff["type_changed"].items():
        out += [f"列 {t}.{c} 类型变更 {a!r}→{b!r}" for c, (a, b) in cm.items()]
    return out


def destructive_message(diff: dict) -> str:
    items = destructive_changes(diff)
    if not items:
        return ""
    return ("检测到破坏性结构变更，已阻断发布（P2/§5：破坏性不自动执行）："
            + "；".join(items)
            + "。请按两步走处理（先加新列/双写并下个版本发布，再于数据轨补回填、"
              "下个版本才从 schema.sql 移除旧列），或设置 deploy.skip_schema_destructive 临时放行")


def backfill_skeleton(diff: dict, *, today: str) -> str:
    """由破坏性 diff 生成数据轨回填骨架文件内容（D<today>_NN_backfill.sql 的正文）。

    只产出注释与被注释掉的幂等 UPDATE 占位——不臆造业务转换（对齐主 spec §19）。
    通体 `--` 注释：未补实即发布也仅是 no-op、不会打断迁移；填实后取消注释生效。
    无破坏性变更 → 返回空串（调用端据此拒绝生成）。
    """
    if not destructive_changes(diff):
        return ""
    L = [
        f"-- 数据轨回填骨架 · 生成于 {today}",
        "-- 用途：承接被破坏性闸门拦下的结构变更（删表/删列/改名/类型变更），在数据轨做一次幂等回填。",
        "-- 这是骨架：以下 UPDATE 均为注释占位，请用真实转换逻辑替换 <...> 后取消注释再发布。",
        "-- 纪律：schema.sql 永不 DROP，物理旧列仍在库中；回填完成后再于下个版本移除旧列声明。",
        "",
    ]
    for t in diff["dropped_tables"]:
        L.append(f"-- 删除表 {t}：先确认无下游依赖并归档数据，下个版本再从 schema.sql 移除")
    if diff["dropped_tables"]:
        L.append("")
    for t in sorted(diff["dropped_columns"]):
        olds = diff["dropped_columns"][t]
        it = iter(list(diff["added_columns"].get(t, [])))
        for c in olds:
            rn = next(it, None)
            if rn:
                L.append(f"-- 疑似改名 {t}.{c} → {t}.{rn}（如非改名请忽略本段）")
                L.append(f"-- UPDATE {t} SET {rn} = <从 {c} 派生的表达式> WHERE {rn} IS NULL;")
            else:
                L.append(f"-- 删除列 {t}.{c}：先归档/迁移其数据，下个版本再从 schema.sql 移除")
                L.append(f"-- UPDATE {t} SET <目标列> = <从 {c} 派生的表达式> WHERE <目标列> IS NULL;")
        L.append("")
    for t in sorted(diff["type_changed"]):
        for c in sorted(diff["type_changed"][t]):
            a, b = diff["type_changed"][t][c]
            L.append(f"-- 列 {t}.{c} 类型变更 {a} → {b}：确认既有值可安全转换，必要时先加新列双写")
            L.append(f"-- UPDATE {t} SET {c} = <按 {b} 归一化的表达式> WHERE <守卫条件>;")
        L.append("")
    return "\n".join(L).rstrip() + "\n"
