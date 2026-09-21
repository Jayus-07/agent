"""test_query_understanding.py — QueryRouter 统一问题理解（治理改造 2026-09-22）。

纯函数测试：零 IO / 零 LLM。用例对齐用户规格 §23 的三类预期：
FAQ（RAG 单步）、SQL 单步、混合任务（SQL+RAG → Planner）。
"""
from __future__ import annotations

from backend.orchestration.router.types import CapabilityScore, ExecutionMode, RouteDecision


def _decision(mode: ExecutionMode, caps: list[tuple[str, float]], conf: float):
    return RouteDecision(
        execution_mode=mode,
        candidates=[CapabilityScore(name=n, score=s) for n, s in caps],
        confidence=conf,
        reason="test",
    )


class TestFaq:
    def test_faq_knowledge_query(self):
        """「公司的年假规则是什么？」→ knowledge_query / need_rag / 不进 Planner。"""
        d = _decision(ExecutionMode.DIRECT, [("rag.search", 0.9)], 0.9)
        u = understand = None
        from backend.orchestration.router.query_understanding import understand_query

        u = understand_query("公司的年假规则是什么？", d)
        assert u["intent"] == "knowledge_query"
        assert u["need_rag"] is True
        assert u["need_sql"] is False
        assert u["need_planner"] is False
        assert u["downgrade"] is None  # 本来就是 direct，无需降级

    def test_faq_plan_mode_downgrades(self):
        """同问题被路由丢给 plan（候选存在）→ 理解层建议降级 direct。"""
        d = _decision(ExecutionMode.PLAN, [("rag.search", 0.55)], 0.6)
        from backend.orchestration.router.query_understanding import understand_query

        u = understand_query("公司的年假规则是什么？", d)
        assert u["need_planner"] is False
        assert u["downgrade"] == {"to": "direct", "capability": "rag.search"}


class TestSql:
    def test_single_fact_sql_no_planner(self):
        """「SKU-A102 当前库存多少？」→ need_sql / 事实查询 / 不进 Planner。"""
        d = _decision(ExecutionMode.DIRECT, [("sql.query", 0.9)], 0.9)
        from backend.orchestration.router.query_understanding import understand_query

        u = understand_query("SKU-A102 当前库存多少？", d)
        assert u["need_sql"] is True
        assert u["query_type"] == "fact_lookup"
        assert u["need_planner"] is False
        assert u["entities"].get("sku") == "SKU-A102"

    def test_sql_no_candidates_falls_back_low_confidence(self):
        """无候选（规则/向量全 miss）→ 低置信，不误判 need_planner。"""
        from backend.orchestration.router.query_understanding import understand_query

        u = understand_query("今天天气不错", None)
        assert u["confidence"] <= 0.5
        assert u["need_planner"] is False

    def test_faq_keyword_fallback_without_decision(self):
        """路由无候选时 Level 1 关键词兜底：FAQ 仍推断出 need_rag。"""
        from backend.orchestration.router.query_understanding import understand_query

        u = understand_query("公司的年假规则是什么？", None)
        assert u["need_rag"] is True
        assert u["need_sql"] is False
        assert u["need_planner"] is False
        assert u["decision_layer"] == "rules_only"
        assert u["confidence"] >= 0.6


class TestComposite:
    def test_mixed_sql_rag_enters_planner(self):
        """「查库存并根据补货制度判断要不要补货」→ SQL+RAG → need_planner。"""
        d = _decision(ExecutionMode.PLAN, [("sql.query", 0.7), ("rag.search", 0.5)], 0.65)
        from backend.orchestration.router.query_understanding import understand_query

        q = "帮我看看 SKU-A102 最近一个月库存情况，并根据库存管理制度判断是否需要补货。"
        u = understand_query(q, d)
        assert u["need_sql"] is True
        assert u["need_rag"] is True
        assert u["need_planner"] is True
        assert u["downgrade"] is None
        assert u["complexity"] == "complex"
        assert u["time_range"] is not None

    def test_composite_wording_alone_keeps_planner(self):
        """组合措辞信号（根据…判断）即使路由候选只有一个也保留 Planner。"""
        d = _decision(ExecutionMode.PLAN, [("sql.query", 0.6)], 0.62)
        from backend.orchestration.router.query_understanding import understand_query

        u = understand_query("查一下 SKU-A102 的库存，再根据退货制度评估风险", d)
        assert u["need_planner"] is True


class TestLevels:
    def test_decision_layer_rule(self):
        from backend.orchestration.router.query_understanding import understand_query

        d = _decision(ExecutionMode.DIRECT, [("rag.search", 0.9)], 0.85)
        u = understand_query("退款流程是什么", d)
        assert u["decision_layer"] == "rule"

    def test_decision_layer_llm_fallback(self):
        from backend.orchestration.router.query_understanding import understand_query

        d = _decision(ExecutionMode.PLAN, [("rag.search", 0.4)], 0.3)
        u = understand_query("随便聊聊最近的市场情况", d)
        assert u["decision_layer"] == "router_llm"

    def test_smalltalk(self):
        from backend.orchestration.router.query_understanding import understand_query

        u = understand_query("你好", None)
        assert u["intent"] == "general_chat"
        assert u["need_planner"] is False
