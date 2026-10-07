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


@pytest.fixture(autouse=True)
def _force_sandbox(monkeypatch):
    """本套件测本地库（sandbox）路径——测试环境 .env 为 http 网关模式，
    且 business-service 不随单测起（2026-10-07 logistics 双模式改造：
    http 分支委托 OrderService 网关，本地 SQL 语义由本套件钉住）。"""
    # 注意：_resolve_order 在函数体内 from order_service 现导 _use_http_gateway，
    # 必须 patch 来源模块才能生效
    monkeypatch.setattr(
        "backend.customer_service.service.order_service._use_http_gateway",
        lambda: False,
    )
    yield


@pytest.fixture
def service():
    return LogisticsService()


class TestHttpGatewayDelegation:
    """http 网关模式：订单事实委托 OrderService.detail（缺陷6.4 红线，
    消除物流侧本地库 split-brain——2026-10-07 收口审计 P1-6）。"""

    def test_http_mode_delegates_to_order_service(self, monkeypatch):
        from types import SimpleNamespace

        from backend.customer_service.service import logistics_service as mod

        captured = {}

        class _FakeOrderService:
            def query_orders(self, user_id, order_id=None, query_type="list"):
                captured["args"] = (user_id, order_id, query_type)
                return SimpleNamespace(orders=[{
                    "id": 9, "order_no": "GW-001", "status": "shipped",
                }])

        monkeypatch.setattr(
            "backend.customer_service.service.order_service._use_http_gateway",
            lambda: True,
        )
        monkeypatch.setattr(
            "backend.customer_service.service.order_service.get_order_service",
            lambda: _FakeOrderService(),
        )
        # 轨迹 Provider 不参与本用例（无 Provider → 状态推导）
        result = mod.LogisticsService().query_logistics(
            user_id="42", order_id="GW-001")
        assert captured["args"] == ("42", "GW-001", "detail")
        assert result.order_no == "GW-001"
        assert result.status == "shipped"
        assert result.status_display == "已发货，运输中"

    def test_http_mode_not_found_propagates(self, monkeypatch):
        from backend.customer_service.errors import OrderNotFoundError as ONF
        from backend.customer_service.service import logistics_service as mod

        class _NotFoundService:
            def query_orders(self, user_id, order_id=None, query_type="list"):
                raise ONF("Order GW-404 not found")

        monkeypatch.setattr(
            "backend.customer_service.service.order_service._use_http_gateway",
            lambda: True,
        )
        monkeypatch.setattr(
            "backend.customer_service.service.order_service.get_order_service",
            lambda: _NotFoundService(),
        )
        with pytest.raises(ONF):
            mod.LogisticsService().query_logistics(
                user_id="42", order_id="GW-404")


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
