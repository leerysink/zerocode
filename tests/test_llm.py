import json, pytest
from types import SimpleNamespace

import app.agent.llm as llm_mod
from app.agent.llm import TOOLS, ScriptedLLM, OpenAICompatLLM

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


# ---------- OpenAICompatLLM 流式聚合 stub 测试（终审要求：merge 前必须补回归网） ----------

def _delta(content=None, tool_calls=None):
    return SimpleNamespace(content=content, tool_calls=tool_calls)

def _tc(index, id=None, name=None, arguments=None):
    """delta.tool_calls 分片：后续分片 id/name 常为 None（只带 arguments 增量）。"""
    return SimpleNamespace(index=index, id=id,
                           function=SimpleNamespace(name=name, arguments=arguments))

def _chunk(delta):
    if delta is None:
        return SimpleNamespace(choices=[])  # usage 块等空 choices chunk
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta)])

class _FakeAsyncOpenAI:
    """AsyncOpenAI 替身：create 返回逐 chunk 的异步流；记录每次 create 的 kwargs。"""
    def __init__(self, chunks, calls):
        self._chunks = chunks
        self.calls = calls
        self.chat = SimpleNamespace(completions=self)

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        async def _stream():
            for c in self._chunks:
                yield c
        return _stream()

def _make_llm(monkeypatch, chunks):
    """不联网不碰 DB：monkeypatch 模块内 AsyncOpenAI，返回 (llm, fake)。"""
    calls = []
    fake = _FakeAsyncOpenAI(chunks, calls)
    monkeypatch.setattr(llm_mod, "AsyncOpenAI", lambda **kw: fake)
    llm = OpenAICompatLLM(base_url="http://127.0.0.1:9/v1", api_key="EMPTY", model="test-model")
    return llm, fake

async def _collect(llm):
    return [ev async for ev in llm.chat_stream([{"role": "user", "content": "hi"}], TOOLS)]

@pytest.mark.asyncio
async def test_stream_aggregates_content_deltas(monkeypatch):
    # 场景 1：多 delta content 聚合为一个 content_delta 流 + done content
    llm, _ = _make_llm(monkeypatch, [
        _chunk(_delta(content="你")),
        _chunk(_delta(content="好")),
        _chunk(_delta(content="")),  # 空 content 不产生事件
    ])
    evs = await _collect(llm)
    assert [e["type"] for e in evs] == ["content_delta", "content_delta", "done"]
    assert evs[0]["content"] == "你" and evs[1]["content"] == "好"
    assert evs[-1]["message"]["role"] == "assistant"
    assert evs[-1]["message"]["content"] == "你好"
    assert evs[-1]["message"]["tool_calls"] is None

@pytest.mark.asyncio
async def test_stream_parallel_tool_calls(monkeypatch):
    # 场景 2：index 0/1 并行交错、arguments 跨 chunk 分片拼接、后续分片 id/name 为 None 不覆盖
    llm, _ = _make_llm(monkeypatch, [
        _chunk(_delta(tool_calls=[_tc(0, id="call_a", name="write_file", arguments='{"pa')])),
        _chunk(_delta(tool_calls=[_tc(1, id="call_b", name="read_file", arguments='{"pa')])),
        _chunk(_delta(tool_calls=[_tc(0, arguments='th": "a.txt"}')])),
        _chunk(_delta(tool_calls=[_tc(1, arguments='th": "a.txt"}')])),
    ])
    evs = await _collect(llm)
    assert all(e["type"] == "done" for e in evs)  # 全程无 content 事件
    assert evs[-1]["message"]["content"] is None
    assert evs[-1]["message"]["tool_calls"] == [  # 按 index 排序 + 分片拼接结果
        {"id": "call_a", "function": {"name": "write_file", "arguments": '{"path": "a.txt"}'}},
        {"id": "call_b", "function": {"name": "read_file", "arguments": '{"path": "a.txt"}'}},
    ]

@pytest.mark.asyncio
async def test_stream_content_with_tool_calls(monkeypatch):
    # 场景 3：content 与 tool_calls 共存
    llm, _ = _make_llm(monkeypatch, [
        _chunk(_delta(content="先读文件")),
        _chunk(_delta(tool_calls=[_tc(0, id="c1", name="read_file", arguments='{"path": "a.txt"}')])),
    ])
    evs = await _collect(llm)
    assert [e["type"] for e in evs] == ["content_delta", "done"]
    assert evs[-1]["message"]["content"] == "先读文件"
    assert evs[-1]["message"]["tool_calls"] == [
        {"id": "c1", "function": {"name": "read_file", "arguments": '{"path": "a.txt"}'}}]

@pytest.mark.asyncio
async def test_stream_skips_empty_choices(monkeypatch):
    # 场景 4：空 choices chunk（如 usage 块）跳过且不影响聚合
    llm, _ = _make_llm(monkeypatch, [
        _chunk(_delta(content="a")),
        _chunk(None),
        _chunk(_delta(content="b")),
    ])
    evs = await _collect(llm)
    assert [e["type"] for e in evs] == ["content_delta", "content_delta", "done"]
    assert evs[-1]["message"]["content"] == "ab"

@pytest.mark.asyncio
async def test_stream_empty_round_done_none(monkeypatch):
    # 场景 5：全空轮 → done message content=None tool_calls=None
    llm, _ = _make_llm(monkeypatch, [
        _chunk(_delta(content=None, tool_calls=None)),
        _chunk(_delta(content="", tool_calls=[])),
    ])
    evs = await _collect(llm)
    assert [e["type"] for e in evs] == ["done"]
    assert evs[-1]["message"] == {"role": "assistant", "content": None, "tool_calls": None, "reasoning": None}

@pytest.mark.asyncio
async def test_stream_create_kwargs_passthrough(monkeypatch):
    # 场景 6：create 收到 stream=True 与 messages/tools/model 透传
    messages = [{"role": "user", "content": "hi"}]
    llm, fake = _make_llm(monkeypatch, [_chunk(_delta(content="ok"))])
    evs = await _collect(llm)
    assert [e["type"] for e in evs] == ["content_delta", "done"]  # 与 ScriptedLLM 协议形状一致
    assert len(fake.calls) == 1
    kw = fake.calls[0]
    assert kw["stream"] is True
    assert kw["model"] == "test-model"
    assert kw["messages"] == messages
    assert kw["tools"] == TOOLS

# ---------- reasoning_content 解析（Task 4） ----------

def _rdelta(reasoning=None, content=None):
    return SimpleNamespace(content=content, tool_calls=None, reasoning_content=reasoning)

@pytest.mark.asyncio
async def test_stream_reasoning_delta_and_done(monkeypatch):
    llm, _ = _make_llm(monkeypatch, [
        _chunk(_rdelta(reasoning="先想")),
        _chunk(_rdelta(reasoning="清楚")),
        _chunk(_rdelta(content="答案")),
    ])
    evs = await _collect(llm)
    assert [e["type"] for e in evs] == ["reasoning_delta", "reasoning_delta", "content_delta", "done"]
    assert evs[0]["content"] == "先想"
    assert evs[-1]["message"]["reasoning"] == "先想清楚"

@pytest.mark.asyncio
async def test_stream_no_reasoning_no_event(monkeypatch):
    # 既有 _delta（无 reasoning_content 属性）不产生 reasoning 事件，done.reasoning=None
    llm, _ = _make_llm(monkeypatch, [_chunk(_delta(content="ok"))])
    evs = await _collect(llm)
    assert all(e["type"] != "reasoning_delta" for e in evs)
    assert evs[-1]["message"]["reasoning"] is None
