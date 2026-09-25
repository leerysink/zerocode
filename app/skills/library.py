"""技能库纯逻辑：frontmatter 解析、slug 生成、母本目录原子落盘。

不含网络与 DB：installer 负责取回 bytes，api 负责落库，本模块只做「内容→名字/路径/磁盘」。
"""
import os
import re
import shutil
import tempfile
import unicodedata
from pathlib import Path

import yaml

from app.config import data_dir

MAX_FILE_BYTES = 1024 * 1024  # 单文件 1MB（spec §3 故事3；目录总量/文件数守卫在 002）
SLUG_MAX_LEN = 80             # slug 上限，给 next_free_slug 的 -N 后缀留空间（列宽 VARCHAR(200)）


class SkillError(Exception):
    """技能安装/解析领域错误；api 层统一映射为 HTTP 400。"""


def parse_skill_md(content: str) -> tuple[str, str]:
    """解析 SKILL.md 的 YAML frontmatter，返回 (name, description)。

    缺 frontmatter / 未闭合 / 缺 name 或 description → SkillError（报错含字段名）。
    """
    text = content.lstrip("\ufeff").lstrip()
    if not text.startswith("---"):
        raise SkillError("SKILL.md 缺少 YAML frontmatter（应以 --- 开头）")
    lines = text.split("\n")
    fm: list[str] = []
    closed = False
    for ln in lines[1:]:
        if ln.strip() == "---":
            closed = True
            break
        fm.append(ln)
    if not closed:
        raise SkillError("SKILL.md frontmatter 未闭合（缺少结束的 ---）")
    try:
        meta = yaml.safe_load("\n".join(fm)) or {}
    except yaml.YAMLError as e:
        raise SkillError(f"frontmatter YAML 解析失败: {e}")
    if not isinstance(meta, dict):
        raise SkillError("frontmatter 不是键值映射")
    name = str(meta.get("name") or "").strip()
    desc = str(meta.get("description") or "").strip()
    if not name:
        raise SkillError("frontmatter 缺少必填字段 name")
    if not desc:
        raise SkillError("frontmatter 缺少必填字段 description")
    return name, desc


def slugify(name: str) -> str:
    """name → ASCII kebab-case slug；非 ASCII 剔除，超长截断，为空回退 'skill'（spec §4 名称来源）。"""
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    s = s[:SLUG_MAX_LEN].rstrip("-")
    return s or "skill"


def next_free_slug(existing: set[str], base: str) -> str:
    """base 未占用则直取，否则追加 -2/-3… 后缀（spec §3 故事4 同名重装）。"""
    if base not in existing:
        return base
    k = 2
    while f"{base}-{k}" in existing:
        k += 1
    return f"{base}-{k}"


def skills_root(uid) -> Path:
    """某用户的技能母本根目录：data/users/<uid>/skills。"""
    return data_dir() / "users" / str(uid) / "skills"


def write_skill_dir(uid, slug: str, files: dict[str, bytes]) -> Path:
    """把 files（relpath→bytes）原子写入 users/<uid>/skills/<slug>/，返回最终目录。

    先写同卷临时目录再 os.replace 搬入：任一步失败不留半成品（spec §3 故事5）。
    所有底层 OSError 统一转 SkillError（api 层映射 400），绝不让文件系统异常裸外溢成 500。
    """
    root = skills_root(uid)
    try:
        root.mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(prefix=f".{slug}.", dir=root)).resolve()
    except OSError as e:
        raise SkillError("技能目录创建失败，请重试") from e
    dest = root / slug
    try:
        for rel, blob in files.items():
            if len(blob) > MAX_FILE_BYTES:
                raise SkillError(f"文件超过 1MB 上限: {rel}")
            target = (tmp / rel).resolve()
            # rel="." 等指向临时目录本身的写法一并拒绝，防空文件写进目录报 PermissionError
            if target == tmp or tmp not in target.parents:
                raise SkillError(f"非法文件路径: {rel}")
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(blob)
            except OSError as e:
                raise SkillError(f"技能文件写入失败: {rel}") from e
        if dest.exists():
            raise SkillError(f"目标技能目录已存在: {slug}")
        try:
            os.replace(tmp, dest)
        except OSError as e:
            raise SkillError("技能目录落盘失败，请重试") from e
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return dest
