"""metadata filter 白名单（2026-09-23 D1-4 回归）。

查询侧曾构造入库元数据从不存在的幽灵键（time_start/time_end/organization）
作为 hard filter → containment/匹配恒假 →「2025年的库存政策」「Amazon 的
退货政策」类查询必然 0 召回假拒答，且 Stage1 放宽阶梯不剥这些键。锁定：
幽灵键永不出现在 to_metadata_filter 产物；白名单键（与入库字段一一对应）
继续生效。
"""
from backend.rag.retrieval.query_analyzer import (
    RETRIEVAL_FILTERABLE_METADATA_FIELDS,
    ParsedQuery,
    QueryAnalyzer,
)


def test_whitelist_matches_actually_indexed_fields():
    """白名单必须与入库元数据真实写入字段一致（metadata.py/chunking.py/indexer.py）。"""
    assert RETRIEVAL_FILTERABLE_METADATA_FIELDS == frozenset({
        "person_names", "doc_type", "business_domain",
        "reporting_period", "is_latest",
    })


def test_time_range_never_enters_hard_filter():
    pq = ParsedQuery(original="2025年的库存政策",
                     time_range_start="2025-01-01",
                     time_range_end="2025-12-31")
    f = pq.to_metadata_filter()
    assert "time_start" not in f
    assert "time_end" not in f


def test_organization_never_enters_hard_filter():
    pq = ParsedQuery(original="Amazon 的退货政策", organizations=["Amazon"])
    f = pq.to_metadata_filter()
    assert "organization" not in f


def test_whitelisted_fields_still_filter():
    pq = ParsedQuery(original="q", persons=["Anker"], doc_types=["policy"],
                     domains=["order"], reporting_period="2026-Q3")
    f = pq.to_metadata_filter()
    assert f["person_names"] == "Anker"
    assert f["doc_type"] == "policy"
    assert f["business_domain"] == "order"
    assert f["reporting_period"] == "2026-Q3"


def test_financial_metrics_fallback_to_latest():
    pq = ParsedQuery(original="毛利率多少", financial_metrics=["gross_margin"])
    assert pq.to_metadata_filter().get("is_latest") is True


def _filter_for(query: str) -> dict:
    return QueryAnalyzer().analyze(query).to_metadata_filter()


def test_year_query_produces_ghost_free_filter():
    """端到端（纯规则）：带年份 query 的过滤器不含任何幽灵键。"""
    f = _filter_for("2025年的库存政策是什么")
    assert not ({"time_start", "time_end", "organization"} & set(f))


def test_time_range_query_produces_ghost_free_filter():
    f = _filter_for("2025年1月到6月的销售政策")
    assert not ({"time_start", "time_end", "organization"} & set(f))


def test_organization_query_produces_ghost_free_filter():
    f = _filter_for("Amazon 平台的退货政策有哪些")
    assert not ({"time_start", "time_end", "organization"} & set(f))


def test_plain_query_filter_remains_empty_or_whitelisted():
    f = _filter_for("退货政策 general 说明")
    assert not ({"time_start", "time_end", "organization"} & set(f))
    assert set(f) <= RETRIEVAL_FILTERABLE_METADATA_FIELDS
