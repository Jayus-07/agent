"""test_state_transition.py — StateTransitionService 单元测试

Phase 0.12: 验证状态转换服务的核心行为
- apply() DB 降级返回 error result
- load_snapshot() DB 降级返回默认快照
- get_state_transition_service() 单例
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from backend.customer_service.state_transition import (
    StateTransitionRequest,
    StateTransitionResult,
    StateTransitionService,
    _default_snapshot,
    get_state_transition_service,
)


class TestDefaultSnapshot:
    def test_contains_all_keys(self):
        snap = _default_snapshot()
        assert snap["conversation_status"] == "open"
        assert snap["handling_mode"] == "ai"
        assert snap["handoff_state"] == "ai_active"
        assert snap["confirmation_state"] == "not_required"
        assert snap["pending_action"] is None


class TestSingleton:
    def test_returns_same_instance(self):
        svc1 = get_state_transition_service()
        svc2 = get_state_transition_service()
        assert svc1 is svc2
        assert isinstance(svc1, StateTransitionService)


class TestApplyDBFallback:
    def test_apply_returns_error_when_db_unavailable(self):
        svc = StateTransitionService()
        with patch("backend.customer_service._db_loop.run_sync", side_effect=RuntimeError("no db")):
            result = svc.apply(StateTransitionRequest(
                user_id="u1",
                session_id="s1",
                conversation_id="c1",
                confirmation_target="pending",
            ))
        assert result["success"] is False
        assert "DB unavailable" in result["errors"]


class TestLoadSnapshotDBFallback:
    def test_load_snapshot_returns_defaults_on_error(self):
        svc = StateTransitionService()
        with patch.object(svc, "_async_load_snapshot", side_effect=RuntimeError("no db")):
            result = svc.load_snapshot("u1", "s1", "c1")
        expected = _default_snapshot()
        assert result == expected
