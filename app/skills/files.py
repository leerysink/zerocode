"""技能母本只读文件访问服务（ADR-0061 / spec 2026-11-20-skill-multifile-display）。

平台展示侧（管理页详情/安装预览）读母本，与 Agent 受控读面同口径：owner 隔离、
路径围栏、文本白名单、图片类型围栏。只读——不提供写/删单文件。
"""
from pathlib import Path

from app.agent.tools import MAX_READ_BYTES, SKILL_READ_EXTS, resolve_skill_path
from app.skills.library import SkillError, skills_root

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
IMAGE_CONTENT_TYPES = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".webp": "image/webp", ".gif": "image/gif",
}
IMAGE_MAX_BYTES = 5 * 1024 * 1024   # 图片预览兜底上限（母本单文件本就 ≤1MB）


def classify(suffix: str) -> str:
    s = (suffix or "").lower()
    if s in SKILL_READ_EXTS:
        return "text"
    if s in IMAGE_EXTS:
        return "image"
    return "binary"


def resolve_within(skill_dir: Path, rel: str | None) -> Path | None:
    """把相对路径收敛到 skill_dir 内；绝对/越界/resolve 失败一律 None（不开第二读面）。"""
    if not rel or not str(rel).strip():
        return None
    p = Path(rel)
    if p.is_absolute():
        return None
    try:
        root_r = Path(skill_dir).resolve()
        full = (root_r / p).resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    if root_r != full and root_r not in full.parents:
        return None
    return full


def owned_skill_dir(uid, skill_dir_str: str | None) -> Path | None:
    """owner 技能根内的目录解析：复用 resolve_skill_path 围栏，须是目录。"""
    sd = resolve_skill_path(skills_root(uid), skill_dir_str)
    if sd is None or not sd.is_dir():
        return None
    return sd


def list_files(skill_dir: Path) -> list[dict]:
    """递归列母本文件（relpath posix + size + kind），排序稳定；跳过逃逸符号链接。"""
    root_r = Path(skill_dir).resolve()
    out = []
    for p in root_r.rglob("*"):
        if not p.is_file():
            continue
        try:
            full = p.resolve()
        except (OSError, RuntimeError):
            continue
        if root_r != full and root_r not in full.parents:
            continue   # 符号链接逃逸母本：不列
        rel = full.relative_to(root_r).as_posix()
        out.append({"path": rel, "size": full.stat().st_size, "kind": classify(full.suffix)})
    out.sort(key=lambda f: f["path"])
    return out


def read_text(skill_dir: Path, rel: str) -> str:
    full = resolve_within(skill_dir, rel)
    if full is None:
        raise SkillError("文件路径越界或非法")
    if not full.is_file():
        raise SkillError(f"文件不存在: {rel}")
    if full.suffix.lower() not in SKILL_READ_EXTS:
        raise SkillError(f"仅可读文本类文件（{'/'.join(sorted(SKILL_READ_EXTS))}）: {rel}")
    size = full.stat().st_size
    if size > MAX_READ_BYTES:
        raise SkillError(f"文件超过 200KB（{size} 字节）: {rel}")
    return full.read_text(encoding="utf-8", errors="replace")


def read_image(skill_dir: Path, rel: str) -> tuple[bytes, str]:
    full = resolve_within(skill_dir, rel)
    if full is None:
        raise SkillError("文件路径越界或非法")
    if not full.is_file():
        raise SkillError(f"文件不存在: {rel}")
    suffix = full.suffix.lower()
    if suffix not in IMAGE_EXTS:
        raise SkillError(f"仅可预览图片（{'/'.join(sorted(IMAGE_EXTS))}）: {rel}")
    size = full.stat().st_size
    if size > IMAGE_MAX_BYTES:
        raise SkillError(f"图片超过预览上限（{size} 字节）: {rel}")
    return full.read_bytes(), IMAGE_CONTENT_TYPES[suffix]