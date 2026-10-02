# tests/test_skills_ai_api.py
"""技能 AI 生成通道的 HTTP 面（spec docs/specs/2026-10-03-skill-ai-generation.md §7）。

接缝＝HTTP API：generate 零副作用、adopt 硬校验落盘落库、错误分区码表、13 条边界。
LLM 用 monkeypatch 换掉 app.skills.generator.OpenAICompatLLM（对齐 tests/test_config.py
既有惯例，不新增 app.state 字段）。
"""
import asyncio

import pytest
from pathlib import Path

from sqlalchemy import text

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _skills_data_dir(tmp_path, monkeypatch):
    """技能母本落 tmp，绝不写仓库 data/。data_dir() 每次现读 env，故作用域内生效。"""
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path / "data"))


DRAFT_MD = ("---\n"
            "name: meeting-minutes\n"
            "description: 把会议记录整理成结构化周报；当用户提到会议纪要或周报时使用。\n"
            "---\n"
            "# 会议纪要\n按议题分组输出。\n")


class FakeLLM:
    """换掉 generator.OpenAICompatLLM：构造签名与真类一致，输出可脚本化。"""

    script: list[str] = [DRAFT_MD]     # 类属性，用例按需覆写
    fail_with: Exception | None = None
    delay: float = 0.0
    built_with: list[dict] = []

    def __init__(self, base_url, api_key, model):
        type(self).built_with.append({"base_url": base_url, "api_key": api_key, "model": model})

    async def chat_stream(self, messages, tools):
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail_with is not None:
            raise self.fail_with
        content = type(self).script[0]
        if content:
            yield {"type": "content_delta", "content": content}
        yield {"type": "done", "message": {"role": "assistant",
                                           "content": content or None, "tool_calls": None}}


@pytest.fixture
def fake_llm(monkeypatch):
    """注入假 LLM 并复位类级脚本状态（用例之间不串味）。"""
    from app.skills import generator
    FakeLLM.script = [DRAFT_MD]
    FakeLLM.fail_with = None
    FakeLLM.delay = 0.0
    FakeLLM.built_with = []
    monkeypatch.setattr(generator, "OpenAICompatLLM", FakeLLM)
    return FakeLLM


async def test_migration_adds_nullable_generation_prompt_column(client):
    """列必须是 text 且可空：老行不回填，NOT NULL 会让存量库迁移直接失败。"""
    async with client.app.state.session_factory() as s:
        row = (await s.execute(text(
            "SELECT data_type, is_nullable FROM information_schema.columns "
            "WHERE table_name='user_skill' AND column_name='generation_prompt'"))).first()
    assert row is not None, "user_skill.generation_prompt 列不存在（迁移未应用）"
    assert row[0] == "text"
    assert row[1] == "YES"


async def test_name_max_is_single_source_of_truth():
    """installer 不得再自己写一个 200：两通道共用 library.NAME_MAX，否则改列宽要改两处。"""
    from app.skills import installer
    from app.skills.library import NAME_MAX
    assert NAME_MAX == 200
    assert installer._NAME_MAX is NAME_MAX


async def _uid(client) -> int:
    return (await client.get("/api/auth/me")).json()["id"]


async def _skill_rows(client) -> list[dict]:
    async with client.app.state.session_factory() as s:
        return [dict(r) for r in (await s.execute(text(
            "SELECT id, slug, name, source_type, generation_prompt "
            "FROM user_skill ORDER BY id"))).mappings().all()]


def _payload_with_name(name: str) -> dict:
    return {"name": name, "description": "d", "files": {"SKILL.md": b"---\n---\n"},
            "source_url": "https://x.invalid/a/SKILL.md", "subpath": None, "source_type": "url"}


async def test_persist_skill_rejects_overlong_name_as_400(client, monkeypatch):
    """name 超列宽必须是 400，不能冒成 500：asyncpg 的 StringDataRightTruncation
    不是 IntegrityError，会走 except Exception 清完目录再原样上抛。"""
    from app.api import skills as sapi

    # 必须是 async：install() 里是 `payload = await installer.fetch_skill_from_url(...)`，
    # 换成同步 lambda 会 await 一个 dict → TypeError 冒成 500，测试红得不是地方。
    async def fake_fetch(url, transport=None):
        return _payload_with_name("x" * 201)

    monkeypatch.setattr(sapi.installer, "fetch_skill_from_url", fake_fetch)
    r = await client.post("/api/skills/install", json={"url": "https://x.invalid/a/SKILL.md"})
    assert r.status_code == 400, r.text
    assert "200" in r.text
    # 零落盘：连技能根目录都不该建出来
    from app.skills.library import skills_root
    assert not skills_root(await _uid(client)).exists()


async def test_persist_skill_accepts_name_at_exactly_max(client, monkeypatch):
    """边界（阈值本身）：恰好 200 字符的 name 不是错误，守卫只能用 >。"""
    from app.api import skills as sapi
    from app.skills.library import NAME_MAX

    async def fake_fetch(url, transport=None):
        return _payload_with_name("x" * NAME_MAX)

    monkeypatch.setattr(sapi.installer, "fetch_skill_from_url", fake_fetch)
    r = await client.post("/api/skills/install", json={"url": "https://x.invalid/a/SKILL.md"})
    assert r.status_code == 201, r.text
    assert len(r.json()["name"]) == NAME_MAX
    # 新列出现在响应形状里（直装通道恒 NULL）
    assert r.json()["generation_prompt"] is None


async def test_generate_returns_draft_with_zero_side_effect(client, fake_llm):
    """主流程 + 边界1：草稿返回，且磁盘与 DB 一个字节都不多。"""
    from app.skills.library import skills_root
    before = await _skill_rows(client)
    r = await client.post("/api/skills/generate", json={"prompt": "帮我把会议记录整理成周报"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["name"] == "meeting-minutes"
    assert "周报" in d["description"]
    assert d["content"] == DRAFT_MD.strip()
    assert d["warnings"] == []
    assert await _skill_rows(client) == before
    assert not skills_root(await _uid(client)).exists()


async def test_generate_uses_account_default_model(client, fake_llm):
    """模型来自 resolve_model_for_user 的全局回落（无 model_config 默认行时）。"""
    r = await client.post("/api/skills/generate", json={"prompt": "p"})
    assert r.status_code == 200, r.text
    assert fake_llm.built_with == [{"base_url": "https://api.deepseek.com/v1",
                                    "api_key": "", "model": "deepseek-chat"}]


@pytest.mark.parametrize("bad", ["", "   ", "\n\t ", None])
async def test_generate_rejects_blank_prompt_as_400(client, fake_llm, bad):
    """边界2：空串/纯空格/换行制表/字段缺失一律 400，且一次模型都不调。"""
    payload = {} if bad is None else {"prompt": bad}
    r = await client.post("/api/skills/generate", json=payload)
    assert r.status_code in (400, 422), r.text   # 缺字段由 pydantic 判 422
    assert fake_llm.built_with == []


async def test_generate_prompt_at_4000_chars_is_accepted(client, fake_llm):
    """边界2：恰好等于阈值放行（阈值本身不是错误）。"""
    from app.skills.generator import PROMPT_MAX
    r = await client.post("/api/skills/generate", json={"prompt": "a" * PROMPT_MAX})
    assert r.status_code == 200, r.text


async def test_generate_prompt_at_4001_chars_is_rejected(client, fake_llm):
    """边界2：阈值 +1 拒绝，且不调模型。"""
    from app.skills.generator import PROMPT_MAX
    r = await client.post("/api/skills/generate", json={"prompt": "a" * (PROMPT_MAX + 1)})
    assert r.status_code == 400, r.text
    assert str(PROMPT_MAX) in r.text
    assert fake_llm.built_with == []


async def test_generate_unwraps_fenced_model_output(client, fake_llm):
    """边界3：模型整段包 ```markdown 围栏 + BOM + 大量空行，剥完仍解析出 name。"""
    fake_llm.script = ["\ufeff\n\n```markdown\n" + DRAFT_MD + "```\n\n\n"]
    r = await client.post("/api/skills/generate", json={"prompt": "p"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["name"] == "meeting-minutes"
    assert d["warnings"] == []
    assert "```" not in d["content"]


async def test_generate_empty_model_output_is_502(client, fake_llm):
    """边界4：空返回是上游问题（502 可重试），不是草稿不规范（200+warnings）。"""
    fake_llm.script = [""]
    r = await client.post("/api/skills/generate", json={"prompt": "p"})
    assert r.status_code == 502, r.text
    assert "空内容" in r.text
    from app.skills.library import skills_root
    assert not skills_root(await _uid(client)).exists()


async def test_generate_whitespace_only_model_output_is_502(client, fake_llm):
    """边界4 变体：只有空白的返回同样判空。"""
    fake_llm.script = ["   \n\n  "]
    assert (await client.post("/api/skills/generate",
                              json={"prompt": "p"})).status_code == 502


async def test_generate_malformed_draft_returns_200_with_warnings(client, fake_llm):
    """边界5：草稿不规范**不失败**——200 + warnings，content 原样透传。"""
    fake_llm.script = ['{"name": "x"}']
    r = await client.post("/api/skills/generate", json={"prompt": "p"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["warnings"] and "frontmatter" in d["warnings"][0]
    assert d["content"] == '{"name": "x"}'


async def test_generate_upstream_exception_is_502_with_reason(client, fake_llm):
    """边界6：上游抛异常 → 502，detail 含异常类型（不泄露内部栈），零落盘。"""
    fake_llm.fail_with = RuntimeError("connection reset by peer")
    r = await client.post("/api/skills/generate", json={"prompt": "p"})
    assert r.status_code == 502, r.text
    assert "RuntimeError" in r.text
    from app.skills.library import skills_root
    assert not skills_root(await _uid(client)).exists()


async def test_generate_upstream_timeout_is_502(client, fake_llm, monkeypatch):
    """边界6：超时走专属文案；把 GENERATE_TIMEOUT_S 调小，用例不真等 60 秒。"""
    from app.skills import generator
    monkeypatch.setattr(generator, "GENERATE_TIMEOUT_S", 0.05)
    fake_llm.delay = 2.0
    r = await client.post("/api/skills/generate", json={"prompt": "p"})
    assert r.status_code == 502, r.text
    assert "超时" in r.text


async def test_generate_without_any_model_is_400(client, fake_llm):
    """边界7：全局种子里的 llm.* 被自己清空 → 400 且文案指向设置页，不是裸 500。"""
    saved = (await client.get("/api/config")).json()
    try:
        await client.put("/api/config", json={"llm.base_url": "", "llm.model": ""})
        r = await client.post("/api/skills/generate", json={"prompt": "p"})
        assert r.status_code == 400, r.text
        assert "未配置模型" in r.text
        assert fake_llm.built_with == []
    finally:
        await client.put("/api/config", json={
            "llm.base_url": saved.get("llm.base_url") or "",
            "llm.api_key": saved.get("llm.api_key") or "",
            "llm.model": saved.get("llm.model") or ""})


async def test_generate_requires_login(anon_client):
    """边界11：全局 guard 兜住，端点不新造鉴权分支。"""
    assert (await anon_client.post("/api/skills/generate",
                                   json={"prompt": "p"})).status_code == 401
