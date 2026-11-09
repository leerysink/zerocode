# app/deploy/mpoverlay.py
"""把应用副本改造成可编译的 mp-weixin 工程（ADR-0044 / 规格 2026-11-09）。

应用源码 `frontend/mobile/` 只被**复制**，叠加全部发生在副本上：补 mp-weixin 依赖与脚本、
manifest 的 appid、project.config.json，并把接口基址从相对 `/api` 改成应用正式域名。
结构不符（缺文件、补丁命中不是恰好一处）一律失败——不产出指错后端的半成品包。
"""
import json
import re
import shutil
from pathlib import Path

from app.config import data_dir
from app.deploy.errors import DeployError
from app.deploy.mpruntime import api_base, request_domain

BUILD_DIR_NAME = "mp_build"
PREVIEW_DIR_NAME = "miniprogram_preview"
UNI_DEP = "@dcloudio/uni-app"
MP_DEP = "@dcloudio/uni-mp-weixin"
STRUCTURE_ERROR = "移动端请求封装结构已变化，需平台适配"

_TARGET = re.compile(r"url:\s*'/api'\s*\+\s*path")


def patch_request_js(text: str, base: str) -> str | None:
    """把 `url: '/api' + path` 换成绝对基址；命中数不是 1 就返回 None。"""
    patched, hits = _TARGET.subn(lambda _m: f"url: {json.dumps(base)} + path", text)
    return patched if hits == 1 else None


def overlay_package(pkg: dict) -> dict:
    deps = dict(pkg.get("dependencies") or {})
    version = deps.get(UNI_DEP)
    if not version:
        raise DeployError(f"移动端工程缺少 {UNI_DEP} 依赖，无法叠加 mp-weixin")
    deps[MP_DEP] = version
    scripts = dict(pkg.get("scripts") or {})
    scripts["build:mp-weixin"] = "uni build -p mp-weixin"
    return {**pkg, "dependencies": deps, "scripts": scripts}


def overlay_manifest(manifest: dict, appid: str) -> dict:
    return {
        **manifest,
        "mp-weixin": {
            "appid": appid,
            "usingComponents": True,
            # urlCheck 只影响开发者工具；真机/预览/体验/正式仍强制校验合法域名
            "setting": {"urlCheck": False, "es6": True, "minified": True},
        },
    }


def project_config(appid: str) -> dict:
    return {
        "appid": appid,
        "projectname": "zerocode-miniprogram",
        "compileType": "miniprogram",
        "miniprogramRoot": "dist/build/mp-weixin/",
        "setting": {"es6": True, "minified": True, "urlCheck": False},
    }


def workspace(slug: str) -> Path:
    return data_dir() / BUILD_DIR_NAME / slug


def preview_path(slug: str) -> Path:
    return data_dir() / PREVIEW_DIR_NAME / f"{slug}.jpg"


def make_workspace(app: dict, root: Path, appid: str, slug: str) -> Path:
    base = api_base(app)
    if not base:
        raise DeployError("小程序要求备案域名 + 有效 HTTPS 证书：请先开启 SSL 并完成域名解析")
    src = Path(root) / "frontend" / "mobile"
    if not (src / "package.json").is_file():
        raise DeployError("移动端工程不存在：缺少 frontend/mobile/package.json")

    ws = workspace(slug)
    shutil.rmtree(ws, ignore_errors=True)
    shutil.copytree(src, ws, ignore=shutil.ignore_patterns(
        "node_modules", "dist", ".git", ".DS_Store"))
    # 叠加失败也要清掉副本：_job 的 finally 只在 make_workspace 返回后才生效（US-15）
    try:
        _overlay_in_place(ws, appid, base)
    except Exception:
        shutil.rmtree(ws, ignore_errors=True)
        raise
    return ws


def _overlay_in_place(ws: Path, appid: str, base: str) -> None:
    pkg_path = ws / "package.json"
    pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
    pkg_path.write_text(json.dumps(overlay_package(pkg), ensure_ascii=False, indent=2),
                        encoding="utf-8", newline="\n")

    manifest_path = ws / "src" / "manifest.json"
    if not manifest_path.is_file():
        raise DeployError(f"{STRUCTURE_ERROR}（缺少 src/manifest.json）")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_path.write_text(json.dumps(overlay_manifest(manifest, appid),
                                        ensure_ascii=False, indent=2),
                             encoding="utf-8", newline="\n")

    (ws / "project.config.json").write_text(
        json.dumps(project_config(appid), ensure_ascii=False, indent=2),
        encoding="utf-8", newline="\n")

    req_path = ws / "src" / "utils" / "request.js"
    if not req_path.is_file():
        raise DeployError(f"{STRUCTURE_ERROR}（缺少 src/utils/request.js）")
    patched = patch_request_js(req_path.read_text(encoding="utf-8"), base)
    if patched is None:
        raise DeployError(f"{STRUCTURE_ERROR}（未找到唯一的 /api 请求基址）")
    req_path.write_text(patched, encoding="utf-8", newline="\n")
