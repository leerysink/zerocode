"""memory_write 工具（ticket 003；spec 故事 6、7、8、9、10、22）。

主接缝 = `execute_tool(ctx, "memory_write", args)`：直接断返回结构与报错文案；
再用 `ScriptedLLM` 的 tool_call 剧本端到端验证「写入 → 下一次 run 注入 <memories>」。

workspace_id 一律由 session 反查，**模型传入即拒**（故事 18 的工具侧）；
id 以字符串回传，避免前端 JSON 里 19 位大整数被 Number() 截断（全局红线）。
"""
import asyncio
import pytest
from app.db import make_session_factory
from app.agent.llm import ScriptedLLM
from app.agent.loop import AgentRunner
from app.agent.tools import ToolContext, ToolError, execute_tool
from app.services import memory_service as mem
from tests.test_agent_loop import Recorder, cfg_provider, delta, done, mk_session, tool_call


def _ctx(test_engine, sid: int, tmp_path, with_db: bool = True) -> ToolContext:
    return ToolContext(root=tmp_path, session_id=sid,
                       session_factory=make_session_factory(test_engine) if with_db else None,
                       send=Recorder())


async def _write(test_engine, sid: int, tmp_path, **args) -> dict:
    return await execute_tool(_ctx(test_engine, sid, tmp_path), "memory_write", args)


async def _ws_of(test_engine, sid: int) -> int:
    return await mem.workspace_id_of_session(make_session_factory(test_engine), sid)


async def _system_text_after(test_engine, sid: int, user_text: str) -> str:
    """在既有会话上再跑一轮，返回该轮发给模型的 system 文本（复用 ScriptedLLM 快照）。"""
    llm = ScriptedLLM([[delta("好"), *done("好了")]])
    runner = AgentRunner(make_session_factory(test_engine), llm, cfg_provider(), Recorder(),
                         lambda *a, **k: asyncio.sleep(0, None), lambda *a, **k: True)
    status = await runner.run(sid, user_text, asyncio.Event())
    assert status == "idle"
    return llm.received[0][0]["content"]


# ---------------- 正常路径 ----------------

async def test_create_lands_in_session_workspace_with_string_id(test_engine, tmp_path):
    sid = await mk_session(test_engine, tmp_path)
    sf = make_session_factory(test_engine)
    ws = await _ws_of(test_engine, sid)
    r = await _write(test_engine, sid, tmp_path, op="create", type="preference",
                     content="导出统一走 pandas 分块", title="导出规范")
    assert r["ok"] is True and r["op"] == "create"
    assert isinstance(r["id"], str) and r["id"].isdigit()  # 字符串形态，不经 int()/Number()
    rows = await mem.list_enabled(sf, ws)
    assert [x["content"] for x in rows] == ["导出统一走 pandas 分块"]
    assert str(rows[0]["id"]) == r["id"]
    assert str(rows[0]["workspace_id"]) == str(ws)
    assert rows[0]["enabled"] is True and rows[0]["type"] == "preference"


async def test_update_changes_type_content_and_title(test_engine, tmp_path):
    sid = await mk_session(test_engine, tmp_path)
    sf = make_session_factory(test_engine)
    ws = await _ws_of(test_engine, sid)
    created = await mem.create(sf, ws, "fact", "导出走 pandas", title="旧标题")
    r = await _write(test_engine, sid, tmp_path, op="update", id=created["id"],
                     type="decision", content="导出改走 openpyxl 分块", title="导出方案")
    assert r["ok"] is True and r["id"] == str(created["id"])
    rows = await mem.list_enabled(sf, ws)
    assert len(rows) == 1  # update 绝不新增行（故事 7「不静默不新建」）
    assert rows[0]["type"] == "decision" and rows[0]["content"] == "导出改走 openpyxl 分块"
    assert rows[0]["title"] == "导出方案"


async def test_delete_soft_disables_and_stops_recalling(test_engine, tmp_path):
    sid = await mk_session(test_engine, tmp_path)
    sf = make_session_factory(test_engine)
    ws = await _ws_of(test_engine, sid)
    created = await mem.create(sf, ws, "pitfall", "pandas 读 xlsx 必须先装 openpyxl")
    r = await _write(test_engine, sid, tmp_path, op="delete", id=created["id"])
    assert r["ok"] is True and r["enabled"] is False
    assert await mem.list_enabled(sf, ws) == []  # 停用即不再被召回（故事 8 / D-5）
    assert await mem.recall_memories(sf, ws, "pandas openpyxl") == []


async def test_tool_written_memory_injects_on_next_run(test_engine, tmp_path):
    """端到端示踪弹：一句话让 Agent 记住 → 入库 → 下一次 run 的 system 带上 <memories>。"""
    sid = await mk_session(test_engine, tmp_path)
    sf = make_session_factory(test_engine)
    llm = ScriptedLLM([
        done(None, [tool_call("c1", "memory_write", op="create", type="fact",
                              content="接口字段一律返回驼峰命名")]),
        done("已记录"),
        done("收到"),
    ])
    runner = AgentRunner(sf, llm, cfg_provider(), Recorder(),
                         lambda *a, **k: asyncio.sleep(0, None), lambda *a, **k: True)
    status = await runner.run(sid, "先写个接口", asyncio.Event())
    assert status == "idle"
    assert "<memories>" not in llm.received[0][0]["content"]  # 写之前没有记忆块
    status = await runner.run(sid, "接口字段怎么命名", asyncio.Event())
    assert status == "idle"
    system_text = llm.received[-1][0]["content"]
    assert "<memories>" in system_text and "接口字段一律返回驼峰命名" in system_text


async def test_tool_result_carries_ok_and_error_text_to_model(test_engine, tmp_path):
    """工具报错必须经 _dispatch 变成 {ok:false,error}，而不是抛穿主链路打断对话。"""
    sid = await mk_session(test_engine, tmp_path)
    sf = make_session_factory(test_engine)
    llm = ScriptedLLM([
        done(None, [tool_call("c1", "memory_write", op="create", type="whatever", content="x")]),
        done("知道了"),
    ])
    rec = Recorder()
    runner = AgentRunner(sf, llm, cfg_provider(), rec,
                         lambda *a, **k: asyncio.sleep(0, None), lambda *a, **k: True)
    await runner.run(sid, "记点东西", asyncio.Event())
    tr = next(e for e in rec.events if e["type"] == "tool_result")
    assert tr["result"]["ok"] is False and "记忆类型" in tr["result"]["error"]
    assert await mem.list_enabled(sf, await _ws_of(test_engine, sid)) == []


# ---------------- 边界与异常分支 ----------------

async def test_rejects_unknown_type(test_engine, tmp_path):
    sid = await mk_session(test_engine, tmp_path)
    with pytest.raises(ToolError) as ei:
        await _write(test_engine, sid, tmp_path, op="create", type="memo", content="一些内容")
    assert "memo" in str(ei.value)
    assert " / ".join(mem.MEMORY_TYPES) in str(ei.value)  # 报错要给出可选清单


async def test_rejects_empty_type(test_engine, tmp_path):
    sid = await mk_session(test_engine, tmp_path)
    with pytest.raises(ToolError):
        await _write(test_engine, sid, tmp_path, op="create", type="", content="一些内容")


@pytest.mark.parametrize("content", ["", "   ", "　　", None, 123])
async def test_rejects_blank_or_non_string_content(test_engine, tmp_path, content):
    """空串 / 纯半角空格 / 纯全角空格 / 缺失 / 非字符串：一律明确拒绝，不落半条也不报 500 栈。"""
    sid = await mk_session(test_engine, tmp_path)
    sf = make_session_factory(test_engine)
    ws = await _ws_of(test_engine, sid)
    args = {"op": "create", "type": "fact"}
    if content is not None:
        args["content"] = content
    with pytest.raises(ToolError):
        await _write(test_engine, sid, tmp_path, **args)
    assert await mem.list_enabled(sf, ws) == []


@pytest.mark.parametrize("op", ["insert", "", "CREATE ", None])
async def test_rejects_unknown_or_missing_op(test_engine, tmp_path, op):
    sid = await mk_session(test_engine, tmp_path)
    args = {"type": "fact", "content": "一些内容"}
    if op is not None:
        args["op"] = op
    with pytest.raises(ToolError) as ei:
        await _write(test_engine, sid, tmp_path, **args)
    assert "create" in str(ei.value)  # 报错给出合法 op 清单


async def test_create_is_case_insensitive_on_op(test_engine, tmp_path):
    """模型常把枚举写成大写：CREATE 应等价 create，不该因大小写判成非法操作。"""
    sid = await mk_session(test_engine, tmp_path)
    r = await _write(test_engine, sid, tmp_path, op="CREATE", type="fact", content="大写 op 也要能记")
    assert r["ok"] is True


async def test_rejects_model_supplied_workspace_id(test_engine, tmp_path):
    """workspace_id 只能由 session 反查：模型递别的工作区 id 必须直接拒。"""
    sid = await mk_session(test_engine, tmp_path)
    with pytest.raises(ToolError) as ei:
        await _write(test_engine, sid, tmp_path, op="create", type="fact",
                     content="越权写入", workspace_id=999999999)
    assert "workspace_id" in str(ei.value)


async def test_rejects_update_without_id(test_engine, tmp_path):
    sid = await mk_session(test_engine, tmp_path)
    with pytest.raises(ToolError) as ei:
        await _write(test_engine, sid, tmp_path, op="update", content="没有 id 的更新")
    assert "id" in str(ei.value)


async def test_rejects_delete_without_id(test_engine, tmp_path):
    sid = await mk_session(test_engine, tmp_path)
    with pytest.raises(ToolError):
        await _write(test_engine, sid, tmp_path, op="delete")


@pytest.mark.parametrize("bad_id", ["abc", "1.5", "-0x1", "", "²"])
async def test_rejects_non_integer_id(test_engine, tmp_path, bad_id):
    """id 形态非法时给明确失败，不能让 asyncpg 的类型异常冒成「工具执行异常」。"""
    sid = await mk_session(test_engine, tmp_path)
    with pytest.raises(ToolError) as ei:
        await _write(test_engine, sid, tmp_path, op="update", id=bad_id, content="改它")
    assert "id" in str(ei.value)


async def test_update_nonexistent_id_fails_without_creating(test_engine, tmp_path):
    """故事 7：id 不存在 → 明确失败，既不静默成功也不当成新建。"""
    sid = await mk_session(test_engine, tmp_path)
    sf = make_session_factory(test_engine)
    ws = await _ws_of(test_engine, sid)
    with pytest.raises(ToolError) as ei:
        await _write(test_engine, sid, tmp_path, op="update", id=987654321, content="不存在就改不了")
    assert "不存在" in str(ei.value)
    assert await mem.list_enabled(sf, ws) == []


async def test_update_foreign_workspace_id_rejected(test_engine, tmp_path):
    """故事 18：改到别人工作区的记忆 → 明确失败，且对方那条原样不动。"""
    other = tmp_path / "other"
    other.mkdir()
    sid_other = await mk_session(test_engine, other)
    sf = make_session_factory(test_engine)
    ws_other = await _ws_of(test_engine, sid_other)
    target = await mem.create(sf, ws_other, "fact", "他区约定不得被我改")

    sid_here = await mk_session(test_engine, tmp_path)
    with pytest.raises(ToolError):
        await _write(test_engine, sid_here, tmp_path, op="update", id=target["id"], content="篡改")
    rows = await mem.list_enabled(sf, ws_other)
    assert [r["content"] for r in rows] == ["他区约定不得被我改"]


async def test_delete_foreign_workspace_id_rejected(test_engine, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    sid_other = await mk_session(test_engine, other)
    sf = make_session_factory(test_engine)
    ws_other = await _ws_of(test_engine, sid_other)
    target = await mem.create(sf, ws_other, "fact", "他区记忆不得被我停")

    sid_here = await mk_session(test_engine, tmp_path)
    with pytest.raises(ToolError):
        await _write(test_engine, sid_here, tmp_path, op="delete", id=target["id"])
    assert len(await mem.list_enabled(sf, ws_other)) == 1


async def test_duplicate_create_hints_existing_but_still_creates(test_engine, tmp_path):
    """故事 10 / D-4：查重命中给既有条目摘要与 update 建议，但不硬拦——新条仍入库。"""
    sid = await mk_session(test_engine, tmp_path)
    sf = make_session_factory(test_engine)
    ws = await _ws_of(test_engine, sid)
    old = await mem.create(sf, ws, "preference", "导出统一走 pandas 分块", title="导出规范")
    r = await _write(test_engine, sid, tmp_path, op="create", type="preference",
                     content="导出统一走 pandas 分块，别改用 csv")
    assert r["ok"] is True
    assert [s["id"] for s in r["similar"]] == [str(old["id"])]
    assert "update" in r["hint"]
    assert "导出规范" in r["similar"][0]["title"]
    assert len(await mem.list_enabled(sf, ws)) == 2  # 不硬拦


async def test_first_create_has_no_similarity_hint(test_engine, tmp_path):
    """库里没有相似旧条时不该凭空给出 similar/hint（否则模型被误导以为有历史）。"""
    sid = await mk_session(test_engine, tmp_path)
    r = await _write(test_engine, sid, tmp_path, op="create", type="fact", content="全新的约定")
    assert "similar" not in r and "hint" not in r


async def test_chinese_quotes_and_newline_content_roundtrip(test_engine, tmp_path):
    """票面验收：中文/引号/换行的 content 入库往返与注入都不能被截断或转义。

    注入侧比的是**折行后的形态**：`_memory_line` 会把正文里的换行换成单个空格（否则
    多行记忆会破 `<memories>` 块格式），文字本身必须一字不少。
    """
    sid = await mk_session(test_engine, tmp_path)
    sf = make_session_factory(test_engine)
    ws = await _ws_of(test_engine, sid)
    body = '接口约定：“code=0” 才算成功\n路径 docs/a-b.md\n备注含 <tag> 尖括号'
    r = await _write(test_engine, sid, tmp_path, op="create", type="fact", content=body)
    rows = await mem.list_enabled(sf, ws)
    assert rows[0]["content"] == body  # 原样往返，不被截断或转义
    recall = await mem.recall_memories(sf, ws, "code 路径 docs")
    assert len(recall) == 1
    system_text = await _system_text_after(test_engine, sid, "code 路径 docs 该怎么写")
    assert "<memories>" in system_text and "</memories>" in system_text
    assert " ".join(body.split()) in system_text  # 引号、尖括号、最后一段全部注到


async def test_update_over_long_title_rejected(test_engine, tmp_path):
    """title 阈值 200 的越界分支（边界 ±1）：201 字拒绝且原行保持旧标题。"""
    sid = await mk_session(test_engine, tmp_path)
    sf = make_session_factory(test_engine)
    ws = await _ws_of(test_engine, sid)
    created = await mem.create(sf, ws, "fact", "pandas 导出", title="原标题")
    with pytest.raises(ToolError) as ei:
        await _write(test_engine, sid, tmp_path, op="update", id=created["id"],
                     title="标" * (mem.TITLE_MAX_LEN + 1))
    assert "200" in str(ei.value)
    rows = await mem.list_enabled(sf, ws)
    assert rows[0]["title"] == "原标题"


async def test_update_without_any_field_rejected(test_engine, tmp_path):
    sid = await mk_session(test_engine, tmp_path)
    sf = make_session_factory(test_engine)
    ws = await _ws_of(test_engine, sid)
    created = await mem.create(sf, ws, "fact", "pandas 导出")
    with pytest.raises(ToolError):
        await _write(test_engine, sid, tmp_path, op="update", id=created["id"])
    assert (await mem.list_enabled(sf, ws))[0]["content"] == "pandas 导出"


async def test_rejects_when_session_has_no_workspace(test_engine, tmp_path):
    """会话不存在 / 已被级联删除 → 明确失败，不能落到「无工作区」的空靶。"""
    with pytest.raises(ToolError) as ei:
        await _write(test_engine, 123456789, tmp_path, op="create", type="fact", content="无处可记")
    assert "工作区" in str(ei.value)


async def test_rejects_without_session_factory(test_engine, tmp_path):
    sid = await mk_session(test_engine, tmp_path)
    ctx = _ctx(test_engine, sid, tmp_path, with_db=False)
    with pytest.raises(ToolError):
        await execute_tool(ctx, "memory_write", {"op": "create", "type": "fact", "content": "x"})


async def test_soft_deleted_entry_can_be_re_enabled_via_service(test_engine, tmp_path):
    """D-5「软删可撤销」：工具侧只停用，恢复走 service（面板/后续 API 用）。"""
    sid = await mk_session(test_engine, tmp_path)
    sf = make_session_factory(test_engine)
    ws = await _ws_of(test_engine, sid)
    created = await mem.create(sf, ws, "fact", "pandas 导出")
    await _write(test_engine, sid, tmp_path, op="delete", id=created["id"])
    assert await mem.list_enabled(sf, ws) == []
    await mem.update(sf, ws, created["id"], enabled=True)
    assert [r["content"] for r in await mem.list_enabled(sf, ws)] == ["pandas 导出"]
