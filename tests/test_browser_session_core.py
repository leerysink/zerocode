"""浏览器 session 纯函数（规格 docs/specs/2026-11-02-agent-browser-interaction.md §6.2）。

刻意不依赖 playwright：ref/敏感/增量/断言/回收这五类判定必须能在没浏览器的机器上跑。
"""
from browser.session_core import (REF_ATTR, can_open, expired, is_sensitive, judge_expect,
                                  new_only, selector_for, shape_interactive)


def test_selector_for_accepts_only_generated_refs():
    assert selector_for("e12") == f'[{REF_ATTR}="e12"]'
    # 模型可控的只有 ref 字符串，任何需要转义的形状都当非法处理，不拼进选择器
    for bad in ("", "e", "e1; x", 'e"][', "e" + "9" * 7, None, 12):
        assert selector_for(bad) is None, bad


def _node(ref, role, name, **extra):
    return {"ref": ref, "role": role, "name": name, **extra}


def test_shape_interactive_caps_and_marks():
    nodes = [_node(f"e{i}", "button", f"按钮{i}") for i in range(1, 46)]
    items, truncated = shape_interactive(nodes, limit=40)
    assert len(items) == 40
    assert truncated is True
    assert items[0] == {"ref": "e1", "role": "button", "name": "按钮1", "disabled": False,
                        "sensitive": False}


def test_shape_interactive_drops_unnamed_and_keeps_disabled():
    nodes = [_node("e1", "button", ""), _node("e2", "button", "删除", disabled=True)]
    items, truncated = shape_interactive(nodes, limit=40)
    assert [i["ref"] for i in items] == ["e2"]      # 没名字的点了也不知道点的啥，不进清单
    assert items[0]["disabled"] is True
    assert truncated is False


def test_is_sensitive_matrix():
    assert is_sensitive(input_type="password", name="", label="")
    assert is_sensitive(input_type="text", name="API Token", label="")
    assert is_sensitive(input_type="text", name="", label="验证码 code")
    assert not is_sensitive(input_type="text", name="备注", label="订单号")
    assert not is_sensitive(input_type="email", name="邮箱", label="")


def test_new_only_returns_only_unseen_and_updates_set():
    seen = {("error", "old")}
    cur = [{"level": "error", "text": "old"}, {"level": "error", "text": "new"}]
    fresh, seen = new_only(seen, cur)
    assert [c["text"] for c in fresh] == ["new"]
    assert ("error", "new") in seen
    again, _ = new_only(seen, cur)
    assert again == []


def test_judge_expect_text_and_element_and_url():
    snap = {"text": "订单已创建，共 4 条", "url": "http://h:8210/orders?id=4",
            "interactive": [{"ref": "e3", "role": "button", "name": "导出"}]}
    assert judge_expect({"type": "text_contains", "value": "订单已创建"}, snap, [])[0] is True
    assert judge_expect({"type": "text_contains", "value": "失败"}, snap, [])[0] is False
    assert judge_expect({"type": "element_visible", "value": "导出"}, snap, [])[0] is True
    assert judge_expect({"type": "url_matches", "value": "/orders*"}, snap, [])[0] is True
    assert judge_expect({"type": "url_matches", "value": "/login"}, snap, [])[0] is False


def test_judge_expect_console_cleanliness():
    err = [{"level": "error", "text": "boom"}]
    assert judge_expect({"type": "no_new_console_error"}, {}, [])[0] is True
    assert judge_expect({"type": "no_new_console_error"}, {}, err)[0] is False


def test_judge_expect_rejects_unknown_kind_and_missing_value():
    assert judge_expect({"type": "nope"}, {}, [])[0] is False
    assert judge_expect({"type": "text_contains"}, {}, [])[0] is False


def test_can_open_respects_limit_and_zero_means_unlimited():
    assert can_open(0, 2) is True
    assert can_open(1, 2) is True
    assert can_open(2, 2) is False
    assert can_open(99, 0) is True          # <=0 不限，与 agent.browser_max_per_run 同口径


def test_expired_boundary():
    assert expired(100.0, 220.0, 120) is False      # 恰好等于 TTL 不算过期
    assert expired(100.0, 220.1, 120) is True
    assert expired(100.0, 100.0, 0) is False        # <=0 不回收
