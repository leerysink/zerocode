# app/deploy/schemacheck.py
"""pg_dump --schema-only 文本规范化与结构差异比对（P1 止血闸门）。

规范化策略（够用即可，P1 为启发式闸门）：
- 丢弃 `--` 注释、空行、`SET ...`、`SELECT pg_catalog.set_config(...)`；
- 丢弃 `ALTER TABLE ... OWNER TO ...` / `GRANT` / `REVOKE` / `COMMENT ON`（角色名跨库不同）；
  （调用方 pg_dump 已带 --no-owner --no-privileges，这里双保险）
- 按 `;` 切成语句块，逐块压缩空白后排序，得到与语句输出顺序无关的稳定集合。
"""
from __future__ import annotations
import re

_DROP_PATTERNS = (
    re.compile(r"^--"), re.compile(r"^SET\b", re.I),
    re.compile(r"^SELECT pg_catalog\.set_config", re.I),
    re.compile(r"^ALTER \w+ .*OWNER TO\b", re.I),
    re.compile(r"^(GRANT|REVOKE)\b", re.I),
    re.compile(r"^COMMENT ON\b", re.I),
)


def _drop_line(line: str) -> bool:
    s = line.strip()
    return not s or any(p.match(s) for p in _DROP_PATTERNS)


def normalize_dump(dump: str) -> str:
    kept = [ln for ln in dump.splitlines() if not _drop_line(ln)]
    blocks = []
    for stmt in "\n".join(kept).split(";"):
        flat = re.sub(r"\s+", " ", stmt).strip()
        if flat:
            blocks.append(flat)
    return "\n".join(sorted(blocks))


def _object_names(blocks: list[str]) -> set[str]:
    """从 CREATE 语句块抽对象名，用于缺失/多余归类。"""
    out = set()
    for b in blocks:
        m = re.match(r"CREATE (?:UNIQUE )?(\w+) (?:IF NOT EXISTS )?([\w.\"]+)", b)
        if m:
            out.add(m.group(2).split(".")[-1].strip('"'))
    return out


def schema_diff(dev_norm: str, files_norm: str) -> dict:
    """返回 dev 相对 files 的差异：only_in_dev / only_in_files（规范化文本块级）。"""
    a = [b for b in dev_norm.split("\n") if b]
    b = [bb for bb in files_norm.split("\n") if bb]
    sa, sb = set(a), set(b)
    only_dev = sorted(sa - sb)
    only_files = sorted(sb - sa)
    return {"only_in_dev": only_dev, "only_in_files": only_files,
            "objects_only_in_dev": sorted(_object_names(only_dev)),
            "objects_only_in_files": sorted(_object_names(only_files))}


def is_parity(diff: dict) -> bool:
    return not diff["only_in_dev"] and not diff["only_in_files"]
