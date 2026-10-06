"""STOP1 Requirement Agent 验收：路线、换城门、日期与缺槽契约。"""
from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

import pytest

from backend.travel.agents.requirement_agent import (
    extract_destination,
    extract_days,
    extract_origin,
    extract_relative_date_expr,
    extract_start_date,
)
from backend.travel.core.events import travel_event_scope
from backend.travel.core.intent import TravelIntent, classify_intent
from backend.travel.graph_state import brief_fingerprint
from backend.travel.models.brief import TravelBrief
from backend.travel.slot_filler import slot_filler_node


@pytest.mark.parametrize(
    ("message", "origin", "destination"),
    [
        ("我从上海出发去杭州玩三天", "上海", "杭州"),
        ("我从上海出发，下周去杭州玩三天", "上海", "杭州"),
        ("下周从上海出发去杭州", "上海", "杭州"),
        ("上海出发，杭州三日游", "上海", "杭州"),
        ("我现在在上海，想去杭州", "上海", "杭州"),
        ("我想从杭州去上海", "杭州", "上海"),
        ("从北京出发，我们下周准备去苏州", "北京", "苏州"),
    ],
)
def test_acceptance_route_roles(message: str, origin: str, destination: str):
    assert extract_origin(message) == origin
    assert extract_destination(message) == destination


def test_chinese_days_is_a_plan_signal():
    message = "我从上海出发，下周去杭州玩三天"
    assert extract_days(message) == 3
    assert classify_intent(message, has_destination=True) is TravelIntent.PLAN


def test_relative_dates_are_deterministic_and_cover_today_next_week():
    today = date(2026, 10, 6)
    cases = {
        "今天": today,
        "明天": today + timedelta(days=1),
        "后天": today + timedelta(days=2),
        "本周末": date(2026, 10, 10),
        "这个周末": date(2026, 10, 10),
        "下周": date(2026, 10, 12),
        "下周五": date(2026, 10, 16),
        "下周末": date(2026, 10, 17),
    }
    for expression, expected in cases.items():
        assert extract_start_date(expression, today=today) == expected
        assert extract_start_date(expression, today=today) == expected
        assert extract_relative_date_expr(expression) == expression


def test_destination_change_is_explicit_and_query_does_not_merge():
    previous = TravelBrief(destination="杭州", days=3)
    base = {
        "brief": previous.model_dump(),
        "brief_fingerprint": brief_fingerprint(previous),
        "itinerary": {"plan_version": 2},
    }

    change_events = []
    with travel_event_scope(change_events.append):
        changed = slot_filler_node({**base, "user_message": "改成苏州"})
    assert changed["brief"]["destination"] == "苏州"
    assert changed["brief_change_reason"]
    assert changed["destination_change"] is True
    assert next(e for e in change_events if e["event"] == "requirement.interpreted")[
        "destination_change"
    ] is True

    with patch(
        "backend.travel.services.live_search_service.search_zhihu_guides",
        return_value={"results": []},
    ):
        query_events = []
        with travel_event_scope(query_events.append):
            queried = slot_filler_node({
                **base,
                "user_message": "苏州有什么好吃的？",
            })
    assert queried["intent"] == TravelIntent.QUERY_STATIC.value
    assert queried["brief"]["destination"] == "杭州"
    assert queried["query_destination"] == "苏州"
    assert "itinerary" not in queried
    assert queried["destination_change"] is False
    assert next(e for e in query_events if e["event"] == "requirement.interpreted")[
        "destination_change"
    ] is False


def test_default_party_size_is_explicit_in_requirement_event():
    events = []
    with travel_event_scope(events.append):
        result = slot_filler_node({"user_message": "杭州三天"})
    interpreted = next(e for e in events if e["event"] == "requirement.interpreted")
    assert result["brief"]["party_size"] == 1
    assert interpreted["slot_sources"]["party_size"] == "default"
    assert any("按 1 人默认" in note for note in interpreted["assumptions"])


def test_missing_required_slots_clarify_without_fake_itinerary():
    result = slot_filler_node({"user_message": "我想出去玩"})
    assert result["brief_missing"] == ["destination", "days"]
    assert result["clarifications"]
    assert "itinerary" not in result

    partial = slot_filler_node({"user_message": "想去杭州"})
    assert partial["brief"]["destination"] == "杭州"
    assert partial["brief_missing"] == ["days"]
    assert "itinerary" not in partial
