"""infra.lbs.api — 腾讯位置服务能力门面（把 WebService 原始响应归一为稳定结构）

分层位置::

    tools/map/*  ──┐
    app/api/routes/map.py ──┴──>  infra/lbs/api.py  ──>  infra/http/tencent_lbs.py  ──> 腾讯
                                   （本文件：语义归一 + 降级）
                                   （下一层：鉴权 / 重试 / 缓存 / 状态码映射）

归一化的意义：腾讯各端点的字段布局并不一致（检索类把数据放在 ``data``、
路线类放在 ``result``，坐标用的是「纬度,经度」而国内开发者习惯反过来）。
调用方只面对一种结构，换数据源时不必改业务代码。

**返回值约定（很重要）**：``None`` 表示「调用失败或未配置」，
``[]`` / ``{}`` 表示「调用成功但确实没有结果」。二者混为一谈会让
「限流」这种可恢复故障被误当成「这个地方没东西」，因此严格区分。
"""
from __future__ import annotations

from urllib.parse import urlencode

from backend.config import map as MAP
from backend.infra.http import tencent_lbs as T
from backend.infra.lbs import geo
from backend.shared.logger import logger

# 路线规划支持的方式（对应 /ws/direction/v1/{mode}/）
ROUTE_MODES = ("driving", "walking", "bicycling", "transit")
# 距离矩阵只支持两种
MATRIX_MODES = ("driving", "walking")
# 驾车偏好策略，见腾讯文档
DRIVING_POLICIES = ("LEAST_TIME", "LEAST_DISTANCE", "AVOID_HIGHWAY", "REAL_TRAFFIC")


# =============================================
# 内部小工具
# =============================================
def _to_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_int(value, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _normalize_poi(raw: dict) -> dict:
    """把检索/提示/逆地理返回的单条 POI 归一。"""
    loc = raw.get("location") or {}
    ad = raw.get("ad_info") or {}
    lat, lng = _to_float(loc.get("lat")), _to_float(loc.get("lng"))
    return {
        "id": str(raw.get("id", "")),
        "name": raw.get("title") or raw.get("name") or "",
        "address": raw.get("address") or "",
        "category": raw.get("category") or "",
        "tel": raw.get("tel") or "",
        "lat": lat,
        "lng": lng,
        # 距离仅在周边检索（boundary=nearby）时由腾讯附带
        "distance_m": _to_int(raw.get("_distance")) if "_distance" in raw else None,
        "province": ad.get("province") or raw.get("province") or "",
        "city": ad.get("city") or raw.get("city") or "",
        "district": ad.get("district") or raw.get("district") or "",
        "adcode": str(ad.get("adcode") or raw.get("adcode") or ""),
    }


def _route_of(payload: dict | None) -> dict | None:
    if not payload:
        return None
    routes = (payload.get("result") or {}).get("routes") or []
    return routes[0] if routes else None


# =============================================
# 定位 / 地址解析
# =============================================
def ip_location(ip: str | None = None) -> dict | None:
    """IP 定位：IP → 城市与大致坐标。

    精度只到城市级（腾讯给到区县，但不保证），适合「用户没说城市、
    先用 IP 猜一个默认值」，**不可**用于任何需要精确位置的功能。

    Returns:
        {"ip","lat","lng","nation","province","city","district","adcode"}
    """
    payload = T.safe_call(T.EP_IP_LOCATION, {"ip": ip}, ttl=MAP.TENCENT_LBS_CACHE_TTL)
    result = T.result_of(payload)
    if not result:
        return None
    loc = result.get("location") or {}
    ad = result.get("ad_info") or {}
    return {
        "ip": result.get("ip", ""),
        "lat": _to_float(loc.get("lat")),
        "lng": _to_float(loc.get("lng")),
        "nation": ad.get("nation", ""),
        "province": ad.get("province", ""),
        "city": ad.get("city", ""),
        "district": ad.get("district", ""),
        "adcode": str(ad.get("adcode", "")),
    }


def geocode(address: str, *, region: str | None = None) -> dict | None:
    """地址 → 坐标（正地理编码）。

    Args:
        address: 结构化程度越高越准（「福州市鼓楼区南后街139号」优于「三坊七巷」）
        region: 城市名限定，用于消歧（「中山路」在福州和厦门都有）

    Returns:
        {"title","lat","lng","formatted_address","province","city","district",
         "adcode","similarity","reliability","level"}；未解析出结果时为 None。
    """
    payload = T.safe_call(T.EP_GEOCODER, {"address": address, "region": region})
    result = T.result_of(payload)
    if not result:
        return None
    loc = result.get("location") or {}
    ad = result.get("ad_info") or {}
    comp = result.get("address_components") or {}
    formatted = "".join(
        filter(None, [comp.get("province"), comp.get("city"),
                      comp.get("district"), comp.get("street"),
                      comp.get("street_number")])
    ) or result.get("title", "")
    return {
        "title": result.get("title", ""),
        "lat": _to_float(loc.get("lat")),
        "lng": _to_float(loc.get("lng")),
        "formatted_address": formatted,
        "province": ad.get("province") or comp.get("province", ""),
        "city": ad.get("city") or comp.get("city", ""),
        "district": ad.get("district") or comp.get("district", ""),
        "adcode": str(ad.get("adcode") or ""),
        "similarity": _to_float(result.get("similarity")),
        # reliability 0~10，腾讯用它表示「这个地址有多可信」；
        # <7 时通常意味着只匹配到了道路/POI 而非门牌，行程单上应标注不确定。
        "reliability": _to_int(result.get("reliability")),
        "level": _to_int(result.get("level")),
    }


def reverse_geocode(lat: float, lng: float, *, get_poi: bool = False,
                    poi_page_size: int = 10) -> dict | None:
    """坐标 → 地址（逆地理编码）。

    Args:
        get_poi: 是否附带周边 POI（做「这个坐标附近有什么」时开）
    """
    params = {
        "location": geo.format_lat_lng(lat, lng),
        "get_poi": 1 if get_poi else 0,
        "poi_options": f"page_size={min(poi_page_size, MAP.TENCENT_LBS_MAX_PAGE_SIZE)}"
                       if get_poi else None,
    }
    payload = T.safe_call(T.EP_GEOCODER, params)
    result = T.result_of(payload)
    if not result:
        return None
    ad = result.get("ad_info") or {}
    comp = result.get("address_components") or {}
    pois = [
        _normalize_poi(p) for p in (result.get("pois") or [])
    ] if get_poi else []
    return {
        "address": result.get("address", ""),
        "formatted_address": result.get("formatted_addresses", {}).get("recommend", "")
                             or result.get("address", ""),
        "province": ad.get("province") or comp.get("province", ""),
        "city": ad.get("city") or comp.get("city", ""),
        "district": ad.get("district") or comp.get("district", ""),
        "street": comp.get("street", ""),
        "street_number": comp.get("street_number", ""),
        "adcode": str(ad.get("adcode") or ""),
        "pois": pois,
    }


def coord_translate(points: list[tuple[float, float]], from_type: int = 1,
                    ) -> list[tuple[float, float]] | None:
    """坐标批量换算（服务端算法）。

    Args:
        points: [(lat, lng), ...]，单次上限 200 个
        from_type: 1=GPS→GCJ02  2=GCJ02→GPS  3=百度→GCJ02  4=GCJ02→百度

    本地纯函数版见 ``infra.lbs.geo``；此处用于需要与腾讯逐位对齐的场景。
    """
    if not points:
        return []
    if len(points) > 200:
        raise ValueError("坐标转换单次上限 200 个点")
    encoded = ";".join(geo.format_lat_lng(lat, lng) for lat, lng in points)
    payload = T.safe_call(T.EP_COORD_TRANSLATE, {"locations": encoded, "type": from_type})
    if payload is None:
        return None
    return [(_to_float(p.get("lat")), _to_float(p.get("lng")))
            for p in (payload.get("locations") or [])]


# =============================================
# 行政区划
# =============================================
# 行政区划全称的常见后缀，按「长的优先」排列（否则「自治区」会被「区」抢先截断）
_DISTRICT_SUFFIXES = (
    "特别行政区", "自治区", "自治州", "自治县", "地区", "盟",
    "省", "市", "区", "县", "旗",
)


def _short_name(node: dict) -> str:
    """取行政区简称。

    实测坑：``getchildren`` 与 ``list`` 的第 2、3 组**只返回 fullname，
    不返回 name**（如 ``{"id":"350102","fullname":"鼓楼区"}``）。
    若直接读 name 会得到一片空串，故此处做「无 name 则从 fullname 剥后缀」的兜底。
    """
    name = (node.get("name") or "").strip()
    if name:
        return name
    full = (node.get("fullname") or "").strip()
    for suffix in _DISTRICT_SUFFIXES:
        if full.endswith(suffix) and len(full) > len(suffix):
            return full[: -len(suffix)]
    return full


def _flatten_districts(result, *, only_group: int | None = None) -> list[dict]:
    """拍平行政区划返回结构。

    实测结构（``result`` 恒为「数组的数组」）::

        /district/v1/list      → [ [34 个省], [493 个地级市], [3094 个区县] ]
        /district/v1/search    → [ [命中的那一级] ]
        /district/v1/getchildren → [ [直接下级] ]

    用递归拍平而非固定层级：腾讯在不同端点、不同 id 深度下组数并不固定。

    Args:
        only_group: 只取指定下标的组（``district_provinces`` 用它取省级）
    """
    flat: list[dict] = []

    def _walk(node) -> None:
        if isinstance(node, list):
            for child in node:
                _walk(child)
            return
        if not isinstance(node, dict) or "id" not in node:
            return
        loc = node.get("location") or {}
        flat.append({
            "id": str(node.get("id", "")),
            "name": _short_name(node),
            "fullname": node.get("fullname") or node.get("name", ""),
            "lat": _to_float(loc.get("lat")),
            "lng": _to_float(loc.get("lng")),
            "pinyin": "".join(node.get("pinyin") or []),
            "level": _to_int(node.get("level")),
        })

    if only_group is not None and isinstance(result, list) and result:
        _walk(result[only_group] if only_group < len(result) else [])
    else:
        _walk(result)
    return flat


def district_provinces() -> list[dict] | None:
    """全量省级行政区（34 条）。

    **注意 ``/district/v1/list`` 不支持关键词过滤**：传 keyword 会被忽略，
    且返回的是「省 + 全部地级市 + 全部区县」共 3621 条（分 3 组）。
    要按名称查请用 :func:`district_search`；这里只取第 0 组。
    """
    payload = T.safe_call(T.EP_DISTRICT_LIST, None, ttl=86400)
    result = T.result_of(payload)
    if not result:
        return []
    return _flatten_districts(result, only_group=0)


def district_search(keyword: str) -> list[dict] | None:
    """按关键词检索行政区划（这才是「查某个城市/区县」的正确端点）。

    Returns:
        [{"id","name","fullname","lat","lng","pinyin","level"}, ...]
        未找到时返回 ``[]``；调用失败返回 ``None``。
    """
    payload = T.safe_call(T.EP_DISTRICT_SEARCH, {"keyword": keyword}, ttl=86400)
    result = T.result_of(payload)
    if not result:
        return []
    return _flatten_districts(result)


def district_children(district_id: str) -> list[dict] | None:
    """查下级行政区（下钻用）。

    返回项通常只有 ``id`` 与 ``fullname``（无 ``name``），
    本层已用 :func:`_short_name` 补齐 ``name``。
    """
    payload = T.safe_call(T.EP_DISTRICT_CHILDREN, {"id": district_id}, ttl=86400)
    result = T.result_of(payload)
    if not result:
        return []
    return _flatten_districts(result)


def resolve_district(name: str) -> dict | None:
    """城市/区县名 → 行政区记录（含 adcode 与中心点）。

    常见用途：把用户口语里的「福州」解析成 adcode=350100 + 中心坐标，
    供检索限定与默认地图视野使用。取检索结果的第一条（腾讯按相关度排序）。
    """
    hits = district_search(name)
    if not hits:
        return None
    return hits[0]


# =============================================
# 地点检索
# =============================================
def _build_boundary(region: str | None, near: tuple[float, float] | None,
                    radius: int | None) -> str | None:
    """构造 boundary 参数：优先周边检索，其次城市限定。"""
    if near is not None:
        r = radius or MAP.TENCENT_LBS_DEFAULT_RADIUS
        return f"nearby({geo.format_lat_lng(near[0], near[1])},{min(r, 50000)})"
    if region:
        return f"region({region},0)"
    return None


def place_search(keyword: str, *, region: str | None = None,
                 near: tuple[float, float] | None = None,
                 radius: int | None = None, page_index: int = 1,
                 page_size: int | None = None) -> list[dict] | None:
    """地点检索（关键词 + 地域/周边限定）。

    Args:
        near: (lat, lng)。给了它就走周边检索，结果会带 distance_m 且按距离排序
        region: 城市名。既没给 near 也没给 region 时用配置里的默认城市兜底，
               否则腾讯会返回全国的噪声结果
    """
    boundary = _build_boundary(region, near, radius)
    if boundary is None:
        boundary = f"region({MAP.TENCENT_LBS_DEFAULT_REGION},0)"
    payload = T.safe_call(T.EP_PLACE_SEARCH, {
        "keyword": keyword,
        "boundary": boundary,
        "page_index": max(1, page_index),
        "page_size": min(page_size or MAP.TENCENT_LBS_MAX_PAGE_SIZE,
                         MAP.TENCENT_LBS_MAX_PAGE_SIZE),
    })
    if payload is None:
        return None
    return [_normalize_poi(p) for p in (payload.get("data") or [])]


def place_suggestion(keyword: str, *, region: str | None = None,
                     region_fix: bool = True,
                     page_size: int | None = None) -> list[dict] | None:
    """关键词输入提示（联想补全）。

    与 ``place_search`` 的区别：更适合用户只打了半截词（「鼓」）时的补全，
    返回的是不带地址详情的轻量候选。
    """
    payload = T.safe_call(T.EP_PLACE_SUGGESTION, {
        "keyword": keyword,
        "region": region or MAP.TENCENT_LBS_DEFAULT_REGION,
        "region_fix": 1 if region_fix else 0,
        "page_size": min(page_size or 10, MAP.TENCENT_LBS_MAX_PAGE_SIZE),
    })
    if payload is None:
        return None
    return [_normalize_poi(p) for p in (payload.get("data") or [])]


# =============================================
# 路线规划
# =============================================
def direction(mode: str, from_lat: float, from_lng: float,
              to_lat: float, to_lng: float, *, policy: str | None = None) -> dict | None:
    """路线规划（单段）。

    Args:
        mode: driving / walking / bicycling / transit
        policy: 仅驾车有效，LEAST_TIME / LEAST_DISTANCE / AVOID_HIGHWAY / REAL_TRAFFIC

    Returns:
        {"mode","distance_m","distance_km","duration_min","duration_s",
         "taxi_fare_cny","toll_cny","traffic_light_count","steps":[{"instruction",
         "road","distance_m","duration_min","direction"}]}

    **单位陷阱（已实测确认）**：``/direction`` 返回的 ``duration`` 单位是
    **分钟**，而 ``/distance/matrix`` 返回的 ``duration`` 单位是**秒**。
    本层统一归一为 ``duration_s`` 与 ``duration_min``，调用方不再关心原始单位。
    """
    mode = (mode or "driving").lower()
    if mode not in ROUTE_MODES:
        raise ValueError(f"不支持的出行方式: {mode}（可选 {ROUTE_MODES}）")

    params = {
        "from": geo.format_lat_lng(from_lat, from_lng),
        "to": geo.format_lat_lng(to_lat, to_lng),
    }
    if mode == "driving" and policy:
        if policy not in DRIVING_POLICIES:
            raise ValueError(f"不支持的驾车策略: {policy}（可选 {DRIVING_POLICIES}）")
        params["policy"] = policy

    # 路线结果带实时路况，TTL 给短一些
    payload = T.safe_call(
        T.EP_DIRECTION.format(mode=mode), params,
        ttl=MAP.TENCENT_LBS_ROUTE_CACHE_TTL,
    )
    route = _route_of(payload)
    if route is None:
        return None

    duration_min = _to_float(route.get("duration"))
    steps = []
    for s in (route.get("steps") or []):
        steps.append({
            "instruction": s.get("instruction", ""),
            "road": s.get("road_name", ""),
            "distance_m": _to_int(s.get("distance")),
            "duration_min": _to_float(s.get("duration")),
            "direction": s.get("dir_desc", ""),
        })

    distance_m = _to_int(route.get("distance"))
    taxi = (route.get("taxi_fare") or {}).get("fare")
    return {
        "mode": route.get("mode", mode.upper()),
        "distance_m": distance_m,
        "distance_km": round(distance_m / 1000.0, 3),
        "duration_min": duration_min,
        "duration_s": int(round(duration_min * 60)),
        "taxi_fare_cny": _to_float(taxi) if taxi is not None else None,
        # 公交的 price 单位是「分」，驾车/步行的 toll 单位是「元」
        "price_cny": round(_to_int(route.get("price")) / 100.0, 2)
                     if route.get("price") is not None else None,
        "toll_cny": _to_int(route.get("toll")),
        "traffic_light_count": _to_int(route.get("traffic_light_count")),
        "steps": steps,
        "polyline_compressed": route.get("polyline") or [],
    }


def distance_matrix(mode: str, from_points: list[tuple[float, float]],
                    to_points: list[tuple[float, float]]) -> list[list[dict]] | None:
    """距离矩阵：一次请求算 N×M 段的距离与时长。

    比循环调 ``direction`` 省得多（N×M 次 → 1 次），适合行程排程的
    候选点两两通勤预计算。**不返回路线详情**，只有距离与时长。

    Args:
        from_points: 起点列表，每项 (lat, lng)，总数上限 25
        to_points:   终点列表，每项 (lat, lng)，总数上限 25

    Returns:
        二维数组 ``matrix[i][j] = {"distance_m","duration_s","duration_min"}``
        —— 注意 duration 原始单位是**秒**（与 ``direction`` 的分钟不同），
        这里已归一。
    """
    mode = (mode or "driving").lower()
    if mode not in MATRIX_MODES:
        raise ValueError(f"距离矩阵仅支持 {MATRIX_MODES}，收到 {mode!r}")
    if not from_points or not to_points:
        return []

    payload = T.safe_call(T.EP_DISTANCE_MATRIX, {
        "mode": mode,
        "from": ";".join(geo.format_lat_lng(a, b) for a, b in from_points),
        "to": ";".join(geo.format_lat_lng(a, b) for a, b in to_points),
    }, ttl=MAP.TENCENT_LBS_ROUTE_CACHE_TTL)
    if payload is None:
        return None

    matrix: list[list[dict]] = []
    for row in ((payload.get("result") or {}).get("rows") or []):
        line: list[dict] = []
        for el in (row.get("elements") or []):
            secs = _to_int(el.get("duration"))
            line.append({
                "distance_m": _to_int(el.get("distance")),
                "duration_s": secs,
                "duration_min": round(secs / 60.0, 1),
            })
        matrix.append(line)
    return matrix


# =============================================
# 天气
# =============================================
# 腾讯 /ws/weather/v1/ 的 type 取值
WEATHER_KINDS = ("now", "future", "hours")

_WEATHER_FIELDS = ("weather", "temperature", "wind_direction", "wind_power",
                   "wind_power_v2", "humidity", "air_pressure")


def _weather_info(raw: dict | None) -> dict:
    """归一单条天气记录（实时 / 预报的 day.night / 逐小时共用同一组字段）。"""
    src = raw or {}
    return {k: src[k] for k in _WEATHER_FIELDS if src.get(k) is not None}


def _weather_meta(rec: dict) -> dict:
    return {
        "province": rec.get("province", ""),
        "city": rec.get("city", ""),
        "district": rec.get("district", ""),
        "adcode": str(rec.get("adcode", "")),
        "update_time": rec.get("update_time", ""),
    }


def weather(*, adcode: str | None = None,
            location: tuple[float, float] | None = None,
            kind: str = "now") -> dict | None:
    """天气查询。

    Args:
        adcode: 行政区划编码（如 ``350100``）；与 ``location`` 二选一
        location: ``(lat, lng)``。**实测比 adcode 更精确** —— 传 adcode=350100
            只返回到市级（district 为空），传坐标能返回到鼓楼区（adcode 350102）
        kind: ``now``(实时) / ``future``(未来几天，含昼夜) / ``hours``(逐小时)

    Returns:
        统一为 ``{"kind", "province", "city", "district", "adcode",
        "update_time", ...}``，其中按 kind 附带：

        - ``now``    → ``current``（单条）
        - ``future`` → ``days``（每条含 ``day`` 与 ``night`` 两组）
        - ``hours``  → ``hours``（每条含 ``hour`` 时刻）

    注意 ``future`` 的 ``infos`` 是**列表**，而 ``now`` 的 ``infos`` 是**字典** ——
    同名字段两种类型，不区分处理会直接报错。
    """
    kind = (kind or "now").strip().lower()
    if kind not in WEATHER_KINDS:
        raise ValueError(f"不支持的天气类型: {kind}（可选 {WEATHER_KINDS}）")

    params: dict[str, str] = {"type": kind}
    if location is not None:
        params["location"] = geo.format_lat_lng(location[0], location[1])
    elif adcode:
        params["adcode"] = str(adcode)
    else:
        raise ValueError("adcode 与 location 至少提供一个")

    ttl = (MAP.TENCENT_LBS_FORECAST_TTL if kind == "future"
           else MAP.TENCENT_LBS_WEATHER_TTL)
    payload = T.safe_call(T.EP_WEATHER, params, ttl=ttl)
    if payload is None:
        return None
    result = payload.get("result") or {}

    if kind == "now":
        rec = (result.get("realtime") or [{}])[0]
        return {"kind": kind, **_weather_meta(rec), "current": _weather_info(rec.get("infos"))}

    if kind == "future":
        rec = (result.get("forecast") or [{}])[0]
        days = [{
            "date": item.get("date", ""),
            "week": item.get("week", ""),
            "day": _weather_info(item.get("day")),
            "night": _weather_info(item.get("night")),
        } for item in (rec.get("infos") or [])]
        return {"kind": kind, **_weather_meta(rec), "days": days}

    rec = (result.get("forecast_hours") or [{}])[0]
    hours = [{"hour": item.get("hour", ""), **_weather_info(item.get("info"))}
             for item in (rec.get("infos") or [])]
    return {"kind": kind, **_weather_meta(rec), "hours": hours}


def weather_for_city(city: str, kind: str = "now") -> dict | None:
    """按城市名查天气（内部先把城市名解析成 adcode）。

    传 adcode 会丢精度（只到市级），所以解析出 adcode 后再补一次坐标查询，
    拿到的结果能精确到区县 —— 对「今天下午在鼓楼区会不会下雨」这类问题更实用。
    """
    district = resolve_district(city)
    if district:
        center = (district["lat"], district["lng"])
        if center[0] and center[1]:
            precise = weather(location=center, kind=kind)
            if precise is not None:
                return precise
        return weather(adcode=district["id"], kind=kind)
    # 解析不到行政区时退化为按城市名当关键词再试一次
    return weather(adcode=city, kind=kind) if city.isdigit() else None


# =============================================
# 街景
# =============================================
# ⚠ 状态说明（诚实标注）：街景服务的端点路径已实测确认存在
# （请求返回 113「此功能未被授权」而非 404「错误的请求路径」），但本 Key
# 尚未开通该服务，因此**下面的响应字段解析基于官方文档而非实测**。
# 故此处刻意采用宽松提取 + 原始 payload 透传，不硬编码字段名，
# 待服务开通后跑 scripts/verify_tencent_lbs.py 的街景段落即可核实并收紧。


def street_view_pano(lat: float, lng: float, *, radius: int = 50) -> dict:
    """取坐标附近的街景全景点。

    Args:
        radius: 搜索半径（米），越大越可能命中但可能偏离目标点

    Returns:
        ``{"pano", "lat", "lng", "description", "raw"}``

    Raises:
        TencentLbsError: 未开通街景服务时抛 113，异常消息里带官方申请路径。
            这里**刻意不吞异常** —— 与其他接口返回 None 的降级策略不同：
            街景失败的原因是「需要用户去申请开通」，这个指引必须传到用户眼前，
            静默返回 None 会让用户以为「这个坐标没有街景」。
    """
    payload = T.call_sync(T.EP_STREETVIEW_PANO, {
        "location": geo.format_lat_lng(lat, lng),
        "radius": max(1, min(int(radius or 50), 1000)),
    })
    result = payload.get("result") or {}
    loc = result.get("location") or {}
    if not loc and isinstance(result.get("location"), dict):
        loc = result["location"]
    return {
        "pano": result.get("pano") or result.get("id") or "",
        "lat": _to_float(loc.get("lat")) or lat,
        "lng": _to_float(loc.get("lng")) or lng,
        "description": result.get("description") or "",
        "raw": result,
    }


def street_view_image_bytes(pano: str, *, heading: int = 0, pitch: int = 0,
                            width: int = 640, height: int = 480,
                            format_: str = "jpg") -> bytes | None:
    """取街景全景图片字节流（供后端代理转发）。

    Args:
        pano: 全景点 id（来自 :func:`street_view_pano`）
        heading: 水平朝向角度 0~360（0=正北）
        pitch: 垂直俯仰 -90~90（负数向下看）
    """
    if not pano:
        return None
    size = f"{max(50, min(int(width), 2048))}x{max(50, min(int(height), 2048))}"
    params = {
        "pano": pano, "size": size,
        "heading": max(0, min(int(heading), 360)),
        "pitch": max(-90, min(int(pitch), 90)),
        "format": format_,
    }
    try:
        return T.call_bytes_sync(T.EP_STREETVIEW_IMAGE, params)
    except T.TencentLbsError as e:
        logger.info("[LBS] 街景图片获取失败: %s", e)
        return None


# =============================================
# 地图调起（URI API）—— Web 端「导航」的可行路径
# =============================================
#: 调起支持的出行方式（URI API 的 type 参数）
URI_MODES = ("drive", "walk", "bus", "bike")
#: 驾车策略（URI API 的 policy 参数，注意与 WebService 的字符串枚举不同）
URI_DRIVE_POLICIES = {0: "默认", 1: "避免拥堵", 2: "躲避收费", 3: "不走高速"}


def navigation_uri(*, to_lat: float, to_lng: float, to_name: str = "",
                   from_lat: float | None = None, from_lng: float | None = None,
                   from_name: str = "", mode: str = "drive", policy: int = 0,
                   coord_type: int = 2) -> dict:
    """生成「在腾讯地图 App / 手机网页中打开导航」的调起链接。

    **为什么 Web 端的导航要用它**：腾讯位置服务没有 Web 端导航 SDK，
    ``Android导航SDK`` / ``iOS导航SDK`` 都是原生库。网页里想要「真导航」
    （语音播报、实时偏航重算），只能把目的地交给腾讯地图 App 来做。

    Args:
        to_lat/to_lng: 目的地坐标（GCJ-02）
        to_name: 目的地名称，会显示在调起页面上
        from_lat/from_lng/from_name: 起点；不传则以用户**当前位置**为起点
        mode: drive / walk / bus / bike。**空串按 drive 处理**（本层对缺省宽松，
            严格校验在工具层做 —— LLM 的输入需要明确报错，服务间调用则不必）
        policy: 仅驾车有效，见 :data:`URI_DRIVE_POLICIES`
        coord_type: 1=GPS(WGS-84) 2=GCJ-02(默认) 3=百度

    Returns:
        ``{"url", "key_kind", "warning"?}``

        ``key_kind`` 为 ``"frontend"`` 表示用的是前端专用 Key（安全）；
        为 ``"backend"`` 表示回退用了后端 Key，此时 ``warning`` 会明确提示
        该 URL **不应发给浏览器**，应改配 ``TENCENT_LBS_FRONTEND_KEY``。
    """
    mode = (mode or "drive").strip().lower()
    if mode not in URI_MODES:
        raise ValueError(f"不支持的调起方式: {mode}（可选 {URI_MODES}）")
    if policy not in URI_DRIVE_POLICIES:
        raise ValueError(f"不支持的驾车策略: {policy}（可选 {sorted(URI_DRIVE_POLICIES)}）")
    if not MAP.is_configured():
        raise T.TencentLbsError("腾讯位置服务未配置：请在 .env 设置 TENCENT_LBS_KEY",
                                status=190)

    pairs: list[tuple[str, str]] = [("type", mode)]
    if from_lat is not None and from_lng is not None:
        pairs.append(("fromcoord", geo.format_lat_lng(from_lat, from_lng)))
        if from_name:
            pairs.append(("from", from_name))
    pairs.append(("tocoord", geo.format_lat_lng(to_lat, to_lng)))
    if to_name:
        pairs.append(("to", to_name))
    if mode == "drive":
        pairs.append(("policy", str(policy)))
    pairs.append(("coord_type", str(coord_type)))

    if MAP.TENCENT_LBS_FRONTEND_KEY:
        url = T.build_uri_url(T.EP_URI_ROUTEPLAN, pairs, key=MAP.TENCENT_LBS_FRONTEND_KEY)
        return {"url": url, "key_kind": "frontend", "mode": mode,
                "policy": URI_DRIVE_POLICIES[policy] if mode == "drive" else ""}

    # 回退用后端 Key：能生成链接，但绝不能外发（实测腾讯会把该 Key
    # 原样带进 302 跳转目标，等于把后端配额暴露给浏览器）
    url = T.build_uri_url(T.EP_URI_ROUTEPLAN, pairs, key=MAP.TENCENT_LBS_KEY)
    return {
        "url": url,
        "key_kind": "backend",
        "mode": mode,
        "policy": URI_DRIVE_POLICIES[policy] if mode == "drive" else "",
        "warning": (
            "当前使用后端 Key 生成调起链接，该 URL 含密钥，不要直接返回给浏览器。"
            "请在 .env 配置 TENCENT_LBS_FRONTEND_KEY（前端专用 Key + Referer 白名单）后重试。"
        ),
    }


# =============================================
# 静态图
# =============================================
def _markers_param(markers: list[dict]) -> list[tuple[str, str]]:
    """把标注列表按样式分组，生成可重复的 markers 查询参数。

    腾讯要求同一样式的多个点写在同一个 markers 值里（``样式|坐标|坐标``），
    不同样式才拆成多个 markers 参数；且总数上限 50。
    """
    groups: dict[tuple, list[str]] = {}
    for m in (markers or [])[:50]:
        key = (m.get("size", "mid"), m.get("color", "red"), m.get("label", ""))
        groups.setdefault(key, []).append(
            geo.format_lat_lng(_to_float(m.get("lat")), _to_float(m.get("lng")))
        )
    params: list[tuple[str, str]] = []
    for (size, color, label), coords in groups.items():
        styles = [f"size:{size}", f"color:{color}"]
        if label:
            styles.append(f"label:{label}")
        params.append(("markers", "|".join(styles) + "|" + "|".join(coords)))
    return params


def static_map_url(*, center: tuple[float, float] | None = None, zoom: int = 14,
                   size: str = "600*400", maptype: str = "roadmap", scale: int = 1,
                   markers: list[dict] | None = None) -> str | None:
    """拼静态图 URL（**含 Key，仅供服务端使用，禁止回传给前端**）。

    前端要用图片时请走 ``GET /api/map/static-map``，由后端取字节流转发，
    这样 Key 不会出现在浏览器地址栏、历史记录和 Referer 里。
    """
    if not MAP.is_configured():
        return None
    params: list[tuple[str, str]] = [("size", size), ("maptype", maptype), ("zoom", str(zoom))]
    if scale == 2:
        params.append(("scale", "2"))
    if center is not None:
        # 腾讯文档：center 与 zoom 成对使用，zoom 取值范围 4~18
        params.insert(0, ("center", geo.format_lat_lng(center[0], center[1])))
    params.extend(_markers_param(markers or []))
    params.append(("key", MAP.TENCENT_LBS_KEY))
    return f"{MAP.TENCENT_LBS_HOST}{T.EP_STATIC_MAP}?{urlencode(params)}"


def static_map_bytes(*, center: tuple[float, float] | None = None, zoom: int = 14,
                     size: str = "600*400", maptype: str = "roadmap", scale: int = 1,
                     markers: list[dict] | None = None) -> bytes | None:
    """取静态图字节流（PNG），供 ``/api/map/static-map`` 代理转发。"""
    # 用键值对列表而非 dict：markers 需要同键多值
    params: list[tuple[str, str]] = [("size", size), ("maptype", maptype), ("zoom", str(zoom))]
    if scale == 2:
        params.append(("scale", "2"))
    if center is not None:
        params.insert(0, ("center", geo.format_lat_lng(center[0], center[1])))
    params.extend(_markers_param(markers or []))
    try:
        return T.call_bytes_sync(T.EP_STATIC_MAP, params)
    except T.TencentLbsError as e:
        logger.info("[LBS] 静态图生成失败: %s", e)
        return None


def capability_report() -> dict:
    """当前可用能力自检信息（供 /api/map/health 使用）。"""
    return {
        "configured": MAP.is_configured(),
        "enabled": MAP.TENCENT_LBS_ENABLED,
        "host": MAP.TENCENT_LBS_HOST,
        "signature": bool(MAP.TENCENT_LBS_SK),
        # 前端专用 Key 是否就绪：决定地图调起能否安全地交给浏览器
        "frontend_key": bool(MAP.TENCENT_LBS_FRONTEND_KEY),
        "cache_enabled": MAP.TENCENT_LBS_CACHE_ENABLED,
        "min_interval_s": MAP.TENCENT_LBS_MIN_INTERVAL,
        "travel_live_map": MAP.is_live_map_enabled(),
        "default_region": MAP.TENCENT_LBS_DEFAULT_REGION,
        "endpoints": [
            T.EP_IP_LOCATION, T.EP_GEOCODER, T.EP_PLACE_SEARCH,
            T.EP_PLACE_SUGGESTION, T.EP_DISTRICT_LIST, T.EP_DISTRICT_SEARCH,
            T.EP_DISTRICT_CHILDREN, T.EP_DIRECTION.format(mode="driving"),
            T.EP_DISTANCE_MATRIX, T.EP_COORD_TRANSLATE, T.EP_STATIC_MAP,
            T.EP_WEATHER, T.EP_STREETVIEW_PANO, T.EP_STREETVIEW_IMAGE,
            T.EP_URI_ROUTEPLAN, T.EP_URI_MARKER,
        ],
    }


def service_availability() -> dict:
    """探活各附加服务：能跑通返回 True，未授权/不可用记录原因。

    与 :func:`capability_report` 的区别：那个只读配置，这个**真发请求**。
    用于一眼看出「哪些服务已经能用、哪些还要去申请」。

    Returns:
        ``{服务名: {"ok": bool, "detail": str}}``
    """
    probes: list[tuple[str, callable]] = [
        ("天气", lambda: weather(location=(26.0824, 119.2968), kind="now")),
        ("街景", lambda: street_view_pano(26.0824, 119.2968)),
        ("行政区划", lambda: resolve_district("福州")),
        ("路线规划", lambda: direction("driving", 26.0824, 119.2968, 26.049, 119.3896)),
    ]
    out: dict[str, dict] = {}
    for name, fn in probes:
        try:
            value = fn()
            out[name] = {"ok": bool(value), "detail": "" if value else "返回空结果"}
        except T.TencentLbsError as e:
            out[name] = {"ok": False, "detail": str(e)}
        except Exception as e:  # noqa: BLE001 — 探活不应因单个服务异常而整体失败
            out[name] = {"ok": False, "detail": f"{type(e).__name__}: {e}"}
    return out
