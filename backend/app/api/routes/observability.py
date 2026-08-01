"""可观测性 REST API — traces / metrics / resources / alerts / graph

数据源统一在 `backend.rag.tracer.trace_collector`（之前是双 store，
已删除 `orchestration.TraceStore` 死代码）。
"""

import os
import json

from fastapi import APIRouter, Query, HTTPException

from backend.orchestration.observability import GRAPH_TOPOLOGY, NODE_LABELS
from backend.shared.monitoring.resource_monitor import resource_monitor
from backend.rag.metrics import metrics_collector
from backend.rag.tracer import trace_collector, TraceRecord, Span
from backend.rag.trace_store import get_trace_store

router = APIRouter(prefix="/observability", tags=["可观测性"])


# ═══════════════════════════════════════════════════
# 适配器：TraceRecord + Span → 前端 TraceRecord DTO
# ═══════════════════════════════════════════════════

def _to_span_dto(s: Span, all_spans: list[Span], total_ms: int) -> dict:
    """Span → 前端 Span DTO。字段名映射：span_id→id, 派生 duration_ratio/children/llm_call。"""
    d: dict = {
        "id": s.span_id,
        "type": s.type,
        "name": s.name,
        "parent_id": s.parent_id,
        "status": s.status,
        "start_time": s.start_time,
        "end_time": s.end_time,
        "duration_ms": s.duration_ms,
        "duration_ratio": s.duration_ms / total_ms if total_ms else 0,
        "metrics": s.metrics,
        "children": [c.span_id for c in all_spans if c.parent_id == s.span_id],
        "input": s.input,
        "output": s.output,
        "events": s.events,
        "errors": s.errors,
    }
    # type=llm_call 时派生 llm_call 子块（前端 LLMCallDetail / CostPanel 直接读）
    if s.type == "llm_call":
        d["llm_call"] = {
            "model": s.metrics.get("model_name", ""),
            "temperature": s.metrics.get("temperature", 0),
            "prompt_tokens": s.metrics.get("prompt_tokens", 0),
            "completion_tokens": s.metrics.get("completion_tokens", 0),
            "cost_usd": s.metrics.get("cost_usd", 0),
            "prompt_text": (s.input or {}).get("prompt", ""),
            "response_text": (s.output or {}).get("response", ""),
        }
    return d


def _to_trace_dto(t: TraceRecord) -> dict:
    """TraceRecord → 前端 TraceRecord DTO。"""
    total_ms = t.duration_ms
    all_spans = t.spans
    has_error = any(s.status == "error" for s in all_spans)
    return {
        "id": t.id,
        "timestamp": t.timestamp,
        "session_id": t.session_id,
        "question": t.question,
        "answer_preview": t.answer_preview,
        "answer_len": t.answer_len,
        "duration_ms": t.duration_ms,
        "model": {"name": t.model, "provider": t.provider},
        "usage": t.usage,
        "cost_usd": sum(s.metrics.get("cost_usd", 0) for s in all_spans),
        "error": t.error,
        "metadata": t.metadata,
        "status": "error" if has_error else ("running" if t.duration_ms == 0 else "success"),
        "workflow_name": t.workflow_name,
        "root_span_id": t.root_span_id,
        "spans": [_to_span_dto(s, all_spans, total_ms) for s in all_spans],
        "sla": {
            "threshold_ms": t.sla_threshold_ms,
            "breached": t.duration_ms > t.sla_threshold_ms,
        },
        "parent_id": t.parent_id,
        "children_ids": t.children_ids,
        "graph": t.graph,
        "tags": t.tags,  # 含 doc_id / kb_id / file_ext 等（文档索引 trace 用）
    }


def _stored_dict_to_dto(d: dict) -> dict:
    """SQLite 存储的 trace dict → 前端兼容的 DTO（spans 已移除，仅列表摘要）"""
    return {
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
        "sla": {"threshold_ms": 10000, "breached": False},
        "parent_id": None,
        "children_ids": [],
        "graph": None,
        "tags": d.get("tags", {}),
    }


# ═══════════════════════════════════════════════════
# Traces
# ═══════════════════════════════════════════════════

@router.get("/traces")
async def list_traces(limit: int = Query(20, ge=1, le=200)):
    """最近 N 条 trace 摘要（内存 + SQLite 兜底）"""
    traces = trace_collector.list(limit)
    seen = {t.id for t in traces}

    # 内存不足 → SQLite 补全（重启后内存为空，trace 仍在 SQLite 中）
    if len(traces) < limit:
        try:
            from backend.rag.trace_store import get_trace_store
            stored = get_trace_store().list(limit)
            for d in stored:
                if d.get("id") not in seen:
                    # SQLite 存的是 dict（spans 已移除），直接传前端兼容格式
                    traces.append(_stored_dict_to_dto(d))
                    seen.add(d["id"])
                    if len(traces) >= limit:
                        break
        except Exception:
            pass

    return {"traces": [_to_trace_dto(t) if isinstance(t, TraceRecord) else t for t in traces[:limit]]}


@router.get("/traces/active")
async def list_active_traces():
    """当前活跃的 trace（answer_preview 为空 = 还没 finish）"""
    active = trace_collector.list_active()
    return {"traces": [_to_trace_dto(t) for t in active]}


@router.get("/traces/{trace_id}")
async def get_trace(trace_id: str):
    """获取单条 trace 完整详情（内存优先，SQLite 兜底）"""
    t = trace_collector.get(trace_id)
    if t is None:
        # 内存未命中 → 从 SQLite 读取（重启后仍可用）
        data = get_trace_store().get(trace_id)
        if data is None:
            raise HTTPException(status_code=404, detail=f"Trace {trace_id} 不存在或已过期")
        return data  # 已是 DTO 格式（JSON dict）
    return _to_trace_dto(t)


# ═══════════════════════════════════════════════════
# RAG Agent Traces（保留独立路由，向后兼容）
# ═══════════════════════════════════════════════════

@router.get("/rag-traces")
async def list_rag_traces(limit: int = Query(50, ge=1, le=200)):
    """最近 N 条 RAG Trace（与 /traces 共享数据源，仅保留向后兼容）"""
    traces = trace_collector.list(limit)
    return {"traces": [_to_trace_dto(t) for t in traces]}


@router.get("/rag-traces/stream")
async def stream_rag_traces():
    """SSE 实时推送新 Trace（轮询式，1s 间隔）"""
    from fastapi.responses import StreamingResponse
    import asyncio

    async def event_stream():
        last_id = ""
        while True:
            traces = trace_collector.list(1)
            if traces and traces[0].id != last_id:
                t = traces[0]
                last_id = t.id
                data = json.dumps(_to_trace_dto(t), ensure_ascii=False)
                yield f"data: {data}\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get("/rag-traces/{trace_id}")
async def get_rag_trace(trace_id: str):
    """获取单条 RAG Trace 详情（内存优先，SQLite 兜底）"""
    t = trace_collector.get(trace_id)
    if t is None:
        data = get_trace_store().get(trace_id)
        if data is None:
            raise HTTPException(status_code=404, detail=f"Trace {trace_id} 不存在或已过期")
        return data
    return _to_trace_dto(t)


# ═══════════════════════════════════════════════════
# Metrics
# ═══════════════════════════════════════════════════

@router.get("/metrics")
async def get_metrics():
    """聚合指标：Pipeline（成功率+P50/P95/P99）+ Retrieval（检索延迟/召回数）"""
    pipeline = trace_collector.compute_metrics()  # 替代已删的 trace_store
    retrieval = metrics_collector.summary()
    return {"pipeline": pipeline, "retrieval": retrieval}


# ═══════════════════════════════════════════════════
# Resources
# ═══════════════════════════════════════════════════

@router.get("/resources")
async def get_resources():
    """实时系统资源快照：CPU / Memory / 运行时间 / 请求计数"""
    return {
        "cpu": resource_monitor.get_cpu_info(),
        "memory": resource_monitor.get_memory_info(),
        "uptime_seconds": round(resource_monitor.get_uptime(), 1),
        "request_count": resource_monitor.request_count,
        "warning_count": resource_monitor.warning_count,
    }


# ═══════════════════════════════════════════════════
# Alerts
# ═══════════════════════════════════════════════════

@router.get("/alerts")
async def get_alerts(limit: int = Query(50, ge=1, le=500)):
    """读取降级/告警日志（degradation.jsonl 尾部 N 行）"""
    from backend.orchestration.supervisor.alerts import DEGRADATION_LOG_FILE

    alerts = []
    total = 0
    try:
        if os.path.exists(DEGRADATION_LOG_FILE):
            with open(DEGRADATION_LOG_FILE, "r", encoding="utf-8") as f:
                lines = f.readlines()
            total = len(lines)
            for line in lines[-limit:]:
                line = line.strip()
                if line:
                    try:
                        alerts.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    except Exception:
        pass

    return {
        "alerts": list(reversed(alerts)),
        "total": total,
        "file": DEGRADATION_LOG_FILE,
    }


# ═══════════════════════════════════════════════════
# Graph
# ═══════════════════════════════════════════════════

@router.get("/graph")
async def get_graph():
    """返回 LangGraph 静态拓扑 + 节点标签"""
    return {
        "topology": GRAPH_TOPOLOGY,
        "node_labels": NODE_LABELS,
    }