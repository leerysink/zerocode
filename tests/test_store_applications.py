# tests/test_store_applications.py
"""申请单状态机：pending → approved/rejected，单向不可回头；审批权收口到创始人（平台管理员）。"""
import secrets
import pytest
from sqlalchemy import text
from app.deploy.errors import DeployError
from app.db import make_session_factory
from app.store import applications as apps_svc, identity


@pytest.fixture
async def sf(test_engine):
    return make_session_factory(test_engine)


@pytest.fixture
async def seed(sf):
    publisher = await identity.create_actor(sf, "发布者")
    stranger = await identity.create_actor(sf, "路人")
    founder = await identity.create_actor(sf, "创始人")
    async with sf() as s:
        await s.execute(text("UPDATE actor SET is_founder = TRUE WHERE id = :i"),
                        {"i": founder["id"]})
        rid = (await s.execute(text(
            "INSERT INTO app_release (app_id, slug, version, title, stack, anchor, fingerprint, "
            "created_by) VALUES (0, :sl, 'b1', '演示', 'vue3-fastapi', '[]'::jsonb, 'fp1', :c) "
            "RETURNING id"), {"sl": "demo" + secrets.token_hex(3), "c": publisher["id"]})).scalar()
        await s.commit()
    return {"publisher": publisher, "stranger": stranger, "founder": founder,
            "release_id": int(rid)}


async def test_create_application_pending(sf, seed):
    a = await apps_svc.create(sf, release_id=seed["release_id"],
                              applicant_id=seed["stranger"]["id"], want_name="我的陌如玉")
    assert a["status"] == "pending" and a["release_id"] == seed["release_id"]
    # 同一个人对同一版本重复申请 → 复用既有单，不再堆 pending
    b = await apps_svc.create(sf, release_id=seed["release_id"],
                              applicant_id=seed["stranger"]["id"], want_name="再要一个")
    assert b["id"] == a["id"]


async def test_reject_requires_reason_and_is_terminal(sf, seed):
    a = await apps_svc.create(sf, release_id=seed["release_id"],
                              applicant_id=seed["stranger"]["id"], want_name="我要")
    with pytest.raises(DeployError):
        await apps_svc.decide(sf, app_id=a["id"], approver_id=seed["founder"]["id"],
                              approver_is_founder=True, approve=False, reason="")   # 驳回必须给原因
    done = await apps_svc.decide(sf, app_id=a["id"], approver_id=seed["founder"]["id"],
                                 approver_is_founder=True, approve=False, reason="名字不合适")
    assert done["status"] == "rejected"
    with pytest.raises(DeployError):
        await apps_svc.decide(sf, app_id=a["id"], approver_id=seed["founder"]["id"],
                              approver_is_founder=True, approve=True, reason="")    # 已决单不可翻


async def test_founder_can_decide_even_not_publisher(sf, seed):
    """审批权归创始人：发布者不是创始人时，创始人照样能批别人的版本。"""
    a = await apps_svc.create(sf, release_id=seed["release_id"],
                              applicant_id=seed["stranger"]["id"], want_name="我要")
    done = await apps_svc.decide(sf, app_id=a["id"], approver_id=seed["founder"]["id"],
                                 approver_is_founder=True, approve=True, reason="")
    assert done["status"] == "approved" and done["decided_by"] == seed["founder"]["id"]


async def test_publisher_without_founder_role_cannot_decide(sf, seed):
    """审批权集中：发布者本人（非创始人）对自己版本的应用也无权批。"""
    a = await apps_svc.create(sf, release_id=seed["release_id"],
                              applicant_id=seed["stranger"]["id"], want_name="我要")
    with pytest.raises(DeployError) as e:
        await apps_svc.decide(sf, app_id=a["id"], approver_id=seed["publisher"]["id"],
                              approver_is_founder=False, approve=True, reason="")
    assert getattr(e.value, "status", None) == 403
    # 路人同样被拒
    with pytest.raises(DeployError) as e2:
        await apps_svc.decide(sf, app_id=a["id"], approver_id=seed["stranger"]["id"],
                              approver_is_founder=False, approve=True, reason="")
    assert getattr(e2.value, "status", None) == 403


async def test_review_box_shows_all_pending_only_to_founder(sf, seed):
    """审批收件箱改版：创始人看全部 pending（不分版本归属），非创始人为空。"""
    a = await apps_svc.create(sf, release_id=seed["release_id"],
                              applicant_id=seed["stranger"]["id"], want_name="我要")
    rows = await apps_svc.list_box(sf, box="review",
                                   actor_id=seed["founder"]["id"], is_founder=True)
    assert [r["id"] for r in rows] == [a["id"]]
    # 发布者本人（非创始人）不再收到自己版本的审批项
    rows2 = await apps_svc.list_box(sf, box="review",
                                    actor_id=seed["publisher"]["id"], is_founder=False)
    assert rows2 == []
    # mine 收件箱不受角色影响
    rows3 = await apps_svc.list_box(sf, box="mine", actor_id=seed["stranger"]["id"])
    assert len(rows3) == 1 and rows3[0]["status"] == "pending"


async def test_withdrawn_release_refuses_new_application(sf, seed):
    async with sf() as s:
        await s.execute(text("UPDATE app_release SET status='withdrawn' WHERE id=:i"),
                        {"i": seed["release_id"]})
        await s.commit()
    with pytest.raises(DeployError):
        await apps_svc.create(sf, release_id=seed["release_id"],
                              applicant_id=seed["stranger"]["id"], want_name="我要")
