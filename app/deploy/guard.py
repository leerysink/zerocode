"""平台保留区写保护（规格 01 §4.2 / 02 FR2-05）：app.json 与 deploy/** 平台唯一写入方。"""
import posixpath
from pathlib import Path

def is_protected(root: Path, rel: str) -> bool:
    if not (root / "app.json").is_file():
        return False
    # C-1（审查 I-1）：判定前先折叠归一——反斜杠、首尾空白、NTFS 尾点/尾空格、./ 与 .. 点段。
    # 折叠后越根的逃逸路径不属保留区判定，由 v1 resolve_in_workspace 兜底拒越界。
    s = str(rel).replace("\\", "/").lstrip("/").strip().rstrip(" .")
    if s.startswith("../"):
        return False
    norm = posixpath.normpath(s).lower()  # Windows 盘大小写不敏感；POSIX 上宁可 fail-closed
    return norm in ("app.json", "deploy") or norm.startswith("deploy/")
