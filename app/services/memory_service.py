"""工作区记忆服务（ticket 002 / spec §4、§5）。

本版本 = 最小闭环：存 / 列 / 关键词召回。真语义（embedding + 向量索引）留 005，
topK 阈值与 token 封顶、降级收口留 006；此处只把**接缝**钉稳：
`create` / `list_enabled` / `recall_memories` / `workspace_id_of_session`。

隔离维度是 workspace（D-1）：所有读写都必须带 workspace_id，跨工作区不可见（故事 18）。
"""
import re
from sqlalchemy import text

from app.deploy.errors import DeployError

# 记忆分类（spec §4）：偏好 / 决策 / 事实 / 踩坑。新增分类改这里 + 迁移注释即可。
MEMORY_TYPES = ("preference", "decision", "fact", "pitfall")
# 注入 prompt 的默认条数上限；top_k <= 0 表示不限制（与 agent.* 参数的 <=0 不限制约定一致）
DEFAULT_TOP_K = 5
TITLE_MAX_LEN = 200

_CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")
_LATIN_WORD = re.compile(r"[a-z0-9_]+")

# 启用记忆的取数列：面板与召回看的是同一批字段，005 换检索实现时只改这一处。
# 自带 ORDER BY：召回同分时要靠它做稳定的“新优先”截断，不能依赖扫描顺序。
_ENABLED_SELECT = (
    "SELECT id, workspace_id, type, title, content, enabled, created_at, updated_at "
    "FROM workspace_memory WHERE workspace_id=:w AND enabled "
    "ORDER BY id DESC"
)


async def create(session_factory, workspace_id: int, type: str, content: str,
                 title: str | None = None, enabled: bool = True) -> dict:
    """落一条记忆，返回整行（含 id，BIGINT）。校验不过即抛 DeployError，绝不落半条。"""
    if type not in MEMORY_TYPES:
        raise DeployError(
            f"未知的记忆类型 {type!r}，可选：{' / '.join(MEMORY_TYPES)}")
    if content is None or not content.strip():
        raise DeployError("记忆内容不能为空")
    if title is not None and len(title) > TITLE_MAX_LEN:
        raise DeployError(f"记忆标题不能超过 {TITLE_MAX_LEN} 字")
    async with session_factory() as s:
        ws = (await s.execute(text("SELECT 1 FROM workspace WHERE id=:w"),
                              {"w": workspace_id})).scalar()
        if ws is None:
            raise DeployError(f"工作区不存在：{workspace_id}", status=404)
        row = (await s.execute(text(
            "INSERT INTO workspace_memory (workspace_id, type, title, content, enabled) "
            "VALUES (:w, :type, :title, :content, :enabled) "
            "RETURNING id, workspace_id, type, title, content, embedding, enabled, "
            "created_at, updated_at"),
            {"w": workspace_id, "type": type, "title": title, "content": content,
             "enabled": enabled})).mappings().first()
        await s.commit()
    return dict(row)


async def list_enabled(session_factory, workspace_id: int) -> list[dict]:
    """面板/查重用的启用清单（新→旧）。停用的记忆不进这里，也不进召回（故事 22）。"""
    async with session_factory() as s:
        rows = (await s.execute(text(_ENABLED_SELECT), {"w": workspace_id})).mappings().all()
    return [dict(r) for r in rows]


async def recall_memories(session_factory, workspace_id: int, query: str | None,
                          top_k: int = DEFAULT_TOP_K) -> list[dict]:
    """按工作区取启用记忆，用关键词命中数排序后截断 topK。

    空查询 / 切不出词的查询 / 无命中都返回空列表 —— 调用方据此不注空块（故事 14）。
    """
    keywords = _keywords(query)
    if not keywords:
        return []
    async with session_factory() as s:
        rows = (await s.execute(text(_ENABLED_SELECT), {"w": workspace_id})).mappings().all()
    hits = [(score, r) for r in rows if (score := _score(r, keywords)) > 0]
    # 稳定排序：只按命中数降序；同分的相对顺序由 SQL 的 id DESC 保证（新记忆优先）
    hits.sort(key=lambda x: -x[0])
    picked = [dict(r) for _score, r in hits]
    return picked[:top_k] if top_k > 0 else picked


async def workspace_id_of_session(session_factory, session_id: int) -> int | None:
    """session → workspace 反查（沿用 loop 里 chat_session→workspace 的同款 join 语义）。

    会话不存在或已被级联删除返回 None（调用方据此跳过召回，不抛错打断对话）。
    """
    async with session_factory() as s:
        return (await s.execute(text(
            "SELECT workspace_id FROM chat_session WHERE id=:i"), {"i": session_id})).scalar()


def _keywords(query: str | None) -> list[str]:
    """查询切词：拉丁词按 [a-z0-9_] 取，中文按二元滑窗（无空格分词的中文必须也能命中）。

    标点/全角符号/空白全部丢弃；结果按首次出现去重，保证命中数不被同一个词重复抬高。
    """
    low = (query or "").lower()
    grams: list[str] = []
    for run in _CJK_RUN.findall(low):
        if len(run) == 1:
            grams.append(run)
        else:
            grams.extend(run[i:i + 2] for i in range(len(run) - 1))
    toks = _LATIN_WORD.findall(low) + grams
    return list(dict.fromkeys(toks))


def _score(row, keywords: list[str]) -> int:
    """命中数 = 有多少个关键词出现在标题或正文里（子串匹配，大小写不敏感）。"""
    hay = ((row["content"] or "") + " " + (row["title"] or "")).lower()
    return sum(1 for k in keywords if k in hay)
