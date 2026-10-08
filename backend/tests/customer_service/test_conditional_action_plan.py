"""条件退款计划必须由显式请求与权威查询事实共同门控。"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from backend.customer_service.experts.action import execute_action


def _state(shipping_status="not_shipped", order_count=1, status="success"):
    return {
        "user_id": "u1", "tenant_id": "tenant-1", "session_id": "s1",
        "task_plan": {
            "schema_version": 1, "primary_intent": "t_logistics",
            "tasks": [
                {"task_id": "q1", "capability": "query_logistics", "depends_on": []},
                {
                    "task_id": "a1", "capability": "propose_refund",
                    "depends_on": ["q1"],
                    "condition": {
                        "fact": "shipping_status", "operator": "eq",
                        "value": "not_shipped",
                    },
                },
            ],
        },
        "current_task": {
            "task_id": "a1", "capability": "propose_refund",
            "depends_on": ["q1"],
            "condition": {
                "fact": "shipping_status", "operator": "eq",
                "value": "not_shipped",
            },
        },
        "task_results": [{
            "task_id": "q1", "status": status,
            "facts": {
                "shipping_status": shipping_status,
                "order_count": order_count,
                "order_id": "trusted-order-1",
            },
            "source": "sandbox_logistics_service", "error_type": None,
        }],
    }


@pytest.mark.parametrize("user_message", [
    "查下物流，如果没发货就申请退款",
    "查下物流，如果没发货就申请退款，订单号是MODEL-9999",
])
@patch("backend.customer_service.experts.action._build_new_proposal")
@patch("backend.customer_service.experts.action._resolve_tenant", return_value="tenant-1")
@patch("backend.customer_service.confirmation_store.get_confirmation_store")
@patch("backend.customer_service.security.permission.PermissionChecker.validate_user_identity", return_value="u1")
@patch("backend.observability.metrics.record_cs_intent")
def test_explicit_request_uses_trusted_query_order_for_existing_proposal_flow(
    _metric, _identity, get_store, _tenant, build_proposal, user_message,
):
    from backend.customer_service.experts.base import ExpertResult

    get_store.return_value.load.return_value = None
    build_proposal.return_value = ExpertResult(
        expert="action", status="success", response_draft="待确认", data={},
    )

    result = execute_action(
        user_message, {"intent": "t_logistics", "metadata": {}}, _state(),
    )

    build_proposal.assert_called_once()
    args = build_proposal.call_args.args
    assert args[1] == "as_refund"
    assert args[2]["metadata"]["order_id"] == "trusted-order-1"
    assert result["data"]["task_result"]["status"] == "success"


@pytest.mark.parametrize(("shipping_status", "order_count", "status", "message"), [
    ("shipped", 1, "success", "查下物流，如果没发货就申请退款"),
    ("unknown", 1, "success", "查下物流，如果没发货就申请退款"),
    ("not_shipped", 2, "success", "查下物流，如果没发货就申请退款"),
    ("not_shipped", 1, "failed", "查下物流，如果没发货就申请退款"),
    ("not_shipped", 1, "success", "查下物流"),
])
@patch("backend.customer_service.experts.action._build_new_proposal")
@patch("backend.customer_service.experts.action._resolve_tenant", return_value="tenant-1")
@patch("backend.customer_service.confirmation_store.get_confirmation_store")
@patch("backend.customer_service.security.permission.PermissionChecker.validate_user_identity", return_value="u1")
@patch("backend.observability.metrics.record_cs_intent")
def test_unsafe_or_non_explicit_condition_never_creates_proposal(
    _metric, _identity, get_store, _tenant, build_proposal,
    shipping_status, order_count, status, message,
):
    get_store.return_value.load.return_value = None

    result = execute_action(
        message, {"intent": "t_logistics", "metadata": {}},
        _state(shipping_status, order_count, status),
    )

    build_proposal.assert_not_called()
    assert not result.get("data", {}).get("pending_action")


@patch("backend.customer_service.experts.action._build_new_proposal")
@patch("backend.customer_service.experts.action._resolve_tenant", return_value="tenant-1")
@patch("backend.customer_service.confirmation_store.get_confirmation_store")
@patch("backend.customer_service.security.permission.PermissionChecker.validate_user_identity", return_value="u1")
@patch("backend.observability.metrics.record_cs_intent")
def test_refund_eligibility_is_rechecked_and_rejection_never_creates_pending(
    _metric, _identity, get_store, _tenant, build_proposal,
):
    from backend.customer_service.errors import OrderNotEligibleError

    get_store.return_value.load.return_value = None
    build_proposal.side_effect = OrderNotEligibleError("订单当前不可退款")

    result = execute_action(
        "查下物流，如果没发货就申请退款",
        {"intent": "t_logistics", "metadata": {}}, _state(),
    )

    build_proposal.assert_called_once()
    assert "没有发起退款申请" in result["response_draft"]
    assert not result["data"].get("pending_action")
    assert result["data"]["task_result"]["status"] == "failed"
