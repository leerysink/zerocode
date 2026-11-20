"""S1 接缝：操作手册与 README 的配图完整性。

纯静态断言，不联网、不起浏览器、不碰数据库——界面重构后复跑这一条就知道
哪一章又变回纯文字、哪张图又过期没人管。
"""

import re
import struct
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MANUAL = REPO / "docs" / "user-manual.md"
README = REPO / "README.md"

# 刻意不配图的章节：1 上手前三件事（纯概念）、19 常见问题、20 端口与目录约定
FIGURELESS_CHAPTERS = {"1", "19", "20"}

# 单张配图允许的最大物理像素宽：既有条款里最宽的是 preview.png 的 1024 CSS px @2x，
# 再宽缩进正文栏就糊字（旧 README 口径 1010，取存量上界 1024）
MAX_IMAGE_WIDTH_PX = 2048

_IMG_REF = re.compile(r"^!\[([^\]]*)\]\(([^)\s]+)[^)]*\)", re.MULTILINE)
_CHAPTER = re.compile(r"^## (\d+)\. ", re.MULTILINE)


def _refs(markdown_path: Path) -> list[tuple[int, str, str]]:
    """该文档里所有本地配图引用：(行号, 目标相对路径, alt 文本)。"""
    text = markdown_path.read_text(encoding="utf-8")
    refs = []
    for match in _IMG_REF.finditer(text):
        alt, target = match.group(1), match.group(2)
        if target.startswith(("http://", "https://")):
            continue
        refs.append((text.count("\n", 0, match.start()) + 1, target, alt))
    return refs


def _chapters_without_figure() -> set[str]:
    text = MANUAL.read_text(encoding="utf-8")
    marks = [(m.group(1), m.start()) for m in _CHAPTER.finditer(text)]
    marks.append((None, len(text)))
    return {
        number
        for i, (number, start) in enumerate(marks[:-1])
        if not _IMG_REF.search(text[start : marks[i + 1][1]])
    }


def test_every_manual_and_readme_image_exists():
    broken = [
        f"{doc.name}:{line} -> {target}"
        for doc in (MANUAL, README)
        for line, target, _ in _refs(doc)
        if not (doc.parent / target).resolve().exists()
    ]
    assert not broken, f"手册/README 存在死链配图：{broken}"


def test_only_deliberately_textual_chapters_lack_figures():
    regressed = _chapters_without_figure() - FIGURELESS_CHAPTERS
    assert not regressed, f"这些章节退化成纯文字，读者无处指认入口：第 {sorted(regressed, key=int)} 章"


def test_no_unreferenced_image_lurks_in_docs_images():
    referenced = {
        (doc.parent / target).resolve()
        for doc in (MANUAL, README)
        for _, target, _ in _refs(doc)
    }
    orphans = [
        p.name
        for p in sorted((REPO / "docs" / "images").glob("*.png"))
        if p.resolve() not in referenced
    ]
    assert not orphans, f"未被任何文档引用的孤儿图：{orphans}"


def _png_size(path: Path) -> tuple[int, int]:
    """从 IHDR 读尺寸：不为一条断言往 venv 里塞 Pillow。"""
    with path.open("rb") as fh:
        head = fh.read(24)
    assert head[:8] == b"\x89PNG\r\n\x1a\n", f"{path.name} 不是 PNG"
    width, height = struct.unpack(">II", head[16:24])
    return width, height


def test_manual_figures_stay_readable_at_column_width():
    for line, target, _ in _refs(MANUAL):
        path = (MANUAL.parent / target).resolve()
        width, _height = _png_size(path)
        assert width % 2 == 0, f"{path.name}（手册第 {line} 行）宽度不是 @2x 的偶数倍"
        assert width <= MAX_IMAGE_WIDTH_PX, (
            f"{path.name}（手册第 {line} 行）宽 {width}px 超过 {MAX_IMAGE_WIDTH_PX}px，"
            "缩进正文栏后字号会低于可读下限"
        )


def test_manual_declares_the_figure_count_it_actually_has():
    text = MANUAL.read_text(encoding="utf-8")
    declared = re.search(r"配图[^。\n]*?(\d+) 张", text)
    assert declared, "手册阅读约定里没写配图张数，过期声明无从核对"
    actual = len(_refs(MANUAL))
    assert int(declared.group(1)) == actual, (
        f"手册声明 {declared.group(1)} 张配图，实际引用 {actual} 张"
    )


def test_every_manual_figure_explains_itself():
    blank = [line for line, _, alt in _refs(MANUAL) if not alt.strip()]
    assert not blank, f"手册这些配图 alt 为空，读图无法知道是哪一屏：行 {blank}"
