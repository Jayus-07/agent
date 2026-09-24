"""providers/travel/live/fake_commerce.py — Hotel/Flight **Fake** 适配器（STOP K2/K3）

**FAKE = 显式测试数据源，严禁作为生产验收**（任务书 §28）。三条纪律：

1. **身份可辨识**：name="fake:commerce"；每条 offer 的 source_id 带
   ``fake:`` 前缀，extra["fake"]=True；渲染层据此披露「测试数据」。
2. **走真执行流**：cache/quota/timeout/telemetry 全部经
   commerce_adapter_base（fake 的价值就是证明统一 Provider Layer，G2）。
3. **场景可注入**：scenario 字典驱动 H1-H12/F1-F12 全部语义场景
   （评测探针与 pytest 共用同一套注入口径，无第二打桩体系）。

scenario["type"] ∈ success | empty | not_found | timeout_fast | unavailable
                 | rate_limited | invalid_price | invalid_availability
                 | sold_out | tax_unknown | currency_jpy | deeplink_invalid
                 | connection（仅 flight：中转） | stale_seed（写一条已过期缓存）

数据本身确定性（同输入同输出），无随机——质量门可复现。
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime, timedelta

from backend.providers.travel.live.commerce_adapter_base import (
    CommerceSearchSignal,
    run_commerce_search,
)
from backend.providers.travel.live.commerce_contracts import (
    AVAIL_AVAILABLE,
    AVAIL_LIMITED,
    AVAIL_SOLD_OUT,
    AVAIL_UNKNOWN,
    FlightOfferRecord,
    FlightSegmentRecord,
    HotelOfferRecord,
    PriceSnapshotRecord,
    TAX_EXCLUDED,
    TAX_INCLUDED,
)
from backend.providers.travel.live.result import (
    ProviderResult,
    ProviderStatus,
)

PROVIDER_NAME = "fake:commerce"

# verified static mapping（任务书 §八：机场代码只能来自 Provider 或静态表）
_CITY_AIRPORT: dict[str, str] = {
    "大阪": "KIX", "东京": "HND", "京都": "KIX", "名古屋": "NGO",
    "福州": "FOC", "厦门": "XMN", "杭州": "HGH", "上海": "SHA",
    "北京": "PEK", "广州": "CAN", "深圳": "SZX", "成都": "CTU",
}
# 币种保真（H7/F8）：日本城市 → JPY，其余 CNY；**绝无隐式换算**
_CITY_CURRENCY: dict[str, str] = {
    "大阪": "JPY", "东京": "JPY", "京都": "JPY", "名古屋": "JPY",
}


def _fake_classifier(exc: Exception) -> ProviderStatus:
    if isinstance(exc, CommerceSearchSignal):
        return exc.status
    if isinstance(exc, TimeoutError):
        return ProviderStatus.TIMEOUT
    return ProviderStatus.UNAVAILABLE


def _scenario_type(scenario: dict | None) -> str:
    return (scenario or {}).get("type", "success")


def _hotels_for(city: str, check_in: str, check_out: str, *,
                adults: int, children: int, rooms: int,
                scenario: dict | None) -> list[HotelOfferRecord]:
    """确定性假酒店（同输入同输出）；价格字符串十进制，无 float。"""
    currency = _CITY_CURRENCY.get(city, "CNY")
    st = _scenario_type(scenario)

    def _record(idx: int, name: str, room: str, amount: str,
                availability: str = AVAIL_AVAILABLE, *,
                taxes: str | None = None, inclusion: str = "unknown",
                cancel: str | None = None, deeplink: str | None = None,
                meal: str | None = None) -> HotelOfferRecord:
        return HotelOfferRecord(
            provider=PROVIDER_NAME, property_id=f"fake-prop-{idx}",
            property_name=f"{name}（测试数据）", city=city,
            check_in=check_in, check_out=check_out,
            availability=availability,
            price=PriceSnapshotRecord(
                amount=amount, currency=currency, taxes=taxes,
                tax_inclusion=inclusion,
                provider_offer_id=f"fake-rate-{idx}",
            ),
            provider_offer_id=f"fake-rate-{idx}",
            room_type=room, adults=adults, children=children, rooms=rooms,
            cancellation_policy=cancel, meal_plan=meal,
            booking_deep_link=deeplink,
            source_id=f"fake:hotel:{idx}",
            extra={"fake": True},
        )

    base_deeplink = f"https://fake-commerce.example.com/hotel?city={city}&in={check_in}"
    if st == "empty":
        return []
    if st == "sold_out":
        return [_record(1, "梅田广场酒店", "标准双床房", "42000",
                        AVAIL_SOLD_OUT)]
    if st == "tax_unknown":
        return [_record(2, "难波花园酒店", "豪华大床房", "58000",
                        taxes=None, inclusion="unknown",
                        deeplink=base_deeplink + "&id=2")]
    if st == "invalid_price":
        # 脏数据：金额非数字（校验门必须拒绝，G10）
        return [_record(3, "心斋桥快捷酒店", "标准大床房", "n/a")]
    if st == "invalid_availability":
        return [_record(4, "天王寺商务酒店", "标准单人间", "36000",
                        availability="maybe")]
    if st == "invalid_price_negative":
        return [_record(5, "_negative", "房", "-100")]
    if st == "deeplink_invalid":
        return [_record(6, "白鹭洲观景酒店", "湖景大床房", "48000",
                        deeplink="javascript:alert(1)")]
    if st == "limited":
        return [_record(7, "锦江之星", "标准双床房", "329",
                        AVAIL_LIMITED, cancel="免费取消（入住前1天）",
                        deeplink=base_deeplink + "&id=7")]

    # success 及默认：两条不同 rate plan（指纹必须不同，§十五）
    return [
        _record(1, "梅田广场酒店", "标准双床房", "42000" if currency == "JPY" else "420",
                taxes="0" if st == "tax_included" else None,
                inclusion=TAX_INCLUDED if st == "tax_included" else "unknown",
                cancel="免费取消（入住前1天18点前）",
                deeplink=base_deeplink + "&id=1",
                meal="含早餐"),
        _record(2, "难波花园酒店", "豪华大床房", "58000" if currency == "JPY" else "580",
                taxes="5800" if currency == "JPY" else "58",
                inclusion=TAX_EXCLUDED,
                deeplink=base_deeplink + "&id=2",
                meal="不含早餐"),
    ]


def _hotel_record(d: dict) -> HotelOfferRecord:
    """缓存 dict → Record（嵌套 price 递归重建；asdict 是递归展开的）。"""
    if isinstance(d.get("price"), dict):
        d = {**d, "price": PriceSnapshotRecord(**d["price"])}
    return HotelOfferRecord(**d)


def _flight_record(d: dict) -> FlightOfferRecord:
    d = dict(d)
    d["segments"] = [
        s if isinstance(s, FlightSegmentRecord) else FlightSegmentRecord(**s)
        for s in d.get("segments", [])]
    if isinstance(d.get("price"), dict):
        d["price"] = PriceSnapshotRecord(**d["price"])
    return FlightOfferRecord(**d)


class FakeHotelSearchProvider:
    """酒店搜索 Fake 适配器（走完整 Provider 层执行流）。"""

    name = PROVIDER_NAME
    operation = "hotel_search"

    def __init__(self, scenario: dict | None = None):
        self._scenario = scenario or {}

    def is_enabled(self) -> bool:
        from backend.config.travel_commerce import provider_mode

        return provider_mode() == "fake"

    def search_hotels(
        self, city: str, check_in: date, check_out: date, *,
        adults: int = 2, children: int = 0, rooms: int = 1,
        star_rating: int | None = None,
    ) -> ProviderResult[list[HotelOfferRecord]]:
        from backend.providers.travel.live import cache as pcache

        city = (city or "").strip()
        ci, co = check_in.isoformat(), check_out.isoformat()
        key = pcache.build_key(self.operation, city.lower(), ci, co,
                               adults, children, rooms, star_rating or 0)
        scenario = self._scenario

        def _load() -> list[HotelOfferRecord]:
            st = _scenario_type(scenario)
            if st == "timeout_fast":
                raise TimeoutError("fake timeout")
            if st == "unavailable":
                raise CommerceSearchSignal(ProviderStatus.UNAVAILABLE,
                                           "fake provider down")
            if st == "rate_limited":
                raise CommerceSearchSignal(ProviderStatus.RATE_LIMITED,
                                           "fake rate limited")
            if st == "not_found":
                raise CommerceSearchSignal(ProviderStatus.NOT_FOUND,
                                           "fake no match")
            return _hotels_for(city, ci, co, adults=adults, children=children,
                               rooms=rooms, scenario=scenario)

        return run_commerce_search(
            provider_name=self.name, operation=self.operation, key=key,
            is_enabled=self.is_enabled(), loader=_load,
            record_factory=_hotel_record,
        )


def _segments_for(origin_city: str, dest_city: str, departure_date: str,
                  *, connection: bool) -> list[FlightSegmentRecord]:
    """确定性假航段；时刻由日期 + 固定偏移派生（含时区偏移 ISO 串）。"""
    o = _CITY_AIRPORT.get(origin_city)
    d = _CITY_AIRPORT.get(dest_city)
    dep0 = f"{departure_date}T08:30:00+08:00"
    if connection:
        hub = "SHA" if o != "SHA" and d != "SHA" else "PEK"
        return [
            FlightSegmentRecord(
                carrier="FakeAir", flight_number="FA101",
                origin_airport=o, destination_airport=hub,
                departure_at=dep0,
                arrival_at=f"{departure_date}T11:05:00+08:00"),
            FlightSegmentRecord(
                carrier="FakeAir", flight_number="FA202",
                origin_airport=hub, destination_airport=d,
                departure_at=f"{departure_date}T12:30:00+08:00",
                arrival_at=f"{departure_date}T15:10:00+08:00"),
        ]
    return [FlightSegmentRecord(
        carrier="FakeAir", flight_number="FA001",
        origin_airport=o, destination_airport=d,
        departure_at=dep0,
        arrival_at=f"{departure_date}T09:55:00+08:00")]


def _flights_for(origin_city: str, dest_city: str, departure_date: str,
                 scenario: dict | None) -> list[FlightOfferRecord]:
    st = _scenario_type(scenario)
    currency = _CITY_CURRENCY.get(dest_city, "CNY")

    def _record(idx: int, segments: list[FlightSegmentRecord], amount: str,
                availability: str = AVAIL_AVAILABLE, *,
                cabin: str | None = "经济舱", baggage: str | None = "手提7kg",
                deeplink: str | None = None) -> FlightOfferRecord:
        return FlightOfferRecord(
            provider=PROVIDER_NAME,
            origin=segments[0].origin_airport,
            destination=segments[-1].destination_airport,
            segments=segments, availability=availability,
            price=PriceSnapshotRecord(
                amount=amount, currency=currency,
                provider_offer_id=f"fake-fare-{idx}"),
            provider_offer_id=f"fake-fare-{idx}",
            cabin=cabin, baggage=baggage,
            booking_deep_link=deeplink,
            source_id=f"fake:flight:{idx}",
            extra={"fake": True},
        )

    link = f"https://fake-commerce.example.com/flight?date={departure_date}"
    if st == "empty":
        return []
    if st == "connection":
        segs = _segments_for(origin_city, dest_city, departure_date,
                             connection=True)
        return [_record(11, segs, "1560", deeplink=link + "&id=11")]
    if st == "sold_out":
        return [_record(2, _segments_for(origin_city, dest_city,
                                         departure_date, connection=False),
                        "890", AVAIL_SOLD_OUT)]
    if st == "invalid_segment":
        rec = _record(3, _segments_for(origin_city, dest_city,
                                       departure_date, connection=False),
                      "890")
        # 脏数据：到达早于出发（模型门必须拒绝）
        rec.segments = [FlightSegmentRecord(
            carrier="FakeAir", flight_number="FA099",
            origin_airport=rec.origin, destination_airport=rec.destination,
            departure_at=f"{departure_date}T10:00:00+08:00",
            arrival_at=f"{departure_date}T09:00:00+08:00")]
        return [rec]
    if st == "invalid_price":
        return [_record(4, _segments_for(origin_city, dest_city,
                                         departure_date, connection=False),
                        "N/A")]
    if st == "unknown_availability":
        return [_record(5, _segments_for(origin_city, dest_city,
                                         departure_date, connection=False),
                        "920", AVAIL_UNKNOWN, deeplink=link + "&id=5")]
    if st == "deeplink_invalid":
        return [_record(6, _segments_for(origin_city, dest_city,
                                         departure_date, connection=False),
                        "880",
                        deeplink="javascript:alert(1)")]

    return [_record(1, _segments_for(origin_city, dest_city, departure_date,
                                     connection=False),
                    "890", deeplink=link + "&id=1")]


class FakeFlightSearchProvider:
    """机票搜索 Fake 适配器（one-way；走完整 Provider 层执行流）。"""

    name = PROVIDER_NAME
    operation = "flight_search"

    def __init__(self, scenario: dict | None = None):
        self._scenario = scenario or {}

    def is_enabled(self) -> bool:
        from backend.config.travel_commerce import provider_mode

        return provider_mode() == "fake"

    def search_flights(
        self, origin: str, destination: str, departure_date: date, *,
        return_date: date | None = None, adults: int = 1, children: int = 0,
        cabin: str | None = None,
    ) -> ProviderResult[list[FlightOfferRecord]]:
        from backend.providers.travel.live import cache as pcache

        origin = (origin or "").strip()
        destination = (destination or "").strip()
        dd = departure_date.isoformat()
        key = pcache.build_key(self.operation, origin.lower(),
                               destination.lower(), dd,
                               return_date.isoformat() if return_date else "",
                               adults, children, cabin or "")
        scenario = self._scenario

        def _load() -> list[FlightOfferRecord]:
            st = _scenario_type(scenario)
            if st == "timeout_fast":
                raise TimeoutError("fake timeout")
            if st == "unavailable":
                raise CommerceSearchSignal(ProviderStatus.UNAVAILABLE,
                                           "fake provider down")
            if st == "rate_limited":
                raise CommerceSearchSignal(ProviderStatus.RATE_LIMITED,
                                           "fake rate limited")
            if st == "not_found":
                raise CommerceSearchSignal(ProviderStatus.NOT_FOUND,
                                           "fake no match")
            if (origin not in _CITY_AIRPORT or destination not in _CITY_AIRPORT):
                raise CommerceSearchSignal(
                    ProviderStatus.NOT_FOUND,
                    f"fake 静态机场表无 {origin}/{destination}")
            return _flights_for(origin, destination, dd, scenario)

        return run_commerce_search(
            provider_name=self.name, operation=self.operation, key=key,
            is_enabled=self.is_enabled(), loader=_load,
            record_factory=_flight_record,
        )


# 供测试构造「已过期缓存」场景（H5/F6 stale fallback）：写入 fresh 过去时点
def seed_stale_cache(operation: str, key_parts: list, records: list) -> None:
    """向共享缓存写入一条已过 fresh 期、物理未过期的 success 条目。

    仅测试/评测使用（构造 stale-ready 状态）；records 为 asdict 后的
    Record dict 列表。物理 TTL = fresh+grace 内才可被 cache_get_stale 读到。
    """
    from backend.providers.travel.live import cache as pcache

    key = pcache.build_key(operation, *key_parts)
    observed = (datetime.utcnow() - timedelta(hours=1)).isoformat() + "Z"
    pcache.cache_put_success(
        key, data=list(records), provider=PROVIDER_NAME, operation=operation,
        observed_at=observed,
    )
    # 人为把 fresh_until 推到过去（cache_put_success 只写 fresh 值）——
    # 这是构造 stale-ready 的唯一途径，仅测试路径接触 cache 模块内部
    env = pcache._read(key)
    if env is not None:
        import time as _t

        env.fresh_until = _t.time() - 1
        pcache._write(key, env, physical_ttl=pcache.FRESH_TTLS.get(operation, 300)
                      + pcache.STALE_GRACE)
