# tests/test_small_stacks_template.py
"""本期两份新栈模板（vue3-node / static）的体检：整棵目录树能被 scaffold 渲染成能跑的工程。

分工同 tests/test_springboot_template.py：渲染器行为在 test_scaffold.py，compose 文本在
test_compose_render.py；这里只管**模板本体**——缺文件、混进构建产物、占位符形态非法、
依赖基线被悄悄降级、接线指错服务名，都在这里红。两份模板各一节，坏在哪栈就报在哪栈。
"""
import json
import re
from pathlib import Path

import pytest

from app.deploy import scaffold

TEMPLATES = Path(__file__).resolve().parents[1] / "app" / "templates"
NODE = TEMPLATES / "vue3-node"
STATIC = TEMPLATES / "static"

MAPPING = {"SLUG": "smallapp", "APP_NAME": "小型应用", "DEV_PORT": "8211", "PROD_PORT": "8210",
           "DEV_DB": "smallapp_dev", "PROD_DB": "smallapp_prod", "TODAY": "20261113"}

# 平台占位符形态（与 scaffold.RESIDUE 同口径）：只认大写键名，Vue 插值 {{ item.title }} 本就该活到产物里。
# 字节版才能连着二进制一起扫。
RESIDUE = re.compile(rb"\{\{\s*[A-Z][A-Z0-9_]*\s*\}\}")
JUNK_DIRS = {"target", "node_modules", "dist", "logs", ".idea", ".vscode", ".scratch"}
JUNK_FILES = {".env", ".DS_Store", "Thumbs.db"}     # .env：凭据只能由平台注入，模板留一份即泄漏
JUNK_SUFFIXES = {".tsbuildinfo", ".pyc"}
FOREIGN = (r"\bleerysink", r"\bbase-pro\b")


def _junk(tree: Path) -> list[str]:
    bad = []
    for p in sorted(tree.rglob("*")):
        rel = p.relative_to(tree).as_posix()
        if p.is_dir() and p.name in JUNK_DIRS:
            bad.append(rel)
        elif p.is_file() and (p.name in JUNK_FILES or p.suffix in JUNK_SUFFIXES):
            bad.append(rel)
    return bad


def _render(template: Path, tmp_path: Path, **kw) -> Path:
    out = tmp_path / "app"
    scaffold.render(template, out, MAPPING, **kw)
    return out


def _body(tree: Path) -> str:
    """整棵模板树拼成一份文本（只在「任何一处都不许出现」型判据上用；窄判据请指到具体文件）。"""
    return "\n".join(p.read_text(encoding="utf-8", errors="replace")
                     for p in sorted(tree.rglob("*")) if p.is_file())


def _clean(tree: Path, tmp_path: Path, *, data_track: bool) -> None:
    """两栈共用的三条：无杂物、无占位符残留、无外来品牌，外加严格 UTF-8。"""
    assert _junk(tree) == []
    out = _render(tree, tmp_path, data_track=data_track)
    for p in out.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(out)
        assert not RESIDUE.search(p.read_bytes()), rel
        for foreign in FOREIGN:
            assert re.search(foreign, rel.as_posix()) is None, (rel, foreign)
            assert re.search(foreign, p.read_text(encoding="utf-8", errors="replace")) is None, \
                (rel, foreign)
    for p in tree.rglob("*"):                     # 模板本体也要能被 utf-8 解开（scaffold 会按文本读）
        if p.is_file():
            p.read_bytes().decode("utf-8")


# ============================ vue3-node ============================

def test_node_template_shape(tmp_path):
    """关键件在位：TS 工程 + 双轨目录 + 前端五件套，且没有 Python 栈的遗留形状。"""
    for rel in ("backend/package.json", "backend/tsconfig.json", "backend/Dockerfile",
                "backend/src/index.ts", "backend/src/db.ts", "backend/src/routes/notes.ts",
                "frontend/package.json", "frontend/vite.config.js", "frontend/nginx.conf",
                "frontend/Dockerfile", "frontend/index.html", "frontend/src/main.js",
                "frontend/src/App.vue", "schema/schema.sql", "README.md"):
        assert (NODE / rel).is_file(), rel
    for env in ("dev", "prod"):                  # 数据轨环境段（ADR-0040）
        assert (NODE / "sqlchange" / env).is_dir(), env
    assert not (NODE / "backend" / "requirements.txt").exists()
    assert not (NODE / "backend" / "app").exists()      # fastapi 那条目录形状不许串味
    _clean(NODE, tmp_path, data_track=True)


def test_node_template_locks_adr_0047_baseline():
    """版本基线写死在模板里（ADR-0047）：AI 只能在清单上追加，不许降级或换库。

    断言按「包名 → 主版本」而不是整串版本号：升级补丁号是正常维护，把 5.1.0 钉死会让每次
    dependabot 式的例行提交都红，红久了这条判据就被人删掉——那才是真损失。
    """
    pkg = json.loads((NODE / "backend" / "package.json").read_text(encoding="utf-8"))
    assert pkg["dependencies"]["express"].startswith("^5"), pkg["dependencies"]
    assert pkg["dependencies"]["pg-promise"].startswith("^11"), pkg["dependencies"]
    for name, major in (("typescript", "^5"), ("tsx", "^4"), ("@types/node", "^20")):
        assert pkg["devDependencies"][name].startswith(major), (name, pkg["devDependencies"][name])
    fe = json.loads((NODE / "frontend" / "package.json").read_text(encoding="utf-8"))
    for name, major in (("vue", "^3.4"), ("ant-design-vue", "^4"), ("pinia", "^2"), ("axios", "^1")):
        assert fe["dependencies"][name].startswith(major), (name, fe["dependencies"][name])
    assert fe["devDependencies"]["vite"].startswith("^5")
    assert fe["scripts"]["build"] == "vite build"          # ADR-0036：prod 构建不带类型检查闸门
    assert fe["scripts"]["dev"] == "vite"
    # 基座镜像同一条：node:20-alpine 出现在两份 Dockerfile 的每一个 FROM 上
    for dockerfile in ("backend/Dockerfile", "frontend/Dockerfile"):
        froms = [ln for ln in (NODE / dockerfile).read_text(encoding="utf-8").splitlines()
                 if ln.strip().upper().startswith("FROM")]
        assert froms and all("node:20-alpine" in ln or "nginx:1.27-alpine" in ln for ln in froms), \
            (dockerfile, froms)


def test_node_template_declares_mirror_arg():
    """两份 Dockerfile 都要有 NPM_CONFIG_REGISTRY 的 ARG 消费方（ADR-0025）。

    平台在 prod 构建期经 build.args 注源；Dockerfile 不声明 ARG 时 BuildKit 只给一条
    「not consumed」警告——源没换上，构建照样成功，境内网络下表现为 npm install 挂满超时。
    默认值必须是官方源：没配镜像源的部署产物行为要逐字节不变。
    """
    for dockerfile in ("backend/Dockerfile", "frontend/Dockerfile"):
        text = (NODE / dockerfile).read_text(encoding="utf-8")
        assert "ARG NPM_CONFIG_REGISTRY=https://registry.npmjs.org" in text, dockerfile
        assert "ENV NPM_CONFIG_REGISTRY=${NPM_CONFIG_REGISTRY}" in text, dockerfile


def test_node_template_wires_compose_service_names():
    """接线指容器服务名而不是宿主地址：dev 与 prod 两条通路都指 backend:8000。

    写 localhost:8000 的后果不在 dev（vite 与后端不同容器，直接 502 很快被人改掉），在 prod：
    nginx.conf 里的 upstream 要在容器网络里解析，指错就是整台前端容器起不来。
    """
    vite = (NODE / "frontend" / "vite.config.js").read_text(encoding="utf-8")
    assert "http://backend:8000" in vite and "/api" in vite
    nginx = (NODE / "frontend" / "nginx.conf").read_text(encoding="utf-8")
    assert "proxy_pass http://backend:8000" in nginx
    assert "localhost" not in nginx and "127.0.0.1" not in nginx
    # 容器内监听端口是 D-3 的 8000，不是 Node 惯例的 3000
    assert "APP_PORT=8000" in (NODE / "backend" / "Dockerfile").read_text(encoding="utf-8")
    body = _body(NODE / "backend")
    assert "3000" not in body, "Node 栈刻意不用 3000（ADR-0047 D-3），出现即说明有人改回了惯例端口"


def test_node_template_carries_the_two_stack_redlines():
    """两条栈专属红线在样板代码里就得是真的（US-18）：参数化 SQL 与 Express 5 通配写法。

    红线只写在 README 里等于没写——AI 抄的是 `routes/notes.ts` 那个样板。所以断言打在样板上。
    """
    notes = (NODE / "backend" / "src" / "routes" / "notes.ts").read_text(encoding="utf-8")
    assert "$1" in notes
    assert not re.search(r"VALUES\s*\(\s*'", notes), notes        # 字面量拼接形态不许出现在样板里
    index = (NODE / "backend" / "src" / "index.ts").read_text(encoding="utf-8")
    assert "/{*path}" in index
    assert not re.search(r"app\.use\(['\"]\*['\"]", index), "Express 5 里 '*' 会在启动期抛 PathError"


def test_node_render_is_runnable_shape(tmp_path):
    """渲染产物：占位符换成真值、种子照旧补一份、后端工程仍是 TS（与 test_app_service 端到端互补）。"""
    out = _render(NODE, tmp_path, data_track=True)
    pkg = json.loads((out / "backend" / "package.json").read_text(encoding="utf-8"))
    assert pkg["name"] == "smallapp-backend"
    assert list((out / "sqlchange").glob("D20261113_01_seed.sql"))
    assert (out / "schema" / "schema.sql").is_file()


# ============================== static ==============================

def test_static_template_shape(tmp_path):
    """US-02/US-21：只有 frontend 一层加一份 README，三个后端目录压根不存在。

    「不存在」要打在模板本体上而不是只打渲染产物：渲染侧 test_scaffold 已断过没有 sqlchange/，
    但谁要是往模板里塞一个 backend/ 目录，产物就会带着它分发出去而 scaffold 那条断言照样绿。
    """
    for rel in ("frontend/package.json", "frontend/vite.config.js", "frontend/nginx.conf",
                "frontend/Dockerfile", "frontend/index.html", "frontend/src/main.js",
                "frontend/src/App.vue", "README.md"):
        assert (STATIC / rel).is_file(), rel
    for gone in ("backend", "schema", "sqlchange"):
        assert not (STATIC / gone).exists(), gone
    assert not list(STATIC.rglob("*.sql"))
    _clean(STATIC, tmp_path, data_track=False)


def test_static_template_has_no_backend_wiring_anywhere():
    """反代与 proxy 段一处都不许有（US-14 的模板侧）。

    判据收窄到这两个文件，不用整树扫：README 里要**讲**「没有 backend」，提到那个词是内容而不是接线。
    nginx 指到不存在的 upstream 是启动期失败——容器根本不起来，体检只报「前端未就绪」，
    从现象倒查回这里最贵，所以宁可在模板侧就钉死。
    """
    nginx = (STATIC / "frontend" / "nginx.conf").read_text(encoding="utf-8")
    assert "proxy_pass" not in nginx and "backend" not in nginx
    assert "try_files" in nginx                              # SPA 回落是本栈唯一需要的 location
    vite = (STATIC / "frontend" / "vite.config.js").read_text(encoding="utf-8")
    # 收窄到 vite 的配置键（`proxy:`）而不是裸词 proxy：文件里的注释正是要**讲**「没有 proxy 段」，
    # 宽判据会被自己的注释误伤——那注释是给下一个改模板的人看的，不该为了迎合断言删掉。
    assert "proxy:" not in vite and "backend" not in vite
    assert "port: 5173" in vite                              # 与平台 dev 端口映射的容器内端口一致


def test_static_template_locks_frontend_baseline():
    """前端锁定行与小栈同形（§7 版本锁基线），构建脚本同样不带类型检查闸门。"""
    pkg = json.loads((STATIC / "frontend" / "package.json").read_text(encoding="utf-8"))
    for name, major in (("vue", "^3.4"), ("ant-design-vue", "^4"), ("pinia", "^2"), ("axios", "^1")):
        assert pkg["dependencies"][name].startswith(major), (name, pkg["dependencies"][name])
    assert pkg["devDependencies"]["vite"].startswith("^5")
    assert pkg["scripts"]["build"] == "vite build"
    assert pkg["scripts"]["dev"] == "vite"


def test_static_template_dockerfile_is_two_stage_static_site():
    """构建产物只有 dist 进 nginx，且带 nginx.conf 的 COPY 接线（平台存量补齐认这条形状）。

    `COPY nginx.conf` 那行缺位时 `_wire_dockerfile_proxy` 会在发布时补它——模板自己带着，
    就让那条幂等回填对本栈永远是 no-op（少一次「平台改了我的 Dockerfile」的意外）。
    """
    text = (STATIC / "frontend" / "Dockerfile").read_text(encoding="utf-8")
    assert "ARG NPM_CONFIG_REGISTRY=https://registry.npmjs.org" in text
    assert "COPY nginx.conf /etc/nginx/conf.d/default.conf" in text
    assert "COPY --from=build /src/dist /usr/share/nginx/html" in text
    assert "backend" not in text


def test_static_template_documents_its_own_limits():
    """README 必须把「没有后端/数据落 localStorage/不进商店」写在首页级文档里（US-20 的人读半边）。

    契约文本管模型，README 管人。少一句「要服务端存储就换栈」，用户就会以为静态页能长出登录。
    """
    text = (STATIC / "README.md").read_text(encoding="utf-8")
    for phrase in ("localStorage", "没有后端", "上架"):
        assert phrase in text, phrase
    assert "{{DEV_PORT}}" in text          # 本机地址由平台占位符给出，不写死端口号
