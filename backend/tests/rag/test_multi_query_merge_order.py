"""MultiQuery 合并后的排序契约（2026-10-18）。

真实缺陷：各变体检索到的候选已由各自 query 重排（rerank_score），但合并
按"查询提交顺序"拼接（原始 query 恒第一组），变体里的高相关证据永远排在
原始 query 结果之后 → Top-1 只由原始 query 决定，MultiQuery 收益无法进入
最终排序。
"""
from backend.rag.retrieval.multi_query import _order_merged_by_score


class _Doc:
    def __init__(self, cid, score=None):
        self.metadata = {"chunk_id": cid}
        if score is not None:
            self.metadata["rerank_score"] = score
        self.page_content = cid


def test_variant_high_score_moves_before_original():
    docs = [
        _Doc("orig-1", 0.4),
        _Doc("orig-2", 0.2),
        _Doc("variant-1", 0.95),
        _Doc("variant-2", 0.7),
    ]

    ordered = [d.metadata["chunk_id"] for d in _order_merged_by_score(docs)]

    assert ordered == ["variant-1", "variant-2", "orig-1", "orig-2"]


def test_order_kept_when_no_scores():
    docs = [_Doc("a"), _Doc("b"), _Doc("c")]
    assert [d.metadata["chunk_id"] for d in _order_merged_by_score(docs)] == [
        "a", "b", "c",
    ]


def test_equal_scores_keep_original_relative_order():
    docs = [_Doc("a", 0.5), _Doc("b", 0.5), _Doc("c", 0.9)]
    assert [d.metadata["chunk_id"] for d in _order_merged_by_score(docs)] == [
        "c", "a", "b",
    ]


def test_missing_score_treated_as_zero_without_dropping():
    docs = [_Doc("a", 0.6), _Doc("b")]
    ordered = _order_merged_by_score(docs)
    assert [d.metadata["chunk_id"] for d in ordered] == ["a", "b"]
    assert len(ordered) == 2
