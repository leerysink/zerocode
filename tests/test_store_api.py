# tests/test_store_api.py
"""商店 REST：身份 cookie、商店列表、申请审批。Docker 不参与（安装用例见 test_store_install.py）。"""
from sqlalchemy import text


async def _sf(client):
    return client.app.state.session_factory


async def test_store_requires_identity(anon_client):
    # Ticket 002 起无身份先被全局 guard 拦 401（detail 为字符串）；
    # store 自身的 identity_required 依赖在 008 身份合流后一并收口。
    r = await anon_client.get("/api/store/me")
    assert r.status_code == 401


async def test_匿名identify建号端点已退役(client):
    """Ticket 008 合流：不再面向前端匿名建号（注册端点收编），路由已删。

    404 或 405 均算数：POST 不匹配任何 API 路由时，SPA 的 GET catch-all 会接住路径回 405，
    语义都是「这个端点不存在了」。"""
    r = await client.post("/api/store/identify", json={"nickname": "看客"})
    assert r.status_code in (404, 405)


async def test_商店身份即登录用户没有第二套actor(client):
    """两套身份合流：/store/me 与 /auth/me 必须是同一个人（cookie 同一、行同一）。"""
    me = (await client.get("/api/auth/me")).json()
    actor = (await client.get("/api/store/me")).json()
    assert actor["id"] == me["id"] and actor["nickname"] == me["nickname"]


async def test_审批权在HTTP层收口到创始人(two_auth_clients, test_engine):
    """端点接线的靶心：is_founder 只能来自会话 actor——
    bob（非创始人，即便是版本发布者）决策 403、收件箱空；alice（创始人，局外人）可批。
    直连 sf 写库的用例显式请求 test_engine（同 test_isolation_config_models:73 先例），
    保证清库 finalizer 的拆序正确，不泄漏状态给下一个用例。"""
    alice, bob = two_auth_clients   # alice 首注册即创始人
    sf = alice.app.state.session_factory
    async with sf() as s:
        bob_id = (await s.execute(text("SELECT id FROM actor WHERE nickname = 'bob'"))).scalar()
        rid = (await s.execute(text(
            "INSERT INTO app_release (app_id, slug, version, title, stack, anchor, "
            "fingerprint, status, created_by) VALUES (0, 'gating', 'b1', '门禁', "
            "'vue3-fastapi', '[]'::jsonb, 'fp-g1', 'published', :a) RETURNING id"),
            {"a": bob_id})).scalar()
        await s.commit()
    a = await bob.post("/api/store/applications",
                       json={"release_id": int(rid), "want_name": "bob的实例"})
    assert a.status_code == 201, a.text
    aid = a.json()["id"]
    # 发布者本人也无权决策（权限翻转）
    r = await bob.post(f"/api/store/applications/{aid}/reject",
                       json={"approve": False, "reason": "越权尝试"})
    assert r.status_code == 403
    # bob 的审批收件箱为空（不再按版本归属分发）
    assert (await bob.get("/api/store/applications?box=review")).json() == []
    # alice 看得到全部 pending 并可代批
    rows = (await alice.get("/api/store/applications?box=review")).json()
    assert [x["id"] for x in rows] == [aid]
    r2 = await alice.post(f"/api/store/applications/{aid}/reject",
                          json={"approve": False, "reason": "名字不合适"})
    assert r2.status_code == 200 and r2.json()["status"] == "rejected"
    assert (await alice.get("/api/store/applications?box=review")).json() == []


async def test_releases_list_only_published(client):
    """未上架时是空列表而不是 404；withdrawn 不出现。"""
    r = await client.get("/api/store/releases")
    assert r.status_code == 200
    assert r.json() == []


async def test_release_endpoint_requires_identity_and_publish(anon_client):
    # 同 store_requires_identity：401 现在由 guard 给出（detail 字符串），不再断言 code 形状
    r = await anon_client.post("/api/apps/1/release", json={"title": "x", "summary": ""})
    assert r.status_code == 401


async def test_store_releases_lists_published(client, monkeypatch):
    """列表不触发 Docker：直接往 app_release 插行，验形状与 withdrawn 过滤。

    001：分类与安装量——列表回 category 与 install_count（approved 计数），不再有 updated_count。
    """
    sf = client.app.state.session_factory
    from sqlalchemy import text
    async with sf() as s:
        aid = (await s.execute(text("SELECT id FROM actor LIMIT 1"))).scalar()
        for version, status, category in (("b1", "published", "数据管理"),
                                          ("b0", "withdrawn", "其他")):
            await s.execute(text(
                "INSERT INTO app_release (app_id, slug, version, title, stack, anchor, "
                "fingerprint, status, category, created_by) VALUES (0, 'demo', :v, '演示应用', "
                "'vue3-fastapi', '[]'::jsonb, :f, :st, :cat, :a)"),
                {"v": version, "f": "fp" + version, "st": status, "cat": category, "a": aid})
        await s.commit()
    r = await client.get("/api/store/releases")
    assert r.status_code == 200
    body = r.json()
    assert [x["version"] for x in body] == ["b1"], "下架版本不得出现在商店列表"
    assert body[0]["title"] == "演示应用"
    assert body[0]["category"] == "数据管理"
    assert body[0]["install_count"] == 0
    assert "updated_count" not in body[0]


async def test_install_count_只数批准安装(client):
    """安装量＝被批准安装的次数：pending/rejected/cancelled 一律不计。"""
    sf = client.app.state.session_factory
    async with sf() as s:
        aid = (await s.execute(text("SELECT id FROM actor LIMIT 1"))).scalar()
        rid = (await s.execute(text(
            "INSERT INTO app_release (app_id, slug, version, title, stack, anchor, "
            "fingerprint, status, created_by) VALUES (0, 'demo2', 'b1', '计数应用', "
            "'vue3-fastapi', '[]'::jsonb, 'fp-c1', 'published', :a) RETURNING id"),
            {"a": aid})).scalar()
        for st in ("pending", "approved", "approved", "rejected", "cancelled"):
            await s.execute(text(
                "INSERT INTO release_application (release_id, applicant_id, want_name, status) "
                "VALUES (:r, :a, '实例', :st)"), {"r": rid, "a": aid, "st": st})
        await s.commit()
    body = (await client.get("/api/store/releases")).json()
    assert body[0]["install_count"] == 2


async def test_上架非法分类最先拒400(client):
    """分类校验在所有权/五道闸门之前（最便宜的闸先打）：错分类直接 400，不泄漏后续闸门。"""
    r = await client.post("/api/apps/999999/release",
                          json={"title": "x", "category": "不存在的分类"})
    assert r.status_code == 400
    assert "分类" in r.text


async def test_搜索分类过滤与排序(client):
    """q 命中标题或简介；category 精确过滤；sort=installs 按安装量降序，非法分类 400。"""
    sf = client.app.state.session_factory
    async with sf() as s:
        aid = (await s.execute(text("SELECT id FROM actor LIMIT 1"))).scalar()
        r1 = (await s.execute(text(
            "INSERT INTO app_release (app_id, slug, version, title, summary, stack, anchor, "
            "fingerprint, status, category, created_by) VALUES (0, 'srchA', 'b1', '陌如玉', "
            "'桌面备忘', 'vue3-fastapi', '[]'::jsonb, 'fp-a', 'published', '数据管理', :a) "
            "RETURNING id"), {"a": aid})).scalar()
        r2 = (await s.execute(text(
            "INSERT INTO app_release (app_id, slug, version, title, summary, stack, anchor, "
            "fingerprint, status, category, created_by) VALUES (0, 'srchB', 'b1', '图库', "
            "'相册与备忘录', 'vue3-fastapi', '[]'::jsonb, 'fp-b', 'published', '内容展示', :a) "
            "RETURNING id"), {"a": aid})).scalar()
        await s.execute(text(
            "INSERT INTO release_application (release_id, applicant_id, want_name, status) "
            "VALUES (:r, :a, '实例', 'approved')"), {"r": r1, "a": aid})
        await s.commit()

    async def titles(**params):
        r = await client.get("/api/store/releases", params=params)
        assert r.status_code == 200, r.text
        return [x["title"] for x in r.json()]

    assert await titles() == ["图库", "陌如玉"], "默认最新在前（id 降序）"
    assert await titles(q="桌面") == ["陌如玉"], "命中简介"
    assert await titles(q="备忘") == ["图库", "陌如玉"], "标题与简介都参与"
    assert await titles(category="内容展示") == ["图库"]
    assert await titles(sort="installs") == ["陌如玉", "图库"], "安装量降序"
    bad = await client.get("/api/store/releases", params={"category": "不存在的分类"})
    assert bad.status_code == 400


async def test_在线试用随来源应用dev状态(client):
    """试用闸门＝来源应用 status_dev===running（ADR-0057）；地址是 dev 预览地址；prod 不参与。"""
    from app.deploy.settings import preview_base_host
    sf = client.app.state.session_factory
    async with sf() as s:
        aid = (await s.execute(text("SELECT id FROM actor LIMIT 1"))).scalar()
        wid = (await s.execute(text(
            "INSERT INTO workspace (name, root_path) VALUES ('试用源', '/tmp/trial-ws-x') "
            "RETURNING id"))).scalar()
        app_id = (await s.execute(text(
            "INSERT INTO app_project (workspace_id, name, slug, scale, stack, prod_port, dev_port, "
            "status_dev, status_prod) VALUES (:w, '试用源', 'trialapp', 'small', 'vue3-fastapi', "
            "18401, 18301, 'stopped', 'running') RETURNING id"), {"w": wid})).scalar()
        await s.execute(text(
            "INSERT INTO app_release (app_id, slug, version, title, stack, anchor, "
            "fingerprint, status, created_by) VALUES (:ap, 'trialapp', 'b1', '试用应用', "
            "'vue3-fastapi', '[]'::jsonb, 'fp-t', 'published', :a)"),
            {"ap": app_id, "a": aid})
        await s.commit()

    async def item():
        body = (await client.get("/api/store/releases")).json()
        return next(x for x in body if x["slug"] == "trialapp")

    it = await item()
    assert it["trial_available"] is False and it["trial_url"] == ""
    async with sf() as s:
        await s.execute(text("UPDATE app_project SET status_dev='running' WHERE id=:i"),
                        {"i": app_id})
        await s.commit()
    it = await item()
    assert it["trial_available"] is True
    assert it["trial_url"] == f"http://{preview_base_host()}:18301"
    async with sf() as s:   # prod 停掉不影响试用（试用只认 dev）
        await s.execute(text("UPDATE app_project SET status_prod='stopped' WHERE id=:i"),
                        {"i": app_id})
        await s.commit()
    assert (await item())["trial_available"] is True


async def test_撤回路由接线(client):
    """POST /applications/{aid}/cancel：本人 pending → cancelled；重复撤 409。"""
    sf = client.app.state.session_factory
    async with sf() as s:
        aid = (await s.execute(text("SELECT id FROM actor LIMIT 1"))).scalar()
        rid = (await s.execute(text(
            "INSERT INTO app_release (app_id, slug, version, title, stack, anchor, "
            "fingerprint, status, created_by) VALUES (0, 'cancelapp', 'b1', '撤回应用', "
            "'vue3-fastapi', '[]'::jsonb, 'fp-x', 'published', :a) RETURNING id"),
            {"a": aid})).scalar()
        await s.commit()
    a = await client.post("/api/store/applications",
                          json={"release_id": int(rid), "want_name": "我的实例"})
    assert a.status_code == 201, a.text
    aid = a.json()["id"]
    r = await client.post(f"/api/store/applications/{aid}/cancel")
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    assert (await client.post(f"/api/store/applications/{aid}/cancel")).status_code == 409


async def test_已装实例打开地址走投影(client, monkeypatch):
    """installed_apps 带 prod_url：SSL 开＝网关子域名，关＝host:prod_port（nics.app_url 单点）。"""
    from app.deploy import nics
    from app.deploy.settings import preview_base_host
    sf = client.app.state.session_factory
    async with sf() as s:
        aid = (await s.execute(text("SELECT id FROM actor LIMIT 1"))).scalar()
        wid = (await s.execute(text(
            "INSERT INTO workspace (name, root_path) VALUES ('实例源', '/tmp/inst-ws-x') "
            "RETURNING id"))).scalar()
        app_id = (await s.execute(text(
            "INSERT INTO app_project (workspace_id, name, slug, scale, stack, prod_port, dev_port, "
            "status_prod) VALUES (:w, '实例', 'instapp', 'small', 'vue3-fastapi', "
            "18402, 18302, 'running') RETURNING id"), {"w": wid})).scalar()
        rid = (await s.execute(text(
            "INSERT INTO app_release (app_id, slug, version, title, stack, anchor, "
            "fingerprint, status, created_by) VALUES (:ap, 'instapp', 'b1', '实例应用', "
            "'vue3-fastapi', '[]'::jsonb, 'fp-i', 'published', :a) RETURNING id"),
            {"ap": app_id, "a": aid})).scalar()
        await s.execute(text(
            "INSERT INTO app_instance_link (app_id, release_id, baseline_manifest, baseline_hash, "
            "owner_actor) VALUES (:ap, :r, '{}'::jsonb, 'bh', :a)"),
            {"ap": app_id, "r": rid, "a": aid})
        await s.commit()

    async def inst():
        body = (await client.get("/api/store/releases")).json()
        return next(x for x in body if x["slug"] == "instapp")["installed_apps"][0]

    monkeypatch.setenv("ZEROCODE_SSL_ENABLE", "true")
    assert (await inst())["prod_url"] == f"https://instapp.{nics.GATEWAY_DOMAIN}"
    monkeypatch.setenv("ZEROCODE_SSL_ENABLE", "false")
    assert (await inst())["prod_url"] == f"http://{preview_base_host()}:18402"


async def _insert_release(sf, *, slug, version, status, creator):
    return (await sf_execute(sf,
        "INSERT INTO app_release (app_id, slug, version, title, stack, anchor, "
        "fingerprint, status, created_by) VALUES (0, :sl, :v, '管理页', "
        "'vue3-fastapi', '[]'::jsonb, :f, :st, :a) RETURNING id",
        {"sl": slug, "v": version, "f": f"fp-{slug}-{version}", "st": status, "a": creator}))


async def sf_execute(sf, sql, params):
    async with sf() as s:
        r = (await s.execute(text(sql), params)).scalar()
        await s.commit()
    return r


async def test_admin_releases_仅创始人且含下架全量行(two_auth_clients):
    """管理列表与发现页刻意不同：全量行含 withdrawn；非创始人 403；status 过滤、非法 400。"""
    alice, bob = two_auth_clients
    sf = alice.app.state.session_factory
    async with sf() as s:
        bob_id = (await s.execute(text("SELECT id FROM actor WHERE nickname = 'bob'"))).scalar()
    await _insert_release(sf, slug="adm", version="b1", status="published", creator=bob_id)
    await _insert_release(sf, slug="adm", version="b0", status="withdrawn", creator=bob_id)
    assert (await bob.get("/api/store/admin/releases")).status_code == 403
    rows = (await alice.get("/api/store/admin/releases")).json()
    # 列表按 id 倒序（端点的既定契约）：后插的 b0 在前，不是按插入序
    assert [r["version"] for r in rows] == ["b0", "b1"]
    assert [r["status"] for r in rows] == ["withdrawn", "published"]
    assert rows[0]["install_count"] == 0 and rows[0]["preview_url"] == ""
    assert rows[0]["publisher"] == "bob"
    only = (await alice.get("/api/store/admin/releases?status=withdrawn")).json()
    assert [r["version"] for r in only] == ["b0"]
    assert (await alice.get("/api/store/admin/releases?status=bogus")).status_code == 400


async def test_下架重新上架所有者与创始人代管(two_auth_clients):
    """withdraw/republish：所有者管自己、创始人代管、旁人 403；published 重上架 409；不存在 404。"""
    alice, bob = two_auth_clients
    sf = alice.app.state.session_factory
    async with sf() as s:
        ids = dict((await s.execute(text(
            "SELECT nickname, id FROM actor"))).all())
    rid = await _insert_release(sf, slug="own", version="b1", status="published", creator=ids["bob"])
    rid_a = await _insert_release(sf, slug="ali", version="b1", status="withdrawn", creator=ids["alice"])
    # 所有者下架自己的 → 重新上架 → 再上架 409
    assert (await bob.post(f"/api/store/releases/{rid}/withdraw")).status_code == 200
    assert (await bob.post(f"/api/store/releases/{rid}/republish")).status_code == 200
    assert (await bob.post(f"/api/store/releases/{rid}/republish")).status_code == 409
    # 创始人代管 bob 的版本
    assert (await alice.post(f"/api/store/releases/{rid}/withdraw")).status_code == 200
    assert (await alice.post(f"/api/store/releases/{rid}/republish")).status_code == 200
    # 旁人碰创始人的版本：403（权限先于状态判定）
    assert (await bob.post(f"/api/store/releases/{rid_a}/withdraw")).status_code == 403
    assert (await bob.post(f"/api/store/releases/{rid_a}/republish")).status_code == 403
    # 创始人重新上架自己的 withdrawn
    assert (await alice.post(f"/api/store/releases/{rid_a}/republish")).status_code == 200
    assert (await bob.post("/api/store/releases/999999/republish")).status_code == 404


async def test_运营编辑_创始人改文案落留痕且不新增行(two_auth_clients):
    """ADR-0061 §1/§2：白名单字段即时生效，留痕两列落库，版本行不增、状态机不动。"""
    alice, bob = two_auth_clients
    sf = alice.app.state.session_factory
    async with sf() as s:
        ids = dict((await s.execute(text("SELECT nickname, id FROM actor"))).all())
    rid = await _insert_release(sf, slug="ops", version="b1", status="published", creator=ids["bob"])

    # 权限先于字段校验：非创始人连白名单内字段也改不动
    assert (await bob.patch(f"/api/store/releases/{rid}", json={"title": "改一下"})).status_code == 403

    r = await alice.patch(f"/api/store/releases/{rid}",
                          json={"title": "新标题", "summary": "新简介", "category": "效率工具"})
    assert r.status_code == 200, r.text
    assert (r.json()["title"], r.json()["summary"], r.json()["category"]) == \
        ("新标题", "新简介", "效率工具")
    assert r.json()["status"] == "published" and r.json()["updated_at"]

    rows = (await alice.get("/api/store/admin/releases")).json()
    me = next(x for x in rows if x["id"] == rid)
    assert me["title"] == "新标题" and me["category"] == "效率工具"
    assert me["editor"] == "alice" and me["publisher"] == "bob" and me["updated_at"]
    # 编辑不是发布：不新增版本行
    assert len([x for x in rows if x["slug"] == "ops"]) == 1

    # 已下架行照样能改文案，但状态机不被编辑带动
    wid = await _insert_release(sf, slug="ops", version="b0", status="withdrawn", creator=ids["bob"])
    assert (await alice.patch(f"/api/store/releases/{wid}", json={"title": "下架条目改名"})).status_code == 200
    w = next(x for x in (await alice.get("/api/store/admin/releases")).json() if x["id"] == wid)
    assert w["status"] == "withdrawn" and w["title"] == "下架条目改名"


async def test_运营编辑_白名单外与非法值整请求拒400不留半改(two_auth_clients):
    """400 要挡在写之前：带一个非法字段的整请求，不能被部分应用。"""
    alice, _ = two_auth_clients
    sf = alice.app.state.session_factory
    async with sf() as s:
        ids = dict((await s.execute(text("SELECT nickname, id FROM actor"))).all())
    rid = await _insert_release(sf, slug="opsbad", version="b1", status="published", creator=ids["bob"])

    bad = [{"slug": "x"}, {"version": "v2"}, {"status": "withdrawn"}, {"app_id": 1},
           {"title": "好标题", "stack": "static"}, {"title": "好标题", "created_by": 1},
           {}, {"title": "   "}, {"title": "长" * 101}, {"category": "不存在"}]
    for body in bad:
        r = await alice.patch(f"/api/store/releases/{rid}", json=body)
        assert r.status_code == 400, (body, r.status_code, r.text)

    me = next(x for x in (await alice.get("/api/store/admin/releases")).json() if x["id"] == rid)
    assert me["title"] == "管理页" and me["updated_at"] is None and me["editor"] is None

    # summary 可清空（作者自己不想留简介是合法诉求）
    assert (await alice.patch(f"/api/store/releases/{rid}", json={"summary": ""})).status_code == 200
    assert (await alice.patch("/api/store/releases/999999", json={"title": "x"})).status_code == 404


async def _actor_ids(client):
    async with client.app.state.session_factory() as s:
        return dict((await s.execute(text("SELECT nickname, id FROM actor"))).all())


async def test_硬删_无引用可删再删404非创始人403(two_auth_clients):
    """ADR-0061 §3：没被引用过的版本行才是硬删的对象。"""
    alice, bob = two_auth_clients
    sf = alice.app.state.session_factory
    ids = await _actor_ids(alice)
    rid = await _insert_release(sf, slug="purge", version="b1", status="published", creator=ids["bob"])
    assert (await bob.delete(f"/api/store/releases/{rid}")).status_code == 403
    r = await alice.delete(f"/api/store/releases/{rid}")
    assert r.status_code == 200, r.text
    assert r.json()["install_count"] == 0 and r.json()["application_count"] == 0
    assert (await alice.get("/api/store/admin/releases")).json() == []
    assert (await alice.delete(f"/api/store/releases/{rid}")).status_code == 404


async def test_硬删守卫_申请后继与安装三类引用各自拦住(two_auth_clients):
    """守卫必须看全三个引用面，且把计数码回给运营看；已下架行同样受约束。"""
    alice, _ = two_auth_clients
    sf = alice.app.state.session_factory
    ids = await _actor_ids(alice)
    a1 = await _insert_release(sf, slug="refapp", version="b1", status="withdrawn", creator=ids["alice"])
    b1 = await _insert_release(sf, slug="refkid", version="b1", status="published", creator=ids["alice"])
    b2 = await _insert_release(sf, slug="refkid", version="b2", status="published", creator=ids["alice"])
    wid = await sf_execute(sf, "INSERT INTO workspace (name, root_path) "
                               "VALUES ('硬删源', '/tmp/purge-ws') RETURNING id", {})
    app_id = await sf_execute(sf, "INSERT INTO app_project (workspace_id, name, slug, scale, stack, "
                                  "prod_port, dev_port) VALUES (:w, '硬删实例', 'purgeinst', 'small', "
                                  "'vue3-fastapi', 18411, 18412) RETURNING id", {"w": wid})
    c1 = await _insert_release(sf, slug="refinst", version="b1", status="published", creator=ids["alice"])

    async with sf() as s:
        # a1 被人申请过（且已批准＝算一次安装）；b2 是 b1 的后继；c1 被别人装着
        await s.execute(text("INSERT INTO release_application (release_id, applicant_id, want_name, "
                             "status) VALUES (:r, :p, 'n1', 'approved')"), {"r": a1, "p": ids["alice"]})
        await s.execute(text("UPDATE app_release SET prev_release_id = :p WHERE id = :i"),
                        {"p": b1, "i": b2})
        await s.execute(text("INSERT INTO app_instance_link (app_id, release_id, baseline_manifest, "
                             "baseline_hash, owner_actor) VALUES (:ap, :r, '{}'::jsonb, 'bh', :o)"),
                        {"ap": app_id, "r": c1, "o": ids["alice"]})
        await s.commit()

    for rid, hint in ((a1, "1 次安装"), (b1, "1 个后继版本"), (c1, "1 个已装实例链接")):
        r = await alice.delete(f"/api/store/releases/{rid}")
        assert r.status_code == 409, (rid, r.text)
        assert hint in r.text, (rid, r.text)
    # 行都还在：409 不是部分成功
    assert len((await alice.get("/api/store/admin/releases")).json()) == 4


async def test_分类字典_创始人增改停删与兜底锁死(two_auth_clients):
    """store_category 的写面只对创始人开放，「其他」不可停用不可删除。"""
    alice, bob = two_auth_clients
    rows = (await alice.get("/api/store/categories")).json()
    assert [r["key"] for r in rows][:5] and any(r["key"] == "其他" for r in rows)
    assert all(r["release_count"] == 0 for r in rows)

    assert (await bob.post("/api/store/categories", json={"key": "模板"})).status_code == 403
    created = await alice.post("/api/store/categories",
                               json={"key": "模板", "label": "模板与示例", "sort": 5})
    assert created.status_code == 201, created.text
    assert created.json()["enabled"] is True
    assert (await alice.post("/api/store/categories", json={"key": "模板"})).status_code == 409
    assert (await alice.post("/api/store/categories", json={"key": "长" * 21})).status_code == 400

    patched = await alice.patch("/api/store/categories/模板", json={"label": "改个名", "sort": 7})
    assert patched.status_code == 200 and patched.json()["label"] == "改个名"
    assert (await alice.patch("/api/store/categories/不存在", json={"label": "x"})).status_code == 404
    assert (await alice.patch("/api/store/categories/其他", json={"enabled": False})).status_code == 400
    assert (await alice.delete("/api/store/categories/其他")).status_code == 400
    # 没有条目引用的分类可以真删
    assert (await alice.delete("/api/store/categories/模板")).status_code == 200


async def test_分类字典_写入口只认启用项停用不影响存量(two_auth_clients):
    """normalize 只认启用项、normalize_filter 认全部：停用 = 退出可选面，不是从报表里消失。"""
    alice, _ = two_auth_clients
    sf = alice.app.state.session_factory
    ids = await _actor_ids(alice)
    await alice.post("/api/store/categories", json={"key": "看板", "label": "看板", "sort": 15})
    rid = await _insert_release(sf, slug="catd", version="b1", status="published", creator=ids["alice"])

    assert (await alice.patch(f"/api/store/releases/{rid}",
                              json={"category": "看板"})).status_code == 200
    assert (await alice.patch(f"/api/store/releases/{rid}",
                              json={"category": "没这个分类"})).status_code == 400
    # 有引用的分类不能删（只能停用）
    assert (await alice.delete("/api/store/categories/看板")).status_code == 409

    assert (await alice.patch("/api/store/categories/看板", json={"enabled": False})).status_code == 200
    other = await _insert_release(sf, slug="catd2", version="b1", status="published", creator=ids["alice"])
    assert (await alice.patch(f"/api/store/releases/{other}",
                              json={"category": "看板"})).status_code == 400
    # 但挂着它的条目还改得动标题：停用不该等于锁死编辑
    assert (await alice.patch(f"/api/store/releases/{rid}",
                              json={"title": "停用后仍后可编辑"})).status_code == 200
    # 发现页按停用分类过滤仍返回那一条，label 也仍显示它
    listed = (await alice.get("/api/store/releases?category=看板")).json()
    assert [x["id"] for x in listed] == [rid] and listed[0]["category"] == "看板"
