"""customer_service/service/after_sales_service.py — 售后服务

退货 / 换货的资格检查、proposal 构建、模拟执行。
Phase 4: 不实际写 DB。
"""
from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from backend.customer_service.action import (
    ActionProposal,
    ActionType,
    AgentActionRecord,
)
from backend.customer_service.errors import (
    DatabaseError,
    OrderNotEligibleError,
    OrderNotFoundError,
)
from backend.customer_service.risk import RiskLevel
from backend.shared.logger import logger


@dataclass
class AfterSalesEligibility:
    eligible: bool
    order_id: str
    order_no: str
    amount: float
    status: str
    reason: str = ""


class AfterSalesService:
    ELIGIBLE_STATUSES = {"shipped", "completed"}
    WINDOW_DAYS = 7

    def check_return_eligibility(
        self, user_id: str, order_id: str
    ) -> AfterSalesEligibility:
        order = self._get_order(user_id, order_id)
        status = str(order.get("status", ""))

        if status not in self.ELIGIBLE_STATUSES:
            raise OrderNotEligibleError(
                f"订单状态 '{status}' 不允许退货，仅支持: {', '.join(sorted(self.ELIGIBLE_STATUSES))}"
            )

        return AfterSalesEligibility(
            eligible=True,
            order_id=str(order["id"]),
            order_no=str(order.get("order_no", "")),
            amount=float(order.get("total_amount", 0)),
            status=status,
        )

    def check_exchange_eligibility(
        self, user_id: str, order_id: str
    ) -> AfterSalesEligibility:
        order = self._get_order(user_id, order_id)
        status = str(order.get("status", ""))

        if status not in self.ELIGIBLE_STATUSES:
            raise OrderNotEligibleError(
                f"订单状态 '{status}' 不允许换货，仅支持: {', '.join(sorted(self.ELIGIBLE_STATUSES))}"
            )

        return AfterSalesEligibility(
            eligible=True,
            order_id=str(order["id"]),
            order_no=str(order.get("order_no", "")),
            amount=float(order.get("total_amount", 0)),
            status=status,
        )

    def build_return_proposal(
        self, user_id: str, order_id: str, reason: str = ""
    ) -> ActionProposal:
        eligibility = self.check_return_eligibility(user_id, order_id)

        proposal_text = (
            f"## 退货申请\n\n"
            f"**订单号:** `{eligibility.order_no}`\n"
            f"**订单金额:** ¥{eligibility.amount:.2f}\n"
            f"**当前状态:** {eligibility.status}\n"
        )
        if reason:
            proposal_text += f"**退货原因:** {reason}\n"
        proposal_text += (
            "\n退货审核通过后，请在 7 天内寄回商品。\n\n"
            "请确认是否提交退货申请？（回复「确认」提交 / 「取消」放弃）"
        )

        return ActionProposal(
            action_type=ActionType.RETURN_REQUEST,
            target_type="order",
            target_id=eligibility.order_id,
            risk_level=RiskLevel.HIGH,
            proposal_text=proposal_text,
            before_state={
                "order_id": eligibility.order_id,
                "status": eligibility.status,
            },
            after_state={
                "order_id": eligibility.order_id,
                "status": "return_requested",
            },
        )

    def build_exchange_proposal(
        self, user_id: str, order_id: str, reason: str = ""
    ) -> ActionProposal:
        eligibility = self.check_exchange_eligibility(user_id, order_id)

        proposal_text = (
            f"## 换货申请\n\n"
            f"**订单号:** `{eligibility.order_no}`\n"
            f"**订单金额:** ¥{eligibility.amount:.2f}\n"
            f"**当前状态:** {eligibility.status}\n"
        )
        if reason:
            proposal_text += f"**换货原因:** {reason}\n"
        proposal_text += (
            "\n换货审核通过后，请在 7 天内寄回原商品。\n\n"
            "请确认是否提交换货申请？（回复「确认」提交 / 「取消」放弃）"
        )

        return ActionProposal(
            action_type=ActionType.EXCHANGE_REQUEST,
            target_type="order",
            target_id=eligibility.order_id,
            risk_level=RiskLevel.MEDIUM,
            proposal_text=proposal_text,
            before_state={
                "order_id": eligibility.order_id,
                "status": eligibility.status,
            },
            after_state={
                "order_id": eligibility.order_id,
                "status": "exchange_requested",
            },
        )

    def simulate_execute(self, proposal: ActionProposal) -> AgentActionRecord:
        logger.info(
            f"[AfterSalesService] simulate_execute: order={proposal.target_id} "
            f"type={proposal.action_type}"
        )
        return AgentActionRecord(
            action_id=str(uuid.uuid4()),
            action_type=proposal.action_type,
            agent_type="ai",
            target_type=proposal.target_type,
            target_id=proposal.target_id,
            data={
                "before_state": proposal.before_state,
                "after_state": proposal.after_state,
                "simulated": True,
            },
            status="simulated",
            executed_at=datetime.now(timezone.utc),
        )

    def _get_order(self, user_id: str, order_id: str) -> dict:
        from backend.customer_service.service.demo_mode import resolve_user_id

        user_id = resolve_user_id(user_id)
        from backend.sql.executor import execute_sql_struct

        sql = """
            SELECT id, order_no, customer_id, total_amount, status,
                   payment_status, created_at
            FROM "order".orders
            WHERE (id::text = %(order_id)s OR order_no = %(order_id)s)
              AND customer_id::text = %(user_id)s
        """
        result = execute_sql_struct(
            sql, params={"order_id": order_id, "user_id": str(user_id)}
        )

        if result.status not in ("success", "no_data"):
            raise DatabaseError(f"查询订单失败: {result.error}")

        if not result.rows:
            raise OrderNotFoundError(
                f"Order {order_id} not found for user {user_id}"
            )

        return result.rows[0]


_service_instance: AfterSalesService | None = None
_service_lock = threading.Lock()


def get_after_sales_service() -> AfterSalesService:
    global _service_instance
    if _service_instance is None:
        with _service_lock:
            if _service_instance is None:
                _service_instance = AfterSalesService()
    return _service_instance
