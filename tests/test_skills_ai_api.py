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
