# tests/test_store_install_flow.py
"""安装编排：铺包源码 → 分两库 → 锚点全量重放 → 自有镜像起栈 → 写绑定关系。

失败必须 teardown（删本流建的库/行/目录），但一旦进到「起栈」阶段就保留实例（不能删数据）。
"""
import hashlib
import json
import secrets
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from app.db import make_session_factory
from app.deploy import engine as deploy_engine
from app.deploy import prod as deploy_prod
from app.deploy import provision, sqlrun
from app.deploy.errors import DeployError
from app.store import anchor as anchor_mod, install, packages, release as release_svc

pytestmark = pytest.mark.asyncio

# 收集期捕获真实实现：stubs 夹具会覆盖模块属性，第三条用例再把它换回来
_REAL_RUN_SQLCHANGE = sqlrun.run_sqlchange


def _slug():
    return "ins" + secrets.token_hex(3)


@pytest.fixture
async def sf(test_engine):
    return make_session_factory(test_engine)


@pytest.fixture
def release_row(sf, tmp_path, monkeypatch):
    """直接造一个已上架版本（包落在 tmp store/），不绕 Task 7 的闸门。

    checksum 一律按写盘内容重算（write_package 会逐个校验锚点 md5，假值过不了它自己）；
    bodies 传 {filename: 内容}，不传则每个锚点文件落 "SELECT 1;\\n"。
    """
    async def _mk(anchor=None, bodies=None, stack="vue3-fastapi"):
        slug = _slug()
        version = "b20260922000001"
        anchor = [dict(e) for e in (anchor or [{"filename": "V20260920_01_init.sql",
                                                "checksum": ""}])]
        src = tmp_path / "apps" / slug
        (src / "backend").mkdir(parents=True)
        (src / "backend" / "main.py").write_text("# hello\n", encoding="utf-8", newline="")
        (src / "frontend").mkdir()
        (src / "frontend" / "index.html").write_text("<html></html>\n", encoding="utf-8")
        (src / "sqlchange").mkdir()
        for e in anchor:
            body = (bodies or {}).get(e["filename"], "SELECT 1;\n")
            # newline=""：write_text 默认把 \n 转 CRLF，checksum 按原始字节算会对不上
            (src / "sqlchange" / e["filename"]).write_text(body, encoding="utf-8", newline="")
            e["checksum"] = hashlib.md5(body.encode("utf-8")).hexdigest()
        pkg_root = tmp_path / "store"
        monkeypatch.setattr(release_svc, "store_root", lambda cfg: pkg_root)
        packages.write_package(out_root=pkg_root, slug=slug, version=version, app_root=src,
                               anchor=anchor, manifest_extra={"title": "演示", "stack":
                                   stack, "app_id": 0, "health": {}})
        async with sf() as s:
            rid = (await s.execute(text(
                "INSERT INTO app_release (app_id, slug, version, title, stack, anchor, "
                "fingerprint) VALUES (0, :sl, :v, '演示', :st, CAST(:a AS JSONB), :f) "
                "RETURNING id, app_id, slug, version, title, stack, anchor, fingerprint"),
                {"sl": slug, "v": version, "st": stack, "a": json.dumps(anchor),
                 "f": anchor_mod.fingerprint(anchor)})).mappings().first()
            await s.commit()
        return dict(rid)
    return _mk


@pytest.fixture
def stubs(monkeypatch, sf):
    """把 Docker 侧四件事换成可观察的假人；真 PG 供给不动（它只依赖 pg16 容器）。"""
    calls = {"migrate": [], "build": [], "start": []}

    async def fake_run_sqlchange(app_dir, env, cred, on_log=None):
        calls["migrate"].append((str(app_dir), env, cred["db"]))
        return {"ok": True, "code": 0, "log": "ALL DONE"}

    async def fake_docker(*args, cwd=None, on_line=None, timeout=600):
        calls["build"].append((args, timeout))     # 连 timeout 一起记（钉按栈取超时）
        return 0, "build ok"

    async def fake_prod_start(session_factory, app, notify=None):
        calls["start"].append(app["id"])
        return {"status": "running"}

    async def fake_image_exists(tag):
        return True

    monkeypatch.setattr(sqlrun, "run_sqlchange", fake_run_sqlchange)
    monkeypatch.setattr(deploy_engine, "docker", fake_docker)
    monkeypatch.setattr(deploy_prod, "prod_start", fake_prod_start)
    monkeypatch.setattr(deploy_prod, "image_exists", fake_image_exists)
    return calls


async def test_install_creates_instance_and_link(sf, stubs, release_row):
    from app.store import identity
    actor = await identity.create_actor(sf, "装机人")
    rel = await release_row()
    app = await install.install_release(sf, release_row=rel, applicant_actor_id=actor["id"],
                                        want_name="我的演示")
    try:
        assert app["slug"].startswith(rel["slug"]) and app["slug"] != rel["slug"]
        assert app["origin"] == f"store:{rel['id']}"
        # 铺的是包里的源码，不是栈模板（模板会带 frontend/src/App.vue 与 demo_notes 迁移）
        root = await deploy_engine._app_root(sf, app)
        assert (root / "backend" / "main.py").read_text(encoding="utf-8") == "# hello\n"
        assert (root / "sqlchange" / "V20260920_01_init.sql").is_file()
        assert not any(p.name == "App.vue" for p in root.rglob("*")), "被模板渲染了"
        # 两库都供给了，两库都重放了
        assert sorted(e for _, e, _ in stubs["migrate"]) == ["dev", "prod"]
        async with sf() as s:
            cred_prod = (await s.execute(text(
                "SELECT value FROM app_config WHERE key=:k"),
                {"k": f"dbcred.{app['slug']}.prod"})).first()
            link = (await s.execute(text(
                "SELECT release_id, channel, applied_fingerprint, owner_actor, baseline_hash, "
                "baseline_manifest FROM app_instance_link WHERE app_id=:i"),
                {"i": app["id"]})).mappings().first()
        assert cred_prod and cred_prod[0]["user"] == f"{app['slug']}_prod"
        assert link["channel"] == "managed"
        assert link["applied_fingerprint"] == rel["fingerprint"]
        assert link["owner_actor"] == actor["id"]
        assert link["baseline_hash"] and len(link["baseline_hash"]) == 64
        assert [e["filename"] for e in link["baseline_manifest"]] == \
            [e["filename"] for e in rel["anchor"]]
        # current_version 已晋升，prod_start 才能起栈
        assert app["current_version"] == rel["version"]
        assert stubs["start"] == [app["id"]]
    finally:
        await install._teardown(sf, app["id"], app["slug"])


async def test_install_build_timeout_follows_stack(sf, stubs, release_row):
    """商店安装链的自有镜像 build 也要按栈取超时（与 prod 发布链同族）：
    装一个 vue3-springboot 包，build 调用必须拿到 large 上限（1800），而不是 engine 默认的 600
    ——否则大型栈包安装会复现发布链那个「长构建被误杀成镜像构建失败」的洞。反向钉 fastapi 仍 600。"""
    from app.store import identity
    actor = await identity.create_actor(sf, "装机人")
    big = await release_row(stack="vue3-springboot")
    app = await install.install_release(sf, release_row=big, applicant_actor_id=actor["id"],
                                        want_name="大型实例")
    try:
        assert app["stack"] == "vue3-springboot"
        build_timeouts = [t for _args, t in stubs["build"] if "build" in _args]
        assert build_timeouts == [1800]
    finally:
        await install._teardown(sf, app["id"], app["slug"])
    small = await release_row(stack="vue3-fastapi")
    app2 = await install.install_release(sf, release_row=small, applicant_actor_id=actor["id"],
                                         want_name="小栈实例")
    try:
        build_timeouts2 = [t for _args, t in stubs["build"] if "build" in _args]
        assert build_timeouts2[-1] == 600              # 非大型栈走通用上限
    finally:
        await install._teardown(sf, app2["id"], app2["slug"])


async def test_migrate_failure_rolls_back_everything(sf, monkeypatch, release_row):
    from app.store import identity
    actor = await identity.create_actor(sf, "装机人2")
    rel = await release_row()

    async def boom(app_dir, env, cred, on_log=None):
        return {"ok": False, "code": 2, "log": "FAIL V20260920_01_init.sql"}
    monkeypatch.setattr(sqlrun, "run_sqlchange", boom)
    dropped = []
    real_drop = provision.drop_env

    async def spy_drop(session_factory, slug, env):
        dropped.append(env)
        return await real_drop(session_factory, slug, env)
    monkeypatch.setattr(provision, "drop_env", spy_drop)

    with pytest.raises(DeployError) as e:
        await install.install_release(sf, release_row=rel, applicant_actor_id=actor["id"],
                                      want_name="失败重试")
    assert "迁移" in str(e.value)
    slug = install._last_slug
    assert slug and sorted(dropped) == ["dev", "prod"], "两库都要收回去"
    async with sf() as s:
        assert not (await s.execute(text("SELECT 1 FROM app_project WHERE slug=:s"),
                                    {"s": slug})).first()
        assert not (await s.execute(text("SELECT 1 FROM app_instance_link WHERE app_id IN "
                                          "(SELECT id FROM app_project WHERE slug=:s)"),
                                    {"s": slug})).first()


@pytest.mark.skipif(not sqlrun.docker_available(), reason="需要 Docker 与 zerocode-pg16")
async def test_baseline_replay_real(sf, stubs, monkeypatch, release_row):
    """真跑一次 sqlrunner：空库重放后，实例 prod 库的 history 必须逐字等于锚点。

    只把 run_sqlchange 换回真实实现（本用例验的是数据轴基线，不是镜像/起栈）；
    假包源码里没有可运行的应用，build/start 保持桩掉。
    """
    from app.store import identity
    monkeypatch.setattr(sqlrun, "run_sqlchange", _REAL_RUN_SQLCHANGE)
    actor = await identity.create_actor(sf, "真跑人")
    body = "CREATE TABLE demo_x (id serial PRIMARY KEY, t text);\n"
    rel = await release_row(anchor=[{"filename": "V20260920_01_init.sql", "checksum": ""}],
                            bodies={"V20260920_01_init.sql": body})
    app = await install.install_release(sf, release_row=rel, applicant_actor_id=actor["id"],
                                        want_name="真跑实例")
    try:
        cred = await provision.read_cred(sf, app["slug"], "prod")
        eng = create_async_engine(anchor_mod.app_db_url(cred))
        try:
            async with eng.connect() as c:
                rows = (await c.execute(text(
                    "SELECT filename, success FROM sqlchange_history ORDER BY filename"))).all()
                tbl = (await c.execute(text(
                    "SELECT 1 FROM information_schema.tables WHERE table_name='demo_x'"))).first()
        finally:
            await eng.dispose()
        assert [r[0] for r in rows] == ["V20260920_01_init.sql"] and rows[0][1] is True
        assert tbl, "锚点没真的执行"
    finally:
        await install._teardown(sf, app["id"], app["slug"])
