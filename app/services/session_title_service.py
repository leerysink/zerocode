"""会话自动命名（2026-09-21 需求：跟 DSH 一样按输入内容自动命名，无须手动起名）。

DSH 的确定性兜底（session-title fallback）同款口径：取首条用户消息的首个非空行，
折叠行内空白，截断 TITLE_MAX 字（超长加省略号）。不接 LLM——零延迟零成本，
且用户手改过的标题（≠ DEFAULT_TITLE）永不覆盖。
"""
from sqlalchemy import text

DEFAULT_TITLE = "新会话"
TITLE_MAX = 24  # chat_session.title 为 VARCHAR(200)，24 字足够侧栏一行展示


def derive_title(text_: str | None) -> str | None:
    """首个非空行 → 折叠空白 → 截断 24 字；派生不出内容时返回 None（保持默认）。"""
    if not text_:
        return None
    for line in text_.splitlines():
        collapsed = " ".join(line.split())
        if collapsed:
            return collapsed if len(collapsed) <= TITLE_MAX else collapsed[:TITLE_MAX] + "…"
    return None


async def ensure_session_title(session_factory, session_id: int, user_text: str) -> str | None:
    """会话标题仍是默认「新会话」时按首条消息改名，返回新标题；否则 None。

    条件更新（WHERE title=DEFAULT）天然幂等：并发/重发只有一边改得到，
    用户手动改过名的会话永不覆盖。
    """
    title = derive_title(user_text)
    if not title:
        return None
    async with session_factory() as s:
        r = await s.execute(text(
            "UPDATE chat_session SET title=:t, updated_at=now() "
            "WHERE id=:i AND title=:d"),
            {"t": title, "i": session_id, "d": DEFAULT_TITLE})
        await s.commit()
        return title if r.rowcount else None
