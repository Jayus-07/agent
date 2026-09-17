"""§7 查询类型路由测试（R4-P3）。

覆盖：
  1. 分类规则：exact_id / table_value / multi_condition / cross_doc / faq
     （基准问题取自 backend/evaluation/datasets/rag/cases.jsonl 真实语料）
  2. 分类优先级：exact_id > multi_condition > cross_doc > table_value > faq
  3. 策略映射与未知类型兜底
  4. kill-switch（RAG_QUERY_ROUTER env，route() 每次调用读取）
  5. fallback hybrid 路径 RRF 加权生效（exact_id → bm25 命中文档胜出）
  6. enhanced 三路融合的权重语义（weight/(rrf_k+rank)）
"""
import pytest
from langchain_core.documents import Document
from types import SimpleNamespace

from backend.rag.retrieval.query_router import (
    QT_CROSS_DOC,
    QT_EXACT_ID,
    QT_FAQ,
    QT_MULTI_CONDITION,
    QT_TABLE_VALUE,
    classify_query_type,
    route,
    strategy_for,
)


# =====================================================
# 1. 分类规则（基准：评测集真实语料）
# =====================================================

class TestClassifyExactId:
    """精确编号：字母前缀-数字格式 + 显式编号标签。"""

    @pytest.mark.parametrize("query", [
        "报销单编号 XC-HT-2025-041 的报销上限是多少",
        "ZC-2021-118 这份制度文件讲了什么",
        "BX-2026-0207 的审批流程是什么",
        "订单号 A123456789 的物流信息",
    ])
    def test_exact_id_patterns(self, query):
        result = classify_query_type(query)
        assert result["query_type"] == QT_EXACT_ID
        assert result["signals"], "signals 应记录命中依据"

    def test_signals_capture_matched_text(self):
        # 命中文本取决于具体模式（\b[A-Z]{2,}[-_]\d{3,}\b 从 HT-2025 处命中）
        result = classify_query_type("报销单编号 XC-HT-2025-041 的上限")
        assert any("2025" in s for s in result["signals"])


class TestClassifyTableValue:
    """表格数值查询（评测集高频类）。"""

    @pytest.mark.parametrize("query", [
        "一线城市出差3晚的住宿报销上限是多少？",
        "采购合同总额是多少？尾款金额是多少？",
        "盘点差异率的要求是多少？",
        "系统灾备的RPO和RTO指标分别是多少？",
        "质检不合格率超过多少时甲方有权要求换货或退货？",
        "报销审批中5000元是一个怎样的分界点？",
    ])
    def test_table_value_patterns(self, query):
        assert classify_query_type(query)["query_type"] == QT_TABLE_VALUE


class TestClassifyMultiCondition:
    def test_simultaneous_conditions(self):
        """评测集语料：评分低于60分 + 同时出现安全事故。"""
        q = "供应商连续两季度评分低于60分，同时出现了安全事故，应如何处理？"
        assert classify_query_type(q)["query_type"] == QT_MULTI_CONDITION

    def test_condition_priority_over_table_value(self):
        """多条件优先于表格数值：多条件查询常含数字，不可误判为单点数值。"""
        q = "同时满足金额和时限要求时，报销金额是多少"
        assert classify_query_type(q)["query_type"] == QT_MULTI_CONDITION


class TestClassifyCrossDoc:
    @pytest.mark.parametrize("query", [
        "海运拼箱、空运快线、专线快递三种物流方式有什么区别？",
        "采购审批和报销审批的权限分级有什么不同？",
    ])
    def test_cross_doc_patterns(self, query):
        assert classify_query_type(query)["query_type"] == QT_CROSS_DOC


class TestClassifyFaq:
    """FAQ 兜底：评测集简单问答不误判为其他类型。"""

    @pytest.mark.parametrize("query", [
        "现货商品的发货时间是多久？",
        "平台支持哪些支付方式？",
        "换货流程是怎样的？",
        "客服的工作时间是什么？",
    ])
    def test_faq_fallback(self, query):
        assert classify_query_type(query)["query_type"] == QT_FAQ

    def test_empty_query_is_faq(self):
        assert classify_query_type("")["query_type"] == QT_FAQ
        assert classify_query_type(None)["query_type"] == QT_FAQ


# =====================================================
# 2. 策略映射
# =====================================================

class TestStrategy:
    def test_exact_id_bm25_priority(self):
        """任务书 §7：精确编号 → BM25 优先。"""
        s = strategy_for(QT_EXACT_ID)
        assert s["bm25_weight"] > s["vector_weight"]
        assert s["bm25_weight"] >= 1.5

    def test_all_types_have_strategy(self):
        for qt in (QT_EXACT_ID, QT_TABLE_VALUE, QT_MULTI_CONDITION,
                   QT_CROSS_DOC, QT_FAQ):
            s = strategy_for(qt)
            assert "vector_weight" in s and "bm25_weight" in s

    def test_unknown_type_neutral(self):
        s = strategy_for("no_such_type")
        assert s == {"vector_weight": 1.0, "bm25_weight": 1.0}

    def test_faq_neutral(self):
        assert strategy_for(QT_FAQ) == {"vector_weight": 1.0, "bm25_weight": 1.0}

    def test_weights_are_conservative(self):
        """非 exact_id 类型权重收敛在 [0.9, 1.15]（回归风险控制）。"""
        for qt in (QT_TABLE_VALUE, QT_MULTI_CONDITION, QT_CROSS_DOC, QT_FAQ):
            s = strategy_for(qt)
            assert 0.9 <= s["vector_weight"] <= 1.15
            assert 0.9 <= s["bm25_weight"] <= 1.15


# =====================================================
# 3. 路由入口 + kill-switch
# =====================================================

class TestRoute:
    def test_route_combines_classify_and_strategy(self):
        r = route("报销单编号 XC-HT-2025-041 的上限")
        assert r["query_type"] == QT_EXACT_ID
        assert r["bm25_weight"] >= 1.5
        assert r["enabled"] is True

    def test_kill_switch_disables(self, monkeypatch):
        """RAG_QUERY_ROUTER=off → disabled + 中性权重（行为与 P3 前一致）。"""
        monkeypatch.setenv("RAG_QUERY_ROUTER", "off")
        r = route("报销单编号 XC-HT-2025-041 的上限")
        assert r["query_type"] == "disabled"
        assert r["vector_weight"] == 1.0 and r["bm25_weight"] == 1.0
        assert r["enabled"] is False

    def test_switch_read_per_call(self, monkeypatch):
        """kill-switch 每次调用读取 env：运维不重启即可关闭/恢复。"""
        q = "住宿报销上限是多少"
        assert route(q)["enabled"] is True
        monkeypatch.setenv("RAG_QUERY_ROUTER", "0")
        assert route(q)["enabled"] is False
        monkeypatch.setenv("RAG_QUERY_ROUTER", "true")
        assert route(q)["enabled"] is True


# =====================================================
# 4. fallback hybrid 路径 RRF 加权
# =====================================================

def _make_retrievers(vector_doc, bm25_doc):
    v = SimpleNamespace(retrieve=lambda q, k=5, doc_ids=None,
                        metadata_filter=None, expanded_queries=None:
                        [vector_doc] if vector_doc else [])
    b = SimpleNamespace(invoke=lambda q: [bm25_doc] if bm25_doc else [])
    return v, b


@pytest.fixture(autouse=True)
def _disable_enhanced_path(monkeypatch):
    """集成测试走 fallback hybrid 逻辑（禁用 enhanced 路由）。"""
    monkeypatch.setattr("backend.config.rag.ADAPTIVE_THRESHOLD_ENABLED", False)
    monkeypatch.setattr("backend.config.rag.CONFIDENCE_AGGREGATOR_ENABLED", False)


class TestHybridWeightedRRF:
    def test_exact_id_boosts_bm25_doc(self):
        """exact_id 查询（bw=1.5 > vw=0.85）：仅 BM25 命中的文档应胜出。"""
        from backend.rag.retrieval.hybrid import hybrid_retrieve

        vector_doc = Document(page_content="向量命中", metadata={
            "chunk_id": "cv", "doc_id": "d1"})
        bm25_doc = Document(page_content="XC-HT-2025-041 报销标准", metadata={
            "chunk_id": "cb", "doc_id": "d2"})
        v, b = _make_retrievers(vector_doc, bm25_doc)

        merged = hybrid_retrieve("报销单编号 XC-HT-2025-041 的上限是多少", v, b, k=5)
        assert len(merged) == 2
        assert merged[0].metadata["chunk_id"] == "cb", \
            "exact_id 加权后 BM25 命中文档应排第一"
        scores = {d.metadata["chunk_id"]: d.metadata["rrf_score"] for d in merged}
        assert scores["cb"] == pytest.approx(1.5 / (60 + 1), abs=1e-4)
        assert scores["cv"] == pytest.approx(0.85 / (60 + 1), abs=1e-4)

    def test_kill_switch_keeps_neutral_rrf_balance(self, monkeypatch):
        """kill-switch 关闭：中性权重下同 rank 双侧命中 RRF 分数相等
        （验证路由关闭时行为与 P3 之前完全一致）。

        注：faq 查询 tier=vector_only 不走融合路径，故此处用 exact_id
        查询 + 关闭路由来构造"中性权重进入 RRF"的场景。
        """
        from backend.rag.retrieval.hybrid import hybrid_retrieve

        vector_doc = Document(page_content="向量命中", metadata={
            "chunk_id": "cv", "doc_id": "d1"})
        bm25_doc = Document(page_content="XC-HT-2025-041 报销", metadata={
            "chunk_id": "cb", "doc_id": "d2"})
        v, b = _make_retrievers(vector_doc, bm25_doc)
        monkeypatch.setenv("RAG_QUERY_ROUTER", "off")

        merged = hybrid_retrieve("报销单编号 XC-HT-2025-041 的上限是多少", v, b, k=5)
        scores = {d.metadata["chunk_id"]: d.metadata["rrf_score"] for d in merged}
        assert scores["cv"] == pytest.approx(scores["cb"])
        assert scores["cv"] == pytest.approx(1 / (60 + 1), abs=1e-4)

    def test_metrics_and_trace_carry_query_type(self):
        """可观测：span metrics 带 query_type，trace 事件带 query_route。"""
        from backend.observability.tracer import trace_collector
        from backend.rag.retrieval.hybrid import hybrid_retrieve

        vector_doc = Document(page_content="v", metadata={
            "chunk_id": "cv", "doc_id": "d1"})
        bm25_doc = Document(page_content="XC-HT-2025-041", metadata={
            "chunk_id": "cb", "doc_id": "d2"})
        v, b = _make_retrievers(vector_doc, bm25_doc)

        trace = trace_collector.start("query-route-trace", session_id="t1")
        try:
            merged = hybrid_retrieve("报销单编号 XC-HT-2025-041 的上限", v, b, k=5)
        finally:
            trace_collector.finish(trace, answer="", model="", total_ms=0)

        assert merged, "应有融合结果"
        span = next((s for s in trace.spans
                     if s.span_id == "hybrid_retrieval"), None)
        assert span is not None
        assert span.metrics.get("query_type") == QT_EXACT_ID
        route_event = next((e for e in span.events
                            if e.get("name") == "rrf_fusion"
                            and "query_route" in (e.get("attributes") or {})), None)
        assert route_event is not None
        qr = route_event["attributes"]["query_route"]
        assert qr["query_type"] == QT_EXACT_ID
        assert qr["bm25_weight"] >= 1.5


# =====================================================
# 5. enhanced 三路融合权重语义
# =====================================================

class TestEnhancedFusionWeights:
    def test_ultimate_rrf_respects_path_weights(self):
        """_ultimate_rrf_fusion 按 weight/(rrf_k+rank) 累加：高权重路径胜出。"""
        from backend.rag.retrieval.enhanced_hybrid_retrieval import (
            _ultimate_rrf_fusion,
        )

        dense_doc = Document(page_content="dense", metadata={
            "chunk_id": "cd", "doc_id": "d1"})
        sparse_doc = Document(page_content="sparse", metadata={
            "chunk_id": "cs", "doc_id": "d2"})

        # 模拟 exact_id 策略：dense 3.0*0.85=2.55，sparse 1.0*1.5=1.5
        # 单条 rank1：dense = 2.55/61 > sparse = 1.5/61 → dense 仍第一
        merged = _ultimate_rrf_fusion(
            [([dense_doc], 3.0 * 0.85), ([sparse_doc], 1.0 * 1.5)], 60, 2)
        assert merged[0].metadata["chunk_id"] == "cd"
        scores = {d.metadata["chunk_id"]: d.metadata["rrf_score"] for d in merged}
        assert scores["cd"] == pytest.approx(2.55 / 61, abs=1e-4)

        # 反转：若 dense 被压得更低（模拟未来更强策略）→ sparse 胜出
        merged2 = _ultimate_rrf_fusion(
            [([dense_doc], 1.0), ([sparse_doc], 2.0)], 60, 2)
        assert merged2[0].metadata["chunk_id"] == "cs"
