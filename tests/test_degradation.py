"""tests for multi_agent.degradation — 通用降级链"""

from multi_agent.degradation import (
    DEGRADATION_CHAIN,
    can_degrade,
    get_fallback_capability,
    MAX_DEGRADATION_PER_STEP,
)


def test_degradation_chain_structure():
    """降级链包含所有 capability"""
    assert "query_database" in DEGRADATION_CHAIN
    assert "search_knowledge" in DEGRADATION_CHAIN
    assert "generate_report" in DEGRADATION_CHAIN


def test_sql_fallback_to_rag():
    """SQL 空结果 → RAG 降级"""
    fb = get_fallback_capability("query_database")
    assert fb == "search_knowledge"


def test_rag_fallback_to_sql():
    """RAG 无结果 → SQL 降级（反向降级）"""
    fb = get_fallback_capability("search_knowledge")
    assert fb == "query_database"


def test_can_degrade_new_step():
    """未尝试过降级的步骤可以降级"""
    attempted = set()
    assert can_degrade("1", attempted) is True


def test_can_degrade_already_attempted():
    """已降级过的步骤不可再降级（防止循环）"""
    attempted = {"1_fallback"}
    assert can_degrade("1_fallback", attempted) is False


def test_get_fallback_unknown_capability():
    """未知 capability 返回 None"""
    assert get_fallback_capability("unknown_cap") is None


def test_max_degradation_per_step():
    """每个步骤最多降级 MAX_DEGRADATION_PER_STEP 次"""
    assert MAX_DEGRADATION_PER_STEP == 1
