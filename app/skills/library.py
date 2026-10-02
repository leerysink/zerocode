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

MAX_FILE_BYTES = 1024 * 1024  # 单文件 1MB（spec §3 故事3）
TOTAL_MAX_BYTES = 10 * 1024 * 1024  # 单技能目录 10MB（spec §3 故事3，002 GitHub 通道）
FILE_COUNT_MAX = 600                # 单技能目录文件数上限（同上）
SLUG_MAX_LEN = 80             # slug 上限，给 next_free_slug 的 -N 后缀留空间（列宽 VARCHAR(200)）
NAME_MAX = 200                # user_skill.name VARCHAR(200)：落库前的唯一真源，两通道共用（AI 生成 spec §5.5）


class SkillError(Exception):
    """技能安装/解析领域错误；api 层统一映射为 HTTP 400。

    可附 status_code 属性（远端 HTTP 状态），供调用方按语义分支而非文案匹配。
    """

    status_code: int | None = None
    oversize: bool = False  # 体积/条数守卫抛出的标记（_fetch_tree 据此改专属文案）


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


def check_size_budget(files: dict[str, bytes]) -> None:
    """整包尺寸守卫：文件数/总量超限即拒（spec §3 故事3；单文件 1MB 在取回/写盘处另有拦截）。"""
    if len(files) > FILE_COUNT_MAX:
        raise SkillError(f"技能文件数超过上限: {len(files)} > {FILE_COUNT_MAX}")
    total = sum(len(b) for b in files.values())
    if total > TOTAL_MAX_BYTES:
        raise SkillError(f"技能总大小超过上限: {total} > {TOTAL_MAX_BYTES} 字节")


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


def delete_skill_dir(uid, skill_dir: str) -> None:
    """删除技能母本目录（003）：限用户自己技能根内，不存在视为已删。

    路径围栏：DB 行理论上只由我们写入，但残行/手工改库也不能让 DELETE
    接口变成任意目录删除器。越界/失败抛 SkillError，调用方不得继续删行。
    """
    root = skills_root(uid)
    try:
        root = root.resolve()
        p = Path(skill_dir).resolve()
    except (OSError, RuntimeError) as e:  # 死链接/reparse 点等让 resolve 本身炸（复审 M-1）
        raise SkillError("技能目录路径异常，已拒绝删除") from e
    if root not in p.parents or p == root:
        raise SkillError("技能目录路径异常，已拒绝删除")
    if not p.exists():
        return
    try:
        shutil.rmtree(p)
    except OSError as e:
        raise SkillError("技能目录删除失败，请重试") from e
