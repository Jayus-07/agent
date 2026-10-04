"""Tool 治理统计 API（M2 / 台账 D2）

GET /admin/tools        — 34 Tool 清单（契约 hash + 归属，复用 M1 lock 派生）
GET /admin/tools/stats  — 运行统计聚合（总数/成功/失败/成功率/Top 失败/Top 错误类）

为什么存在：``agent_tool_*`` 指标齐全但管理端无聚合视图，管理员答不了
「哪个 Tool 在失败、为什么」。``skill_failure_total`` 此前是死指标
（定义零埋点），已随本债补埋（skills/base 失败出口）。

数据源：**TOOL_STATS_SOURCE**（P1-4，默认 process）——
  process = 进程内 Prometheus REGISTRY 直读（prometheus_client 单例），
            不依赖外部 Prometheus 可达性（本机部署零额外依赖）；
  redis   = 仅 Redis 日键合计（TOOL_STATS_REDIS_ENABLED 写侧开启后多副本合计）；
  merged  = 两者相加（scope 如实反映）。
错误分类口径 = M3 统一七分类（observability.error_taxonomy）。
权限：仅管理员（require_admin_user）。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException as _HTTP, Request
from pydantic import BaseModel, Field

from backend.app.api.deps import require_admin_user
from backend.config import ENVIRONMENT
from backend.shared.logger import logger

router = APIRouter(prefix="/admin/tools", tags=["Admin-Tools"])

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
    await require_admin_user(request)
    return _aggregate_tool_stats()


@router.post("/failure-probe")
async def tool_failure_probe(request: Request, body: ToolFailureProbeRequest):
    """生成一条模拟 Tool 失败 Trace，用于管理端稳定验收错误展示。

    探针不调用 Tool、不访问外部服务，只复用生产的 ToolResult、指标、Trace
    和统一错误分类出口；生产环境明确拒绝，避免把模拟数据写入生产观测面。
    """
    await require_admin_user(request)
    if ENVIRONMENT == "production":
        raise _HTTP(403, "生产环境禁止执行 Tool 强制失败测试")
    if body.error_class not in _PROBE_ERROR_CLASSES:
        raise _HTTP(422, f"不支持的统一错误分类: {body.error_class}")

    lock_path = Path(__file__).resolve().parents[4] / "backend" / "tool_contracts.lock.json"
    try:
        lock_tools = json.loads(lock_path.read_text(encoding="utf-8")).get("tools", {})
    except (OSError, ValueError) as exc:
        raise _HTTP(503, f"Tool 契约 lock 读取失败: {exc}") from exc
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
    await require_admin_user(request)
    import json
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[4]
    lock = {}
    lock_error = ""
    lock_path = repo_root / "backend" / "tool_contracts.lock.json"
    try:
        lock = json.loads(lock_path.read_text(encoding="utf-8")).get("tools", {})
    except (OSError, ValueError) as e:
        lock_error = f"lock 读取失败: {e}"

    stats = {t["tool"]: t for t in _aggregate_tool_stats()["tools"]}
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
        "lock_git_sha": json.loads(lock_path.read_text(encoding="utf-8"))
        .get("git_sha", "") if not lock_error else "",
        "lock_error": lock_error,
        "tools": inventory,
    }


@router.get("/errors")
async def tool_errors(request: Request, tool: str | None = None, limit: int = 20):
    """Tool 错误来源明细：进程内 trace 直读，不另建存储。

    按 tool_call span（input.tool = @tool 函数名，治理埋点 W3 落的键）扫
    最近 trace，返回失败明细及其来源（skill 归属 / capability / session /
    错误码与摘要），供 /tools 页失败行展开。旧 trace 无 input.tool 时回退
    span name（skill:capability 口径，只能按前缀过滤到 skill 级）。
    """
    await require_admin_user(request)
    limit = max(1, min(limit, 100))
    from backend.observability.tracer import trace_collector

    records = trace_collector.list(100, include_spans=True)
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


@router.get("/changes")
async def tool_contract_changes(request: Request, limit: int = 20):
    """契约变更历史（治理 #9：gen_tool_contract_lock 检测到变更时自动落库）。"""
    await require_admin_user(request)
    try:
        from backend.config.database import OBS_DB_PG_CONFIG
        from backend.infra.db import engine_for

        with engine_for(OBS_DB_PG_CONFIG).raw_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT id, from_lock_hash, to_lock_hash, classification, "
                "changed_tools, tool_count, git_sha, detected_by, created_at "
                "FROM ai.tool_contract_changes ORDER BY id DESC LIMIT %s",
                (min(limit, 100),))
            rows = cur.fetchall()
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


__all__ = ["router"]
