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
from backend.rag.tracer import trace_collector, TraceRecord, TraceStep

router = APIRouter(prefix="/observability", tags=["可观测性"])


# ═══════════════════════════════════════════════════
# 适配器：TraceRecord → §15 TraceDTO
# ═══════════════════════════════════════════════════

def _to_step_dto(s: TraceStep) -> dict:
    return {
        "id": s.id,
        "label": s.label,
        "duration_ms": s.duration_ms,
        "duration_ratio": s.duration_ratio,
        "status": s.status,
        "metrics": s.metrics,
    }


def _to_trace_dto(t: TraceRecord) -> dict:
    """统一 trace 序列化（list / detail / active 共用）"""
    has_error = any(s.status == "error" for s in t.steps)
    return {
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
        "cost_usd": sum(s.metrics.get("cost_usd", 0) for s in t.steps),  # 派生
        "error": t.error,
        "metadata": t.metadata,
        "status": "error" if has_error else ("running" if t.duration_ms == 0 else "success"),
        "steps": [_to_step_dto(s) for s in t.steps],
    }


# ═══════════════════════════════════════════════════
# Traces
# ═══════════════════════════════════════════════════

@router.get("/traces")
async def list_traces(limit: int = Query(20, ge=1, le=200)):
    """最近 N 条 trace 摘要（统一数据源：TraceCollector）"""
    traces = trace_collector.list(limit)
    return {"traces": [_to_trace_dto(t) for t in traces]}


@router.get("/traces/active")
async def list_active_traces():
    """当前活跃的 trace（answer_preview 为空 = 还没 finish）"""
    active = trace_collector.list_active()
    return {"traces": [_to_trace_dto(t) for t in active]}


@router.get("/traces/{trace_id}")
async def get_trace(trace_id: str):
    """获取单条 trace 完整详情"""
    t = trace_collector.get(trace_id)
    if t is None:
        raise HTTPException(status_code=404, detail=f"Trace {trace_id} 不存在")
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
    """获取单条 RAG Trace 详情"""
    t = trace_collector.get(trace_id)
    if t is None:
        raise HTTPException(status_code=404, detail=f"Trace {trace_id} 不存在")
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