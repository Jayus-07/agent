"""QueryAnalyzer metadata_filter 契约测试。

P0-2（2026-10-19 治理改造）：doc_type/business_domain 不再进 hard filter
（猜测即过滤 = 漏召回根源，TD-13 与「广告法」域漂移事故同源），相关性
交由混合检索 + Rerank。本文件钉死新契约：猜测维度停发、保留维度（实体/
财务版本）语义不变、仍无裸 list。
"""
from backend.rag.retrieval.query_analyzer import ParsedQuery


def test_guessed_doc_type_no_longer_hard_filters():
    """猜测的 doc_type 不进 metadata_filter（P0-2 核心契约）。"""
    pq = ParsedQuery(doc_types=["policy", "financial"])
    f = pq.to_metadata_filter()
    assert "doc_type" not in f
    assert f == {}, "纯类型猜测查询不得产生任何硬过滤"


def test_guessed_business_domain_no_longer_hard_filters():
    """猜测的 business_domain 不进 metadata_filter。"""
    pq = ParsedQuery(domains=["customer", "order"])
    assert "business_domain" not in pq.to_metadata_filter()


def test_person_entity_still_filters():
    """人名/品牌实体（有入库字段且经索引交叉验证）保留硬过滤。"""
    pq = ParsedQuery(persons=["MeridiHome"])
    assert pq.to_metadata_filter()["person_names"] == "MeridiHome"


def test_financial_version_semantics_unchanged():
    """财务版本快照过滤语义不变：有报告期按报告期，有指标查最新。"""
    assert ParsedQuery(reporting_period="2026-Q3").to_metadata_filter()[
        "reporting_period"] == "2026-Q3"
    assert ParsedQuery(financial_metrics=["revenue"]).to_metadata_filter()[
        "is_latest"] is True


def test_no_bare_list_values_reach_chroma():
    """总断言：to_metadata_filter 输出不得含裸 list 值（Chroma where 拒收）。"""
    # 单实体取标量、多实体 $in 包装（2026-09-14 契约；persons 多值此前
    # 漏修输出裸 list，P0-2 顺手修复，此处钉死）
    pq = ParsedQuery(persons=["a", "b"])
    assert pq.to_metadata_filter()["person_names"] == {"$in": ["a", "b"]}
    # 重置为标量路径继续总断言
    pq = ParsedQuery(persons=["a"], doc_types=["x"], domains=["y"])
    f = pq.to_metadata_filter()
    for k, v in f.items():
        assert not isinstance(v, list), f"{k} 输出裸 list 值: {v!r}"
