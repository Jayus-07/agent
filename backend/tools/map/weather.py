"""tools/map/weather.py — 天气工具

对旅行域价值明确：天气会直接改变行程可行性（下雨就别排登山，高温要避开正午）。
"""
from __future__ import annotations

from langchain_core.tools import tool

from backend.config import map as MAP
from backend.infra.lbs import api
from backend.shared.logger import logger
from backend.tools.map import _base

_KIND_ALIASES = {
    "now": "now", "realtime": "now", "实时": "now", "当前": "now", "今天": "now",
    "future": "future", "forecast": "future", "未来": "future", "预报": "future",
    "hours": "hours", "hourly": "hours", "逐小时": "hours", "小时": "hours",
}


@tool
def map_weather_tool(city: str = "", location: str = "", kind: str = "now") -> str:
    """
    查询天气（实时 / 未来几天 / 逐小时）。
    city: 城市名，如 "福州"、"厦门"；会解析到区县级中心点，比城市级更准
    location: 坐标 "纬度,经度"，如 "26.0824,119.2968"；填了就优先用它（精度最高）
    kind: now(实时，默认) / future(未来几天，含昼夜) / hours(逐小时)
    适用场景：排行程前判断天气是否影响户外安排；回答用户"那边现在多少度/会下雨吗"。
    注意：future 返回的每条含 day 与 night 两组数据；hours 为逐小时预报。
    """
    if not MAP.is_configured():
        return _base.not_configured()

    resolved = _KIND_ALIASES.get((kind or "now").strip().lower())
    if resolved is None:
        return _base.fail(f"不支持的天气类型：{kind!r}",
                          supported=list(api.WEATHER_KINDS))

    coord = _base.normalize_coord(location) if location else None
    if location and coord is None:
        return _base.fail(f"location 坐标无法解析：{location!r}，应形如 26.0824,119.2968")

    try:
        if coord is not None:
            result = api.weather(location=coord, kind=resolved)
        elif city.strip():
            result = api.weather_for_city(city.strip(), kind=resolved)
        else:
            # 两者都没给：用 IP 定位猜一个城市的做法过于激进，直接要求补参数
            return _base.fail("city 与 location 至少填写一个")
    except ValueError as e:
        return _base.fail(str(e))
    except Exception as e:  # noqa: BLE001
        logger.warning("[MapTool] weather 失败: %s", e)
        return _base.fail(f"天气查询调用失败: {e}")

    if result is None:
        return _base.fail(f"未查到「{city or location}」的天气")
    return _base.ok(result)


# ==================== Tool Registry 自动注册 ====================
from backend.tools.tool_registry import tool_registry  # noqa: E402

tool_registry.register(map_weather_tool, __file__)
