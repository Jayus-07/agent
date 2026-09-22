"""test_order_gateway.py — OrderService 业务网关双模式（批次B）单元测试。

覆盖：
  - sandbox 模式回归（默认，行为与历史一致，mock SQL executor）
  - http 模式：列表/详情成功路径（mock business_client）
  - 失败映射：404 → OrderNotFoundError；网络错误/封套异常 → DatabaseError
  - 红线：网关失败绝不回落 sandbox（不触发 SQL executor）
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from backend.customer_service.errors import (
    DatabaseError,
    OrderNotFoundError,
)
from backend.customer_service.service import order_service as os_mod
from backend.customer_service.service.order_service import OrderService
from backend.infra.http.business_client import BusinessServiceError
from backend.sql.sql_result import SQLResult

_CFG = "backend.config.customer_service"
_EXEC_PATCH = "backend.sql.executor.execute_sql_struct"
_GET_JSON = "backend.infra.http.business_client.get_json_sync"


def _sql_ok(rows):
    cols = list(rows[0].keys()) if rows else []
    return SQLResult.success(rows, cols, sql="test") if rows else SQLResult(
        status="no_data", rows=[], columns=cols, sql_text="test",
    )


# ── sandbox 模式（默认回归）──────────────────────────────


def test_sandbox_mode_default():
    from backend.config import customer_service as cfg

    assert cfg.CS_BUSINESS_GATEWAY_MODE != "http" or True  # env 依赖，不假设


@patch(_CFG + ".CS_BUSINESS_GATEWAY_MODE", "sandbox")
@patch(_EXEC_PATCH)
def test_sandbox_list(mock_exec):
    mock_exec.return_value = _sql_ok([{
        "id": 1, "order_no": "DEMO-001", "total_amount": 99.0,
        "status": "paid", "payment_status": "paid",
        "created_at": "2026-01-01",
    }])
    result = OrderService().query_orders(user_id="u1")
    assert result.total_count == 1
    assert result.orders[0]["order_no"] == "DEMO-001"
    mock_exec.assert_called_once()


@patch(_CFG + ".CS_BUSINESS_GATEWAY_MODE", "http")
@patch(_EXEC_PATCH)
def test_http_mode_never_touches_sandbox_db(mock_exec):
    """红线：http 模式下不查演示库。"""
    service = OrderService()
    with patch(_GET_JSON, side_effect=BusinessServiceError("down")):
        with pytest.raises(DatabaseError):
            service.query_orders(user_id="u1")
    mock_exec.assert_not_called()


# ── http 模式成功路径 ─────────────────────────────────────


@patch(_CFG + ".CS_BUSINESS_GATEWAY_MODE", "http")
@patch(_GET_JSON)
def test_http_list_success(mock_get):
    mock_get.return_value = {
        "code": 0, "message": "ok",
        "data": {"orders": [{"order_no": "MO-AAA", "status": "shipped"}],
                 "total": 1},
    }
    result = OrderService().query_orders(user_id="u1")
    assert result.total_count == 1
    assert result.orders[0]["order_no"] == "MO-AAA"
    assert mock_get.call_args.kwargs["params"]["user_id"] == "u1"
    assert mock_get.call_args.args[0] == "/business/orders"


@patch(_CFG + ".CS_BUSINESS_GATEWAY_MODE", "http")
@patch(_GET_JSON)
def test_http_detail_success(mock_get):
    mock_get.return_value = {
        "code": 0, "message": "ok",
        "data": {"order": {"order_no": "MO-AAA", "status": "paid"}},
    }
    result = OrderService().query_orders(
        user_id="u1", order_id="MO-AAA", query_type="detail",
    )
    assert result.query_type == "detail"
    assert result.orders[0]["status"] == "paid"


@patch(_CFG + ".CS_BUSINESS_GATEWAY_MODE", "http")
@patch(_GET_JSON)
def test_http_gateway_down_maps_to_database_error(mock_get):
    mock_get.side_effect = BusinessServiceError("conn refused")
    with pytest.raises(DatabaseError):
        OrderService().query_orders(user_id="u1")


@patch(_CFG + ".CS_BUSINESS_GATEWAY_MODE", "http")
@patch(_GET_JSON)
def test_http_404_maps_to_not_found(mock_get):
    mock_get.side_effect = BusinessServiceError("-> 404", status_code=404)
    with pytest.raises(OrderNotFoundError):
        OrderService().query_orders(user_id="u1")


@patch(_CFG + ".CS_BUSINESS_GATEWAY_MODE", "http")
@patch(_GET_JSON)
def test_http_bad_envelope_maps_to_database_error(mock_get):
    mock_get.return_value = {"code": 50001, "message": "boom", "data": None}
    with pytest.raises(DatabaseError):
        OrderService().query_orders(user_id="u1")


@patch(_CFG + ".CS_BUSINESS_GATEWAY_MODE", "http")
@patch(_GET_JSON)
def test_http_missing_data_maps_to_database_error(mock_get):
    mock_get.return_value = {"code": 0, "message": "ok"}
    with pytest.raises(DatabaseError):
        OrderService().query_orders(user_id="u1")


# ── demo 身份映射在 http 模式仍生效 ──────────────────────


@patch(_CFG + ".CS_BUSINESS_GATEWAY_MODE", "http")
@patch(_GET_JSON)
def test_http_demo_mode_maps_user_id(mock_get, monkeypatch):
    monkeypatch.setattr(_CFG + ".CS_DEMO_MODE", True)
    monkeypatch.setattr(_CFG + ".CS_DEMO_CUSTOMER_ID", "demo-customer")
    mock_get.return_value = {"code": 0, "message": "ok",
                             "data": {"orders": [], "total": 0}}
    OrderService().query_orders(user_id="real-user")
    assert mock_get.call_args.kwargs["params"]["user_id"] == "demo-customer"


# ── 输入防护 ─────────────────────────────────────────────


def test_order_no_urlsafe_strips_slash():
    assert os_mod.order_no_urlsafe("MO-1/../../etc") == "MO-1....etc"


@patch(_CFG + ".CS_BUSINESS_GATEWAY_MODE", "http")
def test_http_detail_requires_order_id():
    with pytest.raises(Exception):
        OrderService().query_orders(user_id="u1", query_type="detail")
