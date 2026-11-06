# app/deploy/mobile.py
"""移动端分发产物（规格 2026-11-03 H5 + 规格 2026-11-05 APK 壳）。

H5 地址只读 `nics.app_url`（ADR-0016 单点）。APK 从 2026-11-05 起由平台在生产发布后自动
构建（远程 URL WebView 壳，ADR-0042），落点仍是应用 prod 的 `uploads` 命名卷，下载地址
`{app_url}/api/uploads/mobile/{slug}.apk` 由应用自己的 nginx → backend 提供（`/uploads/**` 本就
permitAll）。平台写入**不走**应用模板的 `/api/file/upload`：那条链要应用自己的 JWT，平台没有也不该
持有终端用户的移动端会话——所以经 `docker compose exec -T backend` 的 stdin 直接灌卷。因此应用前端
nginx 的 `client_max_body_size 50m` 与 Spring multipart 上限**不在这条链上**（R-5 的更正），
真正的体积闸门是本模块的 `APK_MAX_BYTES`。

本模块保留 H5/下载地址投影、产物落卷与体积探测；构建状态与壳参数在 `apkbuild` / `shell`。
"""
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app.deploy import engine, nics, shell
from app.deploy.compose import MOBILE_STACK
from app.deploy.errors import DeployError
from app.deploy.prod import _prod_compose_args
from app.deploy.settings import preview_url

APK_SUBDIR = "mobile"
# 200MB：远高于常见 APK，又能在「选错文件」时把平台磁盘与卷挡住；前端同值提示由接口回传。
APK_MAX_BYTES = 200 * 1024 * 1024
APK_MAX_MB = APK_MAX_BYTES // (1024 * 1024)
APK_UPLOAD_TIMEOUT = 600


def is_mobile(app: dict) -> bool:
    return app.get("stack") == MOBILE_STACK


def h5_url(app: dict) -> str:
    """H5 入口：与「打开生产」同一个 base（`nics.app_url`），挂固定前缀 `/h5/`。"""
    base = nics.app_url(app)
    return f"{base}/h5/" if base else ""


def apk_url(app: dict) -> str:
    """APK 下载地址：应用自己域名的上传件路径，镜像重建（卷保留）后地址不变。"""
    base = nics.app_url(app)
    slug = (app.get("slug") or "").strip()
    return f"{base}/api/uploads/{APK_SUBDIR}/{slug}.apk" if base and slug else ""


def _container_path(slug: str) -> str:
    return f"/uploads/{APK_SUBDIR}/{slug}.apk"


def _iso(dt) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


async def info(sf, app: dict) -> dict:
    """弹框数据源：两条环境地址 + APK 现状（构建状态/壳参数/图标）。prod 没在跑时不去 exec 探文件。"""
    if not is_mobile(app):
        raise DeployError("仅移动端应用支持移动端分发", status=400)
    size = None
    if app.get("status_prod") == "running":
        root = await engine._app_root(sf, app)
        size = await _apk_size(root, app["slug"])
    from app.deploy import apkbuild
    state = await apkbuild.get_state(sf, app["id"])
    params = apkbuild.current_params(app)
    digest = shell.params_hash(params)
    icon_file = apkbuild.custom_icon_path(app["slug"])
    icon_ts = icon_file.stat().st_mtime if icon_file else None
    return {
        "stack": app.get("stack"),
        "status_dev": app.get("status_dev"),
        "status_prod": app.get("status_prod"),
        "dev_url": f"{preview_url(app['dev_port'])}/h5/" if app.get("dev_port") else "",
        "h5_url": h5_url(app),
        "apk_url": apk_url(app),
        "apk_size": size,
        "apk_max_bytes": APK_MAX_BYTES,
        "apk": {
            "status": state["status"],
            "error": state["error"],
            "shell_version": state["shell_version"],
            "params_stale": state["params_hash"] != digest,
            "package_name": params["package"],
            "can_rebuild": app.get("status_prod") == "running" and state["status"] != "building",
            "requested_at": _iso(state["requested_at"]),
            "built_at": _iso(state["built_at"]),
        },
        "icon": {
            "custom": icon_file is not None,
            "url": f"/api/apps/{app['id']}/mobile/icon?v={int(icon_ts) if icon_ts else digest[:12]}",
            "updated_at": _iso(datetime.fromtimestamp(icon_ts, tz=timezone.utc)) if icon_ts else None,
        },
    }


async def _save(root: Path, slug: str, file) -> int:
    """把上传流写进 prod backend 容器的 uploads 卷；先 `.part`、灌完再单独 mv。

    分两条 exec 而不是一条 `cat > .part && mv`：CLI 进程被杀时容器内的 `cat` 会读到 EOF 并继续
    执行 `&&` 后半段——一条命令反而会把半个文件转正。两条命令的语义是「灌流完整（退出码 0）才
    改名」，中断最多留下一个隐藏的 `.part`。
    每次上传用独立 `.part` 名（含随机 token）：两次并发上传同一应用不会互相截断/改名，最终
    `mv` 是原子换名，正式文件必是某一次完整上传的内容。
    """
    reader = _CappedReader(file)
    token = uuid4().hex
    try:
        code, log = await _docker(
            *_prod_compose_args(root), "exec", "-T", "backend", "sh", "-c", _stage_cmd(slug, token),
            cwd=str(root), feed=reader, timeout=APK_UPLOAD_TIMEOUT)
        if code == 0 and reader.total == 0:
            raise DeployError("APK 文件为空", status=400)
        if code == 0:
            code, log = await _docker(
                *_prod_compose_args(root), "exec", "-T", "backend", "sh", "-c", _promote_cmd(slug, token),
                cwd=str(root), timeout=60)
    except FileNotFoundError as e:   # 与 preflight_docker 同口径：CLI 缺失是 503 而非 500
        raise DeployError("Docker CLI 未安装：请安装并启动 Docker Desktop（设置页「部署环境」可一键启动）",
                          status=503) from e
    if code != 0:
        tail = (log or "").strip()[-300:] or "docker 无输出"
        raise DeployError(f"APK 写入失败：请确认正式环境运行正常（{tail}）", status=503)
    return reader.total


def _part_path(slug: str, token: str) -> str:
    return f"/uploads/{APK_SUBDIR}/.{slug}.apk.{token}.part"


def _stage_cmd(slug: str, token: str) -> str:
    """容器内落盘命令：只用 POSIX 内置（mkdir/cat），别假设镜像里有 coreutils 之外的家伙。"""
    return f"mkdir -p /uploads/{APK_SUBDIR} && cat > {_part_path(slug, token)}"


def _promote_cmd(slug: str, token: str) -> str:
    return f"mv {_part_path(slug, token)} {_container_path(slug)}"


async def _apk_size(root: Path, slug: str) -> int | None:
    """卷里现有 APK 的字节数；文件不存在/容器读不到一律 None（弹框只失去「已上传 xx MB」这一行）。"""
    try:
        code, log = await _docker(
            *_prod_compose_args(root), "exec", "-T", "backend", "sh", "-c",
            f"wc -c < {_container_path(slug)} 2>/dev/null || true",
            cwd=str(root), timeout=30)
    except Exception:
        return None
    last = (log or "").strip().splitlines()
    if code != 0 or not last or not last[-1].strip().isdigit():
        return None
    return int(last[-1].strip())


class _CappedReader:
    """给 `engine.docker(feed=…)` 套体积上限：超限抛 413，由 docker() 杀掉写管道子进程。

    半个文件不会 mv 成正式 APK——写入停在 `_stage_cmd` 的 `.part`，转正是灌流成功后的独立一步。
    """

    def __init__(self, file, limit: int | None = None):
        self._file = file
        self._limit = APK_MAX_BYTES if limit is None else limit
        self.total = 0

    async def read(self, n: int) -> bytes:
        chunk = await self._file.read(n)
        self.total += len(chunk)
        if self.total > self._limit:
            raise DeployError(f"APK 不能超过 {self._limit // (1024 * 1024)}MB", status=413)
        return chunk


async def _docker(*args, cwd=None, timeout=600, feed=None):
    """D-1 模块级名字纪律：唯一 Docker 交互点，测试 monkeypatch `mobile._docker` 即可。"""
    return await engine.docker(*args, cwd=cwd, timeout=timeout, feed=feed)
