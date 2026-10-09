"""QueryExpert 条件计划查询输出权威业务事实的测试。"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from backend.customer_service.experts.query import execute_query


def _state(current_task=None):
    return {
        "user_id": "u1", "tenant_id": "tenant-1", "session_id": "s1",
        "current_task": current_task or {
            "task_id": "q1", "capability": "query_logistics", "depends_on": [],
        },
        "task_plan": {
            "schema_version": 1, "primary_intent": "t_logistics", "tasks": [],
        },
        "task_results": [],
    }


def _order(order_no="MO-001", status="paid"):
    return {
        "id": "order-1", "order_no": order_no, "status": status,
        "total_amount": 99.0,
    }


class TestQueryTaskFacts:
    @patch("backend.customer_service.security.permission.PermissionChecker.validate_user_identity", return_value="u1")
    @patch("backend.customer_service.service.logistics_service.get_logistics_service")
    @patch("backend.customer_service.service.order_service.get_order_service")
    @patch("backend.customer_service.experts.query._llm_decompose_intents")
    @patch("backend.config.customer_service.CS_BUSINESS_GATEWAY_MODE", "sandbox")
    def test_logistics_returns_sandbox_facts_without_second_decomposition(
        self, decompose, get_orders, get_logistics, _identity,
    ):
        get_orders.return_value.query_orders.return_value = SimpleNamespace(
            orders=[_order()],
        )
        get_logistics.return_value.query_logistics.return_value = SimpleNamespace(
            order_id="order-1", order_no="MO-001", status="paid",
            status_display="已支付，待发货", tracking_info="尚未发货",
            trace_events=[], estimated_delivery=None,
        )

        result = execute_query(
            "查下物流，如果没发货就申请退款",
            {"intent": "t_logistics", "route_path": "business_query"},
            _state(),
        )

        decompose.assert_not_called()
        task = result["data"]["task_result"]
        assert task["task_id"] == "q1"
        assert task["status"] == "success"
        assert task["facts"]["shipping_status"] == "not_shipped"
        assert task["facts"]["order_count"] == 1
        assert task["facts"]["order_id"] == "order-1"
        assert task["source"] == "sandbox_logistics_service"

    @patch("backend.customer_service.security.permission.PermissionChecker.validate_user_identity", return_value="u1")
    @patch("backend.customer_service.service.logistics_service.get_logistics_service")
    @patch("backend.customer_service.service.order_service.get_order_service")
    def test_multiple_orders_require_clarification_without_latest_fallback(
        self, get_orders, get_logistics, _identity,
    ):
        get_orders.return_value.query_orders.return_value = SimpleNamespace(
            orders=[_order("MO-001"), _order("MO-002")],
        )

        result = execute_query(
            "查下物流，如果没发货就申请退款",
            {"intent": "t_logistics"}, _state(),
        )

        get_logistics.return_value.query_logistics.assert_not_called()
        task = result["data"]["task_result"]
        assert task["status"] == "needs_clarification"
        assert task["facts"]["order_count"] == 2
        assert "选择" in result["response_draft"]

    @patch("backend.customer_service.security.permission.PermissionChecker.validate_user_identity", return_value="u1")
    @patch("backend.customer_service.service.logistics_service.get_logistics_service")
    @patch("backend.customer_service.service.order_service.get_order_service")
    def test_unknown_logistics_status_is_not_coerced_to_not_shipped(
        self, get_orders, get_logistics, _identity,
    ):
        get_orders.return_value.query_orders.return_value = SimpleNamespace(orders=[_order()])
        get_logistics.return_value.query_logistics.return_value = SimpleNamespace(
            order_id="order-1", order_no="MO-001", status="provider_new_status",
            status_display="状态未知", tracking_info="", trace_events=[],
        )

        result = execute_query(
            "查下物流，如果没发货就申请退款",
            {"intent": "t_logistics"}, _state(),
        )

        assert result["data"]["task_result"]["facts"]["shipping_status"] == "unknown"

    @patch("backend.customer_service.security.permission.PermissionChecker.validate_user_identity", return_value="u1")
    @patch("backend.customer_service.service.order_service.get_order_service")
    def test_order_service_failure_returns_failed_task_result(self, get_orders, _identity):
        from backend.customer_service.errors import DatabaseError

        get_orders.return_value.query_orders.side_effect = DatabaseError("offline")
        result = execute_query(
            "查下物流，如果没发货就申请退款",
            {"intent": "t_logistics"}, _state(),
        )

        task = result["data"]["task_result"]
        assert task["status"] == "failed"
        assert task["error_type"] == "provider_error"
        assert task["facts"] == {}
