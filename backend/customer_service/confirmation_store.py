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

from sqlalchemy.exc import IntegrityError

from backend.customer_service.business_guard import (
    BusinessOperationAlreadyActive,
    BusinessOperationAlreadyCompleted,
    BusinessOperationConflict,
    BusinessOperationGuardError,
)
from backend.customer_service.errors import CustomerServiceError
from backend.shared.logger import logger

# 051 partial unique index 的 active 生命周期谓词（与迁移同口径，§34）
_GUARD_ACTIVE_STATES = ("pending", "confirmed", "executing", "verifying")


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


# ── Phase3 STOP D：Business Guard 写入路径辅助 ─────────────────────

async def _raise_on_active_conflict(repo, identity, *, exclude_confirmation_id: str,
                                    completed_message: str) -> None:
    """创建/升级前拦截：active 语义等价操作 + 终态不可重发动作。

    纯预检（确定性规则 §25），真正的并发决胜在 051 唯一索引 —— 预检
    只是把 DB 拒绝转译为可审计的业务语义（§20/§66）。
    """
    existing = await repo.find_by_identity(
        tenant_id=identity.tenant_id,
        action_type=identity.action_type,
        target_type=identity.target_type,
        target_id=identity.target_id,
        semantic_fingerprint=identity.semantic_fingerprint,
        states=_GUARD_ACTIVE_STATES,
    )
    if existing is not None and existing.confirmation_id != exclude_confirmation_id:
        logger.info(
            "[BusinessGuard] duplicate blocked: existing=%s state=%s action=%s",
            existing.confirmation_id, existing.state, identity.action_type,
        )
        raise BusinessOperationAlreadyActive(
            existing_confirmation_id=existing.confirmation_id,
            existing_state=existing.state,
        )
    from backend.customer_service.business_guard import terminal_blocked
    if terminal_blocked(identity.action_type):
        done = await repo.find_by_identity(
            tenant_id=identity.tenant_id,
            action_type=identity.action_type,
            target_type=identity.target_type,
            target_id=identity.target_id,
            semantic_fingerprint=identity.semantic_fingerprint,
            states=("success",),
        )
        if done is not None and done.confirmation_id != exclude_confirmation_id:
            logger.info(
                "[BusinessGuard] terminal-duplicate blocked: done=%s action=%s",
                done.confirmation_id, identity.action_type,
            )
            raise BusinessOperationAlreadyCompleted(completed_message)


async def _raise_on_in_doubt_ledger(repo, identity) -> None:
    """legacy failed 行的 IN_DOUBT 防线（§15/§36/§37）。

    failed 会释放 051 active 索引；若该操作的 Phase2 durable ledger 留有
    running / IDEMPOTENCY_UNCERTAIN 记录（副作用结果未知），守卫必须
    继续占住 —— 禁止「UNKNOWN → FAILED → 释放 → 二次执行」绕过 STOP C。
    """
    failed_rows = await repo.list_ids_by_identity(
        tenant_id=identity.tenant_id,
        action_type=identity.action_type,
        target_type=identity.target_type,
        target_id=identity.target_id,
        semantic_fingerprint=identity.semantic_fingerprint,
        states=("failed",),
    )
    for confirmation_id, user_id in failed_rows:
        if await repo.has_in_doubt_ledger(
                tenant_id=identity.tenant_id, user_id=user_id,
                confirmation_id=confirmation_id):
            logger.info(
                "[BusinessGuard] in-doubt ledger blocked: prior=%s "
                "(uncertain side-effect, STOP E reconciliation pending)",
                confirmation_id,
            )
            raise BusinessOperationAlreadyActive(
                existing_confirmation_id=confirmation_id,
                existing_state="failed(in_doubt_ledger)",
                message="该操作此前执行结果未知（IN_DOUBT），在人工裁决前不允许重复发起。",
            )


async def _update_with_guard(db, repo, confirmation_id: str,
                             pending_action: dict, *, tenant_id: str,
                             fingerprint: str | None) -> None:
    """proposal 覆盖 + 身份原子刷新（D19）；身份变更撞唯一索引 → 冲突（D20）。

    begin_nested：IntegrityError 只回滚内层 savepoint，外层事务
    （conversation ensure）得以保留。
    """
    try:
        async with db.begin_nested():
            await repo.update_proposal(
                confirmation_id, pending_action,
                tenant_id=tenant_id, semantic_fingerprint=fingerprint,
            )
    except IntegrityError as exc:
        logger.info(
            "[BusinessGuard] proposal-update collision on %s (D20)", confirmation_id)
        raise BusinessOperationConflict(
            "修改后的操作与另一处理中的操作语义等价，已被拒绝。",
            user_message="修改后的操作与处理中的另一操作冲突，请先取消当前操作。",
        ) from exc


async def _translate_integrity_error(db, exc: Exception, identity) -> None:
    """INSERT 撞 051 唯一索引 → 回滚并转译为业务冲突（§18，非 500）。"""
    if not isinstance(exc, IntegrityError) or identity is None:
        raise
    await db.rollback()
    from backend.customer_service.repository import ConfirmationRepository
    repo = ConfirmationRepository(db)
    existing = await repo.find_by_identity(
        tenant_id=identity.tenant_id,
        action_type=identity.action_type,
        target_type=identity.target_type,
        target_id=identity.target_id,
        semantic_fingerprint=identity.semantic_fingerprint,
        states=_GUARD_ACTIVE_STATES,
    )
    logger.info(
        "[BusinessGuard] concurrent insert deduplicated: existing=%s",
        existing.confirmation_id if existing is not None else "<race>",
    )
    raise BusinessOperationAlreadyActive(
        existing_confirmation_id=existing.confirmation_id if existing is not None else "",
        existing_state=existing.state if existing is not None else "",
    ) from exc


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
        # DB 认领异常时，L1 只能先消费 pending；若 DB 随后恢复而原行仍
        # 为 pending，下一次确认不能再次把同一动作认领出来。该 tombstone
        # 只覆盖当前进程的降级窗口，新动作 save/cache 时会清除。
        self._claimed_l1: set[tuple[str, str]] = set()
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
            self._claimed_l1.discard((user_id, session_id))
            self._data[(user_id, session_id)] = pending

    def save(self, user_id: str, session_id: str, pending_action: dict,
             tenant_id: str = "") -> None:
        # PostgreSQL 是确认状态的唯一事实源：只有持久化成功后才能更新
        # L1，否则后续 claim 可能把未落库的动作当成可执行状态。
        if self._db_save(user_id, session_id, pending_action, tenant_id=tenant_id):
            with self._lock:
                self._claimed_l1.discard((user_id, session_id))
                self._data[(user_id, session_id)] = pending_action

    def clear(self, user_id: str, session_id: str, *, final_state: str = "cancelled") -> None:
        """清除 pending 并把 DB 行置为终态。

        final_state: cancelled（用户取消）/ success / failed / expired。
        P1 修正：此前一律写 cancelled，过期与失败的审计口径失真。
        """
        if self._db_clear(user_id, session_id, final_state):
            with self._lock:
                self._data.pop((user_id, session_id), None)

    def claim_for_execution(self, user_id: str, session_id: str) -> str | None:
        """原子认领待确认动作（幂等闸门，P1）。

        DB 侧单条条件 UPDATE pending→confirmed：并发重复确认只有一方成功。
        DB 不可用时：严格模式抛 StoreWriteError（执行必须失败，不冒双执行
        之险）；非严格模式（测试/本地调试）降级为进程内 L1 认领并告警。
        返回 confirmation_id；已被处理/不存在 pending 返回 None。
        """
        key = (user_id, session_id)
        with self._lock:
            if key in self._claimed_l1:
                return None
        try:
            claimed_id = self._db_claim(user_id, session_id)
            if claimed_id is not None:
                # DB 认领成功 → 移除 L1 pending 条目
                with self._lock:
                    self._data.pop(key, None)
                return claimed_id
            # DB 确认无行（可能是 save 降级未落库）→ 回退 L1 认领。
            # L1 pop 原子，单进程内幂等保持；DB 有行时走 DB 闸门
            # （多实例安全），两分支都只认领一次。
            with self._lock:
                pending = self._data.pop(key, None)
                if pending is not None:
                    self._claimed_l1.add(key)
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
                pending = self._data.pop(key, None)
                if pending is not None:
                    self._claimed_l1.add(key)
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

    def _db_save(self, user_id: str, session_id: str, pending_action: dict,
                 tenant_id: str = "") -> bool:
        try:
            from backend.customer_service._db_loop import run_sync
            operation = self._async_save(user_id, session_id, pending_action,
                                         tenant_id=tenant_id)
            try:
                run_sync(operation)
            except Exception:
                operation.close()
                raise
            return True
        except BusinessOperationGuardError:
            # 守卫冲突是确定性业务结果，不是 DB 故障 —— 原样上抛给调用方
            # （§20：稳定业务语义，绝不伪装成功或 500）。
            raise
        except Exception as exc:
            # DB 失败时不更新 L1；严格模式继续抛错，非严格模式仅用于
            # 测试/本地诊断，调用方也不会得到一个伪成功的内存状态。
            _db_write_failed("ConfirmationStore", "save", exc)
            return False

    def _db_clear(self, user_id: str, session_id: str, final_state: str) -> bool:
        try:
            from backend.customer_service._db_loop import run_sync
            operation = self._async_clear(user_id, session_id, final_state)
            try:
                run_sync(operation)
            except Exception:
                operation.close()
                raise
            return True
        except Exception as exc:
            _db_write_failed("ConfirmationStore", "clear", exc)
            return False

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
        user_id: str, session_id: str, pending_action: dict,
        tenant_id: str = "",
    ) -> None:
        from backend.customer_service.managers.conversation_manager import (
            ConversationManager,
        )
        from backend.customer_service.repository import ConfirmationRepository
        from backend.memory.database import AsyncSessionLocal

        # Phase3 STOP D：Business Operation Unique Guard。
        # 身份在写入前解析（§26：guard 发生在一切执行之前）；硬约束由
        # migration 051 的 partial unique index 在 INSERT/UPDATE 时决胜
        # （§17：禁止先查后插——并发由 DB 唯一索引保证原子）。
        from backend.customer_service.business_guard import (
            BusinessOperationAlreadyActive,
            BusinessOperationAlreadyCompleted,
            BusinessOperationConflict,
            BusinessOperationGuardError,
            compute_identity,
            terminal_blocked,
        )
        identity = None
        try:
            identity = compute_identity(pending_action, tenant_id)
        except BusinessOperationGuardError:
            if _strict_writes():
                raise  # 正式业务写缺身份：fail closed（§54/§76）
            logger.warning(
                "[BusinessGuard] 身份缺失（非严格模式放行占位行）: %s",
                pending_action.get("action_type", ""),
            )

        async with AsyncSessionLocal() as db:
            # FK 生命周期（缺陷6.5，2026-09-23）：confirmations.conversation_id
            # 是指向 conversations 的 NOT NULL FK，而 conversation row 此前在
            # turn 结束时才 lazy get_or_create（runner._persist_cs_turn_if_needed）
            # —— 流中写 confirmation 先于 conversation insert，触发 FK violation。
            # 不变量：任何 conversation-dependent durable object 落库前，
            # parent row 必须存在。同事务内幂等 ensure（先查后插）+ flush，
            # FK 在同一事务内可见，随后 confirmation insert 才执行。
            # STOP D 并发补强（§17/§19）：同一新会话的并发双提交会在
            # conversations 唯一键上竞争 —— savepoint 内重试语义，撞键即
            # 回滚内层并复用对方已提交的会话行，guard 决胜统一交给 051 索引。
            conv_mgr = ConversationManager(db)
            try:
                async with db.begin_nested():
                    await conv_mgr.get_or_create(session_id, user_id)
                    await db.flush()
            except IntegrityError:
                logger.info(
                    "[BusinessGuard] conversation created concurrently, reusing "
                    "committed row: session=%s", session_id)

            repo = ConfirmationRepository(db)
            existing = await repo.load(user_id, session_id)
            if existing is not None:
                # 已有 pending 行（need_info 升级为正式 proposal、reask 更新
                # retry_count）：整行覆盖 proposal JSON。此前只 update_state
                # 会导致补槽结果/追问计数在 L1 失效后丢失。
                # STOP D（D19/D20）：身份列同事务原子刷新；升级后身份撞上
                # 另一 active 操作时 051 唯一索引拒绝 → 转译为业务冲突。
                if identity is not None:
                    await _raise_on_active_conflict(
                        repo, identity, exclude_confirmation_id="",
                        completed_message="该业务操作已提交成功，不允许重复发起。",
                    )
                await _update_with_guard(
                    db, repo, existing.confirmation_id, pending_action,
                    tenant_id=identity.tenant_id if identity else "",
                    fingerprint=identity.semantic_fingerprint if identity else None,
                )
            else:
                if identity is not None:
                    # 终态策略（§38/D18）：SUCCESS 后不可重发的动作类型
                    await _raise_on_active_conflict(
                        repo, identity, exclude_confirmation_id="",
                        completed_message="该业务操作已提交成功，不允许重复发起。",
                    )
                    # IN_DOUBT 防线（§15/§37/D13/R4）：legacy failed 行若在
                    # Phase2 ledger 留有 UNCERTAIN/running 记录，守卫不得释放
                    await _raise_on_in_doubt_ledger(repo, identity)
                try:
                    await repo.save(
                        user_id, session_id, pending_action,
                        tenant_id=identity.tenant_id if identity else "",
                        semantic_fingerprint=(
                            identity.semantic_fingerprint if identity else None),
                    )
                except Exception as exc:
                    await _translate_integrity_error(db, exc, identity)
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
