"""工作区记忆服务（ticket 002、003、004、005、006 / spec §4、§5）。

接缝清单：`create` / `list_enabled` / `recall_memories` / `workspace_id_of_session`（002），
`update` / `delete`（003，工具与面板共用），`redact_sensitive` / `neutralize_memory_tags` /
`sanitize_memory_text`（004，写入红线），向量写入与向量召回（005，embedding + pgvector），
`memory.*` 配置与召回收口（006：开关短路 + 阈值/topK/token 三重上限 + 无向量行混检补齐）。

隔离维度是 workspace（D-1）：所有读写都必须带 workspace_id，跨工作区不可见（故事 18）。
"""
import dataclasses
import logging
import math
import re
from sqlalchemy import text

from app.agent import embeddings
from app.deploy.errors import DeployError
from app.services import config_service

logger = logging.getLogger(__name__)

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
# 向量列刻意不回读成值而是回读成 has_embedding 布尔位：asyncpg 没有 vector 编解码器，
# embedding 原样取回就是 1024 个浮点数的字符串（实测约 2KB/条），既撑大 tool_result 的
# token，又会顺着 007 的 API 漏进前端；面板只需要知道「这条有没有向量」。
_ROW_COLUMNS = ("id, workspace_id, type, title, content, embedding IS NOT NULL AS has_embedding, "
                "enabled, created_at, updated_at")

# 必须走 CAST 的列：值是 '[...]' 文本字面量，直接绑进 SET 会被当成 text。
_VECTOR_COLUMNS = frozenset({"embedding"})

# ---------------- 召回配置（ticket 006 / spec 故事 14—17）----------------
# 默认值**只存这份代码常量**，不写 app_config 种子：种子一旦落平台级（owner NULL）行，
# conftest 的 TRUNCATE 不清理它，memory.* 的调子就会漏给全平台每个账号（各人 topK 互相影响）。
# 于是「没配」就是走默认，配置表里出现的 memory.* 行必然属于某个账号。
CONFIG_ENABLED = "memory.enabled"
CONFIG_TOP_K = "memory.top_k"
CONFIG_MIN_SIMILARITY = "memory.min_similarity"
CONFIG_MAX_TOKENS = "memory.max_tokens"
# 「不配就不筛」的唯一写法：余弦相似度的值域下界。别用 0.0，理由见下面 _SIMILARITY_RANGE 的注释。
DEFAULT_MIN_SIMILARITY = -1.0

MEMORY_DEFAULTS: dict[str, object] = {
    CONFIG_ENABLED: True,
    CONFIG_TOP_K: DEFAULT_TOP_K,
    CONFIG_MIN_SIMILARITY: DEFAULT_MIN_SIMILARITY,
    CONFIG_MAX_TOKENS: 600,
}

# 合法域：越界一律 warning + 回落默认，不把脏值送进 SQL（负数进 LIMIT 直接是语法/语义错误）。
_TOP_K_RANGE = (1, 100)
# 阈值的合法域是余弦相似度的**完整值域** [-1, 1]，默认 -1.0 = 一条也不因阈值丢掉。
# 这里不能取 0.0 当「不筛」：1 - 余弦距离的值域是 [-1, 1]，反向语义的记忆相似度是负数，
# 0.0 + `>=` 会把这些行静默滤掉，而滤空走的是 `[]` 语义（不降级关键词）——
# 等于「没配任何东西的默认档」也会让部分记忆失联，与故事 5「升级不清零」相反（ticket 006 review）。
_SIMILARITY_RANGE = (DEFAULT_MIN_SIMILARITY, 1.0)
_TOKEN_RANGE = (1, 20000)
# 配置经 HTTP/JSON 传进来常常是字符串表单值，这些拼写按直觉认，认不出才回落默认。
_FALSE_WORDS = frozenset({"false", "off", "0", "no", "none"})
_TRUE_WORDS = frozenset({"true", "on", "1", "yes"})

# 启用记忆的取数列：面板与召回看的是同一批字段，005 换检索实现时只改这一处。
# 自带 ORDER BY：召回同分时要靠它做稳定的“新优先”截断，不能依赖扫描顺序。
# 向量路径在它之上再加 similarity，两条路径返回的 key 集合保持一致。
_ENABLED_SELECT = (
    "SELECT id, workspace_id, type, title, content, enabled, "
    "embedding IS NOT NULL AS has_embedding, created_at, updated_at "
    "FROM workspace_memory WHERE workspace_id=:w AND enabled "
    "ORDER BY id DESC"
)

# 向量召回（005 / 故事 2、3）：CTE 里把字面量换成一次向量，下面 ORDER BY 与投影都能引用，
# 免得同一个 :qv 绑三遍。`<=>` 是余弦距离（0=同向，2=反向），相似度 = 1 - 距离；
# `embedding IS NOT NULL` 必须留着 —— NULL 行参与排序会得到 NULL 距离，顺序不可控。
# 相似度下限由 Python 侧按 policy 过滤（006）：相似度是排序键的单调函数，
# 「先 LIMIT 再过滤」与「先过滤再 LIMIT」取到的行集等价，但少一次查询、阈值口径也不绑在 SQL 里。
_VECTOR_RECALL_HEAD = (
    "WITH q AS (SELECT CAST(:qv AS vector) AS v) "
    "SELECT id, workspace_id, type, title, content, enabled, "
    "embedding IS NOT NULL AS has_embedding, created_at, updated_at, "
    "1 - (embedding <=> q.v) AS similarity "
    "FROM workspace_memory, q "
    "WHERE workspace_id=:w AND enabled AND embedding IS NOT NULL "
)


def _set_clause(fields: dict) -> str:
    """拼 SET 片段：列名只来自调用侧的硬编码分支，值一律走绑定参数。"""
    return ", ".join(
        f"{col}=CAST(:{col} AS vector)" if col in _VECTOR_COLUMNS else f"{col}=:{col}"
        for col in fields)


# ---------------- 写入红线：凭据脱敏 + 结构中和（ticket 004 / spec 故事 11、12）----------------
# 道闸放服务层而不是工具层：面板（008）与工具（003）共用同一对 create/update，前端绕不过去。
# 占位符统一 <redacted:kind>。app/api/models.py 里的 MASK="sk-***" 是「PUT 时保留原密钥」的
# 哨兵，和这里的语义无关，不复用、也不改动。

REDACTED_TEMPLATE = "<redacted:{}>"
# 每条规则的值位都挂这个负向前瞻：同一处凭据只遮一次，二次调用「文本不变 + 计数归零」。
# 工具层与服务层各过一道闸，没有它就等于「已脱敏 N 处」重复计数、占位符套娃。
_NOT_REDACTED = r"(?!<redacted:)"
# 值的右边界：空白、引号、半角结构标点、中日韩标点都算断点。
# 半角括号/@/#/% 留在值里（真密钥常用），全角一律当分隔符 —— 否则中文正文里的
# 「password=hunter2，改成读环境变量」会把后半句一并吃掉，记忆就作废了。
# 代价：紧跟密令后面没有标点时（「password=abc123别写进代码」）会连读一起遮，
# 宁可过度脱敏也不能漏；密令本身含中文时整段被遮仍是安的方向。
_VALUE_CHARS = r"[^\s\"',;\u3000-\u303f\uff00-\uffef\u2010-\u2027\u2030-\u205e]"
_VALUE = _VALUE_CHARS + "+"


def _key_value_pattern(keys: str) -> re.Pattern:
    """拼 key: value / key=value 形态的正则：只吃值、保留键与分隔符，回看时还知道是哪个字段。

    键名右边界用否定前瞻卡住，让 client_secret、access_token 这类复合键在整词起点命中，
    而不是被短键名从中间截走再重复计数。
    """
    return re.compile(
        rf"(?P<keep>(?:{keys})(?![\w\-])\s*[:=]\s*){_NOT_REDACTED}[\"']?{_VALUE}[\"']?",
        re.I)


# 顺序即应用顺序：键值规则必须排在裸 sk- 之前，否则 api_key=sk-... 会被算成两处。
SENSITIVE_RULES: tuple[tuple[str, re.Pattern], ...] = (
    ("private_key", re.compile(
        r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
        re.S)),
    # 连接串里的口令：scheme://user:pass@host，只遮 pass —— 协议/主机/库名都是有用的上下文。
    ("password", re.compile(
        r"(?P<keep>[a-z][a-z0-9+.\-]{1,40}://[^\s:/@]{1,80}:)" + _NOT_REDACTED
        + r"[^\s:/@]{1,200}(?=@)", re.I)),
    ("password", _key_value_pattern(r"passwords?|passwds?|pwd")),
    ("api_key", _key_value_pattern(r"api[\s\-_]?keys?|access[\s\-_]?keys?")),
    ("secret", _key_value_pattern(r"client[\s\-_]?secrets?|secrets?(?:[\s\-_]?keys?)?")),
    ("token", _key_value_pattern(r"auth[\s\-_]?tokens?|access[\s\-_]?tokens?|tokens?")),
    # Bearer 词的凭据同样吃 _VALUE 边界：用裸的「非空白」会把「，后半句说明」整段吞掉。
    # {8,} 是下限：「Bearer 认证」这种讨论凭据形态的句子不该被遮。
    ("token", re.compile(r"(?P<keep>\bbearer\s+)" + _NOT_REDACTED + _VALUE_CHARS + r"{8,}", re.I)),
    ("api_key", re.compile(r"(?<![\w\-])sk[\-_][A-Za-z0-9\-_]{8,}")),
)


def _redact_sub(match: re.Match, kind: str) -> str:
    """有 keep 组的规则保留键名与原分隔符，只把值换成占位符；没有的就整段替换。"""
    keep = match.group("keep") if "keep" in match.re.groupindex else ""
    return f"{keep}{REDACTED_TEMPLATE.format(kind)}"


def redact_sensitive(value: str) -> tuple[str, int]:
    """把正文里的凭据换成 <redacted:kind>，返回（净化后文本, 脱敏处数）。

    命中即替换、原明文不回传也不落库；空串与非字符串原样返回，交给 _check_* 去给业务错误。
    """
    if not isinstance(value, str) or not value:
        return value, 0
    total = 0
    for kind, pat in SENSITIVE_RULES:
        value, hits = pat.subn(lambda m, k=kind: _redact_sub(m, k), value)
        total += hits
    return value, total


# 只中和记忆注入块的标记，不做通用标签清洗：<b>、a < b 这些正文必须原样保留。
_MEMORY_TAG = re.compile(r"</?memories\b[^<>]*>", re.I)


def neutralize_memory_tags(value: str) -> tuple[str, int]:
    """把 </memories> 之类的块标记换成全角，防止一条记忆提前闭合注入块（故事 12 的越狱位）。"""
    if not isinstance(value, str) or not value:
        return value, 0
    return _MEMORY_TAG.subn(
        lambda m: m.group(0).replace("<", "＜").replace(">", "＞"), value)


@dataclasses.dataclass(frozen=True)
class SanitizeOutcome:
    """一次净化的分类结果。两类命中性质不同，计数不能合并成一个数字：

    `secrets` 是被换成占位符的凭据处数，`tags` 是被转成全角的注入块标记处数。
    工具层要分开告诉模型「遮了密码」还是「只改了标签」，混成一个数会让模型
    误判自己写了凭据（正文里只有一个 </memories> 时谎报「已脱敏 1 处凭据」）。
    """
    text: object
    secrets: int
    tags: int


def sanitize_memory_text(value: str) -> SanitizeOutcome:
    """写入前的唯一净化关卡：先遮凭据、再中和块标记，两类命中分开计数。

    脱敏可能把文本变长（占位符比短密码长），调用方必须在净化之后复核长度，不能截断了事。
    非字符串原样透传（text 就是进来的那个值、两个计数都 0），业务错误留给 _check_*。
    """
    if not isinstance(value, str):
        return SanitizeOutcome(text=value, secrets=0, tags=0)
    clean, secrets = redact_sensitive(value)
    clean, tags = neutralize_memory_tags(clean)
    return SanitizeOutcome(text=clean, secrets=secrets, tags=tags)


# ---------------- 向量（ticket 005 / spec 故事 2、5，D-3 定维）----------------
# 写入与召回共用同一个入向量文本口径（标题 + 正文）：两边拼接规则不一致，
# 就变成“按标题算的向量”去配“按正文算的向量”，同一个 owner 自己跟自己有距离。


def _embed_input(title, content) -> str:
    """拼送去 embedding 的文本：只拿非空字符串段，段间一个换行。

    标题缺失就不留前导换行（否则同一段正文因标题有无而向量漂移）；
    非字符串（比如还没过校验的中间态）当作缺失处理，由调用侧判断要不要算。
    """
    parts = [p.strip() for p in (title, content) if isinstance(p, str) and p.strip()]
    return "\n".join(parts)


async def _embedding_literal(s, owner_user_id, title, content) -> str | None:
    """把文本换成入库/查询用的向量字面量；**任何不可用都返回 None，绝不抛**。

    `s` 复用调用侧已打开的会话（不另开连接，免得在写入事务里再套一层）；
    进 embedding 的文本在这里再过一道凭据遮断：写入侧的文本已由 004 净化过，但**召回侧的 query
    是对话原文**，而 kind='embedding' 是用户另配的一个供应商（可以与对话模型不同厂商），
    少一道闸就是新开一条明文外发通道。脱敏对已净化文本幂等，两边口径依旧一致。
    """
    payload = _embed_input(title, content)
    if not payload:
        return None
    payload = redact_sensitive(payload)[0]
    vecs = await embeddings.embed_for_owner(s, owner_user_id, [payload])
    if not vecs:
        return None
    return embeddings.vector_literal(vecs[0])


async def _workspace_owner(s, workspace_id) -> tuple | None:
    """取工作区的 owner 整行；工作区不存在返回 None。

    必须用 `.first()` 取行而不是 `.scalar()`：后者的 None 同时代指「无行」与
    「行存在但 owner 为 NULL」，前者要 404，后者照常写（无主遗留档案也是合法配置）。
    """
    return (await s.execute(text("SELECT owner_user_id FROM workspace WHERE id=:w"),
                            {"w": workspace_id})).first()


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
    # 净化放在入库之前：库里、日志里、事件流里都不留原始明文（票面红线）。
    content = sanitize_memory_text(content).text
    # title 同过一道闸：注入行当下只拼 content，但 title 同样是不可信文本，
    # 面板（008）与后续可能上块的标题都会直接用；字段间信任级别不能不一致。
    # 且必须在净化后再复核长度：占位符可能比原值长，静默截断会顶破 VARCHAR(200)。
    title = sanitize_memory_text(title).text
    _check_title(title)
    async with session_factory() as s:
        ws_row = await _workspace_owner(s, workspace_id)
        if ws_row is None:
            raise DeployError(f"工作区不存在：{workspace_id}", status=404)
        # 向量在净化之后、落库之前算（005）。算不出就是 NULL，行照常进：
        # embedding 是可选增强，不能让它把记忆写入带倒（故事 5）。
        # provider 层的维度校验把字面量锁在 EMBEDDING_DIM，不会把异维向量送到定维列上。
        embedding = await _embedding_literal(s, ws_row[0], title, content)
        row = (await s.execute(text(
            "INSERT INTO workspace_memory (workspace_id, type, title, content, embedding, enabled) "
            "VALUES (:w, :type, :title, :content, CAST(:embedding AS vector), :enabled) "
            "RETURNING " + _ROW_COLUMNS),
            {"w": workspace_id, "type": type, "title": title, "content": content,
             "embedding": embedding, "enabled": enabled})).mappings().first()
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
        content = sanitize_memory_text(content).text
        fields["content"] = content
    if title is not None:
        title = sanitize_memory_text(title).text
        _check_title(title)
        fields["title"] = title
    if enabled is not None:
        fields["enabled"] = bool(enabled)
    if not fields:
        raise DeployError("没有需要更新的记忆字段（type / content / title / enabled 至少给一个）")
    async with session_factory() as s:
        # 先读旧行：算向量要拿「改完之后的完整文本」（只改 title 就得拿旧正文一起算），
        # 404 也只能在这里判：UPDATE…RETURNING 读不到旧值。
        cur = (await s.execute(text(
            "SELECT m.title, m.content, w.owner_user_id FROM workspace_memory m "
            "JOIN workspace w ON w.id = m.workspace_id "
            "WHERE m.id=:i AND m.workspace_id=:w"),
            {"i": mid, "w": workspace_id})).first()
        if cur is None:
            raise DeployError(f"记忆不存在：{memory_id}", status=404)
        if "content" in fields or "title" in fields:
            # 只改 type/enabled 时不重算：文本没动，既省一次调用也不会把可用向量刷成 NULL。
            # 算不出来就写 NULL：陈旧向量比没向量危险——它会按旧文本把这条召回出来。
            fields["embedding"] = await _embedding_literal(
                s, cur[2],
                fields["title"] if "title" in fields else cur[0],
                fields["content"] if "content" in fields else cur[1])
        # 列名只来自上面几个硬编码分支，不取自模型传入的字段名；值全走绑定参数
        row = (await s.execute(text(
            f"UPDATE workspace_memory SET {_set_clause(fields)}, updated_at=now() "
            "WHERE id=:i AND workspace_id=:w RETURNING " + _ROW_COLUMNS),
            dict(fields, i=mid, w=workspace_id))).mappings().first()
        await s.commit()
    # 旧行读到过不等于本行还在：并发硬删/工作区级联删除会让 RETURNING 空行，
    # 少了这道判空就是 dict(None) 的 TypeError，到工具层只糊得出「工具执行异常」。
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


@dataclasses.dataclass(frozen=True)
class RecallPolicy:
    """一次召回的收口参数。字段语义与 `memory.*` 配置键一一对应。

    `top_k` 是 `int | None`：None = 不限制。这是两个口径归一后的结果：
    配置里的 0/负数是非法值（回落默认），而调用方显式传的 `top_k<=0` 是 002/005 定下的
    「不限制」约定（工具层拿它拉全量查重）。两套语义不能合并成一个默认值。
    """
    enabled: bool
    top_k: int | None
    min_similarity: float
    max_tokens: int


def _parse_number(raw) -> float | None:
    """配置值 → 有限浮点；认不了返回 None。bool 先拒：`True == 1` 会把开关当成合法的 top_k=1。"""
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        num = float(raw)
    elif isinstance(raw, str):
        try:
            num = float(raw.strip())
        except ValueError:
            return None
    else:
        return None
    # nan / inf 走 isfinite 挡住：`inf <= 1.0` 为假所以下一步会回落，但 nan 两边比较都是假，
    # 不拦就会把阈值算成「既不合法也不非法」的第三种东西。
    return num if math.isfinite(num) else None


def _coerce_bool(raw, default: bool) -> bool:
    """开关认常见拼写（含字符串表单值）；**认不出的一律回落默认**。

    不能「非空即真」：`"false"` 进 `bool()` 就是真，得到一个关不掉的开关。
    也不能认不出就当关：误关是静默丢功能，误开最多是多注入几条。
    """
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        low = raw.strip().lower()
        if low in _FALSE_WORDS:
            return False
        if low in _TRUE_WORDS:
            return True
        return default
    if raw is None or isinstance(raw, (list, dict, tuple, set)):
        return default
    num = _parse_number(raw)
    return default if num is None else num != 0


def _coerce_int(raw, bounds: tuple[int, int], default: int, key: str) -> int:
    """整数档：非整数/越界都回落默认。2.5 不 `int()` 成 2（会默默少投一条），3.0 当整数认。"""
    num = _parse_number(raw)
    if num is None or not num.is_integer():
        return default
    value = int(num)
    if not bounds[0] <= value <= bounds[1]:
        logger.warning("配置 %s=%r 超出 [%s, %s]，回落默认 %s",
                       key, raw, bounds[0], bounds[1], default)
        return default
    return value


def _coerce_float(raw, bounds: tuple[float, float], default: float, key: str) -> float:
    num = _parse_number(raw)
    if num is None:
        return default
    if not bounds[0] <= num <= bounds[1]:
        logger.warning("配置 %s=%r 超出 [%s, %s]，回落默认 %s",
                       key, raw, bounds[0], bounds[1], default)
        return default
    return num


def _resolve_policy(cfg: dict | None, top_k: int | None = None) -> RecallPolicy:
    """把「原始配置字典 + 调用方显式 top_k」算成一份可用的策略：所有回落规则只在这一个函数里。

    cfg 缺键 ≠ 关掉某项能力：只有显式的 `memory.enabled=false` 会短路。脏值一律回落默认，
    绝不拿脏值去拼 SQL。
    """
    conf = cfg or {}
    if top_k is None:
        limit: int | None = _coerce_int(conf.get(CONFIG_TOP_K), _TOP_K_RANGE,
                                        int(MEMORY_DEFAULTS[CONFIG_TOP_K]), CONFIG_TOP_K)
    else:
        # 显式入参优先于配置；<=0 或认不出的值视为「不限制」（与配置里的 0 判非法刻意不同）
        num = _parse_number(top_k)
        limit = None if num is None or num <= 0 else int(num)
    return RecallPolicy(
        enabled=_coerce_bool(conf.get(CONFIG_ENABLED), bool(MEMORY_DEFAULTS[CONFIG_ENABLED])),
        top_k=limit,
        min_similarity=_coerce_float(conf.get(CONFIG_MIN_SIMILARITY), _SIMILARITY_RANGE,
                                     float(MEMORY_DEFAULTS[CONFIG_MIN_SIMILARITY]),
                                     CONFIG_MIN_SIMILARITY),
        max_tokens=_coerce_int(conf.get(CONFIG_MAX_TOKENS), _TOKEN_RANGE,
                               int(MEMORY_DEFAULTS[CONFIG_MAX_TOKENS]), CONFIG_MAX_TOKENS),
    )


async def _config_for(session_factory, workspace_id: int) -> dict:
    """cfg 没给出时的**唯一**取配置接缝：workspace → owner_user_id → config_service.get_all。

    工作区不存在 / owner 为 NULL 都不抛：get_all 退化成平台级视图，memory.* 走代码默认。
    召回在对话主链路上，任何配置环节都不能把回合打断。
    """
    async with session_factory() as s:
        ws_row = await _workspace_owner(s, workspace_id)
        owner = ws_row[0] if ws_row is not None else None
        return await config_service.get_all(s, owner_user_id=owner)


def format_memory_line(row: dict) -> str:
    """注入行与 token 预算的**共用**格式化：`- [type] 折成单行的 content`。

    两边必须同源：loop 另拼一份，则「按 600 估的预算」与「实际注入的文本」就不是同一个东西，
    封顶会虚高。正文里的换行/制表符折成单空格，否则一条多行记忆会破「一行一条」的块结构。
    """
    one_line = " ".join(str(row["content"] or "").split())
    return f"- [{row['type']}] {one_line}"


# 汉字/假名/中文标点/全角形式都按 1 字 = 1 token 估（BPE 下常为 1—2 字/token，往多里估是安全侧），
# 其余字符 4 个算 1 个。不引 tiktoken：那是几十 MB 词表 + 下载，而这里要的是
# 「别把 prompt 撑爆」的保守上限，不是精确计费。
_CJK_CHAR = re.compile(r"[\u3000-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff00-\uffff]")


def _estimate_tokens(value) -> int:
    """粗估一段注入文本的 token；空串 / None / 非字符串都是 0（不抛，预算不卡写入）。"""
    if not isinstance(value, str) or not value:
        return 0
    cjk = len(_CJK_CHAR.findall(value))
    return cjk + math.ceil((len(value) - cjk) / 4)


def _truncate(rows: list[dict], limit: int | None) -> list[dict]:
    """topK 截断；limit 为 None 表示不限制（行序已由召回路径定好，这里只尾部切）。"""
    return rows if not limit or limit <= 0 else rows[:limit]


def _cap_by_tokens(rows: list[dict], budget: int) -> list[dict]:
    """按预算逐条累计，触顶即停：**整条丢弃，绝不切半条**（半条记录会把模型导向错的结论）。

    是「停」不是「接着找下一条更短的」：预算是注入块的整体上限，跳着塞会让注入顺序与
    相关度不一致，也说不清后面那条为何忽然出现。
    """
    out: list[dict] = []
    used = 0
    for row in rows:
        cost = _estimate_tokens(format_memory_line(row))
        if used + cost > budget:
            break
        used += cost
        out.append(row)
    return out


def dedupe_policy(top_k: int | None = None) -> RecallPolicy:
    """查重/列表类调用专用的策略：**不吃用户注入配置，也不查库**。

    不能跟注入路径共用 _resolve_policy，否则两个与直觉相反的行为会直接溢到用户面前：
    1. `memory.enabled=false` 只应该关掉「往 prompt 里注入」，不该关掉 memory_write 的
       查重建议（故事 10 的防堆积提示）——否则关掉注入后同一个约定会反复入库；
    2. 用户把 min_similarity 调高、max_tokens 压小是为了少注入，不该同时削弱查重名额。
    所以查重固定为：启用 + 不因阈值丢弃 + 不做注入预算封顶，只保留调用方要的 topK。
    """
    return RecallPolicy(enabled=True, top_k=top_k if top_k and top_k > 0 else None,
                        min_similarity=DEFAULT_MIN_SIMILARITY,
                        max_tokens=_TOKEN_RANGE[1])


async def recall_memories(session_factory, workspace_id: int, query: str | None,
                          top_k: int | None = None,
                          cfg: dict | None = None,
                          policy: RecallPolicy | None = None) -> list[dict]:
    """召回收口（006）：开关短路 → 向量路 + 无向量行补齐 → topK 截断 → token 封顶。

    三重上限只在这一个接缝里生效，降级路也不例外：侧门不收口等于没收口。
    顺序是「先 topK 再预算」：预算不能把 topK 的名额又放大（故事 15 的“上限”语义）。

    `cfg` 由调用方传入时不再查库（loop 已解析过 owner 视角的配置，白的一次往返）；
    `cfg=None` 走 `_config_for` 的唯一接缝。显式 `top_k<=0` = 不限制（002/005 旧约定）。
    `policy` 给出时**直接用它**，不读 `memory.*` 也不查库：查重等非注入路径用它与用户
    配置解耦（见 `dedupe_policy`），但行集/截断仍走同一套代码，不留第二套平行实现。
    任何环节都返回**列表**，绝不抛：召回不许打断对话（故事 14）。
    """
    if policy is None:
        policy = _resolve_policy(
            cfg if cfg is not None else await _config_for(session_factory, workspace_id), top_k)
    # 总开关关掉：一条 SQL 也不为召回发出去（而不是“查出来再丢弃”）
    if not policy.enabled:
        return []
    semantic = await _recall_by_vector(session_factory, workspace_id, query, policy)
    if semantic is None:
        # 这轮根本用不上向量：全量走关键词（包括有向量的行），升级前的行为不比现在差
        rows = await _recall_by_keyword(session_factory, workspace_id, query)
    else:
        # 混检：向量路拿相似度，关键词只负责补「没向量」的老条，并按 id 字符串去重。
        # 刻意把补齐行排在向量行之后、共用同一个 topK 名额（006 review 显式登记的取舍）：
        # 向量命中按相似度降序，相关度高于关键词补齐，因此向量路填满名额时本轮就没有补齐位。
        # 故事 17 只保证「老条仍参与召回、不因没向量而永久失联」，不保证它一定挤进 topK；
        # 要给老条保底名额就得把 topK 拆开，那等于抬高注入总量，与故事 15 的「上限」语义冲突。
        rows = semantic + await _recall_by_keyword(
            session_factory, workspace_id, query,
            only_without_vector=True, exclude={str(r["id"]) for r in semantic})
    return _cap_by_tokens(_truncate(rows, policy.top_k), policy.max_tokens)


async def _recall_by_vector(session_factory, workspace_id: int, query: str | None,
                            policy: RecallPolicy) -> list[dict] | None:
    """余弦距离升序取 topK，相似度阈值在 Python 侧过滤。

    **返回 None 与返回 [] 是两种相反语义，混成一个就破功**：
    - None = 「这轮用不上向量」（查询切不出文本、工作区不存在、provider 不可用、
      本工作区一条可用向量都没有、向量 SQL 自身出错），调用侧必须降级关键词，
      否则升级就等于把老库的召回整体清零（故事 5 的验收点）；
    - [] = 有向量行但全被 min_similarity 滤掉，**不许**降级让关键词把弱相关请回来（故事 16）。

    反正余弦排序下只要有可用向量就必然出行，空行集只可能是「没有可用向量」。
    向量 SQL 是 005 新增的执行面（`CAST(:qv AS vector)` / `<=>`），它自身异常同样不得打断对话：
    一律降级返回 None 让关键词接管，否则关键词路径根本没被执行到。
    """
    if not _embed_input(None, query):
        return None
    async with session_factory() as s:
        ws_row = await _workspace_owner(s, workspace_id)
        if ws_row is None:
            return None
        qvec = await _embedding_literal(s, ws_row[0], None, query)
        if qvec is None:
            return None
        sql = _VECTOR_RECALL_HEAD + "ORDER BY embedding <=> q.v"
        params: dict[str, object] = {"qv": qvec, "w": workspace_id}
        if policy.top_k and policy.top_k > 0:
            sql += " LIMIT :lim"
            params["lim"] = policy.top_k
        try:
            rows = (await s.execute(text(sql), params)).mappings().all()
        except Exception:  # noqa: BLE001 - 向量是新开的执行面，连 DB 异常也得留给关键词
            logger.warning("向量召回执行失败，本轮降级关键词", exc_info=True)
            return None
    if not rows:
        return None
    # `>=` 不是 `>`：恰等于阈值的条要留下（阈值就是「包含」语义的下界）
    return [dict(r) for r in rows if r["similarity"] >= policy.min_similarity]


async def _recall_by_keyword(session_factory, workspace_id: int, query: str | None,
                             limit: int | None = None, *, only_without_vector: bool = False,
                             exclude: set[str] | None = None) -> list[dict]:
    """002 的关键词召回：降级主路，兼当「无向量老条」的混检补齐入口。

    `only_without_vector` 是补齐的关键闸门：有向量的行归 min_similarity 管，关键词不许
    越过阈值把它们捣回来（故事 16 的侧门）；没向量的行本来就没有相似度可谈，只能靠这里。
    去重按**字符串**比 id：BIGINT 过 `Number()` 就撞成同一个数（全局红线）。
    命中数降序 + SQL 的 id DESC 稳定截断；`limit` 留 None 时由调用侧统一按 topK 截。
    """
    keywords = _keywords(query)
    if not keywords:
        return []
    async with session_factory() as s:
        rows = (await s.execute(text(_ENABLED_SELECT), {"w": workspace_id})).mappings().all()
    hits = [(score, r) for r in rows
            if (score := _score(r, keywords)) > 0
            and (not only_without_vector or not r["has_embedding"])
            and (exclude is None or str(r["id"]) not in exclude)]
    # 稳定排序：只按命中数降序；同分的相对顺序由 SQL 的 id DESC 保证（新记忆优先）
    hits.sort(key=lambda x: -x[0])
    picked = [dict(r) for _score, r in hits]
    return picked[:limit] if limit and limit > 0 else picked


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
