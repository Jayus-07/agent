"""客服用户侧档案与近期订单只读接口。

所有查询都使用网关注入的当前身份，不接受客户端传入的 user_id。
订单和商品数据复用客服域已有服务及其演示沙盒映射。
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.app.api.identity import Identity, require_identity
from backend.customer_service.errors import AccountNotFoundError, DatabaseError
from backend.customer_service.service.account_service import get_account_service
from backend.customer_service.service.demo_mode import is_demo_mode
from backend.customer_service.service.order_service import get_order_service

router = APIRouter(prefix="/cs", tags=["智能客服-用户资料"])


class CustomerProfileDTO(BaseModel):
    display_name: str
    gender: Optional[str] = None
    level: Optional[str] = None
    register_time: Optional[str] = None


class CustomerOrderDTO(BaseModel):
    id: str
    order_no: str
    total_amount: Optional[float] = None
    status: str
    payment_status: Optional[str] = None
    created_at: Optional[str] = None
    product_summary: str = ""


class CustomerContextResponse(BaseModel):
    customer: CustomerProfileDTO
    orders: list[CustomerOrderDTO]
    demo_mode: bool
    mock_data: bool = False
    profile_unavailable: bool = False


def _uses_mock_business_gateway() -> bool:
    from backend.config.customer_service import CS_BUSINESS_GATEWAY_MODE
    from backend.config.messaging import BUSINESS_SERVICE_URL

    host = urlparse(BUSINESS_SERVICE_URL).hostname or ""
    return CS_BUSINESS_GATEWAY_MODE == "http" and "business-mock" in host


def _as_text(value: object) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def _order_dto(row: dict) -> CustomerOrderDTO:
    amount = row.get("total_amount")
    return CustomerOrderDTO(
        id=str(row.get("id") or row.get("order_no") or ""),
        order_no=str(row.get("order_no") or row.get("id") or ""),
        total_amount=float(amount) if amount is not None else None,
        status=str(row.get("status") or "unknown"),
        payment_status=(
            str(row["payment_status"]) if row.get("payment_status") else None
        ),
        created_at=_as_text(row.get("created_at")),
        product_summary=str(
            row.get("product_names") or row.get("item_summary") or ""
        ),
    )


@router.get("/customer-context", response_model=CustomerContextResponse)
def get_my_customer_context(
    identity: Identity = Depends(require_identity),
) -> CustomerContextResponse:
    """客服独立页展示当前登录用户的档案和近期订单。"""
    if not identity.user_id:
        raise HTTPException(status_code=403, detail="缺少可信用户身份")

    profile_unavailable = False
    try:
        account = get_account_service().query_account(identity.user_id)
        customer = CustomerProfileDTO(
            display_name=account.name or identity.user_name or "已登录用户",
            gender=account.gender,
            level=account.level,
            register_time=account.register_time,
        )
    except AccountNotFoundError:
        # 部分账号尚未关联业务客户档案；仍显示可信身份名称，不伪造客户字段。
        customer = CustomerProfileDTO(
            display_name=identity.user_name or "已登录用户",
        )
    except DatabaseError:
        # 业务订单可能由独立网关提供；档案库短暂不可用时仍查询订单，
        # 并在响应中标出档案状态，避免把“没读到”显示成“没有资料”。
        profile_unavailable = True
        customer = CustomerProfileDTO(
            display_name=identity.user_name or "已登录用户",
        )

    try:
        order_service = get_order_service()
        orders = order_service.list_recent_orders_with_products(
            identity.user_id, limit=8,
        )
        if not orders:
            # HTTP business-service 契约在列表中提供 item_summary；本地沙盒
            # 则由上面的查询联表返回商品名。
            orders = order_service.query_orders(
                user_id=identity.user_id, query_type="list",
            ).orders[:8]
    except DatabaseError as exc:
        raise HTTPException(
            status_code=503,
            detail="客户资料和订单暂时无法读取",
        ) from exc

    return CustomerContextResponse(
        customer=customer,
        orders=[_order_dto(order) for order in orders],
        demo_mode=is_demo_mode(),
        mock_data=_uses_mock_business_gateway(),
        profile_unavailable=profile_unavailable,
    )
