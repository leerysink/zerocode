# tests/test_store_applications.py
"""申请单状态机：pending → approved/rejected，单向不可回头；只有发布者能审。"""
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
    async with sf() as s:
        rid = (await s.execute(text(
            "INSERT INTO app_release (app_id, slug, version, title, stack, anchor, fingerprint, "
            "created_by) VALUES (0, :sl, 'b1', '演示', 'vue3-fastapi', '[]'::jsonb, 'fp1', :c) "
            "RETURNING id"), {"sl": "demo" + secrets.token_hex(3), "c": publisher["id"]})).scalar()
        await s.commit()
    return {"publisher": publisher, "stranger": stranger, "release_id": int(rid)}


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
        await apps_svc.decide(sf, app_id=a["id"], approver_id=seed["publisher"]["id"],
                              approve=False, reason="")         # 驳回必须给原因
    done = await apps_svc.decide(sf, app_id=a["id"], approver_id=seed["publisher"]["id"],
                                 approve=False, reason="名字不合适")
    assert done["status"] == "rejected"
    with pytest.raises(DeployError):
        await apps_svc.decide(sf, app_id=a["id"], approver_id=seed["publisher"]["id"],
                              approve=True, reason="")          # 已决单不可翻


async def test_only_publisher_can_decide(sf, seed):
    a = await apps_svc.create(sf, release_id=seed["release_id"],
                              applicant_id=seed["stranger"]["id"], want_name="我要")
    with pytest.raises(DeployError) as e:
        await apps_svc.decide(sf, app_id=a["id"], approver_id=seed["stranger"]["id"],
                              approve=True, reason="")
    assert getattr(e.value, "status", None) == 403 or "发布者" in str(e.value)


async def test_withdrawn_release_refuses_new_application(sf, seed):
    async with sf() as s:
        await s.execute(text("UPDATE app_release SET status='withdrawn' WHERE id=:i"),
                        {"i": seed["release_id"]})
        await s.commit()
    with pytest.raises(DeployError):
        await apps_svc.create(sf, release_id=seed["release_id"],
                              applicant_id=seed["stranger"]["id"], want_name="我要")
