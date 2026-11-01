"""tests/travel/booking/test_booking_rest_routes.py — 预订确认门 REST 路由测试
（收编重构 §4.2：原 test_booking_routing.py 的聊天子图路由测试退役后的替位）

覆盖：quote → confirm → status 三段正向流 + 鉴权 fail-closed（跨用户 404、
行程不存在 404、未认证 401）+ provider off 如实 503。
真 PG（booking_env，不可达 skip）；TripService 属主校验打桩（被测面是
booking 事务链与 HTTP 门面，非行程仓储）。
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.travel_v2.api.router import router
from backend.travel_v2.services.trip_service import TripNotFound

_TENANT, _USER, _TRIP = "t-rest", "u-rest", "trip-rest-1"
_HEADERS = {
    "X-User-Id": _USER,
    "X-Auth-Type": "jwt",
    "X-Tenant-Id": _TENANT,
}
_SEARCH = {
    "city": "大阪",
    "check_in": "2027-10-03",
    "check_out": "2027-10-05",
}


@pytest.fixture()
def rest_client(booking_env, native_factory, monkeypatch):
    """TestClient + 收编事务测试桩（测试 ledger/连接工厂）+ 行程属主桩。"""
    from backend.travel.booking.service import BookingService
    from backend.travel.booking.store import BookingStore

    import backend.travel_v2.api.bookings as bookings_api

    def _store():
        return BookingStore(connection_factory=_conn)

    def _service(store=None):
        return BookingService(
            store=store or _store(),
            ledger_table="travel.bt_ledger_test",
            conn_factory=_conn,
        )

    def _conn():
        from backend.config.database import MEMORY_DB_CONFIG

        import psycopg

        c = MEMORY_DB_CONFIG
        return psycopg.connect(
            host=c["host"], port=c["port"], user=c["user"],
            password=c["password"], dbname=c["dbname"], connect_timeout=5)

    monkeypatch.setattr(bookings_api, "BookingStore", _store)
    monkeypatch.setattr(bookings_api, "BookingService", _service)

    class _StubTripService:
        def get_trip(self, trip_id, *, tenant_id, owner_id):
            if trip_id != _TRIP or tenant_id != _TENANT or owner_id != _USER:
                raise TripNotFound("行程不存在")
            return {"trip_id": trip_id}

    monkeypatch.setattr(bookings_api, "TripService", _StubTripService)

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _quote(rest_client, **overrides):
    payload = {"commerce_type": "hotel", "search_params": _SEARCH,
               "selection": {"index": 1}}
    payload.update(overrides)
    return rest_client.post(
        f"/travel/v2/trips/{_TRIP}/bookings/quote",
        headers=_HEADERS, json=payload)


# =============================================
# 正向三段（fake provider 全链）
# =============================================


def test_quote_returns_confirmation_card(rest_client):
    resp = _quote(rest_client)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "awaiting_confirmation"
    assert body["booking_intent_id"] and body["order_id"] and body["quote_id"]
    # 金额互检要素：金额/币种/截止时间全部在场（Decimal → 字符串）
    assert body["amount"] and isinstance(body["amount"], str)
    assert body["currency"] in ("JPY", "CNY", "USD")
    assert body["internal_expires_at"]
    assert body["message"]  # 确认卡文案（reporter 白名单）


def test_confirm_then_status_full_chain(rest_client):
    intent = _quote(rest_client).json()["booking_intent_id"]
    resp = rest_client.post(
        f"/travel/v2/trips/{_TRIP}/bookings/{intent}/confirm",
        headers=_HEADERS)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["result"] in ("booked", "executed")
    assert body["order"]["status"] == "booked"
    assert body["message"]  # 执行话术（fake 披露）

    status = rest_client.get(
        f"/travel/v2/trips/{_TRIP}/bookings/{intent}",
        headers=_HEADERS)
    assert status.status_code == 200
    sbody = status.json()
    assert sbody["status"] == "booked"
    assert sbody["events"]  # 事件账可见


# =============================================
# 鉴权 fail-closed
# =============================================


def test_unauthenticated_rejected_401(rest_client):
    resp = _quote(rest_client)
    no_auth = rest_client.post(
        f"/travel/v2/trips/{_TRIP}/bookings/quote",
        headers={"X-Auth-Type": "jwt", "X-Tenant-Id": _TENANT},
        json={"commerce_type": "hotel", "search_params": _SEARCH,
              "selection": {"index": 1}})
    assert no_auth.status_code == 401
    del resp


def test_trip_not_owned_or_missing_404(rest_client):
    resp = rest_client.post(
        "/travel/v2/trips/trip-of-someone-else/bookings/quote",
        headers=_HEADERS,
        json={"commerce_type": "hotel", "search_params": _SEARCH,
              "selection": {"index": 1}})
    assert resp.status_code == 404


def test_order_of_other_user_hidden_404(rest_client):
    intent = _quote(rest_client).json()["booking_intent_id"]
    other = dict(_HEADERS, **{"X-User-Id": "u-attacker"})
    resp = rest_client.get(
        f"/travel/v2/trips/{_TRIP}/bookings/{intent}", headers=other)
    assert resp.status_code == 404


# =============================================
# provider off / 参数校验
# =============================================


def test_provider_off_returns_503_not_fake(rest_client, monkeypatch):
    from backend.config import travel_booking as cfg

    monkeypatch.setattr(cfg, "TRAVEL_BOOKING_PROVIDER", "off")
    resp = _quote(rest_client)
    assert resp.status_code == 503
    assert resp.json()["detail"]["code"] == "BOOKING_PROVIDER_OFF"


def test_bad_params_rejected_422(rest_client):
    resp = rest_client.post(
        f"/travel/v2/trips/{_TRIP}/bookings/quote",
        headers=_HEADERS,
        json={"commerce_type": "hotel",
              "search_params": {"city": "大阪",
                                "check_in": "2020-01-01",
                                "check_out": "2020-01-02"},
              "selection": {"index": 1}})
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "INVALID_BOOKING_PARAM"
