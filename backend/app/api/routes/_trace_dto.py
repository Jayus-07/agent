"""Shared Trace / Span → frontend DTO adapters.

Extracted from ``observability.py`` so that both the existing observability
API and the new CS admin API can produce identical DTOs.
"""
from backend.config.observability import TRACE_DETAIL_LEVEL


def backfill_usage_from_llm_store(data) -> None:
    """读取时回填：历史 trace 的 usage / model / cost 以 llm_usage 明细补齐。

    finish() 时的回填只覆盖新写入的 trace；存量 trace 落库时采集链尚未修好，
    usage 为空、model 为空。详情接口查询成本为一次索引查询，读时补齐即可
    让历史数据在前端正确呈现（真 0 与未采集语义由前端处理）。
    就地修改，无返回值；任何失败静默跳过（回填是增益，不是依赖）。
    """
    try:
        get = (lambda k, d=None: data.get(k, d)) if isinstance(data, dict) \
            else (lambda k, d=None: getattr(data, k, d))
        setv = (lambda k, v: data.__setitem__(k, v)) if isinstance(data, dict) \
            else (lambda k, v: setattr(data, k, v))
        trace_id = get("id", "")
        if not trace_id:
            return
        usage = get("usage", {}) or {}
        if usage.get("total_tokens"):
            return  # 已有真值，不覆盖
        from backend.observability.llm_usage_store import get_llm_usage_store
        rows = get_llm_usage_store().by_trace(trace_id)
        if not rows:
            return
        by_comp: dict[str, dict] = {}
        total_cost = 0.0
        for r in rows:
            comp = r.get("component") or "llm"
            agg = by_comp.setdefault(comp, {
                "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
                "cached_tokens": 0, "reasoning_tokens": 0, "calls": 0,
            })
            for k in ("prompt_tokens", "completion_tokens", "total_tokens",
                      "cached_tokens", "reasoning_tokens"):
                agg[k] += r.get(k) or 0
            agg["calls"] += 1
            total_cost += r.get("cost_usd") or 0.0
        llm_agg = by_comp.get("llm")
        if llm_agg and llm_agg["total_tokens"] > 0:
            usage = dict(llm_agg)
        usage["cost_usd"] = round(total_cost, 6)
        usage["by_component"] = by_comp
        setv("usage", usage)
        setv("cost_usd", round(total_cost, 6))
        if not (get("model", "") or "").strip():
            llm_rows = [r for r in rows if (r.get("component") or "llm") == "llm"]
            if llm_rows:
                best = max(llm_rows, key=lambda r: r.get("total_tokens") or 0)
                setv("model", best.get("model") or "")
                setv("provider", best.get("provider") or "")
        # llm_call span 指标补齐（时间最近邻配对，同 tracer.finish 逻辑）
        from datetime import datetime, timezone

        def _ts_key(iso: str) -> float:
            try:
                dt = datetime.fromisoformat((iso or "").replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.timestamp()
            except Exception:
                return 0.0

        llm_rows = sorted(
            (r for r in rows if (r.get("component") or "llm") == "llm"),
            key=lambda r: _ts_key(r.get("ts", "")),
        )
        pending = [r for r in llm_rows if r.get("total_tokens")]
        spans = get("spans", []) or []
        for span in spans:
            sget = (lambda k, d=None: span.get(k, d)) if isinstance(span, dict) \
                else (lambda k, d=None: getattr(span, k, d))
            if sget("type", "") != "llm_call":
                continue
            m = sget("metrics", {}) or {}
            if m.get("total_tokens") or m.get("prompt_tokens") or not pending:
                continue
            st = _ts_key(sget("start_time", "") or "")
            et = _ts_key(sget("end_time", "") or "") or None
            # 优先取落在 span 时间窗内的调用，仅无命中时退化为全局最近邻
            in_window = [r for r in pending
                         if _ts_key(r.get("ts", "")) >= st
                         and (et is None or _ts_key(r.get("ts", "")) <= et)]
            pool = in_window or pending
            row = min(pool, key=lambda r: abs(_ts_key(r.get("ts", "")) - st))
            pending.remove(row)
            m.update({
                "prompt_tokens": row.get("prompt_tokens") or 0,
                "completion_tokens": row.get("completion_tokens") or 0,
                "total_tokens": row.get("total_tokens") or 0,
                "cost_usd": row.get("cost_usd") or 0.0,
                "model_name": row.get("model") or "",
                "token_source": "llm_usage_backfill",
            })
            smset = (lambda k, v: span.__setitem__(k, v)) if isinstance(span, dict) \
                else (lambda k, v: setattr(span, k, v))
            smset("metrics", m)
    except Exception:
        return


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
        m = get("metrics", {}) or {}
        inp = get("input") or {}
        out = get("output") or {}
        dto["llm_call"] = {
            "model": m.get("model_name", "") if isinstance(m, dict) else "",
            "temperature": m.get("temperature", 0) if isinstance(m, dict) else 0,
            "prompt_tokens": m.get("prompt_tokens", 0) if isinstance(m, dict) else 0,
            "completion_tokens": m.get("completion_tokens", 0) if isinstance(m, dict) else 0,
            "cost_usd": m.get("cost_usd", 0) if isinstance(m, dict) else 0,
            # 文本回退链：prompt/question/query（路由 LLM 的 input 用 query 键）、
            # response/metrics.completion_text（chain 层截断写入）
            "prompt_text": ((inp.get("prompt") or inp.get("question") or inp.get("query") or "")
                            if isinstance(inp, dict) else ""),
            "response_text": (((out.get("response") or "") if isinstance(out, dict) else "")
                              or (m.get("completion_text", "") if isinstance(m, dict) else "")),
        }
    return dto


def to_trace_dto(t, detail_level: str | None = None) -> dict:
    """TraceRecord / dict → frontend TraceRecord DTO."""
    get = lambda k, d=None: t.get(k, d) if isinstance(t, dict) else getattr(t, k, d)
    total_ms = get("duration_ms", 0)
    sla_ms = get("sla_threshold_ms", 10000) or 10000
    all_spans = get("spans", [])

    def _stype(s):
        return s.get("type", "") if isinstance(s, dict) else getattr(s, "type", "")

    # P1-5: 服务端统一统计口径 — 前端概览/过滤/明细三处不再各自计数，
    # 消除 "LLM 调用 2 vs 明细 1"、"工具调用 14 vs 过滤 10" 的口径矛盾。
    summary = {
        "llm_calls": sum(1 for s in all_spans if _stype(s) == "llm_call"),
        "tool_calls": sum(1 for s in all_spans if _stype(s) == "tool_call"),
        "retrieval_calls": sum(1 for s in all_spans if _stype(s) in ("retrieval", "rerank")),
        "span_count": len(all_spans),
    }

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
        "summary": summary,
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
