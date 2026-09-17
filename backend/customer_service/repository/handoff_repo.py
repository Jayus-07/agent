"""HandoffRepository — async CRUD for customer_service.handoffs"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import exists, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.customer_service.models.handoff import CSHandoff


class HandoffRepository:
    def __init__(self, session: AsyncSession):
        self._s = session

    async def load(self, user_id: str, conversation_id: str) -> CSHandoff | None:
        result = await self._s.execute(
            select(CSHandoff).where(
                CSHandoff.user_id == user_id,
                CSHandoff.conversation_id == conversation_id,
                CSHandoff.handoff_state != "closed",
            )
        )
        return result.scalar_one_or_none()

    async def save(
        self,
        user_id: str,
        conversation_id: str,
        handoff_data: dict,
    ) -> CSHandoff:
        handoff_id = handoff_data.get("handoff_id", str(uuid.uuid4()))
        obj = CSHandoff(
            handoff_id=handoff_id,
            conversation_id=conversation_id,
            user_id=user_id,
            handoff_state=handoff_data.get("handoff_state", "initiated"),
            trigger_type=handoff_data.get("trigger_type"),
            trigger_reason=handoff_data.get("trigger_reason"),
            ticket_id=handoff_data.get("ticket_id"),
        )
        self._s.add(obj)
        await self._s.flush()
        return obj

    async def update_state(
        self,
        handoff_id: str,
        state: str,
        closed_at: datetime | None = None,
    ) -> bool:
        values: dict = {"handoff_state": state}
        if closed_at is not None:
            values["closed_at"] = closed_at
        result = await self._s.execute(
            update(CSHandoff)
            .where(CSHandoff.handoff_id == handoff_id)
            .values(**values)
        )
        await self._s.flush()
        return result.rowcount > 0

    async def clear(self, user_id: str, conversation_id: str) -> bool:
        result = await self._s.execute(
            update(CSHandoff)
            .where(
                CSHandoff.user_id == user_id,
                CSHandoff.conversation_id == conversation_id,
                CSHandoff.handoff_state != "closed",
            )
            .values(
                handoff_state="closed",
                closed_at=datetime.now(timezone.utc),
            )
        )
        await self._s.flush()
        return result.rowcount > 0

    async def has_active(self, user_id: str) -> bool:
        result = await self._s.execute(
            select(
                exists().where(
                    CSHandoff.user_id == user_id,
                    CSHandoff.handoff_state != "closed",
                )
            )
        )
        return bool(result.scalar())

    async def get_active(self, user_id: str) -> CSHandoff | None:
        result = await self._s.execute(
            select(CSHandoff).where(
                CSHandoff.user_id == user_id,
                CSHandoff.handoff_state != "closed",
            ).limit(1)
        )
        return result.scalar_one_or_none()

    async def get_open_by_conversation(
        self, conversation_id: str
    ) -> CSHandoff | None:
        """按会话取未关闭的 handoff 行（坐席认领 / 发消息前校验用）。"""
        result = await self._s.execute(
            select(CSHandoff).where(
                CSHandoff.conversation_id == conversation_id,
                CSHandoff.handoff_state != "closed",
            ).limit(1)
        )
        return result.scalar_one_or_none()

    async def list_open(
        self,
        *,
        states: list[str] | None = None,
        limit: int = 50,
    ) -> list[CSHandoff]:
        """列出进行中的 handoff（坐席工作台队列）。

        Args:
            states: 只取这些状态（缺省=全部未关闭），按 updated_at 升序
                （最早请求的排最前，避免老会话饿死）。
        """
        q = select(CSHandoff)
        if states:
            q = q.where(CSHandoff.handoff_state.in_(states))
        else:
            q = q.where(CSHandoff.handoff_state != "closed")
        q = q.order_by(CSHandoff.updated_at).limit(limit)
        result = await self._s.execute(q)
        return list(result.scalars().all())

    async def close_stale(
        self,
        *,
        states: list[str],
        cutoff: datetime,
        limit: int = 100,
    ) -> list[dict]:
        """全局扫描：states 中 updated_at 早于 cutoff 的转接 → closed（P2.4）。

        原子条件 UPDATE（幂等）：beat 任务与运行时恢复（supervisor 的
        _recover_handoff_timeout）并发时只有一方生效。每次最多关 limit 条，
        由 beat 周期驱动逐步收敛。
        返回被关闭转接的 {handoff_id, user_id, conversation_id, handoff_state}。
        """
        result = await self._s.execute(
            update(CSHandoff)
            .where(
                CSHandoff.handoff_state.in_(states),
                CSHandoff.updated_at < cutoff,
            )
            .values(handoff_state="closed", closed_at=datetime.now(timezone.utc))
            .returning(
                CSHandoff.handoff_id,
                CSHandoff.user_id,
                CSHandoff.conversation_id,
                CSHandoff.handoff_state,
            )
        )
        await self._s.flush()
        return [
            {
                "handoff_id": r.handoff_id,
                "user_id": r.user_id,
                "conversation_id": r.conversation_id,
                "handoff_state": r.handoff_state,
            }
            for r in result.all()
        ]
