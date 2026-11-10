"""工具注册表是 schema / 挂载 / 分派 / 前端口径的单一真值（2026-11-10）。

这些用例防的不是「今天内容对不对」，而是「将来加一个工具时有没有漏改某一处」：
- 后端 llm.py 的兼容别名必须由 registry 派生，不能各自留一份字面量；
- 前端 ChatMessages.vue 的工具名数组必须与 registry 分组同名同序；
- 每个会挂载的工具都必须有可解析的 handler，循环自处理的工具不能混进 handler 面。
"""
import re
from pathlib import Path

from app.agent import registry

ROOT = Path(__file__).resolve().parents[1]
BASE_NAMES = ["list_dir", "read_file", "write_file", "edit_file",
              "ask_user", "todo_write", "memory_write"]
APP_NAMES = ["preview_status", "preview_logs", "preview_restart",
             "db_query", "db_write", "preview_page",
             "browser_open", "browser_step", "browser_close"]


def _names(app=False, clarify=False):
    return [t["function"]["name"] for t in registry.build_tools(app=app, clarify=clarify)]


def _js_list(const_name: str) -> list[str]:
    text = (ROOT / "frontend/src/components/ChatMessages.vue").read_text(encoding="utf-8")
    m = re.search(rf"const {const_name} = \[(.*?)\]", text, re.S)
    assert m, f"前端缺少工具名单常量 {const_name}"
    return re.findall(r"'([^']+)'", m.group(1))


def test_specs_have_unique_names_and_matching_schemas():
    names = [s.name for s in registry.SPECS]
    assert len(names) == len(set(names))
    for spec in registry.SPECS:
        assert spec.schema["type"] == "function"
        assert spec.schema["function"]["name"] == spec.name


def test_base_scope_keeps_the_existing_order():
    assert _names() == BASE_NAMES


def test_app_scope_mounts_exactly_the_app_tools():
    assert _names(app=True) == BASE_NAMES + APP_NAMES


def test_clarify_scope_mounts_only_clarify_done():
    assert _names(clarify=True) == BASE_NAMES + ["clarify_done"]


def test_app_and_clarify_mount_in_a_stable_order():
    assert _names(app=True, clarify=True) == BASE_NAMES + APP_NAMES + ["clarify_done"]


def test_confirm_policy_is_registry_metadata():
    assert registry.spec_of("write_file").confirm == "write"
    assert registry.spec_of("edit_file").confirm == "write"
    assert registry.spec_of("db_write").confirm == "always"
    assert registry.spec_of("preview_restart").confirm == "preview_restart"
    assert registry.spec_of("read_file").confirm == "none"


def test_every_executable_tool_resolves_a_handler():
    for spec in registry.SPECS:
        if spec.name in ("ask_user", "clarify_done"):
            assert spec.handler_ref is None
            continue
        assert callable(registry.resolve_handler(spec)), spec.name


def test_loop_owned_tools_are_not_in_the_handler_face():
    for name in ("ask_user", "clarify_done"):
        assert registry.spec_of(name).handler_ref is None


def test_llm_aliases_are_derived_from_the_registry():
    from app.agent import llm

    assert llm.TOOLS == registry.schemas(scope=registry.SCOPE_ALWAYS)
    assert tuple(llm.PREVIEW_TOOL_NAMES) == registry.names("preview")
    assert tuple(llm.DB_TOOL_NAMES) == registry.names("db")
    assert tuple(llm.BROWSER_TOOL_NAMES) == registry.names("browser")
    assert tuple(llm.BROWSER_SESSION_TOOL_NAMES) == registry.names("browser_session")
    assert llm.CLARIFY_TOOL_NAMES == registry.names("clarify")
    assert tuple(t["function"]["name"] for t in llm.PREVIEW_TOOLS) == registry.names("preview")
    assert tuple(t["function"]["name"] for t in llm.DB_TOOLS) == registry.names("db")
    assert tuple(t["function"]["name"] for t in llm.BROWSER_TOOLS) == registry.names("browser")
    assert tuple(t["function"]["name"] for t in llm.BROWSER_SESSION_TOOLS) == registry.names("browser_session")
    assert tuple(t["function"]["name"] for t in llm.CLARIFY_TOOLS) == registry.names("clarify")


def test_browser_session_names_stay_the_same_object():
    """browser_session_tools 的位次名单读的是这份元组本身；复制一份字面量会静默漂移。"""
    from app.agent import browser_session_tools as bst
    from app.agent import llm

    assert bst.SESSION_TOOL_NAMES is llm.BROWSER_SESSION_TOOL_NAMES


def test_frontend_tool_name_lists_match_registry_groups():
    assert _js_list("PREVIEW_TOOL_NAMES") == list(registry.names("preview"))
    assert _js_list("PAGE_TOOL_NAMES") == list(registry.names("browser"))
    assert _js_list("BROWSER_SESSION_TOOL_NAMES") == list(registry.names("browser_session"))
