# tests/test_source_seam.py
"""create_app_project 的 source 参数：模板照常渲染 + 快照照常落盘，失败时零残留。

对照 tests/test_store_install_seam.py 的形状写：那条钉「materialize 生效」，
这条钉「source 与 materialize 正交」——两者同时给时，包铺完源还要有快照。

不挂模块级 `pytestmark = pytest.mark.asyncio`：pytest.ini 已 asyncio_mode=auto，
协程与异步夹具自动接管（同门文件 tests/test_source_guard.py /
test_source_hosting.py / test_source_materialize.py 的同一裁定）。

隔离照 tests/test_apps_api.py：固定 SLUG + 前置/后置各 _wipe 一次，dev/prod 库、
app_project/workspace 行、仓库根/apps 下的目录都不留。
"""
import shutil

import pytest
from sqlalchemy import text

from app import config as app_config     # 注意：不可 from app.config import test_database_url，
                                         # 否则该函数会被 pytest 当作测试用例收集（同名 test_* 前缀）
from app.db import make_engine, make_session_factory
from app.deploy.errors import DeployError
from app.deploy.settings import REPO_ROOT
from app.services import app_service
from app.source import materialize as src_mat

SLUG = "zcsrcseam"
APP_DIR = REPO_ROOT / "apps" / SLUG


def _payload():
    m = {"schema": "zc-source/1", "host": "gitee", "owner": "o", "repo": "r", "ref": "master",
         "sourceUrl": "https://gitee.com/o/r", "fetchedAt": "2026-11-17T00:00:00+00:00",
         "subpath": None, "truncated": False,
         "counts": {"listed": 1, "snapshotted": 1, "skipped": 0},
         "files": [{"path": "a.md", "bytes": 2, "title": "A", "dir": ""}], "skipped": []}
    return {"manifest": m, "files": {"a.md": b"#A"}}


async def _wipe():
    eng = make_engine(app_config.test_database_url())
    sf = make_session_factory(eng)
    try:
        from app.deploy import provision
        for env in ("dev", "prod"):
            try:
                await provision.drop_env(sf, SLUG, env)
            except Exception:
                pass
        async with sf() as s:
            await s.execute(text("DELETE FROM app_project WHERE slug=:s"), {"s": SLUG})
            w = (await s.execute(text("SELECT id FROM workspace WHERE root_path LIKE :p"),
                                 {"p": f"%\\{SLUG}"})).scalars().all()
            for wid in w:
                await s.execute(text("DELETE FROM workspace WHERE id=:i"), {"i": wid})
            await s.commit()
        shutil.rmtree(APP_DIR, ignore_errors=True)
    finally:
        await eng.dispose()


@pytest.fixture(autouse=True)
async def _clean(test_engine):
    await _wipe()
    yield
    await _wipe()


async def test_static_app_gets_template_and_snapshot(test_engine):
    sf = make_session_factory(test_engine)
    app = await app_service.create_app_project(
        sf, name="快照应用", slug=SLUG, scale="small", stack="static",
        source=_payload(), origin="repo:gitee", owner_user_id=None)
    try:
        assert (APP_DIR / "frontend" / "package.json").is_file(), "模板没渲染"
        assert (APP_DIR / src_mat.MANIFEST_REL).is_file()
        assert (APP_DIR / "frontend/public/source/files/a.md").read_bytes() == b"#A"
        assert "https://gitee.com/o/r" in (APP_DIR / "README.md").read_text(encoding="utf-8")
        assert not (APP_DIR / "sqlchange").exists(), "static 不该被铺数据轨"
        meta = app_service.load_app_json(APP_DIR)
        assert meta["stack"] == "static" and meta["slug"] == SLUG
        async with sf() as s:
            got = (await s.execute(text("SELECT origin FROM app_project WHERE id=:i"),
                                   {"i": app["id"]})).scalar()
        assert got == "repo:gitee"
    finally:
        await _wipe()


async def test_source_composes_on_top_of_store_materialize(test_engine):
    """两者同时给：商店包铺完源还要有快照（正交，不是互斥）。"""
    sf = make_session_factory(test_engine)
    seen = {}

    def materialize(root, mapping):
        root.mkdir(parents=True, exist_ok=True)
        seen["called"] = True
        (root / "MARKER").write_text("包铺的", encoding="utf-8")

    app = await app_service.create_app_project(
        sf, name="正交应用", slug=SLUG, scale="small", stack="static",
        origin="store:7", materialize=materialize, source=_payload())
    try:
        assert seen.get("called") is True
        assert (APP_DIR / "MARKER").is_file()
        assert (APP_DIR / src_mat.MANIFEST_REL).is_file(), "source 被 materialize 顶掉了"
    finally:
        await _wipe()


async def test_snapshot_failure_rolls_back_and_writes_nothing(test_engine):
    sf = make_session_factory(test_engine)
    bad = _payload()
    bad["files"]["../../etc/x.md"] = b"boom"
    # `match=` 是必须的，不是装饰（终审 F3）：`create_app_project` 的同一个 try 里今天还有
    # **第二条毫不相干的** DeployError（`数据库供给结果不完整`，status=500，同样回滚）。
    # 只判 `pytest.raises(DeployError)` 的用例可以在那一条上变绿 —— 快照这一步压根没跑，
    # 「坏快照被回滚」这件事就成了一句没有断言的绿。故把匹配钉在快照失败的文本上：
    # 外层包装（创建应用失败: …）+ 真因（快照路径越界）两段都要出现才算数。
    with pytest.raises(DeployError, match=r"创建应用失败: 快照路径越界"):
        await app_service.create_app_project(
            sf, name="坏快照", slug=SLUG, scale="small", stack="static", source=bad)
    assert not APP_DIR.exists(), "失败后留下了半个工作区"
    async with sf() as s:
        assert (await s.execute(text("SELECT count(*) FROM app_project WHERE slug=:s"),
                                {"s": SLUG})).scalar() == 0, "失败后留下了 app 行"


async def test_no_source_is_byte_identical_to_today(test_engine):
    """回归闸：不传 source 时行为不许变。"""
    sf = make_session_factory(test_engine)
    app = await app_service.create_app_project(
        sf, name="无快照", slug=SLUG, scale="small", stack="static")
    try:
        assert not (APP_DIR / "frontend" / "public").exists()
        assert "仓库源码快照" not in (APP_DIR / "README.md").read_text(encoding="utf-8")
    finally:
        await _wipe()
