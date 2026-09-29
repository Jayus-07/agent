"""tests/travel/test_core_contracts.py — 核心契约守护测试（v4 §4）

Evidence 两条 fail-fast 不变量 + 三档消费口径 + 信封/事件可序列化。
"""
import dataclasses
import json
from datetime import datetime, timedelta, timezone

import pytest

from backend.travel.core.contracts import (
    CONFIDENCE_BASELINE,
    SourceType,
    TravelContext,
    Evidence,
)
from backend.travel.core.events import build_travel_event, emit_travel_event


def _ts(**kwargs) -> datetime:
    return datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc, **kwargs)


class TestTravelContext:
    def test_frozen(self):
        ctx = TravelContext(request_id="r1", tenant_id="default",
                            user_id="u1", trace_id="t1")
        with pytest.raises(dataclasses.FrozenInstanceError):
            ctx.request_id = "r2"  # type: ignore[misc]

    def test_defaults_allow_partial(self):
        ctx = TravelContext(request_id="r1")
        assert ctx.tenant_id == "" and ctx.trace_id == ""


class TestEvidenceInvariants:
    def test_baseline_table_frozen(self):
        assert CONFIDENCE_BASELINE == {
            SourceType.LIVE: 0.95,
            SourceType.CACHE: 0.85,
            SourceType.RAG: 0.7,
            SourceType.SEED: 0.5,
            SourceType.ESTIMATE: 0.5,
        }

    def test_within_baseline_ok(self):
        e = Evidence(fact_id="f1", value={"a": 1}, source="tencent_lbs",
                     source_type=SourceType.LIVE, confidence=0.95,
                     verified_at=_ts())
        assert e.confidence == 0.95

    @pytest.mark.parametrize(
        ("source_type", "confidence"),
        [(SourceType.LIVE, 0.96), (SourceType.CACHE, 0.9), (SourceType.RAG, 0.71)],
    )
    def test_above_baseline_raises(self, source_type, confidence):
        with pytest.raises(ValueError):
            Evidence(fact_id="f1", value={}, source="x",
                     source_type=source_type, confidence=confidence,
                     verified_at=_ts())

    def test_unverified_cap(self):
        # 有核实时点：RAG 0.7 合法
        Evidence(fact_id="f2", value={}, source="rag:doc-1",
                 source_type=SourceType.RAG, confidence=0.7, verified_at=_ts())
        # 缺核实时点：0.7 超过 0.5 上限 → fail-fast
        with pytest.raises(ValueError):
            Evidence(fact_id="f2", value={}, source="rag:doc-1",
                     source_type=SourceType.RAG, confidence=0.7)

    def test_json_roundtrip_fields(self):
        e = Evidence(fact_id="f3", value={"price": 60}, source="seed",
                     source_type=SourceType.SEED, confidence=0.5)
        payload = {"fact_id": e.fact_id, "source_type": e.source_type.value,
                   "confidence": e.confidence}
        assert json.loads(json.dumps(payload))["source_type"] == "seed"


class TestEvidenceLevel:
    def test_live_fresh_is_trusted(self):
        e = Evidence(fact_id="f", value={}, source="tencent_lbs",
                     source_type=SourceType.LIVE, confidence=0.95,
                     verified_at=_ts(), expire_at=_ts() + timedelta(hours=1))
        assert e.evidence_level() == "trusted"

    def test_live_expired_is_may_change(self):
        e = Evidence(fact_id="f", value={}, source="tencent_lbs",
                     source_type=SourceType.LIVE, confidence=0.95,
                     verified_at=_ts(), expire_at=_ts() - timedelta(hours=1))
        assert e.evidence_level() == "may_change"

    def test_cache_is_may_change(self):
        e = Evidence(fact_id="f", value={}, source="redis",
                     source_type=SourceType.CACHE, confidence=0.85,
                     verified_at=_ts())
        assert e.evidence_level() == "may_change"

    @pytest.mark.parametrize("source_type", [SourceType.SEED, SourceType.ESTIMATE])
    def test_seed_estimate_need_confirmation(self, source_type):
        e = Evidence(fact_id="f", value={}, source="seed",
                     source_type=source_type, confidence=0.5)
        assert e.evidence_level() == "needs_confirmation"

    def test_low_confidence_needs_confirmation(self):
        e = Evidence(fact_id="f", value={}, source="rag:doc-1",
                     source_type=SourceType.RAG, confidence=0.5,
                     verified_at=_ts())
        assert e.evidence_level() == "needs_confirmation"


class TestEvents:
    def test_build_shape(self):
        event = build_travel_event("agent_timeout", agent="research", timeout_s=15)
        assert event["source"] == "travel"
        assert event["event"] == "agent_timeout"
        assert event["agent"] == "research"

    def test_emit_json_safe_with_unserializable_value(self):
        # default=str 兜底：运行时对象不得炸日志
        payload = emit_travel_event("probe", agent="planning", obj=object())
        json.dumps(payload, ensure_ascii=False, default=str)
