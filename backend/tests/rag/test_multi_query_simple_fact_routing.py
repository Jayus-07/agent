"""R1-B：简单事实问句误触发 MultiQuery 的**真实差异**与回归护栏。

背景（PR #41 P1-D 复核修正）：基线 d1b9668 的兜底 multi_query._is_complex
本身就把「是多少」类简单事实豁免放在业务关键词之前，因此
「退款审核时间是多少？」在基线**并不会**误触发 MQ——原测试场景不成立。

真实缺陷（artifacts/rag-perf-r0r1/probe_r1b_delta.py 逐条取证）：旧兜底自持
一套业务关键词规则，与主分类器真实分叉，例如
  - 「审核时间是多久」：兜底判复杂 ↔ 主分类器判 hybrid；
  - 「差评怎么处理」：兜底判复杂 ↔ 主分类器判 hybrid。
修复方式：兜底不再自持规则，改为复用唯一判定入口；主入口抛错时保守
不改写（保双路召回，只跳过改写），不再出现两套结论。

本文件锁定：主路径/兜底一致性、强信号不可降级、Tier-1 短问语义不变。
"""
from __future__ import annotations

import pytest

from backend.rag.retrieval import hybrid as hybrid_module
from backend.rag.retrieval import multi_query as mq_mod
from backend.rag.retrieval.hybrid import _classify_query_tier, is_simple_fact_query


@pytest.fixture(autouse=True)
def _auto_mode(monkeypatch: pytest.MonkeyPatch):
    """钉回 adaptive 自动判型（防止开发机 env 钉死覆盖判定）。"""
    from backend.config import rag as rag_config

    monkeypatch.setattr(rag_config, "ADAPTIVE_RETRIEVAL_MODE", "auto")


PARITY_QUERIES = [
    "退款审核时间是多少？", "退款审核时间是多少", "退款的审核时间是多少",
    "审核时间", "退款多久到账", "库存周转率是多少", "库存周转率怎么算",
    "库存周转率多少算健康", "什么是库存周转率", "现货商品的发货时间是多久？",
    "退货政策是什么，和换货有什么区别？", "对比退款和退货的审核时间",
    "报销流程怎么走？", "订单号 A123456789 的状态", "差评怎么处理",
    "多少个工作日完成审核", "多少钱可以包邮", "哪个部门负责审核",
    "退款申请之后谁审核", "审核一下库存", "帮我分析一下退款审核流程",
    "退款和退货的审核时间分别是多少", "审核需要哪些材料、由谁审批、多久出结果",
    "库存周转率的计算公式和统计口径是什么，多久更新一次？",
]


class TestSimpleFactQueries:
    """简单 FAQ / 单事实问题：不触发改写，但仍走双路检索（不降级 BM25）。"""

    @pytest.mark.parametrize("query", [
        "退款审核时间是多少？",
        "退款审核时间是多少?",
        "退款审核时间是多少",
        "现货商品的发货时间是多久？",
        "发票要多久能开出来？",
    ])
    def test_simple_fact_not_multi_query(self, query):
        assert _classify_query_tier(query) in ("hybrid", "vector_only")
        use, reason = mq_mod.need_multi_query(query)
        assert use is False, f"{query!r} 不应触发改写，理由={reason}"

    @pytest.mark.parametrize("query", [
        "退款审核时间是多少？",
        "什么是库存周转率",
        "退款的到账时间是几天？",
    ])
    def test_simple_fact_gate_recognises_patterns(self, query):
        assert is_simple_fact_query(query) != ""

    @pytest.mark.parametrize("query", ["审核时间是多久", "退款要审核多久"])
    def test_old_fallback_keyword_misfire_is_gone(self, query):
        """旧兜底的真实误判：单子句简单事实 + 业务关键词被判复杂。

        修复后兜底与主分类器一致为「不改写」。主分类器的 tier 由既有
        Tier-1 语义决定（这两条无弱信号且很短 → vector_only，与改动前一致），
        本用例只断言"是否改写"这一点，不重定义 tier。
        """
        assert is_simple_fact_query(query) != ""
        assert _classify_query_tier(query) in ("hybrid", "vector_only")
        assert mq_mod._is_complex(query)[0] is False
        assert mq_mod.need_multi_query(query)[0] is False


class TestComplexQueriesStillRewrite:
    """真正复杂的问题必须继续触发 MultiQuery（不因本改动丢召回）。"""

    @pytest.mark.parametrize("query", [
        "对比退款和退货的审核时间差异",
        "退款怎么处理，以及审核流程都有哪些步骤？",
        "分析一下差评和投诉的处理流程，同时说明对考核的影响",
        "退款和退货的审核时间分别是多少",
        "审核需要哪些材料、由谁审批、多久出结果",
    ])
    def test_complex_still_multi_query(self, query):
        assert _classify_query_tier(query) == "hybrid_multi_query"
        use, _reason = mq_mod.need_multi_query(query)
        assert use is True

    def test_multi_clause_simple_fact_not_exempt(self):
        """多子句（并列诉求）即使含「是多少」也不豁免。"""
        use, _reason = mq_mod.need_multi_query("退款审核时间是多少？退货时间是多少？")
        assert use is True

    @pytest.mark.parametrize("query", [
        "对比一下退款和退货的审核时间是多少",
        "两种退款方式有什么区别，分别多久到账",
        "对比退款和退货的审核时间",
    ])
    def test_strong_signal_never_downgraded(self, query):
        """强信号恒为 MQ：简单事实豁免不得降级多意图/对比类问句。"""
        assert is_simple_fact_query(query) == ""
        assert _classify_query_tier(query) == "hybrid_multi_query"
        assert mq_mod._is_complex(query)[0] is True

    @pytest.mark.parametrize("query", [
        "订单号 A123456789 的状态",
        "「三坊七巷」的开放时间",
    ])
    def test_identifier_and_proper_noun_unchanged(self, query):
        """精确标识符 / 引号专名仍是 hybrid，不被简单事实规则吞掉。"""
        assert _classify_query_tier(query) == "hybrid"


class TestMultiSourceRouting:
    """多来源/多部门协作问题（2026-10-18）：证据天然分散在多份文档。

    真实失败 case：RC-024「采购新品上架需要哪些部门协作？」只走 hybrid，
    MultiQuery 未触发 → 只召回库存"入库→上架"单文档证据，缺供应商准入/
    AQL/CE-FCC。修复是**结构性**多来源信号，不是 case_id 特判。
    """

    @pytest.mark.parametrize("query", [
        "采购新品上架需要哪些部门协作？",
        "新品上架涉及哪些岗位配合？",
        "退款流程分别由哪些角色审批？",
        "供应商准入由哪些部门负责？",
        "跨境合规需要哪些部门协作完成？",
        "订单履约涉及哪些环节？",
    ])
    def test_multi_source_questions_use_multi_query(self, query):
        assert _classify_query_tier(query) == "hybrid_multi_query"
        assert mq_mod.need_multi_query(query)[0] is True

    @pytest.mark.parametrize("query", [
        "现货商品的发货时间是多久？",
        "退货政策是什么？",
        "报销流程怎么走？",
        "订单号 A123456789 的状态",
        "库存周转率怎么算",
        "七天无理由退货需要满足什么条件？",
    ])
    def test_single_source_questions_not_upgraded(self, query):
        assert _classify_query_tier(query) != "hybrid_multi_query"
        assert mq_mod.need_multi_query(query)[0] is False


class TestSingleRulesetConsistency:
    """P1-D 核心护栏：主分类器与兜底结论必须逐条一致（只有一份规则）。"""

    @pytest.mark.parametrize("query", PARITY_QUERIES)
    def test_fallback_matches_tier(self, query):
        tier_mq = _classify_query_tier(query) == "hybrid_multi_query"
        fallback_mq = mq_mod._is_complex(query)[0]
        assert tier_mq == fallback_mq, query

    def test_two_classifier_misfires_from_baseline_are_gone(self):
        """基线的两处分叉：兜底判 MQ、主分类器判非 MQ。"""
        for query in (
            "差评怎么处理",            # 基线兜底：业务关键词 → MQ
            "审核时间是多久",          # 基线兜底：业务关键词 → MQ
            "库存周转率怎么算",
            "库存周转率多少算健康",
        ):
            assert _classify_query_tier(query) != "hybrid_multi_query"
            assert mq_mod._is_complex(query)[0] is False, query

    def test_need_multi_query_falls_back_conservatively(self, monkeypatch):
        """主分类器抛错 → 兜底保守不改写（保双路召回，只跳过改写）。"""
        def _boom(_query):
            raise RuntimeError("classifier unavailable")

        monkeypatch.setattr(hybrid_module, "_classify_query_tier", _boom)
        use, reason = mq_mod.need_multi_query("对比退款和退货的审核时间")
        assert use is False and "classifier_unavailable" in reason
        use2, _ = mq_mod._is_complex("帮我分析一下退款审核流程")
        assert use2 is False

    def test_simple_fact_source_is_single(self):
        """单一事实来源：兜底使用的清单就是 hybrid 导出的清单。"""
        assert mq_mod.SIMPLE_FACT_PATTERNS is hybrid_module.SIMPLE_FACT_PATTERNS
        assert mq_mod.SIMPLE_PREFIXES is hybrid_module.SIMPLE_PREFIXES
