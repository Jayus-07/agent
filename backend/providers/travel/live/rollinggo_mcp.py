"""providers/travel/live/rollinggo_mcp.py — rollinggo 酒店 **live** 适配器
（travel-v2-production P1，2026-11-01 接线）

数据通道：平台 MCP 能力中心的动态 capability（连接名 rollinggo × 上游工具
searchHotels → ``mcp.rollinggo__searchhotels``），经 mcp_registry 快照解析
连接/凭据，复用 skills/mcp_dynamic 的统一执行核心（运行时在场检查、SSRF
防护、头解析、call_tool）——不建第二套 MCP 调用路径。

边界（与任务红线一致）：
  - **只查不订**。本适配器只包装 searchHotels（READ）；rollinggo 的预订/
    库存写操作一律不接，预订走平台预订确认门（当前 fake + fail-closed）。
  - 上游响应是**不可信输入**：逐条过收编件 normalize 三道门（金额/枚举/
    模型 strict），全部被拒 = INVALID_RESPONSE；适配器只做形状映射，不补
    Provider 未给的事实。
  - 未报价（hasPrice != true）的酒店不是报价：适配器过滤（不进 normalize），
    全部未报价 → SUCCESS+[]（「无可报价候选」），而非 INVALID_RESPONSE。

价格语义（诚实映射，不猜）：
  rollinggo 结构化给出每晚最低价 price.lowestPrice 与展示文本 price.message
  （含「N晚总价：X CNY」）；PriceSnapshot.amount 契约是 **总价**。总价取值
  顺序：① message 中的显式总价（provider 自己陈述的事实）；② 兜底 =
  每晚价 × 请求晚数（确定性算术组合，两因子均为 provider/请求事实）。
  两路都拿不到（无价/非法）→ 该条被过滤，绝不编造金额。
"""
from __future__ import annotations

import json
import re
from datetime import date
from decimal import Decimal, InvalidOperation

from backend.providers.travel.live.commerce_adapter_base import (
    CommerceSearchSignal,
    run_commerce_search,
)
from backend.providers.travel.live.commerce_contracts import (
    AVAIL_UNKNOWN,
    HotelOfferRecord,
    PriceSnapshotRecord,
)
from backend.providers.travel.live.result import (
    ProviderResult,
    ProviderStatus,
)

PROVIDER_NAME = "rollinggo:mcp"

# 与管理端登记的连接名/上游工具名对应（改名需同步管理端与本配置）
_DEFAULT_CAPABILITY = "mcp.rollinggo__searchhotels"

# searchHotels 单页上限（schema：默认 5，最大 10）；下游 MAX_OFFERS 截断披露
_PAGE_SIZE = 10

# price.message 中的显式总价（跨币种；容忍千分位）："2晚总价：1,694CNY"
_TOTAL_IN_MESSAGE_RE = re.compile(r"总价[：:]\s*([\d,]+(?:\.\d+)?)\s*([A-Z]{3})")


def live_capability() -> str:
    """live 通道的 capability（env 可覆盖，默认 = 管理端登记的标准 slug）。"""
    from backend.config.travel_offer_search import (
        TRAVEL_ROLLINGGO_HOTEL_CAPABILITY,
    )

    return TRAVEL_ROLLINGGO_HOTEL_CAPABILITY


def resolve_live_hotel_provider() -> tuple["RollinggoHotelSearchProvider | None", str]:
    """live 模式的 provider 解析：MCP 运行时不在场 = None（如实 disabled）。

    运行时在场 = 连接启用 + 工具 approved + 内容未漂移（快照口径）；审批/
    授权治理在能力中心完成，不在此重复判断。**绝不回退 fake 冒充**（§29）。
    """
    from backend.config.travel_offer_search import provider_mode
    from backend.infra.mcp_registry import store as mcp_store

    if provider_mode() != "live":
        return None, ""
    capability = live_capability()
    if mcp_store.get_runtime(capability) is None:
        return None, (
            f"live 酒店数据源未在 MCP 运行时生效（{capability} 未登记/"
            "未审批或连接停用）——不回退 fake 充数")
    return RollinggoHotelSearchProvider(), ""


class RollinggoHotelSearchProvider:
    """酒店搜索 live 适配器（走完整 Provider 层执行流，与 fake 同一条管道）。"""

    name = PROVIDER_NAME
    operation = "hotel_search"

    def is_enabled(self) -> bool:
        from backend.config.travel_offer_search import provider_mode

        return provider_mode() == "live"

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

        def _load() -> list[HotelOfferRecord]:
            payload = _invoke_mcp(live_capability(), _mcp_args(
                city, nights=(check_out - check_in).days, adults=adults,
                children=children, rooms=rooms, star_rating=star_rating))
            return _records_from_payload(
                payload, city=city, check_in=ci, check_out=co,
                adults=adults, children=children, rooms=rooms,
                nights=(check_out - check_in).days)

        return run_commerce_search(
            provider_name=self.name, operation=self.operation, key=key,
            is_enabled=self.is_enabled(), loader=_load,
            record_factory=_record_from_cached,
        )


def _invoke_mcp(capability: str, params: dict) -> object:
    """经动态 MCP 统一执行核心出网（read 工具；失败归类为语义信号）。"""
    from backend.infra.mcp_client import McpClientError
    from backend.skills.mcp_dynamic.skill import _McpDynamicToolAdapter

    try:
        return _McpDynamicToolAdapter(capability).invoke(dict(params))
    except McpClientError as exc:
        raise CommerceSearchSignal(
            ProviderStatus.UNAVAILABLE, f"MCP 通道失败: {exc}") from exc
    except TimeoutError:
        raise
    except Exception as exc:  # noqa: BLE001 — 未归类异常走 UNAVAILABLE
        raise CommerceSearchSignal(
            ProviderStatus.UNAVAILABLE,
            f"MCP 通道异常: {type(exc).__name__}") from exc


def _mcp_args(city: str, *, nights: int, adults: int, children: int,
              rooms: int, star_rating: int | None) -> dict:
    """请求 → searchHotels 入参（确定性构造，无 LLM 参与）。

    通道能力边界（上游 schema 无对应参数）：多间房 / 携儿童无法如实表达
    → 显式拒绝，不静默降级为「1 间房 / 无儿童」的假答案。
    """
    if rooms > 1:
        raise CommerceSearchSignal(
            ProviderStatus.UNAVAILABLE,
            "rollinggo 通道当前仅支持单间房查询（rooms>1 无法如实表达）")
    if children > 0:
        raise CommerceSearchSignal(
            ProviderStatus.UNAVAILABLE,
            "rollinggo 通道当前不支持携儿童询价（无法如实表达儿童数）")

    check_in_param: dict = {"adultCount": adults, "stayNights": nights}
    args: dict = {
        "originQuery": _origin_query(city, nights=nights, adults=adults,
                                     star_rating=star_rating),
        "place": city,
        "placeType": "城市",
        "checkInParam": check_in_param,
        "size": _PAGE_SIZE,
    }
    if star_rating:
        args["filterOptions"] = {
            "starRatings": [float(star_rating), 5.0]}
    return args


def _origin_query(city: str, *, nights: int, adults: int,
                  star_rating: int | None) -> str:
    """住宿意图的确定性归纳（schema 语义：多轮意图的完整陈述）。"""
    parts = [f"{city}酒店", f"住{nights}晚", f"每间{adults}成人"]
    if star_rating:
        parts.append(f"{star_rating}星级及以上")
    return "，".join(parts)


def _records_from_payload(
    payload: object, *, city: str, check_in: str, check_out: str,
    adults: int, children: int, rooms: int, nights: int,
) -> list[HotelOfferRecord]:
    """上游响应 → Record 列表（形状映射；业务封套失败显式信号）。"""
    data = _unwrap(payload)
    hotels = data.get("hotelInformationList")
    if not isinstance(hotels, list):
        raise CommerceSearchSignal(
            ProviderStatus.INVALID_RESPONSE,
            "响应缺少 hotelInformationList（形状不符契约）")

    records: list[HotelOfferRecord] = []
    for item in hotels:
        if not isinstance(item, dict):
            continue
        record = _record_from_hotel(
            item, city=city, check_in=check_in, check_out=check_out,
            adults=adults, children=children, rooms=rooms, nights=nights)
        if record is not None:
            records.append(record)
    return records


def _unwrap(payload: object) -> dict:
    """MCP 归一 payload → 业务封套 dict（str JSON / {"data": ...} 解包）。"""
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except ValueError as exc:
            raise CommerceSearchSignal(
                ProviderStatus.INVALID_RESPONSE,
                "响应不是合法 JSON") from exc
    if not isinstance(payload, dict):
        raise CommerceSearchSignal(
            ProviderStatus.INVALID_RESPONSE,
            f"响应形状不符契约（{type(payload).__name__}）")
    data = payload.get("data") if isinstance(payload.get("data"), dict) \
        else payload
    # 业务封套：success=false 或非成功码 = 上游明确失败（如实信号，不装没结果）
    if data.get("success") is False:
        raise CommerceSearchSignal(
            ProviderStatus.UNAVAILABLE,
            f"上游业务失败: {data.get('message') or data.get('code')}")
    return data


def _record_from_hotel(
    item: dict, *, city: str, check_in: str, check_out: str,
    adults: int, children: int, rooms: int, nights: int,
) -> HotelOfferRecord | None:
    """单酒店条目 → Record；未报价/币种缺失 = None（过滤，不造报价）。"""
    price = item.get("price") or {}
    if price.get("hasPrice") is not True:
        return None
    currency = str(price.get("currency") or "").strip()
    if not currency:
        return None
    hotel_id = str(item.get("hotelId") or "").strip()
    name = str(item.get("name") or "").strip()
    if not hotel_id or not name:
        return None

    total = _total_price(price, nights=nights)
    if total is None:
        return None

    # 酒店级报价：rate 标识用 hotelId（指纹稳定 = 同酒店同日期同人重搜可复核）
    return HotelOfferRecord(
        provider=PROVIDER_NAME,
        property_id=hotel_id,
        property_name=name,
        city=city,
        check_in=check_in,
        check_out=check_out,
        availability=AVAIL_UNKNOWN,  # 搜索响应无房态字段：unknown，不推断
        price=PriceSnapshotRecord(
            amount=total, currency=currency,
            provider_offer_id=hotel_id,
        ),
        provider_offer_id=hotel_id,
        address=(str(item.get("address")).strip() or None)
        if item.get("address") else None,
        lat=item.get("latitude") if isinstance(item.get("latitude"), (int, float)) else None,
        lng=item.get("longitude") if isinstance(item.get("longitude"), (int, float)) else None,
        adults=adults, children=children, rooms=rooms,
        booking_deep_link=(str(item.get("bookingUrl")).strip() or None)
        if item.get("bookingUrl") else None,
        source_id=f"rollinggo:hotel:{hotel_id}",
        extra=_trace_extra(item, price, nights),
    )


def _total_price(price: dict, *, nights: int) -> str | None:
    """总价字符串：message 显式总价优先，兜底每晚价 × 晚数；拿不到 = None。"""
    match = _TOTAL_IN_MESSAGE_RE.search(str(price.get("message") or ""))
    if match:
        amount = match.group(1).replace(",", "")
        try:
            if Decimal(amount) > 0:
                return amount
        except (InvalidOperation, ValueError):
            pass
    nightly = price.get("lowestPrice")
    if isinstance(nightly, (int, float)) and nightly > 0 and nights >= 1:
        total = (Decimal(str(nightly)) * nights).normalize()
        return str(total)
    return None


def _trace_extra(item: dict, price: dict, nights: int) -> dict:
    """溯源元数据（每晚价/message/星级），供观测与排障；不进用户面。"""
    extra: dict = {"rollinggo": True}
    if price.get("lowestPrice") is not None:
        extra["nightly_price"] = str(price["lowestPrice"])
    if price.get("message"):
        extra["price_message"] = str(price["message"])[:200]
    if isinstance(item.get("starRating"), (int, float)):
        extra["star_rating"] = item["starRating"]
    return extra


def _record_from_cached(d: dict) -> HotelOfferRecord:
    """缓存 dict → Record（嵌套 price 重建；损坏按 miss 由基类处理）。"""
    if isinstance(d.get("price"), dict):
        d = {**d, "price": PriceSnapshotRecord(**d["price"])}
    return HotelOfferRecord(**d)


__all__ = [
    "PROVIDER_NAME",
    "RollinggoHotelSearchProvider",
    "live_capability",
    "resolve_live_hotel_provider",
]
