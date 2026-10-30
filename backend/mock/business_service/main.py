"""有状态 business-service Mock：为客服流程提供确定性的订单和售后数据。"""
from __future__ import annotations

import hashlib
import os
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

app = FastAPI(title="business-service mock", docs_url=None, redoc_url=None)

_INTERNAL_TOKEN = os.getenv("INTERNAL_API_TOKEN", "")
_STATE_LOCK = threading.RLock()
_REFUND_REQUESTS: dict[tuple[str, str, str], dict[str, Any]] = {}
_OPERATIONS: dict[tuple[str, str], dict[str, Any]] = {}
# P2 商城 Mock 账本：订单可变状态与钱包（金额一律 Decimal，禁浮点累加）
_ORDERS: dict[tuple[str, str], dict[str, Any]] = {}
_WALLETS: dict[tuple[str, str], dict[str, Any]] = {}
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
_REFUND_WINDOW_DAYS = int(os.getenv("CS_REFUND_WINDOW_DAYS", "30"))
# 取消订单资格：未付款直接取消；已支付待发货走客服提案（退款回钱包）
_CANCELLABLE_STATUSES = frozenset({"pending", "paid"})
_WALLET_INITIAL_BALANCE = Decimal(
    os.getenv("MOCK_WALLET_INITIAL_BALANCE", "5000.00"),
)


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


class RefundReasonUpdateInput(BaseModel):
    operation_id: str
    user_id: str = Field(min_length=1, max_length=128)
    tenant_id: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=1, max_length=500)


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
        ("paid", "paid", 1299.0, "无线耳机", 7),
        ("shipped", "paid", 699.0, "蓝牙音箱", 3),
        ("completed", "paid", 299.0, "有线耳机", 45),
        # P2 商城域：种子扩至五态（pending/cancelled/refunded 补齐状态机演示）
        ("pending", "unpaid", 358.0, "便携榨汁杯 400ml", 0),
        ("cancelled", "unpaid", 199.0, "户外防水音箱 mini", 12),
        ("refunded", "refunded", 599.0, "羊毛外套", 20),
    )
    orders: list[dict[str, Any]] = []
    for index, (status, payment, amount, product, age_days) in enumerate(
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
            "item_count": 1,
            "item_summary": product,
            "product_names": product,
            "items": [{"product_name": product, "quantity": 1, "price": amount}],
            "created_at": created_at.isoformat(),
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
        _OPERATIONS.clear()
        _ORDERS.clear()
        _WALLETS.clear()
        _SEEDED_USERS.clear()


def set_refund_status_for_test(request_id: str, status: str) -> None:
    """模拟订单系统人工审核结果；只供测试驱动，不开放 HTTP 管理端点。"""
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
            "item_count": sum(i["quantity"] for i in items),
            "item_summary": "；".join(i["product_name"] for i in items),
            "product_names": "；".join(i["product_name"] for i in items),
            "items": items,
            "created_at": now,
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
