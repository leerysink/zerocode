"""S2 接缝：宣传短片的规格与交付形态。

成片缺失时 skip 而不是 fail——截图阶段与成片阶段之间设有验收点，
不能因为片子还没渲出来就把全量测试染红。
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
VIDEO = REPO / "docs" / "media" / "zerocode-intro.mp4"
LFS_POINTER_MAX = 200  # 指针文件只有一两百字节
MIN_SECONDS, MAX_SECONDS = 60, 90

pytestmark = pytest.mark.skipif(
    not VIDEO.exists(), reason="成片未产出：短片是阶段三交付物，先让截图阶段跑通"
)


def _probe_lines() -> str:
    """只依赖 ffmpeg 本体（本机装不到 ffprobe，见 spec 补充说明）。"""
    exe = shutil.which("ffmpeg")
    if not exe:
        pytest.skip("本机没有 ffmpeg，无法校验成片规格")
    proc = subprocess.run([exe, "-hide_banner", "-i", str(VIDEO)],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    return proc.stderr


def test_delivered_file_is_not_an_lfs_pointer():
    size = VIDEO.stat().st_size
    assert size > LFS_POINTER_MAX, (
        f"成片只有 {size} 字节——拿到的是 LFS 指针，装 git-lfs 后重新拉取"
    )
    assert size > 1024 * 1024, f"成片 {size / 1024 / 1024:.1f}MB 小得不像一条 1080p 片子"


def test_duration_budget_is_sixty_to_ninety_seconds():
    hours, minutes, seconds = re.search(
        r"Duration: (\d+):(\d+):([\d.]+)", _probe_lines()
    ).groups()
    total = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    assert MIN_SECONDS <= total <= MAX_SECONDS, (
        f"成片 {total:.1f}s 落在 60–90 秒预算之外（D4：宣传向快剪）"
    )


def test_frame_size_and_audio_track():
    lines = _probe_lines()
    video = re.search(r"Stream #\d+:\d+.*?Video:.*?(\d{3,5})x(\d{3,5})", lines)
    assert video, f"探不到视频流：{lines[-400:]}"
    assert (int(video.group(1)), int(video.group(2))) == (1920, 1080), (
        f"成片是 {video.group(1)}×{video.group(2)}，不是 1920×1080"
    )
    assert "Audio: " in lines, "成片没有音轨——旁白或床乐没混进去"
