"""技能 AI 生成：一句话描述 → SKILL.md 草稿（spec 2026-10-03-skill-ai-generation §5.3~§5.4）。

只做「描述原文 → 草稿文本」这一层纯逻辑：不碰 DB、不碰磁盘、不留任何状态。
草稿不合格**不抛错**，转成 warnings 交回前端——生成端拒了用户只能再花一次 token 重跑，
而绝大多数不规范（缺 description、整段被围栏包住）用户自己改两笔就能救回来。
上游异常与超时原样上抛，由端点映射 502（可重试语义）。
"""
import asyncio
import re

from app.agent.llm import OpenAICompatLLM
from app.skills.library import SkillError, parse_skill_md

PROMPT_MAX = 4000        # 描述输入上限（spec §5.5：超出 400）
GENERATE_TIMEOUT_S = 60  # 单次生成硬超时；到点抛 asyncio.TimeoutError

# 提示词契约（spec §5.4 六条硬约束，写死为常量，不做配置项）
SKILL_SYSTEM_PROMPT = """你是 Agent Skill 的作者。用户会给你一句话需求描述，你要产出一份 SKILL.md 文件全文。

硬约束：
1. 输出必须是 SKILL.md 文件全文，第一个字符即 `---`；不要任何解释文字、不要用 ``` 围栏包裹。
2. YAML frontmatter 恰好两个字段：`name`、`description`。
3. `name` 用小写英文 kebab-case（示例：`meeting-minutes`），不要用中文。
4. `description` 必须同时写清「这个技能做什么」与「什么时候该用它」——平台 Agent 只靠 name 和
   description 判断要不要展开这个技能，写含糊等于这个技能永远不会被用上。
5. 正文是给平台 Agent 读的指令：直接给步骤、约束与判定条件，不写客套话。
6. 遵循 Anthropic Agent Skills 开放标准。"""

# 最外层一对完整围栏：贪婪匹配保证命中**最后**那个收尾 ```，正文内部的代码块不受影响
_FENCE = re.compile(r"^```[^\n]*\n(.*)\n```\s*$", re.DOTALL)


def unwrap_fences(raw: str) -> str:
    """剥掉模型爱加的 ``` 围栏与 BOM/首尾空白。

    adopt 端点也用它：用户可能把整段带围栏的草稿粘回来，不剥就会把围栏写进母本、
    并让 parse_skill_md 报「缺少 YAML frontmatter」。
    """
    s = (raw or "").lstrip("\ufeff").strip()
    m = _FENCE.match(s)
    return m.group(1).strip() if m else s


def build_llm(model_cfg: dict):
    """按解析出的模型档案建客户端。测试 monkeypatch 本模块的 OpenAICompatLLM。"""
    return OpenAICompatLLM(model_cfg["base_url"], model_cfg["api_key"], model_cfg["model"])


async def _collect(llm, prompt: str) -> str:
    """一次性聚合流式输出：content_delta 拼接 + done.message.content 兜底。

    与 app/api/config_api.py::test_llm、compaction_service 同款姿势，不发明第二种。
    """
    parts: list[str] = []
    final: str | None = None
    async for ev in llm.chat_stream(
            [{"role": "system", "content": SKILL_SYSTEM_PROMPT},
             {"role": "user", "content": prompt}], tools=[]):
        if ev.get("type") == "content_delta":
            parts.append(ev.get("content") or "")
        elif ev.get("type") == "done":
            final = (ev.get("message") or {}).get("content")
    return "".join(parts) or (final or "")


async def generate_skill_draft(llm, prompt: str, *,
                               timeout: float = GENERATE_TIMEOUT_S) -> dict:
    """产出草稿 {name, description, content, warnings}。

    timeout 带默认值只为让边界用例传 0.05 秒配慢 fake，不必真等 60 秒。
    """
    raw = await asyncio.wait_for(_collect(llm, prompt), timeout=timeout)
    content = unwrap_fences(raw)
    warnings: list[str] = []
    name = ""
    description = ""
    try:
        name, description = parse_skill_md(content)
    except SkillError as e:
        # 生成端只警告不拒（spec §5.5）：草稿原样透传，前端标黄列出，用户改完再 adopt
        warnings.append(str(e))
    if name:
        try:
            name.encode("ascii")
        except UnicodeEncodeError:
            # 判据是「非 ASCII」而非「slug 退化成了什么」：比对最终 slug 要再拼一次
            # next_free_slug，把磁盘/DB 状态带进纯逻辑校验（spec §5.5）
            warnings.append("name 含非 ASCII 字符，安装后目录名可能退化成 skill")
    return {"name": name, "description": description,
            "content": content, "warnings": warnings}
