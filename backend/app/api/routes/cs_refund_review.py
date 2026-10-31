"""cs_refund_review.py — 退款审核（cs-workstation-v2 P5，supervisor 专属）

队列与审核动作的唯一生产入口：
  - GET  /cs/refunds/review-queue      本租户退款申请（http=Mock / sandbox=cs_case）
  - POST /cs/refunds/{request_id}/review  意见必填，终态不可回退；audit_logs 留痕

http 模式经 RefundService 调 business-service（approved 由订单系统回款钱包）；
sandbox 模式推进 cs_case 本地案件。审核属关键旁路：审计失败不阻断审核结果
返回（与图执行审计口径一致，失败记 store 指标并 log）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.routes.cs_agent_offers import (
    AgentContext,
    get_session,
    require_agent,
)
from backend.customer_service.errors import (
    BusinessRuleError,
    DatabaseError,
    RefundReviewConflictError,
    RefundRequestUnavailableError,
)
from backend.customer_service.service.refund_service import get_refund_service
from backend.shared.idempotency import SideEffectOutcomeUnknown
from backend.shared.logger import logger

router = APIRouter(tags=["智能客服-退款审核"])


class RefundReviewBody(BaseModel):
    decision: str = Field(pattern="^(approved|rejected)$")
    opinion: str = Field(min_length=1, max_length=500)

    @field_validator("opinion")
    @classmethod
    def _opinion_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("审核意见必填")
        return v


class RefundQueueItemDTO(BaseModel):
    request_id: str
    request_no: str | None = None
    status: str
    status_display: str | None = None
    order_no: str | None = None
    amount: str | float | None = None
    reason: str | None = None
    policy_version: str | None = None
    user_id: str | None = None
    conversation_id: str | None = None
    review: dict | None = None
    created_at: str | None = None
    updated_at: str | None = None


class RefundQueueResponse(BaseModel):
    items: list[RefundQueueItemDTO]
    total: int
    mode: str


class RefundReviewResponse(BaseModel):
    request_id: str
    status: str
    status_display: str | None = None
    review: dict | None = None


def _require_supervisor(context: AgentContext) -> None:
    if not context.is_supervisor:
        raise HTTPException(403, detail="退款审核需要客服主管或平台管理员权限")


def _item_dto(row: dict) -> RefundQueueItemDTO:
    return RefundQueueItemDTO(
        request_id=str(row.get("request_id") or ""),
        request_no=row.get("request_no"),
        status=str(row.get("status") or "unknown"),
        status_display=row.get("status_display"),
        order_no=row.get("order_no"),
        amount=row.get("amount"),
        reason=row.get("reason"),
        policy_version=row.get("policy_version"),
        user_id=row.get("user_id"),
        conversation_id=row.get("conversation_id"),
        review=row.get("review"),
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
    )


@router.get("/cs/refunds/review-queue", response_model=RefundQueueResponse)
async def refund_review_queue(
    status: str | None = None,
    limit: int = 50,
    context: AgentContext = Depends(require_agent),
) -> RefundQueueResponse:
    """本租户退款申请队列（supervisor）。"""
    _require_supervisor(context)
    mode = get_refund_service()._review_mode()
    if mode == "disabled":
        raise HTTPException(503, detail="退款审核通道未启用")
    try:
        if mode == "http":
            rows = get_refund_service().list_review_queue(
                tenant_id=context.tenant_id, status=status, limit=limit,
            )
        else:
            from backend.customer_service.case.service import get_case_service

            rows = await get_case_service().async_list_review_queue(
                tenant_id=context.tenant_id, status=status, limit=limit,
            )
    except DatabaseError as exc:
        raise HTTPException(503, detail=str(exc)) from exc
    except BusinessRuleError as exc:
        raise HTTPException(403, detail=exc.user_message) from exc
    items = [_item_dto(row) for row in rows]
    return RefundQueueResponse(items=items, total=len(items), mode=mode)


@router.post(
    "/cs/refunds/{request_id}/review",
    response_model=RefundReviewResponse,
)
async def review_refund(
    request_id: str,
    body: RefundReviewBody,
    context: AgentContext = Depends(require_agent),
    session: AsyncSession = Depends(get_session),
) -> RefundReviewResponse:
    """审核终态推进（supervisor）：意见必填；终态 409；audit_logs 留痕。"""
    _require_supervisor(context)
    mode = get_refund_service()._review_mode()
    if mode == "disabled":
        raise HTTPException(503, detail="退款审核通道未启用")

    try:
        if mode == "http":
            result = get_refund_service().review_refund_request(
                request_id=request_id,
                decision=body.decision,
                opinion=body.opinion,
                reviewer_id=context.agent_id,
            )
        else:
            from backend.customer_service.case.service import get_case_service

            result = await get_case_service().async_review_refund_request(
                request_id,
                tenant_id=context.tenant_id,
                decision=body.decision,
                opinion=body.opinion,
                reviewer_id=context.agent_id,
            )
    except RefundReviewConflictError as exc:
        raise HTTPException(409, detail=exc.user_message) from exc
    except RefundRequestUnavailableError as exc:
        raise HTTPException(404, detail=exc.user_message) from exc
    except SideEffectOutcomeUnknown:
        # 写结果未知：绝不伪装成功/失败，要求审核人刷新确认
        raise HTTPException(
            503,
            detail="审核结果未能确认，请刷新队列核对当前状态后再操作",
        ) from None
    except DatabaseError as exc:
        raise HTTPException(503, detail=str(exc)) from exc
    except BusinessRuleError as exc:
        raise HTTPException(404, detail=exc.user_message) from exc

    # 审计留痕（关键旁路：失败不阻断审核结果）
    try:
        from backend.customer_service.audit import build_audit_entry
        from backend.customer_service.repository.audit_repo import AuditRepository

        await AuditRepository(session).insert_audit_log({
            **build_audit_entry(
                context.user_id,
                "refund_review",
                "success",
                target_type="refund_request",
                target_id=request_id,
                detail=(
                    f"decision={body.decision}; reviewer={context.agent_id}; "
                    f"opinion={body.opinion}; mode={mode}"
                ),
            ),
            "actor_type": "human",
            "actor_id": context.agent_id,
            "before_state": {"status": "pending_review"},
            "after_state": {"status": body.decision},
        })
        await session.commit()
    except Exception:
        logger.error(
            "[RefundReview] audit persist failed: request=%s", request_id,
            exc_info=True,
        )

    return RefundReviewResponse(
        request_id=str(result.get("request_id") or request_id),
        status=str(result.get("status") or body.decision),
        status_display=result.get("status_display"),
        review=result.get("review"),
    )
