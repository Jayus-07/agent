"""tools/map/place.py — 地点检索类工具

与 travel 域 ``travel_poi_search_tool``（本地种子数据）的分工：

  - ``travel_poi_search_tool``：从**带营业时段/票价/建议停留**的本地池子里
    粗筛候选，服务于排程与校验（这些字段腾讯检索不提供）
  - ``map_place_search_tool`` ：从**腾讯实时 POI 库**里发现地点，
    权威性高、覆盖广，但只给到名称/类别/地址/坐标

需要「有哪些地方可去」用前者；需要「这个地方真实存在吗、在哪、周边有什么」
用后者。两者互补，不可互相替代。
"""
from __future__ import annotations

from langchain_core.tools import tool

from backend.config import map as MAP
from backend.infra.lbs import api
from backend.shared.logger import logger
from backend.tools.map import _base


@tool
def map_place_search_tool(
    keyword: str,
    city: str = "",
    near: str = "",
    radius_m: int = 3000,
    page_size: int = 10,
) -> str:
    """
    在腾讯地图 POI 库中检索地点。
    keyword: 检索词，如 "景点"、"美食"、"三坊七巷"、"加油站"
    city: 限定城市，如 "福州"；未提供 near 时建议填写，否则可能返回全国范围内结果
    near: 中心坐标 "纬度,经度"，填写后改为周边检索并按距离排序，结果会带 distance_m
    radius_m: 周边检索半径（米），默认 3000，上限 50000
    page_size: 返回条数，默认 10，上限 20
    适用场景：发现某地有哪些 POI；核实某个地点真实存在性与规范名称；查某坐标附近有什么。
    注意：本工具不返回营业时间与票价，涉及排程请配合 travel_poi_search_tool 使用。
    """
    if not MAP.is_configured():
        return _base.not_configured()
    if not (keyword or "").strip():
        return _base.fail("keyword 不能为空")

    near_coord = _base.normalize_coord(near) if near else None
    if near and near_coord is None:
        return _base.fail(f"near 坐标格式无法解析：{near!r}，应形如 26.0824,119.2968")

    try:
        results = api.place_search(
            keyword.strip(),
            region=(city or "").strip() or None,
            near=near_coord,
            radius=max(1, min(int(radius_m or 3000), 50000)) if near_coord else None,
            page_size=max(1, min(int(page_size or 10), 20)),
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("[MapTool] place_search 失败: %s", e)
        return _base.fail(f"地点检索调用失败: {e}")

    if results is None:
        return _base.fail(f"地点检索失败：{keyword}（可能是配额或网络问题）")
    if not results:
        return _base.ok({"keyword": keyword, "count": 0, "pois": [],
                         "note": "检索成功但无匹配结果，建议放宽关键词或更换城市"})
    return _base.ok({
        "keyword": keyword,
        "city": city or MAP.TENCENT_LBS_DEFAULT_REGION,
        "boundary": "nearby" if near_coord else "region",
        "count": len(results),
        "pois": results,
    })


@tool
def map_place_suggest_tool(keyword: str, city: str = "") -> str:
    """
    地点名称联想补全：用户只说了半截词时，给出可能的完整地名。
    keyword: 不完整的关键词，如 "鼓"、"三坊"
    city: 限定城市，如 "福州"
    适用场景：用户表述含糊（"那个鼓什么的"）时先补全再确认；或做输入框自动提示。
    注意：返回项不含详细地址，仅用于候选名，确认后应再用 map_place_search_tool 取详情。
    """
    if not MAP.is_configured():
        return _base.not_configured()
    if not (keyword or "").strip():
        return _base.fail("keyword 不能为空")
    try:
        results = api.place_suggestion(keyword.strip(),
                                       region=(city or "").strip() or None)
    except Exception as e:  # noqa: BLE001
        logger.warning("[MapTool] place_suggestion 失败: %s", e)
        return _base.fail(f"地点联想调用失败: {e}")
    if results is None:
        return _base.fail(f"地点联想失败：{keyword}")
    if not results:
        return _base.ok({"keyword": keyword, "count": 0, "suggestions": []})
    return _base.ok({
        "keyword": keyword,
        "count": len(results),
        "suggestions": [
            {"name": p["name"], "lat": p["lat"], "lng": p["lng"],
             "city": p["city"], "district": p["district"]}
            for p in results
        ],
    })


# ==================== Tool Registry 自动注册 ====================
from backend.tools.tool_registry import tool_registry  # noqa: E402

for _t in (map_place_search_tool, map_place_suggest_tool):
    tool_registry.register(_t, __file__)
