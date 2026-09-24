"""STOP L 状态机/身份/渲染纪律测试（G6/G11/G12/G20/G21/G22/G38）。"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from backend.travel.booking.state import (
    BookingOrderStatus as S,
    IllegalBookingTransition,
    can_transition,
    require_transition,
)
from backend.travel.booking.identity import (
    booking_intent_id,
    canonical_amount,
    confirmation_fingerprint,
    merchant_order_id,
    quote_fingerprint,
)


# =============================================
# 状态机（G20/G21/G22）
# =============================================


def test_happy_path_transitions_legal():
    require_transition(S.QUOTED, S.AWAITING_CONFIRMATION)
    require_transition(S.AWAITING_CONFIRMATION, S.CONFIRMED)
    require_transition(S.CONFIRMED, S.SUBMITTING)
    require_transition(S.SUBMITTING, S.BOOKED)


def test_skip_confirmation_illegal():
    with pytest.raises(IllegalBookingTransition):
        require_transition(S.QUOTED, S.CONFIRMED)     # 必须经 awaiting gate
    with pytest.raises(IllegalBookingTransition):
        require_transition(S.AWAITING_CONFIRMATION, S.SUBMITTING)


def test_terminal_states_never_regress():
    for terminal in (S.BOOKED, S.FAILED, S.EXPIRED):
        for target in S:
            if target is terminal:
                continue
            assert not can_transition(terminal, target)


def test_in_doubt_exit_requires_reconciliation_or_manual():
    """IN_DOUBT 出口仅 reconciliation/manual；业务 retry 不得拉回。"""
    with pytest.raises(IllegalBookingTransition):
        require_transition(S.IN_DOUBT, S.BOOKED, cause="retry")
    with pytest.raises(IllegalBookingTransition):
        require_transition(S.IN_DOUBT, S.FAILED)
    require_transition(S.IN_DOUBT, S.BOOKED, cause="reconciliation")
    require_transition(S.IN_DOUBT, S.FAILED, cause="manual")


def test_price_changed_blocks_before_create_state():
    require_transition(S.CONFIRMED, S.FAILED)  # B11/B12 阻断路径合法


# =============================================
# 身份派生（G6/G11/G12：确定性、跨进程稳定）
# =============================================


def test_intent_and_merchant_order_deterministic():
    kw = dict(tenant_id="t", user_id="u", quote_id="q",
              confirmation_fingerprint="cf")
    assert booking_intent_id(**kw) == booking_intent_id(**kw)
    assert booking_intent_id(tenant_id="t", user_id="u", quote_id="q2",
                             confirmation_fingerprint="cf") != booking_intent_id(**kw)
    mid = merchant_order_id(tenant_id="t", intent_id="i", provider="p")
    assert mid == merchant_order_id(tenant_id="t", intent_id="i", provider="p")
    assert mid.startswith("MOB-")


def test_confirmation_fingerprint_binds_price_facts():
    base = dict(quote_id="q", quote_fingerprint="qf", amount="420",
                currency="JPY", provider="p", operation="travel.booking.create",
                tenant_id="t", user_id="u")
    assert confirmation_fingerprint(**base) == confirmation_fingerprint(**base)
    changed = dict(base, amount="450")
    assert confirmation_fingerprint(**base) != confirmation_fingerprint(**changed)


def test_quote_fingerprint_binds_required_fields():
    base = dict(tenant_id="t", provider="p", offer_fingerprint="of",
                booking_facts={"city": "大阪", "nights": 2},
                amount="420", currency="JPY")
    assert quote_fingerprint(**base) == quote_fingerprint(**base)
    assert quote_fingerprint(**dict(base, amount="450")) != quote_fingerprint(**base)
    assert quote_fingerprint(**dict(base, tenant_id="t2")) != quote_fingerprint(**base)


def test_fingerprint_stable_across_processes():
    code = (
        "from backend.travel.booking.identity import quote_fingerprint;"
        "print(quote_fingerprint(tenant_id='t', provider='p',"
        "offer_fingerprint='of', booking_facts={'city':'大阪'},"
        "amount='420', currency='JPY'))"
    )
    expected = quote_fingerprint(tenant_id="t", provider="p",
                                 offer_fingerprint="of",
                                 booking_facts={"city": "大阪"},
                                 amount="420", currency="JPY")
    env = dict(os.environ)
    env["PYTHONHASHSEED"] = "random"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, env=env,
                         cwd=str(Path(__file__).resolve().parents[4]))
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == expected


def test_canonical_amount_normalizes_numeric_scale():
    assert canonical_amount("420") == "420"
    assert canonical_amount("420.0000") == "420"    # NUMERIC 回读归一
    assert canonical_amount("420.50") == "420.5"
    from decimal import Decimal

    assert canonical_amount(Decimal("19.99")) == "19.99"


# =============================================
# 渲染纪律（§三十七/§三十八）
# =============================================


def test_forbidden_phrases_never_in_vocabulary():
    from backend.travel.booking.reporter import FORBIDDEN_PHRASES

    for phrase in ("预订成功",):  # 「预订成功」仅在 provider 确认后合法
        assert phrase not in FORBIDDEN_PHRASES  # 话术白名单自检
    for phrase in ("出票成功", "房间已锁定", "扣款成功", "已锁价", "价格保证"):
        assert phrase in FORBIDDEN_PHRASES


def test_in_doubt_never_worded_as_success_or_failure():
    """G37：IN_DOUBT 话术既非成功也非失败。"""
    from backend.travel.booking.executor import ExecutionOutcome
    from backend.travel.booking.reporter import render_execution_outcome

    text = render_execution_outcome(ExecutionOutcome(
        "in_doubt", order={"merchant_order_id": "MOB-x"},
        detail="timeout"))
    assert "暂时无法确认" in text
    assert "不会自动重复提交" in text
    assert "预订成功" not in text
    assert "预订失败" not in text


def test_failed_and_booked_wording():
    from backend.travel.booking.executor import ExecutionOutcome
    from backend.travel.booking.reporter import render_execution_outcome

    booked = render_execution_outcome(ExecutionOutcome(
        "booked", order={"merchant_order_id": "MOB-x",
                         "provider_order_id": "fbk-1",
                         "amount": "420", "currency": "JPY"}))
    assert "预订成功" in booked  # provider 确认后允许
    failed = render_execution_outcome(ExecutionOutcome(
        "failed", order={"merchant_order_id": "MOB-x",
                         "failure_code": "REJECTED"}))
    assert "预订失败" in failed
