import json
from typing import AsyncIterator
from openai import AsyncOpenAI

from app.agent import registry as _registry

# 工具 schema 与名字清单全部由注册表派生（app/agent/registry.py 是唯一真值）。
# 保留这些模块别名只为兼容既有 import：新增工具请改注册表，不要在这里补字面量。
TOOLS = _registry.schemas(scope=_registry.SCOPE_ALWAYS)

CLARIFY_TOOLS = _registry.schemas("clarify")
CLARIFY_TOOL_NAMES = _registry.names("clarify")

PREVIEW_TOOLS = _registry.schemas("preview")
PREVIEW_TOOL_NAMES = _registry.names("preview")
DB_TOOLS = _registry.schemas("db")
DB_TOOL_NAMES = _registry.names("db")
BROWSER_TOOLS = _registry.schemas("browser")
BROWSER_TOOL_NAMES = _registry.names("browser")
BROWSER_EXPECT_SCHEMA = _registry.BROWSER_EXPECT_SCHEMA
BROWSER_SESSION_TOOLS = _registry.schemas("browser_session")
BROWSER_SESSION_TOOL_NAMES = _registry.names("browser_session")


def _reasoning_of(delta):
    """兼容取 reasoning_content：SDK 具名属性优先，其次 model_extra（未知字段兜底）。"""
    if delta is None:
        return None
    rc = getattr(delta, "reasoning_content", None)
    if rc is None:
        rc = (getattr(delta, "model_extra", None) or {}).get("reasoning_content")
    return rc

class OpenAICompatLLM:
    def __init__(self, base_url: str, api_key: str, model: str):
        self.client = AsyncOpenAI(base_url=base_url, api_key=api_key or "EMPTY")
        self.model = model

    async def chat_stream(self, messages: list[dict], tools: list[dict]) -> AsyncIterator[dict]:
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_calls: dict[int, dict] = {}
        stream = await self.client.chat.completions.create(
            model=self.model, messages=messages, tools=tools, stream=True)
        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            rc = _reasoning_of(delta)
            if rc:
                reasoning_parts.append(rc)
                yield {"type": "reasoning_delta", "content": rc}
            if delta and delta.content:
                content_parts.append(delta.content)
                yield {"type": "content_delta", "content": delta.content}
            if delta and delta.tool_calls:
                for tc in delta.tool_calls:
                    # Ark 等严格端点强制要求 tool_calls[].type="function"（缺失 400）
                    slot = tool_calls.setdefault(tc.index, {"type": "function", "id": "", "function": {"name": "", "arguments": ""}})
                    if tc.id:
                        slot["id"] = tc.id
                    if tc.function and tc.function.name:
                        slot["function"]["name"] = tc.function.name
                    if tc.function and tc.function.arguments:
                        slot["function"]["arguments"] += tc.function.arguments
        yield {"type": "done", "message": {
            "role": "assistant",
            "content": "".join(content_parts) or None,
            "reasoning": "".join(reasoning_parts) or None,
            "tool_calls": [tool_calls[i] for i in sorted(tool_calls)] or None}}

class ScriptedLLM:
    """测试用：按轮次重放事件；received 记录每轮收到的 messages。

    received 必须存**快照**而不是 messages 的引用：runner 全程复用同一个 list 并往里追加，
    直接 append(messages) 会让每一轮的记录都变成同一份"最终状态"，任何按轮次断言顺序的
    测试都会自我感觉良好地通过（实测骗过一次注入位置断言）。

    received_tools 与 received 同轮次对齐，用于断言「这一轮模型看得见哪些工具」。
    """
    def __init__(self, turns: list[list[dict]]):
        self.turns = list(turns)
        self.received: list[list[dict]] = []
        self.received_tools: list[list[dict]] = []   # 每轮实际送模的工具清单（挂载条件的断言靶）

    async def chat_stream(self, messages: list[dict], tools: list[dict]) -> AsyncIterator[dict]:
        self.received.append([dict(m) for m in messages])
        self.received_tools.append(list(tools))
        for ev in self.turns.pop(0):
            yield ev
