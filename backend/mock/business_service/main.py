"""有状态 business-service Mock：为客服流程提供确定性的订单和售后数据。"""
from __future__ import annotations

import hashlib
import os
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

app = FastAPI(title="business-service mock", docs_url=None, redoc_url=None)

_INTERNAL_TOKEN = os.getenv("INTERNAL_API_TOKEN", "")
_STATE_LOCK = threading.RLock()
_REFUND_REQUESTS: dict[tuple[str, str, str], dict[str, Any]] = {}
_ADDRESS_CHANGE_REQUESTS: dict[tuple[str, str], dict[str, Any]] = {}
_OPERATIONS: dict[tuple[str, str], dict[str, Any]] = {}
# P2 商城 Mock 账本：订单可变状态与钱包（金额一律 Decimal，禁浮点累加）
_ORDERS: dict[tuple[str, str], dict[str, Any]] = {}
_WALLETS: dict[tuple[str, str], dict[str, Any]] = {}
# B2 会员积分账本：余额与流水（数字与 points-policy.md / FAQ id=8/16 互为事实源）
_POINTS: dict[tuple[str, str], dict[str, Any]] = {}
# B3 优惠券档案 + 核销记录（数字与 coupon-policy.md / FAQ id=23 互为事实源）
_COUPONS: dict[tuple[str, str], list[dict[str, Any]]] = {}
_REDEMPTIONS: dict[tuple[str, str], list[dict[str, Any]]] = {}
_INVOICE_TITLES: dict[tuple[str, str], list[dict[str, Any]]] = {}
_INVOICES: dict[tuple[str, str], list[dict[str, Any]]] = {}
_URGE_REQUESTS: dict[tuple[str, str, str, str], dict[str, Any]] = {}
# B4 支付流水样本；只开放不触碰真实资金的重复扣款模拟退款。
_PAYMENT_RECORDS: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
_COUPON_SEQ = 0
_SEEDED_USERS: set[str] = set()
_WALLET_LEDGER_SEQ = 0

_REFUNDABLE_STATUSES = frozenset(
    value.strip()
    for value in os.getenv(
        "CS_REFUNDABLE_STATUSES", "paid,shipped,completed",
    ).split(",")
    if value.strip()
)
_ACTIVE_REFUND_STATUSES = frozenset({"pending_review", "reviewing", "approved"})
_ELIGIBLE_PRICE_MATCH_STATUSES = frozenset({"paid", "completed"})
_REFUND_WINDOW_DAYS = int(os.getenv("CS_REFUND_WINDOW_DAYS", "30"))
# 取消订单资格：未付款直接取消；已支付待发货走客服提案（退款回钱包）
_CANCELLABLE_STATUSES = frozenset({"pending", "paid"})
_WALLET_INITIAL_BALANCE = Decimal(
    os.getenv("MOCK_WALLET_INITIAL_BALANCE", "5000.00"),
)
_PRICE_MATCH_MODE = os.getenv("CS_PRICE_MATCH_MODE", "disabled").strip().lower()
try:
    _PRICE_MATCH_WINDOW_DAYS = int(os.getenv("CS_PRICE_MATCH_WINDOW_DAYS", "0"))
except ValueError:
    _PRICE_MATCH_WINDOW_DAYS = 0
_PRODUCT_CURRENT_PRICES: dict[str, Decimal] = {
    "SKU-WIRELESS-HEADPHONES": Decimal("1199.00"),
    "SKU-BLUETOOTH-SPEAKER": Decimal("699.00"),
    "SKU-WIRED-HEADPHONES": Decimal("259.00"),
    "SKU-JUICER": Decimal("358.00"),
    "SKU-WATERPROOF-SPEAKER": Decimal("199.00"),
    "SKU-WOOL-COAT": Decimal("599.00"),
}


class RefundRequestInput(BaseModel):
    request_id: str
    user_id: str = Field(min_length=1, max_length=128)
    tenant_id: str = Field(min_length=1, max_length=128)
    conversation_id: str = Field(min_length=1, max_length=128)
    order_no: str = Field(min_length=1, max_length=64)
    expected_order_status: str = Field(min_length=1, max_length=32)
    expected_amount: float = Field(gt=0)
    policy_version: str = Field(min_length=1, max_length=64)
    reason: str = Field(default="", max_length=500)


class DuplicateRefundSimulationInput(BaseModel):
    action_id: uuid.UUID
    user_id: str = Field(min_length=1, max_length=128)
    tenant_id: str = Field(min_length=1, max_length=128)
    order_no: str = Field(min_length=1, max_length=64)


class RefundReasonUpdateInput(BaseModel):
    operation_id: str
    user_id: str = Field(min_length=1, max_length=128)
    tenant_id: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=1, max_length=500)


class AddressChangeInput(BaseModel):
    operation_key: str = Field(min_length=8, max_length=128)
    user_id: str = Field(min_length=1, max_length=128)
    tenant_id: str = Field(min_length=1, max_length=128)
    order_no: str = Field(min_length=1, max_length=64)
    expected_shipping_status: str = Field(min_length=1, max_length=32)
    new_address: str = Field(min_length=6, max_length=255)


class WalletTopUpInput(BaseModel):
    user_id: str = Field(min_length=1, max_length=128)
    tenant_id: str = Field(min_length=1, max_length=128)
    amount: float = Field(gt=0, le=50000)
    memo: str = Field(default="演示充值", max_length=200)
    operation_key: str = Field(min_length=8, max_length=128)


class OrderCreateInput(BaseModel):
    user_id: str = Field(min_length=1, max_length=128)
    tenant_id: str = Field(min_length=1, max_length=128)
    items: list[dict[str, Any]] = Field(min_length=1, max_length=20)
    operation_key: str = Field(min_length=8, max_length=128)


class OrderPayInput(BaseModel):
    user_id: str = Field(min_length=1, max_length=128)
    tenant_id: str = Field(min_length=1, max_length=128)
    expected_amount: float = Field(gt=0)
    operation_key: str = Field(min_length=8, max_length=128)


class OrderCancelInput(BaseModel):
    user_id: str = Field(min_length=1, max_length=128)
    tenant_id: str = Field(min_length=1, max_length=128)
    expected_status: str = Field(min_length=1, max_length=32)
    expected_amount: float = Field(ge=0)
    reason: str = Field(default="", max_length=500)
    operation_key: str = Field(min_length=8, max_length=128)


class PriceMatchRefundInput(BaseModel):
    operation_key: str = Field(min_length=8, max_length=128)
    user_id: str = Field(min_length=1, max_length=128)
    tenant_id: str = Field(min_length=1, max_length=128)
    order_no: str = Field(min_length=1, max_length=64)
    expected_status: str = Field(min_length=1, max_length=32)
    expected_payment_status: str = Field(min_length=1, max_length=32)
    expected_paid_at: str = Field(min_length=1, max_length=64)
    expected_currency: str = Field(min_length=1, max_length=8)
    product_no: str = Field(min_length=1, max_length=64)
    expected_original_price: Decimal = Field(gt=0)
    expected_current_price: Decimal = Field(gt=0)
    expected_amount: Decimal = Field(gt=0)
    window_days: int = Field(gt=0)


class UrgeRequestInput(BaseModel):
    user_id: str = Field(min_length=1, max_length=128)
    tenant_id: str = Field(min_length=1, max_length=128)
    conversation_id: str = Field(min_length=1, max_length=128)
    order_no: str = Field(min_length=1, max_length=64)
    request_type: Literal["shipping", "delivery"]


@app.exception_handler(HTTPException)
async def _http_error_handler(_request: Request, exc: HTTPException):
    """保持 Mock 的失败封套与 business-service 契约一致。"""
    code = {
        401: 40101,
        404: 40401,
        409: 40901,
        422: 42201,
    }.get(exc.status_code, 50001)
    message = exc.detail if isinstance(exc.detail, str) else "业务请求失败"
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": code, "message": message, "data": None},
    )


@app.exception_handler(RequestValidationError)
async def _validation_error_handler(
    _request: Request, _exc: RequestValidationError,
):
    return JSONResponse(
        status_code=422,
        content={"code": 42201, "message": "请求参数不合法", "data": None},
    )


def _check_token(x_internal_token: str | None) -> None:
    """内部令牌配置后必须精确匹配。"""
    if _INTERNAL_TOKEN and x_internal_token != _INTERNAL_TOKEN:
        raise HTTPException(status_code=401, detail="unauthorized")


def _seed_orders(user_id: str) -> list[dict[str, Any]]:
    """为每个用户生成稳定订单号，覆盖订单状态机全部六种状态。"""
    digest = hashlib.sha256(f"orders:{user_id}".encode()).hexdigest()
    now = datetime.now(timezone.utc)
    definitions = (
        ("paid", "paid", 1299.0, "无线耳机", 7, "SKU-WIRELESS-HEADPHONES"),
        ("shipped", "paid", 699.0, "蓝牙音箱", 3, "SKU-BLUETOOTH-SPEAKER"),
        ("completed", "paid", 299.0, "有线耳机", 45, "SKU-WIRED-HEADPHONES"),
        # P2 商城域：种子扩至五态（pending/cancelled/refunded 补齐状态机演示）
        ("pending", "unpaid", 358.0, "便携榨汁杯 400ml", 0, "SKU-JUICER"),
        ("cancelled", "unpaid", 199.0, "户外防水音箱 mini", 12, "SKU-WATERPROOF-SPEAKER"),
        ("refunded", "refunded", 599.0, "羊毛外套", 20, "SKU-WOOL-COAT"),
    )
    orders: list[dict[str, Any]] = []
    for index, (status, payment, amount, product, age_days, product_no) in enumerate(
        definitions, start=1,
    ):
        segment = digest[(index - 1) * 8:index * 8].upper()
        created_at = now - timedelta(days=age_days)
        order_no = f"MO-{segment}"
        order = {
            "id": f"ORD-{segment}",
            "order_no": order_no,
            "status": status,
            "payment_status": payment,
            "total_amount": amount,
            "currency": "CNY",
            "item_count": 1,
            "item_summary": product,
            "product_names": product,
            "items": [{
                "product_no": product_no,
                "product_name": product,
                "quantity": 1,
                "price": amount,
            }],
            "created_at": created_at.isoformat(),
            "paid_at": created_at.isoformat() if payment == "paid" else None,
            "estimated_delivery": (
                (created_at + timedelta(days=4)).isoformat()
                if status == "shipped" else None
            ),
        }
        orders.append(order)
    return orders


def _orders_for(user_id: str) -> list[dict[str, Any]]:
    """物化该用户订单到可变存储：种子首访写入，此后读改均走 _ORDERS。"""
    user_id = user_id.strip()
    if user_id and user_id not in _SEEDED_USERS:
        for order in _seed_orders(user_id):
            _ORDERS.setdefault((user_id, order["order_no"]), order)
        _SEEDED_USERS.add(user_id)
    orders = [o for (uid, _no), o in _ORDERS.items() if uid == user_id]
    orders.sort(key=lambda o: o["created_at"], reverse=True)
    return orders


def _find_order(user_id: str, order_no: str) -> dict[str, Any] | None:
    return next(
        (order for order in _orders_for(user_id)
         if order["order_no"].casefold() == order_no.casefold()
         or order["id"].casefold() == order_no.casefold()),
        None,
    )


def _money(value: float | int | str | Decimal) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _wallet_for(user_id: str, tenant_id: str) -> dict[str, Any]:
    """懒初始化演示钱包：余额 ¥5,000 起 + 初始入账流水（P2 资金闭环演示底座）。"""
    key = (user_id.strip(), tenant_id.strip())
    wallet = _WALLETS.get(key)
    if wallet is None:
        global _WALLET_LEDGER_SEQ
        _WALLET_LEDGER_SEQ += 1
        now = datetime.now(timezone.utc).isoformat()
        wallet = {
            "balance": _WALLET_INITIAL_BALANCE,
            "currency": "CNY",
            "updated_at": now,
            "ledger": [{
                "id": _WALLET_LEDGER_SEQ,
                "direction": "in",
                "amount": _WALLET_INITIAL_BALANCE,
                "balance_after": _WALLET_INITIAL_BALANCE,
                "kind": "topup",
                "ref_no": "",
                "memo": "演示初始资金",
                "created_at": now,
            }],
        }
        _WALLETS[key] = wallet
    return wallet


def _wallet_append(
    wallet: dict[str, Any], *, direction: str, amount: Decimal,
    kind: str, ref_no: str = "", memo: str = "",
) -> None:
    """追加流水并更新余额（调用方必须已持有 _STATE_LOCK）。"""
    global _WALLET_LEDGER_SEQ
    _WALLET_LEDGER_SEQ += 1
    balance_after = (
        wallet["balance"] + amount if direction == "in"
        else wallet["balance"] - amount
    )
    wallet["balance"] = balance_after
    wallet["updated_at"] = datetime.now(timezone.utc).isoformat()
    wallet["ledger"].insert(0, {
        "id": _WALLET_LEDGER_SEQ,
        "direction": direction,
        "amount": amount,
        "balance_after": balance_after,
        "kind": kind,
        "ref_no": ref_no,
        "memo": memo,
        "created_at": wallet["updated_at"],
    })


def _wallet_view(wallet: dict[str, Any]) -> dict[str, Any]:
    return {
        "balance": float(wallet["balance"]),
        "currency": wallet["currency"],
        "updated_at": wallet["updated_at"],
        "ledger": [
            {**entry, "amount": float(entry["amount"]),
             "balance_after": float(entry["balance_after"])}
            for entry in wallet["ledger"][:5]
        ],
    }


def _points_for(user_id: str, tenant_id: str) -> dict[str, Any]:
    """懒初始化积分档案：余额 8000 分 + 黄金档（近 90 天实付 3500 元）。

    数字口径与 docs/customer-service/kb/cs_policy/points-policy.md 及
    ai.cs_faq id=8（白银≥500/黄金≥2000/铂金≥8000 元）一致：3500 落黄金档；
    两笔 earn 流水（消费获取 + 订单完成奖励）与政策 §1 发放口径一致。
    """
    key = (user_id.strip(), tenant_id.strip())
    profile = _POINTS.get(key)
    if profile is None:
        now = datetime.now(timezone.utc).isoformat()
        profile = {
            "points": 8000,
            "tier": "gold",
            "spend_90d": 3500.0,
            "updated_at": now,
            "ledger": [
                {
                    "direction": "in", "points": 200, "balance_after": 8000,
                    "kind": "earn", "memo": "订单完成奖励", "created_at": now,
                },
                {
                    "direction": "in", "points": 7800, "balance_after": 7800,
                    "kind": "earn", "memo": "消费获取", "created_at": now,
                },
            ],
        }
        _POINTS[key] = profile
    return profile


def _coupons_for(user_id: str, tenant_id: str) -> list[dict[str, Any]]:
    """懒初始化券档案：满减/无门槛/折扣三类可用样本 + 一张已过期券。

    数字与 coupon-policy.md §1 示例及 ai.cs_faq id=23 口径一致（互为事实源）：
    一单仅可用一张券（stackable 恒 false）、秒杀/拼团价不参与券抵扣。
    """
    key = (user_id.strip(), tenant_id.strip())
    coupons = _COUPONS.get(key)
    if coupons is None:
        global _COUPON_SEQ
        now = datetime.now(timezone.utc)
        defs = (
            # (code, kind, name, threshold, value, max_discount, valid_until, status)
            ("SAVE30", "full_reduction", "满减券", 200.0, 30.0, None,
             now + timedelta(days=30), "available"),
            ("NEW5", "no_threshold", "无门槛券", 0.0, 5.0, None,
             now + timedelta(days=15), "available"),
            ("D90", "discount", "折扣券", 100.0, 0.9, 50.0,
             now + timedelta(days=45), "available"),
            ("OLD10", "full_reduction", "满减券", 100.0, 10.0, None,
             now - timedelta(days=5), "expired"),
        )
        coupons = []
        for code, kind, name, threshold, value, max_discount, valid_until, status in defs:
            _COUPON_SEQ += 1
            coupons.append({
                "coupon_id": f"C{_COUPON_SEQ:04d}",
                "coupon_code": code,
                "kind": kind,
                "name": name,
                "threshold_amount": threshold,
                "discount_value": value,
                "max_discount": max_discount,
                "stackable": False,
                "exclusions": "秒杀/拼团价商品" if kind != "no_threshold" else "",
                "valid_from": (valid_until - timedelta(days=90)).isoformat(),
                "valid_until": valid_until.isoformat(),
                "status": status,
            })
        _COUPONS[key] = coupons
        # 核销样本绑定「已发货」单（种子第 2 新）：该单购下时已用 NEW5，
        # 为 check 端点提供「一单仅可用一张」的确定性 not_stackable 样本
        _REDEMPTIONS[key] = [{
            "coupon_code": "NEW5",
            "order_no": _orders_for(user_id)[1]["order_no"],
            "redeemed_at": (now - timedelta(days=2)).isoformat(),
            "amount_applied": 5.0,
        }]
    return coupons


def _payments_for(
    user_id: str, tenant_id: str, order_no: str,
) -> dict[str, Any]:
    """按订单状态懒造确定性支付流水，覆盖成功、失败和重复成功三态。"""
    order = _find_order(user_id, order_no)
    if order is None:
        raise HTTPException(status_code=404, detail="order not found")

    key = (tenant_id.strip(), user_id.strip(), order["order_no"])
    records = _PAYMENT_RECORDS.get(key)
    if records is None:
        total = _money(order["total_amount"])
        now = datetime.now(timezone.utc)
        order_status = str(order.get("status") or "").lower()
        payment_status = str(order.get("payment_status") or "").lower()
        if order_status == "shipped":
            records = [
                {
                    "status": "success", "amount": total,
                    "paid_at": (now - timedelta(minutes=2)).isoformat(),
                    "kind": "alipay",
                },
                {
                    "status": "success", "amount": total,
                    "paid_at": (now - timedelta(minutes=1)).isoformat(),
                    "kind": "wechat_pay",
                },
            ]
        elif payment_status == "paid" or order_status in {"paid", "completed"}:
            records = [{
                "status": "success", "amount": total,
                "paid_at": (now - timedelta(minutes=2)).isoformat(),
                "kind": "alipay",
            }]
        elif order_status == "pending":
            records = [{
                "status": "failed", "amount": total,
                "paid_at": "", "kind": "bank_card",
            }]
        else:
            records = []
        _PAYMENT_RECORDS[key] = records

    for index, item in enumerate(records):
        item.setdefault(
            "payment_id",
            str(uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"cs-payment:{key[0]}:{key[1]}:{key[2]}:{index}",
            )),
        )

    successful = [item for item in records if item["status"] == "success"]
    total = _money(order["total_amount"])
    refund_candidate = bool(
        len(records) <= 20
        and len(successful) == 2
        and not any(item.get("refund_status") for item in records)
        and all(_money(item["amount"]) == total for item in successful)
    )
    return {
        "order_no": order["order_no"],
        "total_amount": float(total),
        "currency": "CNY",
        "payments": [
            {**item, "amount": float(item["amount"])} for item in records[:20]
        ],
        "payment_count": len(records),
        "payments_complete": len(records) <= 20,
        "success_count": len(successful),
        "duplicate_payment": len(successful) > 1,
        "refund_candidate": refund_candidate,
    }


def _coupon_check(
    user_id: str, tenant_id: str, order_no: str, coupon_code: str = "",
) -> dict[str, Any]:
    """券可用性校验：确定性规则覆盖三类不生效样本（政策 §6 四类原因）。

    规则优先级：显式券码过期 → 未满足门槛（实付合计、不含运费）→
    不可叠加（该订单已有核销记录：一单仅可用一张券）→ 可用。
    排除品类为政策/结算页校验职责，mock 不模拟命中样本。
    """
    order = _find_order(user_id, order_no)
    if order is None:
        raise HTTPException(status_code=404, detail="order not found")
    key = (user_id.strip(), tenant_id.strip())
    coupons = _coupons_for(user_id, tenant_id)
    redeemed_for_order = {
        item["coupon_code"] for item in _REDEMPTIONS.get(key, [])
        if item["order_no"] == order["order_no"]
    }

    def _result(coupon: dict[str, Any], applicable: bool,
                reason_code: str, reason_text: str) -> dict[str, Any]:
        return {
            "applicable": applicable,
            "reason_code": reason_code,
            "reason_text": reason_text,
            "order_no": order["order_no"],
            "order_total": float(order["total_amount"]),
            "coupon": {
                "coupon_code": coupon["coupon_code"], "kind": coupon["kind"],
                "name": coupon["name"],
                "threshold_amount": float(coupon["threshold_amount"]),
                "discount_value": float(coupon["discount_value"]),
            },
        }

    if coupon_code:
        target = next(
            (c for c in coupons
             if c["coupon_code"].casefold() == coupon_code.strip().casefold()),
            None,
        )
        if target is None:
            raise HTTPException(status_code=404, detail="coupon not found")
        if target["status"] == "expired":
            return _result(
                target, False, "expired",
                "该优惠券已过有效期，过期不可恢复、不可延期。",
            )
    else:
        target = next((c for c in coupons if c["status"] == "available"), None)
    total = float(order["total_amount"])
    threshold = float(target["threshold_amount"])
    if threshold > total:
        return _result(
            target, False, "threshold_not_met",
            f"未满足门槛：该券需满 {threshold:.0f} 元"
            f"（按商品实付合计、不含运费），当前订单 ¥{total:.2f}。",
        )
    if redeemed_for_order:
        return _result(
            target, False, "not_stackable",
            "该订单已使用过一张优惠券：一单仅可使用一张，不可叠加。",
        )
    if target["kind"] == "full_reduction":
        reason_text = (
            f"订单可使用 {target['name']}"
            f"（满 {threshold:.0f} 减 {float(target['discount_value']):.0f}）。"
        )
    elif target["kind"] == "discount":
        reason_text = (
            f"订单可使用 {target['name']}"
            f"（{float(target['discount_value']) * 10:.0f} 折，"
            f"最高减 {float(target['max_discount']):.0f} 元）。"
        )
    else:
        reason_text = f"订单可使用 {target['name']}（直接抵扣 {float(target['discount_value']):.0f} 元）。"
    return _result(target, True, "applicable", reason_text)


def _logistics(order: dict[str, Any]) -> dict[str, Any]:
    """由订单状态派生可重复验证的物流轨迹。"""
    status = order["status"]
    created = datetime.fromisoformat(order["created_at"])
    if status == "paid":
        return {
            "order_no": order["order_no"],
            "shipping_status": "not_shipped",
            "status_display": "已支付，尚未发货",
            "carrier": None,
            "tracking_no": None,
            "tracking_info": "订单已支付，仓库尚未发货。",
            "estimated_delivery": None,
            "events": [],
        }

    tracking_no = f"TRK{order['order_no'][-8:]}"
    events = [
        {
            "time": (created + timedelta(days=1)).isoformat(),
            "location": "杭州分拨中心",
            "description": "包裹已揽收",
        },
        {
            "time": (created + timedelta(days=2)).isoformat(),
            "location": "上海转运中心",
            "description": "包裹运输中",
        },
    ]
    shipping_status = "shipped"
    status_display = "已发货，运输中"
    tracking_info = "您的包裹正在运输中。"
    if status == "completed":
        events.append({
            "time": (created + timedelta(days=4)).isoformat(),
            "location": "收货地址",
            "description": "包裹已签收",
        })
        shipping_status = "delivered"
        status_display = "已签收"
        tracking_info = "您的包裹已签收。"
    elif status == "cancelled":
        shipping_status = "cancelled"
        status_display = "已取消"
        tracking_info = "订单已取消，不涉及物流配送。"
    elif status == "refunded":
        shipping_status = "delivered"
        status_display = "已退款"
        tracking_info = "订单已退款，退款金额已退回账户余额。"
    return {
        "order_no": order["order_no"],
        "shipping_status": shipping_status,
        "status_display": status_display,
        "carrier": "顺丰速运",
        "tracking_no": tracking_no,
        "tracking_info": tracking_info,
        "estimated_delivery": order.get("estimated_delivery"),
        "events": events,
    }


def _ok(data: Any) -> dict[str, Any]:
    return {"code": 0, "message": "ok", "data": data}


def _refund_view(request: dict[str, Any]) -> dict[str, Any]:
    private = {"user_id", "tenant_id", "conversation_id"}
    return {
        key: value for key, value in request.items()
        if not key.startswith("_") and key not in private
    }


def reset_mock_state() -> None:
    """重置售后内存状态，供隔离测试使用。"""
    with _STATE_LOCK:
        _REFUND_REQUESTS.clear()
        _ADDRESS_CHANGE_REQUESTS.clear()
        _OPERATIONS.clear()
        _ORDERS.clear()
        _WALLETS.clear()
        _POINTS.clear()
        _COUPONS.clear()
        _REDEMPTIONS.clear()
        _INVOICE_TITLES.clear()
        _INVOICES.clear()
        _URGE_REQUESTS.clear()
        _PAYMENT_RECORDS.clear()
        _SEEDED_USERS.clear()


def set_refund_status_for_test(request_id: str, status: str) -> None:
    """模拟订单系统人工审核结果；只供测试驱动，不开放 HTTP 管理端点。

    P5 起生产状态推进走 ``POST /business/refund-requests/{id}/review``
    （终态锁 + 审核记录 + approved 钱包回款）；本函数不记审核、不入账，
    仅供既有测试驱动状态，勿在新代码中使用。
    """
    if status not in {"reviewing", "approved", "rejected", "cancelled"}:
        raise ValueError("unsupported test status")
    with _STATE_LOCK:
        request = next(
            (item for item in _REFUND_REQUESTS.values()
             if item["request_id"] == request_id),
            None,
        )
        if request is None:
            raise KeyError(request_id)
        request["status"] = status
        request["status_display"] = {
            "reviewing": "审核中",
            "approved": "审核通过，等待订单系统处理",
            "rejected": "审核未通过",
            "cancelled": "已撤销",
        }[status]
        request["updated_at"] = datetime.now(timezone.utc).isoformat()


@app.get("/health")
def health():
    return {"status": "ok", "service": "business-mock", "time": time.time()}


@app.get("/business/orders")
def list_orders(
    user_id: str = Query("", min_length=1),
    x_internal_token: str | None = Header(default=None),
):
    _check_token(x_internal_token)
    orders = _orders_for(user_id.strip())[:20]
    return _ok({"orders": orders, "total": len(orders)})


@app.get("/business/orders/{order_no}")
def get_order(
    order_no: str,
    user_id: str = Query("", min_length=1),
    x_internal_token: str | None = Header(default=None),
):
    _check_token(x_internal_token)
    order = _find_order(user_id.strip(), order_no)
    if order is None:
        raise HTTPException(status_code=404, detail="order not found")
    return _ok({"order": order})


@app.get("/business/products/{product_no}/current-price")
def get_current_product_price(
    product_no: str,
    x_internal_token: str | None = Header(default=None),
):
    _check_token(x_internal_token)
    price = _PRODUCT_CURRENT_PRICES.get(product_no)
    if price is None:
        raise HTTPException(status_code=404, detail="current price not found")
    return _ok({
        "product_no": product_no,
        "price": str(price),
        "currency": "CNY",
    })


@app.get("/business/orders/{order_no}/logistics")
def get_order_logistics(
    order_no: str,
    user_id: str = Query("", min_length=1),
    x_internal_token: str | None = Header(default=None),
):
    _check_token(x_internal_token)
    order = _find_order(user_id.strip(), order_no)
    if order is None:
        raise HTTPException(status_code=404, detail="order not found")
    return _ok({"logistics": _logistics(order)})


@app.post("/business/logistics/{order_no}/change-address")
def change_address_in_transit(
    order_no: str,
    payload: AddressChangeInput,
    x_internal_token: str | None = Header(default=None),
):
    """只登记演示改址请求；不改订单、不调用真实承运方。"""
    _check_token(x_internal_token)
    user_id = payload.user_id.strip()
    tenant_id = payload.tenant_id.strip()
    normalized_order_no = order_no.strip()
    if normalized_order_no.casefold() != payload.order_no.strip().casefold():
        raise HTTPException(status_code=422, detail="order number mismatch")
    operation_payload = {
        "user_id": user_id,
        "tenant_id": tenant_id,
        "order_no": normalized_order_no,
        "expected_shipping_status": payload.expected_shipping_status.strip().lower(),
        "new_address": payload.new_address.strip(),
    }
    operation_key = ("address-change", payload.operation_key)
    with _STATE_LOCK:
        prior = _OPERATIONS.get(operation_key)
        if prior is not None:
            if prior["payload"] != operation_payload:
                raise HTTPException(status_code=409, detail="request key payload conflict")
            return _ok({"address_change_request": dict(prior["request"])})

        order = _find_order(user_id, normalized_order_no)
        if order is None:
            raise HTTPException(status_code=404, detail="order not found")
        logistics = _logistics(order)
        if (
            order.get("status") != "shipped"
            or logistics.get("shipping_status") != "shipped"
            or operation_payload["expected_shipping_status"] != "shipped"
        ):
            raise HTTPException(status_code=409, detail="order is not in transit")
        already_requested = any(
            item.get("user_id") == user_id
            and str(item.get("order_no") or "").casefold()
            == normalized_order_no.casefold()
            for item in _ADDRESS_CHANGE_REQUESTS.values()
        )
        if already_requested:
            raise HTTPException(status_code=409, detail="address change already requested")

        request = {
            "request_id": payload.operation_key,
            "order_no": order["order_no"],
            "new_address": payload.new_address.strip(),
            "status": "simulated",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "real_carrier_notified": False,
            "real_order_address_changed": False,
        }
        _ADDRESS_CHANGE_REQUESTS[(user_id, payload.operation_key)] = {
            **operation_payload,
            **request,
        }
        _OPERATIONS[operation_key] = {
            "payload": operation_payload,
            "request": dict(request),
        }
        return _ok({"address_change_request": request})
@app.post("/business/urge-requests")
def create_urge_request(
    payload: UrgeRequestInput,
    x_internal_token: str | None = Header(default=None),
):
    """模拟催办登记；同租户/用户/订单/类型 24 小时内只建一条。"""
    _check_token(x_internal_token)
    user_id = payload.user_id.strip()
    order = _find_order(user_id, payload.order_no.strip())
    if order is None:
        raise HTTPException(status_code=404, detail="order not found")

    logistics = _logistics(order)
    if payload.request_type == "shipping" and order["status"] != "paid":
        raise HTTPException(status_code=409, detail="order is not awaiting shipment")
    if payload.request_type == "delivery" and order["status"] != "shipped":
        raise HTTPException(status_code=409, detail="order is not in transit")

    events = logistics.get("events") or []
    last_update = (
        datetime.fromisoformat(events[-1]["time"])
        if events else datetime.fromisoformat(order["created_at"])
    )
    now = datetime.now(timezone.utc)
    if last_update.tzinfo is None:
        last_update = last_update.replace(tzinfo=timezone.utc)
    stale_days = max(0, int((now - last_update).total_seconds() // 86400))
    if stale_days > 5:
        return _ok({
            "handoff_required": True,
            "handoff_reason": "物流超过 5 天无更新，需人工核实",
            "logistics": logistics,
            "stale_days": stale_days,
        })

    key = (
        payload.tenant_id.strip(), user_id,
        order["order_no"].casefold(), payload.request_type,
    )
    with _STATE_LOCK:
        existing = _URGE_REQUESTS.get(key)
        if existing:
            created_at = datetime.fromisoformat(existing["created_at"])
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            if now - created_at < timedelta(hours=24):
                return _ok({
                    "urge_request": dict(existing),
                    "already_registered": True,
                    "handoff_required": False,
                    "logistics": logistics,
                    "stale_days": stale_days,
                })

        urge_request = {
            "request_id": f"URGE-{uuid.uuid4().hex[:12].upper()}",
            "order_no": order["order_no"],
            "request_type": payload.request_type,
            "status": "registered",
            "created_at": now.isoformat(),
            "conversation_id": payload.conversation_id,
        }
        _URGE_REQUESTS[key] = urge_request
    return _ok({
        "urge_request": dict(urge_request),
        "already_registered": False,
        "handoff_required": False,
        "logistics": logistics,
        "stale_days": stale_days,
    })


# ── P2 商城 Mock 账本：钱包 / 创建订单 / 支付 / 取消 ─────────────

@app.get("/business/wallet")
def get_wallet(
    user_id: str = Query("", min_length=1),
    tenant_id: str = Query("", min_length=1),
    x_internal_token: str | None = Header(default=None),
):
    _check_token(x_internal_token)
    with _STATE_LOCK:
        wallet = _wallet_for(user_id.strip(), tenant_id.strip())
        return _ok({"wallet": _wallet_view(wallet)})


@app.get("/business/points")
def get_points(
    user_id: str = Query("", min_length=1),
    tenant_id: str = Query("", min_length=1),
    x_internal_token: str | None = Header(default=None),
):
    _check_token(x_internal_token)
    with _STATE_LOCK:
        profile = _points_for(user_id.strip(), tenant_id.strip())
        return _ok({"points": profile})


@app.get("/business/payments")
def get_payments(
    user_id: str = Query("", min_length=1),
    tenant_id: str = Query("", min_length=1),
    order_no: str = Query("", min_length=1),
    x_internal_token: str | None = Header(default=None),
):
    """查询归属用户订单的支付流水，不提供交易或退款操作。"""
    _check_token(x_internal_token)
    with _STATE_LOCK:
        payment = _payments_for(
            user_id.strip(), tenant_id.strip(), order_no.strip(),
        )
        return _ok({"payment": payment})


@app.post("/business/payments/duplicate-refund-simulations")
def simulate_duplicate_payment_refund(
    payload: DuplicateRefundSimulationInput,
    x_internal_token: str | None = Header(default=None),
):
    """仅在 Mock 内标记最新一笔重复扣款已模拟退款，不动真实资金。"""
    _check_token(x_internal_token)
    user_id = payload.user_id.strip()
    tenant_id = payload.tenant_id.strip()
    order_no = payload.order_no.strip()
    action_id = str(payload.action_id)
    identity = (tenant_id, user_id, order_no)
    operation_key = ("duplicate-refund-simulation", action_id)

    with _STATE_LOCK:
        prior = _OPERATIONS.get(operation_key)
        if prior is not None:
            if prior.get("identity") != identity:
                raise HTTPException(status_code=404, detail="payment not found")
            return _ok({"refund": dict(prior["refund"])})

        payment = _payments_for(user_id, tenant_id, order_no)
        if payment["refund_candidate"] is not True:
            raise HTTPException(
                status_code=409,
                detail="duplicate payment is no longer eligible for simulation",
            )

        key = (tenant_id, user_id, payment["order_no"])
        records = _PAYMENT_RECORDS[key]
        successful = [
            (index, item) for index, item in enumerate(records)
            if item.get("status") == "success"
        ]
        if len(successful) != 2:
            raise HTTPException(status_code=409, detail="payment facts changed")
        payment_index, payment_record = max(
            successful, key=lambda pair: str(pair[1].get("paid_at") or ""),
        )
        refund_id = str(uuid.uuid5(
            uuid.NAMESPACE_URL, f"cs-refund-simulation:{action_id}",
        ))
        payment_record["refund_status"] = "simulated"
        payment_record["refund_id"] = refund_id
        refund = {
            "refund_id": refund_id,
            "action_id": action_id,
            "payment_id": payment_record["payment_id"],
            "order_no": payment["order_no"],
            "amount": payment["total_amount"],
            "currency": payment["currency"],
            "status": "simulated",
            "execution_mode": "simulation",
            "real_funds_affected": False,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        _OPERATIONS[operation_key] = {
            "identity": identity,
            "payment_index": payment_index,
            "refund": dict(refund),
        }
        return _ok({"refund": refund})


@app.get("/business/coupons")
def list_coupons(
    user_id: str = Query("", min_length=1),
    tenant_id: str = Query("", min_length=1),
    x_internal_token: str | None = Header(default=None),
):
    _check_token(x_internal_token)
    with _STATE_LOCK:
        coupons = _coupons_for(user_id.strip(), tenant_id.strip())
        return _ok({"coupons": [
            {**c,
             "threshold_amount": float(c["threshold_amount"]),
             "discount_value": float(c["discount_value"]),
             **({"max_discount": float(c["max_discount"])}
                if c["max_discount"] is not None else {})}
            for c in coupons[:10]
        ]})


class CouponCheckInput(BaseModel):
    user_id: str = Field(min_length=1, max_length=128)
    tenant_id: str = Field(min_length=1, max_length=128)
    order_no: str = Field(min_length=1, max_length=64)
    coupon_code: str = Field(default="", max_length=64)


@app.post("/business/coupons/check")
def check_coupon(
    payload: CouponCheckInput,
    x_internal_token: str | None = Header(default=None),
):
    _check_token(x_internal_token)
    with _STATE_LOCK:
        result = _coupon_check(
            payload.user_id.strip(), payload.tenant_id.strip(),
            payload.order_no.strip(), payload.coupon_code.strip(),
        )
        return _ok({"coupon_check": result})


def _invoice_titles_for(user_id: str, tenant_id: str) -> list[dict[str, Any]]:
    key = (tenant_id.strip(), user_id.strip())
    titles = _INVOICE_TITLES.get(key)
    if titles is None:
        suffix = hashlib.sha256("|".join(key).encode()).hexdigest()[:10].upper()
        titles = [
            {
                "title_id": f"MOCK-{suffix}-PERSONAL",
                "title_name": "个人抬头（模拟）",
                "title_type": "individual",
                "is_default": True,
                "status": "active",
            },
            {
                "title_id": f"MOCK-{suffix}-COMPANY",
                "title_name": "企业抬头（模拟）",
                "title_type": "company",
                "is_default": False,
                "status": "active",
            },
        ]
        _INVOICE_TITLES[key] = titles
    return [dict(item) for item in titles]


def _invoice_view(invoice: dict[str, Any]) -> dict[str, Any]:
    return {
        "invoice_id": invoice["invoice_id"],
        "invoice_no": invoice["invoice_no"],
        "order_no": invoice["order_no"],
        "title_id": invoice["title_id"],
        "title_name": invoice["title_name"],
        "title_type": invoice["title_type"],
        "invoice_type": invoice["invoice_type"],
        "status": invoice["status"],
        "real_invoice_issued": False,
        "tax_system_contacted": False,
        "created_at": invoice["created_at"],
    }


class InvoiceRequestInput(BaseModel):
    operation_key: str = Field(min_length=8, max_length=128)
    user_id: str = Field(min_length=1, max_length=128)
    tenant_id: str = Field(min_length=1, max_length=128)
    conversation_id: str = Field(min_length=1, max_length=128)
    order_no: str = Field(min_length=1, max_length=64)
    expected_order_status: str = Field(min_length=1, max_length=32)
    title_id: str = Field(min_length=1, max_length=128)
    invoice_type: str = Field(default="electronic", max_length=16)


@app.get("/business/invoice-titles")
def list_invoice_titles(
    user_id: str = Query("", min_length=1),
    tenant_id: str = Query("", min_length=1),
    x_internal_token: str | None = Header(default=None),
):
    _check_token(x_internal_token)
    with _STATE_LOCK:
        return _ok({"invoice_titles": _invoice_titles_for(
            user_id.strip(), tenant_id.strip(),
        )})


@app.get("/business/invoices")
def list_invoices(
    user_id: str = Query("", min_length=1),
    tenant_id: str = Query("", min_length=1),
    order_no: str = "",
    x_internal_token: str | None = Header(default=None),
):
    _check_token(x_internal_token)
    key = (tenant_id.strip(), user_id.strip())
    with _STATE_LOCK:
        invoices = [dict(item) for item in _INVOICES.get(key, [])]
        if order_no.strip():
            invoices = [
                item for item in invoices
                if item["order_no"].casefold() == order_no.strip().casefold()
            ]
        return _ok({"invoices": [_invoice_view(item) for item in invoices[:20]]})


@app.post("/business/invoices")
def create_mock_invoice(
    payload: InvoiceRequestInput,
    x_internal_token: str | None = Header(default=None),
):
    _check_token(x_internal_token)
    user_id = payload.user_id.strip()
    tenant_id = payload.tenant_id.strip()
    order_no = payload.order_no.strip()
    key = (tenant_id, user_id)
    operation_key = ("invoice-create", tenant_id, user_id, payload.operation_key)
    request_payload = {
        "user_id": user_id,
        "tenant_id": tenant_id,
        "conversation_id": payload.conversation_id.strip(),
        "order_no": order_no,
        "expected_order_status": payload.expected_order_status,
        "title_id": payload.title_id,
        "invoice_type": payload.invoice_type,
    }
    with _STATE_LOCK:
        prior = _OPERATIONS.get(operation_key)
        if prior is not None:
            if prior["payload"] != request_payload:
                raise HTTPException(status_code=409, detail="operation key payload conflict")
            return _ok({"invoice": _invoice_view(prior["invoice"]), "idempotent": True})

        order = _find_order(user_id, order_no)
        if order is None:
            raise HTTPException(status_code=404, detail="order not found")
        if (
            order["status"] != payload.expected_order_status
            or order["status"] not in {"paid", "shipped", "completed"}
        ):
            raise HTTPException(status_code=409, detail="order facts changed or ineligible")
        if payload.invoice_type != "electronic":
            raise HTTPException(status_code=422, detail="unsupported invoice type")
        title = next(
            (item for item in _invoice_titles_for(user_id, tenant_id)
             if item["title_id"] == payload.title_id and item["status"] == "active"),
            None,
        )
        if title is None:
            raise HTTPException(status_code=404, detail="invoice title not found")
        duplicate = next(
            (item for item in _INVOICES.get(key, [])
             if item["order_no"].casefold() == order_no.casefold()
             and item["title_id"] == title["title_id"]),
            None,
        )
        if duplicate is not None:
            raise HTTPException(status_code=409, detail="order already invoiced for title")

        invoice = {
            "invoice_id": str(uuid.uuid4()),
            "invoice_no": f"MOCK-INV-{uuid.uuid4().hex[:12].upper()}",
            "order_no": order["order_no"],
            "title_id": title["title_id"],
            "title_name": title["title_name"],
            "title_type": title["title_type"],
            "invoice_type": payload.invoice_type,
            "status": "simulated",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        _INVOICES.setdefault(key, []).append(dict(invoice))
        _OPERATIONS[operation_key] = {
            "payload": request_payload,
            "invoice": dict(invoice),
        }
        return _ok({"invoice": _invoice_view(invoice), "idempotent": False})


@app.post("/business/wallet/topup")
def wallet_topup(
    payload: WalletTopUpInput,
    x_internal_token: str | None = Header(default=None),
):
    _check_token(x_internal_token)
    amount = _money(payload.amount)
    with _STATE_LOCK:
        operation_key = ("wallet-topup", payload.operation_key)
        prior = _OPERATIONS.get(operation_key)
        wallet = _wallet_for(payload.user_id.strip(), payload.tenant_id.strip())
        if prior is not None:
            return _ok({"wallet": _wallet_view(prior["wallet"])})
        _wallet_append(
            wallet, direction="in", amount=amount, kind="topup",
            memo=payload.memo.strip() or "演示充值",
        )
        _OPERATIONS[operation_key] = {"wallet": {
            "balance": wallet["balance"], "currency": wallet["currency"],
            "updated_at": wallet["updated_at"],
            "ledger": [dict(e) for e in wallet["ledger"][:5]],
        }}
        return _ok({"wallet": _wallet_view(wallet)})


@app.post("/business/orders")
def create_order(
    payload: OrderCreateInput,
    x_internal_token: str | None = Header(default=None),
):
    _check_token(x_internal_token)
    with _STATE_LOCK:
        operation_key = ("order-create", payload.operation_key)
        prior = _OPERATIONS.get(operation_key)
        if prior is not None:
            return _ok({"order": prior["order"]})
        # 服务端计算总额，不信任调用方金额
        total = Decimal("0.00")
        items: list[dict[str, Any]] = []
        for item in payload.items:
            quantity = int(item.get("quantity", 0))
            price = _money(item.get("price", 0))
            if quantity <= 0 or price <= 0:
                raise HTTPException(status_code=422, detail="invalid order item")
            name = str(item.get("product_name", "")).strip()
            if not name:
                raise HTTPException(status_code=422, detail="order item missing product_name")
            total += price * quantity
            items.append({
                "product_no": str(item.get("product_no", "")),
                "product_id": str(item.get("product_id", "")),
                "product_name": name,
                "quantity": quantity,
                "price": float(price),
            })
        digest = uuid.uuid4().hex[:8].upper()
        now = datetime.now(timezone.utc).isoformat()
        order = {
            "id": f"ORD-{digest}",
            "order_no": f"MO-{digest}",
            "status": "pending",
            "payment_status": "unpaid",
            "total_amount": float(_money(total)),
            "currency": "CNY",
            "item_count": sum(i["quantity"] for i in items),
            "item_summary": "；".join(i["product_name"] for i in items),
            "product_names": "；".join(i["product_name"] for i in items),
            "items": items,
            "created_at": now,
            "paid_at": None,
            "estimated_delivery": None,
        }
        # 先物化种子再插入：防止「首访即创建」跳过种子装载
        _orders_for(payload.user_id.strip())
        _ORDERS[(payload.user_id.strip(), order["order_no"])] = order
        _OPERATIONS[operation_key] = {"order": dict(order)}
        return _ok({"order": order})


@app.post("/business/orders/{order_no}/pay")
def pay_order(
    order_no: str,
    payload: OrderPayInput,
    x_internal_token: str | None = Header(default=None),
):
    """支付 = 余额扣款：余额校验失败 409（余额不足），订单状态机 pending→paid。"""
    _check_token(x_internal_token)
    expected = _money(payload.expected_amount)
    with _STATE_LOCK:
        operation_key = ("order-pay", payload.operation_key)
        prior = _OPERATIONS.get(operation_key)
        if prior is not None:
            return _ok({"order": prior["order"], "wallet": _wallet_view(prior["wallet"])})
        order = _find_order(payload.user_id.strip(), order_no)
        if order is None:
            raise HTTPException(status_code=404, detail="order not found")
        if order["status"] != "pending" or order["payment_status"] != "unpaid":
            raise HTTPException(status_code=409, detail="order is not payable")
        if _money(order["total_amount"]) != expected:
            raise HTTPException(status_code=409, detail="order facts changed or ineligible")
        wallet = _wallet_for(payload.user_id.strip(), payload.tenant_id.strip())
        if wallet["balance"] < expected:
            raise HTTPException(status_code=409, detail="insufficient wallet balance")
        _wallet_append(
            wallet, direction="out", amount=expected, kind="pay",
            ref_no=order["order_no"], memo=f"订单支付 {order['order_no']}",
        )
        order["status"] = "paid"
        order["payment_status"] = "paid"
        paid_at = datetime.now(timezone.utc).isoformat()
        order["paid_at"] = paid_at
        payment_key = (
            payload.tenant_id.strip(),
            payload.user_id.strip(),
            order["order_no"],
        )
        _PAYMENT_RECORDS.setdefault(payment_key, []).append({
            "status": "success",
            "amount": float(expected),
            "paid_at": paid_at,
            "kind": "wallet",
        })
        _OPERATIONS[operation_key] = {
            "order": dict(order),
            "wallet": {
                "balance": wallet["balance"], "currency": wallet["currency"],
                "updated_at": wallet["updated_at"],
                "ledger": [dict(e) for e in wallet["ledger"][:5]],
            },
        }
        return _ok({"order": order, "wallet": _wallet_view(wallet)})


@app.post("/business/orders/{order_no}/cancel")
def cancel_order(
    order_no: str,
    payload: OrderCancelInput,
    x_internal_token: str | None = Header(default=None),
):
    """取消订单：pending 直接取消；paid 取消并退款回钱包（退款入账流水）。"""
    _check_token(x_internal_token)
    expected_amount = _money(payload.expected_amount)
    with _STATE_LOCK:
        operation_key = ("order-cancel", payload.operation_key)
        prior = _OPERATIONS.get(operation_key)
        if prior is not None:
            return _ok({"order": prior["order"], "wallet": _wallet_view(prior["wallet"])})
        order = _find_order(payload.user_id.strip(), order_no)
        if order is None:
            raise HTTPException(status_code=404, detail="order not found")
        if order["status"] != payload.expected_status:
            raise HTTPException(status_code=409, detail="order facts changed or ineligible")
        if order["status"] not in _CANCELLABLE_STATUSES:
            raise HTTPException(status_code=409, detail="order is not cancellable")
        if _money(order["total_amount"]) != expected_amount:
            raise HTTPException(status_code=409, detail="order facts changed or ineligible")
        wallet = _wallet_for(payload.user_id.strip(), payload.tenant_id.strip())
        if order["payment_status"] == "paid":
            refund_amount = _money(order["total_amount"])
            _wallet_append(
                wallet, direction="in", amount=refund_amount, kind="cancel_refund",
                ref_no=order["order_no"], memo=f"订单取消退款 {order['order_no']}",
            )
        order["status"] = "cancelled"
        order["payment_status"] = (
            "refunded" if payload.expected_status == "paid" else "unpaid"
        )
        _OPERATIONS[operation_key] = {
            "order": dict(order),
            "wallet": {
                "balance": wallet["balance"], "currency": wallet["currency"],
                "updated_at": wallet["updated_at"],
                "ledger": [dict(e) for e in wallet["ledger"][:5]],
            },
        }
        return _ok({"order": order, "wallet": _wallet_view(wallet)})


@app.post("/business/orders/{order_no}/partial-refund")
def create_price_match_refund(
    order_no: str,
    payload: PriceMatchRefundInput,
    x_internal_token: str | None = Header(default=None),
):
    """价保演示写入：只增加 business-mock 钱包流水，不触及真实资金。"""
    _check_token(x_internal_token)
    if _PRICE_MATCH_MODE != "mock" or _PRICE_MATCH_WINDOW_DAYS <= 0:
        raise HTTPException(status_code=409, detail="price match is disabled")
    if payload.window_days != _PRICE_MATCH_WINDOW_DAYS:
        raise HTTPException(status_code=409, detail="price match window mismatch")
    if order_no != payload.order_no:
        raise HTTPException(status_code=409, detail="order reference mismatch")
    try:
        expected_paid_at = datetime.fromisoformat(
            payload.expected_paid_at.replace("Z", "+00:00"),
        )
    except ValueError:
        raise HTTPException(status_code=422, detail="invalid paid_at") from None
    if expected_paid_at.tzinfo is None:
        raise HTTPException(status_code=422, detail="invalid paid_at")
    expected_paid_at = expected_paid_at.astimezone(timezone.utc)

    amount_expected = _money(payload.expected_amount)
    original_expected = _money(payload.expected_original_price)
    current_expected = _money(payload.expected_current_price)
    request_facts = (
        payload.user_id.strip(), payload.tenant_id.strip(), order_no,
        payload.expected_status, payload.expected_payment_status,
        expected_paid_at.isoformat(), payload.expected_currency,
        payload.product_no, str(original_expected), str(current_expected),
        str(amount_expected), payload.window_days,
    )
    operation_key = ("price-match", payload.operation_key)
    with _STATE_LOCK:
        previous = _OPERATIONS.get(operation_key)
        if previous is not None:
            if previous.get("request") != request_facts:
                raise HTTPException(status_code=409, detail="operation key conflict")
            return _ok(previous["data"])

        order = _find_order(payload.user_id.strip(), order_no)
        if order is None:
            raise HTTPException(status_code=404, detail="order not found")
        if (
            order.get("status") != payload.expected_status
            or order.get("payment_status") != payload.expected_payment_status
            or order.get("status") not in _ELIGIBLE_PRICE_MATCH_STATUSES
            or order.get("payment_status") != "paid"
            or order.get("currency") != "CNY"
            or payload.expected_currency != "CNY"
        ):
            raise HTTPException(status_code=409, detail="order facts changed or ineligible")

        items = order.get("items")
        if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
            raise HTTPException(status_code=409, detail="order items are ineligible")
        item = items[0]
        try:
            quantity = int(item.get("quantity", 0) or 0)
        except (TypeError, ValueError):
            quantity = 0
        if (
            quantity != 1
            or str(item.get("product_no") or "") != payload.product_no
        ):
            raise HTTPException(status_code=409, detail="product facts changed")
        if _money(item.get("price")) != original_expected:
            raise HTTPException(status_code=409, detail="original price changed")
        if _money(order.get("total_amount")) != original_expected:
            raise HTTPException(status_code=409, detail="order amount changed")

        try:
            paid_at = datetime.fromisoformat(
                str(order.get("paid_at") or "").replace("Z", "+00:00"),
            )
        except ValueError:
            raise HTTPException(status_code=409, detail="paid_at unavailable") from None
        if paid_at.tzinfo is None:
            raise HTTPException(status_code=409, detail="paid_at unavailable")
        paid_at = paid_at.astimezone(timezone.utc)
        age_seconds = (datetime.now(timezone.utc) - paid_at).total_seconds()
        if (
            paid_at != expected_paid_at
            or age_seconds < 0
            or age_seconds > payload.window_days * 86400
        ):
            raise HTTPException(status_code=409, detail="price match window changed")

        current_price = _PRODUCT_CURRENT_PRICES.get(payload.product_no)
        if current_price is None or _money(current_price) != current_expected:
            raise HTTPException(status_code=409, detail="current price changed")
        refund_amount = (original_expected - current_expected).quantize(Decimal("0.01"))
        if refund_amount <= 0 or refund_amount != amount_expected:
            raise HTTPException(status_code=409, detail="price difference changed")

        wallet = _wallet_for(payload.user_id.strip(), payload.tenant_id.strip())
        if any(
            entry.get("kind") == "price_match"
            and entry.get("ref_no") == order_no
            for entry in wallet.get("ledger", [])
        ):
            raise HTTPException(status_code=409, detail="price match already applied")
        _wallet_append(
            wallet,
            direction="in",
            amount=refund_amount,
            kind="price_match",
            ref_no=order_no,
            memo=f"价保差价模拟入账 {order_no}",
        )
        data = {
            "refund": {
                "amount": str(refund_amount),
                "currency": "CNY",
                "kind": "price_match",
                "status": "simulated",
                "real_funds_affected": False,
            },
            "wallet": _wallet_view(wallet),
        }
        _OPERATIONS[operation_key] = {
            "request": request_facts,
            "data": data,
        }
        return _ok(data)


@app.post("/business/refund-requests")
def create_refund_request(
    payload: RefundRequestInput,
    x_internal_token: str | None = Header(default=None),
):
    _check_token(x_internal_token)
    try:
        request_id = str(uuid.UUID(payload.request_id))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(status_code=422, detail="request_id must be a UUID") from None

    key = (payload.user_id, payload.tenant_id, request_id)
    operation_key = ("refund-create", request_id)
    operation = {
        "user_id": payload.user_id,
        "tenant_id": payload.tenant_id,
        "conversation_id": payload.conversation_id,
        "order_no": payload.order_no,
        "expected_order_status": payload.expected_order_status,
        "expected_amount": round(payload.expected_amount, 2),
        "reason": payload.reason.strip(),
        "policy_version": payload.policy_version,
    }
    create_payload = dict(operation)
    with _STATE_LOCK:
        prior_operation = _OPERATIONS.get(operation_key)
        if prior_operation is not None:
            if prior_operation["payload"] != create_payload:
                raise HTTPException(status_code=409, detail="request_id payload conflict")
            return _ok({"request": _refund_view(prior_operation["request"])})

        order = _find_order(payload.user_id, payload.order_no)
        if order is None:
            raise HTTPException(status_code=404, detail="order not found")
        if (
            order["status"] not in _REFUNDABLE_STATUSES
            or order["status"] != payload.expected_order_status
            or round(order["total_amount"], 2) != round(payload.expected_amount, 2)
        ):
            raise HTTPException(
                status_code=409, detail="order facts changed or ineligible",
            )
        created_at = datetime.fromisoformat(order["created_at"])
        if datetime.now(timezone.utc) - created_at > timedelta(
            days=_REFUND_WINDOW_DAYS,
        ):
            raise HTTPException(
                status_code=409, detail="refund policy window expired",
            )

        active = next(
            (item for item in _REFUND_REQUESTS.values()
             if item["user_id"] == payload.user_id
             and item["tenant_id"] == payload.tenant_id
             and item["order_no"] == order["order_no"]
             and item["status"] in _ACTIVE_REFUND_STATUSES),
            None,
        )
        if active is not None:
            raise HTTPException(status_code=409, detail="active refund request exists")

        now = datetime.now(timezone.utc).isoformat()
        request = {
            "request_id": request_id,
            "request_no": f"RA-{request_id.replace('-', '')[:12].upper()}",
            "order_no": order["order_no"],
            "amount": round(order["total_amount"], 2),
            "currency": "CNY",
            "reason": payload.reason.strip(),
            "status": "pending_review",
            "status_display": "等待人工审核",
            "policy_version": payload.policy_version,
            "created_at": now,
            "updated_at": now,
            "user_id": payload.user_id,
            "tenant_id": payload.tenant_id,
            "conversation_id": payload.conversation_id,
        }
        _REFUND_REQUESTS[key] = request
        _OPERATIONS[operation_key] = {
            "payload": create_payload,
            "request": dict(request),
        }
        return _ok({"request": _refund_view(request)})


@app.get("/business/refund-requests")
def list_refund_requests(
    user_id: str = Query("", min_length=1),
    tenant_id: str = Query("", min_length=1),
    order_no: str | None = None,
    limit: int = Query(20, ge=1, le=100),
    x_internal_token: str | None = Header(default=None),
):
    _check_token(x_internal_token)
    with _STATE_LOCK:
        requests = [
            _refund_view(item)
            for item in _REFUND_REQUESTS.values()
            if item["user_id"] == user_id.strip()
            and item["tenant_id"] == tenant_id.strip()
            and (order_no is None or item["order_no"] == order_no)
        ]
    requests.sort(key=lambda item: item["created_at"], reverse=True)
    requests = requests[:limit]
    return _ok({"requests": requests, "total": len(requests)})


@app.get("/business/refund-requests/{request_id}")
def get_refund_request(
    request_id: str,
    user_id: str = Query("", min_length=1),
    tenant_id: str = Query("", min_length=1),
    x_internal_token: str | None = Header(default=None),
):
    _check_token(x_internal_token)
    with _STATE_LOCK:
        request = next(
            (item for item in _REFUND_REQUESTS.values()
             if item["request_id"] == request_id
             and item["user_id"] == user_id.strip()
             and item["tenant_id"] == tenant_id.strip()),
            None,
        )
        if request is None:
            raise HTTPException(status_code=404, detail="refund request not found")
        return _ok({"request": _refund_view(request)})


@app.patch("/business/refund-requests/{request_id}/reason")
def update_refund_reason(
    request_id: str,
    payload: RefundReasonUpdateInput,
    x_internal_token: str | None = Header(default=None),
):
    _check_token(x_internal_token)
    try:
        operation_id = str(uuid.UUID(payload.operation_id))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(status_code=422, detail="operation_id must be a UUID") from None

    key = (request_id, operation_id)
    operation_payload = {
        "user_id": payload.user_id,
        "tenant_id": payload.tenant_id,
        "reason": payload.reason.strip(),
    }
    with _STATE_LOCK:
        prior_operation = _OPERATIONS.get(key)
        if prior_operation is not None:
            if prior_operation["payload"] != operation_payload:
                raise HTTPException(status_code=409, detail="operation_id payload conflict")
            return _ok({"request": _refund_view(prior_operation["request"])})

        request = next(
            (item for item in _REFUND_REQUESTS.values()
             if item["request_id"] == request_id
             and item["user_id"] == payload.user_id
             and item["tenant_id"] == payload.tenant_id),
            None,
        )
        if request is None:
            raise HTTPException(status_code=404, detail="refund request not found")
        if request["status"] not in {"pending_review", "reviewing"}:
            raise HTTPException(status_code=409, detail="refund request is no longer editable")

        request["reason"] = payload.reason.strip()
        request["updated_at"] = datetime.now(timezone.utc).isoformat()
        snapshot = dict(request)
        _OPERATIONS[key] = {"payload": operation_payload, "request": snapshot}
        return _ok({"request": _refund_view(snapshot)})


# ── cs-workstation-v2 P5：退款审核（正式状态推进，替代测试钩子） ────

_REFUND_REVIEW_DISPLAY = {
    "approved": "审核通过，退款已退回钱包",
    "rejected": "审核未通过",
}


class RefundReviewInput(BaseModel):
    decision: str = Field(pattern="^(approved|rejected)$")
    reviewer_id: str = Field(min_length=1, max_length=64)
    opinion: str = Field(min_length=1, max_length=500)
    operation_id: str  # UUID 幂等键


@app.get("/business/refunds/review-queue")
def refund_review_queue(
    tenant_id: str = Query(min_length=1),
    status: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    x_internal_token: str | None = Header(default=None),
):
    """运营审核队列：按租户列出退款申请（supervisor 视角，不限 user）。"""
    _check_token(x_internal_token)
    with _STATE_LOCK:
        rows = []
        for item in _REFUND_REQUESTS.values():
            if item["tenant_id"] != tenant_id.strip():
                continue
            if status is not None and item["status"] != status:
                continue
            # 运营/用户侧查询需要归属字段；_refund_view 的对外契约不含它们
            view = _refund_view(item)
            view["user_id"] = item["user_id"]
            view["tenant_id"] = item["tenant_id"]
            view["conversation_id"] = item["conversation_id"]
            rows.append(view)
    rows.sort(key=lambda item: item["created_at"])
    rows = rows[:limit]
    return _ok({"requests": rows, "total": len(rows)})


@app.post("/business/refund-requests/{request_id}/review")
def review_refund_request(
    request_id: str,
    payload: RefundReviewInput,
    x_internal_token: str | None = Header(default=None),
):
    """审核终态推进：approved 回款钱包；终态不可回退；operation_id 幂等。"""
    _check_token(x_internal_token)
    try:
        operation_id = str(uuid.UUID(payload.operation_id))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(status_code=422, detail="operation_id must be a UUID") from None
    opinion = payload.opinion.strip()
    if not opinion:
        raise HTTPException(status_code=422, detail="opinion is required")

    operation_payload = {
        "decision": payload.decision,
        "reviewer_id": payload.reviewer_id.strip(),
        "opinion": opinion,
    }
    with _STATE_LOCK:
        request = next(
            (item for item in _REFUND_REQUESTS.values()
             if item["request_id"] == request_id),
            None,
        )
        if request is None:
            raise HTTPException(status_code=404, detail="refund request not found")

        operation_key = ("refund-review", request_id, operation_id)
        prior_operation = _OPERATIONS.get(operation_key)
        if prior_operation is not None:
            if prior_operation["payload"] != operation_payload:
                raise HTTPException(status_code=409, detail="operation payload conflict")
            return _ok({"request": _refund_view(request)})

        if request["status"] in {"approved", "rejected", "cancelled"}:
            raise HTTPException(
                status_code=409,
                detail=f"refund request already in final state: {request['status']}",
            )

        request["status"] = payload.decision
        request["status_display"] = _REFUND_REVIEW_DISPLAY[payload.decision]
        request["review"] = {
            "reviewer_id": payload.reviewer_id.strip(),
            "opinion": opinion,
            "decision": payload.decision,
            "reviewed_at": datetime.now(timezone.utc).isoformat(),
        }
        request["updated_at"] = datetime.now(timezone.utc).isoformat()
        _OPERATIONS[operation_key] = {"payload": dict(operation_payload)}

        if payload.decision == "approved":
            # 审核通过 = 订单系统执行退款回款（对齐取消订单的 cancel_refund 语义）；
            # 钱包归属退款申请人（user/tenant），与审核人无关
            wallet = _wallet_for(request["user_id"], request["tenant_id"])
            _wallet_append(
                wallet,
                direction="in",
                amount=Decimal(str(request["amount"])).quantize(Decimal("0.01")),
                kind="refund",
                ref_no=request["request_no"],
                memo=f"退款审核通过 {request['order_no']}（{request['request_no']}）",
            )
        return _ok({"request": _refund_view(request)})
