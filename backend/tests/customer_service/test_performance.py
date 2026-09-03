"""test_performance.py — Phase 6 CS 节点性能基准测试

确保各节点响应时间达标。
每个测试运行多次迭代，计算 p95 延迟并与阈值比较。
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

_KNOWLEDGE = "backend.customer_service.knowledge.get_knowledge_service"
_GUARD = "backend.customer_service.security.output_guard.get_output_guard"
_HANDOFF_STORE = "backend.customer_service.handoff_store.get_handoff_store"
_EXEC = "backend.sql.executor.execute_sql_struct"

ITERATIONS = 100


def _p95(latencies: list[float]) -> float:
    """计算 p95 延迟（毫秒）。"""
    sorted_lat = sorted(latencies)
    idx = int(len(sorted_lat) * 0.95)
    return sorted_lat[min(idx, len(sorted_lat) - 1)] * 1000


class TestCSRouterPerformance:
    """CS Router 延迟基准"""

    def test_router_latency_p95_under_100ms(self):
        from backend.customer_service.router.cs_router import CSRouter
        from backend.customer_service.router.types import CSDetection

        router = CSRouter()
        detection = CSDetection(is_cs=True, rule_hits=["keyword"], rule_score=0.9)

        latencies = []
        for _ in range(ITERATIONS):
            t0 = time.perf_counter()
            router.route("退货政策是什么", detection)
            latencies.append(time.perf_counter() - t0)

        p95_ms = _p95(latencies)
        assert p95_ms < 100, f"CS Router p95={p95_ms:.1f}ms > 100ms"


class TestKnowledgeNodePerformance:
    """知识问答节点延迟基准（含 RAG 检索 mock）"""

    @patch(_KNOWLEDGE)
    def test_knowledge_node_latency_p95_under_500ms(self, mock_knowledge_fn):
        from backend.customer_service.graph.nodes import cs_knowledge_node
        from backend.customer_service.knowledge.answer_decision import Decision
        from backend.customer_service.knowledge.service import CSKnowledgeResult

        mock_service = MagicMock()
        mock_service.answer.return_value = CSKnowledgeResult(
            answer="退货政策：7天无理由退货",
            decision=Decision.ANSWER,
            confidence=0.95,
            kb_ids=["cs_faq"],
        )
        mock_knowledge_fn.return_value = mock_service

        state = {
            "question": "退货政策是什么",
            "cs_context": {"cs_route": {"intent": "k_faq", "kb_ids": ["cs_faq"]}},
        }

        latencies = []
        for _ in range(ITERATIONS):
            t0 = time.perf_counter()
            cs_knowledge_node(state)
            latencies.append(time.perf_counter() - t0)

        p95_ms = _p95(latencies)
        assert p95_ms < 500, f"Knowledge node p95={p95_ms:.1f}ms > 500ms"


class TestBusinessQueryNodePerformance:
    """业务查询节点延迟基准"""

    @patch(_EXEC)
    def test_business_query_latency_p95_under_200ms(self, mock_exec):
        from backend.customer_service.graph.nodes import cs_business_query
        from backend.sql.sql_result import SQLResult

        mock_exec.return_value = SQLResult.success(
            [{"id": 1, "order_no": "ORD-001", "total_amount": 99.9,
              "status": "shipped", "payment_status": "paid",
              "created_at": "2026-09-01"}],
            ["id", "order_no", "total_amount", "status", "payment_status", "created_at"],
            sql="test",
        )

        state = {
            "question": "我的订单",
            "cs_context": {
                "authenticated_user_id": "42",
                "cs_route": {"intent": "t_order_status", "route_path": "business_query"},
            },
        }

        latencies = []
        for _ in range(ITERATIONS):
            t0 = time.perf_counter()
            cs_business_query(state)
            latencies.append(time.perf_counter() - t0)

        p95_ms = _p95(latencies)
        assert p95_ms < 200, f"Business query node p95={p95_ms:.1f}ms > 200ms"


class TestComplaintNodePerformance:
    """投诉处理节点延迟基准（不含 LLM）"""

    @patch(_HANDOFF_STORE)
    @patch(_GUARD)
    @patch("backend.customer_service.service.complaint_service.get_complaint_service")
    def test_complaint_node_latency_p95_under_100ms(
        self, mock_svc_fn, mock_guard_fn, mock_store_fn
    ):
        from backend.customer_service.graph.nodes import cs_complaint
        from backend.customer_service.service.complaint_service import (
            ComplaintDetection,
            ComplaintTicket,
        )

        mock_service = MagicMock()
        mock_service.detect.return_value = ComplaintDetection(
            is_complaint=True, severity="low", matched_patterns=["差"]
        )
        ticket = ComplaintTicket(
            ticket_id="C-001", user_id="u1", conversation_id="c1",
            severity="low", summary="test",
        )
        mock_service.create_ticket.return_value = ticket
        mock_service.simulate_execute.return_value = {}
        mock_service.build_comfort_response.return_value = "抱歉"
        mock_svc_fn.return_value = mock_service

        mock_guard_fn.return_value = MagicMock(
            check=MagicMock(return_value=MagicMock(text="ok"))
        )
        mock_store_fn.return_value = MagicMock()

        state = {
            "question": "服务很差",
            "cs_context": {
                "authenticated_user_id": "u1",
                "session_id": "s1",
                "conversation_id": "c1",
            },
            "cs_audit_entries": [],
        }

        latencies = []
        for _ in range(ITERATIONS):
            t0 = time.perf_counter()
            cs_complaint(state)
            latencies.append(time.perf_counter() - t0)

        p95_ms = _p95(latencies)
        assert p95_ms < 100, f"Complaint node p95={p95_ms:.1f}ms > 100ms"


class TestHandoffNodePerformance:
    """转接节点延迟基准 — 纯状态转换"""

    @patch(_HANDOFF_STORE)
    @patch(_GUARD)
    def test_handoff_node_latency_p95_under_50ms(self, mock_guard_fn, mock_store_fn):
        from backend.customer_service.graph.nodes import cs_handoff

        mock_store = MagicMock()
        mock_store.load.return_value = None
        mock_store_fn.return_value = mock_store
        mock_guard_fn.return_value = MagicMock(
            check=MagicMock(return_value=MagicMock(text="ok"))
        )

        state = {
            "question": "帮我转人工",
            "cs_context": {
                "authenticated_user_id": "u1",
                "session_id": "s1",
                "conversation_id": "c1",
            },
            "cs_audit_entries": [],
        }

        latencies = []
        for _ in range(ITERATIONS):
            t0 = time.perf_counter()
            cs_handoff(state)
            latencies.append(time.perf_counter() - t0)

        p95_ms = _p95(latencies)
        assert p95_ms < 50, f"Handoff node p95={p95_ms:.1f}ms > 50ms"


class TestInputGuardPerformance:
    """输入安全检查延迟基准 — 纯正则"""

    def test_input_guard_latency_p95_under_10ms(self):
        from backend.customer_service.security.input_guard import get_cs_input_guard

        guard = get_cs_input_guard()

        latencies = []
        for _ in range(ITERATIONS):
            t0 = time.perf_counter()
            guard.check("正常的用户问题，退货政策是什么")
            latencies.append(time.perf_counter() - t0)

        p95_ms = _p95(latencies)
        assert p95_ms < 10, f"Input guard p95={p95_ms:.1f}ms > 10ms"
