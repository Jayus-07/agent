"""Tool 治理统计 API（M2 / 台账 D2）

GET /admin/tools        — 34 Tool 清单（契约 hash + 归属，复用 M1 lock 派生）
GET /admin/tools/stats  — 运行统计聚合（总数/成功/失败/成功率/Top 失败/Top 错误类）
POST /admin/tools/failure-analysis — AI 失败分析（决策 #20：聚类 → LLM 归因/
                          建议/候选评测用例；只建议不自动应用任何正式配置）
GET /admin/tools/failure-analysis  — 本租户最近分析列表

为什么存在：``agent_tool_*`` 指标齐全但管理端无聚合视图，管理员答不了
「哪个 Tool 在失败、为什么」。``skill_failure_total`` 此前是死指标
（定义零埋点），已随本债补埋（skills/base 失败出口）。

数据源：**TOOL_STATS_SOURCE**（P1-4，默认 process）——
  process = 进程内 Prometheus REGISTRY 直读（prometheus_client 单例），
            不依赖外部 Prometheus 可达性（本机部署零额外依赖）；
  redis   = 仅 Redis 日键合计（TOOL_STATS_REDIS_ENABLED 写侧开启后多副本合计）；
  merged  = 两者相加（scope 如实反映）。
错误分类口径 = M3 统一七分类（observability.error_taxonomy）。
权限分级：GET（清单/统计/错误明细/变更历史/分析列表）editor 及以上（与前端
「能力治理」导航 minRole 对齐）；失败探针与 AI 失败分析保持 admin +
ADMIN_ACTION 审计。
"""
from __future__ import annotations

import asyncio

import json
import re
import time
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException as _HTTP, Request
from pydantic import BaseModel, Field

from backend.app.api.deps import require_admin_user, require_editor_user, run_governance_mutation
from backend.config import ENVIRONMENT
from backend.shared.logger import logger

router = APIRouter(prefix="/admin/tools", tags=["Admin-Tools"])

# lock 是构建期产物，同一进程内只有文件本身变化（mtime_ns）才会换内容；
# stats 全量 REGISTRY.collect() 代价不低，看板 5 秒陈旧可接受。
_LOCK_CACHE: dict[str, Any] = {"mtime_ns": None, "raw": None}
_STATS_TTL_SECONDS = 5.0
_STATS_CACHE: dict[str, Any] = {"at": 0.0, "payload": None}


def _load_tool_lock(path: Path | None = None) -> tuple[dict, str, str]:
    """读契约 lock（进程内 mtime 缓存），返回 (tools, git_sha, error)。

    path 仅供测试注入；读失败软失败返回 error 字符串，调用方按空清单 +
    lock_error 呈现，不 500；失败不缓存，下一次请求照常重试（lock 可能
    随发布被补齐）。
    """
    path = path or (Path(__file__).resolve().parents[4]
                    / "backend" / "tool_contracts.lock.json")
    try:
        mtime_ns = path.stat().st_mtime_ns
        if _LOCK_CACHE["mtime_ns"] == mtime_ns and _LOCK_CACHE["raw"] is not None:
            raw = _LOCK_CACHE["raw"]
        else:
            raw = json.loads(path.read_text(encoding="utf-8"))
            _LOCK_CACHE.update(mtime_ns=mtime_ns, raw=raw)
    except (OSError, ValueError) as e:
        return {}, "", f"lock 读取失败: {e}"
    return raw.get("tools", {}), str(raw.get("git_sha", "")), ""

_PROBE_ERROR_CLASSES = frozenset({
    "timeout", "network_error", "permission_denied", "validation_error",
    "business_error", "contract_error", "provider_error",
})
_PROBE_STATUS = {
    "timeout": "timeout",
    "network_error": "unavailable",
    "permission_denied": "unauthorized",
    "validation_error": "invalid_request",
    "business_error": "failed",
    "contract_error": "degraded",
    "provider_error": "rate_limited",
}
_PROBE_MESSAGE = {
    "timeout": "模拟上游响应超时",
    "network_error": "模拟上游网络不可用",
    "permission_denied": "模拟上游拒绝访问",
    "validation_error": "模拟 Tool 参数校验失败",
    "business_error": "模拟 Tool 业务失败",
    "contract_error": "模拟 Tool 契约降级",
    "provider_error": "模拟供应商限流",
}


class ToolFailureProbeRequest(BaseModel):
    """管理端只生成观测数据的 Tool 失败探针请求。"""

    tool: str = Field(..., min_length=1, max_length=200)
    error_class: str = Field(..., min_length=1, max_length=64)
    domain: str = Field("", max_length=64)


def _collect_samples(*metric_names: str) -> dict[str, list[dict]]:
    """从进程内 REGISTRY 抽取目标指标的样本（labels+value），软失败返回空。"""
    wanted = set(metric_names)
    found: dict[str, list[dict]] = {}
    try:
        from prometheus_client import REGISTRY

        for metric in REGISTRY.collect():
            if metric.name not in wanted:
                continue
            found.setdefault(metric.name, [])
            for sample in metric.samples:
                if sample.name.endswith("_created"):  # Counter 附带的 _created 序列
                    continue
                found[metric.name].append({
                    "labels": dict(sample.labels),
                    "value": sample.value,
                })
    except Exception:
        pass
    return found


def _collect_redis_tool_stats() -> dict[str, dict]:
    """P1-4 读侧：从 Redis 日键读多副本合计，field 形态与写侧对称。

    不可达/未开启/键缺失返回空 dict（软失败，调用方按可用数据聚合）。
    """
    try:
        from datetime import date

        from backend.config.redis import REDIS_KEY_PREFIX
        from backend.infra.redis.client import get_redis

        r = get_redis()
        if r is None:
            return {}
        key = f"{REDIS_KEY_PREFIX or 'agent:'}tool_stats:{date.today():%Y%m%d}"
        data = r.hgetall(key) or {}
    except Exception as e:  # noqa: BLE001 — 读侧软失败
        logger.debug(f"[AdminTools] Redis 统计读取失败（按空数据处理）: {e}")
        return {}

    tools: dict[str, dict] = {}
    for field, count in data.items():
        tool, _, kind = field.rpartition(":")
        if not tool or not kind:
            continue
        entry = tools.setdefault(tool, {"tool": tool, "domain": "",
                                        "calls": 0.0, "success": 0.0, "failures": 0.0})
        count = float(count)
        if kind == "total":
            entry["calls"] += count
        elif kind == "ok":
            entry["success"] += count
        else:
            entry.setdefault("error_classes", {})
            entry["error_classes"][kind] = entry["error_classes"].get(kind, 0.0) + count
    for entry in tools.values():
        entry["failures"] = entry["calls"] - entry["success"]
    return tools


def _merge_tool_entries(base: dict[str, dict], extra: dict[str, dict]) -> None:
    """merged 模式：extra（Redis 合计）并入 base（进程内），同名逐项相加。"""
    for name, entry in extra.items():
        cur = base.setdefault(name, {"tool": name, "domain": "",
                                     "calls": 0.0, "success": 0.0, "failures": 0.0})
        cur["calls"] += entry.get("calls", 0.0)
        cur["success"] += entry.get("success", 0.0)
        cur["failures"] += entry.get("failures", 0.0)
        for cls, n in (entry.get("error_classes") or {}).items():
            cur.setdefault("error_classes", {})
            cur["error_classes"][cls] = cur["error_classes"].get(cls, 0.0) + n


def _aggregate_tool_stats(source: str | None = None) -> dict[str, Any]:
    """聚合 agent_tool_calls_total / error_class / timeout 指标为管理端视图。

    source（P1-4）：None = 取 TOOL_STATS_SOURCE 配置；process/redis/merged。
    """
    from backend.config import TOOL_STATS_SOURCE

    source = source or TOOL_STATS_SOURCE
    samples = _collect_samples(
        "agent_tool_calls_total",
        "agent_tool_error_class_total",
        "agent_tool_timeout_total",
        "skill_failure_total",
    )
    calls = samples.get("agent_tool_calls_total", [])
    error_classes = samples.get("agent_tool_error_class_total", [])

    tools: dict[str, dict] = {}
    if source != "redis":
        for s in calls:
            name = s["labels"].get("tool", "?")
            status = s["labels"].get("status", "?")
            entry = tools.setdefault(
                name, {"tool": name, "domain": s["labels"].get("domain", ""),
                       "calls": 0.0, "success": 0.0, "failures": 0.0})
            entry["calls"] += s["value"]
            if status == "success":
                entry["success"] += s["value"]
            else:
                entry["failures"] += s["value"]

        for s in error_classes:
            name = s["labels"].get("tool", "?")
            entry = tools.setdefault(name, {"tool": name, "domain": "", "calls": 0.0,
                                            "success": 0.0, "failures": 0.0})
            entry.setdefault("error_classes", {})
            cls = s["labels"].get("error_class", "unknown")
            entry["error_classes"][cls] = entry["error_classes"].get(cls, 0.0) + s["value"]

    if source in ("redis", "merged"):
        redis_tools = _collect_redis_tool_stats()
        if source == "redis":
            tools = redis_tools
        else:
            _merge_tool_entries(tools, redis_tools)

    for entry in tools.values():
        entry["success_rate"] = (
            round(entry["success"] / entry["calls"], 4) if entry["calls"] else None)

    ranked = sorted(tools.values(), key=lambda e: (-e["failures"], -e["calls"]))
    top_failed = [
        {"tool": e["tool"], "failures": e["failures"], "calls": e["calls"],
         "error_classes": e.get("error_classes", {})}
        for e in ranked if e["failures"] > 0
    ][:10]

    error_totals: dict[str, float] = {}
    for s in error_classes:
        cls = s["labels"].get("error_class", "unknown")
        error_totals[cls] = error_totals.get(cls, 0.0) + s["value"]
    top_error_classes = sorted(error_totals.items(), key=lambda kv: -kv[1])[:8]

    skill_failures = {}
    for s in samples.get("skill_failure_total", []):
        key = (s["labels"].get("skill", "?"), s["labels"].get("error_type", "unknown"))
        skill_failures[key] = skill_failures.get(key, 0.0) + s["value"]

    total_calls = sum(e["calls"] for e in tools.values())
    total_success = sum(e["success"] for e in tools.values())
    # scope 如实反映数据源（P1-4）：process=单进程 REGISTRY | redis=多副本日键
    # 合计 | merged=两者相加
    scope = source if source in ("process", "redis", "merged") else "process"
    return {
        "scope": scope,
        "totals": {
            "tools_seen": len(tools),
            "calls": total_calls,
            "success": total_success,
            "failures": total_calls - total_success,
            "success_rate": round(total_success / total_calls, 4) if total_calls else None,
        },
        "top_failed_tools": top_failed,
        "top_error_classes": [
            {"error_class": cls, "count": n} for cls, n in top_error_classes
        ],
        "skill_failures": [
            {"skill": k[0], "error_type": k[1], "count": v}
            for k, v in sorted(skill_failures.items(), key=lambda kv: -kv[1])
        ],
        "tools": sorted(tools.values(), key=lambda e: e["tool"]),
    }


def _cached_tool_stats() -> dict[str, Any]:
    """stats 聚合短 TTL 缓存：/stats 与 /inventory 共用，避免每请求全量 collect。

    失败不缓存（聚合本身各数据源软失败、永不抛错），TTL 过期即重算。
    """
    now = time.monotonic()
    payload = _STATS_CACHE["payload"]
    if payload is None or now - _STATS_CACHE["at"] > _STATS_TTL_SECONDS:
        payload = _aggregate_tool_stats()
        _STATS_CACHE.update(at=now, payload=payload)
    return payload


def _quota_runtime(ds: dict | None) -> dict | None:
    """额度运行时读数（软失败）：声明来自 labels.py data_source.quota。

    usage_provider → travel live 软预算（current_usage/daily_budget）；
    usage_counter → 月键 tool_quota:{counter}:{YYYYMM}（field=upstream_tool）。
    status 三态诚实口径：unlimited=未设预算（不限）/ ok / exhausted（软停）；
    计数源不可用 → untracked（Redis 不可达等），绝不把 0 伪装成已用 0。
    """
    if not ds or not ds.get("quota"):
        return None
    quota = ds["quota"]
    out: dict[str, Any] = {
        "period": quota.get("period"),
        "limit": quota.get("limit"),
        "limit_env": quota.get("limit_env"),
        "note": quota.get("note"),
        "usage": None,
        "status": "untracked",
    }
    try:
        if quota.get("usage_provider"):
            from backend.providers.travel.live.quota import current_usage, daily_budget

            provider = quota["usage_provider"]
            budget = daily_budget(provider)
            usage = current_usage(provider)
            out.update({
                "usage": usage,
                "budget": budget,
                "status": ("unlimited" if budget <= 0
                           else "exhausted" if usage >= budget else "ok"),
            })
        elif quota.get("usage_counter"):
            from datetime import date

            from backend.config.redis import REDIS_KEY_PREFIX
            from backend.infra.redis.client import get_redis

            r = get_redis()
            if r is not None:
                key = (f"{REDIS_KEY_PREFIX or 'agent:'}"
                       f"tool_quota:{quota['usage_counter']}:{date.today():%Y%m}")
                raw = r.hget(key, ds.get("upstream_tool") or "")
                out.update({"usage": int(raw) if raw else 0, "status": "tracked"})
    except Exception as e:  # noqa: BLE001 — 读数软失败，声明信息照常返回
        logger.debug(f"[AdminTools] 额度读数失败（按 untracked 处理）: {e}")
    return out


@router.get("/stats")
async def tool_stats(request: Request):
    """Tool 运行统计聚合（TOOL_STATS_SOURCE 三源，M3 七分类口径）。"""
    await require_editor_user(request)
    return _cached_tool_stats()


@router.post("/failure-probe")
async def tool_failure_probe(request: Request, body: ToolFailureProbeRequest):
    """生成一条模拟 Tool 失败 Trace，用于管理端稳定验收错误展示。

    探针不调用 Tool、不访问外部服务，只复用生产的 ToolResult、指标、Trace
    和统一错误分类出口；生产环境明确拒绝，避免把模拟数据写入生产观测面。
    探针属管理端主动动作，除 synthetic trace 外补 ADMIN_ACTION 审计留痕。
    """
    await require_admin_user(request)
    if ENVIRONMENT == "production":
        raise _HTTP(403, "生产环境禁止执行 Tool 强制失败测试")
    if body.error_class not in _PROBE_ERROR_CLASSES:
        raise _HTTP(422, f"不支持的统一错误分类: {body.error_class}")

    lock_tools, _, lock_error = _load_tool_lock()
    if lock_error:
        raise _HTTP(503, f"Tool 契约 lock 读取失败: {lock_error}")
    contract = lock_tools.get(body.tool)
    if not contract:
        raise _HTTP(404, f"Tool 不在契约 lock 中: {body.tool}")

    from backend.core.tool_runtime.metrics import record_tool_result
    from backend.core.tool_runtime.models import ToolResult, ToolStatus
    from backend.core.tool_runtime.tracing import finish_tool_span, start_tool_span
    from backend.observability.tracer import trace_collector

    status = ToolStatus(_PROBE_STATUS[body.error_class])
    domain = body.domain or body.tool.split("_", 1)[0]
    trace = trace_collector.start(
        f"Tool 强制失败测试：{body.tool} / {body.error_class}",
        session_id=f"admin-probe-{uuid4().hex[:12]}",
        workflow_name="agent",
    )
    trace.tags.update({
        "synthetic": True,
        "tool_failure_probe": True,
        "tool": body.tool,
        "error_class": body.error_class,
    })
    span = start_tool_span(
        body.tool,
        capability=(contract.get("capabilities") or [""])[0],
        params={"failure_probe": True, "error_class": body.error_class},
        agent="admin_tool_failure_probe",
    )
    result = ToolResult(
        status=status,
        tool_name=body.tool,
        latency_ms=0,
        error_code=f"SIMULATED_{body.error_class.upper()}",
        error_message=_PROBE_MESSAGE[body.error_class],
    )
    record_tool_result(result, domain=domain, tool_name=body.tool)
    finish_tool_span(span, result)
    trace.tags["probe_status"] = "simulated"
    trace_collector.finish(
        trace,
        result.error_message or "",
        0,
        model="admin-probe",
        provider="synthetic",
    )
    # 操作审计旁路：record_security_event 自身软失败永不抛错，这里再兜一层
    # 保险（探针结果已生成，不能被审计故障带崩）。
    try:
        from backend.security.events import record_security_event

        record_security_event(
            "ADMIN_ACTION",
            category="tool_failure_probe",
            detail={"tool": body.tool, "error_class": body.error_class,
                    "trace_id": trace.id, "simulated": True},
        )
    except Exception:  # noqa: BLE001 — 审计旁路软失败
        logger.debug("[AdminTools] 探针审计写入失败（不影响探针结果）")
    return {
        "trace_id": trace.id,
        "tool": body.tool,
        "error_class": body.error_class,
        "status": result.status.value,
        "simulated": True,
    }


@router.get("")
async def tool_inventory(request: Request):
    """Tool 治理清单：lock 契约（hash/归属/output_type）× 运行统计合并视图。"""
    await require_editor_user(request)

    lock, lock_git_sha, lock_error = _load_tool_lock()

    stats = {t["tool"]: t for t in _cached_tool_stats()["tools"]}
    inventory = []
    for name, entry in sorted(lock.items()):
        run = stats.get(name, {})
        inventory.append({
            "name": name,
            "display_name": entry.get("display_name", ""),
            "data_source": entry.get("data_source"),
            "quota_runtime": _quota_runtime(entry.get("data_source")),
            "args_schema": entry.get("args_schema", {}),
            "module": entry.get("module", ""),
            "capabilities": entry.get("capabilities", []),
            "output_types": entry.get("output_types", {}),
            "content_hash": entry.get("content_hash", ""),
            "description_hash": entry.get("description_hash", ""),
            "runtime": {k: run.get(k) for k in
                        ("calls", "success", "failures", "success_rate", "error_classes")},
        })
    return {
        "count": len(inventory),
        "lock_git_sha": lock_git_sha,
        "lock_error": lock_error,
        "tools": inventory,
    }


@router.get("/constraints")
async def tool_constraints(request: Request):
    """工具执行约束视图（Batch 3，预算页只读区块数据源）。

    每个 capability 一行：超时 / 每请求调用上限 / 并发隔离 / 重试 / 熔断，
    来源 = ToolPolicy（静态策略表）∪ ToolSpec（动态 mcp.* 来自审核
    governance）。只读派生，不提供写路径——动态工具的约束修改走
    /admin/mcp 审核流。
    """
    await require_editor_user(request)
    from backend.core.tool_governance.registry import all_tool_specs
    from backend.core.tool_runtime.policy import get_policy

    rows = []
    for capability, spec in sorted(all_tool_specs().items()):
        policy = get_policy(capability)
        rows.append({
            "capability": capability,
            "source": "dynamic" if capability.startswith("mcp.") else "static",
            "operation": spec.operation.value,
            "risk_level": spec.risk_level.value,
            "timeout_ms": policy.timeout_ms,
            "max_calls_per_request": spec.max_calls_per_request,
            "bulkhead_limit": policy.bulkhead_limit,
            "retries": policy.retries,
            "circuit_breaker": policy.circuit_breaker,
            "rate_limit_enabled": spec.rate_limit_enabled,
            "rate_limit_requests": spec.rate_limit_requests,
            "rate_limit_window_seconds": spec.rate_limit_window_seconds,
        })
    return {"count": len(rows), "constraints": rows}


@router.get("/errors")
async def tool_errors(
    request: Request,
    tool: str | None = None,
    limit: int = 20,
    scan: int = 100,
):
    """Tool 错误来源明细：进程内 trace 直读，不另建存储。

    按 tool_call span（input.tool = @tool 函数名，治理埋点 W3 落的键）扫
    最近 trace，返回失败明细及其来源（skill 归属 / capability / session /
    错误码与摘要），供 /tools 页失败行展开。旧 trace 无 input.tool 时回退
    span name（skill:capability 口径，只能按前缀过滤到 skill 级）。

    scan = 扫描最近 N 条 trace（默认 100，钳 1-500）：高频系统下 100 条
    只覆盖极短时间窗，调用方可按流量放大扫描窗（换内存扫描成本）。
    """
    await require_editor_user(request)
    limit = max(1, min(limit, 100))
    scan = max(1, min(scan, 500))
    from backend.observability.tracer import trace_collector

    records = trace_collector.list(scan, include_spans=True)
    errors = []
    for rec in records:
        spans = {s.span_id: s for s in rec.spans}
        for s in rec.spans:
            if s.type != "tool_call" or s.status not in ("error", "skipped"):
                continue
            span_tool = (s.input or {}).get("tool") or ""
            span_cap = (s.input or {}).get("capability") or ""
            if tool:
                # 精确匹配契约名；旧口径 span name 是 skill:capability，
                # 只做后缀/前缀模糊兜底（历史 trace 的错误来源也有价值）
                if span_tool != tool and not s.name.endswith(tool):
                    continue
            parent = spans.get(s.parent_id) if s.parent_id else None
            source_error_code, error_class, error_msg = "", "", ""
            for ev in reversed(s.events):
                info = ev.get("attributes") or {}
                if isinstance(info, dict) and (info.get("error") or info.get("error_code")):
                    source_error_code = info.get("error_code", "")
                    error_class = info.get("error_class", "")
                    error_msg = info.get("error", "")
                    break
            if not error_msg and s.errors:
                first = s.errors[0]
                error_msg = first.get("message", "") if isinstance(first, dict) else str(first)
            # BaseSkill 的失败文字历史上写在 metrics，不一定发过 event；
            # 统一按 event/errors/metrics 的优先级读取，兼容新旧 Trace。
            metrics = s.metrics or {}
            source_error_code = source_error_code or metrics.get("error_code", "")
            error_class = error_class or metrics.get("error_class", "")
            error_msg = error_msg or metrics.get("error", "")
            error_code = error_class or source_error_code
            errors.append({
                "trace_id": rec.id,
                "ts": s.end_time or rec.timestamp,
                "session_id": rec.session_id,
                "question": rec.question[:80],
                "tool": span_tool or s.name,
                "capability": span_cap,
                "skill": parent.name if parent else "",
                "error_code": error_code,
                "error_class": error_class or error_code,
                "source_error_code": source_error_code,
                "error": error_msg[:200],
                "latency_ms": s.duration_ms,
            })
            if len(errors) >= limit:
                break
        if len(errors) >= limit:
            break
    return {"count": len(errors), "errors": errors}


_CHANGE_CLASSIFICATIONS = ("BREAKING", "DEGRADED", "COMPATIBLE", "INIT")


@router.get("/changes")
async def tool_contract_changes(
    request: Request,
    limit: int = 20,
    classification: str | None = None,
    since: str | None = None,
    until: str | None = None,
):
    """契约变更历史（治理 #9：gen_tool_contract_lock 检测到变更时自动落库）。

    classification（四分类白名单）/ since / until（ISO 时间，created_at 边界）
    为可选过滤；全部参数化直查，无 offset 翻页（历史量小，LIMIT 截断）。
    """
    await require_editor_user(request)
    if classification is not None and classification not in _CHANGE_CLASSIFICATIONS:
        raise _HTTP(422, f"不支持的契约变更分类: {classification}")
    for name, value in (("since", since), ("until", until)):
        if value is None:
            continue
        try:
            from datetime import datetime

            datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise _HTTP(422, f"{name} 需要 ISO 时间格式: {value}") from exc

    where: list[str] = []
    params: list[Any] = []
    if classification:
        where.append("classification = %s")
        params.append(classification)
    if since:
        where.append("created_at >= %s")
        params.append(since)
    if until:
        where.append("created_at <= %s")
        params.append(until)

    try:
        from backend.config.database import OBS_DB_PG_CONFIG
        from backend.infra.db import engine_for

        sql = (
            "SELECT id, from_lock_hash, to_lock_hash, classification, "
            "changed_tools, tool_count, git_sha, detected_by, created_at "
            "FROM ai.tool_contract_changes"
        )
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY id DESC LIMIT %s"
        params.append(min(limit, 100))

        def _query():
            with engine_for(OBS_DB_PG_CONFIG).raw_connection() as conn:
                cur = conn.cursor()
                cur.execute(sql, tuple(params))
                return cur.fetchall()

        rows = await asyncio.to_thread(_query)
    except Exception as e:  # noqa: BLE001 — 历史查询软失败
        raise _HTTP(503, f"契约历史查询失败: {e}")
    import json as _json

    def _parse(v):
        return v if isinstance(v, list) else _json.loads(v or "[]")

    return {"changes": [
        {"id": r[0], "from_lock_hash": r[1], "to_lock_hash": r[2],
         "classification": r[3], "changed_tools": _parse(r[4]),
         "tool_count": r[5], "git_sha": r[6], "detected_by": r[7],
         "created_at": str(r[8])}
        for r in rows
    ]}


# ═══════════════════════════════════════════════════
# Tool Discovery 策略与模拟器（决策 #18，B1；只读候选解析，零运行时改动）
# ═══════════════════════════════════════════════════

_DISCOVERY_MODES = ("adaptive", "reasoning", "fast")
_DISCOVERY_PRODUCTS = ("agent", "customer_service", "public", "internal")


class DiscoveryPolicyUpsert(BaseModel):
    """发现策略 upsert 请求（数值边界见 _validate_discovery_policy）。"""

    name: str = Field(..., min_length=1, max_length=64)
    mode: str = Field("adaptive", max_length=16)
    top_k: int = Field(3)
    max_rounds: int = Field(2)
    cost_budget_milliunits: int = Field(50)
    timeout_ms: int = Field(8000)
    prompt_key: str = Field("router.tool_selector", min_length=1, max_length=128)


class DiscoverySimulateRequest(BaseModel):
    """发现模拟请求：纯读，不触发执行，不进路由运行时。"""

    query: str = Field(..., min_length=1, max_length=2000)
    domain: str = Field("", max_length=64)
    top_k: int = Field(3)
    product_id: str = Field("agent", max_length=32)


def _validate_discovery_policy(body: DiscoveryPolicyUpsert) -> None:
    """管理端显式 400 校验（Pydantic 默认 422；前端按 message 展示）。"""
    if body.mode not in _DISCOVERY_MODES:
        raise _HTTP(400, f"不支持的发现模式: {body.mode}（可选：{_DISCOVERY_MODES}）")
    if not 1 <= body.top_k <= 20:
        raise _HTTP(400, "top_k 需在 1-20 之间")
    if not 1 <= body.max_rounds <= 5:
        raise _HTTP(400, "max_rounds 需在 1-5 之间")
    if not 1000 <= body.timeout_ms <= 30000:
        raise _HTTP(400, "timeout_ms 需在 1000-30000 之间")
    if not 0 <= body.cost_budget_milliunits <= 1_000_000:
        raise _HTTP(400, "cost_budget_milliunits 需在 0-1000000 之间")


def _audit_discovery(event: str, detail: dict[str, Any], *, user_id: str = "",
                     tenant_id: str = "") -> None:
    """操作审计旁路（照 admin_mcp._audit 同款软失败语义）。"""
    try:
        from backend.security.events import record_security_event

        record_security_event(
            "ADMIN_ACTION", category=event, detail=detail,
            user_id=user_id or None, tenant_id=tenant_id or None,
        )
    except Exception:  # noqa: BLE001 — 审计旁路软失败
        logger.warning("[AdminTools] Discovery 审计写入失败: %s", event, exc_info=True)


def _discovery_tenant(request: Request) -> str:
    """操作租户：网关注入身份头重解析；无租户头（服务凭据）落 default。"""
    from backend.app.api.identity import resolve_identity

    ident = resolve_identity(request)
    return str(ident.tenant_id or "default")


def _discovery_permission_note(name: str, risk: str, product_id: str,
                               domain: str) -> str:
    """单候选的权限/过滤说明（如实描述已通过的检查与剩余执行期门）。"""
    notes = [f"产品范围允许（product={product_id}"]
    if domain:
        notes.append(f"，域={domain}")
    notes.append("）")
    from backend.core.tool_governance.registry import get_tool_spec

    spec = get_tool_spec(name)
    if spec is not None:
        if spec.required_roles:
            notes.append(f"；声明角色 {','.join(spec.required_roles)}（模拟视角角色为空集，仅作展示）")
        if spec.required_scopes:
            notes.append(f"；声明 scope {','.join(spec.required_scopes)}")
    if str(risk).upper() not in ("", "UNKNOWN", "LOW"):
        notes.append(f"；风险级别 {risk}（执行期另有审批门）")
    return "".join(notes)


@router.get("/discovery/policy")
async def get_discovery_policy(request: Request):
    """生效发现策略（无 active 行返回代码内默认值）+ 本租户全部策略行。"""
    await require_editor_user(request)
    tenant = _discovery_tenant(request)
    from backend.orchestration.discovery import service as discovery_service

    def _read() -> dict[str, Any]:
        return {
            "policy": discovery_service.load_active_policy(tenant),
            "policies": discovery_service.load_policies(tenant),
        }

    try:
        return await asyncio.to_thread(_read)
    except Exception as e:  # noqa: BLE001 — 历史查询软失败转 503
        raise _HTTP(503, f"发现策略查询失败: {e}")


@router.put("/discovery/policy")
async def update_discovery_policy(request: Request, body: DiscoveryPolicyUpsert):
    """upsert 并置为本租户唯一 active 策略（治理写：幂等 + 审计）。"""
    operator = await require_admin_user(request)
    _validate_discovery_policy(body)
    tenant = _discovery_tenant(request)
    actor = str(operator.actor or "")
    from backend.orchestration.discovery import service as discovery_service

    def _write() -> dict[str, Any]:
        policy = discovery_service.upsert_policy(
            tenant_id=tenant, name=body.name, actor=actor,
            mode=body.mode, top_k=body.top_k, max_rounds=body.max_rounds,
            cost_budget_milliunits=body.cost_budget_milliunits,
            timeout_ms=body.timeout_ms, prompt_key=body.prompt_key)
        _audit_discovery("tool_discovery_policy_upsert", {
            "policy_id": policy.get("id"), "name": body.name,
            "mode": body.mode, "top_k": body.top_k,
            "max_rounds": body.max_rounds,
            "cost_budget_milliunits": body.cost_budget_milliunits,
            "timeout_ms": body.timeout_ms, "prompt_key": body.prompt_key,
        }, user_id=actor, tenant_id=tenant)
        return policy

    return await asyncio.to_thread(
        run_governance_mutation, request, operator, "tool_discovery_policy_upsert",
        {"name": body.name, "mode": body.mode}, _write)


@router.post("/discovery/simulate")
async def simulate_discovery(request: Request, body: DiscoverySimulateRequest):
    """发现模拟器：当前策略 + 权限下的候选解析视图（纯读，不进 runtime）。

    与线上路由的诚实差异（响应 meta 如实声明）：B1 只做候选解析，
    不做语义排序与 LLM 选型；候选顺序 = registry 声明序。
    """
    await require_editor_user(request)
    if body.product_id not in _DISCOVERY_PRODUCTS:
        raise _HTTP(400, f"不支持的产品视角: {body.product_id}")
    if not 1 <= body.top_k <= 20:
        raise _HTTP(400, "top_k 需在 1-20 之间")
    tenant = _discovery_tenant(request)
    from backend.orchestration.discovery import resolve_candidates

    try:
        result = await asyncio.to_thread(
            resolve_candidates, body.query, body.product_id, tenant,
            body.domain, body.top_k)
    except _HTTP:
        raise
    except Exception as e:  # noqa: BLE001 — 模拟失败显式报错，不伪造结果
        raise _HTTP(503, f"发现模拟失败: {e}")
    result["candidates"] = [
        {**c, "permission_note": _discovery_permission_note(
            c["name"], c["risk"], result["product_id"], result["domain"])}
        for c in result["candidates"]
    ]
    result["notes"] = [
        f"{result['excluded_count']} 个候选被产品/角色/范围过滤剔除",
        "B1 仅候选解析（registry 声明序），不含语义排序与 LLM 选型；"
        "线上执行另有 Tool Governance 执行期复核",
    ]
    return result


# ═══════════════════════════════════════════════════
# AI 失败分析（决策 #20：失败聚类 → LLM 归因/建议/候选评测用例）
# 红线：只生成建议与候选评测集（evalCandidate），不自动应用/改写任何正式
# 配置（Prompt / 语义契约 / Golden 数据）；送 LLM 的输入只有 (tool,
# error_class) 聚类计数与截断 300 字符的错误摘要（URL 已清洗），绝不包含
# 用户参数原文（span input.params 一概不读）与任何凭据材料。
# ═══════════════════════════════════════════════════

_FAILURE_WINDOW_MIN = 5
_FAILURE_WINDOW_MAX = 1440
_FAILURE_LIMIT_MIN = 10
_FAILURE_LIMIT_MAX = 100
_FAILURE_SCAN_DEFAULT = 200      # 与 /errors 同源（trace_collector.list），窗放大
_FAILURE_CLUSTER_MAX = 10        # 送 LLM 的聚类组数上限
_FAILURE_SAMPLES_PER_GROUP = 3   # 每组代表性错误摘要条数
_FAILURE_SAMPLE_CHARS = 300      # 单条摘要截断（红线：摘要而非原文）
_SUGGESTION_MAX = 3              # LLM 建议条数上限
_CANDIDATE_CASES_MAX = 6         # 候选评测用例总条数上限（响应级）

_URL_PATTERN = re.compile(r"[a-zA-Z][a-zA-Z0-9+.\-]*://\S+")


class FailureAnalysisRequest(BaseModel):
    """AI 失败分析请求（边界校验在端点内显式 400，Pydantic 只兜超大 payload）。"""

    window_minutes: int = Field(60)
    limit: int = Field(50)


def _redact_urls(text: str) -> str:
    """prompt 输入清洗：任何 scheme://... 替换为占位符（防 URL 内嵌凭据出口）。"""
    return _URL_PATTERN.sub("[URL已屏蔽]", str(text or ""))


def _failure_tenant(request: Request) -> str:
    """操作租户：网关注入身份头重解析；无租户头（服务凭据）落 default。"""
    from backend.app.api.identity import resolve_identity

    ident = resolve_identity(request)
    return str(ident.tenant_id or "default")


def _span_error_info(s) -> tuple[str, str, str]:
    """从失败 span 抽错误信息（与 /errors 端点同优先级：event→errors→metrics）。"""
    error_code, error_class, error_msg = "", "", ""
    for ev in reversed(s.events):
        info = ev.get("attributes") or {}
        if isinstance(info, dict) and (info.get("error") or info.get("error_code")):
            error_code = info.get("error_code", "")
            error_class = info.get("error_class", "")
            error_msg = info.get("error", "")
            break
    if not error_msg and s.errors:
        first = s.errors[0]
        error_msg = first.get("message", "") if isinstance(first, dict) else str(first)
    metrics = s.metrics or {}
    error_code = error_code or metrics.get("error_code", "")
    error_class = error_class or metrics.get("error_class", "")
    error_msg = error_msg or metrics.get("error", "")
    return error_code, error_class, error_msg


def _span_ts(s, rec) -> float | None:
    """span 失效时间（UTC 秒）；无法解析返回 None（调用方跳过，不进时间窗）。"""
    from datetime import datetime, timezone

    raw = s.end_time or rec.timestamp
    try:
        dt = datetime.fromisoformat(str(raw or "").replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        return None


def _collect_failure_clusters(window_minutes: int,
                              max_failures: int) -> list[dict[str, Any]]:
    """失败聚类（与 /errors 同一数据路径：进程内 trace tool_call span 直读）。

    按 (tool, error_class) 聚类计数，取计数 top N 组；每组带代表性错误摘要
    （截断 + URL 清洗）。**只读 input.tool/capability，绝不读 input.params**。
    max_failures = 聚类输入的失败记录上限（最新优先，超出部分不计入）。
    """
    from datetime import datetime, timezone

    from backend.observability.tracer import trace_collector

    cutoff = datetime.now(timezone.utc).timestamp() - window_minutes * 60
    records = trace_collector.list(_FAILURE_SCAN_DEFAULT, include_spans=True)
    clusters: dict[tuple[str, str], dict[str, Any]] = {}
    counted = 0
    for rec in records:
        if counted >= max_failures:
            break
        for s in rec.spans:
            if s.type != "tool_call" or s.status not in ("error", "skipped"):
                continue
            ts = _span_ts(s, rec)
            if ts is None or ts < cutoff:
                continue
            span_tool = (s.input or {}).get("tool") or s.name
            _, error_class, error_msg = _span_error_info(s)
            key = (span_tool, error_class or "unknown")
            entry = clusters.setdefault(key, {
                "tool": span_tool,
                "error_class": key[1],
                "count": 0,
                "samples": [],
            })
            entry["count"] += 1
            counted += 1
            if len(entry["samples"]) < _FAILURE_SAMPLES_PER_GROUP and error_msg:
                entry["samples"].append(
                    _redact_urls(error_msg)[:_FAILURE_SAMPLE_CHARS])
            if counted >= max_failures:
                break
    ranked = sorted(clusters.values(), key=lambda g: -g["count"])
    return ranked[:_FAILURE_CLUSTER_MAX]


def _render_clusters_text(groups: list[dict[str, Any]]) -> str:
    """聚类统计 → prompt 文本块（只含 tool/分类/计数/摘要，无其他数据）。"""
    lines = []
    for i, g in enumerate(groups, 1):
        lines.append(
            f"{i}. tool={g['tool']} | error_class={g['error_class']} "
            f"| 失败次数={g['count']}")
        for sample in g.get("samples", []):
            lines.append(f"   代表性错误摘要：{sample}")
    return "\n".join(lines)


def _normalize_failure_analysis(content: str,
                                groups: list[dict[str, Any]]) -> list[dict] | None:
    """解析 LLM 输出为 clusters 列表；形状不合法返回 None（调用方重试）。

    防幻觉：只保留输入聚类中存在的 (tool, error_class) 组；建议 ≤3 条、
    候选评测用例合计 ≤6 条，逐字段截断。
    """
    from backend.shared.json_extractor import extract_json

    if not content or "{" not in content:
        return None
    parsed = extract_json(content, source="tool.failure_analysis")
    raw_clusters = parsed.get("clusters") if isinstance(parsed, dict) else None
    if not isinstance(raw_clusters, list):
        return None
    known = {(g["tool"], g["error_class"]) for g in groups}
    clusters: list[dict] = []
    candidate_budget = _CANDIDATE_CASES_MAX
    for raw in raw_clusters:
        if not isinstance(raw, dict):
            continue
        tool = str(raw.get("tool") or "").strip()
        error_class = str(raw.get("error_class") or "").strip()
        if (tool, error_class) not in known:
            continue  # 输入里没有的组 = 模型幻觉，丢弃
        suggestions = []
        for item in (raw.get("suggestions") or [])[:_SUGGESTION_MAX]:
            text = str(item or "").strip()
            if text:
                suggestions.append(text[:200])
        cases: list[dict] = []
        if candidate_budget > 0:
            for raw_case in (raw.get("candidate_eval_cases") or []):
                if not isinstance(raw_case, dict) or candidate_budget <= 0:
                    continue
                question = str(raw_case.get("question") or "").strip()
                if not question:
                    continue
                cases.append({
                    "question": question[:300],
                    "expected_capability":
                        str(raw_case.get("expected_capability") or "").strip()[:128],
                    "expectation_note":
                        str(raw_case.get("expectation_note") or "").strip()[:300],
                })
                candidate_budget -= 1
        attribution = str(raw.get("attribution") or "").strip()
        clusters.append({
            "tool": tool,
            "error_class": error_class,
            "attribution": attribution[:500],
            "suggestions": suggestions,
            "candidate_eval_cases": cases,
        })
    return clusters


async def _invoke_failure_llm(prompt_text: str) -> str:
    """调用当前 LLM 生成失败分析 JSON（同步 invoke 经 to_thread 执行）。"""
    from backend.infra.llm import llm

    resp = await asyncio.to_thread(
        llm.invoke,
        [("system", prompt_text), ("human", "请只输出 JSON 结果。")],
    )
    content = getattr(resp, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):  # 多块内容（LangChain content blocks）
        return "".join(
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in content)
    return str(content)


def _current_llm_model() -> str:
    """当前模型名（软失败留空：只作留痕展示，不影响分析结果）。"""
    try:
        from backend.infra.llm import get_llm_factory

        return str(get_llm_factory().get_current_model_name() or "")
    except Exception:  # noqa: BLE001 — 模型名读取失败不影响落库
        return ""


def _audit_failure_analysis(event: str, detail: dict[str, Any], *,
                            user_id: str = "", tenant_id: str = "") -> None:
    """操作审计旁路（软失败，不因审计故障带崩分析主流程）。"""
    try:
        from backend.security.events import record_security_event

        record_security_event(
            "ADMIN_ACTION", category=event, detail=detail,
            user_id=user_id or None, tenant_id=tenant_id or None,
        )
    except Exception:  # noqa: BLE001 — 审计旁路软失败
        logger.warning("[AdminTools] 失败分析审计写入失败: %s", event, exc_info=True)


@router.post("/failure-analysis")
async def run_failure_analysis(request: Request, body: FailureAnalysisRequest):
    """AI 失败分析：时间窗失败聚类 → LLM 归因/建议/候选评测用例 → 落库。

    边界（决策 #20）：只生成建议与候选用例并落 ai.tool_failure_analyses
    留痕，不自动改写正式 Prompt / 语义契约 / Golden；候选用例仅供评测中心
    人工审核后采纳。LLM 不可用/调用失败 → 503；解析重试 1 次仍失败 → 502，
    绝不伪造分析结果（失败时不落库）。空聚类（时间窗内无失败）也允许调用，
    跳过 LLM 直接落一条空分析（如实反映「无失败可分析」）。
    """
    operator = await require_admin_user(request)
    if not _FAILURE_WINDOW_MIN <= body.window_minutes <= _FAILURE_WINDOW_MAX:
        raise _HTTP(400, f"window_minutes 需在 "
                         f"{_FAILURE_WINDOW_MIN}-{_FAILURE_WINDOW_MAX} 之间")
    if not _FAILURE_LIMIT_MIN <= body.limit <= _FAILURE_LIMIT_MAX:
        raise _HTTP(400, f"limit 需在 "
                         f"{_FAILURE_LIMIT_MIN}-{_FAILURE_LIMIT_MAX} 之间")
    tenant = _failure_tenant(request)
    actor = str(operator.actor or "")

    groups = await asyncio.to_thread(
        _collect_failure_clusters, body.window_minutes, body.limit)

    clusters: list[dict] | None = None
    model_name = ""
    if groups:
        try:
            from backend.prompts.service import prompt_service

            prompt_text = prompt_service.render_sync(
                "tool.failure_analysis",
                window_minutes=str(body.window_minutes),
                clusters_text=_render_clusters_text(groups),
            ).text
        except Exception as exc:  # noqa: BLE001 — prompt 渲染失败显式暴露
            raise _HTTP(503, f"失败分析 prompt 渲染失败: {exc}")
        last_reason = ""
        for _attempt in range(2):  # 解析失败重试 1 次
            try:
                content = await _invoke_failure_llm(prompt_text)
            except Exception as exc:  # noqa: BLE001 — LLM 失败不伪造结果
                raise _HTTP(503, f"LLM 不可用或调用失败: {str(exc)[:300]}")
            clusters = _normalize_failure_analysis(content, groups)
            if clusters is not None:
                break
            last_reason = content.strip()[:200]
        if clusters is None:
            raise _HTTP(502, "失败分析解析失败（重试后仍无法得到合法 JSON）："
                             f"{last_reason}")
        model_name = _current_llm_model()
    else:
        clusters = []

    input_summary = {
        "window_minutes": body.window_minutes,
        "groups": [
            {"tool": g["tool"], "error_class": g["error_class"],
             "count": g["count"], "samples": g.get("samples", [])}
            for g in groups
        ],
    }
    analysis_id = f"tfa_{uuid4().hex[:12]}"

    def _write() -> dict[str, Any]:
        from datetime import datetime, timezone

        from psycopg2.extras import Json as PgJson

        from backend.config.database import OBS_DB_PG_CONFIG
        from backend.infra.db import engine_for

        created_at = datetime.now(timezone.utc).isoformat()
        with engine_for(OBS_DB_PG_CONFIG).raw_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO ai.tool_failure_analyses "
                    "(id, tenant_id, window_minutes, input_summary, result, "
                    " model, created_by) "
                    "VALUES (%(id)s, %(tenant)s, %(window)s, %(summary)s, "
                    "        %(result)s, %(model)s, %(by)s)",
                    {"id": analysis_id, "tenant": tenant,
                     "window": body.window_minutes,
                     "summary": PgJson(input_summary),
                     "result": PgJson({"clusters": clusters}),
                     "model": model_name, "by": actor})
            conn.commit()
        _audit_failure_analysis("tool_failure_analysis_run", {
            "analysis_id": analysis_id, "window_minutes": body.window_minutes,
            "cluster_groups": len(groups), "clusters": len(clusters),
            "candidate_cases": sum(len(c.get("candidate_eval_cases") or [])
                                   for c in clusters),
        }, user_id=actor, tenant_id=tenant)
        return {"analysis_id": analysis_id, "clusters": clusters,
                "created_at": created_at}

    return await asyncio.to_thread(
        run_governance_mutation, request, operator,
        "tool_failure_analysis_run", {"window_minutes": body.window_minutes},
        _write)


@router.get("/failure-analysis")
async def list_failure_analyses(request: Request, limit: int = 10):
    """本租户最近分析列表（id/时间/窗口/聚类计数/模型/完整 result）。"""
    await require_editor_user(request)
    limit = max(1, min(limit, 50))
    tenant = _failure_tenant(request)

    def _read() -> dict[str, Any]:
        import json as _json

        from backend.config.database import OBS_DB_PG_CONFIG
        from backend.infra.db import engine_for

        with engine_for(OBS_DB_PG_CONFIG).raw_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, window_minutes, input_summary, result, model, "
                    "       created_by, created_at "
                    "FROM ai.tool_failure_analyses WHERE tenant_id = %s "
                    "ORDER BY created_at DESC LIMIT %s",
                    (tenant, limit))
                rows = cur.fetchall()

        def _parse(v):
            return v if isinstance(v, (dict, list)) else _json.loads(v or "{}")

        return {"count": len(rows), "analyses": [
            {"id": r[0], "window_minutes": r[1],
             "input_summary": _parse(r[2]), "result": _parse(r[3]),
             "model": r[4], "created_by": r[5], "created_at": str(r[6]),
             "cluster_count": len(_parse(r[3]).get("clusters") or [])}
            for r in rows
        ]}

    try:
        return await asyncio.to_thread(_read)
    except Exception as e:  # noqa: BLE001 — 历史查询软失败转 503
        raise _HTTP(503, f"失败分析查询失败: {e}")


# G6 按域成本归因（capability-governance）：llm_usage ⋈ trace_summary。
# join 伙伴是 013 迁移同源的 trace_summary（有 workflow_name），**不是**
# 020 的 ai.trace_records（新链路，实测与 llm_usage.trace_id 零相交——
# 2026-11-01 覆盖率验证，见任务 PROGRESS）。零 schema 改动，纯只读 join。
_USAGE_BY_DOMAIN_SQL = """
    SELECT t.workflow_name                                   AS domain_key,
           count(*)                                          AS usage_rows,
           sum(u.total_tokens)                               AS total_tokens,
           sum(u.cost_usd)                                   AS cost_usd,
           count(*) FILTER (WHERE u.component = 'llm')       AS llm_calls,
           count(*) FILTER (WHERE u.component = 'embedding') AS embedding_calls,
           count(*) FILTER (WHERE u.component = 'rerank')    AS rerank_calls
    FROM public.llm_usage u
    JOIN trace_summary t ON t.trace_id = u.trace_id
    WHERE u.trace_id <> ''
      AND u.created_at >= %(start)s AND u.created_at < %(end)s
    GROUP BY t.workflow_name
    ORDER BY cost_usd DESC
"""

_USAGE_COVERAGE_SQL = """
    SELECT count(*)                                                       AS total_rows,
           count(*) FILTER (WHERE u.trace_id <> '')                       AS with_trace_id,
           count(*) FILTER (WHERE t.trace_id IS NOT NULL)                 AS joined_rows,
           round(sum(u.cost_usd)::numeric, 6)                             AS total_cost_usd,
           round(sum(u.cost_usd) FILTER (WHERE t.trace_id IS NOT NULL)
                 ::numeric, 6)                                            AS joined_cost_usd
    FROM public.llm_usage u
    LEFT JOIN trace_summary t ON t.trace_id = u.trace_id
    WHERE u.created_at >= %(start)s AND u.created_at < %(end)s
"""


@router.get("/usage-by-domain")
async def usage_by_domain(request: Request, days: int = 7):
    """LLM 成本按域（workflow_name）归因报表（G6，只读 join 报表）。

    零 schema 改动：llm_usage（013）⋈ trace_summary（013 同源，带
    workflow_name）。join 覆盖率在响应 coverage 段显式披露（未命中行
    多为无 workflow 上下文的 embedding/rerank 批任务），不伪造 100%。
    """
    await require_editor_user(request)
    from datetime import datetime, timedelta, timezone

    days = max(1, min(days, 90))
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    # llm_usage.created_at 是 TEXT（ISO8601 UTC，013 设计为字典序过滤）——
    # 传 ISO 字符串而非 datetime 对象
    params = {"start": start.isoformat(), "end": end.isoformat()}

    def _read() -> dict[str, Any]:
        from backend.config.database import OBS_DB_PG_CONFIG
        from backend.infra.db import engine_for

        with engine_for(OBS_DB_PG_CONFIG).raw_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(_USAGE_BY_DOMAIN_SQL, params)
                cols = [d[0] for d in cur.description]
                domains = [dict(zip(cols, row)) for row in cur.fetchall()]
                cur.execute(_USAGE_COVERAGE_SQL, params)
                ccols = [d[0] for d in cur.description]
                coverage = dict(zip(ccols, cur.fetchone()))

        def _num(v):
            return round(float(v), 6) if v is not None else 0.0

        joined = int(coverage.get("joined_rows") or 0)
        total = int(coverage.get("total_rows") or 0)
        return {
            "scope": "db-join",
            "window_days": days,
            "coverage": {
                "total_usage_rows": total,
                "with_trace_id": int(coverage.get("with_trace_id") or 0),
                "joined_rows": joined,
                "coverage_ratio": round(joined / total, 4) if total else None,
                "total_cost_usd": _num(coverage.get("total_cost_usd")),
                "joined_cost_usd": _num(coverage.get("joined_cost_usd")),
                "join_partner": "trace_summary (migration 013, workflow_name)",
            },
            "domains": [
                {
                    "domain_key": d["domain_key"] or "(null)",
                    "usage_rows": int(d["usage_rows"]),
                    "total_tokens": int(d["total_tokens"] or 0),
                    "cost_usd": _num(d["cost_usd"]),
                    "by_component": {
                        "llm": int(d["llm_calls"]),
                        "embedding": int(d["embedding_calls"]),
                        "rerank": int(d["rerank_calls"]),
                    },
                }
                for d in domains
            ],
        }

    try:
        return await asyncio.to_thread(_read)
    except Exception as e:  # noqa: BLE001 — 只读报表软失败转 503
        raise _HTTP(503, f"按域用量查询失败: {e}")


__all__ = ["router"]
