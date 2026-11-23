# tests/test_app_sharing.py
"""共享授权与角色权限矩阵（规格 2026-11-16 §5.1/§5.3/§5.6）。

本文件靶心：
- 共享 CRUD 与状态码口径：自共享 400 / 重复共享 409 / 未知用户 404 / 未共享 404；
- 被共享者经 `get_app_for_user` 拿到 collaborator——自有列表不混排、共享给我带
  owner_nickname 与 role、详情带 role；
- 预览/观测面（Ticket 003）对被共享者放行；尚未开放的面（prod、移动端、DB、发布、删除等）
  一律 403（后续票逐条放行，放行时把对应行从 FENCED 移出并补正向断言）。

Docker 全部替身（与 test_app_access 同款）：本文件验判权与状态码，不碰真栈。
"""
import asyncio

import pytest
from sqlalchemy import text

import app.api.apps as apps_api
import app.deploy.engine as eng_mod
import app.deploy.mpruntime as mpruntime_mod
import app.deploy.prod as prod_mod
from app.deploy import ports, provision, sqlrun

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _stub_docker(monkeypatch):
    """平台启动清理与全部起停/发布引擎收成替身（Docker 不在靶内）。"""
    async def _noop(*args, **kwargs):
        return None

    async def _docker(*args, **kwargs):
        return 0, ""

    async def _stacks(slug):
        return []

    async def _prod_status(*args, **kwargs):
        return {"status_prod": "stopped", "compose_ps": "", "domain": ""}

    async def _egress(*args, **kwargs):
        return ""

    monkeypatch.setattr(eng_mod, "cleanup_orphans", _noop)
    monkeypatch.setattr(eng_mod, "stop_all_dev_on_exit", _noop)
    monkeypatch.setattr(eng_mod, "preflight_docker", _noop)
    monkeypatch.setattr(eng_mod, "start_dev", _noop)
    monkeypatch.setattr(eng_mod, "stop_dev", _noop)
    monkeypatch.setattr(eng_mod, "restart_dev", _noop)
    monkeypatch.setattr(eng_mod, "docker", _docker)
    monkeypatch.setattr(eng_mod, "down_app_stacks", _stacks)
    monkeypatch.setattr(provision, "ensure_zcnet", _noop)
    monkeypatch.setattr(sqlrun, "build_if_missing", _noop)
    monkeypatch.setattr(prod_mod, "preflight_docker", _noop)
    monkeypatch.setattr(prod_mod, "publish", _noop)
    monkeypatch.setattr(prod_mod, "rollback", _noop)
    monkeypatch.setattr(prod_mod, "prod_start", _noop)
    monkeypatch.setattr(prod_mod, "prod_stop", _noop)
    monkeypatch.setattr(prod_mod, "prod_restart", _noop)
    monkeypatch.setattr(prod_mod, "prod_status", _prod_status)
    # /mobile 的 readiness 会探测公网出口 IP（10s 超时）：与 Docker 同理收成替身
    monkeypatch.setattr(mpruntime_mod, "egress_ip", _egress)
    monkeypatch.setattr(apps_api, "_run_sqlchange", _noop)
    monkeypatch.setattr(apps_api, "_reset_dev", _noop)


async def _plant(owner, owner_id: int, root, *, slug="shareapp", stack="vue3-fastapi",
                 scale="small") -> int:
    """直插 workspace + app_project（判权靶心，不烧创建链）。"""
    sf = owner.app.state.session_factory
    async with sf() as s:
        prod_port, dev_port = await ports.allocate_pair(s)
        wid = (await s.execute(text(
            "INSERT INTO workspace (name, root_path, owner_user_id) "
            "VALUES (:n, :p, :o) RETURNING id"),
            {"n": "共享工作区", "p": str(root), "o": owner_id})).scalar()
        aid = (await s.execute(text(
            "INSERT INTO app_project (workspace_id, name, slug, scale, stack, prod_port, "
            "dev_port, owner_user_id) "
            "VALUES (:w, :n, :sl, :sc, :st, :pp, :dp, :o) RETURNING id"),
            {"w": wid, "n": "共享应用", "sl": slug, "sc": scale, "st": stack,
             "pp": prod_port, "dp": dev_port, "o": owner_id})).scalar()
        await s.commit()
    return aid


@pytest.fixture
async def env(app_, tmp_path, monkeypatch):
    """三账号：创始人 / 所有者（非创始人） / 被共享者。

    「陌生人」用创始人充当：创始人只有指标与 DB 域的既有 bypass，详情/共享面仍走
    同一条 404——这同时钉住「bypass 没有被共享改动扩散」。
    """
    from httpx import AsyncClient, ASGITransport

    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path / "data"))
    from tests.conftest import mint_invite

    clients = {}
    for i, name in enumerate(("share-founder", "share-owner", "share-collab")):
        c = AsyncClient(transport=ASGITransport(app=app_), base_url="http://t")
        body = {"nickname": name, "password": "pw-" + name}
        if i:
            # 首位免码自举、其余凭码（ADR-0069）：逐枚现签，签发者要等第一号落地才存在
            body["invite_code"] = (await mint_invite(app_))[0]
        r = await c.post("/api/auth/register", json=body)
        assert r.status_code == 200, r.text
        c.app = app_
        clients[name] = c
    founder, owner, collab = (clients["share-founder"], clients["share-owner"],
                              clients["share-collab"])
    me = {k: (await c.get("/api/auth/me")).json()
          for k, c in (("founder", founder), ("owner", owner), ("collab", collab))}
    assert me["founder"]["is_founder"] is True and me["owner"]["is_founder"] is False

    root = tmp_path / "ws"
    root.mkdir()
    aid = await _plant(owner, me["owner"]["id"], root)

    async def _skip(*args, **kwargs):
        return "skipped"

    for builder in (app_.state.apk_builder, app_.state.mp_builder):
        monkeypatch.setattr(builder, "enqueue", _skip)

    yield {
        "app": app_, "founder": founder, "owner": owner, "collab": collab,
        "founder_id": me["founder"]["id"], "owner_id": me["owner"]["id"],
        "collab_id": me["collab"]["id"], "aid": aid, "root": root,
    }
    for c in clients.values():
        await c.aclose()


async def _share(env) -> None:
    r = await env["owner"].post(f"/api/apps/{env['aid']}/share",
                                json={"user_id": env["collab_id"]})
    assert r.status_code == 201, r.text


async def test_share_grant_list_and_revoke_roundtrip(env):
    """S1/S2/S6：共享 → 出现在「共享给我」→ 撤销 → 消失；自有列表不混排。"""
    owner, collab, aid = env["owner"], env["collab"], env["aid"]

    own = (await owner.get("/api/apps")).json()
    assert [a["id"] for a in own] == [aid]
    assert own[0]["role"] == "owner" and own[0]["pending_publish"] is False
    assert (await collab.get("/api/apps")).json() == []
    assert (await collab.get("/api/apps/shared")).json() == []
    assert (await owner.get(f"/api/apps/{aid}/share")).json() == {
        "members": [], "pending_request": None}

    await _share(env)

    shared = (await collab.get("/api/apps/shared")).json()
    assert len(shared) == 1
    item = shared[0]
    assert item["id"] == aid and item["role"] == "collaborator"
    assert item["owner_nickname"] == "share-owner"
    assert item["pending_publish"] is False and item["publish_request"] is None
    assert item["preview_url"] and "prod_url" in item          # 与 list_apps 同形状
    assert (await collab.get("/api/apps")).json() == []        # 不混进自有列表

    detail = (await collab.get(f"/api/apps/{aid}")).json()
    assert detail["role"] == "collaborator"
    assert (await owner.get(f"/api/apps/{aid}")).json()["role"] == "owner"

    members = (await owner.get(f"/api/apps/{aid}/share")).json()["members"]
    assert [(m["user_id"], m["nickname"]) for m in members] == [
        (env["collab_id"], "share-collab")]

    r = await owner.delete(f"/api/apps/{aid}/share/{env['collab_id']}")
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert (await collab.get("/api/apps/shared")).json() == []
    assert (await collab.get(f"/api/apps/{aid}")).status_code == 404


async def test_share_crud_guards(env):
    """S1/S13：自共享 400、重复 409、未知用户 404、未共享 404、owner 自退 404、
    被共享者自退后立即失效；共享管理端点对被共享者 403。"""
    owner, collab, aid = env["owner"], env["collab"], env["aid"]

    r = await owner.post(f"/api/apps/{aid}/share", json={"user_id": env["owner_id"]})
    assert r.status_code == 400 and "自己" in r.json()["detail"]
    r = await owner.post(f"/api/apps/{aid}/share", json={"user_id": 999999})
    assert r.status_code == 404 and "用户不存在" in r.json()["detail"]
    r = await owner.delete(f"/api/apps/{aid}/share/{env['collab_id']}")
    assert r.status_code == 404                              # 未共享
    r = await owner.post(f"/api/apps/{aid}/share/leave")
    assert r.status_code == 404                              # 所有者没有共享行

    await _share(env)
    r = await owner.post(f"/api/apps/{aid}/share", json={"user_id": env["collab_id"]})
    assert r.status_code == 409                              # 重复共享

    # S5：被共享者调任何共享管理端点 → 403
    assert (await collab.get(f"/api/apps/{aid}/share")).status_code == 403
    assert (await collab.post(f"/api/apps/{aid}/share",
                              json={"user_id": env["collab_id"]})).status_code == 403
    assert (await collab.delete(f"/api/apps/{aid}/share/{env['collab_id']}")).status_code == 403

    # S13：主动退出 → 立即失效；再退 404
    r = await collab.post(f"/api/apps/{aid}/share/leave")
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert (await collab.get(f"/api/apps/{aid}")).status_code == 404
    assert (await collab.post(f"/api/apps/{aid}/share/leave")).status_code == 404
    assert (await owner.get(f"/api/apps/{aid}/share")).json()["members"] == []


# 尚未开放的应用面（后续票逐条移出）：(method, path, kwargs)
# 指标 current/history 与 db/* 不在此列：创始人对它们有既有 bypass（200），
# 不在「陌生人 404」的断言形状里；被共享者的口径由专门的用例覆盖。
FENCED = [
    ("post", "/publish", {"json": {"force": True}}),
    ("post", "/rollback", {"json": {"version": "b20260920123456"}}),
    ("post", "/dev/reset", None),
    ("post", "/sqlchange/apply", None),
    ("post", "/sqlchange/scaffold-backfill", None),
    ("delete", "/records/1", None),
    ("post", "/release", {"json": {"title": "x", "summary": ""}}),
    ("patch", "", {"json": {"name": "改名"}}),
    ("delete", "", None),
    # 移动端写面（Ticket 004）：图标覆盖与小程序凭据/生成/上传仍仅所有者
    ("put", "/mobile/icon", {"files": {"file": ("i.png", b"\x89PNG\r\n\x1a\n", "image/png")}}),
    ("delete", "/mobile/icon", None),
    ("put", "/mobile/miniprogram/credentials",
     {"data": {"appid": "wx123456"}, "files": {"file": ("key.zip", b"key")}}),
    ("delete", "/mobile/miniprogram/credentials", None),
    ("post", "/mobile/miniprogram/preview", None),
    ("post", "/mobile/miniprogram/upload", None),
]


@pytest.mark.parametrize("method, suffix, kwargs", FENCED,
                         ids=[f"{m} {s or '/'}" for m, s, _ in FENCED])
async def test_fenced_app_endpoints_403_for_collaborator_404_for_stranger(env, method, suffix, kwargs):
    """S15/S16（本票口径）：被共享者看得见应用，但未放行的动作 403；陌生人仍 404。

    403 是「已可见但无权」的诚实口径（规格 §5.6）；404 继续藏存在性。
    """
    await _share(env)
    aid = env["aid"]
    r = await getattr(env["collab"], method)(f"/api/apps/{aid}{suffix}", **(kwargs or {}))
    assert r.status_code == 403, (method, suffix, r.text)
    assert "仅应用所有者" in r.json()["detail"]
    r = await getattr(env["founder"], method)(f"/api/apps/{aid}{suffix}", **(kwargs or {}))
    assert r.status_code == 404, (method, suffix, r.text)


async def _drain_dev_task(app, aid: int) -> None:
    """等后台替身跑完再下一步：_spawn 的同应用互斥会按「还在跑」409，done 回调经
    call_soon 排队——让 loop 转一圈，句柄才真正出表。"""
    task = app.state.dev_tasks.get(aid)
    if task is not None:
        await task
    await asyncio.sleep(0)


async def test_collaborator_can_run_and_observe_dev(env):
    """S7/S9：被共享者能起停/重启 dev、读状态/地址/预览日志、发布历史、
    迁移清单/记账与指标；summary 显式并集把共享应用算进来。"""
    await _share(env)
    collab, app, aid = env["collab"], env["app"], env["aid"]

    assert (await collab.get(f"/api/apps/{aid}/urls")).status_code == 200
    st = await collab.get(f"/api/apps/{aid}/dev/status")
    assert st.status_code == 200 and "status_dev" in st.json()
    lg = await collab.get(f"/api/apps/{aid}/dev/logs")
    assert lg.status_code == 200 and "lines" in lg.json()

    for action in ("start", "stop", "restart"):
        r = await collab.post(f"/api/apps/{aid}/dev/{action}")
        assert r.status_code == 202, (action, r.text)
        await _drain_dev_task(app, aid)

    assert (await collab.get(f"/api/apps/{aid}/records")).json() == []
    assert (await collab.get(f"/api/apps/{aid}/sqlchange/files")).json() == []
    assert (await collab.get(f"/api/apps/{aid}/sqlchange/records")).json() == []
    assert (await collab.get(f"/api/apps/{aid}/metrics/current")).status_code == 200
    assert (await collab.get(f"/api/apps/{aid}/metrics/history",
                             params={"window": "1h"})).status_code == 200
    summary = (await collab.get("/api/apps/metrics/summary")).json()
    assert [it["app_id"] for it in summary["items"]] == [aid]


async def test_collaborator_can_manage_prod_and_rebuild_apk(env):
    """S11：被共享者可启停/重启 prod、看 prod 状态；移动弹框只读可见、APK 可重打，
    而图标覆盖与小程序写入口仍 403（UI 由 AppsView/MobileModal 测试钉住）。"""
    await _share(env)
    mob_root = env["root"] / "mob"
    mob_root.mkdir()
    mob_aid = await _plant(env["owner"], env["owner_id"], mob_root, slug="sharemob",
                           stack="vue3-springboot-mobile", scale="large")
    r = await env["owner"].post(f"/api/apps/{mob_aid}/share",
                                json={"user_id": env["collab_id"]})
    assert r.status_code == 201, r.text
    collab, app, aid = env["collab"], env["app"], env["aid"]

    for action in ("start", "stop", "restart"):
        r = await collab.post(f"/api/apps/{aid}/prod/{action}")
        assert r.status_code == 202, (action, r.text)
        await _drain_dev_task(app, aid)
    st = await collab.get(f"/api/apps/{aid}/prod/status")
    assert st.status_code == 200 and "status_prod" in st.json()

    mob = await collab.get(f"/api/apps/{mob_aid}/mobile")
    assert mob.status_code == 200, mob.text
    assert mob.json()["h5_url"] and "apk" in mob.json()
    icon = await collab.get(f"/api/apps/{mob_aid}/mobile/icon")
    assert icon.status_code == 200 and icon.headers["content-type"].startswith("image/svg")
    # 预览码只读接口放行（尚未生成 → 404，而不是权限 403）
    assert (await collab.get(f"/api/apps/{mob_aid}/mobile/miniprogram/preview")
            ).status_code == 404
    # 停炉时重打被业务闸门挡住（409），说明权限闸已放行
    r = await collab.post(f"/api/apps/{mob_aid}/mobile/apk/rebuild")
    assert r.status_code == 409, r.text
    async with app.state.session_factory() as s:
        await s.execute(text("UPDATE app_project SET status_prod='running' WHERE id=:i"),
                        {"i": mob_aid})
        await s.commit()
    r = await collab.post(f"/api/apps/{mob_aid}/mobile/apk/rebuild")
    assert r.status_code == 202 and r.json()["ok"] is True, r.text


async def test_collaborator_reads_files_but_cannot_write(env, monkeypatch):
    """S9（文件部分）：树/单层/读/下载放行；保存与系统打开 403；deploy/ 围栏不因共享放宽。

    `GET /workspaces` 带 role，是整页工作台刷新后仍能判只读的来源（规格 §5.3）。
    """
    import app.api.files as files_api

    await _share(env)
    root = env["root"]
    (root / "app.json").write_text('{"schema": "zc-app/1"}', encoding="utf-8")
    (root / "README.md").write_text("hello", encoding="utf-8")
    (root / "deploy").mkdir()
    (root / "deploy" / ".env").write_text("SECRET=1", encoding="utf-8")
    sf = env["app"].state.session_factory
    async with sf() as s:
        wid = (await s.execute(text(
            "SELECT workspace_id FROM app_project WHERE id=:i"),
            {"i": env["aid"]})).scalar()
    collab, owner, founder = env["collab"], env["owner"], env["founder"]

    # 列表带角色：自有 → owner，被共享 → collaborator
    assert [(w["id"], w["role"]) for w in (await owner.get("/api/workspaces")).json()] == [
        (wid, "owner")]
    assert [(w["id"], w["role"]) for w in (await collab.get("/api/workspaces")).json()] == [
        (wid, "collaborator")]

    # 读面放行；deploy/ 照旧隐藏、读禁
    tree = (await collab.get(f"/api/fs/tree?workspace_id={wid}")).json()
    names = [n["name"] for n in tree]
    assert "README.md" in names and "deploy" not in names
    ls = (await collab.get("/api/fs/ls", params={"workspace_id": wid, "path": ""})).json()
    assert "README.md" in [e["name"] for e in ls["entries"]]
    read = await collab.get("/api/files/read",
                            params={"workspace_id": wid, "path": "README.md"})
    assert read.status_code == 200 and read.json()["content"] == "hello"
    dl = await collab.get("/api/fs/download",
                          params={"workspace_id": wid, "path": "README.md"})
    assert dl.status_code == 200 and dl.text == "hello"
    assert (await collab.get("/api/files/read",
                             params={"workspace_id": wid, "path": "deploy/.env"})
            ).status_code == 403

    # 写面 403；系统资源管理器也不能替被共享者开窗
    launched = []
    monkeypatch.setattr(files_api, "open_in_file_manager", lambda p: launched.append(p))
    r = await collab.put("/api/files/save",
                         json={"workspace_id": wid, "path": "README.md", "content": "hacked"})
    assert r.status_code == 403 and "仅应用所有者" in r.json()["detail"]
    assert (root / "README.md").read_text(encoding="utf-8") == "hello"
    r = await collab.post("/api/fs/open-folder", json={"workspace_id": wid})
    assert r.status_code == 403 and launched == []

    # 所有者行为不变；创始人没有文件域 bypass，与不可见同口径 404
    assert (await owner.put("/api/files/save", json={
        "workspace_id": wid, "path": "README.md", "content": "owner edit"})).status_code == 200
    assert (await founder.get("/api/fs/ls",
                              params={"workspace_id": wid, "path": ""})).status_code == 404


async def test_collaborator_db_readonly_and_select_only(env):
    """S10：审计/表/结构/数据与 SELECT 放行；写、DDL、SELECT INTO、混合批次整批 403。

    真库（与 test_db_admin.db_app 同做法）——权限闸口在连接之前，只有真跑通 SELECT
    才能说明「放行」不是把 409 误读成成功。
    """
    from types import SimpleNamespace

    from sqlalchemy.ext.asyncio import create_async_engine

    from app.deploy import provision
    from app.services import db_admin_service as dbsvc

    await _share(env)
    sf = env["app"].state.session_factory
    cred = await provision.provision_env(sf, "shareapp", "dev")
    eng = create_async_engine(
        dbsvc.target_engine_url(SimpleNamespace(app=env["app"]), cred))
    try:
        async with eng.begin() as conn:
            await conn.exec_driver_sql(
                "CREATE TABLE IF NOT EXISTS widgets (id int primary key, name text)")
            await conn.exec_driver_sql(
                "INSERT INTO widgets VALUES (1, 'alpha') ON CONFLICT (id) DO NOTHING")
        aid, collab, owner = env["aid"], env["collab"], env["owner"]

        # 读面：审计、表清单、结构、数据
        tables = await collab.get(f"/api/apps/{aid}/db/dev/tables")
        assert tables.status_code == 200, tables.text
        assert "widgets" in [t["name"] for t in tables.json()["tables"]]
        assert (await collab.get(
            f"/api/apps/{aid}/db/dev/tables/widgets/schema")).status_code == 200
        data = await collab.get(f"/api/apps/{aid}/db/dev/tables/widgets/data")
        assert data.status_code == 200 and data.json()["rows"][0]["name"] == "alpha"
        assert (await collab.get(f"/api/apps/{aid}/db/audit")).status_code == 200

        # SELECT：单条与纯读批次都放行
        r = await collab.post(f"/api/apps/{aid}/db/dev/sql",
                              json={"sql": "SELECT 1 AS n"})
        assert r.status_code == 200 and r.json()["rows"] == [{"n": 1}]
        r = await collab.post(f"/api/apps/{aid}/db/dev/sql/batch",
                              json={"statements": ["SELECT 1", "SELECT 2"]})
        assert r.status_code == 200, r.text
        assert [x["status"] for x in r.json()["results"]] == ["ok", "ok"]

        # 写 / DDL / SELECT INTO 一律 403（INTO 归 other）
        for sql in ("INSERT INTO widgets VALUES (2, 'b')",
                    "UPDATE widgets SET name='x'",
                    "DELETE FROM widgets",
                    "DROP TABLE widgets",
                    "SELECT * INTO evil FROM widgets"):
            r = await collab.post(f"/api/apps/{aid}/db/dev/sql", json={"sql": sql})
            assert r.status_code == 403 and "共享只读" in r.json()["detail"], (sql, r.text)

        # 混合批次整批拒绝：前半段 SELECT 也不执行
        r = await collab.post(f"/api/apps/{aid}/db/dev/sql/batch",
                              json={"statements": ["SELECT 1", "DELETE FROM widgets"]})
        assert r.status_code == 403 and "仅允许 SELECT" in r.json()["detail"]
        data = await owner.get(f"/api/apps/{aid}/db/dev/tables/widgets/data")
        assert [row["name"] for row in data.json()["rows"]] == ["alpha"]

        # 所有者写路径完全不变
        r = await owner.post(f"/api/apps/{aid}/db/dev/sql",
                             json={"sql": "INSERT INTO widgets VALUES (3, 'gamma')"})
        assert r.status_code == 200 and r.json()["affected"] == 1
    finally:
        await eng.dispose()
        await provision.drop_env(sf, "shareapp", "dev")


async def test_collaborator_reads_chat_history_but_cannot_write(env):
    """S8（历史半边 / Ticket 007）：会话列表/详情/消息/变更/附件读取放行；
    新建、改名、切模型、删会话、上传附件一律 403；陌生人仍 404。

    会话与消息直插（写链不在本票靶内）：这里只验「能读」和「写着被拒」，
    所有者的写行为保持原样。"""
    await _share(env)
    owner, collab, founder, aid = env["owner"], env["collab"], env["founder"], env["aid"]
    sf = env["app"].state.session_factory
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
    async with sf() as s:
        wid = (await s.execute(text(
            "SELECT workspace_id FROM app_project WHERE id=:i"), {"i": aid})).scalar()
        sid = (await s.execute(text(
            "INSERT INTO chat_session (workspace_id, title) VALUES (:w, '所有者的会话') "
            "RETURNING id"), {"w": wid})).scalar()
        await s.execute(text(
            "INSERT INTO message (session_id, seq, role, content) VALUES (:s, 1, 'user', '你好')"),
            {"s": sid})
        cid = (await s.execute(text(
            "INSERT INTO file_change (session_id, path, action, diff) "
            "VALUES (:s, 'a.py', 'edit', '题面') RETURNING id"), {"s": sid})).scalar()
        await s.commit()

    # 附件由所有者正常上传（顺带证明上传链本身没被共享改动）
    r = await owner.post(f"/api/sessions/{sid}/attachments", params={"filename": "p.png"},
                         content=png, headers={"content-type": "image/png"})
    assert r.status_code == 201, r.text
    att_id = r.json()["id"]

    # 读面：列表/详情/消息/变更/附件
    sessions = (await collab.get("/api/sessions", params={"workspace_id": wid})).json()
    assert [x["id"] for x in sessions] == [sid]
    detail = await collab.get(f"/api/sessions/{sid}")
    assert detail.status_code == 200 and detail.json()["title"] == "所有者的会话"
    msgs = (await collab.get(f"/api/sessions/{sid}/messages")).json()
    assert [m["content"] for m in msgs] == ["你好"]
    assert (await collab.get(f"/api/sessions/{sid}/file-changes")).json()[0]["path"] == "a.py"
    assert (await collab.get(
        f"/api/sessions/{sid}/file-changes/{cid}")).status_code == 200
    att = await collab.get(f"/api/attachments/{att_id}")
    assert att.status_code == 200 and att.content == png

    # 写面：全部 403（被共享者看得见但无权，规格 §5.4/§5.6）
    r = await collab.post("/api/sessions", json={"workspace_id": wid})
    assert r.status_code == 403 and "仅应用所有者" in r.json()["detail"]
    r = await collab.patch(f"/api/sessions/{sid}", json={"title": "改名"})
    assert r.status_code == 403 and "仅应用所有者" in r.json()["detail"]
    r = await collab.patch(f"/api/sessions/{sid}/model", json={"model_config_id": None})
    assert r.status_code == 403
    r = await collab.delete(f"/api/sessions/{sid}")
    assert r.status_code == 403
    r = await collab.post(f"/api/sessions/{sid}/attachments", params={"filename": "x.png"},
                          content=png, headers={"content-type": "image/png"})
    assert r.status_code == 403 and "仅应用所有者" in r.json()["detail"]
    async with sf() as s:
        still = (await s.execute(text(
            "SELECT title FROM chat_session WHERE id=:i"), {"i": sid})).scalar()
    assert still == "所有者的会话"
    assert (await owner.patch(f"/api/sessions/{sid}", json={"title": "所有者改名"})
            ).status_code == 200

    # 陌生人（创始人）仍 404：会话可见性没有被共享放大
    assert (await founder.get("/api/sessions", params={"workspace_id": wid})).status_code == 404
    assert (await founder.get(f"/api/sessions/{sid}/messages")).status_code == 404
    assert (await founder.get(f"/api/attachments/{att_id}")).status_code == 404


async def test_metrics_and_db_audit_open_to_collaborator(env):
    """观测面（003）与 DB 读面（006）：被共享者看指标与审计 200；
    创始人既有 bypass（200）不动（规格 §2.5）。"""
    await _share(env)
    aid = env["aid"]
    for suffix, kwargs in (("/metrics/current", None),
                           ("/metrics/history", {"params": {"window": "1h"}})):
        r = await env["collab"].get(f"/api/apps/{aid}{suffix}", **(kwargs or {}))
        assert r.status_code == 200, (suffix, r.text)
        r = await env["founder"].get(f"/api/apps/{aid}{suffix}", **(kwargs or {}))
        assert r.status_code == 200, (suffix, r.text)
    r = await env["collab"].get(f"/api/apps/{aid}/db/audit")
    assert r.status_code == 200, r.text
    assert (await env["founder"].get(f"/api/apps/{aid}/db/audit")).status_code == 200


async def test_founder_bypass_not_widened_to_sharing(env):
    """创始人只有指标/DB 的既有 bypass：共享列表为空、详情 404、不能共享或撤销。"""
    await _share(env)
    founder, aid = env["founder"], env["aid"]
    assert (await founder.get("/api/apps/shared")).json() == []
    assert (await founder.get(f"/api/apps/{aid}")).status_code == 404
    assert (await founder.get(f"/api/apps/{aid}/share")).status_code == 404
    r = await founder.delete(f"/api/apps/{aid}/share/{env['collab_id']}")
    assert r.status_code == 404


async def test_users_list_shape_and_guard(env, anon_client):
    """D7：全量昵称列表只暴露 id/nickname/is_founder，按昵称排序；未登录 401。"""
    r = await env["collab"].get("/api/users")
    assert r.status_code == 200, r.text
    users = r.json()
    assert [u["nickname"] for u in users] == ["share-collab", "share-founder", "share-owner"]
    assert all(set(u) == {"id", "nickname", "is_founder"} for u in users)
    assert [u["nickname"] for u in users if u["is_founder"]] == ["share-founder"]
    assert (await anon_client.get("/api/users")).status_code == 401


async def test_shared_list_owner_nickname_missing_when_app_deleted(env):
    """S20 的本票半程：删应用后共享关系随外键级联消失（重新插入不可能再命中）。"""
    await _share(env)
    sf = env["app"].state.session_factory
    async with sf() as s:
        await s.execute(text("DELETE FROM app_project WHERE id=:i"), {"i": env["aid"]})
        await s.commit()
    assert (await env["collab"].get("/api/apps/shared")).json() == []
    async with sf() as s:
        n = (await s.execute(text("SELECT count(*) FROM app_share"))).scalar()
    assert n == 0


# ---------- 发布申请与审批（Ticket 009，规格 §2.3/§5.2 / ADR-0050） ----------

async def _apply_publish(env, client=None) -> dict:
    r = await (client or env["collab"]).post(f"/api/apps/{env['aid']}/publish-requests")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "pending" and body["id"] and body["created_at"]
    return body


async def _db_request_status(env, rid: int) -> str:
    async with env["app"].state.session_factory() as s:
        return (await s.execute(text(
            "SELECT status FROM app_publish_request WHERE id=:i"), {"i": rid})).scalar()


async def _db_request_count(env) -> int:
    async with env["app"].state.session_factory() as s:
        return (await s.execute(text("SELECT count(*) FROM app_publish_request"))).scalar()


async def test_publish_request_apply_reject_reapply_approve(env):
    """S4/S12/S17/S19：申请 → 驳回（理由必填且可见）→ 重新申请 → 批准即执行。

    批准路径必须走与直接发布同一道闸门（001 `_publish_gates`），发布副作用由 `_stub_docker`
    替身承接；申请状态在列表、共享管理弹框与库里三处同步迁移。
    """
    await _share(env)
    owner, collab, founder, aid = (env["owner"], env["collab"], env["founder"], env["aid"])
    app = env["app"]

    # 陌生人 404（不暴露存在性）、所有者 403（直接发布即可）
    assert (await founder.post(f"/api/apps/{aid}/publish-requests")).status_code == 404
    r = await owner.post(f"/api/apps/{aid}/publish-requests")
    assert r.status_code == 403 and "无需申请" in r.json()["detail"]

    first = await _apply_publish(env)
    assert (await collab.post(f"/api/apps/{aid}/publish-requests")).status_code == 409

    # 三处事实同源：自有列表角标 / 共享列表状态机 / 共享管理待审区块
    own = (await owner.get("/api/apps")).json()
    assert own[0]["pending_publish"] is True
    shared = (await collab.get("/api/apps/shared")).json()
    assert shared[0]["pending_publish"] is True
    assert shared[0]["publish_request"]["status"] == "pending"
    pending = (await owner.get(f"/api/apps/{aid}/share")).json()["pending_request"]
    assert pending["id"] == first["id"]
    assert pending["requester_id"] == env["collab_id"]
    assert pending["requester_nickname"] == "share-collab"
    assert pending["created_at"]

    # 驳回：空理由 400 且申请仍 pending；有理由 → rejected，理由对申请人可见
    r = await owner.post(f"/api/apps/{aid}/publish-requests/{first['id']}/reject",
                         json={"reason": "  "})
    assert r.status_code == 400 and "理由" in r.json()["detail"]
    assert await _db_request_status(env, first["id"]) == "pending"
    r = await owner.post(f"/api/apps/{aid}/publish-requests/{first['id']}/reject",
                         json={"reason": "功能还没做完"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "rejected" and r.json()["reason"] == "功能还没做完"
    shared = (await collab.get("/api/apps/shared")).json()
    assert shared[0]["pending_publish"] is False
    assert shared[0]["publish_request"]["status"] == "rejected"
    assert shared[0]["publish_request"]["reason"] == "功能还没做完"
    assert (await owner.get("/api/apps")).json()[0]["pending_publish"] is False
    # 同一申请不能再被处理一次
    assert (await owner.post(f"/api/apps/{aid}/publish-requests/{first['id']}/reject",
                             json={"reason": "再驳一次"})).status_code == 409

    # 重新申请；审批端点只有所有者（被共享者 403、陌生人 404）
    second = await _apply_publish(env)
    assert (await collab.post(f"/api/apps/{aid}/publish-requests/{second['id']}/approve")
            ).status_code == 403
    assert (await founder.post(f"/api/apps/{aid}/publish-requests/{second['id']}/approve")
            ).status_code == 404

    # 批准即执行：202 → 后台发布（替身），申请 approved 且不再是待审
    r = await owner.post(f"/api/apps/{aid}/publish-requests/{second['id']}/approve")
    assert r.status_code == 202, r.text
    assert r.json()["request"]["status"] == "approved"
    await _drain_dev_task(app, aid)
    assert await _db_request_status(env, second["id"]) == "approved"
    shared = (await collab.get("/api/apps/shared")).json()
    assert shared[0]["publish_request"]["status"] == "approved"
    assert shared[0]["pending_publish"] is False
    assert (await owner.get("/api/apps")).json()[0]["pending_publish"] is False
    # 已批准的不能重复批准 / 撤回
    assert (await owner.post(f"/api/apps/{aid}/publish-requests/{second['id']}/approve")
            ).status_code == 409
    assert (await collab.delete(f"/api/apps/{aid}/publish-requests/{second['id']}")
            ).status_code == 409


async def test_publish_request_owner_direct_publish_creates_no_request(env):
    """S17：所有者发布新版本不产生申请记录、不需要审批，行为与现状一致。"""
    r = await env["owner"].post(f"/api/apps/{env['aid']}/publish",
                                json={"force": True})
    assert r.status_code == 202, r.text
    await _drain_dev_task(env["app"], env["aid"])
    assert await _db_request_count(env) == 0


async def test_publish_request_chat_active_keeps_pending_then_force(env):
    """S19/D15：批准遇活跃会话 → 与直接发布同形状的 409 `chat_active`，申请保持 pending；
    带 force 重试才通过。"""
    await _share(env)
    owner, aid = env["owner"], env["aid"]
    hub = env["app"].state.hub
    sf = env["app"].state.session_factory
    async with sf() as s:
        wid = (await s.execute(text(
            "SELECT workspace_id FROM app_project WHERE id=:i"), {"i": aid})).scalar()
        sid = (await s.execute(text(
            "INSERT INTO chat_session (workspace_id) VALUES (:w) RETURNING id"),
            {"w": wid})).scalar()
        await s.commit()

    req = await _apply_publish(env)
    hub.running.add(sid)   # 数据源与 /sessions/live-status 同源
    try:
        r = await owner.post(f"/api/apps/{aid}/publish-requests/{req['id']}/approve")
        assert r.status_code == 409, r.text
        assert r.json()["detail"]["code"] == "chat_active"
        assert "正在修改代码" in r.json()["detail"]["message"]
        assert await _db_request_status(env, req["id"]) == "pending"   # 闸门失败保留可重试

        r = await owner.post(f"/api/apps/{aid}/publish-requests/{req['id']}/approve",
                             json={"force": True})
        assert r.status_code == 202, r.text
        await _drain_dev_task(env["app"], aid)
        assert await _db_request_status(env, req["id"]) == "approved"
    finally:
        hub.running.discard(sid)
        async with sf() as s:
            await s.execute(text("DELETE FROM chat_session WHERE id=:i"), {"i": sid})
            await s.commit()


async def test_publish_request_withdraw_and_single_pending(env):
    """S12/S18：申请人可撤回；非本人撤回 403；并发两条申请只成一条（唯一索引兜底）。"""
    await _share(env)
    owner, collab, aid = env["owner"], env["collab"], env["aid"]

    req = await _apply_publish(env)
    # 所有者不是申请人 → 403
    assert (await owner.delete(f"/api/apps/{aid}/publish-requests/{req['id']}")
            ).status_code == 403
    r = await collab.delete(f"/api/apps/{aid}/publish-requests/{req['id']}")
    assert r.status_code == 200 and r.json()["status"] == "withdrawn"
    assert (await collab.delete(f"/api/apps/{aid}/publish-requests/{req['id']}")
            ).status_code == 409
    # 未知名 rid → 404
    assert (await collab.delete(f"/api/apps/{aid}/publish-requests/999999")
            ).status_code == 404

    # 并发双申请：一条 201、一条 409（预检可能双过，部分唯一索引是最后判据）
    r1, r2 = await asyncio.gather(
        collab.post(f"/api/apps/{aid}/publish-requests"),
        collab.post(f"/api/apps/{aid}/publish-requests"))
    assert sorted([r1.status_code, r2.status_code]) == [201, 409], (r1.text, r2.text)


async def test_publish_request_rejected_while_publishing(env):
    """发布进行中不收新申请（规格 §2.3）。"""
    await _share(env)
    sf = env["app"].state.session_factory
    async with sf() as s:
        await s.execute(text(
            "UPDATE app_project SET status_prod='publishing' WHERE id=:i"),
            {"i": env["aid"]})
        await s.commit()
    try:
        r = await env["collab"].post(f"/api/apps/{env['aid']}/publish-requests")
        assert r.status_code == 409 and "发布中" in r.json()["detail"]
    finally:
        async with sf() as s:
            await s.execute(text(
                "UPDATE app_project SET status_prod='stopped' WHERE id=:i"),
                {"i": env["aid"]})
            await s.commit()


# ---------- 撤销 / 退出收口（Ticket 010，S2/S13/S14） ----------

async def test_revoke_and_leave_lifecycle(env):
    """撤销/退出只回收访问权：本人待审申请作废、会话与应用端点 404；
    别人的待审申请原样保留；重新共享恢复原有会话可见性。"""
    from httpx import AsyncClient, ASGITransport

    owner, collab, aid, app = env["owner"], env["collab"], env["aid"], env["app"]
    sf = app.state.session_factory
    other = AsyncClient(transport=ASGITransport(app=app), base_url="http://t")
    try:
        from tests.conftest import mint_invite
        r = await other.post("/api/auth/register",
                             json={"nickname": "share-collab-2", "password": "pw2",
                                   "invite_code": (await mint_invite(app))[0]})
        assert r.status_code == 200, r.text
        other_id = (await other.get("/api/auth/me")).json()["id"]

        await _share(env)
        r = await owner.post(f"/api/apps/{aid}/share", json={"user_id": other_id})
        assert r.status_code == 201, r.text

        async with sf() as s:
            wid = (await s.execute(text(
                "SELECT workspace_id FROM app_project WHERE id=:i"), {"i": aid})).scalar()
            sid = (await s.execute(text(
                "INSERT INTO chat_session (workspace_id, title) VALUES (:w, '原有会话') "
                "RETURNING id"), {"w": wid})).scalar()
            await s.commit()
        assert (await collab.get(f"/api/sessions/{sid}/messages")).status_code == 200

        # A 的历史申请（撤回）与 B 的待审申请并存
        a_req = await _apply_publish(env)
        assert (await collab.delete(
            f"/api/apps/{aid}/publish-requests/{a_req['id']}")).status_code == 200
        b_req = await _apply_publish(env, client=other)

        # 撤销 A：A 已处理的申请不动，B 的待审申请不受影响
        r = await owner.delete(f"/api/apps/{aid}/share/{env['collab_id']}")
        assert r.status_code == 200
        assert await _db_request_status(env, a_req["id"]) == "withdrawn"
        assert await _db_request_status(env, b_req["id"]) == "pending"
        assert (await other.get("/api/apps/shared")).json()[0]["pending_publish"] is True

        # A 的访问权立即失效：共享卡消失，应用/工作区/会话端点一律 404
        assert (await collab.get("/api/apps/shared")).json() == []
        assert (await collab.get(f"/api/apps/{aid}")).status_code == 404
        assert (await collab.get(f"/api/apps/{aid}/dev/status")).status_code == 404
        assert (await collab.get(f"/api/sessions/{sid}/messages")).status_code == 404

        # 重新共享：恢复原有会话可见性；历史申请保持 withdrawn（不复活）
        await _share(env)
        assert [x["id"] for x in (await collab.get("/api/apps/shared")).json()] == [aid]
        assert (await collab.get(f"/api/sessions/{sid}/messages")).status_code == 200
        assert await _db_request_status(env, a_req["id"]) == "withdrawn"

        # B 主动退出：自身待审申请作废（cancelled），访问权同刻失效
        r = await other.post(f"/api/apps/{aid}/share/leave")
        assert r.status_code == 200
        assert await _db_request_status(env, b_req["id"]) == "cancelled"
        assert (await other.get("/api/apps/shared")).json() == []

        # 退出路径同样作废 pending：A 重新申请后自退
        c_req = await _apply_publish(env)
        assert (await collab.post(f"/api/apps/{aid}/share/leave")).status_code == 200
        assert await _db_request_status(env, c_req["id"]) == "cancelled"
    finally:
        await other.aclose()


async def test_approved_publish_continues_after_revoke(env):
    """S14：审批节点已越过——批准后立即撤销，发布任务照常执行完，只回收访问权。"""
    await _share(env)
    app, owner, aid = env["app"], env["owner"], env["aid"]

    req = await _apply_publish(env)
    r = await owner.post(f"/api/apps/{aid}/publish-requests/{req['id']}/approve")
    assert r.status_code == 202, r.text
    r = await owner.delete(f"/api/apps/{aid}/share/{env['collab_id']}")
    assert r.status_code == 200

    await _drain_dev_task(app, aid)
    assert await _db_request_status(env, req["id"]) == "approved"
    # 撤销不清发布记录与申请历史
    assert await _db_request_count(env) >= 1
