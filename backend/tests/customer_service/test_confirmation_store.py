"""test_confirmation_store.py — 确认状态持久化测试"""
from unittest.mock import patch
from contextlib import asynccontextmanager

import pytest

from backend.customer_service.confirmation_store import ConfirmationStore, StoreWriteError


@pytest.fixture(autouse=True)
def mock_db_success(monkeypatch):
    """Store 单测只验证缓存语义，持久化成功由显式桩表示。"""
    # STOP D：save 增加 tenant_id kwarg —— mock 签名跟进（断言意图不变）
    monkeypatch.setattr(ConfirmationStore, "_db_save", lambda *args, **kwargs: True)
    monkeypatch.setattr(ConfirmationStore, "_db_clear", lambda *args: True)
    monkeypatch.setattr(ConfirmationStore, "_db_load", lambda *args: None)


class TestConfirmationStore:
    def test_strict_claim_does_not_fall_back_to_l1_when_db_has_no_row(self):
        """strict 模式下 DB 无持久行时不能认领缓存动作触发副作用。"""
        store = ConfirmationStore()
        store.cache_l1("user1", "session1", {"action_id": "act-l1"})

        with patch("backend.customer_service.confirmation_store._strict_writes", return_value=True):
            with patch.object(store, "_db_claim", return_value=None):
                claimed = store.claim_for_execution("user1", "session1")

        assert claimed is None
        assert store.peek_l1("user1", "session1")["action_id"] == "act-l1"

    def test_authoritative_load_fails_closed_when_database_is_unavailable(self):
        store = ConfirmationStore()
        store.cache_l1("user1", "session1", {"action_id": "act-l1"})

        with patch("backend.customer_service.confirmation_store._strict_writes", return_value=True):
            with patch.object(store, "_db_load_authoritative", side_effect=RuntimeError("db down")):
                with pytest.raises(StoreWriteError):
                    store.load_authoritative("user1", "session1", "tenant-1")

    def test_save_does_not_cache_when_db_write_fails(self):
        """DB 写失败时不得把未持久化的确认动作留在 L1。"""
        store = ConfirmationStore()

        with patch.object(store, "_db_save", return_value=False):
            store.save("user1", "session1", {"action_type": "refund_request"})

        assert store.peek_l1("user1", "session1") is None

    def test_save_and_load(self):
        store = ConfirmationStore()
        pending = {
            "action_id": "act-1", "action_type": "refund_request",
            "target_id": "123",
        }
        store.save("user1", "session1", pending)
        assert store.load("user1", "session1") == {
            **pending, "proposal_id": "act-1", "version": 1,
        }

    def test_load_missing_returns_none(self):
        store = ConfirmationStore()
        assert store.load("user1", "session1") is None

    def test_clear(self):
        store = ConfirmationStore()
        store.save("user1", "session1", {"action_type": "test"})
        store.clear("user1", "session1")
        assert store.load("user1", "session1") is None

    def test_clear_nonexistent_no_error(self):
        store = ConfirmationStore()
        store.clear("user1", "session1")

    def test_session_isolation(self):
        store = ConfirmationStore()
        store.save("user1", "session1", {"action_type": "refund"})
        store.save("user1", "session2", {"action_type": "return"})

        assert store.load("user1", "session1")["action_type"] == "refund"
        assert store.load("user1", "session2")["action_type"] == "return"

    def test_user_isolation(self):
        store = ConfirmationStore()
        store.save("user1", "session1", {"action_type": "refund"})
        store.save("user2", "session1", {"action_type": "return"})

        assert store.load("user1", "session1")["action_type"] == "refund"
        assert store.load("user2", "session1")["action_type"] == "return"

    def test_has_pending(self):
        store = ConfirmationStore()
        assert store.has_pending("user1") is False
        store.save("user1", "session1", {"action_type": "refund"})
        assert store.has_pending("user1") is True

    def test_has_pending_after_clear(self):
        store = ConfirmationStore()
        store.save("user1", "session1", {"action_type": "refund"})
        store.clear("user1", "session1")
        assert store.has_pending("user1") is False

    def test_overwrite(self):
        store = ConfirmationStore()
        store.save("user1", "session1", {"action_type": "refund"})
        store.save("user1", "session1", {"action_type": "return"})
        assert store.load("user1", "session1")["action_type"] == "return"


@pytest.mark.asyncio
async def test_proposal_update_lost_race_fails_closed():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from backend.customer_service.confirmation_store import _update_with_guard

    @asynccontextmanager
    async def savepoint():
        yield

    repo = SimpleNamespace(update_proposal=AsyncMock(return_value=False))
    db = SimpleNamespace(begin_nested=savepoint)

    with pytest.raises(StoreWriteError):
        await _update_with_guard(
            db,
            repo,
            "proposal-1",
            {"action_id": "proposal-1", "target_id": "order-2"},
            tenant_id="tenant-1",
            fingerprint=None,
            proposal_version=2,
        )
