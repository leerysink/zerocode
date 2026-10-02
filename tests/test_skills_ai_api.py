# tests/test_skills_ai_api.py
"""技能 AI 生成通道的 HTTP 面（spec docs/specs/2026-10-03-skill-ai-generation.md §7）。

接缝＝HTTP API：generate 零副作用、adopt 硬校验落盘落库、错误分区码表、13 条边界。
LLM 用 monkeypatch 换掉 app.skills.generator.OpenAICompatLLM（对齐 tests/test_config.py
既有惯例，不新增 app.state 字段）。
"""
import pytest
from pathlib import Path

from sqlalchemy import text

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _skills_data_dir(tmp_path, monkeypatch):
    """技能母本落 tmp，绝不写仓库 data/。data_dir() 每次现读 env，故作用域内生效。"""
    monkeypatch.setenv("ZEROCODE_DATA_DIR", str(tmp_path / "data"))


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
