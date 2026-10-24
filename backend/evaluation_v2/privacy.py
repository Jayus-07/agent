"""候选资产最小化和常见敏感值脱敏。"""

from __future__ import annotations

import re
from typing import Any

_PATTERNS = (
    (re.compile(r"\b1[3-9]\d{9}\b"), "[PHONE_REDACTED]"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "[EMAIL_REDACTED]"),
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9._~-]+"), "Bearer [TOKEN_REDACTED]"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"), "[SECRET_REDACTED]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[SECRET_REDACTED]"),
)
_SENSITIVE_KEYS = (
    "secret", "password", "api_key", "access_key", "access_token",
    "refresh_token", "authorization", "手机号", "phone", "email",
)


def sanitize_candidate_payload(value: Any) -> tuple[Any, bool]:
    """递归遮蔽常见 PII/凭据；返回脱敏值和是否发生变更。"""
    if isinstance(value, dict):
        changed = False
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            if any(marker in str(key).lower() for marker in _SENSITIVE_KEYS):
                sanitized[str(key)] = "[REDACTED]"
                changed = True
                continue
            new_item, item_changed = sanitize_candidate_payload(item)
            sanitized[str(key)] = new_item
            changed = changed or item_changed
        return sanitized, changed
    if isinstance(value, list):
        entries = [sanitize_candidate_payload(item) for item in value]
        return [item for item, _changed in entries], any(changed for _item, changed in entries)
    if isinstance(value, str):
        result = value
        for pattern, replacement in _PATTERNS:
            result = pattern.sub(replacement, result)
        return result, result != value
    return value, False

