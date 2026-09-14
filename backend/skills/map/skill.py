"""skills/map/skill.py — 地图信息查询 Skill（聚合入口）

Capability: map.lookup

为什么是一个聚合 Skill 而不是 8 个：
  tools/map 下有 13 个工具，若逐个注册成 capability，Planner 的 prompt
  每轮都要多带十几段能力描述，择优准确率反而下降，且这些能力高度同质
  （都是「查一个地理位置事实」）。因此收敛成一个入口 + action 分发：
  Planner 只需判断「这个问题该不该查地图」，具体查什么由 action 决定。

覆盖与不覆盖：
  - 覆盖：天气、地理编码/逆地理编码、地点检索、路线规划、导航调起、
    静态图、行政区划、街景
  - 不覆盖：行程排期（那是旅游域图的事，见 travel_poi skill 的说明）、
    POI 候选池（travel.poi_search）
"""
from __future__ import annotations

from langchain_core.tools import tool

from backend.shared.logger import logger
from backend.skills.base import BaseSkill
from backend.tools.map._base import fail
from backend.tools.map.geo import (
    map_district_tool,
    map_geocode_tool,
    map_reverse_geocode_tool,
)
from backend.tools.map.place import map_place_search_tool
from backend.tools.map.route import map_navigation_tool, map_route_tool
from backend.tools.map.static_map import map_static_map_tool
from backend.tools.map.street_view import map_street_view_tool
from backend.tools.map.weather import map_weather_tool

# action → (说明, 必填参数)。缺参时给模型可读的指引，而不是让底层抛晦涩错误。
ACTIONS: dict[str, tuple[str, tuple[str, ...]]] = {
    "weather": ("查询天气（实时/未来/逐小时）", ("city 或 location",)),
    "geocode": ("地址 → 坐标", ("address",)),
    "reverse_geocode": ("坐标 → 地址", ("location",)),
    "place_search": ("按关键词检索地点", ("keyword",)),
    "route": ("两点路线规划（距离/时长/分段指令）", ("from_location", "to_location")),
    "navigation": ("生成调起腾讯地图 App 导航的链接", ("to_location",)),
    "static_map": ("生成带标注的静态地图图片地址", ("location 或 markers",)),
    "district": ("行政区划查询", ("keyword 或 district_id",)),
    "street_view": ("取指定坐标的街景全景图", ("location",)),
}

# route 与 navigation 的出行方式取值不同，这里做一次归一化，
# 避免模型把 driving 直接喂给 URI API（后者只认 drive/walk/bus/bike）。
_ROUTE_TO_NAV_MODE = {
    "driving": "drive",
    "walking": "walk",
    "transit": "bus",
    "bicycling": "bike",
}


@tool
def map_lookup_tool(
    action: str,
    address: str = "",
    city: str = "",
    keyword: str = "",
    location: str = "",
    from_location: str = "",
    to_location: str = "",
    to_name: str = "",
    mode: str = "",
    kind: str = "now",
    near: str = "",
    zoom: int = 14,
    size: str = "600*400",
    markers: str = "",
    radius_m: int = 0,
    page_size: int = 10,
    district_id: str = "",
) -> str:
    """按 action 查询地理位置类事实（天气/坐标/地点/路线/导航/静态图/行政区划/街景）。

    action 取值：weather / geocode / reverse_geocode / place_search / route /
                 navigation / static_map / district / street_view
    坐标统一为 "纬度,经度"，如 "26.0824,119.2968"（不是经度在前）。
    各 action 所需参数：weather→city 或 location；geocode→address；
    reverse_geocode→location；place_search→keyword（可选 city/near）；
    route→from_location + to_location；navigation→to_location；
    static_map→location 或 markers；district→keyword 或 district_id；
    street_view→location。
    """
    act = (action or "").strip().lower()
    if act not in ACTIONS:
        return fail(
            f"不支持的 action: {action!r}",
            supported=list(ACTIONS),
        )

    desc, required = ACTIONS[act]
    logger.info("[MapLookup] action=%s (%s)", act, desc)

    # 必填参数前置检查：给出"这个 action 需要什么"，比底层报错好定位
    provided = {
        "city": city, "location": location, "address": address,
        "keyword": keyword, "to_location": to_location, "markers": markers,
        "district_id": district_id, "from_location": from_location,
    }
    for req in required:
        keys = [k.strip() for k in req.split("或")]
        if not any(provided.get(k) for k in keys):
            return fail(f"action={act} 缺少必填参数：{req}", required=list(required))

    try:
        if act == "weather":
            return map_weather_tool.func(city=city, location=location, kind=kind)
        if act == "geocode":
            return map_geocode_tool.func(address=address or keyword, city=city)
        if act == "reverse_geocode":
            return map_reverse_geocode_tool.func(location=location)
        if act == "place_search":
            return map_place_search_tool.func(
                keyword=keyword, city=city, near=near,
                radius_m=radius_m or 3000, page_size=page_size,
            )
        if act == "route":
            return map_route_tool.func(
                from_location=from_location, to_location=to_location,
                mode=mode or "driving",
            )
        if act == "navigation":
            nav_mode = _ROUTE_TO_NAV_MODE.get((mode or "driving").lower(),
                                              mode or "drive")
            return map_navigation_tool.func(
                to_location=to_location, to_name=to_name,
                from_location=from_location, mode=nav_mode,
            )
        if act == "static_map":
            return map_static_map_tool.func(
                center=location, zoom=zoom, size=size, markers=markers,
            )
        if act == "district":
            return map_district_tool.func(keyword=keyword, district_id=district_id)
        # street_view
        return map_street_view_tool.func(
            location=location, radius_m=radius_m or 50,
        )
    except Exception as e:  # noqa: BLE001 — Skill 边界统一兜底
        logger.warning("[MapLookup] action=%s 失败: %s", act, e)
        return fail(f"地图查询失败: {e}", action=act)


class MapLookupSkill(BaseSkill):
    name = "map_lookup"
    capabilities = ["map.lookup"]
    description = (
        "查询地理位置类事实：天气、地址与坐标互转、地点检索、路线规划与耗时、"
        "导航调起链接、静态地图图片、行政区划、街景。"
        "适用于「福州现在天气怎么样」「三坊七巷在哪」「从这里到机场要多久」"
        "「把这个地方画出来」这类问题。"
        "注意：本能力只回答事实查询，不做行程安排（排期请用旅行域能力）；"
        "坐标入参统一为「纬度,经度」；街景需企业开发者权限，未开通会返回明确指引。"
    )
    params_schema = {
        "action": {
            "type": "string", "required": True,
            "enum": ["weather", "geocode", "reverse_geocode", "place_search",
                     "route", "navigation", "static_map", "district",
                     "street_view"],
            "description": "查询类型：weather 天气 / geocode 地址转坐标 / "
                           "reverse_geocode 坐标转地址 / place_search 地点检索 / "
                           "route 路线规划 / navigation 导航调起 / static_map 静态图 / "
                           "district 行政区划 / street_view 街景",
        },
        "city": {"type": "string", "required": False,
                 "description": "城市名，如「福州」；weather/place_search/geocode 用于消歧"},
        "address": {"type": "string", "required": False,
                    "description": "待解析的地址文本（geocode 用）"},
        "keyword": {"type": "string", "required": False,
                    "description": "检索词（place_search/district 用，也可作为 geocode 的地址）"},
        "location": {"type": "string", "required": False,
                     "description": "坐标「纬度,经度」（weather/reverse_geocode/static_map/street_view 用）"},
        "from_location": {"type": "string", "required": False,
                          "description": "起点坐标「纬度,经度」（route/navigation 用）"},
        "to_location": {"type": "string", "required": False,
                        "description": "终点坐标「纬度,经度」（route/navigation 用）"},
        "to_name": {"type": "string", "required": False,
                    "description": "终点名称，显示在导航调起页上（navigation 用）"},
        "mode": {"type": "string", "required": False,
                 "description": "出行方式：route 用 driving/walking/bicycling/transit；"
                                "navigation 用 drive/walk/bus/bike（传前者会自动转换）"},
        "kind": {"type": "string", "required": False,
                 "description": "天气类型：now 实时 / future 未来几天 / hourly 逐小时（默认 now）"},
        "near": {"type": "string", "required": False,
                 "description": "周边检索中心坐标「纬度,经度」（place_search 用）"},
        "zoom": {"type": "integer", "required": False,
                 "description": "静态图缩放级别 4~18，默认 14"},
        "size": {"type": "string", "required": False,
                 "description": "静态图尺寸「宽*高」，默认 600*400"},
        "markers": {"type": "string", "required": False,
                    "description": "静态图标注点，分号分隔，每项「纬度,经度」或「纬度,经度,标注字符」"},
        "radius_m": {"type": "integer", "required": False,
                     "description": "半径（米）：place_search 默认 3000 / street_view 默认 50"},
        "page_size": {"type": "integer", "required": False,
                      "description": "地点检索返回条数，默认 10，上限 20"},
        "district_id": {"type": "string", "required": False,
                        "description": "行政区划 id（district 用，与 keyword 二选一）"},
    }
    examples = [
        {"action": "weather", "city": "福州"},
        {"action": "geocode", "address": "福州市鼓楼区南后街139号", "city": "福州"},
        {"action": "place_search", "keyword": "咖啡", "city": "福州", "page_size": 5},
        {"action": "route", "from_location": "26.0824,119.2968",
         "to_location": "26.049,119.389", "mode": "driving"},
        {"action": "static_map", "location": "26.0824,119.2968", "zoom": 13,
         "markers": "26.0824,119.2968,A"},
    ]
    output_type = "text"

    @property
    def _tool_fn(self):
        return map_lookup_tool


async def map_lookup_skill_node(state: dict) -> dict:
    """Skill 节点函数（供主图 Planner 的 DAG 调用）"""
    skill = MapLookupSkill()
    cap = state.get("plan", {}).get("nodes", {}).get(
        state.get("current_step_id", ""), {}).get("capability", "map.lookup")
    logger.info("[MapLookupSkill] step=%s cap=%s", state.get("current_step_id"), cap)
    return await skill.execute(state, step_capability=cap)
