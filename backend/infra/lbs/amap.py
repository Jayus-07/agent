"""infra.lbs.amap — 高德开放平台能力门面（商家级 POI 归一）

分层位置与腾讯门面（``api.py``）完全对称::

    tools/map/merchant.py ──> infra/lbs/amap.py ──> infra/http/amap.py ──> 高德
                            （本文件：语义归一 + 降级）  （下一层：鉴权/重试/缓存）

**返回值约定（与腾讯门面同一纪律）**：``None`` 表示「调用失败或未配置」
（限流/网络/鉴权），``[]`` 表示「调用成功但确实没有结果」。二者混为
一谈会让「限流」被误当成「这里没商家」。

归一点（全部来自 2026-10-02 实测响应）：

- 高德坐标串是「经度,纬度」，此处换向为平台口径 ``(lat, lng)``；
- ``business`` 块整体可能缺失（如公交站），rating/cost 也可能缺失
  （景点常无 cost）→ 归一为 ``None`` 而非 0，让上层能区分「没数据」
  和「不要钱」；
- ``type`` 是「餐饮服务;中餐厅;火锅店」三级串，原样保留，层级拆分
  交给展示层。
"""
from __future__ import annotations

from backend.config import map as MAP
from backend.infra.http import amap as AMAP
from backend.shared.logger import logger

# v5 检索必须显式声明要的额外字段块；business = 评分/人均/营业时间/电话等
PLACE_SHOW_FIELDS = "business"


def _to_float(value) -> float | None:
    """高德的 rating/cost 是字符串（"4.7"/"120.00"），缺失时键不存在或为空。"""
    if value is None or str(value).strip() == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _parse_location(raw: str) -> tuple[float, float]:
    """高德「经度,纬度」串 → (lat, lng)。解析失败返回 (0.0, 0.0) 并留给上层判空。"""
    parts = (raw or "").split(",")
    if len(parts) != 2:
        return (0.0, 0.0)
    try:
        lng, lat = float(parts[0]), float(parts[1])
    except ValueError:
        return (0.0, 0.0)
    return (lat, lng)


def normalize_merchant(raw: dict) -> dict:
    """把 v5 检索返回的单条 POI 归一为平台口径的商家记录（纯函数，可单测）。"""
    biz = raw.get("business") or {}
    lat, lng = _parse_location(raw.get("location", ""))
    return {
        "id": str(raw.get("id", "")),
        "name": raw.get("name") or "",
        "category": raw.get("type") or "",
        "typecode": str(raw.get("typecode") or ""),
        "rating": _to_float(biz.get("rating")),
        "avg_cost_cny": _to_float(biz.get("cost")),
        "open_time_today": biz.get("opentime_today") or "",
        "open_time_week": biz.get("opentime_week") or "",
        "tel": biz.get("tel") or "",
        "business_area": biz.get("business_area") or "",
        "address": raw.get("address") or "",
        "province": raw.get("pname") or "",
        "city": raw.get("cityname") or "",
        "district": raw.get("adname") or "",
        "adcode": str(raw.get("adcode") or ""),
        "lat": lat,
        "lng": lng,
        # distance 仅周边检索（给了 location）时高德才附带，单位米
        "distance_m": _to_int(raw.get("distance")),
    }


def place_text(
    keywords: str,
    *,
    region: str | None = None,
    location: tuple[float, float] | None = None,
    radius: int | None = None,
    types: str | None = None,
    city_limit: bool = True,
    page_size: int | None = None,
    page_num: int = 1,
    ttl: float | None = None,
) -> list[dict] | None:
    """关键词检索商家级 POI（高德 搜索POI 2.0）。

    Args:
        keywords: 检索词，如 "火锅"、"海底捞"、"咖啡"
        region: 城市限定，如 "福州"；未给时用配置默认城市兜底，
               否则会返回全国噪声结果
        location: (lat, lng) 中心点。给了即按距离排序（周边检索），
               结果带 distance_m。**注意入参是平台口径 (lat, lng)，
               发给高德前在此处换向**
        radius: 周边检索半径（米）
        types: 高德分类码，如 "050000"（餐饮服务），多个用 "|" 分隔
        city_limit: 是否严格限制在 region 内（默认 True，防跨城噪声）
        page_size: 单页条数，上限 25
        page_num: 页码（从 1 起）
        ttl: 缓存秒数覆盖

    Returns:
        归一后的商家记录列表；调用失败/未配置返回 ``None``。
    """
    params: dict = {
        "keywords": keywords,
        "show_fields": PLACE_SHOW_FIELDS,
        "city_limit": "true" if city_limit else "false",
        "page_size": min(page_size or MAP.AMAP_MAX_PAGE_SIZE,
                         MAP.AMAP_MAX_PAGE_SIZE),
        "page_num": max(1, page_num),
    }
    effective_region = (region or "").strip() or MAP.AMAP_DEFAULT_REGION
    if effective_region:
        params["region"] = effective_region
    if location is not None:
        # 高德要「经度,纬度」——与平台 (lat, lng) 口径相反，此处换向
        params["location"] = f"{location[1]},{location[0]}"
        if radius:
            params["radius"] = max(1, min(int(radius), 50000))
    if types:
        params["types"] = types

    try:
        payload = AMAP.call_sync(AMAP.EP_PLACE_TEXT, params, ttl=ttl)
    except AMAP.AmapError as e:
        logger.warning("[AMap] place_text 失败: %s", e)
        return None
    return [normalize_merchant(p) for p in (payload.get("pois") or [])]
