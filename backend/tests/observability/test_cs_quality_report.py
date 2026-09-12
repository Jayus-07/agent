"""test_cs_quality_report.py — CS 质量聚合报告逻辑单测（合成数据，不依赖真实 trace）"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.observability.cs_quality_report import (
    _bucket_handoff_reasons,
    _summarize,
    build_cs_quality_report,
)


def _trace(variant="treatment", target="cs_complaint", expert="cs_complaint_expert",
           answer="已为您处理投诉，专员将尽快与您联系。", duration=5000, handoff="",
           triggers=None, ts=None, trace_id=None):
    return {
        "id": trace_id or f"t-{abs(hash((variant, target, expert, answer, handoff, tuple(triggers or ()))) % 10**8)}",
        "timestamp": ts or datetime.now(timezone.utc).isoformat(),
        "answer_preview": answer,
        "duration_ms": duration,
        "tags": {"cs_variant": variant, "cs_target": target,
                 "cs_expert_final": expert,
                 **({"cs_handoff_state": handoff} if handoff else {})},
        "metadata": {"cs_handoff_triggers": triggers or []},
    }


def _iso_cutoff_patch(monkeypatch, hours=24):
    """保证合成时间戳落在窗口内（默认 now 基准已满足，无需 patch）。"""
    return hours


def test_route_consistency():
    traces = [
        _trace(target="cs_complaint", expert="cs_complaint_expert"),  # 一致
        _trace(target="cs_complaint", expert="cs_query_expert"),      # 不一致
        _trace(target="cs_complaint", expert="cs_complaint_expert"),  # 一致
    ]
    s = _summarize(traces)
    assert s["total"] == 3
    assert s["route_consistency"] == 0.667
    assert s["route_n"] == 3


def test_fallback_rate():
    traces = [
        _trace(answer="已为您处理投诉，专员将尽快与您联系。"),
        _trace(answer=""),                                        # 空 → 兜底
        _trace(answer="抱歉，客服系统暂时不可用，请稍后再试。"),   # 降级文案 → 兜底
    ]
    s = _summarize(traces)
    assert s["fallback_rate"] == 0.667


def test_handoff_rate_and_reason_buckets():
    traces = [
        _trace(handoff="handoff_requested", triggers=["explicit_request"]),   # healthy_user
        _trace(handoff="handoff_requested", triggers=["low_confidence"]),     # capability_gap
        _trace(handoff="handoff_requested", triggers=["complaint_escalation",
                                                      "consecutive_failures"]),  # 两条
        _trace(),  # 未转人工
    ]
    s = _summarize(traces)
    assert s["handoff_rate"] == 0.75
    assert s["handoff_by_reason"] == {"healthy_user": 1, "capability_gap": 2,
                                      "healthy_escalation": 1}


def test_latency_percentiles():
    traces = [_trace(duration=d) for d in (1000, 2000, 3000, 4000, 10000)]
    s = _summarize(traces)
    assert s["p50_ms"] == 3000
    assert s["p95_ms"] == 10000
    assert s["avg_ms"] == 4000


def test_bucket_handoff_reasons_from_metadata():
    t = _trace(triggers=["explicit_request", "unknown_trigger"])
    assert _bucket_handoff_reasons(t) == ["healthy_user", "capability_gap"]


def test_build_report_groups_by_variant_and_alerts(monkeypatch):
    from datetime import datetime as dt
    old = (dt.now(timezone.utc) - timedelta(days=3)).isoformat()  # 窗口外

    traces = [
        _trace(variant="treatment", answer=""),  # 兜底 → 触发告警
        _trace(variant="treatment", expert="cs_query_expert"),  # 与默认 target 不一致
        _trace(variant="control"),
        _trace(variant="treatment", ts=old),  # 窗口外，应被过滤
    ]

    class FakeStore:
        def list(self, limit=500):
            return traces

    import backend.observability.cs_quality_report as mod
    monkeypatch.setattr(
        "backend.observability.trace_store.get_trace_store", lambda: FakeStore())

    report = mod.build_cs_quality_report(hours=24)
    assert report["total_cs_traces"] == 3  # 窗口外被过滤
    assert report["by_variant"]["treatment"]["total"] == 2
    assert report["by_variant"]["control"]["total"] == 1
    # 兜底率 50% > 2% 阈值 → 告警
    types = {a["type"] for a in report["alerts"]}
    assert "cs_fallback_rate" in types
