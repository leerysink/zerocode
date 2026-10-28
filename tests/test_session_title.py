from app.db import make_session_factory
from app.services.session_title_service import DEFAULT_TITLE, ensure_session_title
from tests.test_agent_loop import mk_session


async def test_pure_image_first_message_titles_from_filename(test_engine, tmp_path):
    sf = make_session_factory(test_engine)
    sid = await mk_session(test_engine, tmp_path)
    assert await ensure_session_title(sf, sid, "", ["截图 2026.png"]) == "图片：截图 2026.png"


async def test_text_still_wins_over_attachment(test_engine, tmp_path):
    sf = make_session_factory(test_engine)
    sid = await mk_session(test_engine, tmp_path)
    assert await ensure_session_title(sf, sid, "看看这个", ["a.png"]) == "看看这个"


async def test_manual_title_never_overwritten(test_engine, tmp_path):
    sf = make_session_factory(test_engine)
    sid = await mk_session(test_engine, tmp_path)
    assert await ensure_session_title(sf, sid, "旧标题", ["a.png"]) == "旧标题"
    assert await ensure_session_title(sf, sid, "", ["b.png"]) is None
