"""Task 10：系统提示词包含新能力约定。"""
from app.agent.prompts import build_system_prompt

def test_prompt_mentions_new_capabilities():
    p = build_system_prompt("/ws", "2026-09-17")
    assert "todo_write" in p and "in_progress" in p
    assert "<file" in p and "read_file" in p  # @文件内联与分段读取提示
    assert "<context_summary>" in p

def test_prompt_mandates_immediate_todo_updates():
    # 规则6强约束：任务开始/完成必须立即 todo_write，且指向 <current_todos> 回灌锚
    p = build_system_prompt("/ws", "2026-09-17")
    assert "立即" in p and "<current_todos>" in p


def test_contract_carries_sqlchange_append_only_discipline(tmp_path):
    """M3-a Task 13：迁移只追加与破坏性变更两步走的纪律进应用契约（商店升级线的地基）。"""
    import json
    from app.deploy.contract import build_app_contract
    (tmp_path / "app.json").write_text(json.dumps({
        "schema": "zc-app/1", "name": "甲", "slug": "aaa", "stack": "vue3-fastapi",
        "ports": {"dev": 8300, "prod": 8301},
        "database": {"dev": {"name": "aaa_dev", "user": "aaa_dev"},
                     "prod": {"name": "aaa_prod", "user": "aaa_prod"}},
        "health": {"backend": "/api/health"}}), encoding="utf-8")
    text_ = build_app_contract(tmp_path)
    assert "不得修改" in text_ or "只追加" in text_
    assert "两步" in text_   # 破坏性变更两步走（新契约还没有这条纪律时安装闸门拦不住人）
