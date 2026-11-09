# app/deploy/mpcreds.py
"""小程序凭据（ADR-0045）：AppID + 代码上传密钥的应用级绑定。

密钥只落平台数据区 `data/miniprogram_keys/{slug}/private.key`（0600），**不回显**给前端、不进
模型上下文；DB 只存 AppID、密钥摘要与上传时间。一应用一账号，AppID 全局唯一。
"""
import hashlib
import re
import secrets
import shutil
from contextlib import suppress
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.config import data_dir
from app.deploy.errors import DeployError

KEY_DIR_NAME = "miniprogram_keys"
APPID_RE = re.compile(r"^wx[0-9a-f]{16}$")
KEY_MAX_BYTES = 64 * 1024
_KEY_HEAD = re.compile(rb"^-----BEGIN (?:RSA )?PRIVATE KEY-----\r?\n")
_KEY_FOOT = re.compile(rb"-----END (?:RSA )?PRIVATE KEY-----\s*$")

_EMPTY = {
    "appid": None, "key_sha256": None, "key_uploaded_at": None, "status": "idle",
    "job_kind": None, "error": None, "version": None, "uploaded_at": None,
    "preview_at": None, "updated_at": None,
}


def appid_valid(value: str) -> bool:
    return bool(APPID_RE.match((value or "").strip()))


def mask_appid(value: str | None) -> str:
    v = (value or "").strip()
    return f"{v[:4]}****{v[-4:]}" if len(v) >= 8 else ""


def key_valid(data: bytes) -> bool:
    return bool(data) and bool(_KEY_HEAD.match(data)) and bool(_KEY_FOOT.search(data))


def key_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def keys_dir() -> Path:
    return data_dir() / KEY_DIR_NAME


def key_path(slug: str) -> Path:
    return keys_dir() / slug / "private.key"


def stage_key(slug: str, data: bytes) -> Path:
    """把密钥写进隐藏暂存文件；DB 提交成功后再 `commit_key` 转正。

    直接覆盖 `private.key` 会让唯一性冲突（AppID 已被别的应用占用）顺手毁掉本应用原有的
    有效密钥——冲突只是这次绑定失败，旧绑定必须原样可用。
    """
    root = keys_dir() / slug
    root.mkdir(parents=True, exist_ok=True)
    with suppress(OSError):
        root.chmod(0o700)
    tmp = root / f".private.{secrets.token_hex(4)}.part"
    tmp.write_bytes(data)
    with suppress(OSError):
        tmp.chmod(0o600)
    return tmp


def commit_key(slug: str, tmp: Path) -> Path:
    dst = key_path(slug)
    tmp.replace(dst)
    return dst


def save_key(slug: str, data: bytes) -> Path:
    return commit_key(slug, stage_key(slug, data))


def delete_key(slug: str) -> None:
    with suppress(FileNotFoundError):
        key_path(slug).unlink()


def cleanup_app(slug: str) -> None:
    shutil.rmtree(keys_dir() / slug, ignore_errors=True)


async def get_binding(sf, app_id: int) -> dict:
    async with sf() as s:
        row = (await s.execute(text(
            "SELECT appid, key_sha256, key_uploaded_at, status, job_kind, error, version, "
            "uploaded_at, preview_at, updated_at "
            "FROM app_mobile_miniprogram WHERE app_id=:i"), {"i": app_id})).mappings().first()
    return dict(row) if row else dict(_EMPTY)


async def bind(sf, app: dict, appid: str, key_bytes: bytes) -> dict:
    appid = (appid or "").strip()
    if not appid_valid(appid):
        raise DeployError("AppID 格式不正确（应为 wx 开头的 18 位）", status=400)
    if not key_valid(key_bytes):
        raise DeployError("上传密钥不是有效的 PEM 私钥文件", status=400)
    digest = key_sha256(key_bytes)
    tmp = stage_key(app["slug"], key_bytes)
    try:
        async with sf() as s:
            await s.execute(text(
                "INSERT INTO app_mobile_miniprogram "
                "(app_id, appid, key_sha256, key_uploaded_at, status, error, version, "
                " uploaded_at, preview_at, updated_at) "
                "VALUES (:i, :a, :h, now(), 'idle', NULL, NULL, NULL, NULL, now()) "
                "ON CONFLICT (app_id) DO UPDATE SET appid=:a, key_sha256=:h, "
                "key_uploaded_at=now(), status='idle', job_kind=NULL, error=NULL, "
                "version=NULL, uploaded_at=NULL, preview_at=NULL, updated_at=now()"),
                {"i": app["id"], "a": appid, "h": digest})
            await s.commit()
    except IntegrityError as e:
        with suppress(FileNotFoundError):
            tmp.unlink()
        raise DeployError("该 AppID 已绑定其他应用，请先解绑", status=409) from e
    commit_key(app["slug"], tmp)
    return {"appid_masked": mask_appid(appid)}


async def unbind(sf, app: dict) -> None:
    async with sf() as s:
        await s.execute(text("DELETE FROM app_mobile_miniprogram WHERE app_id=:i"),
                        {"i": app["id"]})
        await s.commit()
    delete_key(app["slug"])
