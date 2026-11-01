"""travel_v2/api/bookings.py — 预订确认门 REST 三段端点（收编重构 §4.2）

原 travel_booking 聊天子图壳删除后的对外形态：Quote → 显式确认 → 状态查询。
事务机制（executor/store/identity/revalidate/webhook/reconciliation/recovery）
原样保留在 backend/travel/booking/，本文件只是**确定性 HTTP 门面**：

  - 鉴权：沿用 travel_v2 网关身份 + 行程属主 fail-closed（trip 归属校验），
    订单/报价归属由 booking/authorization 二次校验（双保险）；
  - 全程参数绑定 SQL（store 层既有参数化；本文件不写任何拼接 SQL）；
  - provider off → 如实 503（不伪造执行）；IN_DOUBT → 200 + 白名单话术
    （reporter.render_status/render_execution_outcome，禁词有测试钉死）。

聊天入口：V2 对话中预订意图由 ai-edits clarify 返回指引卡片（点卡片走
本 REST 确认门）——不在域图内挂预订节点。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from backend.app.api.identity import require_identity
from backend.travel.booking.authorization import (
    BookingAuthorizationError,
    assert_owns_order,
)
from backend.travel.booking.reporter import (
    render_execution_outcome,
    render_quote_confirmation,
    render_status,
)
from backend.travel.booking.service import (
    BookingProviderOff,
    BookingService,
    OfferNotSelectable,
)
from backend.travel.booking.store import BookingStore
from backend.travel_v2.models.booking import BookingQuoteRequest
from backend.travel_v2.services.trip_service import TripNotFound, TripService

router = APIRouter(tags=["旅游 V2 预订确认门"])


def _scope(request: Request) -> tuple[str, str]:
    identity = require_identity(request)
    tenant_id = (identity.tenant_id or "").strip()
    owner_id = (identity.user_id or "").strip()
    if not tenant_id or not owner_id:
        raise HTTPException(status_code=403, detail={
            "code": "TRAVEL_V2_IDENTITY_INCOMPLETE",
            "message": "预订确认门需要完整的用户与租户身份",
        })
    return tenant_id, owner_id


def _assert_trip_owner(tenant_id: str, owner_id: str, trip_id: str) -> None:
    """行程属主校验（fail-closed）：不存在/不属于本人一律 404（不泄露存在性）。"""
    try:
        TripService().get_trip(trip_id, tenant_id=tenant_id, owner_id=owner_id)
    except TripNotFound:
        raise HTTPException(status_code=404, detail={
            "code": "NOT_FOUND", "message": "行程不存在",
        }) from None


def _booking_error(exc: Exception) -> HTTPException:
    if isinstance(exc, BookingProviderOff):
        return HTTPException(status_code=503, detail={
            "code": "BOOKING_PROVIDER_OFF",
            "message": "预订功能未开通（provider off），未执行任何下单动作",
        })
    if isinstance(exc, OfferNotSelectable):
        return HTTPException(status_code=422, detail={
            "code": "OFFER_NOT_SELECTABLE", "message": str(exc),
        })
    if isinstance(exc, (BookingAuthorizationError, PermissionError)):
        return HTTPException(status_code=404, detail={
            "code": "NOT_FOUND", "message": "订单不存在",
        })
    if isinstance(exc, ValueError):
        return HTTPException(status_code=422, detail={
            "code": "INVALID_BOOKING_PARAM", "message": str(exc),
        })
    raise exc


@router.post(
    "/travel/v2/trips/{trip_id}/bookings/quote",
    summary="创建预订报价（TTL/指纹/金额快照，返回确认卡）",
)
def create_booking_quote(trip_id: str, payload: BookingQuoteRequest,
                         request: Request):
    tenant_id, owner_id = _scope(request)
    _assert_trip_owner(tenant_id, owner_id, trip_id)
    # provider off 前置如实拒绝：不给一张永远无法执行的确认卡
    from backend.config.travel_booking import provider_name

    if provider_name() == "off":
        raise _booking_error(BookingProviderOff("TRAVEL_BOOKING_PROVIDER=off"))
    try:
        outcome = BookingService(store=BookingStore()).create_quote(
            tenant_id=tenant_id, user_id=owner_id,
            commerce_type=payload.commerce_type,
            search_params=payload.search_params,
            selection=payload.selection,
        )
    except (BookingProviderOff, OfferNotSelectable, ValueError) as exc:
        raise _booking_error(exc) from exc
    quote = outcome.quote
    return {
        "status": "awaiting_confirmation",
        "trip_id": trip_id,
        "booking_intent_id": outcome.order["booking_intent_id"],
        "order_id": outcome.order["order_id"],
        "quote_id": quote["quote_id"],
        "amount": quote["price_amount"],
        "currency": quote["currency"],
        "tax_inclusion": quote.get("tax_inclusion"),
        "availability": quote.get("availability"),
        "internal_expires_at": quote.get("internal_expires_at"),
        "provider_expires_at": quote.get("provider_expires_at"),
        # 确认卡文案（金额互检/截止时间/幂等语义由 reporter 白名单承载）
        "message": render_quote_confirmation(
            quote, outcome.offer_summary, outcome.order),
    }


@router.post(
    "/travel/v2/trips/{trip_id}/bookings/{booking_intent_id}/confirm",
    summary="显式确认并执行预订（复核→幂等执行→订单状态）",
)
def confirm_booking(trip_id: str, booking_intent_id: str, request: Request):
    tenant_id, owner_id = _scope(request)
    _assert_trip_owner(tenant_id, owner_id, trip_id)
    store = BookingStore()
    order = store.get_order_by_intent(booking_intent_id)
    if order is None:
        raise HTTPException(status_code=404, detail={
            "code": "NOT_FOUND", "message": "订单不存在",
        })
    try:
        # 归属二次校验（订单 tenant/user 与当前身份一致，否则 404 不泄露）
        assert_owns_order(order, tenant_id=tenant_id, user_id=owner_id)
        outcome = BookingService(store=store).confirm_and_execute(
            tenant_id=tenant_id, user_id=owner_id,
            order_id=order["order_id"],
        )
    except (BookingProviderOff, BookingAuthorizationError, PermissionError) as exc:
        raise _booking_error(exc) from exc

    from backend.config.travel_booking import provider_name

    body = {
        "trip_id": trip_id,
        "booking_intent_id": booking_intent_id,
        "result": outcome.result,
        "order": outcome.order,
        "detail": outcome.detail,
        "message": render_execution_outcome(
            outcome, provider_mode=provider_name()),
    }
    if outcome.new_quote is not None:
        body["new_quote"] = {
            "quote_id": outcome.new_quote["quote_id"],
            "amount": outcome.new_quote["price_amount"],
            "currency": outcome.new_quote["currency"],
            "internal_expires_at": outcome.new_quote.get("internal_expires_at"),
        }
    return body


@router.get(
    "/travel/v2/trips/{trip_id}/bookings/{booking_intent_id}",
    summary="查询预订状态（IN_DOUBT 如实呈现白名单话术）",
)
def get_booking_status(trip_id: str, booking_intent_id: str, request: Request):
    tenant_id, owner_id = _scope(request)
    _assert_trip_owner(tenant_id, owner_id, trip_id)
    store = BookingStore()
    order = store.get_order_by_intent(booking_intent_id)
    if order is None:
        raise HTTPException(status_code=404, detail={
            "code": "NOT_FOUND", "message": "订单不存在",
        })
    try:
        assert_owns_order(order, tenant_id=tenant_id, user_id=owner_id)
    except BookingAuthorizationError as exc:
        raise _booking_error(exc) from exc
    report = BookingService(store=store).status_report(
        tenant_id=tenant_id, user_id=owner_id,
        order_id=order["order_id"],
    )
    return {
        "trip_id": trip_id,
        "booking_intent_id": booking_intent_id,
        "status": (report or {}).get("order", {}).get("status", "none"),
        "order": (report or {}).get("order"),
        "events": (report or {}).get("events"),
        "message": render_status(report),
    }
