"""travel/commerce/service.py — Commerce 编排服务（STOP K2-K6 核心）

流程（K0 §7 定稿，LLM 零参与事实链）：

    request（已确定性校验） → Provider factory（mode 三态）
      → adapter（Provider 层执行流：cache/quota/timeout/遥测）
      → ProviderResult[list[Record]]
      → normalize fail-closed 门（脏数据拒收 → INVALID_RESPONSE）
      → 确定性排序 → 截断披露 → CommerceResult（reporter 渲染的唯一输入）

**结局语义矩阵见 K0 §9**（实现与测试的唯一口径）：
  全部记录被拒 ≠ 空结果（前者 INVALID_RESPONSE，后者 SUCCESS+[]）；
  非 SUCCESS 结局不产生任何 offer（失败结构性不可能映射为 sold_out）。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date

from backend.providers.travel.live.commerce_contracts import (
    FlightOfferRecord,
    HotelOfferRecord,
)
from backend.providers.travel.live.result import Freshness, ProviderResult, ProviderStatus
from backend.travel.commerce import telemetry
from backend.travel.commerce.normalize import (
    normalize_flight_offers,
    normalize_hotel_offers,
)
from backend.travel.commerce.ranking import sort_flights, sort_hotels
from backend.travel.commerce.request import FlightSearchRequest, HotelSearchRequest

# 允许对外呈现的 provider 结果状态（渲染话术映射在 reporter）
_PRESENTABLE_FAILURE = {
    ProviderStatus.NOT_FOUND: "not_found",
    ProviderStatus.TIMEOUT: "timeout",
    ProviderStatus.UNAVAILABLE: "unavailable",
    ProviderStatus.RATE_LIMITED: "rate_limited",
    ProviderStatus.INVALID_RESPONSE: "invalid_response",
    ProviderStatus.UNAUTHORIZED: "unauthorized",
    ProviderStatus.DISABLED: "disabled",
}


@dataclass
class CommerceResult:
    """一次商务搜索的完整结局（reporter 的唯一输入；可 JSON 序列化前经
    model_dump——本对象仅进程内传递，不进 LangGraph state 原样存储）。"""

    commerce_type: str                    # hotel | flight
    status: str                           # success | empty | <PRESENTABLE_FAILURE 值>
    offers: list = field(default_factory=list)
    provider: str = ""
    freshness: Freshness = Freshness.UNKNOWN
    latency_ms: int = 0
    reason: str = ""                      # 机器可读原因（rejected 计数等）
    truncated: int = 0                    # 超 MAX_OFFERS 截断的条数（披露用）
    rejects: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status in ("success", "empty")


def _mode_reason(mode: str) -> str:
    if mode == "live":
        return ("真实 Hotel/Flight 供应商尚未接入（无凭据/适配器），"
                "已如实停止查询——不提供未经核实的库存信息")
    return "实时商务查询未启用"


def _resolve_hotel_provider():
    """mode → Hotel Provider 实例；不可用返回 (None, reason)。"""
    from backend.config.travel_commerce import provider_mode

    mode = provider_mode()
    if mode == "fake":
        from backend.providers.travel.live.fake_commerce import (
            FakeHotelSearchProvider,
        )
        return FakeHotelSearchProvider(), ""
    return None, _mode_reason(mode)


def _resolve_flight_provider():
    from backend.config.travel_commerce import provider_mode

    mode = provider_mode()
    if mode == "fake":
        from backend.providers.travel.live.fake_commerce import (
            FakeFlightSearchProvider,
        )
        return FakeFlightSearchProvider(), ""
    return None, _mode_reason(mode)


def search_hotels(request: HotelSearchRequest) -> CommerceResult:
    """酒店搜索（§七）。provider 缺失 → disabled（不伪造）。"""
    provider, reason = _resolve_hotel_provider()
    if provider is None:
        telemetry.record_request("hotel", "-", "disabled", 0)
        telemetry.record_fallback("hotel", "-", "disabled")
        return CommerceResult(commerce_type="hotel", status="disabled",
                              provider="-", reason=reason)

    t0 = time.monotonic()
    result = provider.search_hotels(
        request.city, request.check_in, request.check_out,
        adults=request.adults, children=request.children,
        rooms=request.rooms, star_rating=request.star_rating,
    )
    return _finalize(result, commerce_type="hotel",
                     normalizer=_normalize_hotel_factory(request),
                     sorter=sort_hotels)


def search_flights(request: FlightSearchRequest) -> CommerceResult:
    """机票搜索（§八，one-way 必持；round-trip 走 Provider 支持声明）。"""
    provider, reason = _resolve_flight_provider()
    if provider is None:
        telemetry.record_request("flight", "-", "disabled", 0)
        telemetry.record_fallback("flight", "-", "disabled")
        return CommerceResult(commerce_type="flight", status="disabled",
                              provider="-", reason=reason)

    t0 = time.monotonic()
    result = provider.search_flights(
        request.origin, request.destination, request.departure_date,
        return_date=request.return_date, adults=request.adults,
        children=request.children, cabin=request.cabin,
    )
    return _finalize(result, commerce_type="flight",
                     normalizer=_normalize_flight_factory(request),
                     sorter=sort_flights)


def _normalize_hotel_factory(request: HotelSearchRequest):
    def _run(records, observed_at, freshness, hosts):
        return normalize_hotel_offers(
            records, request_check_in=request.check_in,
            request_check_out=request.check_out,
            request_adults=request.adults, request_children=request.children,
            request_rooms=request.rooms, observed_at=observed_at,
            freshness=freshness, allowed_hosts=hosts,
        )
    return _run


def _normalize_flight_factory(request: FlightSearchRequest):
    def _run(records, observed_at, freshness, hosts):
        return normalize_flight_offers(
            records, departure_date=request.departure_date,
            return_date=request.return_date, observed_at=observed_at,
            freshness=freshness, allowed_hosts=hosts,
        )
    return _run


def _finalize(result: ProviderResult, *, commerce_type: str,
              normalizer, sorter) -> CommerceResult:
    """ProviderResult → CommerceResult（语义矩阵落地点，K0 §9 逐行）。"""
    latency = result.latency_ms
    provider = result.provider or "-"

    if not result.ok:
        status = _PRESENTABLE_FAILURE.get(result.status, "unavailable")
        telemetry.record_request(commerce_type, provider, status, latency)
        if result.freshness == Freshness.STALE:
            telemetry.record_fallback(commerce_type, provider, "stale")
        telemetry.event("travel.commerce.request", commerce_type=commerce_type,
                        provider=provider, status=status,
                        underlying=result.status.value)
        return CommerceResult(
            commerce_type=commerce_type, status=status, provider=provider,
            freshness=result.freshness, latency_ms=latency,
            reason=result.error,
        )

    records = result.data or []
    observed_at = result.observed_at
    hosts = _deeplink_hosts()
    outcome = normalizer(records, observed_at, result.freshness, hosts)

    for key, n in outcome.deeplink_stats.items():
        if n:
            telemetry.record_deeplink(commerce_type, key, n)

    # 全部记录被拒 = INVALID_RESPONSE（供应商给了非法响应，不是「无结果」）
    if records and not outcome.valids:
        telemetry.record_request(commerce_type, provider,
                                 "invalid_response", latency)
        telemetry.record_rejects(commerce_type, provider, len(outcome.rejects))
        telemetry.event("travel.commerce.all_rejected",
                        commerce_type=commerce_type, provider=provider,
                        reject_count=len(outcome.rejects))
        return CommerceResult(
            commerce_type=commerce_type, status="invalid_response",
            provider=provider, freshness=result.freshness,
            latency_ms=latency, reason="all offers rejected by schema gate",
            rejects=outcome.rejects,
        )

    if not outcome.valids:
        # SUCCESS + []（records 为空）：正常「无搜索结果」
        telemetry.record_request(commerce_type, provider, "empty", latency)
        telemetry.record_empty(commerce_type, provider, "empty")
        return CommerceResult(
            commerce_type=commerce_type, status="empty", provider=provider,
            freshness=result.freshness, latency_ms=latency,
        )

    valids = sorter(outcome.valids)
    max_offers = _max_offers()
    truncated = max(0, len(valids) - max_offers)
    valids = valids[:max_offers]

    telemetry.record_request(commerce_type, provider, "success", latency)
    telemetry.record_offers(commerce_type, provider, len(valids))
    if outcome.rejects:
        telemetry.record_rejects(commerce_type, provider, len(outcome.rejects))
    telemetry.record_snapshots(commerce_type, len(valids))
    telemetry.event("travel.commerce.request", commerce_type=commerce_type,
                    provider=provider, status="success",
                    offer_count=len(valids), freshness=result.freshness.value)

    return CommerceResult(
        commerce_type=commerce_type,
        status="success",
        offers=valids,
        provider=provider,
        freshness=result.freshness,
        latency_ms=latency,
        truncated=truncated,
        rejects=outcome.rejects,
    )


def _deeplink_hosts() -> tuple[str, ...]:
    from backend.config.travel_commerce import (
        TRAVEL_COMMERCE_DEEPLINK_ALLOWED_HOSTS,
    )

    return TRAVEL_COMMERCE_DEEPLINK_ALLOWED_HOSTS


def _max_offers() -> int:
    from backend.config.travel_commerce import TRAVEL_COMMERCE_MAX_OFFERS

    return max(1, TRAVEL_COMMERCE_MAX_OFFERS)
