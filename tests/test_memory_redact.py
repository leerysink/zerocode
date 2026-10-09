"""记忆写入脱敏与结构中和（ticket 004 / spec 故事 11、12）。

靶心是两个纯函数：`memory_service.redact_sensitive`（凭据 → `<redacted:kind>`）与
`memory_service.neutralize_memory_tags`（`</memories>` → 全角，防止正文提前闭合注入块），
外加它们组成的写入关卡 `sanitize_memory_text`（两类命中分开计数的契约）。
服务层与工具层的接线在 test_memory_service.py / test_memory_tool.py 里断。

红线：断言**具体脱敏后的字符串**，不接受只断言「没抛异常」；同时反向断言明文彻底消失。
"""
import re
import time

import pytest

from app.services import memory_service as mem

_PEM = ("-----BEGIN RSA PRIVATE KEY-----\n"
        "MIIEpAIBAAKCAQEA0Z3VS5JJcdsBEK3qTn7YmwL\n"
        "-----END RSA PRIVATE KEY-----")


# ---------------- 正常路径 ----------------

def test_redact_password_key_value():
    clean, n = mem.redact_sensitive("导出脚本里写死 password=hunter2 了")
    assert clean == "导出脚本里写死 password=<redacted:password> 了"
    assert n == 1
    assert "hunter2" not in clean


def test_redact_api_key_with_sk_prefixed_value():
    """票面验收原文形态：`api_key=sk-...` 整体只算一处替换。"""
    clean, n = mem.redact_sensitive("api_key=sk-1234567890abcdef")
    assert clean == "api_key=<redacted:api_key>"
    assert n == 1
    assert "1234567890abcdef" not in clean


def test_redact_dsn_keeps_scheme_user_host_and_database():
    clean, n = mem.redact_sensitive(
        "连库串 postgresql+asyncpg://u:topsecret@127.0.0.1:5432/db")
    assert clean == "连库串 postgresql+asyncpg://u:<redacted:password>@127.0.0.1:5432/db"
    assert n == 1
    assert "topsecret" not in clean


def test_redact_bearer_token_keeps_scheme_word():
    clean, n = mem.redact_sensitive("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.abc-def")
    assert clean == "Authorization: Bearer <redacted:token>"
    assert n == 1
    assert "eyJhbGciOiJIUzI1NiJ9" not in clean


@pytest.mark.parametrize("raw,expected", [
    ("带 Bearer abcdefgh12345，请从密钥服务取", "带 Bearer <redacted:token>，请从密钥服务取"),
    ("Bearer abcdef12345, then rotate it", "Bearer <redacted:token>, then rotate it"),
])
def test_redact_bearer_token_does_not_swallow_the_following_clause(raw, expected):
    """Bearer 词的右边界与键值规则同一条红线：遮完 token 不能把后半句描述一起吞掉。

    裸的非空白量词（一次吃 8 个以上非空白字符）会跨过全角逗号一路吃到下一个空格，
    把「，请从密钥服务取」整段带走 —— 服务层的 update 用例把这个问题在入库现场
    抓了出来，这里补上纯函数的最小复现。
    """
    clean, n = mem.redact_sensitive(raw)
    assert clean == expected
    assert n == 1


def test_redact_bare_sk_prefix_secret():
    clean, n = mem.redact_sensitive("密钥是 sk-proj-abcdefghijklmnop")
    assert clean == "密钥是 <redacted:api_key>"
    assert n == 1


def test_redact_pem_private_key_block():
    clean, n = mem.redact_sensitive(_PEM)
    assert clean == "<redacted:private_key>"
    assert n == 1
    assert "MIIEpAIBAAKCAQEA" not in clean


def test_redact_counts_each_occurrence_separately():
    clean, n = mem.redact_sensitive("password=aaa 以及 api_key: bbbcccddddeeee")
    assert n == 2
    assert clean.count("<redacted:") == 2


# ---------------- 边界与异常分支 ----------------

@pytest.mark.parametrize("raw,secret", [
    ("password=hunter2", "hunter2"),          # 票面点名的三种密码键拼写
    ("passwd=hunter2", "hunter2"),
    ("pwd=hunter2", "hunter2"),
    ("api_key=abcdefghijklmnop", "abcdefghijklmnop"),
    ("apikey=abcdefghijklmnop", "abcdefghijklmnop"),
    ("api-key=abcdefghijklmnop", "abcdefghijklmnop"),
    ("access_key=abcdefghijklmnop", "abcdefghijklmnop"),
    ("secret=abcdefghijklmnop", "abcdefghijklmnop"),
    ("secret_key=abcdefghijklmnop", "abcdefghijklmnop"),
    ("client_secret=abcdefghijklmnop", "abcdefghijklmnop"),
    ("token=abcdefghijklmnop", "abcdefghijklmnop"),
    ("auth_token=abcdefghijklmnop", "abcdefghijklmnop"),
])
def test_redact_each_sensitive_key_spelling(raw, secret):
    """票面 L14-16 点名的键形态逐条过闸，一个都不能漏。"""
    clean, n = mem.redact_sensitive(f"配置项 {raw} 记一下")
    assert n == 1, f"{raw} 未被脱敏"
    assert secret not in clean
    assert "<redacted:" in clean


@pytest.mark.parametrize("raw", [
    "API_KEY=abcdefghijklmnop",
    "Api-Key: abcdefghijklmnop",
    "PASSWORD = hunter2",
    "TOKEN :abcdefghijklmnop",
])
def test_redact_is_case_insensitive_and_tolerates_spaces_around_separator(raw):
    clean, n = mem.redact_sensitive(raw)
    assert n == 1
    assert "hunter2" not in clean and "abcdefghijklmnop" not in clean


def test_redact_chinese_prose_around_secret_is_preserved():
    """中文正文里夹带密钥：只吃值，不吃上下文（中文全角逗号不该被当分隔符切断）。"""
    clean, n = mem.redact_sensitive("登录用 admin，密码是 password=abc123def 就行，别写进代码")
    assert clean == "登录用 admin，密码是 password=<redacted:password> 就行，别写进代码"
    assert n == 1
    assert "abc123def" not in clean


@pytest.mark.parametrize("raw,expected", [
    ("密码是 password=hunter2，记得改环境变量",
     "密码是 password=<redacted:password>，记得改环境变量"),
    ("password=hunter2（临时）先记一下",
     "password=<redacted:password>（临时）先记一下"),
    ("password=hunter2、hunter3 都废弃了",
     "password=<redacted:password>、hunter3 都废弃了"),
    ("token=abc123def456。",
     "token=<redacted:token>。"),
])
def test_redact_stops_at_full_width_punctuation(raw, expected):
    """全角标点是断点：后半句是正常描述，不该被当密令的一部分一起遮掉。"""
    clean, n = mem.redact_sensitive(raw)
    assert clean == expected
    assert n == 1


def test_redact_cjk_only_value_is_still_masked():
    """密令本身是中文：整段遮。宁可过度脱敏，不能漏。"""
    clean, n = mem.redact_sensitive("内网密码 password=我的口令行不行")
    assert "我的口令行不行" not in clean
    assert clean == "内网密码 password=<redacted:password>"
    assert n == 1


@pytest.mark.parametrize("raw,expected", [
    ('password="hunter2"', "password=<redacted:password>"),
    ("password='hunter2'", "password=<redacted:password>"),
    ('password="hunter2', "password=<redacted:password>"),   # 引号未闭合也要遮
    ('password=hunter2"', "password=<redacted:password>"),
])
def test_redact_handles_quotes_in_and_around_value(raw, expected):
    clean, n = mem.redact_sensitive(raw)
    assert clean == expected
    assert "hunter2" not in clean


def test_redact_stops_at_newline_but_still_hides_the_value():
    """值跨行（模型把密钥和下一句写在同一 content 里）：第一行仍被遮住。"""
    clean, n = mem.redact_sensitive("password=abc123\n下一句是正常的踩坑描述")
    assert clean.startswith("password=<redacted:password>")
    assert "abc123" not in clean
    assert "正常的踩坑描述" in clean
    assert n == 1


@pytest.mark.parametrize("raw", [
    "password=",            # 只含键不含值：没有秘密可遮（票面已定口径）
    "password= ",
    'password=""',
    "api_key=,",
    "今天讨论了下密码策略，没人给出 password 的具体值",   # 关键词单独出现，不是键值形态
])
def test_key_without_value_or_bare_word_is_not_redacted(raw):
    """防过度脱敏：没有真实凭据时原文一字不改，否则正常叙述会被吃掉。"""
    clean, n = mem.redact_sensitive(raw)
    assert clean == raw
    assert n == 0


@pytest.mark.parametrize("clean_text", [
    "导出统一走 pandas 分块，避免 OOM",
    "登录页样式沿用 antd 主题变量",
    "决策：网关统一用 OpenResty，不在应用容器里配 HTTPS",
    "接口返回 {\"ok\": true, \"count\": 3} 这种结构",   # 普通 JSON 不该被误伤
])
def test_text_without_secrets_is_untouched(clean_text):
    result, n = mem.redact_sensitive(clean_text)
    assert result == clean_text
    assert n == 0


@pytest.mark.parametrize("raw", ["", "   ", "\n", "\t"])
def test_empty_and_whitespace_only_input(raw):
    result, n = mem.redact_sensitive(raw)
    assert result == raw
    assert n == 0


@pytest.mark.parametrize("raw", [
    "api_key=sk-1234567890abcdef",
    "postgresql+asyncpg://u:topsecret@127.0.0.1:5432/db",
    "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9",
    _PEM,
])
def test_redact_is_idempotent(raw):
    """工具层与服务层共用同一道闸，二次调用必须「文本不变 + 计数归零」，
    否则「已脱敏 N 处」会对同一处重复计数，占位符也会被套娃。"""
    once, n1 = mem.redact_sensitive(raw)
    twice, n2 = mem.redact_sensitive(once)
    assert twice == once
    assert n1 == 1
    assert n2 == 0


def test_redact_huge_content_without_secret_is_fast():
    """超长正文（20 万字以上）性能不塌：正则不许在这里出现灾难性回溯。"""
    raw = "记录一次普通踩坑：分页参数越界会 500。" * 12_000
    assert len(raw) > 200_000
    started = time.perf_counter()
    clean, n = mem.redact_sensitive(raw)
    elapsed = time.perf_counter() - started
    assert clean == raw and n == 0
    assert elapsed < 3.0, f"脱敏耗时 {elapsed:.2f}s，疑似回溯炸了"


def test_redact_huge_content_with_trailing_secret_still_redacts():
    secret = "password=verylongsecretvalue9"
    clean, n = mem.redact_sensitive("x" * 100_000 + " " + secret)
    assert n == 1
    assert "verylongsecretvalue9" not in clean
    assert clean.endswith("password=<redacted:password>")


def test_sensitive_rules_are_centralized():
    """票面「规则表集中一处可维护」：形状契约——(kind, 已编译正则) 的有序表，无重复正则。"""
    rules = mem.SENSITIVE_RULES
    assert rules and isinstance(rules, tuple)
    assert all(isinstance(kind, str) and isinstance(pat, re.Pattern) for kind, pat in rules)
    assert len({id(pat) for _, pat in rules}) == len(rules)
    assert {kind for kind, _ in rules} >= {"password", "api_key", "token", "secret", "private_key"}


# ---------------- 结构中和（</memories> 逃逸）----------------

def test_neutralize_closing_tag():
    clean, n = mem.neutralize_memory_tags("这段结束于 </memories> 之后还有内容")
    assert clean == "这段结束于 ＜/memories＞ 之后还有内容"
    assert n == 1


def test_neutralize_opening_and_attributed_tag():
    clean, n = mem.neutralize_memory_tags('<memories>\n<memories type="x">\n</memories >')
    assert "<memories" not in clean and "</memories" not in clean
    assert "＜memories＞" in clean and "＜memories type=\"x\"＞" in clean and "＜/memories ＞" in clean
    assert n == 3


@pytest.mark.parametrize("raw", ["</Memories>", "</MEMORIES>", "<MEMORIES>"])
def test_neutralize_is_case_insensitive(raw):
    clean, n = mem.neutralize_memory_tags(raw)
    assert "<" not in clean and ">" not in clean
    assert n == 1


@pytest.mark.parametrize("raw", [
    "<b>粗</b>",
    "<div class='x'>块</div>",
    "a < b 且 c > d",
    "</memoriesish>",     # 别的标签名：不会闭合我们的块，不该被误伤
    "＜memories＞",        # 已是全角，无需再处理
])
def test_other_angle_brackets_are_preserved(raw):
    """只针对 <memories> 块标记，不做通用标签白名单（002 既有测试也依赖 `<b>` 原样保留）。"""
    clean, n = mem.neutralize_memory_tags(raw)
    assert clean == raw
    assert n == 0


@pytest.mark.parametrize("raw", ["", "   ", "完全正常的记忆正文"])
def test_neutralize_empty_or_plain_text(raw):
    clean, n = mem.neutralize_memory_tags(raw)
    assert clean == raw
    assert n == 0


# ---------------- 写入关卡 sanitize_memory_text：两类命中分开计数 ----------------
# 只测两个单函数不够：组合层的返回形态是工具层文案的数据源，混类计数正是从这里丢的。

def test_sanitize_reports_credentials_and_tags_separately():
    out = mem.sanitize_memory_text("写死 password=hunter2 然后 </memories> 收工")
    assert out.text == "写死 password=<redacted:password> 然后 ＜/memories＞ 收工"
    assert out.secrets == 1 and out.tags == 1


def test_sanitize_is_idempotent_on_both_axes():
    """两道关卡叠起来仍需幂等：工具层与服务层各过一遍，第二次必须文本不变、计数归零。"""
    once = mem.sanitize_memory_text("password=hunter2 </memories>")
    twice = mem.sanitize_memory_text(once.text)
    assert twice.text == once.text
    assert twice.secrets == 0 and twice.tags == 0


@pytest.mark.parametrize("raw", ["", "   ", "完全正常的记忆正文", 123, None])
def test_sanitize_passes_through_clean_or_non_string_input(raw):
    """空值 / 纯空格 / 干净叙述 / 非字符串：一律原文透传且两个计数归 0，不拿它当错误。"""
    out = mem.sanitize_memory_text(raw)
    assert out.text == raw
    assert out.secrets == 0 and out.tags == 0
