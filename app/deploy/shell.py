# app/deploy/shell.py
"""远程 URL WebView 壳的参数与静态文本（ADR-0042 / 规格 2026-11-05）。

纯函数：不碰 Docker、DB、文件系统。包名、版本名、参数哈希、自动图标 SVG、Manifest/资源/Java
启动配置都在这里生成，构建 worker 只负责把结果写进工作区。

壳参数**只包含会影响 APK 本体的东西**：包名、显示名、启动地址、图标、壳代码修订号。应用发布
版本与 H5 内容刻意不进哈希——它们变化不该让 APK 重出。
"""
import hashlib
import json
import re
from xml.sax.saxutils import escape

# 改 MainActivity.java / build.sh / Manifest 结构时 +1：它进参数哈希，老 APK 会在下一次
# 发布或手动重建时被替换。模板内容的自动哈希不做——构建脚本不在平台侧计算范围内，留这个显式闸。
SHELL_REVISION = 1

MOBILE_ICON_MAX_BYTES = 2 * 1024 * 1024
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_JPEG_MAGIC = b"\xff\xd8\xff"
_PKG_SEG_RE = re.compile(r"[^a-z0-9_]")


def _package_segment(raw: str) -> str:
    """Android 包名一段：小写字母/数字/下划线，数字开头补 a；空段丢弃。"""
    s = _PKG_SEG_RE.sub("", (raw or "").lower())
    if s and s[0].isdigit():
        s = "a" + s
    return s


def package_name(domain: str, slug: str) -> str:
    """网关域名反写 + slug：zerocode.com + erp → com.zerocode.erp。

    包名绑域名是远程壳的固有代价（换域名 = 换包名 = 已安装用户只能卸载重装），域名被视为
    长期稳定量，见 ADR-0042「后果」。
    """
    labels = [_package_segment(x) for x in reversed((domain or "").split("."))]
    labels = [x for x in labels if x]
    if not labels:
        labels = ["com", "zerocode"]
    tail = _package_segment(slug)
    return ".".join(labels + ([tail] if tail else []))


def version_name(code: int) -> str:
    return f"1.0.{int(code)}"


def _initial(name: str) -> str:
    for ch in (name or "").strip():
        if not ch.isspace():
            return ch.upper()
    return "A"


def auto_icon_svg(name: str, slug: str) -> str:
    """默认图标：首字符 + slug 派生色。同一 slug/名称永远得到同一张图，不依赖随机数。"""
    ch = escape(_initial(name))
    hue = int(hashlib.sha256((slug or "").encode("utf-8")).hexdigest()[:8], 16) % 360
    hue2 = (hue + 28) % 360
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512" viewBox="0 0 512 512">'
        '<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
        f'<stop offset="0" stop-color="hsl({hue},68%,50%)"/>'
        f'<stop offset="1" stop-color="hsl({hue2},70%,38%)"/>'
        '</linearGradient></defs>'
        '<rect width="512" height="512" rx="112" fill="url(#g)"/>'
        '<text x="256" y="256" text-anchor="middle" dy=".35em" '
        'font-family="Noto Sans CJK SC, Noto Sans, sans-serif" '
        f'font-size="240" font-weight="600" fill="#ffffff">{ch}</text></svg>'
    )


def detect_icon_ext(data: bytes) -> str | None:
    """按魔数认图片；只收 PNG/JPEG，返回落盘后缀（不含点）。"""
    if data.startswith(_PNG_MAGIC):
        return "png"
    if data.startswith(_JPEG_MAGIC):
        return "jpg"
    return None


def icon_digest(custom: bytes | None, name: str, slug: str) -> str:
    if custom is not None:
        return hashlib.sha256(custom).hexdigest()
    return hashlib.sha256(auto_icon_svg(name, slug).encode("utf-8")).hexdigest()


def shell_params(*, name: str, slug: str, domain: str, start_url: str,
                 custom_icon: bytes | None) -> dict:
    return {
        "revision": SHELL_REVISION,
        "package": package_name(domain, slug),
        "label": (name or "").strip() or slug,
        "start_url": start_url or "",
        "icon": icon_digest(custom_icon, name, slug),
    }


def params_hash(params: dict) -> str:
    raw = json.dumps(params, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def is_cleartext(start_url: str) -> bool:
    return (start_url or "").lower().startswith("http://")


def manifest_xml(*, package: str, version_code: int, version_name_: str,
                 cleartext: bool) -> str:
    cleartext_attr = "true" if cleartext else "false"
    return f'''<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    package="{escape(package)}"
    android:versionCode="{int(version_code)}"
    android:versionName="{escape(version_name_)}">
  <uses-permission android:name="android.permission.INTERNET" />
  <application
      android:allowBackup="false"
      android:icon="@mipmap/ic_launcher"
      android:label="@string/app_name"
      android:usesCleartextTraffic="{cleartext_attr}"
      android:theme="@android:style/Theme.Material.Light.NoActionBar">
    <activity
        android:name="com.zerocode.shell.MainActivity"
        android:configChanges="orientation|screenSize|keyboardHidden|screenLayout|smallestScreenSize"
        android:exported="true"
        android:launchMode="singleTask">
      <intent-filter>
        <action android:name="android.intent.action.MAIN" />
        <category android:name="android.intent.category.LAUNCHER" />
      </intent-filter>
    </activity>
  </application>
</manifest>
'''


def _android_string(label: str) -> str:
    """Android 资源字符串：先 XML 转义，再处理 aapt2 认的反斜杠转义。

    单引号不转义会让 aapt2 直接报错；`@`/`?` 开头会被当成资源引用，必须加反斜杠。
    """
    s = escape(label)
    s = s.replace("\\", "\\\\").replace("'", "\\'").replace('"', '\\"')
    if s[:1] in ("@", "?"):
        s = "\\" + s
    return s


def strings_xml(label: str) -> str:
    return ('<?xml version="1.0" encoding="utf-8"?>\n'
            '<resources>\n'
            f'  <string name="app_name">{_android_string(label)}</string>\n'
            '</resources>\n')


def _java_string(text: str) -> str:
    # json.dumps 的转义规则对普通字符串与 Java 字面量兼容；非 ASCII 原样保留，javac 用 UTF-8。
    return json.dumps(text, ensure_ascii=False)


def shell_config_java(start_url: str) -> str:
    return ('package com.zerocode.shell;\n\n'
            'final class ShellConfig {\n'
            f'    static final String START_URL = {_java_string(start_url)};\n\n'
            '    private ShellConfig() {}\n'
            '}\n')
