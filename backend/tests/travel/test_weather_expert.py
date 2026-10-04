"""tests/travel/test_weather_expert.py — 天气专家单测（2026-09-22 P0-2）

覆盖：坏天气判定 / 预报日期提取 / 户外→室内替换（含必去保护、无候选降级）
/ 节点级跳过路径（无日期、无预报）。天气 API 一律 mock，不发真实请求。
"""
from __future__ import annotations

from datetime import date

from backend.travel.experts import weather as W
from backend.travel.experts.weather import (
    bad_weather_dates,
    is_bad_weather,
    plan_weather_swaps,
    weather_expert_node,
)
from backend.travel.models.brief import TravelBrief

from backend.tests.travel.conftest import make_item, make_day, make_itinerary, make_poi

FORECAST_RAIN = {
    "kind": "future",
    "days": [
        {"date": "2026-09-15", "day": {"weather": "中雨"}, "night": {"weather": "阴"}},
        {"date": "2026-09-16", "day": {"weather": "晴"}, "night": {"weather": "晴"}},
    ],
}


def test_is_bad_weather():
    assert is_bad_weather("中雨")
    assert is_bad_weather("雷阵雨")
    assert is_bad_weather("小雪")
    assert not is_bad_weather("晴")
    assert not is_bad_weather("多云")
    assert not is_bad_weather("")
    assert not is_bad_weather(None)


def test_bad_weather_dates_extracts_day_and_night():
    assert bad_weather_dates(FORECAST_RAIN) == ["2026-09-15"]


def _rainy_itinerary(required_outdoor: bool = False):
    outdoor = make_poi(poi_id="p_out", name="鼓山", tags=["自然"],
                       required=required_outdoor)
    indoor_a = make_poi(poi_id="p_in_a", name="博物院", tags=["人文"], rating=4.7)
    indoor_b = make_poi(poi_id="p_in_b", name="科技馆", tags=["亲子"], rating=4.2)
    day = make_day(1, [make_item(title="鼓山", poi=outdoor)], day_date=date(2026, 9, 15))
    itinerary = make_itinerary(
        brief=TravelBrief(destination="测试城", days=1, start_date=date(2026, 9, 15)),
        days=[day],
    )
    return itinerary, [indoor_a.model_dump(), indoor_b.model_dump()]


def test_swap_replaces_outdoor_with_indoor():
    itinerary, candidates = _rainy_itinerary()
    new, actions, notes = plan_weather_swaps(itinerary, candidates, ["2026-09-15"])
    assert new is not None
    assert len(actions) == 1
    swap = actions[0]["swaps"][0]
    assert swap["from"] == "鼓山"
    assert swap["to"] == "博物院"  # 热度高的室内点先上
    names = [i.poi.name for i in new.days[0].visit_items()]
    assert "博物院" in names and "鼓山" not in names


def test_swap_protects_required_outdoor():
    itinerary, candidates = _rainy_itinerary(required_outdoor=True)
    new, actions, notes = plan_weather_swaps(itinerary, candidates, ["2026-09-15"])
    assert new is None  # 必去项不可替换 → 无产物
    assert actions == []
    assert any("必去点" in n for n in notes)


def test_swap_without_indoor_candidates_degrades():
    itinerary, _ = _rainy_itinerary()
    new, actions, notes = plan_weather_swaps(itinerary, [], ["2026-09-15"])
    assert new is None
    assert actions == []
    assert notes == []  # 「无可替换」的人话说明由节点层产出（见下个用例）


def test_node_notes_when_no_indoor_candidates(monkeypatch):
    itinerary, _ = _rainy_itinerary()
    monkeypatch.setattr(W, "fetch_forecast_evidence",
                        lambda city: (FORECAST_RAIN, "", None))
    state = {
        "brief": TravelBrief(destination="测试城", days=1,
                             start_date=date(2026, 9, 15)).model_dump(),
        "itinerary": itinerary.model_dump(),
        "candidates": [],
    }
    update = weather_expert_node(state)
    assert any("没有可替换的室内地点" in n for n in update["notes"])
    assert "itinerary" not in update


def test_node_skips_without_start_date():
    itinerary, _ = _rainy_itinerary()
    state = {
        "brief": TravelBrief(destination="测试城", days=1).model_dump(),
        "itinerary": itinerary.model_dump(),
        "candidates": [],
    }
    update = weather_expert_node(state)
    assert any("未提供出发日期" in n for n in update["notes"])


def test_node_skips_when_forecast_unavailable(monkeypatch):
    itinerary, _ = _rainy_itinerary()
    monkeypatch.setattr(W, "fetch_forecast_evidence",
                        lambda city: (None, "天气服务暂时不可用", None))
    state = {
        "brief": TravelBrief(destination="测试城", days=1,
                             start_date=date(2026, 9, 15)).model_dump(),
        "itinerary": itinerary.model_dump(),
        "candidates": [],
    }
    update = weather_expert_node(state)
    assert any("天气" in n for n in update["notes"])
    assert "itinerary" not in update


def test_node_swaps_and_logs(monkeypatch):
    itinerary, candidates = _rainy_itinerary()
    monkeypatch.setattr(W, "fetch_forecast_evidence",
                        lambda city: (FORECAST_RAIN, "", None))
    state = {
        "brief": TravelBrief(destination="测试城", days=1,
                             start_date=date(2026, 9, 15)).model_dump(),
        "itinerary": itinerary.model_dump(),
        "candidates": candidates,
    }
    update = weather_expert_node(state)
    assert "itinerary" in update
    weather_entries = [a for a in update.get("repair_log", [])
                       if a.get("code") == "weather_swap"]
    assert len(weather_entries) == 1
    assert weather_entries[0]["weather_swaps"][0]["from"] == "鼓山"


def test_node_keys_evidence_by_fact_id(monkeypatch):
    """回归（2026-10-01 实测）：service 返回**单条扁平** evidence dict，
    节点必须按 fact_id 分键后再并入 state —— 修前直接展开，evidences 被
    字符串字段污染，validator check_source_trust 遍历 `.get()` 即
    AttributeError，带出发日期的规划 100% 失败。"""
    itinerary, candidates = _rainy_itinerary()
    flat_evidence = {
        "fact_id": "weather:测试城",
        "value": {"city": "测试城", "days": 2, "served_by": "tencent:lbs"},
        "source": "tencent:lbs",
        "source_type": "live",
        "confidence": 0.95,
        "verified_at": "2026-10-01T08:00:00+08:00",
        "expire_at": "2026-10-01T08:10:00+08:00",
    }
    monkeypatch.setattr(W, "fetch_forecast_evidence",
                        lambda city: (FORECAST_RAIN, "", flat_evidence))
    state = {
        "brief": TravelBrief(destination="测试城", days=1,
                             start_date=date(2026, 9, 15)).model_dump(),
        "itinerary": itinerary.model_dump(),
        "candidates": candidates,
        # 预置 poi 节点证据：合并语义 = 既有证据不被冲掉
        "evidences": {"poi:测试城:0": {"fact_id": "poi:测试城:0"}},
    }
    update = weather_expert_node(state)
    merged = update["evidences"]
    # 顶层键只能是 fact_id，值只能是 dict（validator is_stale 的消费前提）
    assert set(merged) == {"poi:测试城:0", "weather:测试城"}
    assert all(isinstance(v, dict) for v in merged.values())
    assert merged["weather:测试城"]["fact_id"] == "weather:测试城"


def test_node_keys_evidence_when_no_bad_weather(monkeypatch):
    """无坏天气路径同样分键（该路径 evidence 照记供 SOURCE_STALE 判定）。"""
    itinerary, _ = _rainy_itinerary()
    flat_evidence = {
        "fact_id": "weather:测试城",
        "value": {"city": "测试城"},
        "source": "tencent:lbs",
        "source_type": "live",
        "confidence": 0.95,
        "verified_at": None,
        "expire_at": None,
    }
    forecast_sunny = {
        "kind": "future",
        "days": [{"date": "2026-09-15", "day": {"weather": "晴"},
                  "night": {"weather": "晴"}}],
    }
    monkeypatch.setattr(W, "fetch_forecast_evidence",
                        lambda city: (forecast_sunny, "", flat_evidence))
    state = {
        "brief": TravelBrief(destination="测试城", days=1,
                             start_date=date(2026, 9, 15)).model_dump(),
        "itinerary": itinerary.model_dump(),
        "candidates": [],
    }
    update = weather_expert_node(state)
    assert set(update["evidences"]) == {"weather:测试城"}
    assert isinstance(update["evidences"]["weather:测试城"], dict)


# ── 天气分级（2026-10-04）：小雨提示不替换，强天气仍替换 ──────

def test_weather_severity_levels():
    from backend.travel.services.weather_service import weather_severity

    assert weather_severity("小雨") == "mild"
    assert weather_severity("小雨转大雨") == "severe"  # 混强词防降级误放
    assert weather_severity("小雨转雷阵雨") == "severe"
    assert weather_severity("中雨") == "severe"
    assert weather_severity("雷阵雨") == "severe"
    assert weather_severity("小雪") == "severe"  # 雪对出行影响大，不降级
    assert weather_severity("晴") == "none"
    assert weather_severity("多云") == "none"
    assert weather_severity("") == "none"
    # is_bad_weather 旧语义保持（含 mild）——披露/证据口径不变
    assert is_bad_weather("小雨")


def test_bad_weather_dates_excludes_mild_only_days():
    from backend.travel.services.weather_service import (
        bad_weather_dates, mild_weather_dates)

    forecast = {
        "kind": "future",
        "days": [
            {"date": "2026-09-15", "day": {"weather": "小雨"}, "night": {"weather": "阴"}},
            {"date": "2026-09-16", "day": {"weather": "中雨"}, "night": {"weather": "阴"}},
            {"date": "2026-09-17", "day": {"weather": "晴"}, "night": {"weather": "小雨"}},
        ],
    }
    assert bad_weather_dates(forecast) == ["2026-09-16"]
    assert mild_weather_dates(forecast) == ["2026-09-15", "2026-09-17"]


def test_mild_day_not_swapped_only_hinted():
    """小雨日：户外景点保持原样，notes 出带伞提示。"""
    from backend.travel.services import weather_service as ws

    forecast = {
        "kind": "future",
        "days": [
            {"date": "2026-09-15", "day": {"weather": "小雨"}, "night": {"weather": "阴"}},
        ],
    }
    assert ws.bad_weather_dates(forecast) == []  # 不触发替换
    itinerary, candidates = _rainy_itinerary()
    new, actions, _ = ws.plan_weather_swaps(
        itinerary, candidates, ws.bad_weather_dates(forecast))
    assert new is None and actions == []
