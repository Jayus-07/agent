# -*- coding: utf-8 -*-
"""test_refund_candidates.py — 退款点选候选（2026-10-08 拍板）回归。

拍板语义：退款缺订单号时不再让用户手输——按退款政策窗口列出可退订单
（订单号+商品名+金额）供点选/回复序号；无候选退回手输并附政策天数。
"""
from unittest.mock import MagicMock, patch

import pytest

from backend.customer_service.experts.action import (
    _ask_missing_slot,
    _handle_slot_fill,
)


def _confirmation_deps():
    """confirmation 依赖的公共 patch 面（store 由参数注入，无需 mock）。"""
    return [
        patch("backend.customer_service.confirmation.ConfirmationState"),
        patch("backend.customer_service.confirmation.compute_expires_at",
              return_value="2026-10-09T00:00:00+00:00"),
    ]


def _store():
    s = MagicMock()
    s.save.return_value = None
    return s


_CANDIDATES = {
    "window_days": 7,
    "candidates": [
        {"order_id": "101", "order_no": "DEMO-ORD-005", "product_names": "无线鼠标",
         "amount": 599.0, "status": "paid"},
        {"order_id": "102", "order_no": "DEMO-ORD-002", "product_names": "",
         "amount": 1299.0, "status": "shipped"},
    ],
}


def test_missing_slot_offers_candidates():
    with patch(
        "backend.customer_service.service.refund_service.get_refund_service",
    ) as mock_get:
        mock_get.return_value.list_refund_candidates.return_value = dict(_CANDIDATES)
        result = _ask_missing_slot(
            "u1", "as_refund", "s1", _store(), tenant_id="default",
        )

    assert result["status"] == "success"
    assert "DEMO-ORD-005" in result["response_draft"]
    clarify = result["data"]["_clarify"]
    assert clarify["source"] == "refund_candidates"
    assert len(clarify["options"]) == 2
    assert "无线鼠标" in clarify["options"][0]
    pending = result["data"]["pending_action"]
    assert len(pending["candidates"]) == 2
    # 缺商品名的候选也要进候选（上层话术已带商品信息缺失语义）
    assert pending["candidates"][1]["product_names"] == ""


def test_missing_slot_without_candidates_falls_back_to_manual_input():
    with patch(
        "backend.customer_service.service.refund_service.get_refund_service",
    ) as mock_get:
        mock_get.return_value.list_refund_candidates.return_value = {
            "window_days": 7, "candidates": [],
        }
        result = _ask_missing_slot(
            "u1", "as_refund", "s1", _store(), tenant_id="default",
        )

    assert "_clarify" not in result["data"]
    assert "订单号" in result["response_draft"]
    assert "7 天" in result["response_draft"]


def test_slot_fill_by_number_picks_candidate():
    pending = {
        "action_id": "a1", "action_type": "refund_request", "intent": "as_refund",
        "status": "need_info", "missing_slots": ["order_id"],
        "collected_slots": {},
        "candidates": _CANDIDATES["candidates"],
        "target_type": "order", "target_id": "", "risk_level": "high",
        "requires_confirmation": False, "retry_count": 0,
    }
    store = _store()

    with patch(
        "backend.customer_service.experts.action._build_new_proposal",
    ) as mock_build:
        mock_build.return_value = MagicMock(
            expert="action", status="success",
            response_draft="proposal", data={},
        )
        result = _handle_slot_fill(
            pending, "2", "u1", "s1", store, {}, tenant_id="default",
        )

    called_meta = mock_build.call_args.args[2]["metadata"]
    assert called_meta["order_id"] == "102", "回复序号 2 应映射到第二个候选"


def test_slot_fill_by_order_no_still_works():
    pending = {
        "action_id": "a1", "action_type": "refund_request", "intent": "as_refund",
        "status": "need_info", "missing_slots": ["order_id"],
        "collected_slots": {},
        "candidates": _CANDIDATES["candidates"],
        "target_type": "order", "target_id": "", "risk_level": "high",
        "requires_confirmation": False, "retry_count": 0,
    }
    store = _store()

    with patch(
        "backend.customer_service.experts.action._build_new_proposal",
    ) as mock_build:
        mock_build.return_value = MagicMock(
            expert="action", status="success", response_draft="proposal", data={},
        )
        _handle_slot_fill(
            pending, "DEMO-ORD-005", "u1", "s1", store, {}, tenant_id="default",
        )

    called_meta = mock_build.call_args.args[2]["metadata"]
    assert called_meta["order_id"] == "DEMO-ORD-005"
