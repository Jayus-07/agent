"""可观测性 REST API — traces / metrics / resources / alerts / graph

数据源统一在 `backend.rag.tracer.trace_collector`：
Langfuse 主存储（读路径优先），SQLite TraceStore 保留作为降级兜底。
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

from backend.app.api.routes._trace_dto import (  # noqa: E402
    to_span_dto as _to_span_dto,
    to_trace_dto as _to_trace_dto,
    stored_dict_to_dto as _stored_dict_to_dto,
)


# ═══════════════════════════════════════════════════
# Traces
# ═══════════════════════════════════════════════════

@router.get("/traces")
async def list_traces(limit: int = Query(20, ge=1, le=200),
                      workflow_name: str | None = Query(None),
                      session_id: str | None = Query(None)):
    """最近 N 条 trace 摘要（Langfuse 主查询，SQLite 降级）。

    workflow_name / session_id 服务端过滤：前端不再拉 200 条本地 filter。
    """
    stored = trace_collector.list(limit)
    if workflow_name:
        stored = [d for d in stored
                  if (d.get("workflow_name") if isinstance(d, dict)
                      else getattr(d, "workflow_name", "")) == workflow_name]
    if session_id:
        stored = [d for d in stored
                  if (d.get("session_id") if isinstance(d, dict)
                      else getattr(d, "session_id", "")) == session_id]
    traces = [_stored_dict_to_dto(d) if isinstance(d, dict) else _to_trace_dto(d)
              for d in stored]
    return {"traces": traces}


@router.get("/traces/stats")
async def trace_stats(hours: float = Query(24, gt=0, le=24 * 30),
                      workflow_name: str | None = Query(None)):
    """时间窗内聚合统计（前端 StatsBar 下沉，不再客户端遍历 200 条）。

    数据源：P0 analytics 结构化层优先（含新写入数据），否则 SQLite 详情库摘要。
    口径与前端原实现一致：成功率/均值/P95 只统计已完成（duration>0）的 trace。
    """
    from datetime import datetime, timedelta, timezone

    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    def _ts_ok(ts: str) -> bool:
        try:
            dt = datetime.fromisoformat((ts or "").replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt >= cutoff
        except Exception:
            return False

    rows: list[dict] = []
    try:
        from backend.observability.analytics_store import get_analytics_store
        store = get_analytics_store()
        if store.enabled and store.count() > 0:
            rows = store.list(500, workflow_name=workflow_name)
    except Exception:
        logger.debug("stats: analytics 层不可用，降级 SQLite", exc_info=True)
    if not rows:
        rows = [r for r in trace_collector.list(200) if isinstance(r, dict)]
        if workflow_name:
            rows = [r for r in rows if r.get("workflow_name") == workflow_name]

    rows = [r for r in rows if _ts_ok(r.get("timestamp", ""))]
    completed = [r for r in rows if (r.get("duration_ms", 0) or 0) > 0]
    n = len(completed)

    def _is_err(r: dict) -> bool:
        return r.get("status") == "error" or bool(r.get("error"))

    durations = sorted((r.get("duration_ms", 0) for r in completed), reverse=True)
    p95 = durations[int(n * 0.05)] if n else 0
    err_count = sum(1 for r in rows if _is_err(r))
    return {
        "total_24h": len(rows),
        "success_rate": round((n - sum(1 for r in completed if _is_err(r))) / n, 3) if n else 0,
        "avg_duration_ms": round(sum(r.get("duration_ms", 0) for r in completed) / n) if n else 0,
        "p95_duration_ms": p95,
        "error_count": err_count,
        "total_cost_usd": round(sum(r.get("cost_usd", 0) or 0 for r in rows), 6),
    }


@router.get("/traces/active")
async def list_active_traces():
    """当前活跃的 trace（contextvar / thread_local 中）"""
    active = trace_collector.list_active()
    return {"traces": [_stored_dict_to_dto(t) for t in active]}


@router.get("/traces/{trace_id}")
async def get_trace(trace_id: str):
    """获取单条 trace 完整详情（Langfuse 优先，SQLite 兜底）"""
    # trace_collector.get() 内部已做 Langfuse → SQLite 两级回退；
    # 再保留一层 store 直读兜底（极端情况下 collector 异常）
    data = trace_collector.get(trace_id)
    if data is None:
        store = get_trace_store()
        data = store.get(trace_id)
    if data is None:
        raise HTTPException(status_code=404, detail=f"Trace {trace_id} 不存在或已过期")
    return _to_trace_dto(data)  # 统一转为前端 DTO 格式


# ═══════════════════════════════════════════════════
# RAG Agent Traces（保留独立路由，向后兼容）
# ═══════════════════════════════════════════════════

@router.get("/rag-traces")
async def list_rag_traces(limit: int = Query(50, ge=1, le=200)):
    """最近 N 条 RAG Trace（与 /traces 共享数据源，仅保留向后兼容）"""
    traces = trace_collector.list(limit)
    return {"traces": [_stored_dict_to_dto(t) if isinstance(t, dict) else _to_trace_dto(t)
                       for t in traces]}


@router.get("/rag-traces/stream")
async def stream_rag_traces():
    """SSE 实时推送新 Trace（轮询式，1s 间隔）"""
    from fastapi.responses import StreamingResponse
    import asyncio

    async def event_stream():
        last_id = ""
        while True:
            traces = trace_collector.list(1)
            tid = traces[0].get("id") if traces and isinstance(traces[0], dict) \
                else (traces[0].id if traces else "")
            if traces and tid and tid != last_id:
                last_id = tid
                data = json.dumps(
                    _stored_dict_to_dto(traces[0]) if isinstance(traces[0], dict)
                    else _to_trace_dto(traces[0]),
                    ensure_ascii=False)
                yield f"data: {data}\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get("/rag-traces/{trace_id}")
async def get_rag_trace(trace_id: str):
    """获取单条 RAG Trace 详情（Langfuse 优先，SQLite 兜底）"""
    t = trace_collector.get(trace_id)
    if t is None:
        data = get_trace_store().get(trace_id)
        if data is None:
            raise HTTPException(status_code=404, detail=f"Trace {trace_id} 不存在或已过期")
        return data
    return _to_trace_dto(t)


# ═══════════════════════════════════════════════════
# Token 用量看板
# ═══════════════════════════════════════════════════

@router.get("/tokens/summary")
async def token_summary(days: int = Query(7, ge=1, le=365)):
    """Token 用量看板聚合（近 N 天）：总量 / 日趋势 / 按模型细分。

    数据源：llm_usage 明细表（每次 LLM 调用一行，proxy 层写入），
    按模型精确聚合，不受 trace 父子嵌套影响。
    """
    from backend.observability.llm_usage_store import get_llm_usage_store
    data = get_llm_usage_store().dashboard(days)
    data["days"] = days
    return data


@router.get("/tokens/calls")
async def token_calls(days: int = Query(7, ge=1, le=365),
                      model: str | None = Query(None),
                      limit: int = Query(20, ge=1, le=200),
                      offset: int = Query(0, ge=0)):
    """LLM 调用明细（分页，最新在前）—— 每次调用一行的 token/成本记录。"""
    from backend.observability.llm_usage_store import get_llm_usage_store
    return get_llm_usage_store().list_calls(
        days=days, model=model, limit=limit, offset=offset)


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
# Circuit Breakers（R2 熔断器状态）
# ═══════════════════════════════════════════════════

@router.get("/breakers")
async def get_breakers():
    """返回所有熔断器当前状态（CLOSED/OPEN/HALF_OPEN）"""
    from backend.infra.circuit_breaker import get_all_breakers
    return {"breakers": [cb.stats() for cb in get_all_breakers().values()]}


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