"""backend/evaluation_v2/repositories/candidates.py — 测试用例、候选集打标与人工评审"""
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

def _candidate_domain(cur: Any, tenant_id: str, payload: dict[str, Any]) -> str:
    source_ref = payload.get("source_ref") or {}
    dataset_key = str(source_ref.get("dataset_key", "")).strip()
    if not dataset_key:
        return "rag"
    cur.execute(
        "SELECT domain FROM evaluation_v2.eval_datasets WHERE tenant_id = %s AND dataset_key = %s AND status = 'active'",
        (tenant_id, dataset_key),
    )
    dataset = cur.fetchone()
    if not dataset:
        raise RepositoryNotFoundError("候选引用的数据集不存在或不可用")
    domain = validate_dataset_domain(str(dataset["domain"]))
    declared_domain = str(source_ref.get("module", "")).strip()
    if declared_domain and declared_domain != domain:
        raise ValueError("候选声明模块与所属数据集业务域不一致")
    return domain


def get_candidate_review_context(tenant_id: str, candidate_id: str) -> dict[str, Any]:
    """为审核页面解析租户内候选数据及其服务端数据集域。"""
    tenant_id = _tenant(tenant_id)
    with _transaction(tenant_id) as (_conn, cur):
        cur.execute(
            "SELECT payload_json FROM evaluation_v2.eval_candidates WHERE tenant_id = %s AND candidate_id = %s",
            (tenant_id, candidate_id),
        )
        row = _dict(cur.fetchone())
        if not row:
            raise RepositoryNotFoundError("评测候选不存在")
        payload = row["payload_json"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        return {"domain": _candidate_domain(cur, tenant_id, payload), "payload": payload}


def list_case_evidence(
    tenant_id: str, case_revisions: list[tuple[str, int]]
) -> list[dict[str, Any]]:
    tenant_id = _tenant(tenant_id)
    if not case_revisions or len(case_revisions) > 20_000:
        raise ValueError("发布时必须选择 1 到 20000 条已审核样本")
    try:
        case_ids = [str(uuid.UUID(case_id)) for case_id, _ in case_revisions]
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError("发布样本标识必须是 UUID") from exc
    revisions = [revision for _, revision in case_revisions]
    if any(revision < 1 for revision in revisions):
        raise ValueError("样本修订号必须大于 0")
    if len(set(case_ids)) != len(case_ids):
        raise ValueError("同一发布版本中不能重复选择样本")
    with _transaction(tenant_id) as (_conn, cur):
        cur.execute(
            """
            SELECT revision.case_id, revision.revision,
                   revision.expected_json, revision.evidence_json
            FROM evaluation_v2.eval_case_revisions AS revision
            JOIN unnest(%s::uuid[], %s::integer[]) AS requested(case_id, revision)
              ON revision.case_id = requested.case_id
             AND revision.revision = requested.revision
            WHERE revision.tenant_id = %s
            ORDER BY revision.case_id
            """,
            (case_ids, revisions, tenant_id),
        )
        rows = [_dict(row) for row in cur.fetchall()]
        if len(rows) != len(case_ids):
            raise RepositoryNotFoundError("所选样本或修订不存在")
        return rows


def create_candidate(
    tenant_id: str,
    actor: str,
    *,
    source_kind: str,
    payload: dict[str, Any],
    idempotency_key: str,
    privacy_status: str = "redacted",
) -> dict[str, Any]:
    tenant_id = _tenant(tenant_id)
    payload_text = canonical_json(payload)
    digest = content_hash(payload)
    with _transaction(tenant_id) as (_conn, cur):
        _candidate_domain(cur, tenant_id, payload)
        cur.execute(
            """
            INSERT INTO evaluation_v2.eval_candidates
                (tenant_id, source_kind, payload_json, privacy_status, review_status,
                 idempotency_key, created_by, updated_by)
            VALUES (%s, %s, %s::jsonb, %s,
                    CASE WHEN %s = 'redacted' THEN 'in_review' ELSE 'needs_evidence' END,
                    %s, %s, %s)
            ON CONFLICT (tenant_id, idempotency_key) DO NOTHING
            RETURNING candidate_id, tenant_id, source_kind, payload_json, privacy_status,
                      review_status, revision, duplicate_key, promoted_case_id,
                      created_by, updated_by, created_at, updated_at
            """,
            (
                tenant_id, source_kind, payload_text, privacy_status, privacy_status,
                idempotency_key, actor, actor,
            ),
        )
        row = cur.fetchone()
        if row is not None:
            result = _dict(row)
            _record_audit(
                cur, tenant_id, actor, "candidate.created", "candidate",
                str(result["candidate_id"]), after_hash=digest,
                details={"source_kind": source_kind},
            )
            return result
        cur.execute(
            """
            SELECT candidate_id, tenant_id, source_kind, payload_json, privacy_status,
                   review_status, revision, duplicate_key, promoted_case_id,
                   created_by, updated_by, created_at, updated_at
            FROM evaluation_v2.eval_candidates
            WHERE tenant_id = %s AND idempotency_key = %s
            """,
            (tenant_id, idempotency_key),
        )
        existing = _dict(cur.fetchone())
        if not existing or content_hash(existing["payload_json"]) != digest:
            raise RepositoryConflictError("幂等键已用于不同候选内容")
        return existing


def find_candidate_by_source_origin(tenant_id: str, origin: str) -> dict[str, Any] | None:
    """按 source_ref.origin 查既有候选（坏例回流幂等：同一持久化坏例只回流一次）。

    与 idempotency_key 幂等互补：key 防同请求重放，origin 查重防跨请求/跨运营者
    对同一条坏例重复 intake。仅取最新一条；无命中返回 None。
    """
    tenant_id = _tenant(tenant_id)
    if not origin.strip():
        return None
    with _transaction(tenant_id) as (_conn, cur):
        cur.execute(
            """
            SELECT candidate_id, tenant_id, source_kind, payload_json, privacy_status,
                   review_status, revision, duplicate_key, promoted_case_id,
                   created_by, updated_by, created_at, updated_at
            FROM evaluation_v2.eval_candidates
            WHERE tenant_id = %s
              AND payload_json->'source_ref'->>'origin' = %s
            ORDER BY created_at DESC, candidate_id
            LIMIT 1
            """,
            (tenant_id, origin),
        )
        return _dict(cur.fetchone()) or None


def get_case_latest_revision(tenant_id: str, case_id: str) -> dict[str, Any]:
    """读取当前租户下已审核 Case 的最新修订，供人工创建新修订候选。"""
    tenant_id = _tenant(tenant_id)
    try:
        normalized_case_id = str(uuid.UUID(case_id))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError("Case 标识必须是 UUID") from exc
    with _transaction(tenant_id) as (_conn, cur):
        cur.execute(
            """
            SELECT c.case_id, c.lifecycle_status, c.source_kind,
                   r.revision, r.input_json, r.expected_json, r.evidence_json,
                   r.metadata_json, r.source_ref, r.source_content_hash,
                   r.content_hash, r.verified_by, r.created_at
            FROM evaluation_v2.eval_cases AS c
            JOIN LATERAL (
                SELECT revision, input_json, expected_json, evidence_json,
                       metadata_json, source_ref, source_content_hash,
                       content_hash, verified_by, created_at
                FROM evaluation_v2.eval_case_revisions
                WHERE tenant_id = c.tenant_id AND case_id = c.case_id
                ORDER BY revision DESC
                LIMIT 1
            ) AS r ON true
            WHERE c.tenant_id = %s AND c.case_id = %s
              AND c.lifecycle_status IN ('approved', 'published')
            """,
            (tenant_id, normalized_case_id),
        )
        row = _dict(cur.fetchone())
        if not row:
            raise RepositoryNotFoundError("已审核 Case 不存在或不可见")
        return row


def create_case_revision_candidate(
    tenant_id: str,
    actor: str,
    case_id: str,
    *,
    query: str,
    observed_answer: str,
    idempotency_key: str,
) -> dict[str, Any]:
    """基于当前 Case 修订创建审核候选；实际追加 revision 由审核事务完成。"""
    tenant_id = _tenant(tenant_id)
    try:
        normalized_case_id = str(uuid.UUID(case_id))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError("Case 标识必须是 UUID") from exc
    normalized_query = " ".join(query.split())
    if not normalized_query:
        raise ValueError("评测问题不能为空")
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", idempotency_key):
        raise ValueError("幂等键格式无效")

    with _transaction(tenant_id) as (_conn, cur):
        cur.execute(
            """
            SELECT candidate_id, payload_json, review_status, revision,
                   promoted_case_id, revision_target_case_id, revision_base_number,
                   created_by, updated_by, created_at, updated_at
            FROM evaluation_v2.eval_candidates
            WHERE tenant_id = %s AND idempotency_key = %s
            FOR UPDATE
            """,
            (tenant_id, idempotency_key),
        )
        existing = _dict(cur.fetchone())
        if existing:
            payload = existing["payload_json"]
            if isinstance(payload, str):
                payload = json.loads(payload)
            if (
                str(existing.get("revision_target_case_id") or "") != normalized_case_id
                or payload.get("input", {}).get("query") != normalized_query
                or payload.get("observed_answer", "") != observed_answer
            ):
                raise RepositoryConflictError("幂等键已用于不同 Case 修订候选")
            return existing

        cur.execute(
            """
            SELECT c.lifecycle_status, c.source_kind,
                   r.revision, r.expected_json, r.metadata_json, r.source_ref,
                   r.source_content_hash
            FROM evaluation_v2.eval_cases AS c
            JOIN LATERAL (
                SELECT revision, expected_json, metadata_json, source_ref, source_content_hash
                FROM evaluation_v2.eval_case_revisions
                WHERE tenant_id = c.tenant_id AND case_id = c.case_id
                ORDER BY revision DESC
                LIMIT 1
            ) AS r ON true
            WHERE c.tenant_id = %s AND c.case_id = %s
              AND c.lifecycle_status IN ('approved', 'published')
            FOR UPDATE OF c
            """,
            (tenant_id, normalized_case_id),
        )
        current = _dict(cur.fetchone())
        if not current:
            raise RepositoryNotFoundError("已审核 Case 不存在或不可见")
        expected = current["expected_json"]
        metadata = current["metadata_json"]
        source_ref = current["source_ref"]
        for key, value in (("expected_json", expected), ("metadata_json", metadata), ("source_ref", source_ref)):
            if isinstance(value, str):
                current[key] = json.loads(value)
        base_expected = current["expected_json"]
        payload = {
            "input": {"query": normalized_query},
            "observed_answer": observed_answer,
            "proposed_facts": [base_expected],
            # 原 revision 不保存原文引文；新审核必须重新提交 quote 并服务端核验。
            "proposed_evidence": [],
            "metadata": current["metadata_json"],
            "source_ref": current["source_ref"],
            "source_content_hash": str(current["source_content_hash"] or ""),
        }
        cur.execute(
            """
            INSERT INTO evaluation_v2.eval_candidates
                (tenant_id, source_kind, payload_json, privacy_status, review_status,
                 idempotency_key, revision_target_case_id, revision_base_number,
                 created_by, updated_by)
            VALUES (%s, %s, %s::jsonb, 'redacted', 'in_review', %s, %s, %s, %s, %s)
            RETURNING candidate_id, tenant_id, source_kind, payload_json, privacy_status,
                      review_status, revision, duplicate_key, promoted_case_id,
                      revision_target_case_id, revision_base_number,
                      created_by, updated_by, created_at, updated_at
            """,
            (
                tenant_id, current["source_kind"], canonical_json(payload), idempotency_key,
                normalized_case_id, current["revision"], actor, actor,
            ),
        )
        result = _dict(cur.fetchone())
        _record_audit(
            cur, tenant_id, actor, "candidate.revision_created", "candidate",
            str(result["candidate_id"]), after_hash=content_hash(payload),
            details={"target_case_id": normalized_case_id, "base_revision": current["revision"]},
        )
        return result


def archive_case(tenant_id: str, actor: str, case_id: str) -> dict[str, Any]:
    """软归档评测 Case；已进入已发布数据集版本的 Case 拒绝归档以保护锁版引用。

    幂等语义：重复归档返回 200 与当前状态（archived=False 表示本次未重复写审计），
    与既有审核/门禁重放口径一致；仅归档动作本身产生 case.archived 审计事件。
    """
    tenant_id = _tenant(tenant_id)
    try:
        normalized_case_id = str(uuid.UUID(case_id))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError("Case 标识必须是 UUID") from exc

    with _transaction(tenant_id) as (_conn, cur):
        cur.execute(
            """
            SELECT c.case_id, c.lifecycle_status,
                   r.revision, r.content_hash
            FROM evaluation_v2.eval_cases AS c
            JOIN LATERAL (
                SELECT revision, content_hash
                FROM evaluation_v2.eval_case_revisions
                WHERE tenant_id = c.tenant_id AND case_id = c.case_id
                ORDER BY revision DESC
                LIMIT 1
            ) AS r ON true
            WHERE c.tenant_id = %s AND c.case_id = %s
            FOR UPDATE OF c
            """,
            (tenant_id, normalized_case_id),
        )
        case = _dict(cur.fetchone())
        if not case:
            raise RepositoryNotFoundError("Case 不存在或已不可见")
        if case["lifecycle_status"] == "archived":
            return {
                "case_id": normalized_case_id,
                "lifecycle_status": "archived",
                "latest_revision": case["revision"],
                "archived": False,
            }
        cur.execute(
            """
            SELECT 1
            FROM evaluation_v2.eval_dataset_items AS i
            JOIN evaluation_v2.eval_dataset_versions AS v
              ON v.tenant_id = i.tenant_id
             AND v.dataset_version_id = i.dataset_version_id
            WHERE i.tenant_id = %s AND i.case_id = %s AND v.status = 'published'
            LIMIT 1
            """,
            (tenant_id, normalized_case_id),
        )
        if cur.fetchone():
            raise RepositoryConflictError("Case 已进入已发布数据集版本，禁止归档")
        cur.execute(
            """
            UPDATE evaluation_v2.eval_cases
            SET lifecycle_status = 'archived'
            WHERE tenant_id = %s AND case_id = %s
              AND lifecycle_status = %s
            """,
            (tenant_id, normalized_case_id, case["lifecycle_status"]),
        )
        _record_audit(
            cur, tenant_id, actor, "case.archived", "case",
            normalized_case_id,
            before_hash=str(case["content_hash"] or ""),
            details={
                "previous_status": case["lifecycle_status"],
                "latest_revision": case["revision"],
            },
        )
        return {
            "case_id": normalized_case_id,
            "lifecycle_status": "archived",
            "latest_revision": case["revision"],
            "archived": True,
        }


def set_case_golden(
    tenant_id: str,
    actor: str,
    case_id: str,
    *,
    golden: bool,
    reason: str = "",
) -> dict[str, Any]:
    """Golden 标注（批次 6a）：逐题标杆标记，append-only 留治理历史。

    与用例内容修订分离——revision content_hash 覆盖 metadata_json，直改
    已发布修订的 metadata 等于破坏内容锁，故标注独立成表。幂等语义：
    最新标注已同值时不再追加行，返回 changed=False 与现状。
    """
    tenant_id = _tenant(tenant_id)
    try:
        normalized_case_id = str(uuid.UUID(case_id))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError("Case 标识必须是 UUID") from exc
    clean_reason = str(reason or "").strip()[:1000]
    with _transaction(tenant_id) as (_conn, cur):
        cur.execute(
            """
            SELECT lifecycle_status
            FROM evaluation_v2.eval_cases
            WHERE tenant_id = %s AND case_id = %s
            FOR UPDATE
            """,
            (tenant_id, normalized_case_id),
        )
        case = _dict(cur.fetchone())
        if not case or case["lifecycle_status"] not in {"approved", "published"}:
            raise RepositoryNotFoundError("Case 不存在或不在可标注状态")
        cur.execute(
            """
            SELECT golden
            FROM evaluation_v2.eval_case_golden
            WHERE tenant_id = %s AND case_id = %s
            ORDER BY created_at DESC, mark_id DESC
            LIMIT 1
            """,
            (tenant_id, normalized_case_id),
        )
        latest = _dict(cur.fetchone())
        if latest and bool(latest["golden"]) == bool(golden):
            return {
                "case_id": normalized_case_id,
                "golden": bool(golden),
                "changed": False,
            }
        cur.execute(
            """
            INSERT INTO evaluation_v2.eval_case_golden
                (tenant_id, case_id, golden, reason, marked_by)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING mark_id, created_at
            """,
            (tenant_id, normalized_case_id, bool(golden), clean_reason, actor),
        )
        mark = _dict(cur.fetchone())
        return {
            "case_id": normalized_case_id,
            "golden": bool(golden),
            "changed": True,
            "mark_id": str(mark["mark_id"]),
            "marked_by": actor,
            "reason": clean_reason,
        }


def latest_golden_map(tenant_id: str, case_ids: list[str]) -> dict[str, dict[str, Any]]:
    """批量取各 case 最新 golden 标注；无标注的 case 不出现在结果里。"""
    tenant_id = _tenant(tenant_id)
    normalized: list[str] = []
    for item in case_ids[:200]:
        try:
            normalized.append(str(uuid.UUID(str(item))))
        except (TypeError, ValueError, AttributeError):
            continue
    if not normalized:
        return {}
    with _transaction(tenant_id) as (_conn, cur):
        cur.execute(
            """
            SELECT DISTINCT ON (case_id)
                   case_id, golden, reason, marked_by, created_at
            FROM evaluation_v2.eval_case_golden
            WHERE tenant_id = %s AND case_id = ANY(%s)
            ORDER BY case_id, created_at DESC, mark_id DESC
            """,
            (tenant_id, normalized),
        )
        return {
            str(row["case_id"]): {
                "golden": bool(row["golden"]),
                "reason": str(row["reason"] or ""),
                "marked_by": str(row["marked_by"] or ""),
                "created_at": row["created_at"].isoformat(),
            }
            for row in (_dict(item) for item in cur.fetchall())
        }


def create_candidates_bulk(
    tenant_id: str,
    actor: str,
    *,
    source_kind: str,
    payloads: list[dict[str, Any]],
    request_id: str,
) -> list[dict[str, Any]]:
    """全量候选导入在单事务提交；重放同一请求时按内容 hash 返回原结果。"""
    tenant_id = _tenant(tenant_id)
    if not payloads or len(payloads) > 100:
        raise ValueError("单批最多导入 100 条候选")
    results: list[dict[str, Any]] = []
    with _transaction(tenant_id) as (_conn, cur):
        first_source = payloads[0].get("source_ref") or {}
        expected_key = str(first_source.get("dataset_key", "")).strip()
        expected_module = str(first_source.get("module", "")).strip()
        if not expected_key or not expected_module:
            raise ValueError("导入候选必须绑定数据集标识和模块")
        domain = _candidate_domain(cur, tenant_id, payloads[0])
        if expected_module != domain:
            raise ValueError("导入模块与所属数据集业务域不一致")
        for index, payload in enumerate(payloads):
            source_ref = payload.get("source_ref") or {}
            if (
                str(source_ref.get("dataset_key", "")).strip() != expected_key
                or str(source_ref.get("module", "")).strip() != domain
            ):
                raise ValueError("同一批导入候选必须属于同一个数据集和模块")
            payload_hash = content_hash(payload)
            idempotency_key = f"{request_id}_{index:03d}"
            cur.execute(
                """
                SELECT candidate_id, payload_json, review_status, revision
                FROM evaluation_v2.eval_candidates
                WHERE tenant_id = %s AND idempotency_key = %s
                FOR UPDATE
                """,
                (tenant_id, idempotency_key),
            )
            existing = _dict(cur.fetchone())
            if existing:
                if content_hash(existing["payload_json"]) != payload_hash:
                    raise RepositoryConflictError("导入请求标识已用于不同内容")
                results.append(existing)
                continue
            cur.execute(
                """
                INSERT INTO evaluation_v2.eval_candidates
                    (tenant_id, source_kind, payload_json, privacy_status, review_status,
                     idempotency_key, created_by, updated_by)
                VALUES (%s, %s, %s::jsonb, 'redacted', 'in_review', %s, %s, %s)
                ON CONFLICT (tenant_id, idempotency_key) DO NOTHING
                RETURNING candidate_id, payload_json, review_status, revision
                """,
                (tenant_id, source_kind, canonical_json(payload), idempotency_key, actor, actor),
            )
            row = _dict(cur.fetchone())
            inserted = bool(row)
            if not row:
                cur.execute(
                    """
                    SELECT candidate_id, payload_json, review_status, revision
                    FROM evaluation_v2.eval_candidates
                    WHERE tenant_id = %s AND idempotency_key = %s
                    """,
                    (tenant_id, idempotency_key),
                )
                row = _dict(cur.fetchone())
                if not row or content_hash(row["payload_json"]) != payload_hash:
                    raise RepositoryConflictError("导入请求标识已用于不同内容")
            if inserted:
                _record_audit(
                    cur, tenant_id, actor, "candidate.imported", "candidate",
                    str(row["candidate_id"]), after_hash=payload_hash,
                    details={"batch_request_id": request_id, "ordinal": index},
                )
            results.append(row)
    return results


def count_duplicate_queries(tenant_id: str, dataset_key: str, queries: list[str]) -> int:
    """统计导入用例与既有内容的重复题目数（MVP 提示口径）。

    重复 = 归一化后的问题在该数据集的未决/已批准候选或任一已发布版本中出现过；
    仅作提示，不阻断导入——跨请求内容去重由导入幂等键承担。
    """
    tenant_id = _tenant(tenant_id)
    normalized = sorted({q.strip().lower() for q in queries if q and q.strip()})
    if not normalized or not dataset_key.strip():
        return 0
    with _transaction(tenant_id) as (_conn, cur):
        cur.execute(
            """
            SELECT DISTINCT lower(btrim(payload_json->'input'->>'query')) AS q
            FROM evaluation_v2.eval_candidates
            WHERE tenant_id = %s
              AND review_status IN ('in_review', 'approved')
              AND lower(btrim(payload_json->'input'->>'query')) = ANY(%s)
              AND COALESCE(
                    payload_json->'metadata'->>'dataset_key',
                    payload_json->'source_ref'->>'dataset_key', ''
                  ) = %s
            UNION
            SELECT DISTINCT lower(btrim(r.input_json->>'query')) AS q
            FROM evaluation_v2.eval_datasets AS d
            JOIN evaluation_v2.eval_dataset_versions AS v
              ON v.tenant_id = d.tenant_id AND v.dataset_id = d.dataset_id
            JOIN evaluation_v2.eval_dataset_items AS i
              ON i.tenant_id = v.tenant_id AND i.dataset_version_id = v.dataset_version_id
            JOIN evaluation_v2.eval_case_revisions AS r
              ON r.tenant_id = i.tenant_id AND r.case_id = i.case_id
             AND r.revision = i.case_revision
            WHERE d.tenant_id = %s AND d.dataset_key = %s
              AND lower(btrim(r.input_json->>'query')) = ANY(%s)
            """,
            (tenant_id, normalized, dataset_key, tenant_id, dataset_key, normalized),
        )
        existing = {str(item["q"]) for item in (cur.fetchall() or [])}
        return sum(1 for q in queries if q.strip().lower() in existing)


def list_candidates(
    tenant_id: str,
    *,
    review_status: str | None = None,
    dataset_key: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    tenant_id = _tenant(tenant_id)
    with _transaction(tenant_id) as (_conn, cur):
        cur.execute(
            """
            SELECT candidate_id, source_kind, payload_json, privacy_status, review_status,
                   revision, duplicate_key, promoted_case_id, created_by, updated_by,
                   created_at, updated_at
            FROM evaluation_v2.eval_candidates
            WHERE tenant_id = %s
              AND (%s::text IS NULL OR review_status = %s)
              AND (
                  %s::text IS NULL
                  OR COALESCE(
                      NULLIF(payload_json #>> '{metadata,dataset_key}', ''),
                      payload_json #>> '{source_ref,dataset_key}'
                  ) = %s
              )
            ORDER BY created_at DESC, candidate_id
            LIMIT %s OFFSET %s
            """,
            (
                tenant_id, review_status, review_status,
                dataset_key, dataset_key, limit, offset,
            ),
        )
        return [_dict(row) for row in cur.fetchall()]


def list_candidate_annotations(
    tenant_id: str, candidate_id: str
) -> list[dict[str, Any]]:
    """按租户读取候选的追加式审核记录。"""
    tenant_id = _tenant(tenant_id)
    with _transaction(tenant_id) as (_conn, cur):
        cur.execute(
            """
            SELECT a.annotation_id, a.candidate_revision, a.reviewer_id, a.verdict,
                   a.changed_fields, a.evidence_json, a.rationale, a.created_at,
                   c.payload_json->'proposed_evidence' AS candidate_evidence_json
            FROM evaluation_v2.eval_annotations AS a
            JOIN evaluation_v2.eval_candidates AS c
              ON c.tenant_id = a.tenant_id AND c.candidate_id = a.candidate_id
            WHERE a.tenant_id = %s AND a.candidate_id = %s
            ORDER BY a.candidate_revision DESC, a.created_at DESC, a.annotation_id DESC
            """,
            (tenant_id, candidate_id),
        )
        records = [_dict(row) for row in cur.fetchall()]
        for record in records:
            candidate_evidence = record.pop("candidate_evidence_json", None)
            evidence = record.get("evidence_json")
            if isinstance(evidence, str):
                evidence = json.loads(evidence)
            if record.get("verdict") == "rejected" and not evidence:
                if isinstance(candidate_evidence, str):
                    candidate_evidence = json.loads(candidate_evidence)
                evidence = candidate_evidence
            record["evidence_json"] = evidence if isinstance(evidence, list) else []
        return records


def review_candidate(
    tenant_id: str,
    actor: str,
    candidate_id: str,
    *,
    expected_revision: int,
    verdict: str,
    rationale: str,
    expected: dict[str, Any] | None = None,
    evidence: list[dict[str, Any]] | None = None,
    fact_evidence_bindings: list[dict[str, Any]] | None = None,
    quality_checks: dict[str, bool] | None = None,
) -> dict[str, Any]:
    tenant_id = _tenant(tenant_id)
    if not rationale.strip():
        raise ValueError("审核理由不能为空")
    evidence = [dict(item) for item in (evidence or [])]
    with _transaction(tenant_id) as (_conn, cur):
        cur.execute(
            """
            SELECT candidate_id, source_kind, payload_json, privacy_status, review_status,
                   revision, created_by, promoted_case_id,
                   revision_target_case_id, revision_base_number
            FROM evaluation_v2.eval_candidates
            WHERE tenant_id = %s AND candidate_id = %s
            FOR UPDATE
            """,
            (tenant_id, candidate_id),
        )
        current = _dict(cur.fetchone())
        if not current:
            raise RepositoryNotFoundError("评测候选不存在")
        if current["revision"] != expected_revision:
            raise RepositoryConflictError("候选已被其他审核者修改，请刷新后重试")
        if current["privacy_status"] != "redacted":
            raise ValueError("候选未通过隐私检查，不能审核")
        if current["review_status"] in {"approved", "rejected"}:
            raise RepositoryConflictError("候选已完成审核，不能覆盖结论")
        payload = current["payload_json"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        domain = _candidate_domain(cur, tenant_id, payload)

        promoted_case_id = current["promoted_case_id"]
        promoted_case_revision = None
        annotation_fields: dict[str, Any] = {}
        if verdict == "approved":
            if current["created_by"] == actor and str(payload.get("metadata", {}).get("risk_level", "")) in {
                "high", "critical",
            }:
                raise PermissionError("高风险候选需由不同于创建者的审核者批准")
            if not isinstance(expected, dict) or not expected:
                raise ValueError("批准候选必须填写经核验的标准事实")
            if domain == "rag":
                behavior = str(expected.get("behavior", "answer"))
                if behavior == "answer" and (not evidence or not all(_valid_evidence(item) for item in evidence)):
                    raise ValueError("回答样本必须绑定服务端核验的原文锚点")
                if behavior == "refuse":
                    if expected.get("reason") not in {"no_evidence", "permission_denied"}:
                        raise ValueError("拒答样本必须指定可验证的拒答原因")
                    if evidence or expected.get("reference_answer") or expected.get("required_facts"):
                        raise ValueError("拒答样本不能携带答案、必须事实或原文证据")
                elif behavior != "answer":
                    raise ValueError("样本 behavior 仅支持 answer 或 refuse")
            else:
                case_input = payload.get("input")
                validate_non_rag_case(domain, case_input, expected, evidence)
            if any(item.get("verified_by") != actor for item in evidence):
                raise ValueError("证据核验人与当前审核人不一致")
            case_input = payload.get("input")
            if not isinstance(case_input, dict) or not str(case_input.get("query", "")).strip():
                raise ValueError("候选缺少有效评测问题")
            revision_content = {
                "input": case_input,
                "expected": expected,
                "evidence": evidence,
                "metadata": payload.get("metadata", {}),
                "source_ref": payload.get("source_ref", {}),
            }
            revision_hash = content_hash(revision_content)
            revision_target_case_id = current.get("revision_target_case_id")
            if revision_target_case_id:
                cur.execute(
                    """
                    SELECT lifecycle_status
                    FROM evaluation_v2.eval_cases
                    WHERE tenant_id = %s AND case_id = %s
                    FOR UPDATE
                    """,
                    (tenant_id, revision_target_case_id),
                )
                target_case = _dict(cur.fetchone())
                if not target_case or target_case["lifecycle_status"] not in {"approved", "published"}:
                    raise RepositoryConflictError("修订目标已归档或不再可审核")
                cur.execute(
                    """
                    SELECT COALESCE(MAX(revision), 0) AS latest_revision
                    FROM evaluation_v2.eval_case_revisions
                    WHERE tenant_id = %s AND case_id = %s
                    """,
                    (tenant_id, revision_target_case_id),
                )
                latest_revision = int(cur.fetchone()["latest_revision"])
                if latest_revision != int(current["revision_base_number"] or 0):
                    raise RepositoryConflictError("修订候选基于旧版本，请刷新后重新创建")
                promoted_case_id = revision_target_case_id
                promoted_case_revision = latest_revision + 1
            else:
                cur.execute(
                    """
                    INSERT INTO evaluation_v2.eval_cases
                        (tenant_id, source_kind, lifecycle_status, created_by)
                    VALUES (%s, %s, 'approved', %s)
                    RETURNING case_id
                    """,
                    (tenant_id, current["source_kind"], current["created_by"]),
                )
                promoted_case_id = cur.fetchone()["case_id"]
                promoted_case_revision = 1
            cur.execute(
                """
                INSERT INTO evaluation_v2.eval_case_revisions
                    (tenant_id, case_id, revision, input_json, expected_json,
                     evidence_json, metadata_json, source_ref, source_content_hash,
                     content_hash, verified_by)
                VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb,
                        %s::jsonb, %s::jsonb, %s, %s, %s)
                """,
                (
                    tenant_id, promoted_case_id, promoted_case_revision,
                    canonical_json(case_input), canonical_json(expected),
                    canonical_json(evidence), canonical_json(payload.get("metadata", {})),
                    canonical_json(payload.get("source_ref", {})),
                    str(payload.get("source_content_hash", "")), revision_hash, actor,
                ),
            )
            annotation_fields = {
                "expected": expected,
                "evidence": evidence,
                "case_revision": promoted_case_revision,
            }
            if fact_evidence_bindings is not None:
                annotation_fields["fact_evidence_bindings"] = fact_evidence_bindings
            if quality_checks is not None:
                annotation_fields["quality_checks"] = quality_checks
        elif verdict not in {"rejected", "needs_changes"}:
            raise ValueError("审核结论无效")

        next_payload = dict(payload)
        if verdict == "approved":
            next_payload["verified_expected"] = expected
            next_payload["verified_evidence"] = evidence
            next_payload["verified_case_revision"] = promoted_case_revision
        next_revision = expected_revision + 1
        cur.execute(
            """
            UPDATE evaluation_v2.eval_candidates
            SET payload_json = %s::jsonb,
                review_status = %s,
                revision = %s,
                promoted_case_id = %s,
                updated_by = %s,
                updated_at = now()
            WHERE tenant_id = %s AND candidate_id = %s AND revision = %s
            RETURNING candidate_id, source_kind, privacy_status, review_status,
                      revision, promoted_case_id, updated_by, updated_at
            """,
            (
                canonical_json(next_payload),
                "in_review" if verdict == "needs_changes" else verdict,
                next_revision,
                promoted_case_id,
                actor, tenant_id, candidate_id, expected_revision,
            ),
        )
        updated = cur.fetchone()
        if updated is None:
            raise RepositoryConflictError("候选已被其他审核者修改，请刷新后重试")
        cur.execute(
            """
            INSERT INTO evaluation_v2.eval_annotations
                (tenant_id, candidate_id, candidate_revision, reviewer_id, verdict,
                 changed_fields, evidence_json, rationale)
            VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s)
            """,
            (
                tenant_id, candidate_id, next_revision, actor, verdict,
                canonical_json(annotation_fields), canonical_json(evidence), rationale.strip(),
            ),
        )
        _record_audit(
            cur, tenant_id, actor, "candidate.reviewed", "candidate", candidate_id,
            before_hash=content_hash(payload), after_hash=content_hash(next_payload),
            details={"verdict": verdict, "revision": next_revision},
        )
        result = _dict(updated)
        result["promoted_case_revision"] = promoted_case_revision
        return result


