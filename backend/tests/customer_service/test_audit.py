"""test_audit.py — 审计日志构建器测试"""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.customer_service.audit import append_audit, build_audit_entry


class TestBuildAuditEntry:
    def test_required_fields(self):
        entry = build_audit_entry(
            user_id="u1",
            action_type="refund_request",
            result="success",
        )
        assert entry["user_id"] == "u1"
        assert entry["action_type"] == "refund_request"
        assert entry["result"] == "success"
        assert "log_id" in entry
        assert "created_at" in entry

    def test_optional_fields(self):
        entry = build_audit_entry(
            user_id="u1",
            action_type="refund_request",
            result="failure",
            target_type="order",
            target_id="123",
            detail="something went wrong",
            conversation_id="conv-1",
        )
        assert entry["target_type"] == "order"
        assert entry["target_id"] == "123"
        assert entry["detail"] == "something went wrong"
        assert entry["conversation_id"] == "conv-1"

    def test_no_conversation_id_defaults_empty(self):
        entry = build_audit_entry(
            user_id="u1",
            action_type="test",
            result="success",
        )
        assert entry["conversation_id"] == ""

    def test_result_values(self):
        for result in ("success", "failure", "denied", "error"):
            entry = build_audit_entry(
                user_id="u1", action_type="test", result=result,
            )
            assert entry["result"] == result


class TestAppendAudit:
    def test_returns_new_list(self):
        original = [{"log_id": "a", "result": "success"}]
        new_entry = {"log_id": "b", "result": "failure"}
        result = append_audit(original, new_entry)

        assert len(result) == 2
        assert len(original) == 1
        assert result[0]["log_id"] == "a"
        assert result[1]["log_id"] == "b"

    def test_empty_list(self):
        entry = {"log_id": "x"}
        result = append_audit([], entry)
        assert result == [{"log_id": "x"}]


class TestInsertAgentAction:
    """insert_agent_action 对 AgentActionRecord.to_dict() 形状的容忍度。

    回归（2026-09-24 人工验收实机发现）：to_dict() 把 executed_at 序列化成
    ISO 字符串，asyncpg 对 TIMESTAMPTZ 列拒绝字符串 → 整笔审计事务回滚，
    agent_actions 生产表零落库。断言 ORM 对象字段类型（MagicMock 会话不
    校验类型，冻结期 26 用例因此未捕获该缺陷）。
    """

    @pytest.mark.asyncio
    async def test_executed_at_iso_string_coerced_to_datetime(self):
        from backend.customer_service.repository.audit_repo import AuditRepository

        session = AsyncMock()
        session.add = MagicMock()
        repo = AuditRepository(session)

        record = {
            "action_id": "a-1",
            "action_type": "refund_request",
            "agent_type": "ai",
            "target_type": "order",
            "target_id": "MO-1",
            "data": {"before_state": {"status": "paid"},
                     "after_state": {"status": "refund_requested"}},
            "status": "simulated",
            "executed_at": "2026-09-23T23:48:30.148207+00:00",
        }
        await repo.insert_agent_action(record)

        obj = session.add.call_args[0][0]
        assert obj.action_id == "a-1"
        assert isinstance(obj.executed_at, datetime)
        assert obj.executed_at.tzinfo is not None

    @pytest.mark.asyncio
    async def test_executed_at_datetime_passthrough(self):
        from backend.customer_service.repository.audit_repo import AuditRepository

        session = AsyncMock()
        session.add = MagicMock()
        repo = AuditRepository(session)

        ts = datetime(2026, 9, 24, tzinfo=timezone.utc)
        await repo.insert_agent_action({"action_id": "a-2",
                                        "executed_at": ts})
        obj = session.add.call_args[0][0]
        assert obj.executed_at is ts

    @pytest.mark.asyncio
    async def test_executed_at_invalid_string_becomes_none(self):
        from backend.customer_service.repository.audit_repo import AuditRepository

        session = AsyncMock()
        session.add = MagicMock()
        repo = AuditRepository(session)

        await repo.insert_agent_action({"action_id": "a-3",
                                        "executed_at": "not-a-date"})
        obj = session.add.call_args[0][0]
        assert obj.executed_at is None

    @pytest.mark.asyncio
    async def test_executed_at_missing_is_none(self):
        from backend.customer_service.repository.audit_repo import AuditRepository

        session = AsyncMock()
        session.add = MagicMock()
        repo = AuditRepository(session)

        await repo.insert_agent_action({"action_id": "a-4"})
        obj = session.add.call_args[0][0]
        assert obj.executed_at is None
