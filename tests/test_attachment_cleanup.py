import os
import time
from pathlib import Path

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


async def test_cleanup_continues_past_bad_entry(test_engine, tmp_path, monkeypatch):
    """单个坏条目不能让整次扫描停摆（外层的 catch 会吞掉后续整棵树）。

    让 Path.stat 对**第一个**被处理的 png 抛无 errno 的 OSError——Path.is_file()
    对这类异常不当作「不存在」吞掉，会原样冒泡：旧代码直接冒到外层 catch，整个目录
    扫描中止（三个文件全留）；逐条目容错后只跳过这一个，其余照删。
    以「第一个被处理的文件」为靶，不依赖 iter 顺序，故结果是确定的。
    """
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path))
    sf = make_session_factory(test_engine)
    sid = await mk_session(test_engine, tmp_path)
    uploads = tmp_path / "users" / "7" / "chat_uploads" / str(sid)
    uploads.mkdir(parents=True)
    old = time.time() - 25 * 3600
    for name in ("a.png", "b.png", "c.png"):
        p = uploads / name
        p.write_bytes(PNG)
        os.utime(p, (old, old))

    real_stat = Path.stat
    seen = {"n": 0, "bad": None}

    def flaky_stat(self, *args, **kwargs):
        if self.suffix == ".png":
            seen["n"] += 1
            if seen["n"] == 1:
                seen["bad"] = self
                raise OSError("stat 坏了")
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", flaky_stat)
    await att.cleanup_orphans(sf, ttl_hours=24)
    left = {p.name for p in uploads.glob("*.png")}
    assert seen["bad"] is not None
    assert left == {seen["bad"].name}


async def test_cleanup_keeps_bound_attachment_with_old_file(
        test_engine, tmp_path, monkeypatch):
    """已绑定行不受 TTL 影响：文件 mtime 再老也得留着（目录兜底扫描按引用跳过）。"""
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path))
    sf = make_session_factory(test_engine)
    sid = await mk_session(test_engine, tmp_path)
    young = await att.create_attachment(sf, 7, sid, "young.png", PNG, "image/png")
    old = await att.create_attachment(sf, 7, sid, "old.png", PNG, "image/png")
    async with sf() as s:
        mid = (await s.execute(sql(
            "INSERT INTO message (session_id, seq, role, content) "
            "VALUES (:s, 1, 'user', 'x') RETURNING id"), {"s": sid})).scalar()
        await s.commit()
        await att.bind_to_message(s, sid, mid, [young["id"], old["id"]])
        # 让「行」也超期：已绑定是内容，不是垃圾，TTL 只清未绑定行
        await s.execute(sql(
            "UPDATE message_attachment SET created_at = now() - make_interval(hours => 25) "
            "WHERE id=:i"), {"i": old["id"]})
        await s.commit()

    aged = time.time() - 25 * 3600
    sess_dir = tmp_path / "users" / "7" / "chat_uploads" / str(sid)
    for p in sess_dir.iterdir():
        os.utime(p, (aged, aged))

    assert await att.cleanup_orphans(sf, ttl_hours=24) == 0
    async with sf() as s:
        ids = set((await s.execute(sql("SELECT id FROM message_attachment"))).scalars().all())
    assert ids == {young["id"], old["id"]}
    assert len(list(sess_dir.iterdir())) == 2


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
