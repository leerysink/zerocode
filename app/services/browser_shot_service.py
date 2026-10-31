"""preview_page 截图落盘（规格 2026-10-31 §7）。

按会话分目录、不建数据库表：历史卡片的截图引用就是 `/api/browser/shots/{session_id}/{name}`，
读取接口按会话归属授权。单会话有数量与体积上限，超限从最旧淘汰；会话删除时整目录清理。
"""
import datetime
import re
import shutil
import struct
import uuid
from pathlib import Path

from app.config import data_dir

MAX_SHOTS_PER_SESSION = 20
MAX_SHOT_BYTES = 5 * 1024 * 1024
MAX_SESSION_BYTES = 50 * 1024 * 1024
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_NAME_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._-]*\.png$")


class ShotError(ValueError):
    pass


def session_dir(session_id: int) -> Path:
    return data_dir() / "browser_shots" / str(int(session_id))


def png_size(data: bytes) -> tuple[int, int]:
    if len(data) < 24 or not data.startswith(PNG_MAGIC) or data[12:16] != b"IHDR":
        raise ShotError("截图不是合法 PNG")
    width, height = struct.unpack(">II", data[16:24])
    if not width or not height:
        raise ShotError("截图尺寸非法")
    return width, height


def _prune(directory: Path, incoming: int) -> None:
    files = sorted(directory.glob("*.png"), key=lambda p: (p.stat().st_mtime, p.name))
    total = sum(p.stat().st_size for p in files)
    while files and (len(files) >= MAX_SHOTS_PER_SESSION
                     or total + incoming > MAX_SESSION_BYTES):
        old = files.pop(0)
        total -= old.stat().st_size
        old.unlink(missing_ok=True)


def save(session_id: int, data: bytes) -> dict:
    if not data:
        raise ShotError("截图内容为空")
    if len(data) > MAX_SHOT_BYTES:
        raise ShotError(f"截图超过 {MAX_SHOT_BYTES // (1024 * 1024)}MB")
    width, height = png_size(data)
    directory = session_dir(session_id)
    directory.mkdir(parents=True, exist_ok=True)
    _prune(directory, len(data))
    name = f"{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}.png"
    (directory / name).write_bytes(data)
    return {"filename": name, "bytes": len(data), "width": width, "height": height}


def resolve(session_id: int, filename: str) -> Path | None:
    if not filename or not _NAME_RE.match(filename) or Path(filename).name != filename:
        return None
    path = session_dir(session_id) / filename
    return path if path.is_file() else None


def remove_session_dir(session_id: int) -> None:
    shutil.rmtree(session_dir(session_id), ignore_errors=True)
