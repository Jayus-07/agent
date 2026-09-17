"""customer_service/confirmation_store.py — 确认状态持久化

Phase 4: session-scoped in-memory dict.
Phase 7: DB-backed via ConfirmationRepository, in-memory L1 cache retained.
P1 重构（2026-09-17）:
  - claim_for_execution: 原子认领闸门，防止重复确认双执行。
  - clear(final_state=...): 终态区分 success/failed/expired/cancelled。
  - DB 写失败不再静默 cache-only：严格模式下抛 StoreWriteError（生产），
    非严格模式告警继续（单元测试/无 DB 调试）。
"""
from __future__ import annotations

import threading

from backend.customer_service.errors import CustomerServiceError
from backend.shared.logger import logger


class StoreWriteError(CustomerServiceError):
    """PostgreSQL 写失败且严格模式开启 — 禁止静默降级为内存态。"""

    def __init__(self, store: str, op: str):
        super().__init__(
            f"[{store}] DB {op} failed (strict mode)",
            user_message="系统繁忙，请稍后重试；若持续失败请联系人工客服。",
        )


def _strict_writes() -> bool:
    from backend.config.customer_service import CS_STORE_STRICT_WRITES
    return bool(CS_STORE_STRICT_WRITES)


def _db_write_failed(store: str, op: str, exc: Exception) -> None:
    """统一失败处理：error 级日志 + 严格模式抛错（绝不静默吞掉）。"""
    logger.error(
        "[ConfirmationStore] DB %s failed (strict=%s): %s",
        op, _strict_writes(), exc, exc_info=True,
    )
    try:
        from backend.observability.metrics import record_cs_store_db_failure
        record_cs_store_db_failure("confirmation", op)
    except Exception:
        logger.debug("[ConfirmationStore] metrics unavailable")
    if _strict_writes():
        raise StoreWriteError("ConfirmationStore", op) from exc


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

    def peek_l1(self, user_id: str, session_id: str) -> dict | None:
        """P3.5：纯内存直读（无 DB 桥接）——供已运行在 _db_loop 线程的
        async 代码调用（嵌套 run_sync 会自死锁，见 state_transition）。
        """
        with self._lock:
            return self._data.get((user_id, session_id))

    def cache_l1(self, user_id: str, session_id: str, pending: dict) -> None:
        """P3.5：DB 读回填 L1 缓存（与 load 的缓存行为一致）。"""
        with self._lock:
            self._data[(user_id, session_id)] = pending

    def save(self, user_id: str, session_id: str, pending_action: dict) -> None:
        with self._lock:
            self._data[(user_id, session_id)] = pending_action
        self._db_save(user_id, session_id, pending_action)

    def clear(self, user_id: str, session_id: str, *, final_state: str = "cancelled") -> None:
        """清除 pending 并把 DB 行置为终态。

        final_state: cancelled（用户取消）/ success / failed / expired。
        P1 修正：此前一律写 cancelled，过期与失败的审计口径失真。
        """
        with self._lock:
            self._data.pop((user_id, session_id), None)
        self._db_clear(user_id, session_id, final_state)

    def claim_for_execution(self, user_id: str, session_id: str) -> str | None:
        """原子认领待确认动作（幂等闸门，P1）。

        DB 侧单条条件 UPDATE pending→confirmed：并发重复确认只有一方成功。
        DB 不可用时：严格模式抛 StoreWriteError（执行必须失败，不冒双执行
        之险）；非严格模式（测试/本地调试）降级为进程内 L1 认领并告警。
        返回 confirmation_id；已被处理/不存在 pending 返回 None。
        """
        try:
            claimed_id = self._db_claim(user_id, session_id)
            if claimed_id is not None:
                # DB 认领成功 → 移除 L1 pending 条目
                with self._lock:
                    self._data.pop((user_id, session_id), None)
                return claimed_id
            # DB 确认无行（可能是 save 降级未落库）→ 回退 L1 认领。
            # L1 pop 原子，单进程内幂等保持；DB 有行时走 DB 闸门
            # （多实例安全），两分支都只认领一次。
            with self._lock:
                pending = self._data.pop((user_id, session_id), None)
            return pending.get("action_id") if pending else None
        except Exception as exc:
            try:
                from backend.observability.metrics import record_cs_store_db_failure
                record_cs_store_db_failure("confirmation", "claim")
            except Exception:
                pass
            if _strict_writes():
                logger.error(
                    "[ConfirmationStore] DB claim failed (strict): %s", exc,
                    exc_info=True,
                )
                raise StoreWriteError("ConfirmationStore", "claim") from exc
            logger.warning(
                "[ConfirmationStore] DB claim failed, fallback to L1 claim: %s",
                exc,
            )
            with self._lock:
                pending = self._data.pop((user_id, session_id), None)
            return pending.get("action_id") if pending else None

    def has_pending(self, user_id: str) -> bool:
        with self._lock:
            if any(k[0] == user_id for k in self._data):
                return True
        return self._db_has_pending(user_id)

    def _db_load(self, user_id: str, session_id: str) -> dict | None:
        try:
            from backend.customer_service._db_loop import run_sync
            return run_sync(self._async_load(user_id, session_id))
        except Exception as exc:
            logger.warning(
                "[ConfirmationStore] DB load failed (cache fallback): %s", exc,
            )
            return None

    def _db_save(self, user_id: str, session_id: str, pending_action: dict) -> None:
        try:
            from backend.customer_service._db_loop import run_sync
            run_sync(self._async_save(user_id, session_id, pending_action))
        except Exception as exc:
            # P3.5：save 是后置持久化（L1 已写成功、确认卡已可用），DB 慢/
            # 抖动只降级告警——此前 strict 抛错把整个 expert 打成失败，
            # 用户看到「处理出错」。strict 闸门只属于 claim（防双执行）。
            logger.warning(
                "[ConfirmationStore] DB save failed (L1 kept): %s", exc,
                exc_info=True,
            )

    def _db_clear(self, user_id: str, session_id: str, final_state: str) -> None:
        try:
            from backend.customer_service._db_loop import run_sync
            run_sync(self._async_clear(user_id, session_id, final_state))
        except Exception as exc:
            # P3.5：与 _db_save 同理，L1 已清除、终态语义已生效于本进程，
            # DB 抖动不回滚业务结果，只告警（审计口径以 trace 为准）
            logger.warning(
                "[ConfirmationStore] DB clear failed (L1 already cleared): %s",
                exc, exc_info=True,
            )

    def _db_claim(self, user_id: str, session_id: str) -> str | None:
        from backend.customer_service._db_loop import run_sync
        return run_sync(self._async_claim(user_id, session_id))

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
    async def _async_clear(
        user_id: str, session_id: str, final_state: str
    ) -> None:
        from backend.customer_service.repository import ConfirmationRepository
        from backend.memory.database import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            repo = ConfirmationRepository(db)
            if final_state == "cancelled":
                await repo.clear(user_id, session_id)
            else:
                await repo.finalize_current(user_id, session_id, final_state)
            await db.commit()

    @staticmethod
    async def _async_claim(user_id: str, session_id: str) -> str | None:
        from backend.customer_service.repository import ConfirmationRepository
        from backend.memory.database import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            repo = ConfirmationRepository(db)
            claimed_id = await repo.claim_pending(user_id, session_id)
            await db.commit()
            return claimed_id

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
