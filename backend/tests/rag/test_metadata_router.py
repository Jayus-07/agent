"""test_metadata_router.py — 元数据级联路由（metadata_router.py）。

覆盖（规划阶段 2.2/3.1）：
  1. L0 强先验：文件名/路径唯一命中定案；多类型冲突继续下走；
  2. L1 taxonomy 嵌入检索：相似度+分差命中 / gap 不足 miss / embedding 异常回退；
  3. L2 候选内词表复核：强信号命中；
  4. L3：LLM 抽取产物透传，失败 llm_result=None（调用方降级规则链）；
  5. 每层 miss → 下一层的先便宜后贵次序。
"""
import pytest

from backend.rag.preprocessing import metadata_router as mr
from backend.rag.preprocessing.metadata_router import (
    TAXONOMY_DESCRIPTIONS,
    CascadeDecision,
    _l0_strong_prior,
    cascade_route,
)

# ---------- fake embedding：labels 顺序与 TAXONOMY_DESCRIPTIONS.keys() 对齐，
# 向量 = 单位基向量，query 按名字指定方向（cosine 完全可控） ----------

_LABELS = list(TAXONOMY_DESCRIPTIONS.keys())


class _FakeEmbedding:
    model_name = "fake-embed"

    def __init__(self, query_dir: list[float] | None = None, fail: bool = False):
        self._query_dir = query_dir
        self._fail = fail

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        n = len(texts)
        return [[1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]

    def embed_query(self, text: str) -> list[float]:
        if self._fail:
            raise RuntimeError("embed down")
        assert self._query_dir is not None, "测试必须指定 query 方向"
        return self._query_dir


def _dir(label: str) -> list[float]:
    v = [0.0] * len(_LABELS)
    v[_LABELS.index(label)] = 1.0
    return v


def _mix(label_a: str, label_b: str) -> list[float]:
    """两方向等权混合：top1=top2=0.707，gap=0 → L1 必 miss。"""
    a, b = _dir(label_a), _dir(label_b)
    return [(x + y) / (2 ** 0.5) for x, y in zip(a, b)]


@pytest.fixture
def _no_llm(monkeypatch):
    """拦截 L3 的 LLM 抽取，记录调用次数。"""
    calls = {"n": 0}

    async def _fake_extract(full_text, filename, parent_span_id=""):
        calls["n"] += 1
        return {"doc_type": "legal", "confidence": 0.9, "business_domain": "general",
                "summary": "s", "keywords": [], "entities": {}, "time_refs": [],
                "risk": {"level": "none", "signals": []}}

    monkeypatch.setattr("backend.rag.preprocessing.metadata_llm.extract_metadata_llm_async",
                        _fake_extract)
    return calls


# ============ L0 ============

class TestL0:
    def test_filename_unique_hit(self):
        d = _l0_strong_prior("供应商NDA模板.docx", "")
        assert d is not None and d.level == "L0"
        assert d.doc_type == "legal"
        assert d.confidence >= 0.9

    def test_folder_hit(self):
        d = _l0_strong_prior("report.docx", "docs/compliance/xxx.docx")
        assert d is not None and d.doc_type == "compliance"

    def test_conflicting_hits_not_decided(self):
        # 报销→financial 与 制度→policy 同时命中 → 冲突，不得定案
        assert _l0_strong_prior("报销制度.docx", "") is None

    def test_no_hit(self):
        assert _l0_strong_prior("meeting-notes.docx", "") is None


# ============ L1 / L2 / L3 级联 ============

@pytest.mark.asyncio
async def test_l1_hit_no_llm_call(_no_llm):
    d = await cascade_route("一些法律文本", "unknown.docx",
                            embedding=_FakeEmbedding(query_dir=_dir("legal")))
    assert d.level == "L1"
    assert d.doc_type == "legal"
    assert d.confidence == 1.0
    assert _no_llm["n"] == 0, "L1 命中不得触发 LLM"
    assert d.llm_result is None


@pytest.mark.asyncio
async def test_l1_miss_then_l2_hit_by_rule_score(_no_llm):
    text = ("本合同由甲方与乙方签订，第一条 违约责任：任何一方违约应赔偿；"
            "第二条 保密条款；第三条 知识产权归属；第四条 争议解决。")
    d = await cascade_route(text, "unknown.docx",
                            embedding=_FakeEmbedding(query_dir=_mix("legal", "contract_template")))
    assert d.level == "L2", "gap=0 必须 L1 miss，落入 L2 词表复核"
    assert d.doc_type == "legal"
    assert _no_llm["n"] == 0, "L2 命中不得触发 LLM"
    assert d.evidence.get("scores", {}).get("legal", 0) > 0


@pytest.mark.asyncio
async def test_l1_miss_l2_miss_falls_to_l3(_no_llm):
    d = await cascade_route("普通会议纪要内容", "unknown.docx",
                            embedding=_FakeEmbedding(query_dir=_mix("faq", "training")))
    assert d.level == "L3"
    assert _no_llm["n"] == 1
    assert d.llm_result is not None
    assert d.doc_type == "legal"  # 来自 L3 抽取产物


@pytest.mark.asyncio
async def test_embedding_failure_skips_to_l3(_no_llm):
    d = await cascade_route("文本", "unknown.docx",
                            embedding=_FakeEmbedding(query_dir=_dir("legal"), fail=True))
    assert d.level == "L3"
    assert _no_llm["n"] == 1, "embedding 故障必须直达 L3，不允许无分类卡死"


@pytest.mark.asyncio
async def test_no_embedding_goes_l3(_no_llm):
    d = await cascade_route("文本", "unknown.docx", embedding=None)
    assert d.level == "L3"
    assert _no_llm["n"] == 1


@pytest.mark.asyncio
async def test_l3_failure_returns_llm_result_none(monkeypatch):
    async def _boom(full_text, filename, parent_span_id=""):
        return None

    monkeypatch.setattr("backend.rag.preprocessing.metadata_llm.extract_metadata_llm_async",
                        _boom)
    d = await cascade_route("文本", "unknown.docx", embedding=None)
    assert d.level == "L3"
    assert d.llm_result is None, "L3 失败必须显式返回 None（调用方降级规则链）"


# ============ 契约 ============

def test_taxonomy_descriptions_cover_all_types():
    from backend.rag.preprocessing.domain_data import DOC_TYPE_RULES
    assert set(TAXONOMY_DESCRIPTIONS) == set(DOC_TYPE_RULES.keys()) | {"general"}
    assert all(len(v) >= 20 for v in TAXONOMY_DESCRIPTIONS.values()), "描述过短会影响区分度"


def test_decision_is_jsonable():
    d = CascadeDecision(level="L0", doc_type="faq", confidence=0.95, evidence={"a": 1})
    import json
    assert json.loads(json.dumps(d.__dict__, ensure_ascii=False))["level"] == "L0"


# ============ 影子路由（阶段 5 基建） ============

@pytest.mark.asyncio
async def test_shadow_route_l0_hit_no_llm(_no_llm):
    from backend.rag.preprocessing.metadata_router import shadow_route
    d = await shadow_route("文本", "供应商NDA模板.docx", embedding=None)
    assert d is not None and d.level == "L0" and d.doc_type == "legal"
    assert _no_llm["n"] == 0, "影子路由绝不触发 LLM"


@pytest.mark.asyncio
async def test_shadow_route_l1_hit(_no_llm):
    from backend.rag.preprocessing.metadata_router import shadow_route
    d = await shadow_route("一些法律文本", "unknown.docx",
                           embedding=_FakeEmbedding(query_dir=_dir("legal")))
    assert d is not None and d.level == "L1" and d.doc_type == "legal"


@pytest.mark.asyncio
async def test_shadow_route_embedding_failure_returns_none(_no_llm):
    from backend.rag.preprocessing.metadata_router import shadow_route
    d = await shadow_route("文本", "unknown.docx",
                           embedding=_FakeEmbedding(query_dir=_dir("legal"), fail=True))
    assert d is None, "embedding 故障影子必须静默返回 None"


@pytest.mark.asyncio
async def test_shadow_route_never_raises(monkeypatch):
    """影子路由任何内部异常都必须吞掉返回 None（不得引入主路径故障面）。"""
    from backend.rag.preprocessing import metadata_router as mr

    async def _boom(*a, **kw):
        raise RuntimeError("shadow exploded")

    monkeypatch.setattr(mr, "_l0_strong_prior", _boom)
    d = await mr.shadow_route("文本", "x.docx", embedding=None)
    assert d is None
