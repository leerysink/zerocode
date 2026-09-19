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
