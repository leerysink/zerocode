"""浏览器 session 纯函数（规格 docs/specs/2026-11-02-agent-browser-interaction.md §6.2）。

刻意不依赖 playwright：ref/敏感/增量/断言/回收这五类判定必须能在没浏览器的机器上跑。
"""
from browser.session_core import (INTERACTIVE_JS, REF_ATTR, can_open, expired, is_sensitive,
                                  judge_expect, new_only, selector_for, shape_interactive)


def test_selector_for_accepts_only_generated_refs():
    assert selector_for("e12") == f'[{REF_ATTR}="e12"]'
    # 模型可控的只有 ref 字符串，任何需要转义的形状都当非法处理，不拼进选择器
    # "e0" 是「编号从 0 起」的漂移形状：ref 必须从 e1 起，否则浏览器发出去的每个 ref 都被自己拒收
    for bad in ("", "e", "e0", "e1; x", 'e"][', "e" + "9" * 7, None, 12):
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


def test_judge_expect_url_matches_full_url_and_query_patterns():
    # 只比 path 时下面两条恒假。恒假不报错——规格 §6.3 轮询到超时，于是每一步白等满 3s
    # 再回 passed:false，模型把它读成「功能没生效」并反复重试，所以三种形态各要有正向覆盖。
    full = {"url": "http://localhost:8210/orders"}
    assert judge_expect({"type": "url_matches",
                         "value": "http://localhost:8210/orders"}, full, [])[0] is True
    queried = {"url": "http://localhost:8210/orders?id=4"}
    assert judge_expect({"type": "url_matches", "value": "/orders?id=4"}, queried, [])[0] is True
    assert judge_expect({"type": "url_matches", "value": "/orders"}, queried, [])[0] is True
    assert judge_expect({"type": "url_matches", "value": "/orders?id=9"}, queried, [])[0] is False


def test_judge_expect_url_note_tells_the_model_the_pattern_is_unsupported():
    snap = {"url": "http://localhost:8210/orders/12/edit"}
    passed, note = judge_expect({"type": "url_matches", "value": "/orders/*/edit"}, snap, [])
    assert passed is False
    # 星号不在末尾仍不匹配（通配只支持尾部，有意取舍），但 note 必须指导改写而不是只报失败
    assert "结尾单个" in note
    assert "/orders/*/edit" in note
    assert "http://localhost:8210/orders/12/edit" in note
    _, mismatch = judge_expect({"type": "url_matches", "value": "/login"}, snap, [])
    assert "结尾单个" in mismatch


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


def test_interactive_js_marks_with_the_attr_selector_for_reads():
    # 打标属性名一旦和 REF_ATTR 漂移，浏览器发出的 ref 会被 selector_for 全数判非法，
    # 而症状出现在另一个文件里。只锚这一处字面量：不断言 JS 源码里的编号表达式，太脆。
    assert f"const attr = '{REF_ATTR}'" in INTERACTIVE_JS
