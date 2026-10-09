# tests/test_memory_recall_policy.py
"""召回收口（ticket 006 / spec 故事 14—17）：memory.* 配置 + 阈值/topK/token 三重上限 + 混检。

靶心 = `recall_memories` 这**一个**接缝：调用方只拿条目列表，三重上限的顺序、降级的成因
区分全部在服务层内部完成（ADR-0012 后果条：不许写第二套平行实现）。

假 provider 用「向量在前两维的分量」直接编码相似度：查询恒为 [1,0,...]，
于是 strong≈1.0、middle=0.6、weak≈0.3 三档差距极大，pgvector 的 float4 距离噪声（~1e-7）
不可能把阈值判断翻掉——阈值边界要的是「档位差」而不是「小数位差」。
"""
import pytest
from sqlalchemy import text
from app.db import make_session_factory
from app.services import memory_service as mem
from app.services import config_service
from tests.test_memory_service import mk_ws

_QUERY = "alpha query"


def _use_tier_provider(monkeypatch):
    """按文本里的档位词给向量：strong/middle/weak；认不出的文本返回 None（=算不出来）。"""
    async def fake(s, owner_user_id, texts_):
        out = []
        for t in texts_:
            low = (t or "").lower()
            v = [0.0] * mem.embeddings.EMBEDDING_DIM
            if "query" in low or "strong" in low:
                v[0] = 1.0
            elif "middle" in low:
                v[0], v[1] = 0.6, 0.8
            elif "weak" in low:
                v[0], v[1] = 0.3, 0.954
            else:
                return None
            out.append(v)
        return out
    monkeypatch.setattr(mem.embeddings, "embed_for_owner", fake)


def _use_broken_provider(monkeypatch):
    async def fake(s, owner_user_id, texts_):
        return [] if not texts_ else None
    monkeypatch.setattr(mem.embeddings, "embed_for_owner", fake)


async def _mk(test_engine, tmp_path, name):
    return await mk_ws(test_engine, str(tmp_path / name))


async def _add(sf, ws, content, type_="fact"):
    return await mem.create(sf, ws, type_, content)


# ---------------- 配置接缝：cfg 直传 与 库里读 ----------------

async def test_top_k_comes_from_cfg_when_caller_does_not_pass_it(test_engine, tmp_path,
                                                                  monkeypatch):
    """不传 top_k 时读配置：这是 loop 侧的入口，配置改了就生效，不必动调用方。"""
    ws = await _mk(test_engine, tmp_path, "a")
    sf = make_session_factory(test_engine)
    _use_broken_provider(monkeypatch)
    for i in range(3):
        await _add(sf, ws, f"pandas 做法{i}")
    hit = await mem.recall_memories(sf, ws, "pandas", cfg={"memory.top_k": 2})
    assert len(hit) == 2


async def test_cfg_argument_skips_the_database_read(test_engine, tmp_path, monkeypatch):
    """loop 已经把 owner 视角的 cfg 解析好了，传进来就不该再查一次 app_config。"""
    ws = await _mk(test_engine, tmp_path, "a")
    sf = make_session_factory(test_engine)
    await _add(sf, ws, "pandas 做法")

    async def boom(*a, **k):
        raise AssertionError("cfg 已给出，不应再读库")
    monkeypatch.setattr(mem, "_config_for", boom)
    assert len(await mem.recall_memories(sf, ws, "pandas", cfg={})) == 1


async def test_config_is_read_from_database_when_cfg_omitted(client, test_engine, tmp_path):
    """cfg=None 时走「工作区 → owner → app_config」这条唯一接缝（记忆开关按账号各一份）。"""
    ws = await _mk(test_engine, tmp_path, "a")
    sf = make_session_factory(test_engine)
    async with sf() as s:
        owner = (await s.execute(text("SELECT id FROM actor ORDER BY id LIMIT 1"))).scalar()
        assert owner is not None, "client fixture 应已注册账号"
        await s.execute(text("UPDATE workspace SET owner_user_id=:o WHERE id=:w"),
                        {"o": owner, "w": ws})
        # 只写 owner 行：平台级（owner NULL）的 app_config 不被 conftest 的 TRUNCATE 清掉，
        # 写进去会把 memory.top_k 漏给后续每一个测试。
        await config_service.set_many(s, {"memory.top_k": 2}, owner_user_id=owner)
        await s.commit()
    for i in range(4):
        await _add(sf, ws, f"pandas 做法{i}")
    assert len(await mem.recall_memories(sf, ws, "pandas")) == 2
    # 显式入参优先于库里的配置（002/005 已有的调用方语义）
    assert len(await mem.recall_memories(sf, ws, "pandas", top_k=4)) == 4


async def test_missing_workspace_returns_empty_even_with_cfg(test_engine):
    sf = make_session_factory(test_engine)
    assert await mem.recall_memories(sf, 999999999999, "pandas",
                                     cfg={"memory.top_k": 5}) == []


async def test_workspace_without_owner_row_still_reads_cfg_without_raising(
        test_engine, tmp_path):
    """边界：cfg 接缝自己遇不到工作区时不得抛错——召回永远不许打断对话。"""
    sf = make_session_factory(test_engine)
    assert await mem.recall_memories(sf, 999999999999, "pandas") == []


# ---------------- memory.enabled ----------------

async def test_disabled_recall_returns_empty_before_any_lookup(test_engine, tmp_path, monkeypatch):
    """总开关关掉：不查向量、不查关键词，一条 SQL 都不该为召回发出去。"""
    ws = await _mk(test_engine, tmp_path, "a")
    sf = make_session_factory(test_engine)
    await _add(sf, ws, "pandas 做法")
    called: list = []

    async def spy_vector(*a, **k):
        called.append("vector")
        return None
    async def spy_keyword(*a, **k):
        called.append("keyword")
        return []
    monkeypatch.setattr(mem, "_recall_by_vector", spy_vector)
    monkeypatch.setattr(mem, "_recall_by_keyword", spy_keyword)
    assert await mem.recall_memories(sf, ws, "pandas", cfg={"memory.enabled": False}) == []
    assert called == []


@pytest.mark.parametrize("raw", ["false", "False", "  off ", "0", "no", "FALSE"])
async def test_enabled_accepts_common_false_spellings(test_engine, tmp_path, raw):
    """配置值经 HTTP/JSON 传进来常常是字符串，'false' 被当成真值就是「关不掉的开关」。"""
    ws = await _mk(test_engine, tmp_path, "a")
    sf = make_session_factory(test_engine)
    await _add(sf, ws, "pandas 做法")
    assert await mem.recall_memories(sf, ws, "pandas", cfg={"memory.enabled": raw}) == []


@pytest.mark.parametrize("raw", ["true", "1", "yes", True, 1, "on"])
async def test_enabled_truthy_spellings_keep_recall_working(test_engine, tmp_path, raw):
    ws = await _mk(test_engine, tmp_path, "a")
    sf = make_session_factory(test_engine)
    await _add(sf, ws, "pandas 做法")
    assert len(await mem.recall_memories(sf, ws, "pandas", cfg={"memory.enabled": raw})) == 1


async def test_enabled_illegal_value_falls_back_to_true(test_engine, tmp_path):
    """认不出来的值不能顺手当成「关」：误关是静默丢功能，误开最多是多注入几条。"""
    ws = await _mk(test_engine, tmp_path, "a")
    sf = make_session_factory(test_engine)
    await _add(sf, ws, "pandas 做法")
    for bad in ("maybe", None, [], {}, "2"):
        assert len(await mem.recall_memories(sf, ws, "pandas",
                                             cfg={"memory.enabled": bad})) == 1


# ---------------- 相似度阈值 ----------------

async def test_threshold_drops_low_similarity_hits(test_engine, tmp_path, monkeypatch):
    ws = await _mk(test_engine, tmp_path, "a")
    sf = make_session_factory(test_engine)
    _use_tier_provider(monkeypatch)
    await _add(sf, ws, "memo strong one")
    await _add(sf, ws, "memo middle two")
    await _add(sf, ws, "memo weak three")
    hit = await mem.recall_memories(sf, ws, _QUERY, cfg={"memory.min_similarity": 0.7})
    assert [h["content"] for h in hit] == ["memo strong one"]
    assert hit[0]["similarity"] > 0.9


async def test_threshold_equal_to_similarity_is_kept(test_engine, tmp_path, monkeypatch):
    """恰等于边界值：`>=` 不是 `>`，等于阈值的条必须留下。"""
    ws = await _mk(test_engine, tmp_path, "a")
    sf = make_session_factory(test_engine)
    _use_tier_provider(monkeypatch)
    await _add(sf, ws, "memo strong one")
    hit = await mem.recall_memories(sf, ws, _QUERY, cfg={"memory.min_similarity": 1.0})
    assert [h["content"] for h in hit] == ["memo strong one"]


async def test_threshold_just_above_best_similarity_returns_empty(test_engine, tmp_path,
                                                                  monkeypatch):
    """阈值略高于全部命中：返回空列表，而且**不得**回落关键词把弱相关请回来。"""
    ws = await _mk(test_engine, tmp_path, "a")
    sf = make_session_factory(test_engine)
    _use_tier_provider(monkeypatch)
    await _add(sf, ws, "memo middle two")
    hit = await mem.recall_memories(sf, ws, _QUERY, cfg={"memory.min_similarity": 0.9})
    assert hit == []


async def test_threshold_epsilon_around_the_measured_similarity(test_engine, tmp_path, monkeypatch):
    """阈值 ±ε：以实际测得的相似度为锚，加一点就滤掉、减一点就保留（边界不是拍脑袋的档位）。"""
    ws = await _mk(test_engine, tmp_path, "a")
    sf = make_session_factory(test_engine)
    _use_tier_provider(monkeypatch)
    await _add(sf, ws, "memo middle two")
    base = await mem.recall_memories(sf, ws, _QUERY, cfg={})
    assert len(base) == 1
    sim = base[0]["similarity"]
    assert await mem.recall_memories(
        sf, ws, _QUERY, cfg={"memory.min_similarity": sim + 1e-3}) == []
    assert len(await mem.recall_memories(
        sf, ws, _QUERY, cfg={"memory.min_similarity": sim - 1e-3})) == 1


async def test_threshold_does_not_trigger_keyword_fallback(test_engine, tmp_path, monkeypatch):
    """「向量可用但全被滤掉」与「这轮根本没有向量」是两种相反语义，混成一个就破功。"""
    ws = await _mk(test_engine, tmp_path, "a")
    sf = make_session_factory(test_engine)
    # 同一条既走向量（middle=0.6）又能被关键词命中：阈值滤掉它之后关键词不许接管
    _use_tier_provider(monkeypatch)
    await _add(sf, ws, "pandas middle 做法")
    hit = await mem.recall_memories(sf, ws, "pandas query",
                                    cfg={"memory.min_similarity": 0.9})
    assert hit == []


async def test_workspace_without_any_vector_still_falls_back_under_threshold(
        test_engine, tmp_path, monkeypatch):
    """升级不清零（故事 5）：老库一条向量都没有，阈值调到 1.0 也必须照样关键词召回。"""
    ws = await _mk(test_engine, tmp_path, "a")
    sf = make_session_factory(test_engine)
    _use_broken_provider(monkeypatch)
    await _add(sf, ws, "订单导出走 pandas 分块")
    _use_tier_provider(monkeypatch)
    hit = await mem.recall_memories(sf, ws, "pandas query",
                                    cfg={"memory.min_similarity": 1.0})
    assert [h["content"] for h in hit] == ["订单导出走 pandas 分块"]
    assert "similarity" not in hit[0]


async def test_threshold_ignored_when_vector_path_unavailable(test_engine, tmp_path, monkeypatch):
    """阈值只约束向量路：降级到关键词时没有相似度可谈，不能把降级也清零。"""
    ws = await _mk(test_engine, tmp_path, "a")
    sf = make_session_factory(test_engine)
    _use_broken_provider(monkeypatch)
    await _add(sf, ws, "pandas 分块导出")
    hit = await mem.recall_memories(sf, ws, "pandas",
                                    cfg={"memory.min_similarity": 0.95})
    assert len(hit) == 1


@pytest.mark.parametrize("raw", [1.5, "abc", None, [], {}, True, float("nan"), "NaN", 2, "1/2"])
async def test_illegal_min_similarity_falls_back_to_no_filter(test_engine, tmp_path,
                                                              monkeypatch, raw):
    """非法阈值回落到「不筛」而不是某个正数：默认值本身必须不改变行为。"""
    ws = await _mk(test_engine, tmp_path, "a")
    sf = make_session_factory(test_engine)
    _use_tier_provider(monkeypatch)
    await _add(sf, ws, "memo weak three")      # 相似度 ≈0.3
    await _add(sf, ws, "memo strong one")
    hit = await mem.recall_memories(sf, ws, _QUERY, cfg={"memory.min_similarity": raw})
    assert len(hit) == 2
    assert mem._resolve_policy({"memory.min_similarity": raw}, None).min_similarity == -1.0


async def test_default_threshold_keeps_an_opposite_direction_memory(
        test_engine, tmp_path, monkeypatch):
    """默认档（cfg={}）不得因阈值丢条：反向语义的记忆余弦相似度是**负数**，
    把默认写成 0.0 会把它静默滤掉，而滤空走的是 `[]` 语义（不降级关键词）——
    等于用户什么都没配也会丢记忆（006 review 指出的真 bug）。"""
    ws = await _mk(test_engine, tmp_path, "a")
    sf = make_session_factory(test_engine)

    async def opposite(s, owner_user_id, texts_):
        out = []
        for t in texts_:
            v = [0.0] * mem.embeddings.EMBEDDING_DIM
            if "query" in (t or "").lower():
                v[0] = 1.0
            else:
                # 与查询大致反向 → similarity ≈ -0.6。刻意不取【完全反向】的 -1.0：
                # 那就是阈值下界本身，float4 距离一抖就能多出一位负号，测的就不是行为了。
                v[0], v[1] = -0.6, 0.8
            out.append(v)
        return out
    monkeypatch.setattr(mem.embeddings, "embed_for_owner", opposite)
    await _add(sf, ws, "memo opposite")
    default = await mem.recall_memories(sf, ws, _QUERY, cfg={})
    assert [h["content"] for h in default] == ["memo opposite"]
    assert default[0]["similarity"] < -0.5


async def test_negative_threshold_is_legal_and_still_filters(
        test_engine, tmp_path, monkeypatch):
    """合法域推广到 [-1,1] 后，负阈值必须是「可用且能有选择地滤」：
    -0.5 保留正交（similarity=0.0）那条，滤掉反向（≈-0.9）那条。"""
    ws = await _mk(test_engine, tmp_path, "a")
    sf = make_session_factory(test_engine)

    async def mixed(s, owner_user_id, texts_):
        out = []
        for t in texts_:
            v = [0.0] * mem.embeddings.EMBEDDING_DIM
            low = (t or "").lower()
            if "query" in low:
                v[0] = 1.0
            elif "opposite" in low:
                v[0], v[1] = -0.9, 0.4359   # similarity ≈ -0.9，低于 -0.5
            else:
                v[1] = 1.0        # 正交 → similarity = 0.0，高于 -0.5
            out.append(v)
        return out
    monkeypatch.setattr(mem.embeddings, "embed_for_owner", mixed)
    await _add(sf, ws, "memo opposite")
    await _add(sf, ws, "memo orthogonal")
    hit = await mem.recall_memories(sf, ws, _QUERY,
                                    cfg={"memory.min_similarity": -0.5})
    assert [h["content"] for h in hit] == ["memo orthogonal"]


async def test_threshold_zero_keeps_a_perfectly_orthogonal_hit(test_engine, tmp_path, monkeypatch):
    """阈值 0.0 = 不过滤：正交向量相似度就是 0，`>=` 必须放它过。"""
    ws = await _mk(test_engine, tmp_path, "a")
    sf = make_session_factory(test_engine)

    async def fake(s, owner_user_id, texts_):
        out = []
        for t in texts_:
            v = [0.0] * mem.embeddings.EMBEDDING_DIM
            if "query" in (t or "").lower():
                v[0] = 1.0
            else:
                v[1] = 1.0        # 与查询完全正交 → similarity = 0.0
            out.append(v)
        return out
    monkeypatch.setattr(mem.embeddings, "embed_for_owner", fake)
    await _add(sf, ws, "memo orthogonal")
    assert len(await mem.recall_memories(sf, ws, _QUERY,
                                         cfg={"memory.min_similarity": 0.0})) == 1


# ---------------- 混检补充 ----------------

async def test_unvectorized_rows_are_supplemented_in_vector_mode(test_engine, tmp_path, monkeypatch):
    """故事 17：没向量的老条靠关键词补进来，不因没向量而永久失联。"""
    ws = await _mk(test_engine, tmp_path, "a")
    sf = make_session_factory(test_engine)
    _use_broken_provider(monkeypatch)
    await _add(sf, ws, "pandas 老规矩")        # 无向量
    _use_tier_provider(monkeypatch)
    await _add(sf, ws, "memo strong one")      # 有向量
    hit = await mem.recall_memories(sf, ws, "pandas query")
    assert [h["content"] for h in hit] == ["memo strong one", "pandas 老规矩"]
    assert hit[0]["has_embedding"] is True and hit[1]["has_embedding"] is False


async def test_supplement_never_brings_back_a_row_the_threshold_dropped(
        test_engine, tmp_path, monkeypatch):
    """补齐只看「有没有向量」：有向量的行归阈值管，关键词不许越过阈值把它捞回来。"""
    ws = await _mk(test_engine, tmp_path, "a")
    sf = make_session_factory(test_engine)
    _use_tier_provider(monkeypatch)
    await _add(sf, ws, "pandas middle 做法")   # 有向量、0.6，会被 0.9 滤掉
    _use_broken_provider(monkeypatch)
    await _add(sf, ws, "pandas 老规矩")        # 无向量
    _use_tier_provider(monkeypatch)
    hit = await mem.recall_memories(sf, ws, "pandas query",
                                    cfg={"memory.min_similarity": 0.9})
    assert [h["content"] for h in hit] == ["pandas 老规矩"]


async def test_supplement_does_not_duplicate_a_row_already_recalled(
        test_engine, tmp_path, monkeypatch):
    """去重按字符串比 id：BIGINT 过 Number() 就撞成同一个数（全局红线）。"""
    ws = await _mk(test_engine, tmp_path, "a")
    sf = make_session_factory(test_engine)
    _use_tier_provider(monkeypatch)
    row = await _add(sf, ws, "pandas strong 做法")   # 有向量且能被关键词命中
    hit = await mem.recall_memories(sf, ws, "pandas query")
    assert [str(h["id"]) for h in hit] == [str(row["id"])]


async def test_supplement_keeps_disabled_rows_out(test_engine, tmp_path, monkeypatch):
    """补齐走的是同一张启用清单：停用行不许从混检这道侧门溜回来（故事 22）。"""
    ws = await _mk(test_engine, tmp_path, "a")
    sf = make_session_factory(test_engine)
    _use_tier_provider(monkeypatch)
    await _add(sf, ws, "memo strong one")
    _use_broken_provider(monkeypatch)
    # 必须真的停用（enabled=False）：走 _add 建出来的是启用行，会被补齐正常命中，
    # 那条断言就退化成「补齐能用」，测不到侧门。
    off = await mem.create(sf, ws, "fact", "pandas 已停用的旧结论", enabled=False)
    _use_tier_provider(monkeypatch)
    hit = await mem.recall_memories(sf, ws, "pandas query")
    assert [h["content"] for h in hit] == ["memo strong one"]
    assert str(off["id"]) not in [str(h["id"]) for h in hit]


async def test_supplement_squeezed_out_when_vector_hits_fill_top_k(
        test_engine, tmp_path, monkeypatch):
    """显式钉住一个有意取舍（006 review）：补齐行排在向量行之后、共用同一个 topK 名额。

    向量路填满名额时，关键词补齐的老条本轮就进不了注入；这是有意的共享名额，不是 bug：故事 17 只保证
    「老条不因没向量而永久失联」，不保证它一定挤进 topK。放到 K+1 必须又补得回来。
    """
    ws = await _mk(test_engine, tmp_path, "a")
    sf = make_session_factory(test_engine)
    _use_tier_provider(monkeypatch)
    await _add(sf, ws, "memo strong one")       # 有向量，similarity ≈1.0
    await _add(sf, ws, "memo middle two")       # 有向量，similarity =0.6
    _use_broken_provider(monkeypatch)
    await _add(sf, ws, "pandas 老规矩")          # 无向量，但能被关键词命中
    _use_tier_provider(monkeypatch)
    full = await mem.recall_memories(sf, ws, "pandas query",
                                     cfg={"memory.top_k": 2})
    assert [h["content"] for h in full] == ["memo strong one", "memo middle two"]
    loose = await mem.recall_memories(sf, ws, "pandas query",
                                      cfg={"memory.top_k": 3})
    assert [h["content"] for h in loose] == ["memo strong one", "memo middle two",
                                             "pandas 老规矩"]


# ---------------- topK ----------------

async def test_top_k_exact_k_and_k_plus_one(test_engine, tmp_path, monkeypatch):
    ws = await _mk(test_engine, tmp_path, "a")
    sf = make_session_factory(test_engine)
    _use_broken_provider(monkeypatch)
    for i in range(3):
        await _add(sf, ws, f"pandas 做法{i}")
    assert len(await mem.recall_memories(sf, ws, "pandas", cfg={"memory.top_k": 2})) == 2
    assert len(await mem.recall_memories(sf, ws, "pandas", cfg={"memory.top_k": 3})) == 3
    assert len(await mem.recall_memories(sf, ws, "pandas", cfg={"memory.top_k": 4})) == 3
    assert len(await mem.recall_memories(sf, ws, "pandas", cfg={"memory.top_k": 1})) == 1


async def test_top_k_five_of_twelve_candidates(test_engine, tmp_path, monkeypatch):
    """票面验收「命中 50 条只回 5 条」的等价缩样：候选远多于 K 时截断必须真的发生。"""
    ws = await _mk(test_engine, tmp_path, "a")
    sf = make_session_factory(test_engine)
    _use_broken_provider(monkeypatch)
    for i in range(12):
        await _add(sf, ws, f"pandas 做法{i}")
    assert len(await mem.recall_memories(sf, ws, "pandas", cfg={"memory.top_k": 5})) == 5


async def test_single_candidate_with_large_top_k(test_engine, tmp_path):
    ws = await _mk(test_engine, tmp_path, "a")
    sf = make_session_factory(test_engine)
    await _add(sf, ws, "pandas 做法")
    assert len(await mem.recall_memories(sf, ws, "pandas", cfg={"memory.top_k": 100})) == 1


async def test_explicit_top_k_wins_over_cfg_and_zero_means_unlimited(test_engine, tmp_path):
    """显式入参保留 002/005 的「<=0 不限制」契约；配置里的 0 却是非法值，两者刻意不同。"""
    ws = await _mk(test_engine, tmp_path, "a")
    sf = make_session_factory(test_engine)
    for i in range(3):
        await _add(sf, ws, f"pandas 做法{i}")
    assert len(await mem.recall_memories(sf, ws, "pandas", top_k=1,
                                         cfg={"memory.top_k": 5})) == 1
    assert len(await mem.recall_memories(sf, ws, "pandas", top_k=0,
                                         cfg={"memory.top_k": 1})) == 3
    assert len(await mem.recall_memories(sf, ws, "pandas", top_k=-1,
                                         cfg={"memory.top_k": 1})) == 3


async def test_no_keyword_hit_on_degraded_path_returns_empty(test_engine, tmp_path):
    """降级路一命中也没有 → 空列表：候选行存在但不匹配，而不是「工作区没记忆」。"""
    ws = await _mk(test_engine, tmp_path, "a")
    sf = make_session_factory(test_engine)
    await _add(sf, ws, "订单表主键是雪花 ID")
    assert await mem.recall_memories(sf, ws, "pandas", cfg={"memory.top_k": 5}) == []


@pytest.mark.parametrize("raw", ["abc", "", "  ", None, [], {}, True, False, float("nan"),
                                 "NaN", 0, -1, 101, 10 ** 9])
async def test_illegal_top_k_falls_back_to_default(test_engine, tmp_path, raw):
    """非法 top_k 回落默认 5：负数进 SQL 的 LIMIT、True 变成 1 条都是静默错误。"""
    ws = await _mk(test_engine, tmp_path, "a")
    sf = make_session_factory(test_engine)
    for i in range(mem.DEFAULT_TOP_K + 2):
        await _add(sf, ws, f"pandas 做法{i}")
    hit = await mem.recall_memories(sf, ws, "pandas", cfg={"memory.top_k": raw})
    assert len(hit) == mem.DEFAULT_TOP_K


async def test_valid_top_k_string_from_form_is_accepted(test_engine, tmp_path):
    """表单传上来的 "2" 是字符串，该认而不是判成非法：回落默认会把用户显式的选择改掉。"""
    ws = await _mk(test_engine, tmp_path, "a")
    sf = make_session_factory(test_engine)
    for i in range(3):
        await _add(sf, ws, f"pandas 做法{i}")
    assert len(await mem.recall_memories(sf, ws, "pandas", cfg={"memory.top_k": "2"})) == 2


@pytest.mark.parametrize("raw,expect", [(2.5, mem.DEFAULT_TOP_K), (3.0, 3)])
async def test_fractional_top_k_falls_back_but_integral_float_is_ok(test_engine, tmp_path,
                                                                    raw, expect):
    """2.5 该判非法（悄悄 int() 成 2 会少投一条），3.0 是「表单传来的整数值」该认。"""
    ws = await _mk(test_engine, tmp_path, "a")
    sf = make_session_factory(test_engine)
    for i in range(6):
        await _add(sf, ws, f"pandas 做法{i}")
    assert len(await mem.recall_memories(sf, ws, "pandas", cfg={"memory.top_k": raw})) == expect


# ---------------- token 封顶 ----------------

async def test_token_cap_drops_whole_entries_only(test_engine, tmp_path, monkeypatch):
    """触顶即停：绝不产生半条被切碎的记录，第 3 条整条不出现。"""
    ws = await _mk(test_engine, tmp_path, "a")
    sf = make_session_factory(test_engine)
    _use_broken_provider(monkeypatch)
    lines = []
    for _ in range(3):
        row = await _add(sf, ws, "abcdefgh")
        lines.append(mem.format_memory_line(row))
    per = mem._estimate_tokens(lines[0])
    assert per == 5                      # "- [fact] abcdefgh" 17 字符 → ceil(17/4)
    hit = await mem.recall_memories(sf, ws, "abcdefgh", cfg={"memory.max_tokens": per * 2})
    assert len(hit) == 2
    assert all("abcdefgh" in h["content"] for h in hit)


async def test_token_budget_too_small_for_first_entry_returns_empty(test_engine, tmp_path):
    """装不下任何一条 → 空列表（注入侧据此不注空块），不能为了「总得给点」塞半条。"""
    ws = await _mk(test_engine, tmp_path, "a")
    sf = make_session_factory(test_engine)
    await _add(sf, ws, "订单导出统一走 pandas 分块，避免一次性载入整表导致 OOM")
    assert await mem.recall_memories(sf, ws, "pandas", cfg={"memory.max_tokens": 1}) == []


async def test_token_cap_counts_the_exact_injected_line(test_engine, tmp_path):
    """预算估的就是实际注入的那一行：两侧不同源就会「按 600 预算塞进 900」。"""
    ws = await _mk(test_engine, tmp_path, "a")
    sf = make_session_factory(test_engine)
    row = await _add(sf, ws, "导出统一走 pandas\n第二行")
    budget = mem._estimate_tokens(mem.format_memory_line(row))
    assert len(await mem.recall_memories(sf, ws, "pandas",
                                         cfg={"memory.max_tokens": budget})) == 1
    assert await mem.recall_memories(sf, ws, "pandas",
                                     cfg={"memory.max_tokens": budget - 1}) == []


async def test_token_cap_applies_after_top_k(test_engine, tmp_path, monkeypatch):
    """三重上限的顺序：先按 topK 截，再按预算封——预算不能把 topK 的名额又放大。"""
    ws = await _mk(test_engine, tmp_path, "a")
    sf = make_session_factory(test_engine)
    _use_broken_provider(monkeypatch)
    rows = []
    for _ in range(4):
        rows.append(await _add(sf, ws, "abcdefgh"))
    one = mem._estimate_tokens(mem.format_memory_line(rows[0]))
    hit = await mem.recall_memories(sf, ws, "abcdefgh",
                                    cfg={"memory.top_k": 3, "memory.max_tokens": one * 5})
    assert len(hit) == 3


async def test_token_cap_also_covers_the_keyword_fallback(test_engine, tmp_path, monkeypatch):
    """降级路同样受三重上限收口：预算收到装不下时，降级也不许把 prompt 撑爆。"""
    ws = await _mk(test_engine, tmp_path, "a")
    sf = make_session_factory(test_engine)
    _use_broken_provider(monkeypatch)
    rows = [await _add(sf, ws, f"pandas 做法{i}") for i in range(3)]
    # 预算按「实测的单行成本」推，不硬编码数字：硬编码把估算器的实现细节写进期望值，
    # 估算口径一改（比如把全角标点也算进汉字档）测试就红在无关的地方。
    one = mem._estimate_tokens(mem.format_memory_line(rows[0]))
    assert len(await mem.recall_memories(sf, ws, "pandas",
                                         cfg={"memory.top_k": 2, "memory.max_tokens": one * 2})) == 2
    assert len(await mem.recall_memories(sf, ws, "pandas",
                                         cfg={"memory.top_k": 2, "memory.max_tokens": one})) == 1
    assert await mem.recall_memories(sf, ws, "pandas",
                                     cfg={"memory.top_k": 2, "memory.max_tokens": one - 1}) == []


@pytest.mark.parametrize("raw", [0, -5, "abc", None, [], True, 10 ** 9, 20001, 2.5])
async def test_illegal_max_tokens_falls_back_to_default(test_engine, tmp_path, raw):
    ws = await _mk(test_engine, tmp_path, "a")
    sf = make_session_factory(test_engine)
    for i in range(mem.DEFAULT_TOP_K):
        await _add(sf, ws, f"pandas 做法{i}")
    hit = await mem.recall_memories(sf, ws, "pandas", cfg={"memory.max_tokens": raw})
    assert len(hit) == mem.DEFAULT_TOP_K


async def test_default_max_tokens_does_not_truncate_ordinary_entries(test_engine, tmp_path):
    """默认 600 不该在日常长度上就把记忆截掉：否则升级等于默默少注入。"""
    ws = await _mk(test_engine, tmp_path, "a")
    sf = make_session_factory(test_engine)
    for i in range(mem.DEFAULT_TOP_K):
        await _add(sf, ws, f"pandas 第{i}条约定，导出走分块读取，避免 OOM 并把结果落进临时目录")
    assert len(await mem.recall_memories(sf, ws, "pandas", cfg={})) == mem.DEFAULT_TOP_K


# ---------------- 策略解析与估算器本身 ----------------

@pytest.mark.parametrize("value,expect", [
    ("", 0),
    (None, 0),
    ("a", 1),
    ("abcd", 1),
    ("abcde", 2),
    ("中", 1),
    ("导出", 2),
    ("导出统一走 pandas 分块", 9),
    ("    ", 1),
    # 前缀 "- [fact] " 9 字符 + 正文 7 个汉字 + "pandas" 与两个空格 8 个非汉字
    # → 7 + ceil(17/4) = 12
    ("- [fact] 导出统一走 pandas 分块", 12),
])
def test_estimate_tokens_boundaries(value, expect):
    assert mem._estimate_tokens(value) == expect


def test_default_policy_matches_documented_defaults():
    policy = mem._resolve_policy({}, None)
    assert policy.enabled is True
    assert policy.top_k == mem.DEFAULT_TOP_K
    # 默认必须是「不因阈值丢条」：0.0 会滤掉相似度为负的反向记忆
    assert policy.min_similarity == mem.DEFAULT_MIN_SIMILARITY == -1.0
    assert policy.max_tokens == mem.MEMORY_DEFAULTS["memory.max_tokens"]


def test_negative_similarity_thresholds_are_inside_the_legal_range():
    """[-1, 0) 是合法值，不得走「越界回落默认」那条 warning 分支。"""
    for raw in (-1.0, -0.5, "-0.2", -0.001):
        assert mem._resolve_policy({"memory.min_similarity": raw}, None) \
            .min_similarity == pytest.approx(float(raw))


def test_absent_cfg_keys_use_defaults_and_empty_cfg_is_not_disabled():
    """cfg 缺键 ≠ 关掉记忆：只有显式的 memory.enabled=false 才短路。"""
    assert mem._resolve_policy({}, None) == mem._resolve_policy({"other.key": 1}, None)


def test_explicit_top_k_zero_is_encoded_as_no_limit():
    """显式 0/负数 → top_k=None（不限制），与配置里 0 判非法是两套刻意不同的口径。"""
    assert mem._resolve_policy({"memory.top_k": 1}, 0).top_k is None
    assert mem._resolve_policy({"memory.top_k": 1}, -7).top_k is None
    assert mem._resolve_policy({"memory.top_k": 1}, 3).top_k == 3
    assert mem._resolve_policy({"memory.top_k": 1}, None).top_k == 1


def test_memory_defaults_only_contains_the_four_documented_keys():
    assert set(mem.MEMORY_DEFAULTS) == {
        "memory.enabled", "memory.top_k", "memory.min_similarity", "memory.max_tokens"}


def test_dedupe_policy_is_hardcoded_and_ignores_user_config():
    """查重策略必须与注入配置解耦：memory.enabled=false 只关注入，不该连带关掉防堆积建议；
    用户把 min_similarity 调高 / max_tokens 压小也不该顺手削掉查重名额（006 review）。"""
    policy = mem.dedupe_policy(3)
    assert policy.enabled is True
    assert policy.top_k == 3
    assert policy.min_similarity == mem.DEFAULT_MIN_SIMILARITY
    # 不做注入预算封顶：否则三条查重建议会被 600 的默认预算按文本长度切掉
    assert policy.max_tokens == mem._TOKEN_RANGE[1]
    assert mem.dedupe_policy(0).top_k is None
    assert mem.dedupe_policy(None).top_k is None


async def test_policy_argument_does_not_read_the_configuration_seam(test_engine, tmp_path,
                                                                    monkeypatch):
    """给了 policy 就是「不吃用户配置」：连取配置的唯一接缝都不得被碰到。"""
    ws = await _mk(test_engine, tmp_path, "a")
    sf = make_session_factory(test_engine)
    await _add(sf, ws, "pandas 做法")

    async def boom(*a, **k):
        raise AssertionError("policy 已给出，不应再读配置")
    monkeypatch.setattr(mem, "_config_for", boom)
    hit = await mem.recall_memories(sf, ws, "pandas", policy=mem.dedupe_policy(3))
    assert [h["content"] for h in hit] == ["pandas 做法"]
