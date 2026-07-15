"""可观测性 REST API — traces / metrics / resources / alerts / graph"""

import os
import json

from fastapi import APIRouter, Query, HTTPException

from multi_agent.observability import trace_store, GRAPH_TOPOLOGY, NODE_LABELS
from utils.resource_monitor import resource_monitor
from retrieval.metrics import metrics_collector

router = APIRouter(prefix="/observability", tags=["可观测性"])


# ═══════════════════════════════════════════════════
# Traces
# ═══════════════════════════════════════════════════

@router.get("/traces")
async def list_traces(limit: int = Query(20, ge=1, le=200)):
    """最近 N 条 trace 摘要（不含 events，减少 payload）"""
    return {"traces": trace_store.get_recent(limit)}


@router.get("/traces/active")
async def list_active_traces():
    """当前活跃的 trace（status=running）"""
    return {"traces": trace_store.get_active()}


@router.get("/traces/{trace_id}")
async def get_trace(trace_id: str):
    """获取单条 trace 的完整详情（含 events 和 nodes_visited）"""
    trace = trace_store.get_trace(trace_id)
    if trace is None:
        raise HTTPException(status_code=404, detail=f"Trace {trace_id} 不存在")
    return trace


# ═══════════════════════════════════════════════════
# RAG Agent Traces
# ═══════════════════════════════════════════════════

@router.get("/rag-traces")
async def list_rag_traces(limit: int = Query(50, ge=1, le=200)):
    """最近 N 条 RAG Agent Trace（全链路耗时记录）"""
    from retrieval.tracer import trace_collector
    traces = trace_collector.list(limit)
    return {
        "traces": [
            {
                "id": t.id,
                "request_id": t.request_id,
                "timestamp": t.timestamp,
                "session_id": t.session_id,
                "model": {"name": t.model, "provider": t.provider},
                "question": t.question,
                "answer_preview": t.answer_preview,
                "answer_len": t.answer_len,
                "duration_ms": t.duration_ms,
                "usage": t.usage,
                "cost": t.cost,
                "error": t.error,
                "metadata": t.metadata,
                "steps": [{
                    "id": s.id,
                    "label": s.label,
                    "duration_ms": s.duration_ms,
                    "duration_ratio": s.duration_ratio,
                    "status": s.status,
                    "metrics": s.metrics,
                } for s in t.steps],
            }
            for t in traces
        ]
    }


@router.get("/rag-traces/{trace_id}")
async def get_rag_trace(trace_id: str):
    """获取单条 RAG Trace 详情"""
    from retrieval.tracer import trace_collector
    t = trace_collector.get(trace_id)
    if t is None:
        raise HTTPException(status_code=404, detail=f"Trace {trace_id} 不存在")
    return {
        "id": t.id, "request_id": t.request_id, "timestamp": t.timestamp, "session_id": t.session_id,
        "model": {"name": t.model, "provider": t.provider},
        "question": t.question, "answer_preview": t.answer_preview,
        "answer_len": t.answer_len, "duration_ms": t.duration_ms,
        "usage": t.usage, "cost": t.cost, "error": t.error, "metadata": t.metadata,
        "steps": [{
            "id": s.id, "label": s.label, "duration_ms": s.duration_ms,
            "duration_ratio": s.duration_ratio, "status": s.status, "metrics": s.metrics,
        } for s in t.steps],
    }


@router.get("/rag-traces/stream")
async def stream_rag_traces():
    """SSE 实时推送新 Trace"""
    from fastapi.responses import StreamingResponse
    import asyncio, json

    async def event_stream():
        from retrieval.tracer import trace_collector
        last_id = ""
        while True:
            traces = trace_collector.list(1)
            if traces and traces[0].id != last_id:
                t = traces[0]
                last_id = t.id
                data = json.dumps({
                    "id": t.id, "request_id": t.request_id,
                    "timestamp": t.timestamp, "session_id": t.session_id,
                    "model": {"name": t.model, "provider": t.provider},
                    "question": t.question, "duration_ms": t.duration_ms,
                    "usage": t.usage, "steps": [{
                        "id": s.id, "label": s.label,
                        "duration_ms": s.duration_ms, "duration_ratio": s.duration_ratio,
                        "status": s.status, "metrics": s.metrics,
                    } for s in t.steps],
                }, ensure_ascii=False)
                yield f"data: {data}\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ═══════════════════════════════════════════════════
# Metrics
# ═══════════════════════════════════════════════════

@router.get("/metrics")
async def get_metrics():
    """聚合指标：Pipeline（成功率+P50/P95/P99）+ Retrieval（检索延迟/召回数）"""
    pipeline = trace_store.get_aggregated_metrics()
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
    from multi_agent.alerts import DEGRADATION_LOG_FILE

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
        "alerts": list(reversed(alerts)),  # 最新在前
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
