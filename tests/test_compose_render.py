# tests/test_compose_render.py
import copy

import pytest
import yaml
from app.deploy import compose
from app.deploy.errors import DeployError

META = {"schema": "zc-app/1", "name": "来料登记", "slug": "lailiao", "scale": "small",
        "stack": "vue3-fastapi", "ports": {"prod": 8210, "dev": 8211},
        "database": {"dev": {"name": "lailiao_dev", "user": "lailiao_dev"},
                     "prod": {"name": "lailiao_prod", "user": "lailiao_prod"}},
        "health": {"frontend": "/", "backend": "/api/health"}}
CRED = {"dev": {"db": "lailiao_dev", "user": "lailiao_dev", "password": "pw123"}}

# 服务块引用的具名卷必须与顶层声明一一对应（compose 的 undefined volume 是 up 期硬失败）
def _named_volume_refs(doc):
    """服务块里引用到的具名卷（排除 ../x:/y 这类绑定挂载）。"""
    refs = set()
    for svc in (doc.get("services") or {}).values():
        for v in svc.get("volumes") or []:
            src = str(v).split(":", 1)[0]
            if not src.startswith((".", "/", "~", "$")):
                refs.add(src)
    return refs

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
    assert doc["volumes"][compose.VOLUME_NAME] == {}  # 顶层具名卷声明，否则 up 报 undefined volume
    assert _named_volume_refs(doc) == set(doc["volumes"])  # 引用↔声明一致（卷名只有一个事实源）

def test_render_dev_compose_static():
    m = copy.deepcopy(META); m["stack"] = "static"
    y = compose.render_dev_compose(m)
    assert "backend:" not in y and "migrate:" not in y and "frontend:" in y
    doc = yaml.safe_load(y)                        # 同 fastapi：整份文档必须是合法 yaml
    assert doc["volumes"][compose.VOLUME_NAME] == {}  # 顶层具名卷声明，否则 up 报 undefined volume
    assert _named_volume_refs(doc) == set(doc["volumes"])  # 引用↔声明一致
    assert list(doc["services"]) == ["frontend"]   # static 栈仅 frontend（规格 03 §4.1）
    assert doc["services"]["frontend"]["ports"] == ["8211:5173"]

def test_only_frontend_publishes_ports():
    """仅前端端口对外（规格 05 FR5-06 / D5，P0）：backend/migrate 不得出现 ports（审查 I-2 / V9-M1）。"""
    doc = yaml.safe_load(compose.render_dev_compose(META))
    assert doc["services"]["frontend"]["ports"] == ["8211:5173"]
    assert "ports" not in doc["services"]["backend"]
    assert "ports" not in doc["services"]["migrate"]
    assert [n for n, s in doc["services"].items() if s.get("ports")] == ["frontend"]

def test_volume_name_is_single_source_of_truth(monkeypatch):
    """改卷名只动一处：挂载条目与顶层声明都由 VOLUME_NAME 派生（审查 I-1 / V9-M2）。"""
    assert compose.FE_VOLUME == f"{compose.VOLUME_NAME}:{compose.FE_MOUNT_PATH}"
    monkeypatch.setattr(compose, "VOLUME_NAME", "fe_cache")
    y = compose.render_dev_compose(META)
    doc = yaml.safe_load(y)
    assert doc["volumes"] == {"fe_cache": {}}      # 顶层声明跟随改名
    assert "fe_cache:/src/node_modules" in y       # 服务挂载同步跟随，二者不脱钩
    assert _named_volume_refs(doc) == set(doc["volumes"])

def test_render_dev_compose_no_marker_residue():
    """渲染产物不得残留 __KEY__ 标记（全局约束 16 / 审查 I-3）：两栈产物已无 "__"。"""
    for stack in ("vue3-fastapi", "static"):
        m = copy.deepcopy(META); m["stack"] = stack
        assert "__" not in compose.render_dev_compose(m)

def test_templates_still_carry_markers():
    """上一条断言有牙齿的前提：原始模板常量确实带标记，替换确实发生过。"""
    for tpl in (compose._DEV_COMPOSE, compose._FE_ONLY):
        assert "__SLUG__" in tpl and "__DEV_PORT__" in tpl
        assert "__FE_VOL__" in tpl and "__FE_VOL_NAME__" in tpl
    assert "__MIGRATE_SVC__" in compose._DEV_COMPOSE
    assert "__MIGRATE_SVC__" not in compose._MIGRATE_SVC

def test_render_rejects_unreplaced_marker(monkeypatch):
    """漏替换的标记必须显式报错而非静默落盘（审查 I-3 / V9-M3 的同型变异）。"""
    monkeypatch.setattr(compose, "_DEV_COMPOSE", compose._DEV_COMPOSE + "    user: __RUN_USER__\n")
    with pytest.raises(DeployError) as ei:
        compose.render_dev_compose(META)
    assert "__RUN_USER__" in str(ei.value)

def test_render_dev_compose_unknown_stack_raises():
    """未知栈显式报错，不再静默按 fastapi 渲染（审查 I-4）：springboot 模板 M2/M3 才交付。"""
    m = copy.deepcopy(META); m["stack"] = "vue3-springboot"
    with pytest.raises(DeployError) as ei:
        compose.render_dev_compose(m)
    assert "vue3-springboot" in str(ei.value)
    assert ei.value.status == 500

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
