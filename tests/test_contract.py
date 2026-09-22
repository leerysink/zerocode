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

def test_contract_malformed_forms_never_raise(tmp_path):
    """异常形态一律静默：合法 JSON 但顶层非对象 / 空文件 / 非法 JSON → None，不抛。"""
    for bad in ("[1,2]", "", "{oops"):
        (tmp_path / "app.json").write_text(bad, encoding="utf-8")
        assert build_app_contract(tmp_path) is None   # 任一形态都不向聊天链路冒泡异常

def test_contract_lists_databases(tmp_path):
    """契约正文补齐库事实：dev/prod 库名与用户（app.json 本无密码）。"""
    meta = {"schema": "zc-app/1", "name": "来料登记", "slug": "lailiao", "scale": "small",
            "stack": "vue3-fastapi", "ports": {"prod": 8210, "dev": 8211},
            "database": {"dev": {"name": "lailiao_dev", "user": "lailiao_dev"},
                         "prod": {"name": "lailiao_prod", "user": "lailiao_prod"}},
            "health": {"frontend": "/", "backend": "/api/health"}}
    (tmp_path / "app.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    c = build_app_contract(tmp_path)
    assert c and "lailiao_dev" in c and "lailiao_prod" in c
    assert "用户 lailiao_dev" in c and "用户 lailiao_prod" in c

VALID_META = {"schema": "zc-app/1", "name": "来料登记", "slug": "lailiao", "scale": "small",
              "stack": "vue3-fastapi", "ports": {"prod": 8210, "dev": 8211},
              "database": {"dev": {"name": "lailiao_dev", "user": "lailiao_dev"},
                           "prod": {"name": "lailiao_prod", "user": "lailiao_prod"}},
              "health": {"frontend": "/", "backend": "/api/health"}}

def test_contract_gate_requires_zc_app_manifest(tmp_path):
    """M-1 准入闸门收紧：非 zc-app/1 清单（含外来 app.json）或缺 slug → 不注入，杜绝占位文本。"""
    foreign = {"name": "小程序", "pages": ["pages/index"]}   # 小程序/Electron 同名配置，无 schema
    (tmp_path / "app.json").write_text(json.dumps(foreign, ensure_ascii=False), encoding="utf-8")
    assert build_app_contract(tmp_path) is None
    for tampered in ({**VALID_META, "schema": "zc-app/2"},      # 版本漂移不盲渲染
                     {k: v for k, v in VALID_META.items() if k != "schema"},   # 缺清单标识
                     {**VALID_META, "slug": ""}):               # slug 空 → 不再出「」（，…）占位
        (tmp_path / "app.json").write_text(json.dumps(tampered, ensure_ascii=False), encoding="utf-8")
        assert build_app_contract(tmp_path) is None
    (tmp_path / "app.json").write_text(json.dumps(VALID_META, ensure_ascii=False), encoding="utf-8")
    assert build_app_contract(tmp_path)          # 对照：合法清单照常注入，闸门不误伤

def test_contract_sql_and_db_fact_bans(tmp_path):
    """I-2/M-2 文本锚 + P2 双轨口径：第 3 条含单事务建索引禁令；库事实段含库名/用户引用禁令。"""
    (tmp_path / "app.json").write_text(json.dumps(VALID_META, ensure_ascii=False), encoding="utf-8")
    c = build_app_contract(tmp_path)
    assert "单事务内执行" in c and "CREATE INDEX IF NOT EXISTS" in c \
        and "不得使用 CREATE INDEX CONCURRENTLY" in c
    assert "仅供理解，代码与 SQL 不得引用库名/用户名" in c


def test_contract_states_dual_track_model(tmp_path):
    """P2 双轨口径写入契约：结构真相走 schema/schema.sql 目标态、数据变更走 sqlchange/D*。"""
    (tmp_path / "app.json").write_text(json.dumps(VALID_META, ensure_ascii=False), encoding="utf-8")
    c = build_app_contract(tmp_path)
    assert "schema/schema.sql" in c and "sqlchange/D" in c
    assert "数据库变更走双轨" in c and "破坏性" in c
