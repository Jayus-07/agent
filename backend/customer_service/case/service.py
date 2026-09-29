"""customer_service/case/service.py — 统一案件服务（迁移 B12 核心闭环）

设计方案 §10.1 cs_case + §14.2「case 是唯一案件事实源」。本服务承载核心
生命周期闭环：**创建 → 分配 → 处理 → 关闭**；SLA 按 P0/P1/P2 自动计算；
同会话同类型活跃案件防重入由 058 部分唯一索引在 DB 决胜（并发不先查后插），
应用层把 IntegrityError 转译为幂等复用（对齐 051 守卫模式）。

过渡策略：与 tickets 表并存（complaint 源先行并行写入），ticket/after_sales
全面并表走后续批次。状态流转白名单 fail-fast；结案 resolution 必填。
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from backend.customer_service.errors import BusinessRuleError
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
