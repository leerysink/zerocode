# tests/test_mp_overlay.py
"""小程序构建叠加（ADR-0044 / 规格 2026-11-09）：纯函数接缝。

只验「把应用副本改造成可编译的 mp-weixin 工程」这件事：配置叠加不改原对象、
request.js 补丁必须精确命中、地址投影随 SSL 开关走。
"""
import json
import shutil
from pathlib import Path

import pytest

from app.deploy import mpoverlay
from app.deploy.errors import DeployError

TEMPLATE_MOBILE = Path("app/templates/uniapp-overlay/frontend/mobile")
SLUG = "zcmini"
KEY = b"-----BEGIN PRIVATE KEY-----\nx\n-----END PRIVATE KEY-----\n"


def _app(**over):
    base = {"id": 7, "name": "小程序应用", "slug": SLUG, "stack": "vue3-springboot-mobile",
            "prod_port": 8210, "dev_port": 8211, "current_version": "b20261109000000"}
    base.update(over)
    return base


def test_request_domain_requires_ssl(monkeypatch):
    app = _app()
    monkeypatch.delenv("ZEROCODE_SSL_ENABLE", raising=False)
    assert mpoverlay.request_domain(app) == f"https://{SLUG}.zerocode.com"
    assert mpoverlay.api_base(app) == f"https://{SLUG}.zerocode.com/api"

    monkeypatch.setenv("ZEROCODE_SSL_ENABLE", "false")
    assert mpoverlay.request_domain(app) == ""
    assert mpoverlay.api_base(app) == ""


def test_patch_request_js_replaces_relative_api_once():
    src = (TEMPLATE_MOBILE / "src" / "utils" / "request.js").read_text(encoding="utf-8")
    patched = mpoverlay.patch_request_js(src, "https://zcmini.zerocode.com/api")
    assert patched is not None
    assert 'url: "https://zcmini.zerocode.com/api" + path' in patched
    assert "'/api' + path" not in patched


def test_patch_request_js_fails_loudly_when_structure_changed():
    assert mpoverlay.patch_request_js("export const x = 1\n", "https://a/api") is None
    # 多处命中同样不可猜：宁可失败，也不产出指错后端的包
    twice = "url: '/api' + path\nurl: '/api' + path\n"
    assert mpoverlay.patch_request_js(twice, "https://a/api") is None


def test_overlay_package_pins_same_uni_version_without_mutating_source():
    pkg = {"dependencies": {"@dcloudio/uni-app": "3.0.0-5030420261029002"},
           "scripts": {"build:h5": "uni build"}}
    out = mpoverlay.overlay_package(pkg)
    assert out["dependencies"]["@dcloudio/uni-mp-weixin"] == "3.0.0-5030420261029002"
    assert out["scripts"]["build:mp-weixin"] == "uni build -p mp-weixin"
    assert "@dcloudio/uni-mp-weixin" not in pkg["dependencies"]


def test_overlay_package_requires_uni_dependency():
    with pytest.raises(DeployError) as e:
        mpoverlay.overlay_package({"dependencies": {}})
    assert "uni-app" in str(e.value)


def test_overlay_manifest_adds_mp_weixin_appid_and_keeps_h5():
    manifest = {"name": "小程序应用", "h5": {"router": {"base": "/h5/"}}}
    out = mpoverlay.overlay_manifest(manifest, "wx1234567890abcdef")
    assert out["mp-weixin"]["appid"] == "wx1234567890abcdef"
    assert out["h5"] == manifest["h5"]
    assert "mp-weixin" not in manifest


def test_project_config_points_at_built_dist():
    cfg = mpoverlay.project_config("wx1234567890abcdef")
    assert cfg["appid"] == "wx1234567890abcdef"
    assert cfg["compileType"] == "miniprogram"
    assert cfg["miniprogramRoot"] == "dist/build/mp-weixin/"


def test_make_workspace_copies_and_patches_without_touching_source(tmp_path, monkeypatch):
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path / "data"))
    src_root = tmp_path / "app"
    shutil.copytree(TEMPLATE_MOBILE, src_root / "frontend" / "mobile")
    before = (src_root / "frontend" / "mobile" / "src" / "utils" / "request.js").read_bytes()

    ws = mpoverlay.make_workspace(_app(), src_root, "wx1234567890abcdef", SLUG)

    assert (ws / "package.json").is_file() and (ws / "project.config.json").is_file()
    pkg = json.loads((ws / "package.json").read_text(encoding="utf-8"))
    assert pkg["scripts"]["build:mp-weixin"] == "uni build -p mp-weixin"
    manifest = json.loads((ws / "src" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["mp-weixin"]["appid"] == "wx1234567890abcdef"
    patched = (ws / "src" / "utils" / "request.js").read_text(encoding="utf-8")
    assert f"https://{SLUG}.zerocode.com/api" in patched
    # 叠加只碰副本；应用源码字节不变
    assert (src_root / "frontend" / "mobile" / "src" / "utils" / "request.js").read_bytes() == before


def test_make_workspace_fails_when_request_js_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path / "data"))
    src_root = tmp_path / "app"
    (src_root / "frontend" / "mobile" / "src").mkdir(parents=True)
    (src_root / "frontend" / "mobile" / "package.json").write_text(
        json.dumps({"dependencies": {"@dcloudio/uni-app": "3.0.0"}}), encoding="utf-8")
    (src_root / "frontend" / "mobile" / "src" / "manifest.json").write_text("{}", encoding="utf-8")

    with pytest.raises(DeployError) as e:
        mpoverlay.make_workspace(_app(), src_root, "wx1234567890abcdef", SLUG)
    assert "结构已变化" in str(e.value)
    # 叠加失败也不得把源码副本留在工作区（US-15：成功或失败都不长期保留）
    assert not mpoverlay.workspace(SLUG).exists()
