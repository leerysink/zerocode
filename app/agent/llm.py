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
        "description": ("当需求不明确、有多种实现方案或信息缺失时，向用户提一个问题；"
                        "一次只问一个，得到回答并思考后再决定是否继续提问。"
                        "用户明确表示不要再提问时不要调用此工具"),
        "parameters": {"type": "object", "properties": {
            "question": {"type": "string", "description": "要问用户的问题"},
            "options": {"type": "array", "items": {"type": "object", "properties": {
                "label": {"type": "string"},
                "description": {"type": "string", "description": "选项说明"},
                "recommended": {"type": "boolean", "description": "推荐标注"}},
                "required": ["label"]},
                "description": "可选项（可为空，空则用户自由输入）"},
            "multi_select": {"type": "boolean", "description": "是否多选"}},
            "required": ["question"]}}},
    {"type": "function", "function": {
        "name": "todo_write",
        "description": "写入/全量替换当前任务清单。多步任务开始前先建清单，每完成一步即更新状态",
        "parameters": {"type": "object", "properties": {
            "todos": {"type": "array", "items": {"type": "object", "properties": {
                "content": {"type": "string", "description": "任务描述"},
                "status": {"type": "string", "enum": ["pending", "in_progress", "completed"],
                           "description": "状态：待办/进行中/已完成"}},
                "required": ["content", "status"]}}}, "required": ["todos"]}}},
    {"type": "function", "function": {
        "name": "memory_write",
        "description": (
            "沉淀/维护当前工作区的长期记忆（下一次任务起自动注入上下文）。"
            "记：用户明确的偏好与决策、项目事实与命名约定、踩过的坑（原因+规避方式）；"
            "不记：一次性任务细节、代码/文档里本就能看出来的内容、密钥与个人信息。"
            "一条只讲一件事；已有相似记忆请改用 update 而不是重复 create；"
            "记忆归属由当前会话自动判定，不接受 workspace_id 参数。"
        ),
        "parameters": {"type": "object", "properties": {
            "op": {"type": "string", "enum": ["create", "update", "delete"],
                   "description": "create 新增 / update 就地改既有条目 / delete 停用既有条目"},
            "type": {"type": "string", "enum": ["preference", "decision", "fact", "pitfall"],
                     "description": "记忆分类：偏好/决策/事实/踩坑（create 必填，update 可选）"},
            "content": {"type": "string", "description": "记忆正文，单条只讲一件事（create 必填，update 可选）"},
            "title": {"type": "string", "description": "可选短标题（不超过 200 字），便于检索"},
            "id": {"type": "string", "description": "update/delete 必填：create 返回的字符串 id"}},
            "required": ["op"]}}},
]

# 预览工具（规格 docs/specs/2026-10-29-agent-preview-tools.md）：只在会话工作区解析出应用时挂载。
# 刻意没有 app_id / slug / path 参数——应用身份由服务端从 chat_session.workspace_id 反查，
# 模型无从指向别的应用或别人的应用。描述保持一句话：这三条随每次请求发送，教程在 system prompt。
PREVIEW_TOOLS = [
    {"type": "function", "function": {
        "name": "preview_status",
        "description": "查看当前应用 dev 预览的状态与可用性（只读）",
        "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {
        "name": "preview_logs",
        "description": "读取当前应用 dev 预览的最近日志（平台步骤行 + 容器输出，只读）",
        "parameters": {"type": "object", "properties": {
            "tail": {"type": "integer", "description": "最近多少行，默认 80，最大 300"}},
            "required": []}}},
    {"type": "function", "function": {
        "name": "preview_restart",
        "description": ("启动或重启当前应用的 dev 预览，等待就绪后返回最终状态与日志。"
                        "改了依赖清单/配置、或预览未运行时使用；纯代码改动通常会自动热更新，"
                        "先看 preview_status/preview_logs"),
        "parameters": {"type": "object", "properties": {
            "wait_seconds": {"type": "integer",
                             "description": "最多等待多少秒，默认 240；超时返回 starting，预览仍在后台继续"}},
            "required": []}}},
]

PREVIEW_TOOL_NAMES = ("preview_status", "preview_logs", "preview_restart")

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
