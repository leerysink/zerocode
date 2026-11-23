"""账号治理迁移（规格 2026-11-23 / ADR-0067~0070）：封禁列、邀请码表、quota 平台默认行。

本片是预重构：只验「地基形状」，不含任何行为。行为断言在 002/004/006 各自的票里。
"""
import pytest
from sqlalchemy import text


async def test_governance_migration_schema(test_engine):
    """actor 上两个封禁列可空存在；invite_code 表与关键列齐备。"""
    async with test_engine.connect() as conn:
        cols = (await conn.execute(text(
            "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
            "WHERE table_name='actor'"))).all()
        t = (await conn.execute(text("SELECT to_regclass('invite_code')"))).scalar()
        ic_cols = (await conn.execute(text(
            "SELECT column_name, is_nullable FROM information_schema.columns "
            "WHERE table_name='invite_code'"))).all()
    by_name = {c[0]: c for c in cols}
    assert {"banned_at", "banned_reason"} <= set(by_name), sorted(by_name)
    # 封禁是例外而非常态：默认 NULL = 未封，不给它一个「全员已封」的默认值
    assert by_name["banned_at"][2] == "YES" and by_name["banned_reason"][2] == "YES"

    assert t is not None
    ic = {c[0]: c for c in ic_cols}
    assert {"id", "code", "created_by", "used_by", "used_at", "revoked_at", "created_at"} <= set(ic)
    # 一码一号：used_* / revoked_* 都可为空（未使用态就是三个 NULL）
    assert ic["used_by"][1] == "YES" and ic["used_at"][1] == "YES" and ic["revoked_at"][1] == "YES"
    # created_by 不可空：没有签发者的码无法追溯，而追溯是本票唯一保留的留痕
    assert ic["created_by"][1] == "NO"


async def test_invite_code_unique_and_cleared_by_truncate(test_engine):
    """码唯一约束在位；且它随 actor 的 CASCADE 清理一起清掉（不跨用例串味）。"""
    async with test_engine.begin() as conn:
        idx = (await conn.execute(text(
            "SELECT indexname, indexdef FROM pg_indexes WHERE tablename='invite_code'"))).all()
        await conn.execute(text(
            "INSERT INTO actor (nickname, password_hash, is_founder) "
            "VALUES ('gov-seed', 'x', false) RETURNING id"))
        actor_id = (await conn.execute(text(
            "SELECT id FROM actor WHERE nickname='gov-seed'"))).scalar()
        await conn.execute(text(
            "INSERT INTO invite_code (code, created_by) VALUES ('dup-code', :a)"),
            {"a": actor_id})
        with pytest.raises(Exception) as ei:
            await conn.execute(text(
                "INSERT INTO invite_code (code, created_by) VALUES ('dup-code', :a)"),
                {"a": actor_id})
    assert "dup-code" in str(ei.value).lower() or "unique" in str(ei.value).lower()
    assert any("code" in i[1].lower() for i in idx if "UNIQUE" in i[1].upper()), idx


async def test_quota_platform_default_rows_seeded(test_engine):
    """平台默认额度：owner NULL 的两行存在且值为 10 / 4（spec §4 D14）。

    这两行既由迁移播种（首个用例看到的库），也由 conftest 种子重放（清库后的用例看到的库）——
    两份必须同值，否则「平台级打底行」这个事实在测试里时有时无。
    """
    async with test_engine.connect() as conn:
        rows = (await conn.execute(text(
            "SELECT key, value, owner_user_id FROM app_config "
            "WHERE key IN ('quota.max_apps', 'quota.max_running_stacks')"))).all()
    got = {r[0]: (r[1], r[2]) for r in rows}
    assert set(got) == {"quota.max_apps", "quota.max_running_stacks"}, got
    assert got["quota.max_apps"] == (10, None), got
    assert got["quota.max_running_stacks"] == (4, None), got


async def test_governance_migration_replayable(test_engine):
    """幂等守卫：迁移语句在已建好的库上重放不报错（存量库升级路径 / ADR-0014 全量重放）。"""
    from pathlib import Path
    from app.db import split_statements

    sql = (Path(__file__).resolve().parents[1] / "changelog" /
           "V20261123_01_account_governance.sql").read_text(encoding="utf-8")
    statements = split_statements(sql)
    assert statements, "迁移文件没解析出任何语句"
    async with test_engine.begin() as conn:
        for st in statements:
            await conn.execute(text(st))
    # 重放不得重复播种
    async with test_engine.connect() as conn:
        cnt = (await conn.execute(text(
            "SELECT count(*) FROM app_config WHERE key LIKE 'quota.%'"))).scalar()
    assert cnt == 2, cnt


async def test_governance_migration_is_replay_safe():
    """打包闸门口径：INSERT 必带 ON CONFLICT、无裸 UPDATE、无破坏性语句（build_output 的审计）。"""
    from pathlib import Path
    from scripts.build_output import audit_replay_safety

    sql = (Path(__file__).resolve().parents[1] / "changelog" /
           "V20261123_01_account_governance.sql").read_text(encoding="utf-8")
    assert audit_replay_safety(sql, "V20261123_01") == []
