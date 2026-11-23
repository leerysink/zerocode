import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from app.db import make_engine, run_migrations


async def mint_invite_with(session_factory, n: int = 1) -> list[str]:
    """签 n 枚有效邀请码（ADR-0069 关门后建第二个账号的唯一途径）。

    走 `app.store.invites` 而不是拼 SQL：核销条件与状态口径要和生产同一份实现。
    created_by 取库里最早的账号（首注册即管理员），与治理台签发同语义。
    """
    from app.store import invites

    out = []
    async with session_factory() as s:
        admin = (await s.execute(text("SELECT id FROM actor ORDER BY id LIMIT 1"))).scalar()
        if admin is None:
            raise AssertionError(
                "库里还没有任何账号：首位账号免码自举（ADR-0069），无需也没有签发者。"
                "在第一个账号注册之前签码是用法错误，请在建号之后再逐枚签发。")
        for _ in range(n):
            out.append((await invites.issue(s, created_by=admin))["code"])
        await s.commit()
    return out


async def mint_invite(app, n: int = 1) -> list[str]:
    return await mint_invite_with(app.state.session_factory, n)


async def _has_actor(session_factory) -> bool:
    async with session_factory() as s:
        return bool((await s.execute(text("SELECT EXISTS (SELECT 1 FROM actor)"))).scalar())


async def invite_for_registration(app):
    """这一次注册要不要码，由库里有没有账号决定：空库返回 None（首号自举免码），
    有号就现签一枚。免得像某些用例那样把「反正我是第一个」当成永久前提。"""
    if not await _has_actor(app.state.session_factory):
        return None
    return (await mint_invite(app))[0]


async def invite_for_registration_url(url: str):
    """给「测试自己 create_app 出来的独立实例」用。

    那种 app 的引擎归 TestClient 自己的事件循环，测试协程既不能 asyncio.run（已经在循环里）、
    也不该直接借它的连接池（跨循环），所以另起一个引擎连同一个库。
    """
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.db import make_session_factory

    eng = create_async_engine(url)
    try:
        sf = make_session_factory(eng)
        if not await _has_actor(sf):
            return None
        return (await mint_invite_with(sf, 1))[0]
    finally:
        await eng.dispose()


def invite_for_registration_sync(app):
    """同步版：给 TestClient 那批用例用（见 mint_invite_sync 里关于事件循环的说明）。"""
    import asyncio

    from sqlalchemy.ext.asyncio import create_async_engine

    from app.db import make_session_factory

    url = app.state.engine.url.render_as_string(hide_password=False)

    async def _go():
        eng = create_async_engine(url)
        try:
            sf = make_session_factory(eng)
            if not await _has_actor(sf):
                return None
            return (await mint_invite_with(sf, 1))[0]
        finally:
            await eng.dispose()

    return asyncio.run(_go())


def mint_invite_sync(app, n: int = 1) -> list[str]:
    """同步上下文里签码——供 TestClient 那批连开发库的聊天/WS 测试用（它们没有可用事件循环）。

    **另起一个临时引擎并 dispose**，绝不复用 `app.state.engine`：asyncpg 连接绑死创建它的
    事件循环，在 `asyncio.run` 里借道应用的连接池会把跨循环的连接放回池里，等应用自己
    下一次取用时才炸——那种失败离根因隔了十个用例，最难查。
    """
    import asyncio

    from sqlalchemy.ext.asyncio import create_async_engine

    from app.db import make_session_factory

    url = app.state.engine.url.render_as_string(hide_password=False)

    async def _go():
        eng = create_async_engine(url)
        try:
            return await mint_invite_with(make_session_factory(eng), n)
        finally:
            await eng.dispose()

    return asyncio.run(_go())


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
    # 测试间清库（全局约束）。app_config 不在 TRUNCATE 名单里，但它的 owner_user_id FK 指向
    # actor（Ticket 003）→ CASCADE 会带飞全表，故清完重放种子（与全部迁移应用完的终态一致）；
    # ON CONFLICT 不写目标：兼容无主键后的 ux_app_config_owner_key。
    async with engine.begin() as conn:
        await conn.execute(text(
            "TRUNCATE message_attachment, message, file_change, compaction, chat_session, workspace, "
            "app_project, app_share, app_publish_request, model_config, deploy_record, "
            "app_instance_link, release_application, app_release, "
            # store_category 不被上面任何表引用（条目只存 key，没建外键），CASCADE 带不到它，
            # 但分类字典的用例会增改停用它——不清就等于把上一轮的运营改动漏给下一轮（ADR-0061 §4）。
            "store_category, "
            # invite_code 的 created_by 外键指向 actor，CASCADE 本会带它一起清；写进名单是为了
            # 让「治理面每轮从零开始」这件事在名单上可读，而不是靠外键图的隐式行为（ADR-0069）。
            "invite_code, "
            "actor_session, actor RESTART IDENTITY CASCADE"))
        await conn.execute(text(
            "INSERT INTO app_config (key, value) VALUES "
            "('llm.base_url', '\"https://api.deepseek.com/v1\"'), "
            "('llm.api_key', '\"\"'), "
            "('llm.model', '\"deepseek-chat\"'), "
            "('llm.supports_vision', 'false'), "
            "('agent.write_confirm', 'false'), "
            "('agent.ask_timeout_seconds', '0'), "
            "('agent.max_tool_rounds', '30'), "
            "('agent.history_window', '40'), "
            "('deploy.apps_root', '\"\"'), "
            "('deploy.port_base', '8210'), "
            "('deploy.port_max', '8399'), "
            "('deploy.keep_versions', '5'), "
            "('deploy.health_timeout', '240'), "
            "('deploy.auto_stop_dev_on_exit', 'true'), "
            "('deploy.default_nic', '\"\"'), "
            "('store.enabled', 'true'), "
            "('store.keep_prev', '1'), "
            # 需求澄清门禁（ADR-0032）：生产库里这两行由 changelog/V20261101 播成默认开，
            # 但 CASCADE 清库会把 owner NULL 的平台行一并带走、只靠这份种子重放，所以键必须
            # 跟着迁移走，否则「平台级打底行」这类事实在测试里根本不存在、测不出来。
            # 值显式取 false：绝大多数既有用例测的是别的接缝，不该先撞上这道写入门禁；
            # 澄清自身的用例各自打开（见 tests/test_agent_loop.py 的 clarify 夹具）。
            "('clarify.enabled', 'false'), "
            "('clarify.ask_remind_at', '6'), "
            # 账号配额平台默认（ADR-0068）：与 changelog/V20261123_01 同值。清库会把 owner NULL
            # 的打底行带走，不重放则「平台默认存在」这一事实在测试里时有时无——第一个用例看得到
            # 迁移播的行、后面的用例看不到，「默认 10」与「缺键=不限」两条断言会互相掩盖。
            "('quota.max_apps', '10'), "
            "('quota.max_running_stacks', '4') "
            "ON CONFLICT DO NOTHING"))
        # 分类字典种子与 changelog/V20261120_02 同值：清库后必须重放，否则「其他」锁死、
        # 停用分类不影响存量 label 这类事实在测试里根本不存在。
        await conn.execute(text(
            "INSERT INTO store_category (key, label, sort) VALUES "
            "('效率工具', '效率工具', 10), ('数据管理', '数据管理', 20), "
            "('内容展示', '内容展示', 30), ('演示样例', '演示样例', 40), "
            "('其他', '其他', 90) ON CONFLICT (key) DO NOTHING"))
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
async def app_(test_engine):
    from asgi_lifespan import LifespanManager
    from app.main import create_app
    # str(test_engine.url) 会掩码密码（SQLAlchemy 默认 hide_password=True）导致认证失败，
    # 故用 render_as_string(hide_password=False) 取真实连接串
    app = create_app(test_engine.url.render_as_string(hide_password=False))
    async with LifespanManager(app):
        yield app


@pytest.fixture
async def anon_client(app_):
    """无会话 cookie 的裸 client：与生产 guard 同口径的 401 用例靶子。"""
    from httpx import AsyncClient, ASGITransport
    async with AsyncClient(transport=ASGITransport(app=app_), base_url="http://t") as c:
        c.app = app_  # httpx 不自带 .app，补挂同 client 约定，测试可取 app.state
        yield c


@pytest.fixture
async def client(anon_client):
    """默认已登录的 client（Ticket 002 起全局 guard 要求会话）：自动注册并持有 owner 账号。

    需要多账号/匿名语义的用例：多身份用 two_auth_clients，未登录用 anon_client，
    不要在 client 上手动清 cookie。"""
    r = await anon_client.post("/api/auth/register",
                               json={"nickname": "owner", "password": "owner-pw"})
    assert r.status_code == 200, r.text
    yield anon_client


@pytest.fixture
async def two_auth_clients(app_):
    """两个独立注册账号各自的已登录 httpx client（隔离测试专用接缝，Ticket 002 接通）。

    005–009 的隔离用例一律经由本接缝取双账号，不得各自拼 cookie，保证认证靶心只有一个。
    第二个账号凭邀请码进来（ADR-0069）：alice 是空库首位、免码且当选创始人，
    所以本夹具给出的**第一个 client 就是管理员**，第二个是普通账号——
    治理面的双身份用例直接拿这两个，不必再自己造号。"""
    from httpx import AsyncClient, ASGITransport
    out = []
    for name in ("alice", "bob"):
        c = AsyncClient(transport=ASGITransport(app=app_), base_url="http://t")
        body = {"nickname": name, "password": "pw-" + name}
        if out:
            body["invite_code"] = (await mint_invite(app_))[0]
        r = await c.post("/api/auth/register", json=body)
        assert r.status_code == 200, r.text
        c.app = app_
        out.append(c)
    yield out
    for c in out:
        await c.aclose()
