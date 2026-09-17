"""上下文压缩（spec §4.3）：水位读取、字符估算、LLM 摘要、落库与事件下发。

- 摘要覆盖 up_to_seq 之前的所有消息；保留最近 keep_recent 条原文；
- 摘要拼装在 loop._build_messages 中注入 system 尾部 <context_summary>；
- 失败只 send error 事件并返回 None，不抛出、不改会话状态、不阻断对话。
"""
import json
from sqlalchemy import text

SUMMARY_PROMPT = (
    "你是会话摘要器。把以下对话与工具结果压缩为继续工作所需的最小上下文，"
    "必须保留：任务目标、关键决策、涉及文件与改动、当前任务清单、遗留问题。"
    "直接输出摘要正文，不要客套。")

async def latest(sf, session_id: int) -> tuple[str | None, int]:
    async with sf() as s:
        row = (await s.execute(text(
            "SELECT summary, up_to_seq FROM compaction WHERE session_id=:i ORDER BY id DESC LIMIT 1"),
            {"i": session_id})).first()
    return (row[0], row[1]) if row else (None, 0)

async def rows_after(sf, session_id: int, after_seq: int) -> list[dict]:
    async with sf() as s:
        rows = (await s.execute(text(
            "SELECT id, seq, role, content, tool_result FROM message "
            "WHERE session_id=:i AND seq > :a ORDER BY seq"),
            {"i": session_id, "a": after_seq})).mappings().all()
    return [dict(r) for r in rows]

def estimate_chars(rows: list[dict]) -> int:
    n = 0
    for r in rows:
        n += len(r.get("content") or "")
        if r.get("tool_result") is not None:
            n += len(json.dumps(r["tool_result"], ensure_ascii=False))
    return n

async def compact(sf, llm, session_id: int, send, keep_recent: int = 8) -> dict | None:
    _, wm = await latest(sf, session_id)
    rows = await rows_after(sf, session_id, wm)
    if len(rows) <= keep_recent:
        return None
    head, tail = rows[:-keep_recent], rows[-keep_recent:]
    lines = []
    for r in head:
        body = r["content"]
        if r["role"] == "tool" and r["tool_result"] is not None:
            body = json.dumps(r["tool_result"], ensure_ascii=False)
        lines.append(f"[{r['seq']}][{r['role']}] {body}")
    digest = "\n".join(lines)[:120_000]
    parts = []
    final = ""  # done 事件聚合全文（OpenAI 兼容流 done.message.content == 全部 delta 拼接）
    try:
        async for ev in llm.chat_stream(
                [{"role": "system", "content": SUMMARY_PROMPT},
                 {"role": "user", "content": digest}], []):
            if ev["type"] == "content_delta":
                parts.append(ev["content"])
            elif ev["type"] == "done":
                final = (ev.get("message") or {}).get("content") or ""
    except Exception as e:
        await send({"type": "error", "message": f"压缩失败，已保留原文: {e}"})
        return None
    # done 兜底：摘要轮只回 done（无增量 delta）时也能取到摘要（test_auto_compaction_triggers 契约）
    summary = "".join(parts).strip() or final.strip()
    if not summary:
        await send({"type": "error", "message": "压缩失败：模型未返回摘要，已保留原文"})
        return None
    up_to = head[-1]["seq"]
    async with sf() as s:
        await s.execute(text(
            "INSERT INTO compaction (session_id, summary, up_to_seq) VALUES (:i, :s, :u)"),
            {"i": session_id, "s": summary, "u": up_to})
        await s.commit()
    out = {"summary": summary, "up_to_seq": up_to}
    await send({"type": "compaction_done", **out})
    return out
