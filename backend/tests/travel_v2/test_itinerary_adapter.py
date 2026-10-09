from __future__ import annotations

import pytest

from backend.travel_v2.adapters.itinerary_adapter import itinerary_to_trip_document


def agent_itinerary() -> dict:
    return {
        "brief": {
            "destination": "杭州", "origin": "上海",
            "start_date": "2026-10-20", "days": 1,
            "party_size": 2, "adults": 2, "children": 0,
            "budget_cny": 3000, "pace": "moderate",
            "preferences": ["人文"], "must_go": ["西湖"],
            "optional_go": [], "avoid": [],
        },
        "days": [
            {
                "day_index": 1, "day_date": "2026-10-20", "items": [
                    {
                        "title": "西湖", "kind": "visit", "start": "09:00",
                        "end": "11:00", "minutes": 120, "wait_minutes": 0,
                        "poi": {
                            "poi_id": "west-lake", "name": "西湖", "city": "杭州",
                            "category": "景点", "lat": 30.24, "lng": 120.15,
                            "open_time": "08:00", "close_time": "17:00",
                            "closed_weekdays": [], "suggested_minutes": 120,
                            "ticket_cny": 0, "tags": ["自然"], "rating": 4.8,
                            "required": True, "source": "tencent:lbs",
                            "observed_at": "2026-10-09T08:00:00+08:00",
                            "verification_status": "verified",
                            "location_status": "verified", "reason": "实时检索",
                        },
                        "note": "预约待确认",
                    }
                ],
                "legs": [], "active_minutes": 120,
                "transit_minutes": 0, "cost_cny": 0,
            }
        ],
        "cost": {"tickets": 0, "meals": 100, "lodging": 0, "transit": 20},
        "sources": ["tencent:lbs"], "warnings": [], "confidence": 0.9,
    }


def test_agent_output_is_mapped_to_strict_v2_snapshot_with_explicit_provenance() -> None:
    result = itinerary_to_trip_document(agent_itinerary())

    assert result.schema_version == 2
    assert result.brief.destination == "杭州"
    assert result.brief.timezone == "Asia/Shanghai"
    assert result.brief.day_count == 1
    assert result.days[0].items[0].place.place_id == "west-lake"
    assert result.days[0].items[0].place.facts.verification == "verified"
    assert result.days[0].items[0].must_visit is True
    assert result.selections[0].preference == "must_visit"
    assert result.health.status == "needs_attention"
    assert result.health.issues[0].code == "timezone_assumed"


def test_adapter_does_not_invent_route_values_for_coordinate_free_activities() -> None:
    raw = agent_itinerary()
    day = raw["days"][0]
    day["items"].append({
        "title": "午餐", "kind": "meal", "start": "12:00", "end": "13:00",
        "minutes": 60, "wait_minutes": 0, "poi": None, "note": "餐厅待定",
    })
    day["legs"] = [{
        "from_title": "西湖", "to_title": "午餐", "minutes": 18,
        "distance_km": 2.4, "mode": "drive", "cost_cny": 12,
        "source": "estimate:local", "observed_at": None,
        "traffic_aware": False, "is_estimate": True,
    }]

    result = itinerary_to_trip_document(raw)

    assert result.days[0].items[1].kind == "activity"
    assert result.days[0].items[1].activity_type == "meal"
    assert result.days[0].legs[0].reliability == "unavailable"
    assert result.days[0].legs[0].duration_min is None
    assert result.days[0].legs[0].distance_m is None
    assert result.days[0].legs[0].cost_cny is None


def test_adapter_preserves_existing_ids_when_agent_returns_same_places() -> None:
    raw = agent_itinerary()
    first = itinerary_to_trip_document(raw)
    raw["days"][0]["items"][0]["start"] = "10:00"

    updated = itinerary_to_trip_document(raw, previous_document=first)

    assert updated.days[0].day_id == first.days[0].day_id
    assert updated.days[0].items[0].item_id == first.days[0].items[0].item_id
