# tests/test_sqlrunner.py
import shutil
import pytest
from pathlib import Path
from app.db import make_engine, make_session_factory
from app.deploy import provision, sqlrun

pytestmark = [pytest.mark.asyncio,
              pytest.mark.skipif(not sqlrun.docker_available(), reason="docker 不可用")]

SLUG = "zctask9"
ROOT = Path("D:/__zc_task9__")            # 试验目录根（仓库外，docker bind mount 用）
SQL_DIR = ROOT / SLUG / "sqlchange"

GOOD1 = """-- V20260918_01_init.sql
-- 用途：t1 表  影响：t1
CREATE TABLE IF NOT EXISTS t1 (id INT);
"""
GOOD2 = """-- V20260918_02_add.sql
-- 用途：t1 加列  影响：t1
ALTER TABLE t1 ADD COLUMN IF NOT EXISTS name VARCHAR(50);
"""
BAD = """-- V20260918_03_bad.sql
-- 用途：故意失败  影响：t1
THIS IS NOT SQL;
"""

def _write(files):
    # brief 只清 SQL_DIR.parent（= ROOT/SLUG），外层根 ROOT 由 mkdir(parents=True) 建出来后没人收，
    # 这里整棵清掉，跑完不留空壳目录。
    shutil.rmtree(ROOT, ignore_errors=True)
    SQL_DIR.mkdir(parents=True)
    for name, text in files.items():
        (SQL_DIR / name).write_text(text, encoding="utf-8", newline="")

@pytest.fixture(scope="module", autouse=True)
async def _image():
    await sqlrun.build_if_missing()

# fixture 名即参数名：测试用 cred 形参接收本环境凭据（计划已修正 fixture 链缺陷，不用全局 _cred_store）
@pytest.fixture(autouse=True)
async def cred():
    sf = make_session_factory(make_engine())
    await provision.drop_env(sf, SLUG, "dev")
    c = await provision.provision_env(sf, SLUG, "dev")
    yield c
    await provision.drop_env(sf, SLUG, "dev")
    shutil.rmtree(ROOT, ignore_errors=True)

async def _apply(cred):
    return await sqlrun.run_sqlchange(SQL_DIR.parent, "dev", cred)

async def test_apply_skip_and_history(cred):
    _write({"V20260918_01_init.sql": GOOD1, "V20260918_02_add.sql": GOOD2})
    r = await _apply(cred)
    assert r["ok"] is True and "OK V20260918_01_init.sql" in r["log"] and "OK V20260918_02_add.sql" in r["log"]
    r2 = await _apply(cred)
    assert r2["ok"] is True and "SKIP" in r2["log"] and "OK V20260918_01" not in r2["log"]
    # 历史表可查
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy import text
    eng = create_async_engine(
        f"postgresql+asyncpg://{cred['user']}:{cred['password']}@localhost:5433/{cred['db']}")
    async with eng.connect() as c:
        rows = (await c.execute(text("SELECT filename, success FROM sqlchange_history ORDER BY filename"))).fetchall()
    await eng.dispose()
    assert [tuple(r) for r in rows] == [("V20260918_01_init.sql", True), ("V20260918_02_add.sql", True)]

async def test_failure_exit2_and_reapply_after_fix(cred):
    _write({"V20260918_01_init.sql": GOOD1, "V20260918_03_bad.sql": BAD})
    r = await _apply(cred)
    assert r["ok"] is False and r["code"] == 2 and "FAIL V20260918_03_bad.sql" in r["log"]
    _write({"V20260918_01_init.sql": GOOD1, "V20260918_03_bad.sql": GOOD2.replace("02_add", "03_bad").replace("-- V20260918_02_add.sql", "-- V20260918_03_bad.sql")})
    r2 = await _apply(cred)
    assert r2["ok"] is True  # 失败文件修复后补跑成功，01 不重跑
    # 补跑成功后历史行必须记上耗时（规格 07 §4.3「成功 → INSERT history(success=true, duration)」）：
    # 成功分支的 ON CONFLICT DO UPDATE 若不写 duration_ms，重跑成功的行会永久留着失败时的 NULL。
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy import text
    eng = create_async_engine(
        f"postgresql+asyncpg://{cred['user']}:{cred['password']}@localhost:5433/{cred['db']}")
    async with eng.connect() as c:
        rows = (await c.execute(text(
            "SELECT filename, success, duration_ms FROM sqlchange_history ORDER BY filename"))).fetchall()
    await eng.dispose()
    assert [r[0] for r in rows] == ["V20260918_01_init.sql", "V20260918_03_bad.sql"]
    assert rows[1][1] is True and rows[1][2] is not None  # 重跑成功的 03：success=true 且 duration 已补齐

async def test_tamper_exit3(cred):
    _write({"V20260918_01_init.sql": GOOD1})
    assert (await _apply(cred))["ok"] is True
    _write({"V20260918_01_init.sql": GOOD1 + "\n-- 改过一行\nSELECT 1;\n"})
    r = await _apply(cred)
    assert r["ok"] is False and r["code"] == 3 and "TAMPER" in r["log"]

async def test_empty_dir_ok(cred):
    _write({})
    r = await _apply(cred)
    assert r["ok"] is True
