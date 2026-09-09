"""Shared Trace / Span → frontend DTO adapters.

Extracted from ``observability.py`` so that both the existing observability
API and the new CS admin API can produce identical DTOs.
"""
from backend.config.observability import TRACE_DETAIL_LEVEL


def to_span_dto(s, all_spans: list, total_ms: int) -> dict:
    """Span / dict → frontend Span DTO. Compatible with TraceRecord Span and SQLite dict."""
    get = lambda k, d=None: s.get(k, d) if isinstance(s, dict) else getattr(s, k, d)
    span_id = get("span_id", "")
    dto: dict = {
        "id": span_id,
        "type": get("type", ""),
        "name": get("name", ""),
        "parent_id": get("parent_id"),
        "status": get("status", "success"),
        "start_time": get("start_time", ""),
        "end_time": get("end_time", ""),
        "duration_ms": get("duration_ms", 0),
        "duration_ratio": get("duration_ms", 0) / total_ms if total_ms else 0,
        "metrics": get("metrics", {}),
        "children": [
            (c.get("span_id") if isinstance(c, dict) else c.span_id)
            for c in all_spans
            if (c.get("parent_id") if isinstance(c, dict) else c.parent_id) == span_id
        ],
        "input": get("input"),
        "output": get("output"),
        "events": get("events", []),
        "errors": get("errors", []),
    }
    if get("type", "") == "llm_call":
        m = get("metrics", {})
        inp = get("input") or {}
        out = get("output") or {}
        dto["llm_call"] = {
            "model": m.get("model_name", "") if isinstance(m, dict) else "",
            "temperature": m.get("temperature", 0) if isinstance(m, dict) else 0,
            "prompt_tokens": m.get("prompt_tokens", 0) if isinstance(m, dict) else 0,
            "completion_tokens": m.get("completion_tokens", 0) if isinstance(m, dict) else 0,
            "cost_usd": m.get("cost_usd", 0) if isinstance(m, dict) else 0,
            "prompt_text": (inp.get("prompt", "") if isinstance(inp, dict) else ""),
            "response_text": (out.get("response", "") if isinstance(out, dict) else ""),
        }
    return dto


def to_trace_dto(t, detail_level: str | None = None) -> dict:
    """TraceRecord / dict → frontend TraceRecord DTO."""
    get = lambda k, d=None: t.get(k, d) if isinstance(t, dict) else getattr(t, k, d)
    total_ms = get("duration_ms", 0)
    sla_ms = get("sla_threshold_ms", 10000) or 10000
    all_spans = get("spans", [])
    has_error = any(
        (s.get("status") if isinstance(s, dict) else s.status) == "error"
        for s in all_spans
    )
    stored_status = get("status", "")
    if not stored_status:
        stored_status = "error" if has_error else ("running" if total_ms == 0 else "success")
    dto = {
        "id": get("id", ""),
        "timestamp": get("timestamp", ""),
        "session_id": get("session_id", ""),
        "question": get("question", ""),
        "answer_preview": get("answer_preview", ""),
        "answer_len": get("answer_len", 0),
        "duration_ms": total_ms,
        "model": (
            get("model", {})
            if isinstance(get("model", {}), dict)
            else {"name": get("model", ""), "provider": get("provider", "")}
        ),
        "usage": get("usage", {}),
        "cost_usd": get("cost_usd", 0),
        "error": get("error", {}),
        "metadata": get("metadata", {}),
        "status": stored_status,
        "workflow_name": get("workflow_name", ""),
        "root_span_id": get("root_span_id", ""),
        "spans": [to_span_dto(s, all_spans, total_ms) for s in all_spans],
        "sla": {"threshold_ms": sla_ms, "breached": total_ms > 0 and total_ms > sla_ms},
        "parent_id": get("parent_id"),
        "children_ids": get("children_ids", []),
        "graph": get("graph"),
        "tags": get("tags", {}),
    }
    level = detail_level or TRACE_DETAIL_LEVEL
    if level != "full":
        from backend.observability.redaction import redact_trace_dto
        dto = redact_trace_dto(dto, level)
    return dto


def stored_dict_to_dto(d: dict, detail_level: str | None = None) -> dict:
    """SQLite stored trace dict → frontend-compatible DTO (spans stripped)."""
    duration = d.get("duration_ms", 0)
    sla_ms = d.get("sla_threshold_ms", 10000) or 10000
    dto = {
        "id": d.get("id", ""),
        "timestamp": d.get("timestamp", ""),
        "session_id": d.get("session_id", ""),
        "question": d.get("question", ""),
        "answer_preview": d.get("answer_preview", ""),
        "answer_len": d.get("answer_len", 0),
        "duration_ms": d.get("duration_ms", 0),
        "model": d.get("model", {}),
        "usage": d.get("usage", {}),
        "cost_usd": d.get("cost_usd", 0),
        "error": d.get("error", {}),
        "metadata": d.get("metadata", {}),
        "status": d.get("status", "success"),
        "workflow_name": d.get("workflow_name", ""),
        "root_span_id": d.get("root_span_id", ""),
        "spans": [],
        "sla": {"threshold_ms": sla_ms, "breached": duration > 0 and duration > sla_ms},
        "parent_id": d.get("parent_id"),
        "children_ids": d.get("children_ids", []),
        "graph": None,
        "tags": d.get("tags", {}),
    }
    level = detail_level or TRACE_DETAIL_LEVEL
    if level != "full":
        from backend.observability.redaction import redact_trace_dto
        dto = redact_trace_dto(dto, level)
    return dto
