"""技能母本只读文件服务（ADR-0061 / ticket 001/002）：纯单元，不碰 DB。

接缝＝app/skills/files.py 的纯函数（列目录/读文本/读图片/围栏/owner 目录解析）。
API 层 owner 隔离与 HTTP 映射在 test_skills_api 侧（启动 lifespan，受环境约束）。
"""
import pytest

from app.skills.files import (classify, list_files, owned_skill_dir, read_image,
                              read_text, resolve_within)
from app.skills.library import SkillError


def _tree(tmp_path):
    d = tmp_path / "pdf"
    (d / "scripts").mkdir(parents=True)
    (d / "assets").mkdir()
    (d / "SKILL.md").write_text("---\nname: pdf\ndescription: d\n---\n正文", encoding="utf-8")
    (d / "scripts" / "run.py").write_text("print(1)", encoding="utf-8")
    (d / "assets" / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (d / "data.bin").write_bytes(b"\x00\x01")
    return d


def test_classify():
    assert classify(".md") == "text"
    assert classify(".py") == "text"
    assert classify(".png") == "image"
    assert classify(".jpg") == "image"
    assert classify(".bin") == "binary"
    assert classify(".PDF") == "binary"   # 大小写不敏感


def test_list_files_kinds_paths_sizes(tmp_path):
    d = _tree(tmp_path)
    files = list_files(d)
    by = {f["path"]: f for f in files}
    assert set(by) == {"SKILL.md", "scripts/run.py", "assets/logo.png", "data.bin"}
    assert by["SKILL.md"]["kind"] == "text"
    assert by["scripts/run.py"]["kind"] == "text"
    assert by["assets/logo.png"]["kind"] == "image"
    assert by["data.bin"]["kind"] == "binary"
    assert by["scripts/run.py"]["size"] == len("print(1)")
    assert [f["path"] for f in files] == sorted(by)   # 排序稳定


def test_read_text_whitelist(tmp_path):
    d = _tree(tmp_path)
    assert "正文" in read_text(d, "SKILL.md")
    assert "print(1)" in read_text(d, "scripts/run.py")


def test_read_text_non_whitelist_rejected(tmp_path):
    d = _tree(tmp_path)
    with pytest.raises(SkillError):
        read_text(d, "data.bin")
    with pytest.raises(SkillError):
        read_text(d, "assets/logo.png")


def test_read_text_fence(tmp_path):
    d = _tree(tmp_path)
    (tmp_path / "secret.md").write_text("x", encoding="utf-8")
    with pytest.raises(SkillError):
        read_text(d, "../secret.md")          # 逃逸
    with pytest.raises(SkillError):
        read_text(d, str(d / "SKILL.md"))     # 绝对路径
    with pytest.raises(SkillError):
        read_text(d, "missing.md")            # 不存在


def test_read_text_oversize(tmp_path):
    from app.agent.tools import MAX_READ_BYTES
    d = _tree(tmp_path)
    (d / "big.md").write_bytes(b"x" * (MAX_READ_BYTES + 1))
    with pytest.raises(SkillError):
        read_text(d, "big.md")


def test_read_image(tmp_path):
    d = _tree(tmp_path)
    blob, ct = read_image(d, "assets/logo.png")
    assert blob == b"\x89PNG\r\n\x1a\n"
    assert ct == "image/png"


def test_read_image_type_fence(tmp_path):
    d = _tree(tmp_path)
    (tmp_path / "secret.png").write_bytes(b"\x89PNG")
    with pytest.raises(SkillError):
        read_image(d, "data.bin")             # 非图片
    with pytest.raises(SkillError):
        read_image(d, "SKILL.md")             # 文本不是图片
    with pytest.raises(SkillError):
        read_image(d, "../secret.png")        # 逃逸


def test_resolve_within(tmp_path):
    d = _tree(tmp_path)
    assert resolve_within(d, "SKILL.md") == (d / "SKILL.md").resolve()
    assert resolve_within(d, "../secret.md") is None
    assert resolve_within(d, str(d / "SKILL.md")) is None   # 绝对
    assert resolve_within(d, None) is None


def test_owned_skill_dir(tmp_path, monkeypatch):
    import app.skills.files as F
    root = tmp_path / "skills"
    d = root / "pdf"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("x", encoding="utf-8")
    monkeypatch.setattr(F, "skills_root", lambda uid: root)
    assert F.owned_skill_dir(1, str(d)) == d.resolve()
    assert F.owned_skill_dir(1, str(tmp_path / "elsewhere")) is None   # 根外
    assert F.owned_skill_dir(1, str(d / "SKILL.md")) is None           # 不是目录