from sqlalchemy import text


async def add_message(session_factory, session_id: int, role: str, content=None, context_content=None,
                      reasoning=None, tool_call_id=None, tool_name=None, tool_args=None, tool_result=None,
                      attachment_ids: list[int] | None = None) -> dict:
    """消息落库；带 attachment_ids 时在同一事务里绑定附件。

    绑定失败（附件不属于本会话/已被占用/已绑定）抛 ValueError 并整条回滚：
    没有半条消息，也没有「消息在、图没了」的悬空引用。"""
    import json
    from app.services import attachment_service
    async with session_factory() as s:
        row = (await s.execute(text(
            "INSERT INTO message (session_id, seq, role, content, context_content, reasoning, tool_call_id, tool_name, tool_args, tool_result) "
            "VALUES (:sid, COALESCE((SELECT max(seq)+1 FROM message WHERE session_id=:sid), 1), "
            ":role, :content, :context_content, :reasoning, :tcid, :tname, CAST(:targs AS JSONB), CAST(:tresult AS JSONB)) "
            "RETURNING id, seq, role, content, context_content, reasoning, tool_call_id, tool_name, tool_args, tool_result, created_at"),
            {"sid": session_id, "role": role, "content": content, "context_content": context_content,
             "reasoning": reasoning,
             "tcid": tool_call_id, "tname": tool_name,
             "targs": json.dumps(tool_args, ensure_ascii=False) if tool_args is not None else None,
             "tresult": json.dumps(tool_result, ensure_ascii=False) if tool_result is not None else None}
        )).mappings().first()
        attachments = []
        if attachment_ids:
            try:
                attachments = await attachment_service.bind_to_message(
                    s, session_id, row["id"], attachment_ids)
            except ValueError:
                await s.rollback()
                raise
        await s.execute(text("UPDATE chat_session SET updated_at = now() WHERE id = :i"), {"i": session_id})
        await s.commit()
    out = dict(row)
    out["attachments"] = attachments
    return out
