"""customer_service/reconciliation.py — verifying 确认行人工裁决收敛（Phase3 STOP E）

STOP D 落库的 verifying 行（副作用结果未知，§15/§59）持续占住 051 业务
实体唯一守卫；本模块是该守卫的唯一合法释放出口（运维 CLI
scripts/idempotency_ops.py 的 resolve-confirmation 子命令调用）：

  decision='executed'      → CONFIRMED_SUCCESS：账本 UNCERTAIN→SUCCEEDED +
                             确认行 verifying→success（同事务原子）
  decision='not_executed'  → CONFIRMED_NOT_EXECUTED：账本→RESOLVED_NOT_EXECUTED
                             （FAILED 可接管重试语义恢复）+ 确认行 verifying→failed
  decision='unresolved'    → UNRESOLVED：状态一律不动（守卫继续占住），
                             仅落审计——「不知道」绝不演变成「知道了」

安全语义（全部来自冻结契约，本模块不发明新语义）：
  - 账本无未决记录（双形态 CAS 都 no-op）→ 拒绝收敛。确认行卡
    verifying 但账本已不在未决态属异常（外部改库/历史残留），不猜，
    人工查明后再处置。
  - executing 卡死行只在账本 stale-running（进程崩溃窗）时可裁决；
    活跃执行（租约未过期）的账本 CAS 必然 no-op → 自动拒绝。
  - 并发/重复裁决：FOR UPDATE 行锁 + 状态条件 UPDATE，只有一方生效，
    第二方拿到 resolved=False（幂等可重入）。
  - 审计：audit_logs 落 actor_type='human' + operator + before/after
    全量快照，裁决链可追溯（log_id 幂等）。
"""
from __future__ import annotations

import uuid
from typing import Any

from backend.shared.idempotency import resolve_side_effect

# 与 confirmation_flow._execute_confirmed_action 的账本键同源（唯一口径）
_LEDGER_OPERATION = "cs.action.execute"

# 可裁决的卡死态：verifying（执行完但结果未知）+ executing（崩溃窗残留；
# confirming/executing 占守卫口径见 confirmation_store._GUARD_ACTIVE_STATES）
_RESOLVABLE_STATES = ("verifying", "executing")


def _default_connection():
    import psycopg
    from backend.config.database import MEMORY_DB_CONFIG

    config = MEMORY_DB_CONFIG
    dsn = (
        f"postgresql://{config['user']}:{config['password']}"
        f"@{config['host']}:{config['port']}/{config['dbname']}"
    )
    return psycopg.connect(dsn)


def _insert_adjudication_audit(
    cur: Any,
    *,
    audit_table: str,
    user_id: str,
    conversation_id: str,
    confirmation_id: str,
    action_type: str,
    operator: str,
    before_state: str,
    after_state: dict,
    result: str,
    error_detail: str,
) -> None:
    from psycopg.types.json import Json

    cur.execute(
        f"""
        INSERT INTO {audit_table}
            (log_id, user_id, conversation_id, action_id, actor_type,
             actor_id, action, resource_type, resource_id,
             before_state, after_state, result, error_detail)
        VALUES (%s, %s, %s, %s, 'human', %s, 'cs.action.reconcile',
                'confirmation', %s, %s, %s, %s, %s)
        """,
        (
            str(uuid.uuid4()), user_id, conversation_id, confirmation_id,
            operator or "unknown_operator", confirmation_id,
            Json({"state": before_state}), Json(after_state), result,
            error_detail,
        ),
    )
    # audit_logs 无 action_type 独立列：动作语义并入 after_state 快照
    # （由调用方在 after_state dict 内携带 action_type 字段）。


def resolve_verifying_confirmation(
    *,
    confirmation_id: str,
    decision: str,
    reason: str,
    operator: str = "",
    result: dict | None = None,
    connection_factory=None,
    confirmations_table: str = "customer_service.confirmations",
    audit_table: str = "customer_service.audit_logs",
    ledger_table: str = "ai.idempotency_records",
) -> dict:
    """人工裁决一张 verifying/executing 卡死确认行，账本+确认行+审计同事务收敛。

    返回 {"resolved": bool, "state": str, "detail": str}；
    resolved=True 时 state 为收敛后的确认行终态。
    表名参数供测试注入隔离 schema，生产走默认值。
    """
    if decision not in ("executed", "not_executed", "unresolved"):
        raise ValueError("decision 必须是 executed / not_executed / unresolved")
    if not reason:
        raise ValueError("裁决依据 reason 必填（须含 operator 标识与依据）")
    if decision == "executed" and not operator:
        raise ValueError(
            "executed 裁决必须提供 operator（把外部副作用说成已发生，"
            "必须留下可追溯的人工身份）")

    factory = connection_factory or _default_connection
    with factory() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT tenant_id, user_id, conversation_id, action_type,
                       target_type, target_id, state
                FROM {confirmations_table}
                WHERE confirmation_id = %s
                FOR UPDATE
                """,
                (confirmation_id,),
            )
            row = cur.fetchone()
            if row is None:
                conn.rollback()
                return {"resolved": False, "state": "",
                        "detail": "not_found"}
            (tenant_id, user_id, conversation_id, action_type,
             target_type, target_id, state) = row

            if state not in _RESOLVABLE_STATES:
                conn.rollback()
                return {
                    "resolved": False, "state": state,
                    "detail": f"state={state} 不可裁决"
                              f"（仅 {'/'.join(_RESOLVABLE_STATES)} 卡死行）",
                }
            if not tenant_id or not user_id:
                conn.rollback()
                return {
                    "resolved": False, "state": state,
                    "detail": "缺 tenant/user 身份（守卫占位行），不可裁决",
                }

            if decision == "unresolved":
                _insert_adjudication_audit(
                    cur,
                    audit_table=audit_table,
                    user_id=user_id, conversation_id=conversation_id,
                    confirmation_id=confirmation_id,
                    action_type=action_type, operator=operator,
                    before_state=state,
                    after_state={"decision": "unresolved",
                                 "action_type": action_type,
                                 "target_type": target_type,
                                 "target_id": target_id},
                    result="denied", error_detail=reason,
                )
                conn.commit()
                return {"resolved": False, "state": state,
                        "detail": "kept_unresolved（守卫保持阻断）"}

            # 账本双形态 CAS（同事务）：verifying 行对应的未决副作用记录
            ledger_flags = resolve_side_effect(
                conn=conn,
                tenant_id=tenant_id, actor_id=user_id,
                operation=_LEDGER_OPERATION,
                client_key=f"cs_action:{confirmation_id}",
                decision=decision, result=result, reason=reason,
                table=ledger_table,
            )
            if not any(ledger_flags.values()):
                conn.rollback()
                return {
                    "resolved": False, "state": state,
                    "detail": ("ledger 无未决记录（uncertain/stale_running "
                               "均未命中）——不猜，先查明账本去向"),
                }

            target_state = "success" if decision == "executed" else "failed"
            cur.execute(
                f"""
                UPDATE {confirmations_table}
                SET state = %s
                WHERE confirmation_id = %s AND state = %s
                """,
                (target_state, confirmation_id, state),
            )
            if cur.rowcount != 1:
                conn.rollback()
                return {"resolved": False, "state": state,
                        "detail": "confirmation CAS 落空（并发变更）"}

            _insert_adjudication_audit(
                cur,
                audit_table=audit_table,
                user_id=user_id, conversation_id=conversation_id,
                confirmation_id=confirmation_id,
                action_type=action_type, operator=operator,
                before_state=state,
                after_state={
                    "decision": decision,
                    "action_type": action_type,
                    "target_type": target_type,
                    "target_id": target_id,
                    "ledger_forms_resolved": ledger_flags,
                },
                result="success",
                error_detail=reason,
            )
            conn.commit()
    return {"resolved": True, "state": target_state,
            "detail": f"ledger={ledger_flags}"}


def list_verifying_confirmations(
    *, limit: int = 50, connection_factory=None,
    confirmations_table: str = "customer_service.confirmations",
    ledger_table: str = "ai.idempotency_records",
) -> list[dict]:
    """运维视图：全部 verifying/executing 卡死确认行 + 关联账本未决态。"""
    factory = connection_factory or _default_connection
    with factory() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT c.confirmation_id, c.tenant_id, c.user_id,
                   c.conversation_id, c.action_type, c.target_type,
                   c.target_id, c.state, c.created_at, c.confirmed_at,
                   c.executed_at,
                   c.proposal ->> 'proposal_text' AS proposal_text,
                   EXISTS (
                       SELECT 1 FROM {ledger_table} r
                       WHERE r.tenant_id = c.tenant_id
                         AND r.actor_id = c.user_id
                         AND r.operation = 'cs.action.execute'
                         AND r.client_key = 'cs_action:' || c.confirmation_id
                         AND (r.status = 'running'
                              OR (r.status = 'failed'
                                  AND r.error_code = 'IDEMPOTENCY_UNCERTAIN'))
                   ) AS ledger_pending
            FROM {confirmations_table} c
            WHERE c.state IN ('verifying', 'executing')
            ORDER BY c.executed_at NULLS LAST, c.id DESC
            LIMIT %s
            """,
            (limit,),
        )
        cols = [
            "confirmation_id", "tenant_id", "user_id", "conversation_id",
            "action_type", "target_type", "target_id", "state",
            "created_at", "confirmed_at", "executed_at", "proposal_text",
            "ledger_pending",
        ]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    return rows


def inspect_verifying_confirmation(
    *, confirmation_id: str, connection_factory=None,
    confirmations_table: str = "customer_service.confirmations",
    ledger_table: str = "ai.idempotency_records",
) -> dict | None:
    """运维视图：单条卡死确认行的完整关联链（确认行+账本+守卫语义）。"""
    factory = connection_factory or _default_connection
    with factory() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT confirmation_id, tenant_id, user_id, conversation_id,
                   action_type, target_type, target_id, semantic_fingerprint,
                   state, proposal, created_at, expires_at, confirmed_at,
                   executed_at
            FROM {confirmations_table}
            WHERE confirmation_id = %s
            """,
            (confirmation_id,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        cols = [
            "confirmation_id", "tenant_id", "user_id", "conversation_id",
            "action_type", "target_type", "target_id",
            "semantic_fingerprint", "state", "proposal", "created_at",
            "expires_at", "confirmed_at", "executed_at",
        ]
        record = dict(zip(cols, row))

        cur.execute(
            f"""
            SELECT tenant_id, actor_id, operation, client_key, status,
                   attempt, error_code, result IS NOT NULL AS has_result,
                   lease_expires_at, owner_execution_id, created_at,
                   updated_at
            FROM {ledger_table}
            WHERE tenant_id = %s AND actor_id = %s
              AND operation = 'cs.action.execute'
              AND client_key = %s
            """,
            (record["tenant_id"], record["user_id"],
             f"cs_action:{confirmation_id}"),
        )
        lcols = [
            "tenant_id", "actor_id", "operation", "client_key", "status",
            "attempt", "error_code", "has_result", "lease_expires_at",
            "owner_execution_id", "created_at", "updated_at",
        ]
        lrow = cur.fetchone()
        record["ledger"] = dict(zip(lcols, lrow)) if lrow else None
        record["guard_note"] = (
            "051 业务实体唯一守卫：verifying/executing 属 active 生命周期，"
            "同租户+同语义指纹的新操作在裁决前一律被拒。")
    return record
