# tests/test_springboot_template.py
"""大型应用栈模板的渲染体检：整棵目录树必须能被 scaffold 渲染成可运行的应用工程。

与 tests/test_scaffold.py 的分工：那边管「渲染器 + 单栈接线」的行为细节（含 A6 的双轨内容断言），
这边管整棵树的形状与端到产物契约——模板缺文件、混进构建产物、占位符没被替换，都在这里红。
"""
import hashlib
import re
from pathlib import Path
import pytest
from app.deploy import scaffold

TEMPLATE = Path(__file__).resolve().parents[1] / "app" / "templates" / "vue3-springboot"
MAPPING = {"SLUG": "bigapp", "APP_NAME": "大型应用", "DEV_PORT": "8211", "PROD_PORT": "8210",
           "DEV_DB": "bigapp_dev", "PROD_DB": "bigapp_prod", "TODAY": "20260922"}
PKG = Path("com") / "zerocode" / "app"

# 参考工程基线的 favicon（frontend/public/favicon.ico）指纹。
# 刻意 pin 哈希而不是去读 D:/.../base-pro：平台测试不能依赖仓库之外的目录，
# 否则换台机器就静默 skip，而「二进制有没有被当文本重新编码」这件事恰恰只在 CI 上才容易暴露。
FAVICON = Path("frontend") / "public" / "favicon.ico"
FAVICON_SHA256 = "e7773a393ad260e7d434e37dc57501207880984e8e313a1afa8e73c77c6f3821"

# 平台占位符形态：大写字母开头的键名（与 scaffold.RESIDUE 同口径）。
# 不能简单断言 `b"{{" not in ...`：Vue 插值 `{{ appTitle }}` 本来就该活到产物里（见 Task A3b），
# 那样写会把产品形态判成残留。字节版（不是 str 版）才能连着二进制文件一起扫。
RESIDUE = re.compile(rb"\{\{\s*[A-Z][A-Z0-9_]*\s*\}\}")


def _render(tmp_path):
    out = tmp_path / "app"
    scaffold.render(TEMPLATE, out, MAPPING)
    return out


def test_template_shape():
    """模板里不许有构建产物与临时物：它们会随每个生成的应用一起分发。"""
    assert (TEMPLATE / "backend" / "pom.xml").is_file()
    assert (TEMPLATE / "frontend" / "src" / "main.ts").is_file()
    assert (TEMPLATE / "schema" / "schema.sql").is_file()
    assert (TEMPLATE / "sqlchange").is_dir()
    for junk in ("target", "logs", ".scratch"):
        assert not (TEMPLATE / "backend" / junk).exists(), junk
    for junk in ("dist", "node_modules"):
        assert not (TEMPLATE / "frontend" / junk).exists(), junk
    assert not (TEMPLATE / "frontend" / "vite.config.js").exists()      # vue-tsc 产物不带（A5b）
    # 环境差异走平台注入的环境变量，不留第二份 profile 文件
    assert not (TEMPLATE / "backend" / "src" / "main" / "resources" / "application-dev.yml").exists()


def test_render_has_no_residue_and_no_foreign_brand(tmp_path):
    out = _render(tmp_path)
    for p in out.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(out)
        assert not RESIDUE.search(p.read_bytes()), rel                  # 字节扫，二进制文件也不放过
        text = p.read_text(encoding="utf-8", errors="replace")
        for foreign in ("leerysink", "base-pro"):                       # 作者用户名 / 参考工程名
            assert not re.search(rf"\b{foreign}", text), (rel, foreign)


def test_render_replaces_coordicates_and_title(tmp_path):
    out = _render(tmp_path)
    pom = (out / "backend" / "pom.xml").read_text(encoding="utf-8")
    assert "<artifactId>bigapp</artifactId>" in pom and "<name>大型应用</name>" in pom
    assert "spring-boot-starter-actuator" in pom and "3.2.5" in pom     # 探针依赖 + 版本锁定
    assert "com.zerocode.app" in (out / "backend" / "src" / "main" / "java" / PKG /
                                  "BaseApplication.java").read_text(encoding="utf-8")
    assert "大型应用" in (out / "frontend" / "index.html").read_text(encoding="utf-8")
    readme = (out / "README.md").read_text(encoding="utf-8")
    assert "http://localhost:8211" in readme and "bigapp_dev" in readme
    assert "大型应用" in readme and "bigapp" in readme                  # 三处占位都要落地


def test_render_keeps_favicon_byte_identical(tmp_path):
    """二进制资源走字节 copy（Task A1）：既不能与基线指纹漂移，也不能在渲染时被重新编码。"""
    raw = (TEMPLATE / FAVICON).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == FAVICON_SHA256, "模板图标与参考基线已非同一文件"
    out = _render(tmp_path)
    assert (out / FAVICON).read_bytes() == raw


def test_render_ships_rbac_seed_and_no_demo_notes_seed(tmp_path):
    """Task A2 的实战面：本栈数据轨只有 RBAC 基线，平台不得追加 demo_notes 种子。

    双轨的内容细节由 test_scaffold.py 那边钉，这里只确认渲染管线的组合结果没走偏。
    """
    out = _render(tmp_path)
    assert sorted(p.name for p in (out / "sqlchange").glob("*.sql")) == ["D20260101_01_rbac_seed.sql"]
    seed = (out / "sqlchange" / "D20260101_01_rbac_seed.sql").read_text(encoding="utf-8")
    assert "INSERT INTO sys_menu" in seed and "setval(" in seed and "demo_notes" not in seed
    snap = (out / "schema" / "schema.sql").read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS sys_user" in snap
    assert "schema_history" not in snap and "INSERT" not in snap.upper()


def test_template_wiring_targets_compose_service_names(tmp_path):
    """接线必须指向 compose 服务名而不是 localhost：容器里两者含义完全不同。"""
    out = _render(tmp_path)
    vite = (out / "frontend" / "vite.config.ts").read_text(encoding="utf-8")
    assert "http://backend:8080" in vite and "port: 5173" in vite and "8082" not in vite
    ng = (out / "frontend" / "nginx.conf").read_text(encoding="utf-8")
    assert "proxy_pass http://backend:8080" in ng and "try_files" in ng
    assert "client_max_body_size 50m" in ng                             # 与 multipart 上限对齐（A4）
    assert re.search(r"^COPY nginx\.conf /etc/nginx/conf\.d/default\.conf$",
                     (out / "frontend" / "Dockerfile").read_text(encoding="utf-8"), re.M)
    dk = (out / "backend" / "Dockerfile").read_text(encoding="utf-8")
    assert "maven:3.9-eclipse-temurin-17" in dk and "--mount=type=cache,target=/root/.m2" in dk
    y = (out / "backend" / "src" / "main" / "resources" / "application.yml").read_text(encoding="utf-8")
    for env in ("SPRING_DATASOURCE_URL", "SPRING_DATASOURCE_USERNAME", "SPRING_DATASOURCE_PASSWORD",
                "SPRING_DATA_REDIS_HOST", "SPRING_DATA_REDIS_PASSWORD", "JWT_SECRET", "UPLOAD_DIR"):
        assert "${%s}" % env in y, env                                  # 一个都不许写死
    assert "base-path: /" in y and "include: health" in y               # 探针收敛成 /api/health
    sec = (out / "backend" / "src" / "main" / "java" / PKG / "config" /
           "SecurityConfig.java").read_text(encoding="utf-8")
    assert '"/health"' in sec                                            # 不放行则发布体检必挂


RESOURCE_SUFFIXES = {".ico", ".png", ".jpg", ".jpeg", ".gif", ".woff", ".woff2",
                     ".ttf", ".eot", ".jar"}


def test_template_files_are_strict_utf8_or_known_resource():
    """收口 A1 的盲区：渲染器对解不出 UTF-8 的文件只搬字节（A1），所以错编码的文本
    （GBK/UTF-16）会被静默原样复制、占位符永远不替换——模板侧只允许「严格 UTF-8 文本」
    与「已知资源扩展名」两类，第三条路直接红。（A1 的 `_render_file` docstring 指名依赖本测试）
    """
    offenders = []
    for p in sorted(TEMPLATE.rglob("*")):
        if not p.is_file():
            continue
        rel = str(p.relative_to(TEMPLATE))
        raw = p.read_bytes()
        try:
            raw.decode("utf-8")
        except UnicodeDecodeError:
            if p.suffix.lower() not in RESOURCE_SUFFIXES:
                offenders.append(f"{rel}：非 UTF-8 且不是已知资源件")
            elif b"{{" in raw:
                offenders.append(f"{rel}：资源件里含 {{{{ 字节，渲染器不会替换它")
    assert offenders == []
