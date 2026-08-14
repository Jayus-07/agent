"""snippet_match 匹配策略单元测试。

V1.1 新增：expected.match_type='snippet' 时，召回内容含所有 keywords → pass。
不绑定 doc_id/chunk_id，文件重索引不影响评测集稳定性。
"""
from backend.evaluation.runners.builtin import _match_by_snippet


def test_snippet_match_all_keywords_present():
    """关键词都在召回内容里 → hit=True"""
    details = [
        {"snippet": "丢件 48 小时内核查，物流客服跟进", "chunk_id": "a"},
        {"snippet": "其他内容", "chunk_id": "b"},
    ]
    snippets = ["丢件", "48 小时"]
    hit, recall = _match_by_snippet(details, snippets)
    assert hit is True
    assert recall == 1.0


def test_snippet_match_missing_keyword():
    """少一个关键词 → hit=False"""
    details = [
        {"snippet": "丢件立即处理，无时长限制", "chunk_id": "a"},  # 不含"48 小时"
    ]
    snippets = ["丢件", "48 小时"]
    hit, recall = _match_by_snippet(details, snippets)
    assert hit is False
    assert recall == 0.5  # 只命中一半


def test_snippet_match_empty_snippets():
    """expected_snippets 为空 → recall=0, hit=False"""
    details = [{"snippet": "anything", "chunk_id": "a"}]
    hit, recall = _match_by_snippet(details, [])
    assert hit is False
    assert recall == 0.0


def test_snippet_match_empty_details():
    """召回为空 → hit=False, recall=0"""
    hit, recall = _match_by_snippet([], ["丢件"])
    assert hit is False
    assert recall == 0.0


def test_snippet_match_keyword_in_different_chunks():
    """关键词分散在不同 chunk 也算命中"""
    details = [
        {"snippet": "丢件处理流程...", "chunk_id": "a"},
        {"snippet": "48 小时内核查时效", "chunk_id": "b"},
    ]
    snippets = ["丢件", "48 小时"]
    hit, recall = _match_by_snippet(details, snippets)
    assert hit is True
    assert recall == 1.0


def test_snippet_match_partial_in_one_chunk():
    """两个关键词在同一 chunk → 也算命中"""
    details = [
        {"snippet": "丢件 48 小时核查", "chunk_id": "a"},
    ]
    snippets = ["丢件", "48 小时"]
    hit, recall = _match_by_snippet(details, snippets)
    assert hit is True
    assert recall == 1.0


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
