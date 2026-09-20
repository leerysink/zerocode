# app/deploy/settings.py
from pathlib import Path
from sqlalchemy import text

DEFAULTS = {
    "deploy.apps_root": "",
    "deploy.port_base": 8210,
    "deploy.port_max": 8399,
    "deploy.keep_versions": 5,
    "deploy.health_timeout": 240,
    "deploy.auto_stop_dev_on_exit": True,
}
REPO_ROOT = Path(__file__).resolve().parents[2]  # app/deploy/settings.py → 仓库根

async def deploy_cfg(session_factory) -> dict:
    """读 deploy.* 键并与默认值合并（缺键回退默认，保证测试库可用）。"""
    cfg = dict(DEFAULTS)
    async with session_factory() as s:
        rows = (await s.execute(text(
            "SELECT key, value FROM app_config WHERE key LIKE 'deploy.%'"))).fetchall()
    for k, v in rows:
        cfg[k] = v
    return cfg

def resolve_apps_root(cfg: dict) -> Path:
    raw = cfg.get("deploy.apps_root") or ""
    return Path(raw) if raw else REPO_ROOT / "apps"
