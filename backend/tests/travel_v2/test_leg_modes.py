"""backend/tests/travel_v2/test_leg_modes.py — 相邻对交通方式推荐单测
（workbench-ux2 #5，2026-11-05）

纯函数层：距离分段边界与缺坐标回落；不触网不连库。
"""
from __future__ import annotations

from backend.travel_v2.leg_modes import haversine_m, recommend_leg_mode

# 成都市中心两点间距参考（近似）
_PLACE_TIANFU = {"lat": 30.5728, "lng": 104.0668}   # 天府广场
_PLACE_WUHOU = {"lat": 30.6448, "lng": 104.0436}    # 武侯祠（≈8km）
_PLACE_NEARBY = {"lat": 30.5737, "lng": 104.0668}   # ≈100m


def test_short_distance_recommends_walk() -> None:
    assert recommend_leg_mode(_PLACE_TIANFU, _PLACE_NEARBY) == "walk"


def test_medium_distance_recommends_transit() -> None:
    # 造一个 ≈3km 的点对（同经度纬度差 ~0.027°）
    three_km = {"lat": 30.6, "lng": 104.0668}
    assert recommend_leg_mode(_PLACE_TIANFU, three_km) == "transit"


def test_long_distance_recommends_drive() -> None:
    assert recommend_leg_mode(_PLACE_TIANFU, _PLACE_WUHOU) == "drive"


def test_missing_coordinates_fall_back_to_transit() -> None:
    assert recommend_leg_mode(None, _PLACE_TIANFU) == "transit"
    assert recommend_leg_mode({"lat": None, "lng": None}, _PLACE_TIANFU) == "transit"
    assert recommend_leg_mode({"name": "无坐标点"}, _PLACE_TIANFU) == "transit"


def test_haversine_returns_none_for_missing_coordinates() -> None:
    assert haversine_m(None, 104.0, 30.5, 104.1) is None
    assert haversine_m(30.5, 104.0, 30.5, 104.1) is not None


def test_meal_activity_without_place_recommends_conservative_transit() -> None:
    """餐食占位（place=None）参与的相邻对不编造模式，保守 transit。"""
    assert recommend_leg_mode(_PLACE_TIANFU, None) == "transit"
