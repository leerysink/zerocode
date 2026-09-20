import json
from pathlib import Path
from app.deploy.contract import build_app_contract
from app.agent.prompts import build_system_prompt

def test_contract_present(tmp_path):
    meta = {"schema": "zc-app/1", "name": "来料登记", "slug": "lailiao", "scale": "small",
            "stack": "vue3-fastapi", "ports": {"prod": 8210, "dev": 8211},
            "database": {"dev": {"name": "lailiao_dev", "user": "lailiao_dev"},
                         "prod": {"name": "lailiao_prod", "user": "lailiao_prod"}},
            "health": {"frontend": "/", "backend": "/api/health"}}
    (tmp_path / "app.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    c = build_app_contract(tmp_path)
    assert c and "lailiao" in c and "8211" in c and "sqlchange" in c and "deploy/" in c
    p = build_system_prompt(str(tmp_path), "2026-09-18", app_contract=c)
    assert "【应用契约】" in p

def test_contract_absent(tmp_path):
    assert build_app_contract(tmp_path) is None
    p = build_system_prompt(str(tmp_path), "2026-09-18")
    assert "【应用契约】" not in p

def test_contract_broken_json(tmp_path):
    (tmp_path / "app.json").write_text("{oops", encoding="utf-8")
    assert build_app_contract(tmp_path) is None   # 损坏不注入，不抛异常
