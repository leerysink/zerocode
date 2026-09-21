# tests/test_schema_parity_e2e.py
import shutil
from pathlib import Path

import pytest
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import create_async_engine
from app.db import make_engine, make_session_factory
from app.deploy import provision, scratch, schemacheck, sqlrun
from app.deploy.provision import read_cred
from app.deploy.sqlrun import docker_available

pytestmark = pytest.mark.skipif(not docker_available(), reason="docker/pg 不可用")

ROOT = Path("D:/__zc_p1__")
SLUG = "zcp1verify"
SQL_DIR = ROOT / SLUG / "sqlchange"


def _write(files):
    shutil.rmtree(ROOT, ignore_errors=True)
    SQL_DIR.mkdir(parents=True)
    for name, txt in files.items():
        (SQL_DIR / name).write_text(txt, encoding="utf-8", newline="")


@pytest.fixture
async def sf():
    """平台库 session_factory（provision/read_cred 都走它）。"""
    return make_session_factory(make_engine())


@pytest.mark.asyncio
async def test_scratch_create_and_drop(sf):
    name = scratch.verify_db_name("zcp1verify")
    cred = await scratch.ensure_scratch(sf, "zcp1verify")
    assert cred["db"] == name
    await scratch.cleanup_scratch(sf, "zcp1verify")


@pytest.mark.asyncio
async def test_verify_scratch_fold_is_green(sf):
    """主流程冒烟：files 从零 fold 进 scratch，scratch↔scratch 恒等 → parity True。"""
    _write({"V20260922_01_init.sql": "CREATE TABLE IF NOT EXISTS t1 (id INT);\n"})
    await sqlrun.build_if_missing()
    cred = await provision.create_db_with_role(sf, scratch.verify_db_name(SLUG))
    try:
        mig = await sqlrun.run_sqlchange(ROOT / SLUG, "verify", cred)
        assert mig["ok"] is True
        norm = schemacheck.normalize_dump(await schemacheck.dump_schema(cred))
        assert schemacheck.is_parity(schemacheck.schema_diff(norm, norm)) is True
    finally:
        await provision.drop_db(sf, scratch.verify_db_name(SLUG))
        shutil.rmtree(ROOT, ignore_errors=True)


def _dev_url(cred):
    return (f"postgresql+asyncpg://{cred['user']}:{cred['password']}"
            f"@localhost:5433/{cred['db']}")


@pytest.mark.asyncio
async def test_verify_flags_dev_drift(sf):
    """漂移捕获：向 dev 直连建一张不在文件里的表 → verify 报 only_in_dev；删掉后重新 OK。"""
    _write({"V20260922_01_init.sql": "CREATE TABLE IF NOT EXISTS t1 (id INT);\n"})
    await sqlrun.build_if_missing()
    cred_dev = await read_cred(sf, SLUG, "dev") or await provision.provision_env(sf, SLUG, "dev")
    assert (await sqlrun.run_sqlchange(ROOT / SLUG, "dev", cred_dev))["ok"] is True
    try:
        assert (await schemacheck.verify_dev_matches_files(sf, ROOT / SLUG, SLUG))["ok"] is True
        eng = create_async_engine(_dev_url(cred_dev))
        async with eng.begin() as c:
            await c.execute(sa_text("CREATE TABLE IF NOT EXISTS stray (id int)"))
        await eng.dispose()
        res = await schemacheck.verify_dev_matches_files(sf, ROOT / SLUG, SLUG)
        assert res["ok"] is False and "stray" in "\n".join(res["diff"]["only_in_dev"])
        eng = create_async_engine(_dev_url(cred_dev))
        async with eng.begin() as c:
            await c.execute(sa_text("DROP TABLE stray"))
        await eng.dispose()
        assert (await schemacheck.verify_dev_matches_files(sf, ROOT / SLUG, SLUG))["ok"] is True
    finally:
        shutil.rmtree(ROOT, ignore_errors=True)
        await provision.drop_env(sf, SLUG, "dev")
