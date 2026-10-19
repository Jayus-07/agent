"""Stage1 放宽链契约（P0-2 简化版，2026-10-19）。

历史背景（TD-13，2026-10-03）："发票认证时限"被 QueryAnalyzer 按"认证"
词表误抽 doc_type=security → Stage 1 0 匹配 → 放宽链逐级去 business_domain
但保留 doc_type → 召回空间恰只剩 security 文档 → Rerank 8/8 淘汰 → 假拒答。

P0-2 治理：QueryAnalyzer 停发 doc_type/business_domain 硬过滤（猜测即过滤
是漏召回根源），放宽链简化为唯一维度——KBRouter 猜测的 kb_id/$or 失配时
授权范围内跨 KB 全量检索。本文件钉死简化后契约：
1. 猜测维度（doc_type/business_domain）根本不再进入 filter；
2. 0 匹配时 kb 维度被放宽（宁跨 KB 召回，不全量拒答）；
3. 非 kb 维度的保留过滤（person_names 等实体）不受放宽影响。
"""
import pytest

from backend.rag.retrieval.retrievers import ChunkLevelRetriever


@pytest.fixture
def retriever():
    r = ChunkLevelRetriever.__new__(ChunkLevelRetriever)
    r._filter_has_docs = lambda f: False
    yield r


def _staging(filter_dict):
    from backend.rag.retrieval.retrievers import _Staging

    class _Span:
        events = []

    return _Staging(
        query="发票认证时限是多久",
        metadata_filter=filter_dict,
        doc_ids=[],
        stage1_path="metadata_filter",
        stage1_fallback_count=0,
        span=_Span(),
    )


def test_kb_guess_relaxed_on_zero_match(retriever):
    """KBRouter 猜测的 kb_id 0 匹配 → 放宽（跨 KB，不全量拒答）。"""
    st = _staging({"kb_id": "policy_finance"})
    retriever._stage1_relax_on_zero_match(st)
    assert "kb_id" not in st.metadata_filter
    assert st.stage1_path == "kb_fallback"
    assert st.cross_kb_fallback is True


def test_or_kb_guess_also_relaxed(retriever):
    """$or 多 KB 候选同属 kb 维度猜测，一并放宽。"""
    st = _staging({"$or": [{"kb_id": "a"}, {"kb_id": "b"}]})
    retriever._stage1_relax_on_zero_match(st)
    assert "$or" not in st.metadata_filter
    assert st.cross_kb_fallback is True


def test_entity_filter_survives_relaxation(retriever):
    """非 kb 维度的实体过滤（person_names）不被放宽波及。"""
    st = _staging({"person_names": "MeridiHome", "kb_id": "x"})
    retriever._stage1_relax_on_zero_match(st)
    assert st.metadata_filter == {"person_names": "MeridiHome"}


def test_empty_filter_no_relax(retriever):
    """filter 为空时放宽链不启动（无猜测可放宽）。"""
    st = _staging({})
    retriever._stage1_relax_on_zero_match(st)
    assert st.stage1_path == "metadata_filter"
    assert st.stage1_fallback_count == 0
