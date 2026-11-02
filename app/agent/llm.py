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

# 需求澄清收口声明（规格 docs/specs/2026-11-01-agent-clarify-mode.md，ADR-0032）：
# 首轮澄清门禁的两个放行信号之一。只在 clarify.enabled 开启时挂载（loop 侧按配置拼工具列表）。
# understanding 写成必填是设计的关键：豁免之所以成立，全在这句话对用户可见。
CLARIFY_TOOLS = [
    {"type": "function", "function": {
        "name": "clarify_done",
        "description": ("声明需求澄清已收口、可以开工。本轮第一次写文件前必须调用它，否则写入会被门禁挡回。"
                        "understanding 必填：一句话说清你最终理解的需求，它会原样展示给用户；"
                        "basis=clarified 表示已通过 ask_user 与用户对齐，"
                        "basis=already_clear 表示用户消息本身已把目标、范围与约束说清、无需提问"),
        "parameters": {"type": "object", "properties": {
            "understanding": {"type": "string",
                              "description": "你对本次需求的理解，展示给用户"},
            "basis": {"type": "string", "enum": ["clarified", "already_clear"],
                      "description": "收口依据，缺省 clarified"},
            "assumptions": {"type": "array", "items": {"type": "string"},
                            "description": "采用的假设（停止追问但仍有未确认点时给出）"}},
            "required": ["understanding"]}}},
]

CLARIFY_TOOL_NAMES = ("clarify_done",)

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

# 数据库工具（规格 docs/specs/2026-10-30-agent-db-verification-tools.md）：零命令面、无 app 参数，
# 应用身份由服务端解析；db_write 与 expect 参数按工单逐票补齐。
DB_TOOLS = [
    {"type": "function", "function": {
        "name": "db_query",
        "description": "对当前应用的库执行一条只读 SELECT 查询并返回结果表（dev/prod 均只读）",
        "parameters": {"type": "object", "properties": {
            "sql": {"type": "string", "description": "单条 SELECT 语句"},
            "env": {"type": "string", "enum": ["dev", "prod"],
                    "description": "查哪套库，默认 dev"},
            "expect": {"type": "object", "properties": {
                "type": {"type": "string", "enum": ["count", "scalar", "rows"],
                         "description": "断言档：行数/单值/全行比对"},
                "value": {"description": "期望值：count=整数，scalar=单值，rows=行对象数组"}},
                "required": ["type"],
                "description": "可选机器断言；给出后由工具判定 passed，不给则模型自己看结果"}},
            "required": ["sql"]}}},
    {"type": "function", "function": {
        "name": "db_write",
        "description": ("对当前应用的 dev 库执行写入 SQL（灌测试数据/修数据），需用户确认后执行；"
                        "同一连接按序串行、遇错即停，已成功的不回滚。prod 库不可写"),
        "parameters": {"type": "object", "properties": {
            "statements": {"type": "array", "items": {"type": "string"},
                           "description": "按执行顺序的 SQL 语句（INSERT/UPDATE/DELETE/表级 DDL）"}},
            "required": ["statements"]}}},
]

DB_TOOL_NAMES = ("db_query", "db_write")

# 预览页浏览器验证（规格 docs/specs/2026-10-31-agent-browser-verification.md）：
# 与应用工作区同生同灭。唯一参数是同源相对 path——没有 url/app 参数，模型指不到别处。
BROWSER_TOOLS = [
    {"type": "function", "function": {
        "name": "preview_page",
        "description": ("用无头浏览器打开当前应用 dev 预览页，返回标题、可见文本、控制台错误、"
                        "失败请求、截图与视觉诊断（只读）"),
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string",
                     "description": "同源相对路径，默认 /；只能以 / 开头，不能是绝对 URL"}},
            "required": []}}},
]

BROWSER_TOOL_NAMES = ("preview_page",)

# 预览页交互验证（规格 docs/specs/2026-11-02-agent-browser-interaction.md）：与应用工作区同生同灭，
# 挂载条件与 preview_page 完全一致。参数面里没有 app/url/session_id——目标由后端按 dev 端口构造，
# 会话绑定只存在 ctx.run_state（ADR-0037：模型能指到别人的 session 就全完了）。
# 描述保持一句话：这三条随每次请求发送，教程在 system prompt 的 <browser_session> 块里。
# expect 的形状只定义这一份、由 browser_open 与 browser_step 共用同一个对象：抄第二份的话，将来
# 给某一侧加一档（例如 title_contains）时另一侧不会红任何测试——模型于是看到两个同名参数两种形状，
# 并在 open 上写出一个 sidecar 认不出的 type。sidecar 的 `OpenRequest.expect` 与
# `StepRequest.expect` 都是 `dict | None`，走的也是同一个 `judge_expect`。
BROWSER_EXPECT_SCHEMA = {"type": "object", "properties": {
    "type": {"type": "string", "enum": ["text_contains", "element_visible",
                                        "url_matches", "no_new_console_error"]},
    "value": {"type": "string"}}, "required": ["type"],
    "description": ("可选机器断言：给了就由工具轮询等到满足或超时（open 等首屏文案、step 等这一步的"
                    "结果），不给则只等一个短窗口，可能读到中间态")}

BROWSER_SESSION_TOOLS = [
    {"type": "function", "function": {
        "name": "browser_open",
        "description": ("在当前应用 dev 预览开一个可交互浏览器会话，返回标题、可见文本与"
                        "可交互元素清单（ref 只在最近一次返回内有效）；expect 给了就等首屏满足再返回"),
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "同源相对路径，默认 /"},
            "expect": BROWSER_EXPECT_SCHEMA},
            "required": []}}},
    {"type": "function", "function": {
        "name": "browser_step",
        "description": ("对当前浏览器会话执行一个动作并返回新状态；expect 给了就由工具判定"),
        "parameters": {"type": "object", "properties": {
            "action": {"type": "string", "enum": ["click", "fill", "press", "select",
                                                  "scroll_into_view", "navigate"]},
            "target": {"type": "string",
                       "description": "上一次清单里的 ref；navigate 时填同源相对路径"},
            "value": {"type": "string",
                      "description": "fill 的文本 / press 的键名 / select 的选项值"},
            "expect": BROWSER_EXPECT_SCHEMA,
            "screenshot": {"type": "boolean", "description": "是否附截图，默认 false"}},
            "required": ["action", "target"]}}},
    {"type": "function", "function": {
        "name": "browser_close",
        "description": "关闭当前浏览器会话，返回本次路径走查的收口报告",
        "parameters": {"type": "object", "properties": {}, "required": []}}},
]

BROWSER_SESSION_TOOL_NAMES = ("browser_open", "browser_step", "browser_close")


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
