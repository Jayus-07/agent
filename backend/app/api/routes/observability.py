"""可观测性 REST API — traces / metrics / resources / alerts / graph

数据源统一在 `backend.rag.tracer.trace_collector`（之前是双 store，
已删除 `orchestration.TraceStore` 死代码）。
"""

import os
import json

from fastapi import APIRouter, Query, HTTPException

from backend.shared.logger import logger

from backend.observability.topology import GRAPH_TOPOLOGY, NODE_LABELS
from backend.observability.resource import resource_monitor
from backend.rag.metrics import metrics_collector
from backend.observability.tracer import trace_collector, TraceRecord, Span
from backend.observability.trace_store import get_trace_store

router = APIRouter(prefix="/observability", tags=["可观测性"])


# ═══════════════════════════════════════════════════
# 适配器：TraceRecord + Span → 前端 TraceRecord DTO
# ═══════════════════════════════════════════════════

def _to_span_dto(s, all_spans: list, total_ms: int) -> dict:
    """Span / dict → 前端 Span DTO。兼容 TraceRecord Span 和 SQLite dict。"""
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


def _to_trace_dto(t) -> dict:
    """TraceRecord / dict → 前端 TraceRecord DTO。"""
    # 兼容 dict（SQLite 存储格式）和 TraceRecord
    get = lambda k, d=None: t.get(k, d) if isinstance(t, dict) else getattr(t, k, d)
    total_ms = get("duration_ms", 0)
    all_spans = get("spans", [])
    has_error = any((s.get("status") if isinstance(s, dict) else s.status) == "error" for s in all_spans)
    return {
        "id": get("id", ""),
        "timestamp": get("timestamp", ""),
        "session_id": get("session_id", ""),
        "question": get("question", ""),
        "answer_preview": get("answer_preview", ""),
        "answer_len": get("answer_len", 0),
        "duration_ms": total_ms,
        "model": get("model", {}) if isinstance(get("model", {}), dict) else {"name": get("model", ""), "provider": get("provider", "")},
        "usage": get("usage", {}),
        "cost_usd": get("cost_usd", 0),
        "error": get("error", {}),
        "metadata": get("metadata", {}),
        "status": "error" if has_error else ("running" if total_ms == 0 else "success"),
        "workflow_name": get("workflow_name", ""),
        "root_span_id": get("root_span_id", ""),
        "spans": [_to_span_dto(s, all_spans, total_ms) for s in all_spans],
        "sla": {"threshold_ms": 10000, "breached": total_ms > 10000},
        "parent_id": get("parent_id"),
        "children_ids": get("children_ids", []),
        "graph": get("graph"),
        "tags": get("tags", {}),
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
    """最近 N 条 trace 摘要（直接从 SQLite 读取）"""
    stored = trace_collector.list(limit)
    traces = [_stored_dict_to_dto(d) for d in stored]
    return {"traces": traces}


@router.get("/traces/active")
async def list_active_traces():
    """当前活跃的 trace（contextvar / thread_local 中）"""
    active = trace_collector.list_active()
    return {"traces": [_stored_dict_to_dto(t) for t in active]}


@router.get("/traces/{trace_id}")
async def get_trace(trace_id: str):
    """获取单条 trace 完整详情（直接从 SQLite 读取）"""
    data = trace_collector.get(trace_id)
    if data is None:
        raise HTTPException(status_code=404, detail=f"Trace {trace_id} 不存在或已过期")
    return data  # SQLite 返回的 dict 已是 DTO 兼容格式


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
    from backend.observability.alerts import DEGRADATION_LOG_FILE

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
        logger.warning("读取告警文件失败", exc_info=True)

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