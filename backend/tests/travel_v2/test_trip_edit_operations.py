from __future__ import annotations

from copy import deepcopy

import pytest

from backend.travel_v2.services.trip_edit_service import TripEditService
from backend.tests.travel_v2.test_trip_document import valid_document


def _two_item_document() -> dict:
    document = valid_document()
    first = document["days"][0]["items"][0]
    second = deepcopy(first)
    second.update({
        "item_id": "item-2", "title": "灵隐寺", "start_time": "12:00",
        "place": {**first["place"], "place_id": "lingyin", "name": "灵隐寺"},
    })
    document["days"][0]["items"].append(second)
    document["days"][0]["legs"] = [{
        "leg_id": "leg-1", "from_item_id": "item-1", "to_item_id": "item-2",
        "selected_mode": "transit", "duration_min": 25, "distance_m": 5000,
        "cost_cny": 2, "reliability": "verified", "source": "route-provider",
        "observed_at": "2026-10-09T08:00:00+08:00",
    }]
    return document


def _apply(document: dict, operation: dict) -> dict:
    return TripEditService._apply_structured_operation(document, operation)


def test_add_activity_creates_coordinate_free_item_and_unknown_route() -> None:
    result = _apply(valid_document(), {
        "op": "add_activity", "day_id": "day-1", "activity_type": "rest",
        "title": "咖啡休息", "start_time": "12:00", "duration_min": 30,
    })

    day = result["days"][0]
    assert day["items"][-1]["place"] is None
    assert day["items"][-1]["activity_type"] == "rest"
    assert day["legs"][0]["reliability"] == "unavailable"
    assert day["legs"][0]["duration_min"] is None


def test_add_and_remove_day_keep_count_and_dates_consistent() -> None:
    first = _apply(valid_document(), {"op": "add_day"})
    assert first["brief"]["day_count"] == 2
    assert first["days"][1]["date"] == "2026-10-21"

    last = _apply(first, {"op": "remove_day", "day_id": first["days"][1]["day_id"]})
    assert last["brief"]["day_count"] == 1
    assert [day["date"] for day in last["days"]] == ["2026-10-20"]


def test_transport_change_clears_old_route_facts() -> None:
    result = _apply(_two_item_document(), {
        "op": "set_leg_mode", "from_item_id": "item-1", "to_item_id": "item-2",
        "selected_mode": "walk",
    })

    leg = result["days"][0]["legs"][0]
    assert leg["selected_mode"] == "walk"
    assert leg["duration_min"] is None
    assert leg["distance_m"] is None
    assert leg["cost_cny"] is None
    assert leg["reliability"] == "unavailable"
    assert leg["source"] is None
    assert leg["observed_at"] is None
    assert result["totals"]["transit_min"] == 0
    assert result["totals"]["distance_m"] is None


def test_reorder_rebuilds_new_edges_without_reusing_stale_route_values() -> None:
    document = _two_item_document()
    third = deepcopy(document["days"][0]["items"][1])
    third.update({"item_id": "item-3", "title": "西溪湿地", "start_time": "15:00"})
    third["place"]["place_id"] = "xixi"
    third["place"]["name"] = "西溪湿地"
    document["days"][0]["items"].append(third)

    result = _apply(document, {
        "op": "reorder_day", "day_id": "day-1",
        "item_ids": ["item-1", "item-3", "item-2"],
    })

    assert [(leg["from_item_id"], leg["to_item_id"]) for leg in result["days"][0]["legs"]] == [
        ("item-1", "item-3"), ("item-3", "item-2"),
    ]
    assert all(leg["reliability"] == "unavailable" for leg in result["days"][0]["legs"])
    assert all(leg["distance_m"] is None for leg in result["days"][0]["legs"])


def test_schedule_overlap_is_rejected_before_a_write() -> None:
    document = _two_item_document()
    document["days"][0]["items"][0]["duration_min"] = 210

    with pytest.raises(ValueError, match="时间或路程安排冲突"):
        TripEditService._validate_edit_schedule(document)


def test_verified_opening_hours_are_checked_but_unverified_hours_are_not() -> None:
    document = valid_document()
    facts = document["days"][0]["items"][0]["place"]["facts"]
    facts["verification"] = "verified"
    facts["opening_hours"] = {"open_time": "10:00", "close_time": "18:00", "closed_weekdays": []}

    with pytest.raises(ValueError, match="超出已核实营业时间"):
        TripEditService._validate_edit_schedule(document)

    facts["verification"] = "unknown"
    TripEditService._validate_edit_schedule(document)


def test_locked_and_fixed_items_cannot_be_dropped_or_reordered() -> None:
    current = _two_item_document()
    current["days"][0]["items"][0]["locked"] = True
    reordered = _apply(current, {
        "op": "reorder_day", "day_id": "day-1", "item_ids": ["item-2", "item-1"],
    })

    with pytest.raises(ValueError, match="cannot be reordered"):
        TripEditService._validate_protected_items(current, reordered)

    removed = deepcopy(current)
    removed["days"][0]["items"] = removed["days"][0]["items"][1:]
    with pytest.raises(ValueError, match="locked item cannot be removed"):
        TripEditService._validate_protected_items(current, removed)


def test_remove_last_day_is_rejected() -> None:
    with pytest.raises(ValueError, match="至少保留一天"):
        _apply(valid_document(), {"op": "remove_day", "day_id": "day-1"})


def test_cost_affecting_edit_marks_estimate_for_review_without_fabricating_a_total() -> None:
    document = valid_document()
    document["totals"]["estimated_cost"] = None

    result = _apply(document, {
        "op": "add_activity", "day_id": "day-1", "activity_type": "custom",
        "title": "演出", "duration_min": 90,
    })

    assert result["totals"]["estimated_cost"] is None
    assert result["health"]["status"] == "needs_attention"
    assert any(issue["code"] == "BUDGET_ESTIMATE_UNKNOWN" for issue in result["health"]["issues"])


def test_tencent_place_candidate_cannot_claim_unreturned_price_or_hours() -> None:
    place = deepcopy(valid_document()["days"][0]["items"][0]["place"])
    place["place_id"] = "new-poi"
    place["facts"]["source"] = "tencent:lbs"
    place["facts"]["verification"] = "verified"
    place["facts"]["opening_hours"] = {"open_time": "09:00", "close_time": "17:00"}

    with pytest.raises(ValueError, match="没有营业时间或价格核实结果"):
        _apply(valid_document(), {
            "op": "add_place", "day_id": "day-1", "selection_id": "unchecked",
            "place": place, "duration_min": 90,
        })
