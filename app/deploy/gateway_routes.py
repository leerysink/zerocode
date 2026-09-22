# app/deploy/gateway_routes.py
"""网关路由表导出（通配域名 HTTPS 网关）。

把 status_prod=='running' 的应用投影成 {slug: prod_port} 写入 routes.json，
供 OpenResty 网关按 {slug}.zerocode.com 反代到各应用 prod 宿主端口。

最高接缝：平台侧唯一改动面。prod 状态跃迁经 prod.set_status、删除经 app_service.delete_app，
二者之后调用 refresh 即覆盖增删时机。routes.json 是「投影」不是「真相源」，
所以 refresh 失败只告警、绝不打断业务主流程。
"""
import json
import logging
import os
from pathlib import Path

from sqlalchemy import text

from app.deploy import slugs
from app.deploy.settings import REPO_ROOT

logger = logging.getLogger(__name__)

DEFAULT_ROUTES_PATH = REPO_ROOT / "gateway" / "routes" / "routes.json"


def routes_out_path() -> Path:
    """routes.json 落盘位置：env 覆盖优先（本机演示 / 服务器换路径无需改码），否则仓库默认。"""
    raw = os.environ.get("ZEROCODE_GATEWAY_ROUTES") or ""
    return Path(raw) if raw else DEFAULT_ROUTES_PATH


def _render(routes: dict) -> str:
    # 稳定序列化（排序 + 缩进），让「内容没变」能被字符串比较可靠判定
    return json.dumps(routes, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def _atomic_write_if_changed(path: Path, content: str) -> None:
    """内容一致不落盘（幂等，保 mtime/不触发网关无谓刷新）；否则 .tmp + os.replace 原子替换。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(content, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


async def export_routes(session_factory, out_path=None) -> dict:
    """读全部 running 应用 → {slug: prod_port}，原子写盘。返回导出的路由表（便于断言/日志）。

    通配证书只匹配一级域名，故对 slug 做单段约束（复用 slugs.SLUG_RE）：
    含点号等的畸形 slug 跳过并告警，不进表——它们本就无法被 *.zerocode.com 覆盖。
    """
    out_path = Path(out_path) if out_path else routes_out_path()
    async with session_factory() as s:
        rows = (await s.execute(text(
            "SELECT slug, prod_port FROM app_project WHERE status_prod = 'running' "
            "ORDER BY slug"))).mappings().all()
    routes: dict[str, int] = {}
    for r in rows:
        slug = r["slug"]
        if not slugs.SLUG_RE.fullmatch(slug or ""):
            logger.warning("routes.json 跳过非法 slug（不满足一级域名约束）: %r", slug)
            continue
        routes[slug] = int(r["prod_port"])
    _atomic_write_if_changed(out_path, _render(routes))
    return routes


async def refresh(session_factory) -> None:
    """业务旁路刷新：失败只记日志，不上抛（网关数据问题不能回滚 prod 起停/删除）。"""
    try:
        await export_routes(session_factory)
    except Exception:
        logger.exception("gateway routes.json 刷新失败（忽略，不影响主流程）")
