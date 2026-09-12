"""cs_quality_report.py — CS 灰度质量聚合报告

按 cs_variant（treatment=CS graph / control=主图）分组聚合时间窗内的
客服域 trace，输出灰度放量决策所需的全部程序化指标：

  - 路由一致率：tags.cs_target 与 supervisor 实际派发 expert（经
    CS_TARGET_TO_EXPERT 映射）是否一致
  - 兜底率：final_answer 为空/过短/命中降级文案的比例
  - 转人工率：按三桶拆分（healthy_user / healthy_escalation / capability_gap），
    原因从 cs_audit_entries 的 trigger= 字段解析
  - 时延：P50 / P95

告警：兜底率、路由一致率、能力缺口转人工率超阈值时输出 alerts
（阈值见 config.customer_service.CS_QUALITY_ALERT_*）。

用法：
  REST  GET /api/observability/cs-quality?hours=24
  CLI   python -m backend.observability.cs_quality_report --hours 24
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from backend.shared.logger import logger

_FALLBACK_MARK = "稍后再试"
_MIN_ANSWER_LEN = 10
_TRIGGER_RE = re.compile(r"trigger=([a-z_]+)")


def _iso_cutoff(hours: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


def _ts_of(trace: dict) -> str:
    return trace.get("timestamp") or ""


def _parse_int_tags(tags: dict) -> None:
    """兼容 tags 为 JSON 字符串的存量数据。"""
    if isinstance(tags, str):
        try:
            import json
            return json.loads(tags)
        except Exception:
            return {}
    return tags


def _bucket_handoff_reasons(trace: dict) -> list[str]:
    """从 trace metadata 的转人工 trigger 序列 → 分析桶。

    trigger 序列由 cs_graph_node._stamp_execution_tags 写入
    （audit entries 本身不进 trace record）。
    """
    try:
        from backend.customer_service.handoff import handoff_reason_bucket
        meta = trace.get("metadata") or {}
        triggers = meta.get("cs_handoff_triggers") or [] if isinstance(meta, dict) else []
        return [handoff_reason_bucket(t) for t in triggers]
    except Exception:
        return []


def _summarize(traces: list[dict]) -> dict:
    """单组（variant）指标聚合。"""
    total = len(traces)
    if total == 0:
        return {
            "total": 0,
            "route_consistency": None,
            "fallback_rate": None,
            "handoff_rate": None,
            "handoff_by_reason": {},
            "p50_ms": None, "p95_ms": None, "avg_ms": None,
        }

    # 路由一致率
    from backend.customer_service.graph_state import CS_TARGET_TO_EXPERT
    route_ok = route_n = 0
    for t in traces:
        tags = _parse_int_tags(t.get("tags") or {})
        target = tags.get("cs_target")
        final_expert = tags.get("cs_expert_final")
        if not target or not final_expert:
            continue
        route_n += 1
        if CS_TARGET_TO_EXPERT.get(target) == final_expert:
            route_ok += 1

    # 兜底率 + 时延
    fallback_n = 0
    durations: list[int] = []
    for t in traces:
        answer = (t.get("answer_preview") or "").strip()
        if not answer or len(answer) < _MIN_ANSWER_LEN or _FALLBACK_MARK in answer:
            fallback_n += 1
        if t.get("duration_ms"):
            durations.append(int(t["duration_ms"]))

    # 转人工（按 conversation 去重）+ 原因分桶
    handoff_convs: set[str] = set()
    reason_buckets: dict[str, int] = {}
    for t in traces:
        tags = _parse_int_tags(t.get("tags") or {})
        handoff = tags.get("cs_handoff_state") or ""
        if "handoff" in handoff or "human" in handoff:
            conv = tags.get("conversation_id") or t.get("id")
            handoff_convs.add(conv)
            for b in _bucket_handoff_reasons(t):
                reason_buckets[b] = reason_buckets.get(b, 0) + 1

    durations.sort()
    def _pct(p: float) -> int | None:
        return durations[min(int(len(durations) * p), len(durations) - 1)] if durations else None

    return {
        "total": total,
        "route_consistency": round(route_ok / route_n, 3) if route_n else None,
        "route_n": route_n,
        "fallback_rate": round(fallback_n / total, 3),
        "handoff_rate": round(len(handoff_convs) / total, 3) if total else None,
        "handoff_by_reason": reason_buckets,
        "p50_ms": _pct(0.5),
        "p95_ms": _pct(0.95),
        "avg_ms": round(sum(durations) / len(durations)) if durations else None,
    }


def build_cs_quality_report(hours: float = 24, limit: int = 500) -> dict:
    """构建 CS 灰度质量报告。

    Args:
        hours: 统计时间窗（小时）
        limit: 最多扫描的最近 trace 条数
    """
    from backend.observability.trace_store import get_trace_store

    cutoff = _iso_cutoff(hours)
    try:
        traces = get_trace_store().list(limit)
    except Exception:
        logger.warning("cs_quality_report: trace_store 不可用", exc_info=True)
        traces = []

    # 客服域 trace = 灰度打标存在（cs_variant）或 CS 路由命中（cs_route metadata）
    cs_traces: list[dict] = []
    for t in traces:
        if _ts_of(t) and _ts_of(t) < cutoff:
            continue
        tags = _parse_int_tags(t.get("tags") or {})
        meta = t.get("metadata") or {}
        if tags.get("cs_variant") or (meta.get("cs_route") if isinstance(meta, dict) else None):
            cs_traces.append(t)

    by_variant: dict[str, dict] = {}
    for variant in ("treatment", "control"):
        group = [t for t in cs_traces
                 if _parse_int_tags(t.get("tags") or {}).get("cs_variant") == variant]
        by_variant[variant] = _summarize(group)
    # 未打标的存量 CS trace（灰度开关启用前）单列，避免污染对比
    unlabeled = [t for t in cs_traces
                 if not _parse_int_tags(t.get("tags") or {}).get("cs_variant")]
    by_variant["unlabeled"] = _summarize(unlabeled)

    report = {
        "window_hours": hours,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_cs_traces": len(cs_traces),
        "by_variant": by_variant,
        "alerts": _evaluate_alerts(by_variant),
    }
    return report


def _evaluate_alerts(by_variant: dict) -> list[dict]:
    """静态阈值告警（环比需要历史快照，暂用绝对阈值）。"""
    from backend.config.customer_service import (
        CS_QUALITY_ALERT_CAPABILITY_HANDOFF_RATE,
        CS_QUALITY_ALERT_FALLBACK_RATE,
        CS_QUALITY_ALERT_ROUTE_CONSISTENCY,
    )

    alerts: list[dict] = []
    treat = by_variant.get("treatment") or {}
    if treat.get("total", 0) == 0:
        return alerts

    fb = treat.get("fallback_rate")
    if fb is not None and fb > CS_QUALITY_ALERT_FALLBACK_RATE:
        alerts.append({
            "severity": "error",
            "type": "cs_fallback_rate",
            "message": f"CS 兜底率 {fb:.1%} 超过阈值 {CS_QUALITY_ALERT_FALLBACK_RATE:.0%}",
        })
    rc = treat.get("route_consistency")
    if rc is not None and rc < CS_QUALITY_ALERT_ROUTE_CONSISTENCY:
        alerts.append({
            "severity": "warning",
            "type": "cs_route_consistency",
            "message": f"CS 路由一致率 {rc:.1%} 低于阈值 {CS_QUALITY_ALERT_ROUTE_CONSISTENCY:.0%}",
        })
    cap = (treat.get("handoff_by_reason") or {}).get("capability_gap")
    if cap and treat.get("total") and (cap / treat["total"]) > CS_QUALITY_ALERT_CAPABILITY_HANDOFF_RATE:
        alerts.append({
            "severity": "warning",
            "type": "cs_capability_handoff",
            "message": (f"能力缺口转人工占比 {cap / treat['total']:.1%} 超过阈值 "
                        f"{CS_QUALITY_ALERT_CAPABILITY_HANDOFF_RATE:.0%}"),
        })
    return alerts


if __name__ == "__main__":
    import argparse
    import json
    import sys

    sys.path.insert(0, ".")
    parser = argparse.ArgumentParser(description="CS 灰度质量报告")
    parser.add_argument("--hours", type=float, default=24)
    parser.add_argument("--limit", type=int, default=500)
    args = parser.parse_args()
    print(json.dumps(build_cs_quality_report(hours=args.hours, limit=args.limit),
                     ensure_ascii=False, indent=2))
