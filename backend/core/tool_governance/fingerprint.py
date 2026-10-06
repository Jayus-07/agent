"""Tool 操作语义指纹。"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def normalized_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    """递归清理可哈希参数，保持列表顺序并去掉无意义字符串空白。"""

    def normalize(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: normalize(value[key]) for key in sorted(value)}
        if isinstance(value, list):
            return [normalize(item) for item in value]
        if isinstance(value, str):
            return value.strip()
        return value

    return normalize(arguments)


def operation_fingerprint(
    capability: str,
    operation: str,
    arguments: dict[str, Any],
    *,
    tenant_id: str = "",
    user_id: str = "",
    impact: dict[str, Any] | None = None,
) -> str:
    payload = {
        "capability": capability,
        "operation": operation,
        "arguments": normalized_arguments(arguments),
        "tenant_id": tenant_id,
        "user_id": user_id,
        "impact": normalized_arguments(impact or {}),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


__all__ = ["normalized_arguments", "operation_fingerprint"]
