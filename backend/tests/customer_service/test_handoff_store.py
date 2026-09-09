"""test_handoff_store.py — 转接状态持久化测试"""
from backend.customer_service.handoff_store import HandoffStore, get_handoff_store


class TestHandoffStore:

    def test_save_and_load(self):
        store = HandoffStore()
        data = {"handoff_state": "handoff_requested", "ticket_id": "HANDOFF-12345678"}
        store.save("user1", "session1", data)
        assert store.load("user1", "session1") == data

    def test_load_missing_returns_none(self):
        store = HandoffStore()
        assert store.load("user1", "session1") is None

    def test_clear(self):
        store = HandoffStore()
        store.save("user1", "session1", {"handoff_state": "handoff_requested"})
        store.clear("user1", "session1")
        assert store.load("user1", "session1") is None

    def test_clear_nonexistent_no_error(self):
        store = HandoffStore()
        store.clear("user1", "session1")

    def test_session_isolation(self):
        store = HandoffStore()
        store.save("user1", "session1", {"handoff_state": "handoff_requested"})
        store.save("user1", "session2", {"handoff_state": "human_active"})
        assert store.load("user1", "session1")["handoff_state"] == "handoff_requested"
        assert store.load("user1", "session2")["handoff_state"] == "human_active"

    def test_user_isolation(self):
        store = HandoffStore()
        store.save("user1", "session1", {"handoff_state": "handoff_requested"})
        store.save("user2", "session1", {"handoff_state": "human_active"})
        assert store.load("user1", "session1")["handoff_state"] == "handoff_requested"
        assert store.load("user2", "session1")["handoff_state"] == "human_active"


class TestHasActiveHandoff:

    def test_no_handoff(self):
        store = HandoffStore()
        assert store.has_active_handoff("user1") is False

    def test_active_handoff(self):
        store = HandoffStore()
        store.save("user1", "session1", {"handoff_state": "handoff_requested"})
        assert store.has_active_handoff("user1") is True

    def test_closed_not_active(self):
        store = HandoffStore()
        store.save("user1", "session1", {"handoff_state": "closed"})
        assert store.has_active_handoff("user1") is False

    def test_cross_session_active(self):
        store = HandoffStore()
        store.save("user1", "session1", {"handoff_state": "handoff_requested"})
        assert store.has_active_handoff("user1") is True

    def test_other_user_not_affected(self):
        store = HandoffStore()
        store.save("user1", "session1", {"handoff_state": "handoff_requested"})
        assert store.has_active_handoff("user2") is False


class TestGetActiveHandoff:

    def test_returns_active_data(self):
        store = HandoffStore()
        data = {"handoff_state": "handoff_requested", "ticket_id": "HANDOFF-ABC"}
        store.save("user1", "session1", data)
        assert store.get_active_handoff("user1") == data

    def test_returns_none_when_closed(self):
        store = HandoffStore()
        store.save("user1", "session1", {"handoff_state": "closed"})
        assert store.get_active_handoff("user1") is None

    def test_returns_none_when_no_data(self):
        store = HandoffStore()
        assert store.get_active_handoff("user1") is None


class TestSingleton:

    def test_same_instance(self):
        s1 = get_handoff_store()
        s2 = get_handoff_store()
        assert s1 is s2
