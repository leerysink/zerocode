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


# ---- Task 6: adopt 端点 ----

async def test_adopt_main_flow_creates_skill_with_ai_source(client, fake_llm):
    """主流程：generate → adopt → 列表可见 source_type='ai'、generation_prompt=描述原文。
    母本 SKILL.md 字节级等于提交正文（不是 generate 返回的 content 的副本，是 adopt 正文）。"""
    gen = await client.post("/api/skills/generate", json={"prompt": "帮我整理会议纪要"})
    assert gen.status_code == 200
    draft_content = gen.json()["content"]
    r = await client.post("/api/skills/adopt", json={
        "content": draft_content, "prompt": "帮我整理会议纪要"})
    assert r.status_code == 201, r.text
    sk = r.json()
    assert sk["name"] == "meeting-minutes"
    assert sk["source_type"] == "ai"
    assert sk["source_url"] == "ai://generated"
    assert sk["subpath"] is None
    assert sk["generation_prompt"] == "帮我整理会议纪要"
    # 母本字节级等于提交正文
    md = Path(sk["skill_dir"]) / "SKILL.md"
    assert md.read_bytes() == draft_content.strip().encode("utf-8")
    # 列表可见
    rows = await _skill_rows(client)
    assert len(rows) == 1
    assert rows[0]["source_type"] == "ai"
    assert rows[0]["generation_prompt"] == "帮我整理会议纪要"


async def test_adopt_round_trip_with_prompt_preserved(client, fake_llm):
    """generate 返回体 + 前端原样回传 prompt：round-trip 不丢字段。"""
    gen = await client.post("/api/skills/generate", json={"prompt": "周报整理"})
    r = await client.post("/api/skills/adopt", json={
        "content": gen.json()["content"], "prompt": "周报整理"})
    assert r.status_code == 201
    assert r.json()["generation_prompt"] == "周报整理"


async def test_adopt_ignores_client_supplied_name_and_source(client, fake_llm):
    """不信任前端：body 塞 name/description/source_type，行值仍从正文重新解析。
    防止前端被篡改后可往 Agent 上下文注入脏 name。"""
    gen = await client.post("/api/skills/generate", json={"prompt": "p"})
    r = await client.post("/api/skills/adopt", json={
        "content": gen.json()["content"],
        "prompt": "p",
        "name": "INJECTED-NAME",
        "description": "INJECTED-DESC",
        "source_type": "url"})
    assert r.status_code == 201, r.text
    sk = r.json()
    assert sk["name"] == "meeting-minutes"   # 从正文解析，不是 INJECTED-NAME
    assert sk["source_type"] == "ai"         # 硬编码，不取前端值


async def test_adopt_strips_fences_from_pasted_content(client, fake_llm):
    """用户可能把整段带围栏的草稿粘回来：unwrap_fences 剥完再解析。"""
    fenced = "```markdown\n" + DRAFT_MD + "\n```"
    r = await client.post("/api/skills/adopt", json={"content": fenced, "prompt": "p"})
    assert r.status_code == 201, r.text
    assert r.json()["name"] == "meeting-minutes"
    # 母本不含围栏
    sk = r.json()
    md = Path(sk["skill_dir"]) / "SKILL.md"
    assert b"```" not in md.read_bytes()


@pytest.mark.parametrize("blank", ["", "   ", "\n\t", "```\n\n```"])
async def test_adopt_rejects_blank_content_as_400(client, fake_llm, blank):
    """边界：空正文/纯空白/剥完围栏后为空 → 400。"""
    r = await client.post("/api/skills/adopt", json={"content": blank, "prompt": "p"})
    assert r.status_code == 400, r.text


async def test_adopt_missing_frontmatter_is_400(client, fake_llm):
    """正文缺 frontmatter → 400 + 精确 detail（不是 500）。"""
    r = await client.post("/api/skills/adopt", json={
        "content": "# Just a heading\nSome body text", "prompt": "p"})
    assert r.status_code == 400, r.text
    assert "frontmatter" in r.text


async def test_adopt_unclosed_frontmatter_is_400(client, fake_llm):
    r = await client.post("/api/skills/adopt", json={
        "content": "---\nname: x\ndescription: y\n# 正文", "prompt": "p"})
    assert r.status_code == 400, r.text
    assert "未闭合" in r.text


@pytest.mark.parametrize("missing", ["name", "description"])
async def test_adopt_missing_required_field_is_400(client, fake_llm, missing):
    if missing == "name":
        content = "---\ndescription: only desc\n---\nbody"
    else:
        content = "---\nname: only-name\n---\nbody"
    r = await client.post("/api/skills/adopt", json={"content": content, "prompt": "p"})
    assert r.status_code == 400, r.text
    assert missing in r.text


async def test_adopt_chinese_name_warns_and_uses_skill_slug(client, fake_llm):
    """边界8：中文 name → adopt 后 slug 恰为 'skill'（slugify 退化）。"""
    content = "---\nname: 会议纪要\ndescription: 整理会议纪要\n---\n正文"
    r = await client.post("/api/skills/adopt", json={"content": content, "prompt": "p"})
    assert r.status_code == 201, r.text
    assert r.json()["slug"] == "skill"


async def test_adopt_same_name_three_times_gets_unique_slugs(client, fake_llm):
    """边界9：同名连装三次 → skill / skill-2 / skill-3 独立共存。"""
    content = "---\nname: skill\ndescription: d\n---\nbody"
    for _ in range(3):
        r = await client.post("/api/skills/adopt", json={"content": content, "prompt": "p"})
        assert r.status_code == 201, r.text
    rows = await _skill_rows(client)
    assert sorted(r["slug"] for r in rows) == ["skill", "skill-2", "skill-3"]
    # Windows 下 read_text() 必须显式 encoding="utf-8"（默认 gbk 会抛）
    from app.skills.library import skills_root
    uid = await _uid(client)
    for slug in ["skill", "skill-2", "skill-3"]:
        md = skills_root(uid) / slug / "SKILL.md"
        assert "name: skill" in md.read_text(encoding="utf-8")


async def test_adopt_oversize_content_is_400(client, fake_llm):
    """边界10：正文超 1MB → 400，且目录零残留。"""
    header = "---\nname: big\ndescription: big skill\n---\n"
    body = "x" * (1024 * 1024 + 1)
    r = await client.post("/api/skills/adopt", json={"content": header + body, "prompt": "p"})
    assert r.status_code == 400, r.text
    assert "1MB" in r.text
    from app.skills.library import skills_root
    uid = await _uid(client)
    root = skills_root(uid)
    assert (not root.exists()) or list(root.glob("*")) == []


async def test_adopt_name_at_exactly_200_chars_is_accepted(client, fake_llm):
    """边界10：name 恰 200 字符 → 201。"""
    content = f"---\nname: {'a' * 200}\ndescription: d\n---\nbody"
    r = await client.post("/api/skills/adopt", json={"content": content, "prompt": "p"})
    assert r.status_code == 201, r.text


async def test_adopt_name_over_200_chars_is_400(client, fake_llm):
    """边界10：name 超 200 → 400，detail 含 '200'，且目录零残留。"""
    content = f"---\nname: {'a' * 201}\ndescription: d\n---\nbody"
    r = await client.post("/api/skills/adopt", json={"content": content, "prompt": "p"})
    assert r.status_code == 400, r.text
    assert "200" in r.text
    from app.skills.library import skills_root
    uid = await _uid(client)
    root = skills_root(uid)
    assert (not root.exists()) or list(root.glob("*")) == []


async def test_adopt_requires_login(anon_client):
    """边界11：全局 guard 兜住。"""
    assert (await anon_client.post("/api/skills/adopt",
                                   json={"content": "x", "prompt": "p"})).status_code == 401


async def test_adopt_cross_account_isolation(two_auth_clients):
    """边界12：A 装的技能 B 看不到，PATCH/DELETE 404。"""
    alice, bob = two_auth_clients
    content = "---\nname: secret-skill\ndescription: d\n---\nbody"
    r = await alice.post("/api/skills/adopt", json={"content": content, "prompt": "p"})
    assert r.status_code == 201
    skill_id = r.json()["id"]
    # B 列表为空
    bob_list = (await bob.get("/api/skills")).json()
    assert bob_list == []
    # B PATCH/DELETE 404
    assert (await bob.patch(f"/api/skills/{skill_id}",
                            json={"enabled": False})).status_code == 404
    assert (await bob.delete(f"/api/skills/{skill_id}")).status_code == 404


async def test_adopt_slug_collision_is_409_with_orphan_cleanup(client, fake_llm, monkeypatch):
    """边界13：撞 (owner, slug) 唯一约束 → 409 且孤儿目录被清。
    monkeypatch next_free_slug 返回固定值（禁用 -N 后缀），
    预插一行幽灵行（skill_dir='' 绕开目录已存在分支）让 INSERT 撞唯一约束。"""
    from app.api import skills as sapi
    from app.skills.library import skills_root
    uid = await _uid(client)
    # 禁用 slug 后缀分配
    monkeypatch.setattr(sapi, "next_free_slug", lambda taken, base: base)
    # 预插幽灵行：skill_dir='' 让 write_skill_dir 的 dest.exists() 为 False
    async with client.app.state.session_factory() as s:
        await s.execute(text(
            "INSERT INTO user_skill (owner_user_id, slug, name, description, "
            "source_type, source_url, subpath, skill_dir) "
            "VALUES (:o, 'meeting-minutes', 'old', 'old', 'url', 'https://x', NULL, '')"),
            {"o": uid})
        await s.commit()
    content = DRAFT_MD  # name=meeting-minutes → slug=meeting-minutes → 撞
    r = await client.post("/api/skills/adopt", json={"content": content, "prompt": "p"})
    assert r.status_code == 409, r.text
    # 孤儿目录必须被清
    root = skills_root(uid)
    assert (not root.exists()) or list(root.glob("*")) == []


async def test_adopt_prompt_stored_raw_and_blank_stored_as_null(client, fake_llm):
    """prompt 存原文；纯空白存 NULL（不是空串）。"""
    gen = await client.post("/api/skills/generate", json={"prompt": "p"})
    content = gen.json()["content"]
    # 原文存储
    r1 = await client.post("/api/skills/adopt", json={"content": content, "prompt": "  周报  "})
    assert r1.status_code == 201
    assert r1.json()["generation_prompt"] == "  周报  "
    # 纯空白 → NULL
    r2 = await client.post("/api/skills/adopt", json={"content": content, "prompt": "   "})
    assert r2.status_code == 201
    assert r2.json()["generation_prompt"] is None
    # 缺字段 → NULL
    r3 = await client.post("/api/skills/adopt", json={"content": content})
    assert r3.status_code == 201
    assert r3.json()["generation_prompt"] is None
