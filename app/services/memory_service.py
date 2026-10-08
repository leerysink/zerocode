"""工作区记忆服务（ticket 002、003 / spec §4、§5）。

接缝清单：`create` / `list_enabled` / `recall_memories` / `workspace_id_of_session`（002），
`update` / `delete`（003，工具与面板共用）。真语义（embedding + 向量索引）留 005，
topK 阈值与 token 封顶、降级收口留 006。

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
# BIGINT 上界：模型幻觉出 22 位 id 时 int() 能过但 asyncpg 会溢出，必须在这层挡住
_BIGINT_MAX = 2 ** 63 - 1

_CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")
_LATIN_WORD = re.compile(r"[a-z0-9_]+")

# 单行回读列：create/update/delete 返回同一集字段（含 created_at/updated_at），
# 面板与工具都拿得到时间戳，不再各自拼 SQL。
_ROW_COLUMNS = ("id, workspace_id, type, title, content, embedding, enabled, "
                "created_at, updated_at")

# 启用记忆的取数列：面板与召回看的是同一批字段，005 换检索实现时只改这一处。
# 自带 ORDER BY：召回同分时要靠它做稳定的“新优先”截断，不能依赖扫描顺序。
_ENABLED_SELECT = (
    "SELECT id, workspace_id, type, title, content, enabled, created_at, updated_at "
    "FROM workspace_memory WHERE workspace_id=:w AND enabled "
    "ORDER BY id DESC"
)


def _check_type(type: str) -> None:
    if type not in MEMORY_TYPES:
        raise DeployError(
            f"未知的记忆类型 {type!r}，可选：{' / '.join(MEMORY_TYPES)}")


def _check_content(content) -> None:
    """空与零分开拒：缺字段/空串与“传了但不是字符串”给不同的文案，模型才能自纠。"""
    if content is None or (isinstance(content, str) and not content.strip()):
        raise DeployError("记忆内容不能为空")
    if not isinstance(content, str):
        raise DeployError(f"记忆内容必须是字符串，收到 {type(content).__name__}")


def _check_title(title) -> None:
    if title is None:
        return
    if not isinstance(title, str):
        raise DeployError(f"记忆标题必须是字符串，收到 {type(title).__name__}")
    if len(title) > TITLE_MAX_LEN:
        raise DeployError(f"记忆标题不能超过 {TITLE_MAX_LEN} 字")


def _as_id(memory_id) -> int:
    """id 允许 int 或纯数字串（大整数一律字符串传输，禁中途 int() 截断）。

    非数字/负数/超 BIGINT 都给业务错误，不能让 asyncpg 的类型或溢出异常冒到工具层
    被包成语义模糊的「工具执行异常」。

    必须同时卡 isascii：`isdigit()` 认上标/全角/阿拉伯-印度数字，`int()` 对前两者要么抛
    ValueError、要么**悄悄换成另一个数**（"１２"→12），后者会改到毫不相干的另一行记忆。
    """
    text_id = str(memory_id).strip()
    if not (text_id.isascii() and text_id.isdigit()):
        raise DeployError(f"非法的记忆 id：{memory_id!r}")
    value = int(text_id)
    if value > _BIGINT_MAX:
        raise DeployError(f"记忆 id 超出整数范围：{memory_id!r}")
    return value


async def create(session_factory, workspace_id: int, type: str, content: str,
                 title: str | None = None, enabled: bool = True) -> dict:
    """落一条记忆，返回整行（含 id，BIGINT）。校验不过即抛 DeployError，绝不落半条。"""
    _check_type(type)
    _check_content(content)
    _check_title(title)
    async with session_factory() as s:
        ws = (await s.execute(text("SELECT 1 FROM workspace WHERE id=:w"),
                              {"w": workspace_id})).scalar()
        if ws is None:
            raise DeployError(f"工作区不存在：{workspace_id}", status=404)
        row = (await s.execute(text(
            "INSERT INTO workspace_memory (workspace_id, type, title, content, enabled) "
            "VALUES (:w, :type, :title, :content, :enabled) "
            "RETURNING " + _ROW_COLUMNS),
            {"w": workspace_id, "type": type, "title": title, "content": content,
             "enabled": enabled})).mappings().first()
        await s.commit()
    return dict(row)


async def update(session_factory, workspace_id: int, memory_id, *, type: str | None = None,
                 content: str | None = None, title: str | None = None,
                 enabled: bool | None = None) -> dict:
    """就地改同一行（只 SET 传进来的字段），返回改后的整行。

    三个硬规定：① id 不存在或不属于本 workspace → 404，绝不顺手新建（故事 7/8）；
    ② 一个字段都没给 → 明确失败，不能只刷个 updated_at 让调用方以为改成了；
    ③ updated_at 必须显式 SET（表上没有触发器）。
    """
    mid = _as_id(memory_id)
    fields: dict[str, object] = {}
    if type is not None:
        _check_type(type)
        fields["type"] = type
    if content is not None:
        _check_content(content)
        fields["content"] = content
    if title is not None:
        _check_title(title)
        fields["title"] = title
    if enabled is not None:
        fields["enabled"] = bool(enabled)
    if not fields:
        raise DeployError("没有需要更新的记忆字段（type / content / title / enabled 至少给一个）")
    # 列名只来自上面四个硬编码分支，不取自模型传入的字段名；值全走绑定参数
    sets = ", ".join(f"{col}=:{col}" for col in fields)
    async with session_factory() as s:
        row = (await s.execute(text(
            f"UPDATE workspace_memory SET {sets}, updated_at=now() "
            "WHERE id=:i AND workspace_id=:w RETURNING " + _ROW_COLUMNS),
            dict(fields, i=mid, w=workspace_id))).mappings().first()
        await s.commit()
    if row is None:
        raise DeployError(f"记忆不存在：{memory_id}", status=404)
    return dict(row)


async def delete(session_factory, workspace_id: int, memory_id, hard: bool = False) -> dict:
    """默认软删（enabled=false，D-5）；`hard=True` 物理删，留给 008 面板的「彻底删除」。

    软删可重复执行且结果一致（幂等）；硬删第二次就是 404，因为行真的没了。
    """
    mid = _as_id(memory_id)
    stmt = ("DELETE FROM workspace_memory WHERE id=:i AND workspace_id=:w RETURNING "
            + _ROW_COLUMNS) if hard else (
        "UPDATE workspace_memory SET enabled=FALSE, updated_at=now() "
        "WHERE id=:i AND workspace_id=:w RETURNING " + _ROW_COLUMNS)
    async with session_factory() as s:
        row = (await s.execute(text(stmt), {"i": mid, "w": workspace_id})).mappings().first()
        await s.commit()
    if row is None:
        raise DeployError(f"记忆不存在：{memory_id}", status=404)
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
