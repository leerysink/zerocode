"""embedding provider（ticket 005 / spec 故事 2、5，D-3 定维）。

主靶心 = **降级面**：这个模块唯一的承诺是"换不出来就说换不出来"，
所以用例集中在各种畸形返回都必须安静地给 None，而不是抛异常或给半截向量。
一律用假 client，绝不打真实付费接口（红线：测试不联网、不产生账单）。
"""
from types import SimpleNamespace

from sqlalchemy import text
from app.agent import embeddings as emb
from app.db import make_session_factory

DIM = emb.EMBEDDING_DIM
CFG = {"base_url": "http://e/v1", "api_key": "ek", "model": "e-model"}


def _vec(one_hot_at: int) -> list[float]:
    """one-hot 假向量：真接口返回 1024 维，这里也必须凑满，否则测的是维度校验而不是正常路径。"""
    return [1.0 if i == one_hot_at else 0.0 for i in range(DIM)]


class FakeEmbeddingsApi:
    def __init__(self, result=None, exc=None):
        self._result = result
        self._exc = exc
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._exc is not None:
            raise self._exc
        return self._result


def _client(result=None, exc=None) -> tuple[object, FakeEmbeddingsApi]:
    api = FakeEmbeddingsApi(result=result, exc=exc)
    return SimpleNamespace(embeddings=api), api


def _resp(*vectors, indexes=None, as_dict=False):
    """造返回体：as_dict 给「网关直接吐 JSON」的形态，否则给 SDK 的具名对象。

    indexes=[None, ...] 表示这条不带 index 字段（真实响应确实有这种，不能假设有）。"""
    items = []
    for n, v in enumerate(vectors):
        index = n if indexes is None else indexes[n]
        if as_dict:
            payload = {"embedding": v}
            if index is not None:
                payload["index"] = index
        else:
            payload = (SimpleNamespace(embedding=v, index=index) if index is not None
                       else SimpleNamespace(embedding=v))
        items.append(payload)
    return {"data": items} if as_dict else SimpleNamespace(data=items)


# ---------------- 正常路径 ----------------

async def test_embed_texts_returns_vectors_in_order():
    cli, api = _client(result=_resp(_vec(0), _vec(1)))
    out = await emb.embed_texts(CFG, ["甲", "乙"], client=cli)
    assert out is not None and len(out) == 2
    assert out[0] == _vec(0) and out[1] == _vec(1)
    assert api.calls[0]["input"] == ["甲", "乙"]
    assert api.calls[0]["model"] == "e-model"


async def test_embed_texts_accepts_dictShapedResponse():
    """某些网关直接吐 dict（SDK 未解析）：属性与下标两种取法都要认。"""
    cli, _ = _client(result=_resp(_vec(3), as_dict=True))
    out = await emb.embed_texts(CFG, ["x"], client=cli)
    assert out == [_vec(3)]


async def test_embed_texts_reorders_by_index_when_provider_returns_shuffled():
    """协议不保证顺序：给了 index 就必须按 index 归位，否则向量与文本错位（静默的脏数据）。"""
    cli, _ = _client(result=_resp(_vec(1), _vec(0), indexes=[1, 0]))
    out = await emb.embed_texts(CFG, ["第一条", "第二条"], client=cli)
    assert out == [_vec(0), _vec(1)]


async def test_embed_texts_trustsOriginalOrderWhenIndexMissing():
    """缺 index 时退回原顺序（而不是按 None 排序，那会把两条结果随机打乱）。"""
    cli, _ = _client(result=_resp(_vec(0), _vec(1), indexes=[None, None]))
    out = await emb.embed_texts(CFG, ["a", "b"], client=cli)
    assert out == [_vec(0), _vec(1)]


async def test_embed_for_owner_usesDefaultEmbeddingProfile(test_engine, monkeypatch):
    """embed_for_owner 必须走 kind='embedding' 的那一行，而不是随便一条档案。"""
    sf = make_session_factory(test_engine)
    captured: list[dict] = []

    async def fake_texts(cfg, texts, client=None):
        captured.append({"cfg": cfg, "texts": texts})
        return [_vec(0) for _ in texts]

    monkeypatch.setattr(emb, "embed_texts", fake_texts)
    async with sf() as s:
        await s.execute(text(
            "INSERT INTO model_config (name, base_url, api_key, model, kind, is_default) "
            "VALUES ('chat', 'http://c/v1', 'ck', 'c-model', 'chat', TRUE), "
            "('vec', 'http://e/v1', 'vk', 'e-vec', 'embedding', FALSE)"))
        await s.commit()
        out = await emb.embed_for_owner(s, None, ["正文"])
    assert out == [_vec(0)]
    assert captured[0]["cfg"]["model"] == "e-vec"
    assert captured[0]["cfg"]["base_url"] == "http://e/v1"
    assert captured[0]["texts"] == ["正文"]


async def test_vectorLiteral_shape():
    literal = emb.vector_literal([0.1, -2.5, 3, 0])
    assert literal == "[0.1,-2.5,3.0,0.0]"
    assert " " not in literal


async def test_vectorLiteral_roundTripsFullDimension():
    literal = emb.vector_literal(_vec(7))
    assert literal.startswith("[") and literal.endswith("]")
    parts = literal[1:-1].split(",")
    assert len(parts) == DIM and float(parts[7]) == 1.0


# ---------------- 边界与降级 ----------------

async def test_embed_texts_withEmptyTextsSkipsProviderCall():
    """空输入不是失败：返回 [] 且一次都不调用（省一次必然无意义的请求）。"""
    cli, api = _client(exc=AssertionError("不该被调用"))
    assert await emb.embed_texts(CFG, [], client=cli) == []
    assert api.calls == []


async def test_embed_texts_withNoneConfigYieldsNone():
    cli, api = _client(exc=AssertionError("不该被调用"))
    assert await emb.embed_texts(None, ["x"], client=cli) is None
    assert api.calls == []


async def test_embed_texts_withoutBaseUrlYieldsNoneWithoutNetwork(monkeypatch):
    """未配置就绝不建连接：这里把 _build_client 换成会炸的实现，返回 None 才说明它没被调用。"""
    def boom(cfg):
        raise AssertionError("未配置时不得构造客户端")

    monkeypatch.setattr(emb, "_build_client", boom)
    assert await emb.embed_texts({"base_url": "", "model": "m"}, ["x"]) is None
    assert await emb.embed_texts({"base_url": "http://e/v1", "model": ""}, ["x"]) is None


async def test_embed_texts_withMissingModelYieldsNone():
    cli, api = _client(exc=AssertionError("不该被调用"))
    assert await emb.embed_texts({"base_url": "http://e/v1"}, ["x"], client=cli) is None
    assert api.calls == []


async def test_embed_texts_swallowsProviderException():
    """超时/401/连接拒绝都必须变成 None：记忆是可选增强，不能把它带进对话主链路。"""
    for exc in (TimeoutError("slow"), RuntimeError("401 unauthorized"),
                ValueError("bad gateway payload")):
        cli, _ = _client(exc=exc)
        assert await emb.embed_texts(CFG, ["x"], client=cli) is None


async def test_embed_texts_withCountMismatchYieldsNone():
    """请求 2 条只回 1 条：宁可整轮降级，也不能把 1 条向量按顺序配给 2 条文本。"""
    cli, _ = _client(result=_resp(_vec(0)))
    assert await emb.embed_texts(CFG, ["a", "b"], client=cli) is None


async def test_embed_texts_withEmptyDataYieldsNone():
    cli, _ = _client(result=SimpleNamespace(data=[]))
    assert await emb.embed_texts(CFG, ["a"], client=cli) is None


async def test_embed_texts_withMissingDataKeyYieldsNone():
    """网关返回 {"error": ...} 这种非预期结构：_field 拿不到 data 就是 0 条 → 降级，不许抛。"""
    cli, _ = _client(result=SimpleNamespace())
    assert await emb.embed_texts(CFG, ["a"], client=cli) is None


async def test_embed_texts_withWrongDimensionYieldsNone():
    """定维列的代价：维度不符若放行，INSERT 会在 PG 服务端炸掉整条写入。"""
    for wrong in ([0.1] * 3, [0.1] * 1536, []):
        cli, _ = _client(result=_resp(wrong))
        assert await emb.embed_texts(CFG, ["a"], client=cli) is None


async def test_embed_texts_withNonNumericVectorYieldsNone():
    cli, _ = _client(result=_resp(["x"] * DIM))
    assert await emb.embed_texts(CFG, ["a"], client=cli) is None


async def test_embed_texts_withNonFiniteValuesYieldsNone():
    """NaN/inf 能过 float()，但会让 pgvector 的余弦运算直接报错，必须在 provider 层挡住。"""
    for bad in (float("nan"), float("inf"), float("-inf")):
        vec = [0.0] * DIM
        vec[5] = bad
        cli, _ = _client(result=_resp(vec))
        assert await emb.embed_texts(CFG, ["a"], client=cli) is None


async def test_embed_texts_withMissingEmbeddingKeyYieldsNone():
    cli, _ = _client(result=SimpleNamespace(data=[SimpleNamespace(index=0)]))
    assert await emb.embed_texts(CFG, ["a"], client=cli) is None


async def test_embed_texts_partiallyBadVectorYieldsWholeRoundNone():
    """一批里有一条坏的就整轮作废：半批向量入库会让同一工作区的召回口径分裂。"""
    cli, _ = _client(result=_resp(_vec(0), [0.1] * 3))
    assert await emb.embed_texts(CFG, ["a", "b"], client=cli) is None


async def test_embed_for_owner_withoutEmbeddingProfileYieldsNone(test_engine):
    """不回落全局 llm.*：chat 模型产不出向量，回落等于发一次必然 400 的请求。"""
    sf = make_session_factory(test_engine)
    async with sf() as s:
        assert await emb.embed_for_owner(s, None, ["x"]) is None


async def test_embed_for_owner_withEmptyTextsYieldsEmptyList(test_engine):
    sf = make_session_factory(test_engine)
    async with sf() as s:
        assert await emb.embed_for_owner(s, None, []) == []


async def test_build_client_withEmptyApiKeyUsesEmptySentinel():
    """OpenAI 客户端拒绝空 key；本地 embedding 服务（如 Ollama）恰恰不给 key。"""
    assert emb._build_client({"base_url": "http://e/v1", "api_key": ""}).api_key == "EMPTY"
    assert emb._build_client({"base_url": "http://e/v1", "api_key": "k"}).api_key == "k"


async def test_defaultTimeoutIsBounded():
    """20s 是「记忆写入与召回不能拖慢对话」的软上限：调大它得连带改这条断言。"""
    assert isinstance(emb.DEFAULT_TIMEOUT, (int, float)) and 0 < emb.DEFAULT_TIMEOUT <= 60


async def test_vectorOf_acceptsTupleAndIntElements():
    """provider 给 tuple 或整数分量都是合法向量，不能因为类型窄了而降级。"""
    vec = tuple([1 if i == 0 else 0 for i in range(DIM)])
    cli, _ = _client(result=_resp(vec))
    out = await emb.embed_texts(CFG, ["a"], client=cli)
    assert out == [_vec(0)]
