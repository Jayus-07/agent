"""customer_service/audit.py — 审计日志构建器

对齐 audit_logs 表列结构。
result ∈ {success, failure, denied, error}
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone


def build_audit_entry(
    user_id: str,
    action_type: str,
    result: str,
    target_type: str = "",
    target_id: str = "",
    detail: str = "",
    conversation_id: str | None = None,
) -> dict:
    """Build a single audit log entry dict aligned with audit_logs table columns."""
    return {
        "log_id": str(uuid.uuid4()),
        "user_id": user_id,
        "action_type": action_type,
        "result": result,
        "target_type": target_type,
        "target_id": target_id,
        "detail": detail,
        "conversation_id": conversation_id or "",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def append_audit(entries: list[dict], entry: dict) -> list[dict]:
    """Return a new list with the entry appended (no in-place mutation)."""
    return [*entries, entry]
