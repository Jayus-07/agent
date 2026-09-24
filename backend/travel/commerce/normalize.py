"""travel/commerce/normalize.py — Provider Record → 领域模型的 fail-closed 门（STOP K4，G10）

**脏数据不穿透**：每条 Record 过三道门——金额门（Decimal 化 + 非法拒绝）、
枚举门（availability/tax_inclusion 非法值拒绝）、模型门（Pydantic strict
校验，extra=forbid）。任何一门不过 → 该条记入 rejects（原因可观测），
绝不出现在结果里；**全部记录都被拒 → 调用方按 INVALID_RESPONSE 结局处理**
（Provider 返回的是非法响应，不是「无结果」）。

区分两个概念（§K5）：
  拒绝记录（reject）      = Provider 响应非法，信任问题；
  无结果（empty/not_found）= Provider 正常回答没有，事实问题。
  二者混同 = 把「供应商给了脏数据」说成「市场没有供给」，禁止。

Deep Link 校验失败**不拒绝整条 offer**（价格/库存是真实事实），只降为
deep_link=None 并计入 deeplink 拒绝指标（§十二：不提供/不合法 = None）。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from backend.providers.travel.live.commerce_contracts import (
    FlightOfferRecord,
    HotelOfferRecord,
)
from backend.providers.travel.live.result import Freshness
from backend.travel.commerce import deeplink
from backend.travel.commerce.identity import (
    flight_fingerprint,
    hotel_fingerprint,
    snapshot_id,
)
from backend.travel.commerce.models import (
    Availability,
    AvailabilityObservation,
    FlightOffer,
    FlightSegment,
    HotelOffer,
    Money,
    Occupancy,
    PriceSnapshot,
    TaxInclusion,
)


@dataclass
class NormalizeOutcome:
    """归一化结局：valids + rejects（每条拒绝带机器可读原因，入指标/trace）。

    deeplink_stats：链接校验结局计数（pass/rejected/absent，G9 指标口径）
    ——链接不合法不拒绝整条 offer（价格/库存是真实事实），只降为 None。
    """

    valids: list[HotelOffer | FlightOffer]
    rejects: list[str]
    deeplink_stats: dict[str, int]


def _deeplink_or_none(link: str | None, allowed_hosts: tuple[str, ...],
                      stats: dict[str, int]) -> str | None:
    ok, reason = deeplink.validate_deeplink(link, allowed_hosts)
    if ok:
        stats["pass"] += 1
        return link
    stats["absent" if reason == "absent" else "rejected"] += 1
    return deeplink.sanitize(link, allowed_hosts)  # 拒绝原因入日志（不含 URL）


def _to_money(amount: str | None, currency: str) -> Money | None:
    """字符串金额 → Money；None/空/非法 = None（调用方决定拒绝策略）。"""
    if amount is None or not str(amount).strip():
        return None
    try:
        # str 构造 Decimal：十进制精确，无 float 二进制噪声（G4）
        return Money(amount=Decimal(str(amount).strip()), currency=currency)
    except (InvalidOperation, ValueError):
        return None


def _availability(value: str) -> Availability | None:
    try:
        return Availability(str(value).strip().lower())
    except ValueError:
        return None


def _tax_inclusion(value: str) -> TaxInclusion | None:
    try:
        return TaxInclusion(str(value).strip().lower())
    except ValueError:
        return None


def _snapshot(rec_price, provider: str, observed_at: str,
              freshness: Freshness, fingerprint: str) -> PriceSnapshot | None:
    """PriceSnapshotRecord → PriceSnapshot；金额/币种非法 = None。"""
    money = _to_money(rec_price.amount, rec_price.currency)
    if money is None:
        return None
    taxes = _to_money(rec_price.taxes, rec_price.currency) if rec_price.taxes else None
    fees = _to_money(rec_price.fees, rec_price.currency) if rec_price.fees else None
    # taxes 显式给了但金额非法（如 "n/a"）→ 快照整体非法，不得静默降为 unknown
    if rec_price.taxes and taxes is None:
        return None
    if rec_price.fees and fees is None:
        return None
    inclusion = _tax_inclusion(rec_price.tax_inclusion)
    if inclusion is None:
        return None
    return PriceSnapshot(
        snapshot_id=snapshot_id(fingerprint, observed_at),
        provider=provider,
        provider_offer_id=rec_price.provider_offer_id,
        amount=money,
        base_amount=_to_money(rec_price.base_amount, rec_price.currency)
        if rec_price.base_amount else None,
        taxes=taxes,
        fees=fees,
        tax_inclusion=inclusion,
        observed_at=observed_at,
        expires_at=rec_price.expires_at,
        freshness=freshness,
    )


def normalize_hotel_offers(
    records: list[HotelOfferRecord], *, request_check_in: date,
    request_check_out: date, request_adults: int, request_children: int,
    request_rooms: int, observed_at: str, freshness: Freshness,
    allowed_hosts: tuple[str, ...],
) -> NormalizeOutcome:
    """HotelOfferRecord 列表 → 已校验 HotelOffer 列表（fail-closed）。"""
    valids: list[HotelOffer] = []
    rejects: list[str] = []
    deeplink_stats = {"pass": 0, "rejected": 0, "absent": 0}
    ci, co = request_check_in.isoformat(), request_check_out.isoformat()

    for rec in records:
        tag = f"hotel:{rec.property_id or '?'}"

        # 日期必须与请求一致（Provider 回别的日期 = 非法响应）
        if rec.check_in != ci or rec.check_out != co:
            rejects.append(f"{tag}:date-mismatch")
            continue
        # occupancy 以请求为准（回显不一致 = 非法）
        if (rec.adults, rec.children, rec.rooms) != (
                request_adults, request_children, request_rooms):
            rejects.append(f"{tag}:occupancy-mismatch")
            continue
        # rate 标识必须存在（双缺 = 无法区分 rate plan，禁合并 → 拒绝，§十五）
        if not (rec.provider_offer_id or "").strip() and not (rec.room_type or "").strip():
            rejects.append(f"{tag}:no-rate-identifier")
            continue
        avail = _availability(rec.availability)
        if avail is None:
            rejects.append(f"{tag}:invalid-availability")
            continue
        rate_id = (f"offer:{rec.provider_offer_id}" if rec.provider_offer_id
                   else f"room_type:{rec.room_type}")
        fingerprint = hotel_fingerprint(
            provider=rec.provider, property_id=rec.property_id,
            rate_identifier=rate_id, check_in=ci, check_out=co,
            adults=request_adults, children=request_children,
            rooms=request_rooms,
        )
        snapshot = _snapshot(rec.price, rec.provider, observed_at, freshness,
                             fingerprint)
        if snapshot is None:
            rejects.append(f"{tag}:invalid-price")
            continue

        try:
            offer = HotelOffer(
                provider=rec.provider,
                provider_offer_id=rec.provider_offer_id,
                property_id=rec.property_id.strip(),
                property_name=rec.property_name.strip(),
                city=rec.city.strip(),
                address=rec.address,
                lat=rec.lat,
                lng=rec.lng,
                check_in=ci,
                check_out=co,
                nights=(request_check_out - request_check_in).days,
                room_type=rec.room_type,
                occupancy=Occupancy(adults=request_adults,
                                    children=request_children,
                                    rooms=request_rooms),
                availability=AvailabilityObservation(
                    status=avail, observed_at=observed_at),
                price_snapshot=snapshot,
                cancellation_policy=rec.cancellation_policy,
                meal_plan=rec.meal_plan,
                booking_deep_link=_deeplink_or_none(
                    rec.booking_deep_link, allowed_hosts, deeplink_stats),
                observed_at=observed_at,
                freshness=freshness,
                source_id=rec.source_id,
                offer_fingerprint=fingerprint,
            )
        except Exception as e:  # pydantic ValidationError（strict 门）
            rejects.append(f"{tag}:{type(e).__name__}")
            continue
        valids.append(offer)

    return NormalizeOutcome(valids=valids, rejects=rejects,
                            deeplink_stats=deeplink_stats)


def normalize_flight_offers(
    records: list[FlightOfferRecord], *, departure_date: date,
    return_date: date | None, observed_at: str, freshness: Freshness,
    allowed_hosts: tuple[str, ...],
) -> NormalizeOutcome:
    """FlightOfferRecord 列表 → 已校验 FlightOffer 列表（fail-closed）。"""
    valids: list[FlightOffer] = []
    rejects: list[str] = []
    deeplink_stats = {"pass": 0, "rejected": 0, "absent": 0}

    for rec in records:
        seg0 = rec.segments[0] if rec.segments else None
        tag = f"flight:{seg0.flight_number if seg0 else '?'}"

        if not rec.segments:
            rejects.append(f"{tag}:no-segments")
            continue
        if rec.origin != rec.segments[0].origin_airport or (
                rec.destination != rec.segments[-1].destination_airport):
            rejects.append(f"{tag}:endpoint-mismatch")
            continue
        # 单程契约：返程日期在场 = Provider/适配器越出契约（K0 §7 one-way 必持）
        if return_date is not None:
            rejects.append(f"{tag}:round-trip-not-in-contract")
            continue
        # 出发日期必须与请求一致（以首段时刻的日期部分比对）
        try:
            from datetime import datetime as _dt

            seg_date = _dt.fromisoformat(rec.segments[0].departure_at).date()
        except ValueError:
            rejects.append(f"{tag}:invalid-departure-time")
            continue
        if seg_date != departure_date:
            rejects.append(f"{tag}:date-mismatch")
            continue
        avail = _availability(rec.availability)
        if avail is None:
            rejects.append(f"{tag}:invalid-availability")
            continue

        segment_keys = [
            f"{s.carrier}:{s.flight_number}:{s.departure_at}" for s in rec.segments]
        fingerprint = flight_fingerprint(
            provider=rec.provider, provider_offer_id=rec.provider_offer_id,
            segment_keys=segment_keys, cabin=rec.cabin,
        )
        snapshot = _snapshot(rec.price, rec.provider, observed_at, freshness,
                             fingerprint)
        if snapshot is None:
            rejects.append(f"{tag}:invalid-price")
            continue

        stops = rec.stops if rec.stops is not None else len(rec.segments) - 1

        try:
            segments = [FlightSegment(
                carrier=s.carrier, flight_number=s.flight_number,
                origin_airport=s.origin_airport.strip().upper(),
                destination_airport=s.destination_airport.strip().upper(),
                departure_at=s.departure_at, arrival_at=s.arrival_at,
            ) for s in rec.segments]
            offer = FlightOffer(
                provider=rec.provider,
                provider_offer_id=rec.provider_offer_id,
                origin=rec.origin.strip().upper(),
                destination=rec.destination.strip().upper(),
                segments=segments,
                duration_minutes=rec.duration_minutes,
                stops=stops,
                cabin=rec.cabin,
                availability=AvailabilityObservation(
                    status=avail, observed_at=observed_at),
                price_snapshot=snapshot,
                baggage=rec.baggage,
                fare_rules=rec.fare_rules,
                booking_deep_link=_deeplink_or_none(
                    rec.booking_deep_link, allowed_hosts, deeplink_stats),
                observed_at=observed_at,
                freshness=freshness,
                source_id=rec.source_id,
                offer_fingerprint=fingerprint,
            )
        except Exception as e:  # pydantic ValidationError（strict 门）
            rejects.append(f"{tag}:{type(e).__name__}")
            continue
        valids.append(offer)

    return NormalizeOutcome(valids=valids, rejects=rejects,
                            deeplink_stats=deeplink_stats)
