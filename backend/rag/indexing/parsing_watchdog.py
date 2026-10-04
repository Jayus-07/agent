"""解析阶段超时记录的选择规则。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any


def _as_utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.strptime(value.strip()[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def select_stale_parsing(
    rows: list[dict[str, Any]],
    *,
    now: datetime | None = None,
    timeout_seconds: int = 3600,
) -> list[dict[str, Any]]:
    """返回超过阈值的 parsing 行副本，按路径稳定排序。"""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    cutoff = now - timedelta(seconds=timeout_seconds)
    return sorted(
        [
            dict(row)
            for row in rows
            if row.get("status") == "parsing"
            and (updated := _as_utc(row.get("updated_at"))) is not None
            and updated < cutoff
        ],
        key=lambda row: str(row.get("file_path", "")),
    )
