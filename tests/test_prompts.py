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
