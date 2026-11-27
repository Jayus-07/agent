"""backend/evaluation_v2/repositories/gates.py — 评测准入门禁决策与审批流程"""
from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from contextlib import contextmanager
from typing import Any, Iterator

from psycopg2.extras import RealDictCursor

from backend.shared.logger import logger
from backend.evaluation_v2.domain_contracts import (
    validate_dataset_domain,
    validate_non_rag_case,
)

from backend.evaluation_v2.repositories.base import (
    RepositoryConflictError,
    RepositoryNotFoundError,
    RepositoryExportLimitError,
    _TENANT_RE,
    _VERSION_RE,
    canonical_json,
    content_hash,
    normalize_human_rubric,
    _tenant,
    _transaction,
    _record_audit,
    _dict,
    _valid_evidence,
)

def record_gate_decision(
    tenant_id: str,
    actor: str,
    run_id: str,
    *,
    policy_hash: str,
    verdict: str,
    reasons: list[dict[str, Any]],
) -> dict[str, Any]:
    tenant_id = _tenant(tenant_id)
    if verdict not in {"PASS", "FAIL", "INVALID"}:
        raise ValueError("Gate 裁决无效")
    if not re.fullmatch(r"[a-f0-9]{64}", policy_hash):
        raise ValueError("Gate 策略摘要无效")
    with _transaction(tenant_id) as (_conn, cur):
        cur.execute(
            "SELECT job_status FROM evaluation_v2.eval_runs WHERE tenant_id = %s AND run_id = %s FOR UPDATE",
            (tenant_id, run_id),
        )
        run = _dict(cur.fetchone())
        if not run:
            raise RepositoryNotFoundError("评测运行不存在")
        if run["job_status"] not in {"completed", "failed", "cancelled"}:
            raise RepositoryConflictError("运行未终态，暂不能生成 Gate 裁决")
        cur.execute(
            """
            INSERT INTO evaluation_v2.eval_gate_decisions
                (tenant_id, run_id, policy_hash, verdict, reasons_json, decided_by)
            VALUES (%s, %s, %s, %s, %s::jsonb, %s)
            RETURNING decision_id, run_id, policy_hash, verdict, reasons_json,
                      decided_by, decided_at
            """,
            (tenant_id, run_id, policy_hash, verdict, canonical_json(reasons), actor),
        )
        decision = _dict(cur.fetchone())
        cur.execute(
            """
            UPDATE evaluation_v2.eval_runs
            SET gate_verdict = %s, gate_reason = %s::jsonb
            WHERE tenant_id = %s AND run_id = %s
            """,
            (verdict, canonical_json(reasons), tenant_id, run_id),
        )
        _record_audit(
            cur, tenant_id, actor, "run.gate_decided", "run", str(run_id),
            after_hash=policy_hash, details={"verdict": verdict, "reason_count": len(reasons)},
        )
        return decision


def terminate_run_with_gate(
    tenant_id: str,
    actor: str,
    run_id: str,
    *,
    status: str,
    invalid_reason: str,
    policy_hash: str,
    verdict: str,
    reasons: list[dict[str, Any]],
) -> dict[str, Any]:
    """在一个事务中写入失败/取消终态、Gate 裁决和审计。"""
    tenant_id = _tenant(tenant_id)
    if status not in {"failed", "cancelled"}:
        raise ValueError("此事务仅用于失败或取消终态")
    if verdict not in {"PASS", "FAIL", "INVALID"}:
        raise ValueError("Gate 裁决无效")
    if not re.fullmatch(r"[a-f0-9]{64}", policy_hash):
        raise ValueError("Gate 策略摘要无效")

    with _transaction(tenant_id) as (_conn, cur):
        cur.execute(
            """
            SELECT job_status
            FROM evaluation_v2.eval_runs
            WHERE tenant_id = %s AND run_id = %s
            FOR UPDATE
            """,
            (tenant_id, run_id),
        )
        run = _dict(cur.fetchone())
        if not run:
            raise RepositoryNotFoundError("评测运行不存在")
        current_status = run["job_status"]
        transitions = {
            "queued": {"failed", "cancelled"},
            "preparing": {"failed", "cancelled"},
            "running": {"failed", "cancelled"},
            "aggregating": {"failed", "cancelled"},
            "failed": {"failed"},
            "cancelled": {"cancelled"},
        }
        if status not in transitions.get(current_status, set()):
            raise RepositoryConflictError(
                f"运行状态不可从 {current_status} 转换为 {status}"
            )

        if current_status == status:
            cur.execute(
                """
                SELECT decision_id, run_id, policy_hash, verdict, reasons_json,
                       decided_by, decided_at
                FROM evaluation_v2.eval_gate_decisions
                WHERE tenant_id = %s AND run_id = %s
                ORDER BY decided_at DESC, decision_id DESC
                LIMIT 1
                """,
                (tenant_id, run_id),
            )
            existing = _dict(cur.fetchone())
            if existing and (
                str(existing["policy_hash"]).strip() == policy_hash
                and existing["verdict"] == verdict
                and content_hash(existing["reasons_json"]) == content_hash(reasons)
            ):
                return existing

        cur.execute(
            """
            UPDATE evaluation_v2.eval_runs AS r
            SET job_status = %s,
                gate_verdict = %s,
                gate_reason = %s::jsonb,
                invalid_reason = %s,
                finished_at = now(),
                completed_cases = (
                    SELECT COUNT(*) FROM evaluation_v2.eval_case_results x
                    WHERE x.tenant_id = r.tenant_id AND x.run_id = r.run_id
                ),
                passed_cases = (
                    SELECT COUNT(*) FROM evaluation_v2.eval_case_results x
                    WHERE x.tenant_id = r.tenant_id AND x.run_id = r.run_id
                      AND x.result_status = 'pass'
                )
            WHERE r.tenant_id = %s AND r.run_id = %s
            """,
            (status, verdict, canonical_json(reasons), invalid_reason, tenant_id, run_id),
        )
        cur.execute(
            """
            INSERT INTO evaluation_v2.eval_gate_decisions
                (tenant_id, run_id, policy_hash, verdict, reasons_json, decided_by)
            VALUES (%s, %s, %s, %s, %s::jsonb, %s)
            RETURNING decision_id, run_id, policy_hash, verdict, reasons_json,
                      decided_by, decided_at
            """,
            (tenant_id, run_id, policy_hash, verdict, canonical_json(reasons), actor),
        )
        decision = _dict(cur.fetchone())
        if current_status != status:
            _record_audit(
                cur,
                tenant_id,
                actor,
                "run.status_changed",
                "run",
                str(run_id),
                details={"from": current_status, "to": status, "invalid_reason": invalid_reason},
            )
        _record_audit(
            cur,
            tenant_id,
            actor,
            "run.gate_decided",
            "run",
            str(run_id),
            after_hash=policy_hash,
            details={"verdict": verdict, "reason_count": len(reasons)},
        )
        return decision


def complete_run_with_gate(
    tenant_id: str,
    actor: str,
    run_id: str,
    *,
    policy_hash: str,
    verdict: str,
    reasons: list[dict[str, Any]],
) -> dict[str, Any]:
    """同一事务中写入最终 Gate 并完成 Run，支持安全的幂等重放。"""
    tenant_id = _tenant(tenant_id)
    if verdict not in {"PASS", "FAIL", "INVALID"}:
        raise ValueError("Gate 裁决无效")
    if not re.fullmatch(r"[a-f0-9]{64}", policy_hash):
        raise ValueError("Gate 策略摘要无效")

    with _transaction(tenant_id) as (_conn, cur):
        cur.execute(
            """
            SELECT job_status, total_cases
            FROM evaluation_v2.eval_runs
            WHERE tenant_id = %s AND run_id = %s
            FOR UPDATE
            """,
            (tenant_id, run_id),
        )
        run = _dict(cur.fetchone())
        if not run:
            raise RepositoryNotFoundError("评测运行不存在")
        current_status = run["job_status"]
        if current_status not in {"aggregating", "completed"}:
            raise RepositoryConflictError("运行尚未汇总完成，不能生成最终 Gate")

        cur.execute(
            """
            SELECT COUNT(*) AS completed,
                   COUNT(*) FILTER (WHERE result_status = 'pass') AS passed
            FROM evaluation_v2.eval_case_results
            WHERE tenant_id = %s AND run_id = %s
            """,
            (tenant_id, run_id),
        )
        counts = _dict(cur.fetchone())
        completed = int(counts["completed"])
        if completed != int(run["total_cases"]):
            raise RepositoryConflictError("逐例结果未完整持久化，运行不能完成")

        if current_status == "completed":
            cur.execute(
                """
                SELECT decision_id, policy_hash, verdict, reasons_json,
                       decided_by, decided_at
                FROM evaluation_v2.eval_gate_decisions
                WHERE tenant_id = %s AND run_id = %s
                ORDER BY decided_at DESC, decision_id DESC
                LIMIT 1
                """,
                (tenant_id, run_id),
            )
            existing = _dict(cur.fetchone())
            if existing:
                if (
                    str(existing["policy_hash"]).strip() != policy_hash
                    or existing["verdict"] != verdict
                    or content_hash(existing["reasons_json"]) != content_hash(reasons)
                ):
                    raise RepositoryConflictError("已完成运行已有不同的 Gate 裁决")
                return existing

        cur.execute(
            """
            INSERT INTO evaluation_v2.eval_gate_decisions
                (tenant_id, run_id, policy_hash, verdict, reasons_json, decided_by)
            VALUES (%s, %s, %s, %s, %s::jsonb, %s)
            RETURNING decision_id, run_id, policy_hash, verdict, reasons_json,
                      decided_by, decided_at
            """,
            (tenant_id, run_id, policy_hash, verdict, canonical_json(reasons), actor),
        )
        decision = _dict(cur.fetchone())
        cur.execute(
            """
            UPDATE evaluation_v2.eval_runs AS r
            SET job_status = 'completed',
                gate_verdict = %s,
                gate_reason = %s::jsonb,
                finished_at = CASE WHEN r.job_status = 'completed' THEN r.finished_at ELSE now() END,
                completed_cases = %s,
                passed_cases = %s
            WHERE r.tenant_id = %s AND r.run_id = %s
            """,
            (
                verdict,
                canonical_json(reasons),
                completed,
                int(counts["passed"]),
                tenant_id,
                run_id,
            ),
        )
        if current_status != "completed":
            _record_audit(
                cur,
                tenant_id,
                actor,
                "run.status_changed",
                "run",
                str(run_id),
                details={"from": current_status, "to": "completed", "invalid_reason": ""},
            )
        _record_audit(
            cur,
            tenant_id,
            actor,
            "run.gate_decided",
            "run",
            str(run_id),
            after_hash=policy_hash,
            details={"verdict": verdict, "reason_count": len(reasons)},
        )
        return decision


def list_gate_decisions(tenant_id: str, run_id: str) -> list[dict[str, Any]]:
    tenant_id = _tenant(tenant_id)
    with _transaction(tenant_id) as (_conn, cur):
        cur.execute(
            "SELECT 1 FROM evaluation_v2.eval_runs WHERE tenant_id = %s AND run_id = %s",
            (tenant_id, run_id),
        )
        if cur.fetchone() is None:
            raise RepositoryNotFoundError("评测运行不存在")
        cur.execute(
            """
            SELECT decision_id, run_id, policy_hash, verdict, reasons_json,
                   decided_by, decided_at
            FROM evaluation_v2.eval_gate_decisions
            WHERE tenant_id = %s AND run_id = %s
            ORDER BY decided_at DESC, decision_id DESC
            """,
            (tenant_id, run_id),
        )
        return [_dict(row) for row in cur.fetchall()]


def record_gate_approval(
    tenant_id: str,
    actor: str,
    run_id: str,
    *,
    gate_decision_id: str,
    request_id: str,
    decision: str,
    release_reference: str,
    rationale: str,
) -> dict[str, Any]:
    """为最新 PASS Gate 追加人工放行/拒绝意见，支持请求幂等重放。"""
    tenant_id = _tenant(tenant_id)
    if decision not in {"approved", "rejected"}:
        raise ValueError("发布审批结论无效")
    release_reference = release_reference.strip()
    rationale = rationale.strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/@+#-]{2,159}", release_reference):
        raise ValueError("发布引用格式无效")
    if not 8 <= len(rationale) <= 2000:
        raise ValueError("审批理由长度必须为 8 到 2000 个字符")
    request_hash = content_hash({
        "run_id": run_id,
        "gate_decision_id": gate_decision_id,
        "decision": decision,
        "release_reference": release_reference,
        "rationale": rationale,
    })
    with _transaction(tenant_id) as (_conn, cur):
        cur.execute(
            """
            SELECT job_status, gate_verdict
            FROM evaluation_v2.eval_runs
            WHERE tenant_id = %s AND run_id = %s
            FOR UPDATE
            """,
            (tenant_id, run_id),
        )
        run = _dict(cur.fetchone())
        if not run:
            raise RepositoryNotFoundError("评测运行不存在")

        cur.execute(
            """
            SELECT approval_id, run_id, gate_decision_id, request_id,
                   request_hash, decision, release_reference, rationale,
                   decided_by, decided_at
            FROM evaluation_v2.eval_gate_approvals
            WHERE tenant_id = %s AND request_id = %s
            """,
            (tenant_id, request_id),
        )
        existing = _dict(cur.fetchone())
        if existing:
            if existing["request_hash"] != request_hash:
                raise RepositoryConflictError("审批幂等键已用于不同请求")
            return existing

        if run["job_status"] != "completed" or run["gate_verdict"] != "PASS":
            raise RepositoryConflictError("仅已完成且 Gate 为 PASS 的运行可以进入发布审批")
        cur.execute(
            """
            SELECT decision_id, verdict
            FROM evaluation_v2.eval_gate_decisions
            WHERE tenant_id = %s AND run_id = %s
            ORDER BY decided_at DESC, decision_id DESC
            LIMIT 1
            """,
            (tenant_id, run_id),
        )
        latest = _dict(cur.fetchone())
        if not latest or str(latest["decision_id"]) != gate_decision_id:
            raise RepositoryConflictError("Gate 裁决已变化，请刷新后重新审批")
        if latest["verdict"] != "PASS":
            raise RepositoryConflictError("最新 Gate 裁决不是 PASS，不能进行发布审批")

        cur.execute(
            """
            INSERT INTO evaluation_v2.eval_gate_approvals
                (tenant_id, run_id, gate_decision_id, request_id, request_hash,
                 decision, release_reference, rationale, decided_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING approval_id, run_id, gate_decision_id, request_id,
                      decision, release_reference, rationale, decided_by, decided_at
            """,
            (
                tenant_id, run_id, gate_decision_id, request_id, request_hash,
                decision, release_reference, rationale, actor,
            ),
        )
        approval = _dict(cur.fetchone())
        _record_audit(
            cur, tenant_id, actor, f"run.release_{decision}", "run", run_id,
            after_hash=request_hash,
            details={
                "gate_decision_id": gate_decision_id,
                "approval_id": str(approval["approval_id"]),
                "release_reference": release_reference,
            },
        )
        return approval


def list_gate_approvals(tenant_id: str, run_id: str) -> list[dict[str, Any]]:
    tenant_id = _tenant(tenant_id)
    with _transaction(tenant_id) as (_conn, cur):
        cur.execute(
            "SELECT 1 FROM evaluation_v2.eval_runs WHERE tenant_id = %s AND run_id = %s",
            (tenant_id, run_id),
        )
        if cur.fetchone() is None:
            raise RepositoryNotFoundError("评测运行不存在")
        cur.execute(
            """
            SELECT approval_id, run_id, gate_decision_id, decision,
                   release_reference, rationale, decided_by, decided_at
            FROM evaluation_v2.eval_gate_approvals
            WHERE tenant_id = %s AND run_id = %s
            ORDER BY decided_at DESC, approval_id DESC
            """,
            (tenant_id, run_id),
        )
        return [_dict(row) for row in cur.fetchall()]


