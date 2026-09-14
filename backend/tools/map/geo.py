"""tools/map/geo.py — 定位与地址解析类工具

对 LLM 暴露的能力：地址↔坐标互转、IP 定位、行政区划查询、坐标系换算。
这些是所有空间推理的前置步骤 —— 用户说「西湖」时，先要确定是哪个城市的西湖。
"""
from __future__ import annotations

from langchain_core.tools import tool

from backend.config import map as MAP
from backend.infra.lbs import api, geo
from backend.shared.logger import logger
from backend.tools.map import _base


@tool
def map_geocode_tool(address: str, city: str = "") -> str:
    """
    把中文地址解析为经纬度坐标（正地理编码）。
    address: 地址文本，结构化程度越高越准，如 "福州市鼓楼区南后街139号"
    city: 限定城市，用于消歧，如 "福州"；同名地名较多时务必填写
    适用场景：需要拿到某个地点的确切坐标以便后续算距离、画图或排路线。
    注意：返回的 reliability 低于 7 表示只匹配到道路或范围而非门牌，行程中应保留不确定性。
    """
    if not MAP.is_configured():
        return _base.not_configured()
    if not (address or "").strip():
        return _base.fail("address 不能为空")
    try:
        result = api.geocode(address.strip(), region=(city or "").strip() or None)
    except Exception as e:  # noqa: BLE001 — 工具边界统一兜底，避免异常穿透到 Agent
        logger.warning("[MapTool] geocode 失败: %s", e)
        return _base.fail(f"地理编码调用失败: {e}")
    if result is None:
        return _base.fail(f"未能解析地址「{address}」", address=address)
    return _base.ok(result)


@tool
def map_reverse_geocode_tool(location: str, with_poi: bool = False) -> str:
    """
    把经纬度坐标反查为中文地址与行政区划（逆地理编码）。
    location: 坐标，格式 "纬度,经度"，如 "26.0824,119.2968"
    with_poi: 是否同时返回周边 POI 列表，默认 false
    适用场景：已知坐标要写进行程单/播报时，需要人类可读的地名；或想知道坐标附近有什么。
    """
    if not MAP.is_configured():
        return _base.not_configured()
    coord = _base.normalize_coord(location)
    if coord is None:
        return _base.fail(f"坐标格式无法解析：{location!r}，应形如 26.0824,119.2968（纬度在前）")
    try:
        result = api.reverse_geocode(*coord, get_poi=bool(with_poi))
    except Exception as e:  # noqa: BLE001
        logger.warning("[MapTool] reverse_geocode 失败: %s", e)
        return _base.fail(f"逆地理编码调用失败: {e}")
    if result is None:
        return _base.fail(f"坐标 {location} 未查到地址")
    return _base.ok({"lat": coord[0], "lng": coord[1], **result})


@tool
def map_ip_location_tool(ip: str = "") -> str:
    """
    根据 IP 地址定位所在城市（精度到市级）。
    ip: 待查询的 IP；留空则查询本机出口 IP
    适用场景：用户全程未说明城市时，先用它猜一个默认城市再向用户确认。
    注意：精度仅到城市，绝不能用于判断用户的具体位置或替代用户明确给出的城市。
    """
    if not MAP.is_configured():
        return _base.not_configured()
    try:
        result = api.ip_location((ip or "").strip() or None)
    except Exception as e:  # noqa: BLE001
        logger.warning("[MapTool] ip_location 失败: %s", e)
        return _base.fail(f"IP 定位调用失败: {e}")
    if result is None:
        return _base.fail("IP 定位未返回结果")
    return _base.ok(result)


@tool
def map_district_tool(keyword: str = "", district_id: str = "") -> str:
    """
    查询中国行政区划：给定名称返回其 adcode 与中心坐标；给定 id 返回其下辖区域。
    keyword: 城市或区县名称，如 "福州"、"鼓楼区"
    district_id: 行政区划 id（6 位 adcode），如 "350100"；填写此项则查询其下级
    适用场景：把用户口语中的地名统一成标准行政区（确定 adcode、经纬度中心点），
             或需要列出某市下辖的所有区县时。
    注意：两个参数二选一。keyword 走检索，district_id 走下钻。
    """
    if not MAP.is_configured():
        return _base.not_configured()
    try:
        if (district_id or "").strip():
            children = api.district_children(district_id.strip())
            if children is None:
                return _base.fail(f"查询下级行政区失败: {district_id}")
            return _base.ok({"parent_id": district_id.strip(), "count": len(children),
                             "children": children})
        if not (keyword or "").strip():
            return _base.fail("keyword 与 district_id 至少填写一个")
        hits = api.district_search(keyword.strip())
    except Exception as e:  # noqa: BLE001
        logger.warning("[MapTool] district 失败: %s", e)
        return _base.fail(f"行政区划查询调用失败: {e}")
    if hits is None:
        return _base.fail(f"行政区划查询失败: {keyword}")
    if not hits:
        return _base.fail(f"未找到行政区「{keyword}」")
    return _base.ok({"keyword": keyword, "count": len(hits), "districts": hits})


@tool
def map_coord_convert_tool(location: str, from_type: str = "wgs84") -> str:
    """
    坐标系换算：把坐标统一到腾讯地图使用的 GCJ-02（火星坐标）。
    location: 坐标 "纬度,经度"，如 "26.0824,119.2968"
    from_type: 源坐标系，取 wgs84(GPS原始) / gcj02(腾讯/高德) / bd09(百度)
    适用场景：拿到 GPS 设备、海外地图或百度地图上的坐标时，必须先转换再用于腾讯地图，
             否则会有 50~700 米的系统性偏移。
    注意：腾讯位置服务的所有接口输入输出均为 GCJ-02。
    """
    coord = _base.normalize_coord(location)
    if coord is None:
        return _base.fail(f"坐标格式无法解析：{location!r}，应形如 26.0824,119.2968")
    src = (from_type or "wgs84").strip().lower()
    if src in ("gcj02", "gcj-02", "tencent", "高德", "amap"):
        return _base.ok({"lat": coord[0], "lng": coord[1], "crs": "gcj02",
                         "changed": False, "note": "源坐标已是 GCJ-02，无需转换"})
    try:
        lat, lng = geo.to_gcj02(coord[0], coord[1], src)
    except ValueError as e:
        return _base.fail(str(e), supported=["wgs84", "gcj02", "bd09"])
    return _base.ok({
        "lat": round(lat, 6), "lng": round(lng, 6), "crs": "gcj02",
        "source_crs": src, "changed": True,
        "delta_m": round(_delta_meters(coord, (lat, lng)), 1),
    })


def _delta_meters(a: tuple[float, float], b: tuple[float, float]) -> float:
    """偏移量（米），用于向调用方说明「不转换会错多少」。"""
    import math

    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dphi = math.radians(b[0] - a[0])
    dlmb = math.radians(b[1] - a[1])
    h = (math.sin(dphi / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2)
    return 2 * 6371008.8 * math.asin(math.sqrt(h))


# ==================== Tool Registry 自动注册 ====================
from backend.tools.tool_registry import tool_registry  # noqa: E402

for _t in (map_geocode_tool, map_reverse_geocode_tool, map_ip_location_tool,
           map_district_tool, map_coord_convert_tool):
    tool_registry.register(_t, __file__)
