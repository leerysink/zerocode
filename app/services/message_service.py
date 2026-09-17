from sqlalchemy import text

async def add_message(session_factory, session_id: int, role: str, content=None,
                      tool_call_id=None, tool_name=None, tool_args=None, tool_result=None) -> dict:
    import json
    async with session_factory() as s:
        row = (await s.execute(text(
            "INSERT INTO message (session_id, seq, role, content, tool_call_id, tool_name, tool_args, tool_result) "
            "VALUES (:sid, COALESCE((SELECT max(seq)+1 FROM message WHERE session_id=:sid), 1), "
            ":role, :content, :tcid, :tname, CAST(:targs AS JSONB), CAST(:tresult AS JSONB)) "
            "RETURNING id, seq, role, content, tool_call_id, tool_name, tool_args, tool_result, created_at"),
            {"sid": session_id, "role": role, "content": content, "tcid": tool_call_id, "tname": tool_name,
             "targs": json.dumps(tool_args, ensure_ascii=False) if tool_args is not None else None,
             "tresult": json.dumps(tool_result, ensure_ascii=False) if tool_result is not None else None}
        )).mappings().first()
        await s.execute(text("UPDATE chat_session SET updated_at = now() WHERE id = :i"), {"i": session_id})
        await s.commit()
    return dict(row)
