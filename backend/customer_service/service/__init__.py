"""customer_service/service/ — 客服业务服务层

只读业务查询服务 + 业务操作服务（Phase 4 写操作模拟）。
所有查询必须绑定 user_id (WHERE customer_id = %(user_id)s)。
"""
from backend.customer_service.service.account_action_service import AccountActionService, get_account_action_service
from backend.customer_service.service.account_service import AccountService, get_account_service
from backend.customer_service.service.after_sales_service import AfterSalesService, get_after_sales_service
from backend.customer_service.service.complaint_service import ComplaintService, get_complaint_service
from backend.customer_service.service.logistics_service import LogisticsService, get_logistics_service
from backend.customer_service.service.order_service import OrderService, get_order_service
from backend.customer_service.service.refund_service import RefundService, get_refund_service

__all__ = [
    "OrderService", "get_order_service",
    "LogisticsService", "get_logistics_service",
    "AccountService", "get_account_service",
    "RefundService", "get_refund_service",
    "AfterSalesService", "get_after_sales_service",
    "AccountActionService", "get_account_action_service",
    "ComplaintService", "get_complaint_service",
]
