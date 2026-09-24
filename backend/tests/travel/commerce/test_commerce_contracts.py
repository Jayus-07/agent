"""STOP K 契约测试（K1：G3/G4/G8/G13）

红线钉死：Money Decimal（NaN/Infinity/负/精度/币种）、taxes unknown≠0、
availability 枚举语义、fingerprint 跨进程稳定（禁 Python hash）、
模型 forbid 补齐（字段不存在必须 None，extra 拒绝）。
"""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import date, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from backend.travel.commerce.identity import (
    flight_fingerprint,
    hotel_fingerprint,
    snapshot_id,
)
from backend.travel.commerce.models import (
    Availability,
    FlightOffer,
    FlightSegment,
    HotelOffer,
    Money,
    Occupancy,
    PriceSnapshot,
    TaxInclusion,
)


def _money(amount, currency="CNY") -> Money:
    return Money(amount=Decimal(amount), currency=currency)


def _snapshot(amount="420", **kw) -> PriceSnapshot:
    defaults = dict(
        snapshot_id="ps_x", provider="fake:commerce",
        amount=_money(amount), observed_at="2026-09-24T00:00:00+00:00",
    )
    defaults.update(kw)
    return PriceSnapshot(**defaults)


def _hotel_offer(**kw) -> HotelOffer:
    defaults = dict(
        provider="fake:commerce", property_id="p1",
        property_name="酒店", city="大阪",
        check_in="2026-10-03", check_out="2026-10-05", nights=2,
        occupancy=Occupancy(),
        availability={"status": "available",
                      "observed_at": "2026-09-24T00:00:00+00:00"},
        price_snapshot=_snapshot(),
        observed_at="2026-09-24T00:00:00+00:00",
        offer_fingerprint="ho_x",
    )
    defaults.update(kw)
    return HotelOffer(**defaults)


# =============================================
# Money（G4）
# =============================================


def test_money_rejects_nan_and_infinity():
    for bad in ("NaN", "Infinity", "-Infinity"):
        with pytest.raises(ValidationError):
            Money(amount=Decimal(bad), currency="CNY")


def test_money_rejects_negative():
    with pytest.raises(ValidationError):
        Money(amount=Decimal("-0.01"), currency="CNY")


def test_money_preserves_decimal_precision_no_float():
    m = Money(amount=Decimal("420.10"), currency="CNY")
    assert m.amount == Decimal("420.10")
    dumped = m.model_dump(mode="json")
    assert dumped["amount"] == "420.10"  # JSON 落字符串，无 float 精度损耗


def test_money_rejects_bad_currency_and_guessing():
    """小写自动归一为大写（cny→CNY 合法）；错误长度/非字母拒绝。"""
    assert Money(amount=Decimal("1"), currency="cny").currency == "CNY"
    for bad in ("RNMB", "US", "YPY1", "", "12"):
        with pytest.raises(ValidationError):
            Money(amount=Decimal("1"), currency=bad)


def test_zero_price_offer_rejected():
    """0 元报价非法（unknown≠0 红线的镜像：0 不是合法总价）。"""
    with pytest.raises(ValidationError):
        _snapshot(amount="0")


# =============================================
# taxes/fees unknown 语义（G8）
# =============================================


def test_taxes_none_is_unknown_not_zero():
    snap = _snapshot(taxes=None, fees=None)
    assert snap.taxes is None and snap.fees is None
    assert snap.taxes != Decimal("0")  # 语义层面：unknown 不是零


def test_taxes_known_zero_allowed_when_declared():
    """明确免税（taxes=0）与 unknown 语义并存且可区分。"""
    snap = _snapshot(taxes=_money("0"))
    assert snap.taxes is not None and snap.taxes.amount == Decimal("0")


def test_unknown_fields_stay_none_not_fabricated():
    """cancellation/deeplink/坐标等未提供字段必须保持 None（禁补齐）。"""
    offer = _hotel_offer()
    assert offer.cancellation_policy is None
    assert offer.booking_deep_link is None
    assert offer.address is None
    assert offer.lat is None and offer.lng is None
    assert offer.meal_plan is None


def test_extra_fields_forbidden():
    """extra=forbid：Provider 侧多给字段 = 契约漂移，拒绝而非吞掉。"""
    with pytest.raises(ValidationError):
        _hotel_offer(free_breakfast=True)


# =============================================
# Availability（G5）
# =============================================


def test_availability_enum_semantics():
    assert Availability.UNKNOWN != Availability.AVAILABLE
    assert Availability.UNKNOWN != Availability.SOLD_OUT
    assert Availability.SOLD_OUT != Availability.AVAILABLE


def test_dates_validation():
    with pytest.raises(ValidationError):
        _hotel_offer(check_in="2026-10-05", check_out="2026-10-03", nights=2)
    with pytest.raises(ValidationError):
        _hotel_offer(nights=3)  # nights 与日期区间不一致 → 拒绝（禁模型算）


# =============================================
# Flight segments（G10）
# =============================================


def _segment(dep="2026-10-03T08:30:00+08:00",
             arr="2026-10-03T09:55:00+08:00", **kw) -> FlightSegment:
    defaults = dict(carrier="FakeAir", flight_number="FA001",
                    origin_airport="KIX", destination_airport="HND",
                    departure_at=dep, arrival_at=arr)
    defaults.update(kw)
    return FlightSegment(**defaults)


def test_flight_segment_rejects_arrival_before_departure():
    with pytest.raises(ValidationError):
        _segment(dep="2026-10-03T10:00:00+08:00",
                 arr="2026-10-03T09:00:00+08:00")


def test_flight_offer_rejects_unconnected_segments():
    s1 = _segment(origin_airport="KIX", destination_airport="HND")
    s2 = _segment(origin_airport="PEK", destination_airport="SHA",
                  dep="2026-10-03T12:00:00+08:00",
                  arr="2026-10-03T14:00:00+08:00")
    with pytest.raises(ValidationError):
        FlightOffer(
            provider="fake:commerce", origin="KIX", destination="SHA",
            segments=[s1, s2],
            stops=1,
            availability={"status": "available",
                          "observed_at": "2026-09-24T00:00:00+00:00"},
            price_snapshot=_snapshot(), offer_fingerprint="fl_x",
        )


# =============================================
# Fingerprint（G13：跨进程稳定）
# =============================================


def test_fingerprint_stable_across_processes():
    """子进程重算指纹必须一致（Python hash 跨进程随机化被禁用）。"""
    code = (
        "from backend.travel.commerce.identity import hotel_fingerprint;"
        "print(hotel_fingerprint(provider='fake:commerce',"
        "property_id='p1', rate_identifier='offer:r1',"
        "check_in='2026-10-03', check_out='2026-10-05',"
        "adults=2, children=0, rooms=1))"
    )
    expected = hotel_fingerprint(
        provider="fake:commerce", property_id="p1",
        rate_identifier="offer:r1", check_in="2026-10-03",
        check_out="2026-10-05", adults=2, children=0, rooms=1)
    env = dict(os.environ)
    env["PYTHONHASHSEED"] = "random"  # 显式随机化：证明不依赖内建 hash
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True,
        env=env, cwd=str(_repo_root()))
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == expected


def _repo_root() -> str:
    from pathlib import Path

    return str(Path(__file__).resolve().parents[4])


def test_different_rate_plans_never_merge():
    """同 property 不同 rate → 指纹必不同（§十五：禁错误合并 rate plan）。"""
    a = hotel_fingerprint(provider="p", property_id="h1",
                          rate_identifier="offer:r1", check_in="2026-10-03",
                          check_out="2026-10-05", adults=2, children=0,
                          rooms=1)
    b = hotel_fingerprint(provider="p", property_id="h1",
                          rate_identifier="offer:r2", check_in="2026-10-03",
                          check_out="2026-10-05", adults=2, children=0,
                          rooms=1)
    c = hotel_fingerprint(provider="p", property_id="h1",
                          rate_identifier="offer:r1", check_in="2026-10-04",
                          check_out="2026-10-06", adults=2, children=0,
                          rooms=1)
    assert len({a, b, c}) == 3


def test_flight_fingerprint_combines_required_fields():
    a = flight_fingerprint(provider="p", provider_offer_id="f1",
                           segment_keys=["FA:001:2026-10-03T08:30"],
                           cabin="Y")
    b = flight_fingerprint(provider="p", provider_offer_id="f1",
                           segment_keys=["FA:001:2026-10-04T08:30"],
                           cabin="Y")
    c = flight_fingerprint(provider="p", provider_offer_id="f1",
                           segment_keys=["FA:001:2026-10-03T08:30"],
                           cabin="C")
    assert len({a, b, c}) == 3


def test_snapshot_id_deterministic_and_traceable():
    a = snapshot_id("ho_x", "2026-09-24T00:00:00+00:00")
    b = snapshot_id("ho_x", "2026-09-24T00:00:00+00:00")
    c = snapshot_id("ho_x", "2026-09-24T01:00:00+00:00")
    assert a == b and a != c


# =============================================
# 请求校验（G11/G12）
# =============================================


def test_hotel_request_validation_matrix():
    from backend.travel.commerce.request import HotelSearchRequest

    today = date.today()
    far = today + timedelta(days=45)
    with pytest.raises(ValidationError):
        HotelSearchRequest(city="  ", check_in=far,
                           check_out=far + timedelta(days=1))  # 空城市
    with pytest.raises(ValidationError):
        HotelSearchRequest(city="大阪", check_in=date(2020, 1, 1),
                           check_out=date(2020, 1, 2))  # 过去日期
    with pytest.raises(ValidationError):
        HotelSearchRequest(city="大阪", check_in=far,
                           check_out=far)  # check_out==check_in
    with pytest.raises(ValidationError):
        HotelSearchRequest(city="大阪", check_in=far,
                           check_out=far + timedelta(days=1), adults=0)
    with pytest.raises(ValidationError):
        HotelSearchRequest(city="大阪", check_in=far,
                           check_out=far + timedelta(days=1), rooms=0)
    with pytest.raises(ValidationError):
        HotelSearchRequest(city="大阪", check_in=far,
                           check_out=far + timedelta(days=40))  # 超 30 晚
    ok = HotelSearchRequest(city="大阪", check_in=far,
                            check_out=far + timedelta(days=2))
    assert ok.nights == 2  # 晚数 = check_out - check_in（确定性唯一出口）


def test_flight_request_validation_matrix():
    from backend.travel.commerce.request import FlightSearchRequest

    with pytest.raises(ValidationError):
        FlightSearchRequest(origin="东京", destination="东京",
                            departure_date=date(2027, 1, 1))  # 同城
    with pytest.raises(ValidationError):
        FlightSearchRequest(origin="东京", destination="大阪",
                            departure_date=date(2020, 1, 1))  # 过去
    with pytest.raises(ValidationError):
        FlightSearchRequest(origin="东京", destination="大阪",
                            departure_date=date(2027, 1, 5),
                            return_date=date(2027, 1, 5))  # return<=dep
    ok = FlightSearchRequest(origin="东京", destination="大阪",
                             departure_date=date(2027, 1, 5))
    assert ok.round_trip is False
