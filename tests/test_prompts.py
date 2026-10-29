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


def test_available_skills_block_rendered():
    # Ticket 004：已启用技能以 name+description+目录绝对路径注入 <available_skills> 块，
    # 附渐进披露规则（相关时才 read_file 读 SKILL.md 全文）
    p = build_system_prompt("/ws", "2026-09-17", skills=[
        {"name": "PDF 导出", "description": "生成 PDF", "path": "/data/users/1/skills/pdf"}])
    assert "<available_skills>" in p and "</available_skills>" in p
    assert "PDF 导出" in p and "生成 PDF" in p and "/data/users/1/skills/pdf" in p
    assert "read_file" in p and "SKILL.md" in p


def test_no_available_skills_block_when_empty():
    # 反例守卫：无启用技能不渲染空块（省 token，与 <current_todos> 同惯例）
    assert "<available_skills>" not in build_system_prompt("/ws", "2026-09-17", skills=[])
    assert "<available_skills>" not in build_system_prompt("/ws", "2026-09-17")


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


def _app_json_for_contract(tmp_path):
    import json
    (tmp_path / "app.json").write_text(json.dumps({
        "schema": "zc-app/1", "name": "甲", "slug": "aaa", "stack": "vue3-fastapi",
        "ports": {"dev": 8300, "prod": 8301},
        "database": {"dev": {"name": "aaa_dev", "user": "aaa_dev"},
                     "prod": {"name": "aaa_prod", "user": "aaa_prod"}},
        "health": {"backend": "/api/health"}}), encoding="utf-8")


def test_contract_carries_add_column_not_null_redline(tmp_path):
    """生产事故回归：结构轨给已有数据的表补裸 NOT NULL 列不可重放，会把 dev 栈永久锁死在
    fold 那一行（fold 不进 history，每次重启红在同一处）。契约必须自带这条红线与出路。"""
    from app.deploy.contract import build_app_contract
    _app_json_for_contract(tmp_path)
    text_ = build_app_contract(tmp_path)
    assert "补列红线" in text_
    assert "contains null values" in text_     # 报错原文，让模型认得自己将撞什么
    assert "gen_random_uuid()" in text_        # 唯一列的默认值出路（常量默认值会撞唯一索引）
    assert "SET NOT NULL" in text_             # fold 先于数据轨的时序红线


def test_schema_template_readmes_carry_add_column_not_null_redline():
    """两栈的 schema/README.md 是应用工作区里的落地副本，红线必须同步在场（两份不得漂移）。"""
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "app" / "templates"
    for stack in ("vue3-fastapi", "vue3-springboot"):
        md = (root / stack / "schema" / "README.md").read_text(encoding="utf-8")
        assert "补列红线" in md, stack
        assert "contains null values" in md, stack
        assert "SET NOT NULL" in md, stack


def test_contract_carries_unique_index_redline(tmp_path):
    """生产事故回归：给已有数据的表加唯一索引，fold 先于数据轨执行，存量重复（常见为空串）
    会让每次启动都红在建索引那一行。契约必须自带这条红线与两步走出路。"""
    from app.deploy.contract import build_app_contract
    _app_json_for_contract(tmp_path)
    text_ = build_app_contract(tmp_path)
    assert "唯一索引红线" in text_
    assert "could not create unique index" in text_   # 报错原文，让模型认得自己将撞什么
    assert "is duplicated" in text_
    assert "空串" in text_ and "NULL" in text_        # () 是空串不是 NULL：多个 NULL 合法、多个空串不合法
    assert "两步" in text_                             # 存量有重复时先清理、下一版再加索引


def test_schema_template_readmes_carry_unique_index_redline():
    """两栈的 schema/README.md 与契约同步携带唯一索引红线，不得漂移。"""
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "app" / "templates"
    for stack in ("vue3-fastapi", "vue3-springboot"):
        md = (root / stack / "schema" / "README.md").read_text(encoding="utf-8")
        assert "唯一索引红线" in md, stack
        assert "could not create unique index" in md, stack
        assert "is duplicated" in md, stack
        assert "空串" in md and "NULL" in md, stack
        assert "两步" in md, stack


# ---- Ticket 009：基线 prompt 记忆时机规则 ----

def test_prompt_carries_memory_timing_rule():
    """规则 9 存在性断言：票面要求的“三问”关键语句均在 prompt 内。"""
    p = build_system_prompt("/ws", "2026-10-01")
    # 什么值得记
    assert "跨会话仍成立" in p and "主动沉淀" in p
    # 什么不该记
    assert "凭据密钥" in p and "一次性调试输出" in p
    # 记几次
    assert "同义条目用 update 不重复 create" in p
    # 锚点指向
    assert "<memories>" in p


def test_memory_rule_does_not_break_injection_order():
    """规则 9 提到 <memories> 标签，但只是文本说明；实际注入块仍在基线 prompt 之后追加，
    不破坏 <context_summary> < <current_todos> < <memories> 的顺序红线（rindex 取末尾块）。"""
    p = build_system_prompt("/ws", "2026-10-01")
    # 基线 prompt 里提到的标签名（规则文本）
    assert "<context_summary>" in p
    assert "<current_todos>" in p
    assert "<memories>" in p
    # 它们都是说明文字，不是真正的注入块（真正的注入块由 loop 追加，位置在后）
    # 模拟 loop 追加：
    p += "\n\n<context_summary>\n摘要\n</context_summary>"
    p += "\n\n<current_todos>\n[]\n</current_todos>"
    p += "\n\n<memories>\n- [fact] 示例\n</memories>"
    assert (p.rindex("<context_summary>") < p.rindex("<current_todos>")
            < p.rindex("<memories>"))


def test_prompt_builds_without_memory_config():
    """无技能、无契约、纯基线的常见场景下 prompt 仍能正常拼接（规则 9 不引入外部依赖）。"""
    p = build_system_prompt("/ws", "2026-10-01")
    assert isinstance(p, str) and len(p) > 50
    # 规则 9 在基线文本里，不因缺配置而丢失
    assert "memory_write" in p


# ---- 预览工具指引 <preview>（规格 2026-10-29-agent-preview-tools §9）----

def test_preview_block_appended_after_app_contract():
    p = build_system_prompt("/ws", "2026-10-29", app_contract="\n\n【应用契约】slug=aaa\n",
                            preview="\n\n<preview>\n改完要验证\n</preview>")
    assert "<preview>" in p and "改完要验证" in p
    assert p.index("【应用契约】") < p.index("<preview>")     # 契约在前，预览指引在后


def test_no_preview_block_by_default():
    assert "<preview>" not in build_system_prompt("/ws", "2026-10-29")
    assert "<preview>" not in build_system_prompt("/ws", "2026-10-29", app_contract="【应用契约】")

