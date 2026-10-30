"""customer_service/service/cancel_order_service.py — 取消订单全链路（P2）

提案-确认-幂等执行（复用既有确认流）：已支付（待发货）订单的取消属高风险
资金操作，必须经 ActionProposal → 用户确认 → CAS 认领 → 幂等执行；未付款
订单由用户端商城页直接取消（POST /mall/orders/{no}/cancel），不经本链路。

执行纪律对齐 RefundService.execute_refund_request：
  - 确认时重查订单事实（归属/状态/金额），任何变化拒绝执行；
  - 金额 Decimal 对账，不信任提案之外的数字；
  - 真实副作用委托 MallService.cancel_order（双模式：sandbox 直写
    agent_business / http 走 business-service 取消契约），
    幂等键 = confirmation_id（重复确认最多退款一次）。
"""
from __future__ import annotations

import uuid
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone

from backend.customer_service.action import ActionProposal, ActionType, AgentActionRecord
from backend.customer_service.errors import OrderNotEligibleError
from backend.customer_service.risk import RiskLevel
from backend.shared.logger import logger

_CANCELLABLE_STATUS = "paid"


class CancelOrderService:
    def build_cancel_order_proposal(
        self, user_id: str, order_id: str, reason: str = "",
    ) -> ActionProposal:
        """已支付订单的取消提案（未付款订单走商城直取，不进确认流）。"""
        from backend.customer_service.service.order_service import get_order_service

        order = get_order_service().query_orders(
            user_id, order_id=order_id, query_type="detail",
        ).orders[0]
        status = str(order.get("status") or "")
        if status != _CANCELLABLE_STATUS:
            raise OrderNotEligibleError(
                f"订单 {order.get('order_no') or order_id} 当前状态（{status}）"
                "不支持取消：未付款订单可在「我的订单」直接取消，"
                "已发货订单请走退款/退货流程。",
            )

        amount = float(Decimal(str(order.get("total_amount", "0"))))
        order_no = str(order.get("order_no") or "")
        proposal_text = (
            "## 取消订单\n\n"
            f"**订单号:** `{order_no}`\n"
            f"**订单金额:** ¥{amount:.2f}\n"
            f"**当前状态:** 已支付，待发货\n"
            "\n确认后订单立即取消，货款将全额退回账户余额"
            "（可在「资金流水」查看）。\n\n"
            "请确认是否取消该订单？（回复「确认」取消订单 / 「取消」放弃）"
        )
        return ActionProposal(
            action_type=ActionType.ORDER_CANCEL,
            target_type="order",
            target_id=str(order.get("id") or order_id),
            risk_level=RiskLevel.HIGH,
            proposal_text=proposal_text,
            before_state={
                "order_id": str(order.get("id") or order_id),
                "order_no": order_no,
                "status": status,
                "payment_status": str(order.get("payment_status") or "paid"),
                "amount": amount,
            },
            after_state={
                "order_id": str(order.get("id") or order_id),
                "order_no": order_no,
                "status": "cancelled",
                "reason": str(reason or "").strip()[:500],
            },
        )

    def execute_confirmed_cancel(
        self,
        pending_action: dict,
        *,
        confirmation_id: str,
        user_id: str,
        tenant_id: str,
        conversation_id: str,
    ) -> AgentActionRecord:
        """确认后的真实取消执行：重查事实 → MallService 幂等取消+退款。"""
        if pending_action.get("action_type") != ActionType.ORDER_CANCEL:
            raise OrderNotEligibleError("取消订单确认动作类型无效")
        if not user_id or user_id == "anonymous" or not tenant_id or not conversation_id:
            raise OrderNotEligibleError("取消订单缺少可信用户、租户或会话身份")
        try:
            stable_confirmation_id = str(uuid.UUID(str(confirmation_id)))
        except (ValueError, TypeError, AttributeError):
            raise OrderNotEligibleError("确认标识无效，未执行取消") from None

        before = pending_action.get("before_state")
        after = pending_action.get("after_state")
        if not isinstance(before, dict) or not isinstance(after, dict):
            raise OrderNotEligibleError("取消确认信息不完整，请重新发起")

        order_id = str(pending_action.get("target_id") or "").strip()
        if not order_id or str(before.get("order_id") or "") != order_id:
            raise OrderNotEligibleError("取消确认订单与提案不一致，请重新发起申请")

        # 确认时重查订单事实：归属/状态/金额任何变化都拒绝执行
        from backend.customer_service.service.order_service import get_order_service

        order = get_order_service().query_orders(
            user_id, order_id=order_id, query_type="detail",
        ).orders[0]
        try:
            proposed_amount = Decimal(str(before.get("amount"))).quantize(
                Decimal("0.01"),
            )
            current_amount = Decimal(str(order.get("total_amount", "0"))).quantize(
                Decimal("0.01"),
            )
        except (InvalidOperation, ValueError, TypeError):
            raise OrderNotEligibleError("订单金额无法核验，请重新发起申请") from None
        if (
            str(order.get("id") or "") != order_id
            or str(order.get("status") or "") != str(before.get("status") or "")
            or current_amount != proposed_amount
        ):
            raise OrderNotEligibleError(
                "订单状态或金额已变化，取消未执行；请重新查询并发起。",
            )

        from backend.customer_service.service.mall_service import MallError, MallService

        order_no = str(order.get("order_no") or "")
        try:
            result = MallService().cancel_order(
                user_id, tenant_id, order_no,
                reason=str(after.get("reason") or "").strip()[:500],
                operation_key=stable_confirmation_id,
                via_confirmation=True,
            )
        except MallError as exc:
            logger.warning(
                "[CancelOrder] 执行失败 confirmation=%s order=%s: %s",
                confirmation_id, order_no, exc,
            )
            raise OrderNotEligibleError(
                exc.user_message or "订单取消执行失败，请稍后重试。",
            ) from exc

        executed_at = datetime.now(timezone.utc)
        return AgentActionRecord(
            action_id=stable_confirmation_id,
            action_type=ActionType.ORDER_CANCEL,
            agent_type="ai",
            target_type="order",
            target_id=order_no,
            data={
                "order": result.get("order", {}),
                "wallet": result.get("wallet", {}),
                "funds_refunded": True,
                "execution_mode": "mall_service",
            },
            status="executed",
            executed_at=executed_at,
        )


def get_cancel_order_service() -> CancelOrderService:
    return CancelOrderService()
