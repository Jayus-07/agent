"""backend/travel_v2/leg_modes.py — 相邻地点交通方式推荐（确定性合成）。

2026-11-04 workbench-ux2 #5：生成行程后不再让用户逐段手选交通方式——
对没有真实路线数据的相邻对，按直线距离分段给出**推荐模式**，前端显示
「推荐 · 步行」等诚实口径，用户仍可在编辑弹窗显式改选。

口径（agent/用户显式结果优先，推荐只补空白）：
  缺坐标            → transit（保守回落，与旧行为一致）
  距离 < 1km       → walk
  1km ≤ 距离 ≤ 5km → transit
  距离 > 5km       → drive
推荐腿的耗时/费用一律未知（没有真实路线查询就不编造数字），由
「待真实路线查询」披露口径覆盖。
"""
from __future__ import annotations

from math import asin, cos, radians, sin, sqrt


def haversine_m(
    start_lat: float | None, start_lng: float | None,
    end_lat: float | None, end_lng: float | None,
) -> float | None:
    """两点直线距离（米）；任一坐标缺失返回 None。"""
    if start_lat is None or start_lng is None or end_lat is None or end_lng is None:
        return None
    lat1, lng1, lat2, lng2 = map(radians, (start_lat, start_lng, end_lat, end_lng))
    dlat, dlng = lat2 - lat1, lng2 - lng1
    h = sin(dlat / 2) ** 2 + sin(dlng / 2) ** 2 * cos(lat1) * cos(lat2)
    earth_radius_m = 6_371_000.0
    return 2 * earth_radius_m * asin(sqrt(h))


def recommend_leg_mode(
    start: dict | None, end: dict | None,
) -> str:
    """相邻地点对 → 推荐交通方式（Literal 值）；缺坐标保守回落 transit。"""
    distance = haversine_m(
        _coord(start, "lat"), _coord(start, "lng"),
        _coord(end, "lat"), _coord(end, "lng"),
    )
    if distance is None:
        return "transit"
    if distance < 1_000:
        return "walk"
    if distance <= 5_000:
        return "transit"
    return "drive"


def _coord(place: dict | None, key: str) -> float | None:
    if not isinstance(place, dict):
        return None
    value = place.get(key)
    return float(value) if isinstance(value, (int, float)) else None
