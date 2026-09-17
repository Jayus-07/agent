"""
customer_service/state_transition.py — 统一状态转换服务

所有 CS 状态变更（confirmation / handoff / conversation）的唯一入口。
职责:
  1. 接收 StateTransitionRequest
  2. 逐维度校验（调用各状态机 transition()）
  3. 写入 PostgreSQL（通过 Store / Repository）
  4. 返回 StateTransitionResult 快照

设计参考: docs/customer-service/langgraph-multi-expert-design.md §8
"""
from __future__ import annotations

import threading
from typing import Any, TypedDict

from backend.shared.logger import logger


class StateTransitionRequest(TypedDict, total=False):
    """状态转换请求

    所有字段可选 — 只传需要变更的维度。
    user_id + session_id + conversation_id 为必填定位键。
    """

    user_id: str
    session_id: str
    conversation_id: str

    confirmation_target: str
    handoff_target: str
    conversation_status_target: str
    handling_mode_target: str

    pending_action: dict | None
    assigned_agent_id: str | None


class StateTransitionResult(TypedDict, total=False):
    """状态转换结果

    包含转换后各维度的最新状态快照。
    """

    success: bool
    confirmation_state: str
    handoff_state: str
    conversation_status: str
    handling_mode: str
    pending_action: dict | None
    errors: list[str]


def _use_java_write() -> bool:
    """cutover 阶段 C 开关：java=状态写入权已移交 business-service。"""
    from backend.config.messaging import CS_WRITE_SOURCE
    return CS_WRITE_SOURCE == "java"


class StateTransitionService:
    """统一状态转换服务

    所有状态变更通过 apply() 入口，逐维度校验 + 持久化。
    load_snapshot() 从各 Store 读取当前状态，供 cs_state_loader 使用。

    写权模式（CS_WRITE_SOURCE）:
      - local: 本地 PostgreSQL 直写（现状）
      - java: 代理到 business-service /internal/*（Java 持有写权）。
              Java 不可用时返回失败，不静默回落本地写（保证写权唯一）。
    """

    def apply(self, request: StateTransitionRequest) -> StateTransitionResult:
        """执行状态转换（sync facade → _db_loop.run_sync）"""
        if _use_java_write():
            return self._java_apply(request)
        from backend.customer_service._db_loop import run_sync
        operation = self._async_apply(request)
        try:
            return run_sync(operation)
        except Exception:
            # run_sync 在提交协程前失败时，不会接管其生命周期；显式关闭，
            # 避免降级路径产生“coroutine was never awaited”告警。
            operation.close()
            logger.warning("[StateTransitionService] DB unavailable, returning error result")
            return StateTransitionResult(
                success=False,
                errors=["DB unavailable"],
            )

    def _java_apply(self, request: StateTransitionRequest) -> StateTransitionResult:
        """java 写权模式：代理到 business-service（请求/响应结构已对齐）"""
        from backend.infra.http.business_client import BusinessServiceError, post_json_sync

        try:
            result = post_json_sync("/internal/state-transitions", dict(request))
            return StateTransitionResult(
                success=bool(result.get("success", False)),
                confirmation_state=result.get("confirmation_state", ""),
                handoff_state=result.get("handoff_state", ""),
                conversation_status=result.get("conversation_status", ""),
                handling_mode=result.get("handling_mode", ""),
                pending_action=result.get("pending_action"),
                errors=list(result.get("errors") or []),
            )
        except BusinessServiceError as exc:
            logger.warning(f"[StateTransitionService] java write failed: {exc}")
            return StateTransitionResult(
                success=False,
                errors=[f"business-service unavailable: {exc}"],
            )

    async def _async_apply(
        self, request: StateTransitionRequest
    ) -> StateTransitionResult:
        """异步实现：逐维度 validate-then-write"""
        errors: list[str] = []
        confirmation_state = ""
        handoff_state = ""
        conversation_status = ""
        handling_mode = ""
        pending_action = request.get("pending_action")

        user_id = request.get("user_id", "")
        session_id = request.get("session_id", "")
        conversation_id = request.get("conversation_id", "")

        if "confirmation_target" in request:
            confirmation_state, err = await self._apply_confirmation(
                user_id, session_id, request["confirmation_target"], pending_action,
            )
            if err:
                errors.append(err)

        if "handoff_target" in request:
            handoff_state, err = await self._apply_handoff(
                user_id, session_id, request["handoff_target"],
            )
            if err:
                errors.append(err)

        if "conversation_status_target" in request or "handling_mode_target" in request:
            conversation_status, handling_mode, err = await self._apply_conversation(
                conversation_id,
                request.get("conversation_status_target"),
                request.get("handling_mode_target"),
                request.get("assigned_agent_id"),
            )
            if err:
                errors.append(err)

        if not errors:
            if not confirmation_state:
                confirmation_state = await self._load_confirmation_state(user_id, session_id)
            if not handoff_state:
                handoff_state = await self._load_handoff_state(user_id, session_id)
            if not conversation_status or not handling_mode:
                conv = await self._load_conversation_snapshot(conversation_id)
                if conv:
                    conversation_status = conversation_status or conv.get("conversation_status", "")
                    handling_mode = handling_mode or conv.get("handling_mode", "")

        result = StateTransitionResult(
            success=len(errors) == 0,
            confirmation_state=confirmation_state,
            handoff_state=handoff_state,
            conversation_status=conversation_status,
            handling_mode=handling_mode,
            pending_action=pending_action,
            errors=errors,
        )

        # 发布状态变更事件到 Kafka（fire-and-forget，Kafka 未启用时静默跳过）
        if len(errors) == 0:
            try:
                from backend.config.messaging import TOPIC_CONVERSATION_EVENTS
                from backend.infra.messaging.kafka import publish_event
                publish_event(
                    TOPIC_CONVERSATION_EVENTS,
                    "conversation.state_changed",
                    conversation_id or None,
                    user_id or None,
                    {
                        "confirmation_state": confirmation_state,
                        "handoff_state": handoff_state,
                        "conversation_status": conversation_status,
                        "handling_mode": handling_mode,
                    },
                )
            except Exception:
                logger.debug("[StateTransitionService] event publish failed", exc_info=True)

        return result

    async def _apply_confirmation(
        self,
        user_id: str,
        session_id: str,
        target: str,
        pending_action: dict | None,
    ) -> tuple[str, str]:
        """校验 + 写入 confirmation 维度"""
        from backend.customer_service.confirmation import ConfirmationState, transition
        from backend.customer_service.confirmation_store import get_confirmation_store

        store = get_confirmation_store()
        current_data = store.load(user_id, session_id)
        current_str = "not_required"
        if current_data:
            current_str = current_data.get("confirmation_state", "pending")

        try:
            current_enum = ConfirmationState(current_str)
            target_enum = ConfirmationState(target)
            transition(current_enum, target_enum)
        except Exception as exc:
            return current_str, str(exc)

        if pending_action is not None:
            updated = {**pending_action, "confirmation_state": target}
            store.save(user_id, session_id, updated)
        else:
            if current_data:
                updated = {**current_data, "confirmation_state": target}
                store.save(user_id, session_id, updated)

        return target, ""

    async def _apply_handoff(
        self,
        user_id: str,
        session_id: str,
        target: str,
    ) -> tuple[str, str]:
        """校验 + 写入 handoff 维度"""
        from backend.customer_service.handoff import HandoffState as HS, transition
        from backend.customer_service.handoff_store import get_handoff_store

        store = get_handoff_store()
        current_data = store.load(user_id, session_id)
        current_str = "ai_active"
        if current_data:
            current_str = current_data.get("handoff_state", "ai_active")

        try:
            current_enum = HS(current_str)
            target_enum = HS(target)
            transition(current_enum, target_enum)
        except Exception as exc:
            return current_str, str(exc)

        updated = {"handoff_state": target}
        if current_data:
            updated = {**current_data, **updated}
        store.save(user_id, session_id, updated)

        return target, ""

    async def _apply_conversation(
        self,
        conversation_id: str,
        status_target: str | None,
        mode_target: str | None,
        assigned_agent_id: str | None,
    ) -> tuple[str, str, str]:
        """校验 + 写入 conversation 维度"""
        from backend.customer_service.state_machine import (
            ConvStatus, HandlingMode, transition as conv_transition, apply as conv_apply,
        )

        conv = await self._load_conversation_orm(conversation_id)
        if conv is None:
            return "", "", f"Conversation {conversation_id} not found"

        try:
            new_status = ConvStatus(status_target) if status_target else None
            new_mode = HandlingMode(mode_target) if mode_target else None
            result = conv_transition(conv, new_status, new_mode)
            conv_apply(conv, result)

            from backend.memory.database import AsyncSessionLocal
            async with AsyncSessionLocal() as db:
                await db.merge(conv)
                await db.commit()

            return result.conversation_status.value, result.handling_mode.value, ""
        except Exception as exc:
            return (
                conv.conversation_status,
                conv.handling_mode,
                str(exc),
            )

    async def _load_conversation_orm(self, conversation_id: str) -> Any:
        """从 DB 加载 conversation ORM 对象"""
        from sqlalchemy import select
        from backend.customer_service.models.conversation import CSConversation
        from backend.memory.database import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(CSConversation).where(
                    CSConversation.conversation_id == conversation_id
                )
            )
            return result.scalar_one_or_none()

    async def _load_conversation_snapshot(self, conversation_id: str) -> dict | None:
        """从 DB 加载 conversation 状态快照"""
        conv = await self._load_conversation_orm(conversation_id)
        if conv is None:
            return None
        return {
            "conversation_status": conv.conversation_status,
            "handling_mode": conv.handling_mode,
        }

    async def _load_confirmation_state(self, user_id: str, session_id: str) -> str:
        from backend.customer_service.confirmation_store import get_confirmation_store
        store = get_confirmation_store()
        # P3.5：本方法运行在 _db_loop 线程，sync load() 的嵌套 run_sync
        # 会自死锁（10s 超时）——与 _async_load_snapshot_impl 同款修复
        data = store.peek_l1(user_id, session_id)
        if data is None:
            try:
                data = await store._async_load(user_id, session_id)
                if data is not None:
                    store.cache_l1(user_id, session_id, data)
            except Exception:
                data = None
        if data:
            return data.get("confirmation_state", "not_required")
        return "not_required"

    async def _load_handoff_state(self, user_id: str, session_id: str) -> str:
        from backend.customer_service.handoff_store import get_handoff_store
        store = get_handoff_store()
        data = store.peek_l1(user_id, session_id)
        if data is None:
            try:
                data = await store._async_load(user_id, session_id)
                if data is not None:
                    store.cache_l1(user_id, session_id, data)
            except Exception:
                data = None
        if data:
            return data.get("handoff_state", "ai_active")
        return "ai_active"

    def load_snapshot(
        self, user_id: str, session_id: str, conversation_id: str
    ) -> dict[str, Any]:
        """从各 Store 加载当前状态快照（sync facade）

        供 cs_state_loader 节点在 CS Graph 启动时调用。
        java 写权模式下从 business-service 读取（与写入同源，避免读写分裂）。
        """
        if _use_java_write():
            return self._java_load_snapshot(user_id, session_id, conversation_id)
        from backend.customer_service._db_loop import run_sync
        try:
            return run_sync(self._async_load_snapshot(user_id, session_id, conversation_id))
        except Exception:
            logger.warning("[StateTransitionService] load_snapshot failed, returning defaults")
            return _default_snapshot()

    def _java_load_snapshot(
        self, user_id: str, session_id: str, conversation_id: str
    ) -> dict[str, Any]:
        from backend.infra.http.business_client import BusinessServiceError, get_json_sync

        try:
            params = {"user_id": user_id, "session_id": session_id}
            if conversation_id:
                params["conversation_id"] = conversation_id
            return get_json_sync("/internal/state-snapshot", params=params)
        except BusinessServiceError as exc:
            logger.warning(f"[StateTransitionService] java load_snapshot failed: {exc}")
            return _default_snapshot()


async def _async_load_snapshot_impl(
    self: StateTransitionService,
    user_id: str,
    session_id: str,
    conversation_id: str,
) -> dict[str, Any]:
    """异步加载完整状态快照"""
    confirmation_state = await self._load_confirmation_state(user_id, session_id)
    handoff_state = await self._load_handoff_state(user_id, session_id)
    conv = await self._load_conversation_snapshot(conversation_id)

    pending_action = None
    from backend.customer_service.confirmation_store import get_confirmation_store
    store = get_confirmation_store()
    # P3.5 死锁修复：此处运行在 _db_loop 线程，若调 sync 的 store.load()
    # → 内部 run_sync 向同一 loop 提交协程并阻塞等待 = 自死锁（10s 超时
    # → 快照回默认 → pending 丢失 → 文本确认/取消全失效）。改为 L1 直读
    # + 复用 store 的 async 协程（同 loop await，无嵌套桥接）。
    conf_data = store.peek_l1(user_id, session_id)
    if conf_data is None:
        try:
            conf_data = await store._async_load(user_id, session_id)
            if conf_data is not None:
                store.cache_l1(user_id, session_id, conf_data)
        except Exception:
            conf_data = None
    if conf_data and conf_data.get("confirmation_state") == "pending":
        pending_action = conf_data

    return {
        "conversation_status": conv.get("conversation_status", "open") if conv else "open",
        "handling_mode": conv.get("handling_mode", "ai") if conv else "ai",
        "handoff_state": handoff_state,
        "confirmation_state": confirmation_state,
        "pending_action": pending_action,
    }


StateTransitionService._async_load_snapshot = _async_load_snapshot_impl  # type: ignore[attr-defined]


def _default_snapshot() -> dict[str, Any]:
    """DB 不可用时的默认快照"""
    return {
        "conversation_status": "open",
        "handling_mode": "ai",
        "handoff_state": "ai_active",
        "confirmation_state": "not_required",
        "pending_action": None,
    }


_service_instance: StateTransitionService | None = None
_service_lock = threading.Lock()


def get_state_transition_service() -> StateTransitionService:
    """获取 StateTransitionService 单例"""
    global _service_instance
    if _service_instance is None:
        with _service_lock:
            if _service_instance is None:
                _service_instance = StateTransitionService()
    return _service_instance
