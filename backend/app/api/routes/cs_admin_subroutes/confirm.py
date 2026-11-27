"""CS Admin 事务确认卡片与退款申请子路由。"""
from __future__ import annotations

import asyncio
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request

from backend.app.api.routes.cs_admin_subroutes.base import facade, logger
from backend.app.api.routes.cs_admin_subroutes.schemas import (
    ConfirmActionBody,
    RefundDraftUpdateBody,
)

router = APIRouter()

@router.post("/confirm")
async def confirm_pending_action(request: Request, body: ConfirmActionBody) -> dict:
    """确认卡片幂等端点 — 用户点击确认/取消按钮的入口。

    复用 confirmation_flow.process_confirmation 唯一实现（文本路径）：
    原子认领闸门保证并发双击/重复提交只有一次执行（P0-3 幂等语义）。
    无待确认项 → 409（卡片已失效，前端清掉即可）。
    """
    decision = (body.decision or "").strip().lower()
    if decision not in ("confirm", "cancel"):
        raise HTTPException(422, detail="decision 必须为 confirm 或 cancel")
    contract_fields = (
        body.proposal_id, body.expected_version, body.client_action_id,
    )
    if any(value is not None for value in contract_fields) and not all(
        value is not None for value in contract_fields
    ):
        raise HTTPException(422, detail="proposal_id、expected_version 与 client_action_id 必须同时提供")

    from backend.app.api.identity import resolve_identity
    ident = resolve_identity(request)
    user_id = (getattr(ident, "user_id", "") or "").strip()
    if (
        not user_id
        or user_id == "anonymous"
        or not getattr(ident, "authenticated", False)
    ):
        raise HTTPException(401, detail="确认操作需要已认证用户身份")
    tenant_id = (getattr(ident, "tenant_id", "") or "").strip()
    if not tenant_id:
        raise HTTPException(403, detail="确认操作需要有效租户身份")
    if body.proposal_id is None:
        # 旧客户端只提交 session_id，无法证明用户看到的版本；
        # 若直接接受，可能确认掉替换了原提案的新操作。
        raise HTTPException(409, detail="确认卡片缺少版本信息，请刷新后重试")

    from backend.customer_service.confirmation_flow import process_confirmation
    from backend.customer_service.confirmation_store import get_confirmation_store
    from backend.customer_service.confirmation_store import StoreWriteError
    from backend.customer_service.handoff.lifecycle import load_active_handoff_sync

    try:
        handoff = load_active_handoff_sync(
            tenant_id, body.session_id, raise_on_error=True,
        )
    except Exception as exc:
        logger.error("[CSConfirm] Handoff state unavailable; refusing confirmation")
        raise HTTPException(503, detail="人工服务状态暂不可核验，请稍后重试") from exc
    if handoff and handoff.get("handoff_state") in {
        "handoff_requested", "waiting_human", "agent_offered", "human_active",
    }:
        raise HTTPException(409, detail="人工客服已接管该会话，当前确认操作已暂停")

    store = get_confirmation_store()
    try:
        pending = store.load_authoritative(user_id, body.session_id, tenant_id)
    except StoreWriteError as exc:
        raise HTTPException(503, detail="待确认状态暂不可核验，请稍后重试") from exc
    if not pending:
        raise HTTPException(409, detail="当前没有待确认的操作（可能已处理或已过期）")
    if pending.get("tenant_id") != tenant_id:
        raise HTTPException(409, detail="待确认操作与当前租户不匹配")

    current_proposal_id = str(
        pending.get("proposal_id") or pending.get("action_id") or "",
    )
    current_version = int(pending.get("version", 1) or 1)
    if body.proposal_id is not None and (
        body.proposal_id != current_proposal_id
        or body.expected_version != current_version
    ):
        raise HTTPException(409, detail="待确认操作已更新或失效，请刷新后重试")

    client_action_id = str(body.client_action_id) if body.client_action_id else None
    try:
        outcome = process_confirmation(
            pending,
            "确认" if decision == "confirm" else "取消",
            user_id,
            body.session_id,
            tenant_id=tenant_id,
            proposal_id=current_proposal_id,
            expected_version=current_version,
            client_action_id=client_action_id,
        )
    except StoreWriteError as exc:
        raise HTTPException(503, detail="确认状态写入失败，操作未执行") from exc
    if outcome.kind == "duplicate":
        raise HTTPException(409, detail="该确认已被处理，请刷新会话状态")

    if outcome.kind == "failed":
        logger.warning(
            "[CSConfirm] card action failed: user=%s session=%s decision=%s answer=%s",
            user_id, body.session_id, decision, outcome.answer,
        )
    else:
        logger.info(
            "[CSConfirm] card action: user=%s session=%s decision=%s → %s",
            user_id, body.session_id, decision, outcome.kind,
        )
    return {
        "status": outcome.kind,
        "answer": outcome.answer,
        "confirmation_state": outcome.confirmation_state,
        "action_result": outcome.action_result,
        "proposal_id": current_proposal_id,
        "version": current_version,
        "client_action_id": client_action_id,
    }


@router.patch("/conversations/my/{conversation_id}/refund-draft")
async def update_my_refund_draft(
    request: Request,
    conversation_id: str,
    body: RefundDraftUpdateBody,
) -> dict:
    """只编辑本人 pending 退款申请的原因，并以 proposal 版本 CAS 提交。"""
    await facade._ensure_my_conversation(request, conversation_id)
    from backend.app.api.identity import resolve_identity

    ident = resolve_identity(request)
    user_id = (getattr(ident, "user_id", "") or "").strip()
    tenant_id = (getattr(ident, "tenant_id", "") or "").strip()
    if not ident.authenticated or not user_id:
        raise HTTPException(401, detail="修改申请需要已认证用户身份")
    # default 是注册账号的正式租户值，不再视为缺失身份（2026-11-03）。
    if not tenant_id:
        raise HTTPException(403, detail="修改申请需要有效租户身份")

    from backend.config import customer_service as cfg
    if (
        str(getattr(cfg, "CS_REFUND_REQUEST_MODE", "disabled")).lower()
        != "sandbox"
        or str(getattr(cfg, "CS_BUSINESS_GATEWAY_MODE", "")).lower()
        != "sandbox"
    ):
        raise HTTPException(409, detail="当前业务通道不支持在线修改退款申请")

    from backend.customer_service.confirmation_store import (
        StoreWriteError,
        get_confirmation_store,
    )
    from backend.customer_service.handoff.lifecycle import load_active_handoff_sync

    try:
        handoff = load_active_handoff_sync(
            tenant_id, conversation_id, raise_on_error=True,
        )
    except Exception as exc:
        raise HTTPException(503, detail="人工服务状态暂不可核验，请稍后重试") from exc
    if handoff and handoff.get("handoff_state") in {
        "handoff_requested", "waiting_human", "agent_offered", "human_active",
    }:
        raise HTTPException(409, detail="人工客服已接管该会话，当前申请修改已暂停")

    store = get_confirmation_store()
    try:
        pending = store.load_authoritative(user_id, conversation_id, tenant_id)
    except StoreWriteError as exc:
        raise HTTPException(503, detail="待确认状态暂不可核验，请稍后重试") from exc
    current_id = str(
        (pending or {}).get("proposal_id") or (pending or {}).get("action_id") or "",
    )
    current_version = int((pending or {}).get("version", 1) or 1)
    if (
        not pending
        or pending.get("action_type") != "refund_request"
        or current_id != body.proposal_id
        or current_version != body.expected_version
    ):
        raise HTTPException(409, detail="退款申请已更新或失效，请刷新后重试")

    expires_at = pending.get("expires_at")
    if expires_at:
        from datetime import datetime, timezone

        try:
            expiry = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            if expiry <= datetime.now(timezone.utc):
                raise HTTPException(409, detail="退款申请确认已过期，请重新发起")
        except ValueError as exc:
            raise HTTPException(409, detail="退款申请确认状态无效，请重新发起") from exc

    order_id = str(pending.get("target_id") or "").strip()
    reason = body.reason.strip()
    if not order_id or not reason:
        raise HTTPException(422, detail="退款原因不能为空")
    try:
        from decimal import Decimal, InvalidOperation

        from backend.customer_service.action import build_pending_action
        from backend.customer_service.service.refund_service import get_refund_service

        proposal = get_refund_service().build_refund_proposal(
            user_id, order_id, reason,
        )
        previous_facts = pending.get("before_state")
        rebuilt_facts = proposal.before_state
        try:
            previous_amount = Decimal(str(previous_facts.get("amount"))).quantize(
                Decimal("0.01"),
            )
            rebuilt_amount = Decimal(str(rebuilt_facts.get("amount"))).quantize(
                Decimal("0.01"),
            )
        except (AttributeError, InvalidOperation, ValueError, TypeError):
            raise HTTPException(409, detail="原退款金额暂不可核验，请重新发起申请") from None
        if (
            str(previous_facts.get("order_id") or "")
            != str(rebuilt_facts.get("order_id") or "")
            or str(previous_facts.get("status") or "")
            != str(rebuilt_facts.get("status") or "")
            or previous_amount != rebuilt_amount
            or str(previous_facts.get("policy_version") or "")
            != str(rebuilt_facts.get("policy_version") or "")
        ):
            raise HTTPException(
                409,
                detail="订单状态、金额或退款政策已变化，请重新发起申请。",
            )
        updated = build_pending_action(proposal)
        updated.update({
            "action_id": current_id,
            "proposal_id": current_id,
            "version": current_version + 1,
            "tenant_id": tenant_id,
            "created_at": pending.get("created_at"),
            "expires_at": expires_at,
            "confirmation_state": "pending",
        })
    except Exception as exc:
        from backend.customer_service.errors import (
            DatabaseError,
            OrderNotEligibleError,
            OrderNotFoundError,
        )

        if isinstance(exc, (OrderNotEligibleError, OrderNotFoundError)):
            raise HTTPException(409, detail=getattr(exc, "user_message", str(exc))) from exc
        if isinstance(exc, DatabaseError):
            raise HTTPException(503, detail="订单或退款政策暂不可核验，请稍后重试") from exc
        raise

    try:
        saved = store.update_pending_if_version(
            user_id,
            conversation_id,
            updated,
            tenant_id=tenant_id,
            proposal_id=current_id,
            expected_version=current_version,
        )
    except StoreWriteError as exc:
        raise HTTPException(503, detail="退款申请修改结果暂不可核验，请刷新状态") from exc
    if not saved:
        raise HTTPException(409, detail="退款申请已更新或正在处理，请刷新后重试")
    return {
        "updated": True,
        "proposal_id": current_id,
        "version": current_version + 1,
    }


@router.get("/refund-requests")
async def list_my_refund_requests(request: Request, limit: int = Query(20, ge=1, le=100)) -> dict:
    """查询登录用户本人、当前租户的退款申请。

    P5 双模式分流：http 网关 = business-service 为事实源（审核结果实时可见）；
    sandbox = 本地 cs_case 案件投影。
    """
    from backend.app.api.identity import resolve_identity

    ident = resolve_identity(request)
    user_id = (getattr(ident, "user_id", "") or "").strip()
    tenant_id = (getattr(ident, "tenant_id", "") or "").strip()
    if not ident.authenticated or not user_id:
        raise HTTPException(401, detail="查询退款申请需要已认证用户身份")
    # default 是注册账号的正式租户值，不再视为缺失身份（2026-11-03）。
    if not tenant_id:
        raise HTTPException(403, detail="查询退款申请需要有效租户身份")
    from backend.customer_service.service.refund_service import get_refund_service

    if get_refund_service()._review_mode() == "http":
        try:
            rows = get_refund_service().list_review_queue(
                tenant_id=tenant_id, limit=limit,
            )
        except Exception as exc:
            logger.warning("[CSAdmin] list refund requests (http) failed", exc_info=True)
            raise HTTPException(503, detail="退款申请状态暂不可用") from exc
        items = [row for row in rows if str(row.get("user_id") or "") == user_id]
        return {"items": items, "total": len(items)}

    from backend.customer_service.case.service import get_case_service
    try:
        items = await get_case_service().async_list_refund_requests(
            user_id=user_id, tenant_id=tenant_id, limit=limit,
        )
    except Exception as exc:
        logger.warning("[CSAdmin] list refund requests failed", exc_info=True)
        raise HTTPException(503, detail="退款申请状态暂不可用") from exc
    return {"items": items, "total": len(items)}


@router.get("/refund-requests/{request_id}")
async def get_my_refund_request(request: Request, request_id: str) -> dict:
    """按稳定申请 ID 查询本人申请；跨用户/租户统一返回 404。"""
    from backend.app.api.identity import resolve_identity

    ident = resolve_identity(request)
    user_id = (getattr(ident, "user_id", "") or "").strip()
    tenant_id = (getattr(ident, "tenant_id", "") or "").strip()
    if not ident.authenticated or not user_id:
        raise HTTPException(401, detail="查询退款申请需要已认证用户身份")
    # default 是注册账号的正式租户值，不再视为缺失身份（2026-11-03）。
    if not tenant_id:
        raise HTTPException(403, detail="查询退款申请需要有效租户身份")
    from backend.customer_service.service.refund_service import get_refund_service

    if get_refund_service()._review_mode() == "http":
        try:
            rows = get_refund_service().list_review_queue(
                tenant_id=tenant_id, limit=200,
            )
        except Exception as exc:
            logger.warning("[CSAdmin] get refund request (http) failed", exc_info=True)
            raise HTTPException(503, detail="退款申请状态暂不可用") from exc
        for row in rows:
            if (
                str(row.get("request_id") or "") == request_id
                and str(row.get("user_id") or "") == user_id
            ):
                return row
        raise HTTPException(404, detail="未找到本人退款申请")

    from backend.customer_service.case.service import get_case_service
    try:
        item = await get_case_service().async_get_refund_request(
            request_id, user_id=user_id, tenant_id=tenant_id,
        )
    except Exception as exc:
        logger.warning("[CSAdmin] get refund request failed", exc_info=True)
        raise HTTPException(503, detail="退款申请状态暂不可用") from exc
    if item is None:
        raise HTTPException(404, detail="未找到本人退款申请")
    return item

