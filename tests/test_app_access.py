# tests/test_app_access.py
"""判权矩阵（Ticket 000 建骨架，Ticket 011 扩成 owner/被共享者双列）。

本文件是「应用共享」十二张票的判权靶心，逐端点钉住状态码：

- `APP_ROWS`：`(method, path, kwargs, 所有者预期, 被共享者预期)`；陌生人一律 404。
- `_workspace_rows`：同上，靶点换成 workspace_id 与会话/文件。
- 每个测试**先跑被共享者、再跑所有者、最后陌生人**：撤销共享 / 退出这类行会改共享关系，
  协作列的期望必须在关系仍在时取；陌生人 404 不受顺序影响。

刻意**不**进矩阵的表面：
- 列表端点的权限是过滤而不是 404（`GET /api/apps`、`/api/apps/shared`、`/api/workspaces`、
  `/api/sessions/live-status`）：由 `test_app_sharing` 的「共享给我」用例与 `test_ws` 覆盖。
- 附件读取需要真实上传（上传本身仅所有者）：由 `test_app_sharing` 覆盖。
- 商店本体 `/api/store/*`（上架申请/审批/下架）不属于应用判权面；应用侧上架入口在矩阵里。
- 记忆面板在共享范围外（规格 §2.6）：`/api/workspaces/{wid}/memory` 只认 workspace owner，
  被共享者 404——以「不同权即异常」的行放进 workspace 表，锁定它不被共享扩散。
- 删除应用 / 删除工作区会毁靶子：各自单开一例（越权 404 → 所有者删后行消失）。

S1–S20 映射（规格 §3）：
- S1/S2/S13/S15/S18 → `test_app_sharing::test_share_grant_list_and_revoke_roundtrip` /
  `test_share_crud_guards` + 本文件 share 行；
- S3/S4/S12/S17/S19 → `test_app_sharing` 发布申请四例 + 本文件 `test_publish_request_decision_matrix`；
- S5/S16 → 本文件矩阵的 403 列（被共享者可见但无权）；
- S6 → `test_app_sharing::test_share_grant_list_and_revoke_roundtrip`（共享给我列表）；
- S7/S9/S10/S11 → 本文件矩阵行 + `test_db_write_gate_403_for_collaborator` +
  `test_app_sharing` 的 dev/观测/文件/DB/移动端用例；
- S8/S14 → `tests/test_ws.py`（旁观四例）+ `test_app_sharing::test_collaborator_reads_chat_history...`；
- S20 → `test_app_sharing::test_shared_list_owner_nickname_missing_when_app_deleted`。

Docker / 应用 DB 全部替身：本文件验的是判权接线，起栈/发布/查询引擎已有专项测试（DB 只读
真跑 SELECT 在 test_app_sharing，这里只钉状态码分工）。
"""
import pytest
from sqlalchemy import text

import app.api.apps as apps_api
import app.api.files as files_api
import app.api.store as store_api
import app.deploy.engine as eng_mod
import app.deploy.prod as prod_mod
from app.deploy import ports, provision, sqlrun
from app.deploy.errors import DeployError
from app.services import app_service
from app.services import db_admin_service as db_svc

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _stub_docker(monkeypatch):
    """平台启动清理、全部起停/发布引擎、应用 DB 与应用商店收成替身。"""
    async def _noop(*args, **kwargs):
        return None

    async def _docker(*args, **kwargs):
        return 0, ""

    async def _dev_status(sf, app):
        return {"status_dev": "stopped", "compose_ps": "", "url": ""}

    async def _prod_status(sf, app):
        return {"status_prod": "stopped"}

    async def _logs(*args, **kwargs):
        return [], None

    async def _stacks(slug):
        return []

    async def _tables(request, slug, env):
        return [{"name": "widgets"}]

    async def _schema(request, slug, env, table):
        return {"columns": [{"name": "id"}]}

    async def _data(request, slug, env, table, page=1, size=50, sort=None, dirn="asc"):
        return {"rows": [{"id": 1}], "total": 1}

    async def _sql(request, slug, env, sql):
        return {"kind": "rows", "rows": [{"n": 1}]}

    async def _batch(request, slug, env, statements):
        return [{"index": i, "status": "ok"} for i in range(len(statements))]

    async def _create_release(sf, *, app_id, title, summary, actor_id, category=None):
        return {"id": 1, "app_id": app_id, "title": title}

    monkeypatch.setattr(eng_mod, "cleanup_orphans", _noop)
    monkeypatch.setattr(eng_mod, "stop_all_dev_on_exit", _noop)
    monkeypatch.setattr(eng_mod, "preflight_docker", _noop)
    monkeypatch.setattr(eng_mod, "start_dev", _noop)
    monkeypatch.setattr(eng_mod, "stop_dev", _noop)
    monkeypatch.setattr(eng_mod, "restart_dev", _noop)
    monkeypatch.setattr(eng_mod, "dev_status", _dev_status)
    monkeypatch.setattr(eng_mod, "container_logs", _logs)
    monkeypatch.setattr(eng_mod, "docker", _docker)
    monkeypatch.setattr(eng_mod, "down_app_stacks", _stacks)
    monkeypatch.setattr(provision, "ensure_zcnet", _noop)
    monkeypatch.setattr(sqlrun, "build_if_missing", _noop)
    monkeypatch.setattr(prod_mod, "preflight_docker", _noop)
    monkeypatch.setattr(prod_mod, "prod_start", _noop)
    monkeypatch.setattr(prod_mod, "prod_stop", _noop)
    monkeypatch.setattr(prod_mod, "prod_restart", _noop)
    monkeypatch.setattr(prod_mod, "prod_status", _prod_status)
    monkeypatch.setattr(prod_mod, "publish", _noop)
    monkeypatch.setattr(prod_mod, "rollback", _noop)
    monkeypatch.setattr(apps_api, "_run_sqlchange", _noop)
    monkeypatch.setattr(apps_api, "_reset_dev", _noop)
    monkeypatch.setattr(db_svc, "list_tables", _tables)
    monkeypatch.setattr(db_svc, "table_schema", _schema)
    monkeypatch.setattr(db_svc, "table_data", _data)
    monkeypatch.setattr(db_svc, "execute_sql", _sql)
    monkeypatch.setattr(db_svc, "execute_batch", _batch)
    monkeypatch.setattr(db_svc, "log_operation", _noop)
    monkeypatch.setattr(db_svc, "audit_batch", _noop)
    monkeypatch.setattr(store_api.release_svc, "create_release", _create_release)


async def _plant(owner, owner_id: int, collab_id: int, root) -> dict:
    """直插 workspace + app_project + app_share + 会话 + 一条文件变更（判权靶心，
    不烧创建/授权链——共享 CRUD 本身由 test_app_sharing 覆盖）。"""
    sf = owner.app.state.session_factory
    async with sf() as s:
        prod_port, dev_port = await ports.allocate_pair(s)
        wid = (await s.execute(text(
            "INSERT INTO workspace (name, root_path, owner_user_id) "
            "VALUES (:n, :p, :o) RETURNING id"),
            {"n": "矩阵工作区", "p": str(root), "o": owner_id})).scalar()
        aid = (await s.execute(text(
            "INSERT INTO app_project (workspace_id, name, slug, scale, stack, prod_port, "
            "dev_port, owner_user_id) "
            "VALUES (:w, :n, :sl, 'small', 'vue3-fastapi', :pp, :dp, :o) RETURNING id"),
            {"w": wid, "n": "矩阵应用", "sl": "matrixapp", "pp": prod_port, "dp": dev_port,
             "o": owner_id})).scalar()
        await s.execute(text(
            "INSERT INTO app_share (app_id, user_id, created_by) VALUES (:a, :u, :o)"),
            {"a": aid, "u": collab_id, "o": owner_id})
        sid = (await s.execute(text(
            "INSERT INTO chat_session (workspace_id, title) VALUES (:w, '矩阵会话') RETURNING id"),
            {"w": wid})).scalar()
        cid = (await s.execute(text(
            "INSERT INTO file_change (session_id, path, action, diff) "
            "VALUES (:s, 'hello.txt', 'create', '@@ -0,0 +1 @@') RETURNING id"),
            {"s": sid})).scalar()
        await s.commit()
    return {"wid": wid, "aid": aid, "sid": sid, "cid": cid}


@pytest.fixture
async def env(app_, tmp_path, monkeypatch):
    """四账号：创始人 / 所有者 / 陌生人 / 被共享者。

    所有者是第二个注册的**非创始人**——否则指标/DB 两个端点的 owner 列会被 bypass 蒙混过去；
    被共享者最后注册，注册完直插 app_share 共享同一应用。
    """
    from httpx import AsyncClient, ASGITransport

    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path / "data"))
    from tests.conftest import mint_invite

    clients = {}
    for i, name in enumerate(("matrix-founder", "matrix-owner", "matrix-stranger",
                              "matrix-collab")):
        c = AsyncClient(transport=ASGITransport(app=app_), base_url="http://t")
        body = {"nickname": name, "password": "pw-" + name}
        if i:
            # 首位免码自举、其余一律凭码（ADR-0069）。逐枚现签：签码要有签发者，
            # 而签发者要到第一号落地才存在。
            body["invite_code"] = (await mint_invite(app_))[0]
        r = await c.post("/api/auth/register", json=body)
        assert r.status_code == 200, r.text
        c.app = app_
        clients[name] = c
    founder, owner, stranger, collab = (clients["matrix-founder"], clients["matrix-owner"],
                                        clients["matrix-stranger"], clients["matrix-collab"])
    me = {k: (await c.get("/api/auth/me")).json()
          for k, c in (("founder", founder), ("owner", owner),
                       ("stranger", stranger), ("collab", collab))}
    assert me["founder"]["is_founder"] is True and me["owner"]["is_founder"] is False
    assert me["collab"]["is_founder"] is False

    root = tmp_path / "ws"
    root.mkdir()
    (root / "hello.txt").write_text("hello\n", encoding="utf-8")
    planted = await _plant(owner, me["owner"]["id"], me["collab"]["id"], root)

    async def _skip(*args, **kwargs):
        return "skipped"

    for builder in (app_.state.apk_builder, app_.state.mp_builder):
        monkeypatch.setattr(builder, "enqueue", _skip)
    monkeypatch.setattr(files_api, "open_in_file_manager", lambda target: None)

    yield {
        "founder": founder, "owner": owner, "stranger": stranger, "collab": collab,
        "founder_id": me["founder"]["id"], "owner_id": me["owner"]["id"],
        "stranger_id": me["stranger"]["id"], "collab_id": me["collab"]["id"],
        **planted, "root": root,
    }
    for c in clients.values():
        await c.aclose()


async def _drain_tasks(app) -> None:
    """202 受理的后台任务当场收口，避免下一行撞「同应用已有任务」409。"""
    tasks = app.state.dev_tasks
    while tasks:
        _, task = tasks.popitem()
        await task


# (method, path, kwargs, 所有者预期状态, 被共享者预期状态)
# 非移动栈（vue3-fastapi）的 400 证明「已过判权、只差栈校验」；非 SELECT 的库写面
# 走单独一例（403 来自端点内部角色闸，不是 require_owner）。
APP_ROWS = [
    ("GET", "/api/apps/{aid}", {}, 200, 200),
    ("PATCH", "/api/apps/{aid}", {"json": {"name": "矩阵改名"}}, 200, 403),
    ("GET", "/api/apps/{aid}/share", {}, 200, 403),
    # 共享目标用创始人：陌生人列要保持未共享，不能拿它当靶子
    ("POST", "/api/apps/{aid}/share", {"json": {"user_id": "{founder_id}"}}, 201, 403),
    ("POST", "/api/apps/{aid}/share/leave", {}, 404, 200),
    ("DELETE", "/api/apps/{aid}/share/{collab_id}", {}, 200, 403),
    ("POST", "/api/apps/{aid}/publish-requests", {}, 403, 201),
    ("GET", "/api/apps/{aid}/urls", {}, 200, 200),
    ("GET", "/api/apps/{aid}/dev/status", {}, 200, 200),
    ("GET", "/api/apps/{aid}/dev/logs", {}, 200, 200),
    ("POST", "/api/apps/{aid}/dev/start", {}, 202, 202),
    ("POST", "/api/apps/{aid}/dev/stop", {}, 202, 202),
    ("POST", "/api/apps/{aid}/dev/restart", {}, 202, 202),
    ("POST", "/api/apps/{aid}/dev/reset", {}, 202, 403),
    ("GET", "/api/apps/{aid}/metrics/current", {}, 200, 200),
    ("GET", "/api/apps/{aid}/metrics/history?window=1h", {}, 200, 200),
    ("GET", "/api/apps/{aid}/records", {}, 200, 200),
    ("GET", "/api/apps/{aid}/sqlchange/files", {}, 200, 200),
    ("GET", "/api/apps/{aid}/sqlchange/records?env=dev", {}, 200, 200),
    ("POST", "/api/apps/{aid}/sqlchange/apply", {}, 202, 403),
    ("POST", "/api/apps/{aid}/sqlchange/scaffold-backfill", {}, 400, 403),
    ("GET", "/api/apps/{aid}/mobile", {}, 400, 400),
    ("GET", "/api/apps/{aid}/mobile/icon", {}, 400, 400),
    ("POST", "/api/apps/{aid}/mobile/apk/rebuild", {}, 400, 400),
    ("GET", "/api/apps/{aid}/mobile/miniprogram/preview", {}, 400, 400),
    ("PUT", "/api/apps/{aid}/mobile/icon",
     {"files": {"file": ("i.png", b"\x89PNG\r\n\x1a\n", "image/png")}}, 400, 403),
    ("DELETE", "/api/apps/{aid}/mobile/icon", {}, 400, 403),
    ("PUT", "/api/apps/{aid}/mobile/miniprogram/credentials",
     {"data": {"appid": "wx123456"}, "files": {"file": ("key.zip", b"key")}}, 400, 403),
    ("DELETE", "/api/apps/{aid}/mobile/miniprogram/credentials", {}, 400, 403),
    ("POST", "/api/apps/{aid}/mobile/miniprogram/preview", {}, 400, 403),
    ("POST", "/api/apps/{aid}/mobile/miniprogram/upload", {}, 400, 403),
    ("GET", "/api/apps/{aid}/db/audit", {}, 200, 200),
    ("GET", "/api/apps/{aid}/db/dev/tables", {}, 200, 200),
    ("GET", "/api/apps/{aid}/db/dev/tables/widgets/schema", {}, 200, 200),
    ("GET", "/api/apps/{aid}/db/dev/tables/widgets/data", {}, 200, 200),
    ("POST", "/api/apps/{aid}/db/dev/sql", {"json": {"sql": "SELECT 1"}}, 200, 200),
    ("POST", "/api/apps/{aid}/db/dev/sql/batch",
     {"json": {"statements": ["SELECT 1"]}}, 200, 200),
    ("POST", "/api/apps/{aid}/prod/start", {}, 202, 202),
    ("POST", "/api/apps/{aid}/prod/stop", {}, 202, 202),
    ("POST", "/api/apps/{aid}/prod/restart", {}, 202, 202),
    ("GET", "/api/apps/{aid}/prod/status", {}, 200, 200),
    ("POST", "/api/apps/{aid}/publish", {"json": {}}, 202, 403),
    ("POST", "/api/apps/{aid}/rollback",
     {"json": {"version": "b20260101000000"}}, 202, 403),
    # 上架商店仅所有者（release 服务替身；真实闸门在 test_app_sharing 的 FENCED 里钉 403/404）
    ("POST", "/api/apps/{aid}/release",
     {"json": {"title": "矩阵上架", "summary": ""}}, 201, 403),
]


def _render(value, **vals):
    """把行模板里的 {aid}/{founder_id} 等占位符渲染进 path 与嵌套 body（bytes 原样保留）。"""
    if isinstance(value, dict):
        return {k: _render(v, **vals) for k, v in value.items()}
    if isinstance(value, str):
        return value.format(**vals)
    return value


@pytest.mark.parametrize("method,path,kwargs,owner_ok,collab_ok", APP_ROWS)
async def test_app_endpoints_owner_and_collaborator_matrix(
        env, method, path, kwargs, owner_ok, collab_ok):
    """S5/S7/S9/S10/S11/S16：被共享者按行放行或 403，所有者全放行，陌生人一律 404。

    顺序固定为「被共享者 → 所有者 → 陌生人」：撤销共享与退出会改关系，协作列必须
    在共享仍在时取值；陌生人列只看「不暴露存在性」，与顺序无关。
    """
    vals = {"aid": env["aid"], "collab_id": env["collab_id"], "founder_id": env["founder_id"]}
    url = path.format(**vals)
    kwargs = _render(kwargs, **vals)
    r = await env["collab"].request(method, url, **kwargs)
    assert r.status_code == collab_ok, f"collaborator {method} {url} -> {r.status_code} {r.text}"
    if collab_ok == 403:
        assert "仅应用所有者" in r.json()["detail"], (method, url, r.text)
    await _drain_tasks(env["owner"].app)
    r = await env["owner"].request(method, url, **kwargs)
    assert r.status_code == owner_ok, f"owner {method} {url} -> {r.status_code} {r.text}"
    await _drain_tasks(env["owner"].app)
    r2 = await env["stranger"].request(method, url, **kwargs)
    assert r2.status_code == 404, f"stranger {method} {url} -> {r2.status_code} {r2.text}"


def _workspace_rows(env) -> list[tuple[str, str, dict, int, int]]:
    wid, sid, cid = env["wid"], env["sid"], env["cid"]
    q = f"workspace_id={wid}"
    return [
        ("GET", f"/api/fs/tree?{q}", {}, 200, 200),
        ("GET", f"/api/fs/ls?{q}", {}, 200, 200),
        ("GET", f"/api/fs/download?{q}&path=hello.txt", {}, 200, 200),
        ("GET", f"/api/files/read?{q}&path=hello.txt", {}, 200, 200),
        ("PUT", "/api/files/save",
         {"json": {"workspace_id": wid, "path": "hello.txt", "content": "x"}}, 200, 403),
        ("POST", "/api/fs/open-folder", {"json": {"workspace_id": wid}}, 200, 403),
        ("GET", f"/api/sessions?{q}", {}, 200, 200),
        ("POST", "/api/sessions", {"json": {"workspace_id": wid}}, 200, 403),
        ("GET", f"/api/sessions/{sid}", {}, 200, 200),
        ("PATCH", f"/api/sessions/{sid}", {"json": {"title": "矩阵改名"}}, 200, 403),
        ("PATCH", f"/api/sessions/{sid}/model", {"json": {"model_config_id": None}}, 200, 403),
        ("GET", f"/api/sessions/{sid}/messages", {}, 200, 200),
        ("GET", f"/api/sessions/{sid}/file-changes", {}, 200, 200),
        ("GET", f"/api/sessions/{sid}/file-changes/{cid}", {}, 200, 200),
        # 空图片体：400（图片内容为空）证明已过判权；上传本身仅所有者（规格 §2.6）
        ("POST", f"/api/sessions/{sid}/attachments",
         {"content": b"", "headers": {"Content-Type": "image/png"}}, 400, 403),
        # 记忆不进共享范围（§2.6）：被共享者与陌生人同为 404，不因共享放宽
        ("GET", f"/api/workspaces/{wid}/memory", {}, 200, 404),
    ]


async def test_workspace_endpoints_owner_and_collaborator_matrix(env):
    """S7/S8/S9/S16：文件/会话族的双角色口径；记忆面板不随共享放宽。"""
    for method, path, kwargs, owner_ok, collab_ok in _workspace_rows(env):
        r = await env["collab"].request(method, path, **kwargs)
        assert r.status_code == collab_ok, f"collaborator {method} {path} -> {r.status_code} {r.text}"
        if collab_ok == 403:
            assert "仅应用所有者" in r.json()["detail"], (method, path, r.text)
        r = await env["owner"].request(method, path, **kwargs)
        assert r.status_code == owner_ok, f"owner {method} {path} -> {r.status_code} {r.text}"
        r2 = await env["stranger"].request(method, path, **kwargs)
        assert r2.status_code == 404, f"stranger {method} {path} -> {r2.status_code} {r2.text}"


async def test_db_write_gate_403_for_collaborator(env):
    """S10：库写面不是 require_owner，而是端点内的角色闸——单钉一例防漏。

    非 SELECT 单条与「含写语句的混合批次」都整批 403；所有者同一语句照常执行（替身 200）。
    """
    aid = env["aid"]
    r = await env["collab"].post(f"/api/apps/{aid}/db/dev/sql",
                                 json={"sql": "DELETE FROM widgets"})
    assert r.status_code == 403 and "共享只读" in r.json()["detail"]
    r = await env["collab"].post(f"/api/apps/{aid}/db/dev/sql/batch",
                                 json={"statements": ["SELECT 1", "DROP TABLE widgets"]})
    assert r.status_code == 403 and "仅允许 SELECT" in r.json()["detail"]
    r = await env["owner"].post(f"/api/apps/{aid}/db/dev/sql",
                                json={"sql": "DELETE FROM widgets"})
    assert r.status_code == 200, r.text
    r = await env["stranger"].post(f"/api/apps/{aid}/db/dev/sql",
                                   json={"sql": "SELECT 1"})
    assert r.status_code == 404


async def test_publish_request_decision_matrix(env):
    """S4/S12/S18：申请仅协作、审批仅所有者、撤回仅申请人、陌生人一律 404。"""
    aid = env["aid"]
    # 被共享者发起（201），重复申请 409（单待审）
    r = await env["collab"].post(f"/api/apps/{aid}/publish-requests")
    assert r.status_code == 201, r.text
    rid = r.json()["id"]
    assert (await env["collab"].post(f"/api/apps/{aid}/publish-requests")).status_code == 409
    # 审批仅所有者：被共享者 403，陌生人 404
    assert (await env["collab"].post(
        f"/api/apps/{aid}/publish-requests/{rid}/approve", json={})).status_code == 403
    assert (await env["collab"].post(
        f"/api/apps/{aid}/publish-requests/{rid}/reject",
        json={"reason": "不行"})).status_code == 403
    assert (await env["stranger"].post(
        f"/api/apps/{aid}/publish-requests/{rid}/approve", json={})).status_code == 404
    # 撤回仅申请人：所有者 403，被共享者 200
    assert (await env["owner"].delete(
        f"/api/apps/{aid}/publish-requests/{rid}")).status_code == 403
    assert (await env["collab"].delete(
        f"/api/apps/{aid}/publish-requests/{rid}")).status_code == 200
    # 重新申请后所有者批准 → 202，随后申请变 approved（发布本身走发布专项）
    r = await env["collab"].post(f"/api/apps/{aid}/publish-requests")
    assert r.status_code == 201
    rid2 = r.json()["id"]
    r = await env["owner"].post(
        f"/api/apps/{aid}/publish-requests/{rid2}/approve", json={})
    assert r.status_code == 202, r.text
    await _drain_tasks(env["owner"].app)
    async with env["owner"].app.state.session_factory() as s:
        status = (await s.execute(text(
            "SELECT status FROM app_publish_request WHERE id=:i"), {"i": rid2})).scalar()
    assert status == "approved"
    # 已处理的申请不能再驳回/撤回（409），重复批准也 409
    assert (await env["owner"].post(
        f"/api/apps/{aid}/publish-requests/{rid2}/reject",
        json={"reason": "迟了"})).status_code == 409
    assert (await env["collab"].delete(
        f"/api/apps/{aid}/publish-requests/{rid2}")).status_code == 409


async def test_delete_app_is_owner_only(env):
    """删除不在矩阵里（会毁靶子）：越权 404 且行还在，所有者删后行消失。"""
    aid = env["aid"]
    sf = env["owner"].app.state.session_factory
    assert (await env["stranger"].delete(f"/api/apps/{aid}")).status_code == 404
    assert (await env["collab"].delete(f"/api/apps/{aid}")).status_code == 403
    async with sf() as s:
        assert (await s.execute(text("SELECT 1 FROM app_project WHERE id=:i"),
                                {"i": aid})).first()
    snap = await env["owner"].delete(f"/api/apps/{aid}")
    assert snap.status_code == 200 and snap.json()["kept"]["slug"] == "matrixapp"
    async with sf() as s:
        assert (await s.execute(text("SELECT 1 FROM app_project WHERE id=:i"),
                                {"i": aid})).first() is None


async def test_delete_workspace_is_owner_only(env):
    wid = env["wid"]
    assert (await env["stranger"].delete(f"/api/workspaces/{wid}")).status_code == 404
    assert (await env["collab"].delete(f"/api/workspaces/{wid}")).status_code == 403
    assert (await env["owner"].delete(f"/api/workspaces/{wid}")).status_code == 200
    assert (await env["owner"].get(f"/api/sessions?workspace_id={wid}")).status_code == 404


async def test_store_link_gate_matches_app_gate(env):
    """商店单实例详情也走同一判权：陌生人「应用不存在」；被共享者可见但无权 → 403；
    所有者过闸后是「不是商店实例」。"""
    r = await env["stranger"].get(f"/api/store/link/{env['aid']}")
    assert r.status_code == 404 and r.json()["detail"] == "应用不存在"
    r2 = await env["collab"].get(f"/api/store/link/{env['aid']}")
    assert r2.status_code == 403 and "仅应用所有者" in r2.json()["detail"], r2.text
    r3 = await env["owner"].get(f"/api/store/link/{env['aid']}")
    assert r3.status_code == 404 and "不是商店实例" in r3.json()["detail"], r3.text


async def test_seams_unit(env):
    """接缝本体：owner / collaborator / bypass / None / 403 六种形状单点定义。"""
    sf = env["owner"].app.state.session_factory
    app, role = await app_service.get_app_for_user(sf, env["aid"], env["owner_id"])
    assert role == "owner" and app["id"] == env["aid"]
    app_c, role_c = await app_service.get_app_for_user(sf, env["aid"], env["collab_id"])
    assert role_c == "collaborator" and app_c["id"] == env["aid"]
    with pytest.raises(DeployError) as missing:
        await app_service.get_app_for_user(sf, env["aid"], env["stranger_id"])
    assert missing.value.status == 404
    app2, role2 = await app_service.get_app_for_user(sf, env["aid"], None)
    assert role2 == "owner" and app2["slug"] == "matrixapp"
    with pytest.raises(DeployError) as gone:
        await app_service.get_app_for_user(sf, 999999, env["owner_id"])
    assert gone.value.status == 404

    assert await app_service.workspace_role(sf, env["wid"], env["owner_id"]) == "owner"
    assert await app_service.workspace_role(sf, env["wid"], env["collab_id"]) == "collaborator"
    assert await app_service.workspace_role(sf, env["wid"], env["stranger_id"]) is None
    assert await app_service.workspace_role(sf, 999999, env["owner_id"]) is None
    assert await app_service.workspace_role(sf, env["wid"], None) == "owner"

    app_service.require_owner("owner")
    with pytest.raises(DeployError) as denied:
        app_service.require_owner("collaborator")
    assert denied.value.status == 403


async def test_founder_bypass_stays_in_metrics_and_db_admin(env):
    """创始人「看全部」只保留在 metrics / db_admin 两处（现状例外），不扩散到普通应用端点。"""
    founder, aid = env["founder"], env["aid"]
    assert (await founder.get(f"/api/apps/{aid}/metrics/current")).status_code == 200
    assert (await founder.get(f"/api/apps/{aid}/metrics/history?window=1h")).status_code == 200
    assert (await founder.get(f"/api/apps/{aid}/db/audit")).status_code == 200
    assert (await founder.get(f"/api/apps/{aid}")).status_code == 404
    assert (await founder.get(f"/api/apps/{aid}/records")).status_code == 404
