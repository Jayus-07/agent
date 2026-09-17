"""AuditRepository — 审计日志与业务动作落库（P1 重构）

背景（audit-report §P0-7）：audit_logs / agent_actions 表在
006_customer_service.sql 已建，但全代码库零写入 —— 高风险操作审计
只活在 LangGraph state 里，turn 结束即丢。

本仓库提供：
  - insert_audit_log(dict)：对齐 audit_logs 表列（audit.py 构建器 + 扩展）
  - insert_agent_action(record_dict)：对齐 agent_actions 表列
    （action.py AgentActionRecord.to_dict()）

写入失败策略：审计属于「关键旁路」—— 失败必须 error 级告警日志 +
指标，但不得阻断主业务流程（chat 响应优先）；调用方 catch 后不得静默。
"""
from __future__ import annotations

from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.customer_service.models.audit_log import CSAuditLog
from backend.customer_service.models.agent_action import CSAgentAction


class AuditRepository:
    def __init__(self, session: AsyncSession):
        self._s = session

    async def insert_audit_log(self, entry: dict) -> None:
        """写入一条审计日志（entry 由 audit.build_audit_entry 构建并可扩展）。"""
        from datetime import datetime, timezone

        created_at = entry.get("created_at")
        if isinstance(created_at, str):
            try:
                created_at = datetime.fromisoformat(created_at)
            except ValueError:
                created_at = None
        if created_at is None:
            created_at = datetime.now(timezone.utc)

        obj = CSAuditLog(
            log_id=entry.get("log_id") or entry.get("action_id", ""),
            user_id=entry.get("user_id", ""),
            conversation_id=entry.get("conversation_id") or None,
            action_id=entry.get("action_id"),
            actor_type=entry.get("actor_type", "ai"),
            actor_id=entry.get("actor_id"),
            action=entry.get("action_type") or entry.get("action", ""),
            resource_type=entry.get("target_type") or None,
            resource_id=entry.get("target_id") or None,
            before_state=entry.get("before_state"),
            after_state=entry.get("after_state"),
            result=entry.get("result", "success"),
            error_detail=entry.get("detail") or None,
        )
        self._s.add(obj)
        await self._s.flush()

    async def insert_agent_action(self, record: dict) -> None:
        """写入一条业务动作记录（record 由 AgentActionRecord.to_dict() 构建）。"""
        obj = CSAgentAction(
            action_id=record.get("action_id", ""),
            conversation_id=record.get("conversation_id", ""),
            user_id=record.get("user_id", ""),
            action_type=record.get("action_type", ""),
            target_type=record.get("target_type"),
            target_id=record.get("target_id"),
            before_state=record.get("data", {}).get("before_state"),
            after_state=record.get("data", {}).get("after_state"),
            confirmation_state=record.get("confirmation_state", "not_required"),
            status=record.get("status", "pending"),
            executed_by=record.get("agent_type", "ai"),
            executed_at=record.get("executed_at"),
        )
        self._s.add(obj)
        await self._s.flush()
