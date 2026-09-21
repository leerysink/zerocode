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
    sqls = list((tmp_path / "sqlchange").glob("V*.sql"))
    assert len(sqls) == 1 and re.fullmatch(r"V20260918_01_init\.sql", sqls[0].name)
    assert "CREATE TABLE IF NOT EXISTS demo_notes" in sqls[0].read_text(encoding="utf-8")
    # 全目录无残留占位符
    for p in tmp_path.rglob("*"):
        if p.is_file():
            assert "{{" not in p.read_text(encoding="utf-8", errors="replace"), p

def test_render_missing_key_raises(tmp_path):
    import pytest
    from app.deploy.errors import DeployError
    with pytest.raises(DeployError):
        scaffold.render(TEMPLATE, tmp_path, {"SLUG": "x"})  # 缺大部分键

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
    init = (tmp_path / "sqlchange" / "V20260918_01_init.sql").read_text(encoding="utf-8")
    assert "来料登记" in init and "2026-09-18" in init  # APP_NAME 与 TODAY 转横线日期均落到演示迁移


def test_render_init_sql_escapes_single_quote_in_app_name(tmp_path):
    """M-2（终审 F-03）：APP_NAME 含 `'` 原值进 .format → 初始迁移引号不闭合（探针实证
    quote-balanced: False），psql --single-transaction 整文件回滚、首预览必红且 M1 无删除入口。
    修复后须 `'`→`''` 转义：生成行引号闭合、单引号总数为偶数（终审 F-03 探针复核口径）。"""
    mapping = {"SLUG": "lobrien", "APP_NAME": "O'Brien 来料登记", "DEV_PORT": "8211",
               "PROD_PORT": "8210", "DEV_DB": "lobrien_dev", "PROD_DB": "lobrien_prod",
               "TODAY": "20260918"}
    scaffold.render(TEMPLATE, tmp_path, mapping)
    init = (tmp_path / "sqlchange" / "V20260918_01_init.sql").read_text(encoding="utf-8")
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
