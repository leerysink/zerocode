import json
from typing import AsyncIterator
from openai import AsyncOpenAI

TOOLS = [
    {"type": "function", "function": {
        "name": "list_dir",
        "description": "列出工作区内目录内容（单层，忽略隐藏文件）",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "相对工作区的目录路径，空为根目录"}}, "required": []}}},
    {"type": "function", "function": {
        "name": "read_file",
        "description": "读取工作区内文本文件内容（上限200KB）",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "相对路径"}}, "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "write_file",
        "description": "新建或整体覆盖工作区内文件（UTF-8）",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "相对路径"},
            "content": {"type": "string", "description": "完整文件内容"}}, "required": ["path", "content"]}}},
    {"type": "function", "function": {
        "name": "edit_file",
        "description": "对文件做精确唯一匹配替换；old_string 必须在文件中恰好出现一次",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "old_string": {"type": "string"},
            "new_string": {"type": "string"}}, "required": ["path", "old_string", "new_string"]}}},
    {"type": "function", "function": {
        "name": "ask_user",
        "description": "当需求不明确或有多种实现方案时，向用户提问；问题应具体可决策",
        "parameters": {"type": "object", "properties": {
            "question": {"type": "string"},
            "options": {"type": "array", "items": {"type": "string"},
                        "description": "可选项（可为空，空则用户自由输入）"}}, "required": ["question"]}}},
]

class OpenAICompatLLM:
    def __init__(self, base_url: str, api_key: str, model: str):
        self.client = AsyncOpenAI(base_url=base_url, api_key=api_key or "EMPTY")
        self.model = model

    async def chat_stream(self, messages: list[dict], tools: list[dict]) -> AsyncIterator[dict]:
        content_parts: list[str] = []
        tool_calls: dict[int, dict] = {}
        stream = await self.client.chat.completions.create(
            model=self.model, messages=messages, tools=tools, stream=True)
        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta and delta.content:
                content_parts.append(delta.content)
                yield {"type": "content_delta", "content": delta.content}
            if delta and delta.tool_calls:
                for tc in delta.tool_calls:
                    slot = tool_calls.setdefault(tc.index, {"id": "", "function": {"name": "", "arguments": ""}})
                    if tc.id:
                        slot["id"] = tc.id
                    if tc.function and tc.function.name:
                        slot["function"]["name"] = tc.function.name
                    if tc.function and tc.function.arguments:
                        slot["function"]["arguments"] += tc.function.arguments
        yield {"type": "done", "message": {
            "role": "assistant",
            "content": "".join(content_parts) or None,
            "tool_calls": [tool_calls[i] for i in sorted(tool_calls)] or None}}

class ScriptedLLM:
    """测试用：按轮次重放事件；received 记录每轮收到的 messages。"""
    def __init__(self, turns: list[list[dict]]):
        self.turns = list(turns)
        self.received: list[list[dict]] = []

    async def chat_stream(self, messages: list[dict], tools: list[dict]) -> AsyncIterator[dict]:
        self.received.append(messages)
        for ev in self.turns.pop(0):
            yield ev
