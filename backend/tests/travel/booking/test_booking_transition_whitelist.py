"""Booking 状态机白名单执行点强制回归（安全审计 #80，2026-11-09）。

审计结论：ALLOWED_TRANSITIONS / _IN_DOUBT_EXIT_CAUSES 只在 state.py 定义，
生产代码零调用——provider webhook 可把 awaiting_confirmation/quoted/
expired/in_doubt 的订单直接推进终态（跳过用户确认门、金额互检与价格/
库存复核）；in_doubt → booked 还会以 cause=webhook 违反出口白名单。

修复（双层）：
  1. store.transition_order 入口统一 require_transition 硬闸
     （非法转换 fail-closed 抛 IllegalBookingTransition）；
  2. webhook._apply 限 SUBMITTING→BOOKED/FAILED（模块纪律声明原本如此），
     其余状态收到 confirmed/failed 事件 = 误序/迟到投递，幂等忽略。

测试口径 = 审计 Local check：awaiting_confirmation 订单 + 合法 confirmed
事件 → 订单不变、事件账 ignored；submitting 订单同事件 → 正常落 booked
（W5 正路径不回归）。
"""
from __future__ import annotations

import pytest

from backend.travel.booking.state import BookingOrderStatus
from backend.travel.booking.webhook import apply_inbox_event

TENANT, USER = "tenant-wl", "user-wl"

_HOTEL_PARAMS = {
    "city": "大阪", "check_in": "2027-10-03", "check_out": "2027-10-05",
    "adults": 2, "children": 0, "rooms": 1,
}


def _make_order(booking_service, *, status: str) -> dict:
    """建单并推进到指定状态（确认门内的推进均走白名单合法路径）。"""
    q = booking_service.create_quote(
        tenant_id=TENANT, user_id=USER, commerce_type="hotel",
        search_params=dict(_HOTEL_PARAMS), selection={"index": 1})
    order = q.order
    if status == "submitting":
        order = booking_service.store.transition_order(
            order_id=order["order_id"],
            expected_status="awaiting_confirmation", target="confirmed",
            cause="user_confirmed", actor=USER, event_type="test_confirmed")
        order = booking_service.store.transition_order(
            order_id=order["order_id"],
            expected_status="confirmed", target="submitting",
            cause="execute", actor=USER, event_type="test_submit")
    return order


def _confirmed_event(order: dict) -> dict:
    return {
        "event_id": "evt-wl-1",
        "event_type": "booking.confirmed",
        "provider_order_id": "fbk-wl-1",
        "merchant_order_id": order["merchant_order_id"],
        "status": "confirmed",
    }


def _inbox_row(store, provider: str, event_id: str) -> dict | None:
    """读 webhook inbox 行（store 无 inbox_get，测试直接查表）。"""
    with store._factory() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT status, quarantine_reason FROM travel.booking_webhook_inbox "
            "WHERE provider=%s AND event_id=%s", (provider, event_id))
        row = cur.fetchone()
    return {"status": row[0], "reason": row[1]} if row else None


class TestWebhookStateWhitelist:
    """webhook._apply 限 SUBMITTING→BOOKED/FAILED（审计 #80 主场景）。"""

    def test_confirmed_event_on_awaiting_order_is_ignored(
        self, booking_service,
    ):
        """修复点：awaiting_confirmation 订单不得被 webhook 直接落终态。"""
        order = _make_order(booking_service, status="awaiting_confirmation")
        result = apply_inbox_event(
            store=booking_service.store, provider="fake_booking_native",
            event_id="evt-wl-1", payload=_confirmed_event(order))
        after = booking_service.store.get_order(order["order_id"])
        assert after["status"] == "awaiting_confirmation"
        assert result["status"] == "awaiting_confirmation"

    def test_ignored_event_recorded_not_submitting(
        self, booking_service, monkeypatch,
    ):
        """完整 ingest 链（含 HMAC）：ignored/not_submitting 落事件账。

        apply_inbox_event 是补偿通道、不写 inbox；inbox 断言走
        ingest_webhook 完整校验链（审计 Local check 口径）。
        """
        import hashlib
        import hmac
        import json
        import time as _time

        secret = "wl-test-secret"
        monkeypatch.setenv("TRAVEL_BOOKING_WEBHOOK_SECRET_FAKE_BOOKING_NATIVE",
                           secret)
        order = _make_order(booking_service, status="awaiting_confirmation")
        payload = _confirmed_event(order)
        payload["event_id"] = "evt-wl-2"
        payload_bytes = json.dumps(payload, ensure_ascii=False,
                                   separators=(",", ":")).encode("utf-8")
        ts = str(int(_time.time()))
        sig = hmac.new(
            secret.encode("utf-8"),
            f"{ts}.".encode("utf-8") + payload_bytes,
            hashlib.sha256).hexdigest()

        from backend.travel.booking.webhook import ingest_webhook
        result = ingest_webhook(
            provider="fake_booking_native", payload_bytes=payload_bytes,
            signature=sig, timestamp=ts, content_type="application/json",
            store=booking_service.store)

        assert result["accepted"] is True
        # applied 非 None = 事件已处理（含幂等忽略），状态必须保持原状
        assert result["applied"]["status"] == "awaiting_confirmation"
        inbox = _inbox_row(
            booking_service.store, "fake_booking_native", "evt-wl-2")
        assert inbox is not None and inbox["status"] == "ignored"
        assert inbox["reason"] == "not_submitting"
        after = booking_service.store.get_order(order["order_id"])
        assert after["status"] == "awaiting_confirmation"

    def test_confirmed_event_on_submitting_still_applies(
        self, booking_service,
    ):
        """W5 正路径不回归：submitting + confirmed → booked。"""
        order = _make_order(booking_service, status="submitting")
        result = apply_inbox_event(
            store=booking_service.store, provider="fake_booking_native",
            event_id="evt-wl-3", payload=_confirmed_event(order))
        assert result["status"] == "booked"

    def test_failed_event_on_expired_order_is_ignored(self, booking_service):
        """终态订单（expired）收到 failed 事件同样幂等忽略。"""
        order = _make_order(booking_service, status="awaiting_confirmation")
        order = booking_service.store.transition_order(
            order_id=order["order_id"],
            expected_status="awaiting_confirmation", target="expired",
            cause="confirmation_expired", actor=USER,
            event_type="test_expired")
        apply_inbox_event(
            store=booking_service.store, provider="fake_booking_native",
            event_id="evt-wl-4", payload={**_confirmed_event(order),
                                          "event_id": "evt-wl-4",
                                          "status": "failed"})
        after = booking_service.store.get_order(order["order_id"])
        assert after["status"] == "expired"


class TestStoreTransitionHardGate:
    """transition_order 入口统一硬闸（所有调用方受益，非仅 webhook）。"""

    def test_awaiting_to_booked_rejected(self, booking_service):
        """跳过确认门/提交门的直接落终态 → fail-closed。"""
        order = _make_order(booking_service, status="awaiting_confirmation")
        with pytest.raises(Exception, match="非法状态转换"):
            booking_service.store.transition_order(
                order_id=order["order_id"],
                expected_status="awaiting_confirmation", target="booked",
                cause="webhook", actor="attacker",
                event_type="webhook_applied")

    def test_in_doubt_exit_requires_whitelisted_cause(self, booking_service):
        """in_doubt → booked 且 cause=webhook（非 reconciliation/manual）→ 拒绝。"""
        order = _make_order(booking_service, status="submitting")
        order = booking_service.store.transition_order(
            order_id=order["order_id"], expected_status="submitting",
            target="in_doubt", cause="provider_unknown", actor=USER,
            event_type="test_unknown")
        with pytest.raises(Exception, match="非法状态转换"):
            booking_service.store.transition_order(
                order_id=order["order_id"], expected_status="in_doubt",
                target="booked", cause="webhook", actor="webhook:x",
                event_type="webhook_applied")

    def test_in_doubt_manual_resolve_still_allowed(self, booking_service):
        """对照：reconciliation/manual 出口不受影响（人工裁决合法路径）。"""
        order = _make_order(booking_service, status="submitting")
        order = booking_service.store.transition_order(
            order_id=order["order_id"], expected_status="submitting",
            target="in_doubt", cause="provider_unknown", actor=USER,
            event_type="test_unknown")
        resolved = booking_service.store.transition_order(
            order_id=order["order_id"], expected_status="in_doubt",
            target="booked", cause="manual", actor="operator",
            event_type="reconciliation_resolved")
        assert resolved["status"] == "booked"

    def test_unknown_status_value_rejected(self, booking_service):
        """未知状态值 fail-closed（枚举解析失败 = 非法转换）。"""
        order = _make_order(booking_service, status="submitting")
        with pytest.raises(Exception):
            booking_service.store.transition_order(
                order_id=order["order_id"], expected_status="submitting",
                target="not_a_state", cause="x", actor="t",
                event_type="test")

    def test_legal_lifecycle_transitions_unaffected(self, booking_service):
        """合法生命周期转换（确认→提交→终态）不受硬闸影响。"""
        order = _make_order(booking_service, status="submitting")
        booked = booking_service.store.transition_order(
            order_id=order["order_id"], expected_status="submitting",
            target="booked", cause="provider_confirmed", actor=USER,
            event_type="test_succeeded")
        assert booked["status"] == BookingOrderStatus.BOOKED.value
