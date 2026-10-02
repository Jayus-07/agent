"""旅游 v3 P0-A：意图、识别边界与响应契约。"""
from unittest.mock import patch

import pytest

from backend.travel.agents.requirement_agent import (
    _iter_city_hits, extract_days, extract_destination, extract_origin,
)
from backend.travel.core.intent import TravelIntent, classify_intent
from backend.travel.graph_state import TravelGraphState, brief_fingerprint
from backend.travel.models.brief import TravelBrief
from backend.travel.models.graph_result import build_travel_graph_result
from backend.travel.reporter import _assemble
from backend.travel.services.inspiration_service import fetch_destination_inspiration
from backend.travel.services.live_search_service import LiveSearchError
from backend.travel.slot_filler import slot_filler_node
from backend.travel.supervisor import decide


@pytest.mark.parametrize("message,itinerary,destination,expected", [
    ("丽江好玩吗", False, True, TravelIntent.QUERY_STATIC),
    ("明天玉龙雪山开门吗", False, False, TravelIntent.QUERY_DYNAMIC),
    ("丽江三天，直接规划", False, True, TravelIntent.PLAN),
    ("把第二天下午换成室内", True, True, TravelIntent.MODIFY),
    ("把第二天下午换成室内，顺便查天气", True, True, TravelIntent.MODIFY),
    ("周末从福州去哪玩", False, False, TravelIntent.DISCOVER),
    ("福州2天", False, True, TravelIntent.PLAN),
    ("丽江3天好玩吗", False, True, TravelIntent.QUERY_STATIC),
    ("丽江3天天气如何", False, True, TravelIntent.QUERY_DYNAMIC),
    ("丽江自由行怎么样", False, True, TravelIntent.QUERY_STATIC),
    ("丽江自驾游怎么样", True, True, TravelIntent.QUERY_STATIC),
    ("丽江路线怎么样", True, True, TravelIntent.QUERY_STATIC),
    ("近3天订单量", False, False, None),
    ("你好", False, False, None),
    ("", False, False, None),
])
def test_intent_matrix(message, itinerary, destination, expected):
    assert classify_intent(message, has_itinerary=itinerary,
                           has_destination=destination) is expected


@pytest.mark.parametrize("message,expected", [
    ("南京路", []), ("北海公园", []), ("三明治", []),
    ("鹭岛", [(0, "厦门")]), ("丽江", [(0, "丽江")]),
    ("西安", [(0, "西安")]),
    ("DALI", [(0, "大理")]), ("dalian", []),
    ("xianyang", []),
])
def test_city_scan_guards(message, expected):
    assert _iter_city_hits(message) == expected


def test_route_and_negated_destination():
    assert extract_destination("从福州出发去丽江玩3天") == "丽江"
    assert extract_origin("从福州出发去丽江玩3天") == "福州"
    assert extract_destination("不去厦门了，重新规划杭州两天") == "杭州"


@pytest.mark.parametrize("payload,status", [
    ({"results": []}, "empty"),
    ({"results": [{"title": "古城慢游", "url": "https://www.zhihu.com/a", "summary": "体验观点"}]}, "available"),
])
def test_inspiration_states(payload, status):
    with patch("backend.travel.services.live_search_service.search_zhihu_guides", return_value=payload):
        result = fetch_destination_inspiration("丽江")
    assert result["status"] == status
    assert result["destination"] == "丽江"
    if status == "available":
        assert result["guides"][0]["url"] == "https://www.zhihu.com/a"
    else:
        assert result["guides"] == []


def test_inspiration_unavailable():
    with patch("backend.travel.services.live_search_service.search_zhihu_guides", side_effect=LiveSearchError("不可用")):
        assert fetch_destination_inspiration("丽江") == {
            "destination": "丽江", "guides": [], "status": "unavailable"}


@pytest.mark.parametrize("intent", ["query_static", "query_dynamic", "discover", "modify"])
def test_intent_gate_before_missing(intent):
    decision = decide({"intent": intent, "brief_missing": ["days"]})
    assert decision.action == "finish_report"
    assert "缺失" not in decision.reason


def test_static_renderer_sources_and_disclaimer():
    answer = _assemble({"intent": "query_static", "brief": {"destination": "丽江"},
                        "brief_missing": ["days"], "inspiration": {
                            "status": "available", "guides": [{"title": "慢游",
                            "summary": "体验观点", "url": "https://www.zhihu.com/a"}]}})
    assert "https://www.zhihu.com/a" in answer
    assert "官方" in answer and "观点" in answer
    assert "玩几天" not in answer


@pytest.mark.parametrize("intent,expected", [
    ("query_static", "暂时不可用"), ("query_dynamic", "可核验"),
    ("discover", "方向"), ("modify", "逐条改单"),
])
def test_reporter_exits(intent, expected):
    answer = _assemble({"intent": intent, "brief_missing": ["days"],
                        "brief": {"destination": "丽江"},
                        "inspiration": {"status": "unavailable"}})
    assert expected in answer
    assert "https://" not in answer


def test_static_slot_integration():
    with patch("backend.travel.services.live_search_service.search_zhihu_guides", return_value={"results": []}):
        result = slot_filler_node({"user_message": "丽江好玩吗"})
    assert result["intent"] == "query_static"
    assert result["brief"]["destination"] == "丽江"
    assert result["inspiration"]["status"] == "empty"


def test_modify_avoid_uses_existing_replan():
    brief = TravelBrief(destination="厦门", days=3)
    result = slot_filler_node({"user_message": "不去鼓浪屿了", "brief": brief.model_dump(),
                              "brief_fingerprint": brief_fingerprint(brief),
                              "itinerary": {"plan_version": 1}})
    assert result["intent"] == ""
    assert "鼓浪屿" in result["brief"]["avoid"]
    assert result["itinerary"] is None


def test_missing_days_options_require_explicit_acceptance():
    result = slot_filler_node({"user_message": "帮我规划丽江"})
    assert result["brief"]["days"] is None
    assert result["clarification_options"] == [
        {"label": "按 3 天参考规划", "days": 3, "message": "规划丽江3天行程"},
        {"label": "自己填天数", "days": None, "message": ""},
    ]
    assert "clarification_options" in TravelGraphState.__annotations__
    assert decide(result).action == "finish_report"
    accepted = slot_filler_node({**result, "user_message": result["clarification_options"][0]["message"]})
    assert accepted["brief"]["days"] == 3
    assert accepted["brief_missing"] == []
    assert accepted["clarification_options"] == []


@pytest.mark.parametrize("intent", ["query_static", "query_dynamic", "discover", "modify"])
def test_answer_result_never_republishes_old_plan(intent):
    result = build_travel_graph_result({"intent": intent, "brief_missing": ["days"],
                                       "itinerary": {"plan_version": 1},
                                       "expert_history": [{"status": "failed"}],
                                       "final_answer": "已回答"})
    assert result["status"] == "answered"
    assert result["itinerary"] is None
    assert result["clarification"] == ""


def test_options_in_result():
    option = {"label": "按 3 天参考规划", "days": 3, "message": "规划丽江3天行程"}
    result = build_travel_graph_result({"brief_missing": ["days"], "clarification_options": [option]})
    assert result["clarification_options"] == [option]


def test_ordinal_day_modification_does_not_change_trip_length():
    brief = TravelBrief(destination="厦门", days=3)
    result = slot_filler_node({"user_message": "把第二天下午换成室内",
                              "brief": brief.model_dump(),
                              "brief_fingerprint": brief_fingerprint(brief),
                              "itinerary": {"plan_version": 1}})
    assert extract_days("把第二天下午换成室内") is None
    assert result["intent"] == "modify"
    assert result["brief"]["days"] == 3
    assert "itinerary" not in result


def test_new_destination_static_answer_keeps_current_inspiration():
    brief = TravelBrief(destination="厦门", days=3)
    with patch("backend.travel.services.live_search_service.search_zhihu_guides", return_value={"results": [{"title": "丽江慢游"}]}):
        result = slot_filler_node({"user_message": "丽江好玩吗", "brief": brief.model_dump(),
                                  "brief_fingerprint": brief_fingerprint(brief)})
    assert result["inspiration"]["guides"][0]["title"] == "丽江慢游"


@pytest.mark.parametrize("query,expected", [
    ("丽江好玩吗", True), ("西安三日游", True), ("鹭岛好玩吗", True),
    ("帮我规划丽江", True),
    ("丽江三天，直接规划", True), ("帮我规划北京销售目标", False),
    ("帮我直接规划北京销售目标", False),
    ("三明治好玩吗", False), ("南京路3天销量", False),
    ("近3天订单量", False), ("北京最近30天订单量", False),
])
def test_prefilter_city_guards(query, expected):
    from backend.orchestration.graph.travel_prefilter import is_travel_request

    assert is_travel_request(query) is expected


def test_alias_route_is_not_reversed():
    assert extract_destination("从beijing到上海玩3天") == "上海"
    assert extract_origin("从BEIJING到上海玩3天") == "北京"
    assert extract_destination("从鹭岛出发去西安玩3天") == "西安"
    assert extract_destination("从春城到丽江玩3天") == "丽江"
    assert extract_origin("从春城出发") == "昆明"


def test_repeated_city_keeps_first_mention():
    assert extract_destination("北京和上海相比，北京好玩吗") == "北京"


def test_answer_does_not_create_planning_pending():
    from backend.orchestration.graph.travel_graph_node import _sync_travel_run

    with patch("backend.orchestration.context.conversation_context.sync_travel_run_to_context",
               return_value="unexpected-run"):
        result = _sync_travel_run(
            {"user_id": "user", "session_id": "trip",
             "travel_context": {"conversation_id": "trip"}},
            {"intent": "query_static", "brief_missing": ["days"]},
            {"status": "answered"}, {})
    assert result == ""


def test_options_in_requirement_event():
    from backend.travel.core.events import travel_event_scope

    events = []
    with travel_event_scope(events.append):
        result = slot_filler_node({"user_message": "帮我规划丽江"})
    interpreted = next(e for e in events if e["event"] == "requirement.interpreted")
    assert interpreted["clarification_options"] == result["clarification_options"]
    assert interpreted["intent"] == "plan"


def test_graph_schema_preserves_options_and_answer_exit(monkeypatch):
    from backend.config import travel as config
    from backend.travel import graph_builder

    monkeypatch.setattr(config, "TRAVEL_CHECKPOINTER_ENABLED", True)
    monkeypatch.setattr(config, "TRAVEL_CHECKPOINTER_BACKEND", "memory")
    monkeypatch.setattr(config, "TRAVEL_REQUIRE_PERSISTENCE", False)
    monkeypatch.setattr(config, "TRAVEL_PREFS_ENABLED", False)
    monkeypatch.setattr(graph_builder, "_travel_graph", None)
    graph = graph_builder.get_travel_graph()
    run_config = {"configurable": {"thread_id": "intent-options-regression"}}
    first = graph.invoke({"user_message": "帮我规划丽江"}, config=run_config)
    assert first["brief"]["days"] is None
    assert len(first["clarification_options"]) == 2
    assert "itinerary" not in first
    with patch("backend.travel.services.live_search_service.search_zhihu_guides", return_value={"results": []}):
        answer = graph.invoke({"user_message": "丽江自由行怎么样"}, config=run_config)
    assert answer["intent"] == "query_static"
    assert answer["clarification_options"] == []
    assert build_travel_graph_result(answer)["status"] == "answered"
