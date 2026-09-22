"""customer_service/service/refund_service.py — 退款服务

Phase 4: eligibility check + proposal building + simulate_execute.
不实际写 DB — Phase 6 替换 simulate 为真实 DB 写入。
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
class RefundEligibility:
    eligible: bool
    order_id: str
    order_no: str
    amount: float
    status: str
    reason: str = ""


class RefundService:
    REFUNDABLE_STATUSES = {"paid", "shipped", "completed"}
    RETURN_WINDOW_DAYS = 7

    def check_refund_eligibility(
        self, user_id: str, order_id: str
    ) -> RefundEligibility:
        """Check if an order is eligible for refund."""
        order = self._get_order(user_id, order_id)

        status = str(order.get("status", ""))
        amount = float(order.get("total_amount", 0))

        if status not in self.REFUNDABLE_STATUSES:
            raise OrderNotEligibleError(
                f"订单状态 '{status}' 不允许退款，仅支持: {', '.join(sorted(self.REFUNDABLE_STATUSES))}"
            )

        if self._has_existing_refund(order.get("id") or order.get("order_no", "")):
            raise OrderNotEligibleError("该订单已存在退款记录，不可重复退款")

        return RefundEligibility(
            eligible=True,
            order_id=str(order.get("id") or order.get("order_no", "")),
            order_no=str(order.get("order_no", "")),
            amount=amount,
            status=status,
        )

    def build_refund_proposal(
        self, user_id: str, order_id: str, reason: str = ""
    ) -> ActionProposal:
        """Build a refund proposal for user confirmation."""
        eligibility = self.check_refund_eligibility(user_id, order_id)

        proposal_text = (
            f"## 退款申请\n\n"
            f"**订单号:** `{eligibility.order_no}`\n"
            f"**退款金额:** ¥{eligibility.amount:.2f}\n"
            f"**当前状态:** {eligibility.status}\n"
        )
        if reason:
            proposal_text += f"**退款原因:** {reason}\n"
        proposal_text += (
            "\n退款将在 3-5 个工作日内退回原支付方式。\n\n"
            "请确认是否提交退款申请？（回复「确认」提交 / 「取消」放弃）"
        )

        return ActionProposal(
            action_type=ActionType.REFUND_REQUEST,
            target_type="order",
            target_id=eligibility.order_id,
            risk_level=RiskLevel.HIGH,
            proposal_text=proposal_text,
            before_state={
                "order_id": eligibility.order_id,
                "status": eligibility.status,
                "amount": eligibility.amount,
            },
            after_state={
                "order_id": eligibility.order_id,
                "status": "refund_requested",
            },
        )

    def simulate_execute(self, proposal: ActionProposal) -> AgentActionRecord:
        """Simulate refund execution — builds record without DB write."""
        logger.info(
            f"[RefundService] simulate_execute: order={proposal.target_id} "
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
        """订单事实统一取自 OrderService（缺陷6.4，2026-09-23）。

        此前动作侧直查本地 "order".orders（execute_sql_struct），与查询侧
        （http 模式经 business_client）split-brain：前一秒查得到的订单
        退款时「不存在」。现动作与查询共用同一入口 —— CS_BUSINESS_GATEWAY
        模式为 http 时订单事实来自 business service（404 = 订单不存在，
        网关挂 = 业务不可用，绝不 fallback 本地库），sandbox 模式保持本地
        演示库（本地开发模式不受影响）。历史 "latest" 语义化兜底已随
        缺陷6.2 一并删除：动作缺订单号在上游结构化追问，不再走到这里。
        """
        from backend.customer_service.service.order_service import get_order_service

        result = get_order_service().query_orders(
            user_id, order_id=order_id, query_type="detail",
        )
        if not result.orders:
            raise OrderNotFoundError(
                f"Order {order_id} not found for user {user_id}"
            )
        return result.orders[0]

    def _has_existing_refund(self, order_pk: str) -> bool:
        """Check if a refund record already exists for this order.

        P1 修正（audit-report §P0-8）：查询失败此前返回 False（放行），
        降级方向不安全 —— 重复退款闸门失效。改为 fail-closed：无法确认
        不存在退款记录时拒绝并提示用户，宁可多一次人工介入也不双退款。

        http 网关模式跳过本地 refunds 查重（能力边界，非 fallback）：订单
        事实在 business service 侧，其退款记录本地库必然查不到；网关契约
        暂无退款记录端点，重复退款由确认后的执行层把关（当前 simulate，
        真实执行接入时网关侧必须校验）。
        """
        from backend.customer_service.service.order_service import _use_http_gateway

        if _use_http_gateway():
            logger.info(
                "[RefundService] http gateway mode, skip local refunds check: "
                "order=%s", order_pk,
            )
            return False

        from backend.sql.executor import execute_sql_struct

        sql = """
            SELECT id FROM "order".refunds
            WHERE order_id = %(order_id)s
            LIMIT 1
        """
        result = execute_sql_struct(sql, params={"order_id": order_pk})

        if result.status not in ("success", "no_data"):
            logger.error(
                "[RefundService] refunds 查询失败，fail-closed 拒绝退款资格检查: "
                "order=%s status=%s error=%s",
                order_pk, result.status, result.error,
            )
            raise DatabaseError(
                "暂时无法核实该订单的退款记录，为防止重复退款已拒绝本次申请，"
                "请稍后重试或联系人工客服。"
            )

        return len(result.rows) > 0


_service_instance: RefundService | None = None
_service_lock = threading.Lock()


def get_refund_service() -> RefundService:
    global _service_instance
    if _service_instance is None:
        with _service_lock:
            if _service_instance is None:
                _service_instance = RefundService()
    return _service_instance
