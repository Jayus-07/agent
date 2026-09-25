"""Phase2 Step6：副作用幂等 durable ledger 测试（Case A-V）。

被测对象：
  - shared/idempotency.PostgresIdempotencyLedgerStore（PG 原子 claim /
    owner CAS / 接管判定 / IN_DOUBT 保守阻断）
  - execute_idempotent_in_transaction（DB 内部副作用同事务原子执行，G13）
  - resolve_stale_side_effect（stale claim 人工裁决，§五十三）
  - purge_expired_idempotency_records（保留策略，§五十二）
  - tasks/side_effect.run_task_side_effect（fencing + 接管判定集成）
  - customer_service confirmation_flow._execute_confirmed_action（CS 接线）
  - AuditRepository.insert_action_idempotently（同事务落库闸门 SQL）

隔离：自建 pgtest_biz_ 前缀表 + 显式连接工厂，绝不触碰生产 ai 表。
"""
from __future__ import annotations

import threading
import time
from unittest.mock import AsyncMock, MagicMock, patch

import psycopg
import pytest

from backend.shared.idempotency import (
    ClaimStatus,
    IdempotencyConflict,
    IdempotencyContextMissing,
    IdempotencyUnavailable,
    MemoryIdempotencyStore,
    PostgresIdempotencyLedgerStore,
    canonical_fingerprint,
    execute_idempotent_in_transaction,
    purge_expired_idempotency_records,
    resolve_stale_side_effect,
    run_idempotent_side_effect,
)
from backend.shared.logger import logger

LEDGER_TABLE = "s6_idempotency_records"
PROBE_TABLE = "s6_side_effect_probe"

_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS {table} (
    tenant_id text NOT NULL,
    actor_id text NOT NULL,
    operation text NOT NULL,
    client_key text NOT NULL,
    request_hash character(64) NOT NULL,
    status text NOT NULL DEFAULT 'running'
        CHECK (status IN ('running', 'succeeded', 'failed')),
    result jsonb,
    error_code text,
    attempt integer NOT NULL DEFAULT 0 CHECK (attempt >= 0),
    lease_id uuid,
    lease_expires_at timestamptz,
    expires_at timestamptz,
    owner_execution_id varchar(64),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, actor_id, operation, client_key)
)
"""

_PROBE_DDL = """
CREATE TABLE IF NOT EXISTS {table} (
    probe_key varchar(128) NOT NULL,
    execution_id varchar(64) NOT NULL,
    payload jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (probe_key, execution_id)
)
"""


def _dsn() -> str:
    from backend.config.database import MEMORY_DB_CONFIG

    config = MEMORY_DB_CONFIG
    return (
        f"postgresql://{config['user']}:{config['password']}"
        f"@{config['host']}:{config['port']}/{config['dbname']}"
    )


def _factory():
    return psycopg.connect(_dsn())


def test_default_memory_ledger_connection_uses_configured_timeout(monkeypatch):
    """ledger 默认连接必须有界，避免宿主机 PG 半开时无限等待。"""
    from backend.shared import idempotency
    from backend.config import database
    captured = {}
    sentinel = object()

    def fake_connect(dsn, **kwargs):
        captured["dsn"] = dsn
        captured["kwargs"] = kwargs
        return sentinel

    monkeypatch.setattr(psycopg, "connect", fake_connect)
    monkeypatch.setattr(database, "DB_CONNECT_TIMEOUT", 7)
    assert idempotency._default_memory_ledger_connection() is sentinel
    assert captured["kwargs"] == {"connect_timeout": 7}


@pytest.fixture()
def ledger_env():
    """建 pgtest 隔离表 + 返回 (store_factory, 探针计数 helper)。"""
    with _factory() as conn, conn.cursor() as cur:
        cur.execute(_TABLE_DDL.format(table=LEDGER_TABLE))
        cur.execute(_PROBE_DDL.format(table=PROBE_TABLE))
        cur.execute(f"DELETE FROM {LEDGER_TABLE}")
        cur.execute(f"DELETE FROM {PROBE_TABLE}")
        conn.commit()
    yield _factory
    with _factory() as conn, conn.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {LEDGER_TABLE}")
        cur.execute(f"DROP TABLE IF EXISTS {PROBE_TABLE}")
        conn.commit()


def _probe_count(probe_key: str) -> int:
    with _factory() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT count(*) FROM {PROBE_TABLE} WHERE probe_key = %s",
            (probe_key,),
        )
        count = int(cur.fetchone()[0])
        conn.rollback()
        return count


def _expire_claims(probe_key: str) -> None:
    """把 ledger 行的租约拨到过去（模拟 crash 后租约过期）。"""
    with _factory() as conn, conn.cursor() as cur:
        cur.execute(
            f"UPDATE {LEDGER_TABLE} SET lease_expires_at = now() - interval '1s' "
            "WHERE client_key = %s",
            (probe_key,),
        )
        conn.commit()


def _ledger_row(probe_key: str) -> tuple | None:
    with _factory() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT status, error_code, attempt FROM {LEDGER_TABLE} "
            "WHERE client_key = %s",
            (probe_key,),
        )
        row = cur.fetchone()
        conn.rollback()
        return row


# ═════════════════════════════════════════════════
# Ledger store 基础语义（Case A/C/F/G/K）
# ═════════════════════════════════════════════════

def _key(client_key: str, tenant: str = "t1", actor: str = "u1"):
    from backend.shared.idempotency import IdempotencyKey

    return IdempotencyKey(
        tenant_id=tenant, actor_id=actor,
        operation="probe.effect", client_key=client_key,
    )


class TestLedgerStoreBasics:
    def test_case_a_first_claim_new_with_owner(self, ledger_env):
        store = PostgresIdempotencyLedgerStore(
            ledger_env, table=LEDGER_TABLE, owner_execution_id="exec-1")
        claim = store.claim(_key("k-a"), {"v": 1})
        assert claim.status == ClaimStatus.NEW
        assert claim.lease_id
        row = _ledger_row("k-a")
        assert row[0] == "running"
        # owner_execution_id 已登记（Step6 §十七：execution 只能做 owner）
        with _factory() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT owner_execution_id FROM {LEDGER_TABLE} "
                "WHERE client_key = 'k-a'")
            assert cur.fetchone()[0] == "exec-1"
            conn.rollback()

    def test_case_c_complete_success_owner_cas(self, ledger_env):
        store = PostgresIdempotencyLedgerStore(
            ledger_env, table=LEDGER_TABLE, owner_execution_id="exec-1")
        claim = store.claim(_key("k-c"), {"v": 1})
        store.complete(claim.lease_id, {"ok": True}, key=_key("k-c"))
        assert _ledger_row("k-c")[0] == "succeeded"

        # Case F：旧 owner 的 lease 在终态后失效——重复 complete 拒绝
        with pytest.raises(ValueError):
            store.complete(claim.lease_id, {"ok": True}, key=_key("k-c"))

    def test_case_f_stale_owner_cannot_complete_after_takeover(self, ledger_env):
        old = PostgresIdempotencyLedgerStore(
            ledger_env, table=LEDGER_TABLE, owner_execution_id="exec-old")
        claim = old.claim(_key("k-f"), {"v": 1})
        # 租约过期 + owner 已确认死亡 → 新 execution 接管
        _expire_claims("k-f")
        new = PostgresIdempotencyLedgerStore(
            ledger_env, table=LEDGER_TABLE, owner_execution_id="exec-new",
            takeover_allowed=lambda info: True)
        claim2 = new.claim(_key("k-f"), {"v": 1})
        assert claim2.status == ClaimStatus.NEW
        assert claim2.lease_id != claim.lease_id
        # 旧 execution 不得 complete 新 execution 已接管的记录（G7）
        with pytest.raises(ValueError):
            old.complete(claim.lease_id, {"stale": True}, key=_key("k-f"))
        # 新 owner 正常 complete
        new.complete(claim2.lease_id, {"ok": True}, key=_key("k-f"))
        assert _ledger_row("k-f")[0] == "succeeded"

    def test_case_g_fail_then_retry_reexecutes(self, ledger_env):
        store = PostgresIdempotencyLedgerStore(
            ledger_env, table=LEDGER_TABLE, owner_execution_id="exec-1")
        claim = store.claim(_key("k-g"), {"v": 1})
        store.fail(claim.lease_id, "INTERNAL_ERROR", key=_key("k-g"))
        # failed = 已确认未产生副作用 → 接管重试无条件允许（Case G）
        claim2 = store.claim(_key("k-g"), {"v": 1})
        assert claim2.status == ClaimStatus.NEW
        assert _ledger_row("k-g")[2] == 2  # attempt 递增

    def test_case_e_payload_conflict_fail_closed(self, ledger_env):
        store = PostgresIdempotencyLedgerStore(
            ledger_env, table=LEDGER_TABLE, owner_execution_id="exec-1")
        claim = store.claim(_key("k-e"), {"amount": 100})
        assert claim.status == ClaimStatus.NEW
        store.complete(claim.lease_id, {"ok": True}, key=_key("k-e"))
        # 同 key 不同 payload = 调用方错误 → CONFLICT（G6）
        conflict = store.claim(_key("k-e"), {"amount": 200})
        assert conflict.status == ClaimStatus.CONFLICT
        assert conflict.error_code == "IDEMPOTENCY_CONFLICT"

    def test_case_k_key_stable_across_execution_ids(self, ledger_env):
        first = PostgresIdempotencyLedgerStore(
            ledger_env, table=LEDGER_TABLE, owner_execution_id="exec-1")
        claim = first.claim(_key("k-k"), {"v": 1})
        first.complete(claim.lease_id, {"n": 1}, key=_key("k-k"))
        # retry/recovery 换发 execution_id，key 不变 → 重放同一结果（G2）
        second = PostgresIdempotencyLedgerStore(
            ledger_env, table=LEDGER_TABLE, owner_execution_id="exec-2")
        claim2 = second.claim(_key("k-k"), {"v": 1})
        assert claim2.status == ClaimStatus.SUCCEEDED
        assert claim2.result == {"n": 1}

    def test_case_n_tenant_isolation(self, ledger_env):
        store = PostgresIdempotencyLedgerStore(ledger_env, table=LEDGER_TABLE)
        c1 = store.claim(_key("shared", tenant="tA"), {"v": 1})
        c2 = store.claim(_key("shared", tenant="tB"), {"v": 1})
        assert c1.status == ClaimStatus.NEW
        assert c2.status == ClaimStatus.NEW  # 租户隔离：互不 dedup（G3）

    def test_case_o_different_logical_operations_both_execute(self, ledger_env):
        from backend.shared.idempotency import IdempotencyKey

        store = PostgresIdempotencyLedgerStore(ledger_env, table=LEDGER_TABLE)
        c1 = store.claim(_key("conf-a"), {"v": 1})
        c2 = store.claim(_key("conf-b"), {"v": 1})
        assert c1.status == ClaimStatus.NEW
        assert c2.status == ClaimStatus.NEW  # 合法的第二次操作不被挡（Q20）


# ═════════════════════════════════════════════════
# 并发 claim 与 crash 窗口（Case B/H/I/J/R）
# ═════════════════════════════════════════════════

class TestConcurrencyAndCrashWindows:
    def test_case_b_concurrent_claim_single_executor(self, ledger_env):
        """20 线程抢同一 key：恰好 1 个 NEW，其余 RUNNING（G4）。"""
        store = PostgresIdempotencyLedgerStore(
            ledger_env, table=LEDGER_TABLE, owner_execution_id="exec-x")
        results: list[ClaimStatus] = []
        lock = threading.Lock()

        def _claim():
            c = store.claim(_key("k-b"), {"v": 1})
            with lock:
                results.append(c.status)

        threads = [threading.Thread(target=_claim) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert results.count(ClaimStatus.NEW) == 1
        assert results.count(ClaimStatus.RUNNING) == 19
        assert _probe_count("k-b") == 0  # 只有 wrapper 会执行副作用

    def test_case_i_crash_before_effect_conservative_block_then_resolve(
        self, ledger_env,
    ):
        """claim 后 crash（无 takeover 判定）→ 阻断；人工裁决后可重试。"""
        store = PostgresIdempotencyLedgerStore(
            ledger_env, table=LEDGER_TABLE, owner_execution_id="exec-1")
        first = store.claim(_key("k-i"), {"v": 1})
        assert first.status == ClaimStatus.NEW
        _expire_claims("k-i")  # 模拟 crash 后租约过期

        # 无 takeover_allowed → 保守阻断（IN_DOUBT，绝不按时间重执行，G15）
        blocker = PostgresIdempotencyLedgerStore(
            ledger_env, table=LEDGER_TABLE, owner_execution_id="exec-2")
        blocked = blocker.claim(_key("k-i"), {"v": 1})
        assert blocked.status == ClaimStatus.CONFLICT
        assert blocked.error_code == "IDEMPOTENCY_UNCERTAIN"

        # 人工裁决「未执行」→ FAILED → 重试放行（§五十三）
        resolved = resolve_stale_side_effect(
            tenant_id="t1", actor_id="u1", operation="probe.effect",
            client_key="k-i", decision="not_executed",
            connection_factory=ledger_env, table=LEDGER_TABLE,
        )
        assert resolved is True
        retry = blocker.claim(_key("k-i"), {"v": 1})
        assert retry.status == ClaimStatus.NEW

    def test_case_i_takeover_allowed_when_execution_confirmed_dead(
        self, ledger_env,
    ):
        """提供接管判定且判定为真 → 直接接管，无需人工介入。"""
        store = PostgresIdempotencyLedgerStore(
            ledger_env, table=LEDGER_TABLE, owner_execution_id="exec-1")
        store.claim(_key("k-i2"), {"v": 1})
        _expire_claims("k-i2")
        takeover = PostgresIdempotencyLedgerStore(
            ledger_env, table=LEDGER_TABLE, owner_execution_id="exec-2",
            takeover_allowed=lambda info: True)
        assert takeover.claim(_key("k-i2"), {"v": 1}).status == ClaimStatus.NEW

    def test_stale_resolve_never_touches_fresh_claim(self, ledger_env):
        """裁决只处理租约已过期的 running 行，活跃 claim 不受影响。"""
        store = PostgresIdempotencyLedgerStore(
            ledger_env, table=LEDGER_TABLE, owner_execution_id="exec-1")
        store.claim(_key("k-fresh"), {"v": 1})
        resolved = resolve_stale_side_effect(
            tenant_id="t1", actor_id="u1", operation="probe.effect",
            client_key="k-fresh", decision="not_executed",
            connection_factory=ledger_env, table=LEDGER_TABLE,
        )
        assert resolved is False
        assert _ledger_row("k-fresh")[0] == "running"


# ═════════════════════════════════════════════════
# wrapper / 原子执行器（Case D/H/P/Q/U/V）
# ═════════════════════════════════════════════════

class TestWrapperAndAtomicExecutor:
    def _patched_side_effect(self, ledger_env):
        """把 run_idempotent_side_effect 的默认连接/表替换为隔离环境。"""
        import functools

        from backend.shared import idempotency as idem

        partial = functools.partial(
            idem.PostgresIdempotencyLedgerStore,
            connection_factory=ledger_env, table=LEDGER_TABLE)
        return patch.object(idem, "PostgresIdempotencyLedgerStore", partial)

    def test_case_d_duplicate_success_replays_cached_result(self, ledger_env):
        with self._patched_side_effect(ledger_env):
            calls: list[int] = []

            def _fn():
                calls.append(1)
                return {"n": len(calls)}

            first = run_idempotent_side_effect(
                "probe.effect", {"v": 1}, _fn,
                tenant_id="t1", actor_id="u1", client_key="k-d")
            second = run_idempotent_side_effect(
                "probe.effect", {"v": 1}, _fn,
                tenant_id="t1", actor_id="u1", client_key="k-d")
            assert first == second == {"n": 1}
            assert len(calls) == 1  # 真实副作用只发生一次（G5）

    def test_case_h_recovery_after_success_replays(self, ledger_env):
        """副作用已 SUCCEEDED、task 未 mark SUCCESS 即 crash → recovery 重入
        直接 replay，不再执行（本 Step 最关键场景，G9/G12）。"""
        with self._patched_side_effect(ledger_env):
            calls: list[int] = []

            def _fn():
                calls.append(1)
                return {"n": len(calls)}

            run_idempotent_side_effect(
                "probe.effect", {"v": 1}, _fn,
                tenant_id="t1", actor_id="u1", client_key="k-h")
            assert _probe_count("k-h") == 0  # wrapper 层无探针写入
            # 模拟 recovery：全新 wrapper（新 owner）重入同一 logical operation
            run_idempotent_side_effect(
                "probe.effect", {"v": 1}, _fn,
                tenant_id="t1", actor_id="u1", client_key="k-h",
                owner_execution_id="new-exec")
            assert len(calls) == 1

    def test_case_v_missing_identity_fail_closed(self, ledger_env):
        with self._patched_side_effect(ledger_env):
            with pytest.raises(IdempotencyContextMissing):
                run_idempotent_side_effect(
                    "probe.effect", {"v": 1}, lambda: {"ok": 1},
                    tenant_id="", actor_id="u1", client_key="k-v")

    def test_case_p_atomic_rollback_marks_failed_retry_safe(self, ledger_env):
        """同事务模式：业务写失败 → 一起回滚 + ledger FAILED → 重试成功。"""
        calls: list[int] = []

        def _tx_fn(conn):
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("boom")
            with conn.cursor() as cur:
                cur.execute(
                    f"INSERT INTO {PROBE_TABLE} (probe_key, execution_id) "
                    "VALUES (%s, %s)", ("k-p", f"exec-{len(calls)}"))
            return {"n": len(calls)}

        with pytest.raises(RuntimeError):
            execute_idempotent_in_transaction(
                ledger_env, _key("k-p"), {"v": 1}, _tx_fn,
                owner_execution_id="exec-1", table=LEDGER_TABLE)
        # 第一次失败：回滚 → 探针无行 + ledger FAILED（可安全重试）
        assert calls == [1]
        assert _probe_count("k-p") == 0
        assert _ledger_row("k-p")[0] == "failed"
        execute_idempotent_in_transaction(
            ledger_env, _key("k-p"), {"v": 1}, _tx_fn,
            owner_execution_id="exec-2", table=LEDGER_TABLE)
        # 第二次成功：换 owner 重试恰好一次
        assert len(calls) == 2
        assert _probe_count("k-p") == 1
        assert _ledger_row("k-p")[0] == "succeeded"
        assert _probe_count("k-p") == 1  # 副作用恰好一次

    def test_case_q_atomic_success_writes_both(self, ledger_env):
        """同事务模式成功：业务行与 ledger SUCCEEDED 同事务落库（G13）。"""
        def _tx_fn(conn):
            with conn.cursor() as cur:
                cur.execute(
                    f"INSERT INTO {PROBE_TABLE} (probe_key, execution_id) "
                    "VALUES ('k-q', 'exec-1')")
            return {"ok": True}

        result = execute_idempotent_in_transaction(
            ledger_env, _key("k-q"), {"v": 1}, _tx_fn, table=LEDGER_TABLE)
        assert result == {"ok": True}
        assert _probe_count("k-q") == 1
        assert _ledger_row("k-q")[0] == "succeeded"
        # 重放：不再执行
        replay = execute_idempotent_in_transaction(
            ledger_env, _key("k-q"), {"v": 1},
            lambda conn: pytest.fail("重放不得进入事务体"),
            table=LEDGER_TABLE)
        assert replay == {"ok": True}
        assert _probe_count("k-q") == 1

    def test_case_u_retention_purges_only_expired(self, ledger_env):
        """保留策略：只删显式 expires_at 过期行；NULL（永久）与未过期、
        stale RUNNING 一律保留（§五十二）。"""
        with _factory() as conn, conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO {LEDGER_TABLE} (tenant_id, actor_id, operation, "
                "client_key, request_hash, status, expires_at) VALUES "
                "('t1','u1','op','expired','x','succeeded', now() - interval '1d')")
            cur.execute(
                f"INSERT INTO {LEDGER_TABLE} (tenant_id, actor_id, operation, "
                "client_key, request_hash, status) VALUES "
                "('t1','u1','op','permanent','x','succeeded')")
            cur.execute(
                f"INSERT INTO {LEDGER_TABLE} (tenant_id, actor_id, operation, "
                "client_key, request_hash, status, expires_at) VALUES "
                "('t1','u1','op','future','x','succeeded', now() + interval '1d')")
            cur.execute(
                f"INSERT INTO {LEDGER_TABLE} (tenant_id, actor_id, operation, "
                "client_key, request_hash, status, lease_expires_at) VALUES "
                "('t1','u1','op','stale','x','running', now() - interval '1h')")
            conn.commit()
        deleted = purge_expired_idempotency_records(
            ledger_env, table=LEDGER_TABLE)
        assert deleted == 1
        with _factory() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT client_key FROM {LEDGER_TABLE}")
            remaining = {r[0] for r in cur.fetchall()}
            conn.rollback()
        assert remaining == {"permanent", "future", "stale"}


# ═════════════════════════════════════════════════
# 任务侧 wrapper（fencing + 接管判定）
# ═════════════════════════════════════════════════

class TestTaskSideEffectWrapper:
    def test_execution_is_dead_semantics(self, ledger_env):
        from backend.models.task import TaskRecord, TaskStatus
        from backend.tasks.side_effect import execution_is_dead

        def _record(status: TaskStatus, execution_id: str):
            rec = MagicMock()
            rec.status = status
            rec.execution_id = execution_id
            return rec

        running = _record(TaskStatus.RUNNING, "exec-1")
        with patch("backend.services.task_service.get_task",
                   return_value=running):
            assert execution_is_dead("t", "exec-1") is False
            assert execution_is_dead("t", "exec-2") is True  # 已换发
        failed = _record(TaskStatus.FAILED, "exec-1")
        with patch("backend.services.task_service.get_task",
                   return_value=failed):
            assert execution_is_dead("t", "exec-1") is True  # 已离开 RUNNING
        with patch("backend.services.task_service.get_task",
                   return_value=None):
            assert execution_is_dead("t", "exec-1") is True

    def test_fencing_rejects_lost_lease(self, ledger_env):
        """租约失权（fencing）→ TaskLeaseLost，副作用不执行（§十八）。"""
        from backend.models.task import TaskLeaseLost
        from backend.tasks import side_effect as se_mod

        with patch.object(se_mod, "run_idempotent_side_effect") as wrap_mock:
            def _raise(op, payload, fn, **kwargs):
                kwargs["pre_execute"]()
                return {"ok": 1}

            wrap_mock.side_effect = _raise
            with patch("backend.services.task_service.check_lease_active",
                       return_value=False):
                with pytest.raises(TaskLeaseLost):
                    se_mod.run_task_side_effect(
                        task_id="t-1", operation="probe.effect",
                        client_key="c-1", tenant_id="t1", actor_id="u1",
                        payload={}, fn=lambda: {"ok": 1},
                        execution_id="exec-1")

    def test_takeover_predicate_uses_execution_is_dead(self, ledger_env):
        from backend.tasks import side_effect as se_mod

        captured = {}
        with patch.object(se_mod, "run_idempotent_side_effect") as wrap_mock:
            def _capture(op, payload, fn, **kw):
                captured["takeover"] = kw["takeover_allowed"]
                return {"ok": 1}

            wrap_mock.side_effect = _capture
            with patch.object(se_mod, "execution_is_dead",
                              return_value=True) as dead_mock:
                se_mod.run_task_side_effect(
                    task_id="t-1", operation="probe.effect",
                    client_key="c-1", tenant_id="t1", actor_id="u1",
                    payload={}, fn=lambda: {"ok": 1},
                    execution_id="exec-1")
                assert captured["takeover"](
                    {"owner_execution_id": "exec-0"}) is True
                dead_mock.assert_called_once_with("t-1", "exec-0")


# ═════════════════════════════════════════════════
# CS 确认执行接线（真实 PG ledger）+ 审计落库闸门 SQL
# ═════════════════════════════════════════════════

def _pending_action(action_id: str) -> dict:
    return {
        "action_id": action_id,
        "action_type": "refund_request",
        "target_type": "order",
        "target_id": "order-9",
        "risk_level": "high",
        "proposal_text": "退款 proposal",
        "confirmation_state": "pending",
        "created_at": "2099-01-01T00:00:00+00:00",
        "expires_at": "2099-01-02T00:00:00+00:00",
        "retry_count": 0,
    }


class TestCSActionIdempotency:
    def test_case_d_cs_confirm_replay_same_record(self, ledger_env):
        """同 confirmation 重入（crash/重试）→ 复用同一 action_record，
        _simulate_execute 只调一次（G5/G9）。"""
        import functools

        from backend.customer_service import confirmation_flow
        from backend.shared import idempotency as idem

        partial = functools.partial(
            idem.PostgresIdempotencyLedgerStore,
            connection_factory=ledger_env, table=LEDGER_TABLE)
        record = {
            "action_id": "act-cs-1", "action_type": "refund_request",
            "agent_type": "ai", "target_type": "order",
            "target_id": "order-9", "data": {}, "status": "simulated",
            "created_at": "2026-09-24T00:00:00+00:00",
            "executed_at": None, "error_message": None,
        }
        with patch.object(idem, "PostgresIdempotencyLedgerStore", partial), \
                patch("backend.customer_service.experts.action._simulate_execute",
                      return_value=MagicMock(to_dict=lambda: record)) as sim:
            first = confirmation_flow._execute_confirmed_action(
                _pending_action("act-cs-1"),
                user_id="u1", tenant_id="t1")
            second = confirmation_flow._execute_confirmed_action(
                _pending_action("act-cs-1"),
                user_id="u1", tenant_id="t1")
            assert first == second == record
            assert sim.call_count == 1

    def test_cs_confirm_new_confirmation_executes_fresh(self, ledger_env):
        """合法的新 confirmation（新 id）不被旧记录挡住（Q20/Case O）。"""
        import functools

        from backend.customer_service import confirmation_flow
        from backend.shared import idempotency as idem

        partial = functools.partial(
            idem.PostgresIdempotencyLedgerStore,
            connection_factory=ledger_env, table=LEDGER_TABLE)
        record = {"action_id": "", "status": "simulated", "data": {}}
        with patch.object(idem, "PostgresIdempotencyLedgerStore", partial), \
                patch("backend.customer_service.experts.action._simulate_execute",
                      return_value=MagicMock(
                          to_dict=lambda: {"action_id": "new", "v": 1})) as sim:
            r1 = confirmation_flow._execute_confirmed_action(
                _pending_action("conf-1"), user_id="u1", tenant_id="t1")
            r2 = confirmation_flow._execute_confirmed_action(
                _pending_action("conf-2"), user_id="u1", tenant_id="t1")
            assert sim.call_count == 2
            assert r1["action_id"] == "new" and r2["action_id"] == "new"
            del record


class TestAuditPersistGateSQL:
    def _repo(self, first_fetchone, second_fetchone=None):
        from backend.customer_service.repository.audit_repo import (
            AuditRepository,
        )

        repo = AuditRepository(MagicMock())
        first = MagicMock()
        first.fetchone.return_value = first_fetchone
        results = [first]
        if second_fetchone is not None:
            second = MagicMock()
            second.fetchone.return_value = second_fetchone
            results.append(second)
        repo._s = MagicMock()
        repo._s.execute = AsyncMock(side_effect=results)
        repo._s.flush = AsyncMock()
        return repo

    def test_gate_claims_then_writes(self):
        import asyncio

        repo = self._repo(first_fetchone=("cs.action.persist",))
        # 第一次：闸门命中 → 写审计 + 动作
        persisted = asyncio.run(
            repo.insert_action_idempotently(
                {"action_id": "a1", "user_id": "u1"}, [{"user_id": "u1"}]))
        assert persisted is True
        assert repo._s.add.call_count == 2  # audit_log + agent_action

    def test_gate_conflict_same_id_different_content(self):
        import asyncio

        # 闸门未命中（已存在），且 request_hash 不一致 → 冲突 fail closed
        repo = self._repo(first_fetchone=None, second_fetchone=("different",))
        with pytest.raises(ValueError) as exc:
            asyncio.run(
                repo.insert_action_idempotently(
                    {"action_id": "a1", "user_id": "u1"}, [{"user_id": "u1"}]))
        assert "IDEMPOTENCY_CONFLICT" in str(exc.value)

    def test_gate_dedup_same_content(self):
        import asyncio

        from backend.shared.idempotency import canonical_fingerprint

        record = {"action_id": "a1", "user_id": "u1", "status": "simulated"}
        repo = self._repo(
            first_fetchone=None,
            second_fetchone=(canonical_fingerprint(record),))
        persisted = asyncio.run(
            repo.insert_action_idempotently(record, [{"user_id": "u1"}]))
        assert persisted is False  # 已落库过 → 幂等跳过
