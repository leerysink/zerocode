import os
import time

import pytest
from sqlalchemy import text as sql

from app.db import make_session_factory
from app.services import attachment_service as att
from tests.test_agent_loop import mk_session

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


async def _make_orphan(sf, sid, tmp_path, age_hours):
    row = await att.create_attachment(sf, 7, sid, "o.png", PNG, "image/png")
    async with sf() as s:
        await s.execute(sql(
            "UPDATE message_attachment SET created_at = now() - make_interval(hours => :h) "
            "WHERE id=:i"), {"h": age_hours, "i": row["id"]})
        await s.commit()
    return row


async def test_cleanup_removes_old_orphans_only(test_engine, tmp_path, monkeypatch):
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path))
    sf = make_session_factory(test_engine)
    sid = await mk_session(test_engine, tmp_path)
    old = await _make_orphan(sf, sid, tmp_path, 25)
    young = await _make_orphan(sf, sid, tmp_path, 1)
    kept = await att.create_attachment(sf, 7, sid, "k.png", PNG, "image/png")
    async with sf() as s:
        mid = (await s.execute(sql(
            "INSERT INTO message (session_id, seq, role, content) "
            "VALUES (:s, 1, 'user', 'x') RETURNING id"), {"s": sid})).scalar()
        await s.commit()
        await att.bind_to_message(s, sid, mid, [kept["id"]])
        await s.commit()

    removed = await att.cleanup_orphans(sf, ttl_hours=24)
    assert removed == 1
    async with sf() as s:
        ids = set((await s.execute(sql("SELECT id FROM message_attachment"))).scalars().all())
    assert ids == {young["id"], kept["id"]}
    assert len(list((tmp_path / "users" / "7" / "chat_uploads" / str(sid)).iterdir())) == 2


async def test_cleanup_removes_dead_session_dir_and_unreferenced_file(
        test_engine, tmp_path, monkeypatch):
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path))
    sf = make_session_factory(test_engine)
    dead = tmp_path / "users" / "7" / "chat_uploads" / "999999"
    dead.mkdir(parents=True)
    (dead / "x.png").write_bytes(PNG)
    old = time.time() - 25 * 3600
    os.utime(dead, (old, old))

    live_sid = await mk_session(test_engine, tmp_path)
    live = tmp_path / "users" / "7" / "chat_uploads" / str(live_sid)
    live.mkdir(parents=True)
    stray = live / "stray.png"
    stray.write_bytes(PNG)
    os.utime(stray, (old, old))

    await att.cleanup_orphans(sf, ttl_hours=24)
    assert not dead.exists()
    assert not stray.exists()


async def test_cleanup_never_raises(test_engine, tmp_path, monkeypatch):
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path))
    sf = make_session_factory(test_engine)
    sid = await mk_session(test_engine, tmp_path)
    await _make_orphan(sf, sid, tmp_path, 25)
    monkeypatch.setattr(att.shutil, "rmtree", lambda *a, **k: (_ for _ in ()).throw(OSError("x")))
    assert isinstance(await att.cleanup_orphans(sf, ttl_hours=24), int)


async def test_delete_session_removes_upload_dir(client, tmp_path, monkeypatch):
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path / "data"))
    ws = (await client.post("/api/workspaces", json={"root_path": str(tmp_path)})).json()
    sess = (await client.post("/api/sessions", json={"workspace_id": ws["id"]})).json()
    sid = sess["id"]
    r = await client.post(
        f"/api/sessions/{sid}/attachments",
        params={"filename": "a.png"}, content=PNG,
        headers={"Content-Type": "image/png"})
    assert r.status_code == 201, r.text
    dirs = list((tmp_path / "data" / "users").glob(f"*/chat_uploads/{sid}"))
    assert len(dirs) == 1 and dirs[0].is_dir()

    d = await client.delete(f"/api/sessions/{sid}")
    assert d.status_code == 200, d.text
    assert not list((tmp_path / "data" / "users").glob(f"*/chat_uploads/{sid}"))
