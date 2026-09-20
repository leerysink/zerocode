# tests/test_compose_render.py
import pytest
import yaml
from app.deploy import compose

META = {"schema": "zc-app/1", "name": "来料登记", "slug": "lailiao", "scale": "small",
        "stack": "vue3-fastapi", "ports": {"prod": 8210, "dev": 8211},
        "database": {"dev": {"name": "lailiao_dev", "user": "lailiao_dev"},
                     "prod": {"name": "lailiao_prod", "user": "lailiao_prod"}},
        "health": {"frontend": "/", "backend": "/api/health"}}
CRED = {"dev": {"db": "lailiao_dev", "user": "lailiao_dev", "password": "pw123"}}

def test_render_dev_compose_fastapi():
    y = compose.render_dev_compose(META)
    assert "name: zc-lailiao-dev" in y
    for svc in ("migrate:", "backend:", "frontend:"):
        assert svc in y
    assert "zerocode-sqlrunner:1" in y
    assert "TARGET_DB: ${DEV_DB}" in y and "TARGET_USER: ${DEV_USER}" in y
    assert "8211:5173" in y                      # 仅前端端口对外（规格 05 FR5-06）
    assert "zcnet" in y and "external: true" in y
    assert "service_completed_successfully" in y  # 迁移不过应用不起（规格 07 FR7-04）
    assert "${{DEV_DB}}" not in y                 # 渲染器不得吞掉 compose 插值语法
    doc = yaml.safe_load(y)                        # 整份文档必须是合法 yaml（防结构错误）
    assert doc["volumes"]["fe_node_modules"] == {} # 顶层具名卷声明，否则 up 报 undefined volume

def test_render_dev_compose_static():
    import copy
    m = copy.deepcopy(META); m["stack"] = "static"
    y = compose.render_dev_compose(m)
    assert "backend:" not in y and "migrate:" not in y and "frontend:" in y
    doc = yaml.safe_load(y)                        # 同 fastapi：整份文档必须是合法 yaml
    assert doc["volumes"]["fe_node_modules"] == {} # 顶层具名卷声明，否则 up 报 undefined volume

def test_render_env():
    e = compose.render_env(META, CRED)
    assert "DEV_PORT=8211" in e and "PROD_PORT=8210" in e
    assert "DEV_DB=lailiao_dev" in e and "DEV_USER=lailiao_dev" in e and "DEV_PASSWORD=pw123" in e
    assert "PROD_PASSWORD=" in e                  # M2 发布时补齐
    assert "pw123" not in compose.render_env(META, {"dev": CRED["dev"]}).split("DEV_PASSWORD")[0]

def test_ensure_deploy_files_idempotent(tmp_path):
    (tmp_path / "deploy").mkdir()
    compose.ensure_deploy_files(tmp_path, META, CRED)
    y1 = (tmp_path / "deploy" / "dev-compose.yml").read_text(encoding="utf-8")
    compose.ensure_deploy_files(tmp_path, META, CRED)
    y2 = (tmp_path / "deploy" / "dev-compose.yml").read_text(encoding="utf-8")
    assert y1 == y2
    env1 = (tmp_path / "deploy" / ".env").read_text(encoding="utf-8")
    assert "DEV_PASSWORD=pw123" in env1
