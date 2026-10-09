"""ConfirmationRepository — async CRUD for customer_service.confirmations

P1 重构（2026-09-17）：
  - claim_pending / finalize_current 提供原子条件更新（幂等基础）。
    此前 update_state 是无前置条件的裸 UPDATE，重复确认会双执行。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import exists, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.customer_service.models.confirmation import CSConfirmation
from backend.customer_service.models.conversation import CSConversation
from backend.customer_service.models.handoff import CSHandoff

# 可被 finalize 的中间态（pending = 未认领；confirmed/executing = 已认领执行中）
_ACTIVE_CONFIRM_STATES = ("pending", "confirmed", "executing")


class ConfirmationRepository:
    def __init__(self, session: AsyncSession):
        self._s = session

    async def load(
        self, user_id: str, conversation_id: str, *, tenant_id: str | None = None,
    ) -> CSConfirmation | None:
        filters = [
            CSConfirmation.user_id == user_id,
            CSConfirmation.conversation_id == conversation_id,
            CSConfirmation.state == "pending",
        ]
        query = select(CSConfirmation)
        if tenant_id is not None:
            # 兼容早期 need_info 占位记录：它们没有业务 target，旧写路径
            # 因而可能 tenant_id=NULL。只在父会话已绑定本用户/租户时读回，
            # 后续 proposal 升级会在同一事务内补齐确认行的 tenant_id。
            query = query.join(
                CSConversation,
                CSConversation.conversation_id == CSConfirmation.conversation_id,
            )
            filters.extend((
                or_(
                    CSConfirmation.tenant_id == tenant_id,
                    CSConfirmation.tenant_id.is_(None),
                ),
                CSConversation.user_id == user_id,
                CSConversation.tenant_id == tenant_id,
            ))
        result = await self._s.execute(query.where(*filters))
        return result.scalar_one_or_none()

    async def save(
        self,
        user_id: str,
        conversation_id: str,
        pending_action: dict,
        tenant_id: str = "",
        semantic_fingerprint: str | None = None,
    ) -> CSConfirmation:
        confirmation_id = pending_action.get("action_id", str(uuid.uuid4()))
        proposal_version = int(pending_action.get("version", 1) or 1)
        proposal = dict(pending_action)
        proposal.setdefault("proposal_id", confirmation_id)
        proposal["version"] = proposal_version
        obj = CSConfirmation(
            confirmation_id=confirmation_id,
            conversation_id=conversation_id,
            user_id=user_id,
            action_type=pending_action.get("action_type", ""),
            target_type=pending_action.get("target_type", ""),
            target_id=pending_action.get("target_id", ""),
            # Phase3 STOP D：业务操作身份两列（占位行可无指纹，051 谓词排除）
            tenant_id=tenant_id or None,
            semantic_fingerprint=semantic_fingerprint,
            proposal=proposal,
            proposal_version=proposal_version,
            state=pending_action.get("confirmation_state", "pending"),
            expires_at=_parse_dt(pending_action.get("expires_at")),
        )
        self._s.add(obj)
        await self._s.flush()
        return obj

    async def update_state(
        self,
        confirmation_id: str,
        state: str,
        confirmed_at: datetime | None = None,
        executed_at: datetime | None = None,
    ) -> bool:
        values: dict = {"state": state}
        if confirmed_at is not None:
            values["confirmed_at"] = confirmed_at
        if executed_at is not None:
            values["executed_at"] = executed_at
        result = await self._s.execute(
            update(CSConfirmation)
            .where(CSConfirmation.confirmation_id == confirmation_id)
            .values(**values)
        )
        await self._s.flush()
        return result.rowcount > 0

    async def update_proposal(
        self,
        confirmation_id: str,
        pending_action: dict,
        tenant_id: str = "",
        semantic_fingerprint: str | None = None,
        proposal_version: int | None = None,
    ) -> bool:
        """整行覆盖 pending 行的 proposal JSON 及其派生列（缺陷6.3）。

        need_info（缺槽位追问）升级为正式 proposal、reask 更新 retry_count
        时，仅 update_state 不够 —— proposal JSON 必须同步落库，否则补槽
        结果/追问计数在 L1 缓存失效后丢失。
        Phase3 STOP D（D19）：语义身份（fingerprint/tenant）必须在同一
        条 UPDATE 内原子刷新 —— 否则 row 指纹=旧、proposal=新，唯一守卫
        被架空；身份变更撞上另一 active 操作时由 051 唯一索引拒绝（D20），
        调用方转译为 BusinessOperationConflict。
        """
        proposal = dict(pending_action)
        proposal.setdefault("proposal_id", confirmation_id)
        if proposal_version is not None:
            proposal["version"] = proposal_version
        values: dict = {
            "proposal": proposal,
            "state": pending_action.get("confirmation_state", "pending"),
            "action_type": pending_action.get("action_type", ""),
            "target_type": pending_action.get("target_type", ""),
            "target_id": pending_action.get("target_id", ""),
        }
        if tenant_id:
            values["tenant_id"] = tenant_id
        if semantic_fingerprint is not None:
            values["semantic_fingerprint"] = semantic_fingerprint
        if proposal_version is not None:
            values["proposal_version"] = proposal_version
        expires = _parse_dt(pending_action.get("expires_at"))
        if expires is not None:
            values["expires_at"] = expires
        filters = [CSConfirmation.confirmation_id == confirmation_id]
        if proposal_version is not None:
            # CAS 防止并发补槽/追问在另一请求认领后覆盖 Proposal。
            filters.extend((
                CSConfirmation.state == "pending",
                CSConfirmation.proposal_version == proposal_version - 1,
            ))
        result = await self._s.execute(
            update(CSConfirmation).where(*filters).values(**values)
        )
        await self._s.flush()
        return result.rowcount > 0

    async def clear(self, user_id: str, conversation_id: str) -> bool:
        result = await self._s.execute(
            update(CSConfirmation)
            .where(
                CSConfirmation.user_id == user_id,
                CSConfirmation.conversation_id == conversation_id,
                CSConfirmation.state == "pending",
            )
            .values(state="cancelled")
        )
        await self._s.flush()
        return result.rowcount > 0

    async def claim_pending(
        self,
        user_id: str,
        conversation_id: str,
        *,
        tenant_id: str = "",
        proposal_id: str = "",
        expected_version: int | None = None,
        client_action_id: str | None = None,
    ) -> str | None:
        """原子认领：pending → confirmed（幂等闸门）。

        单条 UPDATE 带前置条件 state='pending'，并发重复确认只有一方
        能成功（rowcount=1），另一方拿到 None —— 阻止双执行。
        返回被认领的 confirmation_id；无 pending 行 / 已被处理返回 None。
        """
        if tenant_id:
            conversation_result = await self._s.execute(
                select(CSConversation)
                .where(
                    CSConversation.tenant_id == tenant_id,
                    CSConversation.conversation_id == conversation_id,
                )
                .with_for_update()
            )
            conversation = conversation_result.scalar_one_or_none()
            if (
                conversation is None
                or conversation.user_id != user_id
                or conversation.handling_mode != "ai"
            ):
                return None

            handoff_result = await self._s.execute(
                select(CSHandoff)
                .where(
                    CSHandoff.tenant_id == tenant_id,
                    CSHandoff.conversation_id == conversation_id,
                    CSHandoff.handoff_state.in_(
                        (
                            "handoff_requested", "waiting_human",
                            "agent_offered", "human_active",
                        ),
                    ),
                )
                .with_for_update()
                .limit(1)
            )
            if handoff_result.scalar_one_or_none() is not None:
                return None

        now = datetime.now(timezone.utc)
        filters = [
            CSConfirmation.user_id == user_id,
            CSConfirmation.conversation_id == conversation_id,
            CSConfirmation.state == "pending",
            CSConfirmation.expires_at > now,
        ]
        if tenant_id:
            filters.append(CSConfirmation.tenant_id == tenant_id)
        if proposal_id:
            filters.append(CSConfirmation.confirmation_id == proposal_id)
        if expected_version is not None:
            filters.append(CSConfirmation.proposal_version == expected_version)
        values: dict = {"state": "confirmed", "confirmed_at": now}
        if client_action_id is not None:
            values["client_action_id"] = client_action_id
        result = await self._s.execute(
            update(CSConfirmation)
            .where(*filters)
            .values(**values)
            .returning(CSConfirmation.confirmation_id)
        )
        await self._s.flush()
        rows = result.scalars().all()
        return rows[0] if rows else None

    async def cancel_pending(
        self,
        user_id: str,
        conversation_id: str,
        *,
        tenant_id: str,
        proposal_id: str,
        expected_version: int,
        client_action_id: str | None = None,
    ) -> bool:
        """仅取消认证租户下仍有效且版本匹配的当前 Proposal。"""
        now = datetime.now(timezone.utc)
        result = await self._s.execute(
            update(CSConfirmation)
            .where(
                CSConfirmation.user_id == user_id,
                CSConfirmation.conversation_id == conversation_id,
                CSConfirmation.tenant_id == tenant_id,
                CSConfirmation.confirmation_id == proposal_id,
                CSConfirmation.proposal_version == expected_version,
                CSConfirmation.state == "pending",
                CSConfirmation.expires_at > now,
            )
            .values(
                state="cancelled",
                **({"client_action_id": client_action_id} if client_action_id else {}),
            )
        )
        await self._s.flush()
        return result.rowcount > 0

    async def finalize_current(
        self, user_id: str, conversation_id: str, final_state: str
    ) -> bool:
        """把该会话当前确认行置为终态（success/failed/expired），幂等。

        修正历史缺陷：过期/失败此前被 repo.clear 一律写成 cancelled，
        审计口径失真（audit-report §P1-14）。
        """
        values: dict = {"state": final_state}
        if final_state in ("success", "failed", "verifying"):
            # verifying（STOP D IN_DOUBT）：执行已发生（结果未知），executed_at
            # 记录执行时刻，供 STOP E reconciliation 定位
            values["executed_at"] = datetime.now(timezone.utc)
        result = await self._s.execute(
            update(CSConfirmation)
            .where(
                CSConfirmation.user_id == user_id,
                CSConfirmation.conversation_id == conversation_id,
                CSConfirmation.state.in_(_ACTIVE_CONFIRM_STATES),
            )
            .values(**values)
        )
        await self._s.flush()
        return result.rowcount > 0

    async def has_pending(self, user_id: str) -> bool:
        result = await self._s.execute(
            select(
                exists().where(
                    CSConfirmation.user_id == user_id,
                    CSConfirmation.state == "pending",
                )
            )
        )
        return bool(result.scalar())

    # ── Phase3 STOP D：Business Operation Guard 查询 ────────────────

    async def find_by_identity(
        self, *, tenant_id: str, action_type: str, target_type: str,
        target_id: str, semantic_fingerprint: str,
        states: tuple[str, ...],
    ) -> CSConfirmation | None:
        """按业务操作身份查既有行（冲突定位 §66 / 终态策略 D18）。

        身份列与 051 唯一索引完全同构 —— 查询与约束共用同一套键，
        禁止两套口径（§34）。确定性排序保证多行时返回稳定结果。
        """
        result = await self._s.execute(
            select(CSConfirmation)
            .where(
                CSConfirmation.tenant_id == tenant_id,
                CSConfirmation.action_type == action_type,
                CSConfirmation.target_type == target_type,
                CSConfirmation.target_id == target_id,
                CSConfirmation.semantic_fingerprint == semantic_fingerprint,
                CSConfirmation.state.in_(states),
            )
            .order_by(CSConfirmation.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def list_ids_by_identity(
        self, *, tenant_id: str, action_type: str, target_type: str,
        target_id: str, semantic_fingerprint: str,
        states: tuple[str, ...],
    ) -> list[tuple[str, str]]:
        """同身份全部行的 (confirmation_id, user_id)——IN_DOUBT ledger 防线用。"""
        result = await self._s.execute(
            select(
                CSConfirmation.confirmation_id,
                CSConfirmation.user_id,
            ).where(
                CSConfirmation.tenant_id == tenant_id,
                CSConfirmation.action_type == action_type,
                CSConfirmation.target_type == target_type,
                CSConfirmation.target_id == target_id,
                CSConfirmation.semantic_fingerprint == semantic_fingerprint,
                CSConfirmation.state.in_(states),
            )
        )
        return [(r[0], r[1]) for r in result.all()]

    async def has_in_doubt_ledger(
        self, *, tenant_id: str, user_id: str, confirmation_id: str,
    ) -> bool:
        """Phase2 durable ledger 是否留有该确认的未决副作用记录。

        STOP C 语义（shared/idempotency）：status=running（含租约过期
        crash 窗）或 status=failed 且 error_code=IDEMPOTENCY_UNCERTAIN
        —— 副作用结果未知，guard 不得释放（STOP D §15/§37）。
        """
        from sqlalchemy import text
        check = await self._s.execute(
            text(
                "SELECT 1 FROM ai.idempotency_records "
                "WHERE tenant_id = :t AND actor_id = :u "
                "AND operation = 'cs.action.execute' AND client_key = :k "
                "AND (status = 'running' "
                "     OR (status = 'failed' AND error_code = 'IDEMPOTENCY_UNCERTAIN')) "
                "LIMIT 1"
            ),
            {"t": tenant_id, "u": user_id,
             "k": f"cs_action:{confirmation_id}"},
        )
        return check.first() is not None

    async def expire_stale(self) -> list[dict]:
        """全局扫描：pending 且已过 expires_at 的确认 → expired（P2.4）。

        原子条件 UPDATE（幂等闸门同 claim_pending 模式）：并发扫描/确认
        只有一方生效；用户确认与过期竞争时 state 条件保证二者互斥。
        返回被过期确认的 {confirmation_id, user_id, conversation_id} 列表。
        """
        result = await self._s.execute(
            update(CSConfirmation)
            .where(
                CSConfirmation.state == "pending",
                CSConfirmation.expires_at <= datetime.now(timezone.utc),
            )
            .values(state="expired")
            .returning(
                CSConfirmation.confirmation_id,
                CSConfirmation.user_id,
                CSConfirmation.conversation_id,
            )
        )
        await self._s.flush()
        return [
            {
                "confirmation_id": r.confirmation_id,
                "user_id": r.user_id,
                "conversation_id": r.conversation_id,
            }
            for r in result.all()
        ]


def _parse_dt(value: str | datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)
