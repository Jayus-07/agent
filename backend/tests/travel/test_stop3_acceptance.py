"""STOP 3：自然语言局部改单验收。"""

from backend.travel.models.brief import TravelBrief
from backend.travel.models.poi import Poi
from backend.travel.services.transit_service import build_itinerary
from backend.travel.partial_replan import (
    apply_partial_replan,
    parse_partial_request,
)
from backend.travel.slot_filler import slot_filler_node
from backend.travel.graph_state import save_itinerary
from backend.travel.reporter import _assemble


def _poi(poi_id: str, name: str, *, lat: float = 30.2, lng: float = 120.1) -> Poi:
    return Poi(
        poi_id=poi_id,
        name=name,
        city="杭州",
        lat=lat,
        lng=lng,
        suggested_minutes=60,
    )


def _itinerary():
    brief = TravelBrief(destination="杭州", days=3)
    pois = [
        _poi("west", "西湖", lat=30.25, lng=120.15),
        _poi("museum", "西湖博物馆", lat=30.24, lng=120.14),
        _poi("xixi", "西溪湿地", lat=30.27, lng=120.06),
        _poi("leifeng", "雷峰塔", lat=30.23, lng=120.13),
        _poi("lingyin", "灵隐寺", lat=30.24, lng=120.10),
        _poi("lakepark", "湖滨公园", lat=30.25, lng=120.17),
        _poi("longjing", "龙井村", lat=30.20, lng=120.12),
    ]
    itinerary, _ = build_itinerary(
        brief,
        [[pois[0], pois[1]], [pois[2], pois[3]], [pois[5], pois[6]]],
    )
    return itinerary, {p.poi_id: p for p in pois}


def _names(itinerary, day_index: int) -> list[str]:
    day = next(d for d in itinerary.days if d.day_index == day_index)
    return [item.title for item in day.items if item.poi is not None]


def test_replace_poi_is_targeted_and_marks_partial_replan():
    request = parse_partial_request("第二天不要去西溪湿地，换成灵隐寺")
    assert request is not None
    assert request.operation == "replace_poi"
    assert request.target_day == 2
    assert request.remove_names == ("西溪湿地",)
    assert request.add_names == ("灵隐寺",)

    itinerary, candidates = _itinerary()
    result = apply_partial_replan(itinerary, candidates, request)
    assert result.status == "applied"
    assert result.partial_replan is True
    assert "西溪湿地" not in _names(result.itinerary, 2)
    assert "灵隐寺" in _names(result.itinerary, 2)
    assert _names(result.itinerary, 1) == _names(itinerary, 1)
    assert _names(result.itinerary, 3) == _names(itinerary, 3)


def test_remove_and_add_do_not_replan_unrelated_days():
    itinerary, candidates = _itinerary()
    removed = apply_partial_replan(
        itinerary,
        candidates,
        parse_partial_request("把西湖去掉"),
    )
    assert removed.status == "applied"
    assert all(name != "西湖" for day in removed.itinerary.days for name in _names(removed.itinerary, day.day_index))

    added = apply_partial_replan(
        itinerary,
        candidates,
        parse_partial_request("第二天加一个灵隐寺"),
    )
    assert added.status == "applied"
    assert "灵隐寺" in _names(added.itinerary, 2)
    assert _names(added.itinerary, 1) == _names(itinerary, 1)
    assert _names(added.itinerary, 3) == _names(itinerary, 3)


def test_plain_language_too_tiring_request_maps_to_relaxed_pace():
    request = parse_partial_request("第二天别太累")

    assert request.operation == "pace"
    assert request.target_day == 2
    assert request.pace == "relaxed"


def test_pace_and_end_time_are_day_scoped():
    itinerary, candidates = _itinerary()
    relaxed = apply_partial_replan(
        itinerary,
        candidates,
        parse_partial_request("第一天别太赶"),
    )
    assert relaxed.status == "applied"
    assert relaxed.itinerary.days[0].active_minutes <= 240
    assert _names(relaxed.itinerary, 2) == _names(itinerary, 2)

    early = apply_partial_replan(
        itinerary,
        candidates,
        parse_partial_request("第三天早点结束，我晚上要回上海"),
    )
    assert early.status == "applied"
    day3 = next(d for d in early.itinerary.days if d.day_index == 3)
    assert all(item.end <= "18:00" for item in day3.items)
    assert "上海" in early.message


def test_hard_far_constraint_is_reported_as_partial_failure():
    itinerary, candidates = _itinerary()
    request = parse_partial_request("第二天必须同时安排 8 个相距很远的景点")
    assert request is not None
    result = apply_partial_replan(itinerary, candidates, request)
    assert result.status == "partial"
    assert result.validation_failed is True
    assert "不能全部满足" in result.message


def test_slot_filler_routes_structured_modify_without_planning_reset():
    itinerary, _ = _itinerary()
    update = slot_filler_node({
        "user_message": "第二天加一个灵隐寺",
        "brief": itinerary.brief.model_dump(),
        "itinerary": save_itinerary(itinerary),
        "brief_fingerprint": "same-old-fingerprint",
    })
    assert update["intent"] == "modify"
    assert update["partial_replan"]["operation"] == "add_poi"
    assert "candidates" not in update
    assert "planning_reset" not in update


def test_successful_partial_replan_renders_updated_itinerary_answer():
    itinerary, candidates = _itinerary()
    answer = _assemble({
        "intent": "modify",
        "brief": itinerary.brief.model_dump(),
        "itinerary": save_itinerary(itinerary),
        "candidates": [poi.model_dump() for poi in candidates.values()],
        "partial_replan_result": {
            "status": "applied",
            "message": "已局部修改第 2 天。",
        },
    })
    assert "还在建设中" not in answer
    assert "局部修改：已局部修改第 2 天。" in answer
    assert "# 杭州 3 天行程" in answer
