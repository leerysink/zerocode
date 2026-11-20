"""S2 接缝：宣传短片的规格与交付形态。

成片不在仓库里时 skip：短片按 ADR-0064 以文件另发（Gitee 不提供 LFS，普通二进制进历史又删不掉），
所以这批断言目前是「留着的验收口径」而不是在跑的守卫——别删，进仓即生效。
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
    not VIDEO.exists(),
    # 短片现在**刻意不进仓**（Gitee 无 LFS，二进制进历史即永久焊死，ADR-0064），所以这三条恒 skip。
    # 保留是为了留住验收口径：哪天定了托管、把成片放回仓库，它们立刻恢复生效——别当失效测试删掉。
    reason="成片不在仓内（短片以文件另发，ADR-0064）；进仓后本接缝自动恢复生效",
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
