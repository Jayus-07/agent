"""customer_service/handoff_store.py — 转接状态持久化

Phase 5: session-scoped in-memory dict.
Phase 7: DB-backed via HandoffRepository, in-memory L1 cache retained.
"""
from __future__ import annotations

import threading

from backend.shared.logger import logger


class HandoffStore:
    """跨 turn 的 handoff 状态存储。

    Key: (user_id, session_id) → handoff_data dict.
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

    def save(self, user_id: str, session_id: str, handoff_data: dict) -> None:
        from datetime import datetime, timezone

        # 统一盖章：调用方传入的 dict 不带时间戳（DB 行才有），而
        # CS_HANDOFF_TIMEOUT_SECONDS 超时回退依赖缓存条目的 updated_at
        stamped = dict(handoff_data)
        now = datetime.now(timezone.utc).isoformat()
        stamped.setdefault("created_at", now)
        stamped["updated_at"] = now
        with self._lock:
            self._data[(user_id, session_id)] = stamped
        self._db_save(user_id, session_id, stamped)

    def clear(self, user_id: str, session_id: str) -> None:
        with self._lock:
            self._data.pop((user_id, session_id), None)
        self._db_clear(user_id, session_id)

    def has_active_handoff(self, user_id: str) -> bool:
        with self._lock:
            for k, v in self._data.items():
                if k[0] == user_id:
                    state = v.get("handoff_state")
                    if state and state != "closed":
                        return True
        return self._db_has_active(user_id)

    def get_active_handoff(self, user_id: str) -> dict | None:
        with self._lock:
            for k, v in self._data.items():
                if k[0] == user_id:
                    state = v.get("handoff_state")
                    if state and state != "closed":
                        return v

        db_data = self._db_get_active(user_id)
        if db_data is not None:
            sid = db_data.pop("_conversation_id", "unknown")
            with self._lock:
                self._data[(user_id, sid)] = db_data
        return db_data

    def _db_load(self, user_id: str, session_id: str) -> dict | None:
        try:
            from backend.customer_service._db_loop import run_sync
            return run_sync(self._async_load(user_id, session_id))
        except Exception:
            logger.debug("[HandoffStore] DB load failed, cache-only mode")
            return None

    def _db_save(self, user_id: str, session_id: str, handoff_data: dict) -> None:
        try:
            from backend.customer_service._db_loop import run_sync
            run_sync(self._async_save(user_id, session_id, handoff_data))
        except Exception:
            logger.debug("[HandoffStore] DB save failed, cache-only mode")

    def _db_clear(self, user_id: str, session_id: str) -> None:
        try:
            from backend.customer_service._db_loop import run_sync
            run_sync(self._async_clear(user_id, session_id))
        except Exception:
            logger.debug("[HandoffStore] DB clear failed, cache-only mode")

    def _db_has_active(self, user_id: str) -> bool:
        try:
            from backend.customer_service._db_loop import run_sync
            return run_sync(self._async_has_active(user_id))
        except Exception:
            logger.debug("[HandoffStore] DB has_active failed, cache-only mode")
            return False

    def _db_get_active(self, user_id: str) -> dict | None:
        try:
            from backend.customer_service._db_loop import run_sync
            return run_sync(self._async_get_active(user_id))
        except Exception:
            logger.debug("[HandoffStore] DB get_active failed, cache-only mode")
            return None

    @staticmethod
    async def _async_load(user_id: str, session_id: str) -> dict | None:
        from backend.customer_service.repository import HandoffRepository
        from backend.memory.database import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            repo = HandoffRepository(db)
            row = await repo.load(user_id, session_id)
            if row is not None:
                return _handoff_to_dict(row)
            return None

    @staticmethod
    async def _async_save(
        user_id: str, session_id: str, handoff_data: dict
    ) -> None:
        from backend.customer_service.repository import HandoffRepository
        from backend.memory.database import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            repo = HandoffRepository(db)
            existing = await repo.load(user_id, session_id)
            if existing is not None:
                await repo.update_state(
                    existing.handoff_id,
                    handoff_data.get("handoff_state", "initiated"),
                )
            else:
                await repo.save(user_id, session_id, handoff_data)
            await db.commit()

    @staticmethod
    async def _async_clear(user_id: str, session_id: str) -> None:
        from backend.customer_service.repository import HandoffRepository
        from backend.memory.database import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            repo = HandoffRepository(db)
            await repo.clear(user_id, session_id)
            await db.commit()

    @staticmethod
    async def _async_has_active(user_id: str) -> bool:
        from backend.customer_service.repository import HandoffRepository
        from backend.memory.database import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            repo = HandoffRepository(db)
            return await repo.has_active(user_id)

    @staticmethod
    async def _async_get_active(user_id: str) -> dict | None:
        from backend.customer_service.repository import HandoffRepository
        from backend.memory.database import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            repo = HandoffRepository(db)
            row = await repo.get_active(user_id)
            if row is not None:
                return _handoff_to_dict(row)
            return None


def _handoff_to_dict(row) -> dict:
    return {
        "handoff_state": row.handoff_state,
        "trigger_type": row.trigger_type,
        "trigger_reason": row.trigger_reason,
        "ticket_id": row.ticket_id,
        "handoff_id": row.handoff_id,
        "_conversation_id": row.conversation_id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


_store_instance: HandoffStore | None = None
_store_lock = threading.Lock()


def get_handoff_store() -> HandoffStore:
    global _store_instance
    if _store_instance is None:
        with _store_lock:
            if _store_instance is None:
                _store_instance = HandoffStore()
    return _store_instance
