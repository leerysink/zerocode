# app/deploy/apkbuild.py
"""Android 远程 URL 壳的后台构建（ADR-0042 / 规格 2026-11-05）。

发布成功或用户手动重试时入队；**串行**构建，一次只跑一个。构建在
`zerocode-android-builder:1` 容器里用 build-tools 直出 APK（见 app/templates/android-shell），
成功后用 `mobile._save` 原子写进应用 prod 的 uploads 卷，下载地址与 H5 分发完全复用。

状态落 `app_mobile_apk`（无行=idle）。失败只影响 APK：旧产物保留、发布结果不变、弹框可重试。
应用发布版本不进壳参数哈希——只有名称/图标/启动地址/包名/壳修订号变化才会触发重出包。
"""
import asyncio
import logging
import secrets
import shutil
from contextlib import suppress
from pathlib import Path
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from app.config import data_dir
from app.deploy import engine, mobile, nics, shell
from app.deploy.compose import MOBILE_STACK
from app.deploy.errors import DeployError

logger = logging.getLogger(__name__)

IMAGE = "zerocode-android-builder:1"
TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "templates" / "android-shell"
ICON_DIR_NAME = "mobile_icons"
BUILD_DIR_NAME = "apk_build"
KEYSTORE_DIR_NAME = "apk_keystore"
BUILDER_BUILD_TIMEOUT = 3600
APK_BUILD_TIMEOUT = 1800
_IDLE_ERROR = "平台重启导致构建中断，请重试"
_UNDEFINED_TABLE = "42P01"   # PostgreSQL: relation does not exist


# ---------- 文件位置（API 与 worker 共用同一套口径） ----------

def icon_dir() -> Path:
    return data_dir() / ICON_DIR_NAME


def custom_icon_path(slug: str) -> Path | None:
    for ext in ("png", "jpg"):
        p = icon_dir() / f"{slug}.{ext}"
        if p.is_file():
            return p
    return None


def read_icon(slug: str) -> bytes | None:
    p = custom_icon_path(slug)
    return p.read_bytes() if p else None


def save_icon(slug: str, data: bytes, ext: str) -> Path:
    root = icon_dir()
    root.mkdir(parents=True, exist_ok=True)
    dst = root / f"{slug}.{ext}"
    tmp = root / f".{slug}.{secrets.token_hex(4)}.part"
    tmp.write_bytes(data)
    tmp.replace(dst)
    for other in ("png", "jpg"):
        if other != ext:
            with suppress(FileNotFoundError):
                (root / f"{slug}.{other}").unlink()
    return dst


def delete_icon(slug: str) -> bool:
    hit = False
    for ext in ("png", "jpg"):
        p = icon_dir() / f"{slug}.{ext}"
        if p.is_file():
            p.unlink()
            hit = True
    return hit


def build_workspace(slug: str) -> Path:
    return data_dir() / BUILD_DIR_NAME / slug


def prepare_keys() -> Path:
    """平台统一 keystore 的目录；口令只在首次生成时落盘（0600），构建容器只读它。"""
    root = data_dir() / KEYSTORE_DIR_NAME
    root.mkdir(parents=True, exist_ok=True)
    pw = root / "pass.txt"
    if not pw.is_file():
        pw.write_text(secrets.token_hex(24), encoding="utf-8")
        with suppress(OSError):
            pw.chmod(0o600)
    return root


def cleanup_app(slug: str) -> None:
    """删除应用记录时清平台侧临时文件；prod 卷里的 APK 归属应用环境，不在这里删。"""
    delete_icon(slug)
    shutil.rmtree(build_workspace(slug), ignore_errors=True)


# ---------- 壳参数 / 状态 ----------

def current_params(app: dict) -> dict:
    slug = (app.get("slug") or "").strip()
    return shell.shell_params(
        name=app.get("name") or "", slug=slug, domain=nics.GATEWAY_DOMAIN,
        start_url=mobile.h5_url(app), custom_icon=read_icon(slug) if slug else None)


_EMPTY_STATE = {
    "status": "idle", "error": None, "shell_version": 0, "params_hash": None,
    "requested_at": None, "built_at": None, "apk_size": None,
}


async def get_state(sf, app_id: int) -> dict:
    async with sf() as s:
        row = (await s.execute(text(
            "SELECT status, error, shell_version, params_hash, requested_at, built_at, apk_size "
            "FROM app_mobile_apk WHERE app_id=:i"), {"i": app_id})).mappings().first()
    return dict(row) if row else dict(_EMPTY_STATE)


async def _mark_building(sf, app_id: int) -> None:
    async with sf() as s:
        await s.execute(text(
            "INSERT INTO app_mobile_apk (app_id, status, error, requested_at, updated_at) "
            "VALUES (:i, 'building', NULL, now(), now()) "
            "ON CONFLICT (app_id) DO UPDATE SET status='building', error=NULL, "
            "requested_at=now(), updated_at=now()"), {"i": app_id})
        await s.commit()


async def _mark_ready(sf, app_id: int, *, version: int, params_hash: str, size: int) -> None:
    async with sf() as s:
        await s.execute(text(
            "INSERT INTO app_mobile_apk (app_id, status, error, shell_version, params_hash, "
            "requested_at, built_at, apk_size, updated_at) "
            "VALUES (:i, 'ready', NULL, :v, :h, now(), now(), :s, now()) "
            "ON CONFLICT (app_id) DO UPDATE SET status='ready', error=NULL, shell_version=:v, "
            "params_hash=:h, built_at=now(), apk_size=:s, updated_at=now()"),
            {"i": app_id, "v": version, "h": params_hash, "s": size})
        await s.commit()


async def _mark_failed(sf, app_id: int, error: str) -> None:
    msg = (error or "未知错误").strip()[:1000]
    async with sf() as s:
        await s.execute(text(
            "INSERT INTO app_mobile_apk (app_id, status, error, requested_at, updated_at) "
            "VALUES (:i, 'failed', :e, now(), now()) "
            "ON CONFLICT (app_id) DO UPDATE SET status='failed', error=:e, updated_at=now()"),
            {"i": app_id, "e": msg})
        await s.commit()


# ---------- 构建器 ----------

class _AsyncFile:
    """mobile._save 要 `await read(n)` 的流；这里把同步文件包成异步读。"""

    def __init__(self, path: Path):
        self._fp = path.open("rb")

    async def read(self, n: int) -> bytes:
        return self._fp.read(n)

    def close(self) -> None:
        self._fp.close()


def _tail(log: str, limit: int = 600) -> str:
    return ((log or "").strip()[-limit:] or "docker 无输出")


async def _docker(*args, cwd=None, timeout=600):
    """D-1 模块级名字纪律：测试 monkeypatch `apkbuild._docker` 即可。"""
    return await engine.docker(*args, cwd=cwd, timeout=timeout)


async def _apk_size(root: Path, slug: str) -> int | None:
    return await mobile._apk_size(root, slug)


async def _save(root: Path, slug: str, stream) -> int:
    return await mobile._save(root, slug, stream)


def _make_workspace(app: dict, params: dict, version: int, custom_icon: bytes | None) -> Path:
    ws = build_workspace(app["slug"])
    shutil.rmtree(ws, ignore_errors=True)
    src = ws / "src" / "com" / "zerocode" / "shell"
    (ws / "res" / "values").mkdir(parents=True)
    src.mkdir(parents=True)
    shutil.copy2(TEMPLATE_DIR / "build.sh", ws / "build.sh")
    shutil.copy2(TEMPLATE_DIR / "src" / "MainActivity.java", src / "MainActivity.java")
    (src / "ShellConfig.java").write_text(shell.shell_config_java(params["start_url"]),
                                          encoding="utf-8", newline="\n")
    (ws / "AndroidManifest.xml").write_text(shell.manifest_xml(
        package=params["package"], version_code=version,
        version_name_=shell.version_name(version),
        cleartext=shell.is_cleartext(params["start_url"])), encoding="utf-8", newline="\n")
    (ws / "res" / "values" / "strings.xml").write_text(
        shell.strings_xml(params["label"]), encoding="utf-8", newline="\n")
    if custom_icon is not None:
        # 后缀统一 .png 交给 build.sh 的 convert：ImageMagick 按魔数识别 JPEG，不靠扩展名。
        (ws / "icon.png").write_bytes(custom_icon)
    else:
        (ws / "icon.svg").write_text(shell.auto_icon_svg(params["label"], app["slug"]),
                                     encoding="utf-8", newline="\n")
    return ws


async def _ensure_image() -> None:
    code, _ = await _docker("image", "inspect", IMAGE)
    if code == 0:
        return
    logger.info("Android 构建镜像缺失，开始构建 %s（首次需要下载 Android SDK）", IMAGE)
    code, log = await _docker("build", "-t", IMAGE, str(TEMPLATE_DIR),
                              timeout=BUILDER_BUILD_TIMEOUT)
    if code != 0:
        raise DeployError(f"Android 构建镜像准备失败：{_tail(log)}")


async def _build(sf, app_id: int) -> None:
    from app.services import app_service
    app = await app_service.get_app(sf, app_id)
    if app.get("stack") != MOBILE_STACK:
        return
    if app.get("status_prod") != "running":
        raise DeployError("正式环境未运行：请先发布并启动正式环境，再构建 APK")
    slug = app["slug"]
    root = await engine._app_root(sf, app)
    params = current_params(app)
    if not params["start_url"]:
        raise DeployError("启动地址为空：请确认网关域名与正式环境端口")
    state = await get_state(sf, app_id)
    version = int(state.get("shell_version") or 0) + 1

    ws = _make_workspace(app, params, version, read_icon(slug))
    keys = prepare_keys()
    await _ensure_image()
    code, log = await _docker(
        "run", "--rm", "--network", "none",
        "-v", f"{ws}:/workspace",
        "-v", f"{keys}:/workspace/keystore",
        "-e", f"APP_VERSION_CODE={version}",
        "-e", f"APP_VERSION_NAME={shell.version_name(version)}",
        IMAGE, "/bin/sh", "/workspace/build.sh",
        timeout=APK_BUILD_TIMEOUT)
    if code != 0:
        raise DeployError(f"APK 构建失败：{_tail(log)}")

    apk = ws / "out" / "app.apk"
    if not apk.is_file() or apk.stat().st_size <= 0:
        raise DeployError("APK 构建失败：构建器没有产出 out/app.apk")
    if apk.stat().st_size > mobile.APK_MAX_BYTES:
        raise DeployError(f"APK 超过 {mobile.APK_MAX_MB}MB 上限")

    stream = _AsyncFile(apk)
    try:
        written = await _save(root, slug, stream)
    finally:
        stream.close()
    await _mark_ready(sf, app_id, version=version,
                      params_hash=shell.params_hash(params), size=written)
    logger.info("APK 构建完成 app_id=%s slug=%s version=%s size=%s",
                app_id, slug, version, written)


class ApkBuilder:
    """单 worker 队列：串行构建；同应用重复入队折叠，构建中再来的请求在本轮后补跑一次。"""

    def __init__(self, session_factory):
        self.sf = session_factory
        self._queue: asyncio.Queue[int] = asyncio.Queue()
        self._queued: set[int] = set()
        self._rerun: set[int] = set()
        self._active: int | None = None
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        await self.recover()
        self._task = asyncio.create_task(self._loop(), name="apk-builder")

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def recover(self) -> None:
        try:
            async with self.sf() as s:
                await s.execute(text(
                    "UPDATE app_mobile_apk SET status='failed', error=:e, updated_at=now() "
                    "WHERE status='building'"), {"e": _IDLE_ERROR})
                await s.commit()
        except ProgrammingError as e:
            # 镜像里没有 changelog/：新表要等 deploy.sh 第 8 步 apply-init.sh 才建。
            # 启动收口不能因此拦住平台——否则探活永远失败，apply-init.sh 也轮不到执行。
            if getattr(getattr(e, "orig", None), "sqlstate", "") != _UNDEFINED_TABLE:
                raise
            logger.warning("app_mobile_apk 尚未建表，跳过构建状态收口（等 apply-init.sh）")

    async def enqueue(self, app_id: int, *, force: bool = False, app: dict | None = None) -> str:
        """入队并立即标记 building；返回 queued/reused/deferred/skipped/building。

        `force=True` 是用户手动重建：不要求参数变化，但要求 prod 正在运行（否则抛 409）。
        参数变化但 prod 未运行（`force=False`）时返回 deferred——哈希已经能算出 stale，
        下一次发布成功会自动重建，不在这里造一条失败记录。
        """
        if app_id in self._queued or self._active == app_id:
            if force:
                self._rerun.add(app_id)
            return "building"
        if app is None:
            from app.services import app_service
            app = await app_service.get_app(self.sf, app_id)
        if app.get("stack") != MOBILE_STACK:
            return "skipped"
        if app.get("status_prod") != "running":
            if force:
                raise DeployError("正式环境未运行：请先发布并启动正式环境，再构建 APK", status=409)
            return "deferred"
        params = current_params(app)
        digest = shell.params_hash(params)
        state = await get_state(self.sf, app_id)
        if not force and state.get("status") == "ready" and state.get("params_hash") == digest:
            root = await engine._app_root(self.sf, app)
            if await _apk_size(root, app["slug"]):
                return "reused"
        await _mark_building(self.sf, app_id)
        self._queued.add(app_id)
        await self._queue.put(app_id)
        return "queued"

    async def _loop(self) -> None:
        while True:
            app_id = await self._queue.get()
            self._queued.discard(app_id)
            self._active = app_id
            try:
                await _build(self.sf, app_id)
            except asyncio.CancelledError:
                with suppress(Exception):
                    await _mark_failed(self.sf, app_id, _IDLE_ERROR)
                raise
            except Exception as e:  # noqa: BLE001
                logger.warning("APK 构建失败 app_id=%s: %s", app_id, e)
                with suppress(Exception):
                    await _mark_failed(self.sf, app_id, str(e))
            finally:
                self._active = None
                self._queue.task_done()
                if app_id in self._rerun:
                    self._rerun.discard(app_id)
                    try:
                        await self.enqueue(app_id)
                    except Exception:  # noqa: BLE001
                        logger.debug("APK 补跑入队失败 app_id=%s", app_id, exc_info=True)
