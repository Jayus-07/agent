"""customer_service/confirmation_store.py — 确认状态持久化

Phase 4: session-scoped in-memory dict, thread-safe.
Phase 6: 替换为 customer_service.confirmations DB 表。
"""
from __future__ import annotations

import threading


class ConfirmationStore:
    """跨 turn 的 pending_action 存储。

    Key: (user_id, session_id) → pending_action dict.
    """

    def __init__(self):
        self._data: dict[tuple[str, str], dict] = {}
        self._lock = threading.Lock()

    def load(self, user_id: str, session_id: str) -> dict | None:
        with self._lock:
            return self._data.get((user_id, session_id))

    def save(self, user_id: str, session_id: str, pending_action: dict) -> None:
        with self._lock:
            self._data[(user_id, session_id)] = pending_action

    def clear(self, user_id: str, session_id: str) -> None:
        with self._lock:
            self._data.pop((user_id, session_id), None)

    def has_pending(self, user_id: str) -> bool:
        """Check if a user has any pending confirmation (any session)."""
        with self._lock:
            return any(k[0] == user_id for k in self._data)


_store_instance: ConfirmationStore | None = None
_store_lock = threading.Lock()


def get_confirmation_store() -> ConfirmationStore:
    global _store_instance
    if _store_instance is None:
        with _store_lock:
            if _store_instance is None:
                _store_instance = ConfirmationStore()
    return _store_instance
