"""Phase3 STOP E：verifying / IN_DOUBT 人工 reconcile 闭环测试矩阵。

被测对象：
  - shared/idempotency.resolve_uncertain_side_effect（failed+UNCERTAIN
    主形态裁决——G-E0：旧 stale-running 专用通道对该形态不生效）
  - shared/idempotency.resolve_side_effect（双形态统一分发 + conn 同事务）
  - customer_service/reconciliation.resolve_verifying_confirmation
    （账本+确认行+审计同事务收敛；verifying 守卫唯一合法出口）
  - 运维视图 list/inspect

隔离（与 Step6/STOP L 同纪律）：pgtest_stope schema 自建三表（confirmations /
audit_logs / idempotency_records 镜像生产 DDL），每用例 TRUNCATE，用后 DROP；
PG 不可达 → skip（不假绿）。

红线断言（F1.4）：false_success=0 / false_failure=0 /
duplicate_side_effect=0 / cross_tenant_access=0。
"""
from __future__ import annotations

import json
import threading

import pytest

from backend.shared.idempotency import (
    IdempotencyKey,
    IdempotencyExecutor,
    PostgresIdempotencyLedgerStore,
    SideEffectOutcomeUnknown,
    canonical_fingerprint,
    resolve_side_effect,
    resolve_uncertain_side_effect,
)

SCHEMA = "pgtest_stope"
CONF_TABLE = f"{SCHEMA}.confirmations"
AUDIT_TABLE = f"{SCHEMA}.audit_logs"
LEDGER_TABLE = f"{SCHEMA}.idempotency_records"

TENANT, USER = "stope-t1", "stope-u1"

_CONF_DDL = f"""
CREATE TABLE IF NOT EXISTS {CONF_TABLE} (
    id BIGSERIAL PRIMARY KEY,
    confirmation_id VARCHAR(64) UNIQUE NOT NULL,
    conversation_id VARCHAR(64) NOT NULL,
    user_id VARCHAR(64) NOT NULL,
    action_type VARCHAR(30) NOT NULL,
    target_type VARCHAR(30) NOT NULL,
    target_id VARCHAR(64) NOT NULL,
    tenant_id VARCHAR(64),
    semantic_fingerprint VARCHAR(64),
    proposal JSONB NOT NULL,
    state VARCHAR(20) NOT NULL DEFAULT 'pending',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,
    confirmed_at TIMESTAMPTZ,
    executed_at TIMESTAMPTZ
)
"""

_AUDIT_DDL = f"""
CREATE TABLE IF NOT EXISTS {AUDIT_TABLE} (
    id BIGSERIAL PRIMARY KEY,
    log_id VARCHAR(64) NOT NULL UNIQUE,
    user_id VARCHAR(64) NOT NULL,
    conversation_id VARCHAR(64),
    action_id VARCHAR(64),
    actor_type VARCHAR(20) NOT NULL,
    actor_id VARCHAR(64),
    action VARCHAR(50) NOT NULL,
    resource_type VARCHAR(30),
    resource_id VARCHAR(64),
    before_state JSONB,
    after_state JSONB,
    result VARCHAR(20) NOT NULL,
    error_detail TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

_LEDGER_DDL = f"""
CREATE TABLE IF NOT EXISTS {LEDGER_TABLE} (
    tenant_id varchar(64) NOT NULL,
    actor_id varchar(64) NOT NULL,
    operation varchar(64) NOT NULL,
    client_key varchar(128) NOT NULL,
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


def _factory():
    import psycopg
    from backend.config.database import MEMORY_DB_CONFIG

    c = MEMORY_DB_CONFIG
    return psycopg.connect(
        f"postgresql://{c['user']}:{c['password']}"
        f"@{c['host']}:{c['port']}/{c['dbname']}")


@pytest.fixture()
def stope_env():
    """建 schema+三表 + 每用例清空；PG 不可达 skip（不假绿）。"""
    pytest.importorskip("psycopg")
    try:
        with _factory() as conn, conn.cursor() as cur:
            cur.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
            cur.execute(_CONF_DDL)
            cur.execute(_AUDIT_DDL)
            cur.execute(_LEDGER_DDL)
            for table in (CONF_TABLE, AUDIT_TABLE, LEDGER_TABLE):
                cur.execute(f"TRUNCATE {table} RESTART IDENTITY CASCADE")
            conn.commit()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"PG 不可达，跳过 STOP E 实库测试: {exc}")
    yield
    with _factory() as conn, conn.cursor() as cur:
        cur.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        conn.commit()


def _seed_confirmation(
    confirmation_id: str, *, tenant: str = TENANT, user: str = USER,
    state: str = "verifying",
) -> None:
    with _factory() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            INSERT INTO {CONF_TABLE}
                (confirmation_id, conversation_id, user_id, action_type,
                 target_type, target_id, tenant_id, semantic_fingerprint,
                 proposal, state, expires_at)
            VALUES (%s, %s, %s, 'refund', 'order', 'ord-1', %s, %s,
                    %s, %s, now() + interval '1 hour')
            """,
            (confirmation_id, f"conv-{confirmation_id}", user, tenant,
             f"fp-{confirmation_id}",
             json.dumps({"proposal_text": "退款 100 元", "action_id":
                         confirmation_id}),
             state),
        )
        conn.commit()


def _ledger_key(confirmation_id: str, *, tenant: str = TENANT,
                user: str = USER) -> IdempotencyKey:
    return IdempotencyKey(
        tenant_id=tenant, actor_id=user, operation="cs.action.execute",
        client_key=f"cs_action:{confirmation_id}")


def _run_side_effect(key: IdempotencyKey, payload: dict, fn) -> dict:
    """与 confirmation_flow._execute_confirmed_action 同一账本装配。"""
    store = PostgresIdempotencyLedgerStore(_factory, table=LEDGER_TABLE)
    return IdempotencyExecutor(store).execute(key, payload, fn)


def _run_side_effect_unknown(
    key: IdempotencyKey, payload: dict, calls: list,
) -> None:
    """真实制造 failed+UNCERTAIN 主形态：执行后结果未知（STOP C 语义）。"""

    def _fn() -> dict:
        calls.append(1)
        raise SideEffectOutcomeUnknown("provider timeout, outcome unknown")

    with pytest.raises(SideEffectOutcomeUnknown):
        _run_side_effect(key, payload, _fn)


def _resolve(confirmation_id: str, decision: str, **kwargs) -> dict:
    from backend.customer_service.reconciliation import (
        resolve_verifying_confirmation,
    )

    return resolve_verifying_confirmation(
        confirmation_id=confirmation_id, decision=decision,
        reason=kwargs.pop("reason", "op:tester 实测裁决"),
        operator=kwargs.pop("operator", "tester"),
        connection_factory=_factory,
        confirmations_table=CONF_TABLE, audit_table=AUDIT_TABLE,
        ledger_table=LEDGER_TABLE, **kwargs)


def _confirmation_state(confirmation_id: str) -> str:
    with _factory() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT state FROM {CONF_TABLE} WHERE confirmation_id = %s",
            (confirmation_id,))
        return cur.fetchone()[0]


def _ledger_row(confirmation_id: str, *, tenant: str = TENANT,
                user: str = USER) -> tuple:
    with _factory() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT status, error_code, result FROM {LEDGER_TABLE} "
            "WHERE tenant_id=%s AND actor_id=%s AND operation=%s "
            "AND client_key=%s",
            (tenant, user, "cs.action.execute",
             f"cs_action:{confirmation_id}"))
        return cur.fetchone()


def _audit_rows(confirmation_id: str) -> list[tuple]:
    with _factory() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT actor_type, actor_id, action, resource_id, "
            "before_state, after_state, result, error_detail "
            f"FROM {AUDIT_TABLE} WHERE action_id = %s ORDER BY id",
            (confirmation_id,))
        return cur.fetchall()


# =============================================
# 账本层：failed+UNCERTAIN 主形态（G-E0）
# =============================================


def test_uncertain_form_adjudicated_executed_replays(stope_env):
    """主形态裁决 executed：SUCCEEDED+审计码，同 key 重放已知结果（绝不重执行）。"""
    calls: list = []
    key = _ledger_key("conf-e1")

    def _unknown_fn() -> dict:
        calls.append(1)  # 副作用已发生，但结果未知
        raise SideEffectOutcomeUnknown("provider timeout, outcome unknown")

    with pytest.raises(SideEffectOutcomeUnknown):
        _run_side_effect(key, {"amount": 100}, _unknown_fn)
    assert _ledger_row("conf-e1") == ("failed", "IDEMPOTENCY_UNCERTAIN", None)

    ok = resolve_uncertain_side_effect(
        tenant_id=TENANT, actor_id=USER, operation="cs.action.execute",
        client_key="cs_action:conf-e1", decision="executed",
        result={"refund_id": "r-1"}, reason="op:tester provider 后台已查实",
        connection_factory=_factory, table=LEDGER_TABLE)
    assert ok is True
    status, error_code, result = _ledger_row("conf-e1")
    assert (status, error_code) == ("succeeded", "MANUAL_RESOLVED_EXECUTED")
    assert result["refund_id"] == "r-1"

    # 同 key 再入 → 重放裁决结果，fn 不再执行（duplicate_side_effect=0）
    def _replay_fn() -> dict:  # 若被调用即为重复副作用
        calls.append(1)
        return {"should_not_happen": True}

    replay = _run_side_effect(key, {"amount": 100}, _replay_fn)
    assert replay == {"refund_id": "r-1"}
    assert len(calls) == 1  # 只有第一次 unknown 调用真正执行过副作用


def test_uncertain_form_adjudicated_not_executed_releases_retry(stope_env):
    """主形态裁决 not_executed：解除 UNCERTAIN 阻断，重试恰好执行一次。"""
    calls: list = []
    key = _ledger_key("conf-e2")

    def _unknown_fn() -> dict:
        raise SideEffectOutcomeUnknown("断连：未确认 provider 是否受理")

    with pytest.raises(SideEffectOutcomeUnknown):
        _run_side_effect(key, {"amount": 100}, _unknown_fn)

    ok = resolve_uncertain_side_effect(
        tenant_id=TENANT, actor_id=USER, operation="cs.action.execute",
        client_key="cs_action:conf-e2", decision="not_executed",
        reason="op:tester provider 无此单", connection_factory=_factory,
        table=LEDGER_TABLE)
    assert ok is True
    status, error_code, _ = _ledger_row("conf-e2")
    assert (status, error_code) == ("failed", "RESOLVED_NOT_EXECUTED")

    def _retry_fn() -> dict:
        calls.append(1)  # 重试的真实副作用
        return {"done": True}

    result = _run_side_effect(key, {"amount": 100}, _retry_fn)
    assert result == {"done": True}
    # 副作用恰好一次：unknown 那次未产生效果，重试产生唯一一次
    assert len(calls) == 1


def test_uncertain_form_repeat_adjudication_noop(stope_env):
    """重复裁决幂等：第二次 no-op 返回 False，终态不被二次改写。"""
    calls: list = []
    key = _ledger_key("conf-e3")
    _run_side_effect_unknown(key, {"amount": 100}, calls)
    kwargs = dict(tenant_id=TENANT, actor_id=USER,
                  operation="cs.action.execute",
                  client_key="cs_action:conf-e3",
                  connection_factory=_factory, table=LEDGER_TABLE)
    assert resolve_uncertain_side_effect(
        decision="not_executed", reason="op:tester first", **kwargs) is True
    assert resolve_uncertain_side_effect(
        decision="not_executed", reason="op:tester second", **kwargs) is False
    # executed 也不能再改已裁决行（false_success=0：不许事后翻成功）
    assert resolve_uncertain_side_effect(
        decision="executed", reason="op:tester flip", **kwargs) is False


def test_uncertain_form_invalid_decision_rejected(stope_env):
    kwargs = dict(tenant_id=TENANT, actor_id=USER,
                  operation="cs.action.execute",
                  client_key="cs_action:conf-e4",
                  connection_factory=_factory, table=LEDGER_TABLE)
    with pytest.raises(ValueError):
        resolve_uncertain_side_effect(decision="guessed", **kwargs)


def test_resolve_side_effect_dispatcher_both_forms(stope_env):
    """统一分发：uncertain 与 stale_running 双形态各自命中，互不误伤。"""
    calls: list = []
    key = _ledger_key("conf-e5")
    _run_side_effect_unknown(key, {"p": 1}, calls)  # failed+UNCERTAIN

    # 直接构造崩溃窗形态（running+租约已过期；hash 用真实指纹以便重试）
    payload = {"p": 2}
    with _factory() as conn, conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO {LEDGER_TABLE} (tenant_id, actor_id, operation, "
            "client_key, request_hash, status, lease_expires_at) "
            "VALUES (%s,%s,'cs.action.execute',%s,%s,'running', "
            "now() - interval '10 minutes')",
            (TENANT, USER, "cs_action:conf-e5-stale",
             canonical_fingerprint(payload)))
        conn.commit()

    flags = resolve_side_effect(
        tenant_id=TENANT, actor_id=USER, operation="cs.action.execute",
        client_key="cs_action:conf-e5", decision="executed",
        result={"x": 1}, reason="op:tester", connection_factory=_factory,
        table=LEDGER_TABLE)
    assert flags == {"uncertain": True, "stale_running": False}

    flags2 = resolve_side_effect(
        tenant_id=TENANT, actor_id=USER, operation="cs.action.execute",
        client_key="cs_action:conf-e5-stale", decision="not_executed",
        reason="op:tester", connection_factory=_factory, table=LEDGER_TABLE)
    assert flags2 == {"uncertain": False, "stale_running": True}

    # stale 行裁决后同 key 可重新认领执行（恢复闭环）
    result = _run_side_effect(
        _ledger_key("conf-e5-stale"), payload, lambda: {"retry": True})
    assert result == {"retry": True}


# =============================================
# 确认行收敛：账本+确认行+审计同事务
# =============================================


def test_verifying_executed_converges_all_three(stope_env):
    """executed：verifying→success + 账本→SUCCEEDED + 审计落库（同事务）。"""
    calls: list = []
    _seed_confirmation("conf-c1")
    _run_side_effect_unknown(_ledger_key("conf-c1"), {"amount": 1}, calls)

    outcome = _resolve("conf-c1", "executed",
                       result={"refund_id": "r-c1"},
                       reason="op:alice 银行流水证实已入账", operator="alice")
    assert outcome == {"resolved": True, "state": "success",
                       "detail": "ledger={'uncertain': True, 'stale_running': False}"}
    assert _confirmation_state("conf-c1") == "success"
    status, error_code, _ = _ledger_row("conf-c1")
    assert (status, error_code) == ("succeeded", "MANUAL_RESOLVED_EXECUTED")

    audits = _audit_rows("conf-c1")
    assert len(audits) == 1
    (actor_type, actor_id, action, resource_id, before_state,
     after_state, result, detail) = audits[0]
    assert actor_type == "human" and actor_id == "alice"
    assert action == "cs.action.reconcile" and resource_id == "conf-c1"
    assert before_state == {"state": "verifying"} and result == "success"
    assert after_state["decision"] == "executed"
    assert "银行流水" in detail


def test_verifying_not_executed_converges_and_retry_works(stope_env):
    """not_executed：verifying→failed + 账本解除阻断 + 重试真实生效一次。"""
    calls: list = []
    _seed_confirmation("conf-c2")
    _run_side_effect_unknown(_ledger_key("conf-c2"), {"amount": 2}, calls)

    outcome = _resolve("conf-c2", "not_executed",
                       reason="op:bob provider 后台无此退款单", operator="bob")
    assert outcome["resolved"] is True and outcome["state"] == "failed"
    assert _confirmation_state("conf-c2") == "failed"
    _, error_code, _ = _ledger_row("conf-c2")
    assert error_code == "RESOLVED_NOT_EXECUTED"

    # 业务重试（同 confirmation 语义）真实执行一次
    result = _run_side_effect(
        _ledger_key("conf-c2"), {"amount": 2}, lambda: {"retry": "ok"})
    assert result == {"retry": "ok"}


def test_unresolved_keeps_guard_blocked(stope_env):
    """unresolved：状态与账本一律不动（守卫保持），仅落审计。"""
    calls: list = []
    _seed_confirmation("conf-c3")
    _run_side_effect_unknown(_ledger_key("conf-c3"), {"amount": 3}, calls)

    outcome = _resolve("conf-c3", "unresolved",
                       reason="op:carol 证据不足，保持调查")
    assert outcome["resolved"] is False
    assert "kept_unresolved" in outcome["detail"]
    assert _confirmation_state("conf-c3") == "verifying"
    assert _ledger_row("conf-c3")[:2] == ("failed", "IDEMPOTENCY_UNCERTAIN")
    audits = _audit_rows("conf-c3")
    assert len(audits) == 1 and audits[0][6] == "denied"  # result 列


def test_double_adjudication_concurrent_single_winner(stope_env):
    """并发双裁决：FOR UPDATE+CAS 只有一方生效（duplicate=0）。"""
    calls: list = []
    _seed_confirmation("conf-c4")
    _run_side_effect_unknown(_ledger_key("conf-c4"), {"amount": 4}, calls)

    outcomes: list[dict] = []
    barrier = threading.Barrier(2)

    def _adjudicate():
        barrier.wait()
        outcomes.append(_resolve("conf-c4", "executed", operator="op-x"))

    threads = [threading.Thread(target=_adjudicate) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sum(1 for o in outcomes if o["resolved"]) == 1
    assert _confirmation_state("conf-c4") == "success"
    assert len(_audit_rows("conf-c4")) == 1


def test_repeat_adjudication_idempotent(stope_env):
    """重复裁决：已终态行不可再裁决（false_success/false_failure=0）。"""
    calls: list = []
    _seed_confirmation("conf-c5")
    _run_side_effect_unknown(_ledger_key("conf-c5"), {"amount": 5}, calls)
    assert _resolve("conf-c5", "executed", operator="op-a")["resolved"]
    again = _resolve("conf-c5", "executed", operator="op-b")
    assert again["resolved"] is False and again["state"] == "success"
    flip = _resolve("conf-c5", "not_executed", operator="op-c")
    assert flip["resolved"] is False


def test_cross_tenant_isolation(stope_env):
    """跨租户 IDOR：裁决只命中目标租户的账本与确认行。"""
    calls: list = []
    _seed_confirmation("conf-c6", tenant=TENANT, user=USER)
    _run_side_effect_unknown(_ledger_key("conf-c6"), {"amount": 6}, calls)
    _seed_confirmation("conf-c6-b", tenant="stope-t2", user="stope-u2")
    _run_side_effect_unknown(
        _ledger_key("conf-c6-b", tenant="stope-t2", user="stope-u2"),
        {"amount": 6}, calls)

    _resolve("conf-c6", "executed", operator="op-d")

    assert _confirmation_state("conf-c6") == "success"
    # 另一租户同语义操作完全不受影响
    assert _confirmation_state("conf-c6-b") == "verifying"
    t2_row = _ledger_row("conf-c6-b", tenant="stope-t2", user="stope-u2")
    assert t2_row[:2] == ("failed", "IDEMPOTENCY_UNCERTAIN")


def test_ledger_not_pending_refuses(stope_env):
    """账本无未决记录 → 拒绝收敛（不猜；false_success=0 防线）。"""
    _seed_confirmation("conf-c7")  # 只建确认行，不建账本行
    outcome = _resolve("conf-c7", "executed", operator="op-e")
    assert outcome["resolved"] is False
    assert "ledger 无未决记录" in outcome["detail"]
    assert _confirmation_state("conf-c7") == "verifying"


def test_non_resolvable_state_rejected(stope_env):
    """pending/success 等非卡死行不可裁决。"""
    _seed_confirmation("conf-c8", state="success")
    outcome = _resolve("conf-c8", "executed", operator="op-f")
    assert outcome["resolved"] is False and outcome["state"] == "success"


def test_executed_requires_operator_identity(stope_env):
    """executed 裁决必须留人工身份（把未知说成成功必须可追溯）。"""
    from backend.customer_service.reconciliation import (
        resolve_verifying_confirmation,
    )

    with pytest.raises(ValueError):
        resolve_verifying_confirmation(
            confirmation_id="conf-c9", decision="executed",
            reason="no operator", operator="",
            connection_factory=_factory,
            confirmations_table=CONF_TABLE, audit_table=AUDIT_TABLE,
            ledger_table=LEDGER_TABLE)


def test_stale_running_executing_row_converges(stope_env):
    """executing 崩溃窗行（账本 stale-running）可裁决；活跃执行被账本挡住。"""
    payload = {"amount": 7}
    _seed_confirmation("conf-c10", state="executing")
    with _factory() as conn, conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO {LEDGER_TABLE} (tenant_id, actor_id, operation, "
            "client_key, request_hash, status, lease_expires_at) "
            "VALUES (%s,%s,'cs.action.execute',%s,%s,'running', "
            "now() - interval '10 minutes')",
            (TENANT, USER, "cs_action:conf-c10",
             canonical_fingerprint(payload)))
        conn.commit()

    outcome = _resolve("conf-c10", "not_executed", operator="op-g")
    assert outcome["resolved"] is True and outcome["state"] == "failed"
    _, error_code, _ = _ledger_row("conf-c10")
    assert error_code == "RESOLVED_NOT_EXECUTED"
    # 崩溃窗裁决后重试闭环：真实执行恰好一次
    result = _run_side_effect(
        _ledger_key("conf-c10"), payload, lambda: {"retry": True})
    assert result == {"retry": True}


# =============================================
# 运维视图
# =============================================


def test_list_and_inspect_views(stope_env):
    calls: list = []
    _seed_confirmation("conf-v1")
    _run_side_effect_unknown(_ledger_key("conf-v1"), {"amount": 8}, calls)
    _seed_confirmation("conf-v2", state="executing")  # 无账本行

    from backend.customer_service.reconciliation import (
        inspect_verifying_confirmation,
        list_verifying_confirmations,
    )

    rows = list_verifying_confirmations(
        connection_factory=_factory,
        confirmations_table=CONF_TABLE, ledger_table=LEDGER_TABLE)
    by_id = {r["confirmation_id"]: r for r in rows}
    assert by_id["conf-v1"]["ledger_pending"] is True
    assert by_id["conf-v1"]["state"] == "verifying"
    assert by_id["conf-v2"]["ledger_pending"] is False

    record = inspect_verifying_confirmation(
        confirmation_id="conf-v1", connection_factory=_factory,
        confirmations_table=CONF_TABLE, ledger_table=LEDGER_TABLE)
    assert record["state"] == "verifying"
    assert record["ledger"]["status"] == "failed"
    assert record["ledger"]["error_code"] == "IDEMPOTENCY_UNCERTAIN"
    assert record["proposal"]["proposal_text"] == "退款 100 元"
    assert inspect_verifying_confirmation(
        confirmation_id="missing", connection_factory=_factory,
        confirmations_table=CONF_TABLE, ledger_table=LEDGER_TABLE) is None
