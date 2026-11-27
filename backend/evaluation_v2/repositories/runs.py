"""backend/evaluation_v2/repositories/runs.py — 评测运行生命周期、执行结果记录与统一视图"""
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
    _delegate,
)

def get_run_definition(tenant_id: str, suite_version_id: str) -> dict[str, Any]:
    """读取已发布 Suite 的不可变执行定义与选定修订。"""
    tenant_id = _tenant(tenant_id)
    from backend.evaluation_v2.capabilities import (
        require_dataset_domain_match,
        require_supported_runner_module,
    )

    with _transaction(tenant_id) as (_conn, cur):
        cur.execute(
            """
            SELECT s.suite_id, s.suite_key, s.name AS suite_name, s.module,
                   sv.suite_version_id, sv.version AS suite_version,
                   sv.dataset_version_id, sv.case_selection, sv.evaluator_versions,
                   sv.metric_policy, sv.security_policy, sv.minimum_valid_samples,
                   sv.snapshot_hash AS suite_hash,
                   d.domain AS dataset_domain,
                   dv.dataset_id, dv.version AS dataset_version,
                   dv.content_hash AS dataset_hash
            FROM evaluation_v2.eval_suite_versions AS sv
            JOIN evaluation_v2.eval_suites AS s
              ON s.tenant_id = sv.tenant_id AND s.suite_id = sv.suite_id
            JOIN evaluation_v2.eval_dataset_versions AS dv
              ON dv.tenant_id = sv.tenant_id
             AND dv.dataset_version_id = sv.dataset_version_id
            JOIN evaluation_v2.eval_datasets AS d
              ON d.tenant_id = dv.tenant_id AND d.dataset_id = dv.dataset_id
            WHERE sv.tenant_id = %s AND sv.suite_version_id = %s
              AND sv.status = 'published' AND dv.status = 'published'
            """,
            (tenant_id, suite_version_id),
        )
        definition = _dict(cur.fetchone())
        if not definition:
            raise RepositoryNotFoundError("已发布评测方案版本不存在")
        require_supported_runner_module(str(definition["module"]))
        require_dataset_domain_match(
            str(definition["module"]), str(definition["dataset_domain"]),
        )
        selection = definition.get("case_selection") or {}
        selected_ids = selection.get("case_ids") if isinstance(selection, dict) else None
        cur.execute(
            """
            SELECT i.case_id, i.case_revision, i.ordinal, r.input_json,
                   r.expected_json, r.evidence_json, r.metadata_json,
                   r.content_hash AS revision_hash
            FROM evaluation_v2.eval_dataset_items AS i
            JOIN evaluation_v2.eval_case_revisions AS r
              ON r.tenant_id = i.tenant_id AND r.case_id = i.case_id
             AND r.revision = i.case_revision
            WHERE i.tenant_id = %s AND i.dataset_version_id = %s
              AND (%s::uuid[] IS NULL OR i.case_id = ANY(%s::uuid[]))
            ORDER BY i.ordinal, i.case_id
            """,
            (tenant_id, definition["dataset_version_id"], selected_ids, selected_ids),
        )
        definition["cases"] = [_dict(row) for row in cur.fetchall()]
        if len(definition["cases"]) < int(definition["minimum_valid_samples"]):
            raise ValueError("方案所选的已发布样本少于最小有效样本数")
        evaluator_refs = definition.get("evaluator_versions") or []
        evaluator_ids = [str(item.get("evaluator_version_id", "")) for item in evaluator_refs]
        cur.execute(
            """
            SELECT evaluator_version_id, evaluator_key, version, evaluator_kind,
                   config_json, rubric_hash, status
            FROM evaluation_v2.eval_evaluator_versions
            WHERE tenant_id = %s AND evaluator_version_id = ANY(%s::uuid[])
            ORDER BY evaluator_version_id
            """,
            (tenant_id, evaluator_ids),
        )
        evaluator_rows = {_dict(row)["evaluator_version_id"]: _dict(row) for row in cur.fetchall()}
        definition["evaluator_definitions"] = [
            {
                **evaluator_rows[uuid.UUID(evaluator_id)],
                "binding": ref["binding"],
            }
            for ref, evaluator_id in zip(evaluator_refs, evaluator_ids, strict=True)
            if uuid.UUID(evaluator_id) in evaluator_rows
        ]
        if len(definition["evaluator_definitions"]) != len(evaluator_refs):
            raise RepositoryNotFoundError("评分器版本不存在或未发布")
        return definition


def get_run_by_request(tenant_id: str, request_id: str) -> dict[str, Any]:
    tenant_id = _tenant(tenant_id)
    with _transaction(tenant_id) as (_conn, cur):
        cur.execute(
            """
            SELECT run_id, tenant_id, suite_version_id, corpus_snapshot_id,
                   request_id, job_status, gate_verdict, gate_reason,
                   snapshot_json, env_hash, total_cases, completed_cases,
                   passed_cases, cost_amount, cost_currency, invalid_reason,
                   created_by, created_at, started_at, finished_at
            FROM evaluation_v2.eval_runs
            WHERE tenant_id = %s AND request_id = %s
            """,
            (tenant_id, request_id),
        )
        return _dict(cur.fetchone())


def create_run(
    tenant_id: str,
    actor: str,
    *,
    run_id: str,
    request_id: str,
    suite_version_id: str,
    kb_id: str | None,
    legacy_snapshot: dict[str, Any] | None,
    permissions_digest: str | None,
    snapshot_json: dict[str, Any],
    env_hash: str,
    request_fingerprint: str,
) -> dict[str, Any]:
    """在同一事务中固定语料、Suite 输入与运行记录；重复请求不创建第二个 Run。"""
    tenant_id = _tenant(tenant_id)
    if permissions_digest is not None and not re.fullmatch(r"[a-f0-9]{64}", permissions_digest):
        raise ValueError("权限快照摘要无效")
    if not re.fullmatch(r"[a-f0-9]{64}", env_hash):
        raise ValueError("运行环境摘要无效")
    if not re.fullmatch(r"[a-f0-9]{64}", request_fingerprint):
        raise ValueError("运行请求摘要无效")
    corpus_snapshot_id = snapshot_json.get("corpus_snapshot_id")
    if legacy_snapshot is None:
        if kb_id is not None or permissions_digest is not None or corpus_snapshot_id is not None:
            raise ValueError("非语料 Run 不能引用知识库或语料权限快照")
    else:
        if not kb_id or not permissions_digest:
            raise ValueError("RAG Run 缺少知识库或权限快照")
        legacy_snapshot_id = str(legacy_snapshot.get("snapshot_id", ""))
        snapshot_hash = str(legacy_snapshot.get("content_hash", ""))
        docs = legacy_snapshot.get("documents")
        if (
            not re.fullmatch(r"[0-9a-f]{24}", legacy_snapshot_id)
            or not re.fullmatch(r"[a-f0-9]{64}", snapshot_hash)
            or not isinstance(docs, list)
            or not corpus_snapshot_id
        ):
            raise ValueError("固定语料快照不完整")
        try:
            corpus_snapshot_id = str(uuid.UUID(str(corpus_snapshot_id)))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError("运行语料快照 ID 无效") from exc

    with _transaction(tenant_id) as (_conn, cur):
        cur.execute(
            "SELECT run_id, request_id, snapshot_json FROM evaluation_v2.eval_runs WHERE tenant_id = %s AND request_id = %s FOR UPDATE",
            (tenant_id, request_id),
        )
        existing = _dict(cur.fetchone())
        if existing:
            stored = existing["snapshot_json"]
            if isinstance(stored, str):
                stored = json.loads(stored)
            if stored.get("request_fingerprint") != request_fingerprint:
                raise RepositoryConflictError("幂等键已用于不同的评测请求")
            cur.execute(
                "SELECT * FROM evaluation_v2.eval_runs WHERE tenant_id = %s AND run_id = %s",
                (tenant_id, existing["run_id"]),
            )
            return _dict(cur.fetchone())

        definition_query = """
            SELECT sv.suite_version_id, sv.dataset_version_id, sv.status,
                   sv.snapshot_hash, dv.content_hash AS dataset_hash
            FROM evaluation_v2.eval_suite_versions AS sv
            JOIN evaluation_v2.eval_dataset_versions AS dv
              ON dv.tenant_id = sv.tenant_id AND dv.dataset_version_id = sv.dataset_version_id
            WHERE sv.tenant_id = %s AND sv.suite_version_id = %s
              AND sv.status = 'published' AND dv.status = 'published'
            FOR SHARE OF sv, dv
        """
        cur.execute(definition_query, (tenant_id, suite_version_id))
        definition = _dict(cur.fetchone())
        if not definition:
            raise RepositoryNotFoundError("已发布评测方案版本不存在")
        cases = snapshot_json.get("cases")
        if not isinstance(cases, list) or not cases:
            raise ValueError("运行快照缺少选定的用例")
        _validate_run_environment(snapshot_json, env_hash)
        if snapshot_json.get("suite_hash") != str(definition["snapshot_hash"]):
            raise RepositoryConflictError("评测方案快照已变化，请重新创建运行")
        if snapshot_json.get("dataset_hash") != str(definition["dataset_hash"]):
            raise RepositoryConflictError("Golden 数据集快照已变化，请重新创建运行")
        case_ids = [str(uuid.UUID(str(item.get("case_id", "")))) for item in cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("运行快照不能重复包含同一用例")
        cur.execute(
            """
            SELECT i.case_id, i.case_revision, r.content_hash
            FROM evaluation_v2.eval_dataset_items AS i
            JOIN evaluation_v2.eval_case_revisions AS r
              ON r.tenant_id = i.tenant_id AND r.case_id = i.case_id
             AND r.revision = i.case_revision
            WHERE i.tenant_id = %s AND i.dataset_version_id = %s
              AND i.case_id = ANY(%s::uuid[])
            ORDER BY i.ordinal
            """,
            (tenant_id, definition["dataset_version_id"], case_ids),
        )
        published_cases = [
            {"case_id": str(row["case_id"]), "case_revision": int(row["case_revision"]), "content_hash": str(row["content_hash"])}
            for row in cur.fetchall()
        ]
        requested_cases = sorted(
            (
                {
                    "case_id": str(uuid.UUID(str(item.get("case_id", "")))),
                    "case_revision": int(item.get("case_revision", 0)),
                    "content_hash": str(item.get("content_hash", "")),
                }
                for item in cases
            ),
            key=lambda item: item["case_id"],
        )
        if sorted(published_cases, key=lambda item: item["case_id"]) != requested_cases:
            raise RepositoryConflictError("运行样本与已发布 Golden 修订不一致")

        if legacy_snapshot is not None:
            documents = sorted(
                (dict(item) for item in legacy_snapshot["documents"]),
                key=lambda item: str(item.get("doc_id", "")),
            )
            cur.execute(
                """
                INSERT INTO evaluation_v2.eval_corpus_snapshots
                    (tenant_id, corpus_snapshot_id, kb_id, document_versions,
                     index_generation, permissions_digest, content_hash,
                     artifact_uri, captured_by)
                VALUES (%s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s)
                """,
                (
                    tenant_id, corpus_snapshot_id, kb_id,
                    canonical_json(documents),
                    str((legacy_snapshot.get("vector_manifests") or [""])[0]),
                    permissions_digest, legacy_snapshot["content_hash"],
                    f"rag-snapshot:{legacy_snapshot['snapshot_id']}", actor,
                ),
            )
        cur.execute(
            """
            INSERT INTO evaluation_v2.eval_runs
                (run_id, tenant_id, suite_version_id, corpus_snapshot_id,
                 request_id, job_status, snapshot_json, env_hash,
                 total_cases, created_by)
            VALUES (%s, %s, %s, %s, %s, 'queued', %s::jsonb, %s, %s, %s)
            RETURNING run_id, tenant_id, suite_version_id, corpus_snapshot_id,
                      request_id, job_status, gate_verdict, gate_reason,
                      snapshot_json, env_hash, total_cases, completed_cases,
                      passed_cases, cost_amount, cost_currency, invalid_reason,
                      created_by, created_at, started_at, finished_at
            """,
            (
                run_id, tenant_id, suite_version_id,
                corpus_snapshot_id, request_id,
                canonical_json(snapshot_json), env_hash, len(cases), actor,
            ),
        )
        result = _dict(cur.fetchone())
        _record_audit(
            cur, tenant_id, actor, "run.queued", "run", str(run_id),
            after_hash=content_hash(snapshot_json),
            details={"suite_version_id": suite_version_id, "case_count": len(cases)},
        )
        return result


def _validate_run_environment(snapshot_json: dict[str, Any], env_hash: str) -> None:
    """有环境明细的 Run 必须与其不可变环境哈希一致。"""
    environment = snapshot_json.get("environment")
    if environment is not None:
        if not isinstance(environment, dict) or content_hash(environment) != env_hash:
            raise ValueError("运行环境快照与环境哈希不一致")


def get_run(*args, **kwargs) -> dict[str, Any]:
    fn = _delegate("get_run", get_run, _real_get_run)
    return fn(*args, **kwargs)


def _real_get_run(tenant_id: str, run_id: str) -> dict[str, Any]:
    tenant_id = _tenant(tenant_id)
    with _transaction(tenant_id) as (_conn, cur):
        cur.execute(
            """
            SELECT r.run_id, r.tenant_id, r.suite_version_id, r.corpus_snapshot_id,
                   r.request_id, r.job_status, r.gate_verdict, r.gate_reason,
                   r.snapshot_json, r.env_hash, r.total_cases, r.completed_cases,
                   r.passed_cases, r.cost_amount, r.cost_currency, r.invalid_reason,
                   r.created_by, r.created_at, r.started_at, r.finished_at,
                   s.suite_id, s.suite_key, s.name AS suite_name, s.module,
                   sv.version AS suite_version, sv.snapshot_hash AS suite_hash,
                   c.kb_id, c.content_hash AS corpus_hash,
                   c.document_versions, c.artifact_uri
            FROM evaluation_v2.eval_runs AS r
            JOIN evaluation_v2.eval_suite_versions AS sv
              ON sv.tenant_id = r.tenant_id AND sv.suite_version_id = r.suite_version_id
            JOIN evaluation_v2.eval_suites AS s
              ON s.tenant_id = sv.tenant_id AND s.suite_id = sv.suite_id
            LEFT JOIN evaluation_v2.eval_corpus_snapshots AS c
              ON c.tenant_id = r.tenant_id AND c.corpus_snapshot_id = r.corpus_snapshot_id
            WHERE r.tenant_id = %s AND r.run_id = %s
            """,
            (tenant_id, run_id),
        )
        result = _dict(cur.fetchone())
        if not result:
            raise RepositoryNotFoundError("评测运行不存在")
        return result


def list_runs(*args, **kwargs) -> list[dict[str, Any]]:
    fn = _delegate("list_runs", list_runs, _real_list_runs)
    return fn(*args, **kwargs)


def _real_list_runs(tenant_id: str, *, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
    tenant_id = _tenant(tenant_id)
    with _transaction(tenant_id) as (_conn, cur):
        cur.execute(
            """
            SELECT r.run_id, r.suite_version_id, s.suite_key, s.name AS suite_name,
                   s.module,
                   sv.version AS suite_version, r.job_status, r.gate_verdict,
                   r.cost_amount, r.cost_currency,
                   r.total_cases, r.completed_cases, r.passed_cases,
                   r.snapshot_json->>'evaluation_mode' AS evaluation_mode,
                   r.snapshot_json->>'source' AS source,
                   r.snapshot_json->'prompt_versions' AS prompt_versions,
                   r.created_by, r.created_at, r.started_at, r.finished_at
            FROM evaluation_v2.eval_runs AS r
            JOIN evaluation_v2.eval_suite_versions AS sv
              ON sv.tenant_id = r.tenant_id AND sv.suite_version_id = r.suite_version_id
            JOIN evaluation_v2.eval_suites AS s
              ON s.tenant_id = sv.tenant_id AND s.suite_id = sv.suite_id
            WHERE r.tenant_id = %s
            ORDER BY r.created_at DESC, r.run_id DESC
            LIMIT %s OFFSET %s
            """,
            (tenant_id, limit, offset),
        )
        return [_dict(row) for row in cur.fetchall()]


def _normalize_ledger_row(row: dict[str, Any]) -> dict[str, Any]:
    """将 ai.eval_run_records 中的记录标准化投影为符合 EvaluationV2Run 契约的字典。"""
    module = str(row.get("module") or "")
    total = int(row.get("case_count") or 0)
    passed = int(row.get("pass_count") or 0)
    raw_pass_rate = row.get("pass_rate")
    pass_rate = float(raw_pass_rate) if raw_pass_rate is not None else (passed / total if total > 0 else 0.0)

    status = str(row.get("status") or "")
    if status in {"PASS", "completed"}:
        job_status = "completed"
    elif status in {"FAIL", "failed"}:
        job_status = "failed"
    elif status in {"running", "executing"}:
        job_status = "running"
    elif status in {"cancelled", "canceled"}:
        job_status = "cancelled"
    else:
        job_status = "completed"

    validity = str(row.get("validity") or "")
    if validity in {"PASS", "FAIL", "INVALID"}:
        gate_verdict = validity
    elif pass_rate >= 0.8:
        gate_verdict = "PASS"
    else:
        gate_verdict = "FAIL"

    prompt_snapshot = row.get("prompt_snapshot") or {}
    if isinstance(prompt_snapshot, str):
        try:
            prompt_snapshot = json.loads(prompt_snapshot)
        except Exception:
            prompt_snapshot = {}

    d_ver = row.get("dataset_version")
    if isinstance(d_ver, dict):
        suite_version = str(d_ver.get("dataset_version") or "1.0.0")
    else:
        suite_version = str(d_ver or "1.0.0")

    created_at = row.get("created_at")
    return {
        "run_id": str(row.get("run_id") or ""),
        "suite_version_id": "",
        "suite_key": module or "cli",
        "suite_name": f"{module.upper() if module else 'CLI'} (CLI/门禁)",
        "module": module,
        "suite_version": suite_version,
        "job_status": job_status,
        "gate_verdict": gate_verdict,
        "cost_amount": None,
        "cost_currency": "USD",
        "total_cases": total,
        "completed_cases": total,
        "passed_cases": passed,
        "evaluation_mode": str(row.get("mode") or "offline"),
        "source": str(row.get("trigger") or "ledger"),
        "prompt_versions": prompt_snapshot,
        "created_by": str(row.get("triggered_by") or "cli"),
        "created_at": created_at,
        "started_at": created_at,
        "finished_at": created_at,
    }


def _fetch_ledger_runs(*args, **kwargs) -> list[dict[str, Any]]:
    fn = _delegate("_fetch_ledger_runs", _fetch_ledger_runs, _real_fetch_ledger_runs)
    return fn(*args, **kwargs)


def _real_fetch_ledger_runs(limit: int = 100) -> list[dict[str, Any]]:
    """安全读取 ai.eval_run_records 记录；遇异常软失败返回空列表。"""
    try:
        from backend.config.database import OBS_DB_PG_CONFIG
        from backend.infra.db import engine_for

        with engine_for(OBS_DB_PG_CONFIG).raw_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT run_id, module, mode, status, validity, invalid_reason,
                       case_count, pass_count, pass_rate, metrics,
                       dataset_version, git_sha, trigger, triggered_by,
                       prompt_snapshot, evaluation_snapshot_hash,
                       created_at
                FROM ai.eval_run_records
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (limit,),
            )
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[evaluation_v2] 读取 ai.eval_run_records 失败，软回退: {exc}")
        return []


def list_runs_unified(*args, **kwargs) -> list[dict[str, Any]]:
    fn = _delegate("list_runs_unified", list_runs_unified, _real_list_runs_unified)
    return fn(*args, **kwargs)


def _real_list_runs_unified(
    tenant_id: str,
    *,
    limit: int = 50,
    offset: int = 0,
    include_ledger: bool = True,
) -> list[dict[str, Any]]:
    """双轨收口统一运行列表查询：混排 V2 原生评测与 CLI/发布门禁记录。"""
    tenant_id = _tenant(tenant_id)
    v2_runs = list_runs(tenant_id, limit=limit + offset, offset=0)
    if not include_ledger:
        return v2_runs[offset : offset + limit]

    try:
        raw_ledger_rows = _fetch_ledger_runs(limit=limit + offset)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[evaluation_v2] 获取 ledger runs 失败，降级为空: {exc}")
        raw_ledger_rows = []
    ledger_runs = [_normalize_ledger_row(r) for r in raw_ledger_rows]

    seen_ids = {str(r.get("run_id")) for r in v2_runs}
    merged: list[dict[str, Any]] = list(v2_runs)
    for lr in ledger_runs:
        rid = str(lr.get("run_id"))
        if rid and rid not in seen_ids:
            seen_ids.add(rid)
            merged.append(lr)

    def _sort_key(item: dict[str, Any]) -> str:
        ts = item.get("created_at") or item.get("finished_at") or ""
        return str(ts)

    merged.sort(key=_sort_key, reverse=True)
    return merged[offset : offset + limit]


def get_run_unified(tenant_id: str, run_id: str) -> dict[str, Any]:
    """统一评测详情查询：优先查 V2 表，失败时优雅回退查 ai.eval_run_records 与报告文件。"""
    tenant_id = _tenant(tenant_id)
    try:
        return get_run(tenant_id, run_id)
    except (RepositoryNotFoundError, ValueError):
        pass

    # 查 ai.eval_run_records
    try:
        from backend.config.database import OBS_DB_PG_CONFIG
        from backend.infra.db import engine_for

        with engine_for(OBS_DB_PG_CONFIG).raw_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT run_id, module, mode, status, validity, invalid_reason,
                       case_count, pass_count, pass_rate, metrics,
                       dataset_version, git_sha, trigger, triggered_by,
                       prompt_snapshot, evaluation_snapshot_hash,
                       created_at
                FROM ai.eval_run_records
                WHERE run_id = %s
                LIMIT 1
                """,
                (run_id,),
            )
            row = cur.fetchone()
            if row:
                cols = [d[0] for d in cur.description]
                r = dict(zip(cols, row))
                norm = _normalize_ledger_row(r)
                return {
                    **norm,
                    "tenant_id": tenant_id,
                    "corpus_snapshot_id": None,
                    "request_id": None,
                    "gate_reason": [{"invalid_reason": r.get("invalid_reason")}] if r.get("invalid_reason") else [],
                    "snapshot_json": {
                        "source": r.get("trigger") or "ledger",
                        "evaluation_mode": r.get("mode") or "offline",
                        "runner": "builtin",
                        "module": r.get("module") or "",
                        "prompt_versions": norm.get("prompt_versions") or {},
                        "runtime_environment": {
                            "git_sha": r.get("git_sha", ""),
                        },
                    },
                    "env_hash": r.get("evaluation_snapshot_hash") or "",
                    "invalid_reason": r.get("invalid_reason") or "",
                    "suite_id": None,
                    "suite_hash": r.get("evaluation_snapshot_hash") or "",
                    "kb_id": None,
                    "corpus_hash": None,
                    "document_versions": [],
                    "artifact_uri": f"data/eval_runs/{run_id}.json",
                }
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[evaluation_v2] 查询单条 ledger run 失败: {exc}")

    # 查本地报告文件
    try:
        from backend.evaluation.storage import load_report
        report, meta = load_report(run_id)
        total = len(report.results)
        passed = sum(1 for item in report.results if getattr(item, "status", "") == "pass")
        return {
            "run_id": run_id,
            "tenant_id": tenant_id,
            "suite_version_id": "",
            "suite_key": report.module,
            "suite_name": f"{report.module.upper()} (报告文件)",
            "module": report.module,
            "suite_version": "1.0.0",
            "job_status": "completed",
            "gate_verdict": "PASS" if passed == total else "FAIL",
            "gate_reason": [],
            "cost_amount": None,
            "cost_currency": "USD",
            "total_cases": total,
            "completed_cases": total,
            "passed_cases": passed,
            "evaluation_mode": report.mode,
            "source": (meta.get("env") or {}).get("trigger", "file"),
            "prompt_versions": meta.get("prompt_snapshot") or {},
            "snapshot_json": {
                "source": (meta.get("env") or {}).get("trigger", "file"),
                "evaluation_mode": report.mode,
                "runner": "builtin",
                "module": report.module,
                "prompt_versions": meta.get("prompt_snapshot") or {},
            },
            "env_hash": "",
            "invalid_reason": "",
            "created_by": (meta.get("env") or {}).get("triggered_by", "cli"),
            "created_at": report.timestamp,
            "started_at": report.timestamp,
            "finished_at": report.timestamp,
            "suite_id": None,
            "suite_hash": "",
            "kb_id": None,
            "corpus_hash": None,
            "document_versions": [],
            "artifact_uri": f"data/eval_runs/{run_id}.json",
        }
    except Exception:
        pass

    raise RepositoryNotFoundError(f"评测运行 {run_id} 不存在")


def get_domain_overview(tenant_id: str) -> dict[str, dict[str, Any]]:
    """聚合全域（rag, sql, cs, travel, agent, security）最新评测状态。"""
    tenant_id = _tenant(tenant_id)
    domains = ["rag", "sql", "cs", "travel", "agent", "security"]
    result: dict[str, dict[str, Any]] = {
        d: {
            "domain": d,
            "pass_rate": None,
            "time": None,
            "running": False,
            "source": None,
            "run_id": None,
            "total_cases": 0,
            "passed_cases": 0,
            "status": None,
        }
        for d in domains
    }

    def _normalize_domain(mod: str) -> str:
        mod = (mod or "").strip().lower()
        if mod == "rag":
            return "rag"
        if mod == "sql":
            return "sql"
        if mod in {"cs", "customer_service"}:
            return "cs"
        if mod == "travel":
            return "travel"
        if mod == "security":
            return "security"
        return "agent"

    runs = list_runs_unified(tenant_id, limit=100, offset=0, include_ledger=True)

    for r in runs:
        mod = str(r.get("module") or "")
        d = _normalize_domain(mod)
        cur = result[d]
        t = str(r.get("finished_at") or r.get("created_at") or "")
        if not t:
            continue
        cur_time = str(cur["time"] or "")
        if not cur_time or t > cur_time:
            total = int(r.get("total_cases") or 0)
            passed = int(r.get("passed_cases") or 0)
            pass_rate = round(passed / total, 4) if total > 0 else None
            is_running = str(r.get("job_status") or "") in {"running", "queued", "preparing", "aggregating"}
            result[d] = {
                "domain": d,
                "pass_rate": pass_rate,
                "time": t,
                "running": is_running,
                "source": r.get("source") or ("v2" if r.get("suite_version_id") else "ledger"),
                "run_id": str(r.get("run_id") or ""),
                "total_cases": total,
                "passed_cases": passed,
                "status": str(r.get("job_status") or ""),
            }

    return result


def set_run_status(
    tenant_id: str,
    actor: str,
    run_id: str,
    status: str,
    *,
    invalid_reason: str = "",
    cost_amount: str | None = None,
    cost_currency: str | None = None,
) -> dict[str, Any]:
    tenant_id = _tenant(tenant_id)
    statuses = {"queued", "preparing", "running", "aggregating", "completed", "failed", "cancelled", "blocked"}
    if status not in statuses:
        raise ValueError("运行状态无效")
    with _transaction(tenant_id) as (_conn, cur):
        cur.execute(
            "SELECT job_status FROM evaluation_v2.eval_runs WHERE tenant_id = %s AND run_id = %s FOR UPDATE",
            (tenant_id, run_id),
        )
        row = cur.fetchone()
        if row is None:
            raise RepositoryNotFoundError("评测运行不存在")
        current = row["job_status"]
        transitions = {
            "queued": {"preparing", "cancelled", "blocked"},
            "preparing": {"running", "failed", "cancelled", "blocked"},
            "running": {"aggregating", "failed", "cancelled", "blocked"},
            "aggregating": {"completed", "failed", "cancelled", "blocked"},
            "failed": {"preparing"},
            "blocked": set(),
            "completed": set(),
            "cancelled": set(),
        }
        if status != current and status not in transitions[current]:
            raise RepositoryConflictError(f"运行状态不可从 {current} 转换为 {status}")
        if status == "completed" and current != "completed":
            cur.execute(
                "SELECT total_cases, (SELECT COUNT(*) FROM evaluation_v2.eval_case_results x WHERE x.tenant_id = r.tenant_id AND x.run_id = r.run_id) AS result_count FROM evaluation_v2.eval_runs r WHERE r.tenant_id = %s AND r.run_id = %s",
                (tenant_id, run_id),
            )
            counts = cur.fetchone()
            if counts is None or int(counts["result_count"]) != int(counts["total_cases"]):
                raise RepositoryConflictError("逐例结果未完整持久化，运行不能标记为 completed")
        cur.execute(
            """
            UPDATE evaluation_v2.eval_runs AS r
            SET job_status = %s,
                gate_verdict = CASE WHEN %s IN ('preparing', 'running', 'aggregating') THEN NULL ELSE gate_verdict END,
                gate_reason = CASE WHEN %s IN ('preparing', 'running', 'aggregating') THEN '[]'::jsonb ELSE gate_reason END,
                started_at = CASE WHEN %s = 'preparing' AND started_at IS NULL THEN now() ELSE started_at END,
                finished_at = CASE
                    WHEN %s IN ('preparing', 'running', 'aggregating') THEN NULL
                    WHEN %s IN ('completed', 'failed', 'cancelled', 'blocked') THEN now()
                    ELSE finished_at
                END,
                invalid_reason = CASE
                    WHEN %s IN ('preparing', 'running', 'aggregating') THEN ''
                    WHEN %s <> '' THEN %s
                    ELSE invalid_reason
                END,
                cost_amount = COALESCE(%s::numeric, cost_amount),
                cost_currency = COALESCE(%s, cost_currency),
                completed_cases = (SELECT COUNT(*) FROM evaluation_v2.eval_case_results x WHERE x.tenant_id = r.tenant_id AND x.run_id = r.run_id),
                passed_cases = (SELECT COUNT(*) FROM evaluation_v2.eval_case_results x WHERE x.tenant_id = r.tenant_id AND x.run_id = r.run_id AND x.result_status = 'pass')
            WHERE r.tenant_id = %s AND r.run_id = %s
            RETURNING run_id, job_status, gate_verdict, total_cases,
                      completed_cases, passed_cases, invalid_reason,
                      started_at, finished_at
            """,
            (status, status, status, status, status, status, status, invalid_reason, invalid_reason,
             cost_amount, cost_currency, tenant_id, run_id),
        )
        result = _dict(cur.fetchone())
        _record_audit(
            cur, tenant_id, actor, "run.status_changed", "run", str(run_id),
            details={"from": current, "to": status, "invalid_reason": invalid_reason},
        )
        return result


def append_run_results(
    tenant_id: str,
    actor: str,
    run_id: str,
    results: list[dict[str, Any]],
) -> dict[str, int]:
    tenant_id = _tenant(tenant_id)
    with _transaction(tenant_id) as (_conn, cur):
        cur.execute(
            "SELECT snapshot_json FROM evaluation_v2.eval_runs WHERE tenant_id = %s AND run_id = %s FOR SHARE",
            (tenant_id, run_id),
        )
        run = _dict(cur.fetchone())
        if not run:
            raise RepositoryNotFoundError("评测运行不存在")
        snapshot = run["snapshot_json"]
        if isinstance(snapshot, str):
            snapshot = json.loads(snapshot)
        case_revisions = {
            str(item["case_id"]): int(item["case_revision"])
            for item in snapshot.get("cases", [])
        }
        if len(results) > len(case_revisions):
            raise ValueError("逐例结果超过运行快照样本数")
        inserted = 0
        for result in results:
            case_id = str(uuid.UUID(str(result["case_id"])))
            revision = case_revisions.get(case_id)
            if revision is None:
                raise ValueError("逐例结果包含不属于运行快照的用例")
            status = result.get("status")
            if status not in {"pass", "fail", "error", "skip"}:
                raise ValueError("逐例结果状态无效")
            payload = {
                "result_status": status,
                "scores": result.get("scores") or {},
                "failure_stage": str(result.get("failure_stage") or "")[:40],
                "latency_ms": result.get("latency_ms"),
                "cost_amount": result.get("cost_amount"),
                "cost_currency": result.get("cost_currency"),
                "trace_id": str(result.get("trace_id") or "")[:160],
                "artifact_uri": str(result.get("artifact_uri") or ""),
            }
            cur.execute(
                """
                INSERT INTO evaluation_v2.eval_case_results
                    (tenant_id, run_id, case_id, case_revision, result_status,
                     scores_json, actual_artifact_uri, trace_id, failure_stage,
                     latency_ms, cost_amount, cost_currency)
                VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s,
                        %s::numeric, %s)
                ON CONFLICT (tenant_id, run_id, case_id) DO NOTHING
                RETURNING result_id
                """,
                (
                    tenant_id, run_id, case_id, revision, status,
                    canonical_json(payload["scores"]), payload["artifact_uri"],
                    payload["trace_id"], payload["failure_stage"], payload["latency_ms"],
                    payload["cost_amount"], payload["cost_currency"],
                ),
            )
            row = cur.fetchone()
            if row is not None:
                inserted += 1
                continue
            cur.execute(
                """
                SELECT result_status, scores_json, failure_stage, latency_ms,
                       cost_amount, cost_currency, trace_id, actual_artifact_uri
                FROM evaluation_v2.eval_case_results
                WHERE tenant_id = %s AND run_id = %s AND case_id = %s
                """,
                (tenant_id, run_id, case_id),
            )
            existing = _dict(cur.fetchone())
            if not existing or content_hash({
                "result_status": existing["result_status"],
                "scores": existing["scores_json"],
                "failure_stage": existing["failure_stage"],
                "latency_ms": existing["latency_ms"],
                "cost_amount": str(existing["cost_amount"]) if existing["cost_amount"] is not None else None,
                "cost_currency": existing["cost_currency"],
                "trace_id": existing["trace_id"],
                "artifact_uri": existing["actual_artifact_uri"],
            }) != content_hash(payload):
                raise RepositoryConflictError("同一运行的逐例结果不一致，拒绝覆盖已保存结果")
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
        _record_audit(
            cur, tenant_id, actor, "run.results_appended", "run", str(run_id),
            details={"inserted": inserted, "completed": counts["completed"]},
        )
        return {"inserted": inserted, "completed": counts["completed"], "passed": counts["passed"]}


def list_run_results(*args, **kwargs) -> list[dict[str, Any]]:
    fn = _delegate("list_run_results", list_run_results, _real_list_run_results)
    return fn(*args, **kwargs)


def _real_list_run_results(
    tenant_id: str, run_id: str, *, limit: int = 100, offset: int = 0
) -> list[dict[str, Any]]:
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
            SELECT result_id, run_id, case_id, case_revision, result_status,
                   scores_json, actual_artifact_uri, trace_id, failure_stage,
                   latency_ms, cost_amount, cost_currency, created_at
            FROM evaluation_v2.eval_case_results
            WHERE tenant_id = %s AND run_id = %s
            ORDER BY case_id
            LIMIT %s OFFSET %s
            """,
            (tenant_id, run_id, limit, offset),
        )
        return [_dict(row) for row in cur.fetchall()]


def get_run_result(tenant_id: str, run_id: str, case_id: str) -> dict[str, Any]:
    """只在指定租户与 Run 下读取一条已持久化的逐例索引。"""
    tenant_id = _tenant(tenant_id)
    case_uuid = str(uuid.UUID(str(case_id)))
    with _transaction(tenant_id) as (_conn, cur):
        cur.execute(
            """
            SELECT result_id, run_id, case_id, case_revision, result_status,
                   scores_json, actual_artifact_uri, trace_id, failure_stage,
                   latency_ms, cost_amount, cost_currency, created_at
            FROM evaluation_v2.eval_case_results
            WHERE tenant_id = %s AND run_id = %s AND case_id = %s
            """,
            (tenant_id, run_id, case_uuid),
        )
        result = _dict(cur.fetchone())
        if not result:
            raise RepositoryNotFoundError("评测逐例结果不存在")
        return result


def list_human_reviews(tenant_id: str, run_id: str, case_id: str) -> dict[str, Any]:
    """读取指定 Run/Case 的固定人工 Rubric 与追加式评分记录。"""
    tenant_id = _tenant(tenant_id)
    case_uuid = str(uuid.UUID(str(case_id)))
    with _transaction(tenant_id) as (_conn, cur):
        cur.execute(
            "SELECT job_status, snapshot_json FROM evaluation_v2.eval_runs WHERE tenant_id = %s AND run_id = %s",
            (tenant_id, run_id),
        )
        run = _dict(cur.fetchone())
        if not run:
            raise RepositoryNotFoundError("评测运行不存在")
        if run["job_status"] != "completed":
            raise RepositoryConflictError("运行尚未完成，不能查看人工复核")
        cur.execute(
            "SELECT result_status FROM evaluation_v2.eval_case_results WHERE tenant_id = %s AND run_id = %s AND case_id = %s",
            (tenant_id, run_id, case_uuid),
        )
        result = _dict(cur.fetchone())
        if not result:
            raise RepositoryNotFoundError("评测逐例结果不存在")
        if result["result_status"] not in {"pass", "fail"}:
            raise RepositoryConflictError("仅成功完成的逐例结果可以人工评分")

        snapshot = run["snapshot_json"]
        if isinstance(snapshot, str):
            snapshot = json.loads(snapshot)
        if snapshot.get("execution_semantics") == "contract_only":
            raise RepositoryConflictError("契约检查结果不能作为人工质量评分样本")
        definitions = []
        for item in snapshot.get("evaluator_definitions") or []:
            if item.get("binding") != "human_review" or item.get("evaluator_kind") != "human":
                continue
            config = item.get("config") or {}
            definitions.append({
                "evaluator_version_id": str(item["evaluator_version_id"]),
                "evaluator_key": item["evaluator_key"],
                "version": item["version"],
                "rubric_hash": str(item["rubric_hash"]),
                "config": normalize_human_rubric(config),
            })
        cur.execute(
            """
            SELECT review_id, run_id, case_id, evaluator_version_id, request_id,
                   label, overall_score, scores_json, rationale, reviewed_by, reviewed_at
            FROM evaluation_v2.eval_human_reviews
            WHERE tenant_id = %s AND run_id = %s AND case_id = %s
            ORDER BY reviewed_at DESC, review_id DESC
            """,
            (tenant_id, run_id, case_uuid),
        )
        return {"evaluators": definitions, "reviews": [_dict(row) for row in cur.fetchall()]}


def record_human_review(
    tenant_id: str,
    actor: str,
    run_id: str,
    case_id: str,
    *,
    evaluator_version_id: str,
    request_id: str,
    label: str,
    scores: dict[str, Any],
    rationale: str,
) -> dict[str, Any]:
    """按 Run 快照中的人工 Rubric 追加评分；不回写机器评分或 Gate。"""
    tenant_id = _tenant(tenant_id)
    case_uuid = str(uuid.UUID(str(case_id)))
    evaluator_uuid = str(uuid.UUID(str(evaluator_version_id)))
    if label not in {"correct", "partially_correct", "incorrect", "unsafe", "unverifiable"}:
        raise ValueError("人工复核结论无效")
    rationale = rationale.strip()
    if not 8 <= len(rationale) <= 2000:
        raise ValueError("人工复核理由长度必须为 8 到 2000 个字符")
    if not isinstance(scores, dict):
        raise ValueError("人工评分必须是按 Rubric 维度提交的对象")

    with _transaction(tenant_id) as (_conn, cur):
        cur.execute(
            """
            SELECT job_status, gate_verdict, snapshot_json
            FROM evaluation_v2.eval_runs
            WHERE tenant_id = %s AND run_id = %s
            FOR UPDATE
            """,
            (tenant_id, run_id),
        )
        run = _dict(cur.fetchone())
        if not run:
            raise RepositoryNotFoundError("评测运行不存在")
        if run["job_status"] != "completed":
            raise RepositoryConflictError("运行尚未完成，不能人工评分")
        snapshot = run["snapshot_json"]
        if isinstance(snapshot, str):
            snapshot = json.loads(snapshot)
        if snapshot.get("execution_semantics") == "contract_only":
            raise RepositoryConflictError("契约检查结果不能作为人工质量评分样本")
        expected = {
            "run_id": run_id,
            "case_id": case_uuid,
            "evaluator_version_id": evaluator_uuid,
            "reviewed_by": actor,
            "label": label,
            "scores": scores,
            "rationale": rationale,
        }
        review_hash = content_hash(expected)
        cur.execute(
            """
            SELECT review_id, run_id, case_id, evaluator_version_id, request_id,
                   review_hash, label, overall_score, scores_json, rationale,
                   reviewed_by, reviewed_at
            FROM evaluation_v2.eval_human_reviews
            WHERE tenant_id = %s AND request_id = %s
            """,
            (tenant_id, request_id),
        )
        existing = _dict(cur.fetchone())
        if existing:
            if existing["review_hash"] != review_hash:
                raise RepositoryConflictError("人工复核幂等键已用于不同请求")
            existing.pop("review_hash", None)
            return existing

        evaluator = next((
            item for item in snapshot.get("evaluator_definitions") or []
            if str(item.get("evaluator_version_id")) == evaluator_uuid
            and item.get("binding") == "human_review"
            and item.get("evaluator_kind") == "human"
        ), None)
        if not evaluator:
            raise RepositoryConflictError("该运行快照未绑定此人工评分器版本")
        rubric = normalize_human_rubric(evaluator.get("config") or {})
        criteria = rubric["criteria"]
        keys = {item["key"] for item in criteria}
        if set(scores) != keys:
            raise ValueError("提交的评分维度必须与运行快照中的 Rubric 完全一致")
        normalized_scores: dict[str, float] = {}
        for key, value in scores.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError(f"评分维度 {key} 必须是 0 到 1 的有限数值")
            normalized_scores[key] = float(value)
        overall_score = round(sum(
            normalized_scores[item["key"]] * item["weight"] for item in criteria
        ), 5)

        cur.execute(
            """
            SELECT evaluator_kind, rubric_hash, status
            FROM evaluation_v2.eval_evaluator_versions
            WHERE tenant_id = %s AND evaluator_version_id = %s
            """,
            (tenant_id, evaluator_uuid),
        )
        persisted_evaluator = _dict(cur.fetchone())
        if (
            not persisted_evaluator
            or persisted_evaluator["status"] != "published"
            or persisted_evaluator["evaluator_kind"] != "human"
            or str(persisted_evaluator["rubric_hash"]).strip() != str(evaluator["rubric_hash"]).strip()
        ):
            raise RepositoryConflictError("运行绑定的人工 Rubric 版本不可用")
        cur.execute(
            """
            SELECT result_status FROM evaluation_v2.eval_case_results
            WHERE tenant_id = %s AND run_id = %s AND case_id = %s
            """,
            (tenant_id, run_id, case_uuid),
        )
        result = _dict(cur.fetchone())
        if not result:
            raise RepositoryNotFoundError("评测逐例结果不存在")
        if result["result_status"] not in {"pass", "fail"}:
            raise RepositoryConflictError("仅成功完成的逐例结果可以人工评分")
        cur.execute(
            """
            INSERT INTO evaluation_v2.eval_human_reviews
                (tenant_id, run_id, case_id, evaluator_version_id, request_id,
                 review_hash, label, overall_score, scores_json, rationale, reviewed_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)
            RETURNING review_id, run_id, case_id, evaluator_version_id, request_id,
                      label, overall_score, scores_json, rationale, reviewed_by, reviewed_at
            """,
            (
                tenant_id, run_id, case_uuid, evaluator_uuid, request_id,
                review_hash, label, overall_score, canonical_json(normalized_scores),
                rationale, actor,
            ),
        )
        review = _dict(cur.fetchone())
        _record_audit(
            cur, tenant_id, actor, "run.case_human_reviewed", "run", run_id,
            after_hash=review_hash,
            details={
                "review_id": str(review["review_id"]),
                "case_id": case_uuid,
                "evaluator_version_id": evaluator_uuid,
                "label": label,
                "overall_score": overall_score,
            },
        )
        return review


