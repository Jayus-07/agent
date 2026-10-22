"""有状态 business-service Mock：为客服流程提供确定性的订单和售后数据。"""
from __future__ import annotations

import hashlib
import os
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
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

_REFUNDABLE_STATUSES = frozenset(
    value.strip()
    for value in os.getenv(
        "CS_REFUNDABLE_STATUSES", "paid,shipped,completed",
    ).split(",")
    if value.strip()
)
_ACTIVE_REFUND_STATUSES = frozenset({"pending_review", "reviewing", "approved"})
_REFUND_WINDOW_DAYS = int(os.getenv("CS_REFUND_WINDOW_DAYS", "30"))


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
    """为每个用户生成稳定订单号、近期可退耳机和物流样例。"""
    digest = hashlib.sha256(f"orders:{user_id}".encode()).hexdigest()
    now = datetime.now(timezone.utc)
    definitions = (
        ("paid", "paid", 1299.0, "无线耳机", 7),
        ("shipped", "paid", 699.0, "蓝牙音箱", 3),
        ("completed", "paid", 299.0, "有线耳机", 45),
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


def _find_order(user_id: str, order_no: str) -> dict[str, Any] | None:
    return next(
        (order for order in _seed_orders(user_id)
         if order["order_no"].casefold() == order_no.casefold()
         or order["id"].casefold() == order_no.casefold()),
        None,
    )


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
    orders = _seed_orders(user_id.strip())
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
