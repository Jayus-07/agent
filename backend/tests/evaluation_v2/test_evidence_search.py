"""快照证据切片检索的评分契约测试。"""

from backend.evaluation_v2.evidence_search import search_evidence


def _rows():
    return [
        {
            "doc_id": "policy-aftersale-014",
            "chunk_id": "c-1",
            "chunk_index": 0,
            "content": "非质量问题退货运费由买家承担；质量问题退货运费由商家承担。详见售后政策第二节。",
            "chunk_metadata": {"title": "售后政策"},
        },
        {
            "doc_id": "policy-points-003",
            "chunk_id": "c-2",
            "chunk_index": 1,
            "content": "积分过期后不可恢复，每年 12 月 31 日 24 点清零。",
            "chunk_metadata": "",
        },
        {
            "doc_id": "policy-logistics-102",
            "chunk_id": "c-3",
            "chunk_index": 0,
            "content": "退货需先在订单页发起售后申请，审核通过后安排寄回。",
            "chunk_metadata": None,
        },
    ]


def test_cjk_query_ranks_matching_chunk_first():
    items = search_evidence(_rows(), "退货运费由谁承担", top_k=3)

    assert items, "命中词应至少返回一条"
    assert items[0]["doc_id"] == "policy-aftersale-014"
    assert items[0]["title"] == "售后政策"
    assert "退货运费" in items[0]["snippet"] or "运费" in items[0]["snippet"]
    assert items[0]["score"] >= items[-1]["score"]


def test_score_requires_term_hits_and_respects_top_k():
    items = search_evidence(_rows(), "积分 清零", top_k=1)

    assert len(items) == 1
    assert items[0]["doc_id"] == "policy-points-003"
    assert items[0]["chunk_id"] == "c-2"
    assert items[0]["title"] == ""  # chunk_metadata 非 dict 时标题为空而非报错

    none = search_evidence(_rows(), "完全无关的查询词组", top_k=5)
    # 「完全/无关/查询」等 2-gram 未命中任何切片
    assert none == []


def test_empty_query_returns_no_items():
    assert search_evidence(_rows(), "", top_k=5) == []
    assert search_evidence(_rows(), "   ", top_k=5) == []
