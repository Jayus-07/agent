"""共享的向量索引与运行时 embedding 身份判定。"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any


_UNKNOWN_VALUES = {"", "unknown", "(unknown)"}
_IDENTITY_FIELDS = (
    ("model", "index_model_missing", "runtime_model_missing", "model_mismatch"),
    ("provider", "index_provider_missing", "runtime_provider_missing", "provider_mismatch"),
    ("revision", "index_fingerprint_missing", "runtime_fingerprint_missing", "fingerprint_mismatch"),
)


def _identity_value(identity: Mapping[str, Any], key: str) -> str:
    value = str(identity.get(key) or "").strip()
    return "" if value.lower() in _UNKNOWN_VALUES else value


def embedding_identity_missing_reasons(identity: Mapping[str, Any]) -> list[str]:
    """列出无法登记新向量索引的运行时身份字段。"""
    return [
        runtime_missing
        for key, _, runtime_missing, _ in _IDENTITY_FIELDS
        if not _identity_value(identity, key)
    ]


def embedding_identity_mismatch_reasons(
    index_identity: Mapping[str, Any],
    runtime_identity: Mapping[str, Any],
    *,
    has_vectors: bool,
) -> list[str]:
    """返回身份不兼容原因；有向量时，任何身份字段缺失都不能按兼容放行。"""
    if not has_vectors:
        return []

    reasons: list[str] = []
    for key, index_missing, runtime_missing, mismatch in _IDENTITY_FIELDS:
        index_value = _identity_value(index_identity, key)
        runtime_value = _identity_value(runtime_identity, key)
        if not index_value:
            reasons.append(index_missing)
        elif not runtime_value:
            reasons.append(runtime_missing)
        elif index_value != runtime_value:
            reasons.append(mismatch)
    return reasons
