"""旅游实时增强事件契约红测。"""
from __future__ import annotations

from backend.travel.core.events import build_travel_event
from backend.travel.agents.requirement_agent import extract_fresh_brief, extract_origin


def test_requirement_interpreted_event_carries_user_visible_brief():
    event = build_travel_event(
        "requirement.interpreted",
        agent="requirement",
        brief={"destination": "福州", "days": 2},
        assumptions=["未提供出发日期，按第 1 天展示"],
        missing=[],
    )
    assert event["event"] == "requirement.interpreted"
    assert event["brief"]["destination"] == "福州"
    assert event["assumptions"]


def test_tool_result_event_carries_real_preview_items():
    event = build_travel_event(
        "tool.result",
        agent="research",
        tool="map_merchant_search_tool",
        status="success",
        data_status="available",
        result_count=1,
        preview=[{"name": "真实商户", "source": "amap"}],
    )
    assert event["preview"][0]["source"] == "amap"


def test_travel_event_source_cannot_be_overwritten_by_provider_summary():
    event = build_travel_event("tool.result", source="amap", tool="x")
    assert event["source"] == "travel"


def test_route_sentence_extracts_origin_and_destination_for_train_search():
    brief = extract_fresh_brief(
        "从福州出发去厦门，2026-10-03玩2天，请查高铁票",
    )
    assert extract_origin("从福州出发去厦门") == "福州"
    assert brief.origin == "福州"
    assert brief.destination == "厦门"


def test_train_search_auto_triggered_by_origin_and_date():
    """#7a：有出发地+出发日期即自动查车票，不再要求触发词。"""
    from backend.travel.experts.transit import _needs_train_search
    from backend.travel.models.brief import TravelBrief
    from datetime import date

    brief_with = TravelBrief(destination="厦门", days=2, party_size=2,
                             origin="福州", start_date=date(2026, 10, 5))
    assert _needs_train_search("", brief_with)  # 无触发词也查
    assert _needs_train_search("随便聊聊", brief_with)

    brief_local = TravelBrief(destination="福州", days=2, party_size=2,
                              origin="", start_date=date(2026, 10, 5))
    assert not _needs_train_search("查高铁票", brief_local)  # 单城市无出发地不查

    brief_no_date = TravelBrief(destination="厦门", days=2, party_size=2, origin="福州")
    assert not _needs_train_search("查高铁票", brief_no_date)  # 无日期不查
