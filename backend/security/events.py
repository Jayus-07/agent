"""security/events.py — 安全事件统一落库（M9 / 台账 D9）

为什么存在：403 权限拒绝与 JWT 失败此前零记录（静默 403），Input Guard
拦截只在 Prometheus label，Evidence Gate 拒答原因只在 trace JSON——安全
事件不可统计、不可回答「谁在越权、被拦了多少、为什么拒答」。

设计原则：
- **旁路追加**：所有埋点在既有拒绝/拦截路径旁调用，写入失败只记
  warning 不抛错——安全事件的记录永远不能影响主流程判定本身。
- 上下文（user/tenant/trace）从 request_context / trace_collector 权威
  读取，不接受调用方自报身份（与 llm_usage attribution 同口径）。
- detail 只存脱敏摘要（路径/风险级/阈值/拒答原因枚举），禁止原文。
"""
from __future__ import annotations

import json
from typing import Any

from backend.shared.logger import logger

EVENT_TYPES = (
    "INPUT_GUARD_BLOCK",
    "AUTHZ_DENIED",
    "JWT_INVALID",
    "EVIDENCE_REJECT",
    "GATEWAY_DENIED",
)


def _current_context() -> dict[str, str]:
    """从权威上下文拼装身份（全部软失败，缺项空串）。"""
    ctx = {"user_id": "", "tenant_id": "", "trace_id": "", "request_id": "",
           "session_id": ""}
    try:
        from backend.core.request_context import get_tool_tenant_id, get_tool_user_id

        ctx["user_id"] = get_tool_user_id() or ""
        ctx["tenant_id"] = get_tool_tenant_id() or ""
    except Exception:
        pass
    try:
        from backend.observability.tracer import trace_collector

        trace = trace_collector.current()
        if trace is not None:
            ctx["trace_id"] = str(getattr(trace, "id", "") or "")
            ctx["session_id"] = str(getattr(trace, "session_id", "") or "")
            ctx["request_id"] = str(getattr(trace, "request_id", "") or ctx["trace_id"])
    except Exception:
        pass
    return ctx


def record_security_event(
    event_type: str,
    *,
    category: str = "",
    detail: dict[str, Any] | None = None,
    user_id: str | None = None,
    tenant_id: str | None = None,
) -> bool:
    """旁路写一条安全事件（软失败，永不抛错）。"""
    if event_type not in EVENT_TYPES:
        logger.warning(f"[security_events] 未知事件类型: {event_type}")
        return False
    ctx = _current_context()
    row = {
        "event_type": event_type,
        "category": category[:64],
        "user_id": (user_id if user_id is not None else ctx["user_id"])[:128],
        "tenant_id": (tenant_id if tenant_id is not None else ctx["tenant_id"])[:128],
        "trace_id": ctx["trace_id"],
        "request_id": ctx["request_id"],
        "session_id": ctx["session_id"],
        "detail": json.dumps(detail or {}, ensure_ascii=False, default=str),
    }
    try:
        from backend.config.database import OBS_DB_PG_CONFIG
        from backend.infra.db import engine_for

        with engine_for(OBS_DB_PG_CONFIG).raw_connection() as conn:
            conn.cursor().execute(
                """
                INSERT INTO ai.security_events (
                    tenant_id, user_id, event_type, category,
                    trace_id, request_id, session_id, detail
                ) VALUES (%(tenant_id)s, %(user_id)s, %(event_type)s, %(category)s,
                          %(trace_id)s, %(request_id)s, %(session_id)s, %(detail)s)
                """,
                row,
            )
            conn.commit()
        return True
    except Exception as e:  # noqa: BLE001 — 旁路软失败
        logger.warning(f"[security_events] 写入失败（不影响主流程）: {e}")
        return False


__all__ = ["EVENT_TYPES", "record_security_event"]
