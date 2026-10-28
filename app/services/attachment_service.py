"""聊天图片附件：落盘、元数据、与消息绑定。

文件存在平台数据区而不是用户工作区——避免污染文件树、应用打包与 source_hash。
"""
import base64
import logging
import re
import shutil
import uuid
from pathlib import Path

from sqlalchemy import bindparam, text as _sql

from app.config import data_dir

logger = logging.getLogger(__name__)

ALLOWED_IMAGE_MIMES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}
MAX_ATTACHMENTS_PER_MESSAGE = 4
MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024
MAX_MESSAGE_BYTES = 10 * 1024 * 1024
MAX_INLINE_BYTES = 10 * 1024 * 1024
ORPHAN_TTL_HOURS = 24
IMAGE_CHAR_EQUIVALENT = 4000


class AttachmentError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def sniff_image_mime(head: bytes) -> str | None:
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    return None


def safe_filename(name: str | None, mime: str) -> str:
    base = re.split(r"[\\/]", (name or "").strip())[-1]
    base = re.sub(r"[\x00-\x1f\x7f]", "", base).strip()
    if base in ("", ".", ".."):
        base = "image" + ALLOWED_IMAGE_MIMES.get(mime, "")
    return base[:120]


def session_upload_dir(uid: int, session_id: int) -> Path:
    return Path(data_dir()) / "users" / str(uid) / "chat_uploads" / str(session_id)


def resolve_storage_path(storage_path: str) -> Path | None:
    root = Path(data_dir()).resolve()
    p = (root / storage_path).resolve()
    try:
        p.relative_to(root)
    except ValueError:
        return None
    return p


def format_attachment(row) -> dict:
    return {
        "id": row["id"],
        "filename": row["filename"],
        "mime": row["mime"],
        "size_bytes": row["size_bytes"],
        "url": f"/api/attachments/{row['id']}",
    }


async def create_attachment(sf, uid: int, session_id: int, filename: str,
                            data: bytes, mime: str) -> dict:
    ext = ALLOWED_IMAGE_MIMES.get(mime)
    if ext is None:
        raise AttachmentError("不支持的图片类型")
    if len(data) > MAX_ATTACHMENT_BYTES:
        raise AttachmentError("单张图片不能超过 5MB", 413)
    folder = session_upload_dir(uid, session_id)
    folder.mkdir(parents=True, exist_ok=True)
    disk_name = uuid.uuid4().hex + ext
    path = folder / disk_name
    path.write_bytes(data)
    rel = f"users/{uid}/chat_uploads/{session_id}/{disk_name}"
    try:
        async with sf() as s:
            row = (await s.execute(_sql(
                "INSERT INTO message_attachment "
                "(session_id, filename, mime, size_bytes, storage_path) "
                "VALUES (:sid, :f, :m, :n, :p) "
                "RETURNING id, filename, mime, size_bytes, storage_path"),
                {"sid": session_id, "f": filename, "m": mime,
                 "n": len(data), "p": rel})).mappings().first()
            await s.commit()
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return format_attachment(row)


async def staged_bytes(sf, session_id: int) -> int:
    async with sf() as s:
        return (await s.execute(_sql(
            "SELECT COALESCE(SUM(size_bytes), 0) FROM message_attachment "
            "WHERE session_id=:s AND message_id IS NULL"),
            {"s": session_id})).scalar() or 0


def _in_ids(ids: list[int]):
    return bindparam("ids", expanding=True)


async def load_bindable(sf, session_id: int, ids: list[int]) -> list[dict]:
    ids = list(dict.fromkeys(int(i) for i in (ids or [])))
    if not ids:
        return []
    if len(ids) > MAX_ATTACHMENTS_PER_MESSAGE:
        raise AttachmentError(f"单条消息最多 {MAX_ATTACHMENTS_PER_MESSAGE} 张图片")
    async with sf() as s:
        rows = (await s.execute(_sql(
            "SELECT id, filename, mime, size_bytes, storage_path FROM message_attachment "
            "WHERE session_id=:sid AND message_id IS NULL AND id IN :ids")
            .bindparams(_in_ids(ids)), {"sid": session_id, "ids": ids})).mappings().all()
    by_id = {r["id"]: dict(r) for r in rows}
    out, total = [], 0
    for i in ids:
        row = by_id.get(i)
        if row is None:
            raise AttachmentError("附件不存在或已被使用")
        if row["mime"] not in ALLOWED_IMAGE_MIMES:
            raise AttachmentError("不支持的图片类型")
        if row["size_bytes"] > MAX_ATTACHMENT_BYTES:
            raise AttachmentError("单张图片不能超过 5MB", 413)
        path = resolve_storage_path(row["storage_path"])
        if path is None or not path.is_file():
            raise AttachmentError("附件文件缺失，请重新上传")
        total += row["size_bytes"]
        out.append(row)
    if total > MAX_MESSAGE_BYTES:
        raise AttachmentError("单条消息图片合计不能超过 10MB", 413)
    return out


async def for_ids(sf, session_id: int, ids: list[int]) -> dict[int, dict]:
    ids = list(dict.fromkeys(int(i) for i in (ids or [])))
    if not ids:
        return {}
    async with sf() as s:
        rows = (await s.execute(_sql(
            "SELECT id, filename, mime, size_bytes FROM message_attachment "
            "WHERE session_id=:sid AND id IN :ids").bindparams(_in_ids(ids)),
            {"sid": session_id, "ids": ids})).mappings().all()
    return {r["id"]: format_attachment(r) for r in rows}


async def for_messages(sf, session_id: int, message_ids: list[int]) -> dict[int, list[dict]]:
    ids = [int(m) for m in message_ids if m is not None]
    if not ids:
        return {}
    async with sf() as s:
        rows = (await s.execute(_sql(
            "SELECT id, message_id, filename, mime, size_bytes FROM message_attachment "
            "WHERE session_id=:sid AND message_id IN :mids").bindparams(bindparam("mids", expanding=True)),
            {"sid": session_id, "mids": ids})).mappings().all()
    out: dict[int, list[dict]] = {}
    for r in rows:
        out.setdefault(r["message_id"], []).append(format_attachment(r))
    return out


async def for_llm(sf, message_ids: list[int]) -> dict[int, list[dict]]:
    ids = [int(m) for m in message_ids if m is not None]
    if not ids:
        return {}
    async with sf() as s:
        rows = (await s.execute(_sql(
            "SELECT id, message_id, filename, mime, size_bytes, storage_path "
            "FROM message_attachment WHERE message_id IN :mids")
            .bindparams(bindparam("mids", expanding=True)), {"mids": ids})).mappings().all()
    out: dict[int, list[dict]] = {}
    for r in rows:
        path = resolve_storage_path(r["storage_path"])
        data_url = None
        if path is not None and path.is_file():
            data = path.read_bytes()
            data_url = f"data:{r['mime']};base64,{base64.b64encode(data).decode()}"
        item = format_attachment(r)
        item["data_url"] = data_url
        out.setdefault(r["message_id"], []).append(item)
    return out


async def bind_to_message(s, session_id: int, message_id: int, ids: list[int]) -> list[dict]:
    ids = list(dict.fromkeys(int(i) for i in (ids or [])))
    if not ids:
        return []
    rows = (await s.execute(_sql(
        "UPDATE message_attachment SET message_id=:m "
        "WHERE id IN :ids AND session_id=:sid AND message_id IS NULL "
        "RETURNING id, filename, mime, size_bytes").bindparams(_in_ids(ids)),
        {"m": message_id, "ids": ids, "sid": session_id})).mappings().all()
    if len(rows) != len(ids):
        raise ValueError("附件不可用或已被使用")
    by_id = {r["id"]: format_attachment(r) for r in rows}
    return [by_id[i] for i in ids]


def remove_session_dir(uid: int, session_id: int) -> None:
    try:
        shutil.rmtree(session_upload_dir(uid, session_id))
    except FileNotFoundError:
        pass
    except OSError:
        logger.exception("删除会话附件目录失败 uid=%s session=%s", uid, session_id)
