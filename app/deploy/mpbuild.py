# app/deploy/mpbuild.py
"""微信小程序的构建/上传任务器（ADR-0044/0045 / 规格 2026-11-09）。

一次任务 = 复制 `frontend/mobile/` → 叠加 mp-weixin 配置 → 编译 → `miniprogram-ci` preview/upload。
**串行**执行；发布成功后的自动上传、弹框手动重传、绑定后的预检共用这一条队列。
失败只影响小程序：旧开发版保留、发布结果不变；状态落 `app_mobile_miniprogram`。
"""
import asyncio
import logging
import shutil
from contextlib import suppress
from datetime import datetime
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from app.deploy import engine, mpcreds, mpoverlay, mpruntime
from app.deploy.compose import MOBILE_STACK
from app.deploy.errors import DeployError

logger = logging.getLogger(__name__)

IMAGE = "zerocode-miniprogram-builder:1"
TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "templates" / "miniprogram-builder"
BUILDER_BUILD_TIMEOUT = 1800
JOB_TIMEOUT = 1800
PREVIEW_KINDS = ("precheck", "preview")
UPLOAD_KINDS = ("upload",)
_IDLE_ERROR = "平台重启导致任务中断，请重试"
_UNDEFINED_TABLE = "42P01"
_DESC_MAX = 200
_VERSION_MAX = 64

workspace = mpoverlay.workspace
preview_path = mpoverlay.preview_path


def cleanup_app(slug: str) -> None:
    shutil.rmtree(workspace(slug), ignore_errors=True)
    with suppress(FileNotFoundError):
        preview_path(slug).unlink()


def classify_error(log: str) -> str:
    """把构建/上传输出归成可读分类，尾部保留原文便于排障（规格 §5）。"""
    text = (log or "").lower()
    tail = (log or "").strip()[-600:] or "构建器无输出"
    if any(k in text for k in ("invalid ip", "not in whitelist", "whitelist", "80082")):
        return f"IP 白名单：{tail}"
    if "invalid appid" in text or ("appid" in text and "invalid" in text):
        return f"AppID 无效：{tail}"
    if "private key" in text or ("pkp" in text and "invalid" in text):
        return f"上传密钥无效：{tail}"
    if any(k in text for k in ("getaddrinfo", "enotfound", "econnrefused", "etimedout",
                               "network is unreachable")):
        return f"出网失败：{tail}"
    if any(k in text for k in ("certificate", "self signed", "tls handshake", "ssl error")):
        return f"域名或证书不可用：{tail}"
    return f"编译或上传失败：{tail}"


# ---------- 状态（与 mpcreds.get_binding 同一张表） ----------

async def _mark_job(sf, app_id: int, status: str, kind: str) -> None:
    async with sf() as s:
        await s.execute(text(
            "UPDATE app_mobile_miniprogram SET status=:st, job_kind=:k, error=NULL, "
            "updated_at=now() WHERE app_id=:i"), {"i": app_id, "st": status, "k": kind})
        await s.commit()


async def _mark_ready(sf, app_id: int, kind: str, *, version: str | None = None,
                      preview: bool = False) -> None:
    sets = ["status='ready'", "job_kind=:k", "error=NULL", "updated_at=now()"]
    if version is not None:
        sets += ["version=:v", "uploaded_at=now()"]
    if preview:
        sets.append("preview_at=now()")
    async with sf() as s:
        await s.execute(text(
            f"UPDATE app_mobile_miniprogram SET {', '.join(sets)} WHERE app_id=:i"),
            {"i": app_id, "k": kind, **({"v": version} if version is not None else {})})
        await s.commit()


async def _mark_failed(sf, app_id: int, error: str) -> None:
    msg = (error or "未知错误").strip()[:1000]
    async with sf() as s:
        await s.execute(text(
            "UPDATE app_mobile_miniprogram SET status='failed', error=:e, updated_at=now() "
            "WHERE app_id=:i"), {"i": app_id, "e": msg})
        await s.commit()


# ---------- 构建器 ----------

async def _docker(*args, cwd=None, timeout=JOB_TIMEOUT):
    """模块级名字纪律：测试 monkeypatch `mpbuild._docker` 即可。"""
    return await engine.docker(*args, cwd=cwd, timeout=timeout)


async def _ensure_image() -> None:
    code, _ = await _docker("image", "inspect", IMAGE)
    if code == 0:
        return
    logger.info("小程序构建镜像缺失，开始构建 %s", IMAGE)
    code, log = await _docker("build", "-t", IMAGE, str(TEMPLATE_DIR),
                              timeout=BUILDER_BUILD_TIMEOUT)
    if code != 0:
        raise DeployError(f"小程序构建镜像准备失败：{classify_error(log)}")


def _version_for(app: dict) -> str:
    return (app.get("current_version") or "preview")[:_VERSION_MAX]


def _desc_for(app: dict) -> str:
    return f"{app.get('name') or '应用'} {datetime.now():%Y-%m-%d %H:%M}"[:_DESC_MAX]


async def _job(sf, app_id: int, kind: str) -> None:
    from app.services import app_service
    app = await app_service.get_app(sf, app_id)
    if app.get("stack") != MOBILE_STACK:
        return
    binding = await mpcreds.get_binding(sf, app_id)
    appid = binding.get("appid")
    if not appid:
        raise DeployError("尚未绑定小程序账号")
    if kind in UPLOAD_KINDS:
        if app.get("status_prod") != "running":
            raise DeployError("正式环境未运行：请先发布并启动正式环境，再上传小程序")
        if not app.get("current_version"):
            raise DeployError("应用还没有发布版本：请先完成一次正式发布")
    elif kind in PREVIEW_KINDS:
        # 预检额外验真机可达性：备案域名 + 有效 HTTPS（自签在真机不生效），无跳过分支
        ready = await mpruntime.readiness(app, binding, probe=True)
        if not ready["ok"]:
            raise DeployError(mpruntime.reason_text(ready["reasons"]))

    key = mpcreds.key_path(app["slug"])
    if not key.is_file():
        raise DeployError("上传密钥缺失：请重新绑定小程序账号")
    root = await engine._app_root(sf, app)
    ws = mpoverlay.make_workspace(app, root, appid, app["slug"])
    try:
        await _ensure_image()
        if kind in UPLOAD_KINDS:
            await _mark_job(sf, app_id, "uploading", kind)
        code, log = await _docker(
            "run", "--rm",
            "-v", f"{ws}:/workspace",
            "-v", f"{key.parent}:/keys:ro",
            "-e", f"MP_APPID={appid}",
            "-e", f"MP_MODE={'upload' if kind in UPLOAD_KINDS else 'preview'}",
            "-e", f"MP_VERSION={_version_for(app)}",
            "-e", f"MP_DESC={_desc_for(app)}",
            "-e", "MP_KEY=/keys/private.key",
            IMAGE)
        if code != 0:
            raise DeployError(classify_error(log))

        if kind in PREVIEW_KINDS:
            produced = ws / "preview.jpg"
            if not produced.is_file() or produced.stat().st_size <= 0:
                raise DeployError("预览码未生成：构建器没有输出 preview.jpg")
            dst = preview_path(app["slug"])
            dst.parent.mkdir(parents=True, exist_ok=True)
            tmp = dst.with_name(f".{dst.name}.part")
            shutil.copyfile(produced, tmp)
            tmp.replace(dst)
            await _mark_ready(sf, app_id, kind, preview=True)
        else:
            await _mark_ready(sf, app_id, kind, version=_version_for(app))
        logger.info("小程序任务完成 app_id=%s kind=%s", app_id, kind)
    finally:
        shutil.rmtree(ws, ignore_errors=True)


class MiniprogramBuilder:
    """单 worker 队列：串行构建；同应用重复入队折叠，进行中再来的请求本轮后补跑一次。"""

    def __init__(self, session_factory):
        self.sf = session_factory
        self._queue: asyncio.Queue[tuple[int, str]] = asyncio.Queue()
        self._queued: set[int] = set()
        self._kinds: dict[int, str] = {}
        self._rerun: dict[int, str] = {}
        self._active: int | None = None
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        await self.recover()
        self._task = asyncio.create_task(self._loop(), name="mp-builder")

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
                    "UPDATE app_mobile_miniprogram SET status='failed', error=:e, "
                    "updated_at=now() WHERE status IN ('building', 'uploading')"),
                    {"e": _IDLE_ERROR})
                await s.commit()
        except ProgrammingError as e:
            # 升级镜像里还没有 changelog/：新表等 apply-init.sh 建；启动收口不能拦住平台
            if getattr(getattr(e, "orig", None), "sqlstate", "") != _UNDEFINED_TABLE:
                raise
            logger.warning("app_mobile_miniprogram 尚未建表，跳过任务状态收口（等 apply-init.sh）")

    async def enqueue(self, app_id: int, *, kind: str = "upload", force: bool = False,
                      app: dict | None = None) -> str:
        """返回 queued/building/deferred/skipped。

        precheck/preview 与发布无关（prod 没跑也能验凭据）；upload 要求 prod 正在运行，
        `force=True`（手动重传）不满足时抛 409。
        """
        if app_id in self._queued or self._active == app_id:
            if force or self._kinds.get(app_id) != kind:
                self._rerun[app_id] = kind
            return "building"
        if app is None:
            from app.services import app_service
            app = await app_service.get_app(self.sf, app_id)
        if app.get("stack") != MOBILE_STACK:
            return "skipped"
        binding = await mpcreds.get_binding(self.sf, app_id)
        if not binding.get("appid"):
            return "skipped"
        if kind in UPLOAD_KINDS and app.get("status_prod") != "running":
            if force:
                raise DeployError("正式环境未运行：请先发布并启动正式环境，再上传小程序", status=409)
            return "deferred"
        await _mark_job(self.sf, app_id, "building", kind)
        self._queued.add(app_id)
        self._kinds[app_id] = kind
        await self._queue.put((app_id, kind))
        return "queued"

    async def _loop(self) -> None:
        while True:
            app_id, kind = await self._queue.get()
            self._queued.discard(app_id)
            self._active = app_id
            try:
                await _job(self.sf, app_id, kind)
            except asyncio.CancelledError:
                with suppress(Exception):
                    await _mark_failed(self.sf, app_id, _IDLE_ERROR)
                raise
            except Exception as e:  # noqa: BLE001
                logger.warning("小程序任务失败 app_id=%s kind=%s: %s", app_id, kind, e)
                with suppress(Exception):
                    await _mark_failed(self.sf, app_id, str(e))
            finally:
                self._active = None
                self._queue.task_done()
                self._kinds.pop(app_id, None)
                nxt = self._rerun.pop(app_id, None)
                if nxt:
                    try:
                        await self.enqueue(app_id, kind=nxt, force=True)
                    except Exception:  # noqa: BLE001
                        logger.debug("小程序任务补跑入队失败 app_id=%s", app_id, exc_info=True)
