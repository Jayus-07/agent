from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.travel_v2.models.trip import TripDocumentV2


def valid_document() -> dict:
    return {
        "schema_version": 2,
        "brief": {
            "origin": "上海",
            "destination": "杭州",
            "timezone": "Asia/Shanghai",
            "start_date": "2026-10-20",
            "day_count": 1,
            "travelers": {"adults": 2, "children": 0},
            "budget": {"amount": 3000, "currency": "CNY"},
            "pace": "balanced",
            "interests": ["园林"],
            "requirements": [],
        },
        "selections": [
            {"place_id": "west-lake", "name": "西湖", "preference": "must_visit"}
        ],
        "days": [
            {
                "day_id": "day-1",
                "date": "2026-10-20",
                "title": "湖畔漫游",
                "items": [
                    {
                        "item_id": "item-1",
                        "kind": "place",
                        "title": "西湖",
                        "start_time": "09:00",
                        "duration_min": 120,
                        "fixed_start": False,
                        "place": {
                            "place_id": "west-lake",
                            "name": "西湖",
                            "lat": 30.24,
                            "lng": 120.15,
                            "address": "杭州市西湖区",
                            "facts": {
                                "verification": "verified",
                                "source": "provider",
                                "observed_at": "2026-10-09T08:00:00+08:00",
                            },
                        },
                        "must_visit": True,
                        "locked": False,
                        "note": "",
                    }
                ],
                "legs": [],
            }
        ],
        "totals": {
            "currency": "CNY",
            "estimated_cost": 300,
            "transit_min": 0,
            "distance_m": None,
        },
        "health": {"status": "ok", "issues": []},
    }


def test_rejects_legacy_itinerary_shape_instead_of_storing_it_as_v2() -> None:
    with pytest.raises(ValidationError):
        TripDocumentV2.model_validate({"brief": {}, "days": [], "status": "confirmed"})


def test_requires_day_count_to_match_days_array() -> None:
    document = valid_document()
    document["brief"]["day_count"] = 2

    with pytest.raises(ValidationError, match="day_count"):
        TripDocumentV2.model_validate(document)


def test_rejects_leg_reference_to_missing_item() -> None:
    document = valid_document()
    document["days"][0]["legs"] = [
        {
            "leg_id": "leg-1",
            "from_item_id": "item-1",
            "to_item_id": "missing-item",
            "selected_mode": "walk",
            "duration_min": 10,
            "distance_m": 500,
            "cost_cny": 0,
            "reliability": "estimated",
            "source": "route-estimator",
            "observed_at": None,
        }
    ]

    with pytest.raises(ValidationError, match="Leg item reference"):
        TripDocumentV2.model_validate(document)


def test_rejects_duplicate_item_ids_across_days() -> None:
    document = valid_document()
    duplicate_day = {
        **document["days"][0], "day_id": "day-2", "date": "2026-10-21",
    }
    document["brief"]["day_count"] = 2
    document["days"].append(duplicate_day)

    with pytest.raises(ValidationError, match="item_id"):
        TripDocumentV2.model_validate(document)


def test_accepts_coordinate_free_activity_without_invented_leg() -> None:
    document = valid_document()
    document["days"][0]["items"] = [
        {
            "item_id": "meal-1",
            "kind": "activity",
            "activity_type": "meal",
            "title": "午餐",
            "start_time": "12:00",
            "duration_min": 60,
            "fixed_start": True,
            "place": None,
            "must_visit": False,
            "locked": True,
            "note": "餐厅待确认",
        }
    ]
    document["days"][0]["legs"] = []

    parsed = TripDocumentV2.model_validate(document)

    assert parsed.days[0].items[0].place is None
    assert parsed.days[0].legs == []


def test_v2_document_has_empty_lodging_and_intercity_arrangement_collections() -> None:
    parsed = TripDocumentV2.model_validate(valid_document())

    assert parsed.arrangements.lodgings == []
    assert parsed.arrangements.intercity_trains == []


def test_v2_document_stores_selected_lodging_and_train_as_plan_references() -> None:
    document = valid_document()
    document["brief"]["origin"] = "上海"
    document["brief"]["day_count"] = 2
    document["days"].append({
        "day_id": "day-2", "date": "2026-10-21", "title": "返程",
        "items": [], "legs": [],
    })
    document["days"][0]["items"][0]["start_time"] = "11:00"
    document["arrangements"] = {
        "lodgings": [{
            "selection_id": "amap:hotel-1",
            "merchant": {
                "merchant_id": "hotel-1", "name": "西湖酒店",
                "address": "杭州市西湖区", "lat": 30.24, "lng": 120.15,
                "rating": 4.6, "open_status": "未知", "source": "amap",
                "observed_at": "2026-10-09T08:00:00+08:00",
            },
            "check_in": "2026-10-20", "check_out": "2026-10-21",
            "source": "amap", "queried_at": "2026-10-09T08:00:00+08:00",
            "verification": "unknown", "booking_status": "not_booked",
            "nightly_price_cny": None,
        }],
        "intercity_trains": [{
            "selection_id": "12306:train-1",
            "train_code": "G1", "travel_date": "2026-10-20",
            "departure_station": "上海虹桥", "arrival_station": "杭州东",
            "departure_time": "08:00", "arrival_time": "09:00",
            "duration_min": 60, "tickets": {"二等座": "有"},
            "fares": {"二等座": 73.0}, "ticket_source": "12306",
            "fare_source": "12306", "queried_at": "2026-10-09T08:00:00+08:00",
            "verification": "delayed", "booking_status": "not_booked",
        }],
    }

    parsed = TripDocumentV2.model_validate(document)

    assert parsed.arrangements.lodgings[0].booking_status == "not_booked"
    assert parsed.arrangements.lodgings[0].nightly_price_cny is None
    assert parsed.arrangements.intercity_trains[0].fares == {"二等座": 73.0}


def test_rejects_lodging_with_checkout_before_checkin() -> None:
    document = valid_document()
    document["arrangements"] = {
        "lodgings": [{
            "selection_id": "amap:hotel-1",
            "merchant": {
                "merchant_id": "hotel-1", "name": "西湖酒店",
                "source": "amap", "observed_at": "2026-10-09T08:00:00+08:00",
            },
            "check_in": "2026-10-21", "check_out": "2026-10-20",
            "source": "amap", "queried_at": "2026-10-09T08:00:00+08:00",
            "verification": "unknown", "booking_status": "not_booked",
            "nightly_price_cny": None,
        }],
        "intercity_trains": [],
    }

    with pytest.raises(ValidationError, match="check_out"):
        TripDocumentV2.model_validate(document)


def test_outbound_train_arrival_must_leave_time_before_first_day_visits() -> None:
    document = valid_document()
    document["brief"]["origin"] = "上海"
    document["arrangements"] = {
        "lodgings": [],
        "intercity_trains": [{
            "selection_id": "12306:train-1", "train_code": "G1",
            "travel_date": "2026-10-20", "departure_station": "上海虹桥",
            "arrival_station": "杭州东", "departure_time": "08:00",
            "arrival_time": "09:00", "duration_min": 60,
            "tickets": {"二等座": "有"}, "fares": {},
            "ticket_source": "12306", "fare_source": None,
            "queried_at": "2026-10-09T08:00:00+08:00",
            "verification": "delayed", "booking_status": "not_booked",
        }],
    }

    with pytest.raises(ValidationError, match="首日"):
        TripDocumentV2.model_validate(document)
