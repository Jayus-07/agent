"""商城最小域 API（cs-workstation-v2 P2，Mock 优先）。

用户端商城三页与资金闭环的数据面：商品只读映射（product.products）、
演示钱包账本（余额/充值/流水）、订单状态机操作（创建/支付/未付款取消）。
所有端点使用网关注入的当前身份，不接受客户端传入的 user_id；
金额服务端计价/对账，写操作走 operation_key 幂等。

已支付订单的取消必须走客服提案-确认链路（不开放直接取消端点）。
"""
from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from backend.app.api.identity import Identity, require_identity
from backend.customer_service.service.mall_service import MallError, MallService

router = APIRouter(prefix="/mall", tags=["商城-演示"])

_SERVICE = MallService()

_ORDER_STATUS_ZH = {
    "pending": "待付款",
    "paid": "待发货",
    "shipped": "已发货",
    "completed": "已完成",
    "cancelled": "已取消",
    "refunded": "已退款",
}


class CartItemInput(BaseModel):
    product_id: str = Field(min_length=1, max_length=64)
    quantity: int = Field(ge=1, le=99)


class OrderCreateInput(BaseModel):
    items: list[CartItemInput] = Field(min_length=1, max_length=20)
    operation_key: str = Field(default="", max_length=128)


class OrderPayInput(BaseModel):
    expected_amount: float = Field(gt=0)
    operation_key: str = Field(default="", max_length=128)


class TopUpInput(BaseModel):
    amount: float = Field(gt=0, le=50000)
    operation_key: str = Field(default="", max_length=128)


def _require_user(identity: Identity) -> str:
    if not identity.user_id:
        raise HTTPException(status_code=403, detail="缺少可信用户身份")
    return identity.user_id


def _http_error(exc: MallError) -> HTTPException:
    status = {
        "insufficient_balance": 409,
        "conflict": 409,
        "not_found": 404,
        "invalid_amount": 422,
        "invalid_items": 422,
        "gateway_error": 502,
    }.get(exc.code, 500)
    return HTTPException(status_code=status, detail=exc.user_message or str(exc))


@router.get("/products")
def list_products(
    category: str | None = None,
    q: str | None = Query(None, max_length=64),
    limit: int = Query(20, ge=1, le=56),
    _identity: Identity = Depends(require_identity),
):
    """商品中心列表（product.products 只读映射，含库存快照）。"""
    try:
        return {"products": _SERVICE.list_products(
            category=category, q=q, limit=limit,
        )}
    except MallError as exc:
        raise _http_error(exc) from exc


@router.get("/products/{product_id}")
def get_product(
    product_id: str,
    _identity: Identity = Depends(require_identity),
):
    product = _SERVICE.get_product(product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="商品不存在或已下架")
    return {"product": product}


@router.get("/wallet")
def get_wallet(identity: Identity = Depends(require_identity)):
    """余额 + 最近流水（资金闭环演示底座）。"""
    user_id = _require_user(identity)
    try:
        return {"wallet": _SERVICE.get_wallet(user_id, identity.tenant_id)}
    except MallError as exc:
        raise _http_error(exc) from exc


@router.post("/wallet/topup")
def top_up(
    payload: TopUpInput,
    identity: Identity = Depends(require_identity),
):
    """演示充值（生产支付对接另立项）。"""
    user_id = _require_user(identity)
    operation_key = payload.operation_key or str(uuid4())
    try:
        wallet = _SERVICE.top_up(
            user_id, identity.tenant_id, payload.amount, operation_key,
        )
        return {"wallet": wallet}
    except MallError as exc:
        raise _http_error(exc) from exc


@router.get("/orders")
def list_my_orders(
    identity: Identity = Depends(require_identity),
    limit: int = Query(20, ge=1, le=50),
):
    """我的订单（全状态，含商品名）。"""
    user_id = _require_user(identity)
    from backend.customer_service.service.order_service import get_order_service

    try:
        orders = get_order_service().list_recent_orders_with_products(
            user_id, limit=limit,
        )
        if not orders:
            orders = get_order_service().query_orders(
                user_id=user_id, query_type="list",
            ).orders[:limit]
    except Exception as exc:  # noqa: BLE001 — 统一映射 503
        raise HTTPException(status_code=503, detail="订单暂时无法读取") from exc
    return {"orders": [
        {
            "order_no": str(o.get("order_no") or o.get("id") or ""),
            "total_amount": (
                float(o["total_amount"]) if o.get("total_amount") is not None else None
            ),
            "status": str(o.get("status") or "unknown"),
            "status_zh": _ORDER_STATUS_ZH.get(str(o.get("status")), "未知"),
            "payment_status": (
                str(o["payment_status"]) if o.get("payment_status") else None
            ),
            "created_at": str(o.get("created_at") or ""),
            "product_summary": str(
                o.get("product_names") or o.get("item_summary") or ""
            ),
        }
        for o in orders
    ]}


@router.post("/orders")
def create_order(
    payload: OrderCreateInput,
    identity: Identity = Depends(require_identity),
):
    """购物车结算 → 生成待付款订单（服务端计价）。"""
    user_id = _require_user(identity)
    operation_key = payload.operation_key or str(uuid4())
    try:
        order = _SERVICE.create_order(
            user_id, identity.tenant_id,
            [item.model_dump() for item in payload.items], operation_key,
        )
        return {"order": order}
    except MallError as exc:
        raise _http_error(exc) from exc


@router.post("/orders/{order_no}/pay")
def pay_order(
    order_no: str,
    payload: OrderPayInput,
    identity: Identity = Depends(require_identity),
):
    """去支付：余额校验 + 扣款 + 流水；订单 pending→paid。"""
    user_id = _require_user(identity)
    operation_key = payload.operation_key or str(uuid4())
    try:
        result = _SERVICE.pay_order(
            user_id, identity.tenant_id, order_no,
            payload.expected_amount, operation_key,
        )
        return result
    except MallError as exc:
        raise _http_error(exc) from exc


@router.get("/orders/{order_no}/logistics")
def get_order_logistics(
    order_no: str,
    identity: Identity = Depends(require_identity),
):
    """物流轨迹弹窗（sandbox 按状态派生演示轨迹；http 走业务契约）。"""
    user_id = _require_user(identity)
    try:
        return {"logistics": _SERVICE.get_logistics(
            user_id, identity.tenant_id, order_no,
        )}
    except MallError as exc:
        raise _http_error(exc) from exc


@router.post("/orders/{order_no}/cancel")
def cancel_unpaid_order(
    order_no: str,
    identity: Identity = Depends(require_identity),
):
    """未付款订单直接取消；已支付订单须走客服提案-确认链路。"""
    user_id = _require_user(identity)
    try:
        result = _SERVICE.cancel_order(
            user_id, identity.tenant_id, order_no,
            operation_key=str(uuid4()), via_confirmation=False,
        )
        return result
    except MallError as exc:
        if exc.code == "conflict":
            raise HTTPException(
                status_code=409,
                detail="已支付订单请通过客服会话申请取消（生成提案后确认）",
            ) from exc
        raise _http_error(exc) from exc
