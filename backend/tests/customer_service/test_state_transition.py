"""test_state_transition.py — StateTransitionService 单元测试

Phase 0.12: 验证状态转换服务的核心行为
- apply() DB 降级返回 error result
- load_snapshot() DB 降级返回默认快照
- get_state_transition_service() 单例
"""
from __future__ import annotations

import gc
import warnings
from unittest.mock import MagicMock, patch

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

    def test_apply_closes_coroutine_when_db_bridge_rejects_submission(self):
        """提交到 DB 线程失败时，不能遗留未 await 的协程。

        断言口径（2026-10-09 修正）：原实现叠加了
        filterwarnings("error::PytestUnraisableExceptionWarning")，于是把
        **任何** unraisable 异常都升级成失败。实测失败来自前序真库用例遗留的
        Windows asyncio 传输层析构：
            _ProactorBasePipeTransport.__del__ → RuntimeError: Event loop is closed
        它发生在本用例的 gc.collect() 时点，与本用例要验证的"协程是否被关闭"
        毫无关系，导致该用例在 test_p5_stopcs_a_realpg.py 之后必然红（单跑必绿）。
        这类跨用例 GC 噪声不该由本用例承担。

        这里改为只对**目标症状**断言：捕获到的告警中不得出现
        "coroutine ... was never awaited"。既保留原始回归意图，
        又不会把无关的析构噪声误判为本用例的缺陷。
        """
        svc = StateTransitionService()
        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("always")
            with patch(
                "backend.customer_service._db_loop.run_sync",
                side_effect=RuntimeError("loop stopped"),
            ):
                result = svc.apply(StateTransitionRequest(
                    user_id="u1",
                    session_id="s1",
                    conversation_id="c1",
                ))
            gc.collect()

        assert result["success"] is False
        leaked = [
            str(item.message) for item in captured
            if "was never awaited" in str(item.message)
        ]
        assert not leaked, f"DB 桥接失败时遗留未 await 的协程: {leaked}"


class TestLoadSnapshotDBFallback:
    def test_load_snapshot_returns_defaults_on_error(self):
        svc = StateTransitionService()
        with patch.object(svc, "_async_load_snapshot", side_effect=RuntimeError("no db")):
            result = svc.load_snapshot("u1", "s1", "c1")
        expected = _default_snapshot()
        assert result == expected
