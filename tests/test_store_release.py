# tests/test_store_release.py
"""上架闸门（规格 §6.1）：锚点只取 prod history，五道闸任一不过即拒绝且不产生包目录。"""
import secrets
import shutil
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from app.deploy import prod as deploy_prod
from app.deploy import provision
from app.deploy.errors import DeployError
from app.db import make_session_factory
from app.services import app_service
from app.store import anchor, packages, release


def _slug():
    return "rel" + secrets.token_hex(3)


def _materialize(sql_files: dict[str, str]):
    def mz(root, mapping):
        (root / "backend").mkdir(parents=True, exist_ok=True)
        (root / "backend" / "main.py").write_text("# app\n", encoding="utf-8", newline="")
        d = root / "sqlchange"
        d.mkdir(parents=True, exist_ok=True)
        for name, body in sql_files.items():
            (d / name).write_text(body, encoding="utf-8", newline="")
    return mz


async def _make_app(sf, *, sql_files, publish_ok=True, version="b20260922000001"):
    """造一个「已发布过 prod」的应用：app_project.current_version + publish success 记录。"""
    slug = _slug()
    app = await app_service.create_app_project(
        sf, name="上架测试", slug=slug, scale="small", stack="vue3-fastapi",
        materialize=_materialize(sql_files))
    _APPS.append(app)
    cred = await provision.provision_env(sf, slug, "prod")
    url = anchor.app_db_url(cred)
    eng = create_async_engine(url)
    try:
        async with eng.begin() as c:
            await c.execute(text(
                "CREATE TABLE IF NOT EXISTS sqlchange_history (filename VARCHAR(200) PRIMARY KEY,"
                " checksum VARCHAR(32), success BOOLEAN NOT NULL DEFAULT true)"))
            for name, body in sql_files.items():
                await c.execute(text("INSERT INTO sqlchange_history (filename, checksum) "
                                     "VALUES (:f, :s) ON CONFLICT DO NOTHING"),
                                {"f": name, "s": _md5(body)})
    finally:
        await eng.dispose()
    async with sf() as s:
        await s.execute(text("UPDATE app_project SET current_version=:v WHERE id=:i"),
                        {"v": version, "i": app["id"]})
        if publish_ok:
            await s.execute(text("INSERT INTO deploy_record (app_id, kind, version, status) "
                                 "VALUES (:a, 'publish', :v, 'success')"),
                            {"a": app["id"], "v": version})
        await s.commit()
    return app


def _md5(body: str) -> str:
    import hashlib
    return hashlib.md5(body.encode("utf-8")).hexdigest()


# 本文件用例经 _make_app 在共享 PG/磁盘上造真产物（两库 + apps/<slug> 目录），
# 用例结束必须自己扫干净（与 tests/test_provision.py 现有纪律一致）。
_APPS: list[dict] = []


@pytest.fixture(autouse=True)
async def _sweep(sf):
    yield
    while _APPS:
        app = _APPS.pop()
        for env in ("dev", "prod"):
            try:
                await provision.drop_env(sf, app["slug"], env)
            except Exception:
                pass
        try:
            root = await release._app_root(sf, app)
            shutil.rmtree(root, ignore_errors=True)
        except Exception:
            pass
        try:
            await app_service.delete_app(sf, app["id"])
        except Exception:
            pass


@pytest.fixture
async def sf(test_engine):
    return make_session_factory(test_engine)


@pytest.fixture
def store_root(tmp_path, monkeypatch):
    async def _cfg(session_factory):
        return {"store.root": str(tmp_path / "store")}
    monkeypatch.setattr(release, "deploy_cfg", _cfg)
    return tmp_path / "store"


@pytest.fixture
def images_ok(monkeypatch):
    async def _yes(tag):
        return True
    monkeypatch.setattr(deploy_prod, "image_exists", _yes)


@pytest.fixture
def actor(sf):
    from app.store import identity
    async def _mk():
        return (await identity.create_actor(sf, "发布者"))["id"]
    return _mk


async def _do_release(sf, app_id, actor_id, *, title="演示应用", summary="简介"):
    return await release.create_release(sf, app_id=app_id, title=title, summary=summary,
                                        actor_id=actor_id)


async def test_happy_path_writes_package_and_row(sf, store_root, images_ok, actor):
    files = {"V20260920_01_init.sql": "CREATE TABLE a(id int);\n",
             "V20260920_02_b.sql": "ALTER TABLE a ADD c text;\n"}
    app = await _make_app(sf, sql_files=files)
    rel = await _do_release(sf, app["id"], await actor())
    assert [e["filename"] for e in rel["anchor"]] == sorted(files)   # 锚点=有序成功集
    pkg = packages.package_path(store_root, app["slug"], rel["version"])
    assert (pkg / "manifest.json").is_file()
    assert (pkg / "sqlchange" / "V20260920_01_init.sql").is_file()
    async with sf() as s:
        n = (await s.execute(text("SELECT count(*) FROM deploy_record WHERE app_id=:a "
                                  "AND kind='release' AND status='success'"),
                             {"a": app["id"]})).scalar()
    assert n == 1


async def test_dev_only_sql_not_in_anchor(sf, store_root, images_ok, actor):
    """目录里多一个从未 prod 应用过的文件 → 不进包（锚点取自 history，不取自目录）。"""
    files = {"V20260920_01_init.sql": "SELECT 1;\n"}
    app = await _make_app(sf, sql_files=files)
    root = await release._app_root(sf, app)
    (root / "sqlchange" / "V20260921_09_halfway.sql").write_text("DROP TABLE a;\n",
                                                                encoding="utf-8")
    rel = await _do_release(sf, app["id"], await actor())
    assert [e["filename"] for e in rel["anchor"]] == ["V20260920_01_init.sql"]
    pkg = packages.package_path(store_root, app["slug"], rel["version"])
    assert not (pkg / "sqlchange" / "V20260921_09_halfway.sql").exists()


async def test_edited_history_file_rejected(sf, store_root, images_ok, actor):
    files = {"V20260920_01_init.sql": "SELECT 1;\n"}
    app = await _make_app(sf, sql_files=files)
    root = await release._app_root(sf, app)
    (root / "sqlchange" / "V20260920_01_init.sql").write_text("SELECT 9;\n", encoding="utf-8")
    with pytest.raises(DeployError) as e:
        await _do_release(sf, app["id"], await actor())
    assert "改了历史" in str(e.value) or "不一致" in str(e.value)


async def test_never_published_rejected(sf, store_root, images_ok, actor):
    app = await _make_app(sf, sql_files={"V20260920_01_init.sql": "SELECT 1;\n"},
                          publish_ok=False)
    with pytest.raises(DeployError):
        await _do_release(sf, app["id"], await actor())


async def test_missing_image_rejected(sf, store_root, monkeypatch, actor):
    async def _no(tag):
        return False
    monkeypatch.setattr(deploy_prod, "image_exists", _no)
    app = await _make_app(sf, sql_files={"V20260920_01_init.sql": "SELECT 1;\n"})
    with pytest.raises(DeployError):
        await _do_release(sf, app["id"], await actor())


async def test_prefix_violation_rejected(sf, store_root, images_ok, actor):
    """第二个版本删掉了第一个版本的文件 → 违反约束 L，拒上架。"""
    app = await _make_app(sf, sql_files={"V20260920_01_init.sql": "SELECT 1;\n",
                                         "V20260920_02_b.sql": "SELECT 2;\n"})
    await _do_release(sf, app["id"], await actor())
    root = await release._app_root(sf, app)
    # 作者把已上架的第二个文件从 prod history 里手工清掉（模拟分叉）
    cred = await provision.read_cred(sf, app["slug"], "prod")
    eng = create_async_engine(anchor.app_db_url(cred))
    async with eng.begin() as c:
        await c.execute(text("DELETE FROM sqlchange_history WHERE filename=:f"),
                        {"f": "V20260920_02_b.sql"})
    await eng.dispose()
    (root / "sqlchange" / "V20260920_02_b.sql").unlink()
    async with sf() as s:   # 换一个新版本号，绕开 slug+version 唯一约束
        await s.execute(text("UPDATE app_project SET current_version='b20260923000001' "
                             "WHERE id=:i"), {"i": app["id"]})
        await s.execute(text("INSERT INTO deploy_record (app_id, kind, version, status) "
                             "VALUES (:a, 'publish', 'b20260923000001', 'success')"),
                        {"a": app["id"]})
        await s.commit()
    with pytest.raises(DeployError) as e:
        await _do_release(sf, app["id"], await actor())
    assert "前缀" in str(e.value) or "分叉" in str(e.value)


async def test_empty_anchor_rejected(sf, store_root, images_ok, actor):
    app = await _make_app(sf, sql_files={})
    with pytest.raises(DeployError):
        await _do_release(sf, app["id"], await actor())
