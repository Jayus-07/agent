"""test_order_service.py — OrderService 单元测试 (mock DB)"""
from unittest.mock import patch

import pytest

from backend.customer_service.errors import (
    DatabaseError,
    OrderNotFoundError,
    ValidationError,
)
from backend.customer_service.service.order_service import OrderService
from backend.sql.sql_result import SQLResult

_EXEC_PATCH = "backend.sql.executor.execute_sql_struct"


def _make_sql_result(rows, status="success"):
    cols = list(rows[0].keys()) if rows else []
    if status == "success" and rows:
        return SQLResult.success(rows, cols, sql="test")
    if status == "success" and not rows:
        return SQLResult(rows=[], columns=cols, sql_text="test", status="no_data")
    return SQLResult.failed(status=status, error="mock error", sql="test")


@pytest.fixture
def service():
    return OrderService()


class TestQueryOrdersList:

    @patch(_EXEC_PATCH)
    def test_list_returns_orders(self, mock_exec, service):
        mock_exec.return_value = _make_sql_result([
            {"id": 1, "order_no": "ORD-001", "total_amount": 99.9,
             "status": "shipped", "payment_status": "paid", "created_at": "2026-09-01"},
            {"id": 2, "order_no": "ORD-002", "total_amount": 50.0,
             "status": "completed", "payment_status": "paid", "created_at": "2026-08-15"},
        ])
        result = service.query_orders(user_id="42")
        assert result.total_count == 2
        assert result.query_type == "list"
        mock_exec.assert_called_once()

    @patch(_EXEC_PATCH)
    def test_list_empty(self, mock_exec, service):
        mock_exec.return_value = _make_sql_result([])
        result = service.query_orders(user_id="42")
        assert result.total_count == 0
        assert result.orders == []

    @patch(_EXEC_PATCH)
    def test_list_db_error(self, mock_exec, service):
        mock_exec.return_value = SQLResult.failed(
            status="failed", error="connection lost"
        )
        with pytest.raises(DatabaseError):
            service.query_orders(user_id="42")


class TestQueryOrdersDetail:

    @patch(_EXEC_PATCH)
    def test_detail_found(self, mock_exec, service):
        mock_exec.return_value = _make_sql_result([
            {"id": 1, "order_no": "ORD-001", "customer_id": "42",
             "total_amount": 99.9, "status": "shipped",
             "payment_status": "paid", "created_at": "2026-09-01"},
        ])
        result = service.query_orders(user_id="42", order_id="1", query_type="detail")
        assert result.total_count == 1
        assert result.query_type == "detail"

    @patch(_EXEC_PATCH)
    def test_detail_not_found(self, mock_exec, service):
        mock_exec.return_value = _make_sql_result([])
        with pytest.raises(OrderNotFoundError):
            service.query_orders(user_id="42", order_id="999", query_type="detail")

    def test_detail_without_order_id_raises(self, service):
        with pytest.raises(ValidationError):
            service.query_orders(user_id="42", query_type="detail")

    def test_detail_invalid_order_id(self, service):
        with pytest.raises(ValidationError):
            service.query_orders(user_id="42", order_id="1; DROP TABLE", query_type="detail")


class TestQueryOrderItems:

    @patch(_EXEC_PATCH)
    def test_items_returned(self, mock_exec, service):
        mock_exec.side_effect = [
            _make_sql_result([
                {"id": 1, "order_no": "ORD-001", "customer_id": "42",
                 "total_amount": 99.9, "status": "shipped",
                 "payment_status": "paid", "created_at": "2026-09-01"},
            ]),
            _make_sql_result([
                {"id": 10, "product_id": 5, "quantity": 2, "price": 49.95},
                {"id": 11, "product_id": 8, "quantity": 1, "price": 0.0},
            ]),
        ]
        items = service.query_order_items(user_id="42", order_id="1")
        assert len(items) == 2
        assert mock_exec.call_count == 2

    @patch(_EXEC_PATCH)
    def test_items_order_not_found(self, mock_exec, service):
        mock_exec.return_value = _make_sql_result([])
        with pytest.raises(OrderNotFoundError):
            service.query_order_items(user_id="42", order_id="999")


class TestSecurityBoundQueries:

    @patch(_EXEC_PATCH)
    def test_query_always_binds_user_id(self, mock_exec, service):
        mock_exec.return_value = _make_sql_result([])
        try:
            service.query_orders(user_id="42", order_id="1", query_type="detail")
        except OrderNotFoundError:
            pass
        call_args = mock_exec.call_args
        params = call_args.kwargs.get("params", {})
        assert "user_id" in params
        assert params["user_id"] == "42"
