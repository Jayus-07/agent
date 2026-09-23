"""providers/travel/live/tencent.py — 腾讯 LBS 适配器（STOP J4/J5）

四条纪律：
1. **复用既有基础设施**：底层走 ``tools.travel.live_map`` 与 ``infra.lbs.api``
   （超时/熔断/节流/状态码语义都在那里），本层只加共享缓存、timeout budget、
   输入输出校验、quota 软预算与遥测 —— 不改 infra 层其他消费方（maps 路由等）。
2. **脏数据不穿透（§61/G11）**：坐标必须落在 GCJ-02 中国范围
   （复用 infra.lbs.geo 的 LAT/LNG_RANGE）；名称非空、provider id 必须存在；
   keyword 长度钳制 ≤50（§101）。校验失败 = INVALID_RESPONSE，绝不接纳。
3. **fallback 不跨语义（§59）**：place 失败 → NOT_FOUND/UNAVAILABLE（上层
   走 unresolved 披露）；route 失败 → estimate 语义（is_estimate=true）；
   stale-if-error 只在失败路径取旧缓存并保留原 observed_at（观测时间如实）。
4. **quota 软预算（§54/§55）**：BudgetExhausted → 直接走缓存/降级 +
   quota_exhausted 事件，不硬撞第三方日上限。

业务消费面（POIProvider/TransitProvider 的 Poi/同构 dict 返回）不变：
Poi 构造与 unverified 标注仍由 live_map.resolve_place 唯一出口完成。
"""
from __future__ import annotations

import time
from datetime import date

from backend.providers.travel.live import cache as pcache
from backend.providers.travel.live import quota, telemetry
from backend.providers.travel.live.errors import status_from_lbs_error
from backend.providers.travel.live.resilience import (
    call_with_budget,
    resolve_budget,
    single_flight,
)
from backend.providers.travel.live.result import (
    Freshness,
    ProviderResult,
    ProviderStatus,
    failure,
    success,
)

# keyword 长度上限（§101：用户可控 query 必须 encode + length limit；
# httpx params 已 encode，这里补长度钳制）
_MAX_KEYWORD_LEN = 50


def _poi_record(poi) -> tuple:
    """Poi → (PlaceRecord 等价 tuple 形态)。

    返回 tuple 而非 contracts.PlaceRecord：避免循环依赖（contracts 不被
    本模块 import 的 live_map 依赖链反向引用）。字段顺序与 PlaceRecord 对齐。
    """
    from backend.providers.travel.live.contracts import PlaceRecord

    raw_id = poi.poi_id[4:] if poi.poi_id.startswith("lbs_") else poi.poi_id
    return PlaceRecord(
        provider_id=raw_id, name=poi.name, category=poi.category,
        address="", city=poi.city, lat=poi.lat, lng=poi.lng,
    )


def _validate_place(poi) -> str:
    """POI 输出校验（§61/G11）：返回空串=通过，否则为拒绝原因。"""
    from backend.infra.lbs.geo import LAT_RANGE, LNG_RANGE

    if not (poi.name or "").strip():
        return "empty name"
    if not poi.poi_id or poi.poi_id == "lbs_":
        return "missing provider id"
    if not (LAT_RANGE[0] <= poi.lat <= LAT_RANGE[1]):
        return f"lat out of range: {poi.lat}"
    if not (LNG_RANGE[0] <= poi.lng <= LNG_RANGE[1]):
        return f"lng out of range: {poi.lng}"
    return ""


class TencentPlaceProvider:
    """maps.place_resolve —— must_go 点名解析（缓存+校验+预算+遥测）。"""

    name = "tencent:lbs"
    operation = "place"

    def __init__(self) -> None:
        from backend.tools.travel import live_map

        self._live_map = live_map

    def is_enabled(self) -> bool:
        return self._live_map.is_enabled()

    def resolve_place(self, name: str, city: str):
        """解析地点 → ProviderResult（data 为 PlaceRecord）。

        既有调用方（poi 专家经 resolve_missing_places 通道）拿 Poi 的路径
        见 :meth:`resolve_poi`——同一校验与缓存，data 为 Poi。
        """
        result = self.resolve_poi(name, city)
        if result.ok:
            return success(_poi_record(result.data), provider=self.name,
                           operation=self.operation,
                           source_id=result.source_id,
                           latency_ms=result.latency_ms)
        return result

    def resolve_poi(self, name: str, city: str, *, required: bool = False):
        """Poi 形态解析（业务消费面复用；校验/缓存/遥测同一条路径）。

        required 是**调用语境**（must_go 点名），不进缓存键——缓存只存事实；
        命中缓存后按本次调用补标。
        """
        from backend.providers.travel.live.result import ProviderStatus

        clean_name = (name or "").strip()[:_MAX_KEYWORD_LEN]
        key = pcache.build_key("place", clean_name.lower(), (city or "").strip())

        if not self.is_enabled():
            telemetry.record_fallback(self.name, "disabled")
            return failure(ProviderStatus.DISABLED, provider=self.name,
                           operation=self.operation, error="live map 未启用")

        # 1) 共享缓存：hit / negative 直接返回
        env, cache_status = pcache.cache_get(key)
        telemetry.record_cache(self.name, pcache.cache_hit_status(env, cache_status))
        if cache_status == "hit":
            poi = _poi_from_cached(env.data, required=required)
            if poi is None:
                # 缓存内容损坏按 miss 处理（不降级成假数据）
                telemetry.record_cache(self.name, "miss")
            else:
                telemetry.event("travel.provider.request", provider=self.name,
                                operation=self.operation, status="success",
                                cache="hit")
                return success(poi, provider=self.name,
                               operation=self.operation, source_id=poi.poi_id,
                               latency_ms=0).with_freshness(Freshness.CACHED,
                                                            env.observed_at)
        if cache_status == "negative":
            return failure(ProviderStatus.NOT_FOUND, provider=self.name,
                           operation=self.operation,
                           error="negative cache（近期确认无此地点）")

        # 2) quota 软预算
        try:
            quota.check_and_consume(self.name)
        except quota.BudgetExhausted as e:
            telemetry.record_quota(self.name)
            telemetry.event("travel.provider.quota_exhausted", provider=self.name)
            return self._fallback_stale_or(
                key, ProviderStatus.RATE_LIMITED, str(e), required=required)

        # 3) single-flight + budget 调底层
        def _load():
            return call_with_budget(
                self.operation,
                lambda: self._live_map.resolve_place(clean_name, city),
            )

        t0 = time.monotonic()
        try:
            poi, elected = single_flight(key, _load)
        except TimeoutError:
            latency = int((time.monotonic() - t0) * 1000)
            telemetry.record_request(self.name, self.operation, "timeout", latency)
            telemetry.event("travel.provider.timeout", provider=self.name)
            return self._fallback_stale_or(
                key, ProviderStatus.TIMEOUT,
                f"超出 {resolve_budget(self.operation)}s 预算", required=required)
        except Exception as e:  # noqa: BLE001 — 统一分类
            latency = int((time.monotonic() - t0) * 1000)
            status = status_from_lbs_error(e)
            telemetry.record_request(self.name, self.operation, status.value, latency)
            if status == ProviderStatus.NOT_FOUND:
                pcache.cache_put_not_found(key, provider=self.name,
                                           operation=self.operation,
                                           observed_at=result_observed_at())
                telemetry.event("travel.provider.request", provider=self.name,
                                operation=self.operation, status="not_found")
                return failure(status, provider=self.name,
                               operation=self.operation, error=str(e),
                               latency_ms=latency)
            return self._fallback_stale_or(key, status, str(e), latency_ms=latency,
                                           required=required)

        latency = int((time.monotonic() - t0) * 1000)

        # 4) 输出校验：脏数据不穿透
        if poi is not None:
            reject = _validate_place(poi)
            if reject:
                telemetry.record_request(self.name, self.operation,
                                         "invalid_response", latency)
                telemetry.event("travel.provider.invalid_response",
                                provider=self.name, reason=reject)
                return failure(ProviderStatus.INVALID_RESPONSE,
                               provider=self.name, operation=self.operation,
                               error=reject, latency_ms=latency)

        if poi is None:
            # 底层检索无结果（safe_call 吞错返 None 与真无结果在此合流：
            # live_map.resolve_place 把「检索失败」与「无命中」都归 None——
            # NOT_FOUND 缓存只对「确认无结果」有意义，这里按调用成功但无数据
            # 处理并 negative cache（60s 自愈，误伤窗口极小）。
            pcache.cache_put_not_found(key, provider=self.name,
                                       operation=self.operation,
                                       observed_at=result_observed_at())
            telemetry.record_request(self.name, self.operation, "not_found", latency)
            return failure(ProviderStatus.NOT_FOUND, provider=self.name,
                           operation=self.operation, latency_ms=latency)

        # 缓存只存事实（JSON-safe dict）：required 是调用语境不进缓存；
        # 坐上 required 后返回
        poi.required = poi.required or required
        pcache.cache_put_success(
            key, data=poi.model_dump(), provider=self.name,
            operation=self.operation, observed_at=result_observed_at(),
            source_id=poi.poi_id,
        )
        telemetry.record_request(self.name, self.operation, "success", latency)
        telemetry.event("travel.provider.success", provider=self.name,
                        operation=self.operation, latency_ms=latency)
        return success(poi, provider=self.name, operation=self.operation,
                       source_id=poi.poi_id, latency_ms=latency)

    def _fallback_stale_or(self, key: str, status: ProviderStatus, error: str,
                           latency_ms: int = 0, *, required: bool = False):
        """失败路径：stale-if-error（§32）——只在此处允许用旧数据。"""
        stale = pcache.cache_get_stale(key)
        if stale is not None:
            poi = _poi_from_cached(stale.data, required=required)
            if poi is not None:
                telemetry.record_stale(self.name)
                telemetry.record_fallback(self.name, "stale")
                telemetry.event("travel.provider.fallback", provider=self.name,
                                fallback="stale", underlying=status.value)
                return success(poi, provider=self.name,
                               operation=self.operation,
                               source_id=poi.poi_id,
                               latency_ms=latency_ms).with_freshness(
                    Freshness.STALE, stale.observed_at)
        telemetry.record_request(self.name, self.operation, status.value, latency_ms)
        telemetry.record_fallback(self.name, "unresolved")
        telemetry.event("travel.provider.fallback", provider=self.name,
                        fallback="unresolved", underlying=status.value)
        return failure(status, provider=self.name, operation=self.operation,
                       error=error, latency_ms=latency_ms)


def result_observed_at() -> str:
    from backend.providers.travel.facts import now_iso

    return now_iso()


def resolve_missing_places(
    city: str, candidates, wanted_names,
) -> tuple[list, list[str]]:
    """must_go 缺失地点的批量解析（STOP J4：poi 专家走本通道）。

    与 ``live_map.resolve_missing_places`` 同签名同语义（Poi 构造与
    unverified 标注仍由 live_map.resolve_place 唯一出口完成），但每次解析
    经 Provider 层：共享缓存 / 3s 预算 / 坐标与 id 校验 / quota 软预算 /
    遥测。失败降级按 fallback matrix：NOT_FOUND → 未解析（不伪造）；
    TIMEOUT/UNAVAILABLE/RATE_LIMITED → 提示服务暂不可用（不伪造）。
    """
    provider = TencentPlaceProvider()
    existing = list(candidates)
    added: list = []
    notes: list[str] = []

    from backend.providers.travel.live.result import ProviderStatus

    for raw in wanted_names:
        name = (raw or "").strip()
        if not name:
            continue
        if any(name in p.name or p.name in name for p in existing + added):
            continue  # 候选池里已有，无需补
        result = provider.resolve_poi(name, city, required=True)
        if result.ok and result.data is not None:
            poi = result.data
            added.append(poi)
            stale_mark = "（缓存数据）" if result.freshness in (
                Freshness.CACHED, Freshness.STALE) else ""
            notes.append(
                f"「{poi.name}」不在本地候选数据中，已通过腾讯位置服务解析其真实坐标后补入{stale_mark}"
                f"（类别「{poi.category}」；营业时间与票价未核实，请出行前确认）"
            )
        elif result.status == ProviderStatus.NOT_FOUND:
            # 确认无此地点：不伪造，留给 skeleton 的「未匹配到」披露
            continue
        else:
            notes.append(
                f"地点数据服务暂时不可用，未能解析「{name}」；"
                "可稍后重试或出行前自行确认"
            )

    if added:
        from backend.shared.logger import logger

        logger.info("[TravelPlaceProvider] 为 %s 补入 %d 个地点: %s",
                    city, len(added), [p.name for p in added])
    return added, notes


def _poi_from_cached(data, *, required: bool = False):
    """缓存内容 → Poi（损坏按 None 处理，绝不降级成假数据）。"""
    try:
        from backend.travel.models.poi import Poi

        poi = Poi.model_validate(data)
        poi.required = poi.required or required
        return poi
    except Exception:  # noqa: BLE001 — 反序列化失败按 miss 处理
        return None


class TencentRoutingProvider:
    """maps.route —— 段间路线（共享缓存 + 预算 + 遥测）。

    fallback 到本地估算仍由既有 ``estimate_leg``/``TencentTransitProvider``
    承担（is_estimate=true 语义冻结）；本 Provider 只负责 live 通道与缓存。
    """

    name = "tencent:lbs"
    operation = "route"

    def __init__(self) -> None:
        from backend.tools.travel import live_map

        self._live_map = live_map

    def is_enabled(self) -> bool:
        return self._live_map.is_enabled()

    def route(self, from_lat: float, from_lng: float,
              to_lat: float, to_lng: float, *, mode: str = "driving",
              trip_date: date | None = None):
        """live 路线 → ProviderResult（data=RouteRecord）；失败由上层估算兜底。

        缓存键含起终点（4 位小数归一）+mode（§29/§30）。trip_date 的远期
        强制本地估算语义在 TransitProvider 层（冻结面），此处不重复。
        """
        from backend.providers.travel.live.contracts import RouteRecord
        from backend.providers.travel.live.result import ProviderStatus

        key = pcache.build_key("route", float(from_lat), float(from_lng),
                               float(to_lat), float(to_lng), mode)

        if not self.is_enabled():
            return failure(ProviderStatus.DISABLED, provider=self.name,
                           operation=self.operation, error="live map 未启用")

        env, cache_status = pcache.cache_get(key)
        telemetry.record_cache(self.name, pcache.cache_hit_status(env, cache_status))
        if cache_status == "hit":
            rec = RouteRecord(
                distance_m=int(env.data.get("distance_m", 0)),
                duration_min=float(env.data.get("duration_min", 0.0)),
                mode=mode,
                taxi_fare_cny=env.data.get("taxi_fare_cny"),
                traffic_aware=bool(env.data.get("traffic_aware")),
            )
            return success(rec, provider=self.name, operation=self.operation,
                           latency_ms=0).with_freshness(Freshness.CACHED,
                                                        env.observed_at)

        try:
            quota.check_and_consume(self.name)
        except quota.BudgetExhausted as e:
            telemetry.record_quota(self.name)
            telemetry.event("travel.provider.quota_exhausted", provider=self.name)
            return failure(ProviderStatus.RATE_LIMITED, provider=self.name,
                           operation=self.operation, error=str(e))

        def _load():
            return call_with_budget(
                self.operation,
                lambda: self._live_map.live_leg(from_lat, from_lng,
                                                to_lat, to_lng),
            )

        t0 = time.monotonic()
        try:
            leg, _elected = single_flight(key, _load)
        except TimeoutError:
            latency = int((time.monotonic() - t0) * 1000)
            telemetry.record_request(self.name, self.operation, "timeout", latency)
            telemetry.event("travel.provider.timeout", provider=self.name)
            return failure(ProviderStatus.TIMEOUT, provider=self.name,
                           operation=self.operation, latency_ms=latency)
        except Exception as e:  # noqa: BLE001 — 统一分类
            latency = int((time.monotonic() - t0) * 1000)
            status = status_from_lbs_error(e)
            telemetry.record_request(self.name, self.operation, status.value, latency)
            return failure(status, provider=self.name, operation=self.operation,
                           error=str(e), latency_ms=latency)

        latency = int((time.monotonic() - t0) * 1000)
        if not leg or not leg.get("distance_km"):
            telemetry.record_request(self.name, self.operation, "not_found", latency)
            return failure(ProviderStatus.NOT_FOUND, provider=self.name,
                           operation=self.operation, latency_ms=latency)

        payload = {
            "distance_m": int(round(float(leg["distance_km"]) * 1000)),
            "duration_min": float(leg.get("minutes", 0)),
            "mode": leg.get("mode", "drive"),
            "taxi_fare_cny": leg.get("cost_cny"),
            "traffic_aware": True,
        }
        # 路线校验：负距离/负时长不接纳（§61）
        if payload["distance_m"] <= 0 or payload["duration_min"] <= 0:
            telemetry.record_request(self.name, self.operation,
                                     "invalid_response", latency)
            telemetry.event("travel.provider.invalid_response",
                            provider=self.name)
            return failure(ProviderStatus.INVALID_RESPONSE, provider=self.name,
                           operation=self.operation, latency_ms=latency)

        pcache.cache_put_success(
            key, data=payload, provider=self.name, operation=self.operation,
            observed_at=result_observed_at(),
        )
        telemetry.record_request(self.name, self.operation, "success", latency)
        return success(RouteRecord(**payload), provider=self.name,
                       operation=self.operation, latency_ms=latency)


class TencentWeatherProvider:
    """weather.forecast —— 未来预报（6s 预算接线 + 共享缓存 + 视野元数据）。"""

    name = "tencent:lbs"
    operation = "weather"

    def __init__(self) -> None:
        from backend.infra.lbs import api

        self._api = api

    def is_enabled(self) -> bool:
        from backend.config.travel import TRAVEL_WEATHER_ENABLED

        return bool(TRAVEL_WEATHER_ENABLED)

    def forecast_payload(self, city: str) -> ProviderResult[dict]:
        """future 预报 → ProviderResult[data=api.weather_for_city 同构 dict]。

        dict 形态让 weather 专家的坏天气日判定零改动（冻结面）；horizon =
        len(days) 由调用方与行程日期求交判定 OUT_OF_HORIZON。
        """
        from backend.providers.travel.live.result import ProviderStatus

        key = pcache.build_key("weather", (city or "").strip())

        if not self.is_enabled():
            return failure(ProviderStatus.DISABLED, provider=self.name,
                           operation=self.operation, error="天气检查已关闭")

        env, cache_status = pcache.cache_get(key)
        telemetry.record_cache(self.name, pcache.cache_hit_status(env, cache_status))
        if cache_status == "hit":
            return success(env.data, provider=self.name,
                           operation=self.operation,
                           latency_ms=0).with_freshness(Freshness.CACHED,
                                                        env.observed_at)

        try:
            quota.check_and_consume(self.name)
        except quota.BudgetExhausted as e:
            telemetry.record_quota(self.name)
            telemetry.event("travel.provider.quota_exhausted", provider=self.name)
            return self._stale_or(key, ProviderStatus.RATE_LIMITED, str(e))

        t0 = time.monotonic()
        try:
            payload = call_with_budget(
                self.operation,
                lambda: self._api.weather_for_city(city, kind="future"),
            )
        except TimeoutError:
            latency = int((time.monotonic() - t0) * 1000)
            telemetry.record_request(self.name, self.operation, "timeout", latency)
            telemetry.event("travel.provider.timeout", provider=self.name)
            return self._stale_or(key, ProviderStatus.TIMEOUT,
                                  "weather budget exceeded", latency)
        except Exception as e:  # noqa: BLE001 — 统一分类
            latency = int((time.monotonic() - t0) * 1000)
            status = status_from_lbs_error(e)
            telemetry.record_request(self.name, self.operation, status.value, latency)
            return self._stale_or(key, status, str(e), latency)

        latency = int((time.monotonic() - t0) * 1000)
        if not payload or not (payload.get("days") or []):
            telemetry.record_request(self.name, self.operation, "not_found", latency)
            return self._stale_or(key, ProviderStatus.NOT_FOUND,
                                  "空预报", latency)

        pcache.cache_put_success(
            key, data=payload, provider=self.name, operation=self.operation,
            observed_at=result_observed_at(),
        )
        telemetry.record_request(self.name, self.operation, "success", latency)
        telemetry.event("travel.provider.success", provider=self.name,
                        operation=self.operation, latency_ms=latency)
        return success(payload, provider=self.name, operation=self.operation,
                       latency_ms=latency)

    def _stale_or(self, key: str, status: ProviderStatus, error: str,
                  latency_ms: int = 0) -> ProviderResult[dict]:
        stale = pcache.cache_get_stale(key)
        if stale is not None:
            telemetry.record_stale(self.name)
            telemetry.record_fallback(self.name, "stale")
            telemetry.event("travel.provider.fallback", provider=self.name,
                            fallback="stale", underlying=status.value)
            return success(stale.data, provider=self.name,
                           operation=self.operation,
                           latency_ms=latency_ms).with_freshness(
                Freshness.STALE, stale.observed_at)
        telemetry.record_request(self.name, self.operation, status.value, latency_ms)
        telemetry.record_fallback(self.name, "unavailable")
        telemetry.event("travel.provider.fallback", provider=self.name,
                        fallback="unavailable", underlying=status.value)
        return failure(status, provider=self.name, operation=self.operation,
                       error=error, latency_ms=latency_ms)
