"""技能安装预览快照（ADR-0061 / spec 故事9-13）：preview 取回整包暂存，install 从快照落盘。

token→staging 目录（files/ + meta.json），owner 隔离 + TTL 过期清理，保证 preview==install
（防 upstream 变化/调包）。只读审视，不落地母本；体积守卫在 installer 取回时已生效。
"""
import json
import re
import secrets
import shutil
import time
from pathlib import Path

from app.config import data_dir
from app.skills.library import SkillError

TTL_SECONDS = 600
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")   # urlsafe base64；拒路径 traversal


def _staging_root() -> Path:
    return data_dir() / "skill-preview"


def _sweep_expired(now=None):
    now = time.time() if now is None else now
    root = _staging_root()
    if not root.exists():
        return
    for d in root.iterdir():
        if not d.is_dir():
            continue
        meta = d / "meta.json"
        try:
            m = json.loads(meta.read_text(encoding="utf-8")) if meta.is_file() else {}
            if m.get("expires_at", 0) < now:
                shutil.rmtree(d, ignore_errors=True)
        except (OSError, ValueError):
            shutil.rmtree(d, ignore_errors=True)


def save_snapshot(uid, payload: dict) -> str:
    _sweep_expired()
    token = secrets.token_urlsafe(24)
    d = _staging_root() / token
    files_dir = d / "files"
    try:
        files_dir.mkdir(parents=True)
        fd_r = files_dir.resolve()
        for rel, blob in payload["files"].items():
            target = (files_dir / rel).resolve()
            if fd_r not in target.parents:
                raise SkillError(f"非法文件路径: {rel}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(blob)
        meta = {"uid": uid, "expires_at": time.time() + TTL_SECONDS,
                "name": payload["name"], "description": payload["description"],
                "source_type": payload["source_type"], "source_url": payload["source_url"],
                "subpath": payload.get("subpath")}
        (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    except OSError as e:
        shutil.rmtree(d, ignore_errors=True)
        raise SkillError("预览快照暂存失败，请重试") from e
    return token


def _load_meta(uid, token):
    if not token or not _TOKEN_RE.match(token):
        return None, None
    root = _staging_root().resolve()
    d = (root / token).resolve()
    if root not in d.parents:
        return None, None
    meta_path = d / "meta.json"
    if not meta_path.is_file():
        return None, None
    try:
        m = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, None
    if m.get("uid") != uid or m.get("expires_at", 0) < time.time():
        return None, None
    return d, m


def load_snapshot(uid, token) -> dict | None:
    d, m = _load_meta(uid, token)
    if d is None:
        return None
    files_dir = d / "files"
    files = {p.relative_to(files_dir).as_posix(): p.read_bytes()
             for p in files_dir.rglob("*") if p.is_file()}
    return {"name": m["name"], "description": m["description"], "files": files,
            "source_type": m["source_type"], "source_url": m["source_url"],
            "subpath": m.get("subpath")}


def consume_snapshot(uid, token) -> dict | None:
    d, _ = _load_meta(uid, token)
    payload = load_snapshot(uid, token)
    if payload is not None and d is not None:
        shutil.rmtree(d, ignore_errors=True)
    return payload


def snapshot_files_dir(uid, token) -> Path | None:
    """owner+TTL 校验后的快照 files 目录（预览态按文件读文本/图片用）。"""
    d, _ = _load_meta(uid, token)
    if d is None:
        return None
    fd = d / "files"
    return fd if fd.is_dir() else None


def snapshot_manifest(uid, token) -> dict | None:
    """预览展示用：文件清单 + SKILL.md 内容（读快照，不落地母本）。"""
    payload = load_snapshot(uid, token)
    if payload is None:
        return None
    from app.skills import files as F
    manifest = [{"path": rel, "size": len(blob), "kind": F.classify(Path(rel).suffix)}
                for rel, blob in sorted(payload["files"].items())]
    md = payload["files"].get("SKILL.md")
    return {"name": payload["name"], "description": payload["description"],
            "files": manifest, "skill_md": md.decode("utf-8", errors="replace") if md else None}