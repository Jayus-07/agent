"""评测 V2 的 PostgreSQL Repository。

所有入口都必须传入从可信身份解析的 tenant_id。查询同时显式过滤租户，且
在事务内切换到不可绕过 RLS 的数据库角色，避免复用超级用户连接时失去隔离。
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from contextlib import contextmanager
from typing import Any, Iterator

from psycopg2.extras import RealDictCursor

from backend.evaluation_v2.domain_contracts import (
    validate_dataset_domain,
    validate_non_rag_case,
)

_TENANT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")


class RepositoryConflictError(RuntimeError):
    """资源已存在、幂等键冲突或乐观锁失败。"""


class RepositoryNotFoundError(LookupError):
    """资源不存在或不属于当前租户。"""


class RepositoryExportLimitError(ValueError):
    """Golden 导出超过服务端保护上限。"""


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def content_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def normalize_human_rubric(config: Any) -> dict[str, Any]:
    """校验人工评分标准；权重必须显式配置且合计为 1。"""
    if not isinstance(config, dict) or set(config) != {"criteria"}:
        raise ValueError("人工评分器配置必须且只能包含 criteria")
    criteria = config["criteria"]
    if not isinstance(criteria, list) or not 1 <= len(criteria) <= 20:
        raise ValueError("人工评分 Rubric 需要 1 到 20 个评分维度")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    total_weight = 0.0
    for criterion in criteria:
        if not isinstance(criterion, dict) or set(criterion) != {"key", "label", "weight"}:
            raise ValueError("每个评分维度必须包含 key、label、weight")
        key = str(criterion["key"])
        label = str(criterion["label"]).strip()
        weight = criterion["weight"]
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", key) or key in seen:
            raise ValueError("人工评分维度 key 无效或重复")
        if not label or len(label) > 80:
            raise ValueError("人工评分维度名称长度必须为 1 到 80 个字符")
        if isinstance(weight, bool) or not isinstance(weight, (int, float)) or not math.isfinite(weight) or not 0 < weight <= 1:
            raise ValueError("人工评分维度权重必须大于 0 且不超过 1")
        seen.add(key)
        total_weight += float(weight)
        normalized.append({"key": key, "label": label, "weight": float(weight)})
    if not math.isclose(total_weight, 1.0, rel_tol=0, abs_tol=1e-6):
        raise ValueError("人工评分维度权重之和必须为 1")
    return {"criteria": normalized}


def _tenant(value: str) -> str:
    tenant_id = (value or "").strip()
    if not _TENANT_RE.fullmatch(tenant_id):
        raise PermissionError("缺少有效的可信租户身份")
    return tenant_id


@contextmanager
def _transaction(tenant_id: str) -> Iterator[tuple[Any, Any]]:
    """在一个租户化事务中执行 SQL；role 和 tenant 设置均为 transaction-local。"""
    trusted_tenant = _tenant(tenant_id)
    from backend.config.database import OBS_DB_PG_CONFIG
    from backend.infra.db import engine_for

    conn = engine_for(OBS_DB_PG_CONFIG).raw_connection()
    cur = None
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("BEGIN")
        cur.execute("SET LOCAL ROLE evaluation_v2_app")
        cur.execute("SELECT set_config('app.tenant_id', %s, true)", (trusted_tenant,))
        yield conn, cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        if cur is not None:
            cur.close()
        conn.close()


def _record_audit(
    cur: Any,
    tenant_id: str,
    actor: str,
    action: str,
    target_type: str,
    target_id: str,
    *,
    before_hash: str = "",
    after_hash: str = "",
    details: dict[str, Any] | None = None,
) -> None:
    cur.execute(
        """
        INSERT INTO evaluation_v2.eval_audit_events
            (tenant_id, actor_user_id, action, target_type, target_id,
             before_hash, after_hash, details_json)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
        """,
        (
            tenant_id, actor, action, target_type, target_id,
            before_hash, after_hash, canonical_json(details or {}),
        ),
    )


def _dict(row: Any) -> dict[str, Any]:
    return dict(row) if row is not None else {}


def list_datasets(tenant_id: str, *, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
    tenant_id = _tenant(tenant_id)
    with _transaction(tenant_id) as (_conn, cur):
        cur.execute(
            """
            SELECT d.dataset_id, d.dataset_key, d.name, d.domain, d.description,
                   d.owner_user_id, d.status, d.created_at, d.updated_at,
                   COUNT(DISTINCT v.dataset_version_id) FILTER (WHERE v.status = 'published') AS version_count
            FROM evaluation_v2.eval_datasets AS d
            LEFT JOIN evaluation_v2.eval_dataset_versions AS v
              ON v.tenant_id = d.tenant_id AND v.dataset_id = d.dataset_id
            WHERE d.tenant_id = %s AND d.status = 'active'
            GROUP BY d.dataset_id
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
                   COUNT(i.case_id)::integer AS case_count
            FROM evaluation_v2.eval_dataset_versions AS v
            LEFT JOIN evaluation_v2.eval_dataset_items AS i
              ON i.tenant_id = v.tenant_id AND i.dataset_version_id = v.dataset_version_id
            WHERE v.tenant_id = %s AND v.dataset_id = %s AND v.status = 'published'
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


def _validate_evaluator_refs(
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


def list_candidates(
    tenant_id: str, *, review_status: str | None = None, limit: int = 50, offset: int = 0
) -> list[dict[str, Any]]:
    tenant_id = _tenant(tenant_id)
    with _transaction(tenant_id) as (_conn, cur):
        cur.execute(
            """
            SELECT candidate_id, source_kind, payload_json, privacy_status, review_status,
                   revision, duplicate_key, promoted_case_id, created_by, updated_by,
                   created_at, updated_at
            FROM evaluation_v2.eval_candidates
            WHERE tenant_id = %s AND (%s::text IS NULL OR review_status = %s)
            ORDER BY created_at DESC, candidate_id
            LIMIT %s OFFSET %s
            """,
            (tenant_id, review_status, review_status, limit, offset),
        )
        return [_dict(row) for row in cur.fetchall()]


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


def _valid_evidence(item: dict[str, Any]) -> bool:
    if not isinstance(item, dict) or item.get("stale") is True:
        return False
    return bool(
        item.get("kb_id")
        and item.get("doc_id")
        and item.get("doc_version")
        and item.get("anchor_id")
        and item.get("verified_by")
        and item.get("verified_at")
        and re.fullmatch(r"[a-f0-9]{64}", str(item.get("anchor_text_hash", "")))
        and re.fullmatch(r"[a-f0-9]{64}", str(item.get("source_content_hash", "")))
    )


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


def get_run(tenant_id: str, run_id: str) -> dict[str, Any]:
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


def list_runs(tenant_id: str, *, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
    tenant_id = _tenant(tenant_id)
    with _transaction(tenant_id) as (_conn, cur):
        cur.execute(
            """
            SELECT r.run_id, r.suite_version_id, s.suite_key, s.name AS suite_name,
                   s.module,
                   sv.version AS suite_version, r.job_status, r.gate_verdict,
                   r.cost_amount, r.cost_currency,
                   r.total_cases, r.completed_cases, r.passed_cases,
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


def list_run_results(
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


def list_audit_events(
    tenant_id: str, *, target_type: str = "", target_id: str = "",
    limit: int = 100, offset: int = 0,
) -> list[dict[str, Any]]:
    tenant_id = _tenant(tenant_id)
    with _transaction(tenant_id) as (_conn, cur):
        cur.execute(
            """
            SELECT event_id, actor_user_id, action, target_type, target_id,
                   before_hash, after_hash, details_json, created_at
            FROM evaluation_v2.eval_audit_events
            WHERE tenant_id = %s
              AND (%s = '' OR target_type = %s)
              AND (%s = '' OR target_id = %s)
            ORDER BY created_at DESC, event_id DESC
            LIMIT %s OFFSET %s
            """,
            (tenant_id, target_type, target_type, target_id, target_id, limit, offset),
        )
        return [_dict(row) for row in cur.fetchall()]


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
        cases = [
            {
                "case_id": case_id,
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

