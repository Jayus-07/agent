"""tools/map — 腾讯位置服务工具层

与 ``tools/travel``（本地种子数据 + 直线估算）的关系：

  tools/travel  →  拍行程：候选池、排程、时间/体力/预算校验（离线可跑、可单测）
  tools/map    →  查事实：这个地点真实在哪、有多远、怎么走（依赖网络、权威）

两者互补而非替代。工具清单：

  geo.py         地理编码 / 逆地理编码 / IP 定位 / 行政区划 / 坐标换算
  place.py       地点检索 / 关键词联想
  route.py       路线规划 / 距离矩阵 / 导航调起
  weather.py     天气（实时 / 未来 / 逐小时）
  street_view.py 街景全景图（返回后端代理地址，不泄露密钥）
  static_map.py  静态地图图片（返回后端代理地址，不泄露密钥）

统一约定：返回 JSON 字符串；失败时返回 ``{"error": ...}`` 而非静默空值，
以便 LLM 区分「查不到」与「查不了」。
"""
from backend.tools.map._base import fail, normalize_coord, not_configured, ok
from backend.tools.map.geo import (
    map_coord_convert_tool,
    map_district_tool,
    map_geocode_tool,
    map_ip_location_tool,
    map_reverse_geocode_tool,
)
from backend.tools.map.place import map_place_search_tool, map_place_suggest_tool
from backend.tools.map.route import (
    map_distance_matrix_tool,
    map_navigation_tool,
    map_route_tool,
)
from backend.tools.map.static_map import map_static_map_tool
from backend.tools.map.street_view import map_street_view_tool
from backend.tools.map.weather import map_weather_tool

__all__ = [
    "ok",
    "fail",
    "not_configured",
    "normalize_coord",
    "map_geocode_tool",
    "map_reverse_geocode_tool",
    "map_ip_location_tool",
    "map_district_tool",
    "map_coord_convert_tool",
    "map_place_search_tool",
    "map_place_suggest_tool",
    "map_route_tool",
    "map_distance_matrix_tool",
    "map_navigation_tool",
    "map_weather_tool",
    "map_street_view_tool",
    "map_static_map_tool",
]
