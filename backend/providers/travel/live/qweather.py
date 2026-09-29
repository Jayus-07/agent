"""providers/travel/live/qweather.py — 和风天气适配器（腾讯天气的备用源）

定位（2026-09-28）：**备用源**。配置 ``QWEATHER_API_KEY`` 后，
``get_weather_provider()`` 会把主源 ``TencentWeatherProvider`` 包进
FallbackWeatherProvider——主源失败（超时/限流/鉴权/城市未收录）才降到本适配器；
未配置 Key 时本模块零参与（行为与旧版一致）。

冻结面：``forecast_payload`` 返回 dict 与 ``infra.lbs.api.weather_for_city``
的 future 形状**同构**（``kind/province/city/district/adcode/update_time/
days[{date, week, day{...}, night{...}}]``，day/night 键名对齐
``_WEATHER_FIELDS`` 口径），weather 专家的坏天气日判定零改动。

与腾讯适配器同款纪律：
- cache / quota 软预算 / timeout budget / stale-if-error 全套复用，
  缓存键用独立前缀（缓存命中时的 provider 标注不串源）；
- 脏数据不穿透：城市解析不到、daily 列表为空 = NOT_FOUND / INVALID_RESPONSE，
  绝不把残缺 payload 当成功返回；
- 语义映射：HTTP 401/403 与 body code 401/403 → UNAUTHORIZED；
  402/429 → RATE_LIMITED；404 → NOT_FOUND；超时 → TIMEOUT；
  其余网络/解析失败 → UNAVAILABLE / INVALID_RESPONSE。
"""
from __future__ import annotations

import time
from typing import Any, Callable

import httpx

from backend.providers.travel.facts import now_iso
from backend.providers.travel.live import cache as pcache
from backend.providers.travel.live import quota, telemetry
from backend.providers.travel.live.resilience import call_with_budget
from backend.providers.travel.live.result import (
    Freshness,
    ProviderResult,
    ProviderStatus,
    failure,
    success,
)

# 和风 v7 body code → 结局分类（HTTP 状态码同表）
_CODE_STATUS: dict[str, ProviderStatus] = {
    "401": ProviderStatus.UNAUTHORIZED,
    "403": ProviderStatus.UNAUTHORIZED,
    "402": ProviderStatus.RATE_LIMITED,
    "429": ProviderStatus.RATE_LIMITED,
    "404": ProviderStatus.NOT_FOUND,
}

ForecastTransport = Callable[[str, dict[str, str]], dict[str, Any]]


class _QWeatherError(Exception):
    """携带 ProviderStatus 的适配器内异常（不越过 Provider 层）。"""

    def __init__(self, status: ProviderStatus, msg: str) -> None:
        super().__init__(msg)
        self.status = status


def _daily_info(raw: dict | None, *, is_day: bool) -> dict:
    """和风 daily 行 → 冻结口径的 day/night dict（_WEATHER_FIELDS 子集，
    只填拿得到的字段——缺的留空，绝不编造）。"""
    src = raw or {}
    info: dict[str, Any] = {
        "weather": (src.get("textDay") if is_day else src.get("textNight")) or "",
    }
    if src.get("tempMax") is not None and src.get("tempMin") is not None:
        info["temperature"] = src.get("tempMax") if is_day else src.get("tempMin")
    wind_dir = src.get("windDirDay") or ""
    wind_scale = src.get("windScaleDay") or ""
    if wind_dir:
        info["wind_direction"] = wind_dir
    if wind_scale:
        info["wind_power"] = f"{wind_scale}级"
    return info


class QWeatherProvider:
    """weather.forecast 备用源 —— 城市名 → GeoAPI → 7d 预报。"""

    name = "qweather"
    operation = "weather"

    def __init__(self, *, transport: ForecastTransport | None = None) -> None:
        # transport 注入点：签名 (url, params) -> body dict；测试用它替换网络
        self._transport = transport

    def is_enabled(self) -> bool:
        from backend.config.map import is_qweather_configured
        from backend.config.travel import TRAVEL_WEATHER_ENABLED

        return bool(TRAVEL_WEATHER_ENABLED and is_qweather_configured())

    def forecast_payload(self, city: str) -> ProviderResult[dict]:
        """future 预报 → 与 api.weather_for_city 同构的 ProviderResult[dict]。"""
        from backend.providers.travel.live.result import ProviderStatus

        key = pcache.build_key("qweather:weather", (city or "").strip())

        if not self.is_enabled():
            return failure(ProviderStatus.DISABLED, provider=self.name,
                           operation=self.operation, error="和风备用源未启用")

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
                lambda: self._forecast((city or "").strip()),
            )
        except TimeoutError:
            latency = int((time.monotonic() - t0) * 1000)
            telemetry.record_request(self.name, self.operation, "timeout", latency)
            telemetry.event("travel.provider.timeout", provider=self.name)
            return self._stale_or(key, ProviderStatus.TIMEOUT,
                                  "qweather budget exceeded", latency)
        except _QWeatherError as e:
            latency = int((time.monotonic() - t0) * 1000)
            return self._stale_or(key, e.status, str(e), latency)
        except Exception as e:  # noqa: BLE001 — 网络类异常统一降级
            latency = int((time.monotonic() - t0) * 1000)
            return self._stale_or(key, ProviderStatus.UNAVAILABLE, str(e), latency)

        latency = int((time.monotonic() - t0) * 1000)
        if not payload or not (payload.get("days") or []):
            telemetry.record_request(self.name, self.operation, "not_found", latency)
            return self._stale_or(key, ProviderStatus.NOT_FOUND,
                                  "空预报", latency)

        pcache.cache_put_success(
            key, data=payload, provider=self.name, operation=self.operation,
            observed_at=now_iso(),
        )
        telemetry.record_request(self.name, self.operation, "success", latency)
        telemetry.event("travel.provider.success", provider=self.name,
                        operation=self.operation, latency_ms=latency)
        return success(payload, provider=self.name, operation=self.operation,
                       latency_ms=latency)

    # ---------- 内部 ----------

    def _forecast(self, city: str) -> dict:
        """GeoAPI 城市定位 → 7d 预报 → 冻结 future 形状（同步，受 budget 管）。"""
        if not city:
            raise _QWeatherError(ProviderStatus.NOT_FOUND, "空城市名")
        loc = self._get("/geo/v2/city/lookup", {"location": city, "number": "1"})
        locations = loc.get("location") or []
        if not locations:
            raise _QWeatherError(ProviderStatus.NOT_FOUND,
                                 f"和风未收录城市: {city}")
        first = locations[0]
        daily = self._get("/v7/weather/7d", {"location": first.get("id", "")})
        rows = daily.get("daily") or []
        if not rows:
            raise _QWeatherError(ProviderStatus.INVALID_RESPONSE,
                                 "和风预报缺 daily 列表")
        return {
            "kind": "future",
            "province": "",
            "city": first.get("name", city),
            "district": "",
            "adcode": str(first.get("id", "")),
            "update_time": now_iso(),
            "days": [{
                "date": item.get("fxDate", ""),
                "week": item.get("week", ""),
                "day": _daily_info(item, is_day=True),
                "night": _daily_info(item, is_day=False),
            } for item in rows],
        }

    def _get(self, path: str, params: dict[str, str]) -> dict:
        """单次和风 GET（鉴权/状态码/body code 归一为 _QWeatherError）。"""
        from backend.config.map import QWEATHER_API_HOST, QWEATHER_API_KEY

        if self._transport is not None:
            return self._validate(self._transport(path, params))
        try:
            resp = httpx.get(
                f"https://{QWEATHER_API_HOST}{path}",
                params=params,
                headers={"X-QW-Api-Key": QWEATHER_API_KEY},
                timeout=6.0,
            )
        except httpx.TimeoutException as e:
            raise TimeoutError(str(e)) from e
        if resp.status_code in (401, 403, 402, 404, 429):
            status = _CODE_STATUS[str(resp.status_code)]
            raise _QWeatherError(status, f"和风 HTTP {resp.status_code}")
        if resp.status_code != 200:
            raise _QWeatherError(ProviderStatus.UNAVAILABLE,
                                 f"和风 HTTP {resp.status_code}")
        try:
            return self._validate(resp.json())
        except ValueError as e:
            raise _QWeatherError(ProviderStatus.INVALID_RESPONSE,
                                 f"和风响应非 JSON: {e}") from e

    @staticmethod
    def _validate(body: dict) -> dict:
        code = str(body.get("code", ""))
        if code == "200":
            return body
        status = _CODE_STATUS.get(code, ProviderStatus.INVALID_RESPONSE)
        raise _QWeatherError(status, f"和风 code={code}")

    def _stale_or(self, key: str, status: ProviderStatus, error: str,
                  latency_ms: int = 0) -> ProviderResult[dict]:
        """stale-if-error：失败路径优先回过期缓存（保留原 observed_at）。"""
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
