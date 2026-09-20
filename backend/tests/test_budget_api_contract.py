from datetime import datetime, timezone
from decimal import Decimal

from backend.app.api.routes.budget_dto import build_budget_window, budget_details


def test_budget_window_keeps_decimal_strings_and_utc_reset() -> None:
    window = build_budget_window(
        used=Decimal("8.000000"),
        reserved=Decimal("0.250000"),
        limit=Decimal("10.000000"),
        reset_at=datetime(2026, 9, 18, 16, tzinfo=timezone.utc),
    )

    assert window == {
        "used": "8.000000",
        "reserved": "0.250000",
        "limit": "10.000000",
        "ratio": 0.825,
        "reset_at": "2026-09-18T16:00:00+00:00",
    }


def test_budget_details_distinguishes_scope_and_period() -> None:
    assert budget_details("user", "month", "2026-09-30T16:00:00+00:00") == {
        "budget_kind": "user",
        "limit_kind": "monthly",
        "scope_type": "user",
        "period_type": "month",
        "reset_at": "2026-09-30T16:00:00+00:00",
    }
