"""skills/map/skill.py — 地图信息查询 Skill（聚合入口）

Capability: map.lookup

本 Skill 只声明 capability 并引用 Tool 层的聚合 Tool；聚合入口的设计理由
与不覆盖范围见 ``backend/tools/map/lookup.py`` 的模块文档。

分层约定（2026-09-16 归位）：Skill 不定义 Tool。聚合 Tool 与它内部的
action 分发表属于 Tool 层（backend/tools/map/lookup.py）并已在模块底部
注册进 tool_registry。此处仅做 re-export，保持既有导入路径可用。
"""
from __future__ import annotations

from backend.shared.logger import logger
from backend.skills.base import BaseSkill
from backend.tools.map.lookup import ACTIONS, map_lookup_tool  # noqa: F401


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
