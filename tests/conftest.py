import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from app.db import make_engine, run_migrations

@pytest.fixture
async def test_engine():
    """建测试库（不存在时）并返回 engine；测试库幂等迁移后由各测试自行使用。"""
    # PG 禁止在事务块内 CREATE DATABASE，管理连接必须用 AUTOCOMMIT
    admin = create_async_engine(
        "postgresql+asyncpg://zerocode:zerocode123@localhost:5433/postgres",
        isolation_level="AUTOCOMMIT",
    )
    async with admin.begin() as conn:
        exists = (await conn.execute(text("SELECT 1 FROM pg_database WHERE datname='zerocode_test'"))).first()
        if not exists:
            await conn.exec_driver_sql("CREATE DATABASE zerocode_test")
    await admin.dispose()
    from app.config import test_database_url
    engine = make_engine(test_database_url())
    await run_migrations(engine)
    yield engine
    # 测试间清库（全局约束），保留 schema_migration 与 app_config 种子
    # deploy_record 显式点名：此前只靠 app_project 的 FK ON DELETE CASCADE 隐式带掉，
    # 一旦历史表改成「应用删了历史仍留」就会静默漏表（审查 M-9）。
    async with engine.begin() as conn:
        await conn.execute(text(
            "TRUNCATE message, file_change, compaction, chat_session, workspace, app_project, "
            "model_config, deploy_record, app_instance_link, release_application, app_release, actor "
            "RESTART IDENTITY CASCADE"))
    await engine.dispose()

@pytest.fixture
def gateway_routes_tmp(tmp_path_factory, monkeypatch):
    """把网关 routes.json 导出重定向到会话临时文件：prod 起停/删除会旁路刷新它，
    不隔离就会写进仓库 gateway/routes/（既有 prod 测试不该感知这个副作用）。"""
    p = tmp_path_factory.mktemp("gateway") / "routes.json"
    monkeypatch.setenv("ZEROCODE_GATEWAY_ROUTES", str(p))
    return p


@pytest.fixture(autouse=True)
def _isolate_gateway_routes(gateway_routes_tmp):
    """全量测试自动隔离网关路由表落盘路径（无显式依赖也生效）。"""
    return gateway_routes_tmp


@pytest.fixture
async def client(test_engine):
    from asgi_lifespan import LifespanManager
    from httpx import AsyncClient, ASGITransport
    from app.main import create_app
    # str(test_engine.url) 会掩码密码（SQLAlchemy 默认 hide_password=True）导致认证失败，
    # 故用 render_as_string(hide_password=False) 取真实连接串
    app = create_app(test_engine.url.render_as_string(hide_password=False))
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            # httpx AsyncClient 不自带 .app 属性，补挂 FastAPI 实例（同 starlette TestClient 约定），测试可取 app.state
            c.app = app
            yield c
