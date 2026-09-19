"""元数据动态规则的版本化治理服务。

规则写入只产生草稿；活动路由只读取已发布快照。服务层负责规范化、哈希、
审批门和进程内故障回退，底层存储只负责事务与并发锁。
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Callable, Iterable

from psycopg2 import errors as psycopg2_errors

from backend.infra.cache import get_cache
from backend.observability.metrics import metadata_rule_snapshot_total
from backend.rag.preprocessing.taxonomy_spec import get_taxonomy, normalize_doc_type
from backend.shared.logger import logger


@dataclass(frozen=True)
class RuleSnapshot:
    version: int
    status: str
    taxonomy_version: str
    rules_hash: str
    entries: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    actor: str = ""
    reason: str = ""
    approval_id: str = ""
    approved_by: str = ""
    effective_at: Any = None
    created_at: Any = None

    @classmethod
    def from_record(cls, record: "RuleSnapshot | dict[str, Any]") -> "RuleSnapshot":
        if isinstance(record, cls):
            return record
        raw_entries = record.get("entries") or record.get("items") or []
        normalized_entries: list[dict[str, Any]] = []
        for entry in raw_entries:
            if hasattr(entry, "items"):
                normalized_entries.append(dict(entry))
            else:
                normalized_entries.append(dict(entry))
        return cls(
            version=int(record.get("version") or record.get("snapshot_id")),
            status=str(record.get("status") or "draft"),
            taxonomy_version=str(record.get("taxonomy_version") or ""),
            rules_hash=str(record.get("rules_hash") or ""),
            entries=tuple(normalized_entries),
            actor=str(record.get("actor") or ""),
            reason=str(record.get("reason") or ""),
            approval_id=str(record.get("approval_id") or ""),
            approved_by=str(record.get("approved_by") or ""),
            effective_at=record.get("effective_at"),
            created_at=record.get("created_at"),
        )

    def to_dict(self) -> dict[str, Any]:
        def json_safe(value: Any) -> Any:
            if isinstance(value, (datetime, date)):
                return value.isoformat()
            if isinstance(value, dict):
                return {key: json_safe(item) for key, item in value.items()}
            if isinstance(value, (list, tuple)):
                return [json_safe(item) for item in value]
            return value

        return {
            "version": self.version,
            "status": self.status,
            "taxonomy_version": self.taxonomy_version,
            "rules_hash": self.rules_hash,
            "entries": [json_safe(dict(entry)) for entry in self.entries],
            "actor": self.actor,
            "reason": self.reason,
            "approval_id": self.approval_id,
            "approved_by": self.approved_by,
            "effective_at": json_safe(self.effective_at),
            "created_at": json_safe(self.created_at),
        }

    def to_legacy_mapping(self) -> dict[str, list[tuple[str, int]]]:
        mapping: dict[str, list[tuple[str, int]]] = {}
        for entry in self.entries:
            if not int(entry.get("enabled", 1)):
                continue
            doc_type = str(entry.get("doc_type") or "general")
            mapping.setdefault(doc_type, []).append(
                (str(entry.get("keyword") or ""), int(entry.get("weight") or 1))
            )
        return mapping


class MetadataRuleService:
    """规则快照治理门面，可注入 fake store 做纯单元测试。"""

    _MAX_KEYWORD_LENGTH = 128
    _MAX_CATEGORY_LENGTH = 128
    _MAX_REASON_LENGTH = 500

    def __init__(
        self,
        store=None,
        *,
        cache=None,
        approval_checker: Callable[[str], Any] | None = None,
    ) -> None:
        self._store = store
        self._cache_override = cache
        self._approval_checker = approval_checker
        self._active_snapshot: RuleSnapshot | None = None
        self._active_snapshot_at = 0.0
        self._lock = threading.RLock()

    @property
    def store(self):
        if self._store is None:
            from backend.rag.preprocessing.keyword_store import get_keyword_store

            self._store = get_keyword_store()
        return self._store

    def _cache(self):
        if self._cache_override is not None:
            return self._cache_override
        return get_cache("rag_metadata_rules", ttl=300)

    @staticmethod
    def _cache_key(rules_hash: str) -> str:
        return f"metadata:rules:snapshot:{rules_hash}"

    @staticmethod
    def _hash_entries(entries: Iterable[dict[str, Any]]) -> str:
        canonical = json.dumps(
            list(entries), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _is_known_doc_type(raw: str, normalized: str) -> bool:
        return raw == "general" or normalized != "general"

    def _normalize_entries(self, entries: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        taxonomy = get_taxonomy()
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw_entry in entries:
            if not isinstance(raw_entry, dict):
                raise ValueError("rule entry must be an object")
            keyword = str(raw_entry.get("keyword") or "").strip()
            if not keyword:
                raise ValueError("keyword must not be empty")
            if len(keyword) > self._MAX_KEYWORD_LENGTH:
                raise ValueError("keyword length exceeds limit")
            keyword_key = keyword.casefold()
            if keyword_key in seen:
                raise ValueError(f"duplicate keyword: {keyword}")
            seen.add(keyword_key)

            raw_doc_type = str(raw_entry.get("doc_type") or "general").strip().lower()
            doc_type = normalize_doc_type(raw_doc_type)
            if not self._is_known_doc_type(raw_doc_type, doc_type):
                raise ValueError(f"doc_type out of taxonomy: {raw_doc_type}")
            if doc_type not in taxonomy.doc_types:
                raise ValueError(f"doc_type out of taxonomy: {doc_type}")

            try:
                weight = int(raw_entry.get("weight", 1))
            except (TypeError, ValueError) as exc:
                raise ValueError("weight must be an integer from 1 to 10") from exc
            if not 1 <= weight <= 10:
                raise ValueError("weight must be between 1 and 10")

            category = str(raw_entry.get("category") or "").strip()
            if len(category) > self._MAX_CATEGORY_LENGTH:
                raise ValueError("category length exceeds limit")
            enabled = int(raw_entry.get("enabled", 1))
            if enabled not in (0, 1):
                raise ValueError("enabled must be 0 or 1")
            result.append({
                "keyword": keyword,
                "doc_type": doc_type,
                "category": category,
                "weight": weight,
                "enabled": enabled,
            })
        result.sort(
            key=lambda item: (
                item["keyword"].casefold(),
                item["doc_type"],
                item["category"],
                item["weight"],
                item["enabled"],
            )
        )
        return result

    def create_rule_draft(
        self,
        entries: Iterable[dict[str, Any]],
        actor: str,
        reason: str,
    ) -> RuleSnapshot:
        if not str(actor or "").strip():
            raise ValueError("actor is required")
        reason = str(reason or "").strip()
        if not reason:
            raise ValueError("reason is required")
        if len(reason) > self._MAX_REASON_LENGTH:
            raise ValueError("reason length exceeds limit")
        normalized = self._normalize_entries(entries)
        taxonomy_version = get_taxonomy().version
        rules_hash = self._hash_entries(normalized)
        record = self.store.create_rule_snapshot(
            entries=normalized,
            taxonomy_version=taxonomy_version,
            rules_hash=rules_hash,
            actor=str(actor).strip(),
            reason=reason,
            status="draft",
        )
        snapshot = RuleSnapshot.from_record(record)
        metadata_rule_snapshot_total.labels(result="draft").inc()
        return snapshot

    def _approval_is_valid(self, approval_id: str) -> bool:
        if not approval_id:
            return False
        if self._approval_checker is not None:
            return bool(self._approval_checker(approval_id))
        checker = getattr(self.store, "is_rule_approval_approved", None)
        if checker is None:
            raise PermissionError("rule approval checker is not configured")
        return bool(checker(approval_id))

    def _invalidate_active_cache(self) -> None:
        with self._lock:
            self._active_snapshot = None
            self._active_snapshot_at = 0.0
            invalidate_store_cache = getattr(self._store, "invalidate_rule_cache", None)
            if invalidate_store_cache is not None:
                invalidate_store_cache()
            try:
                self._cache().delete("metadata:rules:active")
            except Exception as exc:
                logger.debug(f"[MetaRule] active snapshot cache invalidation failed: {exc}")

    def publish_rule_snapshot(
        self,
        snapshot_id: int,
        approval_id: str,
        actor: str,
    ) -> RuleSnapshot:
        if not self._approval_is_valid(str(approval_id or "")):
            raise PermissionError("approved approval_id is required")
        record = self.store.publish_rule_snapshot(
            int(snapshot_id), str(approval_id), str(actor or "").strip()
        )
        snapshot = RuleSnapshot.from_record(record)
        self._invalidate_active_cache()
        metadata_rule_snapshot_total.labels(result="published").inc()
        return snapshot

    def rollback_rule_snapshot(
        self,
        target_version: int,
        actor: str,
        reason: str = "manual rollback",
    ) -> RuleSnapshot:
        reason = str(reason or "").strip()
        if not reason:
            raise ValueError("reason is required")
        try:
            record = self.store.rollback_rule_snapshot(
                int(target_version), str(actor or "").strip(), reason
            )
        except TypeError:
            record = self.store.rollback_rule_snapshot(
                int(target_version), str(actor or "").strip()
            )
        snapshot = RuleSnapshot.from_record(record)
        self._invalidate_active_cache()
        metadata_rule_snapshot_total.labels(result="rolled_back").inc()
        return snapshot

    def get_active_snapshot(self) -> RuleSnapshot:
        from backend.config.rag import METADATA_RULE_LOCAL_CACHE_SECONDS

        with self._lock:
            if (
                self._active_snapshot is not None
                and time.monotonic() - self._active_snapshot_at
                < METADATA_RULE_LOCAL_CACHE_SECONDS
            ):
                return self._active_snapshot
        try:
            record = self.store.get_active_rule_snapshot()
            snapshot = RuleSnapshot.from_record(record)
            try:
                cache = self._cache()
                cached = cache.get_json(self._cache_key(snapshot.rules_hash))
                if cached:
                    snapshot = RuleSnapshot.from_record(cached)
                else:
                    cache.set_json(
                        self._cache_key(snapshot.rules_hash), snapshot.to_dict()
                    )
            except Exception as cache_exc:
                # 缓存只优化读取，不应把治理表的可用性变成缓存可用性。
                logger.debug(f"[MetaRule] snapshot cache unavailable: {cache_exc}")
            with self._lock:
                self._active_snapshot = snapshot
                self._active_snapshot_at = time.monotonic()
            return snapshot
        except Exception as exc:
            with self._lock:
                if self._active_snapshot is not None:
                    logger.warning(f"[MetaRule] active snapshot read failed, using last good: {exc}")
                    metadata_rule_snapshot_total.labels(result="read_fallback").inc()
                    return self._active_snapshot
            raise

    def get_rules_by_doc_type(self) -> dict[str, list[tuple[str, int]]]:
        try:
            return self.get_active_snapshot().to_legacy_mapping()
        except psycopg2_errors.UndefinedTable as exc:
            # 025 迁移尚未部署时保留旧读兼容；迁移存在后不会走未版本化表。
            legacy_reader = getattr(self.store, "get_rules_by_doc_type", None)
            if legacy_reader is None:
                raise
            logger.warning(f"[MetaRule] versioned snapshot unavailable, legacy read: {exc}")
            return legacy_reader()
        except LookupError:
            # 治理表已存在但没有 published 快照时必须 fail closed，不能把
            # 未版本化 keyword_rules 当成线上活动规则。
            return {}

    def list_active_entries(self) -> list[dict[str, Any]]:
        """返回管理面兼容行；id 只用于展示，不参与版本写入。"""
        snapshot = self.get_active_snapshot()
        result: list[dict[str, Any]] = []
        for index, entry in enumerate(snapshot.entries, start=1):
            result.append({
                "id": f"{snapshot.version}:{index}",
                "keyword": entry.get("keyword", ""),
                "doc_type": entry.get("doc_type", "general"),
                "category": entry.get("category", ""),
                "weight": int(entry.get("weight", 1)),
                "enabled": int(entry.get("enabled", 1)),
                "source": "snapshot",
                "version": snapshot.version,
                "rules_hash": snapshot.rules_hash,
                "created_at": snapshot.created_at,
                "updated_at": snapshot.effective_at or snapshot.created_at,
            })
        return result

    def list_rule_snapshots(self, limit: int = 20) -> list[RuleSnapshot]:
        """列出版本元数据，不读取 entries，避免管理页无界加载规则内容。"""
        reader = getattr(self.store, "list_rule_snapshots", None)
        if reader is None:
            return []
        return [RuleSnapshot.from_record(row) for row in reader(limit)]


_service: MetadataRuleService | None = None
_service_lock = threading.Lock()


def get_metadata_rule_service() -> MetadataRuleService:
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                _service = MetadataRuleService()
    return _service


__all__ = [
    "RuleSnapshot",
    "MetadataRuleService",
    "get_metadata_rule_service",
]
