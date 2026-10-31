"""需求澄清模式的纯函数单测（规格 docs/specs/2026-11-01-agent-clarify-mode.md §6）。

「本轮发生过什么 → 放行还是挡回」这张矩阵在这里一次跑完，不靠拼对话模拟状态。
穿过 `_dispatch` 的端到端路径见 tests/test_agent_loop.py 的澄清用例。
"""
from pathlib import Path

from app.agent import clarify as cl


# ---------------------------------------------------------------- 配置读取

def test_enabled_defaults_to_on():
    """缺行即开：平台默认启用靠代码兜底，不依赖某一行种子数据存在。"""
    assert cl.enabled({}) is True
    assert cl.enabled(None) is True
    assert cl.enabled({"clarify.enabled": False}) is False
    assert cl.enabled({"clarify.enabled": True}) is True


def test_remind_at_falls_back_on_garbage():
    assert cl.remind_at({}) == cl.DEFAULT_REMIND_AT
    assert cl.remind_at({"clarify.ask_remind_at": "8"}) == 8
    assert cl.remind_at({"clarify.ask_remind_at": "abc"}) == cl.DEFAULT_REMIND_AT


# ---------------------------------------------------------------- 串联判据

def test_nothing_done_blocks():
    assert cl.satisfied({}) is False
    assert cl.gate_block({})


def test_ask_without_declaration_blocks():
    """只问过不收口 = 模型还在拷问中，不该让它一边问一边改文件。"""
    state = {}
    cl.note_ask(state)
    assert cl.satisfied(state) is False
    assert "clarify_done" in cl.gate_block(state)


def test_declaration_without_ask_blocks():
    state = {}
    cl.note_done(state, "clarified")
    assert cl.satisfied(state) is False
    # 「声明过但没问过」与「压根没声明」的引导语不同：前者要它去问，不是重新声明
    assert "没有向用户提过任何问题" in cl.gate_block(state)


def test_ask_plus_declaration_passes():
    state = {}
    cl.note_ask(state)
    cl.note_done(state, "clarified")
    assert cl.satisfied(state) is True
    assert cl.gate_block(state) is None


def test_already_clear_basis_exempts_ask():
    """小任务的豁免通道：不强迫模型编一道无意义的问题（ADR-0032）。"""
    state = {}
    cl.note_done(state, cl.BASIS_ALREADY_CLEAR)
    assert cl.satisfied(state) is True
    assert cl.gate_block(state) is None


def test_decline_waives_everything():
    """用户点「自行决定」是用户侧闸门，优先级高于任何模型侧信号。"""
    state = {}
    cl.note_ask(state, declined=True)
    assert state[cl.WAIVED_KEY] is True
    assert cl.satisfied(state) is True


def test_note_done_rejects_unknown_basis():
    """非法 basis 归到 clarified，不给模型一个能蒙混过关的第三种状态。"""
    state = {}
    assert cl.note_done(state, "whatever") == cl.BASIS_CLARIFIED
    assert cl.satisfied(state) is False
    state2 = {}
    assert cl.note_done(state2, None) == cl.BASIS_CLARIFIED


def test_note_ask_returns_running_count():
    state = {}
    assert cl.note_ask(state) == 1
    assert cl.note_ask(state) == 2


# ---------------------------------------------------------------- 止损提醒

def test_notice_fires_exactly_at_threshold():
    cfg = {"clarify.ask_remind_at": 3}
    state = {}
    for _ in range(2):
        cl.note_ask(state)
    assert cl.ask_notice(state, cfg) is None
    cl.note_ask(state)
    notice = cl.ask_notice(state, cfg)
    assert notice and "3" in notice
    cl.note_ask(state)   # 第 4 次不再重复唠叨
    assert cl.ask_notice(state, cfg) is None


def test_notice_disabled_when_threshold_non_positive():
    state = {}
    for _ in range(10):
        cl.note_ask(state)
    assert cl.ask_notice(state, {"clarify.ask_remind_at": 0}) is None


# ---------------------------------------------------------------- docs 围栏

def test_new_doc_write_fence(tmp_path: Path):
    (tmp_path / "backend").mkdir()
    (tmp_path / "docs" / "adr").mkdir(parents=True)
    existing = tmp_path / "docs" / "adr" / "0001-x.md"
    existing.write_text("# 已存在", encoding="utf-8")

    assert cl.is_new_doc_write(tmp_path, "docs/specs/plan.md") is True
    assert cl.is_new_doc_write(tmp_path, "docs/adr/0002-y.md") is True
    # 覆盖已有文档仍要确认：用户点头的范围是「新建这一份」
    assert cl.is_new_doc_write(tmp_path, "docs/adr/0001-x.md") is False
    # 非 docs 顶层
    assert cl.is_new_doc_write(tmp_path, "backend/app.py") is False
    assert cl.is_new_doc_write(tmp_path, "README.md") is False
    # 拼写绕过：归一化后首段不再是 docs
    assert cl.is_new_doc_write(tmp_path, "docs/../backend/app.py") is False
    assert cl.is_new_doc_write(tmp_path, "docs") is False          # 目录本身
    assert cl.is_new_doc_write(tmp_path, "/etc/passwd") is False    # 绝对路径
    assert cl.is_new_doc_write(tmp_path, None) is False
    assert cl.is_new_doc_write(tmp_path, "") is False


# ---------------------------------------------------------------- 决策索引

def _write(p: Path, text: str):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_collect_decisions_reads_titles_and_summaries(tmp_path: Path):
    _write(tmp_path / "docs" / "adr" / "0001-gate.md",
           "---\ntitle: 首轮澄清门禁\ndescription: 写文件前硬性要求需求对齐\n---\n\n正文\n")
    _write(tmp_path / "docs" / "specs" / "plan.md",
           "# 计划：澄清模式\n第一段讲清了要做什么。\n第二段。\n")
    lines = cl.collect_decisions(tmp_path)
    assert len(lines) == 2
    adr = next(x for x in lines if "0001-gate" in x)
    assert adr.split(" | ")[0] == "docs/adr/0001-gate.md"
    assert adr.split(" | ")[1] == "首轮澄清门禁"
    assert adr.split(" | ")[2] == "写文件前硬性要求需求对齐"
    spec = next(x for x in lines if "plan.md" in x)
    assert spec.split(" | ")[1] == "计划：澄清模式"
    assert spec.split(" | ")[2] == "第一段讲清了要做什么。"


def test_collect_decisions_falls_back_to_file_name(tmp_path: Path):
    """没有标题也没有正文行时不能吐空行，退化用文件名兜底。"""
    _write(tmp_path / "docs" / "adr" / "0001-empty.md", "")
    lines = cl.collect_decisions(tmp_path)
    assert lines and lines[0].split(" | ")[1] == "0001-empty.md"


def test_collect_decisions_caps_at_limit(tmp_path: Path):
    for i in range(30):
        _write(tmp_path / "docs" / "adr" / f"{i:04d}-x.md", f"# 决策 {i}\n")
    assert len(cl.collect_decisions(tmp_path)) == cl.DEFAULT_INDEX_MAX
    assert len(cl.collect_decisions(tmp_path, limit=3)) == 3


def test_collect_decisions_tolerates_missing_dirs(tmp_path: Path):
    assert cl.collect_decisions(tmp_path) == []
    assert cl.collect_decisions(tmp_path / "nope") == []


def test_collect_decisions_skips_unreadable(tmp_path: Path):
    """单份文档读不动只跳过，绝不让一次 IO 抖动把整轮对话打成错误。"""
    _write(tmp_path / "docs" / "adr" / "0001-ok.md", "# 好的\n摘要\n")
    (tmp_path / "docs" / "adr" / "0002-bad.md").mkdir()   # 同名是目录，glob 已过滤，另造一个坏文件
    _write(tmp_path / "docs" / "adr" / "0003-utf16.md", "标题")
    p = tmp_path / "docs" / "adr" / "0003-utf16.md"
    p.write_bytes(b"\xff\xfe\x00\x00broken")
    lines = cl.collect_decisions(tmp_path)
    assert any("0001-ok" in x for x in lines)


def test_render_decisions_skips_empty_block():
    """空清单不注块（与 <memories> 同规矩）：宁可不注入，也不让空标签污染 system。"""
    assert cl.render_decisions([]) == ""
    out = cl.render_decisions(["docs/adr/0001-x.md | 标题 | 摘要"])
    assert out.startswith("\n\n<app_decisions>")
    assert out.endswith("</app_decisions>")
    assert "docs/adr/0001-x.md" in out
