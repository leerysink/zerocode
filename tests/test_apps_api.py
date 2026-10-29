# tests/test_apps_api.py
"""Task 6：/api/apps REST（创建 / 校验 slug / 列表 / 详情）。

REST 层只做参数解析 + DeployError.status → HTTP 状态码映射，业务一律在 app_service（裁定 R-薄 REST）。
控制者裁定覆盖：
  D-1 重复 slug 经 POST → HTTP 409（不是 500）；
  D-2 GET /api/apps/{不存在 id} → HTTP 404；
  D-3 非法入参（缺 name / 坏 slug / 坏 scale）→ 4xx，validate-slug 返回 {ok, suggest?}。

与 brief 的唯一偏差：不重复定义 client fixture，改用 conftest 的 client（→ zerocode_test 测试库）。
理由：brief 的 Global Constraints 第 13 条要求测试库为 zerocode_test，而 brief 自带的局部 fixture
连的是开发库 zerocode；台账 Task 5 遗留项 m1 也裁定「M2 统一改 test_engine」。此处提前落实，
避免真实创建链把 app_project/workspace 行与 dbcred.* 凭据写进开发者在用的库。
"""
import shutil
import pytest
from sqlalchemy import text
from app import config as app_config     # 注意：不可 from app.config import test_database_url，
                                         # 否则该函数会被 pytest 当作测试用例收集（同名 test_* 前缀）
from app.db import make_engine, make_session_factory
from app.deploy import provision
from app.deploy.settings import REPO_ROOT

pytestmark = pytest.mark.asyncio
SLUG = "zctask6"
ROOT = None  # 用平台默认 apps_root/<slug>（brief 指定）
APP_DIR = REPO_ROOT / "apps" / SLUG   # 默认 apps_root 即仓库根/apps

BODY = {"name": "API 测试应用", "slug": SLUG, "scale": "small", "stack": "vue3-fastapi"}


async def _wipe():
    """清干净本用例的全部产物：dev 库/角色/dbcred 凭据 + app_project/workspace 行 + 应用目录（仿 Task 5）。"""
    eng = make_engine(app_config.test_database_url())
    sf = make_session_factory(eng)
    try:
        try:
            await provision.drop_env(sf, SLUG, "dev")      # 库 + 角色 + app_config 的 dbcred.* 键
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
    await _wipe()          # 前置清一次：上一次异常中断留下的残骸不能让本次测试背锅
    yield
    await _wipe()


async def test_validate_slug(client):
    r = await client.post("/api/apps/validate-slug", json={"slug": "Bad Slug"})
    assert r.status_code == 200 and r.json()["ok"] is False and r.json()["suggest"]
    r = await client.post("/api/apps/validate-slug", json={"slug": SLUG})
    assert r.json()["ok"] is True


async def test_create_and_list(client):
    r = await client.post("/api/apps", json={
        "name": "API 测试应用", "slug": SLUG, "scale": "small", "stack": "vue3-fastapi"})
    assert r.status_code == 201, r.text
    app = r.json()
    assert app["slug"] == SLUG and app["status_dev"] == "stopped"
    r2 = await client.get("/api/apps")
    assert any(a["slug"] == SLUG for a in r2.json())
    r3 = await client.get(f"/api/apps/{app['id']}")
    assert r3.json()["name"] == "API 测试应用"
    r4 = await client.get("/api/apps/999999")
    assert r4.status_code == 404                       # D-2：不存在的 id → 404（非 500）


async def _row_by_slug(client, slug):
    rows = [a for a in (await client.get("/api/apps")).json() if a["slug"] == slug]
    assert len(rows) == 1, rows
    return rows[0]


async def test_list_and_detail_carry_preview_url(client, monkeypatch):
    """预览链接由后端按 `ZEROCODE_PREVIEW_HOST` 生成并随列表/详情回表：

    前端此前硬拼 `http://localhost:${dev_port}`，远程部署时那个地址指向访问者自己的机器。
    """
    monkeypatch.setenv("ZEROCODE_PREVIEW_HOST", "10.0.1.25")
    assert (await client.post("/api/apps", json=BODY)).status_code == 201
    row = await _row_by_slug(client, SLUG)
    assert row["preview_url"] == f"http://10.0.1.25:{row['dev_port']}"
    detail = (await client.get(f"/api/apps/{row['id']}")).json()
    assert detail["preview_url"] == row["preview_url"]


async def test_list_preview_url_defaults_to_localhost(client, monkeypatch):
    """没配基址时仍是 localhost（单机部署的存量行为一字不动）。"""
    monkeypatch.delenv("ZEROCODE_PREVIEW_HOST", raising=False)
    assert (await client.post("/api/apps", json=BODY)).status_code == 201
    row = await _row_by_slug(client, SLUG)
    assert row["preview_url"] == f"http://localhost:{row['dev_port']}"


async def test_list_and_detail_carry_prod_url(client, monkeypatch):
    """正式地址与预览地址同批回表（聊天页「打开生产」）。

    为什么不复用 `GET /apps/{id}/urls` 让前端点击时再拉：`window.open` 落在 await 之后就出了
    用户手势窗口，会被弹窗拦截器静默吞掉。地址口径本身仍只在 `nics.app_url` 一处投影（ADR-0016），
    这里只是把那一份带进行里，不另立第二套拼法。
    """
    from app.deploy import nics
    monkeypatch.delenv("ZEROCODE_SSL_ENABLE", raising=False)
    assert (await client.post("/api/apps", json=BODY)).status_code == 201
    row = await _row_by_slug(client, SLUG)
    assert row["prod_url"] == f"https://{SLUG}.{nics.GATEWAY_DOMAIN}"
    detail = (await client.get(f"/api/apps/{row['id']}")).json()
    assert detail["prod_url"] == row["prod_url"]


async def test_prod_url_uses_host_and_prod_port_when_ssl_off(client, monkeypatch):
    """SSL 关（域名未备案期的活路）：正式地址退到 `http://{基址}:{prod_port}`，与预览各用各的端口。"""
    monkeypatch.setenv("ZEROCODE_SSL_ENABLE", "false")
    monkeypatch.setenv("ZEROCODE_PREVIEW_HOST", "10.0.1.25")
    assert (await client.post("/api/apps", json=BODY)).status_code == 201
    row = await _row_by_slug(client, SLUG)
    assert row["prod_url"] == f"http://10.0.1.25:{row['prod_port']}"
    assert row["prod_url"] != row["preview_url"]


async def test_create_conflict_and_bad_request(client):
    body = {"name": "x", "slug": SLUG, "scale": "small", "stack": "vue3-fastapi"}
    assert (await client.post("/api/apps", json=body)).status_code == 201
    assert (await client.post("/api/apps", json=body)).status_code == 409   # D-1：重复 slug → 409
    bad = dict(body, slug=SLUG, scale="middle")
    assert (await client.post("/api/apps", json=bad)).status_code == 400    # D-3：坏 scale → 400


async def test_bad_input_is_4xx_not_5xx(client):
    """D-3：缺 name / 空 name / 坏 slug 一律 4xx，且校验不过时不得留下任何产物。"""
    no_name = {k: v for k, v in BODY.items() if k != "name"}
    r = await client.post("/api/apps", json=no_name)
    assert 400 <= r.status_code < 500, r.text          # schema 缺字段 → 422（仍是 4xx）
    assert not APP_DIR.exists()
    r = await client.post("/api/apps", json=dict(BODY, name="   "))
    assert r.status_code == 400, r.text                # 空 name → app_service 400
    assert not APP_DIR.exists()
    r = await client.post("/api/apps", json=dict(BODY, slug="Bad Slug"))
    assert r.status_code == 400, r.text                # 坏 slug → validate_slug 400
    assert not APP_DIR.exists()
    assert (await client.get("/api/apps")).json() == []


async def test_validate_slug_taken(client):
    """slug 规则合法但已被占用 → ok=False 并给出可用建议（validate-slug 的 taken 分支）。"""
    assert (await client.post("/api/apps", json=BODY)).status_code == 201
    r = await client.post("/api/apps/validate-slug", json={"slug": SLUG})
    assert r.status_code == 200 and r.json()["ok"] is False
    sug = r.json()["suggest"]
    assert sug and sug != SLUG
    r2 = await client.post("/api/apps/validate-slug", json={"slug": sug})
    assert r2.json()["ok"] is True
