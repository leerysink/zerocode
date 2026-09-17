"""Task 7：@文件标记解析与展开（小文件内联 / 大文件·二进制·缺失占位 / 引号路径 / 去重）。"""
from pathlib import Path
from app.agent.mentions import extract_mentions, expand_mentions

def test_extract_basic_and_quoted():
    assert extract_mentions("看下 @a.txt 和 @\"my file.txt\" 再说") == ["a.txt", "my file.txt"]

def test_extract_dedup_and_email_safe():
    # 去重；邮箱 hi@bar.com 不算引用（@ 前是单词字符）
    assert extract_mentions("@a.txt @a.txt hi@bar.com") == ["a.txt"]
    assert extract_mentions("联系 hi@bar.com") == []

def test_expand_small_file_inline(tmp_path):
    (tmp_path / "a.txt").write_text("你好", encoding="utf-8")
    out = expand_mentions(Path(tmp_path), "看 @a.txt")
    assert '<file path="a.txt">\n你好\n</file>' in out
    # 无标记返回 None（不落 context_content）
    assert expand_mentions(Path(tmp_path), "没有引用") is None

def test_expand_big_file_placeholder(tmp_path):
    (tmp_path / "big.txt").write_text("x" * (21 * 1024), encoding="utf-8")
    out = expand_mentions(Path(tmp_path), "@big.txt")
    assert "[大文件 big.txt" in out and "read_file" in out

def test_expand_binary_and_missing(tmp_path):
    (tmp_path / "i.png").write_bytes(b"\x89PNG")
    out = expand_mentions(Path(tmp_path), "@i.png @nope.txt")
    assert "[二进制文件 i.png" in out
    assert "[文件不可用: nope.txt]" in out

def test_expand_out_of_workspace(tmp_path):
    out = expand_mentions(Path(tmp_path), "@/etc/passwd")
    assert "[文件不可用:" in out
