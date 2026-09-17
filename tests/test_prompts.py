"""Task 10：系统提示词包含新能力约定。"""
from app.agent.prompts import build_system_prompt

def test_prompt_mentions_new_capabilities():
    p = build_system_prompt("/ws", "2026-09-17")
    assert "todo_write" in p and "in_progress" in p
    assert "<file" in p and "read_file" in p  # @文件内联与分段读取提示
    assert "<context_summary>" in p
