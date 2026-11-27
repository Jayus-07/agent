"""backend/evaluation_v2/repositories/base.py — 基础异常定义、事务装饰器、审计埋点与通用工具"""
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


def _delegate(name: str, self_func, fallback):
    try:
        import backend.evaluation_v2.repository as repo_mod
        hook = getattr(repo_mod, name, None)
        if hook is not None and hook is not self_func:
            return hook
    except Exception:
        pass
    return fallback


def _transaction(tenant_id: str):
    """在一个租户化事务中执行 SQL；role 和 tenant 设置均为 transaction-local。"""
    fn = _delegate("_transaction", _transaction, _real_transaction)
    return fn(tenant_id)


@contextmanager
def _real_transaction(tenant_id: str) -> Iterator[tuple[Any, Any]]:
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


def _record_audit(*args, **kwargs) -> None:
    fn = _delegate("_record_audit", _record_audit, _real_record_audit)
    return fn(*args, **kwargs)


def _real_record_audit(
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


