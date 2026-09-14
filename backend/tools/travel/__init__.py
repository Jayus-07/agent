"""tools/travel — 旅游域工具层

无状态、可独立单测（与 backend/tools 下其余工具同一约定）：
  routing.py   — 距离/通勤估算（纯函数 + 可插拔真实路线数据源）
  poi.py       — 候选 POI 检索（纯函数 + LangChain Tool）
  poi_seed.py  — P0 本地种子数据（非权威，见模块头声明）
  cost.py      — 费用估算（纯函数）
  live_map.py  — 腾讯位置服务适配（真实路线 + 缺失地点补全）

数据源分级：
  P0 本地种子 + 直线估算  → 离线可跑、可单测，用于跑通全链路
  P1 腾讯位置服务         → 由 TRAVEL_USE_LIVE_MAP 开关启用，失败自动回落 P0
"""
from backend.tools.travel.cost import day_cost, estimate_cost, rooms_needed
from backend.tools.travel.live_map import (
    SOURCE_LBS,
    install_live_map,
    live_leg,
    map_category,
    resolve_missing_places,
    resolve_place,
)
from backend.tools.travel.poi import (
    is_excluded,
    resolve_city,
    score_poi,
    search_poi,
    travel_poi_search_tool,
)
from backend.tools.travel.routing import (
    day_radius_km,
    estimate_leg,
    get_route_provider,
    haversine_km,
    leg_cost_cny,
    leg_minutes,
    route_km,
    set_route_provider,
)

__all__ = [
    "resolve_city",
    "score_poi",
    "is_excluded",
    "search_poi",
    "travel_poi_search_tool",
    "haversine_km",
    "route_km",
    "leg_minutes",
    "leg_cost_cny",
    "estimate_leg",
    "day_radius_km",
    "set_route_provider",
    "get_route_provider",
    "rooms_needed",
    "day_cost",
    "estimate_cost",
    "SOURCE_LBS",
    "install_live_map",
    "live_leg",
    "map_category",
    "resolve_place",
    "resolve_missing_places",
]
