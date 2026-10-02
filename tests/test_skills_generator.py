# tests/test_skills_generator.py
"""generator 纯逻辑单元测试：围栏剥离、warnings 转换、超时上抛。

不走 HTTP、不碰 DB/磁盘——端点层的错误分区在 tests/test_skills_ai_api.py。
"""
import asyncio

import pytest

from app.skills.generator import (GENERATE_TIMEOUT_S, PROMPT_MAX, SKILL_SYSTEM_PROMPT,
                                  generate_skill_draft, unwrap_fences)

pytestmark = pytest.mark.asyncio


class FakeLLM:
    """一次性吐出固定 content 的假 LLM（形状对齐 OpenAICompatLLM.chat_stream）。"""

    def __init__(self, content: str):
        self._content = content
        self.received: list[list[dict]] = []

    async def chat_stream(self, messages, tools):
        self.received.append([dict(m) for m in messages])
        if self._content:
            yield {"type": "content_delta", "content": self._content}
        yield {"type": "done", "message": {"role": "assistant",
                                           "content": self._content or None,
                                           "tool_calls": None}}


GOOD_MD = ("---\n"
           "name: meeting-minutes\n"
           "description: 把会议记录整理成结构化周报；当用户提到会议纪要或周报时使用。\n"
           "---\n"
           "# 会议纪要\n按议题分组输出。\n")


def test_unwrap_fences_strips_markdown_fence():
    raw = "```markdown\n" + GOOD_MD + "```"
    assert unwrap_fences(raw) == GOOD_MD.strip()


def test_unwrap_fences_strips_bom_and_blank_lines():
    raw = "\ufeff\n\n\n" + GOOD_MD + "\n\n   \n"
    assert unwrap_fences(raw) == GOOD_MD.strip()


def test_unwrap_fences_keeps_inner_code_block():
    body = GOOD_MD.strip() + "\n\n```bash\necho hi\n```"
    assert unwrap_fences("```\n" + body + "\n```") == body


def test_unwrap_fences_on_clean_text_is_noop():
    assert unwrap_fences(GOOD_MD) == GOOD_MD.strip()


def test_unwrap_fences_handles_empty_and_none():
    assert unwrap_fences("") == ""
    assert unwrap_fences(None) == ""
    assert unwrap_fences("   \n ") == ""


async def test_generate_returns_clean_draft_without_warnings():
    d = await generate_skill_draft(FakeLLM(GOOD_MD), "帮我整理会议纪要")
    assert d["name"] == "meeting-minutes"
    assert "周报" in d["description"]
    assert d["content"] == GOOD_MD.strip()
    assert d["warnings"] == []


async def test_generate_sends_system_prompt_and_raw_description():
    """提示词契约：system 用常量、user 是描述原文不额外包装（spec §5.4）。"""
    llm = FakeLLM(GOOD_MD)
    await generate_skill_draft(llm, "  帮我把会议记录整理成周报  ")
    msgs = llm.received[0]
    assert [m["role"] for m in msgs] == ["system", "user"]
    assert msgs[0]["content"] == SKILL_SYSTEM_PROMPT
    assert msgs[1]["content"] == "  帮我把会议记录整理成周报  "


async def test_generate_warns_when_frontmatter_missing():
    """模型返 JSON 文本：200 + warning，content 原样透传（spec §7 边界5）。"""
    raw = '{"name": "x", "description": "y"}'
    d = await generate_skill_draft(FakeLLM(raw), "p")
    assert d["warnings"] and "frontmatter" in d["warnings"][0]
    assert d["content"] == raw
    assert d["name"] == "" and d["description"] == ""


async def test_generate_warns_when_description_missing():
    d = await generate_skill_draft(FakeLLM("---\nname: only-name\n---\nbody"), "p")
    assert d["warnings"] == ["frontmatter 缺少必填字段 description"]
    assert d["name"] == ""   # parse_skill_md 抛错时不返回半截结果


async def test_generate_warns_when_frontmatter_unclosed():
    d = await generate_skill_draft(FakeLLM("---\nname: x\ndescription: y\n# 正文"), "p")
    assert d["warnings"] == ["SKILL.md frontmatter 未闭合（缺少结束的 ---）"]


async def test_generate_warns_on_chinese_name_but_keeps_it():
    """中文 name 不拦，只告警（spec §7 边界8）；name 仍解析出来供前端摘要显示。"""
    raw = "---\nname: 会议纪要\ndescription: 整理会议纪要\n---\n正文"
    d = await generate_skill_draft(FakeLLM(raw), "p")
    assert d["name"] == "会议纪要"
    assert any("非 ASCII" in w for w in d["warnings"])


async def test_generate_warns_on_mixed_ascii_cjk_name():
    """中英混合同样命中退化告警：判据是 encode('ascii') 而非「全中文」。"""
    raw = "---\nname: meeting-纪要\ndescription: d\n---\n正文"
    d = await generate_skill_draft(FakeLLM(raw), "p")
    assert d["name"] == "meeting-纪要"
    assert any("非 ASCII" in w for w in d["warnings"])


async def test_generate_on_empty_model_output_yields_warning_not_crash():
    """空返回在纯逻辑层是一条 warning；判 502 是端点的责任（spec §7 边界4）。"""
    d = await generate_skill_draft(FakeLLM(""), "p")
    assert d["content"] == ""
    assert d["warnings"] == ["SKILL.md 缺少 YAML frontmatter（应以 --- 开头）"]


async def test_generate_uses_done_message_content_when_no_delta():
    """某些端点只在 done 里给全文、不发 delta：兜底路径必须生效。"""

    class DoneOnlyLLM:
        async def chat_stream(self, messages, tools):
            yield {"type": "done", "message": {"role": "assistant",
                                               "content": GOOD_MD, "tool_calls": None}}

    d = await generate_skill_draft(DoneOnlyLLM(), "p")
    assert d["name"] == "meeting-minutes"
    assert d["warnings"] == []


async def test_generate_propagates_upstream_error():
    """上游异常原样上抛，不在纯逻辑层吞成 warning（端点据此映射 502）。"""

    class BoomLLM:
        async def chat_stream(self, messages, tools):
            raise RuntimeError("connection reset")
            yield  # pragma: no cover

    with pytest.raises(RuntimeError, match="connection reset"):
        await generate_skill_draft(BoomLLM(), "p")


async def test_generate_timeout_propagates():
    """慢上游到点抛 asyncio.TimeoutError；timeout 参数可注入，用例不真等 60 秒。"""

    class SlowLLM:
        async def chat_stream(self, messages, tools):
            await asyncio.sleep(1)
            yield {"type": "content_delta", "content": GOOD_MD}

    with pytest.raises(asyncio.TimeoutError):
        await generate_skill_draft(SlowLLM(), "p", timeout=0.05)


def test_constants_are_pinned():
    """常量是对外契约的一部分（端点错误文案直接引用），改动必须是有意的。"""
    assert PROMPT_MAX == 4000
    assert GENERATE_TIMEOUT_S == 60
    assert SKILL_SYSTEM_PROMPT.startswith("你是 Agent Skill 的作者")
    for needle in ("kebab-case", "name", "description", "Anthropic Agent Skills", "```"):
        assert needle in SKILL_SYSTEM_PROMPT
