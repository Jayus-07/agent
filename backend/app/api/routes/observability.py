"""可观测性 REST API — traces / metrics / resources / alerts / graph

数据源统一在 `backend.rag.tracer.trace_collector`：
SQLite TraceStore 直读（Langfuse 已下线，2026-09-16）。
"""

import os
import json
import time
import hashlib
import threading

from fastapi import APIRouter, Query, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from backend.shared.logger import logger

from backend.observability.topology import GRAPH_TOPOLOGY, NODE_LABELS
from backend.observability.resource import resource_monitor
from backend.rag.metrics import metrics_collector
from backend.observability.tracer import trace_collector, TraceRecord, Span
from backend.observability.trace_store import get_trace_store
from backend.memory.database import AsyncSessionLocal

router = APIRouter(prefix="/observability", tags=["可观测性"])


# ═══════════════════════════════════════════════════
# 敏感接口收口 —— 网关审计类端点（汇总文档 A3，2026-09-16）
# ═══════════════════════════════════════════════════
#
# 收口前：网关对「无 Bearer 但有 X-API-Key」的请求直接放行（打标 api-key），
# 后端中间件只比对该 Key；而该 Key 正是前端的 NEXT_PUBLIC_API_KEY——编译进
# 浏览器 bundle 的**公开值**。实测仅凭此 Key（无需登录）即可 200 拿到完整
# 网关访问审计明细。同时网关角色闸 ROLE_GATE_PREFIXES 未覆盖 observability，
# 任何 viewer 登录用户也能读全部审计数据。
#
# 收口为：仅允许「经网关验签的 JWT 用户且 role=admin」访问。
#   - 服务凭据通道（X-Internal-Token）**不再**放行：这两个端点是管理端页面
#     专用，无服务间消费方，收窄通道可一并堵住 api-key 匿名访问；
#   - 开关 SENSITIVE_API_GUARD_MODE=audit 可回到「仅记日志、行为不变」，
#     用于灰度回退（与汇总文档 A3 的 audit 先行策略一致）。

def _sensitive_guard_mode() -> str:
    # 2026-09-16 动态化：DB 覆盖层（免重启）→ env 兜底，见 services/sys_config.py
    from backend.services.sys_config import get_mode
    return get_mode("SENSITIVE_API_GUARD_MODE")


async def require_admin_operator(request: Request) -> None:
    """网关审计类端点的管理员闸（A3 收口；enforce 默认 / audit 可回退）。"""
    from backend.app.api.deps import resolve_operator_role  # 局部导入避免循环依赖

    ident = await resolve_operator_role(request)
    # actor 形如 "user:<id>"（JWT 通道）或 "service:internal-token"（服务凭据）
    if ident.role == "admin" and ident.actor.startswith("user:"):
        return
    if _sensitive_guard_mode() == "audit":
        logger.warning(
            f"[SensitiveGuard] audit 模式放行非管理员访问网关审计数据: "
            f"actor={ident.actor} role={ident.role}"
        )
        return
    raise HTTPException(
        status_code=403,
        detail={
            "error": "Forbidden",
            "message": "网关访问审计数据仅限管理员（role=admin）访问；"
                       "服务级 API Key 通道不可访问（该 Key 会下发到浏览器）",
        },
    )


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
    """最近 N 条 trace 摘要（SQLite TraceStore）。

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
    """获取单条 trace 完整详情（SQLite TraceStore）"""
    # trace_collector.get() 直读 SQLite TraceStore；
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
    """获取单条 RAG Trace 详情（SQLite TraceStore）"""
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
async def get_gateway_auth(request: Request, hours: float = Query(6, gt=0, le=24 * 30)):
    """网关认证/限流指标（代理 Prometheus，只读）。

    数据源：apisix_gateway_auth_denied_total / _would_deny_total（gateway-auth 插件）
    与 apisix_http_status（prometheus 插件）。Prometheus 未启动（observability
    profile 可选）时返回 available=false，前端显式降级而不是报错。

    权限：仅管理员（A3 收口，见 require_admin_operator）。
    """
    await require_admin_operator(request)
    window = f"{hours:g}h"
    job = '{job="agent-platform-apisix"}'
    # ⚠️ 状态码查询必须与 job 写在**同一个**选择器内，用逗号分隔。
    # 曾写成 `apisix_http_status{job="..."}{code=~"401|429"}`（两段并列花括号），
    # 是非法 PromQL → Prometheus 400 parse error → 整个接口降级为
    # available=false（2026-09-16 实测修复）。
    status_sel = '{job="agent-platform-apisix",code=~"401|429"}'
    step = max(300, int(hours * 3600 / 120))
    import asyncio

    # 单点容错：任一查询失败不应拖垮整个接口。此前四路 gather 中只要一路抛
    # 异常就整体降级，导致明明有数据的 denied_by_reason 也一并拿不到
    # （2026-09-16 实测：status_codes 拼错后全盘 available=false）。
    gathered = await asyncio.gather(
        _prom_instant(f"sum by (reason) (increase(apisix_gateway_auth_denied_total{job}[{window}]))"),
        _prom_instant(f"sum by (reason) (increase(apisix_gateway_auth_would_deny_total{job}[{window}]))"),
        _prom_instant(f"sum by (code) (increase(apisix_http_status{status_sel}[{window}]))"),
        _prom_range(f"sum(increase(apisix_gateway_auth_denied_total{job}[5m]))", hours, step),
        return_exceptions=True,
    )

    errors: list[str] = []

    def _or_default(idx: int, default):
        r = gathered[idx]
        if isinstance(r, BaseException):
            errors.append(f"query#{idx}: {r}")
            logger.warning(f"[GatewayAuth] Prometheus 查询 {idx} 失败（该分项置空）: {r}")
            return default
        return r

    denied_by_reason = _or_default(0, [])
    would_deny = _or_default(1, [])
    codes = _or_default(2, [])
    series = _or_default(3, [])

    # 全部失败 = Prometheus 不可达/未启动 → 整体降级（前端显示启动指引）
    if len(errors) == len(gathered):
        logger.warning(f"[GatewayAuth] Prometheus 不可达: {errors[0]}")
        return {"available": False, "window_hours": hours, "error": errors[0]}

    def _top(items: list[dict], label: str) -> list[dict]:
        rows = [
            # 计数取整：increase() 补偿 counter 重置时会产生浮点（如 21.03 次），
            # 次数语义必须是整数，否则前端出现「认证拒绝 21.03」这种显示
            {label: (i["labels"].get(label) or "unknown"), "count": int(round(i["value"]))}
            for i in items if i["value"] > 0
        ]
        return sorted(rows, key=lambda r: -r["count"])

    return {
        "available": True,
        "window_hours": hours,
        # 分项查询失败时非空：此时数据是**部分可用**的（前端未消费该字段，
        # 仅用于排障——避免"整体 available=false"掩盖"其实有数据"）
        "partial_errors": errors,
        "total_denied": int(round(sum(r["count"] for r in _top(denied_by_reason, "reason")))),
        "denied_by_reason": _top(denied_by_reason, "reason"),
        "would_deny_by_reason": _top(would_deny, "reason"),
        "status_codes": _top(codes, "code"),
        "denied_series": series,
    }


# ═══════════════════════════════════════════════════
# System Health（2026-09-16：管理端「系统健康」数据源）
# ═══════════════════════════════════════════════════
#
# 设计约束：celery inspect 是阻塞广播（秒级），绝不能随页面请求实时打。
# 采集函数整体在 threadpool 执行 + 进程内 15s 缓存——多标签页/轮询共享一份
# 快照（与 gateway-access-logs 微缓存同模式，但 TTL 更长：广播成本更高）。

_SYSTEM_HEALTH_CACHE_TTL = 15.0
_system_health_cache: dict[str, tuple[float, dict]] = {}
_system_health_lock = threading.Lock()


def _redis_health_block() -> dict:
    """业务 Redis(db0) + Celery broker(db1) + result(db2) 三段健康。"""
    import redis as redis_lib

    from backend.config.tasks import CELERY_BROKER_URL, CELERY_RESULT_BACKEND

    def _probe(url: str) -> dict:
        t0 = time.monotonic()
        client = redis_lib.Redis.from_url(
            url, socket_connect_timeout=2, socket_timeout=2, decode_responses=True)
        try:
            client.ping()
            latency_ms = round((time.monotonic() - t0) * 1000, 1)
            info = client.info("server")
            mem = client.info("memory")
            return {
                "available": True,
                "latency_ms": latency_ms,
                "version": info.get("redis_version", ""),
                "used_memory_human": mem.get("used_memory_human", ""),
                "connected_clients": client.info("clients").get("connected_clients"),
                "keys": int(client.dbsize()),
            }
        except Exception as e:  # noqa: BLE001 — 单段失败不拖垮整体
            return {"available": False, "error": str(e)[:200]}
        finally:
            try:
                client.close()
            except Exception:
                pass

    block = {"db0": _probe(os.getenv("REDIS_URL", "redis://localhost:6379/0")),
             "broker": _probe(CELERY_BROKER_URL),
             "result": _probe(CELERY_RESULT_BACKEND)}

    # 队列长度（broker db1；默认队列名取 celery 配置）
    if block["broker"]["available"]:
        try:
            from backend.tasks.celery_app import celery_app

            qname = celery_app.conf.task_default_queue or "agent"
            client = redis_lib.Redis.from_url(
                CELERY_BROKER_URL, socket_connect_timeout=2, socket_timeout=2,
                decode_responses=True)
            try:
                block["queues"] = {qname: int(client.llen(qname))}
            finally:
                client.close()
        except Exception as e:  # noqa: BLE001
            block["queues"] = {}
            block["queues_error"] = str(e)[:200]
    return block


def _db_health_block() -> dict:
    """PG 健康健查：PING 延迟 / 版本 / 活跃连接 / tasks 表可用性。"""
    t0 = time.monotonic()
    try:
        from backend.config.database import MEMORY_DB_CONFIG
        import psycopg

        c = MEMORY_DB_CONFIG
        with psycopg.connect(
            f"postgresql://{c['user']}:{c['password']}@{c['host']}:{c['port']}/{c['dbname']}",
            connect_timeout=3, autocommit=True,
        ) as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
            latency_ms = round((time.monotonic() - t0) * 1000, 1)
            cur.execute("SHOW server_version")
            version = str(cur.fetchone()[0]).split()[0]  # type: ignore[index]
            cur.execute("SELECT count(*) FROM pg_stat_activity WHERE state = 'active'")
            active = int(cur.fetchone()[0])  # type: ignore[index]
            cur.execute("SELECT count(*) FROM tasks WHERE status IN "
                        "('PENDING','RUNNING','WAITING_USER','PAUSED')")
            open_tasks = int(cur.fetchone()[0])  # type: ignore[index]
        return {"available": True, "latency_ms": latency_ms, "version": version,
                "active_connections": active, "open_tasks": open_tasks}
    except Exception as e:  # noqa: BLE001
        return {"available": False, "error": str(e)[:200]}


def _worker_health_block() -> dict:
    """Celery Worker 广播采集（阻塞调用，仅在缓存 miss 的 threadpool 里执行）。"""
    try:
        from backend.tasks.celery_app import celery_app

        insp = celery_app.control.inspect(timeout=2.0)
        ping = insp.ping() or {}
        stats = insp.stats() or {}
        active = insp.active() or {}
        workers = [
            {
                "name": name,
                "concurrency": (stats.get(name, {}) or {}).get("pool", {}).get("max-concurrency"),
                "version": (stats.get(name, {}) or {}).get("version", ""),
                "active_tasks": len(active.get(name, []) or []),
            }
            for name in ping
        ]
        return {"available": True, "online_count": len(workers), "workers": workers,
                "collected_at": time.time()}
    except Exception as e:  # noqa: BLE001
        return {"available": False, "error": str(e)[:200], "workers": []}


def _tasks_health_block() -> dict:
    """任务面统计（24h）+ 最近失败 TOP5。"""
    try:
        from backend.services import task_service

        stats = task_service.stats_tasks(hours=24)
        fails, _total = task_service.list_tasks_admin(status="FAILED", hours=24, limit=5)
        stats["recent_failures"] = [
            {"task_id": r.id, "error_type": r.error_type,
             "error_message": r.error_message[:200],
             "worker": r.worker, "retry_count": r.retry_count,
             "finished_at": r.finished_at.isoformat() if r.finished_at else None}
            for r in fails
        ]
        return {"available": True, **stats}
    except Exception as e:  # noqa: BLE001
        return {"available": False, "error": str(e)[:200]}


def _collect_system_health() -> dict:
    return {
        "collected_at": time.time(),
        "redis": _redis_health_block(),
        "db": _db_health_block(),
        "worker": _worker_health_block(),
        "tasks": _tasks_health_block(),
    }


@router.get("/system-health")
async def get_system_health(request: Request):
    """系统健康快照（Redis/DB/Worker/任务面，只读）。

    权限：仅管理员（require_admin_operator）。15s 进程内缓存——celery inspect
    广播是秒级阻塞操作，页面轮询共享同一份快照，不随请求实时广播。
    单段不可用以 available=false 降级，不影响其余段。
    """
    await require_admin_operator(request)
    now = time.monotonic()
    with _system_health_lock:
        cached = _system_health_cache.get("v")
        if cached and cached[0] > now:
            return JSONResponse(cached[1], headers={"Cache-Control": "no-cache"})
    from starlette.concurrency import run_in_threadpool

    payload = await run_in_threadpool(_collect_system_health)
    with _system_health_lock:
        _system_health_cache["v"] = (now + _SYSTEM_HEALTH_CACHE_TTL, payload)
    return JSONResponse(payload, headers={"Cache-Control": "no-cache"})


# ═══════════════════════════════════════════════════
# Gateway Access Logs（2026-09-16：访问审计明细，管理端「网关安全」页）
# ═══════════════════════════════════════════════════

# 微缓存（2026-09-16）：管理端 30s 轮询 × 多标签会把同一查询参数打到 PG；
# 进程内 5s 缓存把重叠轮询合并为 1 次 DB 查询。数据为管理员全局审计视图
# （require_admin_operator 后），同参数同结果，无按用户越权风险。
_ACCESS_LOGS_CACHE_TTL = 5.0
_access_logs_cache: dict[str, tuple[float, str, dict]] = {}  # key -> (expires_at, etag, payload)
_access_logs_cache_lock = threading.Lock()


def _etag_of(payload: dict) -> str:
    body = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return f'"{hashlib.sha256(body.encode()).hexdigest()[:32]}"'


@router.get("/gateway-access-logs")
async def get_gateway_access_logs(
    request: Request,
    hours: float = Query(6, gt=0, le=24 * 30),
    user_id: str | None = Query(None, max_length=128),
    ip: str | None = Query(None, max_length=64),
    path: str | None = Query(None, max_length=200),
    abnormal_only: bool = Query(False, description="仅看 4xx/5xx（安全审计默认视角）"),
    include_noise: bool = Query(False, description="包含 /health 心跳与 /observability 自引用（默认排除）"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """网关访问审计明细（APISIX → Redis Streams → ai.gateway_access_logs）。

    user_id 可信性说明（前端需展示提示）：经 gateway-auth 验签注入的头可信；
    /api/auth/*、/api/sys/* 白名单路由不挂插件，客户端自带头原样透传，
    对应行的 auth_type 为空。username 由 auth.users LEFT JOIN 回显
    （user_id 为纯数字才尝试关联，u-* / user:* 等格式安全跳过）。

    默认排除两类噪音（include_noise=true 可看回）：
    - /health 心跳；- /observability/* 自引用——审计查询本身也会被记录，
      30s 轮询下列表会持续自我膨胀。

    排序恒为异常优先：status>=400 在前，其余按时间倒序。

    表未建（迁移未跑）或 PG 不可达时 available=false，前端显式降级。

    权限：仅管理员（A3 收口，见 require_admin_operator）。
    """
    await require_admin_operator(request)

    # 微缓存命中：同参数 5s 内复用上次结果（多标签轮询合并为一次 DB 查询）
    cache_key = "&".join(f"{k}={v}" for k, v in sorted(request.query_params.items()))
    now = time.monotonic()
    with _access_logs_cache_lock:
        cached = _access_logs_cache.get(cache_key)
        cache_hit = bool(cached and cached[0] > now)
        if cache_hit:
            etag, payload = cached[1], cached[2]
    if cache_hit:
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers={"ETag": etag, "Cache-Control": "no-cache"})
        return JSONResponse(payload, headers={"ETag": etag, "Cache-Control": "no-cache"})

    from datetime import datetime, timedelta, timezone

    from sqlalchemy import text
    from sqlalchemy.exc import NoSuchTableError, ProgrammingError

    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    where = ["l.ts >= :cutoff"]
    params: dict = {"cutoff": cutoff, "limit": limit, "offset": offset}
    if user_id:
        where.append("l.user_id = :user_id")
        params["user_id"] = user_id
    if ip:
        where.append("l.client_ip = :ip")
        params["ip"] = ip
    if path:
        where.append("(l.uri ILIKE :path OR l.query ILIKE :path)")
        params["path"] = f"%{path}%"
    if abnormal_only:
        where.append("l.status >= 400")
    if not include_noise:
        where.append("l.uri NOT LIKE '/observability/%'")
        where.append("l.uri NOT LIKE '/api/observability/%'")
        where.append("l.uri NOT IN ('/health', '/api/health')")
    where_sql = " AND ".join(where)

    try:
        async with AsyncSessionLocal() as db:
            total = (await db.execute(
                text(f"SELECT count(*) FROM ai.gateway_access_logs l WHERE {where_sql}"),
                params,
            )).scalar_one()
            rows = (await db.execute(
                text(f"""
                    SELECT l.ts, l.client_ip, l.user_id, u.username, l.auth_type,
                           l.trace_id, l.method, l.uri, l.query, l.status, l.bytes,
                           l.duration_ms, l.ua
                    FROM ai.gateway_access_logs l
                    LEFT JOIN auth.users u
                      ON u.id = CASE WHEN l.user_id ~ '^[0-9]+$' THEN l.user_id::bigint END
                    WHERE {where_sql}
                    ORDER BY (l.status >= 400) DESC, l.ts DESC
                    LIMIT :limit OFFSET :offset
                """),
                params,
            )).mappings().all()
    except (ProgrammingError, NoSuchTableError, OSError) as e:
        # 42P01 undefined_table 等归为数据源不可用；OSError=PG 连不上
        logger.warning(f"[GatewayAccessLogs] 数据源不可用: {e}")
        return {"available": False, "window_hours": hours, "error": str(e)}

    payload = {
        "available": True,
        "window_hours": hours,
        "total": total,
        "logs": [
            {
                "ts": r["ts"].isoformat(),
                "client_ip": r["client_ip"],
                "user_id": r["user_id"],
                "username": r["username"],
                "trace_id": r["trace_id"],
                "method": r["method"],
                "uri": r["uri"],
                "query": r["query"],
                "status": r["status"],
                "bytes": r["bytes"],
                "duration_ms": r["duration_ms"],
                "ua": r["ua"],
            }
            for r in rows
        ],
    }

    # 写缓存 + ETag 协商：浏览器持有旧 ETag 时回 304，省响应体传输
    etag = _etag_of(payload)
    now = time.monotonic()
    with _access_logs_cache_lock:
        if len(_access_logs_cache) > 256:  # 机会式清理，防参数组合爆炸
            expired = [k for k, v in _access_logs_cache.items() if v[0] <= now]
            for k in expired:
                _access_logs_cache.pop(k, None)
        _access_logs_cache[cache_key] = (now + _ACCESS_LOGS_CACHE_TTL, etag, payload)
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag, "Cache-Control": "no-cache"})
    return JSONResponse(payload, headers={"ETag": etag, "Cache-Control": "no-cache"})