"""backend/evaluation_v2/repositories/experiments.py — 评测实验组装与跨运行对比"""
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
from backend.evaluation_v2.repositories.runs import get_run, list_run_results

def list_experiments(tenant_id: str, *, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
    tenant_id = _tenant(tenant_id)
    with _transaction(tenant_id) as (_conn, cur):
        cur.execute(
            """
            SELECT experiment_id, suite_version_id, name,
                   baseline_run_id, candidate_run_id, created_by, created_at
            FROM evaluation_v2.eval_experiments
            WHERE tenant_id = %s
            ORDER BY created_at DESC, experiment_id DESC
            LIMIT %s OFFSET %s
            """,
            (tenant_id, limit, offset),
        )
        return [_dict(row) for row in cur.fetchall()]


def create_experiment(
    tenant_id: str, actor: str, *, suite_version_id: str, name: str
) -> dict[str, Any]:
    tenant_id = _tenant(tenant_id)
    normalized_name = name.strip()
    if not normalized_name or len(normalized_name) > 160:
        raise ValueError("实验名称不能为空且不得超过 160 个字符")
    with _transaction(tenant_id) as (_conn, cur):
        cur.execute(
            "SELECT 1 FROM evaluation_v2.eval_suite_versions WHERE tenant_id = %s AND suite_version_id = %s AND status = 'published'",
            (tenant_id, suite_version_id),
        )
        if cur.fetchone() is None:
            raise RepositoryNotFoundError("已发布评测方案版本不存在")
        cur.execute(
            """
            INSERT INTO evaluation_v2.eval_experiments
                (tenant_id, suite_version_id, name, created_by)
            VALUES (%s, %s, %s, %s)
            RETURNING experiment_id, suite_version_id, name,
                      baseline_run_id, candidate_run_id, created_by, created_at
            """,
            (tenant_id, suite_version_id, normalized_name, actor),
        )
        result = _dict(cur.fetchone())
        _record_audit(
            cur, tenant_id, actor, "experiment.created", "experiment",
            str(result["experiment_id"]),
            after_hash=content_hash({"suite_version_id": suite_version_id, "name": normalized_name}),
        )
        return result


def attach_experiment_run(
    tenant_id: str,
    actor: str,
    experiment_id: str,
    *,
    run_id: str,
    slot: str,
) -> dict[str, Any]:
    tenant_id = _tenant(tenant_id)
    if slot not in {"baseline", "candidate"}:
        raise ValueError("实验运行位必须是 baseline 或 candidate")
    field = "baseline_run_id" if slot == "baseline" else "candidate_run_id"
    with _transaction(tenant_id) as (_conn, cur):
        cur.execute(
            "SELECT suite_version_id FROM evaluation_v2.eval_experiments WHERE tenant_id = %s AND experiment_id = %s FOR UPDATE",
            (tenant_id, experiment_id),
        )
        experiment = _dict(cur.fetchone())
        if not experiment:
            raise RepositoryNotFoundError("实验不存在")
        cur.execute(
            "SELECT suite_version_id, job_status FROM evaluation_v2.eval_runs WHERE tenant_id = %s AND run_id = %s",
            (tenant_id, run_id),
        )
        run = _dict(cur.fetchone())
        if not run:
            raise RepositoryNotFoundError("评测运行不存在")
        if str(run["suite_version_id"]) != str(experiment["suite_version_id"]):
            raise ValueError("实验只能关联相同 Suite 版本的运行")
        cur.execute(
            f"UPDATE evaluation_v2.eval_experiments SET {field} = %s WHERE tenant_id = %s AND experiment_id = %s RETURNING experiment_id, suite_version_id, name, baseline_run_id, candidate_run_id, created_by, created_at",
            (run_id, tenant_id, experiment_id),
        )
        result = _dict(cur.fetchone())
        _record_audit(
            cur, tenant_id, actor, f"experiment.{slot}_attached", "experiment",
            str(experiment_id), details={"run_id": run_id, "job_status": run["job_status"]},
        )
        return result


def compare_runs(tenant_id: str, baseline_run_id: str, candidate_run_id: str) -> dict[str, Any]:
    """只对同方案、同 Golden、同语料快照且结果完整的运行计算差异。"""
    from backend.evaluation_v2.statistics import (
        holm_adjust_p_values,
        paired_binary_inference,
    )

    minimum_slice_samples = 20

    baseline = get_run(tenant_id, baseline_run_id)
    candidate = get_run(tenant_id, candidate_run_id)
    reasons: list[str] = []
    for key, label in (
        ("suite_version_id", "Suite 版本"),
        ("suite_hash", "Suite 快照"),
        ("corpus_hash", "语料快照"),
    ):
        if str(baseline.get(key)) != str(candidate.get(key)):
            reasons.append(f"{label}不一致")
    baseline_snapshot = baseline.get("snapshot_json") or {}
    candidate_snapshot = candidate.get("snapshot_json") or {}
    if isinstance(baseline_snapshot, str):
        baseline_snapshot = json.loads(baseline_snapshot)
    if isinstance(candidate_snapshot, str):
        candidate_snapshot = json.loads(candidate_snapshot)
    if baseline_snapshot.get("dataset_hash") != candidate_snapshot.get("dataset_hash"):
        reasons.append("Golden 数据集版本不一致")
    baseline_slice_tags = {
        str(item.get("case_id")): item.get("slice_tags") or {}
        for item in baseline_snapshot.get("cases", [])
        if isinstance(item, dict)
    }
    candidate_slice_tags = {
        str(item.get("case_id")): item.get("slice_tags") or {}
        for item in candidate_snapshot.get("cases", [])
        if isinstance(item, dict)
    }
    slice_tags_comparable = baseline_slice_tags == candidate_slice_tags
    if baseline["job_status"] != "completed" or candidate["job_status"] != "completed":
        reasons.append("两个运行都必须已完成")
    if baseline.get("gate_verdict") not in {"PASS", "FAIL"} or candidate.get("gate_verdict") not in {"PASS", "FAIL"}:
        reasons.append("两个运行都必须有有效的服务端 Gate 裁决")
    baseline_results = list_run_results(tenant_id, baseline_run_id, limit=20_000)
    candidate_results = list_run_results(tenant_id, candidate_run_id, limit=20_000)
    baseline_by_case = {str(row["case_id"]): row for row in baseline_results}
    candidate_by_case = {str(row["case_id"]): row for row in candidate_results}
    if set(baseline_by_case) != set(candidate_by_case):
        reasons.append("逐例结果覆盖的样本不一致")
    comparable = not reasons
    metric_keys = set()
    for row in (*baseline_results, *candidate_results):
        scores = row.get("scores_json") or {}
        if isinstance(scores, str):
            scores = json.loads(scores)
        metric_keys.update(key for key, value in scores.items() if isinstance(value, (int, float)) and not isinstance(value, bool))
    metrics: list[dict[str, Any]] = []
    cases: list[dict[str, Any]] = []
    slices: list[dict[str, Any]] = []
    pass_rate_inference: dict[str, Any] | None = None
    if comparable:
        for key in sorted(metric_keys):
            left_values = []
            right_values = []
            for case_id in sorted(baseline_by_case):
                left = baseline_by_case[case_id].get("scores_json") or {}
                right = candidate_by_case[case_id].get("scores_json") or {}
                if isinstance(left, str):
                    left = json.loads(left)
                if isinstance(right, str):
                    right = json.loads(right)
                a, b = left.get(key), right.get(key)
                if isinstance(a, (int, float)) and not isinstance(a, bool) and isinstance(b, (int, float)) and not isinstance(b, bool):
                    left_values.append(float(a))
                    right_values.append(float(b))
            if left_values and len(left_values) == len(right_values):
                left_mean = sum(left_values) / len(left_values)
                right_mean = sum(right_values) / len(right_values)
                metrics.append({
                    "key": key,
                    "baseline": round(left_mean, 6),
                    "candidate": round(right_mean, 6),
                    "delta": round(right_mean - left_mean, 6),
                    "sample_count": len(left_values),
                })
        question_by_case: dict[str, str] = {}
        if baseline_by_case:
            with _transaction(tenant_id) as (_conn, qcur):
                qcur.execute(
                    """
                    SELECT DISTINCT ON (r.case_id) r.case_id, r.input_json->>'query' AS query
                    FROM evaluation_v2.eval_case_revisions AS r
                    WHERE r.tenant_id = %s AND r.case_id::text = ANY(%s)
                    ORDER BY r.case_id, r.revision DESC
                    """,
                    (tenant_id, list(baseline_by_case.keys())),
                )
                question_by_case = {str(row["case_id"]): str(row["query"] or "") for row in qcur.fetchall() if row["query"]}
        cases = [
            {
                "case_id": case_id,
                "question": question_by_case.get(case_id, ""),
                "baseline": baseline_by_case[case_id]["result_status"],
                "candidate": candidate_by_case[case_id]["result_status"],
                "scores": {
                    "baseline": baseline_by_case[case_id].get("scores_json") or {},
                    "candidate": candidate_by_case[case_id].get("scores_json") or {},
                },
            }
            for case_id in sorted(baseline_by_case)
        ]
        pass_rate_inference = paired_binary_inference(cases)
        groups: dict[tuple[str, str], list[str]] = {}
        if slice_tags_comparable:
            for case_id in sorted(baseline_by_case):
                tags = baseline_slice_tags.get(case_id, {})
                if not isinstance(tags, dict):
                    continue
                for field, value in tags.items():
                    if field not in {"category", "scenario", "risk_level", "difficulty"}:
                        continue
                    if not isinstance(value, str) or not value.strip():
                        continue
                    groups.setdefault((field, value), []).append(case_id)

        selected_groups = sorted(
            groups.items(),
            key=lambda item: (-len(item[1]), item[0][0], item[0][1]),
        )[:200]
        slice_inference_by_group: dict[tuple[str, str], dict[str, Any]] = {}
        inference_group_keys: list[tuple[str, str]] = []
        raw_p_values: list[float] = []
        for group_key, case_ids in groups.items():
            inference = paired_binary_inference([
                {
                    "baseline": baseline_by_case[case_id]["result_status"],
                    "candidate": candidate_by_case[case_id]["result_status"],
                }
                for case_id in case_ids
            ])
            if inference["sample_count"] < minimum_slice_samples:
                slice_inference_by_group[group_key] = {
                    "eligible": False,
                    "sample_count": inference["sample_count"],
                    "minimum_sample_count": minimum_slice_samples,
                    "reason": "minimum_sample_count",
                }
                continue
            inference_group_keys.append(group_key)
            raw_p_values.append(float(inference["p_value"]))
            slice_inference_by_group[group_key] = {
                **inference,
                "eligible": True,
                "minimum_sample_count": minimum_slice_samples,
                "correction_method": "holm_bonferroni",
            }
        adjusted_p_values = holm_adjust_p_values(raw_p_values)
        family_size = len(adjusted_p_values)
        for group_key, adjusted_p_value in zip(inference_group_keys, adjusted_p_values):
            slice_inference_by_group[group_key]["adjusted_p_value"] = round(adjusted_p_value, 8)
            slice_inference_by_group[group_key]["family_size"] = family_size

        for (field, value), case_ids in selected_groups:
            baseline_passes = sum(
                baseline_by_case[case_id]["result_status"] == "pass"
                for case_id in case_ids
            )
            candidate_passes = sum(
                candidate_by_case[case_id]["result_status"] == "pass"
                for case_id in case_ids
            )
            metric_deltas: list[dict[str, Any]] = []
            for key in sorted(metric_keys):
                left_values: list[float] = []
                right_values: list[float] = []
                for case_id in case_ids:
                    left = baseline_by_case[case_id].get("scores_json") or {}
                    right = candidate_by_case[case_id].get("scores_json") or {}
                    if isinstance(left, str):
                        left = json.loads(left)
                    if isinstance(right, str):
                        right = json.loads(right)
                    a, b = left.get(key), right.get(key)
                    if (
                        isinstance(a, (int, float)) and not isinstance(a, bool)
                        and isinstance(b, (int, float)) and not isinstance(b, bool)
                    ):
                        left_values.append(float(a))
                        right_values.append(float(b))
                if left_values:
                    left_mean = sum(left_values) / len(left_values)
                    right_mean = sum(right_values) / len(right_values)
                    metric_deltas.append({
                        "key": key,
                        "baseline": round(left_mean, 6),
                        "candidate": round(right_mean, 6),
                        "delta": round(right_mean - left_mean, 6),
                        "sample_count": len(left_values),
                    })
            sample_count = len(case_ids)
            baseline_rate = baseline_passes / sample_count
            candidate_rate = candidate_passes / sample_count
            slices.append({
                "field": field,
                "value": value,
                "sample_count": sample_count,
                "baseline_pass_rate": round(baseline_rate, 6),
                "candidate_pass_rate": round(candidate_rate, 6),
                "pass_rate_delta": round(candidate_rate - baseline_rate, 6),
                "pass_rate_inference": slice_inference_by_group.get((field, value), {
                    "eligible": False,
                    "sample_count": sample_count,
                    "minimum_sample_count": minimum_slice_samples,
                    "reason": "slice_tags_not_comparable",
                }),
                "metrics": metric_deltas,
            })
    return {
        "comparable": comparable,
        "reasons": reasons,
        "baseline_run_id": baseline_run_id,
        "candidate_run_id": candidate_run_id,
        "metrics": metrics,
        "pass_rate_inference": pass_rate_inference,
        "cases": cases,
        "slices": slices,
        "slices_truncated": max(0, len(groups) - len(selected_groups)) if comparable else 0,
        "slice_warning": "两个运行的切片标签快照不同，未计算切片汇总。"
        if comparable and not slice_tags_comparable else "",
    }


