"""test_confirmation_store.py — 确认状态持久化测试"""
from backend.customer_service.confirmation_store import ConfirmationStore


class TestConfirmationStore:
    def test_save_and_load(self):
        store = ConfirmationStore()
        pending = {"action_type": "refund_request", "target_id": "123"}
        store.save("user1", "session1", pending)
        assert store.load("user1", "session1") == pending

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
