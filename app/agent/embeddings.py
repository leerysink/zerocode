"""embedding provider（ticket 005 / spec §5，D-2 配置落点、D-3 定维）。

只干一件事：把文本换成 1024 维向量；换不出来就说换不出来（返回 None）。
**任何失败都不抛异常** —— 记忆写入与对话召回都不许因为一个可选增强挂掉（故事 5）。

配置唯一来源是 `model_config` 里 `kind='embedding'` 的档案，刻意**不回落到全局 llm.***：
chat 模型产不出向量，回落只会把一次 embedding 变成一次必然 400 的对话请求。
维度与 changelog/V20261008_02 的 `vector(1024)` 绑死：对端返回别的维度一律按「不可用」处理，
绝不把维度不符的向量交给 INSERT —— 那会让整条写入直接失败（定维的代价见那份迁移的注释）。
"""
import logging
import math

from openai import AsyncOpenAI

from app.agent import model_resolver

logger = logging.getLogger(__name__)

# 与 vector(1024) 的列定义成对修改，不是运行期可调参数
EMBEDDING_DIM = 1024
DEFAULT_TIMEOUT = 20.0


def _field(obj, name):
    """SDK 具名属性优先，其次按下标取（手搓的假响应与某些网关都会给 dict）。"""
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _vector_of(raw) -> list[float] | None:
    """校验并归一成 list[float]。维度不符 / 含非有限数 / 不是数列都返回 None。"""
    if not isinstance(raw, (list, tuple)) or len(raw) != EMBEDDING_DIM:
        return None
    try:
        vec = [float(x) for x in raw]
    except (TypeError, ValueError):
        return None
    return vec if all(math.isfinite(v) for v in vec) else None


def vector_literal(vec) -> str:
    """[0.1,0.2] 形式的字面量，供 SQL 侧 CAST(:v AS vector)。逗号分隔不留空格。"""
    return "[" + ",".join(repr(float(v)) for v in vec) + "]"


def _build_client(cfg: dict):
    # api_key 空串按 llm.py 同款口径填 "EMPTY"：OpenAI 客户端不接受空 key
    return AsyncOpenAI(base_url=cfg["base_url"], api_key=cfg["api_key"] or "EMPTY",
                       timeout=DEFAULT_TIMEOUT)


async def embed_texts(cfg: dict | None, texts: list[str], client=None) -> list[list[float]] | None:
    """OpenAI 兼容 POST /embeddings。空输入返回 []（无需调用，也不算失败）。

    未配置返回 None，只记 debug（没配 embedding 是绝大多数部署的常态，刷 warning 只会淹掉真信号）；
    但必须留一行可开的诊断：「根本没传 cfg」与「传了但缺 base_url/model」在现象上一样。
    超时、结构不符、条数不匹配、维度不符一律 warning + None，交调用侧降级。
    """
    if not texts:
        return []
    if not cfg or not cfg.get("base_url") or not cfg.get("model"):
        logger.debug("embedding 未配置齐 base_url/model，本轮不走向量（cfg=%s）",
                     "空" if not cfg else sorted(cfg.keys()))
        return None
    try:
        cli = client if client is not None else _build_client(cfg)
        res = await cli.embeddings.create(model=cfg["model"], input=list(texts))
        data = list(_field(res, "data") or [])
        if len(data) != len(texts):
            logger.warning("embedding 返回条数 %d 与请求条数 %d 不符，本轮降级", len(data), len(texts))
            return None
        # 协议不保证顺序，给了 index 就按 index 归位；缺 index 才信任原顺序
        indexes = [_field(item, "index") for item in data]
        if all(isinstance(i, int) for i in indexes):
            data = [item for _, item in sorted(zip(indexes, data), key=lambda p: p[0])]
        out = []
        for item in data:
            vec = _vector_of(_field(item, "embedding"))
            if vec is None:
                logger.warning("embedding 返回的向量不是 %d 维有效数列，本轮降级", EMBEDDING_DIM)
                return None
            out.append(vec)
        return out
    except Exception as exc:  # noqa: BLE001 - 降级面必须包住 SDK/网络的一切异常
        logger.warning("embedding 调用失败，本轮降级：%s: %s", type(exc).__name__, exc)
        return None


async def embed_for_owner(s, owner_user_id, texts: list[str]) -> list[list[float]] | None:
    """按 owner 取 embedding 档案并调用。无档案 / 调用失败 → None（调用侧走关键词降级）。

    `s` 必须是已打开的会话（与 resolve_* 系列一致）；本函数只多读一行 model_config，
    不自己开会话，免得在写入事务里再套一个连接。

    无档案也记一行 debug：现场区分「压根没建 embedding 档案」与「建了但 owner 对不上」
    只能靠这个（后者的表现同样是“记忆永远没有向量”，但成因完全不同）。
    """
    if not texts:
        return []
    cfg = await model_resolver.resolve_embedding_for_user(s, owner_user_id)
    if cfg is None:
        logger.debug("owner=%s 没有 kind='embedding' 的模型档案，本轮不走向量", owner_user_id)
        return None
    return await embed_texts(cfg, texts)
