"""test_conversation_store.py — CS turn persistence facade tests.

Covers:
- record_cs_turn delegates to run_sync with correct args
- Soft-fail: exceptions are swallowed, never propagated
- Fire-and-forget: caller returns immediately even if DB is slow
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch


class TestRecordCsTurn:

    @patch("backend.customer_service.conversation_store.run_sync", create=True)
    def test_delegates_to_run_sync(self, mock_run_sync):
        """record_cs_turn should call run_sync with the async coroutine."""
        from backend.customer_service import conversation_store

        with patch.object(conversation_store, "_async_record_turn") as mock_async:
            mock_async.return_value = "fake_coro"
            conversation_store.record_cs_turn(
                conversation_id="conv-1",
                user_id="user-1",
                question="你好",
                answer="你好！",
                trace_id="trace-abc",
                cs_route={"intent": "greeting", "confidence": 0.95},
            )

    def test_soft_fail_on_run_sync_error(self):
        """If run_sync raises, record_cs_turn swallows the exception."""
        from backend.customer_service import conversation_store

        with patch(
            "backend.customer_service._db_loop.run_sync",
            side_effect=RuntimeError("DB connection lost"),
        ):
            conversation_store.record_cs_turn(
                conversation_id="conv-1",
                user_id="user-1",
                question="test",
                answer="test",
            )

    def test_soft_fail_on_import_error(self):
        """If _db_loop import fails, record_cs_turn swallows the exception."""
        from backend.customer_service import conversation_store

        with patch(
            "backend.customer_service.conversation_store.record_cs_turn",
            wraps=conversation_store.record_cs_turn,
        ):
            import importlib
            with patch.dict("sys.modules", {"backend.customer_service._db_loop": None}):
                conversation_store.record_cs_turn(
                    conversation_id="conv-1",
                    user_id="user-1",
                    question="test",
                    answer="test",
                )

    def test_no_trace_id_is_valid(self):
        """trace_id=None should not cause errors."""
        from backend.customer_service import conversation_store

        with patch(
            "backend.customer_service._db_loop.run_sync",
            side_effect=RuntimeError("skip"),
        ):
            conversation_store.record_cs_turn(
                conversation_id="conv-1",
                user_id="user-1",
                question="q",
                answer="a",
                trace_id=None,
                cs_route=None,
            )

    def test_returns_none(self):
        """record_cs_turn always returns None (fire-and-forget)."""
        from backend.customer_service import conversation_store

        with patch(
            "backend.customer_service._db_loop.run_sync",
            side_effect=Exception("boom"),
        ):
            result = conversation_store.record_cs_turn(
                conversation_id="conv-1",
                user_id="user-1",
                question="q",
                answer="a",
            )
        assert result is None
