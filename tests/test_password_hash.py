# tests/test_password_hash.py
"""Ticket 001：actor 口令哈希工具（ADR-0003：actor 升级为登录用户的第一步）。"""
from app.store.identity import hash_password, verify_password


def test_哈希后的口令可以通过验证():
    hashed = hash_password("s3cret-中文密码")
    assert verify_password("s3cret-中文密码", hashed) is True


def test_错误的口令不能通过验证():
    hashed = hash_password("right-pass")
    assert verify_password("wrong-pass", hashed) is False


def test_无口令的存量actor一律验证失败():
    # 存量 actor 行 password_hash 为 NULL/空串（迁移后的扩展态），不得被任何口令登录
    assert verify_password("anything", None) is False
    assert verify_password("anything", "") is False


def test_同一口令两次哈希结果不同():
    # 盐随机：防拖库后比对相同口令的账号
    assert hash_password("same-pass") != hash_password("same-pass")


def test_超长口令按72字节截断口径一致不崩溃():
    # 设计决策：截断而非抛错（spec 明确不做密码复杂度策略，入口不拦超长）
    long_pw = "あ" * 100  # UTF-8 下 300 字节，远超 bcrypt 72 字节线
    hashed = hash_password(long_pw)
    assert verify_password(long_pw, hashed) is True
    # 第 72 字节之后的差异被截断吞掉：两口令前 72 字节相同则互认（已知代价，记录即契约）
    assert verify_password(long_pw + "tail", hashed) is True
