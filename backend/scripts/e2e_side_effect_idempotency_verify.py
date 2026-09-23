"""Side-Effect Idempotency 独立第二路径验证 v2(Phase2-G 同款方法论)。

不依赖 pytest,在容器内直连容器 PG。核心语义全部走
PostgresIdempotencyLedgerStore(显式 s6v_ 隔离表)+ IdempotencyExecutor——
与 run_idempotent_side_effect 的内部路径(shared/idempotency.py 末尾:
构造 store + executor.execute)完全同构;真实入口 run_idempotent_side_effect
另做一条冒烟(写生产表,finally 清理)。
"""
import sys
import threading
import time

import psycopg

from backend.shared.idempotency import (
    ClaimStatus,
    IdempotencyConflict,
    IdempotencyUnavailable,
    IdempotencyExecutor,
    PostgresIdempotencyLedgerStore,
    canonical_fingerprint,
    execute_idempotent_in_transaction,
    purge_expired_idempotency_records,
    resolve_stale_side_effect,
    run_idempotent_side_effect,
)

LEDGER = "s6v_idempotency_records"
SMOKE_KEYS = []


DDL = """
CREATE TABLE IF NOT EXISTS {t} (
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


def factory():
    from backend.config.database import MEMORY_DB_CONFIG

    c = MEMORY_DB_CONFIG
    dsn = (
        f"postgresql://{c['user']}:{c['password']}"
        f"@{c['host']}:{c['port']}/{c['dbname']}"
    )
    return psycopg.connect(dsn, connect_timeout=5)


def cleanup():
    with factory() as conn, conn.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {LEDGER}")
        conn.commit()


def setup():
    with factory() as conn, conn.cursor() as cur:
        cur.execute(DDL.format(t=LEDGER))
        cur.execute(f"DELETE FROM {LEDGER}")
        conn.commit()


def row(client_key, tenant="t1"):
    with factory() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT status, error_code, attempt, owner_execution_id "
            f"FROM {LEDGER} WHERE tenant_id=%s AND actor_id='u1' AND "
            f"operation='probe.effect' AND client_key=%s",
            (tenant, client_key),
        )
        r = cur.fetchone()
        conn.rollback()
        return r


def ledger_store(owner="", lease=300, takeover=None):
    return PostgresIdempotencyLedgerStore(
        factory, table=LEDGER, lease_seconds=lease,
        owner_execution_id=owner, takeover_allowed=takeover,
    )


def executor(owner="", lease=300, takeover=None):
    """与 run_idempotent_side_effect 内部同构:store + executor.execute。"""
    return IdempotencyExecutor(ledger_store(owner, lease, takeover), None)


def key(ck, tenant="t1", actor="u1"):
    from backend.shared.idempotency import IdempotencyKey

    return IdempotencyKey(tenant_id=tenant, actor_id=actor,
                          operation="probe.effect", client_key=ck)


RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(f"{'PASS' if cond else 'FAIL'} | {name} | {detail}", flush=True)


def t1_first_claim():
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        return {"ok": True}

    r = executor(owner="exec-A").execute(key("v-first"), {"k": 1}, fn)
    st = row("v-first")
    check("1_first_claim_new_owner",
          r.get("ok") and calls["n"] == 1 and st and st[0] == "succeeded"
          and st[3] == "exec-A" and st[2] == 1, f"row={st}")


def t2_replay_after_success():
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        return {"order": "A1"}

    ex = executor(owner="exec-1")
    ex.execute(key("v-dup"), {"k": 2}, fn)
    r = executor(owner="exec-2").execute(key("v-dup"), {"k": 2}, fn)
    check("2_replay_cached_no_recall",
          calls["n"] == 1 and r == {"order": "A1"},
          f"fn_calls={calls['n']} replay={r}")


def t3_concurrent_claim():
    store = ledger_store(owner="exec-C")
    outcomes = {"new": 0, "other": 0}
    lock = threading.Lock()

    def worker():
        try:
            res = store.claim(key("v-conc20"), {"p": 1})
            s = res.status.value
        except Exception:
            s = "error"
        with lock:
            outcomes["new" if s == "new" else "other"] += 1

    ts = [threading.Thread(target=worker) for _ in range(20)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    check("3_concurrent_single_winner", outcomes["new"] == 1,
          f"new={outcomes['new']} other={outcomes['other']} (20 threads)")


def t4_failed_takeover_retry():
    store = ledger_store(owner="exec-1")
    res = store.claim(key("v-retry"), {"p": 1})
    store.fail(res.lease_id, "INTERNAL_ERROR", key=key("v-retry"))
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        return {"retry": True}

    r = executor(owner="exec-2").execute(key("v-retry"), {"p": 1}, fn)
    st = row("v-retry")
    check("4_failed_takeover_retry",
          calls["n"] == 1 and st[2] == 2 and st[0] == "succeeded"
          and st[3] == "exec-2", f"attempt={st[2]} owner={st[3]}")


def t5_crash_window_blocked():
    store = ledger_store(owner="exec-1", lease=1)
    store.claim(key("v-crash"), {"p": 1})
    time.sleep(1.3)  # 租约过期,模拟 effect 后 complete 前 crash
    blocked = False
    try:
        executor(owner="exec-2").execute(key("v-crash"), {"p": 1},
                                         lambda: {"x": 1})
    except IdempotencyUnavailable:
        blocked = True  # 无接管判定 → 保守阻断(IN_DOUBT)
    st = row("v-crash")
    check("5_crash_window_in_doubt_blocked",
          blocked and st[0] == "running",
          f"blocked={blocked} row={st[0]}/{st[1]}")


def t6_resolve_executed_replay():
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        return {"once": True}

    ok = resolve_stale_side_effect(
        tenant_id="t1", actor_id="u1", operation="probe.effect",
        client_key="v-crash", decision="executed", result={"once": True},
        reason="s6 verification", connection_factory=factory, table=LEDGER,
    )
    r = executor().execute(key("v-crash"), {"p": 1}, fn)
    check("6_resolve_executed_replay",
          ok and calls["n"] == 0 and r == {"once": True},
          f"resolved={ok} fn_calls={calls['n']} replay={r}")


def t7_resolve_not_executed_retry():
    store = ledger_store(owner="exec-1", lease=1)
    store.claim(key("v-notexec"), {"p": 1})
    time.sleep(1.3)
    ok = resolve_stale_side_effect(
        tenant_id="t1", actor_id="u1", operation="probe.effect",
        client_key="v-notexec", decision="not_executed",
        reason="s6 verification", connection_factory=factory, table=LEDGER,
    )
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        return {"did": True}

    r = executor(owner="exec-2").execute(key("v-notexec"), {"p": 1}, fn)
    check("7_resolve_notexec_retry",
          ok and calls["n"] == 1 and r == {"did": True},
          f"resolved={ok} fn_calls={calls['n']}")


def t8_owner_rotation_same_key():
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        return {"effect": "only-once"}

    executor(owner="exec-1").execute(key("v-rot"), {"p": 1}, fn)
    r = executor(owner="exec-2").execute(key("v-rot"), {"p": 1}, fn)
    check("8_owner_rotation_replay",
          calls["n"] == 1 and r == {"effect": "only-once"},
          f"fn_calls={calls['n']}")


def t9_tenant_isolation():
    calls = {"ta": 0, "tb": 0}

    def run(tenant):
        def fn():
            calls[tenant] += 1
            return {"tenant": tenant}

        return executor().execute(key("v-tenant", tenant=tenant),
                                  {"p": 1}, fn)

    ra = run("ta")
    rb = run("tb")
    check("9_tenant_isolation",
          calls["ta"] == 1 and calls["tb"] == 1
          and ra["tenant"] == "ta" and rb["tenant"] == "tb",
          f"ta={calls['ta']} tb={calls['tb']}")


def t10_atomic_transaction():
    calls = {"n": 0}

    def tx_ok(conn):
        calls["n"] += 1
        with conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO {LEDGER} (tenant_id, actor_id, operation, "
                "client_key, request_hash, status) VALUES "
                "(%s,'u1','probe.effect',%s,%s,'succeeded') "
                "ON CONFLICT (tenant_id, actor_id, operation, client_key) "
                "DO UPDATE SET status='succeeded'",
                ("t1", "v-tx-business-row", canonical_fingerprint({"p": 1})),
            )
        return {"tx": "committed"}

    r1 = execute_idempotent_in_transaction(
        factory, key("v-tx"), {"p": 1}, tx_ok, table=LEDGER,
        owner_execution_id="exec-tx")

    def tx_boom(conn):
        raise RuntimeError("business failure")

    rolled_back = False
    try:
        execute_idempotent_in_transaction(
            factory, key("v-tx2"), {"p": 1}, tx_boom, table=LEDGER)
    except RuntimeError:
        rolled_back = True
    r3 = execute_idempotent_in_transaction(
        factory, key("v-tx2"), {"p": 1}, tx_ok, table=LEDGER)
    check("10_atomic_tx",
          r1 == {"tx": "committed"} and rolled_back
          and r3 == {"tx": "committed"} and calls["n"] == 2,
          f"calls={calls['n']} (1 成功 + 1 回滚后重试)")


def t11_fail_closed():
    def broken_factory():
        raise RuntimeError("pg down")

    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        return {"should": "not-happen"}

    raised = False
    try:
        PostgresIdempotencyLedgerStore(
            broken_factory, table=LEDGER).claim(key("v-failclosed"), {"p": 1})
    except IdempotencyUnavailable:
        raised = True
    check("11_store_down_fail_closed", raised and calls["n"] == 0,
          f"raised={raised} fn_calls={calls['n']}")


def t12_retention():
    with factory() as conn, conn.cursor() as cur:
        cur.execute(
            f"UPDATE {LEDGER} SET expires_at = now() - interval '1h' "
            f"WHERE client_key = 'v-dup'")
        conn.commit()
    deleted = purge_expired_idempotency_records(factory, table=LEDGER)
    remain = row("v-crash")     # running+过期 → 必须保留(IN_DOUBT 裁决对象)
    gone = row("v-dup")         # 显式 TTL 过期 → 被清
    check("12_retention_rules",
          deleted >= 1 and gone is None and remain is not None,
          f"deleted={deleted} v-dup_gone={gone is None} "
          f"v-crash_kept={remain is not None}")


def t13_payload_conflict():
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        return {"p": 1}

    executor(owner="exec-p").execute(key("v-conflict"), {"p": 1}, fn)
    conflicted = False
    try:
        executor(owner="exec-p").execute(key("v-conflict"), {"p": 2}, fn)
    except IdempotencyConflict:
        conflicted = True
    check("13_payload_conflict_fail_closed",
          conflicted and calls["n"] == 1,
          f"conflict={conflicted} fn_calls={calls['n']}")


def t14_owner_cas():
    store = ledger_store(owner="exec-old", lease=1)
    res = store.claim(key("v-cas"), {"p": 1})
    time.sleep(1.3)
    store2 = ledger_store(
        owner="exec-new", takeover=lambda info: True)  # owner 确认死亡 → 接管
    res2 = store2.claim(key("v-cas"), {"p": 1})
    old_rejected = False
    try:
        store.complete(res.lease_id, {"stale": True}, key=key("v-cas"))
    except ValueError:
        old_rejected = True
    st = row("v-cas")
    check("14_owner_cas",
          res2.status == ClaimStatus.NEW and old_rejected
          and st[0] == "running" and st[3] == "exec-new",
          f"new_owner={res2.status.value} old_rejected={old_rejected} "
          f"owner_col={st[3]}")


def t15_real_entry_smoke():
    """真实入口 run_idempotent_side_effect 冒烟(生产表),跑完即清。"""
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        return {"smoke": True}

    try:
        r1 = run_idempotent_side_effect(
            "probe.effect", {"p": 1}, fn, tenant_id="t1", actor_id="u1",
            client_key="s6v-smoke", owner_execution_id="exec-smoke")
        r2 = run_idempotent_side_effect(
            "probe.effect", {"p": 1}, fn, tenant_id="t1", actor_id="u1",
            client_key="s6v-smoke", owner_execution_id="exec-smoke2")
        check("15_real_entry_smoke",
              calls["n"] == 1 and r1 == r2 == {"smoke": True},
              f"fn_calls={calls['n']} same_result={r1 == r2}")
    finally:
        with factory() as conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM ai.idempotency_records WHERE "
                "operation='probe.effect' AND client_key='s6v-smoke'")
            conn.commit()


def main():
    print("=== S6 独立第二路径验证 v2(容器内直连 PG,HEAD 代码)===", flush=True)
    cleanup()
    setup()
    t1_first_claim()
    t2_replay_after_success()
    t3_concurrent_claim()
    t4_failed_takeover_retry()
    t5_crash_window_blocked()
    t6_resolve_executed_replay()
    t7_resolve_not_executed_retry()
    t8_owner_rotation_same_key()
    t9_tenant_isolation()
    t10_atomic_transaction()
    t11_fail_closed()
    t12_retention()
    t13_payload_conflict()
    t14_owner_cas()
    t15_real_entry_smoke()
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"=== SUMMARIZED: {passed}/{len(RESULTS)} PASS ===", flush=True)
    cleanup()
    sys.exit(0 if passed == len(RESULTS) else 1)


if __name__ == "__main__":
    main()
