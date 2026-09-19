# app/deploy/slugs.py
import re
from app.deploy.errors import DeployError

SLUG_RE = re.compile(r"^[a-z][a-z0-9]{1,39}$")

def validate_slug(slug: str) -> None:
    if not SLUG_RE.fullmatch(slug or ""):
        raise DeployError(f"slug 非法（需 ^[a-z][a-z0-9]{{1,39}}$）: {slug}")

def normalize_slug(name: str) -> str:
    """从名称提取可用字符；中文等无法提取时返回空串由调用方要求手填。"""
    s = re.sub(r"[^a-z0-9]", "", (name or "").lower())
    return s[:40]

def suggest_slug(base: str, taken: set[str]) -> str:
    base = base or "app"
    if not SLUG_RE.fullmatch(base):
        base = "app"
    cand, i = base, 1
    while cand in taken:
        i += 1
        cand = f"{base}{i}"
    return cand
