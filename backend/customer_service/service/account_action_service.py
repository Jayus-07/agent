"""customer_service/service/account_action_service.py — 账户操作服务

地址修改 / 密码重置的 proposal 构建、模拟执行。
Phase 4: 不实际写 DB。
"""
from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone

from backend.customer_service.action import (
    ActionProposal,
    ActionType,
    AgentActionRecord,
)
from backend.customer_service.risk import RiskLevel
from backend.shared.logger import logger


class AccountActionService:

    def build_address_update_proposal(
        self, user_id: str, target_id: str, new_address: str = ""
    ) -> ActionProposal:
        # 订单级改址（传入订单号）以订单为操作实体；无订单号才是账户级
        # 默认地址修改。target 塌缩到 user 会让语义守卫把不同订单的改址
        # 判为同一操作（Q3 action 池实测）。
        order_scoped = bool(target_id) and target_id != user_id
        # 确认卡必须点名操作对象：用户要能看出改的是哪张订单的地址
        # （锁版断言 must_contain 订单号；退款/退货卡同口径）。
        scope_line = (
            f"此操作将修改订单 {target_id} 的收货地址。" if order_scoped
            else "此操作将修改您的默认收货地址。"
        )
        proposal_text = (
            f"## 地址修改\n\n"
            f"**新地址:** {new_address or '（未提供）'}\n\n"
            f"{scope_line}\n\n"
            f"请确认是否提交修改？（回复「确认」提交 / 「取消」放弃）"
        )

        return ActionProposal(
            action_type=ActionType.ADDRESS_UPDATE,
            target_type="order" if order_scoped else "account",
            target_id=target_id or user_id,
            risk_level=RiskLevel.MEDIUM,
            proposal_text=proposal_text,
            before_state={"user_id": user_id, "field": "address"},
            after_state={"user_id": user_id, "new_address": new_address},
        )

    def build_password_reset_proposal(
        self, user_id: str
    ) -> ActionProposal:
        proposal_text = (
            "## 密码重置\n\n"
            "系统将向您的注册邮箱发送重置链接。\n\n"
            "请确认是否提交密码重置？（回复「确认」提交 / 「取消」放弃）"
        )

        return ActionProposal(
            action_type=ActionType.PASSWORD_RESET,
            target_type="account",
            target_id=user_id,
            risk_level=RiskLevel.MEDIUM,
            proposal_text=proposal_text,
            before_state={"user_id": user_id, "field": "password"},
            after_state={"user_id": user_id, "action": "password_reset_link_sent"},
        )

    def simulate_execute(self, proposal: ActionProposal) -> AgentActionRecord:
        logger.info(
            f"[AccountActionService] simulate_execute: target={proposal.target_id} "
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


_service_instance: AccountActionService | None = None
_service_lock = threading.Lock()


def get_account_action_service() -> AccountActionService:
    global _service_instance
    if _service_instance is None:
        with _service_lock:
            if _service_instance is None:
                _service_instance = AccountActionService()
    return _service_instance
