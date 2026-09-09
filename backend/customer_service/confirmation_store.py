"""customer_service/confirmation_store.py — 确认状态持久化

Phase 4: session-scoped in-memory dict.
Phase 7: DB-backed via ConfirmationRepository, in-memory L1 cache retained.
"""
from __future__ import annotations

import threading

from backend.shared.logger import logger


class ConfirmationStore:
    """跨 turn 的 pending_action 存储。

    Key: (user_id, session_id) → pending_action dict.
    内存 dict 作为 L1 缓存, DB 作为持久层。
    """

    def __init__(self):
        self._data: dict[tuple[str, str], dict] = {}
        self._lock = threading.Lock()

    def load(self, user_id: str, session_id: str) -> dict | None:
        with self._lock:
            cached = self._data.get((user_id, session_id))
            if cached is not None:
                return cached

        db_data = self._db_load(user_id, session_id)
        if db_data is not None:
            with self._lock:
                self._data[(user_id, session_id)] = db_data
        return db_data

    def save(self, user_id: str, session_id: str, pending_action: dict) -> None:
        with self._lock:
            self._data[(user_id, session_id)] = pending_action
        self._db_save(user_id, session_id, pending_action)

    def clear(self, user_id: str, session_id: str) -> None:
        with self._lock:
            self._data.pop((user_id, session_id), None)
        self._db_clear(user_id, session_id)

    def has_pending(self, user_id: str) -> bool:
        with self._lock:
            if any(k[0] == user_id for k in self._data):
                return True
        return self._db_has_pending(user_id)

    def _db_load(self, user_id: str, session_id: str) -> dict | None:
        try:
            from backend.customer_service._db_loop import run_sync
            return run_sync(self._async_load(user_id, session_id))
        except Exception:
            logger.debug("[ConfirmationStore] DB load failed, cache-only mode")
            return None

    def _db_save(self, user_id: str, session_id: str, pending_action: dict) -> None:
        try:
            from backend.customer_service._db_loop import run_sync
            run_sync(self._async_save(user_id, session_id, pending_action))
        except Exception:
            logger.debug("[ConfirmationStore] DB save failed, cache-only mode")

    def _db_clear(self, user_id: str, session_id: str) -> None:
        try:
            from backend.customer_service._db_loop import run_sync
            run_sync(self._async_clear(user_id, session_id))
        except Exception:
            logger.debug("[ConfirmationStore] DB clear failed, cache-only mode")

    def _db_has_pending(self, user_id: str) -> bool:
        try:
            from backend.customer_service._db_loop import run_sync
            return run_sync(self._async_has_pending(user_id))
        except Exception:
            logger.debug("[ConfirmationStore] DB has_pending failed, cache-only mode")
            return False

    @staticmethod
    async def _async_load(user_id: str, session_id: str) -> dict | None:
        from backend.customer_service.repository import ConfirmationRepository
        from backend.memory.database import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            repo = ConfirmationRepository(db)
            row = await repo.load(user_id, session_id)
            if row is not None:
                return row.proposal
            return None

    @staticmethod
    async def _async_save(
        user_id: str, session_id: str, pending_action: dict
    ) -> None:
        from backend.customer_service.repository import ConfirmationRepository
        from backend.memory.database import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            repo = ConfirmationRepository(db)
            existing = await repo.load(user_id, session_id)
            if existing is not None:
                await repo.update_state(
                    existing.confirmation_id,
                    pending_action.get("confirmation_state", "pending"),
                )
            else:
                await repo.save(user_id, session_id, pending_action)
            await db.commit()

    @staticmethod
    async def _async_clear(user_id: str, session_id: str) -> None:
        from backend.customer_service.repository import ConfirmationRepository
        from backend.memory.database import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            repo = ConfirmationRepository(db)
            await repo.clear(user_id, session_id)
            await db.commit()

    @staticmethod
    async def _async_has_pending(user_id: str) -> bool:
        from backend.customer_service.repository import ConfirmationRepository
        from backend.memory.database import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            repo = ConfirmationRepository(db)
            return await repo.has_pending(user_id)


_store_instance: ConfirmationStore | None = None
_store_lock = threading.Lock()


def get_confirmation_store() -> ConfirmationStore:
    global _store_instance
    if _store_instance is None:
        with _store_lock:
            if _store_instance is None:
                _store_instance = ConfirmationStore()
    return _store_instance
