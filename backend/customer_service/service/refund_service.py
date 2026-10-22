"""customer_service/service/refund_service.py — 退款服务

资格核查和提案构建复用唯一退款政策。确认后仅在显式 sandbox 模式下
登记待审核客服案件；不会调用支付系统或执行资金退款。

退款规则（窗口 / 可退状态 / 政策版本）统一来自
customer_service/policy/refund_policy.py：候选列表与资格判断同源。
"""
from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from backend.customer_service.action import (
    ActionProposal,
    ActionType,
    AgentActionRecord,
)
from backend.customer_service.errors import (
    DatabaseError,
    OrderNotEligibleError,
    OrderNotFoundError,
    RefundRequestUnavailableError,
)
from backend.customer_service.policy.refund_policy import (
    get_refund_policy,
    parse_order_created_at,
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
    policy_version: str = ""


class RefundService:
    @staticmethod
    def _business_request(
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json_body: dict | None = None,
    ) -> dict:
        """访问统一业务客户端；写请求的不确定结果绝不伪装成失败或成功。"""
        from backend.infra.http.business_client import (
            BusinessServiceError,
            request_json_sync,
        )
        from backend.shared.idempotency import SideEffectOutcomeUnknown

        write = method.upper() in {"POST", "PATCH"}
        try:
            response = request_json_sync(
                method, path, params=params, json_body=json_body,
            )
        except BusinessServiceError as exc:
            if write and (exc.status_code == 0 or exc.status_code >= 500):
                raise SideEffectOutcomeUnknown(
                    "业务服务未能确认售后写入结果",
                ) from exc
            if exc.status_code == 404:
                if "/orders/" in path:
                    raise OrderNotFoundError("订单不存在或不属于当前用户") from exc
                raise RefundRequestUnavailableError(
                    "退款申请不存在或不属于当前用户",
                ) from exc
            if exc.status_code == 409:
                raise OrderNotEligibleError(
                    "订单或退款申请状态已变化，请重新查询后再操作。",
                ) from exc
            raise DatabaseError(
                "业务服务暂时不可用，未确认售后操作结果。",
            ) from exc
        except Exception as exc:
            if write:
                raise SideEffectOutcomeUnknown(
                    "业务服务响应异常，售后写入结果未知",
                ) from exc
            raise DatabaseError("业务服务暂时不可用") from exc

        if not isinstance(response, dict) or response.get("code") != 0:
            if write:
                raise SideEffectOutcomeUnknown(
                    "业务服务未返回可核验的售后写入结果",
                )
            raise DatabaseError("业务服务响应封套无效")
        data = response.get("data")
        if not isinstance(data, dict):
            if write:
                raise SideEffectOutcomeUnknown(
                    "业务服务售后响应缺少结果数据",
                )
            raise DatabaseError("业务服务响应缺少 data 字段")
        return data

    def list_refund_requests(
        self,
        *,
        user_id: str,
        tenant_id: str,
        order_no: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """读取当前可信用户和租户可见的售后申请。"""
        if (
            not user_id or user_id == "anonymous" or not tenant_id
            or tenant_id == "default"
        ):
            raise RefundRequestUnavailableError("售后查询缺少可信身份")

        from backend.customer_service.service.order_service import _use_http_gateway

        if _use_http_gateway():
            params = {
                "user_id": user_id,
                "tenant_id": tenant_id,
                "limit": max(1, min(int(limit), 100)),
            }
            if order_no:
                params["order_no"] = order_no
            data = self._business_request(
                "GET", "/business/refund-requests",
                params=params,
            )
            requests = data.get("requests")
            if not isinstance(requests, list) or any(
                not isinstance(item, dict) for item in requests
            ):
                raise DatabaseError("业务服务售后申请列表格式异常")
            return requests

        from backend.customer_service._db_loop import run_sync
        from backend.customer_service.case.service import get_case_service

        requests = run_sync(get_case_service().async_list_refund_requests(
            user_id=user_id,
            tenant_id=tenant_id,
            limit=max(1, min(int(limit), 100)),
        ))
        if order_no:
            requests = [item for item in requests if item.get("order_no") == order_no]
        return list(requests)

    def build_refund_reason_update_proposal(
        self, *, user_id: str, tenant_id: str, reason: str,
    ) -> ActionProposal:
        """为最近一笔仍可编辑的售后申请生成高风险确认提案。"""
        from backend.customer_service.service.order_service import _use_http_gateway

        if not _use_http_gateway():
            raise RefundRequestUnavailableError(
                "退款原因修改需要连接业务服务，请转人工协助。",
            )
        clean_reason = str(reason or "").strip()[:500]
        if not clean_reason:
            raise OrderNotEligibleError("请告诉我正确的退款原因后再修改。")
        requests = self.list_refund_requests(
            user_id=user_id, tenant_id=tenant_id, limit=20,
        )
        editable = [
            item for item in requests
            if item.get("status") in {"pending_review", "reviewing"}
        ]
        if not editable:
            raise RefundRequestUnavailableError(
                "没有找到可修改原因的待审核退款申请。",
            )
        request = editable[0]
        request_id = str(request.get("request_id") or "").strip()
        request_no = str(request.get("request_no") or request_id).strip()
        order_no = str(request.get("order_no") or "").strip()
        if not request_id or not order_no:
            raise DatabaseError("业务服务售后申请缺少关联订单信息")

        proposal_text = (
            "## 修改退款申请原因\n\n"
            f"**申请编号:** `{request_no}`\n"
            f"**订单号:** `{order_no}`\n"
            f"**原原因:** {str(request.get('reason') or '未填写')}\n"
            f"**修改为:** {clean_reason}\n\n"
            "确认后只修改待审核申请的原因，不会执行退款。\n\n"
            "请确认是否修改？（回复「确认」提交 / 「取消」放弃）"
        )
        return ActionProposal(
            action_type=ActionType.REFUND_REQUEST,
            target_type="refund_request",
            target_id=request_id,
            risk_level=RiskLevel.HIGH,
            proposal_text=proposal_text,
            before_state={
                "operation": "update_reason",
                "request_id": request_id,
                "request_no": request_no,
                "order_no": order_no,
                "status": str(request.get("status") or ""),
                "reason": str(request.get("reason") or ""),
            },
            after_state={
                "operation": "update_reason",
                "reason": clean_reason,
            },
        )

    def execute_refund_reason_update(
        self,
        pending_action: dict,
        *,
        confirmation_id: str,
        user_id: str,
        tenant_id: str,
    ) -> dict:
        """重新核对申请归属/状态后，经幂等 PATCH 更新原因。"""
        from backend.shared.idempotency import SideEffectOutcomeUnknown

        before = pending_action.get("before_state")
        after = pending_action.get("after_state")
        if not isinstance(before, dict) or not isinstance(after, dict):
            raise OrderNotEligibleError("退款申请修改信息不完整，请重新发起。")
        request_id = str(before.get("request_id") or "").strip()
        expected_reason = str(before.get("reason") or "")
        reason = str(after.get("reason") or "").strip()[:500]
        if (
            not request_id or not reason or not confirmation_id
            or before.get("operation") != "update_reason"
            or after.get("operation") != "update_reason"
        ):
            raise OrderNotEligibleError("退款申请修改信息无效，未执行修改。")

        data = self._business_request(
            "GET", f"/business/refund-requests/{request_id}",
            params={"user_id": user_id, "tenant_id": tenant_id},
        )
        request = data.get("request")
        if not isinstance(request, dict):
            raise DatabaseError("业务服务售后申请详情格式异常")
        if (
            str(request.get("request_id") or "") != request_id
            or str(request.get("order_no") or "")
            != str(before.get("order_no") or "")
            or str(request.get("reason") or "") != expected_reason
            or request.get("status") not in {"pending_review", "reviewing"}
        ):
            raise OrderNotEligibleError(
                "退款申请状态或原因已变化，未执行修改；请重新查询后再试。",
            )

        updated = self._business_request(
            "PATCH", f"/business/refund-requests/{request_id}/reason",
            json_body={
                "operation_id": confirmation_id,
                "user_id": user_id,
                "tenant_id": tenant_id,
                "reason": reason,
            },
        )
        result = updated.get("request")
        if not isinstance(result, dict):
            raise SideEffectOutcomeUnknown(
                "业务服务未返回可核验的退款申请修改结果",
            )
        return result

    def check_refund_eligibility(
        self, user_id: str, order_id: str
    ) -> RefundEligibility:
        """Check if an order is eligible for refund.

        规则全部来自 get_refund_policy()（与候选列表同源）。政策不可用
        直接抛 RefundPolicyUnavailableError，先于任何订单查询 fail-closed。
        窗口按「下单时间」计，与候选 SQL 同口径；下单时间缺失/无法解析
        同样拒绝，不默认放行。
        """
        policy = get_refund_policy()
        order = self._get_order(user_id, order_id)

        status = str(order.get("status", ""))
        amount = float(order.get("total_amount", 0))

        if status not in policy.refundable_statuses:
            raise OrderNotEligibleError(
                f"订单状态 '{status}' 不允许退款，仅支持: "
                f"{', '.join(sorted(policy.refundable_statuses))}"
            )

        created_at = parse_order_created_at(order.get("created_at"))
        if created_at is None:
            logger.warning(
                "[RefundService] order created_at missing/unparseable, "
                "fail-closed: order=%s policy=%s",
                order.get("order_no") or order.get("id"), policy.version,
            )
            raise OrderNotEligibleError(
                "暂时无法核实该订单的下单时间，不能自动判断是否在退款期限内，"
                "请回复「转人工」由人工客服协助。"
            )
        if not policy.is_within_window(created_at):
            raise OrderNotEligibleError(
                f"该订单下单已超过 {policy.window_days} 天，"
                "不在退款政策期限内，无法在线提交退款申请；"
                "如需协助可回复「转人工」。"
            )

        if self._has_existing_refund(order.get("id") or order.get("order_no", "")):
            raise OrderNotEligibleError("该订单已存在退款记录，不可重复退款")

        return RefundEligibility(
            eligible=True,
            order_id=str(order.get("id") or order.get("order_no", "")),
            order_no=str(order.get("order_no", "")),
            amount=amount,
            status=status,
            policy_version=policy.version,
        )

    def list_refund_candidates(self, user_id: str) -> dict:
        """退款候选列表（点选数据源）：政策窗口 → 可退订单（联商品名）。

        窗口与可退状态取自 get_refund_policy()，与 check_refund_eligibility
        同源；政策不可用时抛 RefundPolicyUnavailableError（调用方退回手输
        订单号，随后资格判断同样 fail-closed）。

        Returns:
            {"window_days": int, "policy_version": str, "candidates": [
                {"order_id", "order_no", "product_names", "amount", "status"},
            ]}
        """
        policy = get_refund_policy()
        window_days = policy.window_days
        from backend.customer_service.service.order_service import get_order_service

        rows = get_order_service().list_refundable_orders(
            user_id,
            window_days=window_days,
            statuses=sorted(policy.refundable_statuses),
        )
        candidates = [
            {
                "order_id": str(r.get("id") or r.get("order_no", "")),
                "order_no": str(r.get("order_no", "")),
                "product_names": str(r.get("product_names") or ""),
                "amount": float(r.get("total_amount") or 0),
                "status": str(r.get("status") or ""),
            }
            for r in rows
        ]
        return {
            "window_days": window_days,
            "policy_version": policy.version,
            "candidates": candidates,
        }

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
            "\n确认后只登记退款申请，进入人工审核；此操作不会即时退款。\n\n"
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
                "policy_version": eligibility.policy_version,
            },
            after_state={
                "order_id": eligibility.order_id,
                "status": "refund_requested",
                "reason": str(reason or "").strip()[:500],
            },
        )

    def execute_refund_request(
        self,
        pending_action: dict,
        *,
        confirmation_id: str,
        user_id: str,
        tenant_id: str,
        conversation_id: str,
    ) -> AgentActionRecord:
        """在明确启用的业务模式中登记申请；此操作不会执行资金退款。"""
        from backend.config import customer_service as cfg

        refund_mode = str(
            getattr(cfg, "CS_REFUND_REQUEST_MODE", "disabled"),
        ).lower()
        gateway_mode = str(
            getattr(cfg, "CS_BUSINESS_GATEWAY_MODE", ""),
        ).lower()
        http_mode = gateway_mode == "http" and refund_mode == "http"
        sandbox_mode = gateway_mode == "sandbox" and refund_mode == "sandbox"
        if not (http_mode or sandbox_mode):
            raise RefundRequestUnavailableError()
        if (
            not user_id or user_id == "anonymous" or not tenant_id
            or tenant_id == "default" or not conversation_id
        ):
            raise RefundRequestUnavailableError(
                "退款申请缺少可信用户、租户或会话身份",
            )
        if pending_action.get("action_type") != ActionType.REFUND_REQUEST:
            raise RefundRequestUnavailableError("退款确认动作类型无效")

        before = pending_action.get("before_state")
        after = pending_action.get("after_state")
        if not isinstance(before, dict) or not isinstance(after, dict):
            raise OrderNotEligibleError("退款确认信息不完整，请重新发起申请")
        try:
            stable_confirmation_id = str(uuid.UUID(str(confirmation_id)))
        except (ValueError, TypeError, AttributeError):
            raise OrderNotEligibleError("确认标识无效，申请未登记") from None

        operation = str(after.get("operation") or "create")
        if operation == "update_reason":
            request_id = str(before.get("request_id") or "").strip()
            if pending_action.get("target_id") != request_id:
                raise OrderNotEligibleError(
                    "退款申请与确认目标不一致，未执行修改。",
                )
            if not http_mode:
                raise RefundRequestUnavailableError(
                    "当前业务模式不支持修改已提交的退款申请。",
                )
            request = self.execute_refund_reason_update(
                pending_action,
                confirmation_id=stable_confirmation_id,
                user_id=user_id,
                tenant_id=tenant_id,
            )
            return AgentActionRecord(
                action_id=stable_confirmation_id,
                action_type=ActionType.REFUND_REQUEST,
                agent_type="ai",
                target_type="refund_request",
                target_id=request_id,
                data={
                    "request": request,
                    "request_status": request.get("status", ""),
                    "funds_refunded": False,
                    "execution_mode": "business_service",
                    "operation": "update_reason",
                },
                status="executed",
                executed_at=datetime.now(timezone.utc),
            )

        if operation != "create":
            raise OrderNotEligibleError("退款操作类型无效，未执行任何写入。")

        order_id = str(pending_action.get("target_id") or "").strip()
        if not order_id or str(before.get("order_id") or "") != order_id:
            raise OrderNotEligibleError("退款确认订单与提案不一致，请重新发起申请")

        # 确认时重查订单、退款记录和当前政策，任何事实变化都拒绝登记。
        eligibility = self.check_refund_eligibility(user_id, order_id)
        try:
            proposed_amount = Decimal(str(before.get("amount"))).quantize(
                Decimal("0.01"),
            )
            current_amount = Decimal(str(eligibility.amount)).quantize(
                Decimal("0.01"),
            )
        except (InvalidOperation, ValueError, TypeError):
            raise OrderNotEligibleError("退款金额无法核验，请重新发起申请") from None
        if (
            eligibility.order_id != order_id
            or eligibility.status != str(before.get("status") or "")
            or current_amount != proposed_amount
            or eligibility.policy_version != str(before.get("policy_version") or "")
        ):
            raise OrderNotEligibleError(
                "订单或退款政策已变化，申请未登记；请重新查询并发起。",
            )

        reason = str(after.get("reason") or "").strip()[:500]
        if http_mode:
            data = self._business_request(
                "POST", "/business/refund-requests",
                json_body={
                    "request_id": stable_confirmation_id,
                    "user_id": user_id,
                    "tenant_id": tenant_id,
                    "conversation_id": conversation_id,
                    "order_no": eligibility.order_no,
                    "expected_order_status": eligibility.status,
                    "expected_amount": format(current_amount, ".2f"),
                    "policy_version": eligibility.policy_version,
                    "reason": reason,
                },
            )
            request = data.get("request")
            if not isinstance(request, dict):
                from backend.shared.idempotency import SideEffectOutcomeUnknown

                raise SideEffectOutcomeUnknown(
                    "业务服务没有返回可核验的退款申请结果",
                )
            return AgentActionRecord(
                action_id=stable_confirmation_id,
                action_type=ActionType.REFUND_REQUEST,
                agent_type="ai",
                target_type="order",
                target_id=eligibility.order_id,
                data={
                    "request": request,
                    "request_status": request.get("status", "pending_review"),
                    "funds_refunded": False,
                    "execution_mode": "business_service",
                    "operation": "create",
                },
                status="executed",
                executed_at=datetime.now(timezone.utc),
            )

        from backend.customer_service.case.service import get_case_service

        request = get_case_service().create_refund_request_sync(
            request_id=stable_confirmation_id,
            conversation_id=conversation_id,
            user_id=user_id,
            tenant_id=tenant_id,
            order_id=eligibility.order_id,
            order_no=eligibility.order_no,
            amount=format(current_amount, ".2f"),
            reason=reason,
            policy_version=eligibility.policy_version,
        )
        return AgentActionRecord(
            action_id=stable_confirmation_id,
            action_type=ActionType.REFUND_REQUEST,
            agent_type="ai",
            target_type="order",
            target_id=eligibility.order_id,
            data={
                "request": request,
                "request_status": "pending_review",
                "funds_refunded": False,
                "execution_mode": "sandbox",
            },
            status="executed",
            executed_at=datetime.now(timezone.utc),
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

        HTTP 模式跳过本地 refunds 查重（能力边界，非 fallback）：订单事实
        在 business-service 侧，活跃申请唯一性由其退款申请写接口原子裁决；
        Python 不以本地演示库结果代替远端查重。
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
