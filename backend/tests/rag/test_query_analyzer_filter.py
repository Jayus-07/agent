"""QueryAnalyzer metadata_filter 的 Chroma 兼容性回归测试（2026-09-14）。

背景（冒烟实测）：多 doc_type 命中时 to_metadata_filter 输出裸 list 值
（doc_type: ['policy', 'financial']），Chroma where 只接受标量或操作符
表达式 → doc 搜索抛 "Expected where value to be a str, int, float, or
operator expression" → 工具重试耗尽 → 空答案。多值必须走 $in（与
business_domain 2026-08-10 的修法一致）。
"""
from backend.rag.retrieval.query_analyzer import ParsedQuery


def test_multi_doc_type_uses_in_operator():
    """多 doc_type → $in 操作符（Chroma 合法），不再输出裸 list。"""
    pq = ParsedQuery(doc_types=["policy", "financial"])
    f = pq.to_metadata_filter()
    assert f["doc_type"] == {"$in": ["policy", "financial"]}


def test_single_doc_type_stays_scalar():
    """单 doc_type 保持标量（精确等值语义不变）。"""
    pq = ParsedQuery(doc_types=["financial"])
    assert pq.to_metadata_filter()["doc_type"] == "financial"


def test_no_bare_list_values_reach_chroma():
    """总断言：to_metadata_filter 输出不得含裸 list 值（Chroma where 拒收）。"""
    pq = ParsedQuery(doc_types=["policy", "financial"],
                     domains=["customer", "order"])
    f = pq.to_metadata_filter()
    for k, v in f.items():
        assert not isinstance(v, list), f"{k} 输出裸 list 值: {v!r}"
