"""平台保留区守卫（规格 01 §4.2 / 02 FR2-05）：app.json 与 deploy/** 平台唯一写入方。

读面收口（终审 M-3）：deploy/** 含凭据明文（deploy/.env），读写皆平台专属——AI 文件工具与
UI 读取端点拒读/隐藏，口令不得进模型上下文与对话历史；app.json 是清单（无口令），读放行。
"""
import posixpath
from pathlib import Path


def _fold(rel: str) -> str:
    """C-1（审查 I-1）：判定前折叠归一——反斜杠、首尾空白、NTFS 尾点/尾空格、./ 与 .. 点段。
    Windows 盘大小写不敏感；POSIX 上宁可 fail-closed。折叠后越根的逃逸路径返回 ../ 前缀，
    不属保留区判定，由 v1 resolve_in_workspace 兜底拒越界。"""
    s = str(rel).replace("\\", "/").lstrip("/").strip().rstrip(" .")
    return posixpath.normpath(s).lower()


def is_protected(root: Path, rel: str) -> bool:
    """写保护：app.json 与 deploy/** 平台唯一写入方。"""
    if not (root / "app.json").is_file():
        return False
    norm = _fold(rel)
    if norm.startswith("../"):
        return False
    return norm in ("app.json", "deploy") or norm.startswith("deploy/")


def is_secret_read(root: Path, rel: str) -> bool:
    """读禁（终审 M-3）：仅 deploy/** 为密文读禁，app.json 读仍放行（AI 需看清单）。

    与 is_protected 共用折叠/归案逻辑；只在应用工作区（盘上有 app.json）生效，
    普通工作区零影响。"""
    if not (root / "app.json").is_file():
        return False
    norm = _fold(rel)
    if norm.startswith("../"):
        return False
    return norm == "deploy" or norm.startswith("deploy/")
