import json, pytest
from app.agent.llm import TOOLS, ScriptedLLM

def test_tools_schema():
    names = {t["function"]["name"] for t in TOOLS}
    assert names == {"list_dir", "read_file", "write_file", "edit_file", "ask_user"}
    w = next(t for t in TOOLS if t["function"]["name"] == "write_file")
    assert set(w["function"]["parameters"]["required"]) == {"path", "content"}

@pytest.mark.asyncio
async def test_scripted_llm_replays():
    llm = ScriptedLLM([
        [{"type": "content_delta", "content": "你"},
         {"type": "content_delta", "content": "好"},
         {"type": "done", "message": {"role": "assistant", "content": "你好", "tool_calls": None}}],
    ])
    outs = [e async for e in llm.chat_stream([{"role": "user", "content": "hi"}], TOOLS)]
    assert outs[-1]["type"] == "done" and outs[-1]["message"]["content"] == "你好"
    assert llm.received[0][0]["role"] == "user"
