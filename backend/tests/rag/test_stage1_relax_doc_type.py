"""TD-13 Stage1 放宽链回归（2026-10-03）。

实测背景："发票认证时限是多久，报销单每月几号截止提交？"被 QueryAnalyzer
按"认证"词表误抽 doc_type=security（发票文档是 policy）→ Stage 1 0 匹配
→ 放宽链逐级去掉 business_domain/kb_id 但保留 doc_type → 召回空间恰只
剩 general_security.pdf（全库唯一 security 文档）→ Rerank 8/8 淘汰 →
空检索短路假拒答。

钉死：kb_fallback 级（跨库放宽）必须同时丢弃 doc_type——QueryAnalyzer
猜测在 0 匹配时不可信，排序交回混合检索与 Rerank。
"""
import pytest

from backend.rag.retrieval.retrievers import ChunkLevelRetriever


@pytest.fixture
def retriever():
    # _stage1_relax_on_zero_match 只读 _filter_has_docs / trace；最小桩
    r = ChunkLevelRetriever.__new__(ChunkLevelRetriever)
    r._filter_has_docs = lambda f: False  # 放宽后仍无文档 → 触发跨库级
    # span=None 时 trace_collector.add_event 走 noop 路径，无需打桩
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


def test_kb_fallback_drops_doc_type(retriever):
    """if 分支：放宽 business_domain 后仍空 → 跨库级丢弃 doc_type。"""
    st = _staging({
        "kb_id": "policy_finance",
        "doc_type": {"$in": ["security", "financial"]},
        "business_domain": "financial",
    })
    retriever._stage1_relax_on_zero_match(st)
    assert "doc_type" not in st.metadata_filter
    assert "kb_id" not in st.metadata_filter
    assert "business_domain" not in st.metadata_filter
    assert st.stage1_path == "kb_fallback"
    assert st.cross_kb_fallback is True


def test_no_domain_branch_drops_doc_type(retriever):
    """else 分支：filter 无 business_domain 时直接放宽 kb_id 也丢弃 doc_type。"""
    st = _staging({
        "kb_id": "policy_finance",
        "doc_type": {"$in": ["security"]},
    })
    retriever._stage1_relax_on_zero_match(st)
    assert "doc_type" not in st.metadata_filter
    assert st.stage1_path == "kb_fallback"


def test_domain_fallback_first_keeps_doc_type(retriever):
    """一级放宽（去 business_domain）时 doc_type 保留——仅跨库级才全丢。

    用 _filter_has_docs=True 桩：放宽 business_domain 后有文档 → 停在
    domain_fallback，doc_type 仍生效（正常猜测可信时保持收窄）。
    """
    retriever._filter_has_docs = lambda f: True
    st = _staging({
        "kb_id": "policy_finance",
        "doc_type": {"$in": ["policy"]},
        "business_domain": "financial",
    })
    retriever._stage1_relax_on_zero_match(st)
    assert st.metadata_filter.get("doc_type") == {"$in": ["policy"]}
    assert "business_domain" not in st.metadata_filter
    assert st.stage1_path == "domain_fallback"
    assert st.cross_kb_fallback is not True
