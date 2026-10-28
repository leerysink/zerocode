import pytest
from pathlib import Path
from sqlalchemy import text as sql
from app.db import make_session_factory
from app.services import attachment_service as att
from tests.test_agent_loop import mk_session

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
WEBP = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 64
GIF = b"GIF89a" + b"\x00" * 64


def test_sniff_image_mime_magic_bytes():
    assert att.sniff_image_mime(PNG) == "image/png"
    assert att.sniff_image_mime(JPEG) == "image/jpeg"
    assert att.sniff_image_mime(WEBP) == "image/webp"
    assert att.sniff_image_mime(GIF) == "image/gif"
    assert att.sniff_image_mime(b"<svg xmlns='http://www.w3.org/2000/svg'></svg>") is None
    assert att.sniff_image_mime(b"not an image") is None


def test_safe_filename_strips_path_and_controls():
    assert att.safe_filename("../x.png", "image/png") == "x.png"
    assert att.safe_filename("..\\x.png", "image/png") == "x.png"
    assert att.safe_filename("a\x00b\x1fc.png", "image/png") == "abc.png"
    assert att.safe_filename("", "image/png") == "image.png"
    assert att.safe_filename("..", "image/jpeg") == "image.jpg"
    assert len(att.safe_filename("x" * 300 + ".png", "image/png")) == 120


@pytest.mark.parametrize("raw,mime,ext", [
    (PNG, "image/png", ".png"), (JPEG, "image/jpeg", ".jpg"),
    (WEBP, "image/webp", ".webp"), (GIF, "image/gif", ".gif"),
])
async def test_create_attachment_writes_file_and_unbound_row(
        test_engine, tmp_path, monkeypatch, raw, mime, ext):
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path))
    sf = make_session_factory(test_engine)
    sid = await mk_session(test_engine, tmp_path)
    row = await att.create_attachment(sf, 7, sid, "截图 1" + ext, raw, mime)
    assert row["filename"] == "截图 1" + ext
    assert row["mime"] == mime and row["size_bytes"] == len(raw)
    assert row["url"] == f"/api/attachments/{row['id']}"
    files = list((tmp_path / "users" / "7" / "chat_uploads" / str(sid)).iterdir())
    assert len(files) == 1 and files[0].read_bytes() == raw
    async with sf() as s:
        bound = (await s.execute(sql(
            "SELECT message_id FROM message_attachment WHERE id=:i"), {"i": row["id"]})).scalar()
    assert bound is None


async def test_staged_bytes_and_bindable(test_engine, tmp_path, monkeypatch):
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path))
    sf = make_session_factory(test_engine)
    sid = await mk_session(test_engine, tmp_path)
    a = await att.create_attachment(sf, 7, sid, "a.png", PNG, "image/png")
    b = await att.create_attachment(sf, 7, sid, "b.png", PNG, "image/png")
    assert await att.staged_bytes(sf, sid) == len(PNG) * 2
    rows = await att.load_bindable(sf, sid, [a["id"], b["id"]])
    assert [r["id"] for r in rows] == [a["id"], b["id"]]
    assert (await att.for_ids(sf, sid, [a["id"]]))[a["id"]]["filename"] == "a.png"

    other_sid = await mk_session(test_engine, tmp_path / "other")
    with pytest.raises(att.AttachmentError):
        await att.load_bindable(sf, other_sid, [a["id"]])


async def test_load_bindable_rejects_bound_missing_and_oversize(
        test_engine, tmp_path, monkeypatch):
    """三个分支在真实常量下各自触发：已绑定 / 文件缺失 / 合计超限。

    早前版本把 MAX_MESSAGE_BYTES 压到 10，已绑定与缺文件都会先撞合计门，
    分支各自的文案与状态码从没被执行过（门形同虚设）。"""
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path))
    sf = make_session_factory(test_engine)
    sid = await mk_session(test_engine, tmp_path)
    a = await att.create_attachment(sf, 7, sid, "a.png", PNG, "image/png")
    b = await att.create_attachment(sf, 7, sid, "b.png", PNG, "image/png")

    # 已绑定：被 message_id IS NULL 过滤剔除 → 400「不存在或已被使用」
    async with sf() as s:
        mid = (await s.execute(sql(
            "INSERT INTO message (session_id, seq, role, content) "
            "VALUES (:s, 1, 'user', 'x') RETURNING id"), {"s": sid})).scalar()
        await s.commit()
        bound = await att.bind_to_message(s, sid, mid, [a["id"]])
        await s.commit()
    assert [r["id"] for r in bound] == [a["id"]]
    with pytest.raises(att.AttachmentError) as bound_err:
        await att.load_bindable(sf, sid, [a["id"]])
    assert bound_err.value.status_code == 400
    assert "已被使用" in str(bound_err.value)

    # 文件缺失：行在、文件被外部删掉 → 400，文案要求重新上传
    async with sf() as s:
        b_path = (await s.execute(sql(
            "SELECT storage_path FROM message_attachment WHERE id=:i"),
            {"i": b["id"]})).scalar()
    att.resolve_storage_path(b_path).unlink()
    with pytest.raises(att.AttachmentError) as missing_err:
        await att.load_bindable(sf, sid, [b["id"]])
    assert missing_err.value.status_code == 400
    assert "文件缺失" in str(missing_err.value)

    # 合计超限：4 张各 2.7MB（单张 < 5MB、张数 4 不触数量门），合计 10.8MB > 10MB → 413
    chunk = PNG + b"\x00" * 2_700_000
    ids = [(await att.create_attachment(sf, 7, sid, f"{i}.png", chunk, "image/png"))["id"]
           for i in range(4)]
    with pytest.raises(att.AttachmentError) as total_err:
        await att.load_bindable(sf, sid, ids)
    assert total_err.value.status_code == 413
    assert "合计" in str(total_err.value)


async def test_load_bindable_rejects_fifth_image(test_engine, tmp_path, monkeypatch):
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path))
    sf = make_session_factory(test_engine)
    sid = await mk_session(test_engine, tmp_path)
    ids = []
    for i in range(5):
        row = await att.create_attachment(sf, 7, sid, f"{i}.png", PNG, "image/png")
        ids.append(row["id"])
    with pytest.raises(att.AttachmentError) as e:
        await att.load_bindable(sf, sid, ids)
    assert e.value.status_code == 400


async def test_for_messages_and_for_llm(test_engine, tmp_path, monkeypatch):
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path))
    sf = make_session_factory(test_engine)
    sid = await mk_session(test_engine, tmp_path)
    a = await att.create_attachment(sf, 7, sid, "a.png", PNG, "image/png")
    b = await att.create_attachment(sf, 7, sid, "b.png", JPEG, "image/jpeg")
    async with sf() as s:
        mid = (await s.execute(sql(
            "INSERT INTO message (session_id, seq, role, content) "
            "VALUES (:s, 1, 'user', '看图') RETURNING id"), {"s": sid})).scalar()
        await s.commit()
        await att.bind_to_message(s, sid, mid, [a["id"], b["id"]])
        await s.commit()
    grouped = await att.for_messages(sf, sid, [mid])
    assert [x["id"] for x in grouped[mid]] == [a["id"], b["id"]]
    llm = await att.for_llm(sf, [mid])
    assert llm[mid][0]["data_url"].startswith("data:image/png;base64,")
    assert llm[mid][1]["data_url"].startswith("data:image/jpeg;base64,")

    # 文件缺失仍返回条目，data_url=None（供 loop 降级成占位）
    next((tmp_path / "users" / "7" / "chat_uploads" / str(sid)).glob("*.jpg")).unlink()
    llm2 = await att.for_llm(sf, [mid])
    assert llm2[mid][1]["data_url"] is None


async def test_for_messages_and_for_llm_order_by_id(test_engine, tmp_path, monkeypatch):
    """顺序是契约：无 ORDER BY 时必然按堆物理顺序返回，本测试据此确定性失败。

    两个附件先 a 后 b 反序改写 id：Postgres 的 UPDATE 是写新元组版本（不是原地改），
    版本按语句顺序追加到页尾，故堆内物理顺序 [a', b'] 对应 id [9002, 9001]，与 id
    顺序相反。顺序扫描（或按 message_id 索引回表，同按 TID 排序）只会给出物理顺序。
    """
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path))
    sf = make_session_factory(test_engine)
    sid = await mk_session(test_engine, tmp_path)
    a = await att.create_attachment(sf, 7, sid, "a.png", PNG, "image/png")
    b = await att.create_attachment(sf, 7, sid, "b.png", JPEG, "image/jpeg")
    async with sf() as s:
        mid = (await s.execute(sql(
            "INSERT INTO message (session_id, seq, role, content) "
            "VALUES (:s, 1, 'user', '看图') RETURNING id"), {"s": sid})).scalar()
        await s.commit()
        await att.bind_to_message(s, sid, mid, [a["id"], b["id"]])
        await s.commit()
        await s.execute(sql("UPDATE message_attachment SET id=9002 WHERE id=:i"), {"i": a["id"]})
        await s.execute(sql("UPDATE message_attachment SET id=9001 WHERE id=:i"), {"i": b["id"]})
        await s.commit()

    grouped = await att.for_messages(sf, sid, [mid])
    assert [x["id"] for x in grouped[mid]] == [9001, 9002]
    assert [x["filename"] for x in grouped[mid]] == ["b.png", "a.png"]
    llm = await att.for_llm(sf, [mid])
    assert [x["id"] for x in llm[mid]] == [9001, 9002]
    # data_url 顺序必须与 id 顺序一致（模型收到的图片顺序 = 气泡顺序）
    assert llm[mid][0]["data_url"].startswith("data:image/jpeg;base64,")
    assert llm[mid][1]["data_url"].startswith("data:image/png;base64,")


async def test_resolve_storage_path_blocks_traversal(tmp_path, monkeypatch):
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path))
    assert att.resolve_storage_path("../etc/passwd") is None
    assert att.resolve_storage_path("users/1/a.png") == (tmp_path / "users/1/a.png").resolve()
