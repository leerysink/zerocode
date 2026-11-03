"""浏览器 session 纯函数（规格 docs/specs/2026-11-02-agent-browser-interaction.md §6.2）。

刻意不依赖 playwright：ref 令牌/敏感/增量/断言/回收这五类判定必须能在没浏览器的机器上跑。
"""
import pytest

from browser.session_core import (INTERACTIVE_JS, INTERACTIVE_JS_SCAN_MAX, REF_ATTR, REF_RE,
                                  REF_TOKEN_ALPHABET, REF_TOKEN_LENGTH, REF_TOKEN_POOL,
                                  REF_TOKEN_SPACE, can_open, expired, is_sensitive, judge_expect,
                                  new_only, new_ref_tokens, selector_for, shape_interactive)

# 句柄字母表里**没有**的字符，外加一切需要转义进 CSS 属性选择器的字符。
# 这两个集合的差集是令牌能被安全拼进 `[data-zc-ref="…"]` 的全部理由，也是 `selector_for`
# 只判形状就够了的前提。
CSS_UNSAFE = '`~!@#$%^&*()+=[]{}\\|;:\'"<>/?,. \t\r\n'
CONFUSABLES = "0o1liI"


def test_new_ref_tokens_are_unique_and_take_the_pinned_shape():
    tokens = new_ref_tokens(50)
    assert len(tokens) == 50
    # 同一次扫描内不得重复：两个元素拿到同一个句柄的话，点哪一个纯看运气
    assert len(set(tokens)) == 50
    for token in tokens:
        assert REF_RE.fullmatch(token), token
        assert selector_for(token) == f'[{REF_ATTR}="{token}"]'


def test_new_ref_tokens_drop_repeats_from_the_randomness_source():
    """used 集合这条本身要能被钉住：喂一份**会重复**的随机源，重复的那几个必须被跳过、继续往后抽。

    真随机数下这条永远测不出来（31^6 的空间里撞一次属于要专门构造的事件），所以「把去重去掉」
    这次变异不会红任何一条按正常调用写的测试——它就是靠这条红的。
    """
    scripted = iter(["rdupxxa"] * 3 + ["ruqk7bx", "rdupxxa", "rmt4sdw", "rzn2qbx"])
    drawn = []

    def picker():
        token = next(scripted)
        drawn.append(token)
        return token

    tokens = new_ref_tokens(3, picker=picker)
    # 第一个照收，连着两个重复被跳过，再收一个、又一个重复被跳过、再收一个就够数了
    assert tokens == ["rdupxxa", "ruqk7bx", "rmt4sdw"]
    assert len(set(tokens)) == 3
    assert len(drawn) == 6                # 抽了 6 次只留 3 个：去重是在「继续往后抽」而不是少发


def test_new_ref_token_pool_covers_the_js_scan_cap():
    """令牌池与遍历上限必须同值，否则「池用完」与「触顶」是两件事。

    遍历到第 201 个元素时如果池里没令牌了，脚本要么越界拿到 `undefined`、要么少采而不报
    truncated——`shape_interactive` 的封顶判据看的是节点数 >= SCAN_MAX，两处数字漂开就等于
    对模型宣称「清单是完整的」。
    """
    assert REF_TOKEN_POOL == INTERACTIVE_JS_SCAN_MAX
    default_pool = new_ref_tokens()
    assert len(default_pool) == INTERACTIVE_JS_SCAN_MAX == len(set(default_pool))
    # 池比令牌空间大时「互不相同」做不到——在这里响亮报错，而不是让脚本拿着重复令牌静默打标
    with pytest.raises(ValueError):
        new_ref_tokens(REF_TOKEN_SPACE + 1)


def test_new_ref_tokens_never_repeat_within_one_process_run_and_are_roughly_stable_across_two():
    """**跨**扫描不重复是概率性的，不是数学上的（规格 §8 那条尾注）。

    这里能确定的是「两次扫描不可能发出同一批」以及「600 个令牌彼此不同」这一现实量级；
    真正的「旧令牌在新扫描里查不到」的验收在 sidecar 层那条替身 page 的测试里（那里扫描是
    可控的），不在这里靠运气断言。
    """
    a, b = new_ref_tokens(300), new_ref_tokens(300)
    assert set(a) != set(b)
    assert len(set(a) | set(b)) == 600     # 600 个令牌、空间 31^6：撞上属于要专门构造的事件


def test_ref_token_charset_is_css_safe_and_free_of_confusables():
    assert len(REF_TOKEN_ALPHABET) == 31
    assert REF_TOKEN_LENGTH == 6
    assert REF_TOKEN_SPACE == 31 ** 6
    for ch in CONFUSABLES:
        assert ch not in REF_TOKEN_ALPHABET, ch
    for ch in CSS_UNSAFE:
        assert ch not in REF_TOKEN_ALPHABET, ch
    # 生成的令牌同样不许带需要转义的字符：字符集改了、这道闸跟着红，才有意义
    for token in new_ref_tokens(120):
        assert not any(ch in CSS_UNSAFE for ch in token), token


def test_selector_for_accepts_only_generated_tokens():
    good = new_ref_tokens(3)
    for token in good:
        assert selector_for(token) == f'[{REF_ATTR}="{token}"]'
    # 非自生成形状一律拒绝、绝不拼进选择器（这道闸只换了判据，没有放松）。
    # 每一条都刻意取**长度也对**的形状，除了它要试的那一个坏点——否则拒收可能只是因为
    # 长度不对，字符集那道闸（防的就是把选择器提前闭合）等于没被测过。
    old_shapes = ("e1", "e7", "e12", "e1; x")               # 序号形状的旧句柄
    wrong_charset = ("rd2k70x", "r0abcde", "rabcde0", "roooooo", "riiiiii", "rllllll",
                     "rD2K7QX", "rABCDEF")                 # 大小写与字母表外的字符
    # 分号 / 引号 / 方括号 / 百分号 / 换行 / 反斜杠：每个都是 `r` + 6 位，坏在字符而不是长度
    css_injection = ("rd2k7x;", "rd2k7q\"", "rd2k7][", "rd2k%7q", "rd2k7q\n", "rd2k7q\\")
    for bad in ("", None, 12, "rd2k7q", "rd2k7qxx", "d2k7qxx", "r" + "a" * 7,
                "r" + "a" * 100, "RAAAAA A", *old_shapes, *wrong_charset, *css_injection):
        assert selector_for(bad) is None, bad


def _node(ref, role, name, **extra):
    return {"ref": ref, "role": role, "name": name, **extra}


def test_shape_interactive_caps_and_marks():
    pool = new_ref_tokens(45)
    nodes = [_node(ref, "button", f"按钮{i}") for i, ref in enumerate(pool, start=1)]
    items, truncated = shape_interactive(nodes, limit=40)
    assert len(items) == 40
    assert truncated is True
    # 清单里的 ref 就是脚本发回来的那一个令牌：这里重新编号或改写会让旧 ref 指向另一个元素
    assert items[0] == {"ref": pool[0], "role": "button", "name": "按钮1", "disabled": False,
                        "sensitive": False}


def test_shape_interactive_drops_unnamed_and_keeps_disabled():
    pool = new_ref_tokens(2)
    nodes = [_node(pool[0], "button", ""), _node(pool[1], "button", "删除", disabled=True)]
    items, truncated = shape_interactive(nodes, limit=40)
    assert [i["ref"] for i in items] == pool[1:]     # 没名字的点了也不知道点的啥，不进清单
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
            "interactive": [{"ref": "r7kqmbt", "role": "button", "name": "导出"}]}
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
    assert "/orders/12/edit" in note
    _, mismatch = judge_expect({"type": "url_matches", "value": "/login"}, snap, [])
    assert "结尾单个" in mismatch


def test_judge_expect_url_note_echoes_path_query_not_the_connect_url():
    """note 里的回显必须是 `path?query`（裁定 R-17），不是整条 url。

    snapshot 的 `url` 是 sidecar 重写后的内部容器地址（`host.docker.internal` 已换成
    192.168.x 一类可达 IP）。整条回显会把它送进模型上下文与 run log——那是拓扑泄漏；
    而模型下一轮该写的模式本来就是 path?query 形状，回显成对的形状还顺带更有指导性。
    比对仍然用完整 url，只是不外发。
    """
    snap = {"url": "http://192.168.65.254:8210/orders?id=4"}
    passed, note = judge_expect({"type": "url_matches", "value": "/orders?id=9"}, snap, [])
    assert passed is False
    assert "/orders?id=4" in note
    assert "192.168.65.254" not in note
    assert "http://" not in note
    # 无 query 的页面回显纯 path；空 url 也不能让 note 生成器抛异常
    assert judge_expect({"type": "url_matches", "value": "/login"},
                        {"url": "http://192.168.65.254:8210/orders"}, [])[1].count("/orders") == 1
    assert isinstance(judge_expect({"type": "url_matches", "value": "/login"}, {}, [])[1], str)


def test_shape_interactive_marks_the_js_scan_cap():
    """浏览器侧遍历封顶也是截断。

    采到 SCAN_MAX 个节点意味着「后面还有没看过的元素」，即便这一批里只有 3 个带名字
    （`named > budget` 为假）也不能对模型宣称清单完整——否则它会以为整页就这几个可点。
    令牌池与 SCAN_MAX 同值，所以「池用完」这一支由同一个判据报出来，不必第二条信号。
    """
    pool = new_ref_tokens(INTERACTIVE_JS_SCAN_MAX)
    nodes = [_node(ref, "button", "提交" if i <= 3 else "")
             for i, ref in enumerate(pool, start=1)]
    assert len(nodes) == INTERACTIVE_JS_SCAN_MAX
    items, truncated = shape_interactive(nodes, limit=40)
    assert len(items) == 3
    assert truncated is True
    # 差一个节点没采满就是「完整清单」——封顶判据必须正好落在 SCAN_MAX 上
    _, not_capped = shape_interactive(nodes[:-1], limit=40)
    assert not_capped is False


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
    # 而症状出现在另一个文件里。只锚这一处字面量：不断言 JS 源码里的取值表达式，太脆。
    assert f"const attr = '{REF_ATTR}'" in INTERACTIVE_JS


def test_interactive_js_reads_the_ref_token_from_one_local_variable():
    """令牌是**传进来的参数**，不是脚本自己编的，且一次取值两处共用。

    脚本里摇随机数会出现「同一个扫描内两个元素撞同一个句柄」而没人能单测它；序号则复活
    旧句柄静默错点那个缺陷。取值表达式只写一次：`out.push({ref…})` 与 `setAttribute(attr, ref)`
    各算一遍的话，报出去的句柄和页面上标的就不是同一个，点的是隔壁那个元素。
    """
    assert INTERACTIVE_JS.lstrip().startswith("(tokens) =>")
    assert "Math.random" not in INTERACTIVE_JS
    assert "tokens[out.length]" in INTERACTIVE_JS
    assert INTERACTIVE_JS.count("tokens[out.length]") == 1
    assert "el.setAttribute(attr, ref)" in INTERACTIVE_JS


def test_interactive_js_never_indexes_past_the_token_pool():
    """令牌不够用时不许静默越界（越界拿到 `undefined` 会把「undefined」当句柄打出去）。

    口径与遍历上限同一条：并列判、同一处 break，因此触顶仍然落在 `shape_interactive`
    那句 `len(raw_nodes) >= SCAN_MAX` 上，清单照旧报 truncated。
    """
    assert "out.length >= SCAN_MAX || out.length >= pool" in INTERACTIVE_JS
    assert "const pool = Array.isArray(tokens) ? tokens.length : 0;" in INTERACTIVE_JS


def test_interactive_js_carries_the_python_scan_cap():
    """遍历上限必须由 Python 常量注入，不能在 JS 里另抄一遍字面量。

    两处数字漂开的话 `shape_interactive` 的封顶判据静默失效：JS 采 500 条、Python 按 200
    判「没采满」，于是永远报 truncated:false，模型以为看到的是整页。这里锚的是「注入这一步
    确实发生了」，不是 JS 的写法。
    """
    assert f"const SCAN_MAX = {INTERACTIVE_JS_SCAN_MAX};" in INTERACTIVE_JS
    assert "__ZC_SCAN_MAX__" not in INTERACTIVE_JS
