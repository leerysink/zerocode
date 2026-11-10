"""Agent 工具注册表：schema、挂载范围、确认策略、handler、结果预算的唯一真值。

以前加一个工具要同时改五处：`tools.execute_tool` 的 if 链、工具模块的 handler、
`llm.py` 的 schema 与名字元组、`loop.py` 的挂载拼接、前端 `ChatMessages.vue` 的卡片名单。
现在 schema/名字/挂载/预算都从这里派生；前端拿不到后端常量，仍保留一份数组，
但 `tests/test_agent_registry.py` 会钉住两边同名同序。

设计边界与既有约定一致：
- 应用身份由服务端解析，schema 里没有 app_id/slug/url（ADR-0037/38）；
- handler 用**惰性引用**，注册表本身不 import 预览/数据库/浏览器模块，避免成环；
- `ask_user` / `clarify_done` 由会话循环处理（要拿 ask/confirm/run_state），没有 handler。
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Callable

from app.agent.result_budget import DEFAULT_POLICY, ResultPolicy

SCOPE_ALWAYS = "always"
SCOPE_APP = "app"
SCOPE_CLARIFY = "clarify"

CONFIRM_NONE = "none"
CONFIRM_WRITE = "write"                  # 受 agent.write_confirm 控制
CONFIRM_ALWAYS = "always"                # 恒确认（不可回滚的写）
CONFIRM_PREVIEW_RESTART = "preview_restart"  # 仅当预览正在运行时确认

# read_file 的上限契约是 200KB **字节**（204800 UTF-8 字节，纯 ASCII 即 204800 字符）；
# 预算上限必须高过它，否则「能读的文件」会被预算误判成需要截断。
READ_FILE_POLICY = ResultPolicy(max_chars=230_000, max_items=200, max_str_chars=230_000)
# list_dir 一页最多 500 条，预算的条目上限必须跟得上，否则「允许翻 500 条」会被预算再砍回 200。
LIST_DIR_POLICY = ResultPolicy(max_chars=32_000, max_items=500, max_str_chars=2_000)
SEARCH_POLICY = ResultPolicy(max_chars=32_000, max_items=200, max_str_chars=2_000)
SMALL_POLICY = ResultPolicy(max_chars=8_000, max_items=100, max_str_chars=2_000)
LOG_POLICY = ResultPolicy(max_chars=20_000, max_items=300, max_str_chars=2_000)
DB_POLICY = ResultPolicy(max_chars=32_000, max_items=500, max_str_chars=8_000)
BROWSER_POLICY = ResultPolicy(max_chars=32_000, max_items=300, max_str_chars=8_000)
# 自检输出只留尾部几 KB 的报错，够模型定位 file:line，不把整段构建日志塞回上下文。
CHECK_POLICY = ResultPolicy(max_chars=16_000, max_items=200, max_str_chars=16_000)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    schema: dict
    group: str
    scope: str = SCOPE_ALWAYS
    confirm: str = CONFIRM_NONE
    handler_ref: tuple[str, str] | None = None
    pass_name: bool = False
    pass_confirmed: bool = False
    result_policy: ResultPolicy = DEFAULT_POLICY


def _fn(name: str, description: str, properties: dict | None = None,
        required: list[str] | None = None) -> dict:
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties or {},
                       "required": required or []}}}


# 基础文件工具：描述保持一句话（随每次请求发送，教程在 system prompt）
_LIST_DIR = _fn("list_dir", "列出工作区内目录内容（单层，忽略隐藏文件）；result.next_offset 非空表示还有下一页", {
    "path": {"type": "string", "description": "相对工作区的目录路径，空为根目录"},
    "offset": {"type": "integer", "description": "从第几个条目开始（0 基，缺省 0）"},
    "limit": {"type": "integer", "description": "本页条目数（缺省 200，上限 500）"}})
_READ_FILE = _fn("read_file", "读取工作区内文本文件；长文件用 offset/limit 按行分段，结果恒带 total_lines 与剩余行", {
    "path": {"type": "string", "description": "相对路径"},
    "offset": {"type": "integer", "description": "起始行号（1 基，缺省 1）"},
    "limit": {"type": "integer", "description": "读取行数（缺省读到文件末；单次仍受 200KB 限制）"}}, ["path"])
_SEARCH = _fn("search", "在工作区内检索：mode=content 搜内容（字面子串、不区分大小写），mode=files 按文件名/路径 glob（如 *.vue）", {
    "mode": {"type": "string", "enum": ["content", "files"],
             "description": "content=搜内容；files=找文件"},
    "pattern": {"type": "string", "description": "content 为字面子串；files 为 glob 模式"},
    "path": {"type": "string", "description": "可选的子目录起点，缺省整个工作区"}}, ["mode", "pattern"])
_WRITE_FILE = _fn("write_file", "新建或整体覆盖工作区内文件（UTF-8）", {
    "path": {"type": "string", "description": "相对路径"},
    "content": {"type": "string", "description": "完整文件内容"}}, ["path", "content"])
_EDIT_FILE = _fn("edit_file", "对文件做精确唯一匹配替换；old_string 必须在文件中恰好出现一次", {
    "path": {"type": "string"},
    "old_string": {"type": "string"},
    "new_string": {"type": "string"}}, ["path", "old_string", "new_string"])
_DELETE_FILE = _fn(
    "delete_file",
    ("删除工作区里的一个文件（真删盘；删除前留存原文，可用 revert_change 复原）。"
     "只删文件、不递归删目录；app.json 与 deploy/ 不可删；二进制或超 200KB 的文件不留原文、不可回滚"),
    {"path": {"type": "string", "description": "要删除的文件相对路径"}}, ["path"])
_MOVE_FILE = _fn(
    "move_file",
    "重命名或移动文件（old_path → new_path）。目标已存在一律拒绝，不会覆盖；只移动文件、不移动目录",
    {"old_path": {"type": "string", "description": "原相对路径"},
     "new_path": {"type": "string", "description": "目标相对路径（不得已存在）"}},
    ["old_path", "new_path"])
_REVERT_CHANGE = _fn(
    "revert_change",
    ("回滚某路径在本会话内最近一次改动（把当时的原文写回）。"
     "磁盘当前内容与该记录不符时拒绝，避免覆盖之后的改动；撤销新建会把文件删掉"),
    {"path": {"type": "string", "description": "要回滚的文件相对路径"}}, ["path"])
_ASK_USER = _fn(
    "ask_user",
    ("当需求不明确、有多种实现方案或信息缺失时，向用户提一个问题；"
     "一次只问一个，得到回答并思考后再决定是否继续提问。"
     "用户明确表示不要再提问时不要调用此工具"),
    {"question": {"type": "string", "description": "要问用户的问题"},
     "options": {"type": "array", "items": {"type": "object", "properties": {
         "label": {"type": "string"},
         "description": {"type": "string", "description": "选项说明"},
         "recommended": {"type": "boolean", "description": "推荐标注"}},
         "required": ["label"]},
         "description": "可选项（可为空，空则用户自由输入）"},
     "multi_select": {"type": "boolean", "description": "是否多选"}},
    ["question"])
_TODO_WRITE = _fn(
    "todo_write", "写入/全量替换当前任务清单。多步任务开始前先建清单，每完成一步即更新状态",
    {"todos": {"type": "array", "items": {"type": "object", "properties": {
        "content": {"type": "string", "description": "任务描述"},
        "status": {"type": "string", "enum": ["pending", "in_progress", "completed"],
                   "description": "状态：待办/进行中/已完成"}},
        "required": ["content", "status"]}}}, ["todos"])
_MEMORY_WRITE = _fn(
    "memory_write",
    ("沉淀/维护当前工作区的长期记忆（下一次任务起自动注入上下文）。"
     "记：用户明确的偏好与决策、项目事实与命名约定、踩过的坑（原因+规避方式）；"
     "不记：一次性任务细节、代码/文档里本就能看出来的内容、密钥与个人信息。"
     "一条只讲一件事；已有相似记忆请改用 update 而不是重复 create；"
     "记忆归属由当前会话自动判定，不接受 workspace_id 参数。"),
    {"op": {"type": "string", "enum": ["create", "update", "delete"],
            "description": "create 新增 / update 就地改既有条目 / delete 停用既有条目"},
     "type": {"type": "string", "enum": ["preference", "decision", "fact", "pitfall"],
              "description": "记忆分类：偏好/决策/事实/踩坑（create 必填，update 可选）"},
     "content": {"type": "string", "description": "记忆正文，单条只讲一件事（create 必填，update 可选）"},
     "title": {"type": "string", "description": "可选短标题（不超过 200 字），便于检索"},
     "id": {"type": "string", "description": "update/delete 必填：create 返回的字符串 id"}},
    ["op"])

_CLARIFY_DONE = _fn(
    "clarify_done",
    ("声明需求澄清已收口、可以开工。本轮第一次写文件前必须调用它，否则写入会被门禁挡回。"
     "understanding 必填：一句话说清你最终理解的需求，它会原样展示给用户；"
     "basis=clarified 表示已通过 ask_user 与用户对齐，"
     "basis=already_clear 表示用户消息本身已把目标、范围与约束说清、无需提问"),
    {"understanding": {"type": "string", "description": "你对本次需求的理解，展示给用户"},
     "basis": {"type": "string", "enum": ["clarified", "already_clear"],
               "description": "收口依据，缺省 clarified"},
     "assumptions": {"type": "array", "items": {"type": "string"},
                     "description": "采用的假设（停止追问但仍有未确认点时给出）"}},
    ["understanding"])

_PREVIEW_STATUS = _fn("preview_status", "查看当前应用 dev 预览的状态与可用性（只读）")
_PREVIEW_LOGS = _fn("preview_logs", "读取当前应用 dev 预览的最近日志（平台步骤行 + 容器输出，只读）", {
    "tail": {"type": "integer", "description": "最近多少行，默认 80，最大 300"}})
_PREVIEW_RESTART = _fn(
    "preview_restart",
    ("启动或重启当前应用的 dev 预览，等待就绪后返回最终状态与日志。"
     "改了依赖清单/配置、或预览未运行时使用；纯代码改动通常会自动热更新，"
     "先看 preview_status/preview_logs"),
    {"wait_seconds": {"type": "integer",
                      "description": "最多等待多少秒，默认 240；超时返回 starting，预览仍在后台继续"}})

_DB_QUERY = _fn(
    "db_query", "对当前应用的库执行一条只读 SELECT 查询并返回结果表（dev/prod 均只读）",
    {"sql": {"type": "string", "description": "单条 SELECT 语句"},
     "env": {"type": "string", "enum": ["dev", "prod"], "description": "查哪套库，默认 dev"},
     "expect": {"type": "object", "properties": {
         "type": {"type": "string", "enum": ["count", "scalar", "rows"],
                  "description": "断言档：行数/单值/全行比对"},
         "value": {"description": "期望值：count=整数，scalar=单值，rows=行对象数组"}},
         "required": ["type"],
         "description": "可选机器断言；给出后由工具判定 passed，不给则模型自己看结果"}},
    ["sql"])
_DB_WRITE = _fn(
    "db_write",
    ("对当前应用的 dev 库执行写入 SQL（灌测试数据/修数据），需用户确认后执行；"
     "同一连接按序串行、遇错即停，已成功的不回滚。prod 库不可写"),
    {"statements": {"type": "array", "items": {"type": "string"},
                    "description": "按执行顺序的 SQL 语句（INSERT/UPDATE/DELETE/表级 DDL）"}},
    ["statements"])
_DIAG_PREFLIGHT = _fn(
    "release_preflight",
    "发布前只读体检：列出阻断项与发布时会自动补齐的可自修项（不写盘、不构建、不起容器）")
_DIAG_DB_SCHEMA = _fn(
    "db_schema", "查看当前应用库结构（只读）：不带 table 回表清单，带 table 回该表列/主键/索引",
    {"env": {"type": "string", "enum": ["dev", "prod"], "description": "看哪套库，默认 dev"},
     "table": {"type": "string", "description": "可选；给出则回该表详情"}})
_DIAG_SQLCHANGE = _fn(
    "sqlchange_status",
    "查看数据轨各环境已执行/未执行的迁移文件（只读，dev 与 prod 各自列出 pending 与 failed）")
_RUN_CHECK = _fn(
    "run_check",
    "在一次性容器里跑平台写死的自检项（只报告，不阻断发布）："
    "frontend_typecheck 前端类型/编译检查；backend_compile 后端编译。不能传命令或参数",
    {"preset": {"type": "string", "enum": ["frontend_typecheck", "backend_compile"],
                "description": "检查项（平台写死的白名单，见 ADR-0046）"}},
    ["preset"])

_PREVIEW_PAGE = _fn(
    "preview_page",
    ("用无头浏览器打开当前应用 dev 预览页，返回标题、可见文本、控制台错误、"
     "失败请求、截图与视觉诊断（只读）；默认以模板 admin 身份打开，"
     "结果里的 identity 标注本次身份"),
    {"path": {"type": "string",
              "description": "同源相对路径，默认 /；只能以 / 开头，不能是绝对 URL"},
     "device": {"type": "string",
                "description": ("视口设备名（Playwright 设备名，如 iPhone 14）：验证 /h5/ "
                                "移动端页面时用；不传为桌面 1280×800")}})

# open 与 step 共用同一个 expect 形状（抄第二份会让某一侧加档时另一侧静默漂移）
BROWSER_EXPECT_SCHEMA = {"type": "object", "properties": {
    "type": {"type": "string", "enum": ["text_contains", "element_visible",
                                        "url_matches", "no_new_console_error"]},
    "value": {"type": "string"}}, "required": ["type"],
    "description": ("可选机器断言：给了就由工具轮询等到满足或超时（open 等首屏文案、step 等这一步的"
                    "结果），不给则只等一个短窗口，可能读到中间态")}

_BROWSER_OPEN = _fn(
    "browser_open",
    ("在当前应用 dev 预览开一个可交互浏览器会话，返回标题、可见文本与"
     "可交互元素清单（ref 只在最近一次返回内有效）；expect 给了就等首屏满足再返回"),
    {"path": {"type": "string", "description": "同源相对路径，默认 /"},
     "device": {"type": "string",
                "description": ("视口设备名（Playwright 设备名，如 iPhone 14）：走查 /h5/ "
                                "移动端页面时用；不传为桌面 1280×800")},
     "expect": BROWSER_EXPECT_SCHEMA,
     "screenshot": {"type": "boolean",
                    "description": "是否附首屏截图，默认 false；expect 未通过时自动附一张"}})
_BROWSER_STEP = _fn(
    "browser_step", "对当前浏览器会话执行一个动作并返回新状态；expect 给了就由工具判定",
    {"action": {"type": "string", "enum": ["click", "fill", "press", "select",
                                          "scroll_into_view", "navigate"]},
     "target": {"type": "string", "description": "上一次清单里的 ref；navigate 时填同源相对路径"},
     "value": {"type": "string", "description": "fill 的文本 / press 的键名 / select 的选项值"},
     "expect": BROWSER_EXPECT_SCHEMA,
     "screenshot": {"type": "boolean", "description": "是否附截图，默认 false"}},
    ["action", "target"])
_BROWSER_CLOSE = _fn("browser_close", "关闭当前浏览器会话，返回本次路径走查的收口报告")


SPECS: tuple[ToolSpec, ...] = (
    ToolSpec("list_dir", _LIST_DIR, "fs", handler_ref=("app.agent.tools", "_handle_list_dir"),
             result_policy=LIST_DIR_POLICY),
    ToolSpec("read_file", _READ_FILE, "fs", handler_ref=("app.agent.tools", "_handle_read_file"),
             result_policy=READ_FILE_POLICY),
    ToolSpec("search", _SEARCH, "fs", handler_ref=("app.agent.tools", "_handle_search"),
             result_policy=SEARCH_POLICY),
    ToolSpec("write_file", _WRITE_FILE, "fs", confirm=CONFIRM_WRITE,
             handler_ref=("app.agent.tools", "_handle_write_file"), result_policy=SMALL_POLICY),
    ToolSpec("edit_file", _EDIT_FILE, "fs", confirm=CONFIRM_WRITE,
             handler_ref=("app.agent.tools", "_handle_edit_file"), result_policy=SMALL_POLICY),
    # Week 4 写面：与 write/edit 同走写入确认（文件写入可回滚，但仍要先让用户看见改什么）。
    ToolSpec("delete_file", _DELETE_FILE, "fs", confirm=CONFIRM_WRITE,
             handler_ref=("app.agent.tools", "_handle_delete_file"), result_policy=SMALL_POLICY),
    ToolSpec("move_file", _MOVE_FILE, "fs", confirm=CONFIRM_WRITE,
             handler_ref=("app.agent.tools", "_handle_move_file"), result_policy=SMALL_POLICY),
    ToolSpec("revert_change", _REVERT_CHANGE, "fs", confirm=CONFIRM_WRITE,
             handler_ref=("app.agent.tools", "_handle_revert_change"), result_policy=SMALL_POLICY),
    ToolSpec("ask_user", _ASK_USER, "chat"),
    ToolSpec("todo_write", _TODO_WRITE, "chat",
             handler_ref=("app.agent.tools", "_handle_todo_write"), result_policy=SMALL_POLICY),
    ToolSpec("memory_write", _MEMORY_WRITE, "memory",
             handler_ref=("app.agent.tools", "_handle_memory_write"), result_policy=SMALL_POLICY),
    ToolSpec("clarify_done", _CLARIFY_DONE, "clarify", scope=SCOPE_CLARIFY),
    ToolSpec("preview_status", _PREVIEW_STATUS, "preview", scope=SCOPE_APP,
             handler_ref=("app.agent.preview_tools", "run_preview_tool"),
             pass_name=True, pass_confirmed=True),
    ToolSpec("preview_logs", _PREVIEW_LOGS, "preview", scope=SCOPE_APP,
             handler_ref=("app.agent.preview_tools", "run_preview_tool"),
             pass_name=True, pass_confirmed=True, result_policy=LOG_POLICY),
    ToolSpec("preview_restart", _PREVIEW_RESTART, "preview", scope=SCOPE_APP,
             confirm=CONFIRM_PREVIEW_RESTART,
             handler_ref=("app.agent.preview_tools", "run_preview_tool"),
             pass_name=True, pass_confirmed=True),
    ToolSpec("db_query", _DB_QUERY, "db", scope=SCOPE_APP,
             handler_ref=("app.agent.db_tools", "run_db_tool"),
             pass_name=True, pass_confirmed=True, result_policy=DB_POLICY),
    ToolSpec("db_write", _DB_WRITE, "db", scope=SCOPE_APP, confirm=CONFIRM_ALWAYS,
             handler_ref=("app.agent.db_tools", "run_db_tool"),
             pass_name=True, pass_confirmed=True, result_policy=DB_POLICY),
    ToolSpec("preview_page", _PREVIEW_PAGE, "browser", scope=SCOPE_APP,
             handler_ref=("app.agent.browser_tools", "run_browser_tool"),
             pass_name=True, result_policy=BROWSER_POLICY),
    ToolSpec("browser_open", _BROWSER_OPEN, "browser_session", scope=SCOPE_APP,
             handler_ref=("app.agent.browser_session_tools", "run_browser_session_tool"),
             pass_name=True, result_policy=BROWSER_POLICY),
    ToolSpec("browser_step", _BROWSER_STEP, "browser_session", scope=SCOPE_APP,
             handler_ref=("app.agent.browser_session_tools", "run_browser_session_tool"),
             pass_name=True, result_policy=BROWSER_POLICY),
    ToolSpec("browser_close", _BROWSER_CLOSE, "browser_session", scope=SCOPE_APP,
             handler_ref=("app.agent.browser_session_tools", "run_browser_session_tool"),
             pass_name=True, result_policy=BROWSER_POLICY),
    # 只读诊断（Week 2）：判据复用 prod.preflight_checks / db_admin_service / sqlrun，不另起一套。
    ToolSpec("release_preflight", _DIAG_PREFLIGHT, "diag", scope=SCOPE_APP,
             handler_ref=("app.agent.diag_tools", "run_diag_tool"),
             pass_name=True, result_policy=SMALL_POLICY),
    ToolSpec("db_schema", _DIAG_DB_SCHEMA, "diag", scope=SCOPE_APP,
             handler_ref=("app.agent.diag_tools", "run_diag_tool"),
             pass_name=True, result_policy=DB_POLICY),
    ToolSpec("sqlchange_status", _DIAG_SQLCHANGE, "diag", scope=SCOPE_APP,
             handler_ref=("app.agent.diag_tools", "run_diag_tool"),
             pass_name=True, result_policy=DB_POLICY),
    # 预设自检（Week 3，ADR-0046）：工具面无通用命令执行能力，只允许平台写死的 preset。
    ToolSpec("run_check", _RUN_CHECK, "check", scope=SCOPE_APP,
             handler_ref=("app.agent.check_tools", "run_check_tool"),
             pass_name=True, result_policy=CHECK_POLICY),
)

SPEC_BY_NAME: dict[str, ToolSpec] = {spec.name: spec for spec in SPECS}


def spec_of(name: str) -> ToolSpec | None:
    return SPEC_BY_NAME.get(name)


def schemas(group: str | None = None, scope: str | None = None) -> list[dict]:
    """按分组/挂载范围筛 schema；两个维度互不干扰（group 是功能簇，scope 是挂载条件）。"""
    return [spec.schema for spec in SPECS
            if (group is None or spec.group == group)
            and (scope is None or spec.scope == scope)]


def names(group: str) -> tuple[str, ...]:
    return tuple(spec.name for spec in SPECS if spec.group == group)


def build_tools(*, app: bool = False, clarify: bool = False) -> list[dict]:
    """按 基础 → 应用 → 澄清 的固定顺序拼接本轮挂载的工具（既有测试钉住这个顺序）。"""
    active = [SCOPE_ALWAYS]
    if app:
        active.append(SCOPE_APP)
    if clarify:
        active.append(SCOPE_CLARIFY)
    return [spec.schema for scope in active for spec in SPECS if spec.scope == scope]


def resolve_handler(spec: ToolSpec) -> Callable | None:
    """把惰性引用解析成 `(ctx, args, *, confirmed=False)` 形状的协程函数。

    handler 在**每次调用时**才 import 对应模块：注册表被 `llm.py` 导入，而
    preview/db/browser 模块又反过来 import `llm.py`，顶层 import 会成环。
    刻意不缓存 handler：缓存会在模块属性被替换（测试 monkeypatch、热更新）后继续调用旧函数。
    """
    if spec.handler_ref is None:
        return None
    module_name, fn_name = spec.handler_ref
    fn = getattr(importlib.import_module(module_name), fn_name)

    async def _handler(ctx, args, *, confirmed: bool = False):
        if spec.pass_name and spec.pass_confirmed:
            return await fn(ctx, spec.name, args, confirmed=confirmed)
        if spec.pass_name:
            return await fn(ctx, spec.name, args)
        if spec.pass_confirmed:
            return await fn(ctx, args, confirmed=confirmed)
        return await fn(ctx, args)

    return _handler
