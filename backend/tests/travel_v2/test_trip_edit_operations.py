from __future__ import annotations

from copy import deepcopy

import pytest

from backend.tests.travel_v2.test_trip_document import valid_document
from backend.travel_v2.services.search_service import stable_selection_id
from backend.travel_v2.services.trip_edit_service import TripEditService
from backend.travel_v2.services.trip_service import canonical_request_hash


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


def test_meal_is_inserted_between_morning_and_evening_without_reordering_items() -> None:
    document = _two_item_document()
    day = document["days"][0]
    day["items"][0]["duration_min"] = 60
    day["items"][1].update({"start_time": "18:00", "fixed_start": True, "locked": True})
    day["legs"][0].update({"duration_min": 30, "reliability": "verified"})
    current = deepcopy(document)
    meal = {
        "item_id": "meal-lunch", "kind": "activity", "activity_type": "meal",
        "title": "午餐", "start_time": "12:00", "duration_min": 60,
        "fixed_start": False, "place": None, "must_visit": False,
        "locked": False, "note": "午餐",
    }

    TripEditService._insert_meal_in_order(day, meal)
    TripEditService._refresh_derived_totals_and_budget(document, "add_meal")
    TripEditService._validate_edit_schedule(document)
    TripEditService._validate_protected_items(current, document)

    assert [item["item_id"] for item in day["items"]] == [
        "item-1", "meal-lunch", "item-2",
    ]
    assert [item["item_id"] for item in day["items"] if item["item_id"] != "meal-lunch"] == [
        "item-1", "item-2",
    ]
    assert [(leg["from_item_id"], leg["to_item_id"]) for leg in day["legs"]] == [
        ("item-1", "meal-lunch"), ("meal-lunch", "item-2"),
    ]
    assert all(leg["reliability"] == "unavailable" for leg in day["legs"])
    assert all(leg["duration_min"] is None for leg in day["legs"])
    assert document["totals"]["transit_min"] == 0
    assert document["totals"]["distance_m"] is None


def test_add_meal_saves_a_lunch_between_morning_and_evening_items(monkeypatch) -> None:
    document = valid_document()
    day = document["days"][0]
    day["items"][0]["duration_min"] = 60
    day["items"].append({
        "item_id": "evening-event", "kind": "activity", "activity_type": "custom",
        "title": "晚间活动", "start_time": "18:00", "duration_min": 60,
        "fixed_start": True, "place": None, "must_visit": False,
        "locked": True, "note": "",
    })
    service = TripEditService()
    base_document = deepcopy(document)
    monkeypatch.setattr(
        service, "_get_trip_document",
        lambda *_args, **_kwargs: {"document": deepcopy(base_document)},
    )
    calls = []
    hashes = []
    command_types = []

    def capture_commit(**kwargs):
        snapshot = kwargs["document"].model_dump(mode="json")
        calls.append(snapshot)
        command_types.append(kwargs["command_type"])
        hashes.append(canonical_request_hash({
            "trip_id": str(kwargs["trip_id"]),
            "expected_revision": kwargs["expected_revision"],
            "command_type": kwargs["command_type"],
            "change_summary": kwargs["change_summary"],
            "document": snapshot,
        }))
        TripEditService._validate_edit_schedule(snapshot)
        return {"saved": True, "revision": 2}

    monkeypatch.setattr(service, "apply_document", capture_commit)
    command = {
        "expected_revision": 1, "day_id": "day-1", "meal_period": "lunch",
        "selection_id": stable_selection_id("food", "amap", "food-1"),
        "merchant": {
            "merchant_id": "food-1", "name": "午餐餐厅", "source": "amap",
            "observed_at": "2026-10-19T08:00:00+08:00",
        },
    }
    call_args = {
        "trip_id": "trip-1", "tenant_id": "tenant-1", "owner_id": "owner-1",
        "actor_id": "owner-1", "idempotency_key": "meal-lunch",
        "command": command,
    }
    result = service.add_meal(**call_args)
    replay_candidate = service.add_meal(**call_args)

    items = calls[0]["days"][0]["items"]
    assert result == {"saved": True, "revision": 2}
    assert replay_candidate == result
    assert len(calls) == 2
    assert hashes[0] == hashes[1]
    assert calls[0] == calls[1]
    assert command_types == ["structured_edit", "structured_edit"]
    assert [(item["title"], item["start_time"]) for item in items] == [
        ("西湖", "09:00"), ("午餐餐厅", "12:00"), ("晚间活动", "18:00"),
    ]
    assert items[2]["locked"] is True
    assert items[2]["fixed_start"] is True


def test_meal_leg_ids_are_deterministic_and_keep_unaffected_route_facts() -> None:
    document = _two_item_document()
    day = document["days"][0]
    day["items"][0]["duration_min"] = 60
    day["items"][1].update({"start_time": "18:00", "duration_min": 60})
    third = {
        "item_id": "item-3", "kind": "activity", "activity_type": "rest",
        "title": "晚间休息", "start_time": "20:00", "duration_min": 30,
        "fixed_start": False, "place": None, "must_visit": False,
        "locked": False, "note": "",
    }
    day["items"].append(third)
    preserved_route = {
        "leg_id": "verified-evening-route", "from_item_id": "item-2",
        "to_item_id": "item-3", "selected_mode": "drive", "duration_min": 30,
        "distance_m": 12000, "cost_cny": 5, "reliability": "verified",
        "source": "route-provider", "observed_at": "2026-10-09T08:00:00+08:00",
    }
    day["legs"] = [preserved_route]
    meal = {
        "item_id": "meal-lunch", "kind": "activity", "activity_type": "meal",
        "title": "午餐", "start_time": "12:00", "duration_min": 60,
        "fixed_start": False, "place": None, "must_visit": False,
        "locked": False, "note": "午餐",
    }
    first = deepcopy(document)
    second = deepcopy(document)

    TripEditService._insert_meal_in_order(first["days"][0], deepcopy(meal))
    TripEditService._insert_meal_in_order(second["days"][0], deepcopy(meal))

    first_legs = first["days"][0]["legs"]
    second_legs = second["days"][0]["legs"]
    assert [leg["leg_id"] for leg in first_legs] == [
        leg["leg_id"] for leg in second_legs
    ]
    assert first_legs[-1] == preserved_route
    assert second_legs[-1] == preserved_route
    assert all(
        leg["reliability"] == "unavailable" and leg["duration_min"] is None
        for leg in first_legs[:-1]
    )
    TripEditService._validate_edit_schedule(first)
    TripEditService._validate_edit_schedule(second)


def test_meal_with_a_real_time_overlap_is_rejected() -> None:
    document = _two_item_document()
    day = document["days"][0]
    day["items"][0]["duration_min"] = 181
    day["items"][1]["start_time"] = "18:00"
    meal = {
        "item_id": "meal-lunch", "kind": "activity", "activity_type": "meal",
        "title": "午餐", "start_time": "12:00", "duration_min": 60,
        "fixed_start": False, "place": None, "must_visit": False,
        "locked": False, "note": "午餐",
    }

    TripEditService._insert_meal_in_order(day, meal)

    with pytest.raises(ValueError, match="时间或路程安排冲突"):
        TripEditService._validate_edit_schedule(document)


def test_schedule_comparison_preserves_seconds_precision() -> None:
    document = _two_item_document()
    day = document["days"][0]
    day["items"][0].update({"start_time": "09:00:59", "duration_min": 60})
    day["items"][1]["start_time"] = "10:00:00"
    day["legs"][0].update({"duration_min": None, "reliability": "unavailable"})

    with pytest.raises(ValueError, match="时间或路程安排冲突"):
        TripEditService._validate_edit_schedule(document)


def test_verified_opening_hours_comparison_preserves_seconds_precision() -> None:
    document = valid_document()
    item = document["days"][0]["items"][0]
    item["start_time"] = "09:00:00"
    item["duration_min"] = 30
    facts = item["place"]["facts"]
    facts["verification"] = "verified"
    facts["opening_hours"] = {
        "open_time": "09:00:01", "close_time": "10:00:00",
        "closed_weekdays": [],
    }

    with pytest.raises(ValueError, match="超出已核实营业时间"):
        TripEditService._validate_edit_schedule(document)


def test_shared_commit_gate_rejects_before_trip_revision_or_ledger_writes() -> None:
    calls = []

    class TrackingRepository:
        def update_document_cas(self, *_args, **_kwargs):
            calls.append("trip")

        def insert(self, *_args, **_kwargs):
            calls.append("revision_or_operation")

    document = _two_item_document()
    conflicting = deepcopy(document)
    conflicting["days"][0]["items"][1]["start_time"] = "10:30"
    repository = TrackingRepository()
    service = TripEditService(
        trip_repository=repository,
        revision_repository=repository,
        edit_operation_repository=repository,
    )

    with pytest.raises(ValueError, match="时间或路程安排冲突"):
        service._commit_locked(
            object(), trip_id="trip-1", tenant_id="tenant-1",
            owner_id="owner-1", actor_id="owner-1", expected_revision=1,
            idempotency_key="blocked", request_hash="hash",
            command_type="structured_edit", change_summary="冲突行程",
            current={"document": document, "title": "测试行程"},
            snapshot=conflicting,
        )

    assert calls == []


def test_known_route_buffer_blocks_but_unknown_duration_stays_unknown() -> None:
    document = _two_item_document()
    document["days"][0]["items"][0]["duration_min"] = 60
    document["days"][0]["items"][1]["start_time"] = "10:15"

    with pytest.raises(ValueError, match="时间或路程安排冲突"):
        TripEditService._validate_edit_schedule(document)

    leg = document["days"][0]["legs"][0]
    leg.update({"duration_min": None, "reliability": "unavailable"})
    TripEditService._validate_edit_schedule(document)
    assert leg["duration_min"] is None
    assert leg["reliability"] == "unavailable"


def test_legacy_item_without_start_time_remains_compatible() -> None:
    document = valid_document()
    document["days"][0]["items"][0]["start_time"] = None

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
