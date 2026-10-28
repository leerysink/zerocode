import logging
import os
from pathlib import Path

from sqlalchemy import text as sql

from app.services import attachment_service as att

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
SVG = b"<svg xmlns='http://www.w3.org/2000/svg'></svg>"


async def _mk_session(client, tmp_path):
    ws = (await client.post("/api/workspaces", json={"root_path": str(tmp_path)})).json()
    return (await client.post("/api/sessions", json={"workspace_id": ws["id"]})).json()


async def _upload(client, sid, data=PNG, filename="a.png", mime="image/png"):
    return await client.post(
        f"/api/sessions/{sid}/attachments",
        params={"filename": filename}, content=data,
        headers={"Content-Type": mime})


async def test_upload_and_read(client, tmp_path, monkeypatch):
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path / "data"))
    sess = await _mk_session(client, tmp_path)
    r = await _upload(client, sess["id"], filename="截图 1.png")
    assert r.status_code == 201, r.text
    att_row = r.json()
    assert att_row["filename"] == "截图 1.png"
    assert att_row["mime"] == "image/png"
    assert att_row["size_bytes"] == len(PNG)
    assert att_row["url"] == f"/api/attachments/{att_row['id']}"

    got = await client.get(att_row["url"])
    assert got.status_code == 200 and got.content == PNG
    assert got.headers["content-type"] == "image/png"
    assert got.headers["x-content-type-options"] == "nosniff"
    assert "private" in got.headers["cache-control"]
    assert "immutable" in got.headers["cache-control"]
    assert "filename*=UTF-8''" in got.headers["content-disposition"]


async def test_upload_magic_wins_over_declared_mime(client, tmp_path, monkeypatch):
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path / "data"))
    sess = await _mk_session(client, tmp_path)
    r = await _upload(client, sess["id"], data=PNG, mime="image/jpeg")
    assert r.status_code == 201 and r.json()["mime"] == "image/png"


async def test_upload_rejects_svg_text_and_empty(client, tmp_path, monkeypatch):
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path / "data"))
    sess = await _mk_session(client, tmp_path)
    assert (await _upload(client, sess["id"], data=SVG, filename="x.svg", mime="image/svg+xml")).status_code == 400
    assert (await _upload(client, sess["id"], data=b"hello", filename="x.txt", mime="text/plain")).status_code == 400
    assert (await _upload(client, sess["id"], data=b"", filename="x.png")).status_code == 400


async def test_upload_rejects_non_image_content_type(client, tmp_path, monkeypatch):
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path / "data"))
    sess = await _mk_session(client, tmp_path)
    r = await _upload(client, sess["id"], data=PNG, mime="application/octet-stream")
    assert r.status_code == 400


async def test_upload_size_limits(client, tmp_path, monkeypatch):
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(att, "MAX_ATTACHMENT_BYTES", 10)
    monkeypatch.setattr(att, "MAX_MESSAGE_BYTES", 200)
    sess = await _mk_session(client, tmp_path)
    # 单文件超限（92 字节 > 10）
    assert (await _upload(client, sess["id"], data=PNG + b"x" * 20)).status_code == 413

    monkeypatch.setattr(att, "MAX_ATTACHMENT_BYTES", 1000)
    body = PNG + b"x" * 52  # 124 字节，单文件合法
    assert (await _upload(client, sess["id"], data=body)).status_code == 201
    # 累计 248 字节 > MAX_MESSAGE_BYTES=200
    assert (await _upload(client, sess["id"], data=body)).status_code == 413


async def test_filename_sanitized(client, tmp_path, monkeypatch):
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path / "data"))
    sess = await _mk_session(client, tmp_path)
    r = await _upload(client, sess["id"], filename="../evil.png")
    assert r.status_code == 201 and r.json()["filename"] == "evil.png"
    r = await _upload(client, sess["id"], filename="")
    assert r.json()["filename"] == "image.png"
    r = await _upload(client, sess["id"], filename="x" * 300 + ".png")
    assert len(r.json()["filename"]) == 120


async def test_isolation_and_404(two_auth_clients, tmp_path, monkeypatch):
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path / "data"))
    alice, bob = two_auth_clients
    sess = await _mk_session(alice, tmp_path)
    att_row = (await _upload(alice, sess["id"])).json()

    assert (await _upload(bob, sess["id"])).status_code == 404
    assert (await bob.get(att_row["url"])).status_code == 404
    assert (await alice.get("/api/attachments/999999")).status_code == 404


async def test_upload_oserror_is_logged_not_echoed(client, tmp_path, monkeypatch, caplog):
    """落盘失败 500：日志留全文，响应体不回显数据区路径。"""
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path / "data"))
    sess = await _mk_session(client, tmp_path)

    async def boom(*args, **kwargs):
        raise OSError("/very/secret/path")

    monkeypatch.setattr(att, "create_attachment", boom)
    with caplog.at_level(logging.ERROR):
        r = await _upload(client, sess["id"])
    assert r.status_code == 500, r.text
    assert "secret" not in r.text
    assert r.json()["detail"] == "数据区不可写，请稍后重试"
    assert any(rec.exc_info and "/very/secret/path" in str(rec.exc_info[1])
               for rec in caplog.records)


async def test_missing_file_returns_404(client, tmp_path, monkeypatch):
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path / "data"))
    sess = await _mk_session(client, tmp_path)
    att_row = (await _upload(client, sess["id"])).json()
    for p in Path(tmp_path / "data").rglob("*.png"):
        p.unlink()
    assert (await client.get(att_row["url"])).status_code == 404
