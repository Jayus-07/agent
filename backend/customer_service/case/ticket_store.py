"""customer_service/ticket_store.py — 统一工单读写层（批次C）。

职责：tickets 表的创建 / 查询 / 状态流转 / 收编辅助。
模式：与 handoff_store 同款 —— sync facade（图节点线程调用）+
async 实现（_db_loop 上执行 AsyncSessionLocal）。

设计约束：
  - 非法状态流转抛 BusinessRuleError（复用 errors 既有语义，路由层 409）
  - 状态流转写 audit（复用 customer_service/audit.py 记录，失败不阻断）
  - 写点收编：投诉工单落库、handoff 关联工单（见 experts/ 两处调用）
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select

from backend.customer_service.errors import BusinessRuleError
from backend.shared.logger import logger


def _now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


def new_ticket_id(prefix: str = "TCK") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8].upper()}"


def validate_transition(current: str, target: str) -> None:
    """校验工单状态流转；非法流转抛 BusinessRuleError。"""
    from backend.customer_service.models.ticket import (
        VALID_TICKET_TRANSITIONS,
    )

    allowed = VALID_TICKET_TRANSITIONS.get(current)
    if allowed is None:
        raise BusinessRuleError(f"未知工单状态: {current}")
    if target not in allowed:
        raise BusinessRuleError(
            f"工单状态不允许从 {current} 流转到 {target}",
        )


class TicketStore:
    """tickets 表读写（单例，见 get_ticket_store）。"""

    # ── sync facade（图节点 / 同步上下文调用）──────────────

    def create_sync(self, **fields: Any) -> dict | None:
        """同步创建工单；DB 不可用返回 None（不阻断专家主流程）。"""
        try:
            from backend.customer_service._db_loop import run_sync

            return run_sync(self.async_create(**fields))
        except Exception:
            logger.warning(
                "[TicketStore] create failed (fire-and-forget)",
                exc_info=True,
            )
            return None

    def get_sync(self, ticket_id: str, tenant_id: str = "default") -> dict | None:
        try:
            from backend.customer_service._db_loop import run_sync

            return run_sync(self.async_get(ticket_id, tenant_id))
        except Exception:
            logger.warning("[TicketStore] get failed", exc_info=True)
            return None

    def list_for_user_sync(
        self, user_id: str, tenant_id: str = "default", limit: int = 20,
    ) -> list[dict]:
        try:
            from backend.customer_service._db_loop import run_sync

            return run_sync(
                self.async_list_for_user(user_id, tenant_id, limit),
            )
        except Exception:
            logger.warning("[TicketStore] list failed", exc_info=True)
            return []

    def transition_sync(
        self, ticket_id: str, target_status: str,
        *, tenant_id: str = "default", actor: str = "",
        resolution: str | None = None,
    ) -> dict:
        """同步状态流转；DB 不可用/非法流转抛 BusinessRuleError（路由层转 409）。"""
        from backend.customer_service._db_loop import run_sync

        return run_sync(
            self.async_transition(
                ticket_id, target_status,
                tenant_id=tenant_id, actor=actor, resolution=resolution,
            ),
        )

    # ── async 实现（_db_loop / FastAPI 事件循环）────────────

    async def async_create(self, **fields: Any) -> dict:
        from backend.customer_service.models.ticket import (
            TICKET_PRIORITIES, TICKET_SOURCES, TICKET_STATUSES, TICKET_TYPES,
            CSTicket,
        )
        from backend.memory.database import AsyncSessionLocal

        for field, allowed in (
            ("type", TICKET_TYPES), ("status", TICKET_STATUSES),
            ("source", TICKET_SOURCES), ("priority", TICKET_PRIORITIES),
        ):
            value = fields.get(field)
            if value is not None and value not in allowed:
                raise BusinessRuleError(f"工单 {field} 非法: {value}")

        async with AsyncSessionLocal() as db, db.begin():
            ticket = CSTicket(**fields)
            db.add(ticket)
            await db.flush()
            return _to_dict(ticket)

    async def async_get(
        self, ticket_id: str, tenant_id: str = "default",
    ) -> dict | None:
        from backend.customer_service.models.ticket import CSTicket
        from backend.memory.database import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            ticket = (
                await db.execute(
                    select(CSTicket).where(
                        CSTicket.ticket_id == ticket_id,
                        CSTicket.tenant_id == tenant_id,
                    ).limit(1)
                )
            ).scalar_one_or_none()
            return _to_dict(ticket) if ticket else None

    async def async_list_for_user(
        self, user_id: str, tenant_id: str = "default", limit: int = 20,
    ) -> list[dict]:
        from backend.customer_service.models.ticket import CSTicket
        from backend.memory.database import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            tickets = (
                await db.execute(
                    select(CSTicket)
                    .where(
                        CSTicket.user_id == user_id,
                        CSTicket.tenant_id == tenant_id,
                    )
                    .order_by(CSTicket.created_at.desc())
                    .limit(limit)
                )
            ).scalars().all()
            return [_to_dict(t) for t in tickets]

    async def async_list_admin(
        self, tenant_id: str = "default", *,
        status: str | None = None, type: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        from backend.customer_service.models.ticket import CSTicket
        from backend.memory.database import AsyncSessionLocal

        conditions = [CSTicket.tenant_id == tenant_id]
        if status:
            conditions.append(CSTicket.status == status)
        if type:
            conditions.append(CSTicket.type == type)
        async with AsyncSessionLocal() as db:
            tickets = (
                await db.execute(
                    select(CSTicket)
                    .where(*conditions)
                    .order_by(CSTicket.created_at.desc())
                    .limit(limit)
                )
            ).scalars().all()
            return [_to_dict(t) for t in tickets]

    async def async_transition(
        self, ticket_id: str, target_status: str,
        *, tenant_id: str = "default", actor: str = "",
        resolution: str | None = None,
    ) -> dict:
        from datetime import timezone

        from backend.customer_service.models.ticket import (
            TICKET_STATUSES,
            CSTicket,
        )
        from backend.memory.database import AsyncSessionLocal

        if target_status not in TICKET_STATUSES:
            raise BusinessRuleError(f"未知工单目标状态: {target_status}")

        async with AsyncSessionLocal() as db, db.begin():
            ticket = (
                await db.execute(
                    select(CSTicket).where(
                        CSTicket.ticket_id == ticket_id,
                        CSTicket.tenant_id == tenant_id,
                    ).limit(1)
                )
            ).scalar_one_or_none()
            if ticket is None:
                raise BusinessRuleError(f"工单不存在: {ticket_id}")

            old_status = ticket.status
            validate_transition(old_status, target_status)

            ticket.status = target_status
            ticket.updated_at = _now()
            if resolution is not None:
                ticket.resolution = resolution
            if target_status == "resolved":
                ticket.resolved_at = _now()
            if target_status == "closed":
                ticket.closed_at = _now()
                if ticket.resolved_at is None:
                    ticket.resolved_at = _now()

            result = _to_dict(ticket)

        logger.info(
            "[TicketStore] transition %s: %s -> %s (actor=%s)",
            ticket_id, old_status, target_status, actor or "-",
        )
        return result

    async def async_count_by_type(
        self, tenant_id: str = "default",
    ) -> dict[str, int]:
        """按 type 计数（质检日报用）。"""
        from backend.customer_service.models.ticket import CSTicket
        from backend.memory.database import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            rows = (
                await db.execute(
                    select(CSTicket.type, func.count())
                    .where(CSTicket.tenant_id == tenant_id)
                    .group_by(CSTicket.type)
                )
            ).all()
            return {row[0]: int(row[1]) for row in rows}


def _to_dict(ticket) -> dict:
    return {
        "ticket_id": ticket.ticket_id,
        "tenant_id": ticket.tenant_id,
        "type": ticket.type,
        "status": ticket.status,
        "source": ticket.source,
        "conversation_id": ticket.conversation_id,
        "user_id": ticket.user_id,
        "handoff_id": ticket.handoff_id,
        "assigned_agent_id": ticket.assigned_agent_id,
        "priority": ticket.priority,
        "title": ticket.title,
        "description": ticket.description,
        "resolution": ticket.resolution,
        "severity": ticket.severity,
        "created_at": (
            ticket.created_at.isoformat() if ticket.created_at else ""
        ),
        "updated_at": (
            ticket.updated_at.isoformat() if ticket.updated_at else ""
        ),
        "resolved_at": (
            ticket.resolved_at.isoformat() if ticket.resolved_at else None
        ),
        "closed_at": (
            ticket.closed_at.isoformat() if ticket.closed_at else None
        ),
    }


_store: TicketStore | None = None
_store_lock = None


def get_ticket_store() -> TicketStore:
    global _store, _store_lock
    import threading

    if _store_lock is None:
        _store_lock = threading.Lock()
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = TicketStore()
    return _store
