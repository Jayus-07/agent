"""backend/evaluation_v2/repositories/datasets.py — 评测数据集、评测套件与评估器版本管理"""
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

def list_datasets(tenant_id: str, *, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
    tenant_id = _tenant(tenant_id)
    with _transaction(tenant_id) as (_conn, cur):
        cur.execute(
            """
            SELECT d.dataset_id, d.dataset_key, d.name, d.domain, d.description,
                   d.owner_user_id, d.status, d.created_at, d.updated_at,
                   COUNT(DISTINCT v.dataset_version_id) FILTER (WHERE v.status = 'published') AS version_count,
                   lv.version AS latest_version,
                   lv.status AS latest_status,
                   COALESCE(lv.case_count, 0) AS case_count
            FROM evaluation_v2.eval_datasets AS d
            LEFT JOIN evaluation_v2.eval_dataset_versions AS v
              ON v.tenant_id = d.tenant_id AND v.dataset_id = d.dataset_id
            LEFT JOIN LATERAL (
                SELECT ver.version, ver.status,
                       (SELECT COUNT(*)::integer
                        FROM evaluation_v2.eval_dataset_items AS i
                        WHERE i.tenant_id = ver.tenant_id
                          AND i.dataset_version_id = ver.dataset_version_id) AS case_count
                FROM evaluation_v2.eval_dataset_versions AS ver
                WHERE ver.tenant_id = d.tenant_id AND ver.dataset_id = d.dataset_id
                ORDER BY CASE ver.status WHEN 'published' THEN 0 ELSE 1 END,
                         ver.created_at DESC
                LIMIT 1
            ) AS lv ON TRUE
            WHERE d.tenant_id = %s AND d.status = 'active'
            GROUP BY d.dataset_id, lv.version, lv.status, lv.case_count
            ORDER BY d.updated_at DESC, d.dataset_id
            LIMIT %s OFFSET %s
            """,
            (tenant_id, limit, offset),
        )
        return [_dict(row) for row in cur.fetchall()]


def get_dataset_domain(
    tenant_id: str, *, dataset_id: str | None = None, dataset_key: str | None = None,
) -> str:
    """按可信租户查询一个已启用数据集的模块域。"""
    tenant_id = _tenant(tenant_id)
    if bool(dataset_id) == bool(dataset_key):
        raise ValueError("必须且只能指定 dataset_id 或 dataset_key")
    where = "dataset_id = %s" if dataset_id else "dataset_key = %s"
    target = dataset_id or dataset_key
    with _transaction(tenant_id) as (_conn, cur):
        cur.execute(
            f"SELECT domain FROM evaluation_v2.eval_datasets WHERE tenant_id = %s AND {where} AND status = 'active'",
            (tenant_id, target),
        )
        row = cur.fetchone()
        if not row:
            raise RepositoryNotFoundError("评测数据集不存在或不可用")
        return str(row["domain"])


def create_dataset(
    tenant_id: str,
    actor: str,
    *,
    dataset_key: str,
    name: str,
    domain: str,
    description: str,
) -> dict[str, Any]:
    tenant_id = _tenant(tenant_id)
    domain = validate_dataset_domain(domain)
    payload = {
        "dataset_key": dataset_key,
        "name": name,
        "domain": domain,
        "description": description,
    }
    digest = content_hash(payload)
    with _transaction(tenant_id) as (_conn, cur):
        cur.execute(
            """
            INSERT INTO evaluation_v2.eval_datasets
                (tenant_id, dataset_key, name, domain, description, owner_user_id)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (tenant_id, dataset_key) DO NOTHING
            RETURNING dataset_id, dataset_key, name, domain, description,
                      owner_user_id, status, created_at, updated_at
            """,
            (tenant_id, dataset_key, name, domain, description, actor),
        )
        row = cur.fetchone()
        if row is None:
            raise RepositoryConflictError("同一租户下的数据集标识已存在")
        result = _dict(row)
        _record_audit(
            cur, tenant_id, actor, "dataset.created", "dataset",
            str(result["dataset_id"]), after_hash=digest,
        )
        return result


def ensure_dataset(
    tenant_id: str,
    actor: str,
    *,
    dataset_key: str,
    name: str,
    domain: str,
    description: str = "",
) -> dict[str, Any]:
    """按 tenant 和稳定 key 幂等取得生成任务的数据集归属。"""
    tenant_id = _tenant(tenant_id)
    domain = validate_dataset_domain(domain)
    with _transaction(tenant_id) as (_conn, cur):
        cur.execute(
            """
            INSERT INTO evaluation_v2.eval_datasets
                (tenant_id, dataset_key, name, domain, description, owner_user_id)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (tenant_id, dataset_key) DO NOTHING
            RETURNING dataset_id, dataset_key, name, domain, description,
                      owner_user_id, status, created_at, updated_at
            """,
            (tenant_id, dataset_key, name, domain, description, actor),
        )
        row = cur.fetchone()
        if row is not None:
            result = _dict(row)
            _record_audit(
                cur, tenant_id, actor, "dataset.created", "dataset",
                str(result["dataset_id"]),
                after_hash=content_hash({"dataset_key": dataset_key, "name": name, "domain": domain}),
                details={"source": "evaluation_generation"},
            )
            return result
        cur.execute(
            """
            SELECT dataset_id, dataset_key, name, domain, description,
                   owner_user_id, status, created_at, updated_at
            FROM evaluation_v2.eval_datasets
            WHERE tenant_id = %s AND dataset_key = %s AND status = 'active'
            """,
            (tenant_id, dataset_key),
        )
        existing = _dict(cur.fetchone())
        if not existing:
            raise RepositoryConflictError("生成目标数据集已归档或不可见")
        if existing["name"] != name or existing["domain"] != domain:
            raise RepositoryConflictError("数据集标识已绑定到不同名称或业务域")
        return existing


def list_dataset_versions(tenant_id: str, dataset_id: str) -> list[dict[str, Any]]:
    tenant_id = _tenant(tenant_id)
    with _transaction(tenant_id) as (_conn, cur):
        cur.execute(
            "SELECT dataset_id FROM evaluation_v2.eval_datasets WHERE tenant_id = %s AND dataset_id = %s",
            (tenant_id, dataset_id),
        )
        if cur.fetchone() is None:
            raise RepositoryNotFoundError("数据集不存在")
        cur.execute(
            """
            SELECT v.dataset_version_id, v.dataset_id, v.version, v.status, v.content_hash,
                   v.published_by, v.published_at, v.created_at,
                   COUNT(i.case_id)::integer AS case_count,
                   (SELECT COUNT(*)::integer FROM evaluation_v2.eval_runs AS r
                     WHERE r.tenant_id = v.tenant_id
                       AND r.snapshot_json->>'dataset_version_id' = v.dataset_version_id::text) AS run_refs
            FROM evaluation_v2.eval_dataset_versions AS v
            LEFT JOIN evaluation_v2.eval_dataset_items AS i
              ON i.tenant_id = v.tenant_id AND i.dataset_version_id = v.dataset_version_id
            WHERE v.tenant_id = %s AND v.dataset_id = %s
            GROUP BY v.dataset_version_id
            ORDER BY v.created_at DESC, v.version DESC
            """,
            (tenant_id, dataset_id),
        )
        return [_dict(row) for row in cur.fetchall()]


def get_dataset_version_coverage(
    tenant_id: str,
    dataset_id: str,
    dataset_version_id: str,
) -> dict[str, Any]:
    """读取发布版本的有限分类标签，不读取问题、答案或证据正文。"""
    tenant_id = _tenant(tenant_id)
    dimensions = ("category", "scenario", "risk_level", "difficulty")
    expressions = ",\n                   ".join(
        f"CASE WHEN jsonb_typeof(r.metadata_json -> '{field}') IN ('string', 'number') "
        f"AND char_length(r.metadata_json ->> '{field}') <= 64 "
        f"THEN r.metadata_json ->> '{field}' END AS {field}"
        for field in dimensions
    )
    with _transaction(tenant_id) as (_conn, cur):
        cur.execute(
            f"""
            SELECT v.dataset_id, v.dataset_version_id, v.version, v.content_hash,
                   COUNT(i.case_id) OVER()::integer AS case_count,
                   i.case_id::text AS case_id,
                   {expressions}
            FROM evaluation_v2.eval_dataset_versions AS v
            JOIN evaluation_v2.eval_datasets AS d
              ON d.tenant_id = v.tenant_id AND d.dataset_id = v.dataset_id
            LEFT JOIN evaluation_v2.eval_dataset_items AS i
              ON i.tenant_id = v.tenant_id
             AND i.dataset_version_id = v.dataset_version_id
            LEFT JOIN evaluation_v2.eval_case_revisions AS r
              ON r.tenant_id = i.tenant_id AND r.case_id = i.case_id
             AND r.revision = i.case_revision
            WHERE v.tenant_id = %s AND v.dataset_id = %s
              AND v.dataset_version_id = %s AND v.status = 'published'
            ORDER BY i.ordinal NULLS LAST, i.case_id
            LIMIT 20001
            """,
            (tenant_id, dataset_id, dataset_version_id),
        )
        rows = [_dict(row) for row in cur.fetchall()]
        if not rows:
            raise RepositoryNotFoundError("已发布 Golden 版本不存在或不属于当前数据集")
        first = rows[0]
        case_count = int(first["case_count"])
        if case_count > 20_000:
            raise RepositoryConflictError("Golden 样本量超过覆盖分布统计上限")
        cases = [
            {field: row.get(field) for field in dimensions}
            for row in rows
            if row.get("case_id") is not None
        ]
        return {
            "dataset_id": str(first["dataset_id"]),
            "dataset_version_id": str(first["dataset_version_id"]),
            "version": str(first["version"]),
            "content_hash": str(first["content_hash"]),
            "case_count": case_count,
            "cases": cases,
        }


def load_dataset_version_export(
    tenant_id: str,
    dataset_id: str,
    dataset_version_id: str,
    *,
    max_cases: int = 5000,
    max_estimated_bytes: int = 8 * 1024 * 1024,
) -> dict[str, Any]:
    """读取受限大小的已发布 Golden，并重算清单哈希后才允许导出。"""
    tenant_id = _tenant(tenant_id)
    with _transaction(tenant_id) as (_conn, cur):
        cur.execute(
            """
            SELECT d.dataset_id, d.dataset_key, d.name AS dataset_name, d.domain,
                   v.dataset_version_id, v.version, v.status, v.content_hash,
                   v.manifest_json, v.published_by, v.published_at
            FROM evaluation_v2.eval_datasets AS d
            JOIN evaluation_v2.eval_dataset_versions AS v
              ON v.tenant_id = d.tenant_id AND v.dataset_id = d.dataset_id
            WHERE d.tenant_id = %s AND d.dataset_id = %s
              AND v.dataset_version_id = %s
            FOR SHARE OF d, v
            """,
            (tenant_id, dataset_id, dataset_version_id),
        )
        dataset_version = _dict(cur.fetchone())
        if not dataset_version:
            raise RepositoryNotFoundError("Golden 版本不存在或不属于当前数据集")
        if dataset_version["status"] != "published":
            raise RepositoryConflictError("只能导出已发布的 Golden 版本")

        cur.execute(
            """
            SELECT COUNT(*)::integer AS case_count,
                   COALESCE(SUM(
                       octet_length(r.input_json::text)
                       + octet_length(r.expected_json::text)
                       + octet_length(r.evidence_json::text)
                       + octet_length(r.metadata_json::text)
                       + 512
                   ), 0)::bigint AS estimated_bytes
            FROM evaluation_v2.eval_dataset_items AS i
            JOIN evaluation_v2.eval_case_revisions AS r
              ON r.tenant_id = i.tenant_id AND r.case_id = i.case_id
             AND r.revision = i.case_revision
            WHERE i.tenant_id = %s AND i.dataset_version_id = %s
            """,
            (tenant_id, dataset_version_id),
        )
        size = _dict(cur.fetchone())
        case_count = int(size["case_count"])
        estimated_bytes = int(size["estimated_bytes"])
        if case_count > max_cases or estimated_bytes > max_estimated_bytes:
            raise RepositoryExportLimitError(
                f"Golden 导出超出限制（最多 {max_cases} 条、约 {max_estimated_bytes // 1024 // 1024} MB）"
            )

        cur.execute(
            """
            SELECT i.case_id::text AS case_id, i.case_revision AS revision,
                   r.input_json, r.expected_json, r.evidence_json, r.metadata_json,
                   r.content_hash AS revision_hash
            FROM evaluation_v2.eval_dataset_items AS i
            JOIN evaluation_v2.eval_case_revisions AS r
              ON r.tenant_id = i.tenant_id AND r.case_id = i.case_id
             AND r.revision = i.case_revision
            WHERE i.tenant_id = %s AND i.dataset_version_id = %s
            ORDER BY i.ordinal, i.case_id
            """,
            (tenant_id, dataset_version_id),
        )
        rows = [_dict(row) for row in cur.fetchall()]
        if len(rows) != case_count:
            raise RepositoryConflictError("Golden 清单与样本修订数量不一致")

        manifest = {
            "schema_version": "evaluation.dataset.v2",
            "dataset_id": str(dataset_version["dataset_id"]),
            "version": str(dataset_version["version"]),
            "items": [
                {
                    "case_id": row["case_id"],
                    "revision": int(row["revision"]),
                    "hash": str(row["revision_hash"]),
                }
                for row in rows
            ],
        }
        stored_manifest = dataset_version["manifest_json"]
        if isinstance(stored_manifest, str):
            stored_manifest = json.loads(stored_manifest)
        if (
            canonical_json(stored_manifest) != canonical_json(manifest)
            or content_hash(manifest) != str(dataset_version["content_hash"])
        ):
            raise RepositoryConflictError("Golden 清单哈希校验失败，拒绝导出")

        return {
            "dataset_id": str(dataset_version["dataset_id"]),
            "dataset_key": str(dataset_version["dataset_key"]),
            "dataset_name": str(dataset_version["dataset_name"]),
            "domain": str(dataset_version["domain"]),
            "dataset_version_id": str(dataset_version["dataset_version_id"]),
            "version": str(dataset_version["version"]),
            "content_hash": str(dataset_version["content_hash"]),
            "published_by": str(dataset_version["published_by"] or ""),
            "published_at": dataset_version["published_at"],
            "cases": [
                {
                    "case_id": row["case_id"],
                    "revision": int(row["revision"]),
                    "input": row["input_json"],
                    "expected": row["expected_json"],
                    "evidence": row["evidence_json"],
                    "metadata": row["metadata_json"],
                    "content_hash": str(row["revision_hash"]),
                }
                for row in rows
            ],
        }


def record_dataset_version_export(
    tenant_id: str,
    actor: str,
    dataset_version_id: str,
    *,
    content_hash_value: str,
    case_count: int,
) -> None:
    """记录 Golden 导出审计，不保存导出正文。"""
    tenant_id = _tenant(tenant_id)
    with _transaction(tenant_id) as (_conn, cur):
        cur.execute(
            """
            SELECT dataset_id, version, content_hash, status
            FROM evaluation_v2.eval_dataset_versions
            WHERE tenant_id = %s AND dataset_version_id = %s
            FOR SHARE
            """,
            (tenant_id, dataset_version_id),
        )
        version = _dict(cur.fetchone())
        if not version:
            raise RepositoryNotFoundError("Golden 版本不存在")
        if version["status"] != "published" or str(version["content_hash"]) != content_hash_value:
            raise RepositoryConflictError("Golden 版本状态或哈希已变化，拒绝记录导出")
        _record_audit(
            cur, tenant_id, actor, "dataset.version_exported", "dataset_version",
            dataset_version_id,
            after_hash=content_hash_value,
            details={
                "dataset_id": str(version["dataset_id"]),
                "version": str(version["version"]),
                "format": "jsonl",
                "case_count": case_count,
            },
        )


def compare_dataset_versions(
    tenant_id: str,
    dataset_id: str,
    base_version_id: str,
    target_version_id: str,
) -> dict[str, Any]:
    """只比较两个已发布 Golden 的样本 ID、修订和内容哈希，不读取正文。"""
    tenant_id = _tenant(tenant_id)
    if base_version_id == target_version_id:
        raise ValueError("请选择两个不同的 Golden 版本")

    with _transaction(tenant_id) as (_conn, cur):
        cur.execute(
            """
            SELECT dataset_version_id, version, status, content_hash
            FROM evaluation_v2.eval_dataset_versions
            WHERE tenant_id = %s AND dataset_id = %s
              AND dataset_version_id = ANY(%s::uuid[])
            """,
            (tenant_id, dataset_id, [base_version_id, target_version_id]),
        )
        versions = {
            str(row["dataset_version_id"]): _dict(row)
            for row in cur.fetchall()
        }
        if set(versions) != {base_version_id, target_version_id}:
            raise RepositoryNotFoundError("Golden 版本不存在或不属于当前数据集")
        if any(row["status"] != "published" for row in versions.values()):
            raise ValueError("只能比较已发布的 Golden 版本")

        cur.execute(
            """
            SELECT COALESCE(base.case_id, target.case_id)::text AS case_id,
                   base.case_revision AS base_revision,
                   base_revision.content_hash AS base_hash,
                   target.case_revision AS target_revision,
                   target_revision.content_hash AS target_hash
            FROM (
                SELECT case_id, case_revision
                FROM evaluation_v2.eval_dataset_items
                WHERE tenant_id = %s AND dataset_version_id = %s
            ) AS base
            FULL OUTER JOIN (
                SELECT case_id, case_revision
                FROM evaluation_v2.eval_dataset_items
                WHERE tenant_id = %s AND dataset_version_id = %s
            ) AS target ON target.case_id = base.case_id
            LEFT JOIN evaluation_v2.eval_case_revisions AS base_revision
              ON base_revision.tenant_id = %s
             AND base_revision.case_id = base.case_id
             AND base_revision.revision = base.case_revision
            LEFT JOIN evaluation_v2.eval_case_revisions AS target_revision
              ON target_revision.tenant_id = %s
             AND target_revision.case_id = target.case_id
             AND target_revision.revision = target.case_revision
            ORDER BY case_id
            """,
            (
                tenant_id, base_version_id,
                tenant_id, target_version_id,
                tenant_id, tenant_id,
            ),
        )

        added: list[dict[str, Any]] = []
        removed: list[dict[str, Any]] = []
        changed: list[dict[str, Any]] = []
        unchanged_count = 0
        for row in cur.fetchall():
            item = _dict(row)
            if item["base_revision"] is None:
                added.append({
                    "case_id": item["case_id"],
                    "revision": item["target_revision"],
                })
            elif item["target_revision"] is None:
                removed.append({
                    "case_id": item["case_id"],
                    "revision": item["base_revision"],
                })
            elif (
                item["base_revision"] != item["target_revision"]
                or item["base_hash"] != item["target_hash"]
            ):
                changed.append({
                    "case_id": item["case_id"],
                    "base_revision": item["base_revision"],
                    "base_hash": item["base_hash"],
                    "target_revision": item["target_revision"],
                    "target_hash": item["target_hash"],
                })
            else:
                unchanged_count += 1

        return {
            "dataset_id": dataset_id,
            "base_version": versions[base_version_id]["version"],
            "target_version": versions[target_version_id]["version"],
            "base_content_hash": versions[base_version_id]["content_hash"],
            "target_content_hash": versions[target_version_id]["content_hash"],
            "added": added,
            "removed": removed,
            "changed": changed,
            "unchanged_count": unchanged_count,
            "summary": {
                "added": len(added),
                "removed": len(removed),
                "changed": len(changed),
                "unchanged": unchanged_count,
            },
        }


def list_dataset_version_evidence(
    tenant_id: str,
    dataset_id: str,
    dataset_version_ids: list[str],
) -> dict[str, list[dict[str, Any]]]:
    """读取 Golden 证据锚点供 API 层按当前 KB 权限复核新鲜度。"""
    tenant_id = _tenant(tenant_id)
    version_ids = list(dict.fromkeys(dataset_version_ids))
    if not version_ids or len(version_ids) > 2:
        raise ValueError("版本证据查询必须指定 1 至 2 个 Golden 版本")

    with _transaction(tenant_id) as (_conn, cur):
        cur.execute(
            """
            SELECT v.dataset_version_id::text AS dataset_version_id,
                   i.case_id::text AS case_id, r.evidence_json
            FROM evaluation_v2.eval_dataset_versions AS v
            JOIN evaluation_v2.eval_dataset_items AS i
              ON i.tenant_id = v.tenant_id
             AND i.dataset_version_id = v.dataset_version_id
            JOIN evaluation_v2.eval_case_revisions AS r
              ON r.tenant_id = i.tenant_id
             AND r.case_id = i.case_id
             AND r.revision = i.case_revision
            WHERE v.tenant_id = %s AND v.dataset_id = %s
              AND v.dataset_version_id = ANY(%s::uuid[])
              AND v.status = 'published'
            ORDER BY v.dataset_version_id, i.ordinal
            """,
            (tenant_id, dataset_id, version_ids),
        )
        result: dict[str, list[dict[str, Any]]] = {version_id: [] for version_id in version_ids}
        for row in cur.fetchall():
            item = _dict(row)
            evidence = item["evidence_json"]
            if isinstance(evidence, str):
                evidence = json.loads(evidence)
            result[item["dataset_version_id"]].append({
                "case_id": item["case_id"],
                "evidence": evidence if isinstance(evidence, list) else [],
            })
        return result


def list_evaluator_versions(tenant_id: str) -> list[dict[str, Any]]:
    tenant_id = _tenant(tenant_id)
    with _transaction(tenant_id) as (_conn, cur):
        cur.execute(
            """
            SELECT evaluator_version_id, evaluator_key, version, evaluator_kind,
                   config_json, rubric_hash, status, created_by, published_by,
                   published_at, created_at
            FROM evaluation_v2.eval_evaluator_versions
            WHERE tenant_id = %s
            ORDER BY evaluator_key, created_at DESC
            """,
            (tenant_id,),
        )
        return [_dict(row) for row in cur.fetchall()]


def create_evaluator_version(
    tenant_id: str,
    actor: str,
    *,
    evaluator_key: str,
    version: str,
    evaluator_kind: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    tenant_id = _tenant(tenant_id)
    if not _VERSION_RE.fullmatch(version):
        raise ValueError("评分器版本必须符合 x.y.z")
    if not re.fullmatch(r"^[a-z][a-z0-9_-]{2,63}$", evaluator_key):
        raise ValueError("评分器标识格式无效")
    if evaluator_kind not in {"deterministic", "evidence", "llm_judge", "human"}:
        raise ValueError("评分器类型无效")
    digest = content_hash({"evaluator_key": evaluator_key, "version": version, "kind": evaluator_kind, "config": config})
    with _transaction(tenant_id) as (_conn, cur):
        cur.execute(
            """
            INSERT INTO evaluation_v2.eval_evaluator_versions
                (tenant_id, evaluator_key, version, evaluator_kind, config_json,
                 rubric_hash, created_by)
            VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s)
            RETURNING evaluator_version_id, evaluator_key, version, evaluator_kind,
                      config_json, rubric_hash, status, created_by, created_at
            """,
            (tenant_id, evaluator_key, version, evaluator_kind, canonical_json(config), digest, actor),
        )
        result = _dict(cur.fetchone())
        _record_audit(
            cur, tenant_id, actor, "evaluator.version_created", "evaluator_version",
            str(result["evaluator_version_id"]), after_hash=digest,
        )
        return result


def publish_evaluator_version(
    tenant_id: str, actor: str, evaluator_version_id: str
) -> dict[str, Any]:
    tenant_id = _tenant(tenant_id)
    with _transaction(tenant_id) as (_conn, cur):
        cur.execute(
            """
            UPDATE evaluation_v2.eval_evaluator_versions
            SET status = 'published', published_by = %s, published_at = now()
            WHERE tenant_id = %s AND evaluator_version_id = %s AND status = 'draft'
            RETURNING evaluator_version_id, evaluator_key, version, evaluator_kind,
                      config_json, rubric_hash, status, published_by, published_at
            """,
            (actor, tenant_id, evaluator_version_id),
        )
        result = _dict(cur.fetchone())
        if not result:
            raise RepositoryNotFoundError("评分器版本不存在或已发布")
        _record_audit(
            cur, tenant_id, actor, "evaluator.version_published", "evaluator_version",
            evaluator_version_id, after_hash=str(result["rubric_hash"]),
        )
        return result


def _validate_evaluator_refs(*args, **kwargs) -> None:
    fn = _delegate("_validate_evaluator_refs", _validate_evaluator_refs, _real_validate_evaluator_refs)
    return fn(*args, **kwargs)


def _real_validate_evaluator_refs(
    cur: Any,
    tenant_id: str,
    versions: list[dict[str, Any]],
    *,
    module: str = "rag",
) -> None:
    if not versions:
        raise ValueError("方案至少需要一个已发布的评分器版本")
    ids = [str(item.get("evaluator_version_id", "")) for item in versions]
    if any(not value for value in ids) or len(ids) != len(set(ids)):
        raise ValueError("评分器引用必须包含唯一 evaluator_version_id")
    cur.execute(
        """
        SELECT evaluator_version_id, evaluator_kind
        FROM evaluation_v2.eval_evaluator_versions
        WHERE tenant_id = %s AND evaluator_version_id = ANY(%s::uuid[]) AND status = 'published'
        FOR SHARE
        """,
        (tenant_id, ids),
    )
    rows = [_dict(row) for row in cur.fetchall()]
    if len(rows) != len(ids):
        raise RepositoryNotFoundError("方案引用的评分器版本不存在或尚未发布")
    from backend.evaluation_v2.capabilities import require_supported_runner_evaluators

    evaluator_kinds = {str(row["evaluator_version_id"]): str(row["evaluator_kind"]) for row in rows}
    require_supported_runner_evaluators(versions, evaluator_kinds, module=module)


def _normalize_suite_selection(selection: dict[str, Any]) -> tuple[dict[str, Any], list[str] | None]:
    if not isinstance(selection, dict) or set(selection) - {"case_ids"}:
        raise ValueError("当前只支持按已发布 Golden 的 case_ids 固定选题")
    selected_ids = selection.get("case_ids")
    if selected_ids is None:
        return {}, None
    if not isinstance(selected_ids, list) or not selected_ids or len(selected_ids) > 20_000:
        raise ValueError("case_ids 必须是 1 到 20000 个用例标识")
    try:
        normalized = [str(uuid.UUID(str(value))) for value in selected_ids]
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError("case_ids 必须是 UUID") from exc
    if len(normalized) != len(set(normalized)):
        raise ValueError("case_ids 不能重复")
    return {"case_ids": normalized}, normalized


def publish_dataset_version(
    tenant_id: str,
    actor: str,
    dataset_id: str,
    *,
    version: str,
    case_ids: list[tuple[str, int]],
) -> dict[str, Any]:
    tenant_id = _tenant(tenant_id)
    if not _VERSION_RE.fullmatch(version):
        raise ValueError("数据集版本必须符合 x.y.z")
    if not case_ids or len(case_ids) > 20_000:
        raise ValueError("发布版本需包含 1 到 20000 条已审核样本")
    if len({case_id for case_id, _ in case_ids}) != len(case_ids):
        raise ValueError("同一发布版本中不能重复选择样本")
    with _transaction(tenant_id) as (_conn, cur):
        cur.execute(
            "SELECT dataset_id, domain FROM evaluation_v2.eval_datasets WHERE tenant_id = %s AND dataset_id = %s AND status = 'active' FOR UPDATE",
            (tenant_id, dataset_id),
        )
        dataset = _dict(cur.fetchone())
        if dataset is None:
            raise RepositoryNotFoundError("数据集不存在")
        dataset_domain = validate_dataset_domain(str(dataset["domain"]))
        selected: list[dict[str, Any]] = []
        for case_id, revision in case_ids:
            cur.execute(
                """
                SELECT c.case_id, c.lifecycle_status, r.revision, r.input_json,
                       r.expected_json, r.evidence_json, r.metadata_json,
                       r.source_ref, r.content_hash
                FROM evaluation_v2.eval_cases AS c
                JOIN evaluation_v2.eval_case_revisions AS r
                  ON r.tenant_id = c.tenant_id AND r.case_id = c.case_id
                WHERE c.tenant_id = %s AND c.case_id = %s AND r.revision = %s
                FOR UPDATE OF c
                """,
                (tenant_id, case_id, revision),
            )
            row = _dict(cur.fetchone())
            if not row:
                raise ValueError("所选样本或修订不存在")
            if row["lifecycle_status"] not in {"approved", "published"}:
                raise ValueError("未审核通过的样本不能发布为 Golden")
            evidence = row["evidence_json"]
            if isinstance(evidence, str):
                evidence = json.loads(evidence)
            expected = row["expected_json"]
            if isinstance(expected, str):
                expected = json.loads(expected)
            if dataset_domain == "rag":
                if expected.get("behavior", "answer") == "answer":
                    if not evidence or not all(_valid_evidence(item) for item in evidence):
                        raise ValueError("回答样本证据缺失或已过期，不能发布")
                elif expected.get("behavior") == "refuse":
                    if expected.get("reason") not in {"no_evidence", "permission_denied"} or evidence:
                        raise ValueError("拒答样本必须指定原因且不能绑定原文证据")
                else:
                    raise ValueError("样本 behavior 不受支持")
            else:
                case_input = row["input_json"]
                if isinstance(case_input, str):
                    case_input = json.loads(case_input)
                validate_non_rag_case(dataset_domain, case_input, expected, evidence)
            selected.append(row)
        selected.sort(key=lambda row: str(row["case_id"]))
        manifest = {
            "schema_version": "evaluation.dataset.v2",
            "dataset_id": dataset_id,
            "version": version,
            "items": [
                {"case_id": str(row["case_id"]), "revision": row["revision"], "hash": row["content_hash"]}
                for row in selected
            ],
        }
        digest = content_hash(manifest)
        cur.execute(
            """
            INSERT INTO evaluation_v2.eval_dataset_versions
                (tenant_id, dataset_id, version, status, content_hash, manifest_json)
            VALUES (%s, %s, %s, 'draft', %s, %s::jsonb)
            RETURNING dataset_version_id
            """,
            (tenant_id, dataset_id, version, digest, canonical_json(manifest)),
        )
        dataset_version_id = cur.fetchone()["dataset_version_id"]
        for ordinal, row in enumerate(selected):
            cur.execute(
                """
                INSERT INTO evaluation_v2.eval_dataset_items
                    (tenant_id, dataset_version_id, case_id, case_revision, ordinal)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (tenant_id, dataset_version_id, row["case_id"], row["revision"], ordinal),
            )
        cur.execute(
            """
            UPDATE evaluation_v2.eval_cases
            SET lifecycle_status = 'published'
            WHERE tenant_id = %s
              AND case_id = ANY(%s::uuid[])
              AND lifecycle_status = 'approved'
            """,
            (tenant_id, [str(row["case_id"]) for row in selected]),
        )
        cur.execute(
            """
            UPDATE evaluation_v2.eval_dataset_versions
            SET status = 'published', published_by = %s, published_at = now()
            WHERE tenant_id = %s AND dataset_version_id = %s AND status = 'draft'
            RETURNING dataset_version_id, dataset_id, version, status, content_hash,
                      manifest_json, published_by, published_at
            """,
            (actor, tenant_id, dataset_version_id),
        )
        result = _dict(cur.fetchone())
        _record_audit(
            cur, tenant_id, actor, "dataset.version_published", "dataset_version",
            str(dataset_version_id), after_hash=digest,
            details={"dataset_id": dataset_id, "version": version, "case_count": len(selected)},
        )
        return result


def list_suites(tenant_id: str, *, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
    tenant_id = _tenant(tenant_id)
    with _transaction(tenant_id) as (_conn, cur):
        cur.execute(
            """
            SELECT s.suite_id, s.suite_key, s.name, s.module, s.status, s.created_at,
                   sv.suite_version_id, sv.version AS suite_version,
                   dv.dataset_id, dv.version AS dataset_version, dv.content_hash AS dataset_hash,
                   dv.manifest_json AS dataset_manifest,
                   sv.case_selection, sv.evaluator_versions, sv.metric_policy,
                   sv.security_policy, sv.minimum_valid_samples, sv.snapshot_hash
            FROM evaluation_v2.eval_suites AS s
            LEFT JOIN LATERAL (
                SELECT * FROM evaluation_v2.eval_suite_versions AS candidate
                WHERE candidate.tenant_id = s.tenant_id
                  AND candidate.suite_id = s.suite_id
                  AND candidate.status = 'published'
                ORDER BY candidate.created_at DESC, candidate.suite_version_id DESC
                LIMIT 1
            ) AS sv ON TRUE
            LEFT JOIN evaluation_v2.eval_dataset_versions AS dv
              ON dv.tenant_id = s.tenant_id AND dv.dataset_version_id = sv.dataset_version_id
            WHERE s.tenant_id = %s AND s.status = 'active'
            ORDER BY s.created_at DESC, s.suite_id
            LIMIT %s OFFSET %s
            """,
            (tenant_id, limit, offset),
        )
        return [_dict(row) for row in cur.fetchall()]


def create_suite(
    tenant_id: str,
    actor: str,
    *,
    suite_key: str,
    name: str,
    module: str,
    dataset_version_id: str,
    version: str,
    case_selection: dict[str, Any],
    evaluator_versions: list[dict[str, Any]],
    metric_policy: dict[str, Any],
    security_policy: dict[str, Any],
    minimum_valid_samples: int,
) -> dict[str, Any]:
    tenant_id = _tenant(tenant_id)
    from backend.evaluation_v2.capabilities import (
        require_dataset_domain_match,
        require_supported_runner_module,
    )

    module = require_supported_runner_module(module)
    if not _VERSION_RE.fullmatch(version):
        raise ValueError("方案版本必须符合 x.y.z")
    with _transaction(tenant_id) as (_conn, cur):
        _validate_evaluator_refs(cur, tenant_id, evaluator_versions, module=module)
        cur.execute(
            """
            SELECT dv.dataset_version_id, dv.manifest_json, dv.content_hash,
                   d.domain
            FROM evaluation_v2.eval_dataset_versions AS dv
            JOIN evaluation_v2.eval_datasets AS d
              ON d.tenant_id = dv.tenant_id AND d.dataset_id = dv.dataset_id
            WHERE dv.tenant_id = %s AND dv.dataset_version_id = %s
              AND dv.status = 'published'
            FOR SHARE OF dv, d
            """,
            (tenant_id, dataset_version_id),
        )
        dataset = _dict(cur.fetchone())
        if not dataset:
            raise RepositoryNotFoundError("已发布数据集版本不存在")
        require_dataset_domain_match(module, str(dataset["domain"]))
        selection, selected_ids = _normalize_suite_selection(case_selection or {})
        if selected_ids is not None:
            cur.execute(
                """
                SELECT COUNT(*) AS total
                FROM evaluation_v2.eval_dataset_items
                WHERE tenant_id = %s AND dataset_version_id = %s AND case_id = ANY(%s::uuid[])
                """,
                (tenant_id, dataset_version_id, selected_ids),
            )
            if int(cur.fetchone()["total"]) != len(selected_ids):
                raise ValueError("方案选题必须来自绑定的 Golden 版本")
            selected_count = len(selected_ids)
        else:
            cur.execute(
                "SELECT COUNT(*) AS total FROM evaluation_v2.eval_dataset_items WHERE tenant_id = %s AND dataset_version_id = %s",
                (tenant_id, dataset_version_id),
            )
            selected_count = int(cur.fetchone()["total"])
        if selected_count < minimum_valid_samples:
            raise ValueError("最小有效样本量不能大于方案选择的样本数")
        suite_config = {
            "suite_key": suite_key,
            "name": name,
            "module": module,
            "dataset_version_id": dataset_version_id,
            "version": version,
            "case_selection": selection,
            "evaluator_versions": evaluator_versions,
            "metric_policy": metric_policy,
            "security_policy": security_policy,
            "minimum_valid_samples": minimum_valid_samples,
            "dataset_hash": str(dataset["content_hash"]),
        }
        digest = content_hash(suite_config)
        cur.execute(
            """
            INSERT INTO evaluation_v2.eval_suites
                (tenant_id, suite_key, name, module, created_by)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING suite_id
            """,
            (tenant_id, suite_key, name, module, actor),
        )
        suite_id = cur.fetchone()["suite_id"]
        cur.execute(
            """
            INSERT INTO evaluation_v2.eval_suite_versions
                (tenant_id, suite_id, version, dataset_version_id, case_selection,
                 evaluator_versions, metric_policy, security_policy,
                 minimum_valid_samples, snapshot_hash)
            VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb,
                    %s::jsonb, %s, %s)
            RETURNING suite_version_id, suite_id, version, dataset_version_id,
                      status, snapshot_hash, created_at
            """,
            (
                tenant_id, suite_id, version, dataset_version_id,
                canonical_json(selection), canonical_json(evaluator_versions),
                canonical_json(metric_policy), canonical_json(security_policy),
                minimum_valid_samples, digest,
            ),
        )
        result = _dict(cur.fetchone())
        _record_audit(
            cur, tenant_id, actor, "suite.created", "suite", str(suite_id),
            after_hash=digest, details={"suite_version_id": str(result["suite_version_id"])},
        )
        return result


def append_suite_version(
    tenant_id: str,
    actor: str,
    suite_id: str,
    *,
    dataset_version_id: str,
    version: str,
    case_selection: dict[str, Any],
    evaluator_versions: list[dict[str, Any]],
    metric_policy: dict[str, Any],
    security_policy: dict[str, Any],
    minimum_valid_samples: int,
) -> dict[str, Any]:
    tenant_id = _tenant(tenant_id)
    from backend.evaluation_v2.capabilities import (
        require_dataset_domain_match,
        require_supported_runner_module,
    )

    if not _VERSION_RE.fullmatch(version):
        raise ValueError("方案版本必须符合 x.y.z")
    with _transaction(tenant_id) as (_conn, cur):
        cur.execute(
            """
            SELECT suite_id, suite_key, name, module
            FROM evaluation_v2.eval_suites
            WHERE tenant_id = %s AND suite_id = %s AND status = 'active'
            FOR UPDATE
            """,
            (tenant_id, suite_id),
        )
        suite = _dict(cur.fetchone())
        if not suite:
            raise RepositoryNotFoundError("评测方案不存在")
        require_supported_runner_module(str(suite["module"]))
        _validate_evaluator_refs(cur, tenant_id, evaluator_versions, module=str(suite["module"]))
        cur.execute(
            """
            SELECT dv.content_hash, d.domain
            FROM evaluation_v2.eval_dataset_versions AS dv
            JOIN evaluation_v2.eval_datasets AS d
              ON d.tenant_id = dv.tenant_id AND d.dataset_id = dv.dataset_id
            WHERE dv.tenant_id = %s AND dv.dataset_version_id = %s
              AND dv.status = 'published'
            FOR SHARE OF dv, d
            """,
            (tenant_id, dataset_version_id),
        )
        dataset = _dict(cur.fetchone())
        if not dataset:
            raise RepositoryNotFoundError("已发布数据集版本不存在")
        require_dataset_domain_match(str(suite["module"]), str(dataset["domain"]))
        selection, selected_ids = _normalize_suite_selection(case_selection or {})
        if selected_ids is not None:
            cur.execute(
                """
                SELECT COUNT(*) AS total
                FROM evaluation_v2.eval_dataset_items
                WHERE tenant_id = %s AND dataset_version_id = %s
                  AND case_id = ANY(%s::uuid[])
                """,
                (tenant_id, dataset_version_id, selected_ids),
            )
            if int(cur.fetchone()["total"]) != len(selected_ids):
                raise ValueError("方案选题必须来自绑定的 Golden 版本")
            selected_count = len(selected_ids)
        else:
            cur.execute(
                """
                SELECT COUNT(*) AS total
                FROM evaluation_v2.eval_dataset_items
                WHERE tenant_id = %s AND dataset_version_id = %s
                """,
                (tenant_id, dataset_version_id),
            )
            selected_count = int(cur.fetchone()["total"])
        if selected_count < minimum_valid_samples:
            raise ValueError("最小有效样本量不能大于方案选择的样本数")
        suite_config = {
            "suite_key": suite["suite_key"],
            "name": suite["name"],
            "module": suite["module"],
            "dataset_version_id": dataset_version_id,
            "dataset_hash": str(dataset["content_hash"]),
            "version": version,
            "case_selection": selection,
            "evaluator_versions": evaluator_versions,
            "metric_policy": metric_policy,
            "security_policy": security_policy,
            "minimum_valid_samples": minimum_valid_samples,
        }
        digest = content_hash(suite_config)
        cur.execute(
            """
            INSERT INTO evaluation_v2.eval_suite_versions
                (tenant_id, suite_id, version, dataset_version_id, case_selection,
                 evaluator_versions, metric_policy, security_policy,
                 minimum_valid_samples, snapshot_hash)
            VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb,
                    %s::jsonb, %s, %s)
            RETURNING suite_version_id, suite_id, version, dataset_version_id,
                      status, snapshot_hash, created_at
            """,
            (
                tenant_id, suite_id, version, dataset_version_id,
                canonical_json(selection), canonical_json(evaluator_versions),
                canonical_json(metric_policy), canonical_json(security_policy),
                minimum_valid_samples, digest,
            ),
        )
        result = _dict(cur.fetchone())
        _record_audit(
            cur, tenant_id, actor, "suite.version_created", "suite_version",
            str(result["suite_version_id"]), after_hash=digest,
            details={"suite_id": suite_id, "version": version},
        )
        return result


def publish_suite_version(
    tenant_id: str, actor: str, suite_version_id: str
) -> dict[str, Any]:
    tenant_id = _tenant(tenant_id)
    from backend.evaluation_v2.capabilities import (
        require_dataset_domain_match,
        require_supported_runner_module,
    )

    with _transaction(tenant_id) as (_conn, cur):
        cur.execute(
            """
            SELECT s.module, sv.evaluator_versions, d.domain
            FROM evaluation_v2.eval_suite_versions AS sv
            JOIN evaluation_v2.eval_suites AS s
              ON s.tenant_id = sv.tenant_id AND s.suite_id = sv.suite_id
            JOIN evaluation_v2.eval_dataset_versions AS dv
              ON dv.tenant_id = sv.tenant_id
             AND dv.dataset_version_id = sv.dataset_version_id
            JOIN evaluation_v2.eval_datasets AS d
              ON d.tenant_id = dv.tenant_id AND d.dataset_id = dv.dataset_id
            WHERE sv.tenant_id = %s AND sv.suite_version_id = %s
              AND sv.status = 'draft'
            FOR UPDATE OF sv, s
            """,
            (tenant_id, suite_version_id),
        )
        suite = cur.fetchone()
        if suite is None:
            raise RepositoryNotFoundError("方案版本不存在或已发布")
        require_supported_runner_module(str(suite["module"]))
        require_dataset_domain_match(str(suite["module"]), str(suite["domain"]))
        evaluator_refs = suite.get("evaluator_versions") or []
        if isinstance(evaluator_refs, str):
            evaluator_refs = json.loads(evaluator_refs)
        if not isinstance(evaluator_refs, list):
            raise ValueError("方案版本的评分器绑定格式无效")
        _validate_evaluator_refs(cur, tenant_id, evaluator_refs)
        cur.execute(
            """
            UPDATE evaluation_v2.eval_suite_versions
            SET status = 'published', published_by = %s, published_at = now()
            WHERE tenant_id = %s AND suite_version_id = %s AND status = 'draft'
            RETURNING suite_version_id, suite_id, version, dataset_version_id,
                      status, snapshot_hash, published_by, published_at
            """,
            (actor, tenant_id, suite_version_id),
        )
        result = _dict(cur.fetchone())
        if not result:
            raise RepositoryNotFoundError("方案版本不存在或已发布")
        _record_audit(
            cur, tenant_id, actor, "suite.version_published", "suite_version",
            str(suite_version_id), after_hash=str(result["snapshot_hash"]),
        )
        return result


