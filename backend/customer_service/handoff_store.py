"""customer_service/handoff_store.py — 转接状态持久化

Phase 5: session-scoped in-memory dict, thread-safe.
Phase 6: 替换为 customer_service.handoffs DB 表。
"""
from __future__ import annotations

import threading


class HandoffStore:
    """跨 turn 的 handoff 状态存储。

    Key: (user_id, session_id) → handoff_data dict.
    handoff_data 包含: handoff_state, trigger_type, trigger_reason,
                       ticket_id, created_at, updated_at
    """

    def __init__(self):
        self._data: dict[tuple[str, str], dict] = {}
        self._lock = threading.Lock()

    def load(self, user_id: str, session_id: str) -> dict | None:
        with self._lock:
            return self._data.get((user_id, session_id))

    def save(self, user_id: str, session_id: str, handoff_data: dict) -> None:
        with self._lock:
            self._data[(user_id, session_id)] = handoff_data

    def clear(self, user_id: str, session_id: str) -> None:
        with self._lock:
            self._data.pop((user_id, session_id), None)

    def has_active_handoff(self, user_id: str) -> bool:
        """Check if a user has any active handoff (any session)."""
        with self._lock:
            for k, v in self._data.items():
                if k[0] == user_id:
                    state = v.get("handoff_state")
                    if state and state != "closed":
                        return True
            return False

    def get_active_handoff(self, user_id: str) -> dict | None:
        """Get the active handoff data for a user (any session)."""
        with self._lock:
            for k, v in self._data.items():
                if k[0] == user_id:
                    state = v.get("handoff_state")
                    if state and state != "closed":
                        return v
            return None


_store_instance: HandoffStore | None = None
_store_lock = threading.Lock()


def get_handoff_store() -> HandoffStore:
    global _store_instance
    if _store_instance is None:
        with _store_lock:
            if _store_instance is None:
                _store_instance = HandoffStore()
    return _store_instance
