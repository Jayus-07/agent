"""客服用户入池 API。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.identity import Identity, require_identity
from backend.customer_service.dispatch.service import (
    ConversationForbidden,
    ConversationNotFound,
    HandoffConflict,
    HandoffResult,
    create_or_reuse_handoff,
)
from backend.memory.database import AsyncSessionLocal, MemoryDatabaseUnavailable

router = APIRouter(prefix="/cs/conversations", tags=["智能客服-派单"])


async def get_session():
    """为路由提供独立请求事务。"""
    try:
        async with AsyncSessionLocal() as session:
            yield session
    except MemoryDatabaseUnavailable as exc:
        # 依赖在进入 endpoint 前执行，不能依赖 endpoint 内部的 try 块
        # 或仅依赖完整 server 的全局异常处理器。
        raise HTTPException(503, detail="Database unavailable") from exc


class HandoffResponse(BaseModel):
    handoff_id: str
    conversation_id: str
    handoff_state: str
    total_deadline_at: str | None
    reused: bool


@router.post("/{conversation_id}/handoff", response_model=HandoffResponse)
async def request_handoff(
    conversation_id: str,
    identity: Identity = Depends(require_identity),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(
        default=None,
        alias="Idempotency-Key",
    ),
) -> HandoffResponse:
    """用户显式请求人工服务，直接写入等待队列。"""
    key = (idempotency_key or "").strip()
    if not key or len(key) > 128:
        raise HTTPException(400, detail="Idempotency-Key 必须为 1-128 个字符")

    if not identity.tenant_id:
        raise HTTPException(403, detail="缺少可信租户身份")

    try:
        result = await create_or_reuse_handoff(
            session=session,
            conversation_id=conversation_id,
            user_id=identity.user_id,
            tenant_id=identity.tenant_id,
            idempotency_key=key,
        )
    except ConversationNotFound as exc:
        raise HTTPException(404, detail=str(exc)) from exc
    except ConversationForbidden as exc:
        raise HTTPException(403, detail=str(exc)) from exc
    except HandoffConflict as exc:
        raise HTTPException(409, detail=str(exc)) from exc
    except MemoryDatabaseUnavailable as exc:
        raise HTTPException(503, detail="Database unavailable") from exc

    return _response(result)


def _response(result: HandoffResult) -> HandoffResponse:
    return HandoffResponse(
        handoff_id=result.handoff_id,
        conversation_id=result.conversation_id,
        handoff_state=result.handoff_state,
        total_deadline_at=(
            result.total_deadline_at.isoformat()
            if result.total_deadline_at is not None
            else None
        ),
        reused=result.reused,
    )
