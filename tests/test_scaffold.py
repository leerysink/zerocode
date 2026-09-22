# tests/test_scaffold.py
import re
from pathlib import Path
from app.deploy import scaffold

TEMPLATE = Path(__file__).resolve().parents[1] / "app" / "templates" / "vue3-fastapi"

def test_render_replaces_and_generates_init_sql(tmp_path):
    mapping = {"SLUG": "lailiao", "APP_NAME": "来料登记", "DEV_PORT": "8211", "PROD_PORT": "8210",
               "DEV_DB": "lailiao_dev", "PROD_DB": "lailiao_prod", "TODAY": "20260918"}
    scaffold.render(TEMPLATE, tmp_path, mapping)
    pkg = (tmp_path / "frontend" / "package.json").read_text(encoding="utf-8")
    assert '"name": "lailiao-frontend"' in pkg and "{{" not in pkg
    readme = (tmp_path / "README.md").read_text(encoding="utf-8")
    assert "来料登记" in readme and "8211" in readme
    seeds = list((tmp_path / "sqlchange").glob("D*.sql"))
    assert len(seeds) == 1 and re.fullmatch(r"D20260918_01_seed\.sql", seeds[0].name)
    snap = (tmp_path / "schema" / "schema.sql").read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS demo_notes" in snap
    # 不再有结构增量 V 文件（结构真相已迁到声明式 schema.sql）
    assert not list((tmp_path / "sqlchange").glob("V*.sql"))
    # 全目录无残留占位符
    for p in tmp_path.rglob("*"):
        if p.is_file():
            assert "{{" not in p.read_text(encoding="utf-8", errors="replace"), p

def test_render_missing_key_raises(tmp_path):
    import pytest
    from app.deploy.errors import DeployError
    with pytest.raises(DeployError):
        scaffold.render(TEMPLATE, tmp_path, {"SLUG": "x"})  # 缺大部分键


def test_render_creates_schema_snapshot_and_data_seed(tmp_path):
    """P2 双轨：结构进声明式 schema.sql（纯幂等 DDL、无数据），种子进数据轨 D 文件。"""
    mapping = {"SLUG": "lailiao", "APP_NAME": "来料登记", "DEV_PORT": "8211", "PROD_PORT": "8210",
               "DEV_DB": "lailiao_dev", "PROD_DB": "lailiao_prod", "TODAY": "20260922"}
    scaffold.render(TEMPLATE, tmp_path, mapping)
    # 结构轨：声明式快照（纯幂等 DDL，无 INSERT）
    snap = (tmp_path / "schema" / "schema.sql").read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS demo_notes" in snap
    assert "ADD COLUMN IF NOT EXISTS" in snap
    assert "INSERT" not in snap.upper()
    # 数据轨：种子进 D 文件（append-only，幂等 ON CONFLICT）
    seeds = list((tmp_path / "sqlchange").glob("D*.sql"))
    assert len(seeds) == 1 and re.fullmatch(r"D20260922_01_seed\.sql", seeds[0].name)
    assert "INSERT INTO demo_notes" in seeds[0].read_text(encoding="utf-8")


def test_app_readme_states_append_only(tmp_path):
    """M3-a Task 13：应用 README 把「只追加 + 两步走」写给作者本人看。"""
    scaffold.render(TEMPLATE, tmp_path, {
        "SLUG": "a", "APP_NAME": "甲", "DEV_PORT": "8300", "PROD_PORT": "8301",
        "DEV_DB": "a_dev", "PROD_DB": "a_prod", "TODAY": "20260922"})
    text_ = (tmp_path / "README.md").read_text(encoding="utf-8")
    assert "不得修改" in text_ and "两步" in text_

def test_render_missing_template_raises(tmp_path):
    import pytest
    from app.deploy.errors import DeployError
    mapping = {"SLUG": "lailiao", "APP_NAME": "来料登记", "DEV_PORT": "8211", "PROD_PORT": "8210",
               "DEV_DB": "lailiao_dev", "PROD_DB": "lailiao_prod", "TODAY": "20260918"}
    with pytest.raises(DeployError) as ei:
        scaffold.render(tmp_path / "no_such_stack", tmp_path, mapping)  # 模板目录不存在
    assert ei.value.status == 500

def test_render_init_sql_content(tmp_path):
    mapping = {"SLUG": "lailiao", "APP_NAME": "来料登记", "DEV_PORT": "8211", "PROD_PORT": "8210",
               "DEV_DB": "lailiao_dev", "PROD_DB": "lailiao_prod", "TODAY": "20260918"}
    scaffold.render(TEMPLATE, tmp_path, mapping)
    init = (tmp_path / "sqlchange" / "D20260918_01_seed.sql").read_text(encoding="utf-8")
    assert "来料登记" in init and "2026-09-18" in init  # APP_NAME 与 TODAY 转横线日期均落到数据轨种子


def test_render_init_sql_escapes_single_quote_in_app_name(tmp_path):
    """M-2（终审 F-03）：APP_NAME 含 `'` 原值进 .format → 初始迁移引号不闭合（探针实证
    quote-balanced: False），psql --single-transaction 整文件回滚、首预览必红且 M1 无删除入口。
    修复后须 `'`→`''` 转义：生成行引号闭合、单引号总数为偶数（终审 F-03 探针复核口径）。"""
    mapping = {"SLUG": "lobrien", "APP_NAME": "O'Brien 来料登记", "DEV_PORT": "8211",
               "PROD_PORT": "8210", "DEV_DB": "lobrien_dev", "PROD_DB": "lobrien_prod",
               "TODAY": "20260918"}
    scaffold.render(TEMPLATE, tmp_path, mapping)
    init = (tmp_path / "sqlchange" / "D20260918_01_seed.sql").read_text(encoding="utf-8")
    assert "O''Brien 来料登记" in init                      # PG 标准转义：单引号双写
    line = next(l for l in init.splitlines() if "INSERT INTO demo_notes" in l)
    assert "VALUES ('欢迎使用 O''Brien 来料登记')" in line   # 开/闭引号 + 转义对，字面量闭合
    assert line.count("'") % 2 == 0 and init.count("'") % 2 == 0   # 引号配对偶数 = 闭合


def test_rendered_backend_has_startup_banner(tmp_path):
    """生成的应用自带启动日志：渲染后的 backend/app/main.py 是一段可编译的 stdout banner。

    口径取自「应用启动时增加启动日志」：应用名/slug、监听端口、健康检查、数据库与连通性、
    就绪耗时都要打出来；占位符替换后必须仍是合法 Python（否则首预览直接起不来）。
    """
    mapping = {"SLUG": "lailiao", "APP_NAME": "来料登记", "DEV_PORT": "8211", "PROD_PORT": "8210",
               "DEV_DB": "lailiao_dev", "PROD_DB": "lailiao_prod", "TODAY": "20260918"}
    scaffold.render(TEMPLATE, tmp_path, mapping)
    main = (tmp_path / "backend" / "app" / "main.py").read_text(encoding="utf-8")
    compile(main, "backend/app/main.py", "exec")
    assert 'APP_NAME = "来料登记"' in main and 'APP_SLUG = "lailiao"' in main
    assert "stream=sys.stdout" in main and "[lailiao]" in main     # 进 stdout，前缀带 slug 可分辨
    for field in ("后端启动", "监听地址", "健康检查", "数据库", "连通性", "就绪耗时"):
        assert field in main, field
    assert "lifespan=lifespan" in main                              # banner 挂在应用生命周期上


def test_template_nginx_prod_proxy(tmp_path):
    from app.deploy import scaffold
    mapping = {"SLUG": "lailiao", "APP_NAME": "来料登记", "DEV_PORT": "8211", "PROD_PORT": "8210",
               "DEV_DB": "lailiao_dev", "PROD_DB": "lailiao_prod", "TODAY": "20260920"}
    scaffold.render(TEMPLATE, tmp_path, mapping)
    ng = (tmp_path / "frontend" / "nginx.conf").read_text(encoding="utf-8")
    assert "proxy_pass http://backend:8000" in ng and "try_files" in ng
    dk = (tmp_path / "frontend" / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY nginx.conf /etc/nginx/conf.d/default.conf" in dk


def test_render_copies_binary_file_byte_identical(tmp_path):
    """favicon.ico 这类非 UTF-8 资源必须按字节 copy（read_text 会抛 UnicodeDecodeError → 创建失败回滚）。"""
    tpl = tmp_path / "tpl"
    (tpl / "frontend" / "public").mkdir(parents=True)
    (tpl / "frontend" / "package.json").write_bytes(b'{"name": "{{SLUG}}-fe"}\n')   # 含换行：钉住 newline=""
    blob = bytes([0, 0, 1, 0, 0, 0, 0, 0, 255, 254, 0, 1, 0, 0, 0, 0])   # 必然解不出 UTF-8
    (tpl / "frontend" / "public" / "favicon.ico").write_bytes(blob)
    scaffold.render(tpl, tmp_path / "out", {
        "SLUG": "s", "APP_NAME": "甲", "DEV_PORT": "1", "PROD_PORT": "2",
        "DEV_DB": "s_dev", "PROD_DB": "s_prod", "TODAY": "20260101"})
    out = tmp_path / "out" / "frontend"
    assert (out / "public" / "favicon.ico").read_bytes() == blob                   # 逐字节相等
    assert (out / "package.json").read_bytes() == b'{"name": "s-fe"}\n'   # 替换生效且不增换行


def test_render_skips_demo_seed_when_template_ships_its_own(tmp_path):
    """springboot 模板自带 RBAC 种子，平台不再追加 demo_notes 种子（该表只存在于 fastapi 栈 → 首迁移必红）。"""
    tpl = tmp_path / "tpl"
    (tpl / "sqlchange").mkdir(parents=True)
    (tpl / "sqlchange" / "D20260101_01_rbac_seed.sql").write_text("SELECT 1;", encoding="utf-8")
    out = tmp_path / "out"
    scaffold.render(tpl, out, {
        "SLUG": "s", "APP_NAME": "甲", "DEV_PORT": "1", "PROD_PORT": "2",
        "DEV_DB": "s_dev", "PROD_DB": "s_prod", "TODAY": "20260922"})
    assert [p.name for p in (out / "sqlchange").glob("*.sql")] == ["D20260101_01_rbac_seed.sql"]


def test_render_appends_demo_seed_when_template_has_no_sql(tmp_path):
    """对照（回归钉）：模板数据轨为空时照旧追加，vue3-fastapi 行为一字不变。"""
    tpl = tmp_path / "tpl"
    (tpl / "sqlchange").mkdir(parents=True)
    (tpl / "sqlchange" / "README.md").write_text("只追加", encoding="utf-8")   # 与真模板同形：目录非空但无 .sql
    out = tmp_path / "out"
    scaffold.render(tpl, out, {
        "SLUG": "s", "APP_NAME": "甲", "DEV_PORT": "1", "PROD_PORT": "2",
        "DEV_DB": "s_dev", "PROD_DB": "s_prod", "TODAY": "20260922"})
    assert (out / "sqlchange" / "D20260922_01_seed.sql").is_file()


def test_render_keeps_vue_interpolation_alive(tmp_path):
    """Vue 插值不是平台占位符：{{ appTitle }} 这类必须能原样活过残留自检。

    宽判据 "{{" in text 会把 vue3-springboot 模板里 93 处插值全判成渲染残留 → 创建应用直接 500。
    """
    tpl = tmp_path / "tpl"
    (tpl / "frontend" / "src" / "views").mkdir(parents=True)
    (tpl / "frontend" / "src" / "views" / "index.vue").write_text(
        "<h1>{{ appTitle }}</h1>\n<span>{{ record.status === 1 ? '启用' : '停用' }}</span>\n"
        "<b>{{SLUG}}</b>\n", encoding="utf-8")
    out = tmp_path / "out"
    scaffold.render(tpl, out, {
        "SLUG": "s", "APP_NAME": "甲", "DEV_PORT": "1", "PROD_PORT": "2",
        "DEV_DB": "s_dev", "PROD_DB": "s_prod", "TODAY": "20260922"})
    vue = (out / "frontend" / "src" / "views" / "index.vue").read_text(encoding="utf-8")
    assert "{{ appTitle }}" in vue and "{{ record.status === 1 ? '启用' : '停用' }}" in vue
    assert "<b>s</b>" in vue          # 平台占位符照旧替换


def test_render_flags_spaced_platform_placeholder_as_residue(tmp_path):
    """真事故仍要拦：平台占位符全是大写下划线，带空格写歪就替换不掉（PLACEHOLDER 不吃空格）。"""
    import pytest
    from app.deploy.errors import DeployError
    tpl = tmp_path / "tpl"
    tpl.mkdir(parents=True)
    (tpl / "README.md").write_text("应用名：{{ APP_NAME }}\n", encoding="utf-8")
    with pytest.raises(DeployError, match="APP_NAME"):
        scaffold.render(tpl, tmp_path / "out", {
            "SLUG": "s", "APP_NAME": "甲", "DEV_PORT": "1", "PROD_PORT": "2",
            "DEV_DB": "s_dev", "PROD_DB": "s_prod", "TODAY": "20260922"})


SPRINGBOOT_TEMPLATE = TEMPLATE.parent / "vue3-springboot"


def test_render_springboot_template_end_to_end(tmp_path):
    """整栈真模板必须能渲染干净（残留自检是这道闸的第一道防线，不留到 A7 才发现）。"""
    import pytest
    if not SPRINGBOOT_TEMPLATE.is_dir():
        pytest.skip("vue3-springboot 模板尚未落仓")
    scaffold.render(SPRINGBOOT_TEMPLATE, tmp_path, {
        "SLUG": "bigapp", "APP_NAME": "大型应用", "DEV_PORT": "8211", "PROD_PORT": "8210",
        "DEV_DB": "bigapp_dev", "PROD_DB": "bigapp_prod", "TODAY": "20260922"})
    assert (tmp_path / "frontend" / "vite.config.ts").is_file()
    assert (tmp_path / "backend" / "src" / "main" / "java" / "com" / "zerocode" / "app"
            / "BaseApplication.java").is_file()


def test_springboot_banner_keeps_percent_in_app_name_harmless(tmp_path):
    """应用名里的 % 不许打挂启动横幅：名字必须是 .formatted 的实参，不能是格式字符串本身。

    check_name 刻意不拒 %（「50% 折扣」是正常业务名，%在 pom/XML/Java 字面量里都合法），而
    String.formatted 会把格式串里的裸 % 当转换符 —— 名字进了 text block 就是启动即抛
    UnknownFormatConversionException，创建成功、预览永远起不来。
    """
    import pytest
    if not SPRINGBOOT_TEMPLATE.is_dir():
        pytest.skip("vue3-springboot 模板尚未落仓")
    name = "50% 折扣 进度%s完成"
    scaffold.render(SPRINGBOOT_TEMPLATE, tmp_path, {
        "SLUG": "bigapp", "APP_NAME": name, "DEV_PORT": "8211", "PROD_PORT": "8210",
        "DEV_DB": "bigapp_dev", "PROD_DB": "bigapp_prod", "TODAY": "20260922"})
    java = (tmp_path / "backend" / "src" / "main" / "java" / "com" / "zerocode" / "app"
            / "BaseApplication.java").read_text(encoding="utf-8")
    body = java.split('"""')[1]                       # 横幅 text block 本体
    assert "%s" in body and "%" not in body.replace("%s", "")   # 只允许 %s 这类合法转换符
    assert body.count("%s") == 2                       # 与下面的两个实参同步（名字、baseUrl）：加行不加实参就是 MissingFormatArgumentException
    assert '"%s"' % name in java.split(".formatted(")[1]        # 名字以带引号实参出现，不在格式串里


def test_fastapi_app_vue_name_sits_in_a_double_quoted_host(tmp_path):
    """名字进 Vue 宿主只能待在被 NAME_UNSAFE 保护的双引号里（评审 A4c IMP-1 同类洞）。

    `:title="'{{APP_NAME}}'"` 是模板表达式里的 **JS 单引号字符串**，而 check_name 刻意放行 `'`
    （SQL 路径已转义，pom/Java/HTML 里也安全）——「O'Brien 仓库」渲染出来就是 `title: 'O'Brien 仓库'`，
    编译产物语法错、预览必碎。改静态属性后属性值里的 `'` 无语法意义（`"` 已被入口拒掉）。
    """
    scaffold.render(TEMPLATE, tmp_path, {
        "SLUG": "lailiao", "APP_NAME": "O'Brien 仓库", "DEV_PORT": "8211", "PROD_PORT": "8210",
        "DEV_DB": "lailiao_dev", "PROD_DB": "lailiao_prod", "TODAY": "20260922"})
    vue = (tmp_path / "frontend" / "src" / "App.vue").read_text(encoding="utf-8")
    assert 'title="O\'Brien 仓库"' in vue and ":title=\"'" not in vue
