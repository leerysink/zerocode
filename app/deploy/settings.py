# app/deploy/settings.py
from pathlib import Path
from sqlalchemy import text

DEFAULTS = {
    "deploy.apps_root": "",
    "deploy.port_base": 8210,
    "deploy.port_max": 8399,
    "deploy.keep_versions": 5,
    "deploy.health_timeout": 240,
    # 大型应用冷启动要在容器里下载 Maven 依赖（实测 ≈410s），与默认 240s 不是一个量级
    "deploy.health_timeout_large": 900,
    # 大型栈首次 prod build：容器内下载整套 Maven 依赖 + npm ci + vue-tsc 类型检查（spike 实测 ≈350s 起）
    "deploy.build_timeout": 600,
    "deploy.build_timeout_large": 1800,
    "deploy.auto_stop_dev_on_exit": True,
    # 商店包是平台资产，默认落在仓库根 store/（与 apps/ 同级）；空串走 store_root() 的兜底。
    "store.root": "",
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


def health_timeout(cfg: dict, stack: str) -> int:
    """按栈取健康等待上限：只有真有长冷启动的栈走 large 值，其余照旧。

    large 键没设时回落到通用键（管理员改一个键就能调两条路径）；回落值从 DEFAULTS 取而不是在此再抄
    一个字面量：默认值只应有一处。

    这里的「长超时的栈」与 engine.JOINT_HEALTH_STACKS「要联合探针的栈」是两套独立语义（一个管等待时长、
    一个管探不探后端），只是今天都恰好只有 vue3-springboot；新增需长冷启动的栈时记得同步这里，
    否则会出现「联合探针却只给 240s」的漂移。
    """
    base = int(cfg.get("deploy.health_timeout") or DEFAULTS["deploy.health_timeout"])
    if stack == "vue3-springboot":
        return int(cfg.get("deploy.health_timeout_large") or base)
    return base


def build_timeout(cfg: dict, stack: str) -> int:
    """镜像构建上限：与 health_timeout 同构，只有真有长构建的栈走 large 值。

    退回值取 `deploy.build_timeout`（可运维下调），两者都读空才落到 DEFAULTS 里的硬默认（默认值只应有一处）。
    这里的「长构建的栈」与 health_timeout「长冷启动的栈」今天恰好都只有 vue3-springboot，但同样是两套
    独立语义（一个管构建时长、一个管启动等待），新增需长构建的栈时记得同步这里。
    """
    base = int(cfg.get("deploy.build_timeout") or DEFAULTS["deploy.build_timeout"])
    if stack == "vue3-springboot":
        return int(cfg.get("deploy.build_timeout_large") or base)
    return base
