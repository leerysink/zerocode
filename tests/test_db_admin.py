# tests/test_db_admin.py
"""应用 DB 操作台服务层与端点。纯函数（env 白名单/语句分类/标识符转义）不碰库；
审计写入用平台 session；连接类端点需真实 provision 的 dev 库（docker+PG 可用才跑）。"""
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.db import make_session_factory
from app.deploy import provision, sqlrun
from app.deploy.errors import DeployError
from app.services import db_admin_service as svc


@pytest.fixture
async def sf(test_engine):
    return make_session_factory(test_engine)


def test_validate_env_ok_and_reject():
    svc.validate_env("dev")
    svc.validate_env("prod")
    with pytest.raises(DeployError) as e:
        svc.validate_env("test")
    assert e.value.status == 400


def test_quote_ident_escapes_double_quote():
    assert svc.quote_ident('orders') == '"orders"'
    assert svc.quote_ident('a"b') == '"a""b"'


def test_classify_op_by_first_keyword():
    assert svc.classify_op("  SELECT * FROM t") == "select"
    assert svc.classify_op("insert into t values (1)") == "insert"
    assert svc.classify_op("UPDATE t SET a=1") == "update"
    assert svc.classify_op("DELETE FROM t") == "delete"
    assert svc.classify_op("CREATE TABLE t(a int)") == "ddl"
    assert svc.classify_op("EXPLAIN SELECT 1") == "other"


async def test_log_operation_writes_row(sf):
    async with sf() as s:
        wid = (await s.execute(text(
            "INSERT INTO workspace (name, root_path) VALUES ('a', '/tmp/a') RETURNING id"))).scalar()
        aid = (await s.execute(text(
            "INSERT INTO app_project (workspace_id, name, slug, scale, stack, prod_port, dev_port) "
            "VALUES (:w,'a','oplogx','small','vue3-fastapi',8501,8502) RETURNING id"),
            {"w": wid})).scalar()
        await s.commit()
    await svc.log_operation(sf, app_id=aid, actor_user_id=7, env="dev",
                            op_type="select", sql_text="SELECT 1", target_table=None,
                            status="success", affected=1, duration_ms=3)
    async with sf() as s:
        row = (await s.execute(text(
            "SELECT env, op_type, status, actor_user_id FROM db_operation_log WHERE app_id=:a"),
            {"a": aid})).mappings().first()
    assert row["env"] == "dev" and row["op_type"] == "select" and row["status"] == "success"
    assert row["actor_user_id"] == 7
