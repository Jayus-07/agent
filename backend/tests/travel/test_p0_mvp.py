"""tests/travel/test_p0_mvp.py — P0/P1 增量功能的纯函数层单测（2026-09-22）

覆盖：城市消费档位 / 目的地推荐 / 知识库降级 / ICS 转换 / 偏好辅助逻辑。
全部不触网、不连库（偏好与知识库的 IO 层走 mock 或禁用开关）。
"""
from __future__ import annotations

from datetime import date

from backend.config import travel as T
from backend.tools.travel.cost import day_cost, estimate_cost
from backend.travel.models.brief import TravelBrief
from backend.travel.models.itinerary import ItineraryDay

from backend.tests.travel.conftest import make_item, make_itinerary, make_poi


# ============================================================
# P0-3 城市消费档位
# ============================================================
def test_city_cost_tier_registered_and_fallback():
    assert T.city_cost_tier("杭州")["meal"] == 160.0
    # 未登记城市回落全局定额
    assert T.city_cost_tier("北京") == {"meal": T.TRAVEL_MEAL_PER_DAY_CNY,
                                        "lodging": T.TRAVEL_LODGING_PER_NIGHT_CNY}
    assert T.city_cost_tier("") == T.city_cost_tier("北京")


def test_estimate_cost_uses_city_tier():
    day = ItineraryDay(day_index=1, items=[make_item()])
    nights = 0  # 1 天 0 晚 → 住宿为 0，只验餐费口径
    assert nights == 0
    cost_hz = estimate_cost([day], party_size=2, city="杭州")
    cost_fz = estimate_cost([day], party_size=2, city="福州")
    assert cost_hz.meals == 160.0 * 2
    assert cost_fz.meals == 120.0 * 2
    assert cost_hz.lodging == 0.0


def test_day_cost_city_param_backward_compatible():
    day = ItineraryDay(day_index=1, items=[make_item()])
    assert day_cost(day, 2) == day_cost(day, 2, city="")


# ============================================================
# P1-3 目的地推荐
# ============================================================
def test_recommend_prefers_matching_tags():
    from backend.travel.recommend import recommend_cities

    recs = recommend_cities(["自然", "亲子"], top=3)
    assert recs, "种子池非空时必须有推荐"
    cities = [r.city for r in recs]
    assert len(cities) == len(set(cities)) <= 3
    # 有命中的城市分数必大于 0；福州有自然+亲子候选，应命中
    fz = next(r for r in recs if r.city == "福州")
    assert fz.score > 0
    assert "自然" in fz.matched_preferences


def test_recommend_deterministic():
    from backend.travel.recommend import recommend_cities

    a = [r.to_dict() for r in recommend_cities(["人文"], top=3)]
    b = [r.to_dict() for r in recommend_cities(["人文"], top=3)]
    assert a == b


def test_recommend_line_rendering():
    from backend.travel.recommend import recommend_cities, render_recommendation_line

    line = render_recommendation_line(recommend_cities(["美食"], top=2))
    assert "先看看" in line
    assert "、".join([""]) != line


# ============================================================
# P0-1 知识库检索降级
# ============================================================
def test_knowledge_disabled_returns_empty(monkeypatch):
    from backend.tools.travel import knowledge as K

    monkeypatch.setattr(T, "TRAVEL_RAG_ENABLED", False)
    chunks, tag = K.retrieve_travel_knowledge("福州 签证")
    assert chunks == [] and tag == ""


def test_knowledge_pipeline_failure_degrades(monkeypatch):
    from backend.tools.travel import knowledge as K

    class _Boom:
        @staticmethod
        def retrieve_knowledge(*a, **kw):
            raise RuntimeError("pg down")

    import backend.rag.pipeline as P
    monkeypatch.setattr(P, "get_rag_pipeline", lambda: _Boom())
    chunks, tag = K.retrieve_travel_knowledge("福州 攻略")
    assert chunks == [] and tag == ""


def test_build_knowledge_query():
    from backend.tools.travel.knowledge import build_knowledge_query

    q = build_knowledge_query("福州", ["人文", "美食"])
    assert "福州" in q and "人文" in q


# ============================================================
# P1-4 模糊反馈关键词
# ============================================================
def test_vague_pace_feedback_extracted():
    from backend.travel.slot_filler import extract_brief

    # 「太赶了」→ relaxed（此前该表述不被识别）
    brief = extract_brief("太赶了，第二天想轻松点",
                          TravelBrief(destination="杭州", days=2, pace="intense"))
    assert brief.pace == "relaxed"


def test_diet_extraction():
    from backend.travel.slot_filler import extract_diet

    assert extract_diet("我不吃辣") == "不吃辣"
    assert extract_diet("对海鲜过敏") == "海鲜过敏"
    assert extract_diet("随便") == ""


# ============================================================
# P0-4 ICS 转换
# ============================================================
def _sample_itinerary_dict() -> dict:
    poi = make_poi(poi_id="p1", name="三坊七巷")
    from backend.travel.experts.transit import build_itinerary

    itinerary, _ = build_itinerary(
        TravelBrief(destination="福州", days=1, party_size=2,
                    start_date=date(2026, 9, 22)),
        [[poi]],
    )
    return itinerary.model_dump()


def test_itinerary_to_ics_basic():
    from backend.app.api.routes.travel import itinerary_to_ics

    ics = itinerary_to_ics(_sample_itinerary_dict())
    assert ics.startswith("BEGIN:VCALENDAR")
    assert ics.rstrip().endswith("END:VCALENDAR")
    assert "SUMMARY:三坊七巷" in ics
    assert "DTSTART:20260922T" in ics
    assert "\r\n" in ics  # RFC 5545 行分隔


def test_ics_escapes_special_chars():
    from backend.app.api.routes.travel import _ics_escape

    assert _ics_escape("a,b;c\\d\ne") == "a\\,b\\;c\\\\d\\ne"


def test_ics_content_disposition_chinese_destination():
    """D1 回归：中文目的地导出 ICS 时 Content-Disposition 必须可按 HTTP 头编码。

    星座框架发送响应头前会做 latin-1 编码，中文直接内嵌 filename 必 500。
    修复后：ASCII fallback 固定名 + filename*=UTF-8'' 百分号编码携带真实名。
    """
    from urllib.parse import unquote

    from backend.app.api.routes.travel import ics_content_disposition

    header = ics_content_disposition("杭州")
    # 强断言：头值必须能按 HTTP 头编码（修复前这里抛 UnicodeEncodeError）
    header.encode("latin-1")
    assert header.startswith('attachment; filename="travel-plan.ics"')
    assert "filename*=UTF-8''" in header
    # 百分号编码无损往返：解码还原出原始目的地
    encoded_part = header.split("filename*=UTF-8''", 1)[1].removesuffix(".ics")
    assert unquote(encoded_part) == "杭州"
