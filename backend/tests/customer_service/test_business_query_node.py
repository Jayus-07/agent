"""test_business_query_node.py — cs_business_query 节点集成测试"""
from unittest.mock import patch

from backend.customer_service.graph.nodes import cs_business_query

_EXEC_PATCH = "backend.sql.executor.execute_sql_struct"


def _make_state(intent="t_order_status", user_id="42", question="我的订单"):
    return {
        "question": question,
        "cs_context": {
            "authenticated_user_id": user_id,
            "cs_route": {
                "intent": intent,
                "route_path": "business_query",
                "domain": "TRANSACTION",
            },
        },
    }


class TestAuthenticationGate:

    def test_anonymous_user_gets_login_prompt(self):
        state = _make_state(user_id="anonymous")
        result = cs_business_query(state)
        assert "登录" in result["final_answer"]

    def test_missing_user_id_gets_login_prompt(self):
        state = {"question": "我的订单", "cs_context": {"cs_route": {"intent": "t_order_status"}}}
        result = cs_business_query(state)
        assert "登录" in result["final_answer"]


class TestOrderQueryDispatch:

    @patch(_EXEC_PATCH)
    def test_order_list_returns_formatted(self, mock_exec):
        from backend.sql.sql_result import SQLResult

        mock_exec.return_value = SQLResult.success(
            [{"id": 1, "order_no": "ORD-001", "total_amount": 99.9,
              "status": "shipped", "payment_status": "paid",
              "created_at": "2026-09-01"}],
            ["id", "order_no", "total_amount", "status", "payment_status", "created_at"],
            sql="test",
        )

        state = _make_state(intent="t_order_status")
        result = cs_business_query(state)
        assert "ORD-001" in result["final_answer"]
        assert "订单" in result["final_answer"]

    @patch(_EXEC_PATCH)
    def test_empty_order_list(self, mock_exec):
        from backend.sql.sql_result import SQLResult

        mock_exec.return_value = SQLResult(
            status="no_data", rows=[], columns=[], sql_text="test",
        )

        state = _make_state(intent="t_order_status")
        result = cs_business_query(state)
        assert "没有" in result["final_answer"] or "订单" in result["final_answer"]


class TestLogisticsDispatch:

    @patch(_EXEC_PATCH)
    def test_logistics_returns_tracking(self, mock_exec):
        from backend.sql.sql_result import SQLResult

        # Both OrderService (for _get_latest_order_id) and LogisticsService
        # call execute_sql_struct. Return shipped order for both.
        mock_exec.side_effect = [
            # OrderService._get_order_list (called by _get_latest_order_id)
            SQLResult.success(
                [{"id": 1, "order_no": "ORD-001", "total_amount": 99.9,
                  "status": "shipped", "payment_status": "paid",
                  "created_at": "2026-09-01"}],
                ["id", "order_no"], sql="test",
            ),
            # LogisticsService.query_logistics
            SQLResult.success(
                [{"id": 1, "order_no": "ORD-001", "status": "shipped",
                  "created_at": "2026-09-01"}],
                ["id", "order_no", "status", "created_at"], sql="test",
            ),
        ]

        state = _make_state(intent="t_logistics")
        result = cs_business_query(state)
        assert "物流" in result["final_answer"]


class TestOutputGuardIntegration:

    @patch(_EXEC_PATCH)
    def test_internal_info_filtered(self, mock_exec):
        from backend.sql.sql_result import SQLResult

        mock_exec.return_value = SQLResult(
            status="no_data", rows=[], columns=[], sql_text="test",
        )

        state = _make_state(intent="t_order_status")
        result = cs_business_query(state)
        assert "SELECT" not in result["final_answer"]
        assert ".py" not in result["final_answer"]


class TestErrorHandling:

    @patch(_EXEC_PATCH)
    def test_order_not_found_message(self, mock_exec):
        from backend.sql.sql_result import SQLResult

        mock_exec.return_value = SQLResult(
            status="no_data", rows=[], columns=[], sql_text="test",
        )

        state = _make_state(intent="t_order_status", question="查订单 ORD-999")
        result = cs_business_query(state)
        assert result["final_answer"]

    @patch(_EXEC_PATCH)
    def test_db_error_returns_generic_message(self, mock_exec):
        from backend.sql.sql_result import SQLResult

        mock_exec.return_value = SQLResult.failed(
            status="failed", error="connection lost",
        )

        state = _make_state(intent="t_order_status")
        result = cs_business_query(state)
        assert "繁忙" in result["final_answer"] or "重试" in result["final_answer"]


class TestQueryMeta:

    @patch(_EXEC_PATCH)
    def test_query_meta_recorded(self, mock_exec):
        from backend.sql.sql_result import SQLResult

        mock_exec.return_value = SQLResult.success(
            [{"id": 1, "order_no": "ORD-001", "total_amount": 99.9,
              "status": "shipped", "payment_status": "paid",
              "created_at": "2026-09-01"}],
            ["id", "order_no"], sql="test",
        )

        state = _make_state(intent="t_order_status")
        result = cs_business_query(state)
        meta = result["cs_context"].get("query_meta", {})
        assert meta["intent"] == "t_order_status"
        assert meta["user_id"] == "42"
