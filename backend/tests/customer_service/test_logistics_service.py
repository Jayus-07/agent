"""test_logistics_service.py — LogisticsService 单元测试 (mock DB)"""
from unittest.mock import patch

import pytest

from backend.customer_service.errors import (
    DatabaseError,
    OrderNotFoundError,
    ValidationError,
)
from backend.customer_service.service.logistics_service import (
    LogisticsService,
)
from backend.sql.sql_result import SQLResult

_EXEC_PATCH = "backend.sql.executor.execute_sql_struct"


def _make_sql_result(rows):
    cols = list(rows[0].keys()) if rows else []
    return SQLResult.success(rows, cols, sql="test") if rows else SQLResult(
        status="no_data", rows=[], columns=cols, sql_text="test",
    )


@pytest.fixture
def service():
    return LogisticsService()


class TestQueryLogistics:

    @patch(_EXEC_PATCH)
    def test_shipped_order(self, mock_exec, service):
        mock_exec.return_value = _make_sql_result([{
            "id": 1, "order_no": "ORD-001",
            "status": "shipped", "created_at": "2026-09-01",
        }])
        result = service.query_logistics(user_id="42", order_id="1")
        assert result.status == "shipped"
        assert result.status_display == "已发货，运输中"
        assert "运输中" in result.tracking_info

    @patch(_EXEC_PATCH)
    def test_completed_order(self, mock_exec, service):
        mock_exec.return_value = _make_sql_result([{
            "id": 1, "order_no": "ORD-001",
            "status": "completed", "created_at": "2026-09-01",
        }])
        result = service.query_logistics(user_id="42", order_id="1")
        assert result.status_display == "已签收"
        assert "签收" in result.tracking_info

    @patch(_EXEC_PATCH)
    def test_paid_order(self, mock_exec, service):
        mock_exec.return_value = _make_sql_result([{
            "id": 1, "order_no": "ORD-001",
            "status": "paid", "created_at": "2026-09-01",
        }])
        result = service.query_logistics(user_id="42", order_id="1")
        assert result.status_display == "已支付，待发货"
        assert "发货" in result.tracking_info

    @patch(_EXEC_PATCH)
    def test_order_not_found(self, mock_exec, service):
        mock_exec.return_value = _make_sql_result([])
        with pytest.raises(OrderNotFoundError):
            service.query_logistics(user_id="42", order_id="999")

    @patch(_EXEC_PATCH)
    def test_db_error(self, mock_exec, service):
        mock_exec.return_value = SQLResult.failed(
            status="failed", error="connection lost",
        )
        with pytest.raises(DatabaseError):
            service.query_logistics(user_id="42", order_id="1")

    def test_invalid_order_id(self, service):
        with pytest.raises(ValidationError):
            service.query_logistics(user_id="42", order_id="1; DROP TABLE")

    @patch(_EXEC_PATCH)
    def test_query_binds_user_id(self, mock_exec, service):
        mock_exec.return_value = _make_sql_result([{
            "id": 1, "order_no": "ORD-001",
            "status": "shipped", "created_at": "2026-09-01",
        }])
        service.query_logistics(user_id="42", order_id="1")
        params = mock_exec.call_args.kwargs.get("params", {})
        assert "user_id" in params
        assert params["user_id"] == "42"
