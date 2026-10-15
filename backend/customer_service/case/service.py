"""customer_service/case/service.py — 统一案件服务（迁移 B12 核心闭环）

设计方案 §10.1 cs_case + §14.2「case 是唯一案件事实源」。本服务承载核心
生命周期闭环：**创建 → 分配 → 处理 → 关闭**；SLA 按 P0/P1/P2 自动计算；
同会话同类型活跃案件防重入由 058 部分唯一索引在 DB 决胜（并发不先查后插），
应用层把 IntegrityError 转译为幂等复用（对齐 051 守卫模式）。

过渡策略：与 tickets 表并存（complaint 源先行并行写入），ticket/after_sales
全面并表走后续批次。状态流转白名单 fail-fast；结案 resolution 必填。
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from backend.customer_service.errors import (
    BusinessRuleError,
    DatabaseError,
    RefundRequestConflictError,
)
from backend.shared.logger import logger

# 流转白名单（状态机：open → waiting_user/processing → resolved/closed）
_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "open": ("waiting_user", "processing", "resolved", "closed"),
    "waiting_user": ("processing", "resolved", "closed"),
    "processing": ("resolved", "closed"),
    "resolved": ("closed",),
    "closed": (),
}


def _to_dict(case: Any) -> dict:
    return {
        "case_id": case.case_id,
        "tenant_id": case.tenant_id,
        "conversation_id": case.conversation_id,
        "user_id": case.user_id,
        "case_type": case.case_type,
        "priority": case.priority,
        "status": case.status,
        "title": case.title,
        "context": case.context,
        "owner_agent_id": case.owner_agent_id,
        "related_confirmation_id": case.related_confirmation_id,
        "resolution": case.resolution,
        "sla_deadline_at": case.sla_deadline_at.isoformat() if case.sla_deadline_at else None,
        "first_responded_at": case.first_responded_at.isoformat() if case.first_responded_at else None,
        "created_at": case.created_at.isoformat() if case.created_at else None,
        "updated_at": case.updated_at.isoformat() if case.updated_at else None,
    }


def _refund_request_matches(case: Any, expected: dict) -> bool:
    """同一确认重放只复用相同的不可变申请内容。"""
    context = case.context if isinstance(case.context, dict) else {}
    stored = context.get("refund_request")
    if not isinstance(stored, dict):
        return False
    keys = (
        "confirmation_id", "conversation_id", "order_id", "order_no",
        "amount", "currency", "reason", "policy_version", "execution_mode",
    )
    return all(stored.get(key) == expected.get(key) for key in keys)


def _refund_request_view(case: Any) -> dict | None:
    """把客服案件投影为退款申请；案件状态不表示资金已退回。"""
    context = case.context if isinstance(case.context, dict) else {}
    request = context.get("refund_request")
    if not isinstance(request, dict):
        return None
    state_to_status = {
        "open": "pending_review",
        "waiting_user": "needs_user",
        "processing": "processing",
        "resolved": "resolved",
        "closed": "closed",
    }
    order_no = str(request.get("order_no") or "")
    return {
        "request_id": str(case.case_id),
        "status": state_to_status.get(case.status, "unknown"),
        "case_status": str(case.status),
        "order_display": f"订单尾号 ****{order_no[-4:]}" if order_no else "订单",
        "amount": str(request.get("amount") or ""),
        "policy_version": str(request.get("policy_version") or ""),
        "created_at": case.created_at.isoformat() if case.created_at else None,
        "updated_at": case.updated_at.isoformat() if case.updated_at else None,
    }


class CSCaseService:
    """统一案件核心闭环（创建 → 分配 → 处理 → 关闭）。"""

    # ── async 实现（_db_loop / FastAPI 事件循环）────────────

    async def async_create(
        self,
        *,
        conversation_id: str,
        user_id: str,
        case_type: str,
        priority: str = "P2",
        title: str = "",
        context: dict | None = None,
        tenant_id: str = "default",
        related_confirmation_id: str | None = None,
    ) -> dict:
        """建案：同会话同类型已有活跃案件时幂等复用（DB 唯一索引决胜）。"""
        from backend.customer_service.models.case import (
            CASE_ACTIVE_STATUSES,
            CASE_TYPES,
            CASE_PRIORITIES,
            CSCase,
            compute_sla_deadline,
        )
        from backend.memory.database import AsyncSessionLocal

        if case_type not in CASE_TYPES:
            raise BusinessRuleError(f"案件类型非法: {case_type}")
        if priority not in CASE_PRIORITIES:
            raise BusinessRuleError(f"案件优先级非法: {priority}")

        async with AsyncSessionLocal() as db:
            case = CSCase(
                conversation_id=conversation_id,
                user_id=user_id,
                case_type=case_type,
                priority=priority,
                status="open",
                title=title[:200],
                context=context or {},
                tenant_id=tenant_id,
                related_confirmation_id=related_confirmation_id,
                sla_deadline_at=compute_sla_deadline(priority),
            )
            db.add(case)
            try:
                await db.flush()
                await db.commit()
                logger.info(
                    "[CSCase] created: case=%s type=%s priority=%s conv=%s",
                    case.case_id, case_type, priority, conversation_id,
                )
                return _to_dict(case)
            except IntegrityError:
                # 同会话同类型活跃案件已存在（并发决胜在 DB）——幂等复用赢家行
                await db.rollback()
                existing = (
                    await db.execute(
                        select(CSCase).where(
                            CSCase.conversation_id == conversation_id,
                            CSCase.case_type == case_type,
                            CSCase.tenant_id == tenant_id,
                            CSCase.status.in_(CASE_ACTIVE_STATUSES),
                        ).limit(1)
                    )
                ).scalar_one_or_none()
                if existing is None:
                    raise
                logger.info(
                    "[CSCase] 幂等复用活跃案件: case=%s conv=%s",
                    existing.case_id, conversation_id,
                )
                return _to_dict(existing)

    async def async_assign(
        self, case_id: str, agent_id: str, *, tenant_id: str = "default",
    ) -> dict:
        """分配责任人（坐席接管；首次响应 SLA 打点）。"""
        from datetime import datetime, timezone

        from backend.customer_service.models.case import CSCase
        from backend.memory.database import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            case = await self._get_for_update(db, case_id, tenant_id)
            if case.status == "closed":
                raise BusinessRuleError("案件已关闭，不可分配")
            case.owner_agent_id = agent_id
            if case.first_responded_at is None:
                case.first_responded_at = datetime.now(timezone.utc)
            await db.commit()
            logger.info("[CSCase] assigned: case=%s agent=%s", case_id, agent_id)
            return _to_dict(case)

    async def async_start_process(
        self, case_id: str, *, actor: str = "", tenant_id: str = "default",
    ) -> dict:
        """开始处理（open/waiting_user → processing）。"""
        from datetime import datetime, timezone

        from backend.customer_service.models.case import CSCase
        from backend.memory.database import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            case = await self._get_for_update(db, case_id, tenant_id)
            self._check_transition(case, "processing", actor)
            case.status = "processing"
            if case.first_responded_at is None:
                case.first_responded_at = datetime.now(timezone.utc)
            await db.commit()
            return _to_dict(case)

    async def async_set_waiting_user(
        self, case_id: str, *, actor: str = "", tenant_id: str = "default",
    ) -> dict:
        """等待用户补充（open/processing → waiting_user）。"""
        from backend.customer_service.models.case import CSCase
        from backend.memory.database import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            case = await self._get_for_update(db, case_id, tenant_id)
            self._check_transition(case, "waiting_user", actor)
            case.status = "waiting_user"
            await db.commit()
            return _to_dict(case)

    async def async_close(
        self, case_id: str, resolution: str, *,
        status: str = "closed", actor: str = "", tenant_id: str = "default",
    ) -> dict:
        """结案：resolution 必填（设计方案 §10.1「结案必填」），可先 resolved 再 closed。"""
        from datetime import datetime, timezone

        from backend.customer_service.models.case import CSCase
        from backend.memory.database import AsyncSessionLocal

        if not (resolution or "").strip():
            raise BusinessRuleError("结案必须填写处理结论（resolution）")
        async with AsyncSessionLocal() as db:
            case = await self._get_for_update(db, case_id, tenant_id)
            if status not in ("resolved", "closed"):
                raise BusinessRuleError(f"结案目标状态非法: {status}")
            self._check_transition(case, status, actor)
            case.status = status
            case.resolution = resolution
            if case.first_responded_at is None:
                case.first_responded_at = datetime.now(timezone.utc)
            await db.commit()
            logger.info(
                "[CSCase] closed: case=%s status=%s actor=%s",
                case_id, status, actor,
            )
            return _to_dict(case)

    async def async_get_active_by_conversation(
        self, conversation_id: str, case_type: str | None = None, *,
        tenant_id: str = "default",
    ) -> dict | None:
        from backend.customer_service.models.case import (
            CASE_ACTIVE_STATUSES, CSCase,
        )
        from backend.memory.database import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            stmt = select(CSCase).where(
                CSCase.conversation_id == conversation_id,
                CSCase.tenant_id == tenant_id,
                CSCase.status.in_(CASE_ACTIVE_STATUSES),
            )
            if case_type:
                stmt = stmt.where(CSCase.case_type == case_type)
            case = (await db.execute(stmt.limit(1))).scalar_one_or_none()
            return _to_dict(case) if case else None

    async def async_list(
        self, *, tenant_id: str = "default", status: str | None = None,
        user_id: str | None = None, limit: int = 50,
    ) -> list[dict]:
        from backend.customer_service.models.case import CSCase
        from backend.memory.database import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            stmt = select(CSCase).where(CSCase.tenant_id == tenant_id)
            if status:
                stmt = stmt.where(CSCase.status == status)
            if user_id:
                stmt = stmt.where(CSCase.user_id == user_id)
            stmt = stmt.order_by(CSCase.created_at.desc()).limit(limit)
            rows = (await db.execute(stmt)).scalars().all()
            return [_to_dict(c) for c in rows]

    async def async_create_refund_request(
        self,
        *,
        request_id: str,
        conversation_id: str,
        user_id: str,
        tenant_id: str,
        order_id: str,
        order_no: str,
        amount: str,
        reason: str,
        policy_version: str,
    ) -> dict:
        """把已确认的本机退款申请写入唯一客服案件事实源。

        request_id 使用稳定 confirmation_id。唯一键竞争时只复用内容完全
        一致的同一请求；不同申请不会被悄悄折叠到同一案件。
        """
        from datetime import datetime, timezone

        from backend.customer_service.models.case import CSCase, compute_sla_deadline
        from backend.memory.database import AsyncSessionLocal

        if not user_id or not tenant_id or tenant_id == "default" or not conversation_id:
            raise BusinessRuleError(
                "退款申请缺少可信用户、租户或会话身份",
                user_message="身份信息暂不可核验，未创建退款申请；请联系人工客服。",
            )
        try:
            stable_id = str(uuid.UUID(str(request_id)))
        except (ValueError, TypeError, AttributeError):
            raise BusinessRuleError(
                "退款申请缺少有效确认标识",
                user_message="确认信息无效，未创建退款申请；请重新发起。",
            ) from None

        normalized_order_id = (
            "" if order_id is None else str(order_id).strip()
        )
        if not normalized_order_id:
            raise BusinessRuleError(
                "退款申请缺少有效订单标识",
                user_message="订单信息暂不可核验，未创建退款申请；请重新查询订单。",
            )

        request_data = {
            "confirmation_id": stable_id,
            "conversation_id": conversation_id,
            "order_id": normalized_order_id,
            "order_no": str(order_no),
            "amount": str(amount),
            "currency": "CNY",
            "reason": str(reason or "").strip()[:500],
            "policy_version": str(policy_version),
            "execution_mode": "sandbox",
        }

        async with AsyncSessionLocal() as db:
            existing = await self._load_refund_case(
                db, stable_id, user_id=user_id, tenant_id=tenant_id,
            )
            if existing is not None:
                if not _refund_request_matches(existing, request_data):
                    raise RefundRequestConflictError(
                        "同一确认标识已关联不同的退款申请内容",
                    )
                view = _refund_request_view(existing)
                if view is None:
                    raise RefundRequestConflictError()
                return view

            from backend.customer_service.models.case import CASE_ACTIVE_STATUSES

            case = CSCase(
                case_id=stable_id,
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                user_id=user_id,
                case_type="refund",
                priority="P2",
                status="open",
                title=f"退款申请：{order_no}"[:200],
                context={"refund_request": request_data},
                related_confirmation_id=stable_id,
                sla_deadline_at=compute_sla_deadline("P2"),
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
            db.add(case)
            try:
                await db.flush()
            except IntegrityError as exc:
                await db.rollback()
                existing = await self._find_refund_case_after_conflict(
                    stable_id,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    tenant_id=tenant_id,
                    order_id=normalized_order_id,
                    active_statuses=CASE_ACTIVE_STATUSES,
                )
                if existing is not None and _refund_request_matches(
                    existing, request_data,
                ):
                    view = _refund_request_view(existing)
                    if view is None:
                        raise DatabaseError("退款申请案件缺少有效申请上下文")
                    return view
                if existing is not None:
                    existing_request = (existing.context or {}).get(
                        "refund_request",
                    )
                    same_order = (
                        isinstance(existing_request, dict)
                        and str(existing_request.get("order_id") or "").strip()
                        == normalized_order_id
                    )
                    raise RefundRequestConflictError(
                        "用户已有同订单或同会话的活跃退款案件",
                        user_message=(
                            "该订单已有待处理的退款申请，系统没有重复创建；"
                            "请查询现有申请状态或联系人工客服。"
                            if same_order else
                            "该会话已有待处理的退款案件，系统没有重复创建；"
                            "请查询申请状态或联系人工客服。"
                        ),
                    ) from exc
                raise

            try:
                await db.commit()
            except Exception as exc:
                try:
                    await db.rollback()
                except Exception:
                    logger.warning("[CSCase] refund commit rollback failed")
                try:
                    persisted = await self._find_refund_case_after_conflict(
                        stable_id,
                        conversation_id=conversation_id,
                        user_id=user_id,
                        tenant_id=tenant_id,
                        order_id=normalized_order_id,
                        active_statuses=CASE_ACTIVE_STATUSES,
                    )
                except Exception as verify_exc:
                    from backend.shared.idempotency import SideEffectOutcomeUnknown

                    raise SideEffectOutcomeUnknown(
                        "退款申请提交结果未知，须按稳定申请 ID 核对"
                    ) from verify_exc
                if persisted is not None and _refund_request_matches(
                    persisted, request_data,
                ):
                    view = _refund_request_view(persisted)
                    if view is None:
                        raise DatabaseError("退款申请案件缺少有效申请上下文")
                    return view
                if persisted is not None:
                    raise RefundRequestConflictError(
                        "申请提交后发现确认标识内容不一致",
                    ) from exc
                raise DatabaseError(
                    "退款申请未能确认写入，未返回成功结果",
                    user_message="暂时无法确认申请是否登记，请查看退款申请列表或联系人工客服；请勿重复提交。",
                ) from exc

            view = _refund_request_view(case)
            if view is None:
                raise DatabaseError("退款申请案件缺少有效申请上下文")
            logger.info(
                "[CSCase] refund request persisted: request=%s tenant=%s",
                stable_id, tenant_id,
            )
            return view

    async def async_get_refund_request(
        self, request_id: str, *, user_id: str, tenant_id: str,
    ) -> dict | None:
        from backend.customer_service.models.case import CSCase
        from backend.memory.database import AsyncSessionLocal

        try:
            stable_id = str(uuid.UUID(str(request_id)))
        except (ValueError, TypeError, AttributeError):
            return None
        async with AsyncSessionLocal() as db:
            case = await self._load_refund_case(
                db, stable_id, user_id=user_id, tenant_id=tenant_id,
            )
            return _refund_request_view(case) if case is not None else None

    async def async_list_refund_requests(
        self, *, user_id: str, tenant_id: str, limit: int = 20,
    ) -> list[dict]:
        from backend.customer_service.models.case import CSCase
        from backend.memory.database import AsyncSessionLocal

        if not user_id or not tenant_id or tenant_id == "default":
            raise BusinessRuleError(
                "退款申请查询缺少可信用户或租户身份",
                user_message="身份信息暂不可核验，无法查询退款申请。",
            )
        async with AsyncSessionLocal() as db:
            stmt = (
                select(CSCase)
                .where(
                    CSCase.tenant_id == tenant_id,
                    CSCase.user_id == user_id,
                    CSCase.case_type == "refund",
                    CSCase.related_confirmation_id.is_not(None),
                )
                .order_by(CSCase.created_at.desc())
                .limit(max(1, min(int(limit), 100)))
            )
            rows = (await db.execute(stmt)).scalars().all()
            result = []
            for case in rows:
                request = (case.context or {}).get("refund_request")
                if (
                    not isinstance(request, dict)
                    or request.get("confirmation_id") != str(case.case_id)
                    or case.related_confirmation_id != str(case.case_id)
                ):
                    continue
                view = _refund_request_view(case)
                if view is not None:
                    result.append(view)
            return result

    @staticmethod
    async def _load_refund_case(
        db: Any,
        request_id: str,
        *,
        user_id: str,
        tenant_id: str,
    ) -> Any | None:
        from backend.customer_service.models.case import CSCase

        result = await db.execute(
            select(CSCase).where(
                CSCase.case_id == request_id,
                CSCase.tenant_id == tenant_id,
                CSCase.user_id == user_id,
                CSCase.case_type == "refund",
                CSCase.related_confirmation_id == request_id,
            ).limit(1)
        )
        case = result.scalar_one_or_none()
        if case is None:
            return None
        request = (case.context or {}).get("refund_request")
        if (
            not isinstance(request, dict)
            or request.get("confirmation_id") != request_id
        ):
            return None
        return case

    async def _find_refund_case_after_conflict(
        self,
        request_id: str,
        *,
        conversation_id: str,
        user_id: str,
        tenant_id: str,
        order_id: str,
        active_statuses: tuple[str, ...],
    ) -> Any | None:
        from backend.customer_service.models.case import CSCase
        from backend.memory.database import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            exact = await self._load_refund_case(
                db, request_id, user_id=user_id, tenant_id=tenant_id,
            )
            if exact is not None:
                return exact
            stored_order_id = func.coalesce(
                func.nullif(
                    func.btrim(
                        CSCase.context["refund_request"]["order_id"].as_string()
                    ),
                    "",
                ),
                "",
            )
            order_match = await db.execute(
                select(CSCase).where(
                    CSCase.tenant_id == tenant_id,
                    CSCase.user_id == user_id,
                    CSCase.case_type == "refund",
                    CSCase.status.in_(active_statuses),
                    stored_order_id == order_id,
                ).limit(1)
            )
            same_order = order_match.scalar_one_or_none()
            if same_order is not None:
                return same_order
            result = await db.execute(
                select(CSCase).where(
                    CSCase.conversation_id == conversation_id,
                    CSCase.tenant_id == tenant_id,
                    CSCase.user_id == user_id,
                    CSCase.case_type == "refund",
                    CSCase.status.in_(active_statuses),
                ).limit(1)
            )
            return result.scalar_one_or_none()

    def create_refund_request_sync(self, **fields: Any) -> dict:
        """图执行边界调用；申请写入失败必须抛出，不得 fire-and-forget。"""
        from backend.customer_service._db_loop import run_sync

        return run_sync(self.async_create_refund_request(**fields))

    # ── 内部 ──

    @staticmethod
    async def _get_for_update(db: Any, case_id: str, tenant_id: str) -> Any:
        from backend.customer_service.models.case import CSCase

        case = (
            await db.execute(
                select(CSCase).where(
                    CSCase.case_id == case_id,
                    CSCase.tenant_id == tenant_id,
                ).with_for_update().limit(1)
            )
        ).scalar_one_or_none()
        if case is None:
            raise BusinessRuleError(f"案件不存在: {case_id}")
        return case

    @staticmethod
    def _check_transition(case: Any, target: str, actor: str) -> None:
        allowed = _TRANSITIONS.get(case.status, ())
        if target not in allowed:
            raise BusinessRuleError(
                f"案件状态流转非法: {case.status} → {target}"
                f"{f'（actor={actor}）' if actor else ''}",
            )

    # ── 同步门面（Graph 节点经 _db_loop 调用；与 TicketStore 同款）──

    def create_sync(self, **fields: Any) -> dict | None:
        """建案（fire-and-forget 语义由调用方决定：失败返回 None 不阻断主流程）。"""
        try:
            from backend.customer_service._db_loop import run_sync

            return run_sync(self.async_create(**fields))
        except Exception:
            logger.warning("[CSCase] create failed", exc_info=True)
            return None

    def get_active_by_conversation_sync(
        self, conversation_id: str, case_type: str | None = None, *,
        tenant_id: str = "default",
    ) -> dict | None:
        try:
            from backend.customer_service._db_loop import run_sync

            return run_sync(self.async_get_active_by_conversation(
                conversation_id, case_type, tenant_id=tenant_id))
        except Exception:
            logger.warning("[CSCase] get_active failed", exc_info=True)
            return None


_service: CSCaseService | None = None


def get_case_service() -> CSCaseService:
    global _service
    if _service is None:
        _service = CSCaseService()
    return _service
