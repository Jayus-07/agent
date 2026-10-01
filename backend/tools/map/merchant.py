"""tools/map/merchant.py — 商家级 POI 检索（高德数据源）

三家数据源的分工（勿混为一谈）：

  - ``travel_poi_search_tool``（本地种子）：带营业时段/票价的候选池，
    服务于排程与校验，离线可跑，但只有种子城市
  - ``map_place_search_tool``（腾讯）：发现地点、核实存在性，
    权威覆盖广，但只有名称/类别/地址
  - ``map_merchant_search_tool``（高德，本文件）：**商家维度详情** ——
    评分、人均消费、营业状态。这三样只有高德 v5 检索
    （show_fields=business）提供，是「推荐哪家店、现在去行不行」的依据

字段口径（用户可见契约，缺数据时显式 null 而非编造）：

  - ``price``     展示串（"人均¥120"），由高德人均消费（cost）推导；
  - ``avg_cost_cny`` 同源数值。高德没有独立的「客单价」字段，价格与
    人均是同一来源的两种呈现，勿在下游当两个独立数据用；
  - ``open_status`` 由 ``opentime_today`` 对照当前时刻推导
    （营业中/已打烊/未知），支持跨午夜时段（"10:00-07:00" 是火锅店的
    常态）；原始时段串原样保留在 ``open_time_today``；
  - ``source``    恒为 "amap"；
  - ``updated_at`` 检索时刻（ISO8601 带时区）。高德 POI 不携带官方
    更新时间戳，本字段是**本平台数据新鲜度口径**，不代表高德侧维护时间。
"""
from __future__ import annotations

import math
import re
from datetime import datetime, time as dt_time

from langchain_core.tools import tool

from backend.config import map as MAP
from backend.infra.lbs import amap as AMAP_LBS
from backend.shared.logger import logger
from backend.tools.map import _base

_EARTH_RADIUS_M = 6371008.8


def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> int:
    """两点球面距离（米）。

    高德 v5 检索实测**不回** distance（恒空串，腾讯周边检索才有），
    周边模式的 distance_m 在本地按坐标算，工具契约的承诺才成立。
    """
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dlng / 2) ** 2)
    return int(round(2 * _EARTH_RADIUS_M * math.asin(math.sqrt(a))))

# 营业时段段解析："09:00-20:00"、"11:00-14:00,17:00-22:00"
# （分隔符兼容 - – ~ 至；两组时段任一命中即算营业中）
_TIME_RANGE_RE = re.compile(r"(\d{1,2}):(\d{2})\s*[-–~至]\s*(\d{1,2}):(\d{2})")


def open_status(open_time_today: str, now: dt_time) -> str:
    """由今日营业时段推导营业状态（纯函数，可单测）。

    Returns:
        "营业中" / "已打烊"；时段缺失或无法解析时 "未知"（不猜）。
    """
    text = (open_time_today or "").strip()
    if not text:
        return "未知"
    if "24" in text.replace("：", ":") and ("小时" in text or "h" in text.lower()):
        return "营业中"

    minutes_now = now.hour * 60 + now.minute
    segments = _TIME_RANGE_RE.findall(text)
    if not segments:
        # 有文案但不是时段格式（如"营业中"之外的杂串）：不猜，标未知
        return "未知"
    for h1, m1, h2, m2 in segments:
        start = int(h1) * 60 + int(m1)
        end = int(h2) * 60 + int(m2)
        if end > 24 * 60:  # 容错 "24:00" 写法
            end = 24 * 60
        if end <= start:
            # 跨午夜（"10:00-07:00" = 次日 07:00 打烊）
            if minutes_now >= start or minutes_now < end:
                return "营业中"
        elif start <= minutes_now < end:
            return "营业中"
    return "已打烊"


def _full_address(rec: dict) -> str:
    """省市区 + 详细地址拼完整地址（地址里已含前缀时不重复拼）。"""
    prefix = "".join(filter(None, [rec.get("province"), rec.get("city"),
                                   rec.get("district")]))
    detail = rec.get("address") or ""
    if detail and prefix and prefix in detail:
        return detail
    return prefix + detail


def _price_display(avg_cost_cny: float | None) -> str | None:
    """人均 → 展示串；无数据返回 None（不编造"免费"）。"""
    if avg_cost_cny is None:
        return None
    return f"人均¥{avg_cost_cny:g}"


@tool
def map_merchant_search_tool(
    keyword: str,
    city: str = "",
    near: str = "",
    radius_m: int = 3000,
    page_size: int = 10,
) -> str:
    """
    在高德 POI 库中检索真实商家，返回商家名、品类、评分、人均消费、地址、
    营业状态与数据来源时间。
    keyword: 商家名或品类词，如 "海底捞"、"咖啡"、"火锅"
    city: 限定城市，如 "福州"；未提供 near 时建议填写，否则可能返回全国结果
    near: 中心坐标 "纬度,经度"，填写后改为周边检索并按距离排序，结果带 distance_m
    radius_m: 周边检索半径（米），默认 3000，上限 50000
    page_size: 返回条数，默认 10，上限 25
    适用场景：需要评分、人均价格、营业状态做「推荐哪家、现在去行不行」判断时；
    或核实某商家的真实地址与联系方式。
    注意：评分/人均/营业时间来自高德商家数据，部分类目（景点、公共设施）
    可能缺失，缺失时对应字段为 null，不要向用户编造数值；
    open_status 是按检索时刻推导的，仅供参考，出行前建议再确认。
    """
    if not MAP.is_amap_configured():
        return _base.not_configured(provider="高德开放平台", key_name="AMAP_KEY")
    if not (keyword or "").strip():
        return _base.fail("keyword 不能为空")

    near_coord = _base.normalize_coord(near) if near else None
    if near and near_coord is None:
        return _base.fail(f"near 坐标格式无法解析：{near!r}，应形如 26.0824,119.2968")

    try:
        results = AMAP_LBS.place_text(
            keyword.strip(),
            region=(city or "").strip() or None,
            location=near_coord,
            radius=max(1, min(int(radius_m or 3000), 50000)) if near_coord else None,
            page_size=max(1, min(int(page_size or 10), MAP.AMAP_MAX_PAGE_SIZE)),
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("[MapTool] merchant_search 失败: %s", e)
        return _base.fail(f"商家检索调用失败: {e}")

    if results is None:
        return _base.fail(f"商家检索失败：{keyword}（可能是配额、限流或网络问题）")
    if not results:
        return _base.ok({"keyword": keyword, "count": 0, "merchants": [],
                         "note": "检索成功但无匹配结果，建议放宽关键词或更换城市"})

    fetched_at = datetime.now().astimezone().isoformat(timespec="seconds")
    now_t = datetime.now().time()
    merchants = []
    for rec in results:
        merchants.append({
            "id": rec["id"],
            "name": rec["name"],
            "category": rec["category"],
            "rating": rec["rating"],
            "price": _price_display(rec["avg_cost_cny"]),
            "avg_cost_cny": rec["avg_cost_cny"],
            "address": _full_address(rec),
            "open_status": open_status(rec["open_time_today"], now_t),
            "open_time_today": rec["open_time_today"],
            "open_time_week": rec["open_time_week"],
            "tel": rec["tel"],
            "lat": rec["lat"],
            "lng": rec["lng"],
            # 周边检索的距离本地计算（高德 v5 不回 distance）；非周边为 None
            "distance_m": (_haversine_m(near_coord[0], near_coord[1],
                                        rec["lat"], rec["lng"])
                           if near_coord else rec["distance_m"]),
            "source": "amap",
            "updated_at": fetched_at,
        })

    return _base.ok({
        "keyword": keyword,
        "city": (city or "").strip() or MAP.AMAP_DEFAULT_REGION,
        "boundary": "nearby" if near_coord else "region",
        "count": len(merchants),
        "merchants": merchants,
    })


# ==================== Tool Registry 自动注册 ====================
from backend.tools.tool_registry import tool_registry  # noqa: E402

tool_registry.register(map_merchant_search_tool, __file__)
