"""tools/travel/live_map.py — 把腾讯位置服务接入旅游域

两处接入，各解决一个 P0 遗留问题：

1. **通勤时长**：``live_leg`` 作为 ``routing.set_route_provider`` 的数据源，
   把「直线距离 × 1.35 ÷ 22km/h」换成腾讯真实路径与路况。
   排程专家、校验器、行程单都无需改动 —— 升级点被收敛在 ``estimate_leg`` 内部。

2. **必去地点缺失**：``resolve_missing_places`` 用腾讯 POI 库把用户点名、
   但本地种子池没有的地点解析成真实坐标补进候选池。P0 遇到这种情况只能在
   备注里写一句「未匹配到」，用户的明确诉求就这么丢了。

**溯源纪律**：腾讯检索不返回营业时间与票价，因此新增的 Poi 只把坐标与名称
当作权威事实（``source="tencent:lbs"``），营业时段沿用契约默认值并如实标注
来源。行程单渲染时会展示 source，用户能分辨哪些是核实过的、哪些是默认值。
"""
from __future__ import annotations

import hashlib
import math
from typing import Iterable

from backend.config import map as MAP
from backend.config import travel as T
from backend.infra.lbs import api
from backend.shared.logger import logger
from backend.tools.travel import routing
from backend.travel.models.poi import (
    CATEGORY_MEAL,
    CATEGORY_NIGHT,
    CATEGORY_PARK,
    CATEGORY_SHOPPING,
    CATEGORY_VISIT,
    Poi,
)

SOURCE_LBS = routing.SOURCE_LIVE


def stable_fallback_id(name: str, city: str) -> str:
    """腾讯未返回 id 时的确定性兜底标识：sha1(name|city) 前 12 位。

    同一 (地点名, 城市) 在任何进程任何时间都得到同一 id——这是
    「poi_id 稳定性」契约的底线（对照：内建 hash() 带进程盐，不可用）。
    """
    digest = hashlib.sha1(f"{name}|{city}".encode("utf-8")).hexdigest()
    return digest[:12]

# 原点位解析的安全边界：用户说「福州的土楼」时腾讯可能返回 250km 外的永定土楼，
# 直接塞进行程会造成「一天跨两个城市」的荒谬排程。超出此半径的解析结果丢弃，
# 退回原有的「未匹配到」提示，由对话层去澄清。
_MAX_RESOLVE_DISTANCE_KM = 120.0

# 腾讯 POI 类别（形如 "旅游景点:国家级景点"）→ 本项目类别常量
# 顺序敏感：先匹配具体类别，再落到宽泛的「景点」
_CATEGORY_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("公园", "广场", "绿地", "植物园"), CATEGORY_PARK),
    (("餐饮", "美食", "餐厅", "小吃", "咖啡", "茶艺"), CATEGORY_MEAL),
    (("购物", "商场", "超市", "市场", "商业街"), CATEGORY_SHOPPING),
    (("酒吧", "KTV", "夜总会", "娱乐", "剧院", "演出"), CATEGORY_NIGHT),
    (("景点", "风景", "名胜", "博物", "寺庙", "教堂", "古迹", "文化", "纪念馆"),
     CATEGORY_VISIT),
)


def map_category(tx_category: str) -> str:
    """腾讯 POI 类别 → 本项目类别常量（未识别时按「景点」处理）。"""
    text = tx_category or ""
    for keywords, category in _CATEGORY_RULES:
        if any(k in text for k in keywords):
            return category
    return CATEGORY_VISIT


def is_enabled() -> bool:
    """真实地图数据是否启用（总开关 + Key 均已就绪）。"""
    return MAP.is_live_map_enabled()


# =============================================
# LBS 调用 span（任务书 §11，Phase 4）
# =============================================
# 此前腾讯 API 往返没有埋点：provider 延迟与失败率在 trace 里不可见，
# 「行程慢」无法归因到 LBS。span 只住**真实 API 调用**（缓存命中不打，
# 否则延迟统计失真）；软失败 —— 无活跃 trace 时为 noop，绝不影响取数。
def _lbs_span(span_id: str, span_name: str, **inputs):
    # 形参叫 span_name 而非 name：inputs 里可能带 name=（地点名），别占这个名字
    try:
        from backend.observability.tracer import trace_collector
        return trace_collector.start_span(
            span_id, name=span_name, type="tool_call", kind="tool",
            input=dict(inputs),
        )
    except Exception:
        return None


def _end_lbs_span(span, *, status: str = "success", **metrics) -> None:
    if span is None:
        return
    try:
        from backend.observability.tracer import trace_collector
        trace_collector.end_span(span, metrics=metrics, status=status)
    except Exception:
        pass


# =============================================
# 1. 通勤时长：真实路线
# =============================================
# 路段缓存（2026-09-15 性能优化）：同一段路线在一次会话/多天行程里可能被
# 重复求解（排程、修复重排、校验都会问到），每次都是一次腾讯 API 往返。
# 键用 4 位小数坐标（约 11m 精度）——排程用的坐标本身来自 POI 库，重复度极高。
_LEG_CACHE: dict[tuple, tuple[float, dict]] = {}
_LEG_CACHE_TTL = 900.0  # 15 分钟


def _leg_cache_key(from_lat: float, from_lng: float, to_lat: float, to_lng: float,
                   mode: str = "main") -> tuple:
    # mode 维度（#41）：主路线（driving/walking 归并为 "main"）与公交候选
    # （"transit"）同坐标共存，混键会让候选顶掉主路线的缓存
    return (round(from_lat, 4), round(from_lng, 4), round(to_lat, 4), round(to_lng, 4), mode)


_PREFETCH_BUDGET_S = 4.0  # 预热硬性时间预算：超时未回的段直接放弃（串行路径自会兜底）


def prefetch_legs(pairs, max_workers: int = 6, loader=None, mode: str = "main") -> None:
    """并行预热路段缓存（best-effort，**有硬性时间预算**）。

    pairs: 可迭代的 (from_lat, from_lng, to_lat, to_lng) 四元组。
    loader: 取数函数，默认 live_leg（主路线）；公交候选预热（#41）
    传 live_leg_transit 并配 mode="transit" —— 缓存命中检查必须与
    loader 的键维度一致，否则会拿主路线的 main 键误判「已有缓存」。
    """
    loader = loader or live_leg
    unique = []
    seen = set()
    import time as _t
    for p in pairs:
        key = _leg_cache_key(*p, mode=mode)
        if key in seen:
            continue
        seen.add(key)
        hit = _LEG_CACHE.get(key)
        if hit and _t.monotonic() - hit[0] < _LEG_CACHE_TTL:
            continue
        unique.append(p)
    if not unique:
        return
    try:
        from concurrent.futures import ThreadPoolExecutor, wait
        pool = ThreadPoolExecutor(max_workers=min(max_workers, len(unique)))
        try:
            futures = [pool.submit(loader, *p) for p in unique]
            _, not_done = wait(futures, timeout=_PREFETCH_BUDGET_S)
            for f in not_done:
                f.cancel()
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
    except Exception as e:  # noqa: BLE001 — 预热失败不影响主流程
        from backend.shared.logger import logger
        logger.debug(f"[TravelLiveMap] 路段预热失败（不致命）: {e}")


def live_leg(from_lat: float, from_lng: float, to_lat: float, to_lng: float) -> dict | None:
    """真实路线通勤估算，返回与 ``routing.estimate_leg`` 同构的 dict。

    返回 None 表示本段未取到真实数据，由 ``estimate_leg`` 回落本地估算。

    出行方式沿用地铁/步行的既有判定逻辑（``routing.choose_mode``），
    把 "walk" 映射为腾讯的 walking、其余映射为 driving —— 公交（transit）
    不作为默认，因为公交路线在腾讯返回的是「步行 + 地铁 + 公交」多段拼接，
    duration 含候车时间，与「打车/自驾」的口径混用会让行程单前后不一致。
    """
    if not is_enabled():
        return None

    import time as _t
    ck = _leg_cache_key(from_lat, from_lng, to_lat, to_lng)
    cached = _LEG_CACHE.get(ck)
    if cached and _t.monotonic() - cached[0] < _LEG_CACHE_TTL:
        return cached[1]

    straight = routing.haversine_km(from_lat, from_lng, to_lat, to_lng)
    mode = routing.choose_mode(straight * T.TRAVEL_ROUTE_DETOUR_FACTOR)
    tx_mode = "walking" if mode == "walk" else "driving"

    span = _lbs_span("travel_lbs_direction", "LBS路线规划", mode=tx_mode)
    route = api.direction(tx_mode, from_lat, from_lng, to_lat, to_lng)
    if route is None or not route.get("distance_km"):
        _end_lbs_span(span, status="error", mode=tx_mode, reason="no_route")
        return None

    distance_km = float(route["distance_km"])
    # 分钟取整向上：宁可让用户早到 1 分钟，也不要让他赶不上
    minutes = max(routing.MIN_LEG_MINUTES.get(mode, 5),
                  int(math.ceil(float(route.get("duration_min") or 0))))

    if mode == "walk":
        cost = 0.0
    else:
        # 腾讯返回的 taxi_fare 是真实计程车价，比「起步价 + 里程费」的
        # 拍脑袋模型准；未返回时退回本地模型。
        taxi = route.get("taxi_fare_cny")
        cost = float(taxi) if taxi else routing.leg_cost_cny(distance_km, mode)

    result = {
        "distance_km": round(distance_km, 2),
        "mode": mode,
        "minutes": minutes,
        "cost_cny": round(cost, 2),
        "source": SOURCE_LBS,
    }
    _end_lbs_span(span, status="success", mode=tx_mode,
                  distance_km=round(distance_km, 2), minutes=minutes)
    _LEG_CACHE[ck] = (_t.monotonic(), result)
    return result


# =============================================
# 1.5 公交/地铁候选（验收 #41）—— 候选参考，不作主路线口径
# =============================================
# 腾讯 transit 结果是「步行 + 地铁 + 公交」多段拼接，duration 含候车时间，
# 与打车/自驾口径混用会让行程单前后不一致（设计边界，非缺陷）。因此公交
# 只作为 TransitLeg.transit_option 候选展示：不进时间轴计算、不重排、
# 不动主路线的 is_estimate 语义。
def _transit_summary(steps: list) -> str:
    """把腾讯 transit 多段 steps 提炼成一句乘坐摘要（#41）。

    实测结构（api.direction 归一化后）：WALKING 段带 distance_m 与嵌套
    instruction，TRANSIT 段带 lines[]（vehicle=SUBWAY/BUS、title=线路名、
    geton/getoff=上下车站、station_count）。拼出「步行 1116m → 地铁2号线
    （南门兜 → 鼓山）」形态；解析不出内容返回空串（前端按无摘要展示，
    不影响时长/距离）。总长 100 字封顶。
    """
    _VEHICLE_LABEL = {"subway": "地铁", "bus": "公交", "rail": "城铁", "tram": "有轨"}
    texts: list[str] = []
    for s in steps:
        s = s or {}
        lines = s.get("lines") or []
        if lines:
            for ln in lines[:2]:  # 同段换乘一般 ≤2 条线
                label = _VEHICLE_LABEL.get(ln.get("vehicle", ""), "乘坐")
                title = ln.get("title", "")
                # 线路名常自带模式前缀（「地铁2号线」），避免拼出「地铁地铁2号线」
                seg = title if label in title else f"{label}{title}".strip()
                if ln.get("geton") and ln.get("getoff"):
                    seg += f"（{ln['geton']} → {ln['getoff']}）"
                if seg:
                    texts.append(seg)
        elif s.get("instruction"):
            texts.append(s["instruction"][:24])
        elif s.get("distance_m"):
            texts.append(f"步行 {s['distance_m']}m")
        if len(texts) >= 4:
            break
    return " → ".join(texts[:4])[:100]


def live_leg_transit(from_lat: float, from_lng: float,
                     to_lat: float, to_lng: float) -> dict | None:
    """公交/地铁候选路线，返回 ``TransitLeg.transit_option`` 同构的 dict。

    返回 None 表示本段无候选（接口失败/未启用），调用方按「无候选」
    处理 —— 与 live_leg 的 None 语义同款，绝不抛异常打断排程。
    """
    if not is_enabled():
        return None

    import time as _t
    ck = _leg_cache_key(from_lat, from_lng, to_lat, to_lng, mode="transit")
    cached = _LEG_CACHE.get(ck)
    if cached and _t.monotonic() - cached[0] < _LEG_CACHE_TTL:
        return cached[1]

    span = _lbs_span("travel_lbs_direction_transit", "LBS公交候选", mode="transit")
    route = api.direction("transit", from_lat, from_lng, to_lat, to_lng)
    if route is None or not route.get("distance_km"):
        _end_lbs_span(span, status="error", mode="transit", reason="no_route")
        return None

    result = {
        "duration_min": max(1, int(round(float(route.get("duration_min") or 0)))),
        "distance_m": int(route.get("distance_m") or 0),
        "summary": _transit_summary(route.get("steps") or []),
        "is_estimate": False,
    }
    _end_lbs_span(span, status="success",
                  minutes=result["duration_min"], distance_m=result["distance_m"])
    _LEG_CACHE[ck] = (_t.monotonic(), result)
    return result


def peek_leg_transit(from_lat: float, from_lng: float,
                     to_lat: float, to_lng: float) -> dict | None:
    """只读缓存版 ``live_leg_transit``（排程组装处专用，#41）。

    排程主链路对延迟零容忍（P95 基线冻结），公交候选只允许消费
    :func:`prefetch_day_legs` 的预热成果，缓存 miss 即无候选 ——
    绝不在组装循环里现场等待网络。
    """
    if not is_enabled():
        return None
    import time as _t
    ck = _leg_cache_key(from_lat, from_lng, to_lat, to_lng, mode="transit")
    cached = _LEG_CACHE.get(ck)
    if cached and _t.monotonic() - cached[0] < _LEG_CACHE_TTL:
        return cached[1]
    return None


def install_live_map() -> bool:
    """按配置接入真实路线数据源（幂等）。

    Returns:
        True 表示已接入，False 表示未启用（保持本地估算）。
    """
    if not is_enabled():
        logger.info("[TravelLiveMap] 未启用（TRAVEL_USE_LIVE_MAP=%s, key=%s）",
                    MAP.TRAVEL_USE_LIVE_MAP, bool(MAP.TENCENT_LBS_KEY))
        routing.set_route_provider(None)
        return False
    routing.set_route_provider(live_leg)
    logger.info("[TravelLiveMap] 已接入腾讯位置服务真实路线规划")
    return True


# =============================================
# 2. 必去地点解析：补齐本地候选池的缺口
# =============================================
def _city_center(city: str) -> tuple[float, float] | None:
    """取城市中心坐标，用于距离安全边界判定。"""
    district = api.resolve_district(city)
    if district and (district["lat"] or district["lng"]):
        return district["lat"], district["lng"]
    return None


def _straight_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    return routing.haversine_km(a[0], a[1], b[0], b[1])


def _same_city(hit_city: str, target_city: str) -> bool | None:
    """POI 真实归属城市与目标城市是否同城（验收 #83）。

    口径：剥「市辖区/城区/市/地区/自治州」等行政后缀后双向包含（「福州市」
    与「福州」同城）。返回 None = hit 未带归属信息（数据缺失不误杀，放行）；
    True = 同城；False = 异地。
    """
    a = (hit_city or "").strip()
    b = (target_city or "").strip()
    if not a or not b:
        return None
    for suffix in ("市辖区", "城区", "自治州", "地区", "市"):
        if a.endswith(suffix) and len(a) > len(suffix):
            a = a[: -len(suffix)]
        if b.endswith(suffix) and len(b) > len(suffix):
            b = b[: -len(suffix)]
        if not a or not b:
            return None
    return a in b or b in a


def resolve_place(name: str, city: str, *, required: bool = False) -> Poi | None:
    """把一个地点名解析为 Poi（腾讯 POI 库）。

    Args:
        name: 用户点名的地点，如 "平潭岛"、"鼓岭"
        city: 所属城市，用于限定检索范围与「城市键」对齐
        required: 是否标记为「用户点名必去」。

            **必须由调用方显式传入**：``poi_expert`` 的骨架分配按
            ``(not required, -rating, poi_id)`` 排序，而补入的 Poi 热度恒为 0，
            不标 required 就会排在所有种子 POI 之后，被节奏容量一挤就丢 ——
            用户明确点名要去的地方就这么消失了（实测踩过）。

    Returns:
        Poi（source="tencent:lbs"）；解析不到或超出安全半径时返回 None。
    """
    if not is_enabled() or not (name or "").strip():
        return None

    span = _lbs_span("travel_lbs_place_search", "LBS地点检索",
                     name=name.strip(), city=city)
    hits = api.place_search(name.strip(), region=city or None, page_size=5)
    if not hits:
        _end_lbs_span(span, status="error", reason="no_hits", name=name)
        logger.info("[TravelLiveMap] 腾讯未检索到地点: %s（城市=%s）", name, city)
        return None
    _end_lbs_span(span, status="success", hits=len(hits))

    center = _city_center(city) if city else None
    for hit in hits:
        lat, lng = hit.get("lat"), hit.get("lng")
        if not lat or not lng:
            continue
        # 同城校验（验收 #83）：region 检索是偏好限定不是硬过滤，同名异地
        # POI（「中山公园」全国几十个）可能穿透。hit 自带真实归属（ad_info），
        # 与目标城市对不上直接丢弃——异地候选不入池（安全半径只挡距离，
        # 挡不住省界附近的异地同名）。
        if _same_city(hit.get("city", ""), city) is False:
            logger.info(
                "[TravelLiveMap] 解析结果「%s」归属 %s，与目标城市 %s 不符，丢弃",
                hit["name"], hit.get("city"), city,
            )
            continue
        if center is not None:
            gap = _straight_km(center, (lat, lng))
            if gap > _MAX_RESOLVE_DISTANCE_KM:
                logger.info(
                    "[TravelLiveMap] 解析结果「%s」距 %s 中心 %.0fkm，超出 %.0fkm 安全半径，丢弃",
                    hit["name"], city, gap, _MAX_RESOLVE_DISTANCE_KM,
                )
                continue
        # 城市键要与 TravelBrief.destination 对齐：能归一就用归一结果
        from backend.tools.travel.poi import resolve_city

        city_key = resolve_city(city) or city or hit.get("city") or ""
        poi = Poi(
            # 用腾讯 POI id 构造稳定标识，跨会话可复现；腾讯缺 id 时以
            # sha1(name|city) 兜底——不能用 hash()：Python 字符串 hash 带
            # 进程级盐（PYTHONHASHSEED），同地点跨进程会得到不同 poi_id，
            # 违反「poi_id 必须稳定」契约（STOP I1 实测修复）
            poi_id=f"lbs_{hit['id'] or stable_fallback_id(name, city_key)}",
            name=hit["name"],
            city=city_key,
            category=map_category(hit.get("category", "")),
            lat=float(lat),
            lng=float(lng),
            # 营业时段腾讯检索不提供，沿用契约默认值；
            # source 已如实标注，行程单会显示来源，不会伪装成核实事实。
            open_time="09:00",
            close_time="17:00",
            suggested_minutes=120,
            ticket_cny=0.0,
            tags=[],
            rating=0.0,
            required=required,
            source=SOURCE_LBS,
        )
        # Phase 1 时效标注（providers/travel/facts）：占位营业时间/票价在唯一
        # 解析出口统一打 unverified —— 任何调用方（resolve_missing_places、
        # TencentPOIProvider、未来新路径）拿到的都是带标事实，「未核实」
        # 沿状态链路走到行程单，不允许中途被当成核实事实消费。
        from backend.providers.travel.facts import UNVERIFIED, now_iso

        poi.verification_status = UNVERIFIED
        poi.observed_at = now_iso()
        return poi
    return None


def resolve_missing_places(
    city: str, candidates: Iterable[Poi], wanted_names: Iterable[str],
) -> tuple[list[Poi], list[str]]:
    """把候选池里缺失的点名地点，用腾讯 POI 库补全。

    Args:
        city: 目的地城市
        candidates: 当前候选池（用于判断「已存在」）
        wanted_names: 用户点名要去的名称列表（brief.must_go）

    Returns:
        (新增的 Poi 列表, 说明文案列表)
    """
    existing = list(candidates)
    added: list[Poi] = []
    notes: list[str] = []

    for raw in wanted_names:
        name = (raw or "").strip()
        if not name:
            continue
        if any(name in p.name or p.name in name for p in existing + added):
            continue  # 候选池里已有，无需补
        # required=True：这些名字全部来自 brief.must_go，用户已明确点名要去的
        poi = resolve_place(name, city, required=True)
        if poi is None:
            continue
        added.append(poi)
        notes.append(
            f"「{poi.name}」不在本地候选数据中，已通过腾讯位置服务解析其真实坐标后补入"
            f"（类别「{poi.category}」；营业时间与票价未核实，请出行前确认）"
        )

    if added:
        logger.info("[TravelLiveMap] 为 %s 补入 %d 个地点: %s",
                    city, len(added), [p.name for p in added])
    return added, notes
