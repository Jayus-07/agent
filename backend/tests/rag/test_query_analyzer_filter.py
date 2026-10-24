"""QueryAnalyzer metadata_filter 契约测试。

P0-2（2026-10-19 治理改造）：doc_type/business_domain 不再进 hard filter
（猜测即过滤 = 漏召回根源，TD-13 与「广告法」域漂移事故同源），相关性
交由混合检索 + Rerank。本文件钉死新契约：猜测维度停发、保留维度（财务
版本）语义不变、仍无裸 list。

2026-10-25：person_names 同理退出 hard filter（NER 猜测 + 双侧同源，
「信息安全管理制度」实测误拒案例见 query_analyzer 白名单注释）。
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


def test_person_entity_no_longer_hard_filters():
    """person_names 不进 hard filter（2026-10-25 契约，与 doc_types 同理）。

    persons 100% 来自查询侧 NER 猜测，双侧同源误判时硬过滤锁定噪声文档；
    实体相关性交由混合检索 + Rerank，Stage1 候选路径保留（D-13 验证 + 回退）。
    """
    pq = ParsedQuery(persons=["MeridiHome"])
    f = pq.to_metadata_filter()
    assert "person_names" not in f


def test_financial_version_semantics_unchanged():
    """财务版本快照过滤语义不变：有报告期按报告期，有指标查最新。"""
    assert ParsedQuery(reporting_period="2026-Q3").to_metadata_filter()[
        "reporting_period"] == "2026-Q3"
    assert ParsedQuery(financial_metrics=["revenue"]).to_metadata_filter()[
        "is_latest"] is True


def test_no_bare_list_values_reach_chroma():
    """总断言：to_metadata_filter 输出不得含裸 list 值（Chroma where 拒收）。"""
    pq = ParsedQuery(persons=["a", "b"], reporting_period="2026-Q1",
                     financial_metrics=["revenue"])
    f = pq.to_metadata_filter()
    for k, v in f.items():
        assert not isinstance(v, list), f"{k} 输出裸 list 值: {v!r}"
