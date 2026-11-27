"""Travel 会话消息历史管理与持久化记忆快照。"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from fastapi import HTTPException, Request

from backend.app.api.identity import require_identity
from .base import facade
from .schemas import (
    TravelConversationMessage,
    TravelConversationMessagesRequest,
)

def _travel_message_session_id(
    conversation_id: str, user_id: str, tenant_id: str,
) -> str:
    """映射到无 schema 变更的 memory 会话键，并绑定用户与租户。"""
    conversation_id = (conversation_id or "").strip()
    if not conversation_id or len(conversation_id) > 128:
        raise HTTPException(status_code=422, detail="会话标识无效")
    tenant = (tenant_id or "").strip()
    if not tenant:
        raise HTTPException(status_code=403, detail="缺少可信租户身份")
    scope = "\0".join((tenant, user_id, conversation_id))
    digest = hashlib.sha256(scope.encode("utf-8")).hexdigest()
    return f"travel:{digest}"


def _travel_message_owner_id(user_id: str, tenant_id: str) -> str:
    """给无 tenant_id 列的共享 memory 表提供租户隔离属主键。"""
    tenant = (tenant_id or "").strip()
    if not tenant:
        raise HTTPException(status_code=403, detail="缺少可信租户身份")
    scope = f"{tenant}\0{user_id}"
    digest = hashlib.sha256(scope.encode("utf-8")).hexdigest()
    return f"travel:{digest[:57]}"  # ChatSession.user_id 列宽为 64


def _run_travel_memory(operation):
    """沿用 memory_manager 后台 loop，避免跨 loop 使用 asyncpg engine。"""
    from backend.memory.manager import memory_manager
    from backend.memory.service import MemoryService

    return memory_manager.run_tool(lambda: operation(MemoryService()))


def travel_conversation_messages(conversation_id: str, request: Request):
    identity = facade.require_identity(request)
    user_id = identity.user_id
    tenant_id = facade._travel_tenant(request, identity)
    memory_owner_id = facade._travel_message_owner_id(user_id, tenant_id)
    memory_session_id = facade._travel_message_session_id(
        conversation_id, user_id, tenant_id,
    )
    result = facade._run_travel_memory(
        lambda service: service.get_session_messages(
            memory_session_id, user_id=memory_owner_id,
        )
    )
    if result.get("error") == "会话不存在":
        # 新会话和无权会话对外同语义，避免泄露内部存储键是否存在。
        return {"conversation_id": conversation_id, "messages": []}
    if result.get("error"):
        raise HTTPException(status_code=503, detail="旅游对话历史暂不可用")
    return {
        "conversation_id": conversation_id,
        "messages": result.get("messages", []),
    }


def replace_travel_conversation_messages(
    conversation_id: str,
    payload: TravelConversationMessagesRequest,
    request: Request,
):
    identity = facade.require_identity(request)
    user_id = identity.user_id
    tenant_id = facade._travel_tenant(request, identity)
    memory_owner_id = facade._travel_message_owner_id(user_id, tenant_id)
    memory_session_id = facade._travel_message_session_id(
        conversation_id, user_id, tenant_id,
    )
    result = facade._run_travel_memory(
        lambda service: service.replace_session_messages(
            memory_session_id,
            [message.model_dump() for message in payload.messages],
            user_id=memory_owner_id,
        )
    )
    if result.get("error") == "会话不存在":
        raise HTTPException(status_code=404, detail="会话不存在")
    if result.get("error"):
        raise HTTPException(status_code=503, detail="旅游对话历史暂不可用")
    return {"conversation_id": conversation_id, "saved": result.get("saved", 0)}


facade.register("_travel_message_session_id", _travel_message_session_id)
facade.register("_travel_message_owner_id", _travel_message_owner_id)
facade.register("_run_travel_memory", _run_travel_memory)
facade.register("travel_conversation_messages", travel_conversation_messages)
facade.register("replace_travel_conversation_messages", replace_travel_conversation_messages)
