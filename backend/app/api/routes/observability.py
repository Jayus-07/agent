"""可观测性 REST API — traces / metrics / resources / alerts / graph

数据源统一在 `backend.rag.tracer.trace_collector`：
Langfuse 主存储（读路径优先），SQLite TraceStore 保留作为降级兜底。
"""

import os
import json
import threading

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
    backfill_usage_from_llm_store as _backfill_usage,
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


@router.get("/cs-quality")
async def cs_quality_report(hours: float = Query(24, gt=0, le=24 * 30)):
    """CS 灰度质量报告：按 cs_variant 分组的路由一致率/兜底率/转人工率/时延 + 告警。"""
    from backend.observability.cs_quality_report import build_cs_quality_report
    return build_cs_quality_report(hours=hours)


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
    # 读取时回填：存量 trace 的 usage/model/cost 以 llm_usage 明细补齐（历史数据兼容）
    _backfill_usage(data)
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
async def token_summary(days: int = Query(7, ge=1, le=365),
                        component: str | None = Query(None)):
    """Token 用量看板聚合（近 N 天）：总量 / 日趋势 / 按模型细分。

    数据源：llm_usage 明细表（每次调用一行，proxy/TokenTracker 层写入），
    按组件类型（llm/embedding/rerank）和模型精确聚合。
    
    Args:
        days: 时间窗天数（默认 7）
        component: 组件类型过滤（all/llm/embedding/rerank，默认 all）
    """
    from backend.observability.llm_usage_store import get_llm_usage_store
    data = get_llm_usage_store().dashboard(days, component=component)
    data["days"] = days
    return data


@router.get("/tokens/calls")
async def token_calls(days: int = Query(7, ge=1, le=365),
                      model: str | None = Query(None),
                      component: str | None = Query(None),
                      limit: int = Query(20, ge=1, le=200),
                      offset: int = Query(0, ge=0)):
    """调用明细（分页，最新在前）—— 每次调用的 token/成本记录。"""
    from backend.observability.llm_usage_store import get_llm_usage_store
    return get_llm_usage_store().list_calls(
        days=days, model=model, component=component, limit=limit, offset=offset)


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
# Skill / 节点健康度
# ═══════════════════════════════════════════════════

@router.get("/skill-health")
async def skill_health(limit: int = Query(200, ge=1, le=1000)):
    """按节点/Skill 聚合最近 trace 的健康度：调用量/成功率/平均耗时/重试次数。

    数据源为最近 N 条 trace 的 span 明细，按 span.name 分组（排除 route/round
    这类流程控制 span）。前端能力健康度卡片的数据入口，也为熔断/错误预算
    策略提供依据。
    """
    rows = trace_collector.list(limit=limit, include_spans=True)

    def _get(obj, key, default=None):
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

    stats: dict[str, dict] = {}
    for row in rows:
        spans = _get(row, "spans", []) or []
        ts = _get(row, "timestamp", "")
        for sp in spans:
            sp_type = str(_get(sp, "type", "") or "")
            if sp_type in ("route", "round"):
                continue  # 流程控制 span 不计入能力健康度
            name = str(_get(sp, "name", "?") or "?")
            status = str(_get(sp, "status", "") or "unknown")
            duration = int(_get(sp, "duration_ms", 0) or 0)
            events = _get(sp, "events", []) or []
            retries = sum(
                1 for ev in events
                if str(_get(ev, "name", "")).startswith("retry_")
            )
            s = stats.setdefault(name, {
                "name": name, "type": sp_type,
                "total": 0, "success": 0, "error": 0, "skipped": 0,
                "duration_sum_ms": 0, "retries": 0,
                "last_status": "", "last_error": "", "last_ts": "",
            })
            s["total"] += 1
            if status == "success":
                s["success"] += 1
            elif status in ("error", "failed"):
                s["error"] += 1
                err = _get(sp, "error", None)
                if err and not s["last_error"]:
                    s["last_error"] = str(err)[:200]
            elif status == "skipped":
                s["skipped"] += 1
            s["duration_sum_ms"] += duration
            s["retries"] += retries
            if ts > s["last_ts"]:
                s["last_ts"] = ts
                s["last_status"] = status

    items = []
    for s in stats.values():
        executed = s["success"] + s["error"]
        items.append({
            **s,
            "avg_duration_ms": round(s["duration_sum_ms"] / s["total"]) if s["total"] else 0,
            "success_rate": round(s["success"] / executed, 4) if executed else None,
        })
    # 失败多的排前，其次按调用量
    items.sort(key=lambda x: (-x["error"], -x["total"]))
    return {"skills": items, "trace_window": limit}


# ═══════════════════════════════════════════════════
# Trace 重放
# ═══════════════════════════════════════════════════

@router.post("/traces/{trace_id}/replay")
async def replay_trace(trace_id: str):
    """重放一条历史 trace：用原问题重新走一遍 Agent 链路（异步启动）。

    失败/降级 trace 的"重试"按钮后端。fire-and-forget：立即返回，
    新 trace 稍后出现在链路追踪列表中。
    """
    data = trace_collector.get(trace_id)
    if data is None:
        store = get_trace_store()
        data = store.get(trace_id)
    if data is None:
        raise HTTPException(status_code=404, detail=f"Trace {trace_id} 不存在或已过期")

    def _get(obj, key, default=None):
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

    question = str(_get(data, "question", "") or "")
    if not question.strip():
        raise HTTPException(status_code=400, detail="该 trace 无原始问题，无法重放")
    session_id = str(_get(data, "session_id", "") or "default")
    tags = _get(data, "tags", {}) or {}
    kb_id = str(tags.get("kb_id", "default") or "default")

    def _run():
        try:
            from backend.orchestration.graph.system import MultiAgentSystem
            MultiAgentSystem().ask(question, session_id=session_id, kb_id=kb_id)
            logger.info(f"[Replay] trace {trace_id[:12]} 重放完成")
        except Exception:
            logger.warning(f"[Replay] trace {trace_id[:12]} 重放失败", exc_info=True)

    # fire-and-forget：重放可能耗时数十秒，绝不能占用 API worker
    threading.Thread(target=_run, name=f"replay-{trace_id[:8]}", daemon=True).start()
    logger.info(f"[Replay] 已启动: 原 trace {trace_id[:12]} question={question[:60]}")
    return {
        "started": True,
        "source_trace_id": trace_id,
        "question": question[:120],
        "session_id": session_id,
        "kb_id": kb_id,
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


# ═══════════════════════════════════════════════════
# Gateway Auth（2026-09-16：管理端「网关安全」页数据源）
# ═══════════════════════════════════════════════════

_PROMETHEUS_TIMEOUT_S = 3.0


def _prometheus_url() -> str:
    return os.getenv("PROMETHEUS_URL", "http://127.0.0.1:9090").rstrip("/")


async def _prom_instant(promql: str) -> list[dict]:
    """即时查询。返回 [{labels, value}]，Prometheus 不可达时抛异常由上层降级。"""
    import httpx
    import time as _time

    async with httpx.AsyncClient(timeout=_PROMETHEUS_TIMEOUT_S) as client:
        resp = await client.get(
            f"{_prometheus_url()}/api/v1/query",
            params={"query": promql, "time": f"{_time.time():.3f}"},
        )
        resp.raise_for_status()
        body = resp.json()
    if body.get("status") != "success":
        raise RuntimeError(f"prometheus query failed: {body.get('errorType')}")
    return [
        {"labels": r.get("metric", {}), "value": float(r["value"][1])}
        for r in body.get("data", {}).get("result", [])
    ]


async def _prom_range(promql: str, hours: float, step_seconds: int) -> list[dict]:
    """区间查询。返回 [{ts, value}]，点数上限约 120（步长按窗口自动放大）。"""
    import httpx
    import time as _time

    end = _time.time()
    start = end - hours * 3600
    async with httpx.AsyncClient(timeout=_PROMETHEUS_TIMEOUT_S) as client:
        resp = await client.get(
            f"{_prometheus_url()}/api/v1/query_range",
            params={
                "query": promql,
                "start": f"{start:.3f}",
                "end": f"{end:.3f}",
                "step": str(step_seconds),
            },
        )
        resp.raise_for_status()
        body = resp.json()
    if body.get("status") != "success":
        raise RuntimeError(f"prometheus query failed: {body.get('errorType')}")
    series = body.get("data", {}).get("result", [])
    if not series:
        return []
    merged: dict[int, float] = {}
    for s in series:  # 多条（按 route 分片）求和为总量
        for ts, val in s.get("values", []):
            merged[int(float(ts))] = merged.get(int(float(ts)), 0.0) + float(val)
    return [{"ts": ts, "value": round(v, 4)} for ts, v in sorted(merged.items())]


@router.get("/gateway-auth")
async def get_gateway_auth(hours: float = Query(6, gt=0, le=24 * 30)):
    """网关认证/限流指标（代理 Prometheus，只读）。

    数据源：apisix_gateway_auth_denied_total / _would_deny_total（gateway-auth 插件）
    与 apisix_http_status（prometheus 插件）。Prometheus 未启动（observability
    profile 可选）时返回 available=false，前端显式降级而不是报错。
    """
    window = f"{hours:g}h"
    job = '{job="agent-platform-apisix"}'
    step = max(300, int(hours * 3600 / 120))
    import asyncio

    try:
        denied_by_reason, would_deny, codes, series = await asyncio.gather(
            _prom_instant(f"sum by (reason) (increase(apisix_gateway_auth_denied_total{job}[{window}]))"),
            _prom_instant(f"sum by (reason) (increase(apisix_gateway_auth_would_deny_total{job}[{window}]))"),
            _prom_instant(f'sum by (code) (increase(apisix_http_status{job}{{code=~"401|429"}}[{window}]))'),
            _prom_range(f"sum(increase(apisix_gateway_auth_denied_total{job}[5m]))", hours, step),
        )
    except Exception as e:  # 连接拒绝/超时/prom 未启动都归为「数据源不可用」
        logger.warning(f"[GatewayAuth] Prometheus 不可达: {e}")
        return {"available": False, "window_hours": hours, "error": str(e)}

    def _top(items: list[dict], label: str) -> list[dict]:
        rows = [
            {label: (i["labels"].get(label) or "unknown"), "count": i["value"]}
            for i in items if i["value"] > 0
        ]
        return sorted(rows, key=lambda r: -r["count"])

    return {
        "available": True,
        "window_hours": hours,
        "total_denied": round(sum(r["count"] for r in _top(denied_by_reason, "reason")), 2),
        "denied_by_reason": _top(denied_by_reason, "reason"),
        "would_deny_by_reason": _top(would_deny, "reason"),
        "status_codes": _top(codes, "code"),
        "denied_series": series,
    }